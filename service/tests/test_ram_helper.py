# SPDX-License-Identifier: GPL-3.0-or-later
"""The root helper is the only privileged code in the project, so its
checks are tested hard. It lives outside the package (it must run with the
standard library only), so it is loaded from its file."""

import importlib.util
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[2] / "helper" / "rgbhub_ram_helper.py"
spec = importlib.util.spec_from_file_location("rgbhub_ram_helper", HELPER)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def make_sysfs(tmp_path, *spd_addresses):
    for address in spd_addresses:
        node = tmp_path / f"0-00{address:02x}"
        node.mkdir()
        (node / "name").write_text("spd5118\n")
    return tmp_path


def test_parse_valid_request():
    assert h.parse_request("W c2 20 64") == (0xC2, 0x20, 0x64)
    assert h.parse_request("  w C6 08 53 \n") == (0xC6, 0x08, 0x53)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "W",
        "W c2 20",  # too few fields
        "W c2 20 64 00",  # too many fields
        "R c2 20 64",  # only writes exist
        "W zz 20 64",  # not hex
        "W c2 20 100",  # value over one byte
        "W c2 -1 00",
        "W c1 20 64",  # odd address: not a stick
        "W a0 20 64",  # the memory's own configuration chip: never
        "W c8 20 64",  # beyond the fourth slot
        "W c2 00 00",  # register outside the lighting set
        "W c2 06 00",  # model register is read-only
        "W c2 80 00",
        "W c2 08 00",  # register 0x08 only takes begin (53) or end (44)
        "W c2 20 65",  # brightness above 100
        "W c2 0c 03",  # direction is 1 or 2
        "W c2 09 ff; rm -rf /",
    ],
)
def test_parse_rejects(line):
    with pytest.raises(ValueError):
        h.parse_request(line)


def test_every_allowed_register_is_a_lighting_register():
    # Guards against someone widening the list by accident.
    assert set(h.ALLOWED_REGISTERS) <= set(range(0x08, 0x74))
    assert 0x06 not in h.ALLOWED_REGISTERS and 0x00 not in h.ALLOWED_REGISTERS
    assert h.ALLOWED_ADDRESSES == (0xC0, 0xC2, 0xC4, 0xC6)


def test_acpi_command_matches_what_worked_on_the_hardware():
    assert h.acpi_command(0xC2, 0x20, 0x64) == (
        "\\_SB.WMID.WMAA 0x0 0x2 b53454355090002000a000000030000002064c2"
    )


def test_reply_ok():
    good = "{0x50, 0x41, 0x53, 0x53, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00}"
    assert h.reply_ok(good)
    assert not h.reply_ok("{0x50, 0x41, 0x53, 0x53, 0x06, 0x00, 0x00, 0x00}")  # BIOS said no
    assert not h.reply_ok("Error: AE_NOT_FOUND")
    assert not h.reply_ok("")


def test_stick_present_checks_the_matching_slot(tmp_path):
    sysfs = make_sysfs(tmp_path, 0x51, 0x53)
    assert h.stick_present(0xC2, sysfs) and h.stick_present(0xC6, sysfs)
    assert not h.stick_present(0xC0, sysfs) and not h.stick_present(0xC4, sysfs)


def test_stick_present_ignores_other_chips(tmp_path):
    node = tmp_path / "0-0051"
    node.mkdir()
    (node / "name").write_text("something-else\n")
    assert not h.stick_present(0xC2, tmp_path)


def run(line, tmp_path, reply="{0x50, 0x41, 0x53, 0x53, 0x00, 0x00, 0x00, 0x00}"):
    sent = []

    def call(command):
        sent.append(command)
        return reply

    answer = h.handle_line(
        line, call, make_sysfs(tmp_path, 0x51), sleep=lambda s: None, ready=lambda: True
    )
    return answer, sent


def test_ping_fails_when_the_bios_cannot_be_reached(tmp_path):
    # Without acpi_call every write would fail, so "connected" would be a lie.
    answer = h.handle_line("PING", lambda c: "", make_sysfs(tmp_path, 0x51), ready=lambda: False)
    assert answer.startswith("ERR")


def test_handle_line_ok(tmp_path):
    answer, sent = run("W c2 20 64", tmp_path)
    assert answer == "OK" and len(sent) == 1


def test_handle_line_ping_touches_nothing(tmp_path):
    answer, sent = run("PING", tmp_path)
    assert answer == "OK" and sent == []


def test_handle_line_refuses_bad_request_without_calling_bios(tmp_path):
    answer, sent = run("W a0 00 00", tmp_path)
    assert answer.startswith("ERR") and sent == []


def test_handle_line_refuses_empty_slot_without_calling_bios(tmp_path):
    answer, sent = run("W c6 20 64", tmp_path)  # only slot 1 (0x51) has a stick
    assert answer.startswith("ERR") and sent == []


def test_handle_line_reports_bios_failure(tmp_path):
    answer, _sent = run("W c2 20 64", tmp_path, reply="Error: AE_NOT_FOUND")
    assert answer.startswith("ERR")


def test_handle_line_survives_call_errors(tmp_path):
    def call(command):
        raise OSError("acpi_call not loaded")

    answer = h.handle_line("W c2 20 64", call, make_sysfs(tmp_path, 0x51), sleep=lambda s: None)
    assert answer.startswith("ERR")


# --- the worst case: a write reaching a stick's SPD EEPROM (0x50-0x57) ----------

SPD_7BIT = range(0x50, 0x58)  # each stick's own description; corrupting it is permanent
SPD_8BIT = range(0xA0, 0xB0)  # the same chips as 8-bit (shifted) addresses


def test_the_address_list_never_reaches_the_spd_eeproms():
    for address in h.ALLOWED_ADDRESSES:
        assert address not in SPD_7BIT and address not in SPD_8BIT
        assert address >> 1 not in SPD_7BIT  # also when read as a shifted address
    # The lighting controllers only: 0x60-0x63 shifted, as HP's software sends them.
    assert {address >> 1 for address in h.ALLOWED_ADDRESSES} <= set(range(0x60, 0x64))


@pytest.mark.parametrize("address", [*SPD_7BIT, *SPD_8BIT])
def test_spd_addresses_are_refused_for_every_register(address):
    for register in h.ALLOWED_REGISTERS:
        with pytest.raises(ValueError):
            h.parse_request(f"W {address:02x} {register:02x} 00")


def test_every_reply_to_an_spd_address_is_an_error_and_nothing_is_sent(tmp_path):
    sysfs = make_sysfs(tmp_path, *SPD_7BIT)
    sent = []
    for address in [*SPD_7BIT, *SPD_8BIT]:
        answer = h.handle_line(
            f"W {address:02x} 20 64", sent.append, sysfs, sleep=lambda s: None, ready=lambda: True
        )
        assert answer.startswith("ERR")
    assert sent == []


def test_the_only_way_out_is_the_bios():
    """No SMBus write path: the helper imports nothing that can reach a bus,
    and the one file it writes is acpi_call's."""
    import ast

    tree = ast.parse(HELPER.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= {"socket", "sys", "time", "pathlib"}, imported

    writes = [
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"write_text", "write_bytes", "open", "write"}
    ]
    # ACPI_CALL.write_text, and the socket stream's replies.
    assert sorted(writes) == ["ACPI_CALL.write_text", "stream.write", "stream.write"]
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"open", "exec", "eval", "__import__"}
    ]
    assert h.ACPI_CALL == Path("/proc/acpi/call")


def test_every_allowed_command_is_the_bios_lighting_call():
    for address in h.ALLOWED_ADDRESSES:
        for register, values in h.ALLOWED_REGISTERS.items():
            for value in (min(values), max(values)):
                command = h.acpi_command(
                    *h.parse_request(f"W {address:02x} {register:02x} {value:02x}")
                )
                assert command.startswith(h.ACPI_METHOD + " b" + h.REQUEST_HEAD)
                assert command.endswith(f"{register:02x}{value:02x}{address:02x}")


# --- the transport is swappable; the checks are not ----------------------------------


def test_handle_line_uses_given_deliver():
    delivered = []

    def deliver(address, register, value):
        delivered.append((address, register, value))
        return True

    present = {0xC2}.__contains__
    assert h.handle_line("W c2 20 32", present=present, deliver=deliver) == "OK"
    assert delivered == [(0xC2, 0x20, 50)]
    # Refused by the checks: never reaches the transport.
    for line in ("W c2 06 00", "W a0 20 32", "W c2 20 65", "W c6 20 32"):
        assert h.handle_line(line, present=present, deliver=deliver).startswith("ERR")
    assert delivered == [(0xC2, 0x20, 50)]


def test_handle_line_reports_a_refused_delivery():
    answer = h.handle_line(
        "W c2 20 32", present=lambda a: True, deliver=lambda a, r, v: False, sleep=lambda s: None
    )
    assert answer.startswith("ERR")


def test_handle_line_survives_delivery_errors():
    def deliver(address, register, value):
        raise OSError("no BIOS")

    answer = h.handle_line("W c2 20 32", present=lambda a: True, deliver=deliver)
    assert answer.startswith("ERR")


def test_acpi_deliver_sends_the_bios_command():
    sent = []

    def call(command):
        sent.append(command)
        return "{0x50, 0x41, 0x53, 0x53, 0x00, 0x00, 0x00, 0x00}"

    assert h.acpi_deliver(0xC2, 0x20, 0x64, call=call)
    assert sent == [h.acpi_command(0xC2, 0x20, 0x64)]
    assert not h.acpi_deliver(0xC2, 0x20, 0x64, call=lambda c: "Error: AE_NOT_FOUND")
