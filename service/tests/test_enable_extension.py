# SPDX-License-Identifier: GPL-3.0-or-later
"""The GNOME extension was installed but silently left off twice: after the
first install and after the rename to a new id. install-user.sh now puts it
on GNOME's enabled list itself, through install/enable-gnome-extension.py.

These tests run that script against a fake gsettings that keeps its values
in files; the real settings are never touched."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENABLER = REPO / "install" / "enable-gnome-extension.py"
EXTENSIONS = REPO / "frontends" / "gnome-shell"
OLD_UUID = "omenrgb@konfus-dev.github.io"

FAKE_GSETTINGS = """#!/bin/sh
# gsettings get|set org.gnome.shell <key> [value], kept in files in $FAKE_DCONF.
store="$FAKE_DCONF/$2.$3"
case "$1" in
    get) if [ -f "$store" ]; then cat "$store"; else cat "$FAKE_DCONF/default.$3"; fi ;;
    set) printf '%s\\n' "$4" > "$store"; echo "$3" >> "$FAKE_DCONF/writes" ;;
    *) exit 1 ;;
esac
"""


def current_uuid() -> str:
    """The id the extension has now, from its own metadata.json."""
    (folder,) = [p for p in EXTENSIONS.iterdir() if (p / "metadata.json").exists()]
    uuid = json.loads((folder / "metadata.json").read_text())["uuid"]
    assert folder.name == uuid
    return uuid


class FakeDconf:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir()
        bin_dir = folder / "bin"
        bin_dir.mkdir()
        self.program = bin_dir / "gsettings"
        self.program.write_text(FAKE_GSETTINGS)
        self.program.chmod(0o755)
        self.put("default.enabled-extensions", "@as []")
        self.put("default.disabled-extensions", "@as []")
        self.put("default.disable-user-extensions", "false")
        self.env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GSETTINGS": str(self.program),
            "FAKE_DCONF": str(folder),
            # Should the fake be missed anyway, nothing reaches the real settings.
            "GSETTINGS_BACKEND": "memory",
            "HOME": str(folder),
        }

    def put(self, name: str, value: str) -> None:
        (self.folder / name).write_text(value + "\n")

    def set(self, key: str, value: str) -> None:
        self.put(f"org.gnome.shell.{key}", value)

    def get(self, key: str) -> list[str]:
        path = self.folder / f"org.gnome.shell.{key}"
        if not path.exists():
            path = self.folder / f"default.{key}"
        text = path.read_text().strip().removeprefix("@as").strip()
        return re.findall(r"'([^']*)'", text)

    def writes(self) -> list[str]:
        path = self.folder / "writes"
        return path.read_text().split() if path.exists() else []

    def run(self, *args: str):
        return subprocess.run(
            [sys.executable, str(ENABLER), *args], env=self.env, capture_output=True, text=True
        )

    def run_as_installer(self):
        """The enabler exactly as install-user.sh calls it."""
        line = next(
            line.strip()
            for line in (REPO / "install" / "install-user.sh").read_text().splitlines()
            if "enable-gnome-extension.py" in line and not line.lstrip().startswith("#")
        )
        assert line.startswith('python3 "$src/install/enable-gnome-extension.py" "$uuid"')
        args = line.split('"$uuid"', 1)[1].removesuffix("|| true").split()
        return self.run(current_uuid(), *args)


@pytest.fixture
def dconf(tmp_path):
    return FakeDconf(tmp_path / "dconf")


def test_the_installer_names_the_extensions_current_id():
    text = (REPO / "install" / "install-user.sh").read_text()
    assert f'uuid="{current_uuid()}"' in text
    assert current_uuid() != OLD_UUID


def test_a_first_install_adds_the_extension(dconf):
    assert dconf.run_as_installer().returncode == 0
    assert dconf.get("enabled-extensions") == [current_uuid()]


def test_the_old_id_is_replaced_and_others_are_kept(dconf):
    dconf.set("enabled-extensions", f"['ding@rastersoft.com', '{OLD_UUID}', 'ubuntu-dock']")
    assert dconf.run_as_installer().returncode == 0
    enabled = dconf.get("enabled-extensions")
    assert enabled.count(current_uuid()) == 1
    assert OLD_UUID not in enabled
    assert enabled == ["ding@rastersoft.com", "ubuntu-dock", current_uuid()]


def test_the_id_is_on_the_list_exactly_once(dconf):
    uuid = current_uuid()
    dconf.set("enabled-extensions", f"['{uuid}', 'a@b', '{uuid}']")
    assert dconf.run_as_installer().returncode == 0
    assert dconf.get("enabled-extensions") == [uuid, "a@b"]


def test_running_it_again_changes_nothing(dconf):
    dconf.run_as_installer()
    writes = dconf.writes()
    assert dconf.run_as_installer().returncode == 0
    assert dconf.writes() == writes
    assert dconf.get("enabled-extensions") == [current_uuid()]


def test_it_is_taken_off_the_disabled_list(dconf):
    # GNOME leaves an extension off while it is on both lists.
    dconf.set("disabled-extensions", f"['{current_uuid()}', 'other@x']")
    assert dconf.run_as_installer().returncode == 0
    assert dconf.get("disabled-extensions") == ["other@x"]


def test_all_user_extensions_off_is_reported(dconf):
    dconf.put("default.disable-user-extensions", "true")
    result = dconf.run_as_installer()
    assert "WARNING" in result.stderr


def test_a_failure_is_reported_not_hidden(dconf):
    dconf.program.write_text("#!/bin/sh\nexit 1\n")
    result = dconf.run_as_installer()
    assert result.returncode != 0
    assert f"gnome-extensions enable {current_uuid()}" in result.stderr


@pytest.mark.parametrize("args", [[], ["x'; rm -rf ~; '"], ["a@b", "--remove"], ["a@b", "x"]])
def test_bad_arguments_change_nothing(dconf, args):
    assert dconf.run(*args).returncode != 0
    assert dconf.writes() == []


def test_the_fake_is_what_runs(dconf):
    # Guards the guard: the tests above must never reach the real settings.
    assert os.access(dconf.program, os.X_OK)
    dconf.run_as_installer()
    assert "enabled-extensions" in dconf.writes()
