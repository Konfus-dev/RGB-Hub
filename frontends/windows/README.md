# RGB Hub on Windows

RGB Hub replaces HP's OMEN Gaming Hub on Windows. It drives two devices,
each of which can follow the app you are using or not: **OMEN-PC** (cooler
LCD, case lights, RAM) and **IQUNIX-KEYBOARD**. How it is installed, from
Ubuntu, is in the [main README](../../README.md#windows).

## What you see

It starts by itself at every sign-in and lives in the tray (the icons by the
clock; it may be under the up-arrow). The menu:

- **Settings** (also a left click on the icon) opens the settings page.
- **Auto mode** is ticked when every device follows the app in front.
  Click it to turn auto on or off for all of them.
- **Approve update...** appears only while an update waits for your
  approval (see below).
- **Quit** shows the Default look on every device and stops RGB Hub. It
  does not start again until you sign in again.

### The settings page

A small window (Microsoft Edge in app mode; the default browser if Edge is
not found) with an Auto tab and a tab per device, like the one on the
Ubuntu desktop. The page is served only to this PC, and its address carries
a secret, so other programs and other people on the network cannot use it.

- **Auto** has one switch for all devices, a switch per device, and which
  app the lights are following now.
- **A device's tab** has lights on or off, brightness, effect, speed, the
  dimmest and brightest of a breathing effect, a colour for each light, and
  for the OMEN-PC the LCD: pick a picture (it is copied to
  `%APPDATA%\rgbhub\pictures\`), how it fits, its brightness, and recent
  pictures.
- **Save as Default / Load Default** keep a look to come back to. Whenever
  RGB Hub stops (Quit, sign-out, shutdown) every device is put back to the
  Default.

### Auto mode

With auto mode on, the app in front decides the look: the LCD shows its
icon and the lights and keyboard take the icon's main colour. The icon is
read from the program file, or from the logos of a Store app. With nothing
in front, the LCD shows what is in `%APPDATA%\rgbhub\lcd\` if anything is
there (one picture, an animated file, or JPEG frames; see the main README),
otherwise your current wallpaper, otherwise a Windows logo. RGB Hub only
reads the wallpaper; use Windows' own Slideshow setting to change it. Its own
windows and the tray menu do not count as an app.

The LCD and lights go dark when the PC sleeps and come back when it wakes.

## Where things are

| What | Where |
|---|---|
| Your settings and the LCD folder | `%APPDATA%\rgbhub\` |
| Logs: the hub (`hub.log`), the updater (`updater.log`), setup (`setup.log`), the RAM helper (`ram-helper.log`) | `%LOCALAPPDATA%\rgbhub\` |
| The program (one folder per version) | `%LOCALAPPDATA%\Programs\RGB-Hub\versions\` |
| The RAM helper and setup scripts (administrators only) | `C:\Program Files\RGB-Hub\` |

If something does not work, start with `hub.log`. Settings are copied from
Ubuntu once, at install; after that Windows and Ubuntu each keep their own.

## Updates

The "RGB-Hub update" task looks at `main` on GitHub at sign-in and then every
hour. A newer version is installed beside the old one, checked, and then the
hub restarts (the devices show the Default look for a moment). If the new
version does not start, the old one comes back and a notification tells you.
Your settings are never changed.

The RAM helper and the setup scripts run with full rights, so they are never
updated silently. If a new version changes them, everything else is updated
and **Approve update...** appears in the tray menu. Choosing it asks for
administrator rights once (UAC), checks those files against GitHub's own
copy, and installs them. Until you approve, the old helper keeps working.

## Uninstall

Open PowerShell as administrator and run:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\RGB-Hub\install\windows\uninstall.ps1"
```

It removes the three scheduled tasks (RGB-Hub, RGB-Hub update, RGB-Hub RAM),
the program folders and the logs, and sets HP's OMEN and analytics services
back to how they were before setup. Your settings stay in
`%APPDATA%\rgbhub\` unless you add `-RemoveSettings`. OMEN Gaming Hub itself
comes back only if you install it again from the Microsoft Store.
