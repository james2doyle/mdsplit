# SPDX-License-Identifier: MIT

"""Pure text-editing helpers for the Markdown editor.

The helpers return a :class:`TextEdit` describing a replacement instead of
touching a GtkTextBuffer directly. That keeps the behavior testable without a
display and lets the window apply an edit as one undoable user action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AUTO_PAIR_ITEMS = (
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ('"', '"'),
    ("'", "'"),
    ("`", "`"),
    ("*", "*"),
    ("_", "_"),
    ("~", "~"),
)
AUTO_PAIRS = dict(_AUTO_PAIR_ITEMS)
AUTO_PAIR_CHARACTERS = frozenset(AUTO_PAIRS) | frozenset(AUTO_PAIRS.values())
_CLOSING_DELIMITERS = frozenset(AUTO_PAIRS.values())
SURROUND_PAIRS = {
    character: pair for pair in _AUTO_PAIR_ITEMS for character in pair
}
SURROUND_PAIRS.update(
    {
        "\u2018": ("\u2018", "\u2019"),
        "\u2019": ("\u2018", "\u2019"),
        "\u201c": ("\u201c", "\u201d"),
        "\u201d": ("\u201c", "\u201d"),
    }
)


@dataclass(frozen=True)
class SearchQuery:
    """Options for an in-document text search."""

    text: str
    match_case: bool = False
    whole_word: bool = False
    regex: bool = False


def compile_search(query: SearchQuery) -> re.Pattern[str] | None:
    """Compile a query, returning ``None`` for empty or invalid patterns."""
    if not query.text:
        return None
    pattern = query.text if query.regex else re.escape(query.text)
    if query.whole_word:
        pattern = rf"\b(?:{pattern})\b"
    flags = 0 if query.match_case else re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def count_matches(text: str, query: SearchQuery) -> int:
    pattern = compile_search(query)
    if pattern is None:
        return 0
    return sum(1 for _match in pattern.finditer(text))


def replace_all_text(
    text: str, query: SearchQuery, replacement: str
) -> tuple[str, int]:
    """Replace all matches and return the new text and match count."""
    pattern = compile_search(query)
    if pattern is None:
        return text, 0
    return pattern.subn(replacement, text)


@dataclass(frozen=True)
class TextEdit:
    """A replacement range and the selection to restore afterward."""

    start: int
    end: int
    replacement: str
    selection_start: int
    selection_end: int

    def apply(self, text: str) -> str:
        return text[: self.start] + self.replacement + text[self.end :]


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """Return source ranges occupied by fenced blocks, including delimiters."""
    ranges: list[tuple[int, int]] = []
    fence: tuple[str, int] | None = None
    fence_start = 0
    line_start = 0
    while line_start < len(text):
        newline = text.find("\n", line_start)
        line_end = len(text) if newline == -1 else newline
        line = text[line_start:line_end]
        if line.endswith("\r"):
            line = line[:-1]
        if fence is None:
            match = _FENCE_OPEN.match(line)
            if match is not None:
                marker, info = match.groups()
                if marker[0] != "`" or "`" not in info:
                    fence = (marker[0], len(marker))
                    fence_start = line_start
        else:
            character, length = fence
            closing = re.match(
                rf"^ {{0,3}}{re.escape(character)}{{{length},}}[ \t]*$",
                line,
            )
            if closing is not None:
                ranges.append((fence_start, line_end))
                fence = None
        if newline == -1:
            break
        line_start = newline + 1
    if fence is not None:
        ranges.append((fence_start, len(text)))
    return ranges


def _in_ranges(position: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= position <= end for start, end in ranges)


def _backtick_runs(
    text: str, cursor: int, fenced_ranges: list[tuple[int, int]]
) -> list[tuple[int, int, bool]]:
    """Find backtick runs, splitting the run under the cursor at that point."""
    runs: list[tuple[int, int, bool]] = []
    position = 0
    fence_index = 0
    while position < len(text):
        while (
            fence_index < len(fenced_ranges)
            and position > fenced_ranges[fence_index][1]
        ):
            fence_index += 1
        if fence_index < len(fenced_ranges):
            fence_start, fence_end = fenced_ranges[fence_index]
            if fence_start <= position <= fence_end:
                position = fence_end + 1
                continue
        if text[position] != "`":
            position += 1
            continue
        start = position
        while position < len(text) and text[position] == "`":
            position += 1
        end = position
        backslashes = 0
        previous = start - 1
        while previous >= 0 and text[previous] == "\\":
            backslashes += 1
            previous -= 1
        escaped = backslashes % 2 == 1
        if start < cursor < end:
            runs.append((start, cursor, escaped))
            runs.append((cursor, end, escaped))
        else:
            runs.append((start, end, escaped))
    return runs


def _in_code_context(text: str, cursor: int) -> bool:
    """Return whether ``cursor`` is in a fenced block or inline code span."""
    fenced_ranges = _fenced_ranges(text)
    if _in_ranges(cursor, fenced_ranges):
        return True
    runs = _backtick_runs(text, cursor, fenced_ranges)
    next_same: list[int | None] = [None] * len(runs)
    next_by_length: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        start, end, _escaped = runs[index]
        length = end - start
        next_same[index] = next_by_length.get(length)
        next_by_length[length] = index

    index = 0
    while index < len(runs):
        start, end, escaped = runs[index]
        if start > cursor:
            break
        closing_index = next_same[index]
        if escaped or closing_index is None:
            index += 1
            continue
        closing_start = runs[closing_index][0]
        if end <= cursor <= closing_start:
            return True
        # Backtick runs inside a code span are literal, not nested delimiters.
        index = closing_index + 1
    return False


def auto_pair(text: str, cursor: int, character: str) -> TextEdit | None:
    """Insert a delimiter pair when the cursor context allows completion."""
    closing = AUTO_PAIRS.get(character)
    if closing is None or not 0 <= cursor <= len(text):
        return None
    next_character = text[cursor : cursor + 1]
    if next_character and (next_character.isalnum() or next_character == "_"):
        return None
    if _in_code_context(text, cursor):
        return None
    return TextEdit(
        start=cursor,
        end=cursor,
        replacement=character + closing,
        selection_start=cursor + 1,
        selection_end=cursor + 1,
    )


def skip_closing_pair(text: str, cursor: int, character: str) -> bool:
    """Return whether typing a closing delimiter should only move the cursor."""
    if character not in _CLOSING_DELIMITERS or not 0 <= cursor <= len(text):
        return False
    return text[cursor : cursor + 1] == character


def delete_empty_pair(text: str, cursor: int) -> TextEdit | None:
    """Delete both delimiters when Backspace is pressed inside an empty pair."""
    if cursor <= 0 or cursor >= len(text):
        return None
    closing = AUTO_PAIRS.get(text[cursor - 1])
    if closing is None or text[cursor] != closing:
        return None
    return TextEdit(
        start=cursor - 1,
        end=cursor + 1,
        replacement="",
        selection_start=cursor - 1,
        selection_end=cursor - 1,
    )


def surround_selection(
    text: str, start: int, end: int, character: str
) -> TextEdit | None:
    """Wrap a non-empty selection with the pair for ``character``."""
    if start == end:
        return None
    pair = SURROUND_PAIRS.get(character)
    if pair is None:
        return None

    start, end = sorted((start, end))
    opening, closing = pair
    selected = text[start:end]
    return TextEdit(
        start=start,
        end=end,
        replacement=f"{opening}{selected}{closing}",
        selection_start=start + len(opening),
        selection_end=start + len(opening) + len(selected),
    )


def complete_link(text: str, cursor: int) -> TextEdit | None:
    """Complete a bare ``[`` with ``]()`` and place the cursor in the parens."""
    if cursor <= 0 or cursor > len(text):
        return None
    if text[cursor - 1 : cursor] != "[":
        return None
    return TextEdit(
        start=cursor,
        end=cursor + (1 if text[cursor : cursor + 1] == "]" else 0),
        replacement="]()",
        selection_start=cursor + 2,
        selection_end=cursor + 2,
    )


def format_inline(
    text: str, start: int, end: int, opening: str, closing: str
) -> TextEdit:
    """Wrap a selection, or insert an empty pair at the cursor."""
    start, end = sorted((start, end))
    selected = text[start:end]
    return TextEdit(
        start=start,
        end=end,
        replacement=f"{opening}{selected}{closing}",
        selection_start=start + len(opening),
        selection_end=start + len(opening) + len(selected),
    )


def format_code(text: str, start: int, end: int) -> TextEdit:
    """Use inline code for one line and a fenced block for multiple lines."""
    start, end = sorted((start, end))
    selected = text[start:end]
    if "\n" in selected:
        return TextEdit(
            start=start,
            end=end,
            replacement=f"```\n{selected}\n```",
            selection_start=start + 4,
            selection_end=start + 4 + len(selected),
        )
    return format_inline(text, start, end, "`", "`")


def toggle_blockquote(text: str, start: int, end: int) -> TextEdit:
    """Toggle a ``> `` prefix on the lines touched by the selection."""
    start, end = sorted((start, end))
    end = min(end, len(text))
    # A selection that ends exactly on a line break belongs to the line before
    # it (the triple-click gesture); do not quote the following line.
    if start < end and text[end - 1] == "\n":
        end -= 1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)

    lines = text[line_start:line_end].split("\n")
    content_lines = [line for line in lines if line.strip()]
    quoted = bool(content_lines) and all(
        line.startswith("> ") for line in content_lines
    )
    if quoted:
        updated = [
            line[2:] if line.startswith("> ") else line for line in lines
        ]
    else:
        updated = ["> " + line if line.strip() else ">" for line in lines]

    replacement = "\n".join(updated)
    return TextEdit(
        start=line_start,
        end=line_end,
        replacement=replacement,
        selection_start=line_start,
        selection_end=line_start + len(replacement),
    )


_BULLET_ITEM = re.compile(r"^([ \t]*)([-+*])([ \t]+)(.*)$")
_TASK_MARKER = re.compile(r"^(\[[ xX]\][ \t]+)(.*)$")
_ORDERED_ITEM = re.compile(r"^([ \t]*)(\d+)([.)])([ \t]+)(.*)$")


def _line_ending(text: str, line_end: int) -> str:
    """Return the line ending that terminates (or precedes) a line."""
    if line_end < len(text) and text[line_end] == "\n":
        return "\r\n" if line_end > 0 and text[line_end - 1] == "\r" else "\n"
    previous = text.rfind("\n", 0, line_end)
    if previous > 0 and text[previous - 1] == "\r":
        return "\r\n"
    return "\n"


def continue_list(text: str, cursor: int) -> TextEdit | None:
    """Continue a list item, or exit an empty item, when Return is pressed."""
    if cursor < 0 or cursor > len(text):
        return None

    line_start = text.rfind("\n", 0, cursor) + 1
    newline = text.find("\n", cursor)
    line_end = len(text) if newline == -1 else newline
    eol = _line_ending(text, line_end)
    content_end = line_end
    if content_end > line_start and text[content_end - 1] == "\r":
        content_end -= 1
    line = text[line_start:content_end]
    relative = min(cursor - line_start, len(line))
    insert_at = line_start + relative

    bullet = _BULLET_ITEM.match(line)
    ordered = _ORDERED_ITEM.match(line)
    if bullet is not None:
        indent, marker, _spaces, rest = bullet.groups()
        marker_end = len(indent) + len(marker) + len(_spaces)
        task = _TASK_MARKER.match(rest)
        if task is not None:
            task_marker, content = task.groups()
            marker_end += len(task_marker)
            next_marker = f"{indent}{marker} [ ] "
        else:
            content = rest
            next_marker = f"{indent}{marker} "
    elif ordered is not None:
        indent, number, delimiter, _spaces, content = ordered.groups()
        marker_end = (
            len(indent) + len(number) + len(delimiter) + len(_spaces)
        )
        try:
            number = int(number) + 1
        except ValueError:
            return None
        next_marker = f"{indent}{number}{delimiter} "
    else:
        return None

    if relative < marker_end:
        return None

    if not content.strip():
        end = newline + 1 if newline != -1 else line_end
        return TextEdit(
            start=line_start,
            end=end,
            replacement=eol,
            selection_start=line_start + len(eol),
            selection_end=line_start + len(eol),
        )

    replacement = eol + next_marker
    return TextEdit(
        start=insert_at,
        end=insert_at,
        replacement=replacement,
        selection_start=insert_at + len(replacement),
        selection_end=insert_at + len(replacement),
    )
