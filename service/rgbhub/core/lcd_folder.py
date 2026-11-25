# SPDX-License-Identifier: GPL-3.0-or-later
"""A folder that says what the LCD shows when nothing is in focus.

Each system has one such folder (a platform decides where). If it holds
something usable, that overrides the system's default idle picture:

- Several JPEG files: an animation. They must be LCD-ready (480x480, at
  most 60,000 bytes each) and are played in name order, in a loop. A text
  file `frame_ms` may give the time per frame in milliseconds.
- One picture file of any common kind: shown as a still.
- One animated file (GIF, animated WebP or PNG): played as an animation.

An empty or missing folder means "no override".
"""

import logging
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageSequence

from .constants import MAX_JPEG_BYTES
from .look import Animation

log = logging.getLogger(__name__)

FRAME_TYPES = (".jpg", ".jpeg")
PICTURE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
DEFAULT_FRAME_MS = 100
MAX_FRAMES = 600  # a minute at ten frames a second


def load_lcd_folder(
    folder: Path, compose: Callable[[Image.Image], bytes]
) -> "Animation | Image.Image | None":
    """What the folder asks for: an Animation, a still picture, or None.

    `compose` turns one picture into an LCD-ready JPEG; it is used for the
    frames of an animated file. A still is returned as it is, for the
    caller to lay out.
    """
    try:
        files = sorted(p for p in folder.iterdir() if p.is_file())
    except OSError:
        return None
    frames = [p for p in files if p.suffix.lower() in FRAME_TYPES]
    if len(frames) > 1:
        return _from_frames(frames, folder)
    pictures = [p for p in files if p.suffix.lower() in PICTURE_TYPES]
    if not pictures:
        return None
    try:
        with Image.open(pictures[0]) as picture:  # closes the file when done
            if getattr(picture, "n_frames", 1) > 1:
                return _from_animated_file(picture, compose)
            return picture.convert("RGBA")
    except Exception as error:  # Pillow raises many kinds
        log.warning("Cannot use %s: %s", pictures[0], error)
        return None


def _from_frames(files: list[Path], folder: Path) -> Animation | None:
    frames = []
    for file in files[:MAX_FRAMES]:
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if data.startswith(b"\xff\xd8") and len(data) <= MAX_JPEG_BYTES:
            frames.append(data)
    if not frames:
        return None
    try:
        frame_ms = int((folder / "frame_ms").read_text().strip())
    except (OSError, ValueError):
        frame_ms = DEFAULT_FRAME_MS
    return Animation(tuple(frames), frame_ms)


def _from_animated_file(picture: Image.Image, compose) -> Animation:
    frame_ms = int(picture.info.get("duration") or DEFAULT_FRAME_MS)
    frames = []
    for index, frame in enumerate(ImageSequence.Iterator(picture)):
        if index >= MAX_FRAMES:
            break
        frames.append(compose(frame.convert("RGBA")))
    return Animation(tuple(frames), frame_ms)
