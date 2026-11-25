# SPDX-License-Identifier: GPL-3.0-or-later
"""USB HID devices on Windows through the hidapi package (module `hid`).

Paths are hidapi's byte paths, carried as latin-1 text so they fit the
HidBus interface. On Windows hidapi wants the full output-report length
including the report id byte; reports are passed on unchanged.
"""

import time

from ...core.interfaces import HidMatch

_READ_SIZE = 64
_PRESENCE_CACHE_S = 1.0


def _present(hid, vendor: int, product: int, path: bytes) -> bool:
    return any(d["path"] == path for d in hid.enumerate(vendor, product))


class HidapiPort:
    """An open hidapi device. Failures raise OSError."""

    def __init__(self, hid, device, path: bytes, vendor: int, product: int, clock):
        self._hid = hid
        self._device = device
        self._path = path
        self._vendor = vendor
        self._product = product
        self._clock = clock
        self._checked = float("-inf")
        self._present = True
        self._open = True

    def write(self, report: bytes) -> None:
        if not self._open:
            raise OSError("connection is closed")
        if not self.alive():
            raise OSError("device is unplugged")
        n = self._device.write(report)
        # hidapi may pad a short report up to the output-report length and return that.
        if n < 0 or n < len(report):
            raise OSError("write failed")

    def read(self, timeout_s: float) -> bytes | None:
        """Wait up to timeout_s; 0 polls for 1 ms, because hidapi treats a
        timeout of 0 as a plain blocking read."""
        if not self._open:
            raise OSError("connection is closed")
        data = self._device.read(_READ_SIZE, max(1, round(timeout_s * 1000)))
        return bytes(data) if data else None

    def alive(self) -> bool:
        if not self._open:
            return False
        now = self._clock()
        if now - self._checked > _PRESENCE_CACHE_S:
            self._present = _present(self._hid, self._vendor, self._product, self._path)
            self._checked = now
        return self._present

    def close(self) -> None:
        if self._open:
            self._open = False
            try:
                self._device.close()
            except OSError:
                pass


class HidapiBus:
    """Finds and opens HID interfaces with hidapi. `hid_module` is for tests."""

    def __init__(self, hid_module=None, clock=time.monotonic):
        if hid_module is None:
            import hid as hid_module  # imported late: only installed on Windows
        self._hid = hid_module
        self._clock = clock
        self._ids: dict[str, tuple[int, int]] = {}

    def find(self, match: HidMatch) -> str | None:
        for d in self._hid.enumerate(match.vendor, match.product):
            if match.usage_page is not None and d["usage_page"] != match.usage_page:
                continue
            if match.usage is not None and d["usage"] != match.usage:
                continue
            path = d["path"].decode("latin-1")
            self._ids[path] = (match.vendor, match.product)
            return path
        return None

    def open(self, path: str, nonblocking: bool = False) -> HidapiPort:
        raw = path.encode("latin-1")
        vendor, product = self._ids.get(path, (0, 0))
        device = self._hid.device()
        try:
            device.open_path(raw)
            if nonblocking:
                device.set_nonblocking(1)
        except Exception as e:  # hidapi raises plain exceptions on some failures
            raise OSError(str(e)) from e
        return HidapiPort(self._hid, device, raw, vendor, product, self._clock)
