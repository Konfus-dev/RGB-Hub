# SPDX-License-Identifier: GPL-3.0-or-later
"""Sleep, wake and sign-out on Windows.

Windows tells top-level windows about these by message, so a hidden window
is made on a thread of its own and listens:
- WM_POWERBROADCAST, PBT_APMSUSPEND: going to sleep -> on_sleep (Windows
  allows about two seconds; the window waits up to that for it to finish)
- WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC: woke up -> on_resume
- WM_QUERYENDSESSION: may Windows sign out / shut down? Always yes.
- WM_ENDSESSION with wParam TRUE: it is happening -> on_end, and the window
  waits (at most 15 s) until it is done, because Windows ends the program as
  soon as the message has been answered.

The callbacks run on the hub's loop (runtime.call_soon), never on the window's
thread. All Win32 calls are made in start(), never at import, so this module
imports on Linux (for the tests).
"""

import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
TRUE = 1
CLASS_NAME = "RGBHubSessionWatcher"
ERROR_CLASS_ALREADY_EXISTS = 1410


class SessionWatcher:
    SLEEP_WAIT_S = 2.0  # Windows gives a program about this long before it sleeps
    END_WAIT_S = 15.0  # at sign-out Windows waits a while, then ends the program anyway

    def __init__(
        self,
        runtime,
        on_sleep: Callable[[], None],
        on_resume: Callable[[], None],
        on_end: Callable[[], None],
    ):
        self._runtime = runtime
        self._on_sleep = on_sleep
        self._on_resume = on_resume
        self._on_end = on_end
        self._window: _Window | None = None

    def handle(self, message: int, wparam: int, lparam: int) -> int | None:
        """The answer to one window message, or None to let Windows answer it."""
        if message == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                log.info("Windows is going to sleep")
                self._on_loop(self._on_sleep, self.SLEEP_WAIT_S)
            elif wparam == PBT_APMRESUMEAUTOMATIC:
                log.info("Windows woke up")
                self._on_loop(self._on_resume, 0)
            return TRUE
        if message == WM_QUERYENDSESSION:
            return TRUE  # never hold sign-out or shutdown back
        if message == WM_ENDSESSION:
            if wparam:
                log.info("Windows is signing out or shutting down")
                self._on_loop(self._on_end, self.END_WAIT_S)
            return 0
        return None

    def _on_loop(self, callback: Callable[[], None], wait_s: float) -> None:
        """Run `callback` on the hub's loop; wait up to `wait_s` for it to finish."""
        done = threading.Event()

        def run() -> None:
            try:
                callback()
            finally:
                done.set()

        try:
            self._runtime.call_soon(run)
        except RuntimeError:  # the loop has closed: the hub has stopped already
            return
        if wait_s and not done.wait(wait_s):
            log.warning("The hub did not finish in %.0f s; carrying on", wait_s)

    def start(self) -> None:
        """Start listening (on Windows; elsewhere there is nothing to listen to)."""
        if sys.platform != "win32" or self._window is not None:
            return
        self._window = _Window(self.handle)
        self._window.start()

    def stop(self) -> None:
        if self._window is not None:
            self._window.stop()
            self._window = None


# --- Win32 (only ever run on Windows) ------------------------------------------------


class _Window:
    """A hidden top-level window and its message loop, on a thread of its own.
    (A message-only window would not be sent the broadcasts.)"""

    def __init__(self, handle: Callable[[int, int, int], int | None]):
        self._handle = handle
        self._hwnd = None
        self._user32 = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rgbhub-session", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(5):
            log.warning("Could not make the window that hears about sleep and sign-out")

    def stop(self) -> None:
        if self._hwnd and self._user32 is not None:
            self._user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            self._thread.join(2)

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("The sleep and sign-out window failed; those will not be heard")
        finally:
            self._ready.set()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        HWND, UINT, WPARAM, LPARAM = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.CreateWindowExW.restype = HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [HWND]
        user32.PostQuitMessage.restype = None
        user32.PostQuitMessage.argtypes = [ctypes.c_int]

        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            try:
                answer = self._handle(message, wparam, lparam)
            except Exception:  # an exception must never cross back into Windows
                log.exception("Error handling window message %#x", message)
                answer = None
            if answer is not None:
                return answer
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._proc = WNDPROC(window_proc)  # kept: Windows calls it for as long as we run
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._proc
        window_class.hInstance = instance
        window_class.lpszClassName = CLASS_NAME
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != ERROR_CLASS_ALREADY_EXISTS:
                raise ctypes.WinError(error)
        # Not WS_VISIBLE: never shown. The title is RGB-Hub's own (focus.py ignores it).
        hwnd = user32.CreateWindowExW(
            0, CLASS_NAME, "RGB-Hub", 0, 0, 0, 0, 0, None, None, instance, None
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._user32, self._hwnd = user32, hwnd
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
