# RGB-Hub on Windows — design

Date: 2026-11-24. Builds on `docs/design.md` and `2026-11-15-rgb-hub-design.md`.

## Intent

The owner dual-boots Ubuntu and Windows 11 on the OMEN 45L. On Windows the
lighting is run by OMEN Gaming Hub, which brings ads, tracking and other
clutter. The owner wants RGB-Hub to replace it there, with the same
behaviour they like on Ubuntu, **auto mode above all** (lights and LCD
follow the focused app's icon; the idle look follows the wallpaper).

The owner's standing rules for this project apply: reuse what exists,
keep backends and front ends cleanly separated, and **adding a new backend
must mean implementing one interface and nothing more**.

## Goals

- `python -m rgbhub` runs on Windows with the same core, devices and
  settings as on Ubuntu: OMEN-PC (cooler LCD, case lights, RAM) and
  IQUNIX-KEYBOARD.
- Auto mode on Windows: the focused app's icon drives the lights and the
  LCD; nothing focused → LCD folder, else wallpaper, else Windows logo.
- Default look shown whenever the hub stops (quit, sign-out, shutdown).
- Sleep/wake handled (lights off before sleep, everything re-sent after).
- A tray icon with a settings window offering what the GNOME window offers:
  Auto tab (master + per device, "Now following"), a tab per device (lights
  on, brightness, effect, speed, pulse range, zone colours, LCD image/fit/
  brightness/recent images), Save/Load Default.
- Hardware access formalised as interfaces in `core/interfaces.py`, each
  with one implementation per system and a conformance test, so a new
  backend is one class.
- Installed onto the owner's Windows from Ubuntu, finishing with at most
  one UAC prompt at the first Windows sign-in.
- The owner's current Ubuntu settings and Default carried over, so Windows
  looks the same from the first boot.

## Non-goals

- OpenRGB on Windows. The case board is driven by our own `tracer.py`
  (already used on Batocera), so nothing on Windows scans for hardware.
- A Windows installer (MSI/EXE) or distributing to other people.
- Reusing the web settings page on Batocera/EmuBox now (it should not
  prevent it later).
- Fan, performance or other OMEN Gaming Hub features.

## Anti-goals (a failure even if it works)

- Anything on Windows touching the RAM's SMBus (addresses 0x61/0x63). RAM
  goes through the BIOS only, as on Linux.
- Running the whole hub elevated. Only a small RAM helper may be.
- Linux behaviour changing. Ubuntu and Batocera must behave exactly as
  today; existing tests keep passing unchanged except where an interface
  is threaded through.
- Leaving Windows in a state the owner can't undo (everything disabled must
  be re-enableable; uninstall documented).
- RGB-Hub and OMEN Gaming Hub both driving the LCD (flicker, fights).

## Approaches (chosen: A)

- **A. Formal interfaces, one implementation per system (chosen).** Add
  `HidBus`, `RamBus` and `Runtime` interfaces; Linux keeps hidraw, the
  socket/acpi_call RAM path and GLib; Windows gets hidapi, a WMI RAM helper
  and a plain-Python runtime; one new `platforms/windows.py`.
- B. hidapi everywhere. One HID implementation, but a new dependency on
  Batocera and changes to tested Linux paths. Rejected.
- C. A plugin registry choosing backends by name from config. More
  machinery than two devices need. Rejected.

## Design

### 1. Interfaces (core/interfaces.py)

All new seams are `Protocol`s next to the existing `Settings` and
`Scheduler`; the core and devices import only these.

- **`HidMatch`** (frozen dataclass): `vendor: int`, `product: int`,
  `usage_page: int | None`, `usage: int | None`. Replaces the sysfs id
  string + descriptor-prefix pair used today:
  - LCD: `03f0:7397`, usage page `0xFF1A` (today's prefix `06 1a ff`).
  - VIA keyboard: `320f:5055`, usage page `0xFF60`, usage `0x61`
    (today's prefix `06 60 ff 09 61`).
  - Case board: `103c:84fd`, any interface.
- **`HidBus`**: `find(match) -> str | None` (an opaque path),
  `open(path) -> HidPort`.
- **`HidPort`**: `write(report: bytes)`, `read(timeout_s) -> bytes | None`,
  `alive() -> bool`, `close()`. Failures raise `OSError`.
  `ViaPort.drain()` becomes reads through `read()`; `update()` in
  `tracer.py`/`via.py` uses `alive()` instead of checking a `/dev` node.
- **`RamBus`**: `send(batch: list[Command])`, `sticks() -> list[int]`.
  Today's `send_to_helper`/`find_sticks` and `direct_sender` become
  implementations; `RamLights` takes one `RamBus`.
- **`Runtime`** = the existing `Scheduler` + `watch_file` +
  `call_soon(fn)` (run on the loop thread, from any thread) +
  `run_until_stopped()` + `stop()`.

Backends (`hidraw.py`, `tracer.py`, `via.py`, LCD opener) take a `HidBus`
instead of sysfs/dev roots. Device-model constants (`constants.py`,
`omen45l.py`, `iqunix_mg65.py`) hold `HidMatch` values.

Implementations:

| Interface | Linux (unchanged behaviour) | Windows (new) |
|---|---|---|
| HidBus | `backends/hidraw.py` (sysfs; usage page read from the report descriptor) | `backends/hidapi_bus.py` (the `hidapi` wheel) |
| RamBus | `backends/ram_bios.py`: socket helper (GNOME) / in-process acpi_call (Batocera) | `backends/ram_wmi.py`: client of the Windows RAM helper |
| Runtime | `platforms/glib_runtime.py` | `core/thread_runtime.py` (the standard library's `asyncio` loop; `watch_file` by polling mtime once a second) |
| Settings | GSettings / JSON | `JsonSettings` (reused as is) |

**Conformance tests**: `tests/test_contracts.py` runs one shared test
suite against every implementation of each interface that can run on the
test machine (fakes for hardware), e.g. `HidBus` find/open/write/read/
alive-after-unplug; `Runtime` timers, cancel, call_soon ordering, file
watch. Adding a backend = add it to the parametrised list.

### 2. Windows platform (platforms/windows.py)

Mirrors `gnome.py`/`batocera.py`: picks backends and builds the same `Hub`
with the same `OmenPc` and `IqunixKeyboard`.

- **Settings**: `%APPDATA%\rgbhub\settings.json` via `JsonSettings`.
- **Case lights**: `Lights(tracer.open_board(bus), omen45l.HIDDEN_ZONES, …)`.
- **LCD**: `LcdStreamer` over `HidBus`.
- **Keyboard**: `via.opener(MG65, bus)`.
- **RAM**: `RamLights(ram_wmi.Client())`; absent helper → "RAM unavailable",
  RAM left alone (as Batocera without acpi_call).
- **Focus** (`platforms/win_focus.py`): `SetWinEventHook(EVENT_SYSTEM_
  FOREGROUND)` on the tray's thread (plus a 1 s poll as a safety net, as
  Batocera does) → foreground window → process. App id = the exe path, or
  the package family for Store/UWP apps (`ApplicationFrameHost`). Written
  to `focused-app`. RGB-Hub's own windows are in `Looks(ignore=…)`.
- **Icons** (`platforms/win_icons.py`): the largest icon for the exe (the
  `icoextract` library) or the Store package's logo from its manifest, as a Pillow RGBA image → `Looks` exactly as on GNOME.
  `version` = icon-cache/theme change marker, so changed icons re-colour.
- **Idle picture**: `%APPDATA%\rgbhub\lcd\` (core `lcd_folder.py`), else
  the wallpaper (`SystemParametersInfo(SPI_GETDESKWALLPAPER)`, falling
  back to `TranscodedWallpaper`), else a Windows logo shipped with the app.
  Wallpaper change → `hub.look_again()` (watch the file / `WM_SETTINGCHANGE`).
- **Sleep**: `WM_POWERBROADCAST` (`PBT_APMSUSPEND` → `hub.on_sleep`,
  `PBT_APMRESUMEAUTOMATIC` → `hub.on_resume`) on the tray's hidden window.
  The hub's existing clock-jump check stays the backstop.
- **Stop**: tray Quit, sign-out and shutdown (`WM_QUERYENDSESSION`/
  `WM_ENDSESSION`) all call `hub.stop()` → Default look.
- `__main__.detect()` returns `"windows"` when `sys.platform == "win32"`.

### 3. RAM helper on Windows

Same rule as Linux: only a tiny helper is elevated; it reuses the request
checking in `helper/rgbhub_ram_helper.py` (`handle_line`, `ALLOWED_*`),
which is factored so the transport (acpi_call vs WMI) is a parameter.

- **BIOS call**: WMI class `hpqBIntM` in `root\wmi`, method
  `hpqBIOSInt128`, input `hpqBDataIn` with `Sign = "SECU"`,
  `Command = 0x20009`, `CommandType = 0x0A`, `Size = 3`,
  `hpqBData = [register, value, address]` — the same request the Linux
  helper sends through `\_SB.WMID.WMAA`. Reply return code 0 = OK.
  The class comes from the BIOS, not from OMEN Gaming Hub: its GUID
  `5FB7F034-2C63-45E9-BE91-3D44E2C707E4` is listed by the firmware itself
  (seen under `/sys/bus/wmi/devices` on Ubuntu, where no HP software runs),
  so removing OMEN Gaming Hub cannot take RAM lighting away.
- **Transport to the hub**: a named pipe `\\.\pipe\rgbhub-ram` through the
  standard library's `multiprocessing.connection` (Listener/Client with an
  authkey only the owner's account can read). Same line protocol
  (`W aa rr vv` / `PING` → `OK` / `ERR …`).
- **Runs** as a scheduled task at the owner's sign-in with highest
  privileges, from `C:\Program Files\RGB-Hub\` (admin-writable only, so the
  elevated code cannot be swapped by unprivileged code).
- **Stick discovery**: `Win32_PhysicalMemory` (`BankLabel`/`DeviceLocator`
  → slot), standing in for sysfs/dmidecode.
- Speed: Linux ≈ 65 ms/write. WMI must be measured; `core/ram.py` already
  batches only changed registers on a worker thread, so slow writes delay
  only the RAM.

### 4. Front end on Windows

- **Tray icon** (`frontends/windows/tray.py`, `pystray`): Open settings,
  Auto mode on/off (master), Quit. The tray owns the hidden window used for
  power/session messages and the focus hook, and runs the hub's
  `Runtime` alongside.
- **Settings window**: a local page served by the hub process on
  `127.0.0.1:<random port>`, URL carrying a random token (refused without
  it), opened in Edge app mode (`msedge --app=…`, falls back to the default
  browser). Stdlib `http.server` + one static HTML/JS page in
  `frontends/web/`. The page reads `status` + settings and writes settings
  through a tiny JSON API (`GET /api/state`, `POST /api/set {key, value}`),
  which goes through `JsonSettings` like any front end. Layout and wording
  follow the GNOME window (`prefs.js`); it is driven by `status`, so new
  devices appear without page changes.
- **LCD image picking**: the page uploads the chosen file; it is stored in
  `%APPDATA%\rgbhub\pictures\` and that path is written to `lcd-image`
  (a browser cannot hand over a file path).

### 5. Install (from Ubuntu) and finishing in Windows

`install/install-windows.sh` (run with `pkexec`; absolute paths):

1. Refuse unless Windows is cleanly shut down (no hibernation file in use,
   Fast Startup off, hive sequence numbers equal — the same checks bt-share
   uses) and the WinBridge VM is not running (`/dev/mapper/winbridge`).
2. Remount `/mnt/windows` rw briefly; restore ro at the end, also on failure.
3. Copy the RAM helper (and the Python it runs on) to
   `C:\Program Files\RGB-Hub\`, admin-writable only. Copy the hub to
   `%LOCALAPPDATA%\Programs\RGB-Hub\versions\<commit>\` (user-writable, so
   the updater needs no elevation): Windows embeddable Python
   (pinned version, checksum-verified), wheels unpacked for `win_amd64`
   (`pip download --only-binary=:all: --platform win_amd64`: Pillow,
   hidapi, pystray), the `rgbhub` package, the helper, the web page,
   `finish-setup.ps1`, `uninstall.ps1`.
4. Write (once: see Updates) `%APPDATA%\rgbhub\settings.json` for the owner's account from the
   current Ubuntu GSettings (via the existing schema list), copying the
   LCD image files it refers to into `%APPDATA%\rgbhub\pictures\` and
   rewriting the paths. Copy `~/.config/rgbhub/lcd/` likewise.
5. Drop `RGB-Hub setup.cmd` in the owner's Startup folder.

At the next Windows sign-in, `RGB-Hub setup.cmd` asks once for elevation
(UAC) and runs `finish-setup.ps1`, which:

- resets the install folder's ACLs to inherit Program Files' (files written
  from Linux carry ntfs3 defaults);
- registers the scheduled tasks: hub and updater at sign-in as the user;
  RAM helper at sign-in, highest privileges;
- uninstalls OMEN Gaming Hub (`Get-AppxPackage AD2F1837.OMENCommandCenter
  | Remove-AppxPackage`, plus the provisioned package so it does not come
  back for new users) and disables `HPOmenCap`, `HPOmenCustomCapDriver` and
  HP's analytics services (`HpTouchpointAnalyticsService`,
  `hp-one-agent-service`); the services' previous start types are logged
  so `uninstall.ps1` can restore them (the app itself comes back only by
  reinstalling it from the Store);
- starts both, then deletes the Startup entry. Logs to
  `%LOCALAPPDATA%\rgbhub\setup.log`.

`uninstall.ps1` removes the tasks and folder and re-enables whatever
setup disabled.

**Updates**: after the first install, Windows updates itself (section 9).
`install-windows.sh` is for the first install (or a repair); re-running it
only *seeds* settings and pictures when they are missing and never
overwrites what the owner changed on Windows. Writing to Windows makes
WinBridge start fresh (new iCloud pairing code), as bt-share's writes
already do; accepted.

**WinBridge VM** (builder's call): WinBridge boots this same Windows as a
throwaway VM with no USB devices passed through. The hub, the RAM helper
and `finish-setup.ps1` check for a virtual machine first (`Win32_
ComputerSystem.Model`/manufacturer is QEMU) and exit without doing
anything, so setup is never "used up" inside the VM and nothing pops up there.

### 6. Linux changes

Only refactoring to the interfaces: `hidraw.py` gains `HidrawBus`
(`find(HidMatch)` by reading the usage page from the report descriptor —
identical matches to today's prefixes), `ram_bios.py` exposes its two
paths as `RamBus` classes, platforms pass the bus/runtime in. Behaviour
identical; all existing tests pass (adjusted only for the new
constructor arguments).

### 7. Package layout (no "backends")

The owner asked that the word "backends" go: OS-specific code is
*platform* code, and protocol code belongs to the device it talks to.

```
rgbhub/
  core/                 hub, auto mode, looks, settings schema, and the interfaces
                        (HidBus, HidPort, RamBus, Runtime, Settings, Scheduler, Device)
  devices/
    __init__.py         DEVICES = (omen_pc, iqunix_keyboard): the one list
    omen_pc/            device.py, lcd (protocol, streamer, fade), case board driver
                        (tracer.py), RAM registers (ram.py), constants (omen45l.py)
    iqunix_keyboard/    device.py, via.py, mg65.py
  platforms/
    linux/              hidraw HidBus, GLib Runtime, RAM via socket helper / acpi_call,
                        OpenRGB client + detector lock
    gnome/              GSettings store, icon theme, wallpaper, logind, main()
    batocera/           game hook, theme, power hooks, JSON store use, main()
    windows/            hidapi HidBus, thread Runtime, WMI RamBus client, focus,
                        icons, wallpaper, tray, web settings server, main()
  updater/              the self-updater (section 9), stdlib only
```

Shared, OS-neutral pieces that several platforms use (`json_store.py`,
`thread_runtime.py`, `lcd_folder.py`, look/icon colour logic) live in
`core/` or a `platforms/common/` folder; no platform imports another
platform except `gnome`/`batocera` importing `linux`. `__main__.py` keeps
`python -m rgbhub` working. Old import paths are not kept (one owner, all
callers updated together, incl. EmuBox's component and MacWinBuntu).

### 8. Adding a device

Today a new device must be wired into every platform's `main()`,
`DEVICE_IDS`, `prefs.js`'s `DEVICES` and (now) the web page. Instead:

- Each device package exposes `build(platform) -> Device | None`.
  `platform` is a small object of what that system offers: `hid_bus`,
  `ram_bus` (or None), `runtime`, `watch_file`, `case_lights` choice
  (OpenRGB on GNOME, tracer elsewhere), `lcd_folder`. A device returns None
  when the system cannot support it.
- `devices/__init__.py` holds the one list `DEVICES`; every platform does
  `[d for m in DEVICES if (d := m.build(platform))]`.
- `DEVICE_IDS` is derived from that list.
- Each device describes itself in `status`: `id`, `label`, `icon` (a
  freedesktop symbolic name, mapped for Windows), `has_lcd`. The GNOME
  window and the web page build their tabs from `status`; prefs.js's
  hard-coded `DEVICES` goes (kept only as the fallback when status is empty).
- Result: a new device = one new folder under `devices/` + one line in
  `DEVICES`. Exception: EmuBox's Batocera menu keeps its fixed list
  (Batocera reads its menu config only at start; decided 2026-11-19), so it
  needs a one-line edit there too.
- A test builds every device in `DEVICES` against a fake platform and
  checks it fits the `Device` interface and describes itself in status.

### 9. Self-updater (all three systems)

Every system keeps an installed copy that follows `main` of
`github.com/Konfus-dev/RGB-Hub` (GitHub redirects the old
`OmenRGB-Controller` name). The owner's working copy
(`~/Source/RGB-Hub`) is never touched by it.

- **Separate process** (`python -m rgbhub.updater`), stdlib only, so a bad
  hub version cannot break updating. Runs at start-up, then hourly:
  systemd user timer (Ubuntu), scheduled task as the user (Windows), the
  `rgb_hub` service script loop (Batocera).
- **Check**: GitHub API `commits/main` → SHA. Apply only if it is a
  descendant of the installed commit (`compare/<installed>...<sha>` says
  `ahead`). Never goes backwards; GitHub `main` today still holds the old
  OmenRGB-Controller code until the owner renames and pushes.
- **Install a version**: download `archive/<sha>.zip` → unpack into
  `versions/<sha>/` → install its Python dependencies into that folder
  (Windows: wheels for the embeddable Python; Ubuntu: a venv per version
  with system site packages; Batocera: none, Pillow is the system's) →
  `python -m rgbhub --self-check` (imports every module the platform uses,
  builds the hub against fakes) → switch `current` (symlink on Linux, a
  `current.txt` the launcher reads on Windows) → restart the hub.
  Keep the previous two versions; delete older ones.
- **When**: at once on Ubuntu/Windows (a brief Default-look flash); on
  Batocera only when no game is running (`focused-app` empty).
- **Rollback**: if the restarted hub exits or crashes within 60 s, switch
  back to the previous version and record the commit as bad; skip it until
  `main` moves on. Logged; on Ubuntu/Windows also a desktop notification.
- **Privileged parts are never updated silently on Ubuntu/Windows.** The
  updater runs with the owner's normal rights. If a new version changes
  the RAM helper, udev rules, systemd units or scheduled-task definitions,
  the rest is applied and the owner gets a notification to approve that
  part (pkexec prompt on Ubuntu, UAC on Windows), running the platform's
  privileged install step from that version. Reason: otherwise anyone
  with push access to the repo would get admin on every machine. On
  Batocera everything already runs as root, so it updates everything.
- **Settings are never touched by updates.** The GNOME extension is
  copied too (takes effect at the next login).
- **Developing on Ubuntu**: `install/install-user.sh --dev` points the
  service at the working copy and pauses the updater; `--release` goes back
  to following `main`. Installs default to `--release`.
- **EmuBox**: its `rgb-hub` component installs the release layout once;
  afterwards the updater keeps it current.

## Testing

- Unit + contract tests on Linux with fakes (all new pure-Python code,
  `thread_runtime`, web API, settings conversion, helper checks with a
  fake WMI transport, `hidapi_bus` with a fake `hid` module).
- `win_focus`, `win_icons`, `ram_wmi` transport, tray: thin ctypes/COM
  layers kept small; checked on the real Windows with the owner watching
  (LCD, case, RAM, keyboard, auto mode across a few apps incl. a Store app,
  sleep/wake, quit → Default, reboot back to Ubuntu keeps working).
- First check on Windows before anything else: hidapi report lengths for
  the LCD (1024 incl. id) and VIA (33 incl. id) are accepted.

## Left to the builder

- Exact module names within the layout above; how `prefs.js`'s rows map
  onto HTML; styling of the page (follow Windows light/dark).
- Python version for the embeddable build (≥ 3.12, matching what Pillow
  and hidapi ship wheels for).
- WMI access from the helper: a long-lived PowerShell child per helper, or
  `comtypes`/ctypes COM — whichever keeps the helper smallest and fast
  enough (measure).
- Commit order; work on `dev`, `main` only once Windows is tested.

## Owner's decisions (2026-11-24)

1. **OMEN Gaming Hub is uninstalled** by setup, and HP's OMEN and
   analytics services are disabled (see section 5).
2. **Settings are copied once** from Ubuntu at install. After that each
   system keeps its own; no ongoing sync.
3. **No "backends"**: OS-specific code lives under `platforms/<system>/`,
   protocol code with its device (section 7).
4. **Adding a device must be easy**: one folder + one line (section 8).
5. **Auto-updater on all three systems**, following `main` (section 9).

Owner's rule (2026-11-24): re-use existing tools and libraries; do not
invent new solutions. Applied: asyncio for the Windows event loop,
`multiprocessing.connection` for the RAM pipe, `icoextract` for exe icons,
Windows' own Slideshow setting for changing wallpapers (RGB-Hub only reads
the wallpaper Windows is showing; it never picks or changes one).
