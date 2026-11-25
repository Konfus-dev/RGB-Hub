# SPDX-License-Identifier: GPL-3.0-or-later
"""The "RGB Hub" app-grid launcher on GNOME: the .desktop, the icon, the installers."""

import configparser
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_layouts import A, B, FakeRun, gnome, make_version

REPO = Path(__file__).resolve().parents[2]
SHELL = REPO / "frontends" / "gnome-shell"
DESKTOP = SHELL / "rgbhub.desktop"
ICON = SHELL / "icons/hicolor/scalable/apps/io.github.konfus_dev.RGBHub.svg"
APP_ID = "io.github.konfus_dev.RGBHub"


def test_desktop_entry_has_exactly_the_agreed_values():
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str
    parser.read(DESKTOP, encoding="utf-8")
    assert dict(parser["Desktop Entry"]) == {
        "Type": "Application",
        "Name": "RGB Hub",
        "Comment": "Lights and cooler screen for the OMEN PC and IQUNIX keyboard",
        "Exec": "gnome-extensions prefs rgbhub@konfus-dev.github.io",
        "Icon": APP_ID,
        "Categories": "Settings;HardwareSettings;",
        "Keywords": "RGB;lights;LCD;OMEN;keyboard;",
        "StartupNotify": "true",
        "OnlyShowIn": "GNOME;",
    }


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None, reason="not installed")
def test_desktop_entry_validates():
    done = subprocess.run(
        ["desktop-file-validate", str(DESKTOP)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_icon_is_valid_svg_with_a_square_viewbox():
    root = ET.parse(ICON).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    _, _, w, h = (float(x) for x in root.get("viewBox").split())
    assert w == h


def test_install_user_installs_through_current():
    script = (REPO / "install/install-user.sh").read_text()
    assert '"$current/frontends/gnome-shell/rgbhub.desktop"' in script
    assert f"app_id={APP_ID}" in script
    assert '"$applications/$app_id.desktop"' in script
    assert '"$icons/hicolor/scalable/apps/$app_id.svg"' in script
    assert "$HOME/.local/share/applications" in script and "$HOME/.local/share/icons" in script
    assert '"$current/frontends/gnome-shell/icons/hicolor/scalable/apps/$app_id.svg"' in script
    assert "update-desktop-database" in script
    assert "gtk-update-icon-cache" in script
    subprocess.run(["bash", "-n", str(REPO / "install/install-user.sh")], check=True)


def _launcher_version(layout, sha, marker):
    v = make_version(layout.root, sha)
    (v / "frontends/gnome-shell/rgbhub.desktop").write_text(f"[Desktop Entry]\n# {marker}\n")
    icon = v / "frontends/gnome-shell/icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_text(f"<svg><!-- {marker} --></svg>")
    return v


def _launcher_paths(layout):
    return (
        layout.applications / f"{APP_ID}.desktop",
        layout.icons / "hicolor/scalable/apps" / f"{APP_ID}.svg",
    )


def test_gnome_activate_installs_and_refreshes_changed_launcher(tmp_path):
    layout = gnome(tmp_path)
    layout.applications = tmp_path / "applications"
    layout.icons = tmp_path / "icons"
    desktop, icon = _launcher_paths(layout)
    layout.activate(_launcher_version(layout, A, "one"))
    assert "one" in desktop.read_text() and "one" in icon.read_text()
    layout.activate(_launcher_version(layout, B, "two"))
    assert "two" in desktop.read_text() and "two" in icon.read_text()
    assert not list(desktop.parent.glob(".*.new"))


def test_gnome_activate_leaves_unchanged_launcher_alone(tmp_path):
    layout = gnome(tmp_path)
    layout.applications = tmp_path / "applications"
    layout.icons = tmp_path / "icons"
    desktop, icon = _launcher_paths(layout)
    layout.activate(_launcher_version(layout, A, "same"))
    old = (desktop.stat().st_ino, icon.stat().st_ino)
    layout.activate(_launcher_version(layout, B, "same"))
    assert (desktop.stat().st_ino, icon.stat().st_ino) == old


def test_gnome_activate_without_launcher_files_is_fine(tmp_path):
    layout = gnome(tmp_path, run=FakeRun())
    layout.applications = tmp_path / "applications"
    layout.icons = tmp_path / "icons"
    layout.activate(make_version(layout.root, A))
    assert not layout.applications.exists()
