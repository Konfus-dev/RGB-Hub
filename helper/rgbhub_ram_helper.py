#!/usr/bin/python3 -I
# SPDX-License-Identifier: GPL-3.0-or-later
"""Root helper for RAM lighting on the HP OMEN 45L.

This is the only part of the project that runs as root, so it is kept small
and uses the Python standard library only. It does exactly one thing: write
one value to one lighting register of one RAM stick, by asking the BIOS to
do it (the same way HP's own software does). Every request is checked
against the fixed lists below; anything else is refused.

Requests arrive as text lines on a socket that systemd creates and that only
the installing user may open:

    W <stick address> <register> <value>     (three hex bytes)  ->  OK | ERR ...
    PING                                                         ->  OK

Needs the acpi_call kernel module (Ubuntu package acpi-call-dkms).

The checks (parse_request, handle_line) are shared with the Windows helper
(rgbhub_ram_helper_windows.py), which hands the same request to the BIOS
through WMI instead of acpi_call: handle_line takes the way of delivering a
checked write as `deliver`, so there is still one place that decides what
may be sent.
"""

import socket
import sys
import time
from pathlib import Path

ACPI_CALL = Path("/proc/acpi/call")
I2C_DEVICES = Path("/sys/bus/i2c/devices")
SPD_DRIVER_NAME = "spd5118"  # the kernel's name for a DDR5 stick's info chip

# The BIOS method HP's software uses, and the fixed start of its argument:
# "SECU", command 0x20009, type 0x0A, three data bytes follow.
ACPI_METHOD = "\\_SB.WMID.WMAA 0x0 0x2"
REQUEST_HEAD = "53454355090002000a00000003000000"
REPLY_OK = "{0x50, 0x41, 0x53, 0x53, 0x00, 0x00, 0x00, 0x00"  # "PASS", return code 0

# Stick addresses as HP writes them: 0xC0 + 2 * slot, for slots 0..3.
ALLOWED_ADDRESSES = (0xC0, 0xC2, 0xC4, 0xC6)

REG_TRANSFER = 0x08
TRANSFER_BEGIN, TRANSFER_END = 0x53, 0x44

# Lighting register -> the values it may take. Nothing else can be written.
ALLOWED_REGISTERS: dict[int, range | tuple] = {
    REG_TRANSFER: (TRANSFER_BEGIN, TRANSFER_END),
    0x09: range(0x00, 0x14),  # effect
    0x0B: range(0, 4),  # position of this stick among the sticks
    0x0C: range(1, 3),  # direction
    0x0D: range(0, 33),  # delay
    0x0E: range(0, 256),  # speed
    **{reg: range(0, 256) for reg in range(0x12, 0x1B)},  # effect timings
    0x1B: range(0, 101),  # breathing: brightest
    0x1C: range(0, 101),  # breathing: middle
    0x1D: range(0, 101),  # breathing: dimmest
    0x20: range(0, 101),  # brightness
    0x23: range(0, 256),  # background red
    0x24: range(0, 256),  # background green
    0x25: range(0, 256),  # background blue
    0x26: range(0, 33),  # effect length
    0x27: range(1, 5),  # how many sticks
    0x30: range(1, 11),  # how many effect colours
    **{reg: range(0, 256) for reg in range(0x31, 0x4F)},  # effect colours
    **{reg: range(0, 256) for reg in range(0x50, 0x74)},  # one colour per LED
}

MAX_LINE = 64  # the longest valid request is 10 characters
PAUSE_AFTER_WRITE_S = 0.005  # HP waits 5 ms after an ordinary write
PAUSE_AFTER_TRANSFER_S = 0.05  # and 50 ms after begin / end


def parse_request(line: str) -> tuple[int, int, int]:
    """Return (address, register, value) or raise ValueError."""
    fields = line.split()
    if len(fields) != 4 or fields[0].upper() != "W":
        raise ValueError("expected: W <address> <register> <value>")
    try:
        address, register, value = (int(field, 16) for field in fields[1:])
    except ValueError:
        raise ValueError("address, register and value must be hex bytes") from None
    if any(len(field) > 2 for field in fields[1:]):
        raise ValueError("address, register and value must be hex bytes")
    if address not in ALLOWED_ADDRESSES:
        raise ValueError("not a RAM stick address")
    if register not in ALLOWED_REGISTERS:
        raise ValueError("not a lighting register")
    if value not in ALLOWED_REGISTERS[register]:
        raise ValueError("value not allowed for this register")
    return address, register, value


def stick_present(address: int, i2c_devices: Path = I2C_DEVICES) -> bool:
    """True if the kernel sees a DDR5 stick in the slot `address` refers to."""
    slot = (address - ALLOWED_ADDRESSES[0]) // 2
    for node in i2c_devices.glob(f"*-00{0x50 + slot:02x}"):
        try:
            if (node / "name").read_text().strip() == SPD_DRIVER_NAME:
                return True
        except OSError:
            continue
    return False


def acpi_command(address: int, register: int, value: int) -> str:
    return f"{ACPI_METHOD} b{REQUEST_HEAD}{register:02x}{value:02x}{address:02x}"


def reply_ok(reply: str) -> bool:
    return reply.startswith(REPLY_OK)


def call_bios(command: str) -> str:
    """Hand one command to the BIOS through acpi_call and return its answer."""
    ACPI_CALL.write_text(command)
    return ACPI_CALL.read_text().strip("\0\n ")


def acpi_deliver(address: int, register: int, value: int, call=None) -> bool:
    """Write one checked value through acpi_call; True if the BIOS accepted it."""
    return reply_ok((call or call_bios)(acpi_command(address, register, value)))


def handle_line(
    line: str,
    call=None,
    i2c_devices: Path = I2C_DEVICES,
    sleep=None,
    present=None,
    ready=None,
    deliver=None,
) -> str:
    """Process one request line and return the reply line.

    `present(address)` says whether a stick is fitted at that address; by
    default the kernel's list of sticks is consulted. `deliver(address,
    register, value)` hands a request that passed every check to the BIOS
    and says whether it was accepted; by default that is acpi_deliver.
    """
    call = call or call_bios
    sleep = sleep or time.sleep
    if line.strip().upper() == "PING":
        # "OK" must mean writes can work. Without the acpi_call module (a
        # kernel update it was not rebuilt for, say) every write would fail,
        # and the RAM would seem to connect and drop over and over.
        if not (ready() if ready else ACPI_CALL.exists()):
            return "ERR the acpi_call kernel module is not loaded"
        return "OK"
    try:
        address, register, value = parse_request(line)
    except ValueError as error:
        return f"ERR {error}"
    fitted = present(address) if present else stick_present(address, i2c_devices)
    if not fitted:
        return "ERR no RAM stick in that slot"
    try:
        if deliver is None:
            accepted = acpi_deliver(address, register, value, call)
        else:
            accepted = deliver(address, register, value)
    except OSError as error:
        hint = "; is acpi_call loaded?" if deliver is None else ""
        return f"ERR cannot reach the BIOS ({error}){hint}"
    if not accepted:
        return "ERR the BIOS refused the command"
    sleep(PAUSE_AFTER_TRANSFER_S if register == REG_TRANSFER else PAUSE_AFTER_WRITE_S)
    return "OK"


def serve(listener: socket.socket) -> None:
    """Answer requests, one connection at a time, until killed."""
    while True:
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(5)
            try:
                with connection.makefile("rw", encoding="ascii", errors="replace") as stream:
                    # readline(limit) so a client cannot make us hold an
                    # endless line in memory; an over-long line is refused.
                    while line := stream.readline(MAX_LINE):
                        if not line.endswith("\n"):
                            stream.write("ERR line too long\n")
                            break
                        stream.write(handle_line(line) + "\n")
                        stream.flush()
            except OSError:
                continue  # client went away or was too slow; wait for the next


if __name__ == "__main__":
    # systemd passes the listening socket as file descriptor 3.
    try:
        serve(socket.socket(fileno=3))
    except OSError as error:
        sys.exit(f"rgbhub-ram-helper must be started by systemd socket activation: {error}")
