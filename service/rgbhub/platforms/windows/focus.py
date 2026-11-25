# SPDX-License-Identifier: GPL-3.0-or-later
"""Which app is in front on Windows, for auto mode's "focused-app".

Every half second the foreground window is looked at: its process's program
file, and for Store apps the package family. The app id is the lower-cased
program path, or "<package family>|<program folder>" for a Store app (the
folder is where icons.py finds the package's manifest and logos).

Nothing in front that can be read (the desktop, the lock screen, an app
running as administrator, which a normal program may not look into) reads as
"" (nothing focused: the idle look). RGB-Hub's own windows (the tray menu, the
settings page in Edge's app window, whose title starts with "RGB-Hub" or
"RGB Hub") leave
the previous value, so opening the settings does not change the lights.

All Win32 calls are made when asked, never at import, so this module imports
on Linux (for the tests).
"""

import logging
import ntpath
import sys
from collections.abc import Callable
from typing import Any, NamedTuple

log = logging.getLogger(__name__)

POLL_MS = 500
# Our windows' titles: the settings page (frontends/web/index.html says "RGB Hub").
OWN_TITLE_PREFIXES = ("RGB-Hub", "RGB Hub")
# The desktop and the taskbar: being "in front" there means no app is.
NO_APP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
FRAME_HOST = "applicationframehost.exe"  # hosts Store apps' windows


class Foreground(NamedTuple):
    """The app in front: its program file, Store package family (None for a
    normal program), process id and window title."""

    exe: str
    family: str | None
    pid: int = 0
    title: str = ""


def app_id_for(exe_path: str, package_family: str | None) -> str:
    """The app id auto mode knows an app by."""
    if package_family:
        return f"{package_family}|{ntpath.dirname(exe_path)}"
    return exe_path.lower()


class FocusWatcher:
    """Writes the app in front to "focused-app", every half second."""

    def __init__(
        self,
        settings,
        runtime,
        own_pid: int,
        read: Callable[[], Foreground | None] | None = None,
    ):
        self._settings = settings
        self._runtime = runtime
        self._own_pid = own_pid
        self._read = read or foreground_app
        self._handle: Any = runtime.every(POLL_MS, self.poll)
        runtime.call_soon(self.poll)  # known at once, not half a second later

    def poll(self) -> bool:
        try:
            app = self._read()
        except Exception as error:  # never let one odd window stop the watching
            log.debug("Cannot read the window in front: %s", error)
            app = None
        if app is not None and self._is_own(app):
            return True
        subject = app_id_for(app.exe, app.family) if app is not None else ""
        if self._settings.get("focused-app") != subject:
            self._settings.set("focused-app", subject)
        return True

    def stop(self) -> None:
        if self._handle is not None:
            self._runtime.cancel(self._handle)
            self._handle = None

    def _is_own(self, app: Foreground) -> bool:
        return app.pid == self._own_pid or app.title.startswith(OWN_TITLE_PREFIXES)


def foreground_app() -> Foreground | None:
    """The app in front, or None when there is none or it cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        return _api().foreground()
    except Exception as error:
        log.debug("Cannot read the window in front: %s", error)
        return None


# --- Win32 (only ever run on Windows) ------------------------------------------------

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_SUCCESS = 0
_win32 = None  # the DLL functions, set up once


def _api() -> "_Win32":
    global _win32
    if _win32 is None:
        api = _Win32()
        api.setup()
        _win32 = api  # only once fully set up
    return _win32


class _Win32:
    """The handful of user32/kernel32 calls needed, with their C types."""

    def setup(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HWND, DWORD, BOOL = wintypes.HWND, wintypes.DWORD, wintypes.BOOL

        user32.GetForegroundWindow.restype = HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowThreadProcessId.restype = DWORD
        user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
        self.enum_proc = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)
        user32.EnumChildWindows.restype = BOOL
        user32.EnumChildWindows.argtypes = [HWND, self.enum_proc, wintypes.LPARAM]

        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
        kernel32.CloseHandle.restype = BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryFullProcessImageNameW.restype = BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(DWORD),
        ]
        # Windows 8 and later; a normal program returns APPMODEL_ERROR_NO_PACKAGE.
        kernel32.GetPackageFamilyName.restype = ctypes.c_long
        kernel32.GetPackageFamilyName.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_uint32),
            wintypes.LPWSTR,
        ]
        self.user32, self.kernel32 = user32, kernel32

    def foreground(self) -> Foreground | None:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd or self._class_name(hwnd) in NO_APP_CLASSES:
            return None
        title = self._title(hwnd)
        pid = self._pid(hwnd)
        exe, family = self._process(pid)
        if exe and ntpath.basename(exe).lower() == FRAME_HOST:
            # A Store app's window: the frame belongs to ApplicationFrameHost, the
            # app itself owns a child window with another process id.
            pid = self._child_pid(hwnd, pid)
            exe, family = self._process(pid) if pid else (None, None)
        if not exe:
            return None
        return Foreground(exe, family, pid, title)

    def _class_name(self, hwnd) -> str:
        buffer = self.ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def _title(self, hwnd) -> str:
        buffer = self.ctypes.create_unicode_buffer(512)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _pid(self, hwnd) -> int:
        pid = self.wintypes.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return pid.value

    def _child_pid(self, hwnd, frame_pid: int) -> int:
        found = [0]

        def visit(child, _param):
            pid = self._pid(child)
            if pid and pid != frame_pid:
                found[0] = pid
                return False  # stop looking
            return True

        self.user32.EnumChildWindows(hwnd, self.enum_proc(visit), 0)
        return found[0]

    def _process(self, pid: int) -> tuple[str | None, str | None]:
        """The program file and package family of a process (None where unreadable)."""
        if not pid:
            return None, None
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:  # access denied (an elevated app), or gone already
            return None, None
        try:
            size = self.wintypes.DWORD(32768)
            path = self.ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(
                handle, 0, path, self.ctypes.byref(size)
            ):
                return None, None
            length = self.ctypes.c_uint32(256)
            family = self.ctypes.create_unicode_buffer(length.value)
            status = self.kernel32.GetPackageFamilyName(handle, self.ctypes.byref(length), family)
            return path.value, (family.value or None) if status == ERROR_SUCCESS else None
        finally:
            self.kernel32.CloseHandle(handle)
