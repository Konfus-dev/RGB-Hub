# Design

How RGB-Hub is put together, and why. For installing and
using it, see the [README](../README.md).

## Goals

- One program for every lit device ("hub"), each device behind the same
  small interface, so a new device is one more module.
- Put the owner's own images on the cooler LCD and keep them there.
- Set colour, effect and brightness for the case lights, the RAM sticks and
  the keyboard.
- One place for all of it: a button in the GNOME system menu that opens a
  settings window with an Auto tab and a tab per device.
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
 front ends                 platforms             core                      devices / backends
 (what you touch)           (one per system)      (the behaviour)           (hardware)

 GNOME Shell extension --+
                         +-> gnome.py -----+
                                           +--> Hub --> OmenPc ---------> LcdStreamer -> hidraw.py   -> cooler LCD
 Batocera service switch +                 |     |        |------------> Lights ------> openrgb.py  -> case lights
 and game hook ----------+-> batocera.py --+     |        |                        \---> tracer.py   -> case lights
                                                 |        \------------> RamLights ---> ram_bios.py -> BIOS -> RAM
                                                 \----> IqunixKeyboard -> Lights ------> via.py      -> keyboard
```

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
- **backends** (`service/rgbhub/backends/`) each reach one kind of
  hardware one way.
- **platforms** (`service/rgbhub/platforms/`) are the only place that
  knows about a particular system. Each has a `main()` that picks backends,
  a settings store, timers, and a source for "what is in focus", and builds
  the `Hub` and its devices from them.
- **front ends** (`frontends/`) never touch hardware. A front end writes
  the settings listed in `core/settings_schema.py` and reads the `status`
  report; that list is the whole contract.

| | GNOME desktop | Batocera |
|---|---|---|
| Front end | Shell extension: menu button and settings window | Service switch, game hook, a JSON file |
| Settings store | GSettings (`gsettings_store.py`) | JSON file (`json_store.py`) |
| Case lights | OpenRGB server (`openrgb.py`) | own driver (`tracer.py`) |
| RAM lights | root helper over a socket | in-process (the service is root), if `acpi_call` exists |
| In focus | the focused app; its icon from the icon theme | the running game; its scraped artwork |
| Idle look (nothing in focus) | the wallpaper and its main colour (`gnome_theme.py`) | the menu theme's background (`batocera_theme.py`) |
| LCD override folder (`core/lcd_folder.py`) | `~/.config/rgbhub/lcd/` | `rgb-hub/` in the active theme |
| Sleep/wake | systemd-logind | Batocera's suspend/resume hooks |
| Timers, file watching | GLib (`glib_runtime.py`) | GLib |

A bug in a front end cannot stall hardware handling, and a new system does
not touch the core: it is one more file in `platforms/`.

On the GNOME desktop the parts run as: the extension inside GNOME Shell,
the service and an OpenRGB server as systemd user units, and the RAM helper
as a socket-activated root unit.

## Cooler LCD

USB `03f0:7397` "LCD-PUMP", two HID interfaces. Only the vendor interface
(report descriptor starting `06 1a ff`) is used; the other is mouse-class
and must not be written to.

| Operation | Report | Meaning |
|---|---|---|
| `0x41` | ID 1, 64 bytes, byte 5 = 1 | Claim the screen. Repeated every second. |
| `0x6c` | ID 2, 1024 bytes, byte 12 = 0–100 | Brightness. |
| `0x6e` | ID 2, 1024 bytes | One chunk of a 480x480 JPEG: total length at bytes 4–5, chunk index at 8, chunk length at 9–10, up to 1013 data bytes from 11. |

The screen stores nothing; without a stream of frames it returns to the
OMEN logo. The service re-sends the current image every 500 ms, the rate
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
except that one is switched off (`backends/openrgb.py`). Its normal RAM
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

Each write takes about 65 ms through the BIOS, so `devices/omen_pc/ram.py` remembers what
every register holds and sends only the ones that changed, on a worker
thread, newest request wins. A colour change is then about ten writes
instead of forty, and never delays the LCD or the case lights. The RAM
cannot fade smoothly at that speed; it switches in one step.

## Auto mode

Optional. The extension writes the focused app's id (for example
`firefox.desktop`) to the `focused-app` setting, and only while auto mode is
on. Everything else happens in the service (`core/look.py`, with the icon lookup in `platforms/gtk_icons.py`): it finds the
app's icon in the current icon theme, picks the icon's most striking colour
(grouping pixels by hue, ignoring greys, then making the winner vivid enough
for LEDs), and builds an LCD picture of the icon on black. That colour
replaces every zone colour and the picture replaces the chosen LCD image
until focus moves on. Apps with no usable icon get the idle look, as when
nothing is in focus (on GNOME: the LCD folder, else the wallpaper, else the
OS logo, with that picture's main colour; `auto-idle-image` and
`auto-idle-colour` override it). This project's own settings window falls
back to the normal settings.

## Sleep

The service holds a logind "delay" lock. Just before the computer sleeps it
turns the LCD, case lights and RAM off, then releases the lock. After
waking it sends every setting again, in case the hardware lost them.
On Batocera the menu's suspend and resume hooks send the same two messages.

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
- A settings file that cannot be read (half-saved, or with a typo) changes
  nothing: the last good settings stay in force and the problem is logged once.
- The RAM helper only reports itself ready when the BIOS can be reached, so a
  missing `acpi_call` module shows as "RAM unavailable" rather than a RAM
  section that comes and goes.

## Settings

Every setting and its default is listed in `core/settings_schema.py`. On
GNOME they are stored in GSettings (schema in
`frontends/gnome-shell/…/schemas/`, checked against that list by a test);
on Batocera in a JSON file. The front end writes; the service watches. The
`status` key goes the other way: the service writes a JSON report (what is
connected, the last image error, and the lighting devices with their
zones, effects and display names).

## Tested hardware

OMEN by HP 45L GT22-3xxx (board 8D2C), Ubuntu 26.04, GNOME Shell 50,
OpenRGB 0.9, LCD firmware V3.05.024.

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
forgets its picture as soon as frames stop, so it is left alone.

The stop paths are SIGTERM from systemd (log-out, shutdown, reboot) and the
Batocera service's `stop`, which waits up to 10 s for the program to finish.
At shutdown on GNOME the root RAM helper is ordered to stop after the
user's session (`Before=user@UID.service`), so it is still there to deliver
the RAM's part.
