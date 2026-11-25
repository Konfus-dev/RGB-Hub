# SPDX-License-Identifier: GPL-3.0-or-later
"""The Windows RAM helper (helper/rgbhub_ram_helper_windows.py) and the hub's
side of its connection (platforms/windows/ram.py).

The helper runs with full rights, so like the Linux one it is tested hard.
Everything Windows-only happens when it runs, not when it is imported, so
these tests run on Linux: PowerShell is stood in for by a small Python
child, and the connection is the same standard-library one on any system.
"""

import ast
import importlib.util
import sys
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener
from pathlib import Path

import pytest

from rgbhub.platforms.windows import ram as winram

HELPER = Path(__file__).resolve().parents[2] / "helper" / "rgbhub_ram_helper_windows.py"
spec = importlib.util.spec_from_file_location("rgbhub_ram_helper_windows", HELPER)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


# --- which sticks are fitted ----------------------------------------------------------


def test_sticks_from_cim_dimm_numbers():
    rows = [{"DeviceLocator": "DIMM2"}, {"DeviceLocator": "DIMM4"}]
    assert w.sticks_from_cim(rows) == [0xC2, 0xC6]


def test_sticks_from_cim_channel_style():
    rows = [
        {"DeviceLocator": "Controller1-ChannelB-DIMM2", "BankLabel": "BANK 0"},
        {"DeviceLocator": "Controller0-ChannelA-DIMM2", "BankLabel": "BANK 0"},
    ]
    assert w.sticks_from_cim(rows) == [0xC2, 0xC6]


def test_sticks_from_cim_uses_the_bank_label_when_the_locator_says_nothing():
    rows = [{"DeviceLocator": "", "BankLabel": "ChannelA-DIMM1"}]
    assert w.sticks_from_cim(rows) == [0xC0]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"DeviceLocator": "Bank 3"}],
        [{"DeviceLocator": "DIMM2"}, {"DeviceLocator": "somewhere"}],  # one unknown: trust none
        [{"DeviceLocator": "DIMM5"}],  # no fifth slot
        [{"DeviceLocator": "ChannelC-DIMM1"}],  # not on this board
        [{"DeviceLocator": None, "BankLabel": None}],
        ["DIMM2"],
        [{"DeviceLocator": "DIMM2"}, {"DeviceLocator": "DIMM2"}],  # the same slot twice
    ],
)
def test_sticks_from_cim_unknown_is_empty(rows):
    assert w.sticks_from_cim(rows) == []


def test_sticks_from_file(tmp_path):
    path = tmp_path / "ram-sticks.txt"
    path.write_text("c2 c6\n")
    assert w.sticks_from_file(path) == [0xC2, 0xC6]
    for bad in ("c2 a0", "c2 zz", "61", ""):
        path.write_text(bad)
        assert w.sticks_from_file(path) == []
    assert w.sticks_from_file(tmp_path / "missing.txt") == []


class FakeShell:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def ask(self, line, timeout_s=None):
        self.asked.append(line)
        answer = self.answers.get(line.split()[0], "")
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_find_sticks_asks_windows_then_falls_back_to_the_file(tmp_path):
    path = tmp_path / "ram-sticks.txt"
    path.write_text("c2 c6")
    cim = '[{"DeviceLocator":"DIMM1","BankLabel":"BANK 0"}]'
    assert w.find_sticks(FakeShell({w.PS_STICKS: cim}), path) == [0xC0]
    single = '{"DeviceLocator":"DIMM3","BankLabel":"BANK 0"}'  # one stick: not a list
    assert w.find_sticks(FakeShell({w.PS_STICKS: single}), path) == [0xC4]
    assert w.find_sticks(FakeShell({w.PS_STICKS: "ERR no CIM"}), path) == [0xC2, 0xC6]
    assert w.find_sticks(FakeShell({w.PS_STICKS: OSError("gone")}), path) == [0xC2, 0xC6]


# --- writes go through the BIOS's WMI method, after the shared checks -------------------


def test_wmi_deliver_asks_the_bios():
    shell = FakeShell({w.PS_WRITE: "0"})
    assert w.wmi_deliver(0xC2, 0x20, 50, shell=shell)
    assert shell.asked == [f"{w.PS_WRITE} 0xc2 0x20 0x32"]
    assert not w.wmi_deliver(0xC2, 0x20, 50, shell=FakeShell({w.PS_WRITE: "5"}))
    assert not w.wmi_deliver(0xC2, 0x20, 50, shell=FakeShell({w.PS_WRITE: "ERR no class"}))


def test_wmi_deliver_checks_again_and_refuses_anything_off_the_lists():
    shell = FakeShell({w.PS_WRITE: "0"})
    for request in ((0xA0, 0x20, 1), (0xC2, 0x06, 0), (0xC2, 0x20, 101), (0xC3, 0x20, 1)):
        with pytest.raises(ValueError):
            w.wmi_deliver(*request, shell=shell)
    assert shell.asked == []


def test_the_powershell_snippet_is_hps_lighting_call():
    script = "\n".join(w.PS_FUNCTIONS)
    assert all("\n" not in line for line in w.PS_FUNCTIONS)  # PowerShell reads one line at a time
    for part in (
        "root\\wmi:hpqBDataIn",
        "'SECU'",
        "0x20009",
        "0x0A",
        "Size = 3",
        "@($r, $v, $a)",
        "hpqBIntM",
        "hpqBIOSInt128",
        "rwReturnCode",
    ):
        assert part in script, part


def test_answer_line():
    sent = []

    def deliver(address, register, value):
        sent.append((address, register, value))
        return True

    answer = w.make_answer([0xC2, 0xC6], deliver, sleep=lambda s: None)
    assert answer("S") == "OK c2 c6"
    assert answer("PING") == "OK"
    assert answer("W c6 20 32\n") == "OK"
    assert answer("W c4 20 32") == "ERR no RAM stick in that slot"
    assert answer("W c2 06 00").startswith("ERR")
    assert sent == [(0xC6, 0x20, 0x32)]
    assert w.make_answer([], deliver)("S") == "OK"


def test_the_helper_refuses_to_start_in_a_virtual_machine(tmp_path):
    shell = FakeShell({w.PS_MAKER: "QEMU"})
    assert w.main(["--key-file", str(tmp_path / "no-key")], shell=shell) == 0
    assert [line.split()[0] for line in shell.asked] == [w.PS_MAKER]


# --- the PowerShell child --------------------------------------------------------------

FAKE_POWERSHELL = r"""
import sys
for line in sys.stdin:
    words = line.split()
    if not words or words[0] == "function" or words[0].startswith("$"):  # setup lines
        continue
    if words[0] == "Die":
        sys.exit(1)
    if words[0] == "Hang":
        continue
    print("chatter that is not an answer", flush=True)
    print("RGBHUB " + " ".join(words[1:]), flush=True)
"""


def fake_shell(tmp_path):
    script = tmp_path / "fake_powershell.py"
    script.write_text(FAKE_POWERSHELL)
    return w.PowerShell(command=[sys.executable, "-I", str(script)], timeout_s=5)


def test_powershell_child_answers_line_by_line(tmp_path):
    shell = fake_shell(tmp_path)
    try:
        assert shell.ask("Echo 0") == "0"
        assert shell.ask("Echo a b") == "a b"
    finally:
        shell.close()


def test_powershell_child_is_restarted_after_it_dies(tmp_path):
    shell = fake_shell(tmp_path)
    try:
        assert shell.ask("Echo 1") == "1"
        with pytest.raises(OSError):
            shell.ask("Die")
        assert shell.ask("Echo 2") == "2"
    finally:
        shell.close()


def test_powershell_child_that_hangs_is_an_error(tmp_path):
    shell = fake_shell(tmp_path)
    try:
        with pytest.raises(OSError):
            shell.ask("Hang", timeout_s=0.3)
        assert shell.ask("Echo 3") == "3"
    finally:
        shell.close()


# --- the helper's only code that may start a program, and nothing else ---------------


def test_the_windows_helper_imports_nothing_that_reaches_a_bus():
    tree = ast.parse(HELPER.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "argparse",
        "importlib",
        "json",
        "multiprocessing",
        "os",
        "pathlib",
        "queue",
        "re",
        "subprocess",
        "sys",
        "threading",
        "time",
    }, imported
    # It takes its checks from the Linux helper; it has none of its own to drift.
    assert "ALLOWED_REGISTERS =" not in HELPER.read_text()
    assert w.shared.ALLOWED_ADDRESSES == (0xC0, 0xC2, 0xC4, 0xC6)


# --- the connection between the hub and the helper --------------------------------------


def run_listener(listener, replies, seen):
    """A stand-in helper: one connection at a time, answering from `replies`."""
    while True:
        try:
            connection = listener.accept()
        except (AuthenticationError, EOFError, ConnectionError):
            continue  # a caller without the key, or one that hung up
        except OSError:
            return  # the listener was closed
        with connection:
            try:
                while True:
                    line = connection.recv_bytes(64).decode()
                    seen.append(line)
                    connection.send_bytes(replies.get(line, "OK").encode())
            except (EOFError, OSError):
                continue


@pytest.fixture
def helper(tmp_path):
    key = b"k" * 32
    key_file = tmp_path / "ram-key"
    key_file.write_bytes(key)
    listener = Listener(("127.0.0.1", 0), authkey=key)
    replies, seen = {}, []
    thread = threading.Thread(target=run_listener, args=(listener, replies, seen), daemon=True)
    thread.start()
    yield listener.address, key_file, replies, seen
    listener.close()


def test_tcp_ram_bus_line_protocol(helper):
    address, key_file, replies, seen = helper
    replies["S"] = "OK c2 c6"
    bus = winram.TcpRamBus(address=address, key_file=key_file)
    assert bus.sticks() == [0xC2, 0xC6]
    bus.send([(0xC2, 0x20, 0x32), (0xC6, 0x08, 0x53)])
    bus.send([])
    assert seen == ["S", "W c2 20 32", "W c6 08 53", "PING"]


def test_tcp_ram_bus_raises_when_the_helper_refuses(helper):
    address, key_file, replies, seen = helper
    replies["W c2 20 32"] = "ERR the BIOS refused the command"
    with pytest.raises(OSError, match="refused"):
        winram.TcpRamBus(address=address, key_file=key_file).send([(0xC2, 0x20, 0x32)])


def test_tcp_ram_bus_caches_the_sticks_for_a_minute(helper):
    address, key_file, replies, seen = helper
    replies["S"] = "OK c2"
    now = [0.0]
    bus = winram.TcpRamBus(address=address, key_file=key_file, clock=lambda: now[0])
    assert bus.sticks() == [0xC2]
    now[0] = 59.0
    assert bus.sticks() == [0xC2]
    now[0] = 61.0
    replies["S"] = "OK c2 c4"
    assert bus.sticks() == [0xC2, 0xC4]
    assert seen.count("S") == 2


def test_tcp_ram_bus_with_the_wrong_key_is_an_os_error(helper, tmp_path):
    address, _key_file, _replies, seen = helper
    wrong = tmp_path / "wrong-key"
    wrong.write_bytes(b"x" * 32)
    bus = winram.TcpRamBus(address=address, key_file=wrong)
    with pytest.raises(OSError):
        bus.send([])
    assert bus.sticks() == []  # sticks() never raises: the hub just tries again later
    assert seen == []


def test_tcp_ram_bus_without_a_helper_is_an_os_error(tmp_path):
    key_file = tmp_path / "ram-key"
    key_file.write_bytes(b"k" * 32)
    listener = Listener(("127.0.0.1", 0))
    address = listener.address
    listener.close()  # nothing listens there now
    bus = winram.TcpRamBus(address=address, key_file=key_file)
    with pytest.raises(OSError):
        bus.send([])
    with pytest.raises(OSError):
        winram.TcpRamBus(address=address, key_file=tmp_path / "no-key").send([])


def test_tcp_ram_bus_gives_up_on_a_helper_that_never_answers(tmp_path):
    key_file = tmp_path / "ram-key"
    key_file.write_bytes(b"k" * 32)
    listener = Listener(("127.0.0.1", 0), authkey=b"k" * 32)  # never accepts: busy helper
    try:
        bus = winram.TcpRamBus(address=listener.address, key_file=key_file, timeout_s=0.2)
        with pytest.raises(OSError):
            bus.send([])
    finally:
        listener.close()


def test_the_real_helper_behind_the_connection(tmp_path):
    """Hub -> connection -> the helper's own serving loop -> shared checks -> deliver."""
    key = b"s" * 32
    key_file = tmp_path / "ram-key"
    key_file.write_bytes(key)
    delivered = []

    def deliver(address, register, value):
        delivered.append((address, register, value))
        return True

    listener = Listener(("127.0.0.1", 0), authkey=key)
    stop = threading.Event()
    answer = w.make_answer([0xC2, 0xC6], deliver, sleep=lambda s: None)
    thread = threading.Thread(target=w.serve, args=(listener, answer, stop.is_set), daemon=True)
    thread.start()
    try:
        bus = winram.TcpRamBus(address=listener.address, key_file=key_file)
        assert bus.sticks() == [0xC2, 0xC6]
        bus.send([(0xC6, 0x20, 0x32)])
        with pytest.raises(OSError):
            bus.send([(0xC4, 0x20, 0x32)])  # no stick there
        with pytest.raises(OSError):
            bus.send([(0xA0, 0x00, 0x00)])  # never: the memory's own chip
        assert delivered == [(0xC6, 0x20, 0x32)]
    finally:
        stop.set()
        listener.close()


def test_default_key_file_is_found_lazily(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "/somewhere/Local")
    assert winram.default_key_file() == Path("/somewhere/Local/rgbhub/ram-key")
    assert winram.HELPER_ADDRESS == ("127.0.0.1", 47231)
