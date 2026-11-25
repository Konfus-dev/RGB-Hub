# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import subprocess
from pathlib import Path

import pytest
from gi.repository import Gio

from rgbhub.platforms.gnome.gsettings_store import load_settings

REPO = Path(__file__).resolve().parents[2]
SCHEMA_SOURCE = REPO / "frontends" / "gnome-shell" / "rgbhub@konfus-dev.github.io" / "schemas"


@pytest.fixture
def schema_dir(tmp_path):
    """A compiled copy of the real schema, so tests never touch the repo."""
    target = tmp_path / "schemas"
    shutil.copytree(SCHEMA_SOURCE, target, ignore=shutil.ignore_patterns("*.compiled"))
    subprocess.run(["glib-compile-schemas", "--strict", str(target)], check=True)
    return target


@pytest.fixture
def settings(schema_dir):
    """Settings held in memory only, so tests never touch the real ones."""
    return load_settings(schema_dir, backend=Gio.memory_settings_backend_new())
