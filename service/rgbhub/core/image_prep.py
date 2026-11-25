# SPDX-License-Identifier: GPL-3.0-or-later
"""Turns any image file into the 480x480 JPEG the cooler LCD expects."""

import io
from pathlib import Path

from PIL import Image, ImageOps

from .constants import LCD_SIZE, MAX_JPEG_BYTES

# JPEG qualities to try, best first, until the result is small enough to send.
QUALITY_STEPS = (90, 80, 70, 60, 50, 40, 30)


class ImageError(Exception):
    """The chosen file could not be turned into an LCD image."""


def prepare_jpeg(path: Path, fit: str) -> bytes:
    """Load `path` and return JPEG bytes ready for the LCD.

    fit="fit" shows the whole picture with black bars; anything else
    ("fill") crops the centre so the screen is completely covered.
    Whatever goes wrong, the only exception that leaves here is ImageError.
    """
    try:
        with Image.open(path) as opened:
            opened.seek(0)  # first frame of an animation
            image = ImageOps.exif_transpose(opened)  # honour phone rotation
            image = _flatten_onto_black(image)
        if fit == "fit":
            image = _letterbox(image)
        else:
            image = ImageOps.fit(image, (LCD_SIZE, LCD_SIZE))
        return encode_for_lcd(image)
    except Exception as error:  # Pillow raises many kinds; the caller needs one
        raise ImageError(f"Cannot use {path}: {error}") from error


def _letterbox(image: Image.Image) -> Image.Image:
    """The whole picture, centred on a black square."""
    scale = min(LCD_SIZE / image.width, LCD_SIZE / image.height)
    # max(1, ...) keeps extremely thin pictures from shrinking to nothing.
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    square = Image.new("RGB", (LCD_SIZE, LCD_SIZE), (0, 0, 0))
    square.paste(image.resize(size), ((LCD_SIZE - size[0]) // 2, (LCD_SIZE - size[1]) // 2))
    return square


def _flatten_onto_black(image: Image.Image) -> Image.Image:
    """Return an RGB copy, with any transparent areas shown as black."""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _encode(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def encode_for_lcd(image: Image.Image) -> bytes:
    """JPEG bytes of a 480x480 RGB picture, small enough for the LCD.

    Lowers the quality, then softens the picture, until the JPEG fits.
    """
    original = image
    detail = LCD_SIZE  # how many pixels of real detail we keep per side
    while True:
        for quality in QUALITY_STEPS:
            data = _encode(image, quality)
            if len(data) <= MAX_JPEG_BYTES:
                return data
        # Extremely detailed picture: soften it by shrinking and restoring
        # its size, keeping half as much detail each round. This always
        # ends, because a picture with almost no detail compresses to
        # almost nothing.
        detail = max(1, detail // 2)
        image = original.resize((detail, detail)).resize((LCD_SIZE, LCD_SIZE))
