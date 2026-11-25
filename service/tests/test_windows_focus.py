# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows auto mode: the focused app, its icon and the wallpaper (the parts that run on Linux)."""

import re
from pathlib import Path

from PIL import Image

from rgbhub.core.interfaces import ManualRuntime
from rgbhub.platforms.windows import focus, icons, wallpaper
from rgbhub.platforms.windows.focus import FocusWatcher, Foreground, app_id_for

CALC_DIR = r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2409.0.0_x64__8wek"
WEB_PAGE = Path(__file__).resolve().parents[2] / "frontends" / "web" / "index.html"
CALC_FAMILY = "Microsoft.WindowsCalculator_8wekyb3d8bbwe"


class MemorySettings:
    def __init__(self):
        self.values = {"focused-app": ""}
        self.writes = []

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value

    def subscribe(self, callback):
        pass

    def unsubscribe(self, callback):
        pass


def tick(runtime):
    for _interval, callback in list(runtime.timers.values()):
        callback()


def watcher(read, own_pid=4242):
    settings, runtime = MemorySettings(), ManualRuntime()
    FocusWatcher(settings, runtime, own_pid, read=read)
    return settings, runtime


# --- app ids -----------------------------------------------------------------


def test_app_id_exe_lowercased():
    assert app_id_for(r"C:\Program Files\Mozilla Firefox\Firefox.EXE", None) == (
        r"c:\program files\mozilla firefox\firefox.exe"
    )


def test_app_id_package_wins():
    exe = CALC_DIR + r"\CalculatorApp.exe"
    assert app_id_for(exe, CALC_FAMILY) == f"{CALC_FAMILY}|{CALC_DIR}"


# --- the watcher ---------------------------------------------------------------


def test_focus_watcher_polls_every_half_second():
    settings, runtime = watcher(lambda: None)
    assert [interval for interval, _ in runtime.timers.values()] == [500]


def test_focus_watcher_writes_app():
    current = [Foreground(r"C:\Games\Doom.exe", None, 100, "DOOM")]
    settings, runtime = watcher(lambda: current[0])
    assert settings.get("focused-app") == r"c:\games\doom.exe"  # read at once on start
    current[0] = Foreground(CALC_DIR + r"\CalculatorApp.exe", CALC_FAMILY, 200, "Calculator")
    tick(runtime)
    assert settings.get("focused-app") == f"{CALC_FAMILY}|{CALC_DIR}"


def test_focus_watcher_writes_only_changes():
    settings, runtime = watcher(lambda: Foreground(r"C:\Games\Doom.exe", None, 100, "DOOM"))
    tick(runtime)
    tick(runtime)
    assert settings.writes == [("focused-app", r"c:\games\doom.exe")]


def test_focus_watcher_unreadable_window_is_idle():
    current = [Foreground(r"C:\Games\Doom.exe", None, 100, "DOOM")]
    settings, runtime = watcher(lambda: current[0])
    current[0] = None  # desktop, lock screen, an elevated app
    tick(runtime)
    assert settings.get("focused-app") == ""


def test_focus_watcher_read_raises_is_idle():
    current = [Foreground(r"C:\Games\Doom.exe", None, 100, "DOOM")]

    def read():
        if current[0] is None:
            raise OSError("access denied")
        return current[0]

    settings, runtime = watcher(read)
    current[0] = None
    tick(runtime)  # nothing escapes the poll
    assert settings.get("focused-app") == ""
    assert len(runtime.timers) == 1  # and it keeps polling


def test_focus_watcher_ignores_own_window():
    current = [Foreground(r"C:\Games\Doom.exe", None, 100, "DOOM")]
    settings, runtime = watcher(lambda: current[0], own_pid=4242)
    # The tray's menu (our own process) and the settings page in Edge's app window.
    current[0] = Foreground(r"C:\RGB-Hub\python\pythonw.exe", None, 4242, "")
    tick(runtime)
    assert settings.get("focused-app") == r"c:\games\doom.exe"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    current[0] = Foreground(edge, None, 900, "RGB-Hub settings")
    tick(runtime)
    assert settings.get("focused-app") == r"c:\games\doom.exe"


def test_focus_watcher_knows_the_settings_page_by_its_title():
    title = re.search(r"<title>(.*)</title>", WEB_PAGE.read_text())[1]
    current = [Foreground(r"C:\Games\Doom.exe", None, 100, "DOOM")]
    settings, runtime = watcher(lambda: current[0])
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    current[0] = Foreground(edge, None, 900, title)
    tick(runtime)
    assert settings.get("focused-app") == r"c:\games\doom.exe"


def test_focus_watcher_stop_cancels_the_poll():
    settings, runtime = MemorySettings(), ManualRuntime()
    watch = FocusWatcher(settings, runtime, 1, read=lambda: None)
    watch.stop()
    assert runtime.timers == {}


def test_foreground_app_is_none_off_windows():
    assert focus.foreground_app() is None


class FakeWin32:
    """The Win32 layer focus.foreground_app() reads through: one window and its processes."""

    def __init__(self, exe, family=None, window_class="Chrome_WidgetWin_1", child=None):
        self.window_class = window_class
        self.processes = {10: (exe, family)}
        if child:
            self.processes[20] = child

    def foreground_window(self):
        return 1

    def class_name(self, hwnd):
        return self.window_class

    def title(self, hwnd):
        return "a window"

    def pid(self, hwnd):
        return 10

    def child_pid(self, hwnd, frame_pid):
        return 20 if 20 in self.processes else 0

    def process(self, pid):
        return self.processes.get(pid, (None, None))


def test_foreground_app_reads_a_program():
    app = focus.foreground_app(FakeWin32(r"C:\Firefox\firefox.exe"))
    assert app == Foreground(r"C:\Firefox\firefox.exe", None, 10, "a window")


def test_foreground_app_looks_past_the_store_frame():
    frame = r"C:\Windows\System32\ApplicationFrameHost.exe"
    calc = (CALC_DIR + r"\CalculatorApp.exe", CALC_FAMILY)
    assert focus.foreground_app(FakeWin32(frame, child=calc)) == Foreground(*calc, 20, "a window")
    assert focus.foreground_app(FakeWin32(frame)) is None  # app not shown yet


def test_foreground_app_desktop_and_unreadable_are_none():
    desktop = FakeWin32(r"C:\Windows\explorer.exe", window_class="WorkerW")
    assert focus.foreground_app(desktop) is None
    assert focus.foreground_app(FakeWin32(None)) is None  # OpenProcess refused (elevated app)


def test_foreground_app_lock_screen_and_start_are_none():
    for exe in (
        r"C:\Windows\SystemApps\Microsoft.LockApp_cw5n1h2txyewy\LockApp.exe",
        r"C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\SearchHost.exe",
        r"C:\Windows\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"
        r"\StartMenuExperienceHost.exe",
        r"C:\Windows\SystemApps\ShellExperienceHost_cw5n1h2txyewy\ShellExperienceHost.exe",
        r"C:\Windows\SystemApps\Microsoft.Windows.Search_cw5n1h2txyewy\SearchApp.exe",
        r"C:\Windows\SystemApps\MicrosoftWindows.Client.Core_cw5n1h2txyewy\ShellHost.exe",
    ):
        assert focus.foreground_app(FakeWin32(exe, "Some.Family_cw5n1h2txyewy")) is None, exe


def test_foreground_app_layer_failure_is_none():
    class Broken(FakeWin32):
        def process(self, pid):
            raise OSError("access denied")

    assert focus.foreground_app(Broken("x")) is None


# --- the wallpaper and the logo -------------------------------------------------


def test_windows_logo_size():
    logo = wallpaper.windows_logo()
    assert logo.size == (480, 480)
    assert logo.getpixel((0, 0))[:3] == (0, 0, 0)
    assert logo.getpixel((240 - 60, 240 - 60))[:3] == (0x00, 0x78, 0xD4)
    assert logo.getpixel((240 + 60, 240 + 60))[:3] == (0x00, 0x78, 0xD4)
    assert logo.getpixel((240, 240))[:3] == (0, 0, 0)  # the gap between the squares


def test_wallpaper_from_the_system(tmp_path):
    picture = tmp_path / "beach.jpg"
    picture.write_bytes(b"x")
    assert wallpaper.current_wallpaper(ask=lambda: str(picture), appdata=None) == picture


def test_wallpaper_falls_back_to_the_transcoded_copy(tmp_path):
    copy = tmp_path / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"x")
    assert wallpaper.current_wallpaper(ask=lambda: "", appdata=str(tmp_path)) == copy
    gone = str(tmp_path / "gone.jpg")
    assert wallpaper.current_wallpaper(ask=lambda: gone, appdata=str(tmp_path)) == copy


def test_no_wallpaper(tmp_path):
    def broken():
        raise OSError("no")

    assert wallpaper.current_wallpaper(ask=broken, appdata=str(tmp_path)) is None
    assert wallpaper.current_wallpaper(ask=lambda: "", appdata=None) is None


# --- icons -----------------------------------------------------------------------

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
  <Properties><Logo>Assets\\StoreLogo.png</Logo></Properties>
  <Applications>
    <Application Id="App" Executable="CalculatorApp.exe">
      <uap:VisualElements DisplayName="Calculator" Description="Calculator"
          BackgroundColor="transparent"
          Square150x150Logo="Assets\\Logo.png" Square44x44Logo="Assets\\Small.png"/>
    </Application>
  </Applications>
</Package>
"""


def make_package(folder, sizes):
    (folder / "Assets").mkdir(parents=True)
    (folder / "AppxManifest.xml").write_text(MANIFEST)
    for name, (size, colour) in sizes.items():
        Image.new("RGBA", (size, size), colour).save(folder / "Assets" / name)


def test_manifest_logo_picks_largest_scale(tmp_path):
    make_package(
        tmp_path,
        {
            "Logo.scale-100.png": (150, "red"),
            "Logo.scale-200.png": (300, "blue"),
            "Small.targetsize-48.png": (48, "green"),
            "Logo.scale-400_contrast-black.png": (600, "white"),  # high contrast: not used
            "StoreLogo.png": (50, "yellow"),
        },
    )
    icon = icons.load_app_icon(f"{CALC_FAMILY}|{tmp_path}")
    assert icon.mode == "RGBA"
    assert icon.size == (300, 300)
    assert icon.getpixel((0, 0)) == (0, 0, 255, 255)


def test_manifest_found_above_the_exe_folder(tmp_path):
    make_package(tmp_path, {"Small.targetsize-256.png": (256, "green")})
    (tmp_path / "bin").mkdir()
    icon = icons.load_app_icon(f"{CALC_FAMILY}|{tmp_path / 'bin'}")
    assert icon.size == (256, 256)


def test_manifest_logo_outside_the_package_is_not_opened(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    Image.new("RGBA", (500, 500), "red").save(tmp_path / "Outside.png")
    Image.new("RGBA", (500, 500), "red").save(tmp_path / "Outside.scale-200.png")
    (package / "AppxManifest.xml").write_text(
        MANIFEST.replace("Assets\\Logo.png", "..\\Outside.png")
    )
    make_assets = package / "Assets"
    make_assets.mkdir()
    Image.new("RGBA", (44, 44), "green").save(make_assets / "Small.png")
    icon = icons.load_app_icon(f"{CALC_FAMILY}|{package}")
    assert icon.size == (44, 44)


def test_package_without_manifest_has_no_icon(tmp_path):
    assert icons.load_app_icon(f"{CALC_FAMILY}|{tmp_path}") is None
    (tmp_path / "AppxManifest.xml").write_text("<not xml")
    assert icons.load_app_icon(f"{CALC_FAMILY}|{tmp_path}") is None


def test_exe_without_icon_is_none(tmp_path):
    assert icons.load_app_icon(str(tmp_path / "missing.exe")) is None
    junk = tmp_path / "junk.exe"
    junk.write_bytes(b"not a program")
    assert icons.load_app_icon(str(junk)) is None
    assert icons.load_app_icon("") is None


def test_exe_icon_largest_frame(tmp_path, monkeypatch):
    """The exe's icon group comes from icoextract; the largest frame is used."""
    from io import BytesIO

    ico = BytesIO()
    Image.new("RGBA", (64, 64), "red").save(ico, format="ICO", sizes=[(16, 16), (32, 32), (64, 64)])

    class FakeExtractor:
        def __init__(self, filename):
            self.filename = filename

        def get_icon(self, num=0):
            ico.seek(0)
            return ico

    exe = tmp_path / "app.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(icons, "_extractor", lambda: FakeExtractor)
    icon = icons.load_app_icon(str(exe))
    assert icon.mode == "RGBA" and icon.size == (64, 64)
