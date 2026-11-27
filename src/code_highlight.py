# SPDX-License-Identifier: MIT

"""Native syntax highlighting for Markdown preview code blocks."""

from __future__ import annotations

import logging
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Gio, GLib, Gtk, GtkSource

log = logging.getLogger(__name__)

MAX_HIGHLIGHT_CHARS = 64 * 1024
MAX_HIGHLIGHT_LINES = 1000

_LANGUAGE_ALIASES = {
    "bash": "sh",
    "c#": "c-sharp",
    "c++": "cpp",
    "cc": "cpp",
    "csharp": "c-sharp",
    "cs": "c-sharp",
    "cxx": "cpp",
    "javascript": "js",
    "js": "js",
    "md": "markdown",
    "plain": "",
    "plaintext": "",
    "py": "python",
    "python3": "python",
    "shell": "sh",
    "text": "",
    "txt": "",
    "ts": "typescript",
    "yml": "yaml",
    "zsh": "sh",
}

_language_manager: GtkSource.LanguageManager | None = None
_scheme_manager: GtkSource.StyleSchemeManager | None = None


def _managers() -> tuple[
    GtkSource.LanguageManager, GtkSource.StyleSchemeManager
]:
    global _language_manager, _scheme_manager
    if _language_manager is None:
        _language_manager = GtkSource.LanguageManager.get_default()
    if _scheme_manager is None:
        _scheme_manager = GtkSource.StyleSchemeManager.get_default()
    return _language_manager, _scheme_manager


def resolve_language(info: str) -> GtkSource.Language | None:
    """Resolve a fence info string to an installed GtkSource language."""
    word = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""
    word = _LANGUAGE_ALIASES.get(word, word)
    if not word:
        return None
    manager, _schemes = _managers()
    return manager.get_language(word)


def _style_scheme(dark: bool) -> GtkSource.StyleScheme | None:
    _languages, schemes = _managers()
    return schemes.get_scheme("Adwaita-dark" if dark else "Adwaita")


def _markup_supported() -> bool:
    version = (GtkSource.get_major_version(), GtkSource.get_minor_version())
    return version >= (5, 18) and hasattr(GtkSource.Buffer, "get_markup")


def _apply_markup(label: Gtk.Label, buffer: GtkSource.Buffer) -> bool:
    start, end = buffer.get_bounds()
    markup = buffer.get_markup(start, end)
    if not markup:
        return False
    label.set_markup(markup)
    return True


def highlight_label(
    label: Gtk.Label,
    code: str,
    info: str,
    *,
    dark: bool,
    cancellable: Gio.Cancellable | None = None,
) -> bool:
    """Highlight ``code`` into ``label`` when a suitable language exists.

    The label starts as plain text. Newer GtkSourceView versions prepare
    languages asynchronously and update it from the main-thread callback.
    """
    if (
        not code
        or not _markup_supported()
        or len(code) > MAX_HIGHLIGHT_CHARS
        or code.count("\n") + 1 > MAX_HIGHLIGHT_LINES
    ):
        return False

    language = resolve_language(info)
    scheme = _style_scheme(dark)
    if language is None or scheme is None:
        return False

    try:
        buffer = GtkSource.Buffer()
        buffer.set_language(language)
        buffer.set_style_scheme(scheme)
        buffer.set_text(code)
    except (GLib.Error, RuntimeError, TypeError, ValueError) as error:
        log.debug("Could not initialize code highlighting: %s", error)
        return False

    prepare_async = getattr(language, "prepare_async", None)
    if callable(prepare_async):
        label_ref = weakref.ref(label)

        def on_prepared(source, result, _user_data) -> None:
            if cancellable is not None and cancellable.is_cancelled():
                return
            target = label_ref()
            if target is None:
                return
            try:
                prepare_finish = getattr(source, "prepare_finish", None)
                if not callable(prepare_finish) or not prepare_finish(result):
                    return
                buffer.ensure_highlight(
                    buffer.get_start_iter(), buffer.get_end_iter()
                )
                _apply_markup(target, buffer)
            except (GLib.Error, RuntimeError, TypeError, ValueError) as error:
                log.debug("Could not render highlighted code: %s", error)

        try:
            prepare_async(cancellable, on_prepared, None)
        except (GLib.Error, RuntimeError, TypeError, ValueError) as error:
            log.debug("Could not prepare code highlighting: %s", error)
            return False
        return True

    try:
        buffer.ensure_highlight(buffer.get_start_iter(), buffer.get_end_iter())
        return _apply_markup(label, buffer)
    except (GLib.Error, RuntimeError, TypeError, ValueError) as error:
        log.debug("Could not render highlighted code: %s", error)
        return False
