# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from rgbhub.core.interfaces import FakeHidBus, FakeHidPort, HidMatch
from rgbhub.core.lights import Lights
from rgbhub.devices.iqunix_keyboard import via
from rgbhub.devices.iqunix_keyboard.mg65 import MG65


class FakePort(FakeHidPort):
    @property
    def sent(self):
        return self.written

    def write(self, report):
        assert len(report) == 1 + via.REPORT_LEN
        super().write(report)


def commands(port):
    """Each report as (command, channel, value id, data...), trailing zeros cut."""
    return [tuple(report[1:].rstrip(b"\x00")) for report in port.sent]


def keyboard():
    port = FakePort()
    client = via.ViaClient(port, MG65)
    return port, client, Lights(lambda: client)


def test_reports_match_the_bytes_the_keyboard_answered_to():
    # Read on the real MG65: 08 03 01 -> brightness, 08 03 04 -> hue, saturation.
    assert via.get_report(1)[:4] == b"\x00\x08\x03\x01"
    assert via.set_report(4, 0x22, 0xFF)[:6] == b"\x00\x07\x03\x04\x22\xff"
    assert via.save_report()[:3] == b"\x00\x09\x03"


def test_colour_becomes_hue_saturation_and_brightness():
    assert via.colour_to_hsv(255, 0, 0) == (0, 255, 255)
    assert via.colour_to_hsv(0, 0, 128) == (170, 255, 128)


def test_a_static_colour_is_sent_once_and_only_changes_after():
    port, _client, lights = keyboard()
    assert lights.try_connect()
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#FF0000"})
    assert commands(port) == [
        (7, 3, 2, 6),
        (7, 3, 4, 0, 255),
        (7, 3, 1, 9),
    ]  # effect, colour, brightness
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#FF0000"})
    assert len(port.sent) == 3  # nothing changed: nothing sent
    lights.apply(True, 50, {}, {"IQUNIX MG65/All keys": "#FF0000"})
    assert commands(port)[3:] == [(7, 3, 1, 5)]  # only the brightness


def test_off_darkens_and_on_comes_back():
    port, _client, lights = keyboard()
    lights.try_connect()
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#00FF00"})
    lights.apply(False, 100, {}, {"IQUNIX MG65/All keys": "#00FF00"})
    assert commands(port)[-1] == (7, 3, 1)  # brightness 0
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#00FF00"})
    assert commands(port)[-1] == (7, 3, 1, 9)


def test_a_moving_effect_gets_its_speed():
    port, _client, lights = keyboard()
    lights.try_connect()
    from rgbhub.core.lights import EffectOptions

    lights.apply(True, 100, {"IQUNIX MG65": "Laser"}, {}, {"IQUNIX MG65": EffectOptions(speed=100)})
    assert (7, 3, 2, 9) in commands(port) and (7, 3, 3, 4) in commands(port)


def test_forgetting_sends_everything_again_and_store_saves():
    port, client, lights = keyboard()
    lights.try_connect()
    lights.apply(True, 100, {}, {})
    sent = len(port.sent)
    lights.forget()
    lights.apply(True, 100, {}, {})
    assert len(port.sent) == sent + 3
    lights.store()
    assert commands(port)[-1] == (9, 3)


def test_the_keyboard_is_found_by_its_via_interface():
    other = FakePort()
    wanted = FakePort()
    bus = FakeHidBus(
        {
            "kbd-main": (HidMatch(0x320F, 0x5055, usage_page=0x0001, usage=0x06), other),
            "kbd-via": (MG65.match, wanted),
        }
    )
    client = via.opener(MG65, bus)()
    client.keyboard.save()
    assert wanted.written and not other.written
    client.disconnect()


def test_opener_raises_when_the_keyboard_is_absent():
    with pytest.raises(OSError):
        via.opener(MG65, FakeHidBus({}))()


def test_via_drains_replies_through_read():
    port = FakePort()
    client = via.ViaClient(port, MG65)
    port.replies = [b"\x07" * 32, b"\x07" * 32]
    client.keyboard.set_mode(client.keyboard.modes[0])
    assert port.replies == []  # both answers to the first set were consumed


def test_update_raises_when_the_keyboard_is_unplugged():
    port = FakePort()
    client = via.ViaClient(port, MG65)
    client.update()
    port.plugged = False
    with pytest.raises(OSError):
        client.update()


def test_a_pale_colour_is_made_more_vivid_but_keeps_its_hue():
    plain = via.colour_to_hsv(255, 177, 44)  # an orange
    vivid = via.colour_to_hsv(255, 177, 44, saturation_curve=2.2)
    assert vivid[0] == plain[0] and vivid[2] == plain[2] and vivid[1] > plain[1]
    assert via.colour_to_hsv(255, 0, 0, 2.2) == (0, 255, 255)


def test_orange_reaches_the_keyboard_as_a_vivid_orange_not_pink():
    """Regression: the MG65 showed a pale orange as pink. Its model makes
    colours more vivid, and the keyboard is sent that, with the hue kept."""
    assert MG65.saturation_curve > 1
    port, _client, lights = keyboard()
    assert lights.try_connect()
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#FFB12C"})
    ((hue, sat),) = [c[3:] for c in commands(port) if c[:3] == (7, 3, via.VALUE_COLOUR)]
    plain_hue, plain_sat, _ = via.colour_to_hsv(255, 177, 44)
    assert hue == plain_hue and 20 <= hue <= 35  # orange; pink is up near 230
    assert sat > plain_sat and sat >= 240
