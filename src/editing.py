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


def _clamped_range(text: str, start: int, end: int) -> tuple[int, int]:
    start = max(0, min(start, len(text)))
    end = max(0, min(end, len(text)))
    return min(start, end), max(start, end)


def _selected_line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Return complete lines touched by a cursor or selection."""
    line_start = text.rfind("\n", 0, start) + 1
    scan_end = end
    # A selection ending at column zero does not include that following line.
    if end > start and end > 0 and text[end - 1] == "\n":
        scan_end -= 1
    newline = text.find("\n", scan_end)
    line_end = len(text) if newline == -1 else newline + 1
    return line_start, line_end


def _split_line_ending(segment: str) -> tuple[str, str]:
    if segment.endswith("\r\n"):
        return segment[:-2], "\r\n"
    if segment.endswith("\n"):
        return segment[:-1], "\n"
    return segment, ""


def move_lines(
    text: str, start: int, end: int, direction: int
) -> TextEdit | None:
    """Move the selected lines one line up (``-1``) or down (``1``)."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    selection_start, selection_end = _clamped_range(text, start, end)
    block_start, block_end = _selected_line_span(
        text, selection_start, selection_end
    )
    block = text[block_start:block_end]
    relative_start = selection_start - block_start
    relative_end = selection_end - block_start

    if direction == -1:
        if block_start == 0:
            return None
        previous_start = text.rfind("\n", 0, block_start - 1) + 1
        previous = text[previous_start:block_start]
        block_body, block_eol = _split_line_ending(block)
        if block_eol:
            replacement = block + previous
        else:
            # The former last line needs the previous line's terminator; the
            # new last line must lose it rather than gaining a blank line.
            previous_body, previous_eol = _split_line_ending(previous)
            replacement = block_body + previous_eol + previous_body
        if replacement == text[previous_start:block_end]:
            return None
        return TextEdit(
            start=previous_start,
            end=block_end,
            replacement=replacement,
            selection_start=previous_start + relative_start,
            selection_end=previous_start + relative_end,
        )

    if block_end == len(text) and text.endswith("\n") and block_start < len(text):
        # GtkTextBuffer exposes an empty final line after a trailing newline,
        # even though that line has no characters in the source string.
        following = ""
        next_end = len(text)
    else:
        if block_end >= len(text):
            return None
        newline = text.find("\n", block_end)
        next_end = len(text) if newline == -1 else newline + 1
        following = text[block_end:next_end]
    block_body, block_eol = _split_line_ending(block)
    _following_body, following_eol = _split_line_ending(following)
    if following_eol:
        replacement = following + block
        moved_start = block_start + len(following)
        moved_length = len(block)
    else:
        replacement = following + block_eol + block_body
        moved_start = block_start + len(following) + len(block_eol)
        # A selection that included the old terminator cannot include it after
        # the terminator moves between the two lines. Keep the selected text.
        moved_length = len(block_body)
    if replacement == text[block_start:next_end]:
        return None
    relative_start = min(relative_start, moved_length)
    relative_end = min(relative_end, moved_length)
    return TextEdit(
        start=block_start,
        end=next_end,
        replacement=replacement,
        selection_start=moved_start + relative_start,
        selection_end=moved_start + relative_end,
    )


def duplicate_line_or_selection(text: str, start: int, end: int) -> TextEdit:
    """Duplicate a selection exactly, or the current line when collapsed."""
    selection_start, selection_end = _clamped_range(text, start, end)
    if selection_start != selection_end:
        return TextEdit(
            start=selection_end,
            end=selection_end,
            replacement=text[selection_start:selection_end],
            selection_start=selection_start,
            selection_end=selection_end,
        )

    line_start, line_end = _selected_line_span(
        text, selection_start, selection_end
    )
    segment = text[line_start:line_end]
    _body, eol = _split_line_ending(segment)
    replacement = segment if eol else _line_ending(text, line_end) + segment
    return TextEdit(
        start=line_end,
        end=line_end,
        replacement=replacement,
        selection_start=selection_start,
        selection_end=selection_end,
    )


def parse_line_target(value: str) -> int | None:
    """Parse a one-based positive line number, rejecting other input."""
    value = value.strip()
    if not value or not value.isascii() or not value.isdigit():
        return None
    try:
        line = int(value)
    except ValueError:
        # Python limits decimal conversion digits; treat oversized pastes as
        # invalid input instead of raising from a dialog response handler.
        return None
    return line if line > 0 else None


def _source_lines(text: str) -> list[tuple[int, int, int]]:
    """Return (start, content end, end) offsets for every source line."""
    lines: list[tuple[int, int, int]] = []
    line_start = 0
    while line_start < len(text):
        newline = text.find("\n", line_start)
        if newline == -1:
            content_end = line_end = len(text)
        else:
            content_end = newline
            if content_end > line_start and text[content_end - 1] == "\r":
                content_end -= 1
            line_end = newline + 1
        lines.append((line_start, content_end, line_end))
        if newline == -1:
            break
        line_start = line_end
    if not text or text.endswith("\n"):
        lines.append((len(text), len(text), len(text)))
    return lines


def _list_item_parts(line: str) -> tuple[int, str] | None:
    bullet = _BULLET_ITEM.match(line)
    if bullet is not None:
        indent, marker, spaces, rest = bullet.groups()
        return len(indent) + len(marker) + len(spaces), rest
    ordered = _ORDERED_ITEM.match(line)
    if ordered is not None:
        indent, number, delimiter, spaces, rest = ordered.groups()
        prefix_length = sum(map(len, (indent, number, delimiter, spaces)))
        return prefix_length, rest
    return None


def _leading_whitespace(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _indent_columns(prefix: str, indent_width: int) -> int:
    return sum(1 if character == " " else indent_width for character in prefix)


def _changed_indent(prefix: str, indent_width: int, outdent: bool) -> str:
    if not outdent:
        return " " * indent_width + prefix
    remaining = indent_width
    position = 0
    while position < len(prefix) and remaining > 0:
        remaining -= 1 if prefix[position] == " " else indent_width
        position += 1
    return prefix[position:]


def indent_list(
    text: str,
    start: int,
    end: int,
    indent_width: int,
    *,
    outdent: bool = False,
) -> TextEdit | None:
    """Indent selected list items and their nested content by one level."""
    if indent_width <= 0:
        raise ValueError("indent_width must be positive")
    selection_start, selection_end = _clamped_range(text, start, end)
    block_start, block_end = _selected_line_span(
        text, selection_start, selection_end
    )
    lines = _source_lines(text)
    fenced_ranges = _fenced_ranges(text)
    roots: list[int] = []
    for index, (line_start, content_end, _line_end) in enumerate(lines):
        if line_start < block_start or line_start >= block_end:
            continue
        line = text[line_start:content_end]
        if _list_item_parts(line) is not None and not _in_ranges(
            line_start, fenced_ranges
        ):
            roots.append(index)

    edited: set[int] = set()
    for root in roots:
        line_start, content_end, _line_end = lines[root]
        line = text[line_start:content_end]
        root_prefix = _leading_whitespace(line)
        if outdent and not root_prefix:
            continue
        edited.add(root)
        root_columns = _indent_columns(root_prefix, indent_width)
        for index in range(root + 1, len(lines)):
            child_start, child_end, _child_line_end = lines[index]
            child = text[child_start:child_end]
            if not child.strip():
                continue
            child_prefix = _leading_whitespace(child)
            if _indent_columns(child_prefix, indent_width) <= root_columns:
                break
            edited.add(index)

    changes: dict[int, tuple[str, str]] = {}
    for index in sorted(edited):
        line_start, content_end, _line_end = lines[index]
        line = text[line_start:content_end]
        previous = _leading_whitespace(line)
        updated = _changed_indent(previous, indent_width, outdent)
        if updated != previous:
            changes[index] = (previous, updated)
    if not changes:
        return None

    first = min(changes)
    last = max(changes)
    edit_start = lines[first][0]
    edit_end = lines[last][1]
    pieces: list[str] = []
    position = edit_start
    for index, (previous, updated) in changes.items():
        line_start, content_end, _line_end = lines[index]
        pieces.append(text[position:line_start])
        pieces.append(updated + text[line_start + len(previous) : content_end])
        position = content_end
    pieces.append(text[position:edit_end])

    def map_position(original: int) -> int:
        shift = 0
        for index, (line_start, content_end, _line_end) in enumerate(lines):
            if original < line_start:
                break
            change = changes.get(index)
            if original <= content_end:
                if change is None:
                    return original + shift
                previous, updated = change
                relative = original - line_start
                delta = len(updated) - len(previous)
                relative = (
                    relative + delta
                    if delta >= 0
                    else max(0, relative + delta)
                )
                return line_start + shift + relative
            if change is not None:
                previous, updated = change
                shift += len(updated) - len(previous)
        return original + shift

    return TextEdit(
        start=edit_start,
        end=edit_end,
        replacement="".join(pieces),
        selection_start=map_position(selection_start),
        selection_end=map_position(selection_end),
    )


def toggle_task(text: str, cursor: int) -> TextEdit | None:
    """Toggle the task marker on the current bullet or ordered list item."""
    if not 0 <= cursor <= len(text):
        return None
    line_start = text.rfind("\n", 0, cursor) + 1
    if _in_ranges(line_start, _fenced_ranges(text)):
        return None
    newline = text.find("\n", cursor)
    content_end = len(text) if newline == -1 else newline
    if content_end > line_start and text[content_end - 1] == "\r":
        content_end -= 1
    line = text[line_start:content_end]
    parts = _list_item_parts(line)
    if parts is None:
        return None
    prefix_length, rest = parts
    if _TASK_MARKER.match(rest) is None:
        return None
    marker_start = line_start + prefix_length
    replacement = "[ ]" if rest[1] in "xX" else "[x]"
    return TextEdit(
        start=marker_start,
        end=marker_start + 3,
        replacement=replacement,
        selection_start=cursor,
        selection_end=cursor,
    )


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
