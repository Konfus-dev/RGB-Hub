# SPDX-License-Identifier: GPL-3.0-or-later
"""The RAM lighting helper for Windows on the HP OMEN 45L.

The only part of the project that runs with full rights on Windows, so it is
kept small and uses the Python standard library only. Started at log-on by
the scheduled task setup made, as

    pythonw.exe -I helper\\rgbhub_ram_helper_windows.py --key-file PATH

It does what the Linux helper does: write one value to one lighting register
of one RAM stick by asking the BIOS, the way HP's own software does. Every
request goes through the Linux helper's own checks (rgbhub_ram_helper.py,
next to this file); only the way to the BIOS differs: HP's WMI method
hpqBIntM.hpqBIOSInt128, called from one long-lived PowerShell. The sticks
are never spoken to directly.

The hub (running with the owner's normal rights) reaches it over the
standard library's multiprocessing connection on 127.0.0.1, which only lets
in a caller that knows the key in the owner's key file:

    W <stick address> <register> <value>   (three hex bytes)   ->  OK | ERR ...
    PING                                                        ->  OK
    S                                                           ->  OK c2 c6  (fitted sticks)
"""

import argparse
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDRESS = ("127.0.0.1", 47231)
# Written by the installer from what Ubuntu saw, for when Windows cannot say.
STICKS_FILE = HERE / "ram-sticks.txt"


def _load_shared():
    # Started with -I, so this folder is not on sys.path: load by file.
    path = HERE / "rgbhub_ram_helper.py"
    spec = importlib.util.spec_from_file_location("rgbhub_ram_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()

MAX_LINE = shared.MAX_LINE
IDLE_S = 5.0  # a connected caller that says nothing for this long is dropped

# --- PowerShell -----------------------------------------------------------------------
#
# Everything said to PowerShell is in PS_FUNCTIONS, plus a function name from
# below followed by numbers this file formatted itself after the checks.
# Each function prints exactly one line: "RGBHUB " and its answer.
# If HP names the class or its fields differently on this BIOS, correct them
# here (Get-CimClass -Namespace root/wmi hpqB*).

ANSWER_MARK = "RGBHUB "
PS_WRITE = "RgbHubWrite"
PS_STICKS = "RgbHubSticks"
PS_MAKER = "RgbHubMaker"
PS_FUNCTIONS = (
    "$ErrorActionPreference = 'Stop'",
    "function RgbHubSay($text) { 'RGBHUB ' + ([string]$text -replace '\\s+', ' ').Trim() }",
    # The "SECU" request, command 0x20009, type 0x0A, data [register, value, address].
    "function RgbHubWrite([byte]$a, [byte]$r, [byte]$v) { try {"
    " if (-not $global:RgbHubBios) {"
    " $global:RgbHubIn = [wmiclass]'root\\wmi:hpqBDataIn';"
    " $global:RgbHubBios = Get-WmiObject -Namespace root\\wmi -Class hpqBIntM };"
    " $in = $global:RgbHubIn.CreateInstance();"
    " $in.Sign = [byte[]][char[]]'SECU';"
    " $in.Command = 0x20009;"
    " $in.CommandType = 0x0A;"
    " $in.Size = 3;"
    " $in.hpqBData = [byte[]]@($r, $v, $a);"
    " RgbHubSay $global:RgbHubBios.hpqBIOSInt128($in).OutData.rwReturnCode"
    " } catch { $global:RgbHubBios = $null; RgbHubSay ('ERR ' + $_.Exception.Message) } }",
    "function RgbHubSticks { try {"
    " RgbHubSay (ConvertTo-Json -Compress -InputObject"
    " @(Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, BankLabel))"
    " } catch { RgbHubSay ('ERR ' + $_.Exception.Message) } }",
    "function RgbHubMaker { try {"
    " RgbHubSay (Get-CimInstance Win32_ComputerSystem).Manufacturer"
    " } catch { RgbHubSay ('ERR ' + $_.Exception.Message) } }",
)


def powershell_command() -> list[str]:
    """Windows PowerShell by its full path (not whatever is first on PATH)."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return [str(exe), "-NoProfile", "-NonInteractive", "-Command", "-"]


class PowerShell:
    """One long-lived PowerShell, asked one line at a time.

    Started on first use and again if it has died; one that does not answer
    in time is stopped (and restarted on the next question).
    """

    def __init__(self, command: list[str] | None = None, timeout_s: float = 10.0):
        self._command = command
        self._timeout_s = timeout_s
        self._child = None
        self._lines: queue.Queue = queue.Queue()

    def _start(self) -> None:
        self.close()
        self._lines = queue.Queue()
        self._child = subprocess.Popen(
            self._command or powershell_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(
            target=self._read, args=(self._child.stdout, self._lines), daemon=True
        ).start()
        for line in PS_FUNCTIONS:
            self._child.stdin.write(line + "\n")
        self._child.stdin.flush()

    @staticmethod
    def _read(stream, lines: queue.Queue) -> None:
        for line in stream:
            lines.put(line)
        lines.put(None)  # the child has gone

    def ask(self, line: str, timeout_s: float | None = None) -> str:
        """Send one line; return what follows "RGBHUB " in the answer, or raise OSError."""
        if self._child is None or self._child.poll() is not None:
            self._start()
        try:
            self._child.stdin.write(line + "\n")
            self._child.stdin.flush()
        except OSError as error:
            self.close()
            raise OSError(f"PowerShell went away: {error}") from None
        deadline = time.monotonic() + (timeout_s or self._timeout_s)
        while True:
            try:
                answer = self._lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise OSError("PowerShell did not answer in time") from None
            if answer is None:
                self.close()
                raise OSError("PowerShell went away")
            if answer.startswith(ANSWER_MARK):
                return answer[len(ANSWER_MARK) :].strip()

    def close(self) -> None:
        child, self._child = self._child, None
        if child is not None:
            child.kill()
            child.wait()
            for stream in (child.stdin, child.stdout):
                try:
                    stream.close()
                except OSError:
                    pass


_shell: PowerShell | None = None


def _default_shell() -> PowerShell:
    global _shell
    if _shell is None:
        _shell = PowerShell()
    return _shell


def wmi_deliver(address: int, register: int, value: int, shell=None) -> bool:
    """Write one value through HP's BIOS WMI method; True if the BIOS said OK.

    The request is checked against the shared lists again here (ValueError
    if it is not allowed), so nothing reaches PowerShell without them.
    """
    address, register, value = shared.parse_request(f"W {address:02x} {register:02x} {value:02x}")
    line = f"{PS_WRITE} {address:#04x} {register:#04x} {value:#04x}"
    reply = (shell or _default_shell()).ask(line)
    return reply == "0"


# --- which sticks are fitted ----------------------------------------------------------

# Slot of each (channel, DIMM) on this board, for "ChannelA-DIMM2" style names.
CHANNEL_SLOTS = {"A1": 0, "A2": 1, "B1": 2, "B2": 3}
_CHANNEL = re.compile(r"Channel([A-Z])-DIMM(\d)\s*$", re.IGNORECASE)
_DIMM = re.compile(r"^\s*DIMM\s*([1-4])\s*$", re.IGNORECASE)


def _slot(text) -> int | None:
    if not isinstance(text, str):
        return None
    if match := _CHANNEL.search(text):
        return CHANNEL_SLOTS.get(match[1].upper() + match[2])
    if match := _DIMM.match(text):
        return int(match[1]) - 1
    return None


def sticks_from_cim(rows: list) -> list[int]:
    """Stick addresses from Win32_PhysicalMemory rows (DeviceLocator, BankLabel).

    [] if any row cannot be placed in a slot: better no RAM lights than
    lights sent to the wrong slot.
    """
    slots = []
    for row in rows:
        if not isinstance(row, dict):
            return []
        slot = _slot(row.get("DeviceLocator"))
        if slot is None:
            slot = _slot(row.get("BankLabel"))
        if slot is None or slot in slots:
            return []
        slots.append(slot)
    return sorted(shared.ALLOWED_ADDRESSES[slot] for slot in slots)


def sticks_from_file(path: Path = STICKS_FILE) -> list[int]:
    """Stick addresses from the installer's file ("c2 c6"); [] if missing or odd."""
    try:
        fields = path.read_text(encoding="ascii").split()
        sticks = sorted({int(field, 16) for field in fields})
    except (OSError, ValueError):
        return []
    if not set(sticks) <= set(shared.ALLOWED_ADDRESSES):
        return []
    return sticks


def find_sticks(shell, sticks_file: Path = STICKS_FILE) -> list[int]:
    """The fitted sticks, as Windows reports them, else from the installer's file."""
    try:
        rows = json.loads(shell.ask(PS_STICKS))
    except (OSError, ValueError):
        rows = []
    if isinstance(rows, dict):  # PowerShell gives a lone row on its own
        rows = [rows]
    sticks = sticks_from_cim(rows) if isinstance(rows, list) else []
    return sticks or sticks_from_file(sticks_file)


# --- answering the hub ------------------------------------------------------------------


def make_answer(sticks: list[int], deliver, ready=lambda: True, sleep=None):
    """A function from one request line to its reply line."""
    fitted = frozenset(sticks)

    def answer(line: str) -> str:
        if line.strip().upper() == "S":
            return " ".join(["OK", *(f"{address:02x}" for address in sorted(fitted))])
        return shared.handle_line(
            line, present=fitted.__contains__, deliver=deliver, ready=ready, sleep=sleep
        )

    return answer


def serve(listener, answer, stopped=lambda: False) -> None:
    """Answer requests, one connection at a time, until stopped."""
    while not stopped():
        try:
            connection = listener.accept()
        except (AuthenticationError, EOFError, ConnectionError):
            continue  # a caller without the key, or one that hung up
        except OSError:
            if stopped():
                return
            time.sleep(0.1)
            continue
        with connection:
            try:
                while connection.poll(IDLE_S):
                    line = connection.recv_bytes(MAX_LINE).decode("ascii", "replace")
                    connection.send_bytes(answer(line).encode("ascii", "replace"))
            except (OSError, EOFError):
                continue  # went away, or sent something too long


def main(argv: list[str] | None = None, shell=None) -> int:
    parser = argparse.ArgumentParser(description="RGB-Hub RAM lighting helper (Windows)")
    parser.add_argument("--key-file", required=True, type=Path)
    args = parser.parse_args(argv)
    shell = shell or _default_shell()
    try:
        maker = shell.ask(PS_MAKER)
    except OSError as error:
        sys.exit(f"rgbhub RAM helper: cannot ask Windows what this machine is: {error}")
    if "QEMU" in maker.upper():
        return 0  # Windows in a virtual machine: there is no RAM of ours to light
    key = args.key_file.read_bytes()
    if len(key) < 16:
        sys.exit(f"rgbhub RAM helper: the key in {args.key_file} is too short")
    sticks = find_sticks(shell)

    def deliver(address: int, register: int, value: int) -> bool:
        return wmi_deliver(address, register, value, shell=shell)

    with Listener(ADDRESS, authkey=key) as listener:
        serve(listener, make_answer(sticks, deliver))
    return 0


if __name__ == "__main__":
    sys.exit(main())
