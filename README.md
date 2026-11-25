# RGB-Hub

One place for every lit device on the PC, without the makers' Windows
software. Today that is two devices:

- **OMEN-PC**: the lighting and the CPU-cooler LCD of an **HP OMEN 45L**
  desktop (case lights, RAM lights, cooler screen).
- **IQUNIX-KEYBOARD**: an **IQUNIX MG65** keyboard's lights.

It runs on a GNOME desktop (developed on Ubuntu), on Batocera and on
Windows, and is built so that more devices, systems and front ends can be
added without touching the core. (It was called OmenRGB-Controller until it learned the
keyboard.)

This is an unofficial hobby project, not affiliated with HP. Use it at your
own risk.

## What it does

- **Cooler LCD:** show any image you choose on the round screen on the CPU
  cooler, and set its brightness.
- **Case lights:** set the colour of the front logo and each front fan, pick
  an effect (static, breathing, colour cycle, ...), set brightness, or turn
  them all off.
- **RAM lights:** a colour per stick, or a rainbow or breathing effect, with
  the same on/off switch and brightness as the case lights.
- **Keyboard lights:** a colour, brightness and effect (with its speed)
  for the IQUNIX keyboard, on or off.
- **Effects you can tune:** moving effects get a speed slider, and breathing
  gets "dimmest" and "brightest" sliders, on the case as well as the RAM.
- **Auto mode:** optionally match your devices to what you are doing. With
  an app (or, on Batocera, a game) in focus, the LCD shows its icon or
  artwork and the lights and keyboard take its main colour. With nothing in
  focus they match the surroundings: the wallpaper on the desktop, the menu
  theme on Batocera (which can even ship its own LCD animation). One switch
  turns auto on for every device; each device also has its own.
- **A Default look:** save a look as the Default, and whenever RGB Hub stops
  (shutting down, logging out, the service switched off) every device is put
  back to it, so booting into something else shows that instead of the last
  app or game. The keyboard stores it in itself; the cooler screen cannot
  hold a picture and shows the OMEN logo once nothing sends it one.
- **Smooth and light:** colours glide and the LCD crossfades when something
  changes, everything goes dark when the PC sleeps, and the service does
  almost nothing while the lights are steady.
- **One place:** an "RGB Hub" button next to Wi-Fi and Bluetooth that
  opens a settings window with an Auto tab and a tab per device. On Windows
  it is an icon in the tray that opens the same kind of window.
- **Stays up to date:** each installed copy follows `main` on GitHub by
  itself, only forwards, and goes back to the old version if the new one
  does not start.
- Your choices come back by themselves after a reboot.

## What it does not do (yet)

- The RGB ring on the pump head, animated GIFs or live stats on the LCD,
  fan speeds.
- Other OMEN models. It may work on them, but it has only been tested on
  the machine below.

## Tested on

| | |
|---|---|
| Computer | OMEN by HP 45L Gaming Desktop GT22-3xxx (board 8D2C) |
| System | Ubuntu 26.04, GNOME Shell 50, Wayland |
| Lighting board | USB `103c:84fd` "HP TracerLED", driven through OpenRGB 0.9 (shown there as "HP Omen 30L") |
| Cooler LCD | USB `03f0:7397` "LCD-PUMP", firmware V3.05.024 |
| RAM | 2 x HP `HP60U40S8HA-16FRE` (Kingston Fury DDR5), set through the BIOS |
| Keyboard | USB `320f:5055` "IQUNIX MG65", VIA protocol 11 (QMK RGB matrix) |

OpenRGB lists seven light zones for the case. On the 45L only the front logo
and the three front fans have lights, so the rest are hidden.

## Install on a GNOME desktop (Ubuntu)

```sh
git clone https://github.com/Konfus-dev/RGB-Hub.git
cd RGB-Hub

# 1. Once, as root: installs OpenRGB and acpi_call, gives your user access
#    to the LCD, and installs the small RAM lighting helper.
#    It is short; read it first.
sudo bash install/setup.sh

# 2. As yourself: installs the latest commit of `main` into
#    ~/.local/share/rgbhub, sets up the background service and the
#    extension, and switches the extension on. The first time (and after an
#    OpenRGB update) it asks before letting OpenRGB do one full hardware
#    scan, which can stall the RAM sensors until a reboot; type 'scan' to
#    agree, or add --allow-detector-scan to agree in advance.
bash install/install-user.sh

# 3. Log out and back in. Only if the button is still missing afterwards:
gnome-extensions enable rgbhub@konfus-dev.github.io
```

The "RGB Hub" button now appears in the top-right menu. Press it to open
the settings window (or bring it to the front if it is already open).
The installer also adds an "RGB Hub" launcher, so the settings window can be
found in Activities search and the app grid like any other app.

Two ways to run it:

- **Release** (the default, `bash install/install-user.sh --release`): the
  installed copy is the newest `main` and the updater keeps it current.
- **Dev** (`bash install/install-user.sh --dev`): the service runs straight
  from your working copy and the updater pauses, so after editing the code
  you only restart the service. `--release` goes back to following `main`.

### Updates

The installed copy keeps itself up to date: shortly after start-up and then
every hour it looks at `main` on GitHub, and if there is a newer commit it
installs it beside the current one, checks it, switches to it and restarts
the hub. If the new version does not start, it goes back to the one before.
It only ever moves forward. Your settings are never touched; a new version
of the extension takes effect at your next login.

Updates only come from GitHub, so a commit reaches your systems once it is
pushed to `main` there. Until then the updater logs that GitHub's `main` is
not ahead of the installed copy and leaves everything as it is.

The updater runs with your normal rights. When a new version changes a part
that needs administrator rights (the RAM helper, the udev rules or the
helper's system units), everything else is updated and a notification asks
you to approve that part; Approve asks for your password and runs that
version's `install/setup.sh --no-apt`.

```sh
systemctl --user list-timers rgbhub-update.timer   # when it looks next
journalctl --user -u rgbhub-update -e             # what it did
```

Each version lives in `~/.local/share/rgbhub/versions/<commit>/`;
`~/.local/share/rgbhub/current` points at the active one.

## How it works

```
 GNOME Shell extension  --settings-->  rgbhub hub ---> OMEN-PC ---------> cooler LCD (USB)
 (button + settings window)            (Python, runs      |  \---------> OpenRGB server --> case lights
                                        at login)         |   \--------> root helper --> BIOS --> RAM lights
                                                          \-> IQUNIX-KEYBOARD --> keyboard (USB, VIA)
```

The extension never touches hardware; it only saves settings. The hub
watches those settings and tells each device what to show. The LCD forgets its picture unless
it is re-sent, so the service sends it twice a second (this costs almost no
CPU).

Only one piece runs as root: a small helper that passes RAM lighting
commands to the BIOS, the same way HP's own software does. It refuses
everything else. Talking to these RAM sticks directly, as other tools do,
jams their control bus on this board until the next reboot, so nothing here
does that. More detail is in [docs/design.md](docs/design.md).

## Batocera

From a Linux PC, with the Batocera drive plugged in and its `SHARE`
partition mounted:

```sh
sudo bash install/install-batocera.sh /path/to/SHARE
```

(EmuBox's `rgb-hub` component runs this same script for you.)

It installs the latest commit of the checkout's `main` (changes not
committed to main are left out; a copy without git history installs the
newest `main` from GitHub instead) into
`/userdata/system/rgb-hub/versions/<commit>/`. Then boot
Batocera and switch **RGB_HUB** on under **Main menu > System
Settings > Services**. While it is on, it looks for a newer `main` on
GitHub at start and about every hour, and installs it when no game is
running (logged in `/userdata/system/logs/rgb-hub.log`). In the menus the LCD shows the Batocera logo; while a
game runs it shows that game's artwork, and the lights take its colour.
Settings are a small JSON file; see
[frontends/batocera/README.md](frontends/batocera/README.md).

## Windows

RGB-Hub replaces HP's OMEN Gaming Hub on the OMEN's Windows 11. It is
installed from Windows itself. The easiest way is
[OS-Configs-And-Tools](https://github.com/Konfus-dev/OS-Configs-And-Tools):
its Windows setup has an `rgbhub` step that does the steps below by itself
(only on a PC with the OMEN cooler, the OMEN case lights or the IQUNIX
keyboard).

By hand: download RGB-Hub's `main` branch
(<https://github.com/Konfus-dev/RGB-Hub/archive/refs/heads/main.zip>),
unzip it, open PowerShell in the unzipped folder (as yourself, not as
administrator) and run:

```powershell
powershell -ExecutionPolicy Bypass -File install\windows\install.ps1
```

It downloads Windows' Python and pip (pinned versions, checksums checked) and
the newest `main` from GitHub, with the Windows libraries, into
`%LOCALAPPDATA%\Programs\RGB-Hub`. Then **Windows asks once for
administrator rights (UAC)**. After you accept, setup finishes by itself:

- it puts Python, the small RAM helper and the setup scripts into
  `C:\Program Files\RGB-Hub` (only administrators can change them there;
  Python's download is checked again there, the helper and scripts are
  checked against GitHub first);
- it registers three scheduled tasks that start at every sign-in (the hub,
  the updater, and the RAM helper, which is the only part with full rights),
  and starts them;
- it **uninstalls OMEN Gaming Hub** (also for new accounts) and **switches
  off HP's OMEN and analytics services** (`HPOmenCap`,
  `HPOmenCustomCapDriver`, `HpTouchpointAnalyticsService`,
  `hp-one-agent-service`). Their old settings are written down so the
  uninstall can put them back. The Hub app itself can be installed again
  from the Microsoft Store.

Settings start as the defaults (an existing `%APPDATA%\rgbhub\settings.json`
is kept). Run it again when RGB-Hub is installed and it only says so; add
`-Repair` to install everything again (quit RGB-Hub from its tray icon
first). If you said No to the administrator prompt, run it again to finish.
Inside a virtual machine it does nothing.

### From Ubuntu (old way)

It can also be installed from Ubuntu, onto the Windows partition, which also
copies your Ubuntu settings and pictures. First shut Windows down
completely (not hibernate, and Fast Startup off; the script checks and
refuses otherwise), boot Ubuntu, and run:

```sh
pkexec /usr/bin/bash /home/konfus/Source/RGB-Hub/install/install-windows.sh --user jercl
```

> **Known problem (2026-11-24):** on Ubuntu's kernel 7.0 the script stops at
> "could not make C:\Program Files\RGB-Hub admin-only from Ubuntu; nothing was
> copied". The kernel's NTFS driver (`ntfs3`) refused the
> `system.ntfs_security` setting the script uses to lock the folder down
> (reading it back gave "No such file or directory" even on existing
> folders). The script stops on purpose before copying anything, and leaves
> Windows read-only and unchanged. Install from Windows instead (above).

(`--user` is the Windows account's folder name under `C:\Users`;
`--windows-mount` says where Ubuntu mounts Windows, `/mnt/windows` by
default.) It copies the program, libraries and Python, and your Ubuntu
settings and pictures (once; settings already on Windows are never
replaced). It needs the network the first time, to fetch Windows' Python and
libraries. The Windows partition is writable only while it copies. You can
run it again as a repair.

Then boot Windows. At the next sign-in **one administrator prompt (UAC)**
appears; after you accept it, setup finishes as described above.

### Updates and uninstall

Windows keeps RGB-Hub updated the same way as Ubuntu. What you see and do
there is in [frontends/windows/README.md](frontends/windows/README.md).

To remove it, open PowerShell as administrator and run:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\RGB-Hub\install\windows\uninstall.ps1"
```

Add `-RemoveSettings` to delete your settings too. It removes the tasks and
folders and sets HP's services back as they were.

## Choosing what the LCD shows when nothing is in focus

With auto mode on, the LCD normally shows your wallpaper (on Batocera: the
menu theme's background) while no app or game is in focus. Each system has
one folder that overrides this:

| System | Folder |
|---|---|
| GNOME desktop | `~/.config/rgbhub/lcd/` |
| Batocera | `rgb-hub/` inside the active theme's folder |
| Windows | `%APPDATA%\rgbhub\lcd\` |

Put **one picture** in it for a still, **one animated file** (GIF, animated
WebP or PNG) for an animation, or **several LCD-ready JPEG frames**
(480x480, at most 60,000 bytes each, named in order) for a pre-rendered
animation; a text file `frame_ms` sets the time per frame. Empty the folder
to go back to the default. On the desktop, restart the service afterwards:
`systemctl --user restart rgbhub`. On Windows the folder is looked at
again every few seconds; no restart is needed.

## How the code is organised

The code is meant to be easy to change and to extend to other systems.

| Folder | What lives there |
|---|---|
| `service/rgbhub/core/` | The hub (`hub.py`) and what lights and the LCD should do. No operating-system or hardware-library imports. `device.py` is what a device must offer, `interfaces.py` what else the core needs handed in; `settings_schema.py` lists every setting. |
| `service/rgbhub/devices/` | One folder per device (`omen_pc/`, `iqunix_keyboard/`) with its protocol code and model facts: zone and effect names, hidden zones, colour correction. |
| `service/rgbhub/platforms/` | One folder per system (`gnome/`, `batocera/`, `windows/`) that picks platform code and wires in that system's settings storage, timers and "what is in focus"; `linux/` holds what the two Linux ones share: `hidraw.py` (USB devices), `ram.py` (RAM writes), `openrgb.py` (case lights), `glib_runtime.py` (event loop). |
| `service/rgbhub/updater/` | The self-updater. Standard library only. |
| `frontends/` | What you see and touch: the GNOME Shell extension, the Batocera service switch and game hook, the settings web page and the Windows notes. A front end only reads and writes settings. |
| `helper/` | The small helpers with full rights for RAM lights (desktop Linux, Windows). |
| `install/`, `systemd/` | Set-up scripts and service definitions. |

**Adding a device**: one new folder under `service/rgbhub/devices/` with its
protocol code and a `device.py` that fits `core/device.py` (most of the
lights work is `core/light_group.py`), whose `__init__.py` has `DEVICE_ID`
and `build(platform)`. Then one line in `DEVICES` in
`devices/__init__.py` and its id in `DEVICE_IDS` in
`core/settings_schema.py`. Every system builds its devices from that list,
and the settings windows build their tabs from what each device reports, so
nothing else changes. The one exception is EmuBox's Batocera menu, which
has a fixed list and needs a one-line edit too.

**Adding a system**: write `platforms/<name>/` with a `main()` that fills
in the interfaces in `core/interfaces.py` (`HidBus`/`HidPort` for USB,
`RamBus` for RAM lights, `Runtime` for timers and the event loop,
`Settings` for storage), a source for "what is in focus" and an icon
loader, and builds the `Hub` and its devices. List it in
`service/rgbhub/__main__.py`. The core does not change. See "Platforms
and interfaces" in [docs/design.md](docs/design.md).

**Adding a front end**: anything that can write the settings in
`core/settings_schema.py` and read the `status` report is a front end.

On the GNOME desktop, first run `bash install/install-user.sh --dev`: the
service then runs straight from your working copy (its `.venv`) and the
updater pauses; `bash install/install-user.sh --release` goes back to
following `main`. While in `--dev`:
after editing the service: `systemctl --user restart rgbhub`;
after editing the settings list: `bash install/install-user.sh --dev`;
after editing the root helper: `sudo bash install/setup.sh`;
after editing the extension's menu button: log out and back in.

To run the tests:

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e 'service[dev,gnome]'
.venv/bin/pytest service && .venv/bin/ruff check service
```

## Troubleshooting

```sh
journalctl --user -u rgbhub -e           # what the service is doing
journalctl --user -u openrgb-server -e    # what OpenRGB is doing
journalctl -u rgbhub-ram -e              # what the RAM helper is doing
systemctl --user status rgbhub openrgb-server

# What the service currently sees:
gsettings --schemadir ~/.local/share/gnome-shell/extensions/rgbhub@konfus-dev.github.io/schemas \
    get org.gnome.shell.extensions.rgbhub status
```

- **LCD shows the OMEN logo:** no image chosen, or the service is not
  running.
- **Lights do not react:** OpenRGB is not running. If it has just been
  restarted it can need a minute or two before it accepts connections.
- **RAM lights do not react:** check `systemctl status rgbhub-ram.socket`
  and that `lsmod | grep acpi_call` shows the module.
- **Auto mode does nothing:** it needs the extension, so log out and back in
  after installing. Apps without an icon in your icon theme get the same
  look as nothing in focus (the wallpaper); this project's own settings
  window keeps your normal look, so you see what you are choosing.
- **After a full power-off** the LCD shows the logo, and the RAM its
  rainbow, until you log in. The keyboard keeps the Default look.
- **Keyboard not found:** `sudo bash install/setup.sh` gives you access to
  its lighting interface; unplug and replug it once afterwards.
- **RAM temperature sensors stopped reporting:** something talked to the RAM
  directly (for example a full OpenRGB scan). Reboot to recover.

## Uninstall

On Windows see the [Windows section](#windows) above. On Ubuntu:

```sh
gnome-extensions disable rgbhub@konfus-dev.github.io
systemctl --user disable --now rgbhub openrgb-server rgbhub-update.timer
rm ~/.config/systemd/user/rgbhub.service ~/.config/systemd/user/openrgb-server.service \
   ~/.config/systemd/user/rgbhub-update.service ~/.config/systemd/user/rgbhub-update.timer
rm ~/.local/share/gnome-shell/extensions/rgbhub@konfus-dev.github.io
rm ~/.local/share/applications/io.github.konfus_dev.RGBHub.desktop \
   ~/.local/share/icons/hicolor/scalable/apps/io.github.konfus_dev.RGBHub.svg
rm -r ~/.local/share/rgbhub
rm -r ~/.config/rgbhub
sudo systemctl disable --now rgbhub-ram.socket rgbhub-ram.service
sudo rm /etc/systemd/system/rgbhub-ram.socket /etc/systemd/system/rgbhub-ram.service
sudo rm -r /usr/local/lib/rgbhub
sudo rm -r /etc/systemd/system/rgbhub-ram.service.d
sudo rm /etc/udev/rules.d/70-rgb-hub.rules /etc/modules-load.d/i2c-dev.conf \
        /etc/modules-load.d/rgbhub-acpi-call.conf
```

## Credits

- The LCD protocol was worked out by the author of
  [omen-led-lcd](https://github.com/Xedecimal/omen-led-lcd). This project
  uses the facts in its protocol notes and none of its code.
- [OpenRGB](https://openrgb.org) drives the case lights, and its Kingston
  Fury driver documents the RAM lighting registers.

## Licence

GPL-3.0-or-later. See `LICENSE`.
