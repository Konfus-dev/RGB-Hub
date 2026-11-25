# SPDX-License-Identifier: GPL-3.0-or-later
"""RAM lighting on Windows: through the small helper that runs with full rights.

The sticks are never spoken to directly (on the OMEN 45L that jams the RAM's
control bus until the next reboot). The hub runs with the owner's normal
rights and hands each write to the helper (helper/rgbhub_ram_helper_windows.py),
which checks it against the fixed lists and passes it to HP's BIOS.

The two talk over the standard library's multiprocessing connection on
127.0.0.1, with a key only the owner can read: a caller without the key is
turned away before it can say anything.
"""

import os
import socket
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Connection, answer_challenge, deliver_challenge
from pathlib import Path

from ...core.interfaces import Command

HELPER_ADDRESS = ("127.0.0.1", 47231)
# The helper normally answers within a tenth of a second. Waiting longer
# would hold up the rest of the service, which has an LCD to keep fed.
HELPER_TIMEOUT_S = 1.0
STICKS_CACHE_S = 60.0  # sticks do not come and go while Windows runs
MAX_REPLY = 256
STICK_ADDRESSES = (0xC0, 0xC2, 0xC4, 0xC6)


def default_key_file() -> Path:
    """%LOCALAPPDATA%\\rgbhub\\ram-key, the key setup made for the owner."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "rgbhub" / "ram-key"


class TcpRamBus:
    """RAM writes through the Windows helper. Fits core.interfaces.RamBus."""

    def __init__(
        self,
        address: tuple[str, int] = HELPER_ADDRESS,
        key_file: Path | str | None = None,
        timeout_s: float = HELPER_TIMEOUT_S,
        clock=time.monotonic,
    ):
        self._address = address
        self._key_file = Path(key_file) if key_file else None
        self._timeout_s = timeout_s
        self._clock = clock
        self._sticks: list[int] = []
        self._sticks_until = 0.0

    def _connect(self) -> Connection:
        """A connection the helper has let in. OSError if it cannot be made in time."""
        key = (self._key_file or default_key_file()).read_bytes()
        # As multiprocessing.connection.Client does it, but never waiting
        # longer than timeout_s: a busy or stuck helper must not hold us up.
        with socket.create_connection(self._address, timeout=self._timeout_s) as sock:
            sock.setblocking(True)
            connection = Connection(sock.detach())
        try:
            if not connection.poll(self._timeout_s):
                raise OSError("the RAM helper did not answer")
            answer_challenge(connection, key)
            deliver_challenge(connection, key)
        except (AuthenticationError, EOFError) as error:
            connection.close()
            raise OSError(f"the RAM helper turned us away: {error}") from None
        except OSError:
            connection.close()
            raise
        return connection

    def _ask(self, connection: Connection, line: str) -> str:
        connection.send_bytes(line.encode("ascii"))
        if not connection.poll(self._timeout_s):
            raise OSError(f"RAM helper did not answer '{line}'")
        try:
            return connection.recv_bytes(MAX_REPLY).decode("ascii", "replace").strip()
        except EOFError:
            raise OSError(f"RAM helper went away after '{line}'") from None

    def send(self, batch: list[Command]) -> None:
        """Send commands to the helper. Raises OSError if anything fails.

        An empty batch just checks that the helper answers.
        """
        lines = [f"W {a:02x} {r:02x} {v:02x}" for a, r, v in batch] or ["PING"]
        with self._connect() as connection:
            for line in lines:
                reply = self._ask(connection, line)
                if reply != "OK":
                    raise OSError(f"RAM helper refused '{line}': {reply or 'no answer'}")

    def sticks(self) -> list[int]:
        """The fitted sticks as the helper sees them; [] if it cannot be reached."""
        if self._clock() < self._sticks_until:
            return list(self._sticks)
        try:
            with self._connect() as connection:
                reply = self._ask(connection, "S")
        except OSError:
            return []
        fields = reply.split()
        if fields[:1] != ["OK"]:
            return []
        try:
            sticks = sorted({int(field, 16) for field in fields[1:]})
        except ValueError:
            return []
        if not set(sticks) <= set(STICK_ADDRESSES):
            return []
        self._sticks, self._sticks_until = sticks, self._clock() + STICKS_CACHE_S
        return list(sticks)
