#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Puts RGB Hub onto a Batocera drive from another Linux PC.
#
#     sudo bash install/install-batocera.sh /path/where/SHARE/is/mounted
#
# (SHARE is the Batocera data partition; inside Batocera it is /userdata.)
# Needs root because Batocera's system folder is owned by root.
# Safe to run again; your settings.json is never overwritten.
set -euo pipefail

share="${1:-}"
if [[ -z "$share" || ! -d "$share/system" ]]; then
    echo "Usage: sudo bash install/install-batocera.sh <mounted SHARE partition>" >&2
    echo "       (the folder that contains 'system', 'roms' and 'bios')" >&2
    exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=install/never-backwards.sh
source "$repo/install/never-backwards.sh"
app="$share/system/rgb-hub"

# Puts a `service` folder on Python's path and runs the updater's installer
# from it (rgbhub/updater/install.py), which records the version the same
# way the updater does.
boot='import sys; sys.path.insert(0, sys.argv.pop(1)); from rgbhub.updater.install import main; sys.exit(main(sys.argv[1:]))'
# Run with sudo, git would refuse a checkout owned by someone else.
git_() { git -c safe.directory="$repo" -C "$repo" "$@"; }

echo "==> Program files -> $app"
mkdir -p "$app"
if [[ -e "$repo/.git" ]]; then
    # The latest commit of `main` (as install-user.sh does): changes not
    # committed to main are not installed. The updater then keeps it on the
    # newest main from GitHub.
    if ! sha="$(git_ rev-parse --verify --quiet 'main^{commit}')"; then
        echo "ERROR: no 'main' branch to install from in $repo (or git is missing)." >&2
        exit 1
    fi
    installed="$(head -c 64 "$app/current.txt" 2>/dev/null | tr -d '\r\n ')" || installed=
    if [[ -n $installed && ! -d $app/versions/$installed ]]; then installed=; fi
    if ! behind_installed "$repo" "$installed" "$sha"; then
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        top="rgb-hub-$sha"
        git_ archive --format=zip --prefix="$top/" -o "$tmp/rgb-hub.zip" "$sha"
        # The snapshot installs itself (its own code, run from inside the zip).
        python3 -I -c "$boot" "$tmp/rgb-hub.zip/$top/service" --platform batocera \
            --root "$app" --zip "$tmp/rgb-hub.zip" --sha "$sha" --no-check
    fi
else
    # A downloaded copy without git history cannot tell which commit it is,
    # and a version without one could never be updated. Install the newest
    # main from GitHub instead (needs the network; fails clearly without).
    echo "    Not a git checkout: installing the newest main from GitHub."
    if ! python3 -I -c "$boot" "$repo/service" --platform batocera \
        --root "$app" --from-github --no-check; then
        echo "ERROR: could not install RGB Hub from GitHub (see above; is this PC online?)." >&2
        echo "       Nothing was changed. Try again online, or use a git checkout." >&2
        exit 1
    fi
fi
sha="$(cat "$app/current.txt")"
version="$app/versions/$sha"
# The layout from before updates existed, and from a first draft of them.
rm -rf "$app/service" "$app/helper" "$app/modules" "$app/versions/unversioned"

# Everything below comes from the installed version, not from this checkout.
frontend="$version/frontends/batocera"

echo "==> Service switch (System Settings > Services > RGB_HUB)"
mkdir -p "$share/system/services"
install -m 0755 "$frontend/services/rgb_hub" "$share/system/services/rgb_hub"

echo "==> Game hook"
mkdir -p "$share/system/scripts"
install -m 0755 "$frontend/scripts/rgb-hub.sh" "$share/system/scripts/rgb-hub.sh"

echo "==> Sleep and wake hooks"
hooks="$share/system/configs/emulationstation/scripts"
mkdir -p "$hooks/suspend" "$hooks/resume"
install -m 0755 "$frontend/power/suspend/rgb-hub.sh" "$hooks/suspend/rgb-hub.sh"
install -m 0755 "$frontend/power/resume/rgb-hub.sh" "$hooks/resume/rgb-hub.sh"

old="$share/system/omen-lights"
if [[ -e "$share/system/services/omen_lights" || -d "$old" ]]; then
    echo "==> Removing the old OMEN Lights program (its settings move on first start)"
    rm -f "$share/system/services/omen_lights" "$share/system/scripts/omen-lights.sh"
    rm -f "$hooks/suspend/omen-lights.sh" "$hooks/resume/omen-lights.sh"
    rm -rf "$old"
    # Batocera remembers enabled services by name in batocera.conf.
    conf="$share/system/batocera.conf"
    if [[ -f "$conf" ]] && grep -q 'omen_lights' "$conf"; then
        sed -i 's/\bomen_lights\b/rgb_hub/g' "$conf"
    fi
fi

echo "==> Settings file (kept if it already exists)"
settings="$share/system/configs/rgb-hub/settings.json"
# An old install's folder is carried over by the program itself on first
# start; only create a fresh one when there is nothing to carry over.
if [[ ! -d "$share/system/configs/omen-lights" ]]; then
    mkdir -p "$share/system/configs/rgb-hub"
    [[ -f "$settings" ]] || printf '{\n}\n' > "$settings"
fi
[[ -d "$share/system/configs/rgb-hub" ]] &&
    cp "$frontend/README.md" "$share/system/configs/rgb-hub/README.md"

echo
echo "Done. Boot Batocera, open Main menu > System Settings > Services,"
echo "and switch RGB_HUB on."
