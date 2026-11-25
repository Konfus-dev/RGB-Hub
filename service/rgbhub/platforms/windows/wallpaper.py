# SPDX-License-Identifier: GPL-3.0-or-later
"""The Windows wallpaper and logo: auto mode's idle picture when nothing is focused.

RGB-Hub never picks or changes the wallpaper; it only reads which picture
Windows shows now (with Slideshow on, Windows changes it by itself).
"""

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

SPI_GETDESKWALLPAPER = 0x0073
MAX_PATH = 260
LOGO_SIZE = 480
LOGO_BLUE = "#0078D4"


def _ask_windows() -> str:
    """The wallpaper's file as Windows reports it ("" if none)."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.SystemParametersInfoW.argtypes = [
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    buffer = ctypes.create_unicode_buffer(32768)
    if not user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, len(buffer), buffer, 0):
        return ""
    return buffer.value


def current_wallpaper(
    ask: Callable[[], str] = _ask_windows,
    appdata: str | None = os.environ.get("APPDATA"),
) -> Path | None:
    """The picture on the desktop now, or None if there is none that can be read.

    Windows names the file it was set from; if that has gone (a picture from a
    removed drive, a slideshow's temporary copy), its own copy of the current
    wallpaper is used: %APPDATA%\\Microsoft\\Windows\\Themes\\TranscodedWallpaper.
    """
    try:
        named = ask()
    except Exception as error:
        log.debug("Cannot ask Windows for the wallpaper: %s", error)
        named = ""
    if named and Path(named).is_file():
        return Path(named)
    if appdata:
        copy = Path(appdata) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
        if copy.is_file():
            return copy
    return None


def windows_logo() -> Image.Image:
    """The Windows logo (four blue squares on black), for when there is no wallpaper."""
    logo = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 255))
    draw = ImageDraw.Draw(logo)
    square, gap = 150, 12
    start = (LOGO_SIZE - 2 * square - gap) // 2
    for row in range(2):
        for column in range(2):
            left = start + column * (square + gap)
            top = start + row * (square + gap)
            draw.rectangle((left, top, left + square - 1, top + square - 1), fill=LOGO_BLUE)
    return logo
