# SPDX-License-Identifier: GPL-3.0-or-later
"""Assertions every implementation of a core interface must satisfy.

A test for a real implementation (or for the in-memory fake) calls the
matching `check_*` function with whatever it needs to set the scene.
"""

import os
import tempfile
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
    """A runtime runs timers and queued calls, reports file changes, and stops on request."""
    runtime = runtime_factory()
    log: list[str] = []
    ticks = []

    def tick() -> bool:
        ticks.append(1)
        if len(ticks) == 2:
            runtime.stop()
            return False
        return True

    runtime.call_soon(lambda: log.append("soon"))
    runtime.every(10, tick)
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "watched")
        open(path, "w").close()
        stop_watching = runtime.watch_file(path, lambda: log.append("changed"))
        assert callable(stop_watching)
        runtime.run_until_stopped()
        stop_watching()
    assert log[0] == "soon"
    assert len(ticks) == 2
