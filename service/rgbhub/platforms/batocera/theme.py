# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the look of Batocera's menu from its active theme.

What goes on the LCD while no game is running:

1. Whatever the theme puts in its `rgb-hub/` folder: an animation or a
   still picture (see core/lcd_folder.py for what the folder may hold).
2. Otherwise the picture behind the menu's system list.

So any theme works, and a theme author can decide exactly what is shown.
"""

import logging
import xml.etree.ElementTree as ElementTree
from pathlib import Path

log = logging.getLogger(__name__)

ES_SETTINGS = Path("/userdata/system/configs/emulationstation/es_settings.cfg")
THEME_FOLDERS = (Path("/userdata/themes"), Path("/usr/share/emulationstation/themes"))
MAX_INCLUDE_DEPTH = 4  # themes split over several files include one another

# The folder a theme uses to say what the LCD shows. "omen-lights" is the
# name from before the project became RGB-Hub; themes made then still work.
LCD_FOLDERS = ("rgb-hub", "omen-lights")


def lcd_folder(es_settings: Path = ES_SETTINGS, theme_folders=THEME_FOLDERS) -> Path | None:
    """The active theme's LCD folder (see core/lcd_folder.py); it need not exist."""
    theme = active_theme(es_settings, theme_folders)
    if not theme:
        return None
    for name in LCD_FOLDERS:
        if (theme / name).is_dir():
            return theme / name
    return theme / LCD_FOLDERS[0]


def active_theme(es_settings: Path = ES_SETTINGS, theme_folders=THEME_FOLDERS) -> Path | None:
    """The folder of the theme chosen in Batocera's menu, or None."""
    try:
        root = ElementTree.parse(es_settings).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    name = next((e.get("value") for e in root.iter("string") if e.get("name") == "ThemeSet"), None)
    if not name:
        return None
    for folder in theme_folders:
        if (Path(folder) / name / "theme.xml").is_file():
            return Path(folder) / name
    return None


def theme_background(es_settings: Path = ES_SETTINGS, theme_folders=THEME_FOLDERS) -> Path | None:
    """The active theme's menu background picture, or None.

    The background of the "system" view (the list of consoles) is preferred;
    failing that, any view's background.
    """
    theme = active_theme(es_settings, theme_folders)
    if theme is None:
        return None
    found = _backgrounds(theme / "theme.xml", theme, MAX_INCLUDE_DEPTH)
    for wanted_system_view in (True, False):
        for is_system_view, picture in found:
            if is_system_view == wanted_system_view:
                return picture
    return None


def _backgrounds(xml_file: Path, theme: Path, depth: int) -> list[tuple[bool, Path]]:
    """(is it the system view?, picture) for every usable background image."""
    try:
        root = ElementTree.parse(xml_file).getroot()
    except (OSError, ElementTree.ParseError) as error:
        log.debug("Cannot read theme file %s: %s", xml_file, error)
        return []
    found = []
    for view in root.iter("view"):
        views = [name.strip() for name in view.get("name", "").split(",")]
        for image in view.iter("image"):
            if image.get("name") != "background":
                continue
            picture = _resolve(image.findtext("path", ""), xml_file.parent, theme)
            if picture:
                found.append(("system" in views, picture))
    if depth > 0:
        for include in root.iter("include"):
            included = _resolve(include.text or "", xml_file.parent, theme)
            if included:
                found.extend(_backgrounds(included, theme, depth - 1))
    return found


def _resolve(text: str, folder: Path, theme: Path) -> Path | None:
    """A path written in a theme file as a real, existing file, or None."""
    text = text.strip().replace("${themePath}", str(theme))
    if not text or "${" in text:  # depends on something only the menu knows
        return None
    path = Path(text)
    if not path.is_absolute():
        path = folder / path
    return path if path.is_file() else None
