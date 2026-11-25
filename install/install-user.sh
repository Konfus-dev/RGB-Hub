#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Per-user install for RGB-Hub. Run WITHOUT sudo:
#     bash install/install-user.sh [--allow-detector-scan]
# Safe to run again after pulling changes or editing the settings schema.
#
# --allow-detector-scan  agree in advance to OpenRGB's one full hardware
#                        scan, if it is needed (otherwise you are asked;
#                        see install/openrgb-detector-scan.sh)
set -euo pipefail

allow_scan=
for arg in "$@"; do
    case "$arg" in
        --allow-detector-scan) allow_scan=--yes ;;
        *)
            echo "Usage: bash install/install-user.sh [--allow-detector-scan]" >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "Do not run this with sudo. Run: bash install/install-user.sh" >&2
    exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uuid="rgbhub@konfus-dev.github.io"
venv="$HOME/.local/share/rgbhub/venv"
extensions="$HOME/.local/share/gnome-shell/extensions"
units="$HOME/.config/systemd/user"

# --- From before RGB-Hub (OmenRGB-Controller) ----------------------------------
# Its settings and files are taken over by the hub itself on first start.
if [[ -e "$units/omenrgb.service" || -L "$extensions/omenrgb@konfus-dev.github.io" ]]; then
    echo "==> Removing the old OmenRGB-Controller service and extension"
    systemctl --user disable --now omenrgb.service 2>/dev/null || true
    rm -f "$units/omenrgb.service"
    gnome-extensions disable omenrgb@konfus-dev.github.io 2>/dev/null || true
    rm -f "$extensions/omenrgb@konfus-dev.github.io"
    rm -rf "$HOME/.local/share/omenrgb"
    # OpenRGB's folder moves with the rest of ~/.config/omenrgb.
    systemctl --user stop openrgb-server.service 2>/dev/null || true
    if [[ -d "$HOME/.config/omenrgb" && ! -e "$HOME/.config/rgbhub" ]]; then
        mv "$HOME/.config/omenrgb" "$HOME/.config/rgbhub"
    fi
    systemctl --user daemon-reload
fi

echo "==> Python environment in $venv"
# --system-site-packages lets it use Ubuntu's own Pillow and GTK bindings.
# Rebuilt when it is missing or broken; an Ubuntu upgrade that brings a new
# Python leaves the old environment unable to run.
if ! "$venv/bin/python" -c "import pip" 2>/dev/null; then
    python3 -m venv --clear --system-site-packages "$venv"
fi
# Editable install: the service runs straight from this repo, so after
# editing the code you only need: systemctl --user restart rgbhub
"$venv/bin/pip" install --quiet --editable "$repo/service"

echo "==> GNOME Shell extension (linked, not copied)"
mkdir -p "$extensions"
if [[ -e "$extensions/$uuid" && ! -L "$extensions/$uuid" ]]; then
    echo "ERROR: $extensions/$uuid exists and is not a link made by this script. Remove it first." >&2
    exit 1
fi
ln -sfn "$repo/frontends/gnome-shell/$uuid" "$extensions/$uuid"
glib-compile-schemas --strict "$repo/frontends/gnome-shell/$uuid/schemas"

echo "==> OpenRGB limited to the case-light board"
orgb="$HOME/.config/rgbhub/openrgb"
known="$("$venv/bin/python" -m rgbhub.platforms.linux.openrgb "$orgb" "$HOME/.config/OpenRGB/OpenRGB.json")"
# A different OpenRGB than last time may have detectors our list does not
# name, and it runs any it does not find there. Learn its list afresh.
rescan=0
if [[ -e "$orgb/openrgb-program" ]] && ! "$venv/bin/python" -m rgbhub.platforms.linux.openrgb --check-program "$orgb" 2>/dev/null; then
    echo "    OpenRGB has been updated; checking its hardware detectors again."
    rescan=1
fi
if (( known < 100 || rescan )); then
    # OpenRGB only writes down the names of its detectors after one full
    # hardware scan. Do that scan once (only with the owner's say-so), then
    # switch the detectors off.
    systemctl --user stop openrgb-server.service 2>/dev/null || true
    if ! bash "$repo/install/openrgb-detector-scan.sh" "$orgb" $allow_scan; then
        echo "ERROR: OpenRGB is not set up, so the case lights stay off. Run this script" >&2
        echo "       again and type 'scan', or add --allow-detector-scan." >&2
        exit 1
    fi
    known="$("$venv/bin/python" -m rgbhub.platforms.linux.openrgb "$orgb")"
    if (( known < 100 )); then
        # Starting OpenRGB without that list would let it probe everything,
        # RAM included, at every login. Better to stop here.
        echo "ERROR: could not get OpenRGB's detector list (is openrgb installed?)." >&2
        echo "       Run 'sudo bash install/setup.sh' first, then this script again." >&2
        exit 1
    fi
fi
"$venv/bin/python" -m rgbhub.platforms.linux.openrgb --record-program "$orgb"

# The RAM helper runs as root from its own copy, which only setup.sh updates.
installed_helper=/usr/local/lib/rgbhub/rgbhub_ram_helper.py
if [[ -e "$installed_helper" ]] && ! cmp -s "$repo/helper/rgbhub_ram_helper.py" "$installed_helper"; then
    echo "NOTE: the RAM helper has changed. To update it: sudo bash install/setup.sh"
fi

echo "==> Background services"
mkdir -p "$units"
install -m 0644 "$repo/systemd/openrgb-server.service" "$repo/systemd/rgbhub.service" "$units/"
systemctl --user daemon-reload
systemctl --user enable openrgb-server.service rgbhub.service
# Stop our service first, so it lets go of OpenRGB before OpenRGB restarts.
# Otherwise OpenRGB cannot reopen its port for a minute or two.
systemctl --user stop rgbhub.service
# "|| true": even if OpenRGB will not start, our service must come back up
# (the LCD and RAM do not need OpenRGB).
systemctl --user restart openrgb-server.service || true
systemctl --user start rgbhub.service

# Switch the extension on. `gnome-extensions enable` only works once GNOME
# Shell has loaded the extension (after a log-in), so also put it on the
# list of enabled extensions directly; GNOME reads that list at log-in.
# The old OmenRGB id comes off the list, so it is never enabled twice.
gnome-extensions enable "$uuid" 2>/dev/null || true
python3 "$repo/install/enable-gnome-extension.py" "$uuid" --remove omenrgb@konfus-dev.github.io || true

echo
echo "Done. If this is the first install (or the first since the rename"
echo "from OmenRGB-Controller): log out and back in once for the menu button."
