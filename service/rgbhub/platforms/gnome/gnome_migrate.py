# SPDX-License-Identifier: GPL-3.0-or-later
"""Carry the settings of an OmenRGB-Controller install over to RGB-Hub, once.

The old extension kept its settings under /org/gnome/shell/extensions/omenrgb/
in dconf, and its files in ~/.config/omenrgb/. Both are read with the
standard tools (dconf, a folder rename) the first time RGB-Hub runs; the
old settings are left where they are.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

from gi.repository import GLib

from ...core.settings_schema import DEFAULT_KEYS, DEFAULTS, migrate

log = logging.getLogger(__name__)

OLD_PATH = "/org/gnome/shell/extensions/omenrgb/"
NEW_PATH = "/org/gnome/shell/extensions/rgbhub/"
MARKER = "default-look"  # never empty once RGB-Hub has run with old settings


def parse_dump(text: str) -> dict:
    """`dconf dump` output of one folder as {key: Python value}."""
    values = {}
    for line in text.splitlines():
        key, equals, value = line.partition("=")
        if not equals or key.startswith("["):
            continue
        try:
            values[key.strip()] = GLib.Variant.parse(None, value.strip(), None, None).unpack()
        except GLib.Error:
            log.warning("Old setting %s could not be read; skipped", key)
    return values


def _dump(path: str) -> str:
    try:
        return subprocess.run(
            ["dconf", "dump", path], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def migrate_once(settings, config_dir: Path | None = None) -> None:
    """Copy old settings into `settings` if RGB-Hub has none of its own yet."""
    config = config_dir or Path(GLib.get_user_config_dir())
    old_folder, new_folder = config / "omenrgb", config / "rgbhub"
    if old_folder.is_dir() and not new_folder.exists():
        shutil.move(old_folder, new_folder)
        log.info("Moved %s to %s", old_folder, new_folder)

    if parse_dump(_dump(NEW_PATH)).get(MARKER) not in (None, "{}"):
        return
    old = parse_dump(_dump(OLD_PATH))
    if not old:
        return
    new = migrate(old)
    for key, value in new.items():
        if key in DEFAULTS and key not in ("status", "focused-app"):
            settings.set(key, value)
    # The look the owner had made is the obvious first Default.
    default = {key: settings.get(key) for key in DEFAULT_KEYS}
    settings.set(MARKER, json.dumps(default, sort_keys=True))
    log.info("Took over %d settings from OmenRGB-Controller", len(new))
