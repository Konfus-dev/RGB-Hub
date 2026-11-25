# SPDX-License-Identifier: GPL-3.0-or-later
"""The devices the hub knows, one package each (see core/device.py).

To add a device: a new folder here with DEVICE_ID and build(platform), one
line in DEVICES, and its id in core/settings_schema.py DEVICE_IDS.
"""

from ..core.device import Device
from ..core.platform import Platform
from . import iqunix_keyboard, omen_pc

DEVICES = (omen_pc, iqunix_keyboard)


def build_all(platform: Platform) -> list[Device]:
    """Every device that can work on this platform, built."""
    built = (module.build(platform) for module in DEVICES)
    return [device for device in built if device is not None]
