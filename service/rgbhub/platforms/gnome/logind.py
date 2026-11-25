# SPDX-License-Identifier: GPL-3.0-or-later
"""Sleep and wake notifications from systemd-logind (most Linux desktops)."""

import logging
import os
from collections.abc import Callable

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

RESUME_SETTLE_S = 3  # give USB devices a moment to come back after sleep


def _safely(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        log.exception("Unexpected error while handling sleep or wake")


def watch_for_sleep(
    on_sleep: Callable[[], None], on_resume: Callable[[], None]
) -> Gio.DBusConnection | None:
    """Call `on_sleep` just before the computer sleeps and `on_resume` a
    moment after it wakes.

    The caller must keep the returned connection for as long as it wants the
    notifications: GLib drops the connection, and the subscription with it,
    once nothing refers to it any more.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error as error:
        log.warning("Cannot watch for sleep/resume: %s", error)
        return None

    # While we hold this "delay" lock, the system waits (a few seconds at
    # most) before sleeping, which gives us time to switch the lights off.
    lock: list[int] = []  # the lock's file descriptor, when held

    def take_lock() -> None:
        try:
            reply, fds = bus.call_with_unix_fd_list_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "Inhibit",
                GLib.Variant(
                    "(ssss)",
                    ("sleep", "RGB Hub", "Switch the lights off before sleep", "delay"),
                ),
                GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )
            lock.append(fds.get(reply.unpack()[0]))
        except GLib.Error as error:
            log.warning("Cannot delay sleep; lights may stay on: %s", error)

    def release_lock() -> None:
        while lock:
            os.close(lock.pop())

    def woke_up() -> bool:
        _safely(on_resume)
        return False  # one-shot timer

    def on_signal(_bus, _sender, _path, _iface, _signal, parameters) -> None:
        (going_to_sleep,) = parameters.unpack()
        if going_to_sleep:
            _safely(on_sleep)
            release_lock()  # done: the system may sleep now
        else:
            take_lock()
            GLib.timeout_add_seconds(RESUME_SETTLE_S, woke_up)

    bus.signal_subscribe(
        "org.freedesktop.login1",
        "org.freedesktop.login1.Manager",
        "PrepareForSleep",
        "/org/freedesktop/login1",
        None,
        Gio.DBusSignalFlags.NONE,
        on_signal,
    )
    take_lock()
    return bus
