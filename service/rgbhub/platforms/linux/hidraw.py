# SPDX-License-Identifier: GPL-3.0-or-later
"""USB HID devices through the Linux kernel's hidraw interface."""

import os
import select
from pathlib import Path

from ...core.interfaces import HidMatch

_READ_SIZE = 4096


def usage_of(descriptor: bytes) -> tuple[int | None, int | None]:
    """The first Usage Page and first Usage of a report descriptor (None if absent)."""
    page = usage = None
    i = 0
    while i < len(descriptor) and (page is None or usage is None):
        prefix = descriptor[i]
        if prefix == 0x06 and page is None and i + 2 < len(descriptor):
            page = descriptor[i + 1] | descriptor[i + 2] << 8
            i += 3
        elif prefix == 0x05 and page is None and i + 1 < len(descriptor):
            page = descriptor[i + 1]
            i += 2
        elif prefix == 0x09 and usage is None and i + 1 < len(descriptor):
            usage = descriptor[i + 1]
            i += 2
        elif prefix == 0x0A and usage is None and i + 2 < len(descriptor):
            usage = descriptor[i + 1] | descriptor[i + 2] << 8
            i += 3
        else:
            i += 1
    return page, usage


class HidrawPort:
    """An open hidraw node. Failures raise OSError."""

    def __init__(self, node: Path, nonblocking: bool = False):
        self._node = node
        flags = os.O_NONBLOCK if nonblocking else 0
        self._fd: int | None = os.open(node, os.O_RDWR | flags)

    def write(self, report: bytes) -> None:
        if self._fd is None:
            raise OSError("connection is closed")
        if not self.alive():
            raise OSError("device is unplugged")
        os.write(self._fd, report)

    def read(self, timeout_s: float) -> bytes | None:
        if self._fd is None:
            raise OSError("connection is closed")
        ready, _, _ = select.select([self._fd], [], [], timeout_s)
        if not ready:
            return None
        data = os.read(self._fd, _READ_SIZE)
        return data or None

    def alive(self) -> bool:
        return self._fd is not None and self._node.exists()

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


class HidrawBus:
    """Finds and opens HID interfaces under /dev/hidrawN."""

    def __init__(
        self, sys_root: Path = Path("/sys/class/hidraw"), dev_root: Path = Path("/dev")
    ):
        self._sys_root = sys_root
        self._dev_root = dev_root

    def find(self, match: HidMatch) -> str | None:
        hid_id = f"HID_ID=0003:{match.vendor:08X}:{match.product:08X}"
        try:
            nodes = sorted(self._sys_root.iterdir())
        except OSError:
            return None
        for node in nodes:
            try:
                uevent = (node / "device" / "uevent").read_text()
                descriptor = (node / "device" / "report_descriptor").read_bytes()
            except OSError:
                continue
            if hid_id not in uevent:
                continue
            page, usage = usage_of(descriptor)
            if match.usage_page is not None and page != match.usage_page:
                continue
            if match.usage is not None and usage != match.usage:
                continue
            return str(self._dev_root / node.name)
        return None

    def open(self, path: str, nonblocking: bool = False) -> HidrawPort:
        return HidrawPort(Path(path), nonblocking)
