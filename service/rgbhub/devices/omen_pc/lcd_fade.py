# SPDX-License-Identifier: GPL-3.0-or-later
"""Crossfade between two LCD pictures."""

import io
from collections.abc import Iterator

from PIL import Image

from .image_prep import encode_for_lcd
from .lcd_constants import LCD_SIZE, MAX_JPEG_BYTES

FADE_JPEG_QUALITY = 75  # in-between frames are on screen for a blink; small beats sharp


def fade_frames(old: bytes | None, new: bytes | None, steps: int) -> Iterator[bytes]:
    """Yield `steps` LCD-ready JPEGs that blend from `old` into `new`.

    None stands for a black screen. The last frame is `new` itself (or
    black). Frames are made one at a time, as they are asked for, so a fade
    never ties the program up for long.
    """
    start, end = _decode(old), _decode(new)
    for step in range(1, steps):
        yield _encode(Image.blend(start, end, step / steps))
    yield new if new is not None else _encode(end)


def _decode(jpeg: bytes | None) -> Image.Image:
    """The picture as a full-size RGB image; black if there is none."""
    if jpeg:
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                image = image.convert("RGB")
            if image.size == (LCD_SIZE, LCD_SIZE):
                return image
        except Exception:  # a picture we cannot read simply fades from black
            pass
    return Image.new("RGB", (LCD_SIZE, LCD_SIZE), (0, 0, 0))


def _encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=FADE_JPEG_QUALITY)
    data = buffer.getvalue()
    return data if len(data) <= MAX_JPEG_BYTES else encode_for_lcd(image)
