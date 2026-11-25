#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Per-user install for RGB-Hub. Run WITHOUT sudo:
#     bash install/install-user.sh [--release | --dev] [--allow-detector-scan]
# Safe to run again.
#
# --release  (the default) install the latest commit of this repo's `main`
#            branch (changes not committed to main are left out) into
#            ~/.local/share/rgbhub/versions/, and switch on the updater,
#            which keeps it on the newest `main` from GitHub.
# --dev      run straight from this working copy instead (its .venv, an
#            editable install): after editing the code you only need
#            systemctl --user restart rgbhub. The updater pauses until
#            --release is run again.
# --allow-detector-scan  agree in advance to OpenRGB's one full hardware
#                        scan, if it is needed (otherwise you are asked;
#                        see install/openrgb-detector-scan.sh)
set -euo pipefail

usage="Usage: bash install/install-user.sh [--release | --dev] [--allow-detector-scan]"
allow_scan=
mode=release
for arg in "$@"; do
    case "$arg" in
        --allow-detector-scan) allow_scan=--yes ;;
        --release) mode=release ;;
        --dev) mode=dev ;;
        *)
            echo "$usage" >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "Do not run this with sudo. Run: bash install/install-user.sh" >&2
    exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=install/never-backwards.sh
source "$repo/install/never-backwards.sh"
uuid="rgbhub@konfus-dev.github.io"
root="$HOME/.local/share/rgbhub"
current="$root/current"           # -> versions/<commit>, or the working copy (--dev)
py="$current/.venv/bin/python"
extensions="$HOME/.local/share/gnome-shell/extensions"
units="$HOME/.config/systemd/user"
app_id=io.github.konfus_dev.RGBHub
applications="$HOME/.local/share/applications"
icons="$HOME/.local/share/icons"

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

mkdir -p "$root"
if [[ -e "$current" && ! -L "$current" ]]; then
    echo "ERROR: $current exists and is not a link made by this script. Remove it first." >&2
    exit 1
fi
if [[ $mode == release ]]; then
    if ! sha="$(git -C "$repo" rev-parse --verify --quiet 'main^{commit}')"; then
        echo "ERROR: $repo has no 'main' branch to install from." >&2
        exit 1
    fi
    installed=
    if [[ -L $current && ! -e $root/dev ]]; then
        installed="$(basename "$(readlink -f "$current")")"
    fi
    if behind_installed "$repo" "$installed" "$sha"; then
        :  # keep what is installed
    else
        echo "==> Installing main (${sha:0:7}) into $root/versions"
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        top="rgbhub-$sha"
        git -C "$repo" archive --format=zip --prefix="$top/" -o "$tmp/rgbhub.zip" "$sha"
        # The snapshot installs itself (its own code, run from inside the zip): it
        # unpacks into versions/<commit>, makes its Python environment (with
        # Ubuntu's own Pillow and GTK bindings), checks it, points `current` at it
        # and records it for the updater.
        boot='import sys; sys.path.insert(0, sys.argv.pop(1)); from rgbhub.updater.install import main; sys.exit(main(sys.argv[1:]))'
        was_dev=; [[ -e "$root/dev" ]] && was_dev=1
        rm -f "$root/dev"
        if ! python3 -I -c "$boot" "$tmp/rgbhub.zip/$top/service" --platform gnome \
            --root "$root" --zip "$tmp/rgbhub.zip" --sha "$sha"; then
            [[ -n $was_dev ]] && touch "$root/dev"
            echo "ERROR: could not install main (${sha:0:7}); nothing was switched." >&2
            exit 1
        fi
    fi
else
    echo "==> Running from the working copy $repo (updates paused)"
    touch "$root/dev"
    # --system-site-packages lets it use Ubuntu's own Pillow and GTK bindings.
    # Rebuilt when it is missing or broken (an Ubuntu upgrade that brings a
    # new Python leaves the old environment unable to run).
    # Also rebuilt when it was made without the system packages.
    if ! "$repo/.venv/bin/python" -c "import pip" 2>/dev/null ||
        ! grep -Eqs '^include-system-site-packages *= *true' "$repo/.venv/pyvenv.cfg"; then
        python3 -m venv --clear --system-site-packages "$repo/.venv"
    fi
    "$repo/.venv/bin/pip" install --quiet --editable "$repo/service[gnome]"
    ln -sfn "$repo" "$root/current.new"
    mv -T "$root/current.new" "$current"
fi
# Everything below comes from the active version (in --dev, the working copy).
src="$(readlink -f "$current")"

echo "==> GNOME Shell extension (reached through $current, so it follows updates at the next login)"
mkdir -p "$extensions"
if [[ -e "$extensions/$uuid" && ! -L "$extensions/$uuid" ]]; then
    echo "ERROR: $extensions/$uuid exists and is not a link made by this script. Remove it first." >&2
    exit 1
fi
ln -sfn "$current/frontends/gnome-shell/$uuid" "$extensions/$uuid"
glib-compile-schemas --strict "$current/frontends/gnome-shell/$uuid/schemas"

echo "==> App launcher ('RGB Hub' in Activities and the app grid)"
mkdir -p "$applications" "$icons/hicolor/scalable/apps"
install -m 0644 "$current/frontends/gnome-shell/rgbhub.desktop" "$applications/$app_id.desktop"
install -m 0644 "$current/frontends/gnome-shell/icons/hicolor/scalable/apps/$app_id.svg" \
    "$icons/hicolor/scalable/apps/$app_id.svg"
# Only caches; the launcher works without them.
update-desktop-database "$applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$icons/hicolor" 2>/dev/null || true

echo "==> OpenRGB limited to the case-light board"
orgb="$HOME/.config/rgbhub/openrgb"
known="$("$py" -m rgbhub.platforms.linux.openrgb "$orgb" "$HOME/.config/OpenRGB/OpenRGB.json")"
# A different OpenRGB than last time may have detectors our list does not
# name, and it runs any it does not find there. Learn its list afresh.
rescan=0
if [[ -e "$orgb/openrgb-program" ]] && ! "$py" -m rgbhub.platforms.linux.openrgb --check-program "$orgb" 2>/dev/null; then
    echo "    OpenRGB has been updated; checking its hardware detectors again."
    rescan=1
fi
if (( known < 100 || rescan )); then
    # OpenRGB only writes down the names of its detectors after one full
    # hardware scan. Do that scan once (only with the owner's say-so), then
    # switch the detectors off.
    systemctl --user stop openrgb-server.service 2>/dev/null || true
    if ! bash "$src/install/openrgb-detector-scan.sh" "$orgb" $allow_scan; then
        echo "ERROR: OpenRGB is not set up, so the case lights stay off. Run this script" >&2
        echo "       again and type 'scan', or add --allow-detector-scan." >&2
        exit 1
    fi
    known="$("$py" -m rgbhub.platforms.linux.openrgb "$orgb")"
    if (( known < 100 )); then
        # Starting OpenRGB without that list would let it probe everything,
        # RAM included, at every login. Better to stop here.
        echo "ERROR: could not get OpenRGB's detector list (is openrgb installed?)." >&2
        echo "       Run 'sudo bash install/setup.sh' first, then this script again." >&2
        exit 1
    fi
fi
"$py" -m rgbhub.platforms.linux.openrgb --record-program "$orgb"

# The RAM helper runs as root from its own copy, which only setup.sh updates.
installed_helper=/usr/local/lib/rgbhub/rgbhub_ram_helper.py
if [[ -e "$installed_helper" ]] && ! cmp -s "$src/helper/rgbhub_ram_helper.py" "$installed_helper"; then
    echo "NOTE: the RAM helper has changed. To update it: pkexec /usr/bin/bash $src/install/setup.sh --no-apt"
fi

echo "==> Background services"
mkdir -p "$units"
install -m 0644 "$src/systemd/openrgb-server.service" "$src/systemd/rgbhub.service" \
    "$src/systemd/rgbhub-update.service" "$src/systemd/rgbhub-update.timer" "$units/"
systemctl --user daemon-reload
systemctl --user enable openrgb-server.service rgbhub.service
# No update run while this script stops and starts the hub (it could take the
# restart for a crash and mark a good version bad); switched on at the end.
systemctl --user disable --now rgbhub-update.timer 2>/dev/null || true
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
python3 "$src/install/enable-gnome-extension.py" "$uuid" --remove omenrgb@konfus-dev.github.io || true

# The environment from before versions (editable install of the repo).
rm -rf "$root/venv"

if [[ $mode == release ]]; then
    echo "==> Updates: looking for a newer main on GitHub every hour"
    systemctl --user enable --now rgbhub-update.timer
fi

echo
echo "Done. If this is the first install (or the first since the rename"
echo "from OmenRGB-Controller): log out and back in once for the menu button."
