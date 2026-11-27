"""Smoke test: load the built gresource and exercise the native window.

Run via `meson test`; APP_RESOURCE points at the built gresource.
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCE = os.environ.get("APP_RESOURCE")

if not RESOURCE or not os.path.exists(RESOURCE):
    print("SKIP: APP_RESOURCE is not set")
    sys.exit(77)

sys.path.insert(0, SOURCE_ROOT)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GObject", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    print("SKIP: no display available")
    sys.exit(77)

Adw.init()

resource = Gio.Resource.load(RESOURCE)
resource._register()

os.environ.setdefault(
    "MD_SPLIT_STATE_DIR",
    os.path.join(
        tempfile.gettempdir(), f"md-split-window-state-{os.getpid()}"
    ),
)

from src import fileio, recovery, window as window_module
from src.window import (
    MdSplitWindow,
    _file_load_error_message,
    _preview_interval,
)

assert _preview_interval(0) == 180
assert _preview_interval(100_000) == 180
assert _preview_interval(100_001) == 400


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def wait_until(predicate, timeout_ms=4000):
    loop = GLib.MainLoop()
    state = {"timed_out": False}

    def poll():
        if predicate():
            loop.quit()
            return False
        return True

    def timeout():
        state["timed_out"] = True
        loop.quit()
        return False

    GLib.timeout_add(10, poll)
    timeout_id = GLib.timeout_add(timeout_ms, timeout)
    loop.run()
    if not state["timed_out"]:
        GLib.source_remove(timeout_id)
    assert not state["timed_out"], "timed out waiting for background work"


from src import spellcheck

window = MdSplitWindow()
assert "MD Split" in window.get_title(), window.get_title()
assert not window.document.is_dirty
# Syntax highlighting attaches from a low-priority idle after construction.
assert window._buffer.get_language() is None
# Spell checking attaches from a low-priority idle after construction.
while GLib.MainContext.default().iteration(False):
    pass
assert window._buffer.get_language() is not None
assert window._buffer.get_language().get_id() == "markdown"
assert window._buffer.get_highlight_syntax()
# The Markdown renderer and parser load with the first preview render.
assert "src.renderer" not in sys.modules
if not spellcheck.backend_available():
    assert window.spell_adapter is None
else:
    assert window.spell_adapter is not None
    assert window.spell_adapter.get_enabled() is True
    window._settings.set_boolean("spell-check", False)
    while GLib.MainContext.default().iteration(False):
        pass
    assert not window.spell_adapter.get_enabled()
    window._settings.set_boolean("spell-check", True)
    while GLib.MainContext.default().iteration(False):
        pass
    assert window.spell_adapter.get_enabled()

window.set_text("# Native preview\n\n**bold** and [link](https://example.com)")
assert "src.renderer" in sys.modules
assert window.get_text().startswith("# Native preview")
assert window.document.is_dirty
assert "Untitled.md" in window.window_title.get_title()
assert "•" in window.window_title.get_title()
assert "words" in window.label_stats.get_label(), window.label_stats.get_label()
assert "minute read" in window.label_stats.get_label(), window.label_stats.get_label()

texts = []
for widget in walk(window.preview):
    if isinstance(widget, Gtk.Label):
        texts.append(widget.get_text())
        texts.append(widget.get_label() or "")
joined = "\n".join(texts)
assert "Native preview" in joined, joined
assert "bold" in joined, joined
assert 'href="https://example.com"' in joined, joined
assert "WebKit" not in sys.modules, "window must not instantiate a web view"

# Selection statistics replace document totals while text is selected.
window.set_text("Hello world from the selection test")
window._buffer.select_range(
    window._buffer.get_iter_at_offset(0),
    window._buffer.get_iter_at_offset(11),
)
window._update_stats()
selection_stats = window.label_stats.get_label()
assert selection_stats.startswith("Selection:"), selection_stats
assert "2 words" in selection_stats, selection_stats
assert "11 characters" in selection_stats, selection_stats
assert "minute read" in selection_stats, selection_stats
window._buffer.place_cursor(window._buffer.get_end_iter())
window._update_stats()
assert not window.label_stats.get_label().startswith("Selection:")
assert "6 words" in window.label_stats.get_label(), window.label_stats.get_label()

# Manual formatting is one undo step and updates the whole document.
window.set_text("#  Heading  #\n\n\n\nbody ")
window.format_document()
assert window.get_text() == "# Heading\n\nbody\n", window.get_text()
window._buffer.undo()
assert window.get_text() == "#  Heading  #\n\n\n\nbody ", window.get_text()

# Large changes ask for confirmation and cancel leaves the text untouched.
large_source = "\n".join(f"#  Heading {index}  #" for index in range(10)) + "\n"
large_expected = (
    "\n".join(f"# Heading {index}" for index in range(10)) + "\n"
)
window.set_text(large_source)
window.format_document()
dialog = window._format_dialog
assert dialog is not None
assert dialog.get_heading() == "Format Document"
assert window.get_text() == large_source
dialog.emit("response", "cancel")
assert window._format_dialog is None
assert window.get_text() == large_source
window.format_document()
dialog = window._format_dialog
assert dialog is not None
dialog.emit("response", "format")
assert window.get_text() == large_expected, window.get_text()

# A selection formats only its own range.
window.set_text("before\n#  Selected  #\nafter\n")
start = len("before\n")
end = start + len("#  Selected  #")
window._buffer.select_range(
    window._buffer.get_iter_at_offset(start),
    window._buffer.get_iter_at_offset(end),
)
window.format_document()
assert window.get_text() == "before\n# Selected\nafter\n", window.get_text()
bounds = window._buffer.get_selection_bounds()
assert bounds
assert window._buffer.get_text(bounds[0], bounds[1], True) == "# Selected"
window._buffer.undo()
assert window.get_text() == "before\n#  Selected  #\nafter\n"

# Format on save rewrites the snapshot before it is written.
saved_texts = []
original_save = fileio.save_text_async


def capture_format_save(file, text, callback, cancellable=None, etag=None):
    saved_texts.append(text)
    callback(None, "etag")


fileio.save_text_async = capture_format_save
try:
    window._settings.set_boolean("format-on-save", True)
    window.set_text("#  Saved  #\n")
    window._write_file(
        Gio.File.new_for_path("/tmp/opencode/format-on-save.md"), None
    )
    assert saved_texts[-1] == "# Saved\n", saved_texts
    assert window.get_text() == "# Saved\n"

    window._settings.set_boolean("format-on-save", False)
    window.set_text("#  Keep  #\n")
    window._write_file(
        Gio.File.new_for_path("/tmp/opencode/format-on-save.md"), None
    )
    assert saved_texts[-1] == "#  Keep  #\n", saved_texts
finally:
    window._settings.set_boolean("format-on-save", True)
    fileio.save_text_async = original_save

# Find and replace wiring.
assert not window.search_bar.get_search_mode()
window.show_find()
assert window.search_bar.get_search_mode()
assert not window.replace_row.get_visible()
window.show_replace()
assert window.search_bar.get_search_mode()
assert window.replace_row.get_visible()

window.set_text("one one one")
window.search_entry.set_text("one")
window._on_search_changed()
assert "3 matches" in window.search_status_label.get_label(), (
    window.search_status_label.get_label()
)


def find_selection():
    bounds = window._buffer.get_selection_bounds()
    if not bounds:
        return None
    return (
        window._buffer.get_text(bounds[0], bounds[1], True),
        bounds[0].get_offset(),
    )


# Return and the next/previous buttons must advance past the current match.
assert find_selection() == ("one", 0), find_selection()
window._find_next()
assert find_selection() == ("one", 4), find_selection()
window.search_next_button.emit("clicked")
assert find_selection() == ("one", 8), find_selection()
window.search_entry.emit("activate")
assert find_selection() == ("one", 0), find_selection()
window.search_previous_button.emit("clicked")
assert find_selection() == ("one", 8), find_selection()

# Refining the query keeps the current match as the anchor.
window.search_entry.set_text("on")
window._on_search_changed()
assert find_selection() == ("on", 8), find_selection()
window.search_entry.set_text("one")
window._on_search_changed()
assert find_selection() == ("one", 8), find_selection()

window.replace_entry.set_text("two")
window._replace_all()
assert window.get_text() == "two two two", window.get_text()
window._buffer.undo()
assert window.get_text() == "one one one", window.get_text()

window._buffer.select_range(
    window._buffer.get_iter_at_offset(0),
    window._buffer.get_iter_at_offset(3),
)
window._replace_current()
assert window.get_text() == "two one one", window.get_text()
window._buffer.undo()
assert window.get_text() == "one one one", window.get_text()

window.search_entry.set_text("[")
window.search_regex_button.set_active(True)
window._on_search_changed()
assert "Invalid regular expression" in window.search_status_label.get_label()
window._close_search()
assert not window.search_bar.get_search_mode()

# External edits prompt once per etag and offer reload/keep choices.
window._ignore_monitor_until = 0.0
window._last_prompted_etag = None
with tempfile.TemporaryDirectory() as tmp:
    external_path = Path(tmp) / "external.md"
    external_path.write_text("base", encoding="utf-8")
    external = Gio.File.new_for_path(str(external_path))
    window.document.load("base", external, "old-etag")
    window._handle_file_change(external, "changed", "new-etag")
    dialog = window._confirm_dialog
    assert dialog is not None
    assert dialog.get_heading() == "File changed on disk"
    dialog.close()
    window._confirm_dialog = None

    window._handle_file_change(external, "deleted", None)
    dialog = window._confirm_dialog
    assert dialog is not None
    assert dialog.get_heading() == "File deleted"
    dialog.close()
    window._confirm_dialog = None

# Autosave drafts are written off the main loop and clear on save/discard.
window.set_text("draft text")
window._autosave_now()
draft_key = window._draft_key()
wait_until(lambda: recovery.read_draft(draft_key) is not None)
draft = recovery.read_draft(draft_key)
assert draft is not None and draft.text == "draft text"
window._clear_recovery()
assert recovery.read_draft(draft_key) is None

with tempfile.TemporaryDirectory() as tmp:
    recovered_path = Path(tmp) / "recovered.md"
    recovered_path.write_text("saved baseline", encoding="utf-8")
    recovered = recovery.Draft(
        key="recovered",
        text="unsaved recovery",
        path=str(recovered_path),
        name="recovered.md",
        updated=0.0,
    )
    window.load_recovered_draft(recovered)
    assert window.get_text() == "unsaved recovery"
    assert window.document.is_dirty
    assert window.document.file is not None
    assert window.document.file.get_path() == str(recovered_path)
    window._clear_recovery()

with tempfile.TemporaryDirectory() as tmp:
    recent_path = Path(tmp) / "recent.md"
    recent_path.write_text("recent", encoding="utf-8")
    window._record_recent(Gio.File.new_for_path(str(recent_path)))
    assert str(recent_path) in window._settings.get_strv("recent-files")
    menu = window.button_recent.get_menu_model()
    assert menu is not None and menu.get_n_items() >= 1
window._settings.set_strv("recent-files", [])

window.set_view_mode("source")
assert window.editor_pane.get_visible()
assert not window.preview_container.get_visible()
assert window.preview._paused
window.set_view_mode("preview")
assert not window.editor_pane.get_visible()
assert window.preview_container.get_visible()
assert not window.preview._paused
window.set_view_mode("both")
assert window.editor_pane.get_visible()
assert window.preview_container.get_visible()

window.destroy()

# Gtk.FileDialog invokes its callback with only (source_object, result).
# A required user-data parameter raises TypeError before the dialog result is
# handled, so guard the arity directly.
import inspect

open_parameters = list(
    inspect.signature(MdSplitWindow._on_open_finished).parameters
)
assert open_parameters == ["self", "dialog", "result"], open_parameters

# A save completing after newer edits must not mark those edits clean.
from src import fileio

window = MdSplitWindow()
window._settings.set_boolean("format-on-save", False)
file = Gio.File.new_for_path("/tmp/opencode/test-save-race.md")
window.document.load("old", file)
pending_saves = []
original_save = fileio.save_text_async


def fake_save(_file, text, callback, cancellable=None, etag=None):
    pending_saves.append((text, callback))


fileio.save_text_async = fake_save
try:
    window.set_text("first version")
    window.save_document()
    assert window.document.is_dirty
    window.set_text("new unsaved edit")
    payload, complete = pending_saves.pop(0)
    complete(None, "new-etag")
    assert payload == "first version"
    assert window.get_text() == "new unsaved edit"
    assert window.document.is_dirty
finally:
    fileio.save_text_async = original_save
    window.destroy()

# A load completing after the user edited must not replace those edits.
window = MdSplitWindow()
pending_loads = []
original_load = fileio.load_text_async


def fake_load(_file, callback, cancellable=None):
    pending_loads.append((callback, cancellable))


fileio.load_text_async = fake_load
try:
    window.open_file(Gio.File.new_for_path("/tmp/opencode/test-load-race.md"))
    window.set_text("typed while loading")
    loaded, _cancellable = pending_loads.pop(0)
    loaded("disk contents", "etag", None)
    assert window.get_text() == "typed while loading"
    assert window.document.is_dirty
finally:
    fileio.load_text_async = original_load
    window.destroy()

# Hidden previews must not synchronously render or start image requests.
window = MdSplitWindow()
window.set_view_mode("source")
rendered = []
window._preview.set_markdown = lambda *args, **kwargs: rendered.append(True)
window.set_text("# Hidden")
window._render_preview()
assert not rendered
window.destroy()

# The editor owns keyboard focus once the window is mapped.
window = MdSplitWindow()
window.present()
while GLib.MainContext.default().iteration(False):
    pass
assert window.get_focus() is window.editor_view, window.get_focus()
window.destroy()

# The bundled demo loads as clean, untitled scratch content.
window = MdSplitWindow()
window.load_demo_document()
assert window.get_text().startswith("# MD Split"), window.get_text()[:40]
assert not window.document.is_dirty
assert window.document.file is None
window.set_text(window.get_text() + "\nchanged")
assert window.document.is_dirty
window.destroy()

# The demo preview renders from an idle, not before the window can paint.
window = MdSplitWindow()
window.set_view_mode("both", persist=False)
rendered = []
window._preview.set_markdown = lambda *args, **kwargs: rendered.append(True)
window.load_demo_document()
assert window.get_text().startswith("# MD Split"), window.get_text()[:40]
assert not rendered, "demo preview should not render before the main loop"
while GLib.MainContext.default().iteration(False):
    pass
assert rendered, "demo preview should render after the main loop runs"
window.destroy()

# Typing ] after an empty [ completes a link with the cursor inside ().
window = MdSplitWindow()
window.set_text("abc [")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_bracketright, 0, Gdk.ModifierType(0))
assert window.get_text() == "abc []()", window.get_text()
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 7, insert.get_offset()
window._settings.set_boolean("autocomplete-links", False)
window.set_text("abc [")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert not window._on_key_pressed(None, Gdk.KEY_bracketright, 0, Gdk.ModifierType(0))
window._settings.set_boolean("autocomplete-links", True)
window.destroy()

# Auto-pairing inserts every delimiter in prose and list content as one undo step.
window = MdSplitWindow()
window.set_view_mode("source", persist=False)
for keyval, opening, closing in (
    (Gdk.KEY_parenleft, "(", ")"),
    (Gdk.KEY_bracketleft, "[", "]"),
    (Gdk.KEY_braceleft, "{", "}"),
    (Gdk.KEY_quotedbl, '"', '"'),
    (Gdk.KEY_apostrophe, "'", "'"),
    (Gdk.KEY_grave, "`", "`"),
    (Gdk.KEY_asterisk, "*", "*"),
    (Gdk.KEY_underscore, "_", "_"),
    (Gdk.KEY_asciitilde, "~", "~"),
):
    original = "- item "
    window.set_text(original)
    window._buffer.place_cursor(window._buffer.get_end_iter())
    assert window._on_key_pressed(None, keyval, 0, Gdk.ModifierType(0))
    assert window.get_text() == original + opening + closing
    insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
    assert insert.get_offset() == len(original) + 1
    window._buffer.undo()
    assert window.get_text() == original, (opening, window.get_text())

# Closing over an existing delimiter only moves the cursor. Undo still reverts
# the insertion in one step because cursor motion does not create an undo action.
window.set_text("abc ")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_parenleft, 0, 0)
assert window._on_key_pressed(None, Gdk.KEY_parenright, 0, 0)
assert window.get_text() == "abc ()"
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == len("abc ()")
window._buffer.undo()
assert window.get_text() == "abc "

# Backspace removes an empty pair as one undoable edit.
window.set_text("abc ")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_bracketleft, 0, 0)
assert window._on_key_pressed(None, Gdk.KEY_BackSpace, 0, 0)
assert window.get_text() == "abc "
window._buffer.undo()
assert window.get_text() == "abc []"

# Auto-paired brackets still compose with empty-link completion.
window.set_text("abc ")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_bracketleft, 0, 0)
assert window.get_text() == "abc []"
assert window._on_key_pressed(None, Gdk.KEY_bracketright, 0, 0)
assert window.get_text() == "abc []()"
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 7

# Word characters, code contexts, shortcuts, and IME preedit bypass pairing.
for text, cursor in (
    ("word", 0),
    ("`code`", 3),
    ("```\ncode\n```", 5),
    ("~~~\ncode\n~~~", 5),
):
    window.set_text(text)
    window._buffer.place_cursor(window._buffer.get_iter_at_offset(cursor))
    assert not window._on_key_pressed(None, Gdk.KEY_parenleft, 0, 0)
    assert window.get_text() == text
window.set_text("abc ")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert not window._on_key_pressed(
    None, Gdk.KEY_parenleft, 0, Gdk.ModifierType.CONTROL_MASK
)
window.editor_view.emit("preedit-changed", "´")
assert not window._on_key_pressed(None, Gdk.KEY_parenleft, 0, 0)
assert window.get_text() == "abc "
window.editor_view.emit("preedit-changed", "")
assert window._on_key_pressed(None, Gdk.KEY_parenleft, 0, 0)
assert window.get_text() == "abc ()"

# Disabling the preference leaves opening, closing, and Backspace keys alone.
window._settings.set_boolean("auto-pairing", False)
window.set_text("")
assert not window._on_key_pressed(None, Gdk.KEY_parenleft, 0, 0)
assert window.get_text() == ""
window.set_text("()")
window._buffer.place_cursor(window._buffer.get_iter_at_offset(1))
assert not window._on_key_pressed(None, Gdk.KEY_parenright, 0, 0)
assert not window._on_key_pressed(None, Gdk.KEY_BackSpace, 0, 0)
assert window.get_text() == "()"
window._settings.set_boolean("auto-pairing", True)
window.destroy()

# Cursor-only keys must not copy the whole buffer.
window = MdSplitWindow()
window.set_text("plain text")
window._buffer.place_cursor(window._buffer.get_start_iter())
text_reads = []
original_get_text = window.get_text


def counting_get_text():
    text_reads.append(True)
    return original_get_text()


window.get_text = counting_get_text
for keyval in (
    Gdk.KEY_Left,
    Gdk.KEY_Right,
    Gdk.KEY_Up,
    Gdk.KEY_Down,
    Gdk.KEY_Home,
    Gdk.KEY_End,
):
    assert not window._on_key_pressed(
        None, keyval, 0, Gdk.ModifierType(0)
    )
window._buffer.select_range(
    window._buffer.get_iter_at_offset(1),
    window._buffer.get_iter_at_offset(4),
)
assert not window._on_key_pressed(None, Gdk.KEY_Left, 0, Gdk.ModifierType(0))
assert not text_reads, text_reads
window.get_text = original_get_text
window.destroy()

# Selections can be surrounded with brackets, braces, backticks and quotes.
window = MdSplitWindow()
for keyval, expected in (
    (Gdk.KEY_bracketleft, "h[ell]o"),
    (Gdk.KEY_braceleft, "h{ell}o"),
    (Gdk.KEY_grave, "h`ell`o"),
    (Gdk.KEY_quotedbl, 'h"ell"o'),
    (Gdk.KEY_apostrophe, "h'ell'o"),
    (Gdk.KEY_asterisk, "h*ell*o"),
    (Gdk.KEY_underscore, "h_ell_o"),
    (Gdk.KEY_asciitilde, "h~ell~o"),
):
    window.set_text("hello")
    window._buffer.select_range(
        window._buffer.get_iter_at_offset(1),
        window._buffer.get_iter_at_offset(4),
    )
    assert window._on_key_pressed(None, keyval, 0, Gdk.ModifierType(0))
    assert window.get_text() == expected, (keyval, window.get_text())
window.destroy()

# Formatting shortcuts operate on the current selection.
window = MdSplitWindow()
for kind, expected in (
    ("bold", "h**ell**o"),
    ("italic", "h*ell*o"),
    ("underline", "h<u>ell</u>o"),
    ("code", "h`ell`o"),
):
    window.set_text("hello")
    window._buffer.select_range(
        window._buffer.get_iter_at_offset(1),
        window._buffer.get_iter_at_offset(4),
    )
    window.format_selection(kind)
    assert window.get_text() == expected, (kind, window.get_text())
window.set_text("one\ntwo")
window._buffer.select_range(
    window._buffer.get_iter_at_offset(0),
    window._buffer.get_iter_at_offset(len("one\ntwo")),
)
window.format_selection("blockquote")
assert window.get_text() == "> one\n> two", window.get_text()
window.destroy()

# Line-operation actions preserve cursors/selections and undo in one step.
from src.main import MdSplitApplication

line_app = MdSplitApplication("line-actions")
line_app.register(None)
window = MdSplitWindow(application=line_app)
window.present()
while GLib.MainContext.default().iteration(False):
    pass
assert line_app.props.active_window is window
window.editor_view.grab_focus()
editor_has_focus = window.editor_view.has_focus
# Parallel display tests can take the actual toplevel focus. Exercise the
# action's editor-focus gate deterministically without synthesizing input.
window.editor_view.has_focus = lambda: True
source = "one\ntwo\nthree"
window.set_text(source)
window._buffer.place_cursor(window._buffer.get_iter_at_offset(2))
line_app.lookup_action("move-lines-down").activate()
assert window.get_text() == "two\none\nthree"
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 6
window._buffer.undo()
assert window.get_text() == source
line_app.lookup_action("move-lines-up").activate()
assert window.get_text() == source

# A four-line source exercises a multi-line move and restored selection.
window.set_text("one\ntwo\nthree\nfour")
window._buffer.select_range(
    window._buffer.get_iter_at_offset(1),
    window._buffer.get_iter_at_offset(8),
)
line_app.lookup_action("move-lines-down").activate()
assert window.get_text() == "three\none\ntwo\nfour"
bounds = window._buffer.get_selection_bounds()
assert tuple(iterator.get_offset() for iterator in bounds) == (7, 14)
window._buffer.undo()
assert window.get_text() == "one\ntwo\nthree\nfour"
bounds = window._buffer.get_selection_bounds()
assert tuple(iterator.get_offset() for iterator in bounds) == (1, 8)

window.set_text("one\ntwo")
window._buffer.place_cursor(window._buffer.get_iter_at_offset(2))
line_app.lookup_action("duplicate-line").activate()
assert window.get_text() == "one\none\ntwo"
window._buffer.undo()
assert window.get_text() == "one\ntwo"
window.set_text("abc")
window._buffer.select_range(
    window._buffer.get_iter_at_offset(1),
    window._buffer.get_iter_at_offset(2),
)
line_app.lookup_action("duplicate-line").activate()
assert window.get_text() == "abbc"
bounds = window._buffer.get_selection_bounds()
assert tuple(iterator.get_offset() for iterator in bounds) == (1, 2)
window._buffer.undo()
assert window.get_text() == "abc"

# Tab indents list subtrees and Shift+Tab outdents them as separate undo steps.
list_source = "- parent\n  - child"
window.set_text(list_source)
window._buffer.place_cursor(window._buffer.get_iter_at_offset(3))
assert window._on_key_pressed(None, Gdk.KEY_Tab, 0, 0)
indented = "  - parent\n    - child"
assert window.get_text() == indented
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 5
assert window._on_key_pressed(
    None, Gdk.KEY_ISO_Left_Tab, 0, Gdk.ModifierType.SHIFT_MASK
)
assert window.get_text() == list_source
window._buffer.undo()
assert window.get_text() == indented
window._buffer.undo()
assert window.get_text() == list_source
assert not window._on_key_pressed(
    None, Gdk.KEY_ISO_Left_Tab, 0, Gdk.ModifierType.SHIFT_MASK
)

window.set_text("- one\n- two")
window._buffer.select_range(
    window._buffer.get_start_iter(), window._buffer.get_end_iter()
)
assert window._on_key_pressed(None, Gdk.KEY_Tab, 0, 0)
assert window.get_text() == "  - one\n  - two"
window.set_text("plain")
window._buffer.place_cursor(window._buffer.get_start_iter())
assert not window._on_key_pressed(None, Gdk.KEY_Tab, 0, 0)
assert window.get_text() == "plain"

# List indentation still composes with Return continuation.
window.set_text("- item")
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_Tab, 0, 0)
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_Return, 0, 0)
assert window.get_text() == "  - item\n  - "

# Task shortcuts toggle bullet and ordered markers and are a no-op elsewhere.
window.set_text("- [ ] task")
window._buffer.place_cursor(window._buffer.get_start_iter())
line_app.lookup_action("toggle-task").activate()
assert window.get_text() == "- [x] task"
window._buffer.undo()
assert window.get_text() == "- [ ] task"
window.set_text("2. [X] done")
window._buffer.place_cursor(window._buffer.get_end_iter())
line_app.lookup_action("toggle-task").activate()
assert window.get_text() == "2. [ ] done"
window.set_text("- item")
line_app.lookup_action("toggle-task").activate()
assert window.get_text() == "- item"

# Go to Line keeps invalid input open, clamps large targets, and refocuses the
# editor. Cancel closes the dialog without moving the cursor.
window.editor_view.has_focus = editor_has_focus
window.editor_view.grab_focus()
window.set_text(source)
window._buffer.place_cursor(window._buffer.get_start_iter())
line_app.lookup_action("go-to-line").activate()
dialog = window._go_to_line_dialog
assert dialog is not None and dialog.get_modal()
entry = next(widget for widget in walk(dialog) if isinstance(widget, Gtk.Entry))
entry.set_text("0")
dialog.response(Gtk.ResponseType.ACCEPT)
assert window._go_to_line_dialog is dialog
assert any(
    isinstance(widget, Gtk.Label)
    and widget.get_visible()
    and widget.get_label().startswith("Enter a line number")
    for widget in walk(dialog)
)
entry.set_text("999")
dialog.response(Gtk.ResponseType.ACCEPT)
while GLib.MainContext.default().iteration(False):
    pass
assert window._go_to_line_dialog is None
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_line() == 2
assert insert.get_offset() == 8
assert window.get_focus() is window.editor_view
line_app.lookup_action("go-to-line").activate()
dialog = window._go_to_line_dialog
assert dialog is not None
entry = next(widget for widget in walk(dialog) if isinstance(widget, Gtk.Entry))
entry.set_text("2")
dialog.response(Gtk.ResponseType.CANCEL)
while GLib.MainContext.default().iteration(False):
    pass
assert window._go_to_line_dialog is None
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 8
assert window.get_focus() is window.editor_view
window.go_to_line(0)
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_line() == 0

# Menu actions do not edit the buffer while another widget owns focus.
window.set_text("one\ntwo")
window._buffer.place_cursor(window._buffer.get_iter_at_offset(2))
window.editor_view.has_focus = lambda: False
line_app.lookup_action("duplicate-line").activate()
assert window.get_text() == "one\ntwo"
window.editor_view.has_focus = editor_has_focus
window._force_close = True
window.destroy()
while GLib.MainContext.default().iteration(False):
    pass

# Link and image authoring actions use native pickers and async preparation.
from unittest import mock
import struct
import zlib


def make_png(width=2, height=2):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


window = MdSplitWindow(application=line_app)
window.set_view_mode("both", persist=False)
window.present()
while GLib.MainContext.default().iteration(False):
    pass
assert line_app.props.active_window is window
window.editor_view.has_focus = lambda: True
window.set_text("")
line_app.lookup_action("insert-link").activate()
assert window.get_text() == "[]()"
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 1
window._buffer.undo()
assert window.get_text() == ""
window.set_text("hello")
window._buffer.select_range(
    window._buffer.get_iter_at_offset(1), window._buffer.get_iter_at_offset(4)
)
line_app.lookup_action("insert-link").activate()
assert window.get_text() == "h[ell]()o"
insert = window._buffer.get_iter_at_mark(window._buffer.get_insert())
assert insert.get_offset() == 7

with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
    root = Path(directory)
    local = root / "local image.png"
    local.write_bytes(make_png())
    external = Path(other) / "external.png"
    external.write_bytes(make_png())
    window._document.file = Gio.File.new_for_path(str(root / "notes.md"))

    # The picker is image-filtered and forwards the selected local file without
    # opening a real system dialog during the test.
    with mock.patch.object(Gtk.FileDialog, "open", autospec=True) as picker:
        window.show_image_picker()
        assert picker.call_count == 1
    dialog = window._file_dialog
    assert dialog.get_title() == "Insert Image"
    assert dialog.get_default_filter() is not None
    original_insert_image_file = window.insert_image_file
    picked = []
    window.insert_image_file = picked.append
    fake_dialog = mock.Mock()
    fake_dialog.open_finish.return_value = Gio.File.new_for_path(str(local))
    window._on_image_picked(fake_dialog, None)
    assert [file.get_path() for file in picked] == [str(local)]

    # A contained file is referenced relatively and resolves in the preview.
    from src import image_insert

    window.set_text("Diagram")
    window._buffer.select_range(
        window._buffer.get_start_iter(), window._buffer.get_end_iter()
    )
    window.insert_image_file = original_insert_image_file
    window.insert_image_file(Gio.File.new_for_path(str(local)))
    wait_until(lambda: "![Diagram]" in window.get_text())
    assert window.get_text() == "![Diagram](local%20image.png)"
    window.flush_preview()
    resolved_preview = window.preview.renderer._resolve_image("local%20image.png")
    assert resolved_preview == ("file", local.resolve()), (
        resolved_preview,
        window.preview.renderer._base_dir,
        window.preview.renderer._base_file,
        local.resolve(),
    )

    # File drops use the same insertion path and reject unsupported values.
    dropped = []
    window.insert_image_file = dropped.append
    external_file = Gio.File.new_for_path(str(external))

    value = GObject.Value()
    value.init(Gdk.FileList)
    value.set_boxed(Gdk.FileList.new_from_array([external_file]))
    assert window._on_image_drop(None, value, 0, 0)
    assert [file.get_path() for file in dropped] == [str(external)]
    assert not window._on_image_drop(None, object(), 0, 0)
    window.insert_image_file = original_insert_image_file

    # Clipboard textures are encoded on a worker and saved under assets/.
    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(make_png()))

    class FakeClipboard:
        def __init__(self, result=None, error=None):
            self.result = result
            self.error = error

        def read_texture_async(self, _cancellable, callback, context):
            callback(self, None, context)

        def read_texture_finish(self, _result):
            if self.error is not None:
                raise self.error
            return self.result

    clipboard = FakeClipboard(texture)
    window.get_clipboard = lambda: clipboard
    prepared_paths = []
    original_prepare = image_insert.prepare_clipboard_image_async

    def record_prepare(texture_value, document_dir, callback):
        def record(path, error):
            if path is not None:
                prepared_paths.append(path)
            callback(path, error)

        return original_prepare(texture_value, document_dir, record)

    image_insert.prepare_clipboard_image_async = record_prepare
    try:
        window.set_text("")
        window._buffer.place_cursor(window._buffer.get_start_iter())
        window.paste_image()
        wait_until(lambda: "assets/clipboard-" in window.get_text())
        assert window.get_text().startswith("![](assets/clipboard-")
        assert prepared_paths[-1].parent == root / "assets"
        assert prepared_paths[-1].read_bytes().startswith(b"\x89PNG")

        # Untitled documents fall back to an absolute temporary image path.
        window._document.file = None
        window.set_text("")
        window.paste_image()
        wait_until(lambda: "md-split-clipboard-" in window.get_text())
        temporary_image = prepared_paths[-1]
        assert temporary_image.is_absolute()
        assert temporary_image.parent.name.startswith("md-split-clipboard-")
        import shutil

        shutil.rmtree(temporary_image.parent)

        # Unsupported clipboard data reports an error without changing text.
        window.set_text("unchanged")
        before = window.get_text()
        clipboard.error = GLib.Error.new_literal(
            Gio.io_error_quark(), "no image", Gio.IOErrorEnum.NOT_SUPPORTED
        )
        window.paste_image()
        assert window.get_text() == before
        assert window._error_dialog is not None
        assert "Clipboard does not contain" in window._error_dialog.get_body()
        window._error_dialog = None
    finally:
        image_insert.prepare_clipboard_image_async = original_prepare

window._force_close = True
window.destroy()
while GLib.MainContext.default().iteration(False):
    pass

# Export overwrites are confirmed, and a missing file is accepted directly.
import tempfile
from pathlib import Path

window = MdSplitWindow()
with tempfile.TemporaryDirectory() as export_dir:
    target = Gio.File.new_for_path(str(Path(export_dir) / "report.html"))
    Path(export_dir, "report.html").write_text("old", encoding="utf-8")
    accepted = []
    window._confirm_export_overwrite(target, accepted.append)
    assert window._confirm_dialog is not None
    assert not accepted, accepted
    window._confirm_dialog.emit("response", "replace")
    assert accepted == [target], accepted
    window._confirm_dialog = None

    missing = Gio.File.new_for_path(str(Path(export_dir) / "new.html"))
    accepted = []
    window._confirm_export_overwrite(missing, accepted.append)
    assert accepted == [missing], accepted
window.destroy()

# Exports run in a worker thread and finish on the main loop.
window = MdSplitWindow()
window.set_text("# Export")
rendered_threads = []
saved = []
from src import exporting

original_render = exporting.render_html
original_save = fileio.save_text_async


def threaded_render(*args, **kwargs):
    rendered_threads.append(threading.get_ident())
    return original_render(*args, **kwargs)


def capture_save(file, text, callback, cancellable=None, etag=None):
    saved.append((file.get_path(), text))
    callback(None, None)


exporting.render_html = threaded_render
fileio.save_text_async = capture_save
try:
    html_target = Gio.File.new_for_path("/tmp/opencode/threaded.html")
    window._export_html_to(html_target)
    assert saved == [], "HTML export blocked the caller"
    wait_until(lambda: bool(saved))
finally:
    exporting.render_html = original_render
    fileio.save_text_async = original_save
assert rendered_threads and rendered_threads[0] != threading.get_ident()
assert saved[0][0] == html_target.get_path()
assert saved[0][1].startswith("<!doctype html>")
window.destroy()

window = MdSplitWindow()
window.set_text("# PDF")
pdf_threads = []
original_pdf = exporting.write_pdf


def threaded_pdf(*args, **kwargs):
    pdf_threads.append(threading.get_ident())


exporting.write_pdf = threaded_pdf
try:
    window._export_pdf_to(
        Gio.File.new_for_path("/tmp/opencode/threaded.pdf")
    )
    wait_until(lambda: bool(pdf_threads))
finally:
    exporting.write_pdf = original_pdf
assert pdf_threads[0] != threading.get_ident()
window.destroy()

# Window geometry is clamped to the schema ranges before it is persisted.
window = MdSplitWindow()
assert window._clamped_setting("window-width", 5000) == 4000
assert window._clamped_setting("window-height", 100) == 400
assert window._clamped_setting("paned-position", 9000) == 4000
window.get_width = lambda: 5000
window.get_height = lambda: 3500
window._save_settings()
assert window.settings.get_int("window-width") == 4000
assert window.settings.get_int("window-height") == 3000
window.destroy()

# -- Vim mode --------------------------------------------------------------

from src.preview_vim import PreviewAction


def spin(times=30):
    for _ in range(times):
        while GLib.MainContext.default().iteration(False):
            pass


def controllers(widget):
    model = widget.observe_controllers()
    return [model.get_item(i) for i in range(model.get_n_items())]


window = MdSplitWindow()
window.set_default_size(900, 500)
window.present()
spin()
assert window.settings.get_boolean("vim-mode") is False
assert not window.vim_enabled
assert window.vim_context is not None
assert window._vim_controller not in controllers(window.editor_view)
assert not window.label_vim.get_visible()
assert not window.editor_view.get_overwrite()
assert window._vim_insert_mode()

preview_nav = window.preview_vim
assert preview_nav is not None

window.settings.set_boolean("vim-mode", True)
spin()
assert window.vim_enabled
assert window._vim_controller in controllers(window.editor_view)
assert window.label_vim.get_visible()
assert window.editor_view.get_overwrite()
assert preview_nav.enabled

window.settings.set_boolean("vim-mode", False)
spin()
assert not window.vim_enabled
assert window._vim_controller not in controllers(window.editor_view)
assert not window.editor_view.get_overwrite()
assert not preview_nav.enabled
window.settings.set_boolean("vim-mode", True)
spin()

display = Gdk.Display.get_default()
keyboard = display.get_default_seat().get_keyboard()
im = window.vim_context


def vim_key(keyval, state=0):
    ok, keys = display.map_keyval(keyval)
    assert ok, keyval
    entry = min(keys, key=lambda key: (key.group, key.level))
    if entry.level == 1:
        state |= Gdk.ModifierType.SHIFT_MASK
    for press in (True, False):
        surface = window.get_surface()
        if surface is None:  # the press closed the window (:q)
            break
        im.filter_key(
            press,
            surface,
            keyboard,
            0,
            entry.keycode,
            state,
            entry.group,
        )
    spin(5)


def vim_type(text):
    for character in text:
        vim_key(Gdk.unicode_to_keyval(ord(character)))


window.set_text("- one")
window.editor_view.grab_focus()
window._buffer.place_cursor(window._buffer.get_end_iter())
spin()

# The helper controller sits on an ancestor of the view, so GTK's capture
# phase always runs it before the Vim IM controller on the view itself.
assert window._key_controller in controllers(window.editor_scroller)
assert window._key_controller not in controllers(window.editor_view)
assert window.editor_view.is_ancestor(window.editor_scroller)

# Normal mode: the Markdown helpers stay out of Vim's way.
assert not window._vim_insert_mode()
assert window._on_key_pressed(None, Gdk.KEY_Return, 0, 0) is False
assert window.get_text() == "- one"

# Insert mode: list continuation still runs, and the mode is shown.
vim_key(Gdk.KEY_A)
assert window._vim_insert_mode(), im.get_command_bar_text()
assert "INSERT" in window.label_vim.get_label(), window.label_vim.get_label()
window._buffer.place_cursor(window._buffer.get_end_iter())
assert window._on_key_pressed(None, Gdk.KEY_Return, 0, 0) is True
assert window.get_text() == "- one\n- ", repr(window.get_text())
vim_key(Gdk.KEY_Escape)
assert not window._vim_insert_mode()

# Editor "/" search shows a match counter like the preview does. Real input
# reaches the window's helper controller before Vim, so mirror that order.
def vim_enter():
    window._on_key_pressed(None, Gdk.KEY_Return, 0, 0)
    vim_key(Gdk.KEY_Return)
    spin()


window.set_text("alpha Beta\nbeta gamma\nBETA delta beta\n")
window._buffer.place_cursor(window._buffer.get_start_iter())
spin()
vim_type("/beta")
assert window.label_vim.get_label() == "/beta"
vim_enter()
assert window.label_vim.get_label() == "/beta [1/2]", window.label_vim.get_label()
vim_type("n")
assert window.label_vim.get_label() == "/beta [2/2]", window.label_vim.get_label()
vim_type("n")
assert window.label_vim.get_label() == "/beta [1/2]", window.label_vim.get_label()
vim_type("j")
assert window.label_vim.get_label() == "", window.label_vim.get_label()
vim_type("/zzz")
vim_enter()
assert window.label_vim.get_label() == "Pattern not found: zzz"
# Real input clears the message in the window helper before Vim sees the key.
# "l" alone may not move at the end of a line, so exercise the helper directly
# instead of relying on the cursor-moved signal.
assert window._on_key_pressed(None, Gdk.KEY_l, 0, 0) is False
window._on_vim_text()
assert window.label_vim.get_label() == ""

# :w <path> writes through the normal save path; :q closes a clean window.
with tempfile.TemporaryDirectory() as tmp:
    target = os.path.join(tmp, "vim.md")
    window.set_text("# Vim\n")
    vim_type(":w " + target)
    vim_key(Gdk.KEY_Return)
    wait_until(lambda: not window.document.is_dirty)
    with open(target, encoding="utf-8") as handle:
        assert handle.read() == "# Vim\n"
    assert window.document.file is not None
    assert window.document.file.get_path() == target

    # Commands the window does not handle fall through to GtkSourceView.
    vim_type(":set nu")
    vim_key(Gdk.KEY_Return)
    assert window.editor_view.get_show_line_numbers()

    closed = []
    window.connect("close-request", lambda *_args: closed.append(True) or False)
    vim_type(":q")
    vim_key(Gdk.KEY_Return)
    spin()
    assert closed

# Preview navigation: scrolling, jumps and search on a long document.
window = MdSplitWindow()
window.set_default_size(900, 500)
window.set_view_mode("preview", persist=False)
window.present()
window.settings.set_boolean("vim-mode", True)
sections = []
for number in range(1, 13):
    body = "\n\n".join(f"Paragraph {number}.{line} filler text." for line in range(8))
    sections.append(f"# Section {number}\n\n{body}")
sections[8] += "\n\nThe needle is here."
sections[10] += "\n\nAnother Needle appears."
window.set_text("\n\n".join(sections))
adjustment = window.preview.get_vadjustment()
wait_until(
    lambda: adjustment.get_upper() > adjustment.get_page_size() * 3
    and window.preview.content_widget() is not None
)
spin()
window.preview.scroll_to_value(0, sync=False)
vim = window.preview_vim
assert vim is not None


def highlighted_text() -> str:
    assert vim is not None
    assert vim._highlighted is not None
    return vim._highlighted.get_text()


vim.perform(PreviewAction("scroll-down", 3))
assert adjustment.get_value() > 100, adjustment.get_value()
vim.perform(PreviewAction("bottom"))
maximum = adjustment.get_upper() - adjustment.get_page_size()
assert abs(adjustment.get_value() - maximum) < 1
vim.perform(PreviewAction("top"))
assert adjustment.get_value() == 0
vim.perform(PreviewAction("goto-percent", 50))
assert abs(adjustment.get_value() - maximum / 2) < 1

# Key presses go through the parser: 5j, gg and Ctrl+D.
window.preview.scroll_to_value(0, sync=False)
for keyval in (Gdk.KEY_5, Gdk.KEY_j):
    assert vim._on_key_pressed(None, keyval, 0, 0)
assert abs(adjustment.get_value() - 200) < 1, adjustment.get_value()
for keyval in (Gdk.KEY_g, Gdk.KEY_g):
    assert vim._on_key_pressed(None, keyval, 0, 0)
assert adjustment.get_value() == 0
assert vim._on_key_pressed(
    None, Gdk.KEY_d, 0, Gdk.ModifierType.CONTROL_MASK
)
assert adjustment.get_value() > 0
assert not vim._on_key_pressed(
    None, Gdk.KEY_s, 0, Gdk.ModifierType.CONTROL_MASK
), "app shortcuts keep working"

# ]] moves to the next heading below the viewport top.
window.preview.scroll_to_value(0, sync=False)
vim.perform(PreviewAction("next-heading"))
first = adjustment.get_value()
assert first > 0
vim.perform(PreviewAction("next-heading"))
assert adjustment.get_value() > first
vim.perform(PreviewAction("previous-heading"))
assert abs(adjustment.get_value() - first) < 1

# / search is smartcase: "needle" finds both, "Needle" only the second.
window.preview.scroll_to_value(0, sync=False)
statuses = []
vim._on_status = statuses.append
assert vim.search("needle")
assert highlighted_text() == "The needle is here."
assert statuses[-1].endswith("[1/2]"), statuses
vim.perform(PreviewAction("next-match"))
assert highlighted_text() == "Another Needle appears."
assert statuses[-1].endswith("[2/2]"), statuses
vim.perform(PreviewAction("next-match"))
assert "continuing at TOP" in statuses[-1], statuses
assert highlighted_text() == "The needle is here."
vim.perform(PreviewAction("previous-match"))
assert highlighted_text() == "Another Needle appears."
window.preview.scroll_to_value(0, sync=False)
assert vim.search("Needle")
assert highlighted_text() == "Another Needle appears."
assert statuses[-1].endswith("[1/1]"), statuses
assert not vim.search("missing")
assert "Pattern not found" in statuses[-1]
assert vim._highlighted is None

# The prompt opens on / and runs the search on Enter.
assert vim._on_key_pressed(None, Gdk.KEY_slash, 0, 0)
assert vim.prompt_open
assert window.preview_prompt_prefix.get_label() == "/"
window.preview_prompt_entry.set_text("needle")
window.preview_prompt_entry.emit("activate")
assert not vim.prompt_open
assert vim._highlighted is not None

# Pane switching: i from preview-only reveals and focuses the editor; the
# status line drops the preview search text.
window._buffer.place_cursor(window._buffer.get_start_iter())
window.preview.grab_focus()
spin()
assert window.view_mode == "preview"
fraction = window.preview.get_scroll_fraction()
assert fraction is not None and fraction > 0.2
assert vim._on_key_pressed(None, Gdk.KEY_i, 0, 0)
spin()
assert window.view_mode == "both"
assert window.get_focus() is window.editor_view
assert "needle" not in window.label_vim.get_label()
# The cursor follows the part being read instead of staying at the top.
wait_until(
    lambda: window._buffer.get_iter_at_mark(window._buffer.get_insert()).get_line() > 0
)

# Ctrl+W w from the editor's Normal mode moves to the preview, and back.
ctrl = Gdk.ModifierType.CONTROL_MASK
assert window._on_key_pressed(None, Gdk.KEY_w, 0, ctrl)
assert window._on_key_pressed(None, Gdk.KEY_w, 0, 0)
spin()
assert window.get_focus() is not None
assert window.get_focus() is window.preview or window.get_focus().is_ancestor(
    window.preview
)
assert vim._on_key_pressed(None, Gdk.KEY_w, 0, ctrl)
assert vim._on_key_pressed(None, Gdk.KEY_w, 0, 0)
spin()
assert window.get_focus() is window.editor_view

# Ctrl+W l from source-only reveals the preview.
window.set_view_mode("source", persist=False)
window.focus_editor()
spin()
assert window._on_key_pressed(None, Gdk.KEY_w, 0, ctrl)
assert window._on_key_pressed(None, Gdk.KEY_l, 0, 0)
spin()
assert window.view_mode == "both"
assert window.get_focus() is window.preview or window.get_focus().is_ancestor(
    window.preview
)

# With Vim mode off the preview ignores the keys.
window.settings.set_boolean("vim-mode", False)
spin()
window.preview.scroll_to_value(0, sync=False)
assert window._vim_controller not in controllers(window.editor_view)
assert vim._controller not in controllers(window.preview)
# Ctrl+click follows Markdown links from the source pane.
class _FakeGesture:
    def __init__(self, state):
        self._state = state

    def get_current_event_state(self):
        return self._state


with tempfile.TemporaryDirectory() as link_directory:
    link_root = Path(link_directory)
    link_target = link_root / "notes.md"
    link_target.write_text("# Notes\n", encoding="utf-8")
    window.document.file = Gio.File.new_for_path(
        str(link_root / "doc.md")
    )
    window.set_text("[notes](notes.md) and [missing](absent.md)")
    launched = []
    window._launch_uri = launched.append
    errors = []
    window._show_error = lambda heading, message: errors.append(
        (heading, message)
    )

    # Pointer coordinates map back to the link's buffer offset.
    window._buffer.place_cursor(window._buffer.get_start_iter())
    window.editor_view.scroll_to_mark(
        window._buffer.get_insert(), 0, False, 0, 0
    )
    spin()
    pointer_iter = window._buffer.get_iter_at_offset(2)
    pointer_rect = window.editor_view.get_iter_location(pointer_iter)
    pointer_x, pointer_y = window.editor_view.buffer_to_window_coords(
        Gtk.TextWindowType.WIDGET,
        pointer_rect.x + pointer_rect.width // 2,
        pointer_rect.y + pointer_rect.height // 2,
    )
    mapped_offset = window._editor_offset_at(pointer_x, pointer_y)
    assert mapped_offset in (1, 2, 3), (
        mapped_offset,
        pointer_x,
        pointer_y,
        pointer_rect,
    )

    notes_offset = window.get_text().index("notes")
    assert window._follow_link_at_offset(notes_offset)
    assert launched == [link_target.as_uri()]

    window.set_text("[notes](notes.md#top)")
    launched.clear()
    assert window._follow_link_at_offset(window.get_text().index("notes"))
    assert launched == [link_target.as_uri() + "#top"]

    window.set_text("[missing](absent.md)")
    missing_offset = window.get_text().index("missing")
    assert window._follow_link_at_offset(missing_offset)
    assert "File not found" in errors[-1][1]
    assert launched == [link_target.as_uri() + "#top"]

    window.set_text("`[notes](notes.md)`")
    assert not window._follow_link_at_offset(window.get_text().index("notes"))

    window.set_text("[bad](javascript:alert(1))")
    assert window._follow_link_at_offset(1)
    assert "Unsupported" in errors[-1][1]

    # The gesture ignores plain clicks, double-clicks and selections.
    window.set_text("[notes](notes.md)")
    window._editor_offset_at = lambda x, y: window.get_text().index("notes")

    # The pointer cursor follows Ctrl+hover over a link.
    window._on_editor_motion(_FakeGesture(Gdk.ModifierType(0)), 0, 0)
    assert window.editor_view.get_cursor().get_name() == "text"
    window._on_editor_motion(
        _FakeGesture(Gdk.ModifierType.CONTROL_MASK), 0, 0
    )
    assert window.editor_view.get_cursor().get_name() == "pointer"
    window._on_link_modifier_released(None, Gdk.KEY_Control_L, 0, 0)
    assert window.editor_view.get_cursor().get_name() == "text"
    window._on_link_modifier_pressed(None, Gdk.KEY_Control_L, 0, 0)
    assert window.editor_view.get_cursor().get_name() == "pointer"
    window._on_editor_leave(None)
    assert window.editor_view.get_cursor() is None

    launched.clear()
    window._on_editor_pressed(
        _FakeGesture(Gdk.ModifierType(0)), 1, 0, 0
    )
    assert not launched
    window._on_editor_pressed(
        _FakeGesture(Gdk.ModifierType.CONTROL_MASK), 2, 0, 0
    )
    assert not launched
    window._buffer.select_range(
        window._buffer.get_start_iter(), window._buffer.get_end_iter()
    )
    window._on_editor_pressed(
        _FakeGesture(Gdk.ModifierType.CONTROL_MASK), 1, 0, 0
    )
    assert not launched
    window._buffer.place_cursor(window._buffer.get_start_iter())
    window._on_editor_pressed(
        _FakeGesture(Gdk.ModifierType.CONTROL_MASK), 1, 0, 0
    )
    assert launched == [link_target.as_uri()]

# Missing files get a clear message; Flatpak adds the sandbox hint only for
# paths outside the shared home directory.
missing_error = GLib.Error.new_literal(
    Gio.io_error_quark(), "missing", Gio.IOErrorEnum.NOT_FOUND
)
outside_home = Gio.File.new_for_path("/var/tmp/md-split-missing.md")
assert _file_load_error_message(missing_error, outside_home) == (
    "File not found: /var/tmp/md-split-missing.md"
)
inside_home = Gio.File.new_for_path(
    str(Path.home() / "md-split-missing.md")
)
original_sandbox = window_module._flatpak_sandbox
window_module._flatpak_sandbox = lambda: True
try:
    assert "Flatpak cannot access" in _file_load_error_message(
        missing_error, outside_home
    )
    assert "File not found" in _file_load_error_message(
        missing_error, inside_home
    )
finally:
    window_module._flatpak_sandbox = original_sandbox

window.destroy()
window.settings.reset("vim-mode")

print("ok: native window")
