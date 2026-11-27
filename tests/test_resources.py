"""Headless tests for packaged gresource content."""

import os
import sys
from pathlib import Path

RESOURCE = os.environ.get("APP_RESOURCE")

if not RESOURCE or not os.path.exists(RESOURCE):
    print("SKIP: APP_RESOURCE is not set")
    sys.exit(77)

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

resource = Gio.Resource.load(RESOURCE)


def contents(path: str) -> bytes:
    return resource.lookup_data(path, Gio.ResourceLookupFlags.NONE).get_data()


demo = contents("/io/github/james2doyle/MdSplit/demo.md").decode("utf-8")
assert demo.startswith("# MD Split"), demo[:40]
assert len(demo) > 200
assert "Auto-pair delimiters" in demo
assert "Ctrl+Shift+D" in demo
assert "Go to Line" in demo
assert "Ctrl+Shift+X / Ctrl+D" in demo

preferences = contents(
    "/io/github/james2doyle/MdSplit/preferences-dialog.ui"
).decode("utf-8")
assert "switch_vim_mode" in preferences
assert "switch_auto_pairing" in preferences
assert "close_button" not in preferences

window = contents("/io/github/james2doyle/MdSplit/window.ui").decode("utf-8")
assert "view_menu" in window
assert "toast_overlay" in window
assert "app.go-to-line" in window
assert "app.move-lines-up" in window
assert "app.duplicate-line" in window
assert "app.toggle-task" in window

assert contents("/io/github/james2doyle/MdSplit/style.css")

icon = contents(
    "/io/github/james2doyle/MdSplit/icons/scalable/apps/io.github.james2doyle.MdSplit.svg"
).decode("utf-8")
assert 'id="background"' in icon
assert 'width="128"' in icon

symbolic = contents(
    "/io/github/james2doyle/MdSplit/icons/symbolic/apps/"
    "io.github.james2doyle.MdSplit-symbolic.svg"
).decode("utf-8")
assert "<svg" in symbolic
assert 'fill="#2e3436"' in symbolic

# Every source module must be installed by the explicit Meson file list.
source_root = Path(__file__).resolve().parent.parent
meson_file = (source_root / "src" / "meson.build").read_text(encoding="utf-8")
for module in sorted((source_root / "src").glob("*.py")):
    assert f"'{module.name}'" in meson_file, (
        f"{module.name} is missing from src/meson.build"
    )

# The export stylesheet lives beside exporting.py and is inlined at export.
assert (source_root / "src" / "export.css").is_file()
assert "'export.css'" in meson_file

print("ok: packaged resources")
