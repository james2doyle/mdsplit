# SPDX-License-Identifier: MIT

"""Shared resource limits and validation errors for local image input."""

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


class ImageValidationError(Exception):
    """The selected input is not a readable, supported image."""


class ImageTooLargeError(ImageValidationError):
    """The selected image exceeds the byte or pixel resource limit."""
