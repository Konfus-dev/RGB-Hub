# SPDX-License-Identifier: GPL-3.0-or-later
"""Installing on Windows from Ubuntu, and Windows' update layout.

Nothing here touches Windows or the real system: the Windows partition, the
install folders and the hub's runtime.json are temporary folders, programs
and HTTP requests go to fakes."""

import importlib.util
import io
import json
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


def test_windows_layout_restart_without_the_hub_uses_the_task(tmp_path):
    run = FakeRun()
    lay = layout(tmp_path, run=run, urlopen=FakeHub(error=OSError("refused")))
    lay.restart_hub()  # no runtime.json: the hub isn't running
    assert run.calls == [
        ["schtasks", "/End", "/TN", "RGB-Hub"],
        ["schtasks", "/Run", "/TN", "RGB-Hub"],
    ]


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


SCRIPTS = sorted((INSTALL / "windows").glob("*"))


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
