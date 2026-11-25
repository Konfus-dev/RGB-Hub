# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

LCD_ID = "HID_ID=0003:000003F0:00007397\nHID_NAME=LCD-PUMP\n"


def make_node(sys_root, name, uevent, descriptor_hex):
    device = sys_root / name / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(uevent)
    (device / "report_descriptor").write_bytes(bytes.fromhex(descriptor_hex))


@pytest.fixture
def roots(tmp_path):
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    sys_root.mkdir()
    dev_root.mkdir()
    make_node(sys_root, "hidraw2", LCD_ID, "05010902a101")  # mouse-class interface
    make_node(sys_root, "hidraw8", LCD_ID, "061aff0993a1")  # vendor interface
    make_node(sys_root, "hidraw4", "HID_ID=0003:0000103C:000084FD\n", "05010900a101")
    return sys_root, dev_root


def test_usage_of_lcd_prefix():
    from rgbhub.platforms.linux.hidraw import usage_of

    assert usage_of(bytes.fromhex("061aff0901"))[0] == 0xFF1A


def test_usage_of_via_prefix():
    from rgbhub.platforms.linux.hidraw import usage_of

    assert usage_of(bytes.fromhex("0660ff0961")) == (0xFF60, 0x61)
    assert usage_of(bytes.fromhex("05010902")) == (1, 2)
    assert usage_of(b"") == (None, None)


def test_hidraw_bus_contract(roots):
    from contracts import check_hid_bus

    from rgbhub.core.interfaces import HidBus, HidMatch
    from rgbhub.platforms.linux.hidraw import HidrawBus

    sys_root, dev_root = roots
    for name in ("hidraw2", "hidraw8", "hidraw4"):
        (dev_root / name).write_bytes(b"")
    bus = HidrawBus(sys_root, dev_root)
    assert isinstance(bus, HidBus)
    vendor = str(dev_root / "hidraw8")
    assert bus.find(HidMatch(0x03F0, 0x7397, usage_page=0xFF1A)) == vendor
    assert bus.find(HidMatch(0x03F0, 0x7397, usage_page=0x0001)) == str(dev_root / "hidraw2")
    assert bus.find(HidMatch(0x103C, 0x84FD, 0xFF1A)) is None
    # A regular file cannot queue replies or record writes separately, so those hooks are left out.
    check_hid_bus(
        bus,
        HidMatch(0x03F0, 0x7397, 0xFF1A),
        vendor,
        lambda path: __import__("os").unlink(path),
    )


def test_usage_of_skips_data_bytes():
    from rgbhub.platforms.linux.hidraw import usage_of

    assert usage_of(bytes.fromhex("8509061aff")) == (0xFF1A, None)
    assert usage_of(bytes.fromhex("15050660ff0961")) == (0xFF60, 0x61)
