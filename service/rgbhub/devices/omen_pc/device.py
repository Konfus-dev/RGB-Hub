# SPDX-License-Identifier: GPL-3.0-or-later
"""The HP OMEN 45L as one hub device: cooler LCD, case lights and RAM.

The LCD keeps the chosen picture (or auto mode's) on screen, with
crossfades and moving pictures. The case lights and RAM are a LightGroup.
Settings are read through the hub (hub.get), so the Default look can take
over when the hub stops.
"""

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

from ...core.interfaces import WatchFile
from ...core.light_group import LightGroup
from ...core.lights import Lights
from . import lcd_constants as c
from .image_prep import prepare_jpeg
from .lcd_streamer import LcdStreamer
from .ram import RamLights

log = logging.getLogger(__name__)

DEVICE_ID = "OMEN-PC"
LCD_IMAGE_KEYS = ("lcd-image", "lcd-fit")
LCD_BRIGHTNESS_KEYS = ("lcd-enabled", "lcd-brightness")
LCD_FADE_STEPS = 8  # frames in a crossfade between two LCD pictures
LCD_FADE_INTERVAL_MS = 45  # time between those frames (about 0.36 s in all)


class OmenPc:
    id = DEVICE_ID
    label = "OMEN-PC"

    def __init__(
        self,
        streamer: LcdStreamer,
        lights: Lights,
        prepare: Callable[[Path, str], bytes] = prepare_jpeg,
        ram: RamLights | None = None,
        display_names: dict[str, str] | None = None,
        fade: Callable[[bytes | None, bytes | None, int], Iterator[bytes]] | None = None,
        watch_file: WatchFile | None = None,
    ):
        """streamer    keeps a picture on the LCD
        lights      the case lights
        ram         the RAM lights, or None where they cannot be controlled
        prepare     turns a picture file into an LCD-ready JPEG
        fade        makes LCD crossfade frames, or None to switch at once
        watch_file  tells us when a file changes, or None not to watch
        display_names  friendlier names for devices and zones
        """
        self._streamer = streamer
        self._lights = lights
        self._ram = ram  # None on machines without controllable RAM lights
        self._prepare = prepare
        self._fade = fade  # makes crossfade frames; None switches pictures at once
        self._watch_file = watch_file
        self._display_names = display_names or {}
        self._hub = None
        self.lights: LightGroup | None = None
        self._own_jpeg: bytes | None = None  # the owner's chosen picture, LCD-ready
        self._shown_jpeg: bytes | None = None  # what the LCD was last told to show
        self._target_jpeg: bytes | None = None  # the picture it should end up showing
        self._fade_iter: Iterator[bytes] | None = None  # frames of a running fade
        self._fade_timer = None
        self._animation_index = 0  # which frame of an animated LCD picture is showing
        self._lcd_animation_timer = None
        self._stop_watching = None  # stops watching the chosen LCD image file
        self._watched_path: str | None = None
        self._error = ""  # last image problem, shown in the settings window
        self._timers: list = []

    def attach(self, hub) -> None:
        self._hub = hub
        parts = [self._lights] + ([self._ram] if self._ram else [])
        self.lights = LightGroup(
            hub,
            DEVICE_ID,
            parts,
            self._auto_colour,
            self._display_names,
            slow=(self._ram,) if self._ram else (),
        )

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._load_own_image()
        self._show_lcd_image()
        self._update_lcd_brightness()
        self._streamer.tick_reconnect()
        self.lights.start()
        every, repeat = self._hub.scheduler.every, self._hub.repeat
        self._timers = [
            every(c.CLAIM_INTERVAL_MS, lambda: repeat(self._streamer.tick_claim)),
            every(c.FRAME_INTERVAL_MS, lambda: repeat(self._streamer.tick_frame)),
        ]

    def stop(self) -> None:
        for timer in (*self._timers, self._fade_timer, self._lcd_animation_timer):
            if timer is not None:
                self._hub.scheduler.cancel(timer)
        self._timers = []
        self._fade_timer = self._lcd_animation_timer = None
        self.lights.stop()
        self._watch_image_file(None)

    def show_default(self) -> None:
        """The LCD forgets its picture as soon as we stop sending it, so
        only the lights matter here."""
        self.lights.finish_now()
        self.lights.keep()

    # --- reacting --------------------------------------------------------------

    def on_changed(self, key: str) -> None:
        if key in LCD_IMAGE_KEYS:
            self._load_own_image()
            self._show_lcd_image()
            self._hub.publish_status()
        elif key in LCD_BRIGHTNESS_KEYS:
            self._update_lcd_brightness()
        else:
            self.lights.on_changed(key)

    def on_look_changed(self) -> None:
        self._show_lcd_image()
        self.lights.on_look_changed()

    def on_sleep(self) -> None:
        self.finish_lcd_fade()
        self._sync_lcd_animation()
        self._update_lcd_brightness()
        self._streamer.tick_claim()  # sends the new brightness now
        self.lights.on_sleep()

    def on_resume(self) -> None:
        self._sync_lcd_animation()
        self._update_lcd_brightness()
        self._streamer.tick_claim()
        self.lights.on_resume()

    def tick_slow(self) -> None:
        self._streamer.tick_reconnect()
        self.lights.tick_slow()

    def status(self) -> dict:
        case, *ram = self.lights.connected
        return {
            "connected": bool(self._streamer.connected or case or any(ram)),
            "lcd": self._streamer.connected,
            "case": case,
            "ram": any(ram),
            "error": self._error,
            "lights": self.lights.describe(),
        }

    # --- auto mode -------------------------------------------------------------

    def _auto_lcd(self) -> bytes | None:
        """The picture auto mode wants on the LCD, if any."""
        hub = self._hub
        if hub.auto_for(DEVICE_ID) and hub.get("auto-lcd"):
            return hub.look.jpeg
        return None

    def _auto_colour(self) -> str | None:
        """The colour auto mode wants on the lights, if any."""
        hub = self._hub
        if hub.auto_for(DEVICE_ID) and hub.get("auto-lights"):
            return hub.look.colour
        return None

    # --- LCD ---------------------------------------------------------------------

    def _watch_image_file(self, path: str | None) -> None:
        """Notice when the chosen image file is edited or replaced."""
        if path == self._watched_path:
            return
        if self._stop_watching:
            self._stop_watching()
        self._stop_watching, self._watched_path = None, path
        if path and self._watch_file:
            self._stop_watching = self._watch_file(
                path, lambda: self._hub.repeat(self.on_image_file_changed)
            )

    def on_image_file_changed(self) -> None:
        """The chosen image was edited on disk: show the new version."""
        self._load_own_image()
        self._show_lcd_image()
        self._hub.publish_status()

    def _load_own_image(self) -> None:
        """Read the owner's chosen picture from disk and get it LCD-ready.

        Done only when the choice or the file changes, never on every app
        switch: decoding a large photo takes long enough to make the LCD and
        a breathing effect stutter.
        """
        path = self._hub.get("lcd-image")
        self._watch_image_file(path or None)
        if not path:
            self._own_jpeg, self._error = None, ""
            return
        try:
            self._own_jpeg = self._prepare(Path(path), self._hub.get("lcd-fit"))
            self._error = ""
        except Exception as error:
            # Normally an ImageError. Either way: keep the previous picture,
            # and never let a bad one stop the service.
            log.warning("%s", error)
            self._error = str(error)

    def _show_lcd_image(self) -> None:
        """Put the right picture on the LCD: auto mode's, else the owner's."""
        auto = self._auto_lcd()
        picture = auto if auto is not None else self._own_jpeg
        if picture == self._target_jpeg:
            return
        self._target_jpeg = picture
        if self._fade is None:
            self._shown_jpeg = picture
            self._streamer.set_image(picture)
            self._streamer.tick_frame()  # now, not at the next half-second tick
            self._sync_lcd_animation()
            return
        self._sync_lcd_animation()  # a moving picture stops while we fade away from it
        # Crossfade from whatever is on screen at this moment.
        self._fade_iter = self._fade(self._shown_jpeg, picture, LCD_FADE_STEPS)
        self.tick_lcd_fade()
        if self._fade_iter is not None and self._fade_timer is None:
            self._fade_timer = self._hub.scheduler.every(LCD_FADE_INTERVAL_MS, self._on_fade_timer)

    @property
    def lcd_fading(self) -> bool:
        return self._fade_iter is not None

    def _on_fade_timer(self) -> bool:
        self._hub.repeat(self.tick_lcd_fade)
        if self._fade_iter is None:
            self._fade_timer = None
            return False  # fade finished: stop the timer
        return True

    def tick_lcd_fade(self) -> None:
        """Show the next frame of a running crossfade."""
        if self._fade_iter is None:
            return
        try:
            frame = next(self._fade_iter, None)
        except Exception as error:
            log.warning("LCD fade failed: %s", error)
            frame = None
        if frame is None:
            self._fade_iter = None
            if self._shown_jpeg != self._target_jpeg:
                # Either the fade failed, or there is no picture any more
                # (None lets the LCD go back to its own logo).
                self._shown_jpeg = self._target_jpeg
                self._streamer.set_image(self._target_jpeg)
            self._sync_lcd_animation()  # the new picture may be a moving one
            return
        self._shown_jpeg = frame
        self._streamer.set_image(frame)
        self._streamer.tick_frame()

    def finish_lcd_fade(self) -> None:
        """Run a crossfade straight to its end."""
        while self._fade_iter is not None:
            self.tick_lcd_fade()

    # --- moving LCD pictures -------------------------------------------------------

    @property
    def lcd_animating(self) -> bool:
        return self._lcd_animation_timer is not None

    def _animation_wanted(self) -> bool:
        """True while auto mode's picture is a moving one and is on the LCD."""
        look = self._hub.look
        return bool(
            look
            and look.frames
            and not self._hub.asleep
            and self._fade_iter is None
            and self._auto_lcd() is not None
            and self._target_jpeg == look.jpeg
        )

    def _sync_lcd_animation(self) -> None:
        """Start or stop playing a moving picture, as the situation requires."""
        wanted = self._animation_wanted()
        if wanted and self._lcd_animation_timer is None:
            self._animation_index = 0
            self._lcd_animation_timer = self._hub.scheduler.every(
                self._hub.look.frame_ms, lambda: self._hub.repeat(self.tick_lcd_animation)
            )
        elif not wanted and self._lcd_animation_timer is not None:
            self._hub.scheduler.cancel(self._lcd_animation_timer)
            self._lcd_animation_timer = None

    def tick_lcd_animation(self) -> None:
        """Show the next frame of a moving picture."""
        if self._lcd_animation_timer is None or not self._animation_wanted():
            return
        frames = self._hub.look.frames
        self._animation_index = (self._animation_index + 1) % len(frames)
        self._shown_jpeg = frames[self._animation_index]
        self._streamer.set_image(self._shown_jpeg)
        self._streamer.tick_frame()

    def _update_lcd_brightness(self) -> None:
        enabled = self._hub.get("lcd-enabled") and not self._hub.asleep
        self._streamer.set_brightness(self._hub.get("lcd-brightness") if enabled else 0)
