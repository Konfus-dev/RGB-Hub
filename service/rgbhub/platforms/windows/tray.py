# SPDX-License-Identifier: GPL-3.0-or-later
"""RGB-Hub's icon in the Windows notification area, and its menu.

    Settings            opens the settings page (also a left click on the icon)
    Auto mode           ticked when every device follows the app in front
    Approve update…     only while an update waits for the owner's approval
    Quit                leaves the Default look and stops RGB-Hub

Drawn with pystray, which runs the icon's own Win32 message loop. On Windows
that loop may run on any thread (run_detached() starts one), so the hub's
loop keeps the main thread, as on the other systems. The menu's actions run
on the icon's thread: the callbacks given here must hand real work to the hub's
loop themselves. pystray is imported in start(), so this module imports
without it (on Linux, for the tests).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

TITLE = "RGB-Hub"
ICON_SIZE = 64
WM_QUERYENDSESSION = 0x0011
READY_WAIT_S = 5.0  # how long stop() waits for an icon still starting


def allow_sign_out(icon) -> None:
    """Let Windows sign out and shut down while the icon is showing.

    pystray 0.19.5 (pinned in the windows extra) answers every message its
    window has no handler for with 0; for WM_QUERYENDSESSION that means "do
    not end the session", which holds sign-out and shutdown back. Its handler
    table is private (Icon._message_handlers, _win32.py): check this on any
    pystray upgrade."""
    handlers = getattr(icon, "_message_handlers", None)
    if isinstance(handlers, dict):
        handlers[WM_QUERYENDSESSION] = lambda _wparam, _lparam: 1
    else:
        log.warning("pystray has changed: the tray icon may hold sign-out back")


@dataclass
class Entry:
    """One menu item, as pystray will be given it."""

    text: str
    action: Callable[[], None]
    checked: Callable[[], bool] | None = None
    visible: Callable[[], bool] = lambda: True
    default: bool = False


def draw_icon(size: int = ICON_SIZE) -> Image.Image:
    """The icon: a ring in red, green and blue on a dark rounded square."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=(24, 24, 28, 255))
    margin, width = size // 6, max(2, size // 9)
    box = (margin, margin, size - 1 - margin, size - 1 - margin)
    for start, colour in ((-90, (235, 50, 60)), (30, (60, 210, 90)), (150, (60, 120, 240))):
        draw.arc(box, start, start + 120, fill=colour + (255,), width=width)
    return image


class Tray:
    def __init__(
        self,
        open_settings: Callable[[], None],
        set_auto_all: Callable[[bool], None],
        auto_all: Callable[[], bool],
        quit: Callable[[], None],
        approve_update: Callable[[], None] | None = None,
    ):
        self._open_settings = open_settings
        self._set_auto_all = set_auto_all
        self._auto_all = auto_all
        self._quit = quit
        self._approve_update = approve_update
        self._icon = None
        self._ready = threading.Event()  # the icon's thread is running

    def entries(self) -> list[Entry]:
        return [
            Entry("Settings", self._guard("Settings", self._open_settings), default=True),
            Entry(
                "Auto mode",
                self._guard("Auto mode", lambda: self._set_auto_all(not self._auto_all())),
                checked=lambda: bool(self._auto_all()),
            ),
            Entry(
                "Approve update…",
                self._guard(
                    "Approve update…", lambda: self._approve_update and self._approve_update()
                ),
                visible=lambda: self._approve_update is not None,
            ),
            Entry("Quit", self._guard("Quit", self._quit)),
        ]

    def offer_update(self, approve_update: Callable[[], None] | None) -> None:
        """Show "Approve update…" (running `approve_update`), or hide it with None."""
        self._approve_update = approve_update
        self.refresh()

    @staticmethod
    def _guard(text: str, action: Callable[[], object]) -> Callable[[], None]:
        def run() -> None:
            try:
                action()
            except Exception:  # pystray would drop it silently
                log.exception("The tray menu's '%s' failed", text)

        return run

    # --- the icon (Windows) -------------------------------------------------------

    def start(self) -> None:
        """Show the icon. Its message loop runs on a thread of pystray's own."""
        import pystray

        def item(entry: Entry):
            checked = entry.checked
            return pystray.MenuItem(
                entry.text,
                lambda _icon, _item: entry.action(),
                checked=(lambda _item: checked()) if checked else None,
                visible=lambda _item: entry.visible(),
                default=entry.default,
            )

        menu = pystray.Menu(*(item(entry) for entry in self.entries()))
        icon = pystray.Icon("rgbhub", draw_icon(), TITLE, menu=menu)
        allow_sign_out(icon)

        def setup(running) -> None:  # on pystray's setup thread, once its loop runs
            running.visible = True
            self._ready.set()

        self._icon = icon
        icon.run_detached(setup)

    def refresh(self) -> None:
        """Show the menu's ticks and items as they are now."""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                log.exception("Could not update the tray menu")

    def stop(self) -> None:
        if self._icon is not None:
            # pystray ignores stop() until its loop runs, and that thread would
            # then keep the process alive: let a starting icon get going first.
            if not self._ready.wait(READY_WAIT_S):
                log.warning("The tray icon did not start in %.0f s", READY_WAIT_S)
            try:
                self._icon.stop()
            except Exception:
                log.exception("Could not remove the tray icon")
            self._icon = None
