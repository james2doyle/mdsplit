# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
import os
import sys
from gettext import gettext as _
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, VIEW_MODES, recovery, session
from .logging_utils import install_known_warning_filter

# The window and preferences modules (GtkSource, the Markdown parser and the
# renderer) load on first use. A second launch that only forwards files to
# the running instance over D-Bus then skips them entirely.
if TYPE_CHECKING:
    from .window import MdSplitWindow

log = logging.getLogger(__name__)

ICON_RESOURCE_PATH = "/io/github/james2doyle/MdSplit/icons"
MINIMUM_ADW = (1, 9)
NEW_INSTANCE_FLAG = "--new-instance"


def split_new_instance(argv: list[str]) -> tuple[bool, list[str]]:
    """Remove --new-instance from argv and report whether present."""
    if NEW_INSTANCE_FLAG not in argv:
        return False, list(argv)
    return True, [arg for arg in argv if arg != NEW_INSTANCE_FLAG]


def application_flags(*, new_instance: bool = False) -> Gio.ApplicationFlags:
    """Return the GApplication flags for a primary or secondary process."""
    flags = Gio.ApplicationFlags.HANDLES_OPEN
    if new_instance:
        flags |= Gio.ApplicationFlags.NON_UNIQUE
    return flags


class MdSplitApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version, *, new_instance=False):
        super().__init__(
            application_id=APP_ID,
            flags=application_flags(new_instance=new_instance),
            resource_base_path="/io/github/james2doyle/MdSplit",
        )
        self.version = version
        self.new_instance = bool(new_instance)
        GLib.set_application_name("MD Split")
        self.settings = Gio.Settings(schema_id=APP_ID)
        if (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) < MINIMUM_ADW:
            log.warning(
                "libadwaita 1.9 or newer is required for the shortcuts dialog"
            )
        self._preferences_dialog = None
        self._cli_view_mode = None
        self._cli_color_scheme = None
        self._session_files: dict[MdSplitWindow, str] = {}
        self._last_active_path = ""
        self._session_written = False
        self._recovery_prompted = False
        self._recovery_dialog = None
        self._missing_dialog = None
        # Adw.StyleManager setup is deferred to startup: a forwarding second
        # instance never needs it.
        self._started = False
        self._register_options()
        self.connect("handle-local-options", self._on_handle_local_options)
        self.settings.connect("changed::appearance", self._on_appearance_changed)
        self.create_action("new", self.on_new_action, ["<control>n"])
        self.create_action("open", self.on_open_action, ["<control>o"])
        self.create_action("save", self.on_save_action, ["<control>s"])
        self.create_action(
            "save-as", self.on_save_as_action, ["<control><shift>s"]
        )
        self.create_action("copy-path", self.on_copy_path_action)
        self.lookup_action("copy-path").set_enabled(False)
        self.create_action("find", self.on_find_action, ["<control>f"])
        self.create_action(
            "find-next", self.on_find_next_action, ["<control>g"]
        )
        self.create_action(
            "find-previous",
            self.on_find_previous_action,
            ["<control><shift>g"],
        )
        self.create_action("replace", self.on_replace_action, ["<control>h"])
        for name, shortcuts in (
            ("move-lines-up", ("<alt>Up",)),
            ("move-lines-down", ("<alt>Down",)),
            ("duplicate-line", ("<control><shift>d",)),
            ("toggle-task", ("<control><shift>x", "<control>d")),
        ):
            self.create_action(name, self.on_line_operation_action, shortcuts)
        self.create_action("go-to-line", self.on_go_to_line_action, ["<control>l"])
        self.create_action(
            "format-document",
            self.on_format_document_action,
            ["<control><shift>r"],
        )
        self.create_action("export-html", self.on_export_html_action)
        self.create_action("export-pdf", self.on_export_pdf_action)
        for name, shortcut in (
            ("format-bold", "<control>b"),
            ("format-italic", "<control>i"),
            ("format-underline", "<control>u"),
            ("format-blockquote", "<control>t"),
            ("format-code", "<control>grave"),
        ):
            self.create_action(name, self.on_format_action, [shortcut])
        self.create_action(
            "view-mode",
            self.on_view_mode_action,
            parameter_type=GLib.VariantType.new("s"),
            state=GLib.Variant.new_string(
                self.settings.get_string("view-mode")
                if self.settings.get_string("view-mode") in VIEW_MODES
                else "both"
            ),
        )
        self.create_action("cycle-view", self.on_cycle_view_action, ["F9"])
        self.create_action(
            "remote-images",
            self.on_remote_images_action,
            state=GLib.Variant.new_boolean(
                self.settings.get_boolean("remote-images")
            ),
        )
        self.create_action(
            "open-recent",
            self.on_open_recent_action,
            parameter_type=GLib.VariantType.new("s"),
        )
        self.create_action("clear-recent", self.on_clear_recent_action)
        self.create_action(
            "preferences", self.on_preferences_action, ["<control>comma"]
        )
        self.create_action("quit", self.on_quit_action, ["<control>q"])
        self.create_action("about", self.on_about_action)
        self.settings.connect("changed::remote-images", self._sync_remote_action)
        self.connect("notify::active-window", self._on_active_window_changed)

    def _register_options(self) -> None:
        for name, description in (
            ("no-source", _("Start with the preview pane only")),
            ("no-preview", _("Start with the source pane only")),
            ("source-only", _("Start with the source pane only")),
            ("preview-only", _("Start with the preview pane only")),
            ("light", _("Force light mode for this run")),
            ("dark", _("Force dark mode for this run")),
            ("new-instance", _("Start a separate application instance")),
        ):
            self.add_main_option(
                name,
                0,
                GLib.OptionFlags.NONE,
                GLib.OptionArg.NONE,
                description,
                None,
            )

    def _on_handle_local_options(self, _app, options) -> int:
        source_only = options.contains("source-only") or options.contains(
            "no-preview"
        )
        preview_only = options.contains("preview-only") or options.contains(
            "no-source"
        )
        if source_only and preview_only:
            print(
                _("error: --source-only and --preview-only cannot be combined"),
                file=sys.stderr,
            )
            return 1

        light = options.contains("light")
        dark = options.contains("dark")
        if light and dark:
            print(
                _("error: --light and --dark cannot be combined"),
                file=sys.stderr,
            )
            return 1

        self._cli_view_mode = (
            "source" if source_only else "preview" if preview_only else None
        )
        if light:
            self._cli_color_scheme = Adw.ColorScheme.FORCE_LIGHT
        elif dark:
            self._cli_color_scheme = Adw.ColorScheme.FORCE_DARK
        else:
            self._cli_color_scheme = None
        if self._started:
            self._apply_appearance()
        return -1

    def _apply_appearance(self) -> None:
        if self._cli_color_scheme is not None:
            scheme = self._cli_color_scheme
        else:
            value = self.settings.get_string("appearance")
            schemes = {
                "light": Adw.ColorScheme.FORCE_LIGHT,
                "dark": Adw.ColorScheme.FORCE_DARK,
            }
            scheme = schemes.get(value, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def _on_appearance_changed(self, _settings, _key) -> None:
        self._cli_color_scheme = None
        self._apply_appearance()

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._started = True
        self._apply_appearance()
        display = Gdk.Display.get_default()
        if display is not None:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            icon_theme.add_resource_path(ICON_RESOURCE_PATH)

    def do_activate(self):
        win = self.props.active_window
        if win is not None:
            win.present()
            return

        restored = False
        missing: list[str] = []
        target = None
        if not self.new_instance and self.settings.get_boolean(
            "restore-session"
        ):
            stored = self.settings.get_strv("session-files")
            existing, missing = session.prune_missing(stored)
            if existing != stored:
                self.settings.set_strv("session-files", existing)
            active = self.settings.get_string("session-active")
            for path in existing:
                restored_win = self._new_window()
                restored_win.open_file(Gio.File.new_for_path(path))
                restored_win.present()
                if path == active:
                    target = restored_win
                restored = True

        if not restored:
            win = self._new_window()
            win.present()
            if self.settings.get_boolean("load-demo-on-startup"):
                # Let the window paint before parsing and rendering the demo.
                GLib.idle_add(
                    win.load_demo_document,
                    priority=GLib.PRIORITY_HIGH_IDLE,
                )

        if target is not None:
            target.present()
        if missing:
            self._warn_missing_files(missing)
        if not self.new_instance:
            self._prompt_recovery_drafts()

    def _prompt_recovery_drafts(self) -> None:
        if self.new_instance or self._recovery_prompted:
            return
        self._recovery_prompted = True
        drafts = recovery.list_drafts()
        if drafts:
            self._prompt_next_draft(drafts)

    def _prompt_next_draft(self, drafts: list[recovery.Draft]) -> None:
        if not drafts:
            return
        draft = drafts[0]
        win = self.props.active_window
        if win is None:
            win = self._ensure_window()
            win.present()
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Recover unsaved document"))
        dialog.set_body(
            _(
                "“{name}” has unsaved changes from a previous run."
            ).format(name=draft.name)
        )
        dialog.add_response("discard", _("Discard"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance(
            "discard", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_response_appearance(
            "restore", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("restore")
        dialog.set_close_response("discard")

        def on_response(_dialog, response: str) -> None:
            self._recovery_dialog = None
            if response == "restore":
                restored = self._new_window()
                restored.load_recovered_draft(draft)
                restored.present()
            else:
                recovery.clear_draft(draft.key)
            self._prompt_next_draft(drafts[1:])

        dialog.connect("response", on_response)
        self._recovery_dialog = dialog
        dialog.present(win)

    def _warn_missing_files(self, missing: list[str]) -> None:
        win = self.props.active_window
        if win is None:
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Some files could not be reopened"))
        dialog.set_body("\n".join(missing))
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")

        def on_response(_dialog, _response: str) -> None:
            self._missing_dialog = None

        dialog.connect("response", on_response)
        self._missing_dialog = dialog
        dialog.present(win)

    def note_window_file(
        self, window: MdSplitWindow, path: str | None
    ) -> None:
        if self.new_instance:
            return
        if path:
            self._session_files[window] = path
            self._last_active_path = path
        else:
            self._session_files.pop(window, None)
        self._session_written = False
        self._write_session()

    def forget_window(self, window: MdSplitWindow) -> None:
        self._session_files.pop(window, None)
        if not self._session_written:
            self._write_session()

    def _on_active_window_changed(self, *_args) -> None:
        win = self.props.active_window
        if win is not None:
            path = self._session_files.get(win)
            if path:
                self._last_active_path = path
            win.sync_copy_path_action()
        else:
            action = self.lookup_action("copy-path")
            if action is not None:
                action.set_enabled(False)

    def _write_session(self) -> None:
        if self.new_instance:
            return
        windows = set(self.get_windows())
        paths = session.dedupe_paths(
            [
                path
                for window, path in self._session_files.items()
                if path and window in windows
            ]
        )
        active = (
            self._last_active_path
            if self._last_active_path in paths
            else (paths[-1] if paths else "")
        )
        self.settings.set_strv("session-files", paths)
        self.settings.set_string("session-active", active)

    def do_shutdown(self):
        if not self._session_written:
            self._write_session()
        Adw.Application.do_shutdown(self)

    def do_open(self, files, n_files, hint):
        if not files:
            self.do_activate()
            return

        first_window = self._ensure_window()
        windows = [(first_window, files[0])]
        for file in files[1:]:
            win = self._new_window()
            windows.append((win, file))

        for win, file in windows:
            win.open_file(file)
            win.present()

    def _new_window(self) -> MdSplitWindow:
        from .window import MdSplitWindow

        return MdSplitWindow(
            application=self, initial_view_mode=self._cli_view_mode
        )

    def _ensure_window(self) -> MdSplitWindow:
        win = self.props.active_window
        if win is None:
            win = self._new_window()
        return win

    # -- actions ---------------------------------------------------------

    def on_new_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.new_document()

    def on_open_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.open_document()

    def on_open_recent_action(self, _action, parameter):
        if parameter is None:
            return
        path = parameter.get_string()
        if not path:
            return
        win = self._ensure_window()
        win.open_file(Gio.File.new_for_path(path))
        win.present()

    def on_clear_recent_action(self, *_args):
        self.settings.set_strv("recent-files", [])
        try:
            Gtk.RecentManager.get_default().purge_items()
        except Exception as error:  # pragma: no cover - session bus optional
            log.warning("Could not clear recent files: %s", error)

    def on_save_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.save_document()

    def on_save_as_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.save_as_document()

    def on_copy_path_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.copy_path()

    def on_find_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.show_find()

    def on_find_next_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.find_next()

    def on_find_previous_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.find_previous()

    def on_replace_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.show_replace()

    def on_line_operation_action(self, action, _parameter):
        win = self.props.active_window
        if win is None or not win.editor_view.has_focus():
            return
        name = action.get_name()
        if name == "duplicate-line":
            win.duplicate_line_or_selection()
        elif name == "toggle-task":
            win.toggle_task()
        else:
            win.move_lines(-1 if name == "move-lines-up" else 1)

    def on_go_to_line_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.show_go_to_line()

    def on_format_document_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.format_document()

    def on_export_html_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.export_html()

    def on_export_pdf_action(self, *_args):
        win = self.props.active_window
        if win is not None:
            win.export_pdf()

    def on_format_action(self, action, _parameter):
        win = self.props.active_window
        if win is not None and win.editor_view.has_focus():
            win.format_selection(action.get_name().removeprefix("format-"))

    def on_preferences_action(self, *_args):
        if self._preferences_dialog is not None:
            self._preferences_dialog.present(self.props.active_window)
            return
        from .preferences import MdSplitPreferencesDialog

        dialog = MdSplitPreferencesDialog()
        dialog.connect("closed", self._on_preferences_closed)
        self._preferences_dialog = dialog
        dialog.present(self.props.active_window)

    def _on_preferences_closed(self, dialog) -> None:
        if self._preferences_dialog is dialog:
            self._preferences_dialog = None

    def on_view_mode_action(self, action, parameter):
        if parameter is None:
            return
        mode = parameter.get_string()
        if mode not in VIEW_MODES:
            return
        win = self.props.active_window
        if win is not None:
            win.set_view_mode(mode)
        else:
            self.settings.set_string("view-mode", mode)
            self.sync_view_mode(mode)

    def on_cycle_view_action(self, *_args):
        win = self.props.active_window
        if win is None:
            return
        index = VIEW_MODES.index(win.view_mode)
        win.set_view_mode(VIEW_MODES[(index + 1) % len(VIEW_MODES)])

    def sync_view_mode(self, mode: str) -> None:
        action = self.lookup_action("view-mode")
        if action is not None and action.get_state().get_string() != mode:
            action.set_state(GLib.Variant.new_string(mode))

    def on_remote_images_action(self, action, _parameter):
        enabled = not action.get_state().get_boolean()
        action.set_state(GLib.Variant.new_boolean(enabled))
        self.settings.set_boolean("remote-images", enabled)

    def _sync_remote_action(self, settings, _key):
        action = self.lookup_action("remote-images")
        if action is not None:
            action.set_state(
                GLib.Variant.new_boolean(settings.get_boolean("remote-images"))
            )

    def on_quit_action(self, *_args):
        self._write_session()
        self._session_written = True
        windows = self.get_windows()
        if not windows:
            self.quit()
            return
        for win in windows:
            win.close()

    def on_about_action(self, *_args):
        about = Adw.AboutDialog(
            application_name="MD Split",
            application_icon=APP_ID,
            developer_name="James Doyle",
            version=self.version,
            comments=_(
                "A native GNOME Markdown editor with a live, "
                "GitHub-Flavored Markdown preview."
            ),
            website="https://github.com/james2doyle/mdsplit",
            issue_url="https://github.com/james2doyle/mdsplit/issues",
            license_type=Gtk.License.MIT_X11,
            translator_credits=_("translator-credits"),
            developers=["James Doyle"],
            copyright="© 2026 James Doyle",
        )
        about.present(self.props.active_window)

    def create_action(
        self,
        name,
        callback,
        shortcuts=None,
        state=None,
        parameter_type=None,
    ):
        if state is None:
            action = Gio.SimpleAction.new(name, parameter_type)
        else:
            action = Gio.SimpleAction.new_stateful(
                name, parameter_type, state
            )
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version):
    """The application's entry point."""
    install_known_warning_filter()
    debug = os.environ.get("MD_SPLIT_DEBUG", "")
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(name)s: %(message)s")
    log.info("MD Split %s", version)
    new_instance, argv = split_new_instance(sys.argv)
    app = MdSplitApplication(version, new_instance=new_instance)
    return app.run(argv)
