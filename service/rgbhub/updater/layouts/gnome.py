# SPDX-License-Identifier: GPL-3.0-or-later
"""Ubuntu (GNOME): versions under `~/.local/share/rgbhub`, `current` is a symlink.

    ~/.local/share/rgbhub/
        versions/<sha>/          a snapshot of the repo, with its own .venv
        current -> versions/<sha>   what rgbhub.service and the extension use
        updater/                 the stable updater copy the timer runs
        dev                      present: `install-user.sh --dev` pointed
                                 `current` at the working copy; updating pauses

Only standard-library modules here; programs are run with `subprocess`.
"""

import logging
import os
import pwd
import subprocess
import time
from pathlib import Path

from rgbhub.updater import UpdateError, is_sha
from rgbhub.updater.stable import refresh_updater_copy

log = logging.getLogger("rgbhub.updater")

UUID = "rgbhub@konfus-dev.github.io"
SERVICE = "rgbhub.service"
PYTHON = "/usr/bin/python3"
SYSTEMCTL = "/usr/bin/systemctl"

# What setup.sh installs as root: (file in a version, where it is installed).
PRIVILEGED = (
    ("helper/rgbhub_ram_helper.py", "usr/local/lib/rgbhub/rgbhub_ram_helper.py"),
    ("install/70-rgb-hub.rules", "etc/udev/rules.d/70-rgb-hub.rules"),
    ("install/71-rgbhub-ram-bus-lock.rules", "etc/udev/rules.d/71-rgbhub-ram-bus-lock.rules"),
    ("systemd/rgbhub-ram.service", "etc/systemd/system/rgbhub-ram.service"),
    ("systemd/rgbhub-ram.socket", "etc/systemd/system/rgbhub-ram.socket"),
)

# Runs outside the updater's own unit (which systemd ends with its children):
# show the notification, and only if the owner clicks Approve ask for the
# password and run that version's setup.sh.
APPROVE_SCRIPT = (
    'answer=$(notify-send --wait --app-name=RGB-Hub --action=approve=Approve "$1" "$2")'
    ' && [ "$answer" = approve ] && exec pkexec /usr/bin/bash "$3" --no-apt'
)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 900)
    return subprocess.run(cmd, check=False, **kw)


def default_root() -> Path:
    return Path.home() / ".local" / "share" / "rgbhub"


def build(root: Path) -> "GnomeLayout":
    return GnomeLayout(root)


class GnomeLayout:
    def __init__(
        self,
        root: Path,
        *,
        run=_run,
        sleep=time.sleep,
        extensions: Path | None = None,
        system: Path = Path("/"),
        user: str | None = None,
    ):
        self.root = Path(root)
        self.current = self.root / "current"
        self.dev_marker = self.root / "dev"
        self.extensions = extensions or Path.home() / ".local/share/gnome-shell/extensions"
        self.system = Path(system)
        self.user = user or pwd.getpwuid(os.getuid()).pw_name
        self._run = run
        self._sleep = sleep
        self._restarts_before: str | None = None

    # ---- helpers --------------------------------------------------------------

    def paused(self) -> bool:
        """True while the working copy is installed (`install-user.sh --dev`)."""
        return self.dev_marker.exists()

    def _check(self, cmd: list[str], what: str, **kw) -> None:
        done = self._run(cmd, **kw)
        if done.returncode != 0:
            detail = (getattr(done, "stderr", "") or "").strip().splitlines()[-1:]
            raise UpdateError(f"{what} failed ({done.returncode}) {' '.join(detail)}".strip())

    def _service_state(self) -> dict[str, str]:
        done = self._run([SYSTEMCTL, "--user", "show", SERVICE, "--property=ActiveState,NRestarts"])
        state = {}
        for line in (done.stdout or "").splitlines():
            key, _, value = line.partition("=")
            state[key.strip()] = value.strip()
        return state

    # ---- Layout -----------------------------------------------------------------

    def prepare(self, version: Path) -> None:
        venv = Path(version) / ".venv"
        self._check([PYTHON, "-m", "venv", "--system-site-packages", str(venv)], "making the venv")
        self._check(
            [str(venv / "bin" / "pip"), "install", "--quiet", f"{version}/service[gnome]"],
            "installing the Python packages",
        )

    def self_check(self, version: Path) -> bool:
        python = Path(version) / ".venv" / "bin" / "python"
        done = self._run([str(python), "-m", "rgbhub", "--self-check", "--platform", "gnome"])
        if done.returncode != 0:
            log.warning("self-check: %s", (done.stderr or done.stdout or "").strip()[-300:])
        return done.returncode == 0

    def privileged_changes(self, old: Path | None, new: Path) -> list[str]:
        """Compared with what is installed on this PC, not with the old version:
        a missing or different installed copy both count."""
        changed = []
        for name, installed in PRIVILEGED:
            source = Path(new) / name
            if not source.is_file():
                continue
            wanted = source.read_bytes()
            if name.endswith(".socket"):
                wanted = wanted.replace(b"@USER@", self.user.encode())
            try:
                have = (self.system / installed).read_bytes()
            except OSError:
                have = None
            if have != wanted:
                changed.append(name)
        return changed

    def request_privileged(self, new: Path, changes: list[str]) -> None:
        title = "RGB-Hub update needs your password"
        body = (
            "The update changes parts that run with administrator rights: "
            + ", ".join(changes)
            + ". Click Approve to install them; everything else is already updated."
        )
        setup = str(Path(new) / "install" / "setup.sh")
        cmd = ["systemd-run", "--user", "--collect", "--quiet", "--", "/usr/bin/sh", "-c"]
        self._check([*cmd, APPROVE_SCRIPT, "sh", title, body, setup], "asking for approval")

    def activate(self, version: Path) -> None:
        if self.paused():
            raise UpdateError("paused: the working copy is installed (install-user.sh --dev)")
        version = Path(version)
        refresh_updater_copy(version, self.root)
        try:
            target = version.relative_to(self.root)
        except ValueError:
            target = version
        tmp = self.root / "current.new"
        tmp.unlink(missing_ok=True)
        os.symlink(target, tmp)
        os.replace(tmp, self.current)
        self._link_extension()

    def _link_extension(self) -> None:
        """The extension is reached through `current`, so it follows every switch."""
        link = self.extensions / UUID
        target = self.current / "frontends" / "gnome-shell" / UUID
        if link.exists() and not link.is_symlink():
            log.warning("%s is not a link made by RGB-Hub; leaving it alone", link)
            return
        if link.is_symlink() and Path(os.readlink(link)) == target:
            return
        self.extensions.mkdir(parents=True, exist_ok=True)
        tmp = link.with_name(f".{UUID}.new")
        tmp.unlink(missing_ok=True)
        os.symlink(target, tmp)
        os.replace(tmp, link)

    def restart_hub(self) -> None:
        self._check([SYSTEMCTL, "--user", "restart", SERVICE], "restarting the hub")
        self._restarts_before = self._service_state().get("NRestarts")

    def hub_healthy(self, wait_s: int) -> bool:
        before = self._restarts_before
        if before is None:
            before = self._service_state().get("NRestarts")
        self._sleep(wait_s)
        after = self._service_state()
        return after.get("ActiveState") == "active" and after.get("NRestarts") == before

    def busy(self) -> bool:
        return self.paused()

    def notify(self, text: str) -> None:
        self._run(["notify-send", "RGB-Hub", text], timeout=30)

    def current_commit(self) -> str | None:
        if self.paused():
            return None
        try:
            name = Path(os.readlink(self.current)).name
        except OSError:
            return None
        return name if is_sha(name) else None
