# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the service for the system it is running on.

    python -m rgbhub                      (works out the platform itself)
    python -m rgbhub --platform gnome ... (or name it)
    python -m rgbhub --self-check [--platform X]  (builds the devices on fakes)

To add a system, write platforms/<name>.py with a main(argv) function and
list it in PLATFORMS below.
"""

import importlib
import sys
from pathlib import Path

PLATFORMS = ("gnome", "batocera", "windows")


def detect() -> str:
    """Windows by its name; Batocera identifies itself with this file;
    everything else is treated as a GNOME desktop for now."""
    if sys.platform == "win32":
        return "windows"
    return "batocera" if Path("/usr/share/batocera/batocera.version").exists() else "gnome"


def self_check(name: str) -> int:
    """Build every device on a fake platform: touches no hardware or settings."""
    try:
        importlib.import_module(f"rgbhub.platforms.{name}")
        from .core.interfaces import FakeHidBus, FakeRamBus, ManualRuntime
        from .core.platform import Platform
        from .devices import build_all

        devices = build_all(Platform(name, FakeHidBus({}), ManualRuntime(), FakeRamBus([])))
        for device in devices:
            if not (device.id and device.label and callable(device.status)):
                raise ValueError(f"{device!r} does not describe itself")
    except Exception as error:
        print(f"self-check failed: {error!r}", file=sys.stderr)
        return 1
    print(f"ok {len(devices)} devices")
    return 0


def main(argv: list[str]) -> int:
    check = "--self-check" in argv
    argv = [arg for arg in argv if arg != "--self-check"]
    name = None
    if len(argv) >= 2 and argv[0] == "--platform":
        name, argv = argv[1], argv[2:]
    name = name or detect()
    if name not in PLATFORMS:
        print(f"Unknown platform '{name}'. Known: {', '.join(PLATFORMS)}", file=sys.stderr)
        return 2
    if check:
        return self_check(name)
    return importlib.import_module(f"rgbhub.platforms.{name}").main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
