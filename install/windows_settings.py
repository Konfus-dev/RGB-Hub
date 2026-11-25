# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy RGB-Hub's settings from Ubuntu to Windows, once (install-windows.sh runs this).

    python3 windows_settings.py dump --schema-dir DIR > values.json
        (as the owner) every setting from Ubuntu's GSettings, as JSON
    python3 windows_settings.py seed --values values.json --windows-mount /mnt/windows
            --user jercl --ubuntu-config ~/.config/rgbhub
        writes C:\\Users\\<user>\\AppData\\Roaming\\rgbhub\\settings.json, only if it
        is not there yet

Only settings that differ from the defaults are written (as the hub's own
JSON store does). Pictures the settings name are copied into
AppData\\Roaming\\rgbhub\\pictures\\ and the settings point there; a picture
that is on the Windows partition already is used where it is. The LCD
folder (~/.config/rgbhub/lcd) is copied too, if Windows has none yet.
Nothing on Windows is ever overwritten: after the first install, each
system keeps its own settings.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

from rgbhub.core.settings_schema import DEFAULTS, valid  # noqa: E402

SCHEMA = "org.gnome.shell.extensions.rgbhub"
# The hub's and the desktop's own, not the owner's choices.
NOT_COPIED = ("status", "focused-app", "default-action")
BAD_NAME_CHARACTERS = '<>:"/\\|?*'


def parse_gvariant(text: str):
    """What `gsettings get` prints, as a plain Python value (GLib's own parser)."""
    from gi.repository import GLib

    return GLib.Variant.parse(None, text, None, None).unpack()


def read_gsettings(schema_dir: Path, run=subprocess.run, parse=parse_gvariant) -> dict:
    """Every copied setting Ubuntu has, read with `gsettings get`."""
    values = {}
    for key in DEFAULTS:
        if key in NOT_COPIED:
            continue
        cmd = ["gsettings", "--schemadir", str(schema_dir), "get", SCHEMA, key]
        done = run(cmd, capture_output=True, text=True, timeout=30)
        if done.returncode != 0:
            print(f"    (setting {key} not read: {done.stderr.strip()})", file=sys.stderr)
            continue
        values[key] = parse(done.stdout.strip())
    return values


class _Pictures:
    """Where each picture goes on Windows; collects the copies to make."""

    def __init__(self, user: str, windows_mount: Path):
        self.folder = PureWindowsPath("C:\\Users", user, "AppData", "Roaming", "rgbhub", "pictures")
        self.mount = Path(windows_mount).resolve()
        self.copies: list[tuple[Path, str]] = []
        self._names: dict[Path, str] = {}

    def place(self, path: str) -> str | None:
        """The Windows path for the picture at `path`, or None if it is missing."""
        source = Path(path)
        if not path or not source.is_file():
            return None
        source = source.resolve()
        if source.is_relative_to(self.mount):
            return str(PureWindowsPath("C:\\", *source.relative_to(self.mount).parts))
        if source not in self._names:
            name = "".join("_" if c in BAD_NAME_CHARACTERS else c for c in source.name)
            stem, suffix = os.path.splitext(name)
            taken = set(self._names.values())
            number = 2
            while name in taken:
                name = f"{stem}-{number}{suffix}"
                number += 1
            self._names[source] = name
            self.copies.append((source, name))
        return str(self.folder / self._names[source])


def convert(values: dict, user: str, windows_mount: Path) -> tuple[dict, list[tuple[Path, str]]]:
    """(settings for Windows, [(picture on Ubuntu, its name in the pictures folder)])."""
    pictures = _Pictures(user, windows_mount)
    settings = {}
    for key, default in DEFAULTS.items():
        if key in NOT_COPIED or key not in values or not valid(key, values[key]):
            continue
        value = values[key]
        if key in ("lcd-image", "auto-idle-image") and value:
            value = pictures.place(value) or ""
        elif key == "lcd-recent-images":
            placed = (pictures.place(p) for p in value)
            value = list(dict.fromkeys(p for p in placed if p))
        elif key == "default-look":
            try:
                look = json.loads(value)
            except ValueError:
                continue
            if isinstance(look, dict) and isinstance(look.get("lcd-image"), str):
                if look["lcd-image"]:
                    look["lcd-image"] = pictures.place(look["lcd-image"]) or ""
                value = json.dumps(look, sort_keys=True)
        if value != default:
            settings[key] = value
    return settings, pictures.copies


def seed(
    values: dict, windows_home: Path, windows_user: str, windows_mount: Path, ubuntu_config: Path
) -> list[str]:
    """Write the owner's Windows settings, pictures and LCD folder where missing.
    Returns what was done, in words."""
    roaming = Path(windows_home) / "AppData" / "Roaming" / "rgbhub"
    target = roaming / "settings.json"
    said = []
    if target.exists():
        said.append(f"Windows settings kept as they are ({target} exists)")
    else:
        settings, copies = convert(values, windows_user, windows_mount)
        for source, name in copies:
            dest = roaming / "pictures" / name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
        roaming.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=4, sort_keys=True) + "\n")
        os.replace(tmp, target)
        said.append(f"Windows settings copied from Ubuntu ({len(settings)} changed from default)")
    lcd = Path(ubuntu_config) / "lcd"
    if lcd.is_dir():
        if (roaming / "lcd").exists():
            said.append("Windows LCD folder kept as it is")
        else:
            shutil.copytree(lcd, roaming / "lcd")
            said.append("LCD folder copied from Ubuntu")
    return said


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    dump = sub.add_parser("dump", help="print Ubuntu's settings as JSON")
    dump.add_argument("--schema-dir", type=Path, required=True)
    put = sub.add_parser("seed", help="write the Windows settings if missing")
    put.add_argument("--values", type=Path, required=True)
    put.add_argument("--windows-mount", type=Path, required=True)
    put.add_argument("--user", required=True)
    put.add_argument("--ubuntu-config", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "dump":
        json.dump(read_gsettings(args.schema_dir), sys.stdout)
        return 0
    values = json.loads(args.values.read_text())
    home = args.windows_mount / "Users" / args.user
    for line in seed(values, home, args.user, args.windows_mount, args.ubuntu_config):
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
