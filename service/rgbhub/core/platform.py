# SPDX-License-Identifier: GPL-3.0-or-later
"""What a system gives the devices so each can build itself.

A platform's main() fills one Platform in and hands it to
devices.build_all(); a device picks what it needs and returns None when it
cannot work here. Only core may be imported from this file.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .interfaces import HidBus, RamBus, Runtime


@dataclass
class Platform:
    name: str  # "gnome" | "batocera" | "windows"
    hid_bus: HidBus
    runtime: Runtime
    ram_bus: RamBus | None = None
    # Opens the case lights' lighting client; None = our own driver over hid_bus.
    case_lights: Callable[[], object] | None = None
    lcd_fade: bool = True
