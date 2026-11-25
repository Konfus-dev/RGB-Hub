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


def _run_with_watchdog(runtime) -> None:
    watchdog = threading.Timer(5.0, runtime.stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        runtime.run_until_stopped()
    finally:
        watchdog.cancel()


def test_thread_runtime_every_from_other_thread_wakes_idle_loop():
    import time

    from rgbhub.core.thread_runtime import ThreadRuntime

    runtime = ThreadRuntime()
    fired = []
    started = time.monotonic()

    def late() -> bool:
        fired.append(time.monotonic() - started)
        runtime.stop()
        return False

    threading.Timer(0.3, lambda: runtime.every(10, late)).start()
    _run_with_watchdog(runtime)
    assert fired and fired[0] < 1.0


def test_thread_runtime_cancel_from_other_thread():
    from rgbhub.core.thread_runtime import ThreadRuntime

    runtime = ThreadRuntime()
    ticks = []
    handle = runtime.every(10, lambda: ticks.append(1) or True)
    runtime.every(300, lambda: runtime.stop() or False)

    def cancel_later() -> None:
        runtime.cancel(handle)
        runtime.cancel(handle)

    threading.Timer(0.1, cancel_later).start()
    _run_with_watchdog(runtime)
    count = len(ticks)
    assert 0 < count < 25  # ran while alive, and stopped after the cancel


def test_thread_runtime_none_return_ends_timer():
    from rgbhub.core.thread_runtime import ThreadRuntime

    runtime = ThreadRuntime()
    calls = []
    runtime.every(10, lambda: calls.append(1))  # returns None
    runtime.every(200, lambda: runtime.stop() or False)
    _run_with_watchdog(runtime)
    assert calls == [1]


def _hidapi_setup():
    from fake_hid_module import FakeHidModule

    from rgbhub.platforms.windows.hid import HidapiBus

    module = FakeHidModule()
    match = HidMatch(0x103C, 0x84FD, 0xFF00, 1)
    module.plug(b"\\\\?\\hid#a", 0x103C, 0x84FD, 0xFF00, 1)
    module.plug(b"\\\\?\\hid#b", 0x103C, 0x84FD, 0x0001, 6)
    now = [100.0]  # the port re-checks presence at most once a second
    return module, match, HidapiBus(module, clock=lambda: now[0]), now


def test_hidapi_bus_contract():
    module, match, bus, now = _hidapi_setup()
    path = bus.find(match)
    assert path == "\\\\?\\hid#a"
    check_hid_bus(
        bus,
        match,
        path,
        lambda p: (module.unplug(p.encode("latin-1")), now.__setitem__(0, now[0] + 2)),
        queue_reply=lambda p, data: module.queue_reply(p.encode("latin-1"), data),
        written=lambda p: module.written(p.encode("latin-1")),
    )


def test_hidapi_reconnects_after_replug():
    module, match, bus, now = _hidapi_setup()
    old = bus.find(match)
    port = bus.open(old)
    module.unplug(old.encode("latin-1"))
    now[0] += 2
    assert not port.alive()
    assert bus.find(match) is None
    module.plug(b"\\\\?\\hid#c", 0x103C, 0x84FD, 0xFF00, 1)
    new = bus.find(match)
    assert new == "\\\\?\\hid#c"
    assert bus.open(new).alive()
