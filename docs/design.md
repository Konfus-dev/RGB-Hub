# Design

How RGB-Hub is put together, and why. For installing and
using it, see the [README](../README.md).

## Goals

- One program for every lit device ("hub"), each device behind the same
  small interface, so a new device is one more module.
- Put the owner's own images on the cooler LCD and keep them there.
- Set colour, effect and brightness for the case lights, the RAM sticks and
  the keyboard.
- One place for all of it: a button in the GNOME system menu (an icon in
  the tray on Windows) that opens a settings window with an Auto tab and a
  tab per device.
- Run on Ubuntu (GNOME), Batocera and Windows from the same core, and make
  a new system one more folder that fills in a few named interfaces.
- Keep each installed copy up to date by itself, safely (see "Updates").
- When the hub stops, leave every device showing the owner's Default look.
- Settings return by themselves after a reboot.
- Easy to change later: one job per file, hardware numbers in one place,
  existing tools reused wherever one does the job.

## Things it deliberately does not do

- Send anything to the LCD beyond three known operations (claim,
  brightness, image frame). No firmware, no guessed commands.
- Talk to the RAM sticks over SMBus. On this board that jams the RAM's
  control bus until the next reboot (see "RAM lights" below).
- Run day-to-day code as root, apart from one small, single-purpose helper.
- Send the keyboard anything beyond three standard VIA commands (below).
  No firmware, no keymap writes, no vendor commands.

## Layers

```
 front ends                 platforms             core                      devices
 (what you touch)           (one per system)      (the behaviour)           (hardware)

 GNOME Shell extension --+
                         +-> gnome/ -------+
 Batocera service switch |                 |
 and game hook ----------+-> batocera/ ----+--> Hub --> OmenPc ---------> LcdStreamer -> HidBus --------> cooler LCD
                         |                 |     |        |------------> Lights ------> OpenRGB or      case lights
 Windows tray icon and --+-> windows/ -----+     |        |                             case_board.py
 settings web page                               |        \------------> RamLights ---> RamBus --------> RAM
                                                 \----> IqunixKeyboard -> Lights ------> HidBus --------> keyboard
```

`HidBus` and `RamBus` are the interfaces from `core/interfaces.py`; each
platform hands the core its own version (`platforms/linux/hidraw.py` or
`platforms/windows/hid.py` for USB, and so on; see "Platforms and
interfaces").

- **core** (`service/rgbhub/core/`) decides what each device should show.
  It imports no operating-system or hardware-library modules. The `Hub`
  (`hub.py`) owns the settings, auto mode, sleep and wake, the status
  report and the Default look; it tells each device what happened through
  the calls in `device.py`. `light_group.py` is the lights logic devices
  share (batching, fades and breathing, reconnecting). Everything is handed
  in: settings, a scheduler, the objects that reach hardware.
  `interfaces.py` describes those shapes.
- **devices** (`service/rgbhub/devices/`) are one module per product:
  `omen_pc.py` (LCD, case lights, RAM) and `iqunix_keyboard.py`. A device
  reads settings only through the hub (`hub.get`), which is what lets the
  hub show the Default look on the way out.
- **platforms** (`service/rgbhub/platforms/`) are the only place that
  knows about a particular system, one folder each (`gnome/`, `batocera/`,
  `windows/`), plus `linux/` for the code the two Linux ones share (USB
  devices, RAM writes, OpenRGB, the GLib event loop). Each system has a
  `main()` that picks the platform code to use, a settings store, timers,
  and a source for "what is in focus", and builds the `Hub` and its devices
  from them.
- **updater** (`service/rgbhub/updater/`) keeps an installed copy up to
  date. It is a separate program that uses only the standard library (see
  "Updates").
- **front ends** (`frontends/`) never touch hardware. A front end writes
  the settings listed in `core/settings_schema.py` and reads the `status`
  report; that list is the whole contract.

| | GNOME desktop | Batocera | Windows |
|---|---|---|---|
| Front end | Shell extension: menu button and settings window | Service switch, game hook, a JSON file | Tray icon (`windows/tray.py`) and a settings web page (`frontends/web/`, served by `core/web.py`) in an Edge app window |
| Settings store | GSettings (`gnome/gsettings_store.py`) | JSON file (`core/json_store.py`) | JSON file, `%APPDATA%\rgbhub\settings.json` |
| USB devices (`HidBus`) | hidraw (`linux/hidraw.py`) | hidraw | hidapi (`windows/hid.py`) |
| Case lights | OpenRGB server (`linux/openrgb.py`) | own driver (`case_board.py`) | own driver (`case_board.py`) |
| RAM lights (`RamBus`) | root helper over a socket | in-process (the service is root), if `acpi_call` exists | helper with full rights, reached over a local connection with a key (`windows/ram.py`) |
| In focus | the focused app; its icon from the icon theme | the running game; its scraped artwork | the app in front (`windows/focus.py`); its icon from the program file, or from the Store app's package (`windows/icons.py`) |
| Idle look (nothing in focus) | the wallpaper and its main colour (`gnome/gnome_theme.py`) | the menu theme's background (`batocera/theme.py`) | the wallpaper Windows shows now, else the Windows logo (`windows/wallpaper.py`) |
| LCD override folder (`core/lcd_folder.py`) | `~/.config/rgbhub/lcd/` | `rgb-hub/` in the active theme | `%APPDATA%\rgbhub\lcd\` |
| Sleep/wake | systemd-logind | Batocera's suspend/resume hooks | Windows' power messages, heard by a hidden window (`windows/session.py`) |
| Timers, file watching (`Runtime`) | GLib (`linux/glib_runtime.py`) | GLib | `core/thread_runtime.py` (the standard library's asyncio loop) |
| Updates | systemd user timer | the service script's loop | a scheduled task |

A bug in a front end cannot stall hardware handling, and a new system does
not touch the core: it is one more folder in `platforms/`.

On the GNOME desktop the parts run as: the extension inside GNOME Shell,
the service and an OpenRGB server as systemd user units, and the RAM helper
as a socket-activated root unit. On Windows they run as three scheduled
tasks that start at sign-in: the hub and the updater with the owner's normal
rights, and the RAM helper with full rights.

## Platforms and interfaces

The core is handed everything that touches a system. The shapes it expects
are written down in `core/interfaces.py`:

| Interface | What it is for |
|---|---|
| `HidBus` and `HidPort` | Find a USB device (`HidMatch`: vendor, product, and optionally the usage page) and open it; write and read reports; say whether it was unplugged. A failure raises `OSError`. |
| `RamBus` | Send a batch of RAM register writes; say which sticks are there. |
| `Runtime` | Timers (`every`, `cancel`), file watching, `call_soon` (run something on the loop from any thread), `run_until_stopped`, `stop`. |
| `Settings` | `get`, `set`, `subscribe`. Where the front end's choices live and where `status` is written. |
| `Platform` (`core/platform.py`) | What a system gives the devices: its `HidBus`, `Runtime`, a `RamBus` if it has one, how to open the case lights, whether the LCD fades. |

`interfaces.py` also holds in-memory fakes (`FakeHidBus`, `FakeHidPort`)
for tests. `tests/test_contracts.py` runs one set of checks against every
real version of each interface that can run on the test machine, so a new
one is tested by adding it to that list.

**To add a system**, write `platforms/<name>/` with:

- a `HidBus`/`HidPort` for its USB access, and a `RamBus` if the RAM lights
  can work there (otherwise leave it out and the RAM is reported
  unavailable);
- a `Runtime` for its timers and event loop (`core/thread_runtime.py` is
  one that needs nothing system-specific);
- a `Settings` store (`core/json_store.py` already does a JSON file);
- the pieces for auto mode: a source for "what is in focus", an icon
  loader, an idle picture, and a `Looks(...)` built from them
  (`core/look.py`);
- sleep/wake calls to `hub.on_sleep` and `hub.on_resume`, and a stop that
  calls `hub.stop()`;
- a `main()` that builds a `Platform`, calls `devices.build_all(platform)`
  and starts the `Hub`; then list the system in `service/rgbhub/__main__.py`.

Nothing in `core/` or `devices/` changes. Reuse what another platform
already has: Windows and Batocera share the JSON store and the case lights'
own driver; the two Linux platforms share `linux/`.

## Adding a device

A device is one folder in `service/rgbhub/devices/` with the protocol code,
its model facts (zone and effect names, hidden zones) and a `device.py` that
fits `core/device.py`. The folder's `__init__.py` gives `DEVICE_ID` and
`build(platform)`. `build` takes what the system offers (its `HidBus`,
`RamBus`, ...) and returns the device, or `None` if this system cannot run
it. (Both devices today work everywhere, so neither returns `None` yet.)

Then two small edits:

- one line in `devices/__init__.py`, in `DEVICES`, which every platform
  builds its devices from;
- the id in `DEVICE_IDS` in `core/settings_schema.py` (a test checks that
  the two lists agree).

Each device describes itself in the `status` report (`label`, `icon`, whether it has an LCD, what is connected),
and the GNOME window and the web page build their tabs from that, so no
front end needs a change. The one exception is EmuBox's Batocera menu: it
reads its list of devices only when Batocera starts, so it keeps a fixed list
and needs a one-line edit too.

## Cooler LCD

USB `03f0:7397` "LCD-PUMP", two HID interfaces. Only the vendor interface
(report descriptor starting `06 1a ff`) is used; the other is mouse-class
and must not be written to.

| Operation | Report | Meaning |
|---|---|---|
| `0x41` | ID 1, 64 bytes, byte 5 = 1 | Claim the screen. Repeated every second. |
| `0x6c` | ID 2, 1024 bytes, byte 12 = 0–100 | Brightness. |
| `0x6e` | ID 2, 1024 bytes | One chunk of a 480x480 JPEG: total length at bytes 4–5, chunk index at 8, chunk length at 9–10, up to 1013 data bytes from 11. |

The screen keeps the last frame it was sent (on the 45L it does not go
back to the OMEN logo by itself). The service re-sends the current image every 500 ms, the rate
HP's software uses for a still picture. When the picture changes, a short
burst of blended frames crossfades from the old one to the new
(`devices/omen_pc/lcd_fade.py`). Confirmed on firmware V3.05.024.

Protocol facts are from the notes in
[omen-led-lcd](https://github.com/Xedecimal/omen-led-lcd); none of its code
is used.

## Case lights

USB `103c:84fd` "HP TracerLED". OpenRGB drives it with its "HP Omen 30L"
driver. It reports seven zones; on the 45L only the front logo and the
three front fans have lights, so the other three are hidden
(`devices/omen_pc/model.py` (`HIDDEN_ZONES`)).

OpenRGB runs with its own configuration folder in which every detector
except that one is switched off (`platforms/linux/openrgb.py`). Its normal RAM
probing is what jams the RAM bus.

Plain colours and breathing are not left to the board. The service puts it
in "Direct" mode, where it shows whatever colours it is sent, and draws them
itself (`lights._Glow`):

- A colour change glides from the old colours to the new over 0.4 s.
- Breathing is a smoothly changing brightness (`lights.breath_level`), which
  is what makes the speed, dimmest and brightest sliders work. The board's
  own breathing always fades to black and has three fixed speeds.

Frames go out about 30 times a second, but only while something is fading
or breathing; the fast timer stops itself otherwise, so a steady colour
costs nothing. The board's other effects (Wave, Color Cycle, ...) are still
its own.

## RAM lights

The sticks are HP-branded Kingston Fury DDR5 (`HP60U40S8HA-16FRE`). They
speak the standard Kingston lighting registers, but on this board any
direct SMBus access to their light controllers (address `0x61`, `0x63`),
even a read, makes the whole RAM bus stop answering until reboot.

HP's OMEN Gaming Hub avoids that by asking the BIOS to deliver each
register write: WMI command `0x20009`, type `0x0A`, data
`[register, value, address]` with address `0xC0 + 2 * slot`. From Linux
that is ACPI method `\_SB.WMID.WMAA`, reached through the `acpi_call`
kernel module. Sent this way the sticks obey and the bus stays healthy.

Because only root can call ACPI methods, a root helper sits in between.
It was chosen over a custom kernel module (more to maintain) and over a
password prompt per change (unusable). The helper:

- runs from a root-owned copy in `/usr/local/lib/rgbhub/`, standard
  library only;
- listens on `/run/rgbhub-ram.sock`, which only the installing user can
  open;
- accepts one request, "write value V to register R of stick A", and
  checks A, R and V against fixed lists and that a stick is in that slot.

Register meanings come from HP's module and from OpenRGB's
`KingstonFuryDRAMController`.

On Windows the same rule holds: a helper with full rights is the only part
that reaches the BIOS, and it is the same helper's checks
(`helper/rgbhub_ram_helper.py`) with a different way to the BIOS. It asks
HP's WMI method `hpqBIntM.hpqBIOSInt128` (the same request as the Linux
ACPI call), which the BIOS provides itself, so removing OMEN Gaming Hub does
not take RAM lighting away. The helper (`helper/rgbhub_ram_helper_windows.py`,
standard library only) runs from `C:\Program Files\RGB-Hub\`, where only
administrators can write, so unprivileged code cannot swap it. The hub
reaches it over a local connection on `127.0.0.1` using the standard
library's `multiprocessing.connection`, which turns away anyone without the
key in `%LOCALAPPDATA%\rgbhub\ram-key` (readable only by the owner,
SYSTEM and administrators). Without the helper the RAM is reported
unavailable, as on Batocera without `acpi_call`.

Each write takes about 65 ms through the BIOS, so `devices/omen_pc/ram.py` remembers what
every register holds and sends only the ones that changed, on a worker
thread, newest request wins. A colour change is then about ten writes
instead of forty, and never delays the LCD or the case lights. The RAM
cannot fade smoothly at that speed; it switches in one step.

## Auto mode

Optional. The extension writes the focused app's id (for example
`firefox.desktop`) to the `focused-app` setting, and only while auto mode is
on. Everything else happens in the service (`core/look.py`, with the icon lookup in `platforms/gnome/gtk_icons.py`): it finds the
app's icon in the current icon theme, picks the icon's most striking colour
(grouping pixels by hue, ignoring greys, then making the winner vivid enough
for LEDs), and builds an LCD picture of the icon on black. That colour
replaces every zone colour and the picture replaces the chosen LCD image
until focus moves on. Apps with no usable icon get the idle look, as when
nothing is in focus (on GNOME: the LCD folder, else the wallpaper, else the
OS logo, with that picture's main colour; `auto-idle-image` and
`auto-idle-colour` override it). This project's own settings window falls
back to the normal settings.

On Windows the app in front is checked twice a second (`platforms/windows/focus.py`)
and its id is the program file's path, or the package family for a Store app.
Its icon is read out of the program file by the `icoextract` library (the
largest picture in it) or, for a Store app, from the logos its package
names in its manifest (`icons.py`). With nothing in focus the LCD shows the
LCD folder, else the wallpaper Windows is showing now, else a Windows logo
(`wallpaper.py`). RGB-Hub only reads the wallpaper; with Windows' own
Slideshow setting it changes by itself. The tray menu, the lock screen,
Start and RGB-Hub's own settings window do not count as an app in front.

## Sleep

The service holds a logind "delay" lock. Just before the computer sleeps it
turns the LCD, case lights and RAM off, then releases the lock. After
waking it sends every setting again, in case the hardware lost them.
On Batocera the menu's suspend and resume hooks send the same two messages.
On Windows a hidden window hears the power messages (`windows/session.py`);
it also hears sign-out and shutdown and stops the hub, so the Default look
is shown then too.

Those messages are not relied on. Every few seconds the service compares a
clock that counts through sleep with one that does not; a jump means the
computer slept, and everything is sent again whether or not anyone said so.
And if it was told "going to sleep" but is still running 45 seconds later,
it switches everything back on.

## Staying up

- **Ubuntu**: systemd restarts the service if it fails. It is tied to the
  OpenRGB server (`PartOf`), so they restart in an order in which OpenRGB can
  always reopen its port. OpenRGB is not started at all if the program has
  changed since `install-user.sh` last ran, because a new version may bring
  hardware detectors that the restricted config does not switch off.
- **Batocera**: the service script keeps the program running itself,
  restarting it after a crash with a growing pause.
- **Windows**: three scheduled tasks start at sign-in. Quitting from the tray
  stops the hub for good; any other exit makes the launcher
  (`windows/launcher.py`) start it again. Inside the WinBridge virtual
  machine, which boots this same Windows, none of it starts: the lights belong
  to the real PC.
- A settings file that cannot be read (half-saved, or with a typo) changes
  nothing: the last good settings stay in force and the problem is logged once.
- The RAM helper only reports itself ready when the BIOS can be reached, so a
  missing `acpi_call` module shows as "RAM unavailable" rather than a RAM
  section that comes and goes.

## Settings

Every setting and its default is listed in `core/settings_schema.py`. On
GNOME they are stored in GSettings (schema in
`frontends/gnome-shell/…/schemas/`, checked against that list by a test);
on Batocera and Windows in a JSON file. The front end writes; the service watches. The
`status` key goes the other way: the service writes a JSON report (what is
connected, the last image error, and the lighting devices with their
zones, effects and display names).

## Updates

Each system keeps an installed copy that follows `main` on GitHub
(`Konfus-dev/RGB-Hub`). Your working copy (`~/Source/RGB-Hub`) is never
touched by it.

- **A separate program.** `python -m rgbhub.updater` uses only the standard
  library, so a broken hub version cannot break updating. It runs at
  start-up and then every hour: a systemd user timer on Ubuntu, a scheduled
  task on Windows, the service script's loop on Batocera.
- **It only goes forward.** It asks GitHub for the newest commit on `main`
  and applies it only if GitHub says it is ahead of the installed one.
  A commit that is older, or on another branch, is ignored.
- **Each version has its own folder**, `versions/<commit>/`, next to a
  `current` marker (a symlink on Ubuntu; a `current.txt` on Windows and
  Batocera, where the disk may not allow symlinks). A new version is
  downloaded and unpacked beside the old one, gets its libraries, and must
  pass `python -m rgbhub --self-check` (it imports everything the platform
  uses and builds the hub against fakes) before the marker moves. Then the
  hub restarts. The last few versions are kept.
- **Rollback.** If the restarted hub exits or crashes within a minute, the
  updater switches back to the previous version, remembers the bad commit
  and skips it until `main` moves on. It says so in its log and, on Ubuntu
  and Windows, with a notification.
- **When.** At once on Ubuntu and Windows (the lights show the Default look
  for a moment while the hub restarts). On Batocera only when no game is
  running.
- **Parts that run with full rights are never updated silently** on Ubuntu
  and Windows: the RAM helper, the udev rules and systemd units, the
  Windows setup scripts and scheduled-task definitions. Otherwise anyone
  who could push to the repository would get administrator rights on
  every machine. The updater runs with your normal rights, installs
  everything else, and asks you to approve that part: a notification and a
  password prompt (`pkexec`) on Ubuntu; "Approve update..." in the tray menu
  and one admin prompt (UAC) on Windows. On Windows the admin step checks
  those files against GitHub's own copy before installing them. Batocera
  runs everything as root already, so it updates everything.
- **Settings are never changed by an update.** A new GNOME extension takes
  effect at the next login.
- **Developing on Ubuntu**: `bash install/install-user.sh --dev` runs the
  service straight from the working copy and pauses the updater;
  `--release` (the default) goes back to following `main`.

The first install on each system lays out the same folders, so the updater
needs no special case for it: `install-user.sh` on Ubuntu,
`install-batocera.sh` (and EmuBox's `rgb-hub` component) on Batocera,
`install-windows.sh` on Windows.

## Windows install

`install/install-windows.sh` runs on Ubuntu with `pkexec` and installs onto
the Windows partition. It first refuses unless Windows was shut down fully
(not hibernated, no Fast Startup, registry closed cleanly) and the WinBridge
virtual machine is not running. It mounts the partition writable only for
the copy, and always makes it read-only again, also when something fails.

It puts the RAM helper and Windows' embeddable Python in
`C:\Program Files\RGB-Hub\` (admin-writable only), and the hub, with its
libraries, in `%LOCALAPPDATA%\Programs\RGB-Hub\versions\<commit>\`, which the
owner can write, so the updater needs no elevation. It copies your Ubuntu
settings and pictures into `%APPDATA%\rgbhub\` once, only if Windows has
no settings yet, and leaves `RGB-Hub setup.cmd` in the Startup folder.

At the next Windows sign-in that file asks once for administrator rights
(UAC) and runs `finish-setup.ps1`, which:

- resets the permissions of the install folders;
- makes the key the hub and the RAM helper share;
- registers the three scheduled tasks;
- uninstalls OMEN Gaming Hub (for new accounts too) and switches off HP's
  OMEN and analytics services, noting their old start types so
  `uninstall.ps1` can put them back;
- starts the tasks and deletes the Startup entry.

Everything is logged to `%LOCALAPPDATA%\rgbhub\setup.log`. The scripts
refuse to follow a link or junction in the owner's folders, because they
run with full rights.

## Tested hardware

OMEN by HP 45L GT22-3xxx (board 8D2C), Ubuntu 26.04, GNOME Shell 50,
OpenRGB 0.9, LCD firmware V3.05.024. The same machine's Windows 11 is the
Windows target.

## Devices and their settings

Settings that belong to a whole device are tables keyed by the device's id
(`OMEN-PC`, `IQUNIX-KEYBOARD`): `device-enabled`, `device-brightness` and
`auto-devices`. Lighting settings (`device-modes`, `zone-colours`, speeds,
pulse range) stay keyed by the lighting device inside it (`HP Omen 30L`,
`RAM`, `IQUNIX MG65`). There is no stored "auto for everything" switch:
front ends show it as on when every device follows, and flipping it sets
them all.

Settings from OmenRGB-Controller are carried over on first start
(`settings_schema.migrate`; on GNOME read from the old dconf folder with
`dconf dump`, on Batocera by moving the old folder), and the look the owner
had then becomes the first Default.

## IQUNIX keyboard

USB `320f:5055`, IQUNIX MG65. Its interface with usage page `0xFF60`,
usage `0x61` is VIA's raw HID (32-byte reports). It answers VIA protocol
11 and keeps its lights on the "RGB matrix" channel (3). `devices/iqunix_keyboard/via.py`
sends only:

| Command | Meaning |
|---|---|
| `0x07` custom set value | channel 3: brightness (1), effect (2), speed (3), colour as hue + saturation (4) |
| `0x08` custom get value | read one back (only used by hand so far) |
| `0x09` custom save | store the live look in the keyboard |

A set changes only what is shown; a save makes the keyboard keep it with
no program running. Saves are sent only when the hub stops. Only values
that changed are sent, and the hub draws nothing frame by frame on the
keyboard (no fades, no hub-drawn breathing): the keyboard's own effects do
the moving. The effects' names and numbers, and the brightness (0..9) and
speed (0..4) ranges, are from IQUNIX's VIA definition of the MG65
(`devices/iqunix_keyboard/mg65.py`).

On GNOME a udev rule (`install/70-rgb-hub.rules`) gives the logged-in user
the lighting interface only, not the ones that carry keystrokes.

## The Default look

`default-look` holds a copy of the look settings (`DEFAULT_KEYS`). Front
ends ask for "save" (current look -> Default) or "load" (Default -> current)
by writing `default-action`; the hub does it and clears the request.

When the hub stops cleanly it sets `hub.stopping`, from which point
`hub.get` answers from the Default and auto mode is off. Each device then
shows that at once (no fades; the RAM is waited for) and keeps it where the
hardware can: the keyboard saves it, the case board stays in direct mode
with the colours until it loses power. Breathing that the hub draws itself
would freeze mid-breath, so those lights are left steady instead. The LCD
keeps the last frame it gets, so the Default's picture is sent as the last
one. With no Default saved the hub stops with `factory_look()`: every light
steady purple at full brightness, and the bundled `core/pictures/on-stop.jpg`
(Spike) on the LCD.

The stop paths are SIGTERM from systemd (log-out, shutdown, reboot) and the
Batocera service's `stop`, which waits up to 10 s for the program to finish.
At shutdown on GNOME the root RAM helper is ordered to stop after the
user's session (`Before=user@UID.service`), so it is still there to deliver
the RAM's part.
