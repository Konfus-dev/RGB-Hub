# SPDX-License-Identifier: GPL-3.0-or-later
"""Assertions every implementation of a core interface must satisfy.

A test for a real implementation (or for the in-memory fake) calls the
matching `check_*` function with whatever it needs to set the scene.
"""

import os
import tempfile
import threading
import time
from collections.abc import Callable

from rgbhub.core.interfaces import HidMatch


def check_hid_bus(
    bus,
    present_match: HidMatch,
    path_of_present: str,
    unplug: Callable[[str], None],
    queue_reply: Callable[[str, bytes], None] | None = None,
    written: Callable[[str], list[bytes]] | None = None,
) -> None:
    """A bus finds a present device, talks to it, and notices it being unplugged.

    `queue_reply(path, data)` and `written(path)` are only needed to check
    what went over the wire; real hardware leaves them out.
    """
    assert bus.find(present_match) == path_of_present
    assert bus.find(HidMatch(1, 2)) is None
    port = bus.open(path_of_present)
    assert port.alive()
    if queue_reply:
        queue_reply(path_of_present, b"\x01reply")
    port.write(b"\x00one")
    port.write(b"\x00two")
    if written:
        assert written(path_of_present) == [b"\x00one", b"\x00two"]
    if queue_reply:
        assert port.read(0.01) == b"\x01reply"
    assert port.read(0.01) is None
    unplug(path_of_present)
    assert not port.alive()
    try:
        port.write(b"\x00late")
    except OSError:
        pass
    else:
        raise AssertionError("write after unplug must raise OSError")
    port.close()


def check_runtime(runtime_factory: Callable[[], object]) -> None:
    """A runtime runs timers in interval order, can cancel one, runs calls queued
    from another thread on the loop thread, reports a file replaced with
    os.replace within 3 s, and returns from run_until_stopped() after stop()."""
    runtime = runtime_factory()
    fired: list[str] = []
    loop_thread: list[int] = []
    queued_on: list[int] = []
    changed: list[float] = []
    state = {"replaced_at": 0.0, "returned": False}

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "watched")
        with open(path, "w") as f:
            f.write("old")

        def fast() -> bool:
            fired.append("fast")
            return len(fired) < 40  # ends itself; the slow one fires in between

        def slow() -> bool:
            fired.append("slow")
            return False

        def cancelled() -> bool:
            fired.append("cancelled")
            return True

        def begin() -> None:
            # Everything below runs on the loop, from its first turn.
            loop_thread.append(threading.get_ident())
            handle = runtime.every(30, cancelled)
            runtime.cancel(handle)
            runtime.cancel(handle)  # cancelling twice must be harmless
            runtime.every(20, fast)
            runtime.every(70, slow)
            threading.Thread(
                target=lambda: runtime.call_soon(lambda: queued_on.append(threading.get_ident())),
            ).start()
            # JsonSettings saves like this: a temporary file, then os.replace.
            temporary = path + ".tmp"
            with open(temporary, "w") as f:
                f.write("new")
            state["replaced_at"] = time.monotonic()
            os.replace(temporary, path)

        stop_watching = runtime.watch_file(path, lambda: changed.append(time.monotonic()))
        assert callable(stop_watching)

        def finish() -> bool:
            done = queued_on and changed and "slow" in fired
            late = time.monotonic() - state["replaced_at"] > 3.0
            if done or (state["replaced_at"] and late):
                runtime.stop()
                return False
            return True

        runtime.call_soon(begin)
        runtime.every(20, finish)
        # A last resort so that a broken runtime fails instead of hanging.
        watchdog = threading.Timer(10.0, lambda: runtime.call_soon(runtime.stop))
        watchdog.daemon = True
        watchdog.start()
        try:
            runtime.run_until_stopped()
            state["returned"] = True
        finally:
            watchdog.cancel()
            stop_watching()

    assert state["returned"]
    assert "cancelled" not in fired, "cancel() must stop a timer"
    assert "slow" in fired and fired.index("slow") > 1, "timers fire in interval order"
    assert queued_on == loop_thread, "call_soon from another thread runs on the loop thread"
    assert changed, "watch_file must report os.replace onto the watched path"
    assert changed[0] - state["replaced_at"] < 3.0
