# SPDX-License-Identifier: GPL-3.0-or-later
"""The event loop, timers and file watching, done with GLib.

GLib ships with every Linux desktop and with Batocera, and also runs on
Windows, so every current platform shares this file.
"""

import logging
import signal
from collections.abc import Callable

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

_CHANGE_EVENTS = (
    Gio.FileMonitorEvent.CHANGES_DONE_HINT,
    Gio.FileMonitorEvent.CREATED,
)


class GLibRuntime:
    """The event loop on GLib (fits core.interfaces.Runtime)."""

    def __init__(self) -> None:
        self._loop = GLib.MainLoop()

    def every(self, interval_ms: int, callback: Callable[[], bool]) -> int:
        return GLib.timeout_add(interval_ms, callback)

    def cancel(self, handle: int) -> None:
        # A timer that already ended by returning False is gone; removing it
        # again would only produce a warning.
        if GLib.MainContext.default().find_source_by_id(handle) is not None:
            GLib.source_remove(handle)

    def watch_file(self, path: str, on_change: Callable[[], None]) -> Callable[[], None]:
        """Call `on_change` whenever `path` is written or replaced.
        Returns a function that stops watching."""
        try:
            monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error as error:
            log.debug("Cannot watch %s: %s", path, error)
            return lambda: None

        def on_event(_monitor, _file, _other, event) -> None:
            # CHANGES_DONE_HINT arrives once the program saving the file is
            # done; CREATED is also reported when a save by "write a temporary file,
            # then os.replace it onto the path" swaps the file in.
            if event in _CHANGE_EVENTS:
                on_change()

        monitor.connect("changed", on_event)
        return monitor.cancel

    def call_soon(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the loop. Safe to call from any thread."""

        def run() -> bool:
            try:
                fn()
            except Exception:
                log.exception("Unexpected error in a queued call; carrying on")
            return False  # once only

        GLib.idle_add(run)

    def run_until_stopped(self) -> None:
        """Run the loop until stop() or SIGINT/SIGTERM."""
        for signum in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._loop.quit)
        self._loop.run()

    def stop(self) -> None:
        self._loop.quit()


def on_signal(signum: int, callback: Callable[[], None]) -> None:
    """Call `callback` on the event loop each time the process gets `signum`."""

    def handler() -> bool:
        try:
            callback()
        except Exception:
            log.exception("Unexpected error handling signal %s; carrying on", signum)
        return True  # keep listening

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, handler)
