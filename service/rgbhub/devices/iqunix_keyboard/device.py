# SPDX-License-Identifier: GPL-3.0-or-later
"""The IQUNIX keyboard as one hub device.

Its lights are one LightGroup over via.py. In auto mode the whole
keyboard takes the colour of what is in focus. When the hub stops, the
Default look is stored in the keyboard itself, so it stays after the
computer stops talking to it (in Windows, say, or with the PC off).
"""

from ...core.light_group import LightGroup
from ...core.lights import Lights

DEVICE_ID = "IQUNIX-KEYBOARD"


class IqunixKeyboard:
    id = DEVICE_ID
    label = "IQUNIX-KEYBOARD"
    icon = "input-keyboard-symbolic"

    def __init__(self, lights: Lights, display_names: dict[str, str] | None = None):
        """`lights` drives the keyboard (Lights over via.py)."""
        self._lights = lights
        self._display_names = display_names or {}
        self._hub = None
        self.lights: LightGroup | None = None

    def attach(self, hub) -> None:
        self._hub = hub
        self.lights = LightGroup(
            hub, DEVICE_ID, [self._lights], self._auto_colour, self._display_names
        )

    def _auto_colour(self) -> str | None:
        hub = self._hub
        return hub.look.colour if hub.auto_for(DEVICE_ID) else None

    def start(self) -> None:
        self.lights.start()

    def stop(self) -> None:
        self.lights.stop()

    def show_default(self) -> None:
        self.lights.finish_now()
        self.lights.keep()

    def on_changed(self, key: str) -> None:
        self.lights.on_changed(key)

    def on_look_changed(self) -> None:
        self.lights.on_look_changed()

    def on_sleep(self) -> None:
        self.lights.on_sleep()

    def on_resume(self) -> None:
        self.lights.on_resume()

    def tick_slow(self) -> None:
        self.lights.tick_slow()

    def status(self) -> dict:
        (connected,) = self.lights.connected
        return {
            "label": self.label,
            "icon": self.icon,
            "has-lcd": False,
            "connected": connected,
            "lights": self.lights.describe(),
        }
