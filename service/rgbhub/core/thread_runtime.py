# SPDX-License-Identifier: GPL-3.0-or-later
"""The event loop, timers and file watching, done with the standard library.

For systems without GLib (Windows). It is the standard asyncio event loop;
timers are `call_later` chains and file watching polls `os.stat`.
"""

import asyncio
import logging
import os
import signal
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class _Timer:
    """One repeating timer; the handle returned by every()."""

    def __init__(
        self, loop: asyncio.AbstractEventLoop, interval_s: float, callback: Callable[[], bool]
    ):
        self._loop = loop
        self._interval_s = interval_s
        self._callback = callback
        self._cancelled = False
        self._handle = loop.call_later(interval_s, self._fire)

    def _fire(self) -> None:
        if self._cancelled:
            return
        try:
            keep = self._callback()
        except Exception:
            log.exception("Unexpected error in a timer; keeping it")
            keep = True
        if keep is False:
            self._cancelled = True
        elif not self._cancelled:
            self._handle = self._loop.call_later(self._interval_s, self._fire)

    def cancel(self) -> None:
        self._cancelled = True
        self._handle.cancel()


def _signature(path: str) -> tuple[int, int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size, info.st_ino)


class ThreadRuntime:
    """The event loop on asyncio (fits core.interfaces.Runtime)."""

    def __init__(self, poll_s: float = 1.0) -> None:
        self._poll_s = poll_s
        self._loop = asyncio.new_event_loop()

    def every(self, interval_ms: int, callback: Callable[[], bool]) -> _Timer:
        return _Timer(self._loop, interval_ms / 1000.0, callback)

    def cancel(self, handle: _Timer) -> None:
        handle.cancel()  # harmless on a timer that already ended

    def watch_file(self, path: str, on_change: Callable[[], None]) -> Callable[[], None]:
        """Call `on_change` whenever `path` is written, replaced or created.
        Returns a function that stops watching."""
        last = [_signature(path)]

        def poll() -> bool:
            now = _signature(path)
            changed = now is not None and now != last[0]
            last[0] = now
            if changed:
                on_change()  # exceptions are logged by the timer, which carries on
            return True

        timer = self.every(max(1, round(self._poll_s * 1000)), poll)
        return timer.cancel

    def call_soon(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the loop. Safe to call from any thread."""

        def run() -> None:
            try:
                fn()
            except Exception:
                log.exception("Unexpected error in a queued call; carrying on")

        self._loop.call_soon_threadsafe(run)

    def run_until_stopped(self) -> None:
        """Run the loop until stop() or SIGINT/SIGTERM."""
        asyncio.set_event_loop(self._loop)
        previous = self._install_signals()
        try:
            self._loop.run_forever()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def _install_signals(self) -> dict[int, object]:
        if threading.current_thread() is not threading.main_thread():
            return {}  # signal.signal only works on the main thread
        previous: dict[int, object] = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[signum] = signal.signal(signum, lambda *_: self.stop())
            except (ValueError, OSError):
                pass
        return previous

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
