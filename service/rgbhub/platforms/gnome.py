# SPDX-License-Identifier: GPL-3.0-or-later
"""The hub on a GNOME desktop (developed on Ubuntu).

Devices: the OMEN PC (cooler LCD, case lights, RAM) and the IQUNIX keyboard.

What this platform chooses:
- settings:     GSettings, shared with the GNOME Shell extension
                (frontends/gnome-shell), which is the front end
- case lights:  an OpenRGB server
- RAM lights:   the root helper, which asks the BIOS
- keyboard:     devices/iqunix_keyboard/via.py (a udev rule lets the owner open it)
- what is in focus: the app the extension reports; its icon comes from the
                icon theme. With nothing in focus the LCD shows what is in
                ~/.config/rgbhub/lcd/ if anything, else the wallpaper, and
                the lights take that picture's main colour
- sleep/wake:   systemd-logind
"""

import argparse
import logging
from pathlib import Path

from gi.repository import GLib

from ..backends import openrgb
from ..core.hub import Hub
from ..core.lcd_folder import load_lcd_folder
from ..core.lights import Lights
from ..core.look import Looks, cover_lcd
from ..devices.iqunix_keyboard import IqunixKeyboard, mg65, via
from ..devices.omen_pc import OmenPc
from ..devices.omen_pc import model as omen_model
from ..devices.omen_pc.lcd_fade import fade_frames
from ..devices.omen_pc.lcd_streamer import LcdStreamer, lcd_opener
from ..devices.omen_pc.ram import RamLights
from . import glib_runtime, gnome_migrate, gnome_theme, gtk_icons, logind
from .gsettings_store import GSettingsStore, load_settings
from .linux.hidraw import HidrawBus
from .linux.ram import HelperRamBus

log = logging.getLogger(__name__)

EXTENSION_UUID = "rgbhub@konfus-dev.github.io"

# The extension reports the focused app as soon as it is loaded and auto
# mode is on. Silence for this long after start means it is most likely not
# loaded (it was left off twice: after the first install and after a rename).
FOCUS_REPORT_WAIT_S = 180
FOCUS_REPORT_HINT = (
    f"Is the GNOME Shell extension switched on? Check with: gnome-extensions info "
    f"{EXTENSION_UUID} (State should be ACTIVE); if not: gnome-extensions enable "
    f"{EXTENSION_UUID}, or run install/install-user.sh again, then log out and back in."
)

# Auto mode leaves the normal look alone while one of these apps has focus.
# The first is the window GNOME shows this project's settings in, so that
# you see the colours you are choosing while you choose them.
AUTO_IGNORED_APPS = ("org.gnome.Shell.Extensions.desktop", "org.gnome.Extensions.desktop")


def default_schema_dir() -> Path:
    extensions = Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions"
    return extensions / EXTENSION_UUID / "schemas"


def default_lcd_folder() -> Path:
    """Where the owner can put an animation or picture for the LCD to show
    when nothing is in focus (see core/lcd_folder.py)."""
    return Path(GLib.get_user_config_dir()) / "rgbhub" / "lcd"


def make_looks(theme, lcd_folder: Path) -> Looks:
    """Auto mode's looks: app icons from the icon theme the desktop names
    now, and the idle picture (the LCD folder, else the wallpaper, else the
    OS logo). `theme` is a gnome_theme.DesktopTheme."""

    def idle_picture():
        """What the owner put in the LCD folder; else the wallpaper; else
        the OS logo."""
        override = load_lcd_folder(lcd_folder, cover_lcd)
        if override is not None:
            return override
        wallpaper = theme.wallpaper()
        if wallpaper:
            try:
                return gtk_icons.draw_image_file(wallpaper)
            except Exception as error:  # GLib raises its own error types
                log.warning("Cannot use the wallpaper %s: %s", wallpaper, error)
        return gtk_icons.load_os_logo()

    return Looks(
        gtk_icons.load_app_icon,
        ignore=AUTO_IGNORED_APPS,
        # Another icon theme: other icons, other colours. Asked at each look,
        # never fixed at start (the service starts before the session's theme
        # is known, and app colours came from the wrong icons).
        version=lambda _app: gtk_icons.icon_theme_name(),
        load_idle=idle_picture,
        compose_idle=cover_lcd,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", type=Path, default=default_schema_dir())
    parser.add_argument("--lcd-folder", type=Path, default=default_lcd_folder())
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = GSettingsStore(load_settings(args.schema_dir))
        gnome_migrate.migrate_once(settings)
    except FileNotFoundError as error:
        log.error("%s", error)
        return 1

    theme = gnome_theme.DesktopTheme()
    looks = make_looks(theme, args.lcd_folder)
    bus = HidrawBus()
    hub = Hub(
        settings,
        [
            OmenPc(
                LcdStreamer(lcd_opener(bus)),
                Lights(openrgb.connect, omen_model.HIDDEN_ZONES, omen_model.LED_GAMMA),
                ram=RamLights(
                    HelperRamBus(),
                    gamma=omen_model.LED_GAMMA,
                    background=True,
                ),
                display_names=omen_model.DISPLAY_NAMES,
                fade=fade_frames,
                watch_file=glib_runtime.watch_file,
            ),
            IqunixKeyboard(
                Lights(via.opener(mg65.MG65, bus), name="Keyboard lights"),
                display_names=mg65.DISPLAY_NAMES,
            ),
        ],
        looks=looks,
        scheduler=glib_runtime.GLibScheduler(),
        focus_report_wait_s=FOCUS_REPORT_WAIT_S,
        focus_report_hint=FOCUS_REPORT_HINT,
    )
    hub.start()

    theme.on_change(hub.look_again)
    # Kept in a variable until we exit: the notifications stop once nothing
    # refers to the connection any more.
    sleep_watch = logind.watch_for_sleep(hub.on_sleep, hub.on_resume)
    log.info("RGB-Hub running (GNOME)")
    glib_runtime.run_until_stopped()
    del sleep_watch
    hub.stop()  # leaves the Default look on every device
    return 0
