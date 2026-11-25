# SPDX-License-Identifier: GPL-3.0-or-later
"""The lights of one hub device, kept in step with the settings.

A hub device can have several sets of lights (the OMEN PC has its case
board and its RAM). This class turns the settings into calls on them:
batching changes while a slider is dragged, running the fast timer only
while something fades or breathes, reconnecting, and sleep and wake.

Each set of lights is a lights.Lights or a ram.RamLights: both take the
same apply() call.
"""

import logging
import time
from collections.abc import Callable

from .lights import SOFTWARE_BREATHING, DeviceInfo, EffectOptions, UniformColours, zone_key

log = logging.getLogger(__name__)

# Changes arriving within this time of each other are sent as one. It only
# needs to cover a slider being dragged; fades hide the rest.
LIGHTS_DEBOUNCE_MS = 40
ANIMATION_INTERVAL_MS = 33  # about 30 frames a second while an effect is animated
SLEEP_WAIT_S = 3.0  # how long to wait for the RAM to go dark before sleeping
STOP_WAIT_S = 5.0  # how long to wait for the Default to reach the RAM when stopping
STEADY_MODE = "Static"  # what auto mode's colour is shown with on a colourless effect

LIGHT_KEYS = (
    "device-enabled",
    "device-brightness",
    "device-modes",
    "zone-colours",
    "device-speeds",
    "device-min-brightness",
    "device-max-brightness",
)


class LightGroup:
    def __init__(
        self,
        hub,
        device_id: str,
        lights: list,
        auto_colour: Callable[[], str | None],
        display_names: dict[str, str] | None = None,
        slow: tuple = (),
    ):
        """`hub` gives settings and timers (core.hub.Hub). `lights` are the
        Lights / RamLights of this device. `auto_colour()` is the colour
        auto mode wants on them now, or None. `slow` are the members of
        `lights` that are sent in the background and must be waited for
        (the RAM)."""
        self._hub = hub
        self._id = device_id
        self._lights = list(lights)
        self._slow = tuple(slow)
        self._auto_colour = auto_colour
        self._names = display_names or {}
        self._sent_auto_colour: str | None = None
        self._debounce = None  # timer handle while an update is waiting
        self._animation = None  # timer handle while an effect is animated

    # --- what the front end sees ------------------------------------------------

    @property
    def connected(self) -> list[bool]:
        return [lights.connected for lights in self._lights]

    def describe(self) -> list[dict]:
        """Every lighting device found, as the settings window needs it.

        "name" and "zones" are the real names, used as keys when saving.
        "label" and "zone_labels" are what a person should read.
        """
        return [self._describe(device) for lights in self._lights for device in lights.devices()]

    def _describe(self, device: DeviceInfo) -> dict:
        names = self._names
        return {
            "name": device.name,
            "label": names.get(device.name, device.name),
            "modes": list(device.modes),
            "default_mode": device.default_mode,
            "colourless_modes": list(device.colourless_modes),
            "speed_modes": list(device.speed_modes),
            "range_modes": list(device.range_modes),
            "zones": list(device.zones),
            "zone_labels": [names.get(zone_key(device.name, zone), zone) for zone in device.zones],
        }

    # --- reacting ---------------------------------------------------------------

    def start(self) -> None:
        for lights in self._lights:
            lights.try_connect()
        self.flush()

    def on_changed(self, key: str) -> None:
        if key in LIGHT_KEYS:
            self.schedule()

    def on_look_changed(self) -> None:
        if self._auto_colour() != self._sent_auto_colour:
            self.schedule()  # only when the lights would really change

    def schedule(self) -> None:
        """Update the lights shortly, once, however many changes arrive."""
        if self._debounce is None:
            self._debounce = self._hub.scheduler.every(LIGHTS_DEBOUNCE_MS, self._on_debounce)

    @property
    def update_pending(self) -> bool:
        return self._debounce is not None

    def _on_debounce(self) -> bool:
        self._debounce = None
        self._hub.repeat(self.flush)
        return False  # one-shot timer

    def flush(self) -> None:
        """Send the current light settings now."""
        if self._debounce is not None:
            self._hub.scheduler.cancel(self._debounce)
            self._debounce = None
        self._sent_auto_colour = self._auto_colour()
        arguments = self.settings()
        for lights in self._lights:
            if lights.connected:
                lights.apply(*self._for(lights, arguments))
        self._sync_animation()

    def _for(self, lights, arguments: tuple) -> tuple:
        """While the Default is being left behind, a breath this program
        draws itself would stop mid-breath when the program ends: show
        those lights steady instead. (The RAM breathes by itself.)"""
        if not self._hub.stopping or lights in self._slow:
            return arguments
        enabled, brightness, modes, colours, options = arguments
        steady = {
            name: ("Static" if mode == SOFTWARE_BREATHING else mode) for name, mode in modes.items()
        }
        return enabled, brightness, steady, colours, options

    def settings(self) -> tuple[bool, int, dict, dict, dict]:
        """The arguments for Lights.apply / RamLights.apply."""
        hub = self._hub
        speeds = hub.get("device-speeds")
        dimmest = hub.get("device-min-brightness")
        brightest = hub.get("device-max-brightness")
        defaults = EffectOptions()
        options = {
            name: EffectOptions(
                speed=speeds.get(name, defaults.speed),
                min_brightness=dimmest.get(name, defaults.min_brightness),
                max_brightness=brightest.get(name, defaults.max_brightness),
            )
            for name in {*speeds, *dimmest, *brightest}
        }
        auto = self._auto_colour()
        modes = hub.get("device-modes")
        return (
            bool(hub.get("device-enabled").get(self._id, True)) and not hub.asleep,
            int(hub.get("device-brightness").get(self._id, 100)),
            self._showing_colour(modes) if auto else modes,
            UniformColours(auto) if auto else hub.get("zone-colours"),
            options,
        )

    def _showing_colour(self, modes: dict) -> dict:
        """Auto mode shows a colour, so an effect that ignores colours would
        hide it: such a device (the RAM, whose own default is Rainbow) shows
        the colour steady instead. Effects that use the colour are kept."""
        result = dict(modes)
        for lights in self._lights:
            for device in lights.devices():
                mode = result.get(device.name, device.default_mode)
                if mode in device.colourless_modes and STEADY_MODE in device.modes:
                    result[device.name] = STEADY_MODE
        return result

    # --- fades and breathing ----------------------------------------------------

    @property
    def animating(self) -> bool:
        return any(getattr(lights, "animating", False) for lights in self._lights)

    @property
    def animation_running(self) -> bool:
        return self._animation is not None

    def _sync_animation(self) -> None:
        """Run the fast animation timer only while something is animated."""
        if self.animating and self._animation is None:
            self._animation = self._hub.scheduler.every(ANIMATION_INTERVAL_MS, self._on_animation)
        elif not self.animating and self._animation is not None:
            self._hub.scheduler.cancel(self._animation)
            self._animation = None

    def _on_animation(self) -> bool:
        self._hub.repeat(self._tick_animation)
        if not self.animating:
            self._animation = None
            return False  # fade finished and nothing is breathing: stop the timer
        return True

    def _tick_animation(self) -> None:
        now = time.monotonic()
        for lights in self._lights:
            if getattr(lights, "animating", False):
                lights.tick_animation(now)

    # --- housekeeping -----------------------------------------------------------

    def tick_slow(self) -> None:
        """Every few seconds: reconnect what went away."""
        reconnected = False
        for lights in self._lights:
            if not lights.check():
                reconnected |= lights.try_connect()
            elif hasattr(lights, "refresh"):
                lights.refresh()  # in case the board forgot its colours
        if reconnected:
            self.flush()
        self._sync_animation()  # a connection may have come or gone

    def on_sleep(self) -> None:
        """Lights off at once (the hub already counts as asleep)."""
        self.finish_now(SLEEP_WAIT_S)

    def on_resume(self) -> None:
        """The hardware may have forgotten its settings: send them all again."""
        for lights in self._lights:
            lights.forget()
        self.flush()

    def finish_now(self, wait_s: float = STOP_WAIT_S) -> None:
        """Send the settings, skip any fade, and wait for slow lights."""
        self.flush()
        for lights in self._lights:
            if hasattr(lights, "finish_transitions"):
                lights.finish_transitions()
        for lights in self._slow:
            lights.wait_idle(wait_s)

    def keep(self) -> None:
        """Ask lights that can store their look to store it (when stopping)."""
        for lights in self._lights:
            if hasattr(lights, "store"):
                lights.store()

    def stop(self) -> None:
        for timer in (self._debounce, self._animation):
            if timer is not None:
                self._hub.scheduler.cancel(timer)
        self._debounce = self._animation = None
