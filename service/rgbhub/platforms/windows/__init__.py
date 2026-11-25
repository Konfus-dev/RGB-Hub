# SPDX-License-Identifier: GPL-3.0-or-later
"""The hub on Windows (in place of HP's OMEN Gaming Hub).

Devices: the OMEN PC (cooler LCD, case lights, RAM) and the IQUNIX keyboard.

What this platform chooses:
- settings:     a JSON file, %APPDATA%\\rgbhub\\settings.json
- front end:    the settings web page (core/web.py, frontends/web) in an Edge
                app window, and an icon in the notification area (tray.py)
- case lights, keyboard: our own drivers over hidapi (hid.py)
- RAM lights:   the helper that runs with full rights (ram.py); without it
                the RAM is reported unavailable and asked again later
- what is in focus: the app in front (focus.py), its icon (icons.py). With
                nothing in focus the LCD shows what is in %APPDATA%\\rgbhub\\lcd
                if anything, else the wallpaper, else the Windows logo
- sleep/wake, sign-out: a hidden window hears Windows' messages (session.py)

Logs go to %LOCALAPPDATA%\\rgbhub\\hub.log. Exit code 0 means Quit (the
launcher stops), 75 means "restart me" (the launcher starts the version
current.txt names). Inside a virtual machine (the WinBridge VM boots this
same Windows) nothing is started: the lights belong to the real PC.

Threads: the hub's loop (ThreadRuntime) runs on the main thread; the tray
icon, the hidden session window and the web server each have their own and
hand their work to the loop.
"""

import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ...core.hub import Hub
from ...core.json_store import JsonSettings
from ...core.lcd_folder import load_lcd_folder
from ...core.look import Looks, cover_lcd
from ...core.platform import Platform
from ...core.settings_schema import DEVICE_IDS
from ...core.thread_runtime import ThreadRuntime
from ...core.web import SettingsServer
from ...devices import build_all
from . import icons, wallpaper
from .focus import FocusWatcher

log = logging.getLogger(__name__)

EXIT_QUIT = 0
EXIT_RESTART = 75
LOG_BYTES = 1_000_000
LOOK_AGAIN_MS = 5000  # how often the wallpaper and the LCD folder are checked for changes
VM_MARKERS = ("qemu", "virtual")  # in Win32_ComputerSystem's manufacturer or model
EDGE_WINDOW_SIZE = "900,760"
CREATE_NO_WINDOW = 0x08000000  # a console program started from pythonw.exe shows no window
MUTEX_NAME = "Local\\RGBHub"  # one hub per signed-in session
ERROR_ALREADY_EXISTS = 183
EXIT_WAIT_S = 5.0  # how long leftover threads may hold the process open after a stop


@dataclass(frozen=True)
class Places:
    """Where this platform keeps things."""

    settings_file: Path  # %APPDATA%\rgbhub\settings.json (roams with the account)
    lcd_folder: Path  # %APPDATA%\rgbhub\lcd
    pictures: Path  # %APPDATA%\rgbhub\pictures: pictures chosen on the settings page
    data: Path  # %LOCALAPPDATA%\rgbhub: logs, runtime.json, the RAM helper's key

    @property
    def runtime_file(self) -> Path:
        return self.data / "runtime.json"

    @property
    def log_file(self) -> Path:
        return self.data / "hub.log"

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> "Places":
        home = Path.home()
        roaming = Path(environ.get("APPDATA") or home / "AppData" / "Roaming") / "rgbhub"
        local = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "rgbhub"
        return cls(roaming / "settings.json", roaming / "lcd", roaming / "pictures", local)


def page_dir() -> Path:
    """The settings page: <version>\\frontends\\web, beside <version>\\service\\rgbhub."""
    return Path(__file__).resolve().parents[4] / "frontends" / "web"


def setup_logging(log_file: Path, verbose: bool = False) -> None:
    """To the log file (about 1 MB, one older copy kept), and to a console when
    there is one. Done before anything imports icoextract, whose own
    logging.basicConfig() would otherwise decide."""
    handlers: list[logging.Handler] = []
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8"
            )
        )
    except OSError as error:
        print(f"Cannot write the log {log_file}: {error}", file=sys.stderr)
    # pythonw.exe has no stderr; under the launcher it is a file for crashes only.
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    if not handlers:
        root.addHandler(logging.NullHandler())
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    def thread_crashed(info) -> None:
        log.error(
            "Thread %s stopped by an error",
            info.thread.name if info.thread else "?",
            exc_info=(info.exc_type, info.exc_value, info.exc_traceback),
        )

    threading.excepthook = thread_crashed


def in_virtual_machine(run: Callable[..., subprocess.CompletedProcess] | None = None) -> bool:
    """True inside a virtual machine (QEMU, Hyper-V, VMware, VirtualBox), asked
    of Windows each time. False when it cannot be told."""
    if run is None:
        if sys.platform != "win32":
            return False
        run = subprocess.run
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$c = Get-CimInstance Win32_ComputerSystem; $c.Manufacturer; $c.Model",
    ]
    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("Cannot tell whether this is a virtual machine: %s", error)
        return False
    described = (result.stdout or "").lower()
    return any(marker in described for marker in VM_MARKERS)


def write_runtime_file(
    path: Path, pid: int, port: int, token: str, now: Callable[[], float] = time.time
) -> None:
    """Tell the updater how to reach this hub: {"pid", "port", "token", "started"}.
    Only the owner may read it (the token lets its holder change the settings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    data = {"pid": pid, "port": port, "token": token, "started": now()}
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(data, file)
    if sys.platform == "win32":
        _owner_only(temporary)
    os.replace(temporary, path)  # a move keeps the file's permissions


def _owner_only(path: Path) -> None:
    """Windows ignores the mode above: drop inherited permissions, give the owner full control."""
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    if not user:
        return
    account = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
            capture_output=True,
            timeout=30,
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        # %LOCALAPPDATA% is the owner's (and the system's) already.
        log.warning("Could not make %s readable by the owner only: %s", path, error)


# --- one hub at a time -----------------------------------------------------------------

_instance = None  # our mutex, held for the life of the process


def _create_mutex(name: str) -> tuple[object, int]:
    """CreateMutexW: (handle or None, GetLastError())."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, name)
    return handle, ctypes.get_last_error()


def claim_single_instance(create: Callable[[str], tuple[object, int]] | None = None) -> bool:
    """True if no other hub runs in this session; the claim lasts until the
    process ends. True also when it cannot be told (better two than none)."""
    global _instance
    if create is None:
        if sys.platform != "win32":
            return True
        create = _create_mutex
    try:
        handle, error = create(MUTEX_NAME)
    except Exception as error:
        log.warning("Cannot check for another RGB-Hub: %s", error)
        return True
    if not handle:
        log.warning("Cannot check for another RGB-Hub (error %s)", error)
        return True
    if error == ERROR_ALREADY_EXISTS:
        return False
    _instance = handle
    return True


# --- sleep the hub was not told about ----------------------------------------------------

_clocks = None  # the two kernel32 functions, set up once


def _read_clocks() -> tuple[int, int]:
    """(GetTickCount64 in ms, QueryUnbiasedInterruptTime in 100 ns units)."""
    global _clocks
    import ctypes

    if _clocks is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetTickCount64.restype = ctypes.c_uint64
        kernel32.GetTickCount64.argtypes = []
        kernel32.QueryUnbiasedInterruptTime.restype = ctypes.c_int
        kernel32.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
        _clocks = kernel32
    unbiased = ctypes.c_uint64(0)
    _clocks.QueryUnbiasedInterruptTime(ctypes.byref(unbiased))
    return _clocks.GetTickCount64(), unbiased.value


def suspended_seconds(read: Callable[[], tuple[int, int]] | None = None) -> float:
    """Seconds spent asleep since Windows started: the tick count runs on
    through sleep, the unbiased interrupt time does not. For the hub's check
    for sleeps nobody announced (core.hub.suspended_seconds needs Linux's clocks)."""
    if read is None:
        if sys.platform != "win32":
            return 0.0
        read = _read_clocks
    try:
        tick_ms, unbiased_100ns = read()
    except Exception as error:
        log.debug("Cannot read the clocks: %s", error)
        return 0.0
    return max(0.0, tick_ms / 1e3 - unbiased_100ns / 1e7)


def auto_all(settings) -> bool:
    """Does every device follow the app in front?"""
    chosen = settings.get("auto-devices")
    return all(chosen.get(device, False) for device in DEVICE_IDS)


def set_auto_all(settings, on: bool) -> None:
    settings.set("auto-devices", {**settings.get("auto-devices"), **dict.fromkeys(DEVICE_IDS, on)})


def _load_picture(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        return opened.convert("RGBA")


def idle_picture(
    lcd_folder: Path,
    wallpaper: Callable[[], Path | None] = wallpaper.current_wallpaper,
    logo: Callable[[], Image.Image] = wallpaper.windows_logo,
):
    """What the LCD shows with nothing in focus: what the owner put in the
    LCD folder; else the wallpaper; else the Windows logo."""
    override = load_lcd_folder(lcd_folder, cover_lcd)
    if override is not None:
        return override
    path = wallpaper()
    if path is not None:
        try:
            return _load_picture(path)
        except Exception as error:  # Pillow raises several kinds
            log.warning("Cannot use the wallpaper %s: %s", path, error)
    return logo()


def _file_signature(path: Path | None) -> tuple | None:
    if path is None:
        return None
    try:
        info = path.stat()
    except OSError:
        return (str(path),)
    return (str(path), info.st_mtime_ns, info.st_size)


def idle_signature(lcd_folder: Path) -> tuple:
    """Changes when the idle picture would (another wallpaper, the LCD folder edited)."""
    return (_file_signature(wallpaper.current_wallpaper()), _file_signature(lcd_folder))


def change_watch(read: Callable[[], object], on_change: Callable[[], None]) -> Callable[[], bool]:
    """A timer callback that calls `on_change` whenever `read()` gives something new."""
    last = [read()]

    def poll() -> bool:
        now = read()
        if now != last[0]:
            last[0] = now
            on_change()
        return True

    return poll


def find_edge(
    which: Callable[[str], str | None] = shutil.which, environ: Mapping[str, str] = os.environ
) -> str | None:
    found = which("msedge")
    if found:
        return found
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = environ.get(variable)
        if base:
            edge = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if edge.is_file():
                return str(edge)
    return None


def open_settings_window(
    url: str,
    find: Callable[[], str | None] = find_edge,
    popen: Callable[[list[str]], object] = subprocess.Popen,
    browser: Callable[[str], object] = webbrowser.open,
) -> None:
    """The settings page in an Edge app window (no tabs or address bar), else
    in the default browser."""
    edge = find()
    if edge:
        try:
            popen([edge, f"--app={url}", f"--window-size={EDGE_WINDOW_SIZE}"])
            return
        except OSError as error:
            log.warning("Cannot start Edge (%s); using the default browser", error)
    browser(url)


def run(
    places: Places,
    hid_bus=None,
    ram_bus=None,
    runtime=None,
    tray_factory=None,
    session_factory=None,
    open_window: Callable[[str], None] = open_settings_window,
    single_instance: Callable[[], bool] = claim_single_instance,
) -> int:
    """Run the hub until Quit (returns 0), a restart request (75) or sign-out (0).
    The arguments after `places` are for tests."""
    if not single_instance():
        log.info("RGB-Hub is running already; this one stops")
        return EXIT_QUIT
    if hid_bus is None:
        from .hid import HidapiBus

        hid_bus = HidapiBus()
    if ram_bus is None:
        from .ram import TcpRamBus

        # Always given: without the helper it reports no sticks, the RAM shows
        # as unavailable and is asked again at each slow tick.
        ram_bus = TcpRamBus()
    if tray_factory is None:
        from .tray import Tray as tray_factory
    if session_factory is None:
        from .session import SessionWatcher as session_factory
    runtime = runtime or ThreadRuntime()

    settings = JsonSettings(places.settings_file)
    stop_watching = runtime.watch_file(str(places.settings_file), settings.reload)
    exit_code = [EXIT_QUIT]

    def finish(code: int) -> None:  # on the loop
        exit_code[0] = code
        runtime.stop()

    looks = Looks(
        icons.load_app_icon,
        load_idle=lambda: idle_picture(places.lcd_folder),
        compose_idle=cover_lcd,  # wallpapers suit a full screen
    )
    platform = Platform("windows", hid_bus, runtime, ram_bus=ram_bus, lcd_fade=True)
    hub = Hub(
        settings, build_all(platform), looks=looks, scheduler=runtime, slept=suspended_seconds
    )
    hub_stopped = threading.Event()  # the Default look is showing: the session may end

    def stop_hub() -> None:
        try:
            hub.stop()  # leaves the Default look on every device; once only
        finally:
            hub_stopped.set()

    server = SettingsServer(
        settings,
        runtime,
        page_dir(),
        places.pictures,
        on_quit=lambda: finish(EXIT_QUIT),
        on_restart=lambda: finish(EXIT_RESTART),
    )
    # Whatever has been started is stopped again, in reverse order, however
    # this ends: a device's threads left running would keep the process alive.
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(stop_watching)
        cleanup.callback(stop_hub)  # last: leaves the Default look on every device
        hub.start()
        url = server.start()
        cleanup.callback(server.stop)
        write_runtime_file(places.runtime_file, os.getpid(), server.port, server.token)
        cleanup.callback(places.runtime_file.unlink, missing_ok=True)

        focus = FocusWatcher(settings, runtime, os.getpid())
        cleanup.callback(focus.stop)
        look_timer = runtime.every(
            LOOK_AGAIN_MS, change_watch(lambda: idle_signature(places.lcd_folder), hub.look_again)
        )
        cleanup.callback(runtime.cancel, look_timer)

        def end_of_session() -> None:  # on the loop; Windows ends us right after
            stop_hub()
            finish(EXIT_QUIT)

        session = session_factory(
            runtime,
            hub.on_sleep,
            hub.on_resume,
            end_of_session,
            on_quit=lambda: finish(EXIT_QUIT),
            stopped=hub_stopped,
        )
        session.start()
        cleanup.callback(session.stop)

        # The tray's actions come on its own thread: hand them to the loop.
        tray = tray_factory(
            open_settings=lambda: open_window(url),
            set_auto_all=lambda on: runtime.call_soon(lambda: set_auto_all(settings, on)),
            auto_all=lambda: auto_all(settings),
            quit=lambda: runtime.call_soon(lambda: finish(EXIT_QUIT)),
        )

        def on_setting(key: str) -> None:
            if key == "auto-devices":
                tray.refresh()  # the tick follows changes made on the settings page

        try:
            tray.start()
            cleanup.callback(tray.stop)
            settings.subscribe(on_setting)
            cleanup.callback(settings.unsubscribe, on_setting)
        except Exception:  # the lights matter more; Quit is on the settings page too
            log.exception("No tray icon; RGB-Hub runs on without it")
        log.info("RGB-Hub running (Windows); settings page on port %d", server.port)
        runtime.run_until_stopped()
    log.info("RGB-Hub stopped (exit code %d)", exit_code[0])
    return exit_code[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RGB-Hub on Windows.")
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)
    places = Places.from_environ()
    setup_logging(places.log_file, args.verbose)
    if in_virtual_machine():
        log.info("Running in a virtual machine: the lights belong to the real PC; not starting")
        return EXIT_QUIT
    try:
        code = run(places)
    except Exception:
        log.exception("RGB-Hub stopped by an unexpected error")
        code = 1
    _exit_if_held_open(code)
    return code


def _exit_if_held_open(code: int, wait_s: float = EXIT_WAIT_S) -> None:
    """Everything has been stopped. Should a thread still hold the process
    open (a tray icon that never got going), end it anyway: the launcher
    waits for the exit code."""
    deadline = time.monotonic() + wait_s
    for thread in threading.enumerate():
        if thread is threading.current_thread() or thread.daemon:
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            log.warning("Thread %s did not end; ending the process", thread.name)
            logging.shutdown()
            os._exit(code)
