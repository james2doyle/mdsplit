"""In-process tests for application actions and pending file operations."""

import gc
import logging
import os
import sys
import tempfile
import time
import weakref
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
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    print("SKIP: no display available")
    sys.exit(77)

Adw.init()
resource = Gio.Resource.load(RESOURCE)
resource._register()

os.environ.setdefault(
    "MD_SPLIT_STATE_DIR",
    os.path.join(
        tempfile.gettempdir(), f"md-split-lifecycle-state-{os.getpid()}"
    ),
)

from src import APP_ID, fileio, recovery
from src import main as main_module
from src.main import MdSplitApplication
from src.window import MdSplitWindow

app = MdSplitApplication("test")
app.register(None)
app.do_startup()

theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
assert theme.has_icon(APP_ID)
assert theme.has_icon(f"{APP_ID}-symbolic")

# Defaults: follow the system theme, load the demo, and enable both helpers.
assert app.settings.get_string("appearance") == "system"
assert app.settings.get_string("view-mode") == "both"
assert app.settings.get_boolean("load-demo-on-startup") is True
assert app.settings.get_boolean("sync-scroll") is True
assert app.settings.get_boolean("autocomplete-links") is True
assert app.settings.get_boolean("auto-pairing") is True
assert app.settings.get_boolean("format-on-save") is True
assert app.settings.get_boolean("restore-session") is True
assert app.settings.get_boolean("autosave") is True
assert app.settings.get_int("autosave-interval") == 30
assert app.settings.get_boolean("backup-files") is False
assert app.settings.get_int("backup-retention") == 5
assert app.settings.get_boolean("spell-check") is True
assert app.settings.get_string("spell-language") == ""
assert app.settings.get_strv("session-files") == []
assert app.settings.get_strv("recent-files") == []

# Keep save-queue tests focused on serialization, not formatting payloads.
app.settings.set_boolean("format-on-save", False)

# Quit must request a window close so the dirty-document guard runs.
close_requests = []
original_close_request = MdSplitWindow._on_close_request


def guarded_close(self, *_args):
    close_requests.append(True)
    return True


MdSplitWindow._on_close_request = guarded_close
try:
    window = MdSplitWindow(application=app)
    window.present()
    while GLib.MainContext.default().iteration(False):
        pass
    window.set_text("unsaved")
    app.lookup_action("quit").activate()
    assert close_requests == [True]
    window._force_close = True
    window.destroy()
finally:
    MdSplitWindow._on_close_request = original_close_request

# Global settings/style-manager signals must not retain destroyed windows.
probe = MdSplitWindow(application=app)
reference = weakref.ref(probe)
probe.destroy()
probe.run_dispose()
while GLib.MainContext.default().iteration(False):
    pass
del probe
gc.collect()
gc.collect()
assert reference() is None, "destroyed window is still retained"

# Every GIO-opened file gets a window instead of silently dropping extras.
opened = []
original_open_file = MdSplitWindow.open_file


def record_open(self, file, on_loaded=None):
    opened.append(file.get_path())


MdSplitWindow.open_file = record_open
try:
    app.do_open(
        [
            Gio.File.new_for_path("/tmp/opencode/one.md"),
            Gio.File.new_for_path("/tmp/opencode/two.md"),
        ],
        2,
        "",
    )
    assert opened == ["/tmp/opencode/one.md", "/tmp/opencode/two.md"]
    for extra_window in app.get_windows():
        extra_window._force_close = True
        extra_window.destroy()
finally:
    MdSplitWindow.open_file = original_open_file

# Saves are serialized and a later snapshot supersedes earlier continuations.
window = MdSplitWindow(application=app)
file = Gio.File.new_for_path("/tmp/opencode/serialized-save.md")
window.document.load("saved", file)
pending = []
original_save = fileio.save_text_async
continuations = []


def fake_save(_file, text, callback, cancellable=None, etag=None):
    pending.append((text, callback))


fileio.save_text_async = fake_save
try:
    window.set_text("first")
    window.save_document_then(lambda: continuations.append("first"))
    window.set_text("second")
    window.save_document_then(lambda: continuations.append("second"))
    assert len(pending) == 1

    first_payload, finish_first = pending.pop(0)
    finish_first(None, "etag-one")
    assert first_payload == "first"
    assert len(pending) == 1
    assert not continuations

    second_payload, finish_second = pending.pop(0)
    finish_second(None, "etag-two")
    assert second_payload == "second"
    assert continuations == ["second"]
    assert window.document.file is not None
    assert window.document.file.equal(file)
    assert not window.document.is_dirty
    assert window.document.etag == "etag-two"
finally:
    fileio.save_text_async = original_save
    window.destroy()

# A callback-less save interleaved after a continuation keeps the continuation.
window = MdSplitWindow(application=app)
file = Gio.File.new_for_path("/tmp/opencode/interleaved-save.md")
window.document.load("saved", file)
pending = []
continuations = []
fileio.save_text_async = fake_save
try:
    window.set_text("first")
    window.save_document_then(lambda: continuations.append("open"))
    window.set_text("second")
    window.save_document()
    assert len(pending) == 1

    first_payload, finish_first = pending.pop(0)
    finish_first(None, "etag-one")
    assert first_payload == "first"
    assert len(pending) == 1
    assert not continuations

    second_payload, finish_second = pending.pop(0)
    assert second_payload == "second"
    finish_second(None, "etag-two")
    assert continuations == ["open"], continuations
    assert not window.document.is_dirty
finally:
    fileio.save_text_async = original_save
    window._force_close = True
    window.destroy()

# A stale write completing after New must not touch the new document.
window = MdSplitWindow(application=app)
old_file = Gio.File.new_for_path("/tmp/opencode/stale-save.md")
window.document.load("old", old_file)
pending = []
fileio.save_text_async = fake_save
try:
    window.set_text("old edit")
    window.save_document()
    payload, finish_old = pending.pop(0)
    window._reset_document()
    finish_old(None, "stale-etag")
    assert payload == "old edit"
    assert window.get_text() == ""
    assert window.document.file is None
    assert window.document.etag is None
    assert not window.document.is_dirty
finally:
    fileio.save_text_async = original_save
    window._force_close = True
    window.destroy()

# Appearance changes apply immediately and default to the system preference.
app.settings.set_string("appearance", "dark")
assert (
    Adw.StyleManager.get_default().get_color_scheme()
    == Adw.ColorScheme.FORCE_DARK
)
app.settings.set_string("appearance", "light")
assert (
    Adw.StyleManager.get_default().get_color_scheme()
    == Adw.ColorScheme.FORCE_LIGHT
)
app.settings.set_string("appearance", "system")
assert (
    Adw.StyleManager.get_default().get_color_scheme()
    == Adw.ColorScheme.DEFAULT
)

# Formatting actions are registered with the documented accelerators.
assert [value.lower() for value in app.get_accels_for_action("app.format-bold")] == [
    "<control>b"
]
assert [
    value.lower()
    for value in app.get_accels_for_action("app.format-italic")
] == ["<control>i"]
assert [
    value.lower()
    for value in app.get_accels_for_action("app.format-underline")
] == ["<control>u"]
assert [
    value.lower()
    for value in app.get_accels_for_action("app.format-blockquote")
] == ["<control>t"]
assert [
    value.lower()
    for value in app.get_accels_for_action("app.format-code")
] == ["<control>grave"]

assert app.lookup_action("preferences") is not None
assert app.lookup_action("export-html") is not None
assert app.lookup_action("export-pdf") is not None
assert app.lookup_action("format-bold") is not None
assert [
    value.lower() for value in app.get_accels_for_action("app.find")
] == ["<control>f"]
assert [
    value.lower() for value in app.get_accels_for_action("app.replace")
] == ["<control>h"]
assert app.lookup_action("find-next") is not None
assert app.lookup_action("find-previous") is not None
format_accels = [
    value.lower()
    for value in app.get_accels_for_action("app.format-document")
]
assert len(format_accels) == 1, format_accels
assert "<control>" in format_accels[0], format_accels
assert "<shift>" in format_accels[0], format_accels
assert format_accels[0].endswith("r"), format_accels
assert app.lookup_action("format-document") is not None
assert app.lookup_action("open-recent") is not None
assert app.lookup_action("clear-recent") is not None

def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.MainContext.default().iteration(False):
            pass
        if predicate():
            return True
        time.sleep(0.01)
    return False


# Copy Path follows the active document and writes its location to the
# clipboard; it is disabled for untitled documents.
copy_action = app.lookup_action("copy-path")
assert copy_action is not None
assert copy_action.get_enabled() is False
assert app.get_accels_for_action("app.copy-path") == []

for existing in app.get_windows():
    existing._force_close = True
    existing.destroy()
while GLib.MainContext.default().iteration(False):
    pass

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "copy-path.md"
    target.write_text("# Copy\n", encoding="utf-8")
    window = MdSplitWindow(application=app)
    window.present()
    assert wait_for(lambda: app.props.active_window is window)
    assert not copy_action.get_enabled()

    window.open_file(Gio.File.new_for_path(str(target)))
    assert wait_for(lambda: window.document.path_or_uri == str(target))
    assert copy_action.get_enabled()

    copied = []

    def read_clipboard(source, result):
        copied.append(source.read_text_finish(result))

    copy_action.activate(None)
    window.get_clipboard().read_text_async(None, read_clipboard)
    assert wait_for(lambda: copied), copied
    assert copied[0] == str(target), copied

    window.new_document()
    assert not copy_action.get_enabled()

    # A completed save, including Save As, enables the action again.
    window.document.mark_saved(Gio.File.new_for_path(str(target)))
    window._update_ui()
    assert copy_action.get_enabled()

    window._force_close = True
    window.destroy()
    while GLib.MainContext.default().iteration(False):
        pass
assert not copy_action.get_enabled()

# Secondary instances leave the primary session and recovery prompts alone.
primary_session = list(app.settings.get_strv("session-files"))
original_prompted = app._recovery_prompted
app.new_instance = True
try:
    secondary_window = MdSplitWindow(application=app)
    app.note_window_file(secondary_window, "/tmp/opencode/secondary.md")
    assert app.settings.get_strv("session-files") == primary_session

    app._recovery_prompted = False
    app._prompt_recovery_drafts()
    assert not app._recovery_prompted

    secondary_window.destroy()
finally:
    app.new_instance = False
    app._recovery_prompted = original_prompted

# Session restore reopens file windows and focuses the previous active file.
for existing in app.get_windows():
    existing._force_close = True
    existing.destroy()
while GLib.MainContext.default().iteration(False):
    pass

with tempfile.TemporaryDirectory() as tmp:
    first_path = Path(tmp) / "first.md"
    second_path = Path(tmp) / "second.md"
    missing_path = Path(tmp) / "missing.md"
    first_path.write_text("# First\n", encoding="utf-8")
    second_path.write_text("# Second\n", encoding="utf-8")
    app.settings.set_boolean("restore-session", True)
    app.settings.set_boolean("load-demo-on-startup", False)
    app.settings.set_strv(
        "session-files",
        [str(first_path), str(missing_path), str(second_path)],
    )
    app.settings.set_string("session-active", str(second_path))
    app.do_activate()
    assert wait_for(
        lambda: sum(
            1 for win in app.get_windows() if win.document.file is not None
        )
        == 2
    ), [win.document.file for win in app.get_windows()]
    restored_paths = sorted(
        win.document.file.get_path()
        for win in app.get_windows()
        if win.document.file is not None
    )
    assert restored_paths == sorted([str(first_path), str(second_path)])
    assert app.props.active_window is not None
    assert (
        app.props.active_window.document.file.get_path()
        == str(second_path)
    )
    # File loads complete in any order, so the session list is a set of
    # windows rather than a required ordering.
    assert sorted(app.settings.get_strv("session-files")) == sorted(
        [str(first_path), str(second_path)]
    ), app.settings.get_strv("session-files")
    assert app._missing_dialog is not None
    assert (
        app._missing_dialog.get_heading()
        == "Some files could not be reopened"
    )
    app._missing_dialog.close()
    app._missing_dialog = None

for existing in app.get_windows():
    existing._force_close = True
    existing.destroy()
while GLib.MainContext.default().iteration(False):
    pass
app.settings.set_strv("session-files", [])
app.settings.set_string("session-active", "")
app.settings.set_boolean("restore-session", True)

# Recovery prompts discard drafts when the user declines to restore them.
with tempfile.TemporaryDirectory() as tmp:
    draft = recovery.Draft(
        key="lifecycle-recovery",
        text="unsaved text",
        path=None,
        name="Untitled.md",
        updated=time.time(),
    )
    recovery.write_draft(draft)
    app._recovery_prompted = False
    app._prompt_recovery_drafts()
    assert app._recovery_dialog is not None
    assert app._recovery_dialog.get_heading() == "Recover unsaved document"
    app._recovery_dialog.close()
    assert recovery.read_draft(draft.key) is None
    for existing in app.get_windows():
        existing._force_close = True
        existing.destroy()
    while GLib.MainContext.default().iteration(False):
        pass

# Startup loads the bundled demo as clean untitled content when enabled.
# The window is presented first; the demo fills in from an idle callback.
app.settings.set_boolean("load-demo-on-startup", True)
events = []
original_present = MdSplitWindow.present
original_load_demo = MdSplitWindow.load_demo_document


def record_present(self):
    events.append("present")
    return original_present(self)


def record_load_demo(self):
    events.append("load")
    return original_load_demo(self)


MdSplitWindow.present = record_present
MdSplitWindow.load_demo_document = record_load_demo
try:
    app.do_activate()
    window = app.props.active_window
    assert window is not None
    assert events == ["present"], events
    assert window.get_text() == ""
    while GLib.MainContext.default().iteration(False):
        pass
    assert events == ["present", "load"], events
    assert window.get_text().startswith("# MD Split"), window.get_text()[:40]
    assert not window.document.is_dirty
    assert window.document.file is None
    window._force_close = True
    window.destroy()
    while GLib.MainContext.default().iteration(False):
        pass
finally:
    MdSplitWindow.present = original_present
    MdSplitWindow.load_demo_document = original_load_demo

app.settings.set_boolean("load-demo-on-startup", False)
app.do_activate()
window = app.props.active_window
assert window is not None
assert window.get_text() == ""
window._force_close = True
window.destroy()
app.settings.set_boolean("load-demo-on-startup", True)

# An older libadwaita is reported instead of failing silently at the menu.
messages = []


class CaptureHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        messages.append(record)


handler = CaptureHandler()
logger = logging.getLogger("src.main")
logger.addHandler(handler)
original_minimum = main_module.MINIMUM_ADW
main_module.MINIMUM_ADW = (99, 0)
try:
    main_module.MdSplitApplication("test")
finally:
    main_module.MINIMUM_ADW = original_minimum
    logger.removeHandler(handler)
assert any(
    "libadwaita 1.9" in record.getMessage() for record in messages
), messages

print("ok: application lifecycle")
