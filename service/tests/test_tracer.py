# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from rgbhub.core.interfaces import FakeHidBus, FakeHidPort
from rgbhub.core.lights import Colour, Lights
from rgbhub.devices.omen_pc import case_board as tracer


def test_direct_report_layout():
    report = tracer.direct_report(5, 10, 20, 30)  # zone 5 = bottom fan
    assert len(report) == 58
    assert report[0x02] == 0x12 and report[0x03] == 0x04  # version, direct mode
    assert report[0x04] == 1 and report[0x05] == 1
    assert report[0x30] == 100 and report[0x31] == 0x04  # brightness, type
    assert report[0x36] == 5 and report[0x37] == 0x01  # zone, "while powered on"
    # Zone N's colour sits at 0x08 + 4 * (N - 1): level, red, green, blue.
    assert report[0x18:0x1C] == bytes([100, 10, 20, 30])
    # Nothing else is set.
    assert sum(report) == 0x12 + 0x04 + 1 + 1 + 100 + 0x04 + 5 + 1 + 100 + 10 + 20 + 30


def test_direct_report_clamps_and_checks_the_zone():
    assert tracer.direct_report(1, 300, -5, 0)[0x08:0x0C] == bytes([100, 255, 0, 0])
    with pytest.raises(ValueError):
        tracer.direct_report(0, 0, 0, 0)
    with pytest.raises(ValueError):
        tracer.direct_report(8, 0, 0, 0)


class Port(FakeHidPort):
    """Stands in for the open case-light board."""

    def __init__(self):
        super().__init__()
        self.closed = False

    @property
    def reports(self):
        return self.written

    @property
    def fail(self):
        return not self.plugged

    @fail.setter
    def fail(self, value):
        self.plugged = not value

    def close(self):
        self.closed = True


def test_client_looks_like_openrgb_to_the_lights_module():
    port = Port()
    lights = Lights(lambda: tracer.TracerClient(port))
    assert lights.try_connect() is True
    [info] = lights.devices()
    # Same device and zone names as OpenRGB uses, so settings carry over.
    assert info.name == "HP Omen 30L"
    assert info.zones == ("Omen Logo", "Front Bottom Fan", "Front Middle Fan", "Front Top Fan")
    assert (
        "Static" in info.modes and "Breathing" in info.modes and info.range_modes == ("Breathing",)
    )


def test_setting_colours_writes_one_report_per_zone():
    port = Port()
    lights = Lights(lambda: tracer.TracerClient(port))
    lights.try_connect()
    lights.apply(True, 100, {}, {"HP Omen 30L/Front Top Fan": "#0A141E"})
    assert port.reports == [
        tracer.direct_report(1, 255, 255, 255),
        tracer.direct_report(5, 255, 255, 255),
        tracer.direct_report(6, 255, 255, 255),
        tracer.direct_report(7, 10, 20, 30),
    ]


def test_an_unplugged_board_disconnects_the_lights():
    port = Port()
    lights = Lights(lambda: tracer.TracerClient(port))
    lights.try_connect()
    port.fail = True
    lights.apply(True, 100, {}, {})
    assert lights.connected is False and port.closed is True


def test_case_board_gone_when_port_not_alive():
    port = Port()
    client = tracer.TracerClient(port)
    client.update()  # present: no error
    port.plugged = False
    with pytest.raises(OSError):
        client.update()


def test_open_board_finds_the_board_by_its_ids():
    port = Port()
    bus = FakeHidBus({"board": (tracer.BOARD_MATCH, port)})
    client = tracer.open_board(bus)
    client.devices[0].set_colors([Colour(1, 2, 3)] * 4)
    assert len(port.written) == 4


def test_open_board_raises_when_there_is_no_board():
    with pytest.raises(OSError):
        tracer.open_board(FakeHidBus({}))


def test_colour_packs_the_way_openrgb_expects():
    assert Colour(1, 2, 3).pack() == b"\x01\x02\x03\x00"
    assert Colour(1, 2, 3).red == 1
