# SPDX-License-Identifier: GPL-3.0-or-later
"""Our own small driver for the OMEN case-light board ("HP TracerLED").

Used where OpenRGB is not available (Batocera). It only knows the board's
"direct" mode, in which each zone shows the colour it is sent; fades and
breathing are drawn on top of that by lights.py, exactly as with OpenRGB.

To lights.py it looks like an OpenRGB client: a `devices` list whose one
device has `modes`, `zones`, `set_mode()` and `set_colors()`. It uses the
same device and zone names as OpenRGB, so saved settings work with either.

Packet layout from OpenRGB's HPOmen30LController (GPL-2.0-or-later).
"""

import logging
from dataclasses import dataclass, field

from ...core.interfaces import HidBus, HidMatch, HidPort

log = logging.getLogger(__name__)

BOARD_MATCH = HidMatch(0x103C, 0x84FD)
DEVICE_NAME = "HP Omen 30L"  # OpenRGB's name for this board

# The zones that have lights on the OMEN 45L, and the board's number for each.
ZONES = (
    ("Omen Logo", 1),
    ("Front Bottom Fan", 5),
    ("Front Middle Fan", 6),
    ("Front Top Fan", 7),
)

REPORT_LEN = 58
VERSION = 0x12
MODE_DIRECT = 0x04
POWER_ON = 0x01  # these settings apply while the computer is on
FULL_LEVEL = 100


def direct_report(zone: int, red: int, green: int, blue: int) -> bytes:
    """The report that makes one zone (1..7) show one colour."""
    if not 1 <= zone <= 7:
        raise ValueError(f"zone must be 1..7, got {zone}")
    report = bytearray(REPORT_LEN)
    report[0x02] = VERSION
    report[0x03] = MODE_DIRECT
    report[0x04] = 1  # one colour ...
    report[0x05] = 1  # ... and this is the first
    offset = 0x08 + 4 * (zone - 1)
    report[offset] = FULL_LEVEL
    report[offset + 1 : offset + 4] = bytes(max(0, min(255, c)) for c in (red, green, blue))
    report[0x30] = FULL_LEVEL  # overall brightness
    report[0x31] = MODE_DIRECT
    report[0x36] = zone
    report[0x37] = POWER_ON
    return bytes(report)


@dataclass
class _Mode:
    """Just enough of an OpenRGB mode for lights.py."""

    name: str
    colors: None = None  # no palette: every mode here is drawn by us
    color_mode: int = 1
    speed_min: None = None
    speed_max: None = None


@dataclass
class _Zone:
    name: str
    number: int
    leds: list = field(default_factory=lambda: [0])  # one light per zone


class _Device:
    name = DEVICE_NAME

    def __init__(self, port):
        self._port = port
        self.modes = [_Mode("Direct"), _Mode("Static"), _Mode("Breathing")]
        self.zones = [_Zone(name, number) for name, number in ZONES]

    def set_mode(self, mode) -> None:
        """Nothing to do: the board is always driven in direct mode."""

    def set_colors(self, colours, fast: bool = False) -> None:
        """Show one colour per zone, in zone order."""
        for zone, colour in zip(self.zones, colours, strict=True):
            self._port.write(direct_report(zone.number, colour.red, colour.green, colour.blue))
        log.debug("Case lights: sent %s", [tuple(colour) for colour in colours])


class TracerClient:
    def __init__(self, port: HidPort):
        self._port = port
        self.devices = [_Device(port)]

    def update(self) -> None:
        """Raise OSError if the board has gone away."""
        if not self._port.alive():
            raise OSError("case-light board is gone")

    def disconnect(self) -> None:
        self._port.close()


def open_board(bus: HidBus) -> TracerClient:
    """Find and open the board. Raises OSError if it is not there."""
    path = bus.find(BOARD_MATCH)
    if path is None:
        raise OSError("case-light board not found")
    log.info("Case-light board found at %s", path)
    return TracerClient(bus.open(path))
