# SPDX-License-Identifier: MIT

"""Validated, asynchronous image preparation for Markdown insertion."""

from __future__ import annotations

import os
import re
import stat
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gdk, GLib

from .image_policy import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    ImageTooLargeError,
    ImageValidationError,
)

try:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
except (ImportError, ValueError):
    GdkPixbuf = None

ImageCallback = Callable[[Path | None, Exception | None], None]


def validate_image_file(path: Path) -> None:
    """Reject missing, invalid, or oversized local image files."""
    try:
        info = path.stat()
    except OSError as error:
        raise ImageValidationError from error
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise ImageValidationError
    if info.st_size > MAX_IMAGE_BYTES:
        raise ImageTooLargeError
    if GdkPixbuf is None:
        return
    try:
        image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    except GLib.Error as error:
        raise ImageValidationError from error
    if image_format is None or width <= 0 or height <= 0:
        raise ImageValidationError
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageTooLargeError


def _read_image(path: Path) -> bytes:
    validate_image_file(path)
    with path.open("rb") as stream:
        payload = stream.read(MAX_IMAGE_BYTES + 1)
    if len(payload) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError
    return payload


def _texture_png_bytes(texture: Gdk.Texture) -> bytes:
    width = texture.get_width()
    height = texture.get_height()
    if width <= 0 or height <= 0:
        raise ImageValidationError
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageTooLargeError
    payload = texture.save_to_png_bytes().get_data()
    if not payload:
        raise ImageValidationError
    if len(payload) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError
    return payload


def _assets_directory(document_dir: Path) -> Path:
    root = document_dir.resolve()
    directory = root / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to(root):
        raise ImageValidationError
    return directory


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".image"


def _deliver(
    callback: ImageCallback, path: Path | None, error: Exception | None
) -> None:
    GLib.idle_add(callback, path, error)


def prepare_file_image_async(
    source: Path, document_dir: Path | None, callback: ImageCallback
) -> None:
    """Validate a picked file, copying it into assets when it is outside root."""

    def worker() -> None:
        try:
            resolved = source.resolve()
            root = document_dir.resolve() if document_dir is not None else None
            if root is None or resolved.is_relative_to(root):
                validate_image_file(resolved)
                prepared = resolved
            else:
                payload = _read_image(resolved)
                directory = _assets_directory(root)
                prepared = directory / f"image-{uuid.uuid4().hex}{_suffix(resolved)}"
                _write_atomic(prepared, payload)
        except Exception as error:
            _deliver(callback, None, error)
            return
        _deliver(callback, prepared, None)

    threading.Thread(
        target=worker, daemon=True, name="md-split-image-file"
    ).start()


def prepare_clipboard_image_async(
    texture: Gdk.Texture, document_dir: Path | None, callback: ImageCallback
) -> None:
    """Encode a clipboard texture off-main-thread and save it as a PNG."""

    def worker() -> None:
        try:
            payload = _texture_png_bytes(texture)
            if document_dir is not None:
                directory = _assets_directory(document_dir)
            else:
                directory = Path(tempfile.mkdtemp(prefix="md-split-clipboard-"))
            prepared = directory / f"clipboard-{uuid.uuid4().hex}.png"
            _write_atomic(prepared, payload)
        except Exception as error:
            _deliver(callback, None, error)
            return
        _deliver(callback, prepared, None)

    threading.Thread(
        target=worker, daemon=True, name="md-split-image-clipboard"
    ).start()
