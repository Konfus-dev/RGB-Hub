# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the look of a GNOME desktop: its wallpaper.

Used as the idle look, so that with nothing in focus the PC matches the
desktop: the wallpaper on the LCD, and its main colour on the lights (picked
the same way as for an app's icon).
"""

from collections.abc import Callable
from urllib.parse import unquote, urlparse

from gi.repository import Gio

INTERFACE = "org.gnome.desktop.interface"
BACKGROUND = "org.gnome.desktop.background"


def pick_wallpaper(colour_scheme: str, uri: str, uri_dark: str) -> str | None:
    """The wallpaper file in use, given GNOME's settings; None if it is not
    a local file."""
    chosen = uri_dark if colour_scheme == "prefer-dark" and uri_dark else uri
    parsed = urlparse(chosen)
    if parsed.scheme != "file" or not parsed.path:
        return None
    return unquote(parsed.path)


class DesktopTheme:
    """The current wallpaper, and a way to hear when it changes."""

    def __init__(self):
        self._interface = Gio.Settings.new(INTERFACE)
        self._background = Gio.Settings.new(BACKGROUND)

    def wallpaper(self) -> str | None:
        return pick_wallpaper(
            self._interface.get_string("color-scheme"),
            self._background.get_string("picture-uri"),
            self._background.get_string("picture-uri-dark"),
        )

    def on_change(self, callback: Callable[[], None]) -> None:
        """Call `callback` when the wallpaper, the dark/light choice or the icon theme changes."""
        for key in ("color-scheme", "icon-theme"):
            self._interface.connect(f"changed::{key}", lambda *_: callback())
        for key in ("picture-uri", "picture-uri-dark"):
            self._background.connect(f"changed::{key}", lambda *_: callback())
