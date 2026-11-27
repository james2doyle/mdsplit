# SPDX-License-Identifier: MIT

"""Main window for MD Split: a native GFM editor and preview."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from gettext import gettext as _
from gettext import ngettext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, GtkSource

from . import (
    APP_ID,
    VIEW_MODES,
    editing,
    filechanges,
    fileio,
    image_insert,
    launcher as launcher_mod,
    links,
    preview_vim,
    recovery,
    session,
    spellcheck,
)
from .document import Document, text_statistics
from .markdown_view import MarkdownView
from .image_policy import ImageTooLargeError, ImageValidationError
from .paths import ensure_file_extension, markdown_image_path, resolve_link

if TYPE_CHECKING:
    from . import formatting

log = logging.getLogger(__name__)


def _io_error_matches(error: Exception, code: Gio.IOErrorEnum) -> bool:
    matches = getattr(error, "matches", None)
    return bool(
        matches and matches(Gio.io_error_quark(), code)
    )


def _flatpak_sandbox() -> bool:
    """Return True when the app is running inside a Flatpak sandbox."""
    return bool(os.environ.get("FLATPAK_ID")) or os.path.exists("/.flatpak-info")


def _flatpak_home_path(path: str) -> bool:
    """Return True when ``path`` is inside the shared home directory."""
    try:
        return Path(path).resolve().is_relative_to(Path.home().resolve())
    except (OSError, RuntimeError):
        return True


def _file_load_error_message(error: Exception, file: Gio.File) -> str:
    """Turn a load failure into an actionable message for the user."""
    if not _io_error_matches(error, Gio.IOErrorEnum.NOT_FOUND):
        return str(error)
    path = file.get_path()
    if path is None:
        return str(error)
    if _flatpak_sandbox() and not _flatpak_home_path(path):
        return _(
            "Flatpak cannot access {path}. The sandbox shares your home "
            "directory; grant access to other locations with Flatseal or a "
            "flatpak override."
        ).format(path=path)
    return _("File not found: {path}").format(path=path)

_MAX_WORD_COUNT_CHARS = 2_000_000
_LARGE_PREVIEW_CHARS = 100_000


def _preview_interval(length: int) -> int:
    """Debounce shorter updates for large documents."""
    return 400 if length > _LARGE_PREVIEW_CHARS else 180

_gtksource_types_ready = False


def _adjustment_fraction(adjustment: Gtk.Adjustment) -> float | None:
    maximum = adjustment.get_upper() - adjustment.get_page_size()
    if maximum <= 0:
        return None
    return max(0.0, min(1.0, adjustment.get_value() / maximum))


def _ensure_gtksource_types() -> None:
    """Force GtkSource GTypes to register before GtkBuilder parses the template.

    PyGObject registers namespace types lazily, and GtkBuilder needs
    GtkSourceView to exist when it builds window.ui.
    """
    global _gtksource_types_ready
    if _gtksource_types_ready:
        return
    GtkSource.View()
    _gtksource_types_ready = True


@Gtk.Template(resource_path="/io/github/james2doyle/MdSplit/window.ui")
class MdSplitWindow(Adw.ApplicationWindow):
    __gtype_name__ = "MdSplitWindow"

    window_title = Gtk.Template.Child("window_title")
    button_recent = Gtk.Template.Child("button_recent")
    editor_pane = Gtk.Template.Child("editor_pane")
    editor_scroller = Gtk.Template.Child("editor_scroller")
    editor_view = Gtk.Template.Child("editor_view")
    paned = Gtk.Template.Child("paned")
    preview_container = Gtk.Template.Child("preview_container")
    preview_prompt_revealer = Gtk.Template.Child("preview_prompt_revealer")
    preview_prompt_prefix = Gtk.Template.Child("preview_prompt_prefix")
    preview_prompt_entry = Gtk.Template.Child("preview_prompt_entry")
    placeholder_label = Gtk.Template.Child("placeholder_label")
    label_cursor = Gtk.Template.Child("label_cursor")
    label_stats = Gtk.Template.Child("label_stats")
    label_vim = Gtk.Template.Child("label_vim")
    toast_overlay = Gtk.Template.Child("toast_overlay")
    search_bar = Gtk.Template.Child("search_bar")
    search_entry = Gtk.Template.Child("search_entry")
    search_case_button = Gtk.Template.Child("search_case_button")
    search_word_button = Gtk.Template.Child("search_word_button")
    search_regex_button = Gtk.Template.Child("search_regex_button")
    search_wrap_button = Gtk.Template.Child("search_wrap_button")
    search_status_label = Gtk.Template.Child("search_status_label")
    search_previous_button = Gtk.Template.Child("search_previous_button")
    search_next_button = Gtk.Template.Child("search_next_button")
    replace_row = Gtk.Template.Child("replace_row")
    replace_entry = Gtk.Template.Child("replace_entry")
    replace_button = Gtk.Template.Child("replace_button")
    replace_all_button = Gtk.Template.Child("replace_all_button")

    def __init__(self, initial_view_mode=None, **kwargs):
        _ensure_gtksource_types()
        self._initial_view_mode = initial_view_mode
        super().__init__(**kwargs)
        self._settings = Gio.Settings(schema_id=APP_ID)
        self._document = Document()
        self._loading = False
        self._preview_timeout = 0
        self._language_idle = 0
        self._stats_timeout = 0
        self._search_timeout = 0
        self._search_previous_mode: str | None = None
        self._file_monitor = None
        self._monitor_handler = 0
        self._monitor_timeout = 0
        self._ignore_monitor_until = 0.0
        self._last_prompted_etag: str | None = None
        self._pending_monitor_kind = "changed"
        self._draft_token = os.urandom(16).hex()
        self._last_draft_key: str | None = None
        self._autosave_timeout = 0
        self._recovery_generation = 0
        self.spell_adapter: Any = None
        self._preview_text: str | None = None
        self._file_dialog = None
        self._confirm_dialog = None
        self._format_dialog = None
        self._go_to_line_dialog = None
        self._error_dialog = None
        self._load_cancellable = None
        self._load_generation = 0
        self._document_epoch = 0
        self._save_queue: list[
            tuple[Gio.File, Callable[[], None] | None, int]
        ] = []
        self._save_active = False
        self._destroyed = False
        self._force_close = False
        self._focused_once = False
        self._scroll_guard = False
        self._view_mode = "both"
        self._saved_paned_position = None
        self._key_controller = None
        self._signal_handlers: list[tuple[object, int]] = []
        self._vim_im: GtkSource.VimIMContext | None = None
        self._vim_controller: Gtk.EventControllerKey | None = None
        self._vim_attached = False
        self._vim_mode_handler = 0
        self._editor_ctrl_w = False
        self._editor_preedit = ""
        self._preview_vim: preview_vim.PreviewVim | None = None
        # Editor "/" search mirrored for the status-bar match counter.
        self._vim_search: tuple[str, str] | None = None
        self._vim_search_context: GtkSource.SearchContext | None = None
        self._vim_search_handler = 0
        self._vim_search_message = ""
        self._vim_search_check = False
        self._link_motion: Gtk.EventControllerMotion | None = None
        self._text_cursor: Gdk.Cursor | None = None
        self._pointer_cursor: Gdk.Cursor | None = None
        self._editor_cursor_name: str | None = None
        self._link_hover_position: tuple[float, float] | None = None
        self._link_hover_links: list[links.SourceLink] = []
        self._link_hover_revision = -1

        self.set_icon_name(APP_ID)
        self._preview = MarkdownView()
        self.preview_container.set_child(self._preview)

        self._setup_editor()
        self._setup_search()
        self._setup_window()
        self._connect_signals()
        self._update_ui(render_preview=True)

    # -- setup -----------------------------------------------------------

    def _setup_editor(self) -> None:
        self._buffer = GtkSource.Buffer()
        self.editor_view.set_buffer(self._buffer)
        self._apply_editor_scheme()
        # Creating the Markdown highlighter costs ~50 ms; attach it after the
        # first frame instead of delaying window construction.
        self._language_idle = GLib.idle_add(
            self._attach_markdown_language, priority=GLib.PRIORITY_LOW
        )

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._signal_handlers.append(
            (controller, controller.connect("key-pressed", self._on_key_pressed))
        )
        # Attach to the scroller, an ancestor of the view: capture-phase
        # controllers run on ancestors before the target, so the Markdown
        # helpers always see keys before the Vim IM controller on the view.
        self.editor_scroller.add_controller(controller)
        self._key_controller = controller
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._signal_handlers.append(
            (click, click.connect("pressed", self._on_editor_pressed))
        )
        self.editor_view.add_controller(click)
        self._link_click = click
        motion = Gtk.EventControllerMotion.new()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._signal_handlers.append(
            (motion, motion.connect("motion", self._on_editor_motion))
        )
        self._signal_handlers.append(
            (motion, motion.connect("leave", self._on_editor_leave))
        )
        self.editor_view.add_controller(motion)
        self._link_motion = motion
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        self._signal_handlers.append(
            (drop_target, drop_target.connect("drop", self._on_image_drop))
        )
        self.editor_view.add_controller(drop_target)
        self._image_drop_target = drop_target
        modifier = Gtk.EventControllerKey.new()
        modifier.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._signal_handlers.append(
            (
                modifier,
                modifier.connect(
                    "key-pressed", self._on_link_modifier_pressed
                ),
            )
        )
        self._signal_handlers.append(
            (
                modifier,
                modifier.connect(
                    "key-released", self._on_link_modifier_released
                ),
            )
        )
        self.add_controller(modifier)
        self._text_cursor = Gdk.Cursor.new_from_name("text")
        self._pointer_cursor = Gdk.Cursor.new_from_name("pointer")
        # libspelling and dictionary loading can take a while; let the first
        # frame paint before spell checking attaches to the buffer.
        self._spell_idle = GLib.idle_add(
            self._setup_spellcheck_idle, priority=GLib.PRIORITY_LOW
        )
        self._setup_vim()

    def _attach_markdown_language(self) -> bool:
        self._language_idle = 0
        if self._destroyed:
            return False
        language = GtkSource.LanguageManager.get_default().get_language(
            "markdown"
        )
        if language is not None:
            self._buffer.set_language(language)
        self._buffer.set_highlight_syntax(True)
        return False

    # -- source links ----------------------------------------------------

    def _on_editor_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        """Follow a Markdown link on Ctrl+click without affecting plain clicks."""
        if n_press != 1:
            return
        state = gesture.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return
        if state & Gdk.ModifierType.SHIFT_MASK:
            return
        if self._buffer.get_has_selection():
            return
        offset = self._editor_offset_at(x, y)
        if offset is not None:
            self._follow_link_at_offset(offset)

    def _on_editor_motion(
        self, controller: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        self._link_hover_position = (x, y)
        state = controller.get_current_event_state()
        self._update_link_cursor(
            x, y, bool(state & Gdk.ModifierType.CONTROL_MASK)
        )

    def _on_editor_leave(
        self, _controller: Gtk.EventControllerMotion
    ) -> None:
        self._link_hover_position = None
        self._editor_cursor_name = None
        self.editor_view.set_cursor(None)

    def _on_link_modifier_pressed(
        self, _controller, keyval: int, _keycode: int, _state
    ) -> bool:
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self._refresh_link_cursor(True)
        return False

    def _on_link_modifier_released(
        self, _controller, keyval: int, _keycode: int, _state
    ) -> bool:
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self._refresh_link_cursor(False)
        return False

    def _refresh_link_cursor(self, ctrl: bool) -> None:
        position = self._link_hover_position
        if position is not None:
            self._update_link_cursor(position[0], position[1], ctrl)

    def _update_link_cursor(self, x: float, y: float, ctrl: bool) -> None:
        name = "text"
        if ctrl and not self._buffer.get_has_selection():
            offset = self._editor_offset_at(x, y)
            if offset is not None and self._link_at_offset(offset):
                name = "pointer"
        if name == self._editor_cursor_name:
            return
        self._editor_cursor_name = name
        cursor = self._pointer_cursor if name == "pointer" else self._text_cursor
        self.editor_view.set_cursor(cursor)

    def _link_at_offset(self, offset: int) -> links.SourceLink | None:
        if self._link_hover_revision != self._document.revision:
            self._link_hover_links = links.links_in_text(self.get_text())
            self._link_hover_revision = self._document.revision
        for link in self._link_hover_links:
            if link.start <= offset < link.end:
                return link
        return None

    def _editor_offset_at(self, x: float, y: float) -> int | None:
        buffer_x, buffer_y = self.editor_view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, int(x), int(y)
        )
        found, iterator = self.editor_view.get_iter_at_location(
            buffer_x, buffer_y
        )
        if not found:
            return None
        return iterator.get_offset()

    def _follow_link_at_offset(self, offset: int) -> bool:
        """Open or reveal the link at ``offset``; return whether one was found."""
        link = self._link_at_offset(offset)
        if link is None:
            return False
        uri = resolve_link(
            link.destination, self._document.directory, self._document.file
        )
        if uri is None:
            self._show_error(
                _("Could not open link"),
                _("Unsupported or invalid link: {destination}").format(
                    destination=link.destination
                ),
            )
            return True
        if uri.startswith("#"):
            self._preview.scroll_to_anchor(uri[1:])
            return True
        if uri.startswith("file:"):
            file_uri = uri.split("#", 1)[0].split("?", 1)[0]
            target = Gio.File.new_for_uri(file_uri)
            try:
                exists = target.query_exists(None)
            except GLib.Error:
                exists = False
            if not exists:
                self._show_error(
                    _("Could not open link"),
                    _("File not found: {path}").format(
                        path=target.get_path() or link.destination
                    ),
                )
                return True
        self._launch_uri(uri)
        return True

    def _launch_uri(self, uri: str) -> None:
        # Local .md links open in a new MD Split instance; other file links
        # go through FileLauncher because Gtk.UriLauncher sends plain OpenURI
        # portal calls, which xdg-desktop-portal >= 1.21 rejects for file://
        # URIs ("The application launch failed"); FileLauncher passes a GFile
        # and uses the fd-based OpenFile path.
        if uri.startswith("file:"):
            if launcher_mod.spawn_markdown_instance(uri):
                return
            file_uri = uri.split("#", 1)[0].split("?", 1)[0]
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_uri(file_uri))
        else:
            launcher = Gtk.UriLauncher.new(uri)
        launcher.launch(self, None, self._on_link_launched, None)

    def _on_link_launched(self, launcher, result, _data) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error as error:
            self._show_error(_("Could not open link"), error.message)

    # -- vim mode --------------------------------------------------------

    def _setup_vim(self) -> None:
        """Create the Vim contexts; they attach when ``vim-mode`` is on.

        The Markdown helper controller sits on the editor's scroller, so in
        Insert mode it still sees Return and ``]`` before Vim does.
        """
        im = GtkSource.VimIMContext.new()
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.set_im_context(im)
        self._vim_im = im
        self._vim_controller = controller
        self._signal_handlers.extend(
            [
                (im, im.connect("notify::command-bar-text", self._on_vim_text)),
                (im, im.connect("notify::command-text", self._on_vim_text)),
                (
                    self._buffer,
                    self._buffer.connect(
                        "notify::cursor-position", self._on_vim_cursor_moved
                    ),
                ),
                (im, im.connect("write", self._on_vim_write)),
                (im, im.connect("edit", self._on_vim_edit)),
                (im, im.connect("execute-command", self._on_vim_command)),
                (im, im.connect("format-text", self._on_vim_format)),
            ]
        )
        focus = Gtk.EventControllerFocus()
        self._signal_handlers.append(
            (focus, focus.connect("enter", self._on_editor_focus_enter))
        )
        self.editor_view.add_controller(focus)
        self._editor_focus_controller = focus
        self._preview_vim = preview_vim.PreviewVim(
            self._preview,
            prompt_revealer=self.preview_prompt_revealer,
            prompt_prefix=self.preview_prompt_prefix,
            prompt_entry=self.preview_prompt_entry,
            on_status=self._set_vim_status,
            on_focus=self._focus_vim_pane,
        )
        self._preview_focusable = self._preview.get_focusable()
        self._vim_mode_handler = self._settings.connect(
            "changed::vim-mode", self._on_vim_mode_changed
        )
        self._apply_vim_mode()

    @property
    def vim_enabled(self) -> bool:
        return self._vim_attached

    @property
    def vim_context(self) -> GtkSource.VimIMContext | None:
        return self._vim_im

    @property
    def preview_vim(self) -> preview_vim.PreviewVim | None:
        return self._preview_vim

    def _on_vim_mode_changed(self, *_args) -> None:
        self._apply_vim_mode()

    def _apply_vim_mode(self) -> None:
        enabled = self._settings.get_boolean("vim-mode")
        if self._vim_controller is None or self._vim_im is None:
            return
        if enabled and not self._vim_attached:
            # Setting the client widget enters Vim's Normal mode, which turns
            # on the block cursor. Only do that while Vim is actually active.
            self._vim_im.set_client_widget(self.editor_view)
            self.editor_view.add_controller(self._vim_controller)
            self._vim_attached = True
        elif not enabled and self._vim_attached:
            self._vim_attached = False
            self._vim_im.reset()
            self._vim_im.set_client_widget(None)
            self.editor_view.remove_controller(self._vim_controller)
            self.editor_view.set_overwrite(False)
        self._editor_ctrl_w = False
        self._preview.set_focusable(enabled or self._preview_focusable)
        if self._preview_vim is not None:
            self._preview_vim.set_enabled(enabled)
        self.label_vim.set_visible(enabled)
        self._set_vim_status("")
        if enabled:
            self._on_vim_text()

    def _vim_insert_mode(self) -> bool:
        """True when typing helpers should run: Vim off, or in Insert mode."""
        if not self._vim_attached or self._vim_im is None:
            return True
        text = self._vim_im.get_command_bar_text() or ""
        insert = GLib.dgettext("gtksourceview-5", "-- INSERT --")
        return text.startswith(insert) or text.startswith("-- INSERT --")

    def _on_vim_text(self, *_args) -> None:
        if self._vim_im is None or not self._vim_attached:
            return
        bar = self._vim_im.get_command_bar_text() or ""
        command = self._vim_im.get_command_text() or ""
        text = f"{bar} {command}".strip()
        if not text:
            text = self._vim_search_message or self._vim_search_status()
        self._set_vim_status(text)

    def _on_vim_cursor_moved(self, *_args) -> None:
        if self._vim_attached and self.editor_view.has_focus():
            self._vim_search_message = ""
            self._on_vim_text()

    # GtkSourceView's Vim search reports no match count, so mirror the last
    # "/" or "?" pattern in our own search context. Its search is regex and
    # case-sensitive, like Vim without ignorecase.

    def _capture_vim_search(self) -> None:
        """Called on Return, before Vim runs the command bar."""
        if self._vim_im is None:
            return
        bar = self._vim_im.get_command_bar_text() or ""
        if not bar or bar[0] not in "/?":
            return
        pattern = bar[1:]
        if not pattern and self._vim_search is not None:
            pattern = self._vim_search[1]
        if not pattern:
            return
        self._set_vim_search(bar[0], pattern)
        self._vim_search_check = True
        GLib.idle_add(self._check_vim_search)

    def _set_vim_search(self, prefix: str, pattern: str) -> None:
        if self._vim_search == (prefix, pattern):
            return
        self._vim_search = (prefix, pattern)
        settings = GtkSource.SearchSettings()
        settings.set_case_sensitive(True)
        settings.set_wrap_around(True)
        try:
            GLib.Regex.new(pattern, 0, 0)
            settings.set_regex_enabled(True)
        except GLib.Error:
            settings.set_regex_enabled(False)
        settings.set_search_text(pattern)
        self._drop_vim_search_context()
        context = GtkSource.SearchContext.new(self._buffer, settings)
        context.set_highlight(False)
        self._vim_search_context = context
        self._vim_search_handler = context.connect(
            "notify::occurrences-count", self._on_vim_occurrences
        )

    def _drop_vim_search_context(self) -> None:
        if self._vim_search_context is not None and self._vim_search_handler:
            self._vim_search_context.disconnect(self._vim_search_handler)
        self._vim_search_context = None
        self._vim_search_handler = 0

    def _on_vim_occurrences(self, *_args) -> None:
        if self._vim_search_check:
            self._check_vim_search()
        else:
            self._on_vim_text()

    def _check_vim_search(self) -> bool:
        context = self._vim_search_context
        if context is None or self._vim_search is None:
            return False
        count = context.get_occurrences_count()
        if count < 0:
            return False  # still scanning; notify::occurrences-count follows
        self._vim_search_check = False
        if count == 0:
            self._vim_search_message = _("Pattern not found: {pattern}").format(
                pattern=self._vim_search[1]
            )
        self._on_vim_text()
        return False

    def _vim_search_status(self) -> str:
        context = self._vim_search_context
        if (
            context is None
            or self._vim_search is None
            or self._vim_insert_mode()
        ):
            return ""
        count = context.get_occurrences_count()
        if count <= 0:
            return ""
        cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        found, start, end, _wrapped = context.forward(cursor)
        if not found or not start.equal(cursor):
            return ""
        position = context.get_occurrence_position(start, end)
        if position <= 0:
            return ""
        prefix, pattern = self._vim_search
        return f"{prefix}{pattern} [{position}/{count}]"

    def _on_editor_focus_enter(self, *_args) -> None:
        if self._vim_attached:
            self._on_vim_text()

    def _set_vim_status(self, text: str) -> None:
        self.label_vim.set_label(text)
        self.label_vim.set_tooltip_text(text or None)

    def _focus_vim_pane(self, target: str) -> None:
        """Move focus between panes, revealing a hidden pane first."""
        if target == "other":
            focus = self.get_focus()
            in_editor = focus is not None and (
                focus is self.editor_view or focus.is_ancestor(self.editor_view)
            )
            target = "preview" if in_editor else "editor"
        hidden = "preview" if target == "editor" else "source"
        if self._view_mode == hidden:
            self.set_view_mode("both", persist=False)
        if target == "editor":
            from_preview = self._view_mode == "both"
            self.focus_editor()
            if from_preview:
                # Let the paned allocate and sync-scroll settle, then put the
                # cursor where the reader is looking instead of jumping back
                # to the old cursor on the first keystroke.
                GLib.idle_add(self._place_cursor_at_view_top)
        else:
            self._preview.grab_focus()

    def _place_cursor_at_view_top(self, attempts: int = 10) -> bool:
        """Move the cursor to the top of what the editor shows.

        A pane that was just revealed may not be allocated yet; retry for a
        few frames, then fall back to the preview's scroll fraction.
        """
        if self._destroyed:
            return False
        rect = self.editor_view.get_visible_rect()
        if rect.height <= 0:
            if attempts > 0:
                GLib.timeout_add(16, self._place_cursor_at_view_top, attempts - 1)
                return False
            fraction = self._preview.get_scroll_fraction() or 0.0
            line = round(fraction * max(0, self._buffer.get_line_count() - 1))
            _found, target = self._buffer.get_iter_at_line(line)
            self._buffer.place_cursor(target)
            return False
        insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        _found, top = self.editor_view.get_iter_at_location(rect.x, rect.y)
        _found, bottom = self.editor_view.get_iter_at_location(
            rect.x, rect.y + rect.height - 1
        )
        if insert.in_range(top, bottom) or insert.equal(bottom):
            return False
        if not top.starts_line():
            top.forward_line()
        self._buffer.place_cursor(top)
        return False

    def _vim_file(self, path: str) -> Gio.File:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            directory = self._document.directory
            if directory is None:
                folder = self._initial_folder()
                directory = Path(folder.get_path()) if folder else Path.home()
            candidate = directory / candidate
        return Gio.File.new_for_path(str(candidate))

    def _on_vim_write(self, _im, _view, path: str | None) -> None:
        if path:
            self._document.set_text(self.get_text())
            self._write_file(self._vim_file(path), None)
        else:
            self.save_document()

    def _on_vim_edit(self, _im, _view, path: str | None) -> None:
        if path:
            self.open_file(self._vim_file(path))
        elif self._document.file is not None:
            self.open_file(self._document.file)

    def _on_vim_command(self, _im, command: str) -> bool:
        name = command.strip().lstrip(":").strip()
        if name in ("q", "quit", "clo", "close"):
            self.close()
            return True
        if name in ("q!", "quit!"):
            self._close_now()
            return True
        if name in ("wq", "x", "xit", "exit", "wq!", "x!"):
            if name.startswith("x") and not self._document.is_dirty:
                self.close()
            else:
                self.save_document_then(self._close_now)
            return True
        return False

    def _on_vim_format(self, _im, start: Gtk.TextIter, end: Gtk.TextIter) -> None:
        from . import formatting

        begin = start.copy()
        finish = end.copy()
        if begin.compare(finish) > 0:
            begin, finish = finish, begin
        begin.set_line_offset(0)
        if not finish.ends_line():
            finish.forward_to_line_end()
        source = self._buffer.get_text(begin, finish, True)
        formatted = formatting.format_markdown(
            source, ensure_final_newline=source.endswith("\n")
        )
        if formatted == source:
            return
        offset = begin.get_offset()
        self._apply_text_edit(
            editing.TextEdit(
                offset,
                finish.get_offset(),
                formatted,
                offset,
                offset,
            )
        )

    def _setup_spellcheck_idle(self) -> bool:
        self._spell_idle = 0
        if not self._destroyed:
            self._setup_spellcheck()
        return GLib.SOURCE_REMOVE

    def _setup_spellcheck(self) -> None:
        self.spell_adapter = spellcheck.create_adapter(self._buffer)
        if self.spell_adapter is None:
            return
        self.editor_view.set_extra_menu(
            self.spell_adapter.get_menu_model()
        )
        self.editor_view.insert_action_group("spelling", self.spell_adapter)
        self._apply_spell_settings()
        self._spell_handler = self._settings.connect(
            "changed::spell-check", self._on_spell_settings_changed
        )
        self._spell_language_handler = self._settings.connect(
            "changed::spell-language", self._on_spell_settings_changed
        )

    def _apply_spell_settings(self) -> None:
        if self.spell_adapter is None:
            return
        self.spell_adapter.set_enabled(
            self._settings.get_boolean("spell-check")
        )
        language = spellcheck.choose_language(
            spellcheck.language_choices(),
            self._settings.get_string("spell-language"),
        )
        if language:
            self.spell_adapter.set_language(language)

    def _on_spell_settings_changed(self, *_args) -> None:
        self._apply_spell_settings()

    def _setup_search(self) -> None:
        self._search_settings = GtkSource.SearchSettings()
        self._search_settings.set_wrap_around(True)
        self._search_context = GtkSource.SearchContext.new(
            self._buffer, self._search_settings
        )
        self._search_context.set_highlight(True)

    def _setup_window(self) -> None:
        width = self._settings.get_int("window-width")
        height = self._settings.get_int("window-height")
        if width >= 600 and height >= 400:
            self.set_default_size(width, height)

        position = self._settings.get_int("paned-position")
        if position > 0:
            self._saved_paned_position = position
            GLib.idle_add(self._apply_paned_position, position)

        style_manager = Adw.StyleManager.get_default()
        self._style_handler = style_manager.connect(
            "notify::dark", self._on_dark_changed
        )
        self._preview.set_dark(style_manager.get_dark())
        self._preview.set_remote_images(
            self._settings.get_boolean("remote-images")
        )
        self._settings_handler = self._settings.connect(
            "changed::remote-images", self._on_remote_images_changed
        )
        self._sync_scroll_handler = self._settings.connect(
            "changed::sync-scroll", self._on_sync_scroll_changed
        )
        self._recent_handler = self._settings.connect(
            "changed::recent-files", self._on_recent_changed
        )
        self._autosave_handler = self._settings.connect(
            "changed::autosave", self._on_autosave_settings_changed
        )
        self._autosave_interval_handler = self._settings.connect(
            "changed::autosave-interval",
            self._on_autosave_settings_changed,
        )
        self._refresh_recent_menu()

        mode = self._initial_view_mode or self._settings.get_string(
            "view-mode"
        )
        if mode not in VIEW_MODES:
            mode = "both"
        self._apply_view_mode(mode, persist=False)

    def _connect_signals(self) -> None:
        self._signal_handlers.extend(
            [
                (
                    self.editor_view,
                    self.editor_view.connect(
                        "preedit-changed", self._on_editor_preedit_changed
                    ),
                ),
                (
                    self._buffer,
                    self._buffer.connect("changed", self._on_buffer_changed),
                ),
                (
                    self._buffer,
                    self._buffer.connect(
                        "notify::cursor-position", self._on_cursor_moved
                    ),
                ),
                (
                    self._buffer,
                    self._buffer.connect("mark-set", self._on_mark_set),
                ),
                (self, self.connect("close-request", self._on_close_request)),
                (self, self.connect("destroy", self._on_destroy)),
                (self, self.connect("map", self._on_map)),
                (
                    self._preview,
                    self._preview.connect(
                        "scroll-fraction-changed", self._on_preview_scrolled
                    ),
                ),
                (
                    self.editor_scroller.get_vadjustment(),
                    self.editor_scroller.get_vadjustment().connect(
                        "value-changed", self._on_editor_scrolled
                    ),
                ),
            ]
        )
        self._connect_search_signals()

    def _connect_search_signals(self) -> None:
        self._signal_handlers.extend(
            [
                (
                    self.search_entry,
                    self.search_entry.connect(
                        "search-changed", self._on_search_changed
                    ),
                ),
                (
                    self.search_entry,
                    self.search_entry.connect(
                        "activate", self._find_next
                    ),
                ),
                (
                    self.search_entry,
                    self.search_entry.connect(
                        "next-match", self._find_next
                    ),
                ),
                (
                    self.search_entry,
                    self.search_entry.connect(
                        "previous-match", self._find_previous
                    ),
                ),
                (
                    self.search_entry,
                    self.search_entry.connect(
                        "stop-search", self._close_search
                    ),
                ),
                (
                    self.search_bar,
                    self.search_bar.connect(
                        "notify::search-mode-enabled",
                        self._on_search_mode_changed,
                    ),
                ),
                (
                    self.search_case_button,
                    self.search_case_button.connect(
                        "toggled", self._on_search_options_changed
                    ),
                ),
                (
                    self.search_word_button,
                    self.search_word_button.connect(
                        "toggled", self._on_search_options_changed
                    ),
                ),
                (
                    self.search_regex_button,
                    self.search_regex_button.connect(
                        "toggled", self._on_search_options_changed
                    ),
                ),
                (
                    self.search_wrap_button,
                    self.search_wrap_button.connect(
                        "toggled", self._on_search_options_changed
                    ),
                ),
                (
                    self.search_previous_button,
                    self.search_previous_button.connect(
                        "clicked", self._find_previous
                    ),
                ),
                (
                    self.search_next_button,
                    self.search_next_button.connect(
                        "clicked", self._find_next
                    ),
                ),
                (
                    self.replace_entry,
                    self.replace_entry.connect(
                        "activate", self._replace_current
                    ),
                ),
                (
                    self.replace_button,
                    self.replace_button.connect(
                        "clicked", self._replace_current
                    ),
                ),
                (
                    self.replace_all_button,
                    self.replace_all_button.connect(
                        "clicked", self._replace_all
                    ),
                ),
            ]
        )

    def _apply_editor_scheme(self) -> None:
        scheme_id = "Adwaita-dark" if Adw.StyleManager.get_default().get_dark() else "Adwaita"
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(scheme_id)
        if scheme is not None:
            self._buffer.set_style_scheme(scheme)

    def _apply_paned_position(self, position: int) -> bool:
        self.paned.set_position(position)
        return False

    def _on_map(self, *_args) -> None:
        if not self._focused_once:
            self._focused_once = True
            self.focus_primary_pane()

    # -- public API ------------------------------------------------------

    @property
    def document(self) -> Document:
        return self._document

    @property
    def settings(self) -> Gio.Settings:
        return self._settings

    def focus_editor(self) -> None:
        self.editor_view.grab_focus()

    @property
    def preview(self) -> MarkdownView:
        return self._preview

    def get_text(self) -> str:
        start, end = self._buffer.get_bounds()
        return self._buffer.get_text(start, end, True)

    def set_text(self, text: str) -> None:
        self._set_buffer_text(text)
        self._document.set_text(text)
        self._update_ui()
        self.flush_preview()

    def flush_preview(self) -> None:
        self._cancel_preview_timeout()
        self._render_preview()

    def load_demo_document(self) -> None:
        """Load the bundled demo as clean, untitled scratch content."""
        if (
            self._destroyed
            or self._document.is_dirty
            or self._document.file is not None
        ):
            return
        try:
            data = Gio.resources_lookup_data(
                "/io/github/james2doyle/MdSplit/demo.md",
                Gio.ResourceLookupFlags.NONE,
            )
            text = data.get_data().decode("utf-8")
        except (GLib.Error, UnicodeDecodeError):
            log.warning("Bundled demo document is unavailable")
            return

        self._invalidate_file_operations()
        self._document.load_demo(text)
        self._set_buffer_text(text)
        self._clear_recovery()
        self._note_session_file(None)
        self._update_ui()
        # Let the first frame paint before building the demo preview tree.
        GLib.idle_add(
            self._render_preview, priority=GLib.PRIORITY_DEFAULT_IDLE
        )
        self._buffer.place_cursor(self._buffer.get_start_iter())
        self.focus_primary_pane()

    def new_document(self) -> None:
        self._confirm_discard(
            _("Save changes before creating a new document?"),
            self._reset_document,
        )

    def open_document(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Open Markdown File"))
        dialog.set_accept_label(_("Open"))
        filters, default_filter = self._make_filters()
        dialog.set_filters(filters)
        dialog.set_default_filter(default_filter)
        folder = self._initial_folder()
        if folder is not None:
            dialog.set_initial_folder(folder)
        self._file_dialog = dialog
        dialog.open(self, None, self._on_open_finished)

    def open_file(self, file: Gio.File, on_loaded=None) -> None:
        if self._document.is_dirty:
            self._confirm_discard(
                _("Save changes before opening another file?"),
                lambda: self._load_file(file, on_loaded),
            )
        else:
            self._load_file(file, on_loaded)

    def save_document(self) -> None:
        self.save_document_then(None)

    def save_document_then(self, callback) -> None:
        self._document.set_text(self.get_text())
        if self._document.file is not None:
            self._write_file(self._document.file, callback)
        else:
            self._save_as(callback)

    def save_as_document(self) -> None:
        self._save_as(None)

    def copy_path(self) -> None:
        path = self._document.path_or_uri
        if path is None:
            self._show_toast(_("This document has no file path"))
            return
        clipboard = self.get_clipboard()
        clipboard.set_content(
            Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8",
                GLib.Bytes.new(path.encode("utf-8")),
            )
        )
        self._show_toast(_("Path copied"))

    def sync_copy_path_action(self) -> None:
        """Enable Copy Path only while the active document has a location."""
        # The application can make this window active before __init__ finishes.
        document = getattr(self, "_document", None)
        if document is None:
            return
        app = self.get_application()
        if app is None or app.props.active_window is not self:
            return
        action = app.lookup_action("copy-path")
        if action is not None:
            action.set_enabled(document.path_or_uri is not None)

    @property
    def view_mode(self) -> str:
        return self._view_mode

    def focus_primary_pane(self) -> None:
        if self._view_mode == "preview":
            self._preview.grab_focus()
        else:
            self.focus_editor()

    def set_view_mode(self, mode: str, *, persist: bool = True) -> None:
        if mode not in VIEW_MODES:
            mode = "both"
        self._apply_view_mode(mode, persist=persist)

    def _apply_view_mode(self, mode: str, *, persist: bool) -> None:
        if mode != "both" and self._view_mode == "both":
            position = self.paned.get_position()
            if position > 0:
                self._saved_paned_position = position
        previous = self._view_mode
        self._view_mode = mode

        source_visible = mode in ("both", "source")
        preview_visible = mode in ("both", "preview")
        preview_was_visible = previous in ("both", "preview")
        self.editor_pane.set_visible(source_visible)
        self.preview_container.set_visible(preview_visible)
        self._preview.set_paused(not preview_visible)
        if preview_visible:
            if not preview_was_visible:
                self.flush_preview()
            self._on_editor_scrolled()
        else:
            self._preview_text = None
            self._cancel_preview_timeout()

        if mode == "both" and previous != "both":
            position = self._saved_paned_position
            if position is not None:
                GLib.idle_add(self._apply_paned_position, position)
        if source_visible:
            self.focus_editor()
        elif preview_visible:
            self._preview.grab_focus()

        if persist:
            self._settings.set_string("view-mode", mode)
        app = self.get_application()
        if app is not None:
            app.sync_view_mode(mode)

    # -- editor signals --------------------------------------------------

    def _on_editor_preedit_changed(self, _view, preedit: str) -> None:
        self._editor_preedit = preedit

    def _on_buffer_changed(self, *_args) -> None:
        self._link_hover_revision = -1
        if self._loading:
            return
        self._document.set_text(self.get_text(), changed=True)
        self._update_ui(update_stats=False)
        self._schedule_stats()
        self._schedule_search_refresh()
        self._schedule_autosave()
        self._schedule_preview()

    def _on_cursor_moved(self, *_args) -> None:
        insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        line = insert.get_line() + 1
        column = insert.get_line_offset() + 1
        self.label_cursor.set_label(
            _("Ln {line}, Col {column}").format(line=line, column=column)
        )

    def _on_mark_set(self, _buffer, _location, mark) -> None:
        if self._loading:
            return
        if mark.get_name() in ("insert", "selection-bound"):
            self._schedule_stats()

    def _on_key_pressed(
        self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType
    ) -> bool:
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if self._vim_attached:
            self._vim_search_message = ""
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._capture_vim_search()
        if self._vim_attached and self._handle_vim_window_keys(keyval, modifiers):
            return True
        if not self._vim_insert_mode():
            return False
        blocked = (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if modifiers & blocked or self._editor_preedit:
            return False

        codepoint = Gdk.keyval_to_unicode(keyval)
        character = chr(codepoint) if codepoint else ""
        bounds = self._buffer.get_selection_bounds()
        if (
            not bounds
            and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
            and not (state & Gdk.ModifierType.SHIFT_MASK)
        ):
            insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            edit = editing.continue_list(self.get_text(), insert.get_offset())
            if edit is not None:
                self._apply_text_edit(edit)
                return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            start, end = self._selection_or_cursor_offsets()
            edit = editing.indent_list(
                self.get_text(),
                start,
                end,
                self.editor_view.get_indent_width(),
                outdent=(
                    keyval == Gdk.KEY_ISO_Left_Tab
                    or bool(state & Gdk.ModifierType.SHIFT_MASK)
                ),
            )
            if edit is not None:
                self._apply_text_edit(edit)
                return True
        if bounds:
            start_iter, end_iter = bounds
            if character in editing.SURROUND_PAIRS:
                edit = editing.surround_selection(
                    self.get_text(),
                    start_iter.get_offset(),
                    end_iter.get_offset(),
                    character,
                )
                if edit is not None:
                    self._apply_text_edit(edit)
                    return True
            return False

        auto_pairing = self._settings.get_boolean("auto-pairing")
        if auto_pairing and keyval == Gdk.KEY_BackSpace:
            insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            edit = editing.delete_empty_pair(
                self.get_text(), insert.get_offset()
            )
            if edit is not None:
                self._apply_text_edit(edit)
                return True

        if (
            keyval == Gdk.KEY_bracketright
            and self._settings.get_boolean("autocomplete-links")
        ):
            insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            edit = editing.complete_link(
                self.get_text(), insert.get_offset()
            )
            if edit is not None:
                self._apply_text_edit(edit)
                return True

        if auto_pairing and character in editing.AUTO_PAIR_CHARACTERS:
            text = self.get_text()
            insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            cursor = insert.get_offset()
            if editing.skip_closing_pair(text, cursor, character):
                self._buffer.place_cursor(
                    self._buffer.get_iter_at_offset(cursor + 1)
                )
                self.editor_view.scroll_mark_onscreen(self._buffer.get_insert())
                return True
            edit = editing.auto_pair(text, cursor, character)
            if edit is not None:
                self._apply_text_edit(edit)
                return True
        return False

    def _handle_vim_window_keys(
        self, keyval: int, modifiers: Gdk.ModifierType
    ) -> bool:
        """Handle Ctrl+W pane switching from the editor's Normal mode."""
        ctrl = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)
        name = preview_vim.key_name(keyval)
        if self._editor_ctrl_w:
            if name in ("Shift_L", "Shift_R", "Control_L", "Control_R"):
                return False
            self._editor_ctrl_w = False
            if name in ("w", "W", "p", "l") or (ctrl and name in ("w", "l")):
                self._focus_vim_pane("preview")
            return True
        if ctrl and name in ("w", "W") and not self._vim_insert_mode():
            self._editor_ctrl_w = True
            return True
        return False

    # -- line operations -------------------------------------------------

    def _selection_or_cursor_offsets(self) -> tuple[int, int]:
        bounds = self._buffer.get_selection_bounds()
        if bounds:
            start_iter, end_iter = bounds
            return start_iter.get_offset(), end_iter.get_offset()
        insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        return insert.get_offset(), insert.get_offset()

    def move_lines(self, direction: int) -> None:
        self._focus_authoring_editor()
        start, end = self._selection_or_cursor_offsets()
        edit = editing.move_lines(self.get_text(), start, end, direction)
        if edit is not None:
            self._apply_text_edit(edit)

    def duplicate_line_or_selection(self) -> None:
        self._focus_authoring_editor()
        start, end = self._selection_or_cursor_offsets()
        self._apply_text_edit(
            editing.duplicate_line_or_selection(self.get_text(), start, end)
        )

    def toggle_task(self) -> None:
        self._focus_authoring_editor()
        insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        edit = editing.toggle_task(self.get_text(), insert.get_offset())
        if edit is not None:
            self._apply_text_edit(edit)

    def _focus_authoring_editor(self) -> None:
        if self._view_mode == "preview":
            self._apply_view_mode("both", persist=False)
        self.focus_editor()

    def insert_link(self) -> None:
        self._focus_authoring_editor()
        start, end = self._selection_or_cursor_offsets()
        self._apply_text_edit(editing.insert_link(self.get_text(), start, end))

    def show_image_picker(self) -> None:
        self._focus_authoring_editor()
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Insert Image"))
        dialog.set_accept_label(_("Insert"))
        image_filter = Gtk.FileFilter()
        image_filter.set_name(_("Images"))
        image_filter.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(image_filter)
        folder = self._initial_folder()
        if folder is not None:
            dialog.set_initial_folder(folder)
        self._file_dialog = dialog
        dialog.open(self, None, self._on_image_picked)

    def _on_image_picked(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error as error:
            if not self._is_dismissed(error):
                self._show_error(_("Could not open image"), error.message)
            return
        if file is not None:
            self.insert_image_file(file)

    def _image_context(self):
        start, end = self._selection_or_cursor_offsets()
        return (
            self._document_epoch,
            self._document.revision,
            start,
            end,
            self._document.directory,
        )

    def insert_image_file(self, file: Gio.File) -> None:
        path = file.get_path()
        if path is None:
            self._show_error(
                _("Could not insert image"), _("Choose a local image file.")
            )
            return
        context = self._image_context()
        image_insert.prepare_file_image_async(
            Path(path),
            context[4],
            lambda prepared, error: self._finish_image_insert(
                context, prepared, error
            ),
        )

    def paste_image(self) -> None:
        self._focus_authoring_editor()
        context = self._image_context()
        clipboard = self.get_clipboard()
        clipboard.read_texture_async(
            None, self._on_clipboard_texture, context
        )

    def _on_clipboard_texture(self, clipboard, result, context) -> None:
        if self._destroyed or context[0] != self._document_epoch:
            return
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as error:
            self._show_image_error(error, clipboard=True)
            return
        if texture is None:
            self._show_image_error(
                ImageValidationError(), clipboard=True
            )
            return
        image_insert.prepare_clipboard_image_async(
            texture,
            context[4],
            lambda prepared, error: self._finish_image_insert(
                context, prepared, error, clipboard=True
            ),
        )

    def _finish_image_insert(
        self,
        context,
        prepared: Path | None,
        error: Exception | None,
        *,
        clipboard: bool = False,
    ) -> None:
        if self._destroyed or context[0] != self._document_epoch:
            return
        if error is not None:
            self._show_image_error(error, clipboard=clipboard)
            return
        if context[1] != self._document.revision:
            self._show_toast(_("Document changed; image was not inserted"))
            return
        if prepared is None:
            self._show_image_error(
                ImageValidationError(), clipboard=clipboard
            )
            return
        _epoch, _revision, start, end, directory = context
        destination = markdown_image_path(prepared, directory)
        self._apply_text_edit(
            editing.insert_image(self.get_text(), start, end, destination)
        )

    def _show_image_error(self, error: Exception, *, clipboard: bool) -> None:
        if isinstance(error, ImageTooLargeError):
            message = _(
                "Images must be at most 10 MiB and 20 megapixels."
            )
        elif clipboard:
            message = _("Clipboard does not contain a supported image.")
        elif isinstance(error, ImageValidationError):
            message = _("The selected file is not a supported image.")
        else:
            message = str(error)
        self._show_error(_("Could not insert image"), message)

    def _on_image_drop(self, _target, value, x: float, y: float) -> bool:
        try:
            files = value.get_boxed().get_files()
        except (AttributeError, TypeError):
            return False
        if not files:
            return False
        offset = self._editor_offset_at(x, y)
        if offset is not None:
            self._buffer.place_cursor(self._buffer.get_iter_at_offset(offset))
        self.insert_image_file(files[0])
        return True

    def show_go_to_line(self) -> None:
        if self._go_to_line_dialog is not None:
            self._go_to_line_dialog.present()
            return
        if self._view_mode == "preview":
            self._apply_view_mode("both", persist=False)

        dialog = Gtk.Dialog(
            title=_("Go to Line"), transient_for=self, modal=True
        )
        dialog.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        go_button = dialog.add_button(_("_Go"), Gtk.ResponseType.ACCEPT)
        go_button.add_css_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)

        content = dialog.get_content_area()
        content.set_spacing(6)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        line_count = self._buffer.get_line_count()
        label = Gtk.Label(
            label=_("Line number (1–{count})").format(count=line_count),
            xalign=0.0,
        )
        content.append(label)
        entry = Gtk.Entry()
        entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        entry.set_activates_default(True)
        entry.set_hexpand(True)
        insert = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        entry.set_text(str(insert.get_line() + 1))
        content.append(entry)
        error = Gtk.Label(xalign=0.0)
        error.set_wrap(True)
        error.add_css_class("error")
        error.set_visible(False)
        content.append(error)

        def on_response(current, response: Gtk.ResponseType) -> None:
            if response == Gtk.ResponseType.ACCEPT:
                target = editing.parse_line_target(entry.get_text())
                if target is None:
                    count = self._buffer.get_line_count()
                    error.set_label(
                        _("Enter a line number from 1 to {count}.").format(
                            count=count
                        )
                    )
                    error.set_visible(True)
                    entry.grab_focus()
                    entry.select_region(0, -1)
                    return
                self._go_to_line_dialog = None
                current.destroy()
                self.go_to_line(target)
                return
            self._go_to_line_dialog = None
            current.destroy()
            if not self._destroyed:
                self.focus_editor()

        def on_destroy(current) -> None:
            if self._go_to_line_dialog is current:
                self._go_to_line_dialog = None

        dialog.connect("response", on_response)
        dialog.connect("destroy", on_destroy)
        self._go_to_line_dialog = dialog
        dialog.present()
        entry.grab_focus()
        entry.select_region(0, -1)

    def go_to_line(self, line: int) -> None:
        if self._view_mode == "preview":
            self._apply_view_mode("both", persist=False)
        line_count = self._buffer.get_line_count()
        target = max(1, min(line, line_count))
        _found, target_iter = self._buffer.get_iter_at_line(target - 1)
        self._buffer.place_cursor(target_iter)
        self.focus_editor()
        self.editor_view.scroll_mark_onscreen(self._buffer.get_insert())

    def format_selection(self, kind: str) -> None:
        text = self.get_text()
        start, end = self._selection_or_cursor_offsets()

        markers = {
            "bold": ("**", "**"),
            "italic": ("*", "*"),
            "underline": ("<u>", "</u>"),
        }
        if kind in markers:
            edit = editing.format_inline(text, start, end, *markers[kind])
        elif kind == "blockquote":
            edit = editing.toggle_blockquote(text, start, end)
        elif kind == "code":
            edit = editing.format_code(text, start, end)
        else:
            return
        self._apply_text_edit(edit)

    def _apply_text_edit(self, edit: editing.TextEdit) -> None:
        self._buffer.begin_user_action()
        try:
            self._buffer.delete(
                self._buffer.get_iter_at_offset(edit.start),
                self._buffer.get_iter_at_offset(edit.end),
            )
            self._buffer.insert(
                self._buffer.get_iter_at_offset(edit.start),
                edit.replacement,
            )
            if edit.selection_start == edit.selection_end:
                self._buffer.place_cursor(
                    self._buffer.get_iter_at_offset(edit.selection_start)
                )
            else:
                self._buffer.select_range(
                    self._buffer.get_iter_at_offset(edit.selection_start),
                    self._buffer.get_iter_at_offset(edit.selection_end),
                )
        finally:
            self._buffer.end_user_action()
        self.editor_view.scroll_mark_onscreen(self._buffer.get_insert())

    # -- formatting ------------------------------------------------------

    def format_document(self) -> None:
        """Format the selection, or the whole document when nothing is selected."""
        self._format_scope(confirm=True, notify=True, selection=True)

    def _format_scope(
        self, *, confirm: bool, notify: bool, selection: bool
    ) -> bool:
        from . import formatting

        bounds = self._buffer.get_selection_bounds() if selection else None
        if bounds:
            source = self._buffer.get_text(bounds[0], bounds[1], True)
            start = bounds[0].get_offset()
            end = bounds[1].get_offset()
            scope = "selection"
        else:
            source = self.get_text()
            start = 0
            end = self._buffer.get_char_count()
            scope = "document"

        formatted = formatting.format_markdown(
            source,
            ensure_final_newline=(
                scope == "document" or source.endswith("\n")
            ),
        )
        if formatted == source:
            if notify:
                self._show_toast(
                    _("Selection is already formatted")
                    if scope == "selection"
                    else _("Document is already formatted")
                )
            return False

        summary = formatting.summarize_changes(source, formatted)
        if confirm and summary.changed_lines > formatting.CONFIRM_CHANGE_LINES:
            self._confirm_format(scope, source, formatted, summary, start, end)
            return False
        self._apply_format(scope, formatted, start, end, notify=notify)
        return True

    def _confirm_format(
        self,
        scope: str,
        source: str,
        formatted: str,
        summary: formatting.ChangeSummary,
        start: int,
        end: int,
    ) -> None:
        if self._format_dialog is not None:
            self._format_dialog.present(self)
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading(
            _("Format Selection")
            if scope == "selection"
            else _("Format Document")
        )
        dialog.set_body(
            ngettext(
                "{n} line will change.",
                "{n} lines will change.",
                summary.changed_lines,
            ).format(n=summary.changed_lines)
        )
        preview = Gtk.Label(label=summary.preview)
        preview.set_xalign(0.0)
        preview.set_wrap(False)
        preview.add_css_class("monospace")
        preview.add_css_class("caption")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(180)
        scroller.set_min_content_width(420)
        scroller.set_has_frame(True)
        scroller.set_child(preview)
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("format", _("Format"))
        dialog.set_response_appearance(
            "format", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("format")
        dialog.set_close_response("cancel")
        revision = self._document.revision

        def on_response(_dialog, response: str) -> None:
            self._format_dialog = None
            if response != "format":
                return
            if self._destroyed or self._document.revision != revision:
                self._show_toast(
                    _("Document changed; run Format again")
                )
                return
            self._apply_format(
                scope, formatted, start, end, notify=True
            )

        dialog.connect("response", on_response)
        self._format_dialog = dialog
        dialog.present(self)

    def _apply_format(
        self,
        scope: str,
        formatted: str,
        start: int,
        end: int,
        *,
        notify: bool,
    ) -> None:
        total = self._buffer.get_char_count()
        start = min(max(0, start), total)
        end = min(max(start, end), total)
        insert = self._buffer.get_iter_at_mark(
            self._buffer.get_insert()
        ).get_offset()
        if scope == "selection":
            selection_start = start
            selection_end = start + len(formatted)
        else:
            if insert <= start:
                mapped = insert
            elif insert >= end:
                mapped = insert - (end - start) + len(formatted)
            else:
                mapped = start + len(formatted)
            selection_start = selection_end = min(
                mapped, total - (end - start) + len(formatted)
            )
        self._apply_text_edit(
            editing.TextEdit(
                start,
                end,
                formatted,
                selection_start,
                selection_end,
            )
        )
        if notify:
            self._show_toast(
                _("Formatted selection")
                if scope == "selection"
                else _("Formatted document")
            )

    # -- search ----------------------------------------------------------

    def show_find(self) -> None:
        self._show_search(replace=False)

    def show_replace(self) -> None:
        self._show_search(replace=True)

    def find_next(self) -> None:
        self._find_next()

    def find_previous(self) -> None:
        self._find_previous()

    def _show_search(self, *, replace: bool) -> None:
        if self._view_mode == "preview":
            self._search_previous_mode = "preview"
            self._apply_view_mode("both", persist=False)
        self.search_bar.set_search_mode(True)
        self.replace_row.set_visible(replace)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)
        self._refresh_search()
        self._update_placeholder()

    def _close_search(self, *_args) -> None:
        if not self.search_bar.get_search_mode():
            self.focus_editor()
            return
        self.search_bar.set_search_mode(False)

    def _on_search_mode_changed(self, _bar, _pspec) -> None:
        if not self.search_bar.get_search_mode():
            self._cancel_search_timeout()
            self._search_context.set_highlight(False)
            self.replace_row.set_visible(False)
            self.search_status_label.set_label("")
            if self._search_previous_mode is not None:
                previous = self._search_previous_mode
                self._search_previous_mode = None
                self._apply_view_mode(previous, persist=False)
            else:
                self.focus_editor()
        self._update_placeholder()

    def _on_search_changed(self, *_args) -> None:
        count = self._refresh_search()
        if count and self.search_entry.get_text():
            # Keep the current match as the anchor while the query is refined,
            # otherwise every keystroke would advance to a later match.
            self._search(forward=True, advance=False)

    def _on_search_options_changed(self, *_args) -> None:
        self._refresh_search()

    def _search_query(self) -> editing.SearchQuery:
        return editing.SearchQuery(
            self.search_entry.get_text(),
            match_case=self.search_case_button.get_active(),
            whole_word=self.search_word_button.get_active(),
            regex=self.search_regex_button.get_active(),
        )

    def _sync_search_settings(self) -> None:
        query = self._search_query()
        self._search_settings.set_search_text(query.text)
        self._search_settings.set_case_sensitive(query.match_case)
        self._search_settings.set_regex_enabled(query.regex)
        self._search_settings.set_at_word_boundaries(query.whole_word)
        self._search_settings.set_wrap_around(
            self.search_wrap_button.get_active()
        )

    def _refresh_search(self) -> int:
        self._sync_search_settings()
        query = self._search_query()
        if not query.text:
            self.search_status_label.set_label("")
            self._search_context.set_highlight(False)
            return 0
        if query.regex and editing.compile_search(query) is None:
            self.search_status_label.set_label(
                _("Invalid regular expression")
            )
            self._search_context.set_highlight(False)
            return 0
        count = editing.count_matches(self.get_text(), query)
        self.search_status_label.set_label(
            ngettext("{n} match", "{n} matches", count).format(n=count)
        )
        self._search_context.set_highlight(count > 0)
        return count

    def _prepare_search(self) -> bool:
        if not self.search_bar.get_search_mode():
            self.show_find()
        self._sync_search_settings()
        query = self._search_query()
        if not query.text:
            return False
        if query.regex and editing.compile_search(query) is None:
            self.search_status_label.set_label(
                _("Invalid regular expression")
            )
            self._search_context.set_highlight(False)
            return False
        return True

    def _select_search_match(self, start, end) -> None:
        self._buffer.select_range(start, end)
        self.editor_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)

    def _find_next(self, *_args) -> None:
        self._search(forward=True)

    def _find_previous(self, *_args) -> None:
        self._search(forward=False)

    def _search(self, *, forward: bool, advance: bool = True) -> None:
        if not self._prepare_search():
            return
        bounds = self._buffer.get_selection_bounds()
        if bounds:
            # Explicit next/previous must move past the selection; searching
            # from its start would select the same match again. Refining a
            # query stays anchored to the start so typing does not skip ahead.
            anchor = bounds[1] if forward and advance else bounds[0]
        else:
            anchor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        if forward:
            found, start, end, _wrapped = self._search_context.forward(anchor)
        else:
            found, start, end, _wrapped = self._search_context.backward(anchor)
        if found:
            self._select_search_match(start, end)

    def _replace_current(self, *_args) -> None:
        if not self._prepare_search():
            return
        bounds = self._buffer.get_selection_bounds()
        if not bounds:
            self._find_next()
            bounds = self._buffer.get_selection_bounds()
            if not bounds:
                return
        start, end = bounds
        replacement = self.replace_entry.get_text()
        edit = editing.TextEdit(
            start.get_offset(),
            end.get_offset(),
            replacement,
            start.get_offset() + len(replacement),
            start.get_offset() + len(replacement),
        )
        self._apply_text_edit(edit)
        self._refresh_search()
        self._find_next()

    def _replace_all(self, *_args) -> None:
        query = self._search_query()
        if not query.text:
            return
        if query.regex and editing.compile_search(query) is None:
            self._refresh_search()
            return
        old_text = self.get_text()
        new_text, count = editing.replace_all_text(
            old_text, query, self.replace_entry.get_text()
        )
        if count == 0:
            self._show_toast(_("No matches to replace"))
            return
        offset = min(
            self._buffer.get_iter_at_mark(self._buffer.get_insert()).get_offset(),
            len(new_text),
        )
        self._apply_text_edit(
            editing.TextEdit(0, len(old_text), new_text, offset, offset)
        )
        self._show_toast(
            ngettext(
                "Replaced {n} match", "Replaced {n} matches", count
            ).format(n=count)
        )
        self._refresh_search()

    def _schedule_search_refresh(self) -> None:
        if not self.search_bar.get_search_mode():
            return
        self._cancel_search_timeout()
        self._search_timeout = GLib.timeout_add(
            150, self._refresh_search_now
        )

    def _refresh_search_now(self) -> bool:
        self._search_timeout = 0
        if not self._destroyed and self.search_bar.get_search_mode():
            self._refresh_search()
        return False

    def _cancel_search_timeout(self) -> None:
        if self._search_timeout:
            GLib.source_remove(self._search_timeout)
            self._search_timeout = 0

    def _on_dark_changed(self, *_args) -> None:
        self._apply_editor_scheme()
        self._preview.set_dark(Adw.StyleManager.get_default().get_dark())

    def _on_remote_images_changed(self, settings, _key) -> None:
        self._preview.set_remote_images(settings.get_boolean("remote-images"))
        if self.preview_container.get_visible():
            self.flush_preview()

    def _on_sync_scroll_changed(self, _settings, _key) -> None:
        self._on_editor_scrolled()

    def _on_editor_scrolled(self, *_args) -> None:
        if (
            self._scroll_guard
            or not self._settings.get_boolean("sync-scroll")
            or not self.preview_container.get_visible()
        ):
            return
        adjustment = self.editor_scroller.get_vadjustment()
        if adjustment is None:
            return
        fraction = _adjustment_fraction(adjustment)
        if fraction is None:
            return
        self._scroll_guard = True
        try:
            self._preview.set_scroll_fraction(fraction)
        finally:
            self._scroll_guard = False

    def _on_preview_scrolled(self, _view, fraction: float) -> None:
        if (
            self._scroll_guard
            or not self._settings.get_boolean("sync-scroll")
            or not self.preview_container.get_visible()
        ):
            return
        adjustment = self.editor_scroller.get_vadjustment()
        if adjustment is None:
            return
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        if maximum <= 0:
            return
        self._scroll_guard = True
        try:
            adjustment.set_value(
                max(0.0, min(1.0, fraction)) * maximum
            )
        finally:
            self._scroll_guard = False

    # -- UI updates ------------------------------------------------------

    def _update_ui(
        self, render_preview: bool = False, update_stats: bool = True
    ) -> None:
        name = self._document.display_name
        dirty_marker = " \u2022" if self._document.is_dirty else ""
        self.window_title.set_title(f"{name}{dirty_marker}")
        self.window_title.set_subtitle(self._document.location_subtitle)
        self.set_title(f"{name}{dirty_marker} \u2014 MD Split")
        if update_stats:
            self._cancel_stats_timeout()
            self._update_stats()
        self._update_placeholder()
        self._on_cursor_moved()
        self.sync_copy_path_action()
        if render_preview:
            self.flush_preview()

    def _update_stats(self) -> None:
        bounds = self._buffer.get_selection_bounds()
        if bounds:
            text = self._buffer.get_text(bounds[0], bounds[1], True)
            selected = True
        else:
            text = self._document.text
            selected = False

        characters = len(text)
        character_text = ngettext(
            "{n} character", "{n} characters", characters
        ).format(n=characters)
        if characters > _MAX_WORD_COUNT_CHARS:
            if selected:
                message = _(
                    "Selection: {characters} \u00b7 "
                    "word count paused for large selection"
                )
            else:
                message = _(
                    "{characters} \u00b7 word count paused for large document"
                )
            self.label_stats.set_label(message.format(characters=character_text))
            return

        words, _characters, minutes = text_statistics(text)
        word_text = ngettext("{n} word", "{n} words", words).format(n=words)
        minute_text = ngettext(
            "{n} minute read", "{n} minutes read", minutes
        ).format(n=minutes)
        stats = f"{word_text} \u00b7 {character_text} \u00b7 {minute_text}"
        if selected:
            stats = _("Selection: {stats}").format(stats=stats)
        self.label_stats.set_label(stats)

    def _update_placeholder(self) -> None:
        self.placeholder_label.set_visible(
            self._buffer.get_char_count() == 0
            and not self.search_bar.get_search_mode()
        )

    def _schedule_stats(self) -> None:
        self._cancel_stats_timeout()
        self._stats_timeout = GLib.timeout_add(120, self._update_stats_now)

    def _cancel_stats_timeout(self) -> None:
        if self._stats_timeout:
            GLib.source_remove(self._stats_timeout)
            self._stats_timeout = 0

    def _update_stats_now(self) -> bool:
        self._stats_timeout = 0
        if not self._destroyed:
            self._update_stats()
        return False

    def _schedule_preview(self) -> None:
        self._cancel_preview_timeout()
        if not self.preview_container.get_visible():
            return
        # Full re-renders block the main thread, so large documents debounce
        # longer instead of rebuilding on nearly every keystroke.
        interval = _preview_interval(len(self._document.text))
        self._preview_timeout = GLib.timeout_add(interval, self._render_preview)

    def _cancel_preview_timeout(self) -> None:
        if self._preview_timeout:
            GLib.source_remove(self._preview_timeout)
            self._preview_timeout = 0

    def _render_preview(self) -> bool:
        self._preview_timeout = 0
        if not self.preview_container.get_visible() or self._destroyed:
            return False
        text = self._document.text
        if text == self._preview_text:
            return False
        self._preview_text = text
        self._preview.set_markdown(
            text,
            self._document.directory,
            base_file=self._document.file,
        )
        return False

    def _set_buffer_text(self, text: str) -> None:
        self._loading = True
        self._buffer.set_text(text)
        self._loading = False

    def _reset_document(self) -> None:
        self._invalidate_file_operations()
        self._document.reset()
        self._set_buffer_text("")
        self._clear_recovery()
        self._note_session_file(None)
        self._update_ui(render_preview=True)
        self.focus_primary_pane()

    # -- file operations -------------------------------------------------

    def _make_filters(self):
        markdown_filter = Gtk.FileFilter()
        markdown_filter.set_name(_("Markdown files"))
        for pattern in ("*.md", "*.markdown", "*.mdown", "*.mkd"):
            markdown_filter.add_pattern(pattern)
        markdown_filter.add_mime_type("text/markdown")
        markdown_filter.add_mime_type("text/x-markdown")

        all_filter = Gtk.FileFilter()
        all_filter.set_name(_("All files"))
        all_filter.add_pattern("*")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(markdown_filter)
        filters.append(all_filter)
        return filters, markdown_filter

    def _initial_folder(self) -> Gio.File | None:
        directory = self._document.directory
        if directory is not None and directory.is_dir():
            return Gio.File.new_for_path(str(directory))
        last_folder = self._settings.get_string("last-folder")
        if last_folder and Path(last_folder).is_dir():
            return Gio.File.new_for_path(last_folder)
        return None

    def _remember_folder(self, file: Gio.File) -> None:
        parent = file.get_parent()
        path = parent.get_path() if parent is not None else None
        if path:
            self._settings.set_string("last-folder", path)

    def _refresh_recent_menu(self) -> None:
        menu = Gio.Menu()
        section = Gio.Menu()
        paths = self._settings.get_strv("recent-files")
        for path in paths:
            item = Gio.MenuItem.new(Path(path).name, None)
            item.set_action_and_target_value(
                "app.open-recent", GLib.Variant.new_string(path)
            )
            section.append_item(item)
        if not paths:
            section.append_item(Gio.MenuItem.new(_("No Recent Files"), None))
        section.append_item(
            Gio.MenuItem.new(_("Clear Recent"), "app.clear-recent")
        )
        menu.append_section(None, section)
        self.button_recent.set_menu_model(menu)

    def _on_recent_changed(self, *_args) -> None:
        self._refresh_recent_menu()

    def _record_recent(self, file: Gio.File) -> None:
        path = file.get_path()
        if path is None:
            return
        recent = session.add_recent(
            self._settings.get_strv("recent-files"), path
        )
        self._settings.set_strv("recent-files", recent)
        uri = file.get_uri()
        if uri:
            try:
                Gtk.RecentManager.get_default().add_item(uri)
            except Exception as error:  # pragma: no cover - optional session
                log.debug("Could not update recent manager: %s", error)

    def _note_session_file(self, file: Gio.File | None) -> None:
        app = self.get_application()
        if app is None or not hasattr(app, "note_window_file"):
            return
        path = file.get_path() if file is not None else None
        app.note_window_file(self, path)

    def _on_open_finished(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error as error:
            if not self._is_dismissed(error):
                self._show_error(_("Could not open file"), error.message)
            return
        if file is not None:
            self.open_file(file)

    def _save_as(self, callback) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Save Markdown File"))
        dialog.set_accept_label(_("Save"))
        filters, default_filter = self._make_filters()
        dialog.set_filters(filters)
        dialog.set_default_filter(default_filter)
        dialog.set_initial_name(self._document.display_name)
        folder = self._initial_folder()
        if folder is not None:
            dialog.set_initial_folder(folder)
        self._file_dialog = dialog
        dialog.save(self, None, self._on_save_as_finished, callback)

    def _on_save_as_finished(self, dialog: Gtk.FileDialog, result, callback) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error as error:
            if not self._is_dismissed(error):
                self._show_error(_("Could not save file"), error.message)
            return
        if file is not None:
            self._write_file(file, callback)

    def export_html(self) -> None:
        self._open_export_dialog(
            _("Export HTML"),
            "html",
            _("HTML files"),
            "*.html",
            "text/html",
            self._on_export_html_finished,
        )

    def export_pdf(self) -> None:
        self._open_export_dialog(
            _("Export PDF"),
            "pdf",
            _("PDF files"),
            "*.pdf",
            "application/pdf",
            self._on_export_pdf_finished,
        )

    def _open_export_dialog(
        self,
        title: str,
        extension: str,
        filter_name: str,
        pattern: str,
        mime_type: str,
        callback,
    ) -> None:
        file_filter = Gtk.FileFilter()
        file_filter.set_name(filter_name)
        file_filter.add_pattern(pattern)
        file_filter.add_mime_type(mime_type)
        all_filter = Gtk.FileFilter()
        all_filter.set_name(_("All files"))
        all_filter.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        filters.append(all_filter)

        dialog = Gtk.FileDialog()
        dialog.set_title(title)
        dialog.set_accept_label(_("Export"))
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        stem = Path(self._document.display_name).stem
        dialog.set_initial_name(f"{stem}.{extension}")
        folder = self._initial_folder()
        if folder is not None:
            dialog.set_initial_folder(folder)
        self._file_dialog = dialog
        dialog.save(self, None, callback)

    def _finish_export_dialog(
        self, dialog: Gtk.FileDialog, result, heading: str, extension: str
    ) -> Gio.File | None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error as error:
            if not self._is_dismissed(error):
                self._show_error(heading, error.message)
            return None
        if file is None:
            return None
        path = file.get_path()
        if path is None:
            self._show_error(
                heading, _("Choose a local file for the export.")
            )
            return None
        target = ensure_file_extension(Path(path), extension)
        return Gio.File.new_for_path(str(target))

    def _confirm_export_overwrite(
        self, file: Gio.File, on_accept
    ) -> None:
        """Run ``on_accept`` after confirming an existing target is replaced."""
        if not file.query_exists(None):
            on_accept(file)
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Replace existing file?"))
        source = self._document.file
        if source is not None and source.equal(file):
            dialog.set_body(
                _("This will replace the document you are editing.")
            )
        else:
            dialog.set_body(
                _("“{name}” already exists.").format(
                    name=file.get_basename() or file.get_parse_name()
                )
            )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("replace", _("Replace"))
        dialog.set_response_appearance(
            "replace", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            self._confirm_dialog = None
            if response == "replace":
                on_accept(file)

        dialog.connect("response", on_response)
        self._confirm_dialog = dialog
        dialog.present(self)

    def _on_export_html_finished(
        self, dialog: Gtk.FileDialog, result
    ) -> None:
        file = self._finish_export_dialog(
            dialog,
            result,
            _("Could not export HTML"),
            "html",
        )
        if file is None:
            return
        self._confirm_export_overwrite(
            file,
            self._export_html_to,
        )

    def _export_html_to(self, file: Gio.File) -> None:
        try:
            from . import exporting
        except Exception as error:  # pragma: no cover - optional Cairo
            self._show_error(_("Could not export HTML"), str(error))
            return
        text = self.get_text()
        title = Path(self._document.display_name).stem
        base_dir = self._document.directory
        base_file = self._document.file

        def produce() -> None:
            try:
                content = exporting.render_html(
                    text,
                    title=title,
                    base_dir=base_dir,
                    base_file=base_file,
                )
            except Exception as error:  # pragma: no cover - reported to user
                GLib.idle_add(self._export_failed, "HTML", error)
                return
            GLib.idle_add(self._write_export_html, file, content)

        threading.Thread(
            target=produce,
            daemon=True,
            name="md-split-export-html",
        ).start()

    def _write_export_html(self, file: Gio.File, content: str) -> bool:
        if self._destroyed:
            return False
        fileio.save_text_async(
            file,
            content,
            lambda error, _etag: self._on_export_written("HTML", error),
        )
        return False

    def _on_export_pdf_finished(
        self, dialog: Gtk.FileDialog, result
    ) -> None:
        file = self._finish_export_dialog(
            dialog,
            result,
            _("Could not export PDF"),
            "pdf",
        )
        if file is None:
            return
        self._confirm_export_overwrite(
            file,
            self._export_pdf_to,
        )

    def _export_pdf_to(self, file: Gio.File) -> None:
        path = file.get_path()
        if path is None:
            self._show_error(
                _("Could not export PDF"),
                _("Choose a local file for the PDF export."),
            )
            return
        try:
            from . import exporting
        except Exception as error:  # pragma: no cover - optional Cairo
            self._show_error(_("Could not export PDF"), str(error))
            return
        text = self.get_text()
        title = Path(self._document.display_name).stem
        base_dir = self._document.directory
        base_file = self._document.file
        base_font_size = float(self._settings.get_int("export-font-size"))

        def produce() -> None:
            try:
                exporting.write_pdf(
                    text,
                    path,
                    title=title,
                    base_dir=base_dir,
                    base_file=base_file,
                    base_font_size=base_font_size,
                )
            except Exception as error:  # pragma: no cover - reported to user
                GLib.idle_add(self._export_failed, "PDF", error)
                return
            GLib.idle_add(self._export_pdf_written)

        threading.Thread(
            target=produce,
            daemon=True,
            name="md-split-export-pdf",
        ).start()

    def _export_pdf_written(self) -> bool:
        if not self._destroyed:
            self._show_toast(_("Exported PDF"))
        return False

    def _export_failed(self, kind: str, error: Exception) -> bool:
        if not self._destroyed:
            self._show_error(
                _("Could not export {kind}").format(kind=kind), str(error)
            )
        return False

    def _on_export_written(self, kind: str, error: Exception | None) -> None:
        if error is not None:
            self._show_error(
                _("Could not export {kind}").format(kind=kind), str(error)
            )
            return
        self._show_toast(_("Exported {kind}").format(kind=kind))

    def _show_toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(2)
        self.toast_overlay.add_toast(toast)

    def _cancel_load(self) -> None:
        if self._load_cancellable is not None:
            self._load_cancellable.cancel()
            self._load_cancellable = None

    def _invalidate_file_operations(self) -> None:
        """Detach all pending file callbacks from the current document."""
        self._document_epoch += 1
        self._load_generation += 1
        self._cancel_load()
        self._stop_file_monitor()
        self._save_queue.clear()
        self.editor_view.set_editable(True)

    def _draft_key(self) -> str:
        path = (
            self._document.file.get_path()
            if self._document.file is not None
            else None
        )
        return recovery.draft_key(path, self._draft_token)

    def _schedule_autosave(self) -> None:
        self._cancel_autosave_timeout()
        if self._loading or not self._document.is_dirty:
            return
        if not self._settings.get_boolean("autosave"):
            return
        interval = max(5, self._settings.get_int("autosave-interval"))
        self._autosave_timeout = GLib.timeout_add_seconds(
            interval, self._autosave_now
        )

    def _autosave_now(self) -> bool:
        self._autosave_timeout = 0
        if self._destroyed or not self._document.is_dirty:
            return False
        key = self._draft_key()
        self._last_draft_key = key
        draft = recovery.Draft(
            key=key,
            text=self.get_text(),
            path=(
                self._document.file.get_path()
                if self._document.file is not None
                else None
            ),
            name=self._document.display_name,
            updated=time.time(),
        )
        threading.Thread(
            target=self._write_recovery,
            args=(draft, self._recovery_generation),
            daemon=True,
            name="md-split-recovery",
        ).start()
        return False

    def _write_recovery(
        self, draft: recovery.Draft, generation: int
    ) -> None:
        if generation != self._recovery_generation:
            return
        try:
            recovery.write_draft(draft)
        except OSError as error:
            GLib.idle_add(self._autosave_failed, str(error))

    def _autosave_failed(self, message: str) -> bool:
        if not self._destroyed:
            log.warning("Could not write recovery draft: %s", message)
            self._show_toast(_("Could not save recovery draft"))
        return False

    def _clear_recovery(self) -> None:
        self._recovery_generation += 1
        keys = {self._draft_key()}
        if self._last_draft_key is not None:
            keys.add(self._last_draft_key)
        self._last_draft_key = None
        for key in keys:
            try:
                recovery.clear_draft(key)
            except OSError as error:
                log.warning("Could not clear recovery draft: %s", error)

    def _on_autosave_settings_changed(self, *_args) -> None:
        self._schedule_autosave()

    def _cancel_autosave_timeout(self) -> None:
        if self._autosave_timeout:
            GLib.source_remove(self._autosave_timeout)
            self._autosave_timeout = 0

    def load_recovered_draft(self, draft: recovery.Draft) -> None:
        """Restore a draft, keeping its file association when possible."""
        file = None
        baseline = ""
        if draft.path:
            candidate = Gio.File.new_for_path(draft.path)
            if candidate.query_exists(None):
                file = candidate
                try:
                    baseline = Path(draft.path).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    baseline = ""
        if file is not None:
            self._document.load(baseline, file)
            self._start_file_monitor(file)
        else:
            self._document.load_demo("")
        self._set_buffer_text(draft.text)
        self._document.set_text(draft.text)
        self._note_session_file(file)
        self._update_ui(render_preview=True)
        self._buffer.place_cursor(self._buffer.get_start_iter())
        self._schedule_autosave()

    def _start_file_monitor(self, file: Gio.File) -> None:
        self._stop_file_monitor()
        path = file.get_path()
        if path is None or not Path(path).is_file():
            return
        try:
            monitor = file.monitor_file(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
        except GLib.Error as error:
            log.warning("Could not watch %s: %s", path, error.message)
            return
        self._file_monitor = monitor
        self._monitor_handler = monitor.connect(
            "changed", self._on_monitor_changed
        )
        self._ignore_monitor_until = time.monotonic() + 1.0

    def _stop_file_monitor(self) -> None:
        if self._monitor_timeout:
            GLib.source_remove(self._monitor_timeout)
            self._monitor_timeout = 0
        monitor = self._file_monitor
        if monitor is not None:
            if self._monitor_handler:
                try:
                    monitor.disconnect(self._monitor_handler)
                except (TypeError, RuntimeError):
                    pass
            monitor.cancel()
        self._file_monitor = None
        self._monitor_handler = 0

    def _on_monitor_changed(
        self, _monitor, _file, _other_file, event_type
    ) -> None:
        kind = filechanges.classify_event(event_type)
        if kind == "ignore":
            return
        if time.monotonic() < self._ignore_monitor_until:
            return
        if self._monitor_timeout:
            GLib.source_remove(self._monitor_timeout)
        self._pending_monitor_kind = kind
        self._monitor_timeout = GLib.timeout_add(
            350, self._check_file_change
        )

    def _check_file_change(self) -> bool:
        self._monitor_timeout = 0
        if self._destroyed or self._document.file is None:
            return False
        file = self._document.file
        if self._pending_monitor_kind == "deleted":
            self._handle_file_change(file, "deleted", None)
            return False
        file.query_info_async(
            Gio.FILE_ATTRIBUTE_ETAG_VALUE,
            Gio.FileQueryInfoFlags.NONE,
            GLib.PRIORITY_DEFAULT,
            None,
            lambda source, result, _data: self._on_monitor_info(
                source, result, file
            ),
            None,
        )
        return False

    def _on_monitor_info(
        self, source: Gio.File, result, expected_file: Gio.File
    ) -> None:
        if self._destroyed:
            return
        if (
            self._document.file is None
            or not self._document.file.equal(expected_file)
        ):
            return
        try:
            info = source.query_info_finish(result)
        except GLib.Error as error:
            if error.matches(
                Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND
            ):
                self._handle_file_change(expected_file, "deleted", None)
            else:
                log.warning("Could not check changed file: %s", error.message)
            return
        self._handle_file_change(
            expected_file, "changed", info.get_etag()
        )

    def _handle_file_change(
        self, file: Gio.File, kind: str, etag: str | None
    ) -> None:
        action = filechanges.change_action(
            kind,
            dirty=self._document.is_dirty,
            self_saved=time.monotonic() < self._ignore_monitor_until,
            etag_changed=kind != "deleted"
            and etag != self._document.etag,
            exists=kind != "deleted",
        )
        if action == "ignore":
            return
        if action != "deleted" and etag == self._last_prompted_etag:
            return
        self._last_prompted_etag = etag
        if action == "deleted":
            self._prompt_deleted_file(file)
        else:
            self._prompt_external_change(file, etag)

    def _prompt_external_change(
        self, file: Gio.File, etag: str | None
    ) -> None:
        if self._confirm_dialog is not None:
            return
        dirty = self._document.is_dirty
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("File changed on disk"))
        name = file.get_basename() or file.get_parse_name()
        if dirty:
            dialog.set_body(
                _(
                    "“{name}” changed on disk. Reloading will discard your "
                    "unsaved changes."
                ).format(name=name)
            )
        else:
            dialog.set_body(
                _("“{name}” changed on disk.").format(name=name)
            )
        dialog.add_response("keep", _("Keep Editing"))
        dialog.add_response("reload", _("Reload"))
        if dirty:
            dialog.set_response_appearance(
                "reload", Adw.ResponseAppearance.DESTRUCTIVE
            )
        else:
            dialog.set_response_appearance(
                "reload", Adw.ResponseAppearance.SUGGESTED
            )
        dialog.set_default_response("keep" if dirty else "reload")
        dialog.set_close_response("keep")

        def on_response(_dialog, response: str) -> None:
            self._confirm_dialog = None
            if response == "reload":
                self._load_file(file)

        dialog.connect("response", on_response)
        self._confirm_dialog = dialog
        dialog.present(self)

    def _prompt_deleted_file(self, file: Gio.File) -> None:
        if self._confirm_dialog is not None:
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("File deleted"))
        name = file.get_basename() or file.get_parse_name()
        dialog.set_body(
            _(
                "“{name}” was removed from disk. Your text is still here."
            ).format(name=name)
        )
        dialog.add_response("keep", _("Keep Editing"))
        dialog.add_response("save-as", _("Save As…"))
        dialog.set_default_response("save-as")
        dialog.set_close_response("keep")

        def on_response(_dialog, response: str) -> None:
            self._confirm_dialog = None
            if response == "save-as":
                self.save_as_document()

        dialog.connect("response", on_response)
        self._confirm_dialog = dialog
        dialog.present(self)

    def _load_file(self, file: Gio.File, on_loaded=None) -> None:
        self._document_epoch += 1
        self._save_queue.clear()
        self._stop_file_monitor()
        self._load_generation += 1
        generation = self._load_generation
        self._cancel_load()
        cancellable = Gio.Cancellable()
        self._load_cancellable = cancellable
        starting_revision = self._document.revision
        self.editor_view.set_editable(False)
        fileio.load_text_async(
            file,
            lambda text, etag, error: self._on_file_loaded(
                generation,
                starting_revision,
                file,
                text,
                etag,
                error,
                on_loaded,
            ),
            cancellable,
        )

    def _on_file_loaded(
        self,
        generation: int,
        starting_revision: int,
        requested_file: Gio.File,
        text: str | None,
        etag: str | None,
        error: Exception | None,
        on_loaded,
    ) -> None:
        if self._destroyed or generation != self._load_generation:
            return
        self.editor_view.set_editable(True)
        self._load_cancellable = None

        if error is not None:
            if _io_error_matches(error, Gio.IOErrorEnum.CANCELLED):
                return
            if isinstance(error, UnicodeDecodeError):
                self._show_error(
                    _("Could not open file"),
                    _("The file is not valid UTF-8 text."),
                )
            else:
                self._show_error(
                    _("Could not open file"),
                    _file_load_error_message(error, requested_file),
                )
            return

        assert text is not None
        if self._document.revision != starting_revision:
            self._show_error(
                _("Could not open file"),
                _("The document changed while the file was loading."),
            )
            return

        # Invalidate saves started against the previous document while this
        # load was pending.
        self._document_epoch += 1
        self._save_queue.clear()
        self._document.load(text, requested_file, etag)
        self._set_buffer_text(text)
        self._start_file_monitor(requested_file)
        self._update_ui(render_preview=True)
        self._remember_folder(requested_file)
        self._record_recent(requested_file)
        self._note_session_file(requested_file)
        self._buffer.place_cursor(self._buffer.get_start_iter())
        self.editor_view.scroll_to_iter(
            self._buffer.get_start_iter(), 0.0, False, 0.0, 0.0
        )
        self.focus_primary_pane()
        if on_loaded is not None:
            on_loaded()

    def _write_file(
        self, file: Gio.File, callback: Callable[[], None] | None
    ) -> None:
        if (
            not self._destroyed
            and not self._loading
            and self._settings.get_boolean("format-on-save")
        ):
            self._format_scope(confirm=False, notify=False, selection=False)
        self._document.set_text(self.get_text())
        self._save_queue.append((file, callback, self._document_epoch))
        self._start_next_save()

    def _start_next_save(self) -> None:
        if self._save_active:
            return
        while self._save_queue and self._save_queue[0][2] != self._document_epoch:
            self._save_queue.pop(0)
        if not self._save_queue:
            return

        requested_file, callback, epoch = self._save_queue.pop(0)
        snapshot = self.get_text()
        self._document.set_text(snapshot)
        same_file = (
            self._document.file is not None
            and requested_file.equal(self._document.file)
        )
        expected_etag = self._document.etag if same_file else None
        self._save_active = True
        self._ignore_monitor_until = time.monotonic() + 1.5
        fileio.save_text_async(
            requested_file,
            snapshot,
            lambda error, etag: self._on_file_written(
                epoch,
                requested_file,
                snapshot,
                error,
                etag,
                callback,
            ),
            etag=expected_etag,
        )

    def _on_file_written(
        self,
        epoch: int,
        requested_file: Gio.File,
        snapshot: str,
        error: Exception | None,
        etag: str | None,
        callback: Callable[[], None] | None,
    ) -> None:
        self._save_active = False
        if self._destroyed or epoch != self._document_epoch:
            self._start_next_save()
            return

        if error is not None:
            self._save_queue.clear()
            if _io_error_matches(error, Gio.IOErrorEnum.CANCELLED):
                return
            if _io_error_matches(error, Gio.IOErrorEnum.WRONG_ETAG):
                self._show_error(
                    _("File changed on disk"),
                    _(
                        "The file was changed by another application. "
                        "Reopen it before saving again."
                    ),
                )
            else:
                self._show_error(_("Could not save file"), str(error))
            return

        previous_text = self._document.saved_text
        self._document.mark_saved(
            requested_file, saved_text=snapshot, etag=etag
        )
        if (
            self._settings.get_boolean("backup-files")
            and previous_text
            and previous_text != snapshot
            and requested_file.get_path()
        ):
            retention = max(
                1, self._settings.get_int("backup-retention")
            )
            threading.Thread(
                target=recovery.write_backup,
                args=(Path(requested_file.get_path()), previous_text),
                kwargs={"retention": retention},
                daemon=True,
                name="md-split-backup",
            ).start()
        self._clear_recovery()
        self._ignore_monitor_until = time.monotonic() + 1.5
        self._start_file_monitor(requested_file)
        self._remember_folder(requested_file)
        self._record_recent(requested_file)
        self._note_session_file(requested_file)
        self._update_ui()

        # A queued save without a continuation must not swallow an earlier
        # continuation (open-after-save, close-after-save): carry the latest
        # pending continuation forward to the tail save.
        if callback is not None:
            current = [
                (index, entry)
                for index, entry in enumerate(self._save_queue)
                if entry[2] == self._document_epoch
            ]
            if current:
                tail_index, tail = current[-1]
                if tail[1] is None:
                    carried = next(
                        (
                            entry[1]
                            for _index, entry in reversed(current)
                            if entry[1] is not None
                        ),
                        callback,
                    )
                    self._save_queue[tail_index] = (
                        tail[0],
                        carried,
                        tail[2],
                    )

        has_queued_save = bool(self._save_queue)
        self._start_next_save()
        if (
            callback is not None
            and not has_queued_save
            and self._document.text == snapshot
        ):
            callback()

    # -- dialogs ---------------------------------------------------------

    def _confirm_discard(self, heading: str, on_discard) -> None:
        if not self._document.is_dirty:
            on_discard()
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(_("Your changes will be lost if you don’t save them."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("discard", _("Discard"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            if response == "discard":
                self._clear_recovery()
                on_discard()
            elif response == "save":
                self.save_document_then(on_discard)

        dialog.connect("response", on_response)
        self._confirm_dialog = dialog
        dialog.present(self)

    def _show_error(self, heading: str, message: str) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(message)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        self._error_dialog = dialog
        dialog.present(self)

    def _is_dismissed(self, error: GLib.Error) -> bool:
        return error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED) or error.matches(
            Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED
        )

    # -- window lifecycle ------------------------------------------------

    def _on_destroy(self, *_args) -> None:
        self._destroyed = True
        self._cancel_preview_timeout()
        self._cancel_stats_timeout()
        self._cancel_search_timeout()
        self._cancel_autosave_timeout()
        if not self._document.is_dirty:
            self._clear_recovery()
        self._invalidate_file_operations()
        for owner, handler_id in self._signal_handlers:
            disconnect = getattr(owner, "disconnect", None)
            if disconnect is not None:
                try:
                    disconnect(handler_id)
                except (TypeError, RuntimeError):
                    pass
        self._signal_handlers.clear()
        style_manager = Adw.StyleManager.get_default()
        if getattr(self, "_style_handler", 0):
            style_manager.disconnect(self._style_handler)
            self._style_handler = 0
        if getattr(self, "_settings_handler", 0):
            self._settings.disconnect(self._settings_handler)
            self._settings_handler = 0
        if getattr(self, "_sync_scroll_handler", 0):
            self._settings.disconnect(self._sync_scroll_handler)
            self._sync_scroll_handler = 0
        if getattr(self, "_recent_handler", 0):
            self._settings.disconnect(self._recent_handler)
            self._recent_handler = 0
        if getattr(self, "_autosave_handler", 0):
            self._settings.disconnect(self._autosave_handler)
            self._autosave_handler = 0
        if getattr(self, "_autosave_interval_handler", 0):
            self._settings.disconnect(self._autosave_interval_handler)
            self._autosave_interval_handler = 0
        if getattr(self, "_spell_idle", 0):
            GLib.source_remove(self._spell_idle)
            self._spell_idle = 0
        if self._language_idle:
            GLib.source_remove(self._language_idle)
            self._language_idle = 0
        if getattr(self, "_spell_handler", 0):
            self._settings.disconnect(self._spell_handler)
            self._spell_handler = 0
        if getattr(self, "_spell_language_handler", 0):
            self._settings.disconnect(self._spell_language_handler)
            self._spell_language_handler = 0
        if self.spell_adapter is not None:
            self.editor_view.insert_action_group("spelling", None)
            self.editor_view.set_extra_menu(None)
            self.spell_adapter = None
        app = self.get_application()
        if app is not None and hasattr(app, "forget_window"):
            app.forget_window(self)
        if self._key_controller is not None:
            self.editor_scroller.remove_controller(self._key_controller)
            self._key_controller = None
        if getattr(self, "_editor_focus_controller", None) is not None:
            self.editor_view.remove_controller(self._editor_focus_controller)
            self._editor_focus_controller = None
        if self._vim_mode_handler:
            self._settings.disconnect(self._vim_mode_handler)
            self._vim_mode_handler = 0
        self._drop_vim_search_context()
        if self._vim_attached and self._vim_controller is not None:
            self.editor_view.remove_controller(self._vim_controller)
            self._vim_attached = False
        if self._vim_im is not None:
            self._vim_im.set_client_widget(None)
        if self._preview_vim is not None:
            self._preview_vim.shutdown()
            self._preview_vim = None
        self._preview.shutdown()

    def _on_close_request(self, *_args) -> bool:
        if self._force_close:
            return False
        self._save_settings()
        if not self._document.is_dirty:
            return False
        self._confirm_discard(
            _("Save changes before closing?"),
            self._close_now,
        )
        return True

    def _close_now(self) -> None:
        self._force_close = True
        self.destroy()

    def _save_settings(self) -> None:
        width = self.get_width()
        height = self.get_height()
        if width > 0:
            self._settings.set_int(
                "window-width", self._clamped_setting("window-width", width)
            )
        if height > 0:
            self._settings.set_int(
                "window-height", self._clamped_setting("window-height", height)
            )
        if self._view_mode == "both":
            self._settings.set_int(
                "paned-position",
                self._clamped_setting(
                    "paned-position", self.paned.get_position()
                ),
            )

    def _clamped_setting(self, key: str, value: int) -> int:
        """Clamp a value to the range declared in the GSettings schema."""
        key_range = self._settings.props.settings_schema.get_key(key)
        _kind, (minimum, maximum) = key_range.get_range().unpack()
        return max(minimum, min(value, maximum))
