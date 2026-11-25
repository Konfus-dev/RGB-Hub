# RGB-Hub: platforms, device list, Windows and self-updater — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure RGB-Hub into core / devices / platforms with formal interfaces, add a Windows platform (replacing OMEN Gaming Hub) and a self-updater that follows `main` on Ubuntu, Batocera and Windows.

**Architecture:** OS-neutral core and device packages depend only on Protocols in `core/interfaces.py` (`HidBus`, `HidPort`, `RamBus`, `Runtime`, `Settings`). Each system is a folder under `platforms/` implementing those and building the hub from the single `devices.DEVICES` list. A stdlib-only `rgbhub.updater` process installs new commits of `main` into per-version folders, switches a `current` pointer, restarts the hub and rolls back on failure.

**Tech Stack:** Python ≥ 3.12 (Ubuntu 3.14, Windows embeddable 3.13), Pillow, openrgb-python (GNOME only), hidapi + pystray (Windows only), PyGObject/GLib (Linux only), ctypes for Win32, PowerShell for WMI/CIM/setup, bash installers, pytest + ruff.

**Spec:** `docs/superpowers/specs/2026-11-24-windows-design.md` (read it, and `docs/design.md`, before starting).

## Global Constraints

- Never touch the RAM's SMBus (0x61/0x63) on any system; RAM only via the BIOS (`\_SB.WMID.WMAA` on Linux, WMI `hpqBIntM.hpqBIOSInt128` on Windows). `tests/test_no_direct_ram_bus.py` must keep passing and must cover the new Windows files.
- Linux behaviour unchanged: the existing 416 tests keep passing (imports/constructor arguments may be updated, assertions not weakened).
- Only the RAM helper runs elevated; the hub, tray, web server and updater run with the owner's normal rights (Batocera: everything root, as today).
- The updater is stdlib-only and never moves backwards: apply a commit only if GitHub `compare/<installed>...<new>` reports `"status": "ahead"`.
- Repo for updates: `Konfus-dev/RGB-Hub`, branch `main`. HTTP User-Agent `rgbhub-updater` (no personal data).
- Updates never write settings files; Windows settings are seeded once, never overwritten.
- Settings window, tray and installers use the owner's wording style: plain sentences, no jargon.
- Work on branch `dev`. Commit after every task with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push (no push access).
- Every new source file starts with `# SPDX-License-Identifier: GPL-3.0-or-later`; `ruff check service` stays clean (line length 100).
- Run tests with `cd service && ../.venv/bin/python -m pytest -q`.

## Review Focus

1. GitHub unreachable, rate-limited (HTTP 403/429) or returning 404 for an unknown installed commit → updater logs one line, keeps the current version, tries again next hour; never activates anything. (Task 13)
2. An update interrupted mid-download/unpack (power cut) → the half-made version folder is never activated and is deleted on the next run. (Task 13)
3. A device unplugged and replugged on Windows (hidapi path changes) → `alive()` turns False, the device reconnects within the slow tick, exactly as on Linux. (Task 8)
4. Foreground window that cannot be inspected (elevated app → `OpenProcess` access denied, desktop, lock screen, no window) → focus reads as `""` (idle look), no exception escapes the poll. (Task 11)
5. The web page posting a bad value (unknown key, wrong type, `status`) or no/incorrect token → HTTP 400/403, nothing written. (Task 10)

---

## Target layout (after Task 5)

```
service/rgbhub/
  __main__.py                 detect() -> "gnome" | "batocera" | "windows"; --self-check
  core/   interfaces.py hub.py device.py look.py lights.py light_group.py
          settings_schema.py platform.py json_store.py thread_runtime.py lcd_folder.py
  devices/__init__.py         DEVICES = (omen_pc, iqunix_keyboard)
  devices/omen_pc/            __init__.py (build, DEVICE_ID) device.py model.py (was omen45l.py)
                              lcd_constants.py (was core/constants.py) lcd_protocol.py
                              lcd_streamer.py lcd_fade.py image_prep.py case_board.py (was
                              backends/tracer.py) ram.py (was core/ram.py)
  devices/iqunix_keyboard/    __init__.py (build, DEVICE_ID) device.py via.py mg65.py
  platforms/linux/            hidraw.py glib_runtime.py ram.py (was backends/ram_bios.py) openrgb.py
  platforms/gnome/            __init__.py(main) gsettings_store.py gnome_theme.py gtk_icons.py
                              logind.py gnome_migrate.py
  platforms/batocera/         __init__.py(main) games.py theme.py
  platforms/windows/          __init__.py(main) hid.py ram.py focus.py icons.py wallpaper.py
                              session.py tray.py web.py launcher.py
  updater/                    __main__.py github.py store.py flow.py
                              layouts/{gnome,batocera,windows}.py
helper/rgbhub_ram_helper.py           (shared checks; Linux acpi_call transport)
helper/rgbhub_ram_helper_windows.py   (named pipe + WMI transport)
frontends/web/                index.html app.js style.css
install/windows/              finish-setup.ps1 uninstall.ps1 RGB-Hub-setup.cmd approve-privileged.ps1
install/install-windows.sh    install/windows_settings.py
systemd/rgbhub-update.service systemd/rgbhub-update.timer
```

---

### Task 1: Interfaces and contract-test helpers

**Files:**
- Modify: `service/rgbhub/core/interfaces.py`
- Create: `service/tests/contracts.py`, `service/tests/test_contracts.py`

**Interfaces:**
- Produces (in `core/interfaces.py`):
  - `@dataclass(frozen=True) class HidMatch: vendor: int; product: int; usage_page: int | None = None; usage: int | None = None`
  - `class HidPort(Protocol): write(report: bytes) -> None; read(timeout_s: float) -> bytes | None; alive() -> bool; close() -> None` (failures raise `OSError`)
  - `class HidBus(Protocol): find(match: HidMatch) -> str | None; open(path: str, nonblocking: bool = False) -> HidPort`
  - `Command = tuple[int, int, int]` (address, register, value) — moved here from `core/ram.py` (re-export there until Task 3 moves it)
  - `class RamBus(Protocol): send(batch: list[Command]) -> None; sticks() -> list[int]`
  - `class Runtime(Scheduler, Protocol): watch_file(path: str, on_change) -> Callable[[], None]; call_soon(fn: Callable[[], None]) -> None; run_until_stopped() -> None; stop() -> None`
  - `class FakeHidBus` / `FakeHidPort` (in-memory; `FakeHidBus(devices: dict[str, tuple[HidMatch, FakeHidPort]])`, `unplug(path)`), kept next to `ManualScheduler` for tests and self-check.
- Produces (in `tests/contracts.py`): `check_hid_bus(bus, present_match, path_of_present, unplug)`, `check_runtime(runtime_factory)` — shared assertion functions later tasks parametrise.

- [ ] **Step 1: Write failing tests** in `tests/test_contracts.py`:
  - `test_fake_hid_bus_contract`: `check_hid_bus(FakeHidBus(...), ...)` where `check_hid_bus` asserts `find(match) == path`, `find(HidMatch(1, 2)) is None`, written reports recorded in order, `read(0.01)` returns queued reply then `None`, `alive()` True then False after `unplug(path)`, `write` after unplug raises `OSError`.
  - `test_hidmatch_any_interface`: `HidMatch(0x103C, 0x84FD)` matches a device of any usage page in `FakeHidBus`.
- [ ] **Step 2: Run** `../.venv/bin/python -m pytest tests/test_contracts.py -q` → FAIL (ImportError).
- [ ] **Step 3: Implement** the Protocols, `HidMatch`, `Command`, `FakeHidBus`, `FakeHidPort` and `tests/contracts.py`. Update the module docstring's "Shapes" list to name the new interfaces.
- [ ] **Step 4: Run** full suite → all pass.
- [ ] **Step 5: Commit** `Interfaces: HidBus, HidPort, RamBus, Runtime and contract tests`.

### Task 2: Linux HidBus over hidraw

**Files:**
- Create: `service/rgbhub/platforms/linux/__init__.py`, `service/rgbhub/platforms/linux/hidraw.py`
- Modify: `service/tests/test_hidraw.py`, `service/tests/test_contracts.py`

**Interfaces:**
- Consumes: `HidMatch`, `HidBus`, `HidPort` (Task 1).
- Produces: `class HidrawBus(sys_root: Path = Path("/sys/class/hidraw"), dev_root: Path = Path("/dev"))` fitting `HidBus`; `class HidrawPort` fitting `HidPort` (`read` via `select`, `alive()` = node still exists); `usage_of(descriptor: bytes) -> tuple[int | None, int | None]` (first Usage Page item `0x06 lo hi` / `0x05 v` and first Usage `0x09 v` / `0x0a lo hi`).
- Matching: uevent `HID_ID=0003:0000VVVV:0000PPPP` vs `match.vendor/product`; `usage_page`/`usage` compared only when not None.

- [ ] **Step 1: Write failing tests**: `test_usage_of_lcd_prefix` (`usage_of(bytes.fromhex("061aff0901"))[0] == 0xFF1A`), `test_usage_of_via_prefix` (`bytes.fromhex("0660ff0961")` → `(0xFF60, 0x61)`), `test_hidraw_bus_contract` running `check_hid_bus` on a fake sysfs tree in `tmp_path` (two LCD interfaces: mouse-class `05010902` and vendor `061aff`; only the vendor one is found), with a FIFO or a regular file as the node.
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement** `platforms/linux/hidraw.py`. Leave `backends/hidraw.py` in place for now (removed in Task 5).
- [ ] **Step 4: Run** full suite → pass.
- [ ] **Step 5: Commit** `Linux: HidrawBus finds HID interfaces by vendor, product and usage`.

### Task 3: Device packages (protocol code moves to its device)

**Files:**
- Create: `service/rgbhub/devices/omen_pc/{__init__,device,model,lcd_constants,lcd_protocol,lcd_streamer,lcd_fade,image_prep,case_board,ram}.py`, `service/rgbhub/devices/iqunix_keyboard/{__init__,device,via,mg65}.py`
- Delete: `devices/omen_pc.py`, `devices/iqunix_keyboard.py`, `omen45l.py`, `iqunix_mg65.py`, `core/constants.py`, `core/lcd_protocol.py`, `core/lcd_streamer.py`, `core/lcd_fade.py`, `core/image_prep.py`, `core/ram.py`, `backends/tracer.py`, `backends/via.py`
- Modify: every test importing those; `tests/test_no_direct_ram_bus.py` paths; `platforms/gnome.py`, `platforms/batocera.py` imports.

**Interfaces:**
- Consumes: `HidBus`, `HidMatch`, `HidPort` (Task 1).
- Produces:
  - `devices/omen_pc/lcd_constants.py`: `LCD_MATCH = HidMatch(0x03F0, 0x7397, usage_page=0xFF1A)` replaces `LCD_HID_ID`/`LCD_DESCRIPTOR_PREFIX`.
  - `devices/omen_pc/case_board.py`: `BOARD_MATCH = HidMatch(0x103C, 0x84FD)`; `open_board(bus: HidBus) -> TracerClient` (raises `OSError` if absent); `TracerClient.update()` raises `OSError` when `not port.alive()`.
  - `devices/omen_pc/lcd_streamer.py`: unchanged API; new `lcd_opener(bus: HidBus) -> Callable[[], HidPort | None]` (opens non-blocking).
  - `devices/iqunix_keyboard/mg65.py`: `MG65.match = HidMatch(0x320F, 0x5055, usage_page=0xFF60, usage=0x61)` replaces `hid_id`.
  - `devices/iqunix_keyboard/via.py`: `opener(model, bus: HidBus) -> Callable[[], ViaClient]`; `_Keyboard` drains replies with `port.read(REPLY_WAIT_S)` until `None`; `ViaClient.update()` uses `alive()`.
  - Classes keep their names: `OmenPc` (in `devices/omen_pc/device.py`), `IqunixKeyboard`, `RamLights`.
- `ViaPort`, `LcdDevice`, `find_hidraw`, `find_hid_node` disappear; tests use `FakeHidBus`.

- [ ] **Step 1: Move files with `git mv`** to keep history, then fix imports until `python -c "import rgbhub.devices.omen_pc.device, rgbhub.devices.iqunix_keyboard.device"` works.
- [ ] **Step 2: Rewrite the HID-touching tests** (`test_via.py`, `test_tracer.py`, `test_lcd_streamer.py`, `test_hidraw.py` LCD part) against `FakeHidBus`, keeping every existing assertion about bytes sent. Add `test_via_drains_replies_through_read` (two queued replies are both consumed after one set) and `test_case_board_gone_when_port_not_alive`.
- [ ] **Step 3: Run** full suite → pass.
- [ ] **Step 4: Commit** `Devices: each product's protocol code lives with it and talks through HidBus`.

### Task 4: RamBus

**Files:**
- Create: `service/rgbhub/platforms/linux/ram.py`
- Delete: `service/rgbhub/backends/ram_bios.py`
- Modify: `devices/omen_pc/ram.py`, `tests/test_ram.py`, `tests/test_hub_omen_pc.py`, gnome/batocera platform wiring

**Interfaces:**
- Consumes: `RamBus`, `Command` (Task 1).
- Produces:
  - `RamLights(bus: RamBus, gamma: float = 1.0, background: bool = False)` (was `(send, find, ...)`).
  - `platforms/linux/ram.py`: `class HelperRamBus(socket_path: str = "/run/rgbhub-ram.sock")` (today's `send_to_helper` + `find_sticks`); `class AcpiRamBus(helper_module, find: Callable[[], list[int]])` (today's `direct_sender`); keep `find_sticks`, `sticks_from_dmidecode`, `find_sticks_dmi` as module functions.
  - `FakeRamBus` in `core/interfaces.py` (records batches; `sticks` given).

- [ ] **Step 1: Update tests** to construct `RamLights(FakeRamBus([0xC2, 0xC6]))`; add `test_contracts.py::test_ram_buses_fit` (both Linux classes and the fake satisfy `isinstance(x, RamBus)` with `@runtime_checkable`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** full suite → pass.
- [ ] **Step 5: Commit** `RAM: RamLights takes one RamBus; Linux helper and acpi_call paths implement it`.

### Task 5: Platform packages; "backends" gone

**Files:**
- Move: `platforms/gnome.py` → `platforms/gnome/__init__.py`; `gsettings_store.py gnome_theme.py gtk_icons.py logind.py gnome_migrate.py` → `platforms/gnome/`; `platforms/batocera.py` → `platforms/batocera/__init__.py`; `batocera_games.py` → `platforms/batocera/games.py`; `batocera_theme.py` → `platforms/batocera/theme.py`; `glib_runtime.py` → `platforms/linux/glib_runtime.py`; `backends/openrgb.py` → `platforms/linux/openrgb.py`; `platforms/json_store.py` → `core/json_store.py`
- Delete: `service/rgbhub/backends/`, `platforms/linux`-duplicated `backends/hidraw.py`
- Modify: `install/install-user.sh` (`rgbhub.backends.openrgb` → `rgbhub.platforms.linux.openrgb`), `install/openrgb-detector-scan.sh` if it references the module, `tests/*`, `docs/design.md` layer diagram + table, `README.md` paths.

**Interfaces:**
- Produces: `class GLibRuntime` in `platforms/linux/glib_runtime.py` fitting `Runtime` (`every`, `cancel`, `watch_file`, `call_soon` = `GLib.idle_add` returning False, `run_until_stopped` with SIGINT/SIGTERM, `stop` = `loop.quit`); keep `on_signal(signum, cb)` as a module function.
- `__main__.PLATFORMS = ("gnome", "batocera", "windows")`; `importlib.import_module(f"rgbhub.platforms.{name}").main(argv)` unchanged in form.

- [ ] **Step 1: `git mv`** everything, fix imports; `grep -rn "backends" service install systemd frontends docs/design.md README.md` returns only prose that is then reworded ("platform code").
- [ ] **Step 2: Add** `test_contracts.py::test_glib_runtime_contract` = `check_runtime(GLibRuntime)`. `check_runtime` asserts: two `every` timers fire in interval order; `cancel` stops one; `call_soon` from another thread runs on the loop thread (compare `threading.get_ident()`); `watch_file` fires within 3 s of an `os.replace` onto the path; `stop()` makes `run_until_stopped()` return.
- [ ] **Step 3: Run** full suite + `../.venv/bin/ruff check .` → pass/clean.
- [ ] **Step 4: Commit** `Platforms: one folder per system, OS code under platforms/linux; no more backends`.

### Task 6: One device list; devices build themselves and describe themselves

**Files:**
- Create: `service/rgbhub/core/platform.py`, `service/tests/test_devices.py`
- Modify: `devices/__init__.py`, `devices/omen_pc/__init__.py`, `devices/iqunix_keyboard/__init__.py`, `devices/*/device.py` (status), `core/hub.py` (`publish_status`), `platforms/gnome/__init__.py`, `platforms/batocera/__init__.py`, `__main__.py`, `frontends/gnome-shell/rgbhub@konfus-dev.github.io/prefs.js`

**Interfaces:**
- Produces:
  - `core/platform.py`:
    ```python
    @dataclass
    class Platform:
        name: str                      # "gnome" | "batocera" | "windows"
        hid_bus: HidBus
        runtime: Runtime
        ram_bus: RamBus | None = None
        case_lights: Callable[[], object] | None = None   # lighting client opener; None = case_board over hid_bus
        lcd_fade: bool = True
    ```
  - Each device package: `DEVICE_ID: str`; `build(platform: Platform) -> Device | None`.
  - `devices/__init__.py`: `DEVICES = (omen_pc, iqunix_keyboard)`; `build_all(platform) -> list[Device]`.
  - Device `status()` adds `"label"`, `"icon"` (freedesktop symbolic: `computer-symbolic`, `input-keyboard-symbolic`), `"has-lcd"` (bool). `Hub.publish_status` already adds `id`/`label`.
  - `__main__.py --self-check [--platform X]`: imports `rgbhub.platforms.X`, calls `build_all(Platform(X, FakeHidBus({}), ManualRuntime(), FakeRamBus([])))`, prints `ok <n> devices`, exit 0; any exception → exit 1. (`ManualRuntime` = `ManualScheduler` + no-op watch/call_soon, in `core/interfaces.py`.)
- `settings_schema.DEVICE_IDS` stays (core must not import devices); `test_devices.py::test_device_ids_match_list` asserts `DEVICE_IDS == tuple(m.DEVICE_ID for m in DEVICES)`.
- prefs.js: tabs come from `status.devices` (`id`, `label`, `icon`, `has-lcd`); its constant `DEVICES` stays only as the fallback when status has no devices.

- [ ] **Step 1: Write failing tests** `test_devices.py`: `test_every_device_builds_on_fake_platform` (each `m.build(...)` returns an object with every `Device` method), `test_status_describes_device` (`label`, `icon`, `has-lcd` present; OMEN `has-lcd` True, keyboard False), `test_device_ids_match_list`, `test_self_check_exits_zero` (`subprocess.run([sys.executable, "-m", "rgbhub", "--self-check", "--platform", "gnome"])` returncode 0).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; gnome/batocera `main()` build a `Platform` and call `build_all` instead of naming devices (GNOME: `case_lights=openrgb.connect`, `ram_bus=HelperRamBus()`; Batocera: `ram_bus=AcpiRamBus(...)` or None, `case_lights=None`).
- [ ] **Step 4: Run** full suite → pass. Check prefs.js syntax with `gjs -c 'imports'` not needed; instead `node --check` is unavailable for ESM GJS — run `gnome-extensions prefs` manually in Task 17.
- [ ] **Step 5: Commit** `Devices: one list, each device builds itself from the platform and describes itself`.

### Task 7: Plain-Python Runtime

**Files:**
- Create: `service/rgbhub/core/thread_runtime.py`
- Modify: `service/tests/test_contracts.py`

**Interfaces:**
- Produces: `class ThreadRuntime(poll_s: float = 1.0)` fitting `Runtime`, built on the standard library's `asyncio` event loop (no home-made loop): `every` = a `loop.call_later` chain, `cancel` = `TimerHandle.cancel`, `call_soon` = `loop.call_soon_threadsafe`, `run_until_stopped` = `loop.run_forever` (SIGINT/SIGTERM via `signal.signal` where supported), `stop` = `loop.call_soon_threadsafe(loop.stop)`; `watch_file` polls `os.stat` (mtime_ns, size, inode) every `poll_s` and calls back on change or on creation; callback exceptions are logged and the timer is kept (same as GLib: a raising timer must not kill the loop — log and continue, treat as `True`).

- [ ] **Step 1: Add** `test_thread_runtime_contract` = `check_runtime(lambda: ThreadRuntime(poll_s=0.05))` plus `test_thread_runtime_survives_raising_timer`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `Runtime: a stdlib event loop for systems without GLib`.

### Task 8: Windows HidBus (hidapi)

**Files:**
- Create: `service/rgbhub/platforms/windows/__init__.py` (placeholder `main` raising `SystemExit("Windows platform not finished")` until Task 12), `service/rgbhub/platforms/windows/hid.py`, `service/tests/fake_hid_module.py`
- Modify: `service/pyproject.toml` (`[project.optional-dependencies] windows = ["hidapi==0.14.0.post4", "pystray==0.19.5", "pillow>=11"]`; move `openrgb-python` to `gnome = [...]` and keep it in `dependencies` only if the GNOME install still installs extras — install-user.sh must install `.[gnome]`)

**Interfaces:**
- Consumes: `HidBus`, `HidPort`, `HidMatch`.
- Produces: `class HidapiBus(hid_module=None)` (imports `hid` lazily) — `find` walks `hid.enumerate(vendor, product)` comparing `usage_page`/`usage`; returns the entry's `path` decoded as latin-1 str; `open(path, nonblocking)` → `HidapiPort` (`hid.device().open_path(path.encode("latin-1"))`, `set_nonblocking`); `write` checks returned length `== len(report)` else `OSError`; `read(timeout_s)` → `device.read(64, int(timeout_s*1000))` → bytes or None; `alive()` = path still present in `hid.enumerate(vendor, product)` (cached ≤ 1 s).
- Windows report lengths: hidapi on Windows needs the full output-report length including the id byte (LCD long 1024, short 64; VIA 33; board 58+1 if the board has no report ids — `HidapiPort.write` passes bytes unchanged; Task 17 verifies on hardware).

- [ ] **Step 1: Write** `tests/fake_hid_module.py` (enumerate/device mimicking hidapi) and `test_contracts.py::test_hidapi_bus_contract` + `test_hidapi_reconnects_after_replug` (unplug → `alive()` False → `find` returns the new path after replug).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `Windows: HidBus over hidapi`.

### Task 9: RAM on Windows (helper over a named pipe, WMI to the BIOS)

**Files:**
- Modify: `helper/rgbhub_ram_helper.py` (split checks from transport)
- Create: `helper/rgbhub_ram_helper_windows.py`, `service/rgbhub/platforms/windows/ram.py`, `service/tests/test_ram_helper_windows.py`
- Modify: `service/tests/test_no_direct_ram_bus.py` (scan the new files too), `service/tests/test_ram_helper.py`

**Interfaces:**
- Produces:
  - `rgbhub_ram_helper.handle_line(line: str, present: Callable[[int], bool], deliver: Callable[[int, int, int], bool] = acpi_deliver) -> str` — `"OK"` / `"ERR …"`; existing checks unchanged; `acpi_deliver` is today's acpi_call path.
  - `rgbhub_ram_helper_windows.py` (stdlib + ctypes only, imports the shared module from its own folder):
    - `wmi_deliver(address, register, value) -> bool` via one long-lived `powershell.exe -NoProfile -NonInteractive -Command -` child that loops reading lines `W aa rr vv` and runs: `$in = ([wmiclass]'root\wmi:hpqBDataIn').CreateInstance(); $in.Sign=[byte[]][char[]]'SECU'; $in.Command=0x20009; $in.CommandType=0x0A; $in.Size=3; $in.hpqBData=[byte[]](r,v,a); (Get-WmiObject -Namespace root\wmi -Class hpqBIntM).hpqBIOSInt128($in).OutData.rwReturnCode` → prints `0` for OK. (If the class name or field names differ on this BIOS, Task 17 corrects them from `Get-CimClass -Namespace root/wmi hpqB*`.)
    - `sticks_from_cim(rows: list[dict]) -> list[int]`: slot from `DeviceLocator`/`BankLabel` trailing digit (`DIMM1..4` → slot 0..3; `ChannelA-DIMM1` style → map channel letter A,B and DIMM index per board table `{"A1":0,"A2":1,"B1":2,"B2":3}`); returns `0xC0 + 2*slot`, sorted; `[]` if any row is unparseable.
    - Pipe server: the standard library's `multiprocessing.connection.Listener(r"\\.\pipe\rgbhub-ram", authkey=key)` (no home-made pipe/ACL code). `key` = 32 random bytes in `%LOCALAPPDATA%\rgbhub\ram-key` (created by setup, readable by the owner only; the helper runs as the owner with highest privileges, so it can read it). Messages are the same text lines (`conn.send_bytes`/`recv_bytes`), plus `S` → `OK c2 c6` (sticks). One client at a time.
    - Refuses to start inside a VM (`Win32_ComputerSystem.Manufacturer` contains `QEMU`) and exits 0.
  - `platforms/windows/ram.py`: `class PipeRamBus(path=r"\\.\pipe\rgbhub-ram", key_file=..., timeout_s=1.0)` fitting `RamBus` (`multiprocessing.connection.Client(path, authkey=key)`; `sticks()` sends `S`, caches 60 s; `OSError` if the pipe is absent).
  - Fallback stick list: if `sticks_from_cim` gives `[]`, helper reads `C:\Program Files\RGB-Hub\ram-sticks.txt` (written by install-windows.sh from Ubuntu's `find_sticks()`; today `c2 c6`).

- [ ] **Step 1: Write failing tests**: `test_handle_line_uses_given_deliver` (fake deliver records `(0xC2, 0x20, 50)`; refused register never reaches deliver), `test_sticks_from_cim_dimm_numbers` (`[{"DeviceLocator":"DIMM2"},{"DeviceLocator":"DIMM4"}]` → `[0xC2, 0xC6]`), `test_sticks_from_cim_channel_style` (`"Controller0-ChannelA-DIMM2"`, `"Controller1-ChannelB-DIMM2"` → `[0xC2, 0xC6]`), `test_sticks_from_cim_unknown_is_empty`, `test_pipe_ram_bus_line_protocol` (PipeRamBus against a real `Listener` on a Unix socket address in tmp — same API, so it runs on Linux), and extend `test_no_direct_ram_bus.py` so `helper/rgbhub_ram_helper_windows.py` and `platforms/windows/*.py` are scanned (no `smbus`, `0x61`/`0x63` addressing, `i2c`, `WinRing0`, `inpout`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (ctypes pipe code guarded by `sys.platform == "win32"` so the module imports on Linux for tests). **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `RAM on Windows: the same checked helper, over a private pipe, through HP's BIOS WMI`.

### Task 10: Web settings page

**Files:**
- Create: `service/rgbhub/core/web.py` (OS-neutral; any platform may serve it), `frontends/web/index.html`, `frontends/web/app.js`, `frontends/web/style.css`, `service/tests/test_web.py`

**Interfaces:**
- Consumes: `Settings`, `Runtime.call_soon`, `settings_schema.DEFAULTS/valid`.
- Produces: `class SettingsServer(settings: Settings, runtime: Runtime, page_dir: Path, pictures_dir: Path, on_quit: Callable[[], None] | None = None, on_restart: Callable[[], None] | None = None)` with `start() -> str` (returns URL `http://127.0.0.1:<port>/?t=<token>`, port 0 = random, `ThreadingHTTPServer` on a daemon thread), `stop()`, `token`, `port`.
  - Every request needs the token (`?t=` for `GET /`, header `X-RGBHub-Token` for `/api/*`), else 403. Bind `127.0.0.1` only. Reject requests whose `Host` is not `127.0.0.1:<port>` (DNS-rebinding guard) with 403.
  - `GET /api/state` → `{"settings": {k: v for k in DEFAULTS if k not in ("status",)}, "status": json.loads(status)}`.
  - `POST /api/set {"key", "value"}` → 400 if key unknown, `status`/`focused-app`, or `not valid(key, value)`; else `runtime.call_soon(lambda: settings.set(key, value))`, 204.
  - `POST /api/picture` (body = file bytes ≤ 20 MB, header `X-Filename`) → checks Pillow can open it, saves as `<pictures_dir>/<sha1[:12]><ext>`, returns `{"path": ...}`.
  - `POST /api/quit`, `POST /api/restart` → call the callbacks (used by tray and updater).
  - Static files only from `page_dir` (no `..`).
- Page: tabs Auto + one per `status.devices` entry; rows/wording follow `prefs.js` (Auto mode master + per device, "Lights follow the app", "LCD shows the app", "Now following"; Lights on, Brightness, per lighting device Effect/Speed/Brightest/Dimmest/All + zone colours; LCD Screen on, Image (file picker → `/api/picture` → set `lcd-image`, push onto `lcd-recent-images` max 8), Recent, Fit, Brightness; Save as Default / Load Default via `default-action`). Polls `/api/state` every 2 s. `prefers-color-scheme` light/dark.

- [ ] **Step 1: Write failing tests** `test_web.py` (server on a `JsonSettings` in tmp, `ManualRuntime` that runs `call_soon` immediately): `test_state_needs_token` (403), `test_wrong_host_refused`, `test_set_valid_value_saved`, `test_set_unknown_key_400`, `test_set_wrong_type_400` (`lcd-brightness: "80"`), `test_set_status_400`, `test_picture_upload_saved_and_path_returned`, `test_picture_not_an_image_400`, `test_static_path_traversal_404`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** server, then page. **Step 4: Run** → pass; open the page once on Ubuntu against a temp JSON store (`python -m rgbhub.core.web --settings /tmp/x.json`, a `__main__` for manual checking) and click through every tab.
- [ ] **Step 5: Commit** `Settings page: a local web page any platform can serve`.

### Task 11: Windows focus, icons and wallpaper

**Files:**
- Create: `service/rgbhub/platforms/windows/focus.py`, `icons.py`, `wallpaper.py`, `service/tests/test_windows_focus.py`

**Interfaces:**
- Produces:
  - `focus.app_id_for(exe_path: str, package_family: str | None) -> str`: package family name for Store apps, else lower-cased exe path. `focus.FocusWatcher(settings, runtime, own_pid: int, read=foreground_app)`: every 500 ms on the runtime, `settings.set("focused-app", app_id)`; RGB-Hub's own windows (own pid or the Edge `--app` window, recognised by title prefix `RGB-Hub`) → leave the previous value. `foreground_app() -> tuple[str, str | None] | None` (ctypes: `GetForegroundWindow` → `GetWindowThreadProcessId`; for `ApplicationFrameHost.exe` use the child window whose pid differs (`EnumChildWindows`); `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` → `QueryFullProcessImageNameW`, `GetPackageFamilyName`). Any failure → `None` → `""`.
  - `icons.load_app_icon(app_id: str) -> Image.Image | None`: package → largest `Square150x150Logo`/`Square44x44Logo` PNG (scale/targetsize variants) from the package's `AppxManifest.xml` (install location via `PackageManager` is not available in stdlib → use the exe's folder for UWP processes, read in `focus`, and pass the folder: app id for packages is `"<family>|<install dir>"`); exe → the `icoextract` library (pure Python, reads the exe's icon group; add `icoextract` to the `windows` extra) → largest frame opened with Pillow (no home-made Win32 icon code).
  - `wallpaper.current_wallpaper() -> Path | None` (`SystemParametersInfoW(SPI_GETDESKWALLPAPER=0x0073)`, else `%APPDATA%\Microsoft\Windows\Themes\TranscodedWallpaper`); `wallpaper.windows_logo() -> Image.Image` (drawn with Pillow: four `#0078D4` squares on black, 480×480).

- [ ] **Step 1: Write failing tests** (Linux-runnable parts only): `test_app_id_exe_lowercased`, `test_app_id_package_wins`, `test_focus_watcher_writes_app` (fake `read`), `test_focus_watcher_unreadable_window_is_idle` (`read` returns None → `""`), `test_focus_watcher_read_raises_is_idle`, `test_focus_watcher_ignores_own_window`, `test_windows_logo_size`, `test_manifest_logo_picks_largest_scale` (tmp folder with `AppxManifest.xml` + `Assets/Logo.scale-100.png`, `.scale-200.png`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (ctypes only under `sys.platform == "win32"`). **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `Windows: focused app, its icon and the wallpaper for auto mode`.

### Task 12: Windows platform main, session events, tray, launcher

**Files:**
- Create: `platforms/windows/session.py`, `tray.py`, `launcher.py`; fill `platforms/windows/__init__.py`
- Modify: `service/rgbhub/__main__.py` (`detect()` → `"windows"` when `sys.platform == "win32"`)
- Test: `service/tests/test_windows_platform.py`

**Interfaces:**
- Produces:
  - `windows.main(argv)`: settings `%APPDATA%\rgbhub\settings.json` (`JsonSettings`), `Platform("windows", HidapiBus(), ThreadRuntime(), PipeRamBus())` (RamBus None-equivalent when the pipe is absent: `RamLights` already reports unavailable on `OSError`), `Looks(icons.load_app_icon, ignore=…, load_idle=lcd folder `%APPDATA%\rgbhub\lcd` → wallpaper → logo, compose_idle=cover_lcd)`, `SettingsServer(...)`, `FocusWatcher`, `SessionWatcher`, tray; writes `%LOCALAPPDATA%\rgbhub\runtime.json` `{"pid", "port", "token", "started"}` (owner-only file); exits `0` on Quit, `75` on restart request.
  - VM guard: `in_virtual_machine() -> bool` (CIM `Win32_ComputerSystem` manufacturer/model contains `QEMU` or `Virtual`), main exits 0 at once when True.
  - `session.SessionWatcher(on_sleep, on_resume, on_end)`: hidden top-level window on its own thread handling `WM_POWERBROADCAST` (`PBT_APMSUSPEND=4` → on_sleep, `PBT_APMRESUMEAUTOMATIC=0x12` → on_resume), `WM_QUERYENDSESSION` (return TRUE), `WM_ENDSESSION` (wParam TRUE → call `on_end` and block until it returns, ≤ 15 s). Callbacks marshalled with `runtime.call_soon` + `threading.Event`.
  - `tray.Tray(open_settings, set_auto_all, auto_all, quit, approve_update=None)` with pystray; menu: "Settings", "Auto mode" (checkbox), "Approve update…" (only visible when `approve_update` is set), "Quit". Icon drawn with Pillow (no asset file).
  - `launcher.py` (stdlib): `python launcher.py` reads `<root>\current.txt` (a version folder name), puts `<root>\versions\<v>\service` and `<root>\versions\<v>\site-packages` on `sys.path`, runs `rgbhub.__main__.main(["--platform","windows"])` in a child `pythonw.exe` process; restarts the child on exit code 75 or a crash (pause 5 s doubling to 300 s, reset after 120 s of uptime), stops on exit code 0.
  - Edge app window: `msedge.exe --app=<url> --window-size=900,760` via `shutil.which` or `%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe`; else `webbrowser.open`.

- [ ] **Step 1: Write failing tests**: `test_detect_windows` (monkeypatch `sys.platform`), `test_launcher_reads_current_and_restarts_on_75` (fake spawn returning 75 then 0 → two runs, second uses new `current.txt`), `test_launcher_backoff_on_crash`, `test_self_check_windows` (`--self-check --platform windows` exits 0 on Linux: Windows modules must import without ctypes Win32 calls at import time).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `Windows: platform main, tray, sleep and sign-out handling, launcher`.

### Task 13: Updater core

**Files:**
- Create: `service/rgbhub/updater/{__init__,__main__,github,store,flow}.py`, `service/rgbhub/updater/layouts/__init__.py`, `service/tests/test_updater.py`

**Interfaces:**
- Produces:
  - `github.GitHub(repo="Konfus-dev/RGB-Hub", opener=urllib.request.urlopen)`: `latest(branch="main") -> str` (`GET https://api.github.com/repos/{repo}/commits/{branch}`, `sha`), `is_ahead(base, head) -> bool` (`GET …/compare/{base}...{head}`, `status == "ahead"`; 404 → False), `download(sha, dest: Path)` (`https://codeload.github.com/{repo}/zip/{sha}` to `dest.with_suffix(".part")`, then rename). All raise `UpdateError` on HTTP/network errors. Header `User-Agent: rgbhub-updater`, timeout 30 s.
  - `store.Store(root: Path)`: `state` file `root/update-state.json` `{"current", "previous", "bad": [], "checked"}`; `version_dir(sha)` = `root/versions/<sha>`; `unpack(zip, sha) -> Path` (extract to `versions/.<sha>.partial`, strip the zip's top folder, rename to final — never leaves a half folder under the final name); `clean_partials()`; `prune(keep=3)` (never deletes current/previous); `mark_bad(sha)`.
  - `layouts.Layout(Protocol)`: `prepare(version: Path) -> None`, `self_check(version: Path) -> bool`, `privileged_changes(old: Path | None, new: Path) -> list[str]`, `request_privileged(new: Path, changes: list[str]) -> None`, `activate(version: Path) -> None`, `restart_hub() -> None`, `hub_healthy(wait_s: int) -> bool`, `busy() -> bool`, `notify(text: str) -> None`, `current_commit() -> str | None`.
  - `flow.update_once(gh, store, layout, health_wait_s=60) -> str` returning one of `"up-to-date" | "not-ahead" | "busy" | "bad-commit" | "updated" | "rolled-back" | "failed"`. Order: `clean_partials` → `latest` → equal to current → `up-to-date`; in `bad` → `bad-commit`; `not is_ahead` → `not-ahead`; `busy()` → `busy`; download → unpack → `prepare` → `self_check` (False → mark bad, `failed`) → `privileged_changes` (non-empty → `request_privileged`, continue) → `activate` → `restart_hub` → `hub_healthy` (False → activate previous, restart, mark bad, notify, `rolled-back`) → update state, `prune`, notify "RGB-Hub updated to <short sha>", `updated`. Any `UpdateError` → log one line, `failed`, state untouched.
  - `python -m rgbhub.updater --platform {gnome,batocera,windows} [--once | --loop] [--root PATH]`: `--loop` runs at start, then every 3600 s ± 300 s jitter; never raises out of the loop.

- [ ] **Step 1: Write failing tests** with a fake GitHub and fake layout: `test_up_to_date`, `test_never_goes_backwards` (`is_ahead` False → nothing downloaded), `test_github_unreachable_keeps_current` (Review Focus 1: opener raises `URLError` / HTTP 403 / 404 → `failed`/`not-ahead`, `activate` never called), `test_interrupted_unpack_never_activated` (Review Focus 2: leave `versions/.abc.partial`; next run removes it), `test_self_check_failure_marks_bad`, `test_unhealthy_rolls_back` (activate called with new then previous; sha in `bad`), `test_bad_commit_skipped_until_main_moves`, `test_busy_waits`, `test_privileged_changes_requested_but_rest_applied`, `test_prune_keeps_current_and_previous`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `Updater: follow main safely, one version folder each, roll back on failure`.

### Task 14: Update layouts and release installs for Ubuntu and Batocera

**Files:**
- Create: `service/rgbhub/updater/layouts/gnome.py`, `layouts/batocera.py`, `systemd/rgbhub-update.service`, `systemd/rgbhub-update.timer`, `service/tests/test_layouts.py`
- Modify: `systemd/rgbhub.service`, `install/install-user.sh`, `install/install-batocera.sh`, `frontends/batocera/services/rgb_hub`, `README.md`

**Interfaces:**
- GNOME layout (root `~/.local/share/rgbhub`):
  - `versions/<sha>/` = repo snapshot; `prepare` = `python3 -m venv --system-site-packages <v>/.venv && <v>/.venv/bin/pip install --quiet "<v>/service[gnome]"`; `self_check` = `<v>/.venv/bin/python -m rgbhub --self-check --platform gnome`; `activate` = atomic symlink swap `current` → `versions/<sha>` (`os.symlink` to `current.new` then `os.replace`) and re-point the extension link; `restart_hub` = `systemctl --user restart rgbhub.service`; `hub_healthy` = `ActiveState=active` and `NRestarts` unchanged after `wait_s`; `busy` = False; `notify` = `notify-send RGB-Hub <text>`; `privileged_changes` compares `helper/rgbhub_ram_helper.py`, `install/70-rgb-hub.rules`, `install/71-rgbhub-ram-bus-lock.rules`, `systemd/rgbhub-ram.*` against the installed copies under `/usr/local/lib/rgbhub/` and `/etc/udev/rules.d/`, `/etc/systemd/system/`; `request_privileged` = background `notify-send --wait --action=approve=Approve "RGB-Hub update needs your password" "<list>"` and on `approve` run `pkexec /usr/bin/bash <new>/install/setup.sh --no-apt`; `current_commit` = name of the `current` target; a `dev` file in the root → updater does nothing (paused).
  - `rgbhub.service`: `ExecStart=%h/.local/share/rgbhub/current/.venv/bin/python -m rgbhub`; `ExecStartPre` uses `%h/.local/share/rgbhub/current/frontends/gnome-shell/rgbhub@konfus-dev.github.io/schemas`.
  - `rgbhub-update.service` (oneshot, `ExecStart=/usr/bin/python3 -I %h/.local/share/rgbhub/updater/run.py --platform gnome --once`) + `rgbhub-update.timer` (`OnBootSec=2min`, `OnUnitActiveSec=1h`, `RandomizedDelaySec=5min`, `Persistent=true`). The updater runs from a stable copy `~/.local/share/rgbhub/updater/` (copied from the active version at each `activate`) so updating cannot replace the running code mid-run; `run.py` puts that folder on `sys.path` and calls `rgbhub.updater.__main__.main`.
  - `install-user.sh [--release (default) | --dev] [--allow-detector-scan]`: `--release` = `git -C "$repo" archive --format=zip HEAD` of the working copy's `main` as the first version (`git rev-parse main`), `prepare`, `activate`, enable the timer, remove `dev` marker; `--dev` = `current` → the working copy, `.venv` in the repo with `pip install -e "service[gnome]"`, create the `dev` marker. Everything else in the script (OpenRGB detector lock, units, extension enable) unchanged; the extension link points at `~/.local/share/rgbhub/current/frontends/gnome-shell/<uuid>`.
- Batocera layout (root `/userdata/system/rgb-hub`):
  - `versions/<sha>/` holds `service/`, `helper/`, `modules/`, `frontends/batocera/`; `prepare` = nothing; `self_check` = `python3 -m rgbhub --self-check --platform batocera` with `PYTHONPATH=<v>/service`; `activate` = write `current.txt` (tmp + `os.replace`); `restart_hub` = `kill -TERM $(cat /var/run/rgb-hub.pid)` (keeper restarts it); `hub_healthy` = pid alive and unchanged after `wait_s`; `busy` = `/var/run/rgb-hub/now-playing` non-empty; `notify` = log only; `privileged_changes` = `[]` (all root already); also re-install `services/rgb_hub`, game hook and power hooks from the new version when they differ.
  - `rgb_hub` service script: `app` version from `current.txt` (fallback: legacy `$app/service` if no `current.txt`); keeper restarts on any exit unless `/var/run/rgb-hub.stopping` exists (created by `stop`); `start` also starts `python3 -I $app/updater/run.py --platform batocera --loop` in the background (pid in `/var/run/rgb-hub.updater.pid`, killed by `stop`).
  - `install-batocera.sh`: writes the release layout (version folder named after `git rev-parse HEAD`, `current.txt`, stable updater copy) instead of `$app/service`; old flat layout removed.

- [ ] **Step 1: Write failing tests** `test_layouts.py` (tmp roots, fake `run` for subprocess calls): `test_gnome_activate_swaps_symlink_atomically`, `test_gnome_dev_marker_pauses`, `test_gnome_privileged_changes_detects_helper_diff`, `test_batocera_activate_writes_current_txt`, `test_batocera_busy_when_game_running`, `test_batocera_service_script_reads_current` (run the script's path logic with `bash -n` + a grep-free sourced function `app_dir`), `test_stable_updater_copy_refreshed_on_activate`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** layouts, units, scripts. **Step 4: Run** → pass; `bash -n` on every changed script.
- [ ] **Step 5: Commit** `Updater: release installs that follow main on Ubuntu and Batocera`.

### Task 15: Windows install (from Ubuntu) and Windows update layout

**Files:**
- Create: `install/install-windows.sh`, `install/windows_settings.py`, `install/windows/finish-setup.ps1`, `install/windows/uninstall.ps1`, `install/windows/RGB-Hub-setup.cmd`, `install/windows/approve-privileged.ps1`, `service/rgbhub/updater/layouts/windows.py`, `service/tests/test_windows_install.py`

**Interfaces:**
- `install-windows.sh [--windows-mount /mnt/windows] [--user jercl]` (run with `pkexec`, absolute paths; `PKEXEC_UID` is the owner):
  1. Refuse if `/dev/mapper/winbridge` exists; if `hiberfil.sys` is in use (ntfs3 mount fails rw / `ntfsinfo`-free check: rw remount refusal is the signal); if the SYSTEM hive's two sequence numbers (offsets 4 and 8) differ. Same messages style as bt-share.
  2. `mount -o remount,rw`; `trap` restores `ro` on exit.
  3. Build in a temp dir (as the owner, not root): download `python-3.13.<pinned>-embed-amd64.zip` from python.org, verify pinned SHA-256 (look up and pin in the script when writing it), `pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp -d wheels ".[windows]" pip`, unpack wheels into `site-packages` with Python `zipfile`, edit `python313._pth` to add `import site`.
  4. Copy: `C:\Program Files\RGB-Hub\` ← `python\`, `helper\` (both helper files), `ram-sticks.txt` (`c2 c6` from Ubuntu `find_sticks()`), `install\windows\*.ps1`; `C:\Users\<user>\AppData\Local\Programs\RGB-Hub\` ← `python\` (same embeddable), `versions\<sha>\` (repo snapshot + `site-packages`), `current.txt`, `launcher.py`, `updater\` stable copy + `run.py`, `pip.whl`.
  5. `windows_settings.py` seeds `C:\Users\<user>\AppData\Roaming\rgbhub\settings.json` only if absent: reads every key in `DEFAULTS` from Ubuntu GSettings (`gsettings get org.gnome.shell.extensions.rgbhub <key>` with the extension's schema dir, run as the owner), keeps non-default ones, copies `lcd-image`, `lcd-recent-images`, `auto-idle-image` and the `default-look`'s `lcd-image` files into `AppData\Roaming\rgbhub\pictures\` and rewrites them as `C:\Users\<user>\AppData\Roaming\rgbhub\pictures\<name>`; copies `~/.config/rgbhub/lcd/` to `AppData\Roaming\rgbhub\lcd\` if absent.
  6. Drop `RGB-Hub-setup.cmd` into the owner's Startup folder.
- `RGB-Hub-setup.cmd`: exits at once inside a VM (`wmic`-free: PowerShell `(Get-CimInstance Win32_ComputerSystem).Manufacturer -match 'QEMU'`); else `powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File \"C:\Program Files\RGB-Hub\install\windows\finish-setup.ps1\" -User <user>'"`.
- `finish-setup.ps1 -User <user> [-HelperOnly]` (elevated): `icacls "C:\Program Files\RGB-Hub" /reset /T /Q`; `icacls "%LOCALAPPDATA%\Programs\RGB-Hub" /reset /T /Q` then owner full control; scheduled tasks via `Register-ScheduledTask`: `RGB-Hub` (at logon of the owner, `pythonw.exe launcher.py`, user, limited), `RGB-Hub update` (at logon + every 1 h, `pythonw.exe -I updater\run.py --platform windows --once`, user, limited), `RGB-Hub RAM` (at logon, `C:\Program Files\RGB-Hub\python\pythonw.exe -I helper\rgbhub_ram_helper_windows.py --owner-sid <sid>`, highest privileges); OMEN Gaming Hub: `Get-AppxPackage -AllUsers AD2F1837.OMENCommandCenter | Remove-AppxPackage -AllUsers`, `Get-AppxProvisionedPackage -Online | ? DisplayName -eq AD2F1837.OMENCommandCenter | Remove-AppxProvisionedPackage -Online`; for `HPOmenCap`, `HPOmenCustomCapDriver`, `HpTouchpointAnalyticsService`, `hp-one-agent-service`: record `StartType` into `C:\Program Files\RGB-Hub\disabled-services.json`, `Stop-Service -Force`, `Set-Service -StartupType Disabled`; start the three tasks; delete the Startup `.cmd`; log to `%LOCALAPPDATA%\rgbhub\setup.log`. `-HelperOnly` (used for privileged updates) does only the Program Files copy from a given version folder + task refresh.
- `uninstall.ps1`: unregister tasks, restore service start types from the JSON, remove both folders (settings kept unless `-RemoveSettings`), message that OMEN Gaming Hub can be reinstalled from the Microsoft Store.
- Windows layout (root `%LOCALAPPDATA%\Programs\RGB-Hub`): `prepare` = copy `site-packages` from current when `[project.optional-dependencies].windows` (read with `tomllib`) is unchanged, else `python\python.exe pip.whl\pip install --only-binary=:all: --target <v>\site-packages ...`; `self_check` = `python.exe -I launcher.py --self-check <v>`; `activate` = `current.txt`; `restart_hub` = POST `/api/restart` using `runtime.json` (fallback: `schtasks /End` + `/Run /TN "RGB-Hub"`); `hub_healthy` = `runtime.json` `started` changed once and `pid` still alive after `wait_s`; `busy` False; `notify` = through the hub (`POST /api/notify` → tray balloon) else log; `privileged_changes` compares `helper/*.py` and `install/windows/*.ps1` with `C:\Program Files\RGB-Hub\…`; `request_privileged` = tray "Approve update…" item (via `/api/approve-pending` writing `pending-privileged.json`), which runs `approve-privileged.ps1` elevated → `finish-setup.ps1 -HelperOnly -From <v>`.

- [ ] **Step 1: Write failing tests** `test_windows_install.py`: `test_settings_conversion_keeps_only_non_defaults`, `test_settings_conversion_rewrites_and_copies_pictures`, `test_settings_never_overwritten`, `test_windows_layout_reuses_site_packages_when_deps_unchanged`, `test_windows_layout_privileged_changes`, `bash -n install/install-windows.sh`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass; `pwsh -NoProfile -Command "[ScriptBlock]::Create((Get-Content -Raw <file>))"` parse check if `pwsh` is installed, else skip and note it.
- [ ] **Step 5: Commit** `Windows: install from Ubuntu, finish with one UAC prompt, update itself`.

### Task 16: Docs

**Files:** `docs/design.md`, `README.md`, `frontends/batocera/README.md`, new `frontends/windows/README.md` (what the owner sees on Windows, how to uninstall)

- [ ] **Step 1:** Update the layer diagram and system table (add Windows column: JSON store, hidapi, case board driver, WMI helper over pipe, foreground app + exe/Store icons, wallpaper, WM_POWERBROADCAST, ThreadRuntime), a "Platforms and interfaces" section ("to add a system: implement these five interfaces in platforms/<name>/ and a main()"), "Adding a device" (one folder + one line, EmuBox exception), "Updates" (section 9 of the spec in plain words, `--dev`/`--release`).
- [ ] **Step 2:** `grep -rn "backends" docs README.md frontends` → none.
- [ ] **Step 3: Commit** `Docs: platforms, device list, Windows and updates`.

### Task 17: Install and check on the real machines (owner in the loop)

No code unless a check fails (then fix with a test first, as in earlier tasks).

- [ ] **Step 1: Ubuntu.** `bash install/install-user.sh --release`; `systemctl --user status rgbhub rgbhub-update.timer`; `python3 -I ~/.local/share/rgbhub/updater/run.py --platform gnome --once` → `not-ahead` (GitHub main is the old code) and nothing changes; ask the owner to check LCD/lights/auto still work and to re-login once for the extension link.
- [ ] **Step 2: Batocera (internal SHARE).** Mount SHARE and run `pkexec /usr/bin/bash /home/konfus/Source/RGB-Hub/install/install-batocera.sh <mount>` in one command (it tends to unmount); check `current.txt`, `versions/<sha>`. Owner boots Batocera later; read `/userdata/system/logs/rgb-hub.log` afterwards.
- [ ] **Step 3: Windows install.** `pkexec /usr/bin/bash /home/konfus/Source/RGB-Hub/install/install-windows.sh --user jercl`; check files on `/mnt/windows`; remount is back to `ro`.
- [ ] **Step 4: Owner boots Windows.** Expected: one UAC prompt; OMEN Gaming Hub gone; tray icon; LCD shows the Ubuntu Default/auto look; case lights, RAM (if `hpqBIntM` call works: else correct class/field names from `Get-CimClass -Namespace root/wmi hpqB*` and retry), keyboard; auto mode across Firefox, a Store app, the desktop; sleep/wake; Quit → Default; reboot back to Ubuntu → Ubuntu hub still fine. Collect `%LOCALAPPDATA%\rgbhub\*.log` and `setup.log` from Ubuntu via `/mnt/windows` afterwards.
- [ ] **Step 5:** Record outcomes in the spec's "Owner's decisions"/notes and in memory; commit.
