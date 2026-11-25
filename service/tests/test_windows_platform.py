# SPDX-License-Identifier: GPL-3.0-or-later
"""The Windows platform's main, session events, tray and launcher (the parts that run on Linux)."""

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from rgbhub import __main__ as rgbhub_main
from rgbhub.core.interfaces import FakeHidBus, FakeRamBus, ManualRuntime
from rgbhub.core.json_store import JsonSettings
from rgbhub.core.thread_runtime import ThreadRuntime
from rgbhub.platforms import windows
from rgbhub.platforms.windows import launcher, session, tray

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parent


# --- detect / self-check ---------------------------------------------------------------


def test_detect_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert rgbhub_main.detect() == "windows"


def test_detect_linux_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert rgbhub_main.detect() in ("gnome", "batocera")


def test_self_check_windows():
    """Imports every Windows module on Linux: no Win32 calls, no pystray, at import."""
    result = subprocess.run(
        [sys.executable, "-m", "rgbhub", "--self-check", "--platform", "windows"],
        cwd=SERVICE,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_windows_modules_import_without_pystray():
    code = (
        "import sys; sys.modules['pystray'] = None; sys.modules['hid'] = None;"
        "import rgbhub.platforms.windows, rgbhub.platforms.windows.session,"
        " rgbhub.platforms.windows.tray, rgbhub.platforms.windows.launcher"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=SERVICE, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


# --- launcher --------------------------------------------------------------------------


def make_root(tmp_path, *versions, current=None):
    root = tmp_path / "RGB-Hub"
    for version in versions:
        (root / "versions" / version / "service").mkdir(parents=True)
        (root / "versions" / version / "site-packages").mkdir()
    if current:
        (root / "current.txt").write_text(current + "\n")
    return root


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_launcher_reads_current_and_restarts_on_75(tmp_path):
    root = make_root(tmp_path, "aaa", "bbb", current="aaa")
    commands = []

    def spawn(command):
        commands.append(command)
        if len(commands) == 1:
            (root / "current.txt").write_text("bbb")  # the updater activated a new version
            return launcher.EXIT_RESTART
        return launcher.EXIT_QUIT

    sleeps = []
    code = launcher.supervise(root, spawn, python="pythonw.exe", sleep=sleeps.append)
    assert code == 0
    assert len(commands) == 2
    first, second = commands
    assert str(root / "versions" / "aaa" / "service") in first
    assert str(root / "versions" / "aaa" / "site-packages") in first
    assert str(root / "versions" / "bbb" / "service") in second
    assert str(root / "versions" / "bbb" / "site-packages") in second
    assert first[0] == "pythonw.exe"
    assert first[-2:] == ["--platform", "windows"]
    assert sleeps == []  # a restart request is carried out at once


def test_launcher_backoff_on_crash(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    clock = FakeClock()
    codes = iter([1, 1, 1, 1, 1, 1, 1, 1, 0])
    sleeps = []

    def spawn(command):
        clock.now += 1  # crashes after a second
        return next(codes)

    code = launcher.supervise(root, spawn, python="py", sleep=sleeps.append, clock=clock)
    assert code == 0
    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 300]


def test_launcher_backoff_resets_after_long_uptime(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    clock = FakeClock()
    runs = iter([(1, 1), (1, 1), (1, 1), (200, 1), (1, 1), (1, 0)])
    sleeps = []

    def spawn(command):
        uptime, code = next(runs)
        clock.now += uptime
        return code

    assert launcher.supervise(root, spawn, python="py", sleep=sleeps.append, clock=clock) == 0
    assert sleeps == [5, 10, 20, 5, 10]


def test_launcher_without_a_version_gives_up(tmp_path):
    root = make_root(tmp_path, "aaa")  # no current.txt
    spawned = []
    assert launcher.supervise(root, spawned.append, python="py", sleep=lambda s: None) == 1
    assert spawned == []


@pytest.mark.parametrize("text", ["", "..", "../aaa", "a/b", "a\\b", "missing"])
def test_launcher_refuses_odd_current(tmp_path, text):
    root = make_root(tmp_path, "aaa")
    (root / "current.txt").write_text(text)
    assert launcher.read_current(root) is None


def test_launcher_child_runs_the_hub_from_the_version(tmp_path):
    """The child command really puts the version's folders first and runs rgbhub."""
    root = tmp_path / "root"
    service = root / "versions" / "v1" / "service" / "rgbhub"
    service.mkdir(parents=True)
    (root / "versions" / "v1" / "site-packages").mkdir()
    (service / "__init__.py").write_text("")
    (service / "__main__.py").write_text(
        "import sys\ndef main(argv):\n    print('ran', argv)\n    return 75 if argv else 0\n"
    )
    command = launcher.child_command(
        sys.executable, launcher.version_paths(root, "v1"), ["--platform", "windows"]
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 75, result.stderr
    assert "ran ['--platform', 'windows']" in result.stdout


def test_launcher_self_check_runs_the_named_version(tmp_path):
    root = make_root(tmp_path, "aaa", "bbb", current="aaa")
    commands = []

    def spawn(command):
        commands.append(command)
        return 0

    assert launcher.main(["--root", str(root), "--self-check", "bbb"], spawn=spawn) == 0
    (command,) = commands
    assert str(root / "versions" / "bbb" / "service") in command
    assert command[-3:] == ["--self-check", "--platform", "windows"]


def test_launcher_pythonw_beside_python(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    assert launcher.windowless_python(str(tmp_path / "python.exe")) == str(tmp_path / "pythonw.exe")
    assert launcher.windowless_python("/usr/bin/python3") == "/usr/bin/python3"


def test_launcher_is_stdlib_only():
    source = Path(launcher.__file__).read_text()
    assert "from ." not in source and "import rgbhub" not in source
    assert "ctypes" not in source


# --- session events ----------------------------------------------------------------------


def make_session(runtime=None):
    calls = []
    watcher = session.SessionWatcher(
        runtime or ManualRuntime(),
        on_sleep=lambda: calls.append("sleep"),
        on_resume=lambda: calls.append("resume"),
        on_end=lambda: calls.append("end"),
    )
    return watcher, calls


def test_session_sleep_and_resume():
    watcher, calls = make_session()
    assert watcher.handle(session.WM_POWERBROADCAST, session.PBT_APMSUSPEND, 0) == 1
    assert watcher.handle(session.WM_POWERBROADCAST, session.PBT_APMRESUMEAUTOMATIC, 0) == 1
    assert watcher.handle(session.WM_POWERBROADCAST, 7, 0) == 1  # PBT_APMRESUMESUSPEND: ignored
    assert calls == ["sleep", "resume"]


def test_session_end():
    watcher, calls = make_session()
    assert watcher.handle(session.WM_QUERYENDSESSION, 0, 0) == 1  # never holds sign-out back
    assert watcher.handle(session.WM_ENDSESSION, 0, 0) == 0  # cancelled: nothing to do
    assert calls == []
    assert watcher.handle(session.WM_ENDSESSION, 1, 0) == 0
    assert calls == ["end"]


def test_session_other_messages_go_to_windows():
    watcher, _calls = make_session()
    assert watcher.handle(0x000F, 0, 0) is None  # WM_PAINT: DefWindowProc


def test_session_end_waits_for_the_loop():
    """on_end runs on the hub's loop, and the window waits until it is done."""
    runtime = ThreadRuntime()
    seen = []

    def on_end():
        seen.append(threading.current_thread().name)

    watcher = session.SessionWatcher(runtime, lambda: None, lambda: None, on_end)
    loop = threading.Thread(target=runtime.run_until_stopped, name="loop")
    loop.start()
    try:
        watcher.handle(session.WM_ENDSESSION, 1, 0)
        assert seen == ["loop"]
    finally:
        runtime.stop()
        loop.join(5)


def test_session_wait_is_bounded():
    class NeverRuns:
        def call_soon(self, fn):
            pass

    watcher = session.SessionWatcher(NeverRuns(), lambda: None, lambda: None, lambda: None)
    watcher.END_WAIT_S = 0.05
    watcher.handle(session.WM_ENDSESSION, 1, 0)  # returns, does not hang


def test_session_after_the_loop_closed():
    runtime = ThreadRuntime()
    runtime.stop()
    runtime.run_until_stopped()  # closes the loop
    watcher = session.SessionWatcher(runtime, lambda: None, lambda: None, lambda: None)
    assert watcher.handle(session.WM_ENDSESSION, 1, 0) == 0


def test_session_start_off_windows_is_harmless():
    watcher, _calls = make_session()
    watcher.start()
    watcher.stop()


# --- tray -------------------------------------------------------------------------------


def make_tray(approve=None):
    state = {"auto": False, "calls": []}
    t = tray.Tray(
        open_settings=lambda: state["calls"].append("settings"),
        set_auto_all=lambda on: state.update(auto=on),
        auto_all=lambda: state["auto"],
        quit=lambda: state["calls"].append("quit"),
        approve_update=approve,
    )
    return t, state


def entry(t, text):
    return next(e for e in t.entries() if e.text == text)


def test_tray_menu():
    t, state = make_tray()
    assert [e.text for e in t.entries()] == ["Settings", "Auto mode", "Approve update…", "Quit"]
    assert entry(t, "Settings").default  # a left click opens the settings
    assert not entry(t, "Approve update…").visible()
    assert entry(t, "Auto mode").checked() is False
    entry(t, "Auto mode").action()
    assert state["auto"] is True and entry(t, "Auto mode").checked() is True
    entry(t, "Settings").action()
    entry(t, "Quit").action()
    assert state["calls"] == ["settings", "quit"]


def test_tray_approve_update_shown_when_offered():
    approved = []
    t, _state = make_tray(approve=lambda: approved.append(1))
    assert entry(t, "Approve update…").visible()
    entry(t, "Approve update…").action()
    assert approved == [1]
    t.offer_update(None)
    assert not entry(t, "Approve update…").visible()


def test_tray_action_errors_are_logged_not_raised(caplog):
    t = tray.Tray(lambda: 1 / 0, lambda on: None, lambda: False, lambda: None)
    with caplog.at_level(logging.ERROR):
        entry(t, "Settings").action()
    assert "Settings" in caplog.text


def test_tray_icon_is_drawn():
    image = tray.draw_icon(64)
    assert isinstance(image, Image.Image)
    assert image.size == (64, 64) and image.mode == "RGBA"
    assert len(image.getcolors(maxcolors=64 * 64)) > 3  # not a blank square


def test_tray_without_start_is_harmless():
    t, _state = make_tray()
    t.refresh()
    t.stop()


# --- platform main: places, settings, runtime file ------------------------------------------


def test_places_from_environment(tmp_path):
    places = windows.Places.from_environ(
        {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
    )
    assert places.settings_file == tmp_path / "Roaming" / "rgbhub" / "settings.json"
    assert places.lcd_folder == tmp_path / "Roaming" / "rgbhub" / "lcd"
    assert places.pictures == tmp_path / "Roaming" / "rgbhub" / "pictures"
    assert places.data == tmp_path / "Local" / "rgbhub"
    assert places.runtime_file == tmp_path / "Local" / "rgbhub" / "runtime.json"
    assert places.log_file == tmp_path / "Local" / "rgbhub" / "hub.log"


def test_page_dir_is_next_to_the_installed_service():
    # <version>\service\rgbhub\... and <version>\frontends\web\ (here: the repo)
    assert windows.page_dir() == REPO / "frontends" / "web"
    assert (windows.page_dir() / "index.html").is_file()


def test_runtime_file(tmp_path):
    path = tmp_path / "Local" / "rgbhub" / "runtime.json"
    windows.write_runtime_file(path, pid=42, port=5000, token="secret", now=lambda: 12.5)
    assert json.loads(path.read_text()) == {
        "pid": 42,
        "port": 5000,
        "token": "secret",
        "started": 12.5,
    }
    assert path.stat().st_mode & 0o077 == 0  # the owner's alone
    assert list(path.parent.iterdir()) == [path]  # no temporary file left


def test_auto_all_and_set_auto_all(tmp_path):
    settings = JsonSettings(tmp_path / "settings.json")
    assert windows.auto_all(settings) is False
    windows.set_auto_all(settings, True)
    assert settings.get("auto-devices") == {"OMEN-PC": True, "IQUNIX-KEYBOARD": True}
    assert windows.auto_all(settings) is True
    settings.set("auto-devices", {"OMEN-PC": True, "IQUNIX-KEYBOARD": False})
    assert windows.auto_all(settings) is False


def test_idle_picture_prefers_lcd_folder_then_wallpaper_then_logo(tmp_path):
    folder = tmp_path / "lcd"
    wallpaper_file = tmp_path / "wall.png"
    Image.new("RGB", (100, 60), "red").save(wallpaper_file)

    # nothing in the folder, a wallpaper: the wallpaper
    picture = windows.idle_picture(folder, wallpaper=lambda: wallpaper_file)
    assert picture.size == (100, 60) and picture.getpixel((0, 0))[:3] == (255, 0, 0)

    # no wallpaper either: the Windows logo
    picture = windows.idle_picture(folder, wallpaper=lambda: None)
    assert picture.size == (480, 480)

    # an unreadable wallpaper: the logo, no exception
    broken = tmp_path / "broken.png"
    broken.write_text("not a picture")
    assert windows.idle_picture(folder, wallpaper=lambda: broken).size == (480, 480)

    # a picture in the folder wins
    folder.mkdir()
    Image.new("RGB", (50, 50), "blue").save(folder / "mine.png")
    picture = windows.idle_picture(folder, wallpaper=lambda: wallpaper_file)
    assert picture.getpixel((0, 0))[:3] == (0, 0, 255)


def test_wallpaper_watch_calls_on_change_only_when_it_changes():
    signatures = iter(["a", "a", "b", "b", "a"])
    changes = []
    poll = windows.change_watch(lambda: next(signatures), lambda: changes.append(1))
    for _ in range(4):
        assert poll() is True
    assert changes == [1, 1]


# --- VM guard, settings window ---------------------------------------------------------


class FakeRun:
    def __init__(self, stdout="", error=None):
        self.stdout, self.error, self.commands = stdout, error, []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, 0, self.stdout, "")


@pytest.mark.parametrize(
    "stdout, inside",
    [
        ("QEMU\nStandard PC (Q35 + ICH9, 2009)\n", True),
        ("Microsoft Corporation\nVirtual Machine\n", True),
        ("VMware, Inc.\nVMware Virtual Platform\n", True),
        ("HP\nOMEN 45L Gaming Desktop GT22-1xxx\n", False),
        ("", False),
    ],
)
def test_in_virtual_machine(stdout, inside):
    run = FakeRun(stdout)
    assert windows.in_virtual_machine(run=run) is inside
    assert "Win32_ComputerSystem" in " ".join(run.commands[0])


def test_in_virtual_machine_when_powershell_fails():
    assert windows.in_virtual_machine(run=FakeRun(error=OSError("no powershell"))) is False
    error = subprocess.TimeoutExpired("powershell", 30)
    assert windows.in_virtual_machine(run=FakeRun(error=error)) is False


def test_in_virtual_machine_off_windows_asks_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert windows.in_virtual_machine() is False


def test_main_does_nothing_in_a_virtual_machine(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(windows, "in_virtual_machine", lambda: True)
    monkeypatch.setattr(windows, "run", lambda *a, **k: pytest.fail("started in a VM"))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert windows.main([]) == 0
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
    assert "virtual machine" in (tmp_path / "Local" / "rgbhub" / "hub.log").read_text()


def test_open_settings_window_uses_edge_app_mode(tmp_path):
    opened = []
    windows.open_settings_window(
        "http://127.0.0.1:1/?t=x",
        find=lambda: "C:/Edge/msedge.exe",
        popen=opened.append,
        browser=lambda url: pytest.fail("used the browser"),
    )
    assert opened == [
        ["C:/Edge/msedge.exe", "--app=http://127.0.0.1:1/?t=x", "--window-size=900,760"]
    ]


def test_open_settings_window_falls_back_to_the_browser():
    shown = []
    windows.open_settings_window("http://x/", find=lambda: None, browser=shown.append)
    assert shown == ["http://x/"]

    def broken(command):
        raise OSError("gone")

    windows.open_settings_window(
        "http://y/", find=lambda: "edge", popen=broken, browser=shown.append
    )
    assert shown == ["http://x/", "http://y/"]


def test_find_edge(tmp_path):
    edge = tmp_path / "x86" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    environ = {"ProgramFiles(x86)": str(tmp_path / "x86")}
    assert windows.find_edge(which=lambda name: None, environ=environ) == str(edge)
    assert windows.find_edge(which=lambda name: "/bin/msedge", environ=environ) == "/bin/msedge"
    assert windows.find_edge(which=lambda name: None, environ={}) is None


# --- the whole platform on fakes ----------------------------------------------------------


class FakeTray:
    instances = []

    def __init__(self, open_settings, set_auto_all, auto_all, quit, approve_update=None):
        self.open_settings, self.set_auto_all = open_settings, set_auto_all
        self.auto_all, self.quit = auto_all, quit
        self.refreshes = 0
        self.stopped = False
        FakeTray.instances.append(self)

    def start(self):
        pass

    def refresh(self):
        self.refreshes += 1

    def stop(self):
        self.stopped = True


class FakeSession:
    instances = []

    def __init__(self, runtime, on_sleep, on_resume, on_end):
        self.runtime = runtime
        self.on_sleep, self.on_resume, self.on_end = on_sleep, on_resume, on_end
        self.started = self.stopped = False
        FakeSession.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def run_platform(tmp_path, act):
    """Run windows.run() on fakes; `act(tray, session, places)` runs on another thread."""
    FakeTray.instances.clear()
    FakeSession.instances.clear()
    places = windows.Places.from_environ(
        {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
    )
    opened = []
    runtime = ThreadRuntime(poll_s=0.05)

    def drive():
        for _ in range(200):
            if FakeTray.instances and places.runtime_file.exists():
                break
            threading.Event().wait(0.02)
        act(FakeTray.instances[0], FakeSession.instances[0], places)

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    code = windows.run(
        places,
        hid_bus=FakeHidBus({}),
        ram_bus=FakeRamBus([]),
        runtime=runtime,
        tray_factory=FakeTray,
        session_factory=FakeSession,
        open_window=opened.append,
    )
    driver.join(5)
    return code, places, opened


def test_run_quits_from_the_tray(tmp_path):
    def act(t, s, places):
        assert s.started
        t.open_settings()
        t.set_auto_all(True)
        threading.Event().wait(0.2)
        t.quit()

    code, places, opened = run_platform(tmp_path, act)
    assert code == 0
    t, s = FakeTray.instances[0], FakeSession.instances[0]
    assert t.stopped and s.stopped
    assert t.refreshes >= 1  # the tick follows the setting
    info = json.loads(places.runtime_file.read_text())
    assert info["pid"] > 0 and info["port"] > 0 and info["token"]
    assert len(opened) == 1 and opened[0].startswith(f"http://127.0.0.1:{info['port']}/?t=")
    saved = json.loads(places.settings_file.read_text())
    assert saved["auto-devices"] == {"OMEN-PC": True, "IQUNIX-KEYBOARD": True}


def test_run_restarts_on_request_from_the_web_page(tmp_path):
    import http.client

    def act(t, s, places):
        info = json.loads(places.runtime_file.read_text())
        connection = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=5)
        connection.request(
            "POST",
            "/api/restart",
            body=b"{}",
            headers={"X-RGBHub-Token": info["token"], "Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 204

    code, _places, _opened = run_platform(tmp_path, act)
    assert code == 75


def test_run_ends_with_the_session(tmp_path):
    def act(t, s, places):
        s.runtime.call_soon(s.on_end)  # what SessionWatcher does: on the loop

    code, _places, _opened = run_platform(tmp_path, act)
    assert code == 0


def test_run_carries_on_without_a_tray_icon(tmp_path, monkeypatch):
    def broken_start(self):
        raise ImportError("no pystray")

    monkeypatch.setattr(FakeTray, "start", broken_start)

    def act(t, s, places):
        t.quit()  # the menu is gone on Windows, but Quit still ends it here

    code, _places, _opened = run_platform(tmp_path, act)
    assert code == 0
    assert not FakeTray.instances[0].stopped  # never started, so not stopped either


def test_launcher_self_check_refuses_odd_versions(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    for name in ("..", "missing", "../aaa"):
        assert launcher.main(["--root", str(root), "--self-check", name], spawn=pytest.fail) == 1
