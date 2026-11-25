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
app="$share/system/rgb-hub"

# Runs the installing code from the snapshot itself (inside the zip), so what
# installs is exactly what gets installed.
boot='import sys; sys.path.insert(0, sys.argv.pop(1)); from rgbhub.updater.install import main; sys.exit(main(sys.argv[1:]))'
# Run with sudo, git would refuse a checkout owned by someone else.
git_() { git -c safe.directory="$repo" -C "$repo" "$@"; }

echo "==> Program files -> $app"
mkdir -p "$app"
if sha="$(git_ rev-parse --verify --quiet 'HEAD^{commit}' 2>/dev/null)"; then
    # The committed state only: changes not yet committed are not installed.
    # The updater then keeps it on the newest main from GitHub.
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    top="rgb-hub-$sha"
    git_ archive --format=zip --prefix="$top/" -o "$tmp/rgb-hub.zip" "$sha"
    python3 -I -c "$boot" "$tmp/rgb-hub.zip/$top/service" --platform batocera \
        --root "$app" --zip "$tmp/rgb-hub.zip" --sha "$sha" --no-check
    rm -rf "$app/versions/unversioned"
else
    # Not a git checkout (a downloaded copy): install it as it is. Without a
    # commit id the updater cannot tell newer from older, so it leaves it alone.
    echo "    Not a git checkout: installing without automatic updates."
    version="$app/versions/unversioned"
    rm -rf "$version.new"
    mkdir -p "$version.new"
    cp -r "$repo/service" "$repo/helper" "$version.new/"
    mkdir -p "$version.new/frontends"
    cp -r "$repo/frontends/batocera" "$version.new/frontends/"
    find "$version.new" -name __pycache__ -type d -exec rm -rf {} +
    rm -rf "$version"
    mv "$version.new" "$version"
    printf 'unversioned\n' >"$app/current.txt.new"
    mv -f "$app/current.txt.new" "$app/current.txt"
fi
# The layout from before updates existed.
rm -rf "$app/service" "$app/helper" "$app/modules"

echo "==> Service switch (System Settings > Services > RGB_HUB)"
mkdir -p "$share/system/services"
install -m 0755 "$repo/frontends/batocera/services/rgb_hub" "$share/system/services/rgb_hub"

echo "==> Game hook"
mkdir -p "$share/system/scripts"
install -m 0755 "$repo/frontends/batocera/scripts/rgb-hub.sh" "$share/system/scripts/rgb-hub.sh"

echo "==> Sleep and wake hooks"
hooks="$share/system/configs/emulationstation/scripts"
mkdir -p "$hooks/suspend" "$hooks/resume"
install -m 0755 "$repo/frontends/batocera/power/suspend/rgb-hub.sh" "$hooks/suspend/rgb-hub.sh"
install -m 0755 "$repo/frontends/batocera/power/resume/rgb-hub.sh" "$hooks/resume/rgb-hub.sh"

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
    cp "$repo/frontends/batocera/README.md" "$share/system/configs/rgb-hub/README.md"

echo
echo "Done. Boot Batocera, open Main menu > System Settings > Services,"
echo "and switch RGB_HUB on."
