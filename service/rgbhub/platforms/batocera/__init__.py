# SPDX-License-Identifier: GPL-3.0-or-later
"""The hub on Batocera (a console-style system; everything runs as root).

Devices: the OMEN PC (cooler LCD, case lights, RAM) and the IQUNIX keyboard.

What this platform chooses:
- settings:     a JSON file the owner can edit,
                /userdata/system/configs/rgb-hub/settings.json
- front end:    the "RGB_HUB" switch under System Settings > Services,
                plus a game hook (frontends/batocera) that writes the path
                of the running game to a small file
- case lights:  our own driver (devices/omen_pc/case_board.py); Batocera has no OpenRGB
- keyboard:     devices/iqunix_keyboard/via.py, as on every system
- RAM lights:   straight through the BIOS, if the acpi_call kernel module
                is available; otherwise the RAM is left alone
- what is in focus: the running game. Its picture is the artwork Batocera
                scraped for it; with no game running, the background of the
                active menu theme (or the Batocera logo if it has none).
- sleep/wake:   Batocera runs the scripts in frontends/batocera/power
                before sleeping and after waking; they signal this process
                (SIGUSR1 = going to sleep, SIGUSR2 = woke up)
"""

import argparse
import importlib.util
import json
import logging
import signal
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from ...core.hub import Hub
from ...core.json_store import JsonSettings
from ...core.lcd_folder import load_lcd_folder
from ...core.lights import Lights
from ...core.look import Looks, cover_lcd
from ...core.settings_schema import DEFAULT_KEYS, DEVICE_IDS, migrate
from ...devices.iqunix_keyboard import IqunixKeyboard, mg65, via
from ...devices.omen_pc import OmenPc, case_board
from ...devices.omen_pc import model as omen_model
from ...devices.omen_pc.lcd_fade import fade_frames
from ...devices.omen_pc.lcd_streamer import LcdStreamer, lcd_opener
from ...devices.omen_pc.ram import RamLights
from ..linux.glib_runtime import GLibRuntime, on_signal
from ..linux.hidraw import HidrawBus
from ..linux.ram import AcpiRamBus, find_sticks, find_sticks_dmi
from . import games as batocera_games
from . import theme as batocera_theme

log = logging.getLogger(__name__)

SETTINGS_FILE = Path("/userdata/system/configs/rgb-hub/settings.json")
OLD_CONFIG = Path("/userdata/system/configs/omen-lights")  # before RGB-Hub
SUBJECT_FILE = Path("/var/run/rgb-hub/now-playing")  # written by the game hook
POLL_MS = 1000  # how often the game hook's file and the settings are re-read
ASLEEP_MARKER = Path("/var/run/rgb-hub/asleep")  # the suspend script waits for this
OS_LOGO = Path("/usr/share/batocera/splash/boot-logo.png")
ACPI_CALL = Path("/proc/acpi/call")
# The helper sits beside the "service" folder, in the repo and on the drive.
RAM_HELPER = Path(__file__).resolve().parents[3] / "helper" / "rgbhub_ram_helper.py"

DEFAULTS = {
    # On a games machine, following the game is the point: on unless switched off.
    "auto-devices": {device: True for device in DEVICE_IDS},
    # The RAM's own default effect is a rainbow, which ignores colours and so
    # could never follow a game or the theme. Give it a plain colour instead.
    "device-modes": {"RAM": "Static"},
}


def migrate_old_config(old: Path, settings_file: Path) -> None:
    """Carry an OmenRGB-Controller install's folder (settings, pictures)
    over to RGB-Hub's, once. Paths inside the settings follow the move."""
    new = settings_file.parent
    if not old.is_dir() or new.exists():
        return
    old.rename(new)
    log.info("Moved %s to %s", old, new)
    try:
        text = settings_file.read_text()
    except OSError:
        return
    try:
        data = json.loads(text.replace(str(old) + "/", str(new) + "/"))
    except ValueError:
        log.warning("Old settings in %s could not be read; starting afresh", settings_file)
        return
    if isinstance(data, dict):
        new_settings = migrate(data)
        # The look the owner had made is the obvious first Default.
        default = {key: new_settings[key] for key in DEFAULT_KEYS if key in new_settings}
        new_settings["default-look"] = json.dumps(default, sort_keys=True)
        settings_file.write_text(json.dumps(new_settings, indent=4, sort_keys=True) + "\n")


def _load_picture(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        return opened.convert("RGBA")


def _game_artwork(roms: Path):
    def load(rom: str) -> Image.Image | None:
        artwork = batocera_games.find_artwork(rom, roms)
        return _load_picture(artwork) if artwork else None

    return load


def _idle_picture(logo: Path):
    """What to show in the menus, so the PC matches the menu theme: what the
    theme's own LCD folder asks for, else the picture behind its system
    list, else the Batocera logo."""
    folder = batocera_theme.lcd_folder()
    override = load_lcd_folder(folder, cover_lcd) if folder else None
    if override is not None:
        log.info("Menu look taken from %s", folder)
        return override
    background = batocera_theme.theme_background()
    log.info("Menu look taken from %s", background or f"{logo} (nothing usable in the theme)")
    return _load_picture(background or logo)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _find_sticks() -> list[int]:
    """The fitted RAM sticks. Batocera's kernel does not list them, so the
    BIOS's tables are asked; the kernel's list is still tried first."""
    return find_sticks() or find_sticks_dmi()


def _ram_lights() -> RamLights | None:
    """RAM lights if the BIOS can be reached from here, else None."""
    if not (ACPI_CALL.exists() and RAM_HELPER.exists()):
        log.info("RAM lights unavailable (no acpi_call module); leaving the RAM alone")
        return None
    spec = importlib.util.spec_from_file_location("rgbhub_ram_helper", RAM_HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return RamLights(
        AcpiRamBus(helper, _find_sticks),
        gamma=omen_model.LED_GAMMA,
        background=True,
    )


def subject_reader(path: Path, settings) -> Callable[[], None]:
    """A function that copies the game hook's file into "focused-app"."""

    def read() -> None:
        try:
            # A game's file name need not be valid text; never let one stop us.
            subject = path.read_text(errors="replace").strip()
        except OSError:
            subject = ""
        settings.set("focused-app", subject)

    return read


def power_handlers(hub, marker: Path) -> tuple[Callable[[], None], Callable[[], None]]:
    """What to do when told the system is going to sleep, and that it woke.

    Going to sleep ends by creating `marker`: the suspend script holds the
    system back until it appears, so the lights are really off first.
    """

    def sleep() -> None:
        try:
            hub.on_sleep()
        except Exception:
            log.exception("Could not switch everything off before sleep")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def wake() -> None:
        marker.unlink(missing_ok=True)
        try:
            hub.on_resume()
        except Exception:
            log.exception("Could not restore the lights after waking")

    return sleep, wake


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=SETTINGS_FILE)
    parser.add_argument("--subject-file", type=Path, default=SUBJECT_FILE)
    parser.add_argument("--roms", type=Path, default=batocera_games.ROMS)
    parser.add_argument("--logo", type=Path, default=OS_LOGO)
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.settings == SETTINGS_FILE:
        migrate_old_config(OLD_CONFIG, SETTINGS_FILE)
    settings = JsonSettings(args.settings, DEFAULTS)
    looks = Looks(
        _game_artwork(args.roms),
        # scraped anew: other artwork
        version=lambda rom: batocera_games.artwork_version(rom, args.roms),
        load_idle=lambda: _idle_picture(args.logo),
        compose_idle=cover_lcd,  # menu backgrounds and the logo both suit a full screen
    )
    runtime = GLibRuntime()
    bus = HidrawBus()
    hub = Hub(
        settings,
        [
            OmenPc(
                LcdStreamer(lcd_opener(bus)),
                Lights(
                    lambda: case_board.open_board(bus),
                    omen_model.HIDDEN_ZONES,
                    omen_model.LED_GAMMA,
                ),
                ram=_ram_lights(),
                display_names=omen_model.DISPLAY_NAMES,
                fade=fade_frames,
                watch_file=runtime.watch_file,
            ),
            IqunixKeyboard(
                Lights(via.opener(mg65.MG65, bus), name="Keyboard lights"),
                display_names=mg65.DISPLAY_NAMES,
            ),
        ],
        looks=looks,
        scheduler=runtime,
    )
    # Three ways to hear that a game started or stopped, because missing it
    # means the PC never changes: the hook signals us (SIGHUP), the file is
    # watched, and it is looked at once a second regardless.
    args.subject_file.parent.mkdir(parents=True, exist_ok=True)
    args.subject_file.touch()
    read_subject = subject_reader(args.subject_file, settings)
    read_subject()
    on_signal(signal.SIGHUP, read_subject)
    stop_following = runtime.watch_file(str(args.subject_file), read_subject)
    stop_watching = runtime.watch_file(str(args.settings), settings.reload)

    theme_seen = [_mtime(batocera_theme.ES_SETTINGS)]

    def look_again() -> bool:
        try:
            read_subject()
            settings.reload()  # picks up hand edits even if file watching fails
            if (now := _mtime(batocera_theme.ES_SETTINGS)) != theme_seen[0]:
                theme_seen[0] = now  # the menu's settings changed: maybe a new theme
                hub.look_again()
        except Exception:
            # One bad look must not end the looking: a timer that raises is dropped.
            log.exception("Unexpected error while checking for changes; carrying on")
        return True

    # Before anything slow: until these are in place, a "going to sleep"
    # signal would end the program instead of being handled.
    going_to_sleep, woke_up = power_handlers(hub, ASLEEP_MARKER)
    ASLEEP_MARKER.unlink(missing_ok=True)
    on_signal(signal.SIGUSR1, going_to_sleep)
    on_signal(signal.SIGUSR2, woke_up)
    runtime.every(POLL_MS, look_again)
    hub.start()
    log.info("RGB-Hub running (Batocera)")
    runtime.run_until_stopped()
    stop_following()
    stop_watching()
    hub.stop()  # leaves the Default look on every device
    return 0
