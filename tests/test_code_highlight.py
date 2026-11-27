"""Headless tests for native preview code-block highlighting."""

import os
import sys
from unittest import mock

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

import gi

gi.require_version("GLib", "2.0")
gi.require_version("GtkSource", "5")
gi.require_version("Pango", "1.0")

from gi.repository import GLib, Pango

from src import code_highlight


class RecordingLabel:
    def __init__(self):
        self.markup = None

    def set_markup(self, markup):
        self.markup = markup


def wait_for_markup(label, timeout_ms=3000):
    state = {"timed_out": False}

    def poll():
        if label.markup is not None:
            loop.quit()
            return False
        return True

    def timeout():
        state["timed_out"] = True
        loop.quit()
        return False

    loop = GLib.MainLoop()
    GLib.timeout_add(10, poll)
    timeout_id = GLib.timeout_add(timeout_ms, timeout)
    loop.run()
    if not state["timed_out"]:
        GLib.source_remove(timeout_id)
    assert not state["timed_out"], "timed out waiting for code highlighting"


def language_id(info):
    language = code_highlight.resolve_language(info)
    assert language is not None
    return language.get_id()


assert language_id("PyThOn") == "python"
assert language_id("javascript") == "js"
assert language_id("py") == "python"
assert language_id("yml") == "yaml"
assert code_highlight.resolve_language("not-a-language") is None
assert code_highlight.resolve_language("text") is None

code = 'print("<native>")\n# comment'
label = RecordingLabel()
assert code_highlight.highlight_label(label, code, "python", dark=False)
wait_for_markup(label)
assert label.markup is not None
assert "<span" in label.markup, label.markup
assert "&lt;native&gt;" in label.markup, label.markup
_valid, _attributes, text, _accelerator = Pango.parse_markup(
    label.markup, -1, "\0"
)
assert _valid
assert text == code

dark_label = RecordingLabel()
assert code_highlight.highlight_label(dark_label, code, "python", dark=True)
wait_for_markup(dark_label)
assert dark_label.markup != label.markup

unknown = RecordingLabel()
assert not code_highlight.highlight_label(
    unknown, "plain code", "not-a-language", dark=False
)
assert unknown.markup is None

oversized = RecordingLabel()
assert not code_highlight.highlight_label(
    oversized,
    "x" * (code_highlight.MAX_HIGHLIGHT_CHARS + 1),
    "python",
    dark=False,
)
assert oversized.markup is None

unsupported = RecordingLabel()
with mock.patch.object(code_highlight, "_markup_supported", return_value=False):
    assert not code_highlight.highlight_label(
        unsupported, "print('plain')", "python", dark=False
    )
assert unsupported.markup is None

print("ok: native code highlighting")
