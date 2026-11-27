"""Headless tests for source-editor link detection and resolution."""

import os
import sys
import tempfile
from pathlib import Path

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

from gi.repository import Gio

from src import paths
from src.links import link_at_offset, links_in_text


def offset_of(text, needle, delta=0):
    return text.index(needle) + delta


def assert_link(text, needle, start, end, destination, delta=0):
    link = link_at_offset(text, offset_of(text, needle, delta))
    assert link is not None, (text, needle)
    assert (link.start, link.end) == (start, end), (text, link)
    assert link.destination == destination, (text, link)


# Inline links cover the whole construct, including the destination.
text = "See [the docs](https://example.com/guide)."
assert_link(text, "the docs", 4, len(text) - 1, "https://example.com/guide")
assert_link(text, "https://", 4, len(text) - 1, "https://example.com/guide")
assert link_at_offset(text, 3) is None
assert link_at_offset(text, len(text)) is None
assert link_at_offset(text, -1) is None

# Angle autolinks, bare GFM autolinks and bare email addresses.
text = "Read <https://example.com> or https://example.org/a or me@example.com"
assert_link(text, "<https", 5, 26, "https://example.com")
assert_link(text, "https://example.org", 30, 51, "https://example.org/a")
assert_link(text, "me@", 55, 69, "mailto:me@example.com")
assert_link("<foo@localhost>", "foo@", 0, 15, "mailto:foo@localhost")
text = "Visit www.example.com."
assert_link(text, "www.", 6, 21, "http://www.example.com")
assert text
assert link_at_offset("https://example.com.", len("https://example.com.")) is None

# Trailing punctuation and closing parentheses stay outside bare links.
text = "(https://example.com/path_(x))"
assert_link(text, "https://", 1, 29, "https://example.com/path_(x)")

# Full, collapsed and shortcut reference links resolve from the document.
reference = "\n\n[r]: notes/guide.md"
text = "[the guide][r]" + reference
assert_link(text, "the guide", 0, len("[the guide][r]"), "notes/guide.md")
text = "[the guide][]" + reference.replace("[r]", "[the guide]")
assert_link(
    text, "the guide", 0, len("[the guide][]"), "notes/guide.md"
)
text = "[the guide]" + reference.replace("[r]", "[the guide]")
assert_link(text, "the guide", 0, len("[the guide]"), "notes/guide.md")
text = "[missing][nope]" + reference
assert link_at_offset(text, 2) is None

# Definitions inside block quotes and list items are used; a definition-like
# line inside a paragraph is not.
text = "[quote]\n\n> [r]: quoted.md\n\n[r]"
link = link_at_offset(text, text.rindex("[r]") + 1)
assert link is not None and link.destination == "quoted.md"
text = "[item]\n\n- [r]: listed.md\n\n[r]"
link = link_at_offset(text, text.rindex("[r]") + 1)
assert link is not None and link.destination == "listed.md"
text = "paragraph\n[r]: inline.md\n\n[r]"
assert link_at_offset(text, text.rindex("[r]") + 1) is None

# Reference definitions are not links themselves.
text = "[the guide]" + reference
assert link_at_offset(text, offset_of(text, "[r]:")) is None
text = "    [r]: notes.md"
assert link_at_offset(text, offset_of(text, "[r]")) is None

# Nested labels and formatted labels keep the outer link range.
assert_link(
    "[outer [inner] label](target.md)",
    "inner",
    0,
    len("[outer [inner] label](target.md)"),
    "target.md",
)
assert_link("[**bold**](target.md)", "bold", 0, 21, "target.md")

# Inline code and fenced or indented code blocks suppress links.
assert link_at_offset("`[x](target.md)`", 2) is None
assert link_at_offset("```\n[x](target.md)\n```", 5) is None
assert link_at_offset("    [x](target.md)", 5) is None
assert link_at_offset("~~~\n[x](target.md)\n~~~", 5) is None

# Escapes and images are not links.
assert link_at_offset(r"\[x](target.md)", 2) is None
assert link_at_offset("![alt](image.png)", 2) is None
assert link_at_offset("\\<https://example.com>", 2) is None

# Block containers do not confuse offsets.
text = "> quote [link](target.md)\n\n- item [other](other.md)"
assert_link(text, "link", 8, 25, "target.md")
assert_link(text, "other", 34, 51, "other.md")

# Unsupported destinations are still detected so the window can report them.
link = link_at_offset("[x](ftp://example.com)", 1)
assert link is not None and link.destination == "ftp://example.com"
assert paths.resolve_link(link.destination, None, None) is None

# Percent-encoding and angle destinations match the parser's output.
link = link_at_offset("[x](<a b>)", 1)
assert link is not None and link.destination == "a%20b"

# Link resolution keeps contained paths, fragments and external schemes.
assert [link.destination for link in links_in_text(
    "[a](one.md), <https://two.example>, [b][r]\n\n[r]: three.md"
)] == ["one.md", "https://two.example", "three.md"]

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    (root / "notes").mkdir()
    (root / "notes" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    document = root / "doc.md"
    document.write_text("# Doc\n", encoding="utf-8")
    base_file = Gio.File.new_for_path(str(document))

    assert paths.resolve_link("notes/guide.md", None, base_file) == (
        root / "notes" / "guide.md"
    ).as_uri()
    assert paths.resolve_link("notes/guide.md", root, None) == (
        root / "notes" / "guide.md"
    ).as_uri()
    assert paths.resolve_link(
        "notes/guide.md?raw=1#top", root, None
    ) == (root / "notes" / "guide.md").as_uri() + "?raw=1#top"
    assert paths.resolve_link("https://example.com/a", root, None) == (
        "https://example.com/a"
    )
    assert paths.resolve_link("mailto:me@example.com", root, None) == (
        "mailto:me@example.com"
    )
    assert paths.resolve_link("#section", root, None) == "#section"
    assert paths.resolve_link("../outside.md", root, None) is None
    assert paths.resolve_link("javascript:alert(1)", root, None) is None
    assert paths.resolve_link("vbscript:msgbox(1)", root, None) is None
    assert paths.resolve_link("data:text/plain,x", root, None) is None
    assert paths.resolve_link("ftp://example.com", root, None) is None
    assert paths.resolve_link("//example.com/path", root, None) is None
    assert paths.resolve_link("", root, None) is None

    image = root / "notes" / "a b#(diagram).png"
    assert paths.markdown_image_path(image, root) == (
        "notes/a%20b%23%28diagram%29.png"
    )
    outside = root.parent / "outside image.png"
    assert paths.markdown_image_path(outside, root) == (
        outside.as_posix().replace(" ", "%20")
    )
    assert paths.markdown_image_path(image, None) == (
        image.as_posix()
        .replace(" ", "%20")
        .replace("#", "%23")
        .replace("(", "%28")
        .replace(")", "%29")
    )

print("ok: link helpers")
