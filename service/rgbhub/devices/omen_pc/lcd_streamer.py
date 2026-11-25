# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeps an image on the cooler LCD.

The LCD forgets its picture unless it is re-sent constantly, and it can
disappear at any time (suspend, USB hiccup). This class hides both facts:
tell it what to show, call its tick_* methods on timers, and it copes.
"""

import logging
from collections.abc import Callable

from ...core.interfaces import HidBus, HidPort
from . import lcd_protocol
from .lcd_constants import LCD_MATCH

log = logging.getLogger(__name__)

DEFAULT_BRIGHTNESS = 80


def lcd_opener(bus: HidBus) -> Callable[[], HidPort | None]:
    """A function that opens the cooler LCD, or returns None if it is not there.

    Opened non-blocking: a write the LCD cannot take fails (and the streamer
    reconnects) instead of holding up the service.
    """

    def open_lcd() -> HidPort | None:
        path = bus.find(LCD_MATCH)
        return bus.open(path, nonblocking=True) if path else None

    return open_lcd


class LcdStreamer:
    def __init__(self, open_device: Callable[[], object | None]):
        """`open_device` returns an object with write() and close(), or None
        if the LCD is not there. It may also raise OSError."""
        self._open_device = open_device
        self._device = None
        self._frames: list[bytes] = []
        self._brightness = DEFAULT_BRIGHTNESS
        self._brightness_pending = False

    @property
    def connected(self) -> bool:
        return self._device is not None

    def set_image(self, jpeg: bytes | None) -> None:
        """Show this JPEG from the next tick on. None stops sending frames,
        which lets the LCD fall back to its built-in logo."""
        if jpeg is None:
            self._frames = []
            return
        try:
            self._frames = lcd_protocol.frame_reports(jpeg)
        except ValueError as error:
            log.warning("Keeping the previous LCD image: %s", error)

    def set_brightness(self, percent: int) -> None:
        if percent != self._brightness:
            self._brightness = percent
            self._brightness_pending = True

    def tick_claim(self) -> None:
        """Call every CLAIM_INTERVAL_MS."""
        reports = [lcd_protocol.claim_report()]
        if self._brightness_pending:
            reports.append(lcd_protocol.brightness_report(self._brightness))
        if self._send(reports):
            self._brightness_pending = False

    def tick_frame(self) -> None:
        """Call every FRAME_INTERVAL_MS."""
        self._send(self._frames)

    def tick_reconnect(self) -> None:
        """Call every RECONNECT_INTERVAL_S. Does nothing while connected."""
        if self._device is not None:
            return
        try:
            self._device = self._open_device()
        except OSError as error:
            log.debug("LCD not available: %s", error)
            return
        if self._device is None:
            return
        log.info("LCD connected")
        hello = [lcd_protocol.claim_report(), lcd_protocol.brightness_report(self._brightness)]
        if self._send(hello):
            self._brightness_pending = False

    def _send(self, reports: list[bytes]) -> bool:
        """Write reports to the LCD. On any failure, drop the connection."""
        if self._device is None:
            return False
        try:
            for report in reports:
                self._device.write(report)
        except OSError as error:
            log.warning("LCD disconnected: %s", error)
            self._device.close()
            self._device = None
            return False
        return True
