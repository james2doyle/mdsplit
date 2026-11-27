# SPDX-License-Identifier: MIT

"""Containment helpers for document-relative Markdown paths.

Markdown files are untrusted input: a relative or absolute path in a document
must not be able to read or open files outside the directory that contains the
document. Preview links, preview images and export images all resolve through
these helpers.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import gi

gi.require_version("Gio", "2.0")

from gi.repository import Gio

_UNSAFE_SCHEMES = {"javascript", "vbscript", "data"}


def document_root(
    base_dir: Path | None, base_file: Gio.File | None
) -> Path | None:
    """Return the directory that document-relative paths are confined to."""
    if base_dir is not None:
        return base_dir.resolve()
    if base_file is not None:
        parent = base_file.get_parent()
        if parent is None:
            return None
        local = parent.get_path()
        if local:
            return Path(local).resolve()
    return None


def markdown_image_path(path: Path, base_dir: Path | None) -> str:
    """Return an encoded document-relative image path when it is contained."""
    resolved = path.resolve()
    root = base_dir.resolve() if base_dir is not None else None
    if root is not None and resolved.is_relative_to(root):
        value = resolved.relative_to(root).as_posix()
    else:
        value = resolved.as_posix()
    return quote(value, safe="/")


def ensure_file_extension(path: Path, extension: str) -> Path:
    """Return ``path`` with the export extension appended when missing."""
    suffix = extension if extension.startswith(".") else f".{extension}"
    if path.suffix.lower() == suffix.lower():
        return path
    return path.with_name(path.name + suffix)


def contained_file(path: Path, root: Path | None) -> Path | None:
    """Resolve ``path`` and return it only when it stays inside ``root``.

    Symlinks are resolved before the check so a link inside the document
    directory cannot point at a file outside it.
    """
    resolved = path.resolve()
    if root is None:
        return resolved
    if resolved.is_relative_to(root):
        return resolved
    return None


def resolve_link(
    value: str, base_dir: Path | None, base_file: Gio.File | None
) -> str | None:
    """Resolve a Markdown link to a URI, or ``None`` when it is unsafe.

    External http(s) and mailto links pass through, fragments stay fragments,
    and document-relative paths become contained ``file:`` URIs.
    """
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme in _UNSAFE_SCHEMES:
        return None
    if scheme in ("http", "https", "mailto"):
        return value
    if scheme:
        return None
    if value.startswith("#"):
        return value
    if value.startswith("//"):
        return None

    suffix = ""
    if parsed.query:
        suffix += f"?{parsed.query}"
    if parsed.fragment:
        suffix += f"#{parsed.fragment}"

    root = document_root(base_dir, base_file)
    if base_file is not None:
        parent = base_file.get_parent()
        if parent is None:
            return None
        target = parent.resolve_relative_path(unquote(parsed.path))
        if root is None:
            # No local directory to confine to (for example a remote GIO
            # mount); keep the existing URI behaviour.
            uri = target.get_uri()
            return uri + suffix if uri else None
        local = target.get_path()
        if local is None:
            return None
        path = contained_file(Path(local), root)
        if path is None:
            return None
        return path.as_uri() + suffix
    if base_dir is None:
        return value
    path = contained_file(base_dir / unquote(parsed.path), root)
    if path is None:
        return None
    return path.as_uri() + suffix
