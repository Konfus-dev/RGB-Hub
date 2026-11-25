# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import subprocess
import sys
from pathlib import Path

from rgbhub.platforms.linux import openrgb
from rgbhub.platforms.linux.openrgb import (
    DETECTORS,
    MIN_KNOWN_DETECTORS,
    program_unchanged,
    record_program,
    restrict_detectors,
    why_unsafe_to_start,
    write_config,
)

REPO = Path(__file__).resolve().parents[2]


def test_only_wanted_detectors_stay_on():
    config = {
        "Detectors": {"detectors": {"HP Omen 30L": True, "Kingston Fury DDR5 DRAM": True}},
        "Other": {"keep": "me"},
    }
    result = restrict_detectors(config, ["HP Omen 30L"])
    assert result["Detectors"]["detectors"] == {
        "HP Omen 30L": True,
        "Kingston Fury DDR5 DRAM": False,
    }
    assert result["Other"] == {"keep": "me"}


def test_wanted_detector_is_added_when_openrgb_has_not_listed_it_yet():
    result = restrict_detectors({}, ["HP Omen 30L"])
    assert result["Detectors"]["detectors"] == {"HP Omen 30L": True}


def test_input_is_not_modified():
    config = {"Detectors": {"detectors": {"X": True}}}
    restrict_detectors(config, [])
    assert config["Detectors"]["detectors"] == {"X": True}


def test_write_config_creates_and_updates_file(tmp_path):
    folder = tmp_path / "openrgb"
    write_config(folder, ["HP Omen 30L"])
    path = folder / "OpenRGB.json"
    assert json.loads(path.read_text())["Detectors"]["detectors"] == {"HP Omen 30L": True}

    # OpenRGB later adds every detector it knows, switched on; we switch them off again.
    path.write_text(json.dumps({"Detectors": {"detectors": {"HP Omen 30L": True, "ENE": True}}}))
    write_config(folder, ["HP Omen 30L"])
    assert json.loads(path.read_text())["Detectors"]["detectors"] == {
        "HP Omen 30L": True,
        "ENE": False,
    }


def test_write_config_survives_a_corrupt_file(tmp_path):
    (tmp_path / "OpenRGB.json").write_text("{ not json")
    write_config(tmp_path, ["HP Omen 30L"])
    assert json.loads((tmp_path / "OpenRGB.json").read_text())["Detectors"]["detectors"] == {
        "HP Omen 30L": True
    }


def test_seed_file_supplies_detector_names_to_switch_off(tmp_path):
    # Detectors OpenRGB has not listed in our folder yet would run once at
    # its next start. Names learned from another OpenRGB config prevent that.
    seed = tmp_path / "global.json"
    seed.write_text(json.dumps({"Detectors": {"detectors": {"ENE": True, "HP Omen 30L": True}}}))
    folder = tmp_path / "ours"
    count = write_config(folder, ["HP Omen 30L"], seeds=[seed, tmp_path / "missing.json"])
    assert json.loads((folder / "OpenRGB.json").read_text())["Detectors"]["detectors"] == {
        "HP Omen 30L": True,
        "ENE": False,
    }
    assert count == 2  # how many detectors are now known


def test_a_changed_openrgb_program_is_noticed(tmp_path):
    program = tmp_path / "openrgb"
    program.write_bytes(b"version one")
    folder = tmp_path / "config"
    assert not program_unchanged(folder, str(program))  # never recorded: not safe to start
    record_program(folder, str(program))
    assert program_unchanged(folder, str(program))
    program.write_bytes(b"version two, with new detectors")
    assert not program_unchanged(folder, str(program))


# --- OpenRGB is never started with a detector list that lets it scan -------------


def test_only_the_case_light_board_is_detected():
    # Every other detector stays off; the RAM's ones jam its bus.
    assert DETECTORS == ("HP Omen 30L",)


def full_config(folder, count=MIN_KNOWN_DETECTORS + 50, on=("HP Omen 30L",)):
    detectors = {f"Detector {n}": False for n in range(count)}
    detectors.update(dict.fromkeys(on, True))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "OpenRGB.json").write_text(json.dumps({"Detectors": {"detectors": detectors}}))


def test_start_is_allowed_with_a_full_list_from_the_recorded_program(tmp_path):
    program = tmp_path / "openrgb"
    program.write_bytes(b"version one")
    full_config(tmp_path / "config")
    record_program(tmp_path / "config", str(program))
    assert why_unsafe_to_start(tmp_path / "config", str(program)) is None


def test_start_is_refused_when_the_detector_list_is_lost(tmp_path):
    """A deleted or corrupt OpenRGB.json is rebuilt by write_config with the
    one wanted detector only; OpenRGB would then run every other one."""
    program = tmp_path / "openrgb"
    program.write_bytes(b"version one")
    folder = tmp_path / "config"
    record_program(folder, str(program))
    write_config(folder, DETECTORS)  # nothing else known
    assert "probe" in why_unsafe_to_start(folder, str(program))
    (folder / "OpenRGB.json").write_text("{ corrupt")
    assert why_unsafe_to_start(folder, str(program))
    (folder / "OpenRGB.json").unlink()
    assert why_unsafe_to_start(folder, str(program))
    full_config(folder, count=MIN_KNOWN_DETECTORS - 2)
    assert why_unsafe_to_start(folder, str(program))


def test_start_is_refused_with_another_detector_on(tmp_path):
    program = tmp_path / "openrgb"
    program.write_bytes(b"version one")
    folder = tmp_path / "config"
    full_config(folder, on=("HP Omen 30L", "Kingston Fury DDR5 DRAM"))
    record_program(folder, str(program))
    assert "Kingston" in why_unsafe_to_start(folder, str(program))


def test_start_is_refused_after_an_update_even_with_a_full_list(tmp_path):
    program = tmp_path / "openrgb"
    program.write_bytes(b"version one")
    folder = tmp_path / "config"
    full_config(folder)
    record_program(folder, str(program))
    program.write_bytes(b"version two")
    assert why_unsafe_to_start(folder, str(program))


def run_module(*args, path):
    """python -m rgbhub.platforms.linux.openrgb, as the systemd unit runs it. Only a
    fake openrgb file is on PATH; it is looked at, never run."""
    env = {**os.environ, "PATH": str(path), "PYTHONPATH": str(REPO / "service")}
    return subprocess.run(
        [sys.executable, "-m", "rgbhub.platforms.linux.openrgb", *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_the_check_the_unit_runs_refuses_a_lost_list(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "openrgb"
    fake.write_text("#!/bin/sh\nexit 99\n")
    fake.chmod(0o755)
    folder = tmp_path / "config"
    # What the unit's first ExecStartPre does with a lost list: one detector.
    assert run_module(str(folder), path=bin_dir).stdout.strip() == "1"
    assert run_module("--record-program", str(folder), path=bin_dir).returncode == 0
    refused = run_module("--check-program", str(folder), path=bin_dir)
    assert refused.returncode != 0 and "RAM" in refused.stderr
    full_config(folder)
    assert run_module("--check-program", str(folder), path=bin_dir).returncode == 0


def test_the_installer_uses_the_same_threshold():
    text = (REPO / "install" / "install-user.sh").read_text()
    assert text.count(f"known < {MIN_KNOWN_DETECTORS}") == 2
    assert openrgb.MIN_KNOWN_DETECTORS == 100


def unit_lines(name):
    lines = (REPO / "systemd" / name).read_text().splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def test_the_server_unit_restricts_and_checks_before_it_starts_openrgb():
    lines = unit_lines("openrgb-server.service")
    pre = [line.split("=", 1)[1] for line in lines if line.startswith("ExecStartPre=")]
    start = [line.split("=", 1)[1] for line in lines if line.startswith("ExecStart=")]
    folder = "%h/.config/rgbhub/openrgb"
    module = "%h/.local/share/rgbhub/current/.venv/bin/python -m rgbhub.platforms.linux.openrgb"
    # A leading "-" would let OpenRGB start even when a check fails.
    assert pre == [
        f"{module} {folder} %h/.config/OpenRGB/OpenRGB.json",
        f"{module} --check-program {folder}",
    ]
    assert start == [
        f"/usr/bin/openrgb --server --server-host 127.0.0.1 --noautoconnect --config {folder}"
    ]
    assert not [line for line in lines if line.startswith(("ExecStartPost", "ExecReload"))]


def test_no_other_unit_starts_openrgb():
    for unit in (REPO / "systemd").iterdir():
        if unit.name == "openrgb-server.service":
            continue
        for line in unit_lines(unit.name):
            if line.startswith("Exec"):
                assert "openrgb " not in line and "/openrgb" not in line, (unit.name, line)
