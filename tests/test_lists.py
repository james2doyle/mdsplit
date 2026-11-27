"""Headless tests for list continuation and empty-item exit."""

import os
import sys

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

from src.editing import continue_list, indent_list, toggle_task


def apply(text, edit):
    assert edit is not None
    return edit.apply(text)


# Return continues bullet, ordered, task, and nested list items.
edit = continue_list("- item", 6)
assert edit is not None
assert apply("- item", edit) == "- item\n- "
assert (edit.selection_start, edit.selection_end) == (9, 9)

edit = continue_list("1. item", 7)
assert edit is not None
assert apply("1. item", edit) == "1. item\n2. "

edit = continue_list("3) item", 7)
assert edit is not None
assert apply("3) item", edit) == "3) item\n4) "

edit = continue_list("- [x] done", 10)
assert edit is not None
assert apply("- [x] done", edit) == "- [x] done\n- [ ] "

edit = continue_list("  - nested", 10)
assert edit is not None
assert apply("  - nested", edit) == "  - nested\n  - "

for marker in ("*", "+"):
    source = f"{marker} item"
    edit = continue_list(source, len(source))
    assert edit is not None
    assert apply(source, edit) == f"{source}\n{marker} "

# Enter on an empty item removes it and leaves a blank line.
for source, cursor, expected in (
    ("- ", 2, "\n"),
    ("1. ", 3, "\n"),
    ("- [ ] ", 6, "\n"),
    ("- item\n- ", 9, "- item\n\n"),
    ("- item\n- \nnext", 9, "- item\n\nnext"),
):
    edit = continue_list(source, cursor)
    assert edit is not None, source
    assert apply(source, edit) == expected, source

# Cursor positions inside the marker or on non-list lines fall through.
assert continue_list("plain text", 10) is None
assert continue_list("- item", 1) is None
assert continue_list("", 0) is None
assert continue_list("-item", 5) is None

# Return in the middle of an item splits it and continues the list.
edit = continue_list("- one two", 4)
assert edit is not None
assert apply("- one two", edit) == "- on\n- e two"
assert (edit.selection_start, edit.selection_end) == (7, 7)

# Tabs, larger ordered numbers, and whitespace-only items are supported.
edit = continue_list("\t- item", 7)
assert edit is not None
assert apply("\t- item", edit) == "\t- item\n\t- "

# CRLF documents continue and exit items with CRLF line endings.
for source, cursor, expected in (
    ("- one\r\n- two\r\n", 5, "- one\r\n- \r\n- two\r\n"),
    ("- \r\n", 2, "\r\n"),
    ("- one\r\n- \r\nnext", 9, "- one\r\n\r\nnext"),
    ("1. one\r\n", 6, "1. one\r\n2. \r\n"),
):
    edit = continue_list(source, cursor)
    assert edit is not None, source
    assert apply(source, edit) == expected, (source, apply(source, edit))

edit = continue_list("10. item", 8)
assert edit is not None
assert apply("10. item", edit) == "10. item\n11. "

for source, cursor in (("-   ", 4), ("+   ", 4), ("- item\n-   ", 11)):
    edit = continue_list(source, cursor)
    assert edit is not None, source
    assert apply(source, edit) == ("- item\n\n" if "\n" in source else "\n")

# Tab indents a list item and all of its nested content as one subtree.
source = "- parent\n  - child\n    continuation\n- sibling"
edit = indent_list(source, 3, 3, 2)
assert edit is not None
indented = apply(source, edit)
assert indented == "  - parent\n    - child\n      continuation\n- sibling"
assert (edit.selection_start, edit.selection_end) == (5, 5)
edit = indent_list(indented, 5, 5, 2, outdent=True)
assert edit is not None
assert apply(indented, edit) == source
assert (edit.selection_start, edit.selection_end) == (3, 3)

# Ordered markers, numbering, task markers, and tabs survive indentation.
edit = indent_list("10. [ ] item", 5, 5, 2)
assert edit is not None
assert apply("10. [ ] item", edit) == "  10. [ ] item"
edit = indent_list("\t- item", 7, 7, 2, outdent=True)
assert edit is not None
assert apply("\t- item", edit) == "- item"
assert (edit.selection_start, edit.selection_end) == (6, 6)
edit = indent_list(" - item", 3, 3, 2, outdent=True)
assert edit is not None
assert apply(" - item", edit) == "- item"
assert indent_list("- item", 6, 6, 2, outdent=True) is None

# Mixed selections change only list items; a selection ending at column zero
# excludes the following item, and CRLF remains byte-for-byte intact.
source = "intro\n- one\nparagraph\n2. two\noutro"
start = source.index("- one")
end = source.index("2. two") + len("2. two")
edit = indent_list(source, start, end, 2)
assert edit is not None
assert apply(source, edit) == "intro\n  - one\nparagraph\n  2. two\noutro"
assert (edit.selection_start, edit.selection_end) == (start + 2, end + 4)
source = "- one\n- two\n- three"
edit = indent_list(source, 0, len("- one\n"), 2)
assert edit is not None
assert apply(source, edit) == "  - one\n- two\n- three"
source = "- one\r\n2. two"
edit = indent_list(source, 0, len(source), 2)
assert edit is not None
assert apply(source, edit) == "  - one\r\n  2. two"

# Non-list text and list-looking lines inside fences fall through to normal Tab.
assert indent_list("plain text", 0, 10, 2) is None
assert indent_list("```\n- item\n```", 6, 6, 2) is None

# Task toggling targets only the current item and preserves all other text.
for source, expected in (
    ("- [ ] task", "- [x] task"),
    ("- [x] task", "- [ ] task"),
    ("- [X] task", "- [ ] task"),
    ("10. [ ] task", "10. [x] task"),
    ("-   [ ]  task", "-   [x]  task"),
    ("- [ ] task\r\n", "- [x] task\r\n"),
):
    edit = toggle_task(source, 0)
    assert edit is not None, source
    assert apply(source, edit) == expected
    assert (edit.selection_start, edit.selection_end) == (0, 0)
source = "  - [x] done\n- [ ] next"
cursor = source.index("next")
edit = toggle_task(source, cursor)
assert edit is not None
assert apply(source, edit) == "  - [x] done\n- [x] next"
assert (edit.selection_start, edit.selection_end) == (cursor, cursor)
for source, cursor in (
    ("plain", 0),
    ("- item", 0),
    ("- [ ]task", 0),
    ("```\n- [ ] task\n```", 6),
):
    assert toggle_task(source, cursor) is None
assert toggle_task("- [ ] task", -1) is None
assert toggle_task("- [ ] task", len("- [ ] task") + 1) is None

print("ok: list continuation, indentation, and task toggles")
