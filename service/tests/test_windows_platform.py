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
    assert sleeps == [launcher.RESTART_PAUSE_S]  # a short pause, no back-off


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


def test_launcher_counts_quick_restart_requests(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    clock = FakeClock()
    codes = iter([75] * 7 + [0])
    sleeps = []

    def spawn(command):
        clock.now += 1
        return next(codes)

    assert launcher.supervise(root, spawn, python="py", sleep=sleeps.append, clock=clock) == 0
    # five quick restarts with the short pause, then it is treated as a crash loop
    assert sleeps == [2, 2, 2, 2, 2, 5, 10]


def test_launcher_restart_requests_after_long_runs_are_not_a_loop(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    clock = FakeClock()
    codes = iter([75] * 8 + [0])
    sleeps = []

    def spawn(command):
        clock.now += 3600  # an hour between updates
        return next(codes)

    assert launcher.supervise(root, spawn, python="py", sleep=sleeps.append, clock=clock) == 0
    assert sleeps == [2] * 8


def test_launcher_falls_back_to_the_last_version(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    commands = []

    def spawn(command):
        commands.append(command)
        if len(commands) == 1:
            (root / "current.txt").write_text("not-there")  # a broken update
            return launcher.EXIT_RESTART
        return launcher.EXIT_QUIT

    assert launcher.supervise(root, spawn, python="py", sleep=lambda s: None) == 0
    assert len(commands) == 2
    assert str(root / "versions" / "aaa" / "service") in commands[1]


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


def test_launcher_self_check_takes_the_version_folder_and_passes_its_exit_code(tmp_path):
    root = make_root(tmp_path, "aaa", "bbb", current="aaa")
    commands = []

    def spawn(command):
        commands.append(command)
        return 3

    folder = str(root / "versions" / "bbb")
    assert launcher.main(["--root", str(root), "--self-check", folder], spawn=spawn) == 3
    assert str(root / "versions" / "bbb" / "service") in commands[0]
    outside = tmp_path / "elsewhere" / "bbb"
    (outside / "service").mkdir(parents=True)
    argv = ["--root", str(root), "--self-check", str(outside)]
    assert launcher.main(argv, spawn=pytest.fail) == 1


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


def test_session_close_from_outside_means_quit():
    quits = []
    watcher = session.SessionWatcher(
        ManualRuntime(), lambda: None, lambda: None, lambda: None, on_quit=lambda: quits.append(1)
    )
    assert watcher.handle(session.WM_CLOSE, 0, 0) == 0  # the window stays; the hub quits
    assert quits == [1]


def test_session_end_during_a_quit_waits_for_the_hub_to_stop():
    """The loop has stopped (a Quit is unwinding): wait for `stopped`, not the loop."""
    runtime = ThreadRuntime()
    runtime.stop()
    runtime.run_until_stopped()  # closed: the end-of-session call cannot run
    stopped = threading.Event()
    ended = []
    watcher = session.SessionWatcher(
        runtime, lambda: None, lambda: None, lambda: ended.append(1), stopped=stopped
    )
    timer = threading.Timer(0.2, stopped.set)  # the hub finishes stopping a moment later
    timer.start()
    finished = threading.Event()
    thread = threading.Thread(
        target=lambda: (watcher.handle(session.WM_ENDSESSION, 1, 0), finished.set())
    )
    thread.start()
    assert not finished.wait(0.1)  # still waiting while the hub stops
    assert finished.wait(5)  # and released as soon as it has
    assert ended == []
    timer.join()


def test_session_end_wait_after_a_quit_is_bounded():
    runtime = ThreadRuntime()
    runtime.stop()
    runtime.run_until_stopped()
    watcher = session.SessionWatcher(
        runtime, lambda: None, lambda: None, lambda: None, stopped=threading.Event()
    )
    watcher.END_WAIT_S = 0.05
    assert watcher.handle(session.WM_ENDSESSION, 1, 0) == 0


def test_session_end_when_already_stopped_does_not_wait():
    class NeverRuns:
        def call_soon(self, fn):
            pass  # the loop is stopping: queued calls never run

    stopped = threading.Event()
    stopped.set()
    watcher = session.SessionWatcher(
        NeverRuns(), lambda: None, lambda: None, lambda: None, stopped=stopped
    )
    watcher.END_WAIT_S = 30  # would hang the test if it waited
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


def test_tray_lets_windows_sign_out():
    class FakeIcon:
        _message_handlers = {0x0401: lambda wparam, lparam: 0}

    icon = FakeIcon()
    tray.allow_sign_out(icon)
    assert icon._message_handlers[tray.WM_QUERYENDSESSION](0, 0) == 1
    assert 0x0401 in icon._message_handlers  # pystray's own are kept


class FakePystray:
    """Just enough of pystray for Tray.start() and stop()."""

    def __init__(self, ready=True):
        self.ready = ready
        self.icons = []
        self.notes = []
        outer = self

        class Icon:
            def __init__(self, name, image, title, menu):
                self._message_handlers = {}
                self.menu, self.visible, self.stopped = menu, False, False
                outer.icons.append(self)

            def run_detached(self, setup):
                if outer.ready:
                    setup(self)

            def update_menu(self):
                pass

            def notify(self, message, title=None):
                outer.notes.append((message, title))

            def stop(self):
                self.stopped = True

        self.Icon = Icon
        self.Menu = lambda *items: list(items)
        self.MenuItem = lambda text, action, **options: (text, action, options)


def test_tray_start_shows_an_icon_that_lets_windows_sign_out(monkeypatch):
    fake = FakePystray()
    monkeypatch.setitem(sys.modules, "pystray", fake)
    t, _state = make_tray()
    t.start()
    (icon,) = fake.icons
    assert icon.visible
    assert icon._message_handlers[tray.WM_QUERYENDSESSION](0, 0) == 1
    assert [text for text, _action, _options in icon.menu] == [
        "Settings",
        "Auto mode",
        "Approve update…",
        "Quit",
    ]
    t.stop()
    assert icon.stopped


def test_tray_stop_waits_a_while_for_a_starting_icon(monkeypatch):
    fake = FakePystray(ready=False)  # pystray's loop never got going
    monkeypatch.setitem(sys.modules, "pystray", fake)
    monkeypatch.setattr(tray, "READY_WAIT_S", 0.05)
    t, _state = make_tray()
    t.start()
    t.stop()  # returns after the wait, and still asks the icon to stop
    assert fake.icons[0].stopped


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
        self.approve_update = approve_update
        self.refreshes = 0
        self.stopped = False
        self.messages = []
        FakeTray.instances.append(self)

    def offer_update(self, approve_update):
        self.approve_update = approve_update

    def notify(self, text):
        self.messages.append(text)

    def start(self):
        pass

    def refresh(self):
        self.refreshes += 1

    def stop(self):
        self.stopped = True


class FakeSession:
    instances = []

    def __init__(self, runtime, on_sleep, on_resume, on_end, on_quit=None, stopped=None):
        self.runtime = runtime
        self.on_sleep, self.on_resume, self.on_end = on_sleep, on_resume, on_end
        self.on_quit, self.hub_stopped = on_quit, stopped
        self.started = self.stopped = False
        FakeSession.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class RecordingDevice:
    """A device that notes what the hub asks of it."""

    id = "OMEN-PC"
    label = "Recording"

    def __init__(self):
        self.calls = []

    def attach(self, hub):
        self.hub = hub

    def status(self):
        return {"connected": True}

    def __getattr__(self, name):  # start, stop, show_default, on_changed, ...
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args: self.calls.append(name)


def run_platform(tmp_path, monkeypatch, act, **extra):
    """Run windows.run() on fakes; `act(tray, session, places)` runs on another thread.
    Returns (exit code, places, opened URLs, the recording device)."""
    FakeTray.instances.clear()
    FakeSession.instances.clear()
    device = RecordingDevice()
    monkeypatch.setattr(windows, "build_all", lambda platform: [device])
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
        **extra,
    )
    driver.join(5)
    return code, places, opened, device


def assert_stopped_once_with_default(device, places):
    assert device.calls.count("show_default") == 1
    assert device.calls.count("stop") == 1
    assert device.calls.index("show_default") < device.calls.index("stop")
    assert FakeSession.instances[0].hub_stopped.is_set()
    assert not places.runtime_file.exists()  # removed on the way out


def test_run_quits_from_the_tray(tmp_path, monkeypatch):
    seen = {}

    def act(t, s, places):
        assert s.started
        seen["info"] = json.loads(places.runtime_file.read_text())
        t.open_settings()
        t.set_auto_all(True)
        threading.Event().wait(0.2)
        t.quit()

    code, places, opened, device = run_platform(tmp_path, monkeypatch, act)
    assert code == 0
    t, s = FakeTray.instances[0], FakeSession.instances[0]
    assert t.stopped and s.stopped
    assert t.refreshes >= 1  # the tick follows the setting
    info = seen["info"]
    assert info["pid"] > 0 and info["port"] > 0 and info["token"]
    assert len(opened) == 1 and opened[0].startswith(f"http://127.0.0.1:{info['port']}/?t=")
    saved = json.loads(places.settings_file.read_text())
    assert saved["auto-devices"] == {"OMEN-PC": True, "IQUNIX-KEYBOARD": True}
    assert_stopped_once_with_default(device, places)


def test_run_restarts_on_request_from_the_web_page(tmp_path, monkeypatch):
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

    code, places, _opened, device = run_platform(tmp_path, monkeypatch, act)
    assert code == 75
    assert_stopped_once_with_default(device, places)


def test_run_ends_with_the_session(tmp_path, monkeypatch):
    def act(t, s, places):
        s.runtime.call_soon(s.on_end)  # what SessionWatcher does: on the loop

    code, places, _opened, device = run_platform(tmp_path, monkeypatch, act)
    assert code == 0
    assert_stopped_once_with_default(device, places)


def test_run_quits_when_closed_from_outside(tmp_path, monkeypatch):
    def act(t, s, places):
        s.runtime.call_soon(s.on_quit)  # WM_CLOSE from taskkill, via SessionWatcher

    code, places, _opened, device = run_platform(tmp_path, monkeypatch, act)
    assert code == 0
    assert_stopped_once_with_default(device, places)


def test_run_carries_on_without_a_tray_icon(tmp_path, monkeypatch):
    def broken_start(self):
        raise ImportError("no pystray")

    monkeypatch.setattr(FakeTray, "start", broken_start)

    def act(t, s, places):
        t.quit()  # the menu is gone on Windows, but Quit still ends it here

    code, _places, _opened, _device = run_platform(tmp_path, monkeypatch, act)
    assert code == 0
    assert not FakeTray.instances[0].stopped  # never started, so not stopped either


def test_run_gives_way_to_a_hub_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(windows, "build_all", lambda platform: pytest.fail("built devices"))
    places = windows.Places.from_environ(
        {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
    )
    code = windows.run(
        places,
        hid_bus=FakeHidBus({}),
        ram_bus=FakeRamBus([]),
        runtime=ManualRuntime(),
        tray_factory=lambda **k: pytest.fail("tray"),
        session_factory=lambda *a, **k: pytest.fail("session"),
        single_instance=lambda: False,
    )
    assert code == 0
    assert not places.runtime_file.exists()


def test_run_when_it_is_the_only_hub(tmp_path, monkeypatch):
    claims = []

    def claim():
        claims.append(1)
        return True

    code, *_rest = run_platform(
        tmp_path, monkeypatch, lambda t, s, p: t.quit(), single_instance=claim
    )
    assert code == 0 and claims == [1]


# --- one hub at a time, missed sleeps ------------------------------------------------------


def test_claim_single_instance(monkeypatch):
    monkeypatch.setattr(windows, "_instance", None)
    names = []

    def first(name):
        names.append(name)
        return 1234, 0

    assert windows.claim_single_instance(first) is True
    assert names == ["Local\\RGBHub"]
    assert windows._instance == 1234  # kept for the life of the process
    assert windows.claim_single_instance(lambda name: (99, 183)) is False  # ERROR_ALREADY_EXISTS


def test_claim_single_instance_when_it_cannot_tell(monkeypatch):
    monkeypatch.setattr(windows, "_instance", None)
    assert windows.claim_single_instance(lambda name: (None, 5)) is True

    def broken(name):
        raise OSError("no kernel32")

    assert windows.claim_single_instance(broken) is True
    monkeypatch.setattr(sys, "platform", "linux")
    assert windows.claim_single_instance() is True


def test_suspended_seconds_from_the_two_clocks():
    # 1000 s since boot by the tick count; 940 s of it awake: slept 60 s
    assert windows.suspended_seconds(lambda: (1_000_000, 9_400_000_000)) == pytest.approx(60.0)
    assert windows.suspended_seconds(lambda: (5_000, 50_000_000)) == 0.0
    # the tick count's coarser steps may put it a little behind: never negative
    assert windows.suspended_seconds(lambda: (4_990, 50_000_000)) == 0.0


def test_suspended_seconds_when_the_clocks_cannot_be_read(monkeypatch):
    def broken():
        raise OSError("no kernel32")

    assert windows.suspended_seconds(broken) == 0.0
    monkeypatch.setattr(sys, "platform", "linux")
    assert windows.suspended_seconds() == 0.0


def test_hub_notices_an_unannounced_sleep_with_the_windows_clocks(tmp_path):
    """The hub's backstop, fed by the Windows clocks: a jump means it slept."""
    from rgbhub.core.hub import Hub

    clocks = [(1_000_000, 10_000_000_000)]  # awake all along
    device = RecordingDevice()
    hub = Hub(
        JsonSettings(tmp_path / "settings.json"),
        [device],
        slept=lambda: windows.suspended_seconds(lambda: clocks[0]),
    )
    hub.tick_slow()
    assert "on_resume" not in device.calls
    clocks[0] = (1_600_000, 10_100_000_000)  # 600 s on, only 10 s of them awake
    hub.tick_slow()
    assert "on_resume" in device.calls


def test_launcher_self_check_refuses_odd_versions(tmp_path):
    root = make_root(tmp_path, "aaa", current="aaa")
    for name in ("..", "missing", "../aaa"):
        assert launcher.main(["--root", str(root), "--self-check", name], spawn=pytest.fail) == 1


# --- updates waiting for approval, messages from the updater ------------------------------


def post(info, path, body):
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=5)
    connection.request(
        "POST",
        path,
        body=json.dumps(body).encode(),
        headers={"X-RGBHub-Token": info["token"], "Content-Type": "application/json"},
    )
    status = connection.getresponse().status
    connection.close()
    return status


def test_run_shows_the_updaters_messages_and_offers_approval(tmp_path, monkeypatch):
    sha = "d" * 40
    approved = []
    seen = {}

    def act(t, s, places):
        info = json.loads(places.runtime_file.read_text())
        assert t.approve_update is None  # nothing waits yet
        assert post(info, "/api/notify", {"text": "RGB-Hub updated to ddddddd"}) == 204
        body = {"version": sha, "changes": ["helper/rgbhub_ram_helper_windows.py"]}
        assert post(info, "/api/approve-pending", body) == 204
        for _ in range(100):
            if t.approve_update is not None:
                break
            threading.Event().wait(0.02)
        seen["pending"] = json.loads(places.pending_file.read_text())
        t.approve_update()
        places.pending_file.unlink()  # what approve-privileged.ps1 does once it is done
        monkeypatch.setattr(windows, "LOOK_AGAIN_MS", 50)
        for _ in range(400):
            if t.approve_update is None:
                break
            threading.Event().wait(0.02)
        seen["after"] = t.approve_update
        t.quit()

    monkeypatch.setattr(windows, "LOOK_AGAIN_MS", 50)
    code, places, _opened, _device = run_platform(
        tmp_path, monkeypatch, act, approve=approved.append, current_version=lambda: sha
    )
    assert code == 0
    t = FakeTray.instances[0]
    assert t.messages == ["RGB-Hub updated to ddddddd"]
    assert seen["pending"] == {"version": sha, "changes": ["helper/rgbhub_ram_helper_windows.py"]}
    assert approved == [sha]
    assert seen["after"] is None  # hidden again once approved


def test_run_offers_an_approval_that_waited_from_before(tmp_path, monkeypatch):
    sha = "e" * 40
    places = windows.Places.from_environ(
        {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
    )
    windows.write_pending(places.pending_file, sha, ["install/windows/finish-setup.ps1"])
    approved = []

    def act(t, s, places):
        t.approve_update()
        t.quit()

    run_platform(tmp_path, monkeypatch, act, approve=approved.append, current_version=lambda: sha)
    assert approved == [sha]


def test_run_offers_approval_only_for_the_running_version(tmp_path, monkeypatch):
    """After a roll-back current.txt names the old version again: the failed
    version's approval is not offered."""
    sha, old = "e" * 40, "a" * 40
    places = windows.Places.from_environ(
        {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}
    )
    windows.write_pending(places.pending_file, sha, ["helper/rgbhub_ram_helper.py"])
    running = [sha]
    seen = {}
    monkeypatch.setattr(windows, "LOOK_AGAIN_MS", 50)

    def act(t, s, places):
        seen["before"] = t.approve_update is not None
        running[0] = old  # rolled back
        for _ in range(200):
            if t.approve_update is None:
                break
            threading.Event().wait(0.02)
        seen["after"] = t.approve_update
        t.quit()

    run_platform(tmp_path, monkeypatch, act, current_version=lambda: running[0])
    assert seen == {"before": True, "after": None}


def test_running_version_reads_current_txt(tmp_path):
    assert windows.running_version(tmp_path) is None
    (tmp_path / "current.txt").write_text("f" * 40 + "\n")
    assert windows.running_version(tmp_path) == "f" * 40
    (tmp_path / "current.txt").write_text("..\\x\n")
    assert windows.running_version(tmp_path) is None


def test_read_pending_refuses_odd_versions(tmp_path):
    path = tmp_path / "pending-privileged.json"
    assert windows.read_pending(path) is None
    for text in ("not json", "[1]", '{"version": "..\\\\x"}', '{"version": 5}'):
        path.write_text(text)
        assert windows.read_pending(path) is None, text


def test_approve_update_asks_for_admin_rights_with_the_installed_script(tmp_path):
    calls = []
    sha = "f" * 40
    root = Path("C:/Users/jercl/AppData/Local/Programs/RGB-Hub")
    programs = Path("C:/Program Files/RGB-Hub")
    windows.approve_update(sha, root, programs, popen=lambda cmd, **kw: calls.append(cmd))
    ((cmd),) = calls
    assert cmd[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert cmd[4].startswith("Start-Process powershell.exe -Verb RunAs -ArgumentList '")
    script = programs / "install" / "windows" / "approve-privileged.ps1"
    assert f'-File "{script}" -From "{root / "versions" / sha}"' in cmd[4]

    calls.clear()
    windows.approve_update("../x", root, programs, popen=lambda cmd, **kw: calls.append(cmd))
    odd = Path("C:/it's/RGB-Hub")
    windows.approve_update(sha, odd, programs, popen=lambda cmd, **kw: calls.append(cmd))
    assert calls == []


def test_tray_notify_shows_a_message(monkeypatch):
    fake = FakePystray()
    monkeypatch.setitem(sys.modules, "pystray", fake)
    t, _state = make_tray()
    t.notify("before the icon")  # only logged
    t.start()
    t.notify("RGB-Hub updated")
    assert fake.notes == [("RGB-Hub updated", "RGB-Hub")]
    t.stop()
