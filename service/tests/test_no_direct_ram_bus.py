# SPDX-License-Identifier: GPL-3.0-or-later
"""Nothing in this project may talk to the RAM's control bus (SMBus / I2C).

On the OMEN 45L any direct read or write to the RAM light controllers
(0x61 / 0x63) jams the bus until the next reboot; it happened three times.
The worst case is worse: a write landing on a stick's SPD EEPROM (0x50-0x57)
can corrupt it for good. RAM lighting goes through the BIOS only (see
helper/rgbhub_ram_helper.py).

This scans every code file in the repo (Python, shell, Batocera hooks,
systemd units, the GNOME extension) for ways of reaching the bus. Reading
the kernel's list of devices under /sys/bus/i2c/devices to see whether a
stick is fitted does not touch the bus and is allowed, in the files named
in ALLOWED below.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()

SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", ".superpowers", "node_modules"}
SKIP_SUFFIXES = {".md", ".ko", ".compiled", ".png", ".jpg", ".jpeg", ".gif", ".pyc", ".svg"}

# (pattern, why it is forbidden)
FORBIDDEN = [
    (r"\bimport\s+smbus2?\b|\bfrom\s+smbus2?\b", "SMBus library"),
    (r"\bsmbus2?\.", "SMBus library"),
    (r"\b(?:import|from)\s+(?:periphery|pyftdi\.i2c|i2c|pyi2c|fcntl)\b", "raw bus access"),
    (r"\bioctl\b", "raw ioctl (I2C_SLAVE / I2C_RDWR)"),
    (r"\bI2C_(?:SLAVE|RDWR|SMBUS)\b", "I2C ioctl"),
    (r"/dev/i2c", "the I2C device nodes"),
    (r"\bi2c(?:get|set|detect|transfer|dump)\b", "i2c-tools"),
    (r"\bdecode-dimms\b", "i2c-tools (reads the SPD EEPROM)"),
    (r"\bi2c-tools\b", "i2c-tools"),
    # Windows: the port-access drivers that SMBus tools (OpenRGB among them) use.
    (r"(?i)\bwinring0|\binpout", "raw port access on Windows"),
]

# ctypes reaches any C function, so it is allowed only where Windows' own desktop
# functions are called (the focused window, icons, wallpaper, session, tray), and
# only into the DLLs in WINDOWS_DLLS below (none of which reaches a bus).
CTYPES_FILES = {
    f"service/rgbhub/platforms/windows/{name}.py"
    for name in ("focus", "icons", "wallpaper", "session", "tray", "__init__")
}
WINDOWS_DLLS = {
    "user32", "kernel32", "shell32", "gdi32", "ole32", "combase", "dwmapi", "advapi32",
}  # fmt: skip
IMPORTS_CTYPES = re.compile(r"\b(?:import|from)\s+ctypes\b|\b__import__\(\s*[\"']ctypes")
# Any way of loading a DLL by name: ctypes.WinDLL("x"), windll.x, cdll.LoadLibrary("x"), ...
LOADS_DLL = re.compile(
    r"\b(?:WinDLL|CDLL|OleDLL|PyDLL|LoadLibrary\w*)\s*\(\s*(?P<call>[^,)]*)"
    r"|\b(?:windll|oledll|cdll|pydll)\.(?P<attr>\w+)",
    re.IGNORECASE,
)
# Kernel drivers that give programs raw port / SMBus access on Windows.
PORT_DRIVERS = re.compile(r"(?i)winring0|inpout|pawnio")

# Mentions of the I2C bus that are allowed, and why. Only these files may
# mention I2C at all; anything else that does is flagged by the test below.
ALLOWED = {
    # Reads /sys/bus/i2c/devices/*/name to see which slots have a stick.
    # Plain sysfs files the kernel fills in; no bus traffic.
    "helper/rgbhub_ram_helper.py": "sysfs presence check",
    "service/rgbhub/platforms/linux/ram.py": "sysfs presence check",
    # Loads the i2c-dev module at boot because OpenRGB expects it. Loading
    # the module sends nothing; OpenRGB's RAM detectors are switched off.
    "install/setup.sh": "loads the i2c-dev module for OpenRGB",
    # The udev rule that locks the RAM's SMBus to root (matches the i2c-dev
    # subsystem by name; sends nothing).
    "install/71-rgbhub-ram-bus-lock.rules": "locks the SMBus device node to root",
}
I2C_MENTION = re.compile(r"i2c", re.IGNORECASE)


def code_files():
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.resolve() == THIS_FILE:
            continue
        rel = path.relative_to(REPO)
        dirs = rel.parts[:-1]
        if set(dirs) & SKIP_DIRS or any(d.startswith(".") or d.endswith(".egg-info") for d in dirs):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue  # binary
        yield rel.as_posix(), text


FILES = list(code_files())


def test_the_scan_sees_the_code():
    names = {name for name, _ in FILES}
    # If these go missing the scan has stopped looking where the code is.
    for expected in (
        "helper/rgbhub_ram_helper.py",
        "service/rgbhub/platforms/linux/ram.py",
        "service/rgbhub/devices/omen_pc/ram.py",
        "helper/rgbhub_ram_helper_windows.py",
        "service/rgbhub/platforms/windows/ram.py",
        "install/install-user.sh",
        "install/setup.sh",
        "frontends/batocera/services/rgb_hub",
        "systemd/openrgb-server.service",
        "frontends/gnome-shell/rgbhub@konfus-dev.github.io/extension.js",
    ):
        assert expected in names
    assert len(FILES) > 50


@pytest.mark.parametrize("pattern,why", FORBIDDEN, ids=[why for _, why in FORBIDDEN])
def test_nothing_reaches_the_ram_bus_directly(pattern, why):
    regex = re.compile(pattern)
    hits = [
        f"{name}:{number}: {line.strip()}"
        for name, text in FILES
        for number, line in enumerate(text.splitlines(), 1)
        if regex.search(line)
    ]
    assert hits == [], f"{why} found; this jams the RAM bus on the OMEN 45L:\n" + "\n".join(hits)


# The code that deals with the RAM. None of it may name the light controllers'
# own bus addresses: it only ever names them as the BIOS wants them (0xC0-0xC6).
RAM_CODE = [
    name
    for name, _ in FILES
    if name.startswith(("helper/", "service/rgbhub/platforms/windows/"))
    or name
    in {
        "service/rgbhub/platforms/linux/ram.py",
        "service/rgbhub/devices/omen_pc/ram.py",
    }
]
CONTROLLER_ADDRESS = re.compile(r"\b0x6[13]\b|\b0x[cC][2-7]\s*>>|\b9[79]\b", re.IGNORECASE)


def test_the_ram_code_never_names_the_controllers_bus_addresses():
    assert "helper/rgbhub_ram_helper_windows.py" in RAM_CODE
    assert "service/rgbhub/platforms/windows/ram.py" in RAM_CODE
    hits = [
        f"{name}:{number}: {line.strip()}"
        for name, text in FILES
        if name in RAM_CODE
        for number, line in enumerate(text.splitlines(), 1)
        if CONTROLLER_ADDRESS.search(line)
    ]
    assert hits == [], "\n".join(hits)


def test_ctypes_only_in_the_windows_desktop_files():
    using = {name for name, text in FILES if IMPORTS_CTYPES.search(text)}
    assert using <= CTYPES_FILES, sorted(using - CTYPES_FILES)
    assert "service/rgbhub/platforms/windows/focus.py" in using  # the scan sees them


def _dll_name(match: re.Match) -> str:
    """The DLL a LOADS_DLL match loads: its file name without folder or ".dll"
    (anything that is not a plain name, like a variable, stays as written)."""
    if match["attr"] is not None:
        return match["attr"].lower()
    name = match["call"].strip().lstrip("rRbB").strip("\"'")
    return re.split(r"[\\/]+", name)[-1].lower().removesuffix(".dll")


def test_only_windows_own_dlls_are_loaded():
    hits = [
        f"{name}:{number}: {line.strip()}"
        for name, text in FILES
        if name.startswith(("service/", "helper/"))
        for number, line in enumerate(text.splitlines(), 1)
        for match in LOADS_DLL.finditer(line)
        if _dll_name(match) not in WINDOWS_DLLS
    ]
    assert hits == [], "Only Windows' own DLLs may be loaded:\n" + "\n".join(hits)


def test_the_dll_check_catches_other_dlls():
    def names(line):
        return [_dll_name(m) for m in LOADS_DLL.finditer(line)]

    assert names('ctypes.WinDLL("user32", use_last_error=True)') == ["user32"]
    assert names("ctypes.windll.kernel32.Beep(1, 1)") == ["kernel32"]
    assert names('ctypes.WinDLL(r"C:\\Tools\\WinRing0x64.dll")') == ["winring0x64"]
    assert names('ctypes.cdll.LoadLibrary("inpoutx64.dll")') == ["loadlibrary"]
    assert names("ctypes.CDLL(path)") == ["path"]


def test_no_port_access_drivers_are_named():
    hits = [
        f"{name}:{number}: {line.strip()}"
        for name, text in FILES
        if name.startswith(("service/", "helper/"))
        for number, line in enumerate(text.splitlines(), 1)
        if PORT_DRIVERS.search(line)
    ]
    assert hits == [], "\n".join(hits)


def test_only_the_listed_files_mention_i2c():
    mentioning = {name for name, text in FILES if I2C_MENTION.search(text)}
    unexpected = mentioning - set(ALLOWED)
    assert not unexpected, (
        "These files mention I2C. Direct RAM bus access is forbidden; if this is only a "
        f"sysfs presence check, add it to ALLOWED with the reason: {sorted(unexpected)}"
    )


def test_i2c_mentions_in_code_are_sysfs_reads_only():
    for name in ("helper/rgbhub_ram_helper.py", "service/rgbhub/platforms/linux/ram.py"):
        text = dict(FILES)[name]
        for line in text.splitlines():
            if I2C_MENTION.search(line) and "/" in line and '"' in line:
                assert '"/sys/bus/i2c/devices"' in line, line


def test_setup_only_loads_the_module():
    lines = [
        line.strip()
        for line in dict(FILES)["install/setup.sh"].splitlines()
        if I2C_MENTION.search(line) and not line.strip().startswith(("#", "echo"))
    ]
    assert lines == [
        "printf 'i2c-dev\\n' > /etc/modules-load.d/i2c-dev.conf",
        'modprobe i2c-dev || warn "could not load i2c-dev"',
        # the lock: the rule file installed, udev told, earlier access taken back
        "udevadm trigger --subsystem-match=i2c-dev --action=change",
        "for bus in /sys/class/i2c-dev/*; do",
    ]


# --- programs the service may start --------------------------------------------

# Every place in the Python code that starts another program, and why it is safe.
ALLOWED_PROGRAMS = {
    "service/rgbhub/platforms/gnome/gnome_migrate.py": "dconf dump: reads old settings",
    "service/rgbhub/platforms/linux/ram.py": "dmidecode -t 17: the BIOS's memory table, no bus",
    "install/enable-gnome-extension.py": "gsettings get/set org.gnome.shell (installer)",
    "helper/rgbhub_ram_helper_windows.py": "Windows PowerShell: HP's BIOS WMI method, no bus",
}
STARTS_PROGRAM = re.compile(r"\bsubprocess\b|\bos\.(?:system|popen|exec\w*|spawn\w*)\b")


def test_python_starts_only_the_listed_programs():
    starting = {
        name
        for name, text in FILES
        if name.endswith(".py") and not name.startswith("service/tests/")
        if STARTS_PROGRAM.search(text)
    }
    assert starting == set(ALLOWED_PROGRAMS)
    commands = re.findall(
        r'\["dconf", "dump"|\("dmidecode", "-t", "17"\)'
        r'|os\.environ\.get\("GSETTINGS", "gsettings"\)'
        r'|\[str\(exe\), "-NoProfile", "-NonInteractive", "-Command", "-"\]',
        "".join(dict(FILES)[name] for name in ALLOWED_PROGRAMS),
    )
    assert len(commands) == 4


def test_the_windows_helper_starts_only_powershell():
    text = dict(FILES)["helper/rgbhub_ram_helper_windows.py"]
    assert re.findall(r"\bsubprocess\.\w+\(", text) == ["subprocess.Popen("]
    assert "self._command or powershell_command()" in text


# --- the RAM's bus is root-only on the PC (install/71-rgbhub-ram-bus-lock.rules) ---------------

_LOCK = Path(__file__).resolve().parents[2] / "install" / "71-rgbhub-ram-bus-lock.rules"


def test_the_ram_bus_is_locked_to_root_by_udev():
    text = _LOCK.read_text().splitlines()
    rules = [line for line in text if line.strip() and not line.startswith("#")]
    assert len(rules) == 1
    rule = rules[0]
    assert (
        'SUBSYSTEM=="i2c-dev"' in rule and 'ATTR{name}=="SMBus*"' in rule
    )  # only the chipset's SMBus
    assert 'TAG-="uaccess"' in rule  # no access for the logged-in user (OpenRGB's rule adds it)
    assert 'MODE="0600"' in rule and 'GROUP="root"' in rule
    # after the rules that add "uaccess" (60-*) and before 73-seat-late.rules turns it into access
    assert 60 < int(_LOCK.name.split("-")[0]) < 73


def test_setup_installs_the_lock_and_takes_back_earlier_access():
    setup = (_LOCK.parent / "setup.sh").read_text()
    assert '71-rgbhub-ram-bus-lock.rules" /etc/udev/rules.d/' in setup
    assert "udevadm trigger --subsystem-match=i2c-dev" in setup
    assert "setfacl -b" in setup
