# SPDX-License-Identifier: GPL-3.0-or-later
import threading

from contracts import check_hid_bus, check_runtime

from rgbhub.core.interfaces import FakeHidBus, FakeHidPort, HidMatch, HidPort


def test_fake_hid_bus_contract():
    match = HidMatch(0x103C, 0x84FD, 0xFF00, 1)
    devices = {"/dev/fake0": (match, FakeHidPort())}
    bus = FakeHidBus(devices)
    check_hid_bus(
        bus,
        match,
        "/dev/fake0",
        bus.unplug,
        queue_reply=lambda path, data: devices[path][1].replies.append(data),
        written=lambda path: devices[path][1].written,
    )
    assert isinstance(devices["/dev/fake0"][1], HidPort)


def test_hidmatch_any_interface():
    bus = FakeHidBus(
        {
            "a": (HidMatch(0x103C, 0x84FD, 0xFF00, 1), FakeHidPort()),
            "b": (HidMatch(0x103C, 0x84FD, 0x0001, 6), FakeHidPort()),
        }
    )
    assert bus.find(HidMatch(0x103C, 0x84FD)) in {"a", "b"}
    assert bus.find(HidMatch(0x103C, 0x84FD, usage_page=0x0001)) == "b"
    assert bus.find(HidMatch(0x103C, 0x0000)) is None
    bus.unplug("a")
    bus.unplug("b")
    assert bus.find(HidMatch(0x103C, 0x84FD)) is None


def test_ram_buses_fit():
    from rgbhub.core.interfaces import FakeRamBus, RamBus
    from rgbhub.platforms.linux.ram import AcpiRamBus, HelperRamBus

    fake = FakeRamBus([0xC2, 0xC6])
    assert isinstance(fake, RamBus)
    assert isinstance(HelperRamBus(), RamBus)
    assert isinstance(AcpiRamBus(object(), lambda: []), RamBus)
    assert fake.sticks() == [0xC2, 0xC6]
    fake.send([(0xC2, 0x20, 1)])
    assert fake.batches == [[(0xC2, 0x20, 1)]]


def test_glib_runtime_contract():
    from rgbhub.platforms.linux.glib_runtime import GLibRuntime

    check_runtime(GLibRuntime)


def test_thread_runtime_contract():
    from rgbhub.core.thread_runtime import ThreadRuntime

    check_runtime(lambda: ThreadRuntime(poll_s=0.05))


def test_thread_runtime_survives_raising_timer():
    from rgbhub.core.thread_runtime import ThreadRuntime

    runtime = ThreadRuntime(poll_s=0.05)
    calls = []

    def raising() -> bool:
        calls.append(1)
        if len(calls) >= 3:
            runtime.stop()
        raise RuntimeError("boom")

    runtime.every(10, raising)
    runtime.call_soon(lambda: 1 / 0)  # a failing queued call must not kill the loop either
    watchdog = threading.Timer(5.0, runtime.stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        runtime.run_until_stopped()
    finally:
        watchdog.cancel()
    assert len(calls) >= 3
