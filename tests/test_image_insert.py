"""Headless tests for validated asynchronous image insertion helpers."""

import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from unittest import mock

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gdk, GLib

from src import image_insert
from src.image_policy import ImageTooLargeError, ImageValidationError


def make_png(width=2, height=2):
    def chunk(kind, data):
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


def wait_result():
    result = []
    deadline = time.monotonic() + 4

    def callback(path, error):
        result.append((path, error))

    def wait():
        while not result and time.monotonic() < deadline:
            while GLib.MainContext.default().iteration(False):
                pass
            time.sleep(0.005)
        assert result, "timed out waiting for image preparation"
        return result[0]

    return callback, wait


with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
    root = Path(directory)
    outside = Path(other)
    local = root / "local image.png"
    local.write_bytes(make_png())
    external = outside / "external.png"
    external.write_bytes(make_png(3, 4))

    callback, wait = wait_result()
    image_insert.prepare_file_image_async(local, root, callback)
    prepared, error = wait()
    assert error is None
    assert prepared == local.resolve()
    assert not (root / "assets").exists()

    callback, wait = wait_result()
    image_insert.prepare_file_image_async(external, root, callback)
    prepared, error = wait()
    assert error is None
    assert prepared is not None and prepared.parent == root / "assets"
    assert prepared.read_bytes() == external.read_bytes()

    callback, wait = wait_result()
    image_insert.prepare_file_image_async(external, None, callback)
    prepared, error = wait()
    assert error is None
    assert prepared == external.resolve()

    invalid = root / "invalid.png"
    invalid.write_text("not an image", encoding="utf-8")
    callback, wait = wait_result()
    image_insert.prepare_file_image_async(invalid, root, callback)
    prepared, error = wait()
    assert prepared is None
    assert isinstance(error, ImageValidationError)

    with mock.patch.object(image_insert, "MAX_IMAGE_BYTES", 1):
        callback, wait = wait_result()
        image_insert.prepare_file_image_async(local, root, callback)
        prepared, error = wait()
        assert prepared is None
        assert isinstance(error, ImageTooLargeError)

    callback, wait = wait_result()
    worker_threads = []
    original_write = image_insert._write_atomic

    def recording_write(path, payload):
        worker_threads.append(threading.get_ident())
        return original_write(path, payload)

    image_insert._write_atomic = recording_write
    try:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(make_png()))
        image_insert.prepare_clipboard_image_async(texture, root, callback)
        prepared, error = wait()
    finally:
        image_insert._write_atomic = original_write
    assert error is None
    assert prepared is not None and prepared.parent == root / "assets"
    assert prepared.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert worker_threads and worker_threads[0] != threading.get_ident()

    callback, wait = wait_result()
    image_insert.prepare_clipboard_image_async(texture, None, callback)
    prepared, error = wait()
    assert error is None
    assert prepared is not None and prepared.is_absolute()
    assert prepared.parent.name.startswith("md-split-clipboard-")
    shutil.rmtree(prepared.parent)


class OversizedTexture:
    def get_width(self):
        return 5000

    def get_height(self):
        return 5000


try:
    image_insert._texture_png_bytes(OversizedTexture())
except ImageTooLargeError:
    pass
else:
    raise AssertionError("oversized clipboard texture was accepted")

print("ok: asynchronous image insertion")
