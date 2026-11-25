# SPDX-License-Identifier: GPL-3.0-or-later
"""An in-memory stand-in for the `hid` module of the hidapi package.

Mirrors the real API (enumerate, device().open_path/set_nonblocking/write/
read/close) so the real module can be dropped in on Windows.
"""

import time


class FakeHidModule:
    def __init__(self):
        self._devices: dict[bytes, dict] = {}
        self._queues: dict[bytes, list[bytes]] = {}
        self._writes: dict[bytes, list[bytes]] = {}
        self.enumerate_calls = 0
        self.report_length: dict[bytes, int] = {}  # output-report length per device
        self.read_timeouts: list[int] = []
        self.device = lambda: _FakeDevice(self)

    # test helpers -------------------------------------------------------
    def plug(self, path: bytes, vendor: int, product: int, usage_page: int = 0, usage: int = 0):
        self._devices[path] = {
            "path": path,
            "vendor_id": vendor,
            "product_id": product,
            "usage_page": usage_page,
            "usage": usage,
            "interface_number": 0,
        }
        self._queues.setdefault(path, [])
        self._writes.setdefault(path, [])

    def unplug(self, path: bytes):
        self._devices.pop(path, None)

    def queue_reply(self, path: bytes, data: bytes):
        self._queues[path].append(data)

    def written(self, path: bytes) -> list[bytes]:
        return self._writes[path]

    # hid API -----------------------------------------------------------
    def enumerate(self, vendor_id: int = 0, product_id: int = 0) -> list[dict]:
        self.enumerate_calls += 1
        return [
            dict(d)
            for d in self._devices.values()
            if vendor_id in (0, d["vendor_id"]) and product_id in (0, d["product_id"])
        ]


class _FakeDevice:
    def __init__(self, module: FakeHidModule):
        self._m = module
        self._path: bytes | None = None

    def open_path(self, path: bytes):
        if path not in self._m._devices:
            raise OSError("open failed")
        self._path = path

    def set_nonblocking(self, value: int):
        return 0

    def write(self, data) -> int:
        if self._path is None or self._path not in self._m._devices:
            return -1
        self._m._writes[self._path].append(bytes(data))
        return max(len(data), self._m.report_length.get(self._path, 0))

    def read(self, max_length: int, timeout_ms: int = 0) -> list[int]:
        if self._path is None or self._path not in self._m._devices:
            raise OSError("read error")
        self._m.read_timeouts.append(timeout_ms)
        queue = self._m._queues[self._path]
        if queue:
            return list(queue.pop(0)[:max_length])
        time.sleep(min(timeout_ms, 20) / 1000)
        return []

    def close(self):
        self._path = None
