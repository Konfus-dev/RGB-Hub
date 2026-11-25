# SPDX-License-Identifier: GPL-3.0-or-later
from rgbhub.backends.ram_bios import find_sticks
from rgbhub.core.lights import DeviceInfo, EffectOptions
from rgbhub.devices.omen_pc.ram import RamLights, commands

BEGIN, END = 0x53, 0x44
LEFT, RIGHT = 0xC2, 0xC6  # slots 1 and 3, as on the test machine


def make_sysfs(tmp_path, *spd_addresses, name="spd5118"):
    for address in spd_addresses:
        node = tmp_path / f"0-00{address:02x}"
        node.mkdir()
        (node / "name").write_text(name + "\n")
    return tmp_path


def test_find_sticks(tmp_path):
    assert find_sticks(make_sysfs(tmp_path, 0x53, 0x51)) == [LEFT, RIGHT]


def test_find_sticks_ignores_other_chips_and_missing_sysfs(tmp_path):
    assert find_sticks(make_sysfs(tmp_path, 0x51, name="eeprom")) == []
    assert find_sticks(tmp_path / "missing") == []


def test_static_is_hps_sequence_with_a_colour_per_stick():
    result = commands("Static", 80, [(255, 0, 0), (0, 0, 255)], [LEFT, RIGHT])
    assert result == [
        (LEFT, 0x08, BEGIN), (RIGHT, 0x08, BEGIN),
        (LEFT, 0x09, 0x00), (RIGHT, 0x09, 0x00),
        (LEFT, 0x20, 80), (RIGHT, 0x20, 80),
        (LEFT, 0x30, 1), (RIGHT, 0x30, 1),
        (LEFT, 0x31, 255), (RIGHT, 0x31, 0),
        (LEFT, 0x32, 0), (RIGHT, 0x32, 0),
        (LEFT, 0x33, 0), (RIGHT, 0x33, 255),
        (LEFT, 0x08, END), (RIGHT, 0x08, END),
    ]  # fmt: skip


def test_off_is_hps_sequence():
    assert commands("Off", 80, [(1, 2, 3)], [LEFT]) == [
        (LEFT, 0x08, BEGIN), (LEFT, 0x20, 0), (LEFT, 0x08, END),
    ]  # fmt: skip


def test_rainbow_sets_effect_and_brightness_and_stick_order():
    result = commands("Rainbow", 60, [], [LEFT, RIGHT])
    assert result[0] == (LEFT, 0x08, BEGIN) and result[-1] == (RIGHT, 0x08, END)
    assert (LEFT, 0x09, 0x01) in result and (RIGHT, 0x20, 60) in result
    # Each stick is told its position so the rainbow flows across them.
    assert (LEFT, 0x0B, 1) in result and (RIGHT, 0x0B, 0) in result
    assert (LEFT, 0x27, 2) in result


def test_breathing_uses_each_sticks_colour():
    result = commands("Breathing", 100, [(255, 0, 0), (0, 255, 0)], [LEFT, RIGHT])
    assert (LEFT, 0x09, 0x03) in result
    assert (LEFT, 0x31, 255) in result and (RIGHT, 0x32, 255) in result


def test_every_command_is_one_the_root_helper_accepts():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "helper" / "rgbhub_ram_helper.py"
    spec = importlib.util.spec_from_file_location("helper", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    for mode in ("Static", "Rainbow", "Breathing", "Off"):
        for address, register, value in commands(
            mode, 100, [(1, 2, 3)] * 4, [0xC0, 0xC2, 0xC4, 0xC6]
        ):
            helper.parse_request(f"W {address:02x} {register:02x} {value:02x}")  # must not raise


def test_unknown_mode_falls_back_to_static_and_values_are_clamped():
    result = commands("Disco", 250, [(300, -5, 0)], [LEFT])
    assert (LEFT, 0x09, 0x00) in result and (LEFT, 0x20, 100) in result
    assert (LEFT, 0x31, 255) in result and (LEFT, 0x32, 0) in result


def test_missing_colours_default_to_white():
    assert (RIGHT, 0x31, 255) in commands("Static", 100, [(1, 1, 1)], [LEFT, RIGHT])


def test_no_sticks_no_commands():
    assert commands("Static", 100, [], []) == []


class Sender:
    def __init__(self):
        self.batches, self.fail = [], False

    def __call__(self, batch):
        if self.fail:
            raise OSError("helper not running")
        self.batches.append(batch)


NOW = [1000.0]  # a clock the tests can move forward


def connected(tmp_path, *spd):
    sender = Sender()
    sysfs = make_sysfs(tmp_path, *spd)
    ram = RamLights(sender, lambda: find_sticks(sysfs), clock=lambda: NOW[0])
    assert ram.try_connect() is True
    sender.batches.clear()  # drop the connection test
    return ram, sender


def test_devices_lists_ram_like_any_other_light(tmp_path):
    ram, _ = connected(tmp_path, 0x51, 0x53)
    assert ram.devices() == [
        DeviceInfo(
            "RAM",
            ("Static", "Rainbow", "Breathing"),
            ("Stick 1", "Stick 2"),
            default_mode="Rainbow",
            colourless_modes=("Rainbow",),
            speed_modes=("Rainbow", "Breathing"),
            range_modes=("Breathing",),
        )
    ]


def test_apply_reads_mode_and_per_stick_colours(tmp_path):
    ram, sender = connected(tmp_path, 0x51, 0x53)
    ram.apply(True, 40, {"RAM": "Static"}, {"RAM/Stick 2": "#00FF00"})
    assert sender.batches == [commands("Static", 40, [(255, 255, 255), (0, 255, 0)], [LEFT, RIGHT])]


def test_apply_defaults_to_rainbow(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(True, 100, {}, {})
    assert sender.batches == [commands("Rainbow", 100, [(255, 255, 255)], [LEFT])]


def test_apply_disabled_turns_sticks_off(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(False, 100, {"RAM": "Static"}, {})
    assert sender.batches == [commands("Off", 100, [], [LEFT])]


def test_identical_apply_is_not_sent_twice(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(True, 100, {}, {})
    ram.apply(True, 100, {}, {})
    assert len(sender.batches) == 1


def test_helper_missing_is_quiet(tmp_path):
    sender = Sender()
    sender.fail = True
    ram = RamLights(sender, lambda: [LEFT])
    assert ram.try_connect() is False and ram.connected is False and ram.devices() == []
    ram.apply(True, 100, {}, {})  # must not raise


def test_no_sticks_means_not_connected():
    assert RamLights(Sender(), lambda: []).try_connect() is False


def test_send_failure_disconnects_then_recovers_and_resends(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    sender.fail = True
    ram.apply(True, 100, {}, {})
    assert ram.connected is False and ram.check() is False
    sender.fail = False
    NOW[0] += 60  # past the back-off
    assert ram.try_connect() is True
    ram.apply(True, 100, {}, {})
    assert sender.batches[-1] == commands("Rainbow", 100, [(255, 255, 255)], [LEFT])


def test_ram_applies_gamma_to_stick_colours(tmp_path):
    sender = Sender()
    sysfs = make_sysfs(tmp_path, 0x51)
    ram = RamLights(sender, lambda: find_sticks(sysfs), gamma=2.2)
    ram.try_connect()
    sender.batches.clear()
    ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": "#613583"})
    assert sender.batches == [commands("Static", 100, [(30, 8, 59)], [LEFT])]


def value(result, stick, register):
    return next(v for a, r, v in result if a == stick and r == register)


def test_rainbow_speed():
    def speed(percent):
        return value(commands("Rainbow", 100, [], [LEFT], speed=percent), LEFT, 0x0E)

    assert speed(50) == 25  # the default the owner approved by eye
    assert speed(100) == 0 and speed(0) == 50  # smaller number is faster
    assert speed(999) == 0 and speed(-1) == 50


def test_breathing_speed():
    def times(percent):
        result = commands("Breathing", 100, [(1, 2, 3)], [LEFT], speed=percent)
        return [value(result, LEFT, r) for r in (0x16, 0x17, 0x18, 0x19)]

    assert times(50) == [15, 5, 5, 15]  # the default the owner approved by eye
    assert times(100) == [3, 1, 1, 3] and times(0) == [27, 9, 9, 27]


def test_breathing_brightness_range():
    result = commands("Breathing", 100, [(1, 2, 3)], [LEFT], min_brightness=20, max_brightness=70)
    assert [value(result, LEFT, r) for r in (0x1B, 0x1C, 0x1D)] == [70, 52, 20]
    default = commands("Breathing", 100, [(1, 2, 3)], [LEFT])
    assert [value(default, LEFT, r) for r in (0x1B, 0x1C, 0x1D)] == [100, 64, 0]


def test_breathing_range_is_kept_sane():
    result = commands("Breathing", 100, [(1, 2, 3)], [LEFT], min_brightness=90, max_brightness=40)
    brightest, _mid, dimmest = (value(result, LEFT, r) for r in (0x1B, 0x1C, 0x1D))
    assert dimmest <= brightest
    wild = commands("Breathing", 100, [(1, 2, 3)], [LEFT], min_brightness=-5, max_brightness=500)
    assert [value(wild, LEFT, r) for r in (0x1B, 0x1D)] == [100, 0]


def test_apply_passes_effect_options(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    options = {"RAM": EffectOptions(speed=80, min_brightness=10, max_brightness=90)}
    ram.apply(True, 100, {"RAM": "Breathing"}, {}, options)
    assert sender.batches == [
        commands(
            "Breathing", 100, [(255, 255, 255)], [LEFT],
            speed=80, min_brightness=10, max_brightness=90,
        )
    ]  # fmt: skip


def test_failed_helper_is_left_alone_for_a_while(tmp_path):
    # A stuck helper must not be retried every few seconds: each try can
    # block the service, which also has to keep the LCD fed.
    now = [1000.0]
    sender = Sender()
    sysfs = make_sysfs(tmp_path, 0x51)
    ram = RamLights(sender, lambda: find_sticks(sysfs), clock=lambda: now[0])
    sender.fail = True
    assert ram.try_connect() is False
    sender.fail = False
    now[0] += 5
    assert ram.try_connect() is False  # still backing off
    now[0] += 60
    assert ram.try_connect() is True


def test_send_failure_also_backs_off(tmp_path):
    now = [1000.0]
    sender = Sender()
    sysfs = make_sysfs(tmp_path, 0x51)
    ram = RamLights(sender, lambda: find_sticks(sysfs), clock=lambda: now[0])
    ram.try_connect()
    sender.fail = True
    ram.apply(True, 100, {}, {})
    sender.fail = False
    assert ram.try_connect() is False
    now[0] += 60
    assert ram.try_connect() is True


def test_forget_makes_the_next_apply_send_again(tmp_path):
    # After sleep the sticks may have lost their settings even though ours
    # have not changed.
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(True, 100, {}, {})
    ram.forget()
    ram.apply(True, 100, {}, {})
    assert len(sender.batches) == 2


# --- only what changed is sent; slow sends happen in the background -------------


def test_a_colour_change_sends_only_the_colour():
    # Each write goes through the BIOS and takes about 65 ms, so repeating
    # the whole set-up for every colour change would take over a second.
    sender = Sender()
    ram = RamLights(sender, lambda: [LEFT, RIGHT])
    ram.try_connect()
    sender.batches.clear()
    ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": "#FF0000", "RAM/Stick 2": "#FF0000"})
    ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": "#0000FF", "RAM/Stick 2": "#FF0000"})
    assert sender.batches[-1] == [
        (LEFT, 0x08, BEGIN), (RIGHT, 0x08, BEGIN),
        (LEFT, 0x31, 0), (LEFT, 0x33, 255),
        (LEFT, 0x08, END), (RIGHT, 0x08, END),
    ]  # fmt: skip


def test_off_then_on_restores_brightness_only():
    sender = Sender()
    ram = RamLights(sender, lambda: [LEFT])
    ram.try_connect()
    ram.apply(True, 80, {"RAM": "Static"}, {})
    ram.apply(False, 80, {"RAM": "Static"}, {})
    ram.apply(True, 80, {"RAM": "Static"}, {})
    assert sender.batches[-1] == [(LEFT, 0x08, BEGIN), (LEFT, 0x20, 80), (LEFT, 0x08, END)]


def test_background_sending_does_not_hold_up_the_caller_and_latest_wins():
    import threading

    started, release = threading.Event(), threading.Event()
    sent = []

    def slow_send(batch):
        if batch:  # not the connection test
            started.set()
            release.wait(5)
            sent.append(batch)

    ram = RamLights(slow_send, lambda: [LEFT], background=True)
    ram.try_connect()
    ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": "#FF0000"})  # returns at once
    assert started.wait(5)  # the slow send is now under way
    for colour in ("#00FF00", "#0000FF"):
        ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": colour})
    assert sent == []  # the first send is still in progress
    release.set()
    assert ram.wait_idle(5) is True
    # The first request went out; the middle one was overtaken; the last one won.
    assert len(sent) == 2
    assert (LEFT, 0x33, 255) in sent[-1] and (LEFT, 0x31, 0) in sent[-1]


def test_a_request_still_queued_at_a_disconnect_keeps_its_begin_and_end():
    import threading

    started, release = threading.Event(), threading.Event()
    sent = []

    def slow_send(batch):
        if batch:
            started.set()
            release.wait(5)
            sent.append(batch)

    ram = RamLights(slow_send, lambda: [LEFT], background=True, clock=lambda: NOW[0])
    ram.try_connect()
    ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": "#FF0000"})
    assert started.wait(5)
    ram.apply(True, 100, {"RAM": "Static"}, {"RAM/Stick 1": "#00FF00"})  # queued
    ram._failed = True  # as if a send had failed: the main thread disconnects
    assert ram.check() is False
    release.set()
    assert ram.wait_idle(5) is True
    assert len(sent) == 2
    for batch in sent:
        assert batch[0] == (LEFT, 0x08, BEGIN) and batch[-1] == (LEFT, 0x08, END)


def test_background_failure_is_noticed_by_check():
    def failing_send(batch):
        if batch:
            raise OSError("helper died")

    ram = RamLights(failing_send, lambda: [LEFT], background=True, clock=lambda: NOW[0])
    ram.try_connect()
    ram.apply(True, 100, {}, {})
    assert ram.wait_idle(5) is True
    assert ram.check() is False and ram.connected is False


# --- finding the sticks from DMI, where the kernel does not list them ------------

DMIDECODE = """# dmidecode 3.6
Handle 0x0010, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tLocator: DIMM 1

Handle 0x0011, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tLocator: DIMM 2

Handle 0x0012, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tLocator: DIMM 3

Handle 0x0013, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tLocator: DIMM 4
"""


def test_sticks_from_dmidecode_output():
    from rgbhub.backends.ram_bios import sticks_from_dmidecode

    assert sticks_from_dmidecode(DMIDECODE) == [LEFT, RIGHT]  # slots 1 and 3
    assert sticks_from_dmidecode("") == []
    assert sticks_from_dmidecode("Memory Device\n\tSize: 16 GB\n" * 6) == [0xC0, 0xC2, 0xC4, 0xC6]


def test_find_sticks_dmi_survives_a_missing_tool():
    from rgbhub.backends.ram_bios import find_sticks_dmi

    assert find_sticks_dmi(command=["/no/such/dmidecode"]) == []


def test_direct_sender_trusts_the_platforms_list_of_sticks(tmp_path):
    import importlib.util
    from pathlib import Path

    from rgbhub.backends.ram_bios import direct_sender

    path = Path(__file__).resolve().parents[2] / "helper" / "rgbhub_ram_helper.py"
    spec = importlib.util.spec_from_file_location("helper", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    sent = []
    helper.call_bios = lambda command: sent.append(command) or helper.REPLY_OK + "}"
    helper.time.sleep = lambda seconds: None
    send = direct_sender(helper, lambda: [LEFT])
    send([(LEFT, 0x20, 50)])
    assert len(sent) == 1
    import pytest

    with pytest.raises(OSError):
        send([(RIGHT, 0x20, 50)])  # not a fitted stick: refused before reaching the BIOS
    with pytest.raises(OSError):
        send([(LEFT, 0x00, 0)])  # the helper's own checks still apply
    assert len(sent) == 1
