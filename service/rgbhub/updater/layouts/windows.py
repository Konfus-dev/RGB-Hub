# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows: versions under `%LOCALAPPDATA%\\Programs\\RGB-Hub`, `current.txt` names the active one.

    %LOCALAPPDATA%\\Programs\\RGB-Hub\\     (the owner's: updates need no admin rights)
        python\\               Windows' embeddable Python (python.exe, pythonw.exe)
        pip.whl               pip, run from the wheel to install a version's libraries
        launcher.py           starts the hub of the version current.txt names
        current.txt           the active version's folder name
        versions\\<sha>\\       a snapshot of the repo, plus site-packages\\ (its libraries)
        updater\\              the stable updater copy the "RGB-Hub update" task runs

    C:\\Program Files\\RGB-Hub\\  (admin-writable only: the RAM helper runs with full rights)
        python\\  helper\\  install\\windows\\*.ps1

    %LOCALAPPDATA%\\rgbhub\\runtime.json   how to reach the running hub (pid, port, token)

The updater runs with the owner's normal rights. A version that changes the RAM
helper or the setup scripts is applied except for those; the hub's tray menu
then offers "Approve update...", which asks Windows for admin rights (UAC) and
copies them into Program Files (approve-privileged.ps1).

Only standard-library modules here; programs are run with `subprocess`.
"""

import json
import logging
import os
import shutil
import subprocess
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path

from rgbhub.updater import UpdateError, is_sha
from rgbhub.updater.stable import refresh_updater_copy
from rgbhub.updater.store import _safe_parts

log = logging.getLogger("rgbhub.updater")

TASK = "RGB-Hub"  # the scheduled task that runs launcher.py at sign-in
CREATE_NO_WINDOW = 0x08000000  # no console window flashing up from pythonw.exe
OLD_EXIT_WAIT_S = 20  # how long the old hub gets to end after its task is ended
NEW_START_WAIT_S = 60  # how long the launcher may take to start the new hub
HTTP_TIMEOUT_S = 10
# What finish-setup.ps1 installs into Program Files: (folder in a version, files).
PRIVILEGED = (("helper", "*.py"), ("install/windows", "*.ps1"), ("install/windows", "*.py"))
# Run by the embeddable python.exe: pip from pip.whl (first argument), with the rest.
PIP_CODE = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from pip._internal.cli.main import main; sys.exit(main())"
)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 900)
    if os.name == "nt":
        kw.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.run(cmd, check=False, **kw)


def _pid_alive(pid: int) -> bool:
    """Is process `pid` running? (On Windows os.kill would end it, so ask tasklist.)"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    done = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], timeout=30)
    return f'","{pid}",' in (done.stdout or "")


# Never through a proxy: the hub is on this computer.
_urlopen = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def default_root() -> Path:
    return _local_appdata() / "Programs" / "RGB-Hub"


def default_program_files() -> Path:
    return Path(os.environ.get("ProgramFiles") or "C:\\Program Files") / "RGB-Hub"


def build(root: Path) -> "WindowsLayout":
    return WindowsLayout(root)


def windows_requirements(version: Path) -> list[str]:
    """The `windows` extra of the version's pyproject.toml: what goes into site-packages."""
    path = Path(version) / "service" / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        wanted = data["project"]["optional-dependencies"]["windows"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise UpdateError(f"cannot read the Windows libraries from {path}: {e}") from e
    if not isinstance(wanted, list) or not all(
        isinstance(w, str) and w.strip() and not w.lstrip().startswith("-") for w in wanted
    ):
        raise UpdateError(f"odd list of Windows libraries in {path}")
    return wanted


def unpack_wheels(wheels: Path, target: Path) -> None:
    """Unpack every wheel in `wheels` into `target` (what pip install --target does,
    for wheels: no scripts, no compiling)."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(wheels).glob("*.whl")):
        try:
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    parts = _safe_parts(info.filename)
                    if info.is_dir() or not parts[-1]:
                        continue
                    if parts[0].endswith(".data"):
                        if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                            continue  # scripts, headers, data: not needed
                        parts = parts[2:]
                    dest = target.joinpath(*parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as source, open(dest, "wb") as out:
                        shutil.copyfileobj(source, out)
        except (OSError, zipfile.BadZipFile) as e:
            raise UpdateError(f"could not unpack {path.name}: {e}") from e


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


class WindowsLayout:
    def __init__(
        self,
        root: Path,
        *,
        run=_run,
        sleep=time.sleep,
        program_files: Path | None = None,
        data: Path | None = None,
        wheels: Path | None = None,
        urlopen=None,
        pid_alive=_pid_alive,
    ):
        """`wheels`: a folder of wheels to unpack instead of running pip (the
        first install, made from Ubuntu, where Windows' Python cannot run)."""
        self.root = Path(root)
        self.current_txt = self.root / "current.txt"
        self.program_files = Path(program_files) if program_files else default_program_files()
        self.data = Path(data) if data else _local_appdata() / "rgbhub"
        self.wheels = Path(wheels) if wheels else None
        self._run = run
        self._sleep = sleep
        self._urlopen = urlopen or _urlopen
        self._pid_alive = pid_alive
        self._started_before: float | None = None
        self._left_quit = False
        self._activated: Path | None = None

    # ---- helpers --------------------------------------------------------------

    @property
    def python(self) -> Path:
        return self.root / "python" / "python.exe"

    def _check(self, cmd: list[str], what: str) -> None:
        done = self._run(cmd)
        if done.returncode != 0:
            detail = (getattr(done, "stderr", "") or "").strip().splitlines()[-1:]
            raise UpdateError(f"{what} failed ({done.returncode}) {' '.join(detail)}".strip())

    def _hub(self) -> dict | None:
        """The running hub's runtime.json ({"pid", "port", "token", "started"}), or None."""
        try:
            info = json.loads((self.data / "runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not (
            isinstance(info, dict)
            and type(info.get("pid")) is int
            and type(info.get("port")) is int
            and isinstance(info.get("token"), str)
            and type(info.get("started")) in (int, float)
        ):
            return None
        return info

    def _post(self, path: str, payload: dict | None) -> bool:
        """POST to the running hub; True if it took it."""
        info = self._hub()
        if info is None:
            return False
        request = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}{path}",
            data=json.dumps(payload).encode() if payload is not None else b"",
            headers={"X-RGBHub-Token": info["token"], "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=HTTP_TIMEOUT_S) as reply:
                return 200 <= reply.status < 300
        except (OSError, ValueError) as e:  # URLError and HTTPError are OSErrors
            log.info("the hub did not take %s: %s", path, e)
            return False

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def paused(self) -> bool:
        return False

    # ---- Layout -----------------------------------------------------------------

    def prepare(self, version: Path) -> None:
        """Give the version its site-packages: a copy of the active version's when
        the Windows libraries did not change, else pip (from pip.whl)."""
        version = Path(version)
        target = version / "site-packages"
        partial = version / ".site-packages.partial"
        _remove(partial)
        if self.wheels is not None:
            unpack_wheels(self.wheels, partial)
        else:
            wanted = windows_requirements(version)
            active = self.current_commit()
            have = self.root / "versions" / active / "site-packages" if active else None
            if have is not None and have.is_dir() and self._same_libraries(have.parent, wanted):
                shutil.copytree(have, partial)
            else:
                cmd = [str(self.python), "-I", "-c", PIP_CODE, str(self.root / "pip.whl")]
                cmd += ["install", "--quiet", "--disable-pip-version-check", "--no-input"]
                cmd += ["--only-binary=:all:", "--target", str(partial), *wanted]
                self._check(cmd, "installing the Python packages")
                partial.mkdir(exist_ok=True)
        _remove(target)
        os.rename(partial, target)

    @staticmethod
    def _same_libraries(version: Path, wanted: list[str]) -> bool:
        try:
            return windows_requirements(version) == wanted
        except UpdateError:
            return False

    def self_check(self, version: Path) -> bool:
        cmd = [str(self.python), "-I", str(self.root / "launcher.py"), "--root", str(self.root)]
        done = self._run([*cmd, "--self-check", Path(version).name])
        if done.returncode != 0:
            log.warning("self-check: %s", (done.stderr or done.stdout or "").strip()[-300:])
        return done.returncode == 0

    def privileged_changes(self, old: Path | None, new: Path) -> list[str]:
        """Compared with what is installed in Program Files, not with the old
        version: a missing or different installed copy both count."""
        changed = []
        for folder, pattern in PRIVILEGED:
            for source in sorted((Path(new) / folder).glob(pattern)):
                try:
                    have = (self.program_files / folder / source.name).read_bytes()
                except OSError:
                    have = None
                if have != source.read_bytes():
                    changed.append(f"{folder}/{source.name}")
        return changed

    def request_privileged(self, new: Path, changes: list[str]) -> None:
        """The hub records it and shows "Approve update..." in its tray menu. With
        no hub running, the record is written here; the hub offers it when it starts."""
        pending = {"version": Path(new).name, "changes": list(changes)}
        if not self._post("/api/approve-pending", pending):
            self._write_json(self.data / "pending-privileged.json", pending)

    def activate(self, version: Path) -> None:
        version = Path(version)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / ".current.txt.tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(version.name + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.current_txt)
        self._activated = version

    def restart_hub(self) -> None:
        """Ask the hub to restart (the launcher then starts the version current.txt
        names); if it cannot be reached, restart the scheduled task."""
        info = self._hub()
        self._started_before = info["started"] if info else None
        self._left_quit = False
        if self._post("/api/restart", None):
            return
        if info is None:
            # No runtime.json: the owner quit the hub. Not ours to start again.
            log.info("the hub is not running (the owner quit it); not starting it")
            self._left_quit = True
            return
        log.info("the hub did not answer; restarting the %r task", TASK)
        self._run(["schtasks", "/End", "/TN", TASK])
        self._end_old_hub(info["pid"])
        self._check(["schtasks", "/Run", "/TN", TASK], "starting the hub")

    def _end_old_hub(self, pid: int) -> None:
        """Make sure process `pid` is gone before the task is run again (two hubs
        would fight over the lights): wait for it, then force it."""
        for _ in range(OLD_EXIT_WAIT_S):
            if not self._pid_alive(pid):
                return
            self._sleep(1)
        if self._pid_alive(pid):
            log.info("the old hub (pid %s) did not end; stopping it", pid)
            system = Path(os.environ.get("SystemRoot") or "C:\\Windows")
            self._run([str(system / "System32" / "taskkill.exe"), "/PID", str(pid), "/T", "/F"])

    def hub_healthy(self, wait_s: int) -> bool:
        """A new hub started (runtime.json's `started` changed) and is the same,
        running one `wait_s` later. (A hub the owner had quit stays quit: nothing to check.)"""
        if self._left_quit:
            return True
        for _ in range(NEW_START_WAIT_S):
            info = self._hub()
            if (
                info is not None
                and info["started"] != self._started_before
                and self._pid_alive(info["pid"])
            ):
                break
            self._sleep(1)
        else:
            return False
        self._sleep(wait_s)
        again = self._hub()
        healthy = (
            again is not None
            and (again["pid"], again["started"]) == (info["pid"], info["started"])
            and self._pid_alive(again["pid"])
        )
        if healthy and self._activated is not None:
            # Only a version seen working gets to replace the updater.
            try:
                refresh_updater_copy(self._activated, self.root, "windows")
            except Exception as e:
                log.warning("kept the old updater: %s", e)
        return healthy

    def busy(self) -> bool:
        return False

    def notify(self, text: str) -> None:
        """A balloon from the hub's tray icon; just the log if the hub isn't running."""
        if not self._post("/api/notify", {"text": text}):
            log.info("%s", text)

    def current_commit(self) -> str | None:
        try:
            name = self.current_txt.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            return None
        return name if is_sha(name) else None
