# SPDX-License-Identifier: MIT

"""Targeted filtering for known benign GTK, GDK and libenchant warnings.

Six upstream warnings are noisy but do not indicate a document or
data-loss problem:

- ``GtkTextView`` updates scrollbar adjustments during snapshot
  (GNOME/gtk#6057), producing ``Trying to snapshot GtkGizmo ...`` messages.
- The Wayland text-input context warns when a selection exceeds its 4 KiB
  surrounding-text window, producing ``notify_surrounding_text: code should
  not be reached``.
- The GDK frame clock can receive a compositor throttling hint while the
  frame is still being prepared, producing a
  ``gdk_frame_timings_throttling_hint: runtime check failed`` warning.
- A compositor can report the same presented frame twice with slightly
  different timestamps, producing a ``Duplicate call to
  gdk_frame_timings_presented()`` warning.
- A compositor can report presentation after GDK discarded a frame during
  rapid layout changes, producing a ``gdk_frame_timings_presented() called
  on discarded frame`` warning.
- libenchant warns about provider plugins that are not installed while it
  initializes the spelling backend. The dictionaries that are available still
  load through their working plugin.

The filter keeps unrelated warnings visible. Set
``MD_SPLIT_SHOW_WARNINGS=1`` to disable it while debugging.
"""

from __future__ import annotations

import ctypes
import os
import re

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

_SNAPSHOT_WARNING = re.compile(
    r"^Trying to snapshot GtkGizmo 0x[0-9a-fA-F]+ "
    r"without a current allocation$"
)
_WAYLAND_IM_WARNING = re.compile(
    r"^\(.*gtkimcontextwayland\.c:\d+\):"
    r"notify_surrounding_text: code should not be reached$"
)
_GDK_THROTTLING_WARNING = re.compile(
    r"^\(.*gdkframetimings\.c:\d+\):"
    r"gdk_frame_timings_throttling_hint: runtime check failed: "
    r"\(self->result != GDK_FRAME_PREPARING\)$"
)
_GDK_PRESENTED_WARNING = re.compile(
    r"^Duplicate call to gdk_frame_timings_presented\(\) with different "
    r"values: presentation time is -?\d+(?:\.\d+)?ms off from expected$"
)
_GDK_DISCARDED_WARNING = re.compile(
    r"^gdk_frame_timings_presented\(\) called on discarded frame\.$"
)
_ENCHANT_PLUGIN_WARNING = re.compile(
    r"^broker\.vala:\d+: Error loading plugin: "
    r"lib[^:\s]+\.so(?:\.\d+)*: "
    r"cannot open shared object file: No such file or directory$"
)


def is_known_warning(domain: str | None, message: str | None) -> bool:
    """Return True for the known harmless GTK, GDK and libenchant messages."""
    if not message:
        return False
    if domain == "Gtk":
        return bool(
            _SNAPSHOT_WARNING.match(message)
            or _WAYLAND_IM_WARNING.match(message)
        )
    if domain == "Gdk":
        return bool(
            _GDK_THROTTLING_WARNING.match(message)
            or _GDK_PRESENTED_WARNING.match(message)
            or _GDK_DISCARDED_WARNING.match(message)
        )
    if domain == "libenchant":
        return bool(_ENCHANT_PLUGIN_WARNING.match(message))
    return False


def _on_known_warning(
    domain: str | None,
    level: GLib.LogLevelFlags,
    message: str | None,
    user_data: object = None,
) -> None:
    if bool(level & GLib.LogLevelFlags.LEVEL_WARNING) and is_known_warning(
        domain, message
    ):
        return
    GLib.log_default_handler(domain, level, message, user_data)


def _field_text(field) -> str | None:
    if field is None:
        return None
    value = field.value
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).split(b"\0", 1)[0].decode(
            "utf-8", errors="replace"
        )
    if isinstance(value, int) and value > 0:
        # PyGObject exposes structured fields as pointers. MESSAGE and
        # GLIB_DOMAIN are UTF-8; a negative length means NUL-terminated.
        raw = (
            ctypes.string_at(value, field.length)
            if field.length >= 0
            else ctypes.string_at(value)
        )
        return raw.decode("utf-8", errors="replace")
    return None


def _on_structured_warning(
    level: GLib.LogLevelFlags,
    fields,
    _field_count: int | None = None,
    user_data: object = None,
) -> GLib.LogWriterOutput:
    values = {field.key: field for field in fields}
    domain = _field_text(values.get("GLIB_DOMAIN"))
    message = _field_text(values.get("MESSAGE"))
    if bool(level & GLib.LogLevelFlags.LEVEL_WARNING) and is_known_warning(
        domain, message
    ):
        return GLib.LogWriterOutput.HANDLED
    return GLib.log_writer_default(level, fields, user_data)


def install_known_warning_filter() -> None:
    """Install the narrow warning filter once for the application run."""
    if os.environ.get("MD_SPLIT_SHOW_WARNINGS") == "1":
        return
    for domain in ("Gtk", "Gdk", "libenchant"):
        GLib.log_set_handler(
            domain, GLib.LogLevelFlags.LEVEL_WARNING, _on_known_warning
        )
    # Per-domain handlers are ignored when structured logging is enabled.
    GLib.log_set_writer_func(_on_structured_warning, None)
