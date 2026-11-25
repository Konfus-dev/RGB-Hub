# SPDX-License-Identifier: GPL-3.0-or-later
"""Delivering RAM lighting writes through the BIOS, and finding the sticks.

The sticks are never spoken to directly over SMBus: on the OMEN 45L that
jams the RAM's control bus until the next reboot. Every write is handed to
the BIOS, the way HP's own software does it. Only root can do that, so an
ordinary user's service goes through the small root helper
(helper/rgbhub_ram_helper.py).
"""

import socket
import subprocess
from pathlib import Path

from ..core.interfaces import Command

HELPER_SOCKET = "/run/rgbhub-ram.sock"
# The helper normally answers within a tenth of a second. Waiting longer
# would hold up the rest of the service, which has an LCD to keep fed.
HELPER_TIMEOUT_S = 1.0

I2C_DEVICES = Path("/sys/bus/i2c/devices")
SPD_DRIVER_NAME = "spd5118"  # the kernel's name for a DDR5 stick's info chip


def find_sticks(i2c_devices: Path = I2C_DEVICES) -> list[int]:
    """Addresses of the sticks that are fitted, lowest slot first.

    A stick in slot N has its info chip at 0x50 + N and is addressed for
    lighting as 0xC0 + 2 * N.
    """
    sticks = []
    for slot in range(4):
        for node in i2c_devices.glob(f"*-00{0x50 + slot:02x}"):
            try:
                if (node / "name").read_text().strip() == SPD_DRIVER_NAME:
                    sticks.append(0xC0 + 2 * slot)
                    break
            except OSError:
                continue
    return sticks


def send_to_helper(batch: list[Command], path: str = HELPER_SOCKET) -> None:
    """Send commands to the root helper. Raises OSError if anything fails.

    An empty batch just checks that the helper answers.
    """
    lines = [f"W {a:02x} {r:02x} {v:02x}" for a, r, v in batch] or ["PING"]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(HELPER_TIMEOUT_S)
        connection.connect(path)
        with connection.makefile("rw", encoding="ascii") as stream:
            for line in lines:
                stream.write(line + "\n")
                stream.flush()
                reply = stream.readline().strip()
                if reply != "OK":
                    raise OSError(f"RAM helper refused '{line}': {reply or 'no answer'}")


def sticks_from_dmidecode(text: str) -> list[int]:
    """Stick addresses from the output of `dmidecode -t 17`.

    The Nth "Memory Device" block is slot N; a slot with a size is fitted.
    """
    sticks = []
    slot = -1
    for line in text.splitlines():
        line = line.strip()
        if line == "Memory Device":
            slot += 1
        elif line.startswith("Size:") and "No Module Installed" not in line and 0 <= slot < 4:
            sticks.append(0xC0 + 2 * slot)
    return sticks


def find_sticks_dmi(command: tuple[str, ...] | list[str] = ("dmidecode", "-t", "17")) -> list[int]:
    """Fitted sticks according to the BIOS's own tables (needs root).

    For systems whose kernel does not list the sticks (Batocera has no
    spd5118 driver). Returns [] if the tool is missing or fails.
    """
    try:
        result = subprocess.run(list(command), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return sticks_from_dmidecode(result.stdout)


def direct_sender(helper, find):
    """A RAM sender for a service that is itself root (Batocera).

    `helper` is the root helper's module (helper/rgbhub_ram_helper.py),
    loaded by the platform. Its checks on registers and values are used
    exactly as in the socket case, so there is still only one place that
    decides what may be sent. `find()` lists the fitted sticks, standing in
    for the kernel's list the helper would otherwise consult.
    """

    def send(batch: list[Command]) -> None:
        fitted = set(find())
        for address, register, value in batch:
            reply = helper.handle_line(
                f"W {address:02x} {register:02x} {value:02x}", present=fitted.__contains__
            )
            if reply != "OK":
                raise OSError(f"RAM write refused: {reply}")

    return send
