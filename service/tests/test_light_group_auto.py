# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto mode's colour reaches lights whose effect ignores colours (the RAM's Rainbow)."""

from rgbhub.core.light_group import LightGroup
from rgbhub.core.lights import DeviceInfo, UniformColours


class FakeHub:
    stopping = False
    asleep = False

    def __init__(self, modes=None):
        self._settings = {
            "device-speeds": {},
            "device-min-brightness": {},
            "device-max-brightness": {},
            "device-enabled": {},
            "device-brightness": {},
            "device-modes": modes or {},
            "zone-colours": {"RAM: Stick 1": "#00FF00"},
        }

    def get(self, key):
        return self._settings[key]


class FakeLights:
    def __init__(self, device):
        self._device = device

    def devices(self):
        return [self._device]


RAM = DeviceInfo(
    "RAM",
    ("Static", "Rainbow", "Breathing"),
    ("Stick 1", "Stick 2"),
    default_mode="Rainbow",
    colourless_modes=("Rainbow",),
)
CASE = DeviceInfo("Case", ("Static", "Direct", "Breathing"), ("Fan",), default_mode="Static")


def group(hub, auto):
    return LightGroup(hub, "OMEN-PC", [FakeLights(CASE), FakeLights(RAM)], lambda: auto)


def test_ram_on_its_default_rainbow_shows_auto_colour_steady():
    _enabled, _brightness, modes, colours, _options = group(FakeHub(), "#FF8800").settings()
    assert modes["RAM"] == "Static"
    assert isinstance(colours, UniformColours) and colours.get("RAM: Stick 1") == "#FF8800"
    assert "Case" not in modes  # a device already showing colours is left alone


def test_ram_chosen_rainbow_also_shows_auto_colour():
    _e, _b, modes, _c, _o = group(FakeHub({"RAM": "Rainbow"}), "#FF8800").settings()
    assert modes["RAM"] == "Static"


def test_effects_that_use_the_colour_are_kept():
    _e, _b, modes, _c, _o = group(FakeHub({"RAM": "Breathing"}), "#FF8800").settings()
    assert modes["RAM"] == "Breathing"


def test_without_auto_the_owners_rainbow_stays():
    hub = FakeHub({"RAM": "Rainbow"})
    _e, _b, modes, colours, _o = group(hub, None).settings()
    assert modes == {"RAM": "Rainbow"}
    assert colours == hub.get("zone-colours")
    assert hub.get("device-modes") == {"RAM": "Rainbow"}  # the saved settings are not changed
