# SPDX-License-Identifier: GPL-3.0-or-later
"""The Ubuntu (GNOME) and Batocera update layouts, the stable updater copy and
the first-install entry point. Nothing here touches the real system: every
root is a temporary folder and every program call goes to a fake `run`."""

import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from rgbhub.updater import UpdateError
from rgbhub.updater import install as install_mod
from rgbhub.updater.flow import update_once
from rgbhub.updater.layouts.batocera import BatoceraLayout
from rgbhub.updater.layouts.gnome import UUID, GnomeLayout
from rgbhub.updater.stable import check_copy, refresh_updater_copy
from rgbhub.updater.store import Store

A = "a" * 40
B = "b" * 40
REPO = Path(__file__).resolve().parents[2]
UPDATER_DIR = REPO / "service" / "rgbhub" / "updater"
SERVICE_SCRIPT = REPO / "frontends" / "batocera" / "services" / "rgb_hub"


class FakeRun:
    """Records program calls; answers `systemctl show` from `states` in turn."""

    def __init__(self, returncode=0, states=()):
        self.calls = []
        self.returncode = returncode
        self.states = list(states)

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        out = ""
        if "show" in cmd and self.states:
            out = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return subprocess.CompletedProcess(cmd, self.returncode, out, "")


def make_version(root: Path, sha: str, marker: str = "") -> Path:
    """A version folder with a real updater copy and the files layouts look at."""
    v = root / "versions" / sha
    shutil.copytree(
        UPDATER_DIR, v / "service/rgbhub/updater", ignore=shutil.ignore_patterns("__pycache__")
    )
    (v / "service/rgbhub/__init__.py").write_text("")
    (v / "service/rgbhub/updater/marker.txt").write_text(marker or sha)
    ext = v / "frontends/gnome-shell" / UUID
    ext.mkdir(parents=True)
    for name, text in {
        "frontends/batocera/services/rgb_hub": f"#!/bin/bash\n# {sha}\n",
        "frontends/batocera/scripts/rgb-hub.sh": "#!/bin/bash\n# game\n",
        "frontends/batocera/power/suspend/rgb-hub.sh": "#!/bin/bash\n# suspend\n",
        "frontends/batocera/power/resume/rgb-hub.sh": "#!/bin/bash\n# resume\n",
        "helper/rgbhub_ram_helper.py": "# helper\n",
        "install/70-rgb-hub.rules": "rules70\n",
        "install/71-rgbhub-ram-bus-lock.rules": "rules71\n",
        "install/setup.sh": "#!/bin/bash\n",
        "systemd/rgbhub-ram.service": "[Service]\n",
        "systemd/rgbhub-ram.socket": "[Socket]\nSocketUser=@USER@\n",
    }.items():
        (v / name).parent.mkdir(parents=True, exist_ok=True)
        (v / name).write_text(text)
    return v


def gnome(tmp_path, **kw):
    root = tmp_path / "rgbhub"
    root.mkdir(exist_ok=True)
    kw.setdefault("run", FakeRun())
    kw.setdefault("sleep", lambda s: None)
    return GnomeLayout(
        root, extensions=tmp_path / "extensions", system=tmp_path / "sys", user="owner", **kw
    )


# ---- GNOME ----------------------------------------------------------------


def test_gnome_activate_swaps_symlink_atomically(tmp_path, monkeypatch):
    layout = gnome(tmp_path)
    a, b = make_version(layout.root, A), make_version(layout.root, B)
    layout.activate(a)
    assert os.readlink(layout.current) == f"versions/{A}"
    assert layout.current_commit() == A

    seen = []
    real_replace = os.replace

    def replace(src, dst):
        # `current` is never missing: the new link replaces it in one rename.
        if Path(dst) == layout.current:
            seen.append(os.readlink(dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    layout.activate(b)
    assert seen == [f"versions/{A}"]
    assert os.readlink(layout.current) == f"versions/{B}"
    assert layout.current_commit() == B
    assert not (layout.root / "current.new").exists()
    link = tmp_path / "extensions" / UUID
    assert os.readlink(link) == str(layout.current / "frontends/gnome-shell" / UUID)
    assert link.resolve() == (b / "frontends/gnome-shell" / UUID).resolve()


def test_gnome_extension_folder_that_is_not_ours_is_left_alone(tmp_path):
    layout = gnome(tmp_path)
    (tmp_path / "extensions" / UUID).mkdir(parents=True)
    layout.activate(make_version(layout.root, A))
    assert not (tmp_path / "extensions" / UUID).is_symlink()
    assert layout.current_commit() == A


def test_gnome_dev_marker_pauses(tmp_path):
    from test_updater import FakeGitHub

    layout = gnome(tmp_path)
    store = Store(layout.root)
    layout.activate(make_version(layout.root, A))
    store.set_current(A, previous=None)
    working_copy = tmp_path / "RGB-Hub"
    working_copy.mkdir()
    layout.current.unlink()
    os.symlink(working_copy, layout.current)
    (layout.root / "dev").touch()

    assert layout.current_commit() is None
    assert layout.busy()
    with pytest.raises(UpdateError):
        layout.activate(layout.root / "versions" / A)
    store.set_pending(A, B)  # even something to "recover" is left alone

    class NoNetwork(FakeGitHub):
        def latest(self, branch="main"):
            raise AssertionError("no network while paused")

    gh = NoNetwork(latest=B)
    assert update_once(gh, store, layout, health_wait_s=0) == "paused"
    assert gh.downloads == []
    assert os.readlink(layout.current) == str(working_copy)
    assert Store(layout.root).load()["pending"] == {"sha": A, "from": B}
    assert not (layout.root / "update.lock").exists()


def test_gnome_privileged_changes_detects_helper_diff(tmp_path):
    layout = gnome(tmp_path)
    new = make_version(layout.root, B)
    installed = {
        "usr/local/lib/rgbhub/rgbhub_ram_helper.py": "# helper\n",
        "etc/udev/rules.d/70-rgb-hub.rules": "rules70\n",
        "etc/udev/rules.d/71-rgbhub-ram-bus-lock.rules": "rules71\n",
        "etc/systemd/system/rgbhub-ram.service": "[Service]\n",
        "etc/systemd/system/rgbhub-ram.socket": "[Socket]\nSocketUser=owner\n",
    }
    for name, text in installed.items():
        (tmp_path / "sys" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "sys" / name).write_text(text)
    assert layout.privileged_changes(None, new) == []

    (new / "helper/rgbhub_ram_helper.py").write_text("# helper, changed\n")
    assert layout.privileged_changes(None, new) == ["helper/rgbhub_ram_helper.py"]
    (tmp_path / "sys/etc/udev/rules.d/70-rgb-hub.rules").unlink()
    assert layout.privileged_changes(None, new) == [
        "helper/rgbhub_ram_helper.py",
        "install/70-rgb-hub.rules",
    ]


def test_gnome_request_privileged_asks_first_then_runs_setup(tmp_path):
    run = FakeRun()
    layout = gnome(tmp_path, run=run)
    new = make_version(layout.root, B)
    layout.request_privileged(new, ["helper/rgbhub_ram_helper.py"])
    ((cmd, _),) = run.calls
    assert cmd[:4] == ["systemd-run", "--user", "--collect", "--quiet"]
    script = cmd[cmd.index("-c") + 1]
    assert "notify-send --wait" in script and "--action=approve=Approve" in script
    assert "pkexec /usr/bin/bash" in script and "--no-apt" in script
    assert "exec pkexec" not in script  # the shell stays pkexec's parent
    title, body, setup, _, fallback = cmd[-5:]
    assert title == "RGB-Hub update needs your password"
    assert "helper/rgbhub_ram_helper.py" in body
    assert setup == str(new / "install/setup.sh")
    assert f"pkexec /usr/bin/bash {setup} --no-apt" in fallback


def _approve(tmp_path, *, click: str, pkexec_status: int):
    """Run the approval script with fake notify-send and pkexec; returns their calls."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls"
    (bin_ / "notify-send").write_text(
        f'#!/bin/sh\necho "notify-send $*" >>{calls}\ncase "$*" in *--wait*) echo {click} ;; esac\n'
    )
    (bin_ / "pkexec").write_text(f'#!/bin/sh\necho "pkexec $*" >>{calls}\nexit {pkexec_status}\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    run = FakeRun()
    layout = gnome(tmp_path, run=run)
    new = make_version(layout.root, B)
    layout.request_privileged(new, ["install/70-rgb-hub.rules"])
    ((cmd, _),) = run.calls
    sh_args = cmd[cmd.index("/usr/bin/sh") :]
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    subprocess.run(sh_args, env=env, check=False, timeout=30)
    return calls.read_text().splitlines() if calls.exists() else [], new


def test_gnome_approval_runs_setup_after_approve(tmp_path):
    calls, new = _approve(tmp_path, click="approve", pkexec_status=0)
    assert calls[1] == f"pkexec /usr/bin/bash {new}/install/setup.sh --no-apt"
    assert len(calls) == 2  # no second notification


def test_gnome_approval_dismissed_runs_nothing(tmp_path):
    calls, _ = _approve(tmp_path, click="", pkexec_status=0)
    assert len(calls) == 1 and calls[0].startswith("notify-send --wait")


@pytest.mark.parametrize("status", [126, 127])
def test_gnome_approval_without_password_dialog_tells_the_command(tmp_path, status):
    calls, new = _approve(tmp_path, click="approve", pkexec_status=status)
    assert calls[1].startswith("pkexec ")
    assert len(calls) == 3
    assert calls[2].startswith("notify-send --app-name=RGB-Hub RGB-Hub could not install")
    assert f"pkexec /usr/bin/bash {new}/install/setup.sh --no-apt" in calls[2]


def test_gnome_approval_when_pkexec_is_missing_tells_the_command(tmp_path):
    bin_ = tmp_path / "bin"
    calls, new = None, None
    # pkexec not on PATH at all: the shell's "not found" (127) leads to the same help.
    bin_.mkdir()
    log = tmp_path / "calls"
    (bin_ / "notify-send").write_text(
        f'#!/bin/sh\necho "notify-send $*" >>{log}\ncase "$*" in *--wait*) echo approve ;; esac\n'
    )
    (bin_ / "notify-send").chmod(0o755)
    run = FakeRun()
    layout = gnome(tmp_path, run=run)
    new = make_version(layout.root, B)
    layout.request_privileged(new, ["install/70-rgb-hub.rules"])
    ((cmd, _),) = run.calls
    sh_args = cmd[cmd.index("/usr/bin/sh") :]
    subprocess.run(sh_args, env={"PATH": str(bin_)}, check=False, timeout=30)
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert f"pkexec /usr/bin/bash {new}/install/setup.sh --no-apt" in calls[1]


def test_gnome_prepare_and_self_check_use_the_versions_venv(tmp_path):
    run = FakeRun()
    layout = gnome(tmp_path, run=run)
    v = layout.root / "versions" / B
    layout.prepare(v)
    assert run.calls[0][0] == [
        "/usr/bin/python3",
        "-m",
        "venv",
        "--system-site-packages",
        str(v / ".venv"),
    ]
    assert run.calls[1][0] == [
        str(v / ".venv/bin/pip"),
        "install",
        "--quiet",
        f"{v}/service[gnome]",
    ]
    assert layout.self_check(v)
    assert run.calls[2][0] == [
        str(v / ".venv/bin/python"),
        "-m",
        "rgbhub",
        "--self-check",
        "--platform",
        "gnome",
    ]
    with pytest.raises(UpdateError):
        gnome(tmp_path, run=FakeRun(returncode=1)).prepare(v)


def test_gnome_hub_healthy_watches_restarts(tmp_path):
    ok = gnome(tmp_path, run=FakeRun(states=["ActiveState=active\nNRestarts=0\n"]))
    ok.restart_hub()
    assert ok.hub_healthy(60)
    crashed = gnome(
        tmp_path,
        run=FakeRun(states=["ActiveState=active\nNRestarts=0", "ActiveState=active\nNRestarts=1"]),
    )
    crashed.restart_hub()
    assert not crashed.hub_healthy(60)
    failed = gnome(
        tmp_path,
        run=FakeRun(states=["ActiveState=active\nNRestarts=0", "ActiveState=failed\nNRestarts=0"]),
    )
    failed.restart_hub()
    assert not failed.hub_healthy(60)


# ---- Batocera ---------------------------------------------------------------


def batocera(tmp_path, **kw):
    root = tmp_path / "system" / "rgb-hub"
    root.mkdir(parents=True, exist_ok=True)
    kw.setdefault("run", FakeRun())
    kw.setdefault("sleep", lambda s: None)
    return BatoceraLayout(root, run_dir=tmp_path / "run", **kw)


def test_batocera_activate_writes_current_txt(tmp_path):
    layout = batocera(tmp_path)
    layout.activate(make_version(layout.root, A))
    assert (layout.root / "current.txt").read_text() == A + "\n"
    assert layout.current_commit() == A
    system = tmp_path / "system"
    assert (system / "services/rgb_hub").read_text() == f"#!/bin/bash\n# {A}\n"
    assert os.access(system / "services/rgb_hub", os.X_OK)
    assert (system / "configs/emulationstation/scripts/suspend/rgb-hub.sh").is_file()

    running = (system / "services/rgb_hub").stat().st_ino
    layout.activate(make_version(layout.root, B))
    assert layout.current_commit() == B
    # A new file, not the running script rewritten in place.
    assert (system / "services/rgb_hub").stat().st_ino != running
    assert not list(layout.root.glob(".current.txt*"))


def test_batocera_busy_when_game_running(tmp_path):
    layout = batocera(tmp_path)
    assert not layout.busy()
    (tmp_path / "run/rgb-hub").mkdir(parents=True)
    (tmp_path / "run/rgb-hub/now-playing").write_text("Super Mario World\n")
    assert layout.busy()
    (tmp_path / "run/rgb-hub/now-playing").write_text("")
    assert not layout.busy()


def test_batocera_restart_and_health(tmp_path):
    pids = iter(["100", "100", "200", "200"])
    killed = []
    layout = batocera(tmp_path, kill=lambda pid, sig: killed.append(pid), pid_alive=lambda p: True)
    pidfile = tmp_path / "run/rgb-hub.pid"
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text("100\n")
    layout.restart_hub()
    assert killed == [100]

    def sleep(s):
        pidfile.write_text(next(pids))

    layout._sleep = sleep
    assert layout.hub_healthy(60)

    crashing = iter(["200", "300"])
    layout._sleep = lambda s: pidfile.write_text(next(crashing))
    pidfile.write_text("100")
    layout.restart_hub()
    assert not layout.hub_healthy(60)


def test_batocera_self_check_uses_the_versions_service(tmp_path):
    run = FakeRun()
    layout = batocera(tmp_path, run=run)
    v = layout.root / "versions" / A
    assert layout.self_check(v)
    cmd, kw = run.calls[0]
    assert cmd[1:] == ["-m", "rgbhub", "--self-check", "--platform", "batocera"]
    assert kw["env"]["PYTHONPATH"] == str(v / "service")
    assert layout.privileged_changes(None, v) == []


def _app_dir(base: Path) -> str:
    done = subprocess.run(
        ["bash", "-c", 'source "$1"; base="$2"; app_dir', "bash", str(SERVICE_SCRIPT), str(base)],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_batocera_service_script_reads_current(tmp_path):
    subprocess.run(["bash", "-n", str(SERVICE_SCRIPT)], check=True)
    base = tmp_path / "rgb-hub"
    base.mkdir()
    assert _app_dir(base) == str(base)  # the old flat layout: $base/service
    (base / "versions" / A).mkdir(parents=True)
    (base / "current.txt").write_text(A + "\n")
    assert _app_dir(base) == str(base / "versions" / A)
    (base / "current.txt").write_text("../../etc\n")
    assert _app_dir(base) == str(base)
    (base / "current.txt").write_text(B + "\n")  # no such folder
    assert _app_dir(base) == str(base)


# ---- stable updater copy -------------------------------------------------------


def _healthy(layout, tmp_path):
    """Make the layout's next health check pass."""
    if isinstance(layout, GnomeLayout):
        layout._run = FakeRun(states=["ActiveState=active\nNRestarts=0"])
    else:
        pidfile = tmp_path / "run/rgb-hub.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text("4242")
        layout._old_pid = 1
        layout._pid_alive = lambda pid: True


def test_stable_updater_copy_refreshed_on_activate(tmp_path):
    """Refreshed once the hub runs healthy on the new version, never at the switch."""
    for layout in (gnome(tmp_path), batocera(tmp_path)):
        copy = layout.root / "updater"
        layout.activate(make_version(layout.root, A, marker="first"))
        assert not copy.exists()  # switching alone does not touch it
        _healthy(layout, tmp_path)
        assert layout.hub_healthy(0)
        assert (copy / "rgbhub/updater/marker.txt").read_text() == "first"

        layout.activate(make_version(layout.root, B, marker="second"))
        assert (copy / "rgbhub/updater/marker.txt").read_text() == "first"
        _healthy(layout, tmp_path)
        assert layout.hub_healthy(0)
        assert (copy / "rgbhub/updater/marker.txt").read_text() == "second"
        assert not list(layout.root.glob(".updater.*"))
        # It runs on its own, with nothing but the standard library.
        done = subprocess.run(
            [sys.executable, "-I", str(copy / "run.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert done.returncode == 0, done.stderr
        assert "--platform" in done.stdout and "--check" in done.stdout


def test_stable_updater_copy_not_refreshed_when_unhealthy(tmp_path):
    layout = gnome(tmp_path)
    layout.activate(make_version(layout.root, A))
    layout._run = FakeRun(states=["ActiveState=failed\nNRestarts=0"])
    assert not layout.hub_healthy(0)
    assert not (layout.root / "updater").exists()


@pytest.mark.parametrize("platform", ["gnome", "batocera"])
def test_broken_updater_never_replaces_the_working_one(tmp_path, platform):
    root = tmp_path / "root"
    good = make_version(root, A, marker="good")
    refresh_updater_copy(good, root, platform)
    bad = make_version(root, B, marker="bad")
    (bad / "service/rgbhub/updater/flow.py").write_text("this is not python(\n")
    with pytest.raises(UpdateError, match="does not run"):
        refresh_updater_copy(bad, root, platform)
    assert (root / "updater/rgbhub/updater/marker.txt").read_text() == "good"
    assert not list(root.glob(".updater.*"))
    assert check_copy(root / "updater", platform) is None


def test_broken_updater_is_kept_out_after_a_healthy_switch(tmp_path):
    """The new version runs fine but its updater does not: the old updater stays."""
    layout = gnome(tmp_path)
    layout.activate(make_version(layout.root, A, marker="good"))
    _healthy(layout, tmp_path)
    assert layout.hub_healthy(0)
    bad = make_version(layout.root, B)
    (bad / "service/rgbhub/updater/__main__.py").write_text("raise SystemExit(3)\n")
    layout.activate(bad)
    _healthy(layout, tmp_path)
    assert layout.hub_healthy(0)  # the hub is fine; only the updater copy is refused
    assert (layout.root / "updater/rgbhub/updater/marker.txt").read_text() == "good"


def test_gnome_activate_installs_changed_user_units(tmp_path):
    run = FakeRun()
    layout = gnome(tmp_path, run=run, units=tmp_path / "units")
    v = make_version(layout.root, A)
    (v / "systemd/rgbhub.service").write_text("[Service]\nExecStart=new\n")
    (v / "systemd/rgbhub-update.timer").write_text("[Timer]\n")
    (tmp_path / "units").mkdir()
    (tmp_path / "units/rgbhub-update.timer").write_text("[Timer]\n")  # already the same
    layout.activate(v)
    assert (tmp_path / "units/rgbhub.service").read_text() == "[Service]\nExecStart=new\n"
    assert [c for c, _ in run.calls] == [["/usr/bin/systemctl", "--user", "daemon-reload"]]
    run.calls.clear()
    layout.activate(v)  # nothing differs now: no reload
    assert run.calls == []


# ---- first install ---------------------------------------------------------------


def version_zip(tmp_path, sha, top="rgbhub-x"):
    n = len(list(tmp_path.glob("src*")))
    src = make_version(tmp_path / f"src{n}", sha)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, f"{top}/{path.relative_to(src)}")
    z = tmp_path / f"{sha[:7]}.zip"
    z.write_bytes(buf.getvalue())
    return z


def test_install_records_the_version_with_the_store(tmp_path, monkeypatch):
    layout = batocera(tmp_path)
    monkeypatch.setattr(install_mod, "load_layout", lambda platform, root: (layout, layout.root))
    root = str(layout.root)
    args = ["--platform", "batocera", "--root", root, "--no-check"]
    assert install_mod.main([*args, "--zip", str(version_zip(tmp_path, A)), "--sha", A]) == 0
    state = Store(layout.root).load()
    assert state["current"] == A and state["previous"] is None
    assert (layout.root / "versions" / A / "service/rgbhub/updater/flow.py").is_file()
    assert layout.current_commit() == A

    assert install_mod.main([*args, "--zip", str(version_zip(tmp_path, B)), "--sha", B]) == 0
    state = json.loads((layout.root / "update-state.json").read_text())
    assert state["current"] == B and state["previous"] == A
    assert (layout.root / "versions" / A).is_dir()

    # Installing the active one again keeps its folder (the hub may be running from it).
    inode = (layout.root / "versions" / B).stat().st_ino
    assert install_mod.main([*args, "--zip", str(version_zip(tmp_path, B)), "--sha", B]) == 0
    assert (layout.root / "versions" / B).stat().st_ino == inode
    assert Store(layout.root).load()["previous"] == A


class ZipGitHub:
    """A fake GitHub whose main is `sha`, serving `zip_path`; or unreachable."""

    def __init__(self, sha=None, zip_path=None):
        self.sha, self.zip_path = sha, zip_path

    def latest(self, branch="main"):
        if self.sha is None:
            raise UpdateError("could not reach GitHub (fake)")
        return self.sha

    def download(self, sha, dest):
        assert sha == self.sha
        dest.write_bytes(self.zip_path.read_bytes())


def test_install_from_github_installs_main(tmp_path, monkeypatch):
    layout = batocera(tmp_path)
    monkeypatch.setattr(install_mod, "load_layout", lambda platform, root: (layout, layout.root))
    monkeypatch.setattr(install_mod, "GitHub", lambda: ZipGitHub(B, version_zip(tmp_path, B)))
    args = ["--platform", "batocera", "--root", str(layout.root), "--no-check"]
    assert install_mod.main([*args, "--from-github"]) == 0
    assert layout.current_commit() == B
    assert Store(layout.root).load()["current"] == B
    assert (layout.root / "updater/run.py").is_file()


def test_install_from_github_offline_fails_and_installs_nothing(tmp_path, monkeypatch, capsys):
    layout = batocera(tmp_path)
    monkeypatch.setattr(install_mod, "load_layout", lambda platform, root: (layout, layout.root))
    monkeypatch.setattr(install_mod, "GitHub", lambda: ZipGitHub())
    assert install_mod.main(["--platform", "batocera", "--from-github", "--no-check"]) == 1
    assert "could not reach GitHub" in capsys.readouterr().err
    assert not (layout.root / "versions").exists()
    assert not (layout.root / "current.txt").exists()


def test_install_with_a_broken_updater_changes_nothing(tmp_path, monkeypatch):
    layout = batocera(tmp_path)
    monkeypatch.setattr(install_mod, "load_layout", lambda platform, root: (layout, layout.root))
    src = make_version(tmp_path / "broken", A)
    (src / "service/rgbhub/updater/store.py").write_text("import nothing_like_this\n")
    z = tmp_path / "broken.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, f"top/{path.relative_to(src)}")
    args = ["--platform", "batocera", "--zip", str(z), "--sha", A, "--no-check"]
    assert install_mod.main(args) == 1
    assert layout.current_commit() is None
    assert not (layout.root / "updater").exists()


def test_install_failed_self_check_changes_nothing(tmp_path, monkeypatch):
    layout = batocera(tmp_path, run=FakeRun(returncode=1))
    monkeypatch.setattr(install_mod, "load_layout", lambda platform, root: (layout, layout.root))
    z = version_zip(tmp_path, A)
    assert install_mod.main(["--platform", "batocera", "--zip", str(z), "--sha", A]) == 1
    assert not (layout.root / "versions" / A).exists()
    assert layout.current_commit() is None
    assert Store(layout.root).load()["current"] is None


def _commit_copy_of_this_checkout(dest: Path) -> str:
    """This checkout's files (committed or not) as one commit of a new repo."""
    listed = (
        subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z", "-co", "--exclude-standard"],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    for name in filter(None, listed):
        if (REPO / name).is_file():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, dest / name)
    git = [
        "git",
        "-C",
        str(dest),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "snapshot"], check=True)
    return subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_install_batocera_script_writes_the_release_layout(tmp_path):
    """The real installer on a pretend SHARE, from a committed copy of this checkout."""
    if (
        shutil.which("git") is None
        or subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--verify", "HEAD"], capture_output=True
        ).returncode
    ):
        pytest.skip("needs a git checkout")
    repo = tmp_path / "repo"
    head = _commit_copy_of_this_checkout(repo)  # main
    script = repo / "frontends/batocera/services/rgb_hub"
    on_main = script.read_text()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "checkout", "-q", "-b", "other"], check=True)
    script.write_text(on_main + "# only on another branch\n")
    subprocess.run([*git, "commit", "-q", "-am", "other", "--no-gpg-sign"], check=True)
    script.write_text(on_main + "# not committed\n")
    (repo / "uncommitted.txt").write_text("not installed\n")
    share = tmp_path / "share"
    app = share / "system/rgb-hub"
    (app / "service/rgbhub").mkdir(parents=True)  # an old flat install
    (app / "helper").mkdir()
    done = subprocess.run(
        ["bash", str(repo / "install/install-batocera.sh"), str(share)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (app / "current.txt").read_text().strip() == head
    assert (app / "versions" / head / "service/rgbhub/__main__.py").is_file()
    assert (app / "versions" / head / "helper/rgbhub_ram_helper.py").is_file()
    assert not (app / "versions" / head / "uncommitted.txt").exists()
    assert (app / "updater/run.py").is_file()
    assert Store(app).load()["current"] == head
    assert not (app / "service").exists() and not (app / "helper").exists()
    # The service script and hooks come from the installed main, not the checkout.
    assert (share / "system/services/rgb_hub").read_text() == on_main
    assert (share / "system/configs/rgb-hub/settings.json").is_file()

    # Again: same version, kept as it is.
    done = subprocess.run(
        ["bash", str(repo / "install/install-batocera.sh"), str(share)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(p.name for p in (app / "versions").iterdir()) == [head]


def test_install_batocera_script_without_git_and_offline_fails_clearly(tmp_path):
    """A downloaded copy (no .git) installs main from GitHub; offline it stops
    with a clear message and installs nothing (never a copy that can't update)."""
    if shutil.which("git") is None:
        pytest.skip("needs git to list the files")
    repo = tmp_path / "repo"
    _commit_copy_of_this_checkout(repo)
    shutil.rmtree(repo / ".git")
    share = tmp_path / "share"
    (share / "system").mkdir(parents=True)
    dead = "http://127.0.0.1:9"  # nothing listens there: GitHub is unreachable
    env = dict(
        os.environ,
        GIT_CEILING_DIRECTORIES=str(tmp_path),
        https_proxy=dead,
        HTTPS_PROXY=dead,
        no_proxy="",
        NO_PROXY="",
    )
    done = subprocess.run(
        ["bash", str(repo / "install/install-batocera.sh"), str(share)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "could not reach GitHub" in done.stderr
    assert "ERROR: could not install RGB Hub from GitHub" in done.stderr
    app = share / "system/rgb-hub"
    assert not (app / "current.txt").exists()
    assert not (app / "versions").exists()
    assert not (share / "system/services/rgb_hub").exists()


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    done = subprocess.run([*cmd, *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _behind(repo: Path, installed: str, main: str) -> subprocess.CompletedProcess:
    script = f'source "{REPO}/install/never-backwards.sh"; behind_installed "$@"'
    return subprocess.run(
        ["bash", "-c", script, "x", str(repo), installed, main], capture_output=True, text=True
    )


def test_never_backwards_helper(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one", "--no-gpg-sign")
    one = _git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("2")
    _git(repo, "commit", "-q", "-am", "two", "--no-gpg-sign")
    two = _git(repo, "rev-parse", "HEAD")
    assert _behind(repo, "", two).returncode == 1  # nothing installed: install
    assert _behind(repo, one, two).returncode == 1  # older installed: moving forward
    assert _behind(repo, two, two).returncode == 1  # the same: nothing to protect
    assert _behind(repo, "unversioned", two).returncode == 1  # not a commit id
    ahead = _behind(repo, two, one)  # installed is newer than main
    assert ahead.returncode == 0 and "no going back" in ahead.stdout
    unknown = _behind(repo, "f" * 40, two)  # a commit this checkout never heard of
    assert unknown.returncode == 0 and "fffffff" in unknown.stdout


def test_install_scripts_never_move_backwards():
    for name, guard in (
        ("install-user.sh", 'behind_installed "$repo" "$installed" "$sha"'),
        ("install-batocera.sh", 'behind_installed "$repo" "$installed" "$sha"'),
    ):
        text = (REPO / "install" / name).read_text()
        assert 'source "$repo/install/never-backwards.sh"' in text
        assert guard in text
    subprocess.run(["bash", "-n", str(REPO / "install/never-backwards.sh")], check=True)


def test_install_batocera_keeps_a_version_that_main_does_not_contain(tmp_path):
    if (
        shutil.which("git") is None
        or subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--verify", "HEAD"], capture_output=True
        ).returncode
    ):
        pytest.skip("needs a git checkout")
    repo = tmp_path / "repo"
    head = _commit_copy_of_this_checkout(repo)
    share = tmp_path / "share"
    app = share / "system/rgb-hub"
    (share / "system").mkdir(parents=True)
    run = ["bash", str(repo / "install/install-batocera.sh"), str(share)]
    first = subprocess.run(run, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    # The updater took a newer commit; local main is rewound to something unrelated.
    _git(repo, "checkout", "-q", "--orphan", "rewound")
    (repo / "marker.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unrelated", "--no-gpg-sign")
    _git(repo, "branch", "-M", "main")
    assert _git(repo, "rev-parse", "main") != head
    done = subprocess.run(run, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "no going back" in done.stdout
    assert (app / "current.txt").read_text().strip() == head
    assert sorted(p.name for p in (app / "versions").iterdir()) == [head]
