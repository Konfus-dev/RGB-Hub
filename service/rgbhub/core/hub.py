# SPDX-License-Identifier: GPL-3.0-or-later
"""The hub: one program for every device with lights.

It watches the settings the front end writes, works out what auto mode
wants shown, notices sleep and wake, and tells every device (see device.py)
about each of these. It writes a small status report back for the settings
window, and when it stops it leaves every device showing the Default look.
"""

import dataclasses
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from . import constants as c
from .interfaces import ManualScheduler, Scheduler, Settings
from .look import Look, Looks
from .settings_schema import DEFAULT_KEYS, parse_default

log = logging.getLogger(__name__)

AUTO_KEYS = (
    "auto-devices",
    "auto-lights",
    "auto-lcd",
    "auto-idle-image",
    "auto-idle-colour",
    "focused-app",
)
SLEPT_JUMP_S = 5.0  # the clocks drifting apart by this much means the computer slept
SLEEP_NEVER_CAME_S = 45.0  # told "going to sleep" this long ago and still running: it did not


def suspended_seconds() -> float:
    """How long this computer has spent asleep since it booted.

    One clock keeps counting through sleep and the other stops, so the
    difference grows by the length of every sleep. 0 where there is no such
    pair of clocks.
    """
    boottime = getattr(time, "CLOCK_BOOTTIME", None)
    if boottime is None:
        return 0.0
    return time.clock_gettime(boottime) - time.clock_gettime(time.CLOCK_MONOTONIC)


class Hub:
    def __init__(
        self,
        settings: Settings,
        devices: list,
        looks: Looks | None = None,
        scheduler: Scheduler | None = None,
        slept: Callable[[], float] = suspended_seconds,
        clock: Callable[[], float] = time.monotonic,
        focus_report_wait_s: float | None = None,
        focus_report_hint: str = "",
    ):
        """settings  where the front end's choices live (interfaces.Settings)
        devices   the devices, each fitting device.Device
        looks     auto mode's source of looks, or None for no auto mode
        scheduler runs our timers (interfaces.Scheduler)
        focus_report_wait_s  where a front end reports what is in focus (the
                  GNOME extension): warn if auto mode is on and no report has
                  come this long after start, or after auto mode was switched
                  on. The front end is then most likely not running.
        focus_report_hint    what to do about it, added to that warning
        """
        self.scheduler: Scheduler = scheduler or ManualScheduler()
        self._settings = settings
        self.devices = list(devices)
        self._looks = looks
        self.look: Look | None = None  # what auto mode wants shown right now
        self.asleep = False  # between "going to sleep" and "woke up"
        self.stopping = False  # set while the Default look is being shown
        self._default: dict[str, Any] = {}
        self._slept = slept
        self._clock = clock
        self._slept_seen = slept()
        self._asleep_since = 0.0
        self._timers: list = []
        self._started = False
        self._focus_report_wait_s = focus_report_wait_s
        self._focus_report_hint = focus_report_hint
        self._focus_reported = False  # a front end has written "focused-app"
        self._focus_watch = None  # the timer that warns if it never does
        for device in self.devices:
            device.attach(self)

    # --- what devices ask ---------------------------------------------------------

    def get(self, key: str) -> Any:
        """A setting, as the devices should see it now: while stopping, the
        Default look's value where it has one."""
        if self.stopping and key in self._default:
            return self._default[key]
        return self._settings.get(key)

    def auto_for(self, device_id: str) -> bool:
        """Does this device follow what is in focus right now?"""
        if self.stopping or self.look is None:
            return False
        return bool(self._settings.get("auto-devices").get(device_id, False))

    @staticmethod
    def repeat(tick: Callable[[], None]) -> bool:
        """Run one timer tick; an error is logged, never raised."""
        try:
            tick()
        except Exception:
            # A timer whose callback raises is usually dropped for good; one
            # bad tick must not stop the LCD or the reconnect checks.
            log.exception("Unexpected error in a timer; carrying on")
        return True  # tells the scheduler to keep the timer running

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Apply the current settings, then keep everything up to date."""
        self._settings.subscribe(self.on_changed)
        self._started = True
        self._refresh_look()
        for device in self.devices:
            self._each(device.start)
        # A request made while the hub was not running (a front end that
        # writes the file directly can) would otherwise never be carried out,
        # and asking again would not count as a change.
        self._default_action()
        self.publish_status()
        self._timers = [
            self.scheduler.every(c.RECONNECT_INTERVAL_S * 1000, lambda: self.repeat(self.tick_slow))
        ]
        self._watch_for_focus_reports()

    def stop(self, show_default: bool = True) -> None:
        """Stop, leaving every device showing the Default look."""
        for timer in self._timers:
            self.scheduler.cancel(timer)
        self._timers = []
        self._stop_focus_watch()
        if not self._started:
            return
        self._started = False
        self._settings.unsubscribe(self.on_changed)
        if show_default:
            self.stopping = True
            self._default = parse_default(self._settings.get("default-look"))
            log.info("Stopping: showing the Default look on every device")
            for device in self.devices:
                self._each(device.show_default)
        for device in self.devices:
            self._each(device.stop)
        # Tell the front end nobody is driving the hardware now.
        self._settings.set("status", "{}")

    @staticmethod
    def _each(call: Callable[[], None]) -> None:
        """One device failing must not stop the others."""
        try:
            call()
        except Exception:
            log.exception("A device failed; carrying on with the others")

    # --- reacting to settings ------------------------------------------------

    def on_changed(self, key: str) -> None:
        """Called whenever one setting changes."""
        if key == "default-action":
            self._default_action()
            return
        if key == "focused-app":
            self._focus_reported = True
            self._stop_focus_watch()
        elif key == "auto-devices":
            self._watch_for_focus_reports()
        if key in AUTO_KEYS:
            self._refresh_look()
            for device in self.devices:
                self._each(device.on_look_changed)
            self.publish_status()
            return
        for device in self.devices:
            self._each(lambda device=device: device.on_changed(key))

    def _default_action(self) -> None:
        """The front end asked to save the current look as the Default, or
        to make the Default the current look."""
        action = self._settings.get("default-action")
        if not action:
            return
        if action == "save":
            look = {key: self._settings.get(key) for key in DEFAULT_KEYS}
            self._settings.set("default-look", json.dumps(look, sort_keys=True))
            log.info("Saved the current look as the Default")
        elif action == "load":
            for key, value in parse_default(self._settings.get("default-look")).items():
                self._settings.set(key, value)
            log.info("The Default look is now the current look")
        else:
            log.warning("Unknown default-action %r ignored", action)
        self._settings.set("default-action", "")
        self.publish_status()

    # --- auto mode -------------------------------------------------------------

    @property
    def auto_wanted(self) -> bool:
        """Does any device follow what is in focus?"""
        return any(self._settings.get("auto-devices").values())

    def _refresh_look(self) -> None:
        """Work out what auto mode wants shown right now.

        An app (or game) with a usable icon gets its own look. Nothing in
        focus, or something without an icon, gets the idle look: the
        system's idle picture (the wallpaper, the menu theme, the OS logo)
        and its colour, unless the owner chose a picture or colour. Apps on
        the ignore list leave the owner's normal settings showing.
        """
        self.look = None
        if not (self._looks and self.auto_wanted):
            return
        subject = self._settings.get("focused-app")
        if self._looks.is_ignored(subject):
            return
        self.look = self._looks.look_for(subject) or self._idle_look()
        log.debug(
            "Auto mode: in focus %r -> colour %s, %s picture",
            subject,
            self.look.colour if self.look else None,
            "a" if self.look else "no",
        )

    def _idle_look(self) -> Look | None:
        idle = self._looks.idle_look(self._settings.get("auto-idle-image"))
        colour = self._settings.get("auto-idle-colour")
        if idle and colour:
            idle = dataclasses.replace(idle, colour=colour)  # keeps an animation's frames
        return idle

    def look_again(self) -> None:
        """Something the looks are made from changed (the desktop's theme)."""
        if self._looks:
            self._looks.forget_idle()
        self.on_changed("auto-idle-image")

    # --- is anyone reporting what is in focus? ----------------------------------

    def _watch_for_focus_reports(self) -> None:
        """Start the wait for a first focus report, if one is due.

        Auto mode silently does nothing when the front end that reports the
        focused app is not running (an extension GNOME never switched on);
        this makes that show up in the log.
        """
        if (
            self._focus_report_wait_s is None
            or self._focus_reported
            or self._focus_watch is not None
            or not self._started
            or not self.auto_wanted
        ):
            return
        self._focus_watch = self.scheduler.every(
            round(self._focus_report_wait_s * 1000), self._focus_report_overdue
        )

    def _stop_focus_watch(self) -> None:
        if self._focus_watch is not None:
            self.scheduler.cancel(self._focus_watch)
            self._focus_watch = None

    def _focus_report_overdue(self) -> bool:
        self._focus_watch = None
        if self._started and not self._focus_reported and self.auto_wanted:
            log.warning(
                "Auto mode is on, but in %.0f s nothing has reported which app is in focus, "
                "so auto mode cannot follow it. %s",
                self._focus_report_wait_s,
                self._focus_report_hint,
            )
        return False  # once is enough

    # --- sleep and wake ------------------------------------------------------

    def on_sleep(self) -> None:
        """The computer is about to sleep: switch the LCD and all lights off.

        There are only a few seconds before it sleeps, so nothing fades.
        """
        log.info("Going to sleep; switching every device off")
        self.asleep = True
        self._asleep_since = self._clock()
        for device in self.devices:
            self._each(device.on_sleep)

    def on_resume(self) -> None:
        """The computer woke from sleep: switch everything back on."""
        log.info("Woke from sleep; restoring every device")
        self.asleep = False
        self._slept_seen = self._slept()
        for device in self.devices:
            self._each(device.on_resume)

    def _notice_missed_wake(self) -> None:
        """Recover when nobody told us about a sleep, or about its end.

        The "woke up" message can get lost (a hook that did not run, a crash
        of whatever sends it), and some ways of sleeping send no messages at
        all. Either would leave the lights dark, or the RAM showing whatever
        the BIOS put back, until a restart. So we also look for ourselves.
        """
        slept = self._slept()
        if slept - self._slept_seen > SLEPT_JUMP_S:
            log.info("The computer slept %.0f s unannounced; restoring", slept - self._slept_seen)
            self.on_resume()
        elif self.asleep and self._clock() - self._asleep_since > SLEEP_NEVER_CAME_S:
            log.info("Told the computer was going to sleep, but it never did; lights back on")
            self.on_resume()

    # --- housekeeping --------------------------------------------------------

    def tick_slow(self) -> None:
        """Every few seconds: reconnect whatever went away, refresh status."""
        self._notice_missed_wake()
        for device in self.devices:
            self._each(device.tick_slow)
        self.publish_status()

    def _followed_app(self) -> str:
        """The app whose look is showing; empty for the idle look or none."""
        subject = self._settings.get("focused-app")
        if self.look and self._looks and self._looks.look_for(subject) == self.look:
            return subject
        return ""

    def publish_status(self) -> None:
        """Tell the settings window what we found. Only writes on change."""
        devices = []
        for device in self.devices:
            try:
                report = device.status()
            except Exception:
                log.exception("Could not describe %s", device.id)
                report = {"connected": False}
            devices.append({"id": device.id, "label": device.label, **report})
        status = json.dumps(
            {
                "auto": {
                    "app": self._followed_app(),
                    "colour": (self.look.colour or "") if self.look else "",
                },
                "has-default": bool(parse_default(self._settings.get("default-look"))),
                "devices": devices,
            }
        )
        if status != self._settings.get("status"):
            self._settings.set("status", status)
