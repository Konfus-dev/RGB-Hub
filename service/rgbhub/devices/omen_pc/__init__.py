# SPDX-License-Identifier: GPL-3.0-or-later
"""The HP OMEN 45L: cooler LCD, case lights and RAM."""

from ...core.lights import Lights
from ...core.platform import Platform
from . import case_board, model
from .device import DEVICE_ID, OmenPc
from .lcd_fade import fade_frames
from .lcd_streamer import LcdStreamer, lcd_opener
from .ram import RamLights

__all__ = ["DEVICE_ID", "OmenPc", "build"]


def build(platform: Platform) -> OmenPc:
    bus = platform.hid_bus
    opener = platform.case_lights or (lambda: case_board.open_board(bus))
    ram = (
        RamLights(platform.ram_bus, gamma=model.LED_GAMMA, background=True)
        if platform.ram_bus is not None
        else None
    )
    return OmenPc(
        LcdStreamer(lcd_opener(bus)),
        Lights(opener, model.HIDDEN_ZONES, model.LED_GAMMA),
        ram=ram,
        display_names=model.DISPLAY_NAMES,
        fade=fade_frames if platform.lcd_fade else None,
        watch_file=platform.runtime.watch_file,
    )
