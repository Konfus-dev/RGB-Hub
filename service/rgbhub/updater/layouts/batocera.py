# SPDX-License-Identifier: GPL-3.0-or-later
"""Batocera: versions under `/userdata/system/rgb-hub`, `current.txt` names the active one.

    /userdata/system/rgb-hub/
        versions/<sha>/     a snapshot of the repo (service/, helper/, frontends/batocera/)
        current.txt         the active version's folder name (SHARE may be exFAT: no symlinks)
        updater/            the stable updater copy the `rgb_hub` service runs

Everything on Batocera runs as root, so updates install everything, including
the service script and the game and sleep hooks. The `rgb_hub` service script
restarts the hub whenever it exits, reading `current.txt` each time.

Only standard-library modules here; programs are run with `subprocess`.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rgbhub.updater import is_sha
from rgbhub.updater.stable import refresh_updater_copy

log = logging.getLogger("rgbhub.updater")

# Files of a version that live outside it: (in the version, under /userdata/system).
HOOKS = (
    ("frontends/batocera/services/rgb_hub", "services/rgb_hub"),
    ("frontends/batocera/scripts/rgb-hub.sh", "scripts/rgb-hub.sh"),
    (
        "frontends/batocera/power/suspend/rgb-hub.sh",
        "configs/emulationstation/scripts/suspend/rgb-hub.sh",
    ),
    (
        "frontends/batocera/power/resume/rgb-hub.sh",
        "configs/emulationstation/scripts/resume/rgb-hub.sh",
    ),
)
NEW_PID_WAIT_S = 30  # the hub shows the Default look before it exits; the keeper then restarts it


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 300)
    return subprocess.run(cmd, check=False, **kw)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def default_root() -> Path:
    return Path("/userdata/system/rgb-hub")


def build(root: Path) -> "BatoceraLayout":
    return BatoceraLayout(root)


class BatoceraLayout:
    def __init__(
        self,
        root: Path,
        *,
        run=_run,
        sleep=time.sleep,
        run_dir: Path = Path("/var/run"),
        pid_alive=_pid_alive,
        kill=os.kill,
    ):
        self.root = Path(root)
        self.system = self.root.parent  # /userdata/system
        self.current_txt = self.root / "current.txt"
        self.pidfile = Path(run_dir) / "rgb-hub.pid"
        self.now_playing = Path(run_dir) / "rgb-hub" / "now-playing"
        self._run = run
        self._sleep = sleep
        self._pid_alive = pid_alive
        self._kill = kill
        self._old_pid: int | None = None
        self._activated: Path | None = None

    def _pid(self) -> int | None:
        try:
            text = self.pidfile.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            return None
        return int(text) if text.isdigit() and int(text) > 0 else None

    # ---- Layout -----------------------------------------------------------------

    def prepare(self, version: Path) -> None:
        """Nothing to install: Batocera's own Python has everything (Pillow included)."""

    def self_check(self, version: Path) -> bool:
        env = dict(os.environ, PYTHONPATH=str(Path(version) / "service"))
        cmd = [sys.executable, "-m", "rgbhub", "--self-check", "--platform", "batocera"]
        done = self._run(cmd, env=env)
        if done.returncode != 0:
            log.warning("self-check: %s", (done.stderr or done.stdout or "").strip()[-300:])
        return done.returncode == 0

    def paused(self) -> bool:
        return False

    def privileged_changes(self, old: Path | None, new: Path) -> list[str]:
        return []  # everything already runs as root

    def request_privileged(self, new: Path, changes: list[str]) -> None:
        pass

    def activate(self, version: Path) -> None:
        version = Path(version)
        self._install_hooks(version)
        tmp = self.current_txt.with_name(".current.txt.tmp")
        with open(tmp, "w", encoding="ascii") as f:
            f.write(version.name + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.current_txt)
        self._activated = version

    def _install_hooks(self, version: Path) -> None:
        """Copy the service script and hooks of `version` where Batocera looks for them.

        Replaced by rename, never rewritten in place: bash reads a running
        script as it goes, and the `rgb_hub` script is running right now."""
        for name, where in HOOKS:
            source = version / name
            dest = self.system / where
            if not source.is_file():
                continue
            data = source.read_bytes()
            try:
                if dest.read_bytes() == data:
                    continue
            except OSError:
                pass
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.new")
            tmp.write_bytes(data)
            tmp.chmod(0o755)
            os.replace(tmp, dest)
            log.info("installed %s", where)

    def restart_hub(self) -> None:
        """Stop the hub; the `rgb_hub` keeper starts it again from `current.txt`."""
        self._old_pid = self._pid()
        if self._old_pid is not None:
            try:
                self._kill(self._old_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # already gone; the keeper starts it again either way

    def hub_healthy(self, wait_s: int) -> bool:
        """A new hub process appeared and is the same, alive one `wait_s` later."""
        pid = None
        for _ in range(NEW_PID_WAIT_S):
            pid = self._pid()
            if pid is not None and pid != self._old_pid and self._pid_alive(pid):
                break
            self._sleep(1)
        else:
            return False
        self._sleep(wait_s)
        healthy = self._pid() == pid and self._pid_alive(pid)
        if healthy and self._activated is not None:
            # Only a version seen working gets to replace the updater.
            try:
                refresh_updater_copy(self._activated, self.root, "batocera")
            except Exception as e:
                log.warning("kept the old updater: %s", e)
        return healthy

    def busy(self) -> bool:
        """A game is running (the game hook writes its name, and empties it at the end)."""
        try:
            return bool(self.now_playing.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            return False

    def notify(self, text: str) -> None:
        log.info("%s", text)

    def current_commit(self) -> str | None:
        try:
            name = self.current_txt.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            return None
        return name if is_sha(name) else None
