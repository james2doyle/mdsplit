"""Smoke-test the native GTK Markdown renderer.

Run via `meson test`; skips when no display is available.
"""

import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GLib", "2.0")
from gi.repository import Adw, Gdk, GLib, Gtk

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    print("SKIP: no display available")
    sys.exit(77)

Adw.init()

from src.renderer import MarkdownRenderer


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def labels(root):
    return [widget for widget in walk(root) if isinstance(widget, Gtk.Label)]


def all_text(root):
    values = []
    for label in labels(root):
        for getter in ("get_text", "get_label"):
            try:
                value = getattr(label, getter)()
            except Exception:
                continue
            if value:
                values.append(value)
    return "\n".join(values)


def wait_for(condition, timeout_ms=4000):
    loop = GLib.MainLoop()
    state = {"timed_out": False}

    def poll():
        if condition():
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
    assert not state["timed_out"], "timed out waiting for local image"


def make_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
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


SAMPLE = """# Native Markdown

A paragraph with **bold**, *italic*, ~~strike~~, `inline code`, a
[link](https://example.com) and a bare https://gnome.org autolink.

> A quoted paragraph with *emphasis*.

- [x] Completed task
- [ ] Open task
- Nested list
  - Child item

1. First
2. Second

| Name | Value |
|:-----|------:|
| Alpha | 1 |
| Beta | 2 |

```python
print("native")
```

---

> [!NOTE]
> GitHub alerts render as native boxes.

Footnote reference[^note].

[^note]: Footnote text.

![Tiny dot](dot.png)

![Remote dot](https://127.0.0.1:9/not-fetched.png)
"""

with tempfile.TemporaryDirectory() as tmp:
    with open(os.path.join(tmp, "dot.png"), "wb") as image_file:
        image_file.write(make_png(40, 24))

    renderer = MarkdownRenderer()
    root = renderer.render(SAMPLE, base_dir=Path(tmp))
    wait_for(lambda: not renderer._local_pending)

    assert isinstance(root, Gtk.Box)
    assert root.get_first_child() is not None

    text = all_text(root)
    for expected in (
        "Native Markdown",
        "bold",
        "italic",
        "strike",
        "inline code",
        "Completed task",
        "Open task",
        "Child item",
        'print("native")',
        "GitHub alerts render as native boxes.",
        "Footnote text.",
        "Tiny dot",
        "Remote dot",
        "Remote image not loaded",
        "Name",
        "Alpha",
    ):
        assert expected in text, f"missing text: {expected!r}\n---\n{text}"
    assert "Loading image" not in text

    check_buttons = [w for w in walk(root) if isinstance(w, Gtk.CheckButton)]
    assert len(check_buttons) == 2, len(check_buttons)
    assert check_buttons[0].get_active() is True
    assert check_buttons[1].get_active() is False
    for button in check_buttons:
        assert button.get_valign() == Gtk.Align.CENTER
        content = button.get_next_sibling()
        assert isinstance(content, Gtk.Box)
        assert content.get_valign() == Gtk.Align.BASELINE_CENTER
        assert content.get_baseline_child() == 0

    grids = [w for w in walk(root) if isinstance(w, Gtk.Grid)]
    assert grids, "table grid missing"
    assert any("md-table" in grid.get_css_classes() for grid in grids)

    pictures = [w for w in walk(root) if isinstance(w, Gtk.Picture)]
    assert pictures, "image picture missing"
    assert pictures[0].get_size_request()[1] == 24, pictures[0].get_size_request()

    from src import renderer as renderer_module

    original_limit = renderer_module._MAX_IMAGE_PIXELS
    renderer_module._MAX_IMAGE_PIXELS = 100
    try:
        oversized = MarkdownRenderer().render(
            "![Too many pixels](dot.png)", base_dir=Path(tmp)
        )
    finally:
        renderer_module._MAX_IMAGE_PIXELS = original_limit
    assert not [
        widget for widget in walk(oversized) if isinstance(widget, Gtk.Picture)
    ]
    assert "Image is too large" in all_text(oversized)

    # Preview images cannot escape the document directory either.
    escaped_image = Path(tmp).parent / f"md-split-escaped-{os.getpid()}.png"
    escaped_image.write_bytes(make_png(8, 8))
    try:
        escaped_renderer = MarkdownRenderer()
        escaped_root = escaped_renderer.render(
            f"![Escaped](../{escaped_image.name})", base_dir=Path(tmp)
        )
    finally:
        escaped_image.unlink()
    assert not escaped_renderer._local_pending
    assert not [
        widget for widget in walk(escaped_root) if isinstance(widget, Gtk.Picture)
    ]
    assert "[Image: Escaped]" in all_text(escaped_root)

    code_labels = [
        w
        for w in labels(root)
        if "md-code-body" in w.get_css_classes()
    ]
    assert code_labels, "code block missing"
    assert code_labels[0].get_text().strip() == 'print("native")'
    wait_for(lambda: "<span" in (code_labels[0].get_label() or ""))
    light_code_markup = code_labels[0].get_label() or ""
    assert "<span" in light_code_markup

    dark_code_labels = [
        w
        for w in labels(
            MarkdownRenderer().render(
                '```python\nprint("native")\n```', dark=True
            )
        )
        if "md-code-body" in w.get_css_classes()
    ]
    wait_for(lambda: "<span" in (dark_code_labels[0].get_label() or ""))
    assert (dark_code_labels[0].get_label() or "") != light_code_markup

    unknown_labels = [
        w
        for w in labels(
            MarkdownRenderer().render("```not-a-language\n<&>\n```")
        )
        if "md-code-body" in w.get_css_classes()
    ]
    assert unknown_labels[0].get_text() == "<&>"
    assert "<span" not in (unknown_labels[0].get_label() or "")

    markups = "\n".join(
        label.get_label() or "" for label in labels(root)
    )
    assert 'href="https://example.com"' in markups
    assert 'href="https://gnome.org"' in markups
    assert 'href="#fn-0"' in markups
    assert "<s>strike</s>" in markups

    # Inline code keeps its background in prose but not inside table cells.
    table_code = MarkdownRenderer().render(
        "| Code |\n| --- |\n| `x` |"
    )
    cells = [
        label
        for label in labels(table_code)
        if "md-table-cell" in label.get_css_classes()
    ]
    assert cells, "table cells missing"
    cell_markup = "\n".join(label.get_label() or "" for label in cells)
    assert 'font_family="monospace"' in cell_markup, cell_markup
    assert "background=" not in cell_markup, cell_markup

    prose_code = MarkdownRenderer().render("`x`")
    prose_markup = "\n".join(
        label.get_label() or "" for label in labels(prose_code)
    )
    assert 'background="' in prose_markup, prose_markup

    # A link whose only content is an image renders the image, clickable.
    opened = []
    linked_renderer = MarkdownRenderer()
    linked = linked_renderer.render(
        "[![Linked dot](dot.png)](https://example.com)",
        base_dir=Path(tmp),
        on_external_link=opened.append,
    )
    wait_for(lambda: not linked_renderer._local_pending)
    linked_pictures = [
        widget for widget in walk(linked) if isinstance(widget, Gtk.Picture)
    ]
    assert len(linked_pictures) == 1, linked_pictures
    assert linked_pictures[0].get_size_request()[1] == 24
    linked_buttons = [
        widget for widget in walk(linked) if isinstance(widget, Gtk.Button)
    ]
    assert len(linked_buttons) == 1, linked_buttons
    assert linked_buttons[0].get_child() is not None
    linked_buttons[0].emit("clicked")
    assert opened == ["https://example.com"], opened

    # GitHub numbers duplicate heading anchors from -1, not -2.
    slugs = MarkdownRenderer()
    slugs_root = slugs.render("## Foo\n\n## Foo\n\n## Foo")
    for name in ("foo", "foo-1", "foo-2"):
        assert slugs.get_anchor(name) is not None, name
    assert slugs.get_anchor("foo-3") is None
    assert slugs_root.get_first_child() is not None

    # Nested list items are indented relative to their parent item.
    nested = MarkdownRenderer().render("- parent\n  - child\n    - grandchild")
    list_boxes = [
        widget
        for widget in walk(nested)
        if "md-list" in widget.get_css_classes()
    ]
    assert len(list_boxes) == 3, list_boxes

    def indent(box):
        total = box.get_margin_start()
        parent = box.get_parent()
        while parent is not None:
            if "md-list" in parent.get_css_classes():
                total += parent.get_margin_start()
            parent = parent.get_parent()
        return total

    indents = [indent(box) for box in list_boxes]
    assert indents[0] == 0, indents
    assert indents[1] > indents[0], indents
    assert indents[2] > indents[1], indents

    # The plain-text markup fallback decodes escaped entities.
    fallback = MarkdownRenderer()._make_label("Tom &amp; Jerry < U")
    assert fallback.get_text() == "Tom & Jerry < U", fallback.get_text()

    assert "WebKit" not in sys.modules, "renderer must not use WebKit"

    from src.markdown_view import MarkdownView

    view = MarkdownView()
    view.renderer.render = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("oversized documents must not be parsed")
    )
    view.set_markdown("x" * 2_000_001)
    assert view._stack.get_visible_child_name() == "oversized"
    view.shutdown()

    print("ok: native renderer widgets")
