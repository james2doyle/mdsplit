# SPDX-License-Identifier: MIT

"""Render GitHub-Flavored Markdown as native GTK 4 widgets.

Parsing is done by the vendored markdown-it-py parser with its composite GFM
plugin (tables, strikethrough, autolinks, task lists, alerts and footnotes).
The renderer then builds a tree of ordinary GTK widgets: labels with Pango
markup, grids for tables, boxes for lists and block quotes, and pictures for
images. No web view is involved.
"""

from __future__ import annotations

import html
import logging
import re
import sys
import threading
from collections.abc import Callable
from gettext import gettext as _
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

try:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
except (ImportError, ValueError):
    GdkPixbuf = None

_VENDOR = Path(__file__).resolve().parent / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from markdown_it.token import Token

from .image_policy import (
    MAX_IMAGE_BYTES as _MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS as _MAX_IMAGE_PIXELS,
)
from .markdown_parser import create_parser, has_valid_underline_inline
from .paths import contained_file, document_root, resolve_link

log = logging.getLogger(__name__)

_HEADING_CLASSES = {
    1: ("title-1", "md-h1"),
    2: ("title-2", "md-h2"),
    3: ("title-3", "md-h3"),
    4: ("title-4", "md-h4"),
    5: ("heading", "md-h5"),
    6: ("heading", "md-h6"),
}

_ANCHOR_TAG = re.compile(r"<(/?)a\b[^>]*>")
_MAX_REMOTE_IMAGES = 32
_MAX_LOCAL_IMAGES = 64
_MAX_CACHED_IMAGES = 32
_MAX_CACHED_IMAGE_PIXELS = 20_000_000
_IMAGE_READ_CHUNK = 64 * 1024
_IMAGE_TIMEOUT_SECONDS = 15
_NESTED_LIST_INDENT = 18


def _is_cancelled_error(error: Exception) -> bool:
    matches = getattr(error, "matches", None)
    return bool(
        matches and matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
    )


def _escape(text: str) -> str:
    return GLib.markup_escape_text(text)


def _escape_text(text: str, *, underline: bool) -> str:
    """Escape text, allowing balanced <u> tags through as Pango markup."""
    if not underline:
        return _escape(text)
    parts = text.split("<u>")
    escaped = []
    for index, part in enumerate(parts):
        if index:
            escaped.append("<u>")
        closing = part.split("</u>")
        for close_index, segment in enumerate(closing):
            if close_index:
                escaped.append("</u>")
            escaped.append(_escape(segment))
    return "".join(escaped)


class MarkdownRenderer:
    """Build a native widget tree from a Markdown string."""

    def __init__(self) -> None:
        self._md = create_parser()

        self._dark = False
        self._base_dir: Path | None = None
        self._base_file: Gio.File | None = None
        self._anchors: dict[str, Gtk.Widget] = {}
        self._heading_slugs: dict[str, int] = {}
        self._on_internal_link = None
        self._on_external_link = None
        self._allow_remote_images = False
        self._render_cancellable: Gio.Cancellable | None = None
        self._remote_images: dict[str, Gdk.Texture] = {}
        self._remote_pending: dict[
            str, list[Callable[[Gdk.Texture | None], None]]
        ] = {}
        self._remote_started = 0
        self._local_images = 0
        self._local_images_cache: dict[str, Gdk.Texture] = {}
        self._local_pending: dict[
            str, list[Callable[[Gdk.Texture | None], None]]
        ] = {}

        self._code_background = "#eceff1"
        self._muted_color = "#77767b"

    # -- public API -----------------------------------------------------

    def render(
        self,
        text: str,
        *,
        dark: bool = False,
        base_dir: Path | None = None,
        base_file: Gio.File | None = None,
        allow_remote_images: bool = False,
        cancellable: Gio.Cancellable | None = None,
        on_internal_link=None,
        on_external_link=None,
    ) -> Gtk.Widget:
        self._dark = dark
        self._base_dir = base_dir
        self._base_file = base_file
        self._anchors = {}
        self._heading_slugs = {}
        self._on_internal_link = on_internal_link
        self._on_external_link = on_external_link
        self._allow_remote_images = allow_remote_images
        self._render_cancellable = cancellable
        self._remote_pending.clear()
        self._local_pending.clear()
        self._remote_started = 0
        self._local_images = 0
        self._code_background = "#3d3f42" if dark else "#eceff1"
        self._muted_color = "#9a9a9a" if dark else "#77767b"

        tokens = self._md.parse(text)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.add_css_class("md-document")
        root.set_margin_top(24)
        root.set_margin_bottom(32)
        root.set_margin_start(28)
        root.set_margin_end(28)

        widgets, _ignored = self._render_blocks(tokens, 0, len(tokens))
        for widget in widgets:
            root.append(widget)
        return root

    def shutdown(self) -> None:
        self._render_cancellable = None
        self._remote_pending.clear()
        self._remote_images.clear()
        self._local_pending.clear()
        self._local_images_cache.clear()

    def get_anchor(self, name: str) -> Gtk.Widget | None:
        return self._anchors.get(name)

    def inline_markup(self, text: str, *, dark: bool = False) -> str:
        """Convert inline Markdown to Pango markup without creating widgets.

        This is the headless seam used by tests and by any caller that only
        needs a formatted label.
        """
        self._dark = dark
        self._base_dir = None
        self._on_internal_link = None
        self._on_external_link = None
        self._code_background = "#3d3f42" if dark else "#eceff1"
        self._muted_color = "#9a9a9a" if dark else "#77767b"
        tokens = self._md.parseInline(text)
        if not tokens:
            return ""
        return self._render_inline(tokens[0])

    def inline_token_markup(
        self,
        token: Token,
        *,
        dark: bool = False,
        code_background: str | None = None,
        plain_code: bool = False,
    ) -> str:
        """Convert an existing inline token to Pango markup."""
        self._dark = dark
        self._base_dir = None
        self._base_file = None
        self._on_internal_link = None
        self._on_external_link = None
        self._code_background = code_background or (
            "#3d3f42" if dark else "#eceff1"
        )
        self._muted_color = "#9a9a9a" if dark else "#77767b"
        return self._render_inline(token, code_background=not plain_code)

    # -- block rendering -------------------------------------------------

    def _render_blocks(
        self, tokens: list[Token], start: int, end: int, list_depth: int = 0
    ) -> tuple[list[Gtk.Widget], int]:
        widgets: list[Gtk.Widget] = []
        i = start
        while i < end:
            token = tokens[i]
            kind = token.type

            if kind == "heading_open":
                inline = tokens[i + 1] if i + 1 < end else None
                widget = self._render_heading(token, inline)
                if widget is not None:
                    widgets.append(widget)
                i += 3
            elif kind == "paragraph_open":
                inline = tokens[i + 1] if i + 1 < end else None
                widget = self._render_paragraph(token, inline)
                if widget is not None:
                    widgets.append(widget)
                i += 3
            elif kind in ("bullet_list_open", "ordered_list_open"):
                widget, i = self._render_list(tokens, i, list_depth)
                widgets.append(widget)
            elif kind == "blockquote_open":
                widget, i = self._render_blockquote(tokens, i)
                widgets.append(widget)
            elif kind == "alert_open":
                widget, i = self._render_alert(tokens, i)
                widgets.append(widget)
            elif kind == "table_open":
                widget, i = self._render_table(tokens, i)
                widgets.append(widget)
            elif kind in ("fence", "code_block"):
                widgets.append(self._render_code_block(token))
                i += 1
            elif kind == "hr":
                widgets.append(self._render_rule())
                i += 1
            elif kind == "footnote_block_open":
                widget, i = self._render_footnotes(tokens, i)
                widgets.append(widget)
            elif kind == "inline":
                widget = self._render_paragraph(token, token)
                if widget is not None:
                    widgets.append(widget)
                i += 1
            else:
                i += 1

        return widgets, i

    def _render_heading(
        self, open_token: Token, inline_token: Token | None
    ) -> Gtk.Widget | None:
        if inline_token is None or inline_token.type != "inline":
            return None
        markup = self._render_inline(inline_token)
        if not markup.strip():
            return None

        level = 1
        if len(open_token.tag) == 2 and open_token.tag[0] == "h":
            level = int(open_token.tag[1])

        label = self._make_label(markup)
        for css_class in _HEADING_CLASSES.get(level, _HEADING_CLASSES[6]):
            label.add_css_class(css_class)
        label.set_margin_top(12 if level <= 2 else 6)

        slug = self._unique_slug(self._plain_text(inline_token))
        self._anchors[slug] = label
        return label

    def _render_paragraph(
        self, open_token: Token, inline_token: Token | None
    ) -> Gtk.Widget | None:
        if inline_token is None or inline_token.type != "inline":
            return None

        image = self._sole_image(inline_token)
        if image is not None:
            return self._render_image(image)

        linked_image = self._sole_link_image(inline_token)
        if linked_image is not None:
            image_token, href = linked_image
            return self._render_linked_image(image_token, href)

        markup = self._render_inline(inline_token)
        if not markup.strip():
            return None

        label = self._make_label(markup)
        label.add_css_class("md-paragraph")
        if getattr(open_token, "hidden", False):
            label.add_css_class("md-tight")
        return label

    def _render_list(
        self, tokens: list[Token], start: int, list_depth: int = 0
    ) -> tuple[Gtk.Widget, int]:
        open_token = tokens[start]
        ordered = open_token.type == "ordered_list_open"
        close_type = "ordered_list_close" if ordered else "bullet_list_close"
        close = self._find_close(tokens, start, open_token.type, close_type)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("md-list")
        if list_depth:
            box.set_margin_start(_NESTED_LIST_INDENT)

        if ordered:
            try:
                number = int(open_token.attrs.get("start", 1))
            except (TypeError, ValueError):
                number = 1
        else:
            number = 1

        i = start + 1
        while i < close:
            if tokens[i].type != "list_item_open":
                i += 1
                continue

            item_token = tokens[i]
            item_close = self._find_close(
                tokens, i, "list_item_open", "list_item_close"
            )
            blocks, _ignored = self._render_blocks(
                tokens, i + 1, item_close, list_depth + 1
            )

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_hexpand(True)
            for block in blocks:
                content.append(block)

            checked = None
            if item_token.meta:
                checked = item_token.meta.get("checked")

            if checked is not None:
                marker: Gtk.Widget = Gtk.CheckButton()
                marker.set_active(bool(checked))
                marker.set_sensitive(False)
                marker.set_can_focus(False)
                marker.set_valign(Gtk.Align.CENTER)
                marker.add_css_class("md-task-check")
                # Align the item text with the checkbox along the first
                # block's baseline rather than the top of the row.
                content.set_baseline_child(0)
                content.set_valign(Gtk.Align.BASELINE_CENTER)
            elif ordered:
                label = Gtk.Label(label=f"{number}.")
                label.set_xalign(1.0)
                label.set_valign(Gtk.Align.START)
                label.add_css_class("dim-label")
                label.add_css_class("md-list-number")
                marker = label
                number += 1
            else:
                label = Gtk.Label(label="\u2022")
                label.set_xalign(0.0)
                label.set_valign(Gtk.Align.START)
                label.add_css_class("dim-label")
                label.add_css_class("md-list-bullet")
                marker = label

            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            item.append(marker)
            item.append(content)
            box.append(item)
            i = item_close + 1

        return box, close + 1

    def _render_blockquote(self, tokens: list[Token], start: int) -> tuple[Gtk.Widget, int]:
        close = self._find_close(tokens, start, "blockquote_open", "blockquote_close")
        blocks, _ignored = self._render_blocks(tokens, start + 1, close)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for block in blocks:
            content.append(block)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        wrapper.add_css_class("md-blockquote")
        wrapper.append(content)
        return wrapper, close + 1

    def _render_alert(self, tokens: list[Token], start: int) -> tuple[Gtk.Widget, int]:
        close = self._find_close(tokens, start, "alert_open", "alert_close")
        open_token = tokens[start]
        kind = (open_token.meta.get("kind") or open_token.info or "NOTE").upper()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("md-alert")
        box.add_css_class(f"md-alert-{kind.lower()}")

        content_start = start + 1
        if (
            start + 2 < close
            and tokens[start + 1].type == "alert_title_open"
            and tokens[start + 2].type == "inline"
        ):
            title = self._make_label(self._render_inline(tokens[start + 2]))
            title.add_css_class("md-alert-title")
            box.append(title)
            content_start = start + 4

        blocks, _ignored = self._render_blocks(tokens, content_start, close)
        for block in blocks:
            box.append(block)
        return box, close + 1

    def _render_table(self, tokens: list[Token], start: int) -> tuple[Gtk.Widget, int]:
        close = self._find_close(tokens, start, "table_open", "table_close")
        rows: list[list[tuple[Token | None, bool, float]]] = []
        current: list[tuple[Token | None, bool, float]] | None = None

        i = start + 1
        while i < close:
            kind = tokens[i].type
            if kind == "tr_open":
                current = []
            elif kind in ("th_open", "td_open"):
                inline = tokens[i + 1] if i + 1 < close else None
                if inline is not None and inline.type != "inline":
                    inline = None
                align = self._alignment(tokens[i].attrs.get("style", ""))
                if current is None:
                    current = []
                current.append((inline, kind == "th_open", align))
            elif kind == "tr_close":
                if current is not None:
                    rows.append(current)
                current = None
            i += 1

        max_columns = max((len(row) for row in rows), default=0)
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(0)
        grid.set_row_spacing(0)
        grid.set_hexpand(True)
        grid.add_css_class("md-table")

        for row_index, row in enumerate(rows):
            for column_index, (inline, is_header, align) in enumerate(row):
                markup = (
                    self._render_inline(inline, code_background=False)
                    if inline is not None
                    else ""
                )
                label = self._make_label(markup)
                label.set_hexpand(True)
                label.set_halign(Gtk.Align.FILL)
                label.set_xalign(align)
                label.add_css_class("md-table-cell")
                if is_header:
                    label.add_css_class("md-table-header")
                if column_index == max_columns - 1:
                    label.add_css_class("md-table-last-column")
                if row_index == len(rows) - 1:
                    label.add_css_class("md-table-last-row")
                grid.attach(label, column_index, row_index, 1, 1)

        return grid, close + 1

    def _render_footnotes(self, tokens: list[Token], start: int) -> tuple[Gtk.Widget, int]:
        close = self._find_close(
            tokens, start, "footnote_block_open", "footnote_block_close"
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(separator)
        heading = Gtk.Label(label=_("Footnotes"))
        heading.set_xalign(0.0)
        heading.add_css_class("title-4")
        box.append(heading)

        i = start + 1
        while i < close:
            if tokens[i].type != "footnote_open":
                i += 1
                continue

            footnote_close = self._find_close(
                tokens, i, "footnote_open", "footnote_close"
            )
            meta = tokens[i].meta or {}
            footnote_id = meta.get("id", 0)
            label_text = meta.get("label", str(footnote_id + 1))
            blocks, _ignored = self._render_blocks(tokens, i + 1, footnote_close)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for block in blocks:
                content.append(block)

            marker = Gtk.Label(label=f"[{label_text}]")
            marker.set_xalign(0.0)
            marker.set_valign(Gtk.Align.START)
            marker.add_css_class("dim-label")

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(marker)
            row.append(content)
            self._anchors[f"fn-{footnote_id}"] = row
            box.append(row)
            i = footnote_close + 1

        return box, close + 1

    def _render_code_block(self, token: Token, language: str | None = None) -> Gtk.Widget:
        code = token.content.rstrip("\n")
        if language is None:
            language = token.info.strip().split()[0] if token.info.strip() else ""

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class("md-code-block")
        outer.set_hexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("md-code-header")
        header.set_margin_start(12)
        header.set_margin_end(6)
        header.set_margin_top(4)
        header.set_margin_bottom(4)

        language_label = Gtk.Label(label=language or _("Code"))
        language_label.set_xalign(0.0)
        language_label.set_hexpand(True)
        language_label.add_css_class("caption")
        language_label.add_css_class("dim-label")
        header.append(language_label)

        copy_button = Gtk.Button()
        copy_button.set_icon_name("edit-copy-symbolic")
        copy_button.set_has_frame(False)
        copy_button.set_tooltip_text(_("Copy code"))
        copy_button.add_css_class("flat")
        copy_button.connect("clicked", self._copy_to_clipboard, code)
        header.append(copy_button)
        outer.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_has_frame(False)

        body = Gtk.Label(label=code)
        body.set_xalign(0.0)
        body.set_wrap(False)
        body.set_selectable(True)
        body.add_css_class("monospace")
        body.add_css_class("md-code-body")
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(8)
        body.set_margin_bottom(12)
        scroller.set_child(body)
        outer.append(scroller)
        return outer

    def _render_rule(self) -> Gtk.Widget:
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(8)
        separator.set_margin_bottom(8)
        return separator

    def _render_image(self, token: Token) -> Gtk.Widget:
        src = (token.attrs.get("src") or "").strip()
        alt = token.content or token.attrs.get("alt") or ""

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_halign(Gtk.Align.CENTER)
        box.set_hexpand(True)

        picture = Gtk.Picture()
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_halign(Gtk.Align.CENTER)
        picture.set_hexpand(True)

        resolved = self._resolve_image(src)
        if resolved is not None and resolved[0] == "file":
            if self._local_images >= _MAX_LOCAL_IMAGES:
                return self._image_fallback(alt, _("Too many local images"))
            path = resolved[1]
            if not isinstance(path, Path):
                return self._image_fallback(alt)
            try:
                file_info = path.stat()
                if not path.is_file() or file_info.st_size > _MAX_IMAGE_BYTES:
                    return self._image_fallback(alt, _("Image is too large"))
                if GdkPixbuf is not None:
                    _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(
                        str(path)
                    )
                    if width * height > _MAX_IMAGE_PIXELS:
                        return self._image_fallback(alt, _("Image is too large"))
            except (GLib.Error, OSError):
                return self._image_fallback(alt)
            self._local_images += 1
            cache_key = (
                f"{path}:{file_info.st_size}:{file_info.st_mtime_ns}"
            )
            cached = self._local_images_cache.get(cache_key)
            if cached is not None:
                picture.set_paintable(cached)
                self._size_picture(picture)
                box.append(picture)
            elif GdkPixbuf is None:
                # GdkPixbuf is a GTK dependency in supported runtimes. Keep a
                # fallback for unusual installations with a broken GI typelib.
                picture.set_filename(str(path))
                if picture.get_paintable() is None:
                    return self._image_fallback(alt)
                self._size_picture(picture)
                box.append(picture)
            else:
                loading = Gtk.Label(label=_("Loading image\u2026"))
                loading.set_xalign(0.0)
                loading.add_css_class("dim-label")
                box.append(loading)
                self._load_local_image(
                    cache_key, path, picture, loading, alt
                )
        elif resolved is not None and resolved[0] == "uri":
            uri = resolved[1]
            if not isinstance(uri, str):
                return self._image_fallback(alt)
            if self._remote_started >= _MAX_REMOTE_IMAGES:
                return self._image_fallback(
                    alt, _("Too many remote images")
                )
            self._remote_started += 1
            cached = self._remote_images.get(uri)
            if cached is not None:
                picture.set_paintable(cached)
                self._size_picture(picture)
                box.append(picture)
            else:
                loading = Gtk.Label(label=_("Loading image\u2026"))
                loading.set_xalign(0.0)
                loading.add_css_class("dim-label")
                box.append(loading)
                self._load_remote_image(picture, loading, uri, alt)
        elif resolved is not None and resolved[0] == "disabled":
            return self._image_fallback(
                alt,
                _("Remote image not loaded. Enable Load Remote Images in the menu."),
            )
        else:
            return self._image_fallback(alt)

        if alt:
            caption = Gtk.Label(label=alt)
            caption.set_xalign(0.5)
            caption.set_wrap(True)
            caption.add_css_class("caption")
            caption.add_css_class("dim-label")
            box.append(caption)
        return box

    def _render_linked_image(self, image: Token, href: str) -> Gtk.Widget:
        widget = self._render_image(image)
        resolved = self._resolve_link(href)
        if resolved is None:
            return widget
        button = Gtk.Button()
        button.set_child(widget)
        button.set_has_frame(False)
        button.add_css_class("flat")
        button.set_tooltip_text(image.content or resolved)
        button.connect("clicked", self._on_image_clicked, resolved)
        return button

    def _on_image_clicked(self, _button: Gtk.Button, uri: str) -> None:
        self._dispatch_link(uri)

    def _image_fallback(self, alt: str, detail: str | None = None) -> Gtk.Widget:
        text = _("[Image: {alt}]").format(alt=alt) if alt else _("[Image]")
        if detail:
            text = f"{text} {detail}"
        label = Gtk.Label(label=text)
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.add_css_class("dim-label")
        return label

    def _load_remote_image(
        self,
        picture: Gtk.Picture,
        loading: Gtk.Label,
        uri: str,
        alt: str,
    ) -> None:
        if uri in self._remote_pending:
            self._remote_pending[uri].append(
                lambda texture: self._show_remote_image(
                    picture, loading, texture, alt
                )
            )
            return

        callbacks: list[Callable[[Gdk.Texture | None], None]] = [
            lambda texture: self._show_remote_image(
                picture, loading, texture, alt
            )
        ]
        self._remote_pending[uri] = callbacks

        request_cancellable = Gio.Cancellable()
        if self._render_cancellable is not None:
            self._render_cancellable.connect(
                lambda *_args: request_cancellable.cancel()
            )
        timeout_state = {"active": True}
        timeout_id = GLib.timeout_add_seconds(
            _IMAGE_TIMEOUT_SECONDS,
            self._cancel_remote_request,
            request_cancellable,
            timeout_state,
        )
        self._read_remote_image(
            uri, request_cancellable, callbacks, timeout_state, timeout_id
        )

    def _cancel_remote_request(
        self, cancellable: Gio.Cancellable, state: dict[str, bool]
    ) -> bool:
        state["active"] = False
        cancellable.cancel()
        return False

    def _finish_remote_request(
        self,
        uri: str,
        callbacks: list[Callable[[Gdk.Texture | None], None]],
        texture: Gdk.Texture | None,
        timeout_state: dict[str, bool],
        timeout_id: int,
    ) -> None:
        if timeout_state["active"]:
            GLib.source_remove(timeout_id)
            timeout_state["active"] = False
        if self._remote_pending.get(uri) is not callbacks:
            return
        self._remote_pending.pop(uri, None)
        if texture is not None:
            self._cache_texture(self._remote_images, uri, texture)
        for callback in callbacks:
            callback(texture)

    def _read_remote_image(
        self,
        uri: str,
        cancellable: Gio.Cancellable,
        callbacks: list[Callable[[Gdk.Texture | None], None]],
        timeout_state: dict[str, bool],
        timeout_id: int,
    ) -> None:
        thread = threading.Thread(
            target=self._read_remote_worker,
            args=(uri, cancellable, callbacks, timeout_state, timeout_id),
            daemon=True,
            name="md-split-image",
        )
        thread.start()

    def _read_remote_worker(
        self,
        uri: str,
        cancellable: Gio.Cancellable,
        callbacks: list[Callable[[Gdk.Texture | None], None]],
        timeout_state: dict[str, bool],
        timeout_id: int,
    ) -> None:
        """Read an image off the UI thread, with a hard byte limit."""
        # Loaded on first use: urllib.request pulls in ssl and http.client,
        # which would otherwise slow down startup for every document.
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        chunks: list[bytes] = []
        total = 0
        error: Exception | None = None
        try:
            request = Request(
                uri,
                headers={
                    "User-Agent": "MD Split",
                    "Accept": "image/*",
                },
            )
            with urlopen(request, timeout=_IMAGE_TIMEOUT_SECONDS) as response:
                if urlparse(response.geturl()).scheme.lower() not in (
                    "http",
                    "https",
                ):
                    raise ValueError("Redirected to an unsupported URL scheme")
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > _MAX_IMAGE_BYTES:
                    raise ValueError("Remote image exceeds byte limit")
                read = getattr(response, "read1", response.read)
                while True:
                    if cancellable.is_cancelled():
                        raise GLib.Error.new_literal(
                            Gio.io_error_quark(),
                            Gio.IOErrorEnum.CANCELLED,
                            "Image request cancelled",
                        )
                    remaining = _MAX_IMAGE_BYTES - total
                    chunk = read(min(_IMAGE_READ_CHUNK, remaining + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        raise ValueError("Remote image exceeds byte limit")
                    chunks.append(chunk)
        except (
            GLib.Error,
            HTTPError,
            OSError,
            TypeError,
            URLError,
            ValueError,
        ) as caught:
            error = caught

        payload: object | None = None
        if error is None:
            data = b"".join(chunks)
            try:
                payload = (
                    self._decode_remote_pixbuf(data)
                    if GdkPixbuf is not None
                    else data
                )
            except (GLib.Error, TypeError, ValueError) as caught:
                error = caught

        GLib.idle_add(
            self._finish_remote_read,
            uri,
            callbacks,
            payload,
            error,
            timeout_state,
            timeout_id,
        )

    def _decode_remote_pixbuf(self, data: bytes):
        """Validate dimensions before completing a remote image decode."""
        pixbuf_api = GdkPixbuf
        if pixbuf_api is None:
            raise ValueError("GdkPixbuf is unavailable")
        loader = pixbuf_api.PixbufLoader()
        oversized = []

        def size_prepared(_loader, width, height):
            if width * height > _MAX_IMAGE_PIXELS:
                oversized.append(True)

        loader.connect("size-prepared", size_prepared)
        try:
            for start in range(0, len(data), _IMAGE_READ_CHUNK):
                loader.write(data[start : start + _IMAGE_READ_CHUNK])
                if oversized:
                    raise ValueError("Image exceeds pixel limit")
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            try:
                loader.close()
            except GLib.Error:
                pass
            raise
        if pixbuf is None:
            raise ValueError("Could not decode image")
        return pixbuf

    def _finish_remote_read(
        self,
        uri: str,
        callbacks: list[Callable[[Gdk.Texture | None], None]],
        payload: object | None,
        error: Exception | None,
        timeout_state: dict[str, bool],
        timeout_id: int,
    ) -> bool:
        if payload is None:
            if error is not None and not _is_cancelled_error(error):
                log.warning("Could not load remote image: %s", error)
            self._finish_remote_request(
                uri, callbacks, None, timeout_state, timeout_id
            )
            return False

        try:
            if GdkPixbuf is not None and isinstance(
                payload, GdkPixbuf.Pixbuf
            ):
                texture = Gdk.Texture.new_for_pixbuf(payload)
            else:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(payload))
        except (GLib.Error, TypeError, ValueError):
            self._finish_remote_request(
                uri, callbacks, None, timeout_state, timeout_id
            )
            return False
        pixels = texture.get_width() * texture.get_height()
        if pixels > _MAX_IMAGE_PIXELS:
            log.warning("Remote image exceeds pixel limit")
            self._finish_remote_request(
                uri, callbacks, None, timeout_state, timeout_id
            )
            return False
        self._finish_remote_request(
            uri, callbacks, texture, timeout_state, timeout_id
        )
        return False

    def _show_remote_image(
        self,
        picture: Gtk.Picture,
        loading: Gtk.Label,
        texture: Gdk.Texture | None,
        alt: str,
    ) -> None:
        if texture is None:
            loading.set_label(_("Could not load image"))
            return
        picture.set_paintable(texture)
        self._size_picture(picture)
        parent = loading.get_parent()
        if parent is not None:
            parent.remove(loading)
            parent.prepend(picture)

    def _load_local_image(
        self,
        cache_key: str,
        path: Path,
        picture: Gtk.Picture,
        loading: Gtk.Label,
        alt: str,
    ) -> None:
        callbacks = self._local_pending.get(cache_key)
        if callbacks is not None:
            callbacks.append(
                lambda texture: self._show_local_image(
                    picture, loading, texture, alt
                )
            )
            return

        callbacks = [
            lambda texture: self._show_local_image(
                picture, loading, texture, alt
            )
        ]
        self._local_pending[cache_key] = callbacks
        request_cancellable = Gio.Cancellable()
        if self._render_cancellable is not None:
            self._render_cancellable.connect(
                lambda *_args: request_cancellable.cancel()
            )
        thread = threading.Thread(
            target=self._read_local_worker,
            args=(cache_key, path, callbacks, request_cancellable),
            daemon=True,
            name="md-split-local-image",
        )
        thread.start()

    def _read_local_worker(
        self,
        cache_key: str,
        path: Path,
        callbacks: list[Callable[[Gdk.Texture | None], None]],
        cancellable: Gio.Cancellable,
    ) -> None:
        pixbuf = None
        error: Exception | None = None
        pixbuf_api = GdkPixbuf
        if not cancellable.is_cancelled():
            try:
                if pixbuf_api is None:
                    raise ValueError("GdkPixbuf is unavailable")
                if path.stat().st_size > _MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds byte limit")
                _fmt, width, height = pixbuf_api.Pixbuf.get_file_info(str(path))
                if (
                    width <= 0
                    or height <= 0
                    or width * height > _MAX_IMAGE_PIXELS
                ):
                    raise ValueError("Image exceeds pixel limit")
                scale = min(1.0, 360 / height)
                target_width = max(1, int(width * scale))
                target_height = max(1, int(height * scale))
                if not cancellable.is_cancelled():
                    pixbuf = pixbuf_api.Pixbuf.new_from_file_at_scale(
                        str(path), target_width, target_height, True
                    )
            except (GLib.Error, OSError, ValueError) as caught:
                error = caught

        GLib.idle_add(
            self._finish_local_read,
            cache_key,
            callbacks,
            pixbuf,
            error,
            cancellable,
        )

    def _finish_local_read(
        self,
        cache_key: str,
        callbacks: list[Callable[[Gdk.Texture | None], None]],
        pixbuf,
        error: Exception | None,
        cancellable: Gio.Cancellable,
    ) -> bool:
        if self._local_pending.get(cache_key) is not callbacks:
            return False
        self._local_pending.pop(cache_key, None)
        if cancellable.is_cancelled():
            return False
        texture = None
        if pixbuf is not None and error is None:
            try:
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            except (GLib.Error, TypeError, ValueError):
                texture = None
        if texture is not None:
            self._cache_texture(
                self._local_images_cache, cache_key, texture
            )
        for callback in callbacks:
            callback(texture)
        return False

    def _cache_texture(
        self,
        cache: dict[str, Gdk.Texture],
        key: str,
        texture: Gdk.Texture,
    ) -> None:
        cache[key] = texture
        pixels = sum(
            item.get_width() * item.get_height() for item in cache.values()
        )
        while (
            len(cache) > _MAX_CACHED_IMAGES
            or pixels > _MAX_CACHED_IMAGE_PIXELS
        ):
            old_key = next(iter(cache))
            if old_key == key:
                break
            removed = cache.pop(old_key)
            pixels -= removed.get_width() * removed.get_height()

    def _show_local_image(
        self,
        picture: Gtk.Picture,
        loading: Gtk.Label,
        texture: Gdk.Texture | None,
        alt: str,
    ) -> None:
        if texture is None:
            loading.set_label(_("Could not load image"))
            return
        picture.set_paintable(texture)
        self._size_picture(picture)
        parent = loading.get_parent()
        if parent is not None:
            parent.remove(loading)
            parent.prepend(picture)

    def _size_picture(self, picture: Gtk.Picture) -> None:
        """Reserve enough height for the image without letting it dominate."""
        paintable = picture.get_paintable()
        if paintable is None:
            return
        height = paintable.get_intrinsic_height()
        if height > 0:
            picture.set_size_request(-1, min(height, 360))

    # -- inline rendering ------------------------------------------------

    def _render_inline(
        self, token: Token | None, *, code_background: bool = True
    ) -> str:
        if token is None or not token.children:
            return ""

        underline = has_valid_underline_inline(
            token,
            link_allowed=lambda href: self._resolve_link(href) is not None,
        )

        parts: list[str] = []
        open_tags: list[str] = []
        for child in token.children:
            kind = child.type
            if kind == "text":
                parts.append(_escape_text(child.content, underline=underline))
            elif kind == "softbreak":
                parts.append(" ")
            elif kind == "hardbreak":
                parts.append("\n")
            elif kind == "code_inline":
                if code_background:
                    parts.append(
                        '<span font_family="monospace" background="{}">{}</span>'.format(
                            self._code_background, _escape(child.content)
                        )
                    )
                else:
                    parts.append(
                        '<span font_family="monospace">{}</span>'.format(
                            _escape(child.content)
                        )
                    )
            elif kind == "em_open":
                parts.append("<i>")
            elif kind == "em_close":
                parts.append("</i>")
            elif kind == "strong_open":
                parts.append("<b>")
            elif kind == "strong_close":
                parts.append("</b>")
            elif kind == "s_open":
                parts.append("<s>")
            elif kind == "s_close":
                parts.append("</s>")
            elif kind == "link_open":
                href = child.attrs.get("href", "")
                resolved = self._resolve_link(href)
                if resolved is not None:
                    parts.append(f'<a href="{_escape(resolved)}">')
                    open_tags.append("</a>")
                else:
                    open_tags.append("")
            elif kind == "link_close":
                parts.append(open_tags.pop() if open_tags else "")
            elif kind == "image":
                alt = child.content or child.attrs.get("alt") or ""
                if alt:
                    parts.append(
                        '<span foreground="{}">[{}]</span>'.format(
                            self._muted_color, _escape(alt)
                        )
                    )
                else:
                    parts.append(
                        '<span foreground="{}">[Image]</span>'.format(
                            self._muted_color
                        )
                    )
            elif kind == "footnote_ref":
                meta = child.meta or {}
                footnote_id = meta.get("id", 0)
                label_text = meta.get("label", str(footnote_id + 1))
                parts.append(
                    f'<sup><a href="#fn-{footnote_id}">[{_escape(str(label_text))}]</a></sup>'
                )
            elif kind == "footnote_anchor":
                continue
            elif child.content:
                parts.append(_escape(child.content))

        return "".join(parts)

    def _sole_image(self, token: Token) -> Token | None:
        meaningful = self._inline_children(token)
        if len(meaningful) == 1 and meaningful[0].type == "image":
            return meaningful[0]
        return None

    def _sole_link_image(self, token: Token) -> tuple[Token, str] | None:
        children = self._inline_children(token)
        if (
            len(children) == 3
            and children[0].type == "link_open"
            and children[1].type == "image"
            and children[2].type == "link_close"
        ):
            return children[1], children[0].attrs.get("href", "")
        return None

    def _inline_children(self, token: Token) -> list[Token]:
        return [
            child
            for child in token.children or []
            if not (child.type == "text" and not child.content.strip())
        ]

    def _make_label(self, markup: str) -> Gtk.Label:
        label = Gtk.Label()
        label.set_wrap(True)
        label.set_selectable(True)
        label.set_xalign(0.0)
        try:
            # Gtk.Label.set_markup only warns on invalid markup and leaves the
            # label empty, so validate with Pango to reach the plain fallback.
            # Pango itself does not know GTK's <a> tag; validate it as a span.
            probe = _ANCHOR_TAG.sub(
                lambda match: "</span>" if match.group(1) else "<span>",
                markup,
            )
            Pango.parse_markup(probe, -1, "\x00")
        except GLib.Error:
            log.warning("Invalid Pango markup; falling back to plain text")
            label.set_text(
                html.unescape(re.sub(r"<[^>]+>", "", markup))
            )
        else:
            label.set_use_markup(True)
            label.set_markup(markup)
        label.connect("activate-link", self._on_activate_link)
        return label

    def _on_activate_link(self, _label: Gtk.Label, uri: str) -> bool:
        self._dispatch_link(uri)
        return True

    def _dispatch_link(self, uri: str) -> None:
        if uri.startswith("#"):
            if self._on_internal_link is not None:
                self._on_internal_link(uri[1:])
        elif self._on_external_link is not None:
            self._on_external_link(uri)

    # -- helpers ---------------------------------------------------------

    def _find_close(
        self,
        tokens: list[Token],
        start: int,
        open_type: str,
        close_type: str,
    ) -> int:
        depth = 0
        for index in range(start, len(tokens)):
            kind = tokens[index].type
            if kind == open_type:
                depth += 1
            elif kind == close_type:
                depth -= 1
                if depth <= 0:
                    return index
        return len(tokens) - 1

    def _alignment(self, style: str) -> float:
        if "center" in style:
            return 0.5
        if "right" in style:
            return 1.0
        return 0.0

    def _resolve_link(self, href: str) -> str | None:
        return resolve_link(href, self._base_dir, self._base_file)

    def _resolve_image(
        self, src: str
    ) -> tuple[str, str | Path] | None:
        src = src.strip()
        if not src:
            return None
        try:
            parsed = urlparse(src)
        except ValueError:
            return None
        scheme = parsed.scheme.lower()
        if scheme in ("http", "https"):
            if not self._allow_remote_images:
                return ("disabled", src)
            return ("uri", src)
        if scheme or src.startswith("//"):
            return None
        path = Path(unquote(parsed.path))
        if not path.is_absolute():
            if self._base_dir is not None:
                path = self._base_dir / path
            elif self._base_file is not None:
                parent = self._base_file.get_parent()
                if parent is None:
                    return None
                parent_path = parent.get_path()
                if parent_path is not None:
                    path = Path(parent_path) / path
                else:
                    if not self._allow_remote_images:
                        return ("disabled", src)
                    target = parent.resolve_relative_path(unquote(parsed.path))
                    uri = target.get_uri()
                    return ("uri", uri) if uri else None
            else:
                return None
        resolved = contained_file(path, document_root(
            self._base_dir, self._base_file
        ))
        if resolved is None:
            return None
        return ("file", resolved)

    def _plain_text(self, token: Token) -> str:
        parts: list[str] = []
        for child in token.children or []:
            if child.type in ("text", "code_inline"):
                parts.append(child.content)
            elif child.type == "image":
                parts.append(child.content or "")
            elif child.type in ("softbreak", "hardbreak"):
                parts.append(" ")
        return "".join(parts)

    def _unique_slug(self, text: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", text.strip().lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-") or "section"
        count = self._heading_slugs.get(slug, 0) + 1
        self._heading_slugs[slug] = count
        return slug if count == 1 else f"{slug}-{count - 1}"

    def _copy_to_clipboard(self, button: Gtk.Button, text: str) -> None:
        clipboard = button.get_clipboard()
        provider = Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(text.encode("utf-8"))
        )
        clipboard.set_content(provider)
