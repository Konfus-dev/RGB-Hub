# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenRGB's full hardware scan probes the RAM and jams its bus. It may
happen in one place only (install/openrgb-detector-scan.sh) and only when
the owner agrees. These tests run that script with a fake openrgb; the real
one is never started."""

import os
import pty
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN = REPO / "install" / "openrgb-detector-scan.sh"


@pytest.fixture
def fake(tmp_path):
    """A fake openrgb that records how it was called, first on PATH and
    named explicitly, so the real one cannot be reached."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "openrgb-calls"
    program = bin_dir / "openrgb"
    program.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n')
    program.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "OPENRGB": str(program),
        "HOME": str(tmp_path),
    }
    return env, log


def scan(env, *args, stdin=subprocess.DEVNULL):
    return subprocess.run(
        ["bash", str(SCAN), *args], env=env, stdin=stdin, capture_output=True, text=True
    )


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_no_scan_without_anyone_to_ask(fake):
    env, log = fake
    result = scan(env, "/cfg")
    assert result.returncode != 0 and calls(log) == []
    assert "RAM" in result.stderr  # the owner is told why


def test_no_scan_without_a_folder(fake):
    env, log = fake
    assert scan(env, "", "--yes").returncode != 0 and calls(log) == []


def test_agreed_in_advance_it_scans_once_with_its_own_config(fake):
    env, log = fake
    assert scan(env, "/cfg", "--yes").returncode == 0
    assert calls(log) == ["--config /cfg --noautoconnect --list-devices"]


def answer_at_prompt(env, answer):
    main, terminal = pty.openpty()
    try:
        os.write(main, answer.encode() + b"\n")
        return scan(env, "/cfg", stdin=terminal)
    finally:
        os.close(main)
        os.close(terminal)


@pytest.mark.parametrize("answer", ["", "y", "yes", "no", "SCAN"])
def test_anything_but_scan_at_the_prompt_stops(fake, answer):
    env, log = fake
    assert answer_at_prompt(env, answer).returncode != 0 and calls(log) == []


def test_typing_scan_at_the_prompt_scans(fake):
    env, log = fake
    assert answer_at_prompt(env, "scan").returncode == 0
    assert calls(log) == ["--config /cfg --noautoconnect --list-devices"]


def test_the_installer_scans_only_through_the_script():
    text = (REPO / "install" / "install-user.sh").read_text()
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert sum("openrgb-detector-scan.sh" in line for line in code) == 1
    # The flag is passed only when the owner gave --allow-detector-scan.
    assert "--allow-detector-scan) allow_scan=--yes ;;" in text
    assert 'openrgb-detector-scan.sh" "$orgb" $allow_scan; then' in text


# --- where the OpenRGB program is started, repo-wide ------------------------------

# Every place that runs the openrgb program, and why it cannot scan everything.
ALLOWED_LAUNCHES = {
    # The server, with its own config in which every detector but the case
    # lights is off; checked by --check-program first (test_openrgb.py).
    "systemd/openrgb-server.service",
    # The deliberate one-time scan, behind the owner's confirmation.
    "install/openrgb-detector-scan.sh",
}
LAUNCH = re.compile(r'(?:^|[\s;&|(=`])(?:/usr/bin/)?(?:openrgb|"\$openrgb"|"\$OPENRGB")\s+-')
SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist"}


def launches():
    found = {}
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        dirs = rel.parts[:-1]
        if not path.is_file() or set(dirs) & SKIP_DIRS or any(d.startswith(".") for d in dirs):
            continue
        if path.suffix in {".md", ".ko", ".compiled", ".pyc"} or rel.parts[:2] == (
            "service",
            "tests",
        ):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for line in text.splitlines():
            if line.lstrip().startswith(("#", "//", "echo")):
                continue
            if LAUNCH.search(line):
                found.setdefault(rel.as_posix(), []).append(line.strip())
    return found


def test_openrgb_is_started_only_in_the_listed_places():
    found = launches()
    assert set(found) == ALLOWED_LAUNCHES, found
    scan_lines = found["install/openrgb-detector-scan.sh"]
    assert scan_lines == [
        'timeout 120 "$openrgb" --config "$folder" --noautoconnect --list-devices '
        ">/dev/null 2>&1 || true"
    ]
    # Every start names a config folder: OpenRGB without one uses its global
    # config, where nothing is switched off.
    for lines in found.values():
        assert all("--config" in line for line in lines)


def test_the_launch_pattern_finds_launches():
    for line in (
        "openrgb --list-devices",
        "timeout 120 openrgb --config x",
        "ExecStart=/usr/bin/openrgb --server",
        'timeout 120 "$openrgb" --config "$folder"',
    ):
        assert LAUNCH.search(line), line
    for line in (
        "python -m rgbhub.platforms.linux.openrgb --check-program x",
        "openrgb-server.service",
    ):
        assert not LAUNCH.search(line), line
