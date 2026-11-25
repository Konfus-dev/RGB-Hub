# SPDX-License-Identifier: GPL-3.0-or-later
"""The IQUNIX VIA keyboard."""

from ...core.lights import Lights
from ...core.platform import Platform
from . import mg65, via
from .device import DEVICE_ID, IqunixKeyboard

__all__ = ["DEVICE_ID", "IqunixKeyboard", "build"]


def build(platform: Platform) -> IqunixKeyboard:
    return IqunixKeyboard(
        Lights(via.opener(mg65.MG65, platform.hid_bus), name="Keyboard lights"),
        display_names=mg65.DISPLAY_NAMES,
    )
