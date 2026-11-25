# RGB Hub on Batocera

Switch it on or off under **Main menu > System Settings > Services >
RGB_HUB**.

It drives two devices, each of which can follow the game or not:
**OMEN-PC** (cooler LCD, case lights, RAM) and **IQUNIX-KEYBOARD**.

While it is on:

- **In the menus** the PC matches your menu theme. If the theme has an
  `rgb-hub/` folder, the LCD shows what is in it (an animation or a
  picture); otherwise it shows the theme's menu background. The lights take
  that picture's main colour.
- **While a game runs** the LCD shows that game's artwork (its logo if one
  was scraped, otherwise its box or a screenshot) and the lights take the
  artwork's main colour. Games without artwork keep the menu look. The
  keyboard takes the same colour.
- **When it is switched off, or Batocera shuts down,** every device is put
  back to the **Default look**, so whatever starts next (Windows, say) does
  not show the last game. The keyboard stores it in itself; the cooler's
  screen cannot hold a picture and goes back to the OMEN logo. Save the
  Default from EmuBox's menu (Game Settings > RGB HUB), or by hand with
  `"default-action": "save"` in the settings file.

## Where it lives, and updates

The program is installed in `/userdata/system/rgb-hub/`, one folder per
version in `versions/<commit>/`; `current.txt` names the one that runs. The
service script starts that version and starts it again if it stops. At start
and about every hour it looks for a newer `main` on GitHub (a newer one only;
never an older one) and, when no game is running, installs it beside the
current one, checks it and switches over. If the new version does not start
within a minute, the old one comes back. Your `settings.json` is never
touched. Everything on Batocera runs as root, so an update replaces the
service script and the game and sleep hooks too, with no approval step.
What the updater did is in `/userdata/system/logs/rgb-hub.log`.

## Settings

Edit `settings.json` in this folder (from Batocera it is
`/userdata/system/configs/rgb-hub/settings.json`). Changes apply as soon
as the file is saved. Only list what you want to change, for example:

```json
{
    "device-brightness": {"OMEN-PC": 60, "IQUNIX-KEYBOARD": 40},
    "auto-devices": {"OMEN-PC": true, "IQUNIX-KEYBOARD": false},
    "lcd-brightness": 70,
    "auto-idle-image": "/userdata/system/configs/rgb-hub/menu.png",
    "auto-idle-colour": "#00AA00",
    "device-modes": {"HP Omen 30L": "Breathing"}
}
```

| Setting | Meaning | Default |
|---|---|---|
| `auto-devices` | Which devices follow the running game | all |
| `auto-lights` / `auto-lcd` | OMEN-PC: let the lights / the LCD follow | `true` |
| `auto-idle-image` | Picture shown in the menus | the theme's background |
| `auto-idle-colour` | Light colour in the menus, `"#RRGGBB"` | from the picture |
| `device-enabled` | Lights on or off, per device | on |
| `device-brightness` | Light brightness per device, percent | `100` |
| `lcd-brightness` | Cooler screen brightness, percent | `80` |
| `device-modes` | Effect per light: `HP Omen 30L` and `RAM`: `Static` or `Breathing` (RAM also `Rainbow`, which ignores colours); `IQUNIX MG65`: `Static`, `Wave`, `Breathe`, `Rain` and others (see `service/rgbhub/devices/iqunix_keyboard/mg65.py`) | `Static` |
| `zone-colours` | Colour per zone, e.g. `"IQUNIX MG65/All keys": "#800080"` | white |
| `lcd-image` | Picture shown when auto mode is off | none |
| `default-look` | The Default look (written by `"default-action": "save"`) | |

Settings from OMEN Lights (`/userdata/system/configs/omen-lights`) are
moved here the first time RGB Hub runs.

## If something is wrong

The log is `/userdata/system/logs/rgb-hub.log`; the previous run's is
kept beside it as `rgb-hub.log.old`. For a detailed log, create an empty
file `/userdata/system/configs/rgb-hub/debug` and switch the service off
and on. Remove it afterwards: the detailed log grows quickly.

If the program stops by itself it is started again automatically, and a
mistake in `settings.json` is reported in the log while the previous settings
stay in force.

RAM lights need a small kernel module that is built for one exact Batocera
kernel (the one for Batocera 43.1 is included). After a Batocera update the
RAM may go back to its own pattern until a new module is built; see
`frontends/batocera/modules/README.md` in the project.

When the PC sleeps, the LCD and lights are switched off, and they come back
when it wakes.

## The theme's LCD folder

A folder named `rgb-hub` inside a theme's folder decides what the LCD
shows in the menus while that theme is active. Without it, the theme's menu
background is shown. The folder may hold:

- **Several JPEG files: an animation.** They must be LCD-ready (480x480, at
  most 60,000 bytes each) and are played in name order, in a loop
  (`000.jpg`, `001.jpg`, ...). A text file `frame_ms` may give the time per
  frame in milliseconds (default 100).
- **One picture** (PNG, JPEG, WebP, ...): shown as a still.
- **One animated file** (GIF, animated WebP or PNG): played as an animation.

The screen is round, so keep what matters inside the circle.
