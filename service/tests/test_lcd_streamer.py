# SPDX-License-Identifier: GPL-3.0-or-later
from rgbhub.core.interfaces import FakeHidBus, FakeHidPort
from rgbhub.devices.omen_pc import lcd_protocol as p
from rgbhub.devices.omen_pc.lcd_constants import LCD_MATCH
from rgbhub.devices.omen_pc.lcd_streamer import LcdStreamer, lcd_opener

JPEG = bytes(range(256)) * 10  # 3 chunks


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.fail = False

    def write(self, report):
        if self.fail:
            raise OSError("unplugged")
        self.writes.append(report)

    def close(self):
        self.closed = True


class Opener:
    """Hands out the queued devices one per call; None when it runs out."""

    def __init__(self, *devices):
        self.devices = list(devices)

    def __call__(self):
        return self.devices.pop(0) if self.devices else None


def connected_streamer():
    device = FakeDevice()
    streamer = LcdStreamer(Opener(device))
    streamer.tick_reconnect()
    return streamer, device


def test_starts_disconnected():
    assert LcdStreamer(Opener()).connected is False


def test_connect_sends_claim_then_brightness():
    streamer, device = connected_streamer()
    assert streamer.connected is True
    assert device.writes == [p.claim_report(), p.brightness_report(80)]


def test_tick_frame_sends_all_chunks_in_order():
    streamer, device = connected_streamer()
    streamer.set_image(JPEG)
    device.writes.clear()
    streamer.tick_frame()
    assert device.writes == p.frame_reports(JPEG)


def test_no_image_sends_no_frames():
    streamer, device = connected_streamer()
    device.writes.clear()
    streamer.tick_frame()
    assert device.writes == []


def test_tick_claim_repeats_claim():
    streamer, device = connected_streamer()
    device.writes.clear()
    streamer.tick_claim()
    streamer.tick_claim()
    assert device.writes == [p.claim_report(), p.claim_report()]


def test_brightness_change_sent_once():
    streamer, device = connected_streamer()
    device.writes.clear()
    streamer.set_brightness(40)
    streamer.tick_claim()
    streamer.tick_claim()
    assert device.writes.count(p.brightness_report(40)) == 1


def test_write_error_disconnects_without_raising():
    streamer, device = connected_streamer()
    streamer.set_image(JPEG)
    device.fail = True
    streamer.tick_frame()
    assert streamer.connected is False and device.closed is True
    streamer.tick_claim()  # still must not raise
    streamer.tick_frame()


def test_reconnect_resumes_image_and_brightness():
    first, second = FakeDevice(), FakeDevice()
    streamer = LcdStreamer(Opener(first, second))
    streamer.tick_reconnect()
    streamer.set_image(JPEG)
    streamer.set_brightness(30)
    first.fail = True
    streamer.tick_claim()
    streamer.tick_reconnect()
    assert streamer.connected is True
    assert second.writes == [p.claim_report(), p.brightness_report(30)]
    streamer.tick_frame()
    assert second.writes[2:] == p.frame_reports(JPEG)


def test_reconnect_does_nothing_while_connected():
    spare = FakeDevice()
    opener = Opener(FakeDevice(), spare)
    streamer = LcdStreamer(opener)
    streamer.tick_reconnect()
    streamer.tick_reconnect()
    assert opener.devices == [spare]


def test_open_returning_none_stays_disconnected():
    streamer = LcdStreamer(Opener())
    streamer.set_image(JPEG)
    for tick in (streamer.tick_reconnect, streamer.tick_claim, streamer.tick_frame):
        tick()
    assert streamer.connected is False


def test_open_raising_oserror_stays_disconnected():
    def opener():
        raise PermissionError("no access to /dev/hidraw8")

    streamer = LcdStreamer(opener)
    streamer.tick_reconnect()
    assert streamer.connected is False


def test_oversized_image_is_refused_and_previous_kept():
    streamer, device = connected_streamer()
    streamer.set_image(JPEG)
    streamer.set_image(bytes(70000))  # too big for the LCD
    device.writes.clear()
    streamer.tick_frame()
    assert device.writes == p.frame_reports(JPEG)


def test_lcd_opener_opens_the_vendor_interface_only():
    from rgbhub.core.interfaces import HidMatch

    mouse, vendor = FakeHidPort(), FakeHidPort()
    bus = FakeHidBus(
        {
            "mouse": (HidMatch(0x03F0, 0x7397, usage_page=0x0001), mouse),
            "vendor": (LCD_MATCH, vendor),
        }
    )
    streamer = LcdStreamer(lcd_opener(bus))
    streamer.tick_reconnect()
    assert streamer.connected is True
    assert vendor.written == [p.claim_report(), p.brightness_report(80)]
    assert mouse.written == []


def test_lcd_opener_returns_none_when_the_lcd_is_absent():
    assert lcd_opener(FakeHidBus({}))() is None
