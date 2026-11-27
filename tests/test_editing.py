"""Headless tests for text editing helpers used by the editor."""

import os
import sys

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

from src.editing import (
    AUTO_PAIRS,
    SearchQuery,
    TextEdit,
    auto_pair,
    complete_link,
    count_matches,
    delete_empty_pair,
    duplicate_line_or_selection,
    format_code,
    format_inline,
    insert_image,
    insert_link,
    move_lines,
    parse_line_target,
    replace_all_text,
    skip_closing_pair,
    surround_selection,
    toggle_blockquote,
)


def apply(text, edit):
    assert edit is not None
    return edit.apply(text)


# Surround mappings include brackets, braces, parentheses, backticks and
# ASCII/typographic quotes.
for opening in ("[", "(", "{", "`", '"', "'", "\u2018", "\u201c"):
    edit = surround_selection("hello", 1, 4, opening)
    assert edit is not None, opening
    wrapped = apply("hello", edit)
    assert wrapped.startswith("h" + opening), (opening, wrapped)
    assert wrapped.endswith(edit.replacement[-1] + "o"), (opening, wrapped)
    assert "ell" in wrapped

assert apply("hello", surround_selection("hello", 1, 4, "[")) == "h[ell]o"
assert apply("hello", surround_selection("hello", 1, 4, "]")) == "h[ell]o"
assert surround_selection("hello", 2, 2, "[") is None
assert surround_selection("hello", 1, 4, "x") is None

# The selection remains selected inside the new pair.
edit = surround_selection("hello", 1, 4, "[")
assert edit is not None
assert (edit.selection_start, edit.selection_end) == (2, 5)

# Inline formatting wraps the selected text or inserts a cursor placeholder.
edit = format_inline("hello", 1, 4, "**", "**")
assert (edit.selection_start, edit.selection_end) == (3, 6)
assert apply("hello", edit) == "h**ell**o"

edit = format_inline("hello", 2, 2, "**", "**")
assert apply("hello", edit) == "he****llo"
assert (edit.selection_start, edit.selection_end) == (4, 4)

edit = format_inline("hello", 0, 5, "<u>", "</u>")
assert apply("hello", edit) == "<u>hello</u>"

# Inline code uses one backtick per side; multi-line selections use a fence.
edit = format_code("hello", 1, 4)
assert apply("hello", edit) == "h`ell`o"

edit = format_code("a\nb", 0, 3)
assert apply("a\nb", edit) == "```\na\nb\n```"

edit = format_code("hello", 2, 2)
assert apply("hello", edit) == "he``llo"
assert (edit.selection_start, edit.selection_end) == (3, 3)

# Block quotes add a marker to every line and toggle it back off.
edit = toggle_blockquote("one\ntwo", 0, 7)
assert apply("one\ntwo", edit) == "> one\n> two"

quoted = apply("one\ntwo", edit)
edit = toggle_blockquote(quoted, 0, len(quoted))
assert apply(quoted, edit) == "one\ntwo"

# A selection ending on a line break only quotes the line it ends in.
edit = toggle_blockquote("first\nsecond\nthird", 0, 6)
assert apply("first\nsecond\nthird", edit) == "> first\nsecond\nthird"
edit = toggle_blockquote("first\nsecond\nthird", 0, 7)
assert apply("first\nsecond\nthird", edit) == "> first\n> second\nthird"

# Ctrl+` uses a fence when the selection already spans multiple lines.
edit = format_code("a\nb", 0, 3)
assert apply("a\nb", edit).startswith("```\n")

# Typing ] after an empty [ completes a link and places the cursor in ().
edit = complete_link("abc [", 5)
assert edit is not None
completed = apply("abc [", edit)
assert completed == "abc []()"
assert (edit.selection_start, edit.selection_end) == (7, 7)
assert complete_link("abc", 3) is None
assert complete_link("abc [x", 6) is None
assert complete_link("abc  [", 6) is not None

# Link completion consumes the closing bracket inserted by auto-pairing.
edit = complete_link("abc []", 5)
assert edit is not None
assert apply("abc []", edit) == "abc []()"
assert (edit.selection_start, edit.selection_end) == (7, 7)

# Link insertion focuses the empty label or the URL after a selected label.
edit = insert_link("", 0, 0)
assert apply("", edit) == "[]()"
assert (edit.selection_start, edit.selection_end) == (1, 1)
edit = insert_link("hello", 1, 4)
assert apply("hello", edit) == "h[ell]()o"
assert (edit.selection_start, edit.selection_end) == (7, 7)
edit = insert_link("a [label]", 0, 9)
assert apply("a [label]", edit) == r"[a \[label\]]()"
assert edit.selection_start == len(edit.replacement) - 1

# Image insertion uses a prepared destination and selection as escaped alt text.
edit = insert_image("", 0, 0, "assets/a%20b.png")
assert apply("", edit) == "![](assets/a%20b.png)"
assert edit.selection_start == len(edit.replacement)
edit = insert_image("diagram", 0, 7, "assets/chart.png")
assert apply("diagram", edit) == "![diagram](assets/chart.png)"
assert edit.selection_start == len(edit.replacement)
edit = insert_image("a [diagram]", 0, 11, "assets/chart.png")
assert apply("a [diagram]", edit) == r"![a \[diagram\]](assets/chart.png)"

# Every opening delimiter inserts its partner and leaves the cursor inside.
for opening, closing in AUTO_PAIRS.items():
    edit = auto_pair("", 0, opening)
    assert edit is not None, opening
    assert apply("", edit) == opening + closing
    assert (edit.selection_start, edit.selection_end) == (1, 1)

    edit = auto_pair("- prose ", 8, opening)
    assert edit is not None, opening
    assert apply("- prose ", edit) == f"- prose {opening}{closing}"
    assert skip_closing_pair(opening + closing, 1, closing), opening
    assert not skip_closing_pair(opening + closing, 2, closing), opening

    edit = delete_empty_pair(opening + closing, 1)
    assert edit is not None, opening
    assert apply(opening + closing, edit) == ""
    assert (edit.selection_start, edit.selection_end) == (0, 0)

# Pairing preserves surrounding prose and places the cursor between delimiters.
edit = auto_pair("hello world", 5, "(")
assert edit is not None
assert apply("hello world", edit) == "hello() world"
assert (edit.selection_start, edit.selection_end) == (6, 6)

# A word character or underscore immediately after the cursor blocks pairing.
for opening in AUTO_PAIRS:
    assert auto_pair("word", 0, opening) is None, opening
    assert auto_pair("1", 0, opening) is None, opening
    assert auto_pair("_", 0, opening) is None, opening
    assert auto_pair("é", 0, opening) is None, opening
    assert auto_pair(" ", 0, opening) is not None, opening

# No pair is inserted inside inline code, including multi-backtick spans.
for opening in AUTO_PAIRS:
    assert auto_pair("`code`", 3, opening) is None, opening
    assert auto_pair("`` `code` ``", 6, opening) is None, opening
    assert auto_pair("``", 1, opening) is None, opening
    assert auto_pair("```\ncode\n```", 5, opening) is None, opening
    assert auto_pair("~~~\ncode\n~~~", 5, opening) is None, opening

# Opening and closing fence lines are protected, but following prose is not.
fenced = "```python\ncode\n```\nprose"
assert auto_pair(fenced, 2, "(") is None
assert auto_pair(fenced, fenced.index("code") + 2, "(") is None
assert auto_pair(fenced, len(fenced), "(") is not None
assert auto_pair("`code`\nprose", len("`code`\nprose"), "(") is not None
assert auto_pair(r"\`lit er al\`", 5, "(") is not None
assert auto_pair("`un closed", 3, "(") is not None

# Cursor skipping and pair deletion never duplicate or leave a stray delimiter.
assert skip_closing_pair("a)b", 1, ")")
assert not skip_closing_pair("a)b", 1, "]")
assert not skip_closing_pair("a[b", 1, "[")
assert not skip_closing_pair("abc", 4, ")")
assert delete_empty_pair("a(b)", 2) is None
assert delete_empty_pair("a(b)", 1) is None
assert delete_empty_pair("abc", 0) is None
assert delete_empty_pair("abc", 4) is None
assert auto_pair("abc", -1, "(") is None
assert auto_pair("abc", 4, "(") is None
assert auto_pair("abc", 1, "x") is None

# Duplication preserves the original cursor or selection and inserts one copy.
edit = duplicate_line_or_selection("one\ntwo", 2, 2)
assert apply("one\ntwo", edit) == "one\none\ntwo"
assert (edit.selection_start, edit.selection_end) == (2, 2)
edit = duplicate_line_or_selection("one\ntwo", 6, 6)
assert apply("one\ntwo", edit) == "one\ntwo\ntwo"
assert (edit.selection_start, edit.selection_end) == (6, 6)
edit = duplicate_line_or_selection("one\r\ntwo", 6, 6)
assert apply("one\r\ntwo", edit) == "one\r\ntwo\r\ntwo"
assert apply("", duplicate_line_or_selection("", 0, 0)) == "\n"
edit = duplicate_line_or_selection("abc", 1, 2)
assert apply("abc", edit) == "abbc"
assert (edit.selection_start, edit.selection_end) == (1, 2)
edit = duplicate_line_or_selection("one\ntwo\nthree", 0, 8)
assert apply("one\ntwo\nthree", edit) == "one\ntwo\none\ntwo\nthree"
assert (edit.selection_start, edit.selection_end) == (0, 8)

# Moving one line down and back restores the source and cursor in one edit each.
source = "one\ntwo\nthree"
edit = move_lines(source, 2, 2, 1)
assert edit is not None
moved = apply(source, edit)
assert moved == "two\none\nthree"
assert (edit.selection_start, edit.selection_end) == (6, 6)
edit = move_lines(moved, 6, 6, -1)
assert edit is not None
assert apply(moved, edit) == source
assert (edit.selection_start, edit.selection_end) == (2, 2)

# Document boundaries are no-ops, and moving a final line transfers its EOL.
assert move_lines(source, 2, 2, -1) is None
assert move_lines(source, len(source), len(source), 1) is None
assert move_lines("\n", 1, 1, -1) is None
edit = move_lines("a\nb\n", 2, 2, 1)
assert edit is not None
assert apply("a\nb\n", edit) == "a\n\nb"
assert (edit.selection_start, edit.selection_end) == (3, 3)
edit = move_lines("a\n\nb", 3, 3, -1)
assert edit is not None
assert apply("a\n\nb", edit) == "a\nb\n"
edit = move_lines("one\ntwo", 6, 6, -1)
assert edit is not None
assert apply("one\ntwo", edit) == "two\none"
assert (edit.selection_start, edit.selection_end) == (2, 2)
edit = move_lines("one\ntwo", 2, 2, 1)
assert edit is not None
assert apply("one\ntwo", edit) == "two\none"
assert (edit.selection_start, edit.selection_end) == (6, 6)
edit = move_lines("one\r\ntwo", 2, 2, 1)
assert edit is not None
assert apply("one\r\ntwo", edit) == "two\r\none"

# Multi-line moves retain the selection, excluding a line at the end column 0.
source = "one\ntwo\nthree\nfour"
edit = move_lines(source, 1, 8, 1)
assert edit is not None
moved = apply(source, edit)
assert moved == "three\none\ntwo\nfour"
assert (edit.selection_start, edit.selection_end) == (7, 14)
edit = move_lines(moved, 7, 14, -1)
assert edit is not None
assert apply(moved, edit) == source
assert (edit.selection_start, edit.selection_end) == (1, 8)
edit = move_lines("one\ntwo", 0, 4, 1)
assert edit is not None
assert apply("one\ntwo", edit) == "two\none"
assert (edit.selection_start, edit.selection_end) == (4, 7)

# Go-to-line input is a one-based ASCII integer; range clamping is a UI concern.
assert parse_line_target("1") == 1
assert parse_line_target(" 42\n") == 42
assert parse_line_target("999999") == 999999
assert parse_line_target("9" * 5000) is None
for value in ("", " ", "0", "-1", "+1", "1.5", "one", "1 2", "１２"):
    assert parse_line_target(value) is None, value

# Search matching handles case, whole words, regex and empty patterns.
assert count_matches("One one ONE", SearchQuery("one")) == 3
assert (
    count_matches(
        "One one ONE", SearchQuery("one", match_case=True)
    )
    == 1
)
assert (
    count_matches(
        "one stone one", SearchQuery("one", whole_word=True)
    )
    == 2
)
assert (
    count_matches(
        r"item 1, item 22",
        SearchQuery(r"\d+", regex=True),
    )
    == 2
)
assert (
    count_matches(
        "One stone",
        SearchQuery("one", match_case=True, whole_word=True),
    )
    == 0
)
assert count_matches("anything", SearchQuery("", regex=True)) == 0
assert count_matches("anything", SearchQuery("[", regex=True)) == 0

# Replace-all is safe for zero-width matches and reports the count.
replaced, matches = replace_all_text(
    "a a a", SearchQuery("a"), "b"
)
assert (replaced, matches) == ("b b b", 3)
replaced, matches = replace_all_text(
    "abc", SearchQuery("^", regex=True), "-"
)
assert (replaced, matches) == ("-abc", 1)
replaced, matches = replace_all_text(
    "abc", SearchQuery("$", regex=True), "!"
)
assert (replaced, matches) == ("abc!", 1)
replaced, matches = replace_all_text(
    "abc", SearchQuery("[", regex=True), "-"
)
assert (replaced, matches) == ("abc", 0)

# Edits are immutable and carry an explicit replacement range.
edit = TextEdit(1, 2, "X", 2, 2)
assert edit.apply("abc") == "aXc"

print("ok: editing helpers")
