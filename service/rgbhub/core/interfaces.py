# SPDX-License-Identifier: GPL-3.0-or-later
"""The seams between the core and everything system-specific.

The core (this folder) decides what the LCD and lights should show. It never
imports an operating system's or a hardware library's modules; it is handed
objects that fit the shapes described here. A platform (../platforms) picks
the implementations for one system. To support a new system, write that one
file; nothing in the core changes.

Shapes described elsewhere, next to the code that uses them:
- a lighting client (core/lights.py): `devices`, `update()`, `disconnect()`,
  optionally `save()`; each device has `name`, `modes`, `zones`,
  `set_mode()`, `set_colors()`, optionally `forget()`. OpenRGB's client fits
  as it is; backends/tracer.py and backends/via.py are two more.
- an LCD port (core/lcd_streamer.py): `write(bytes)` and `close()`.
- a RAM sender (core/ram.py): a function taking a list of register writes.

Shapes described here:
- Settings, Scheduler (and ManualScheduler for tests), Runtime: the front end's
  choices, timers, and the event loop that drives them.
- HidBus and HidPort (with HidMatch): finding and talking to a USB device.
  FakeHidBus and FakeHidPort are in-memory versions for tests.
- RamBus: sending register writes (Command) to the RAM sticks.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# watch_file(path, on_change) starts watching one file and returns a
# function that stops watching.
WatchFile = Callable[[str, Callable[[], None]], Callable[[], None]]

Command = tuple[int, int, int]  # (stick address, register, value)


@dataclass(frozen=True)
class HidMatch:
    """Which device to look for. Leave `usage_page` and `usage` as None to
    accept any interface of that vendor and product."""

    vendor: int
    product: int
    usage_page: int | None = None
    usage: int | None = None


@runtime_checkable
class HidPort(Protocol):
    """One open USB device. Failures raise OSError."""

    def write(self, report: bytes) -> None: ...

    def read(self, timeout_s: float) -> bytes | None:
        """The next report, or None if none arrives in time."""

    def alive(self) -> bool:
        """False once the device has been unplugged."""

    def close(self) -> None: ...


@runtime_checkable
class HidBus(Protocol):
    """Finds and opens USB devices."""

    def find(self, match: HidMatch) -> str | None:
        """The path of a matching device that is plugged in, or None."""

    def open(self, path: str, nonblocking: bool = False) -> HidPort: ...


@runtime_checkable
class RamBus(Protocol):
    """Reaches the RAM sticks' lighting, one way or another."""

    def send(self, batch: list[Command]) -> None: ...

    def sticks(self) -> list[int]:
        """The addresses of the sticks that are there."""


class Settings(Protocol):
    """Where the front end's choices live, and where status is reported.

    Keys and their defaults are listed in settings_schema.py. Values are
    plain Python: bool, int, str, list of str, or dict.
    """

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...

    def subscribe(self, callback: Callable[[str], None]) -> None:
        """Call `callback(key)` whenever a setting changes, whoever changed it."""

    def unsubscribe(self, callback: Callable[[str], None]) -> None: ...


class Scheduler(Protocol):
    """Runs timers on the program's event loop."""

    def every(self, interval_ms: int, callback: Callable[[], bool]) -> Any:
        """Call `callback` every `interval_ms` until it returns False.
        Returns a handle for cancel()."""

    def cancel(self, handle: Any) -> None: ...


class ManualScheduler:
    """A Scheduler that never fires by itself. Used in tests, where each
    tick is called directly, and as the default when none is given."""

    def __init__(self):
        self.timers: dict[int, tuple[int, Callable[[], bool]]] = {}
        self._next = 1

    def every(self, interval_ms: int, callback: Callable[[], bool]) -> int:
        handle, self._next = self._next, self._next + 1
        self.timers[handle] = (interval_ms, callback)
        return handle

    def cancel(self, handle: int) -> None:
        self.timers.pop(handle, None)


class Runtime(Scheduler, Protocol):
    """The program's event loop: timers, file watching, and running until told to stop."""

    def watch_file(self, path: str, on_change: Callable[[], None]) -> Callable[[], None]:
        """Call `on_change` when the file changes. Returns a function that stops watching."""

    def call_soon(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the loop. Safe to call from any thread."""

    def run_until_stopped(self) -> None: ...

    def stop(self) -> None: ...


class FakeHidPort:
    """An in-memory HidPort. Put replies in `replies`; reports written show up in `written`."""

    def __init__(self):
        self.replies: list[bytes] = []
        self.written: list[bytes] = []
        self.plugged = True

    def write(self, report: bytes) -> None:
        if not self.plugged:
            raise OSError("device unplugged")
        self.written.append(report)

    def read(self, timeout_s: float) -> bytes | None:
        if not self.plugged:
            raise OSError("device unplugged")
        return self.replies.pop(0) if self.replies else None

    def alive(self) -> bool:
        return self.plugged

    def close(self) -> None:
        pass


class FakeHidBus:
    """An in-memory HidBus over a fixed set of devices, keyed by path."""

    def __init__(self, devices: dict[str, tuple[HidMatch, FakeHidPort]]):
        self.devices = devices

    def find(self, match: HidMatch) -> str | None:
        for path, (own, port) in self.devices.items():
            if (
                port.plugged
                and own.vendor == match.vendor
                and own.product == match.product
                and match.usage_page in (None, own.usage_page)
                and match.usage in (None, own.usage)
            ):
                return path
        return None

    def open(self, path: str, nonblocking: bool = False) -> FakeHidPort:
        if path not in self.devices or not self.devices[path][1].plugged:
            raise OSError(f"no device at {path}")
        return self.devices[path][1]

    def unplug(self, path: str) -> None:
        self.devices[path][1].plugged = False
