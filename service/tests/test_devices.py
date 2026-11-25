# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys

from test_hub import MemorySettings

from rgbhub.core.hub import Hub
from rgbhub.core.interfaces import FakeHidBus, FakeRamBus, ManualRuntime
from rgbhub.core.platform import Platform
from rgbhub.core.settings_schema import DEVICE_IDS
from rgbhub.devices import DEVICES, build_all


def fake_platform(**extra) -> Platform:
    return Platform("gnome", FakeHidBus({}), ManualRuntime(), FakeRamBus([]), **extra)


def test_every_device_builds_on_fake_platform():
    built = build_all(fake_platform())
    assert len(built) == len(DEVICES)
    for device in built:
        for name in ("id", "label", "status"):
            assert hasattr(device, name)


def test_status_describes_device():
    settings = MemorySettings()
    hub = Hub(settings, build_all(fake_platform()))
    hub.start()
    reports = {d["id"]: d for d in json.loads(settings.get("status"))["devices"]}
    hub.stop(show_default=False)
    omen, keyboard = reports["OMEN-PC"], reports["IQUNIX-KEYBOARD"]
    for report in (omen, keyboard):
        assert report["label"] and report["icon"]
    assert omen["has-lcd"] is True and keyboard["has-lcd"] is False
    assert omen["icon"] == "computer-symbolic"
    assert keyboard["icon"] == "input-keyboard-symbolic"


def test_no_ram_bus_means_no_ram_lights():
    platform = Platform("batocera", FakeHidBus({}), ManualRuntime(), ram_bus=None, lcd_fade=False)
    omen = build_all(platform)[0]
    assert omen._ram is None and omen._fade is None


def test_device_ids_match_list():
    assert DEVICE_IDS == tuple(m.DEVICE_ID for m in DEVICES)


def test_self_check_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "rgbhub", "--self-check", "--platform", "gnome"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok 2 devices")
