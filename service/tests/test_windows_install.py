# SPDX-License-Identifier: GPL-3.0-or-later
"""Installing on Windows from Ubuntu, and Windows' update layout.

Nothing here touches Windows or the real system: the Windows partition, the
install folders and the hub's runtime.json are temporary folders, programs
and HTTP requests go to fakes."""

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from rgbhub.core.settings_schema import DEFAULTS
from rgbhub.updater import UpdateError
from rgbhub.updater import install as install_mod
from rgbhub.updater.layouts import windows as win_layout
from rgbhub.updater.layouts.windows import WindowsLayout, unpack_wheels, windows_requirements
from rgbhub.updater.store import Store

A = "a" * 40
B = "b" * 40
REPO = Path(__file__).resolve().parents[2]
UPDATER_DIR = REPO / "service" / "rgbhub" / "updater"
INSTALL = REPO / "install"
PYPROJECT = (REPO / "service" / "pyproject.toml").read_text()


def load_settings_module():
    spec = importlib.util.spec_from_file_location(
        "windows_settings", INSTALL / "windows_settings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ws = load_settings_module()


# ---- settings from Ubuntu ------------------------------------------------------


def convert(values, tmp_path, **kw):
    kw.setdefault("windows_mount", tmp_path / "mnt")
    return ws.convert(values, "jercl", **kw)


def test_settings_conversion_keeps_only_non_defaults(tmp_path):
    values = {key: value for key, value in DEFAULTS.items()}
    values["lcd-brightness"] = 55
    values["zone-colours"] = {"HP Omen 30L/Omen Logo": "#DC8ADD"}
    values["auto-lcd"] = True  # the default
    values["status"] = '{"busy": true}'  # the hub's, never copied
    values["focused-app"] = "firefox"
    values["default-action"] = "save"
    values["device-speeds"] = {"RAM": "fast"}  # wrong kind: dropped
    values["no-such-key"] = 1
    settings, copies = convert(values, tmp_path)
    assert settings == {"lcd-brightness": 55, "zone-colours": {"HP Omen 30L/Omen Logo": "#DC8ADD"}}
    assert copies == []


def test_settings_conversion_rewrites_and_copies_pictures(tmp_path):
    pics = tmp_path / "home" / "Pictures"
    pics.mkdir(parents=True)
    (pics / "cat.png").write_bytes(b"cat")
    (pics / "dog.jpg").write_bytes(b"dog")
    other = tmp_path / "home" / "Downloads"
    other.mkdir()
    (other / "cat.png").write_bytes(b"another cat")  # same name, different picture
    on_windows = tmp_path / "mnt" / "Users" / "jercl" / "OneDrive" / "Pictures"
    on_windows.mkdir(parents=True)
    (on_windows / "spike.jpg").write_bytes(b"spike")
    look = {"lcd-image": str(pics / "dog.jpg"), "lcd-brightness": 90}
    values = {
        "lcd-image": str(pics / "cat.png"),
        "auto-idle-image": str(on_windows / "spike.jpg"),
        "lcd-recent-images": [
            str(pics / "cat.png"),
            str(other / "cat.png"),
            str(tmp_path / "gone.png"),  # missing: left out
            str(on_windows / "spike.jpg"),
        ],
        "default-look": json.dumps(look),
    }
    settings, copies = convert(values, tmp_path)
    folder = "C:\\Users\\jercl\\AppData\\Roaming\\rgbhub\\pictures\\"
    assert settings["lcd-image"] == folder + "cat.png"
    # A picture already on the Windows partition is used where it is.
    assert settings["auto-idle-image"] == "C:\\Users\\jercl\\OneDrive\\Pictures\\spike.jpg"
    assert settings["lcd-recent-images"] == [
        folder + "cat.png",
        folder + "cat-2.png",
        "C:\\Users\\jercl\\OneDrive\\Pictures\\spike.jpg",
    ]
    assert json.loads(settings["default-look"]) == {
        "lcd-image": folder + "dog.jpg",
        "lcd-brightness": 90,
    }
    assert sorted(copies) == sorted(
        [
            (pics / "cat.png", "cat.png"),
            (other / "cat.png", "cat-2.png"),
            (pics / "dog.jpg", "dog.jpg"),
        ]
    )

    # A missing picture setting falls back to the default (and is then left out).
    settings, _ = convert({"lcd-image": str(tmp_path / "gone.png")}, tmp_path)
    assert settings == {}


def seed(tmp_path, values):
    home = tmp_path / "mnt" / "Users" / "jercl"
    home.mkdir(parents=True, exist_ok=True)
    return ws.seed(
        values,
        windows_home=home,
        windows_user="jercl",
        windows_mount=tmp_path / "mnt",
        ubuntu_config=tmp_path / "home" / ".config" / "rgbhub",
    ), home / "AppData" / "Roaming" / "rgbhub"


def test_settings_seed_writes_settings_pictures_and_lcd_folder(tmp_path):
    pics = tmp_path / "home" / "Pictures"
    pics.mkdir(parents=True)
    (pics / "cat.png").write_bytes(b"cat")
    lcd = tmp_path / "home" / ".config" / "rgbhub" / "lcd"
    lcd.mkdir(parents=True)
    (lcd / "a.png").write_bytes(b"lcd")
    _, roaming = seed(tmp_path, {"lcd-image": str(pics / "cat.png"), "lcd-brightness": 40})
    saved = json.loads((roaming / "settings.json").read_text())
    assert saved == {
        "lcd-brightness": 40,
        "lcd-image": "C:\\Users\\jercl\\AppData\\Roaming\\rgbhub\\pictures\\cat.png",
    }
    assert (roaming / "pictures" / "cat.png").read_bytes() == b"cat"
    assert (roaming / "lcd" / "a.png").read_bytes() == b"lcd"


def test_settings_never_overwritten(tmp_path):
    pics = tmp_path / "home" / "Pictures"
    pics.mkdir(parents=True)
    (pics / "cat.png").write_bytes(b"new cat")
    lcd = tmp_path / "home" / ".config" / "rgbhub" / "lcd"
    lcd.mkdir(parents=True)
    (lcd / "a.png").write_bytes(b"new lcd")
    roaming = tmp_path / "mnt" / "Users" / "jercl" / "AppData" / "Roaming" / "rgbhub"
    (roaming / "lcd").mkdir(parents=True)
    (roaming / "lcd" / "mine.png").write_bytes(b"windows lcd")
    (roaming / "settings.json").write_text('{"lcd-brightness": 10}\n')
    messages, _ = seed(tmp_path, {"lcd-image": str(pics / "cat.png"), "lcd-brightness": 40})
    assert (roaming / "settings.json").read_text() == '{"lcd-brightness": 10}\n'
    assert not (roaming / "pictures").exists()
    assert sorted(p.name for p in (roaming / "lcd").iterdir()) == ["mine.png"]
    assert any("kept" in m for m in messages)

    # Pictures already on Windows are never replaced either.
    (roaming / "settings.json").unlink()
    (roaming / "pictures").mkdir()
    (roaming / "pictures" / "cat.png").write_bytes(b"windows cat")
    seed(tmp_path, {"lcd-image": str(pics / "cat.png")})
    assert (roaming / "pictures" / "cat.png").read_bytes() == b"windows cat"


def test_settings_read_from_gsettings_as_text(tmp_path):
    asked = []

    def run(cmd, **kw):
        asked.append(cmd)
        key = cmd[-1]
        out = {"lcd-brightness": "90\n", "zone-colours": "{'RAM/Stick 1': '#DC8ADD'}\n"}
        if key not in out:
            return subprocess.CompletedProcess(cmd, 1, "", "No such key")
        return subprocess.CompletedProcess(cmd, 0, out[key], "")

    def parse(text):
        return {"90": 90, "{'RAM/Stick 1': '#DC8ADD'}": {"RAM/Stick 1": "#DC8ADD"}}[text]

    values = ws.read_gsettings(tmp_path / "schemas", run=run, parse=parse)
    assert values == {"lcd-brightness": 90, "zone-colours": {"RAM/Stick 1": "#DC8ADD"}}
    assert asked[0][:5] == ["gsettings", "--schemadir", str(tmp_path / "schemas"), "get", ws.SCHEMA]
    assert {cmd[-1] for cmd in asked} == set(DEFAULTS) - set(ws.NOT_COPIED)


# ---- the update layout ------------------------------------------------------------


class FakeRun:
    def __init__(self, returncode=0, stdout=""):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, cmd, **kw):
        self.calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "boom")


class FakeHub:
    """Answers the layout's HTTP requests to the hub, recording them."""

    def __init__(self, status=204, error=None):
        self.requests = []
        self.status = status
        self.error = error

    def __call__(self, request, timeout=None):
        body = request.data
        self.requests.append(
            (
                request.full_url,
                request.get_header("X-rgbhub-token"),
                json.loads(body) if body else None,
            )
        )
        if self.error:
            raise self.error
        response = io.BytesIO(b"")
        response.status = self.status
        return response


def layout(tmp_path, **kw):
    root = tmp_path / "Programs" / "RGB-Hub"
    root.mkdir(parents=True, exist_ok=True)
    kw.setdefault("run", FakeRun())
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("urlopen", FakeHub())
    kw.setdefault("pid_alive", lambda pid: True)
    return WindowsLayout(
        root, program_files=tmp_path / "Program Files" / "RGB-Hub", data=tmp_path / "rgbhub", **kw
    )


def make_version(root: Path, sha: str, pyproject: str = PYPROJECT) -> Path:
    v = root / "versions" / sha
    shutil.copytree(
        UPDATER_DIR, v / "service/rgbhub/updater", ignore=shutil.ignore_patterns("__pycache__")
    )
    (v / "service/rgbhub/__init__.py").write_text("")
    (v / "service/pyproject.toml").write_text(pyproject)
    for name, text in {
        "helper/rgbhub_ram_helper.py": "# shared checks\n",
        "helper/rgbhub_ram_helper_windows.py": "# windows helper\n",
        "install/windows/finish-setup.ps1": "# finish\n",
        "install/windows/uninstall.ps1": "# uninstall\n",
        "install/windows/approve-privileged.ps1": "# approve\n",
        "install/windows/RGB-Hub-setup.cmd": "@echo off\n",
    }.items():
        (v / name).parent.mkdir(parents=True, exist_ok=True)
        (v / name).write_text(text)
    return v


def write_runtime(data_dir: Path, pid=100, port=4321, token="tok", started=1.0):
    data_dir.mkdir(parents=True, exist_ok=True)
    info = {"pid": pid, "port": port, "token": token, "started": started}
    (data_dir / "runtime.json").write_text(json.dumps(info))


def test_windows_requirements_come_from_the_windows_extra(tmp_path):
    v = make_version(tmp_path, A)
    assert windows_requirements(v) == [
        "hidapi==0.14.0.post4",
        "pystray==0.19.5",
        "pillow>=11",
        "icoextract==0.3.1",
    ]
    line = next(x for x in PYPROJECT.splitlines() if x.startswith("windows = "))
    for bad in ('["--index-url=http://x", "a"]', '"hidapi"', "[1]"):
        text = PYPROJECT.replace(line, f"windows = {bad}")
        assert text != PYPROJECT
        (v / "service/pyproject.toml").write_text(text)
        with pytest.raises(UpdateError):
            windows_requirements(v)


def test_windows_layout_reuses_site_packages_when_deps_unchanged(tmp_path):
    run = FakeRun()
    lay = layout(tmp_path, run=run)
    a = make_version(lay.root, A)
    (a / "site-packages" / "PIL").mkdir(parents=True)
    (a / "site-packages" / "PIL" / "__init__.py").write_text("# pillow\n")
    lay.activate(a)
    b = make_version(lay.root, B)
    lay.prepare(b)
    assert run.calls == []  # no pip
    assert (b / "site-packages" / "PIL" / "__init__.py").read_text() == "# pillow\n"
    assert not list(b.glob(".site-packages*"))


def test_windows_layout_runs_pip_when_deps_changed(tmp_path):
    run = FakeRun()
    lay = layout(tmp_path, run=run)
    a = make_version(lay.root, A)
    (a / "site-packages").mkdir()
    lay.activate(a)
    b = make_version(lay.root, B, PYPROJECT.replace("pystray==0.19.5", "pystray==0.19.6"))
    lay.prepare(b)
    ((cmd),) = run.calls
    assert cmd[0] == str(lay.root / "python" / "python.exe")
    assert cmd[1:3] == ["-I", "-c"]
    assert cmd[4] == str(lay.root / "pip.whl")
    assert "install" in cmd and "--only-binary=:all:" in cmd
    target = cmd[cmd.index("--target") + 1]
    assert Path(target).parent == b
    assert cmd[-4:] == [
        "hidapi==0.14.0.post4",
        "pystray==0.19.6",
        "pillow>=11",
        "icoextract==0.3.1",
    ]
    assert (b / "site-packages").is_dir()

    failing = layout(tmp_path, run=FakeRun(returncode=1))
    with pytest.raises(UpdateError):
        failing.prepare(b)


def test_windows_layout_privileged_changes(tmp_path):
    lay = layout(tmp_path)
    new = make_version(lay.root, B)
    installed = lay.program_files
    for name in (
        "helper/rgbhub_ram_helper.py",
        "helper/rgbhub_ram_helper_windows.py",
        "install/windows/finish-setup.ps1",
        "install/windows/uninstall.ps1",
        "install/windows/approve-privileged.ps1",
    ):
        (installed / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(new / name, installed / name)
    assert lay.privileged_changes(None, new) == []

    (new / "helper/rgbhub_ram_helper_windows.py").write_text("# changed\n")
    (installed / "install/windows/uninstall.ps1").unlink()
    (new / "install/windows/RGB-Hub-setup.cmd").write_text("@echo changed\n")  # not privileged
    assert lay.privileged_changes(None, new) == [
        "helper/rgbhub_ram_helper_windows.py",
        "install/windows/uninstall.ps1",
    ]


def test_windows_layout_request_privileged_goes_through_the_hub(tmp_path):
    hub = FakeHub()
    lay = layout(tmp_path, urlopen=hub)
    write_runtime(lay.data, port=5000, token="secret")
    new = make_version(lay.root, B)
    lay.request_privileged(new, ["helper/rgbhub_ram_helper_windows.py"])
    assert hub.requests == [
        (
            "http://127.0.0.1:5000/api/approve-pending",
            "secret",
            {"version": B, "changes": ["helper/rgbhub_ram_helper_windows.py"]},
        )
    ]
    assert not (lay.data / "pending-privileged.json").exists()  # the hub writes it


def test_windows_layout_request_privileged_without_the_hub_leaves_it_waiting(tmp_path):
    lay = layout(tmp_path, urlopen=FakeHub(error=OSError("refused")))
    new = make_version(lay.root, B)
    lay.request_privileged(new, ["helper/rgbhub_ram_helper.py"])  # no runtime.json at all
    pending = json.loads((lay.data / "pending-privileged.json").read_text())
    assert pending == {"version": B, "changes": ["helper/rgbhub_ram_helper.py"]}


def test_windows_layout_activate_writes_current_txt(tmp_path):
    lay = layout(tmp_path)
    assert lay.current_commit() is None
    a, b = make_version(lay.root, A), make_version(lay.root, B)
    lay.activate(a)
    assert (lay.root / "current.txt").read_text() == A + "\n"
    lay.activate(b)
    assert lay.current_commit() == B
    assert not list(lay.root.glob(".current*"))
    (lay.root / "current.txt").write_text("..\\..\n")
    assert lay.current_commit() is None


def test_windows_layout_self_check_asks_the_launcher(tmp_path):
    run = FakeRun()
    lay = layout(tmp_path, run=run)
    v = make_version(lay.root, A)
    assert lay.self_check(v)
    assert run.calls == [
        [
            str(lay.root / "python" / "python.exe"),
            "-I",
            str(lay.root / "launcher.py"),
            "--root",
            str(lay.root),
            "--self-check",
            A,
        ]
    ]
    assert not layout(tmp_path, run=FakeRun(returncode=1)).self_check(v)


def test_windows_layout_restart_and_health(tmp_path):
    hub = FakeHub()
    run = FakeRun()
    lay = layout(tmp_path, urlopen=hub, run=run)
    write_runtime(lay.data, pid=100, started=1.0)
    lay.activate(make_version(lay.root, A))

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        write_runtime(lay.data, pid=200, started=2.0)  # the launcher started the new hub

    lay._sleep = sleep
    lay.restart_hub()
    assert hub.requests[-1][0] == "http://127.0.0.1:4321/api/restart"
    assert run.calls == []
    assert lay.hub_healthy(0)
    assert (lay.root / "updater" / "run.py").is_file()  # a healthy version's updater

    # The new hub went away during the wait: not healthy.
    lay2 = layout(tmp_path / "2", urlopen=FakeHub(), pid_alive=lambda pid: pid != 300)
    write_runtime(lay2.data, pid=100, started=1.0)
    lay2.restart_hub()
    seen = []

    def sleep2(seconds):
        seen.append(seconds)
        write_runtime(lay2.data, pid=300, started=3.0)

    lay2._sleep = sleep2
    assert not lay2.hub_healthy(0)

    # No new start at all.
    lay3 = layout(tmp_path / "3", urlopen=FakeHub())
    write_runtime(lay3.data, pid=100, started=1.0)
    lay3.restart_hub()
    assert not lay3.hub_healthy(0)


def test_windows_layout_restart_without_the_hub_leaves_a_quit_hub_alone(tmp_path):
    """No runtime.json and nothing to ask: the owner quit the hub. Do not start it,
    and do not count that against the new version."""
    run = FakeRun()
    lay = layout(tmp_path, run=run, urlopen=FakeHub(error=OSError("refused")))
    lay.restart_hub()
    assert run.calls == []
    assert lay.hub_healthy(0)
    assert not (lay.root / "updater" / "run.py").exists()  # not seen working: no new updater


def test_windows_layout_restart_by_task_waits_for_the_old_hub_to_go(tmp_path):
    run = FakeRun()
    alive = {100: 3}  # still running for three checks after the task is ended

    def pid_alive(pid):
        alive[pid] -= 1
        return alive[pid] >= 0

    sleeps = []
    lay = layout(
        tmp_path,
        run=run,
        urlopen=FakeHub(error=OSError("refused")),
        pid_alive=pid_alive,
        sleep=sleeps.append,
    )
    write_runtime(lay.data, pid=100)
    lay.restart_hub()
    assert [c[:2] for c in run.calls] == [["schtasks", "/End"], ["schtasks", "/Run"]]
    assert sleeps  # it waited for the process to end
    assert not any("taskkill" in c[0].lower() for c in run.calls)


def test_windows_layout_restart_by_task_kills_a_hub_that_will_not_go(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    run = FakeRun()
    sleeps = []
    lay = layout(
        tmp_path,
        run=run,
        urlopen=FakeHub(error=OSError("refused")),
        pid_alive=lambda pid: True,
        sleep=sleeps.append,
    )
    write_runtime(lay.data, pid=100)
    lay.restart_hub()
    assert [c[0] for c in run.calls][0] == "schtasks"
    kill = run.calls[1]
    assert kill[0].lower().endswith("taskkill.exe") and "System32" in kill[0]
    assert kill[1:] == ["/PID", "100", "/T", "/F"]
    assert run.calls[2] == ["schtasks", "/Run", "/TN", "RGB-Hub"]
    assert 15 <= sum(sleeps) <= 25


def test_windows_layout_notify_through_the_hub_else_log(tmp_path, caplog):
    hub = FakeHub()
    lay = layout(tmp_path, urlopen=hub)
    write_runtime(lay.data)
    lay.notify("RGB-Hub updated to aaaaaaa")
    assert hub.requests == [
        ("http://127.0.0.1:4321/api/notify", "tok", {"text": "RGB-Hub updated to aaaaaaa"})
    ]
    lay = layout(tmp_path, urlopen=FakeHub(status=403))
    with caplog.at_level("INFO", logger="rgbhub.updater"):
        lay.notify("hello there")
    assert "hello there" in caplog.text


def test_windows_layout_never_busy_or_paused(tmp_path):
    lay = layout(tmp_path)
    assert not lay.busy() and not lay.paused()


def test_windows_default_root(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "/x/Local")
    assert win_layout.default_root() == Path("/x/Local/Programs/RGB-Hub")


def wheel(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_unpack_wheels(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel(
        wheels / "hidapi-0.14.0.post4-cp313-cp313-win_amd64.whl",
        {
            "hid.cp313-win_amd64.pyd": b"binary",
            "hidapi-0.14.0.post4.dist-info/METADATA": b"Name: hidapi",
        },
    )
    wheel(
        wheels / "odd-1.0-py3-none-any.whl",
        {
            "odd/__init__.py": b"",
            "odd-1.0.data/purelib/odd_extra.py": b"# extra",
            "odd-1.0.data/scripts/odd.exe": b"no scripts",
        },
    )
    target = tmp_path / "site-packages"
    unpack_wheels(wheels, target)
    assert (target / "hid.cp313-win_amd64.pyd").read_bytes() == b"binary"
    assert (target / "hidapi-0.14.0.post4.dist-info" / "METADATA").is_file()
    assert (target / "odd" / "__init__.py").is_file()
    assert (target / "odd_extra.py").read_bytes() == b"# extra"
    assert not (target / "odd-1.0.data").exists()

    wheel(wheels / "evil-1.0-py3-none-any.whl", {"../escape.py": b"x"})
    with pytest.raises(UpdateError):
        unpack_wheels(wheels, tmp_path / "other")
    assert not (tmp_path / "escape.py").exists()


def test_first_install_from_ubuntu_unpacks_the_wheels(tmp_path):
    """What install-windows.sh runs: the Store install with wheels, no self-check
    (Windows' Python cannot run on Ubuntu)."""
    source = make_version(tmp_path / "src", A)
    zip_path = tmp_path / "rgbhub.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, f"rgbhub-{A}/{path.relative_to(source)}")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel(wheels / "pystray-0.19.5-py2.py3-none-any.whl", {"pystray/__init__.py": b"#"})
    root = tmp_path / "Local" / "Programs" / "RGB-Hub"
    lay = WindowsLayout(root, wheels=wheels, run=FakeRun(returncode=1))  # nothing may be run
    install_mod.install(Store(root), lay, zip_path, A, "windows", check=False)
    assert (root / "current.txt").read_text() == A + "\n"
    assert (root / "versions" / A / "site-packages" / "pystray" / "__init__.py").is_file()
    assert (root / "updater" / "run.py").is_file()
    assert Store(root).load()["current"] == A


# ---- the scripts ------------------------------------------------------------------


SCRIPTS = sorted(p for p in (INSTALL / "windows").glob("*") if p.is_file())


def test_install_windows_sh_parses():
    done = subprocess.run(["bash", "-n", str(INSTALL / "install-windows.sh")], capture_output=True)
    assert done.returncode == 0, done.stderr


def test_windows_scripts_are_ascii():
    names = {p.name for p in SCRIPTS}
    assert {
        "finish-setup.ps1",
        "uninstall.ps1",
        "approve-privileged.ps1",
        "RGB-Hub-setup.cmd",
    } <= names
    for path in SCRIPTS:
        path.read_bytes().decode("ascii")  # Windows PowerShell 5.1 reads files as ANSI


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell (pwsh) is not installed")
@pytest.mark.parametrize("path", [p for p in SCRIPTS if p.suffix == ".ps1"], ids=lambda p: p.name)
def test_powershell_scripts_parse(path):
    command = (
        "$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path}', [ref]$null, [ref]$e) | Out-Null; "
        "if ($e.Count) { $e | ForEach-Object { $_.ToString() }; exit 1 }"
    )
    done = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_setup_cmd_does_nothing_in_a_virtual_machine():
    text = (INSTALL / "windows" / "RGB-Hub-setup.cmd").read_text()
    assert "Win32_ComputerSystem" in text and "QEMU" in text
    assert "-Verb RunAs" in text and "finish-setup.ps1" in text and "@USER@" in text
    assert "wmic" not in text.lower()


def test_finish_setup_names_the_tasks_and_services():
    text = (INSTALL / "windows" / "finish-setup.ps1").read_text()
    for needed in (
        "'RGB-Hub'",
        "'RGB-Hub update'",
        "'RGB-Hub RAM'",
        "AD2F1837.OMENCommandCenter",
        "Remove-AppxProvisionedPackage",
        "HPOmenCap",
        "HPOmenCustomCapDriver",
        "HpTouchpointAnalyticsService",
        "hp-one-agent-service",
        "disabled-services.json",
        "ram-key",
        "--key-file",
        "-RunLevel Highest",
        "QEMU",
        "setup.log",
    ):
        assert needed in text, needed
    assert "--owner-sid" not in text


# ---- admin-only Program Files, written from Ubuntu ------------------------------------


def load_install_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ntfs = load_install_module("ntfs_security", INSTALL / "ntfs_security.py")


def test_sid_bytes_match_windows():
    # Administrators as Windows stores it: revision 1, 2 sub-authorities, NT authority 5, 32, 544.
    assert ntfs.sid_bytes("S-1-5-32-544") == bytes.fromhex("01020000000000052000000020020000")
    assert ntfs.sid_bytes("S-1-5-18") == bytes.fromhex("010100000000000512000000")
    for sid in ("S-1-5-32-544", "S-1-5-18", "S-1-5-32-545"):
        assert ntfs.sid_text(ntfs.sid_bytes(sid)) == (sid, len(ntfs.sid_bytes(sid)))


@pytest.mark.parametrize("folder", [True, False])
def test_security_descriptor_decodes_back(folder):
    data = ntfs.descriptor(folder)
    assert len(data) == 20 + 16 + 12 + 8 + (8 + 16) + (8 + 12) + (8 + 16)
    # Header: revision 1, self-relative + DACL protected + DACL present, no SACL.
    assert data[:4] == bytes([1, 0]) + (0x8000 | 0x1000 | 0x0004).to_bytes(2, "little")
    got = ntfs.decode(data)
    inherit = 0x03 if folder else 0
    assert got == {
        "control": 0x9004,
        "owner": "S-1-5-32-544",
        "group": "S-1-5-18",
        "sacl": False,
        "aces": [
            (0, inherit, 0x001F01FF, "S-1-5-32-544"),
            (0, inherit, 0x001F01FF, "S-1-5-18"),
            (0, inherit, 0x001200A9, "S-1-5-32-545"),
        ],
    }
    assert ntfs.is_ours(data, folder)
    assert not ntfs.is_ours(ntfs.descriptor(not folder), folder)
    assert not ntfs.is_ours(b"\x01\x00\x04\x80" + bytes(16), folder)  # Everyone-style junk
    assert not ntfs.is_ours(b"", folder)


def test_secure_tree_sets_every_folder_and_file(tmp_path):
    (tmp_path / "pf" / "python").mkdir(parents=True)
    (tmp_path / "pf" / "python" / "pythonw.exe").write_bytes(b"MZ")
    (tmp_path / "pf" / "helper.py").write_text("#")
    store = {}

    def setxattr(path, name, value, follow_symlinks=True):
        assert name == "system.ntfs_security" and not follow_symlinks
        store[Path(path)] = value

    def getxattr(path, name, follow_symlinks=True):
        return store[Path(path)]

    assert ntfs.secure_tree(tmp_path / "pf", setxattr, getxattr) == 4
    assert store[tmp_path / "pf"] == ntfs.descriptor(True)
    assert store[tmp_path / "pf" / "python" / "pythonw.exe"] == ntfs.descriptor(False)

    with pytest.raises(OSError):  # read back as something else
        ntfs.secure_tree(tmp_path / "pf", setxattr, lambda p, n, follow_symlinks: b"junk")
    (tmp_path / "pf" / "link").symlink_to(tmp_path)
    with pytest.raises(OSError):
        ntfs.secure_tree(tmp_path / "pf", setxattr, getxattr)


def test_ntfs_security_fails_where_it_cannot_be_set(tmp_path, capsys):
    """Not an ntfs3 file system: exit 1, and install-windows.sh stops before copying."""
    assert ntfs.main([str(tmp_path)]) == 1
    assert "admin-only" in capsys.readouterr().err


def test_install_script_makes_program_files_admin_only_before_copying():
    text = (INSTALL / "install-windows.sh").read_text()
    first_secure = text.index('python3 -I "$snap/install/ntfs_security.py" "$pf_new"')
    first_copy = text.index('cp -r "$tmp/python" "$pf_new/python"')
    assert first_secure < first_copy
    assert text.count('ntfs_security.py" "$pf_new"') == 2  # again once everything is in
    assert 'mv "$pf_new" "$program"' in text  # swapped in whole
    assert "merge-base --is-ancestor" in text  # never backwards
    assert "s/$/\\r/" in text and '"$pf_new/install/windows/RGB-Hub-setup.cmd"' in text


# ---- an approved update is checked against GitHub ----------------------------------------


verify = load_install_module("verify_update", INSTALL / "windows" / "verify_update.py")


def approved_version(tmp_path, sha=A):
    v = make_version(tmp_path / "Programs" / "RGB-Hub", sha)
    (v / "install/windows/verify_update.py").write_text("# checker\n")
    return v


class FakeGitHub:
    def __init__(self, version, status="ahead", changed=None, error=None):
        self.version = version
        self.status = status
        self.changed = changed or {}
        self.error = error
        self.asked = []

    def json(self, url):
        self.asked.append(url)
        if self.error:
            raise self.error
        return {"status": self.status}

    def bytes(self, url):
        self.asked.append(url)
        prefix = f"{verify.RAW}/{self.version.name}/"
        assert url.startswith(prefix)
        name = url[len(prefix) :]
        return self.changed.get(name, (self.version / name).read_bytes())


def test_verified_update_stages_githubs_copies(tmp_path):
    v = approved_version(tmp_path)
    gh = FakeGitHub(v, status="identical")
    staging = tmp_path / "staging"
    names = verify.stage(v, staging, gh.json, gh.bytes)
    assert names == [
        "helper/rgbhub_ram_helper.py",
        "helper/rgbhub_ram_helper_windows.py",
        "install/windows/approve-privileged.ps1",
        "install/windows/finish-setup.ps1",
        "install/windows/uninstall.ps1",
        "install/windows/verify_update.py",
    ]
    assert gh.asked[0] == f"https://api.github.com/repos/Konfus-dev/RGB-Hub/compare/{A}...main"
    for name in names:
        assert (staging / name).read_bytes() == (v / name).read_bytes()
    assert not (staging / "install/windows/RGB-Hub-setup.cmd").exists()


def test_verified_update_refuses_a_changed_file(tmp_path):
    v = approved_version(tmp_path)
    gh = FakeGitHub(v, changed={"helper/rgbhub_ram_helper_windows.py": b"# the real one\n"})
    with pytest.raises(verify.Refused, match="rgbhub_ram_helper_windows.py"):
        verify.stage(v, tmp_path / "staging", gh.json, gh.bytes)
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("status", ["behind", "diverged", None])
def test_verified_update_refuses_a_commit_not_on_main(tmp_path, status):
    v = approved_version(tmp_path)
    gh = FakeGitHub(v, status=status)
    with pytest.raises(verify.Refused, match="main"):
        verify.stage(v, tmp_path / "staging", gh.json, gh.bytes)
    assert not (tmp_path / "staging").exists()


def test_verified_update_refuses_odd_folders_and_says_when_github_is_away(tmp_path, capsys):
    odd = tmp_path / "versions" / "not-a-commit"
    (odd / "helper").mkdir(parents=True)
    (odd / "helper" / "x.py").write_text("#")
    gh = FakeGitHub(odd)
    with pytest.raises(verify.Refused):
        verify.stage(odd, tmp_path / "staging", gh.json, gh.bytes)
    assert gh.asked == []

    v = approved_version(tmp_path)
    away = FakeGitHub(v, error=verify.Unreachable("cannot reach GitHub: offline"))
    with pytest.raises(verify.Unreachable):
        verify.stage(v, tmp_path / "staging", away.json, away.bytes)
    assert not (tmp_path / "staging").exists()


def test_verify_update_exit_codes(tmp_path, monkeypatch, capsys):
    v = approved_version(tmp_path)
    staging = str(tmp_path / "staging")

    def unreachable(version, staging):
        raise verify.Unreachable("cannot reach GitHub: offline")

    monkeypatch.setattr(verify, "stage", unreachable)
    assert verify.main(["--from", str(v), "--staging", staging]) == 2
    assert "try again later" in capsys.readouterr().err

    def refuse(version, staging):
        raise verify.Refused("helper/x.py differs")

    monkeypatch.setattr(verify, "stage", refuse)
    assert verify.main(["--from", str(v), "--staging", staging]) == 1
    monkeypatch.setattr(verify, "stage", lambda version, staging: ["helper/x.py"])
    assert verify.main(["--from", str(v), "--staging", staging]) == 0


def test_layout_counts_the_checker_as_privileged(tmp_path):
    lay = layout(tmp_path)
    new = approved_version(tmp_path)
    assert "install/windows/verify_update.py" in lay.privileged_changes(None, new)


def test_finish_setup_checks_approved_updates_and_refuses_links():
    text = (INSTALL / "windows" / "finish-setup.ps1").read_text()
    assert "verify_update.py" in text and "--staging" in text
    assert "Assert-NoReparse" in text and "ReparsePoint" in text
    assert "/setowner" in text  # Program Files only
    assert text.count("/setowner") == 1
    assert "/grant'" not in text and "(OI)(CI)F" not in text  # no grants on the owner's trees
    # The services' note is saved before each service is changed.
    body = text[text.index("function Disable-HpServices") :]
    assert body.index("Save-Record $saved") < body.index("sc.exe config")


@pytest.mark.parametrize("name", ["a b.py", "x%2f..py", "x#y.py", "ünï.py", "a?.ps1"])
def test_verified_update_refuses_odd_file_names(tmp_path, name):
    v = approved_version(tmp_path)
    folder = "helper" if name.endswith(".py") else "install/windows"
    (v / folder / name).write_text("#")
    gh = FakeGitHub(v)
    with pytest.raises(verify.Refused, match="odd file names"):
        verify.stage(v, tmp_path / "staging", gh.json, gh.bytes)
    assert gh.asked == []  # nothing was asked of GitHub
    assert not (tmp_path / "staging").exists()


def restore_function() -> str:
    text = (INSTALL / "install-windows.sh").read_text()
    start = text.index("restore_program_files() {")
    return text[start : text.index("\n}\n", start) + 3]


@pytest.mark.parametrize("program_there", [False, True])
def test_install_swap_is_always_restorable(tmp_path, program_there):
    """A swap stopped after the old copy was moved aside: the old copy goes back;
    with a new copy in place, the old one is removed."""
    program, old = tmp_path / "RGB-Hub", tmp_path / ".RGB-Hub.old"
    (old / "helper").mkdir(parents=True)
    (old / "helper" / "old.py").write_text("#")
    if program_there:
        (program / "helper").mkdir(parents=True)
    script = restore_function() + f'program="{program}"\npf_old="{old}"\nrestore_program_files\n'
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert not old.exists()
    if program_there:
        assert not (program / "helper" / "old.py").exists()
    else:
        assert (program / "helper" / "old.py").is_file()


def test_uninstall_never_deletes_through_links():
    text = (INSTALL / "windows" / "uninstall.ps1").read_text()
    assert "function Find-Reparse" in text and "ReparsePoint" in text
    # Every recursive delete goes through Remove-Folder, which looks for links first.
    assert text.count("Remove-Item -LiteralPath $Folder -Recurse -Force") == 1
    assert len(re.findall(r"Remove-Item[^\n]*-Recurse", text)) == 1
    body = text[text.index("function Remove-Folder") :]
    assert body.index("Find-Reparse $Folder") < body.index("Remove-Item -LiteralPath $Folder")
    assert "Remove-Folder $Roaming" in text


def cleanup_function() -> str:
    text = (INSTALL / "install-windows.sh").read_text()
    start = text.index("cleanup() {")
    return text[start : text.index("\n}\n", start) + 3]


def test_cleanup_remounts_read_only_even_when_every_earlier_step_fails(tmp_path):
    """The exit trap runs under `set -e`. A failing restore (or sync, or rm) must not
    stop it before Windows is made read-only again. Commands are fakes that log."""
    log = tmp_path / "log"
    script = f"""set -euo pipefail
mount() {{ echo "mount $*" >> "{log}"; }}
umount() {{ echo "umount $*" >> "{log}"; }}
sync() {{ echo sync >> "{log}"; return 1; }}
rm() {{ echo "rm $*" >> "{log}"; return 1; }}
restore_program_files() {{ return 1; }}
tmp=/fake/tmp; mounted_here=1; made_rw=1; pf_new=/fake/pf_new; mnt=/fake/mnt
{cleanup_function()}
trap cleanup EXIT
exit 3
"""
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert done.returncode == 3, done.stderr  # the original status survives
    lines = log.read_text().splitlines()
    assert "mount -o remount,ro /fake/mnt" in lines
    assert "umount /fake/mnt" in lines
    assert "rm -rf /fake/tmp" in lines
    assert lines.index("mount -o remount,ro /fake/mnt") < lines.index("umount /fake/mnt")


def test_install_from_ubuntu_ignores_a_lock_left_by_windows(tmp_path):
    """A pid in Windows' update.lock means nothing on Ubuntu (it may even be a live
    Linux process): with foreign_lock the install is not blocked; without it, it is."""
    source = make_version(tmp_path / "src", A)
    zip_path = tmp_path / "rgbhub.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, f"rgbhub-{A}/{path.relative_to(source)}")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    root = tmp_path / "Local" / "Programs" / "RGB-Hub"
    root.mkdir(parents=True)
    (root / "update.lock").write_text(str(os.getpid()))  # a pid that is alive here
    lay = WindowsLayout(root, wheels=wheels, run=FakeRun(returncode=1))
    with pytest.raises(UpdateError, match="running"):
        install_mod.install(Store(root), lay, zip_path, A, "windows", check=False)
    install_mod.install(Store(root), lay, zip_path, A, "windows", check=False, foreign_lock=True)
    assert (root / "current.txt").read_text() == A + "\n"
    assert not (root / "update.lock").exists()
    assert "foreign_lock=True" in (INSTALL / "install-windows.sh").read_text()
