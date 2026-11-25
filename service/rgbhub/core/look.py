# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto mode: what the lights and LCD should look like for whatever is in
focus (an app on a desktop, a game on a console-style system).

Given a picture of the thing in focus (its icon or artwork), this picks the
picture's most striking colour for the lights and builds an LCD image of
it. Finding the picture is the platform's job.
"""

import colorsys
import io
import logging
import math
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PIL import Image, ImageOps

from .constants import LCD_SIZE
from .image_prep import encode_for_lcd

log = logging.getLogger(__name__)

ICON_SIZE_ON_LCD = 300  # the screen is round, so the icon stays well inside it

# --- choosing a colour (change these to taste) ---------------------------------
SAMPLE_SIZE = 48  # about this many pixels per side are looked at
HUE_BINS = 24  # colours are grouped into this many slices of the colour wheel
MIN_SATURATION = 0.25  # paler than this counts as grey/white
MIN_VALUE = 0.20  # darker than this counts as black
MIN_COLOURFUL_SHARE = 0.01  # ignore icons with only a speck of colour
LED_SATURATION = 0.75  # the chosen colour is made at least this vivid


@dataclass(frozen=True)
class Look:
    """How one app should be shown."""

    colour: str | None  # '#RRGGBB' for the lights; None if the picture has no real colour
    jpeg: bytes  # the picture, ready for the LCD (an animation's first frame)
    frames: tuple[bytes, ...] = ()  # every frame of an animated picture, LCD-ready
    frame_ms: int = 0  # how long each frame is shown


@dataclass(frozen=True)
class Animation:
    """A moving picture a platform can hand over in place of a still one."""

    frames: tuple[bytes, ...]  # LCD-ready JPEGs (480x480), in order, played in a loop
    frame_ms: int


def dominant_colour(icon: Image.Image) -> str | None:
    """The most striking colour of an icon as '#RRGGBB', vivid enough for
    LEDs, or None if the icon is essentially grey, black, white or empty."""
    # Look at a grid of about SAMPLE_SIZE x SAMPLE_SIZE real pixels. (Shrinking
    # the icon instead would invent in-between colours along every edge.)
    rgba = icon.convert("RGBA")
    raw, width, height = rgba.tobytes(), rgba.width, rgba.height
    step_x, step_y = max(1, width // SAMPLE_SIZE), max(1, height // SAMPLE_SIZE)
    pixels = (
        raw[offset : offset + 4]
        for y in range(0, height, step_y)
        for offset in range(y * width * 4, (y + 1) * width * 4, step_x * 4)
    )
    weight = [0.0] * HUE_BINS  # how much of each slice of the colour wheel
    hue_x, hue_y, saturation = [0.0] * HUE_BINS, [0.0] * HUE_BINS, [0.0] * HUE_BINS
    opaque = colourful = 0
    for red, green, blue, alpha in pixels:
        if alpha < 128:
            continue
        opaque += 1
        h, s, v = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if s < MIN_SATURATION or v < MIN_VALUE:
            continue
        colourful += 1
        slot = int(h * HUE_BINS) % HUE_BINS
        strength = s * v  # vivid, bright pixels count for more
        weight[slot] += strength
        hue_x[slot] += math.cos(2 * math.pi * h) * strength
        hue_y[slot] += math.sin(2 * math.pi * h) * strength
        saturation[slot] += s * strength
    if not opaque or colourful < max(1, opaque * MIN_COLOURFUL_SHARE):
        return None
    best = max(range(HUE_BINS), key=weight.__getitem__)
    hue = (math.atan2(hue_y[best], hue_x[best]) / (2 * math.pi)) % 1.0
    vivid = max(saturation[best] / weight[best], LED_SATURATION)
    red, green, blue = (round(255 * part) for part in colorsys.hsv_to_rgb(hue, vivid, 1.0))
    return f"#{red:02X}{green:02X}{blue:02X}"


def icon_on_black(icon: Image.Image) -> bytes:
    """An LCD-ready JPEG of the icon, centred on black."""
    icon = icon.convert("RGBA")
    scale = ICON_SIZE_ON_LCD / max(icon.size)
    size = (max(1, round(icon.width * scale)), max(1, round(icon.height * scale)))
    icon = icon.resize(size, Image.LANCZOS)
    screen = Image.new("RGB", (LCD_SIZE, LCD_SIZE), (0, 0, 0))
    screen.paste(icon, ((LCD_SIZE - size[0]) // 2, (LCD_SIZE - size[1]) // 2), icon)
    return encode_for_lcd(screen)


def cover_lcd(picture: Image.Image) -> bytes:
    """An LCD-ready JPEG of the picture's centre, filling the whole screen."""
    return encode_for_lcd(ImageOps.fit(picture.convert("RGB"), (LCD_SIZE, LCD_SIZE)))


def _idle_look_from(picture, compose: Callable[[Image.Image], bytes]) -> Look | None:
    """A Look from whatever a platform supplied as its idle picture."""
    if picture is None:
        return None
    if isinstance(picture, Animation):
        if not picture.frames:
            return None
        with Image.open(io.BytesIO(picture.frames[0])) as first:
            colour = dominant_colour(first)
        return Look(colour, picture.frames[0], tuple(picture.frames), max(20, picture.frame_ms))
    return Look(dominant_colour(picture), compose(picture))


class Looks:
    """Works out, and remembers, the Look of each app."""

    def __init__(
        self,
        load_icon: Callable[[str], Image.Image | None],
        remember: int = 64,
        ignore: Iterable[str] = (),
        load_idle: Callable[[], "Image.Image | Animation | None"] | None = None,
        compose: Callable[[Image.Image], bytes] = icon_on_black,
        compose_idle: Callable[[Image.Image], bytes] = icon_on_black,
        version: Callable[[str], object] | None = None,
    ):
        """`load_icon(app_id)` returns the app's icon, or None if it has none.
        Apps listed in `ignore` never get a look. `load_idle()` returns the
        picture for when nothing is in focus: a still image or an Animation.
        `version(app_id)` is cheap to ask and changes when the app's icon would
        (another icon theme, new artwork for a game): a remembered look is then
        worked out again instead of being shown for as long as the service runs."""
        self._load_icon = load_icon
        self._ignore = frozenset(ignore)
        self._load_idle = load_idle
        self._compose = compose  # lays a subject's picture out for the LCD
        self._compose_idle = compose_idle  # the same for the idle picture
        self._idle: dict[str, Look | None] = {}  # idle looks by picture path ('' = load_idle's)
        self._remember = remember
        self._version = version
        self._known: OrderedDict[str, tuple[object, Look | None]] = OrderedDict()

    def is_ignored(self, app_id: str) -> bool:
        """True for apps during which the owner's normal settings stay on."""
        return app_id in self._ignore

    def idle_look(self, image_path: str = "") -> Look | None:
        """The Look for when nothing is in focus: `image_path` if given,
        otherwise the platform's own idle picture (load_idle: the wallpaper,
        the menu theme, the OS logo). None if there is no usable picture."""
        if image_path not in self._idle:
            try:
                if image_path:
                    with Image.open(image_path) as opened:
                        picture = opened.convert("RGBA")
                else:
                    picture = self._load_idle() if self._load_idle else None
                look = _idle_look_from(picture, self._compose_idle)
            except Exception as error:
                log.warning("No idle look (%s): %s", image_path or "the system's picture", error)
                look = None
            self._idle[image_path] = look
        return self._idle[image_path]

    def forget_idle(self) -> None:
        """Build the idle look afresh next time (the theme has changed)."""
        self._idle.clear()

    def look_for(self, app_id: str) -> Look | None:
        """The Look for an app id such as 'firefox.desktop'; None if unknown."""
        if not app_id or app_id in self._ignore:
            return None
        try:
            version = self._version(app_id) if self._version else None
        except Exception as error:
            log.warning("Cannot tell whether the look of %s is current: %s", app_id, error)
            version = None
        if app_id in self._known and self._known[app_id][0] == version:
            self._known.move_to_end(app_id)
            return self._known[app_id][1]
        try:
            icon = self._load_icon(app_id)
            look = Look(dominant_colour(icon), self._compose(icon)) if icon else None
        except Exception as error:  # icon themes and image files can fail in many ways
            log.warning("No look for %s: %s", app_id, error)
            look = None
        self._known[app_id] = (version, look)
        self._known.move_to_end(app_id)
        while len(self._known) > self._remember:
            self._known.popitem(last=False)
        return look
