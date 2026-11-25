#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install RGB-Hub on this PC's Windows, from Ubuntu (in place of HP's OMEN
# Gaming Hub). Run with pkexec, with absolute paths:
#     pkexec /usr/bin/bash /home/<you>/Source/RGB-Hub/install/install-windows.sh \
#         [--windows-mount /mnt/windows] [--user jercl]
# Safe to run again (a repair): settings already on Windows are never replaced.
#
# --windows-mount  where Ubuntu mounts the Windows partition (default /mnt/windows;
#                  mounted for the install if it is not, and left as it was found)
# --user           the Windows account's folder under C:\Users (default jercl)
#
# What it does:
#  1. Refuses to touch Windows unless it was shut down fully (not hibernated,
#     no Fast Startup, registry closed cleanly) and WinBridge's VM is not using it.
#  2. Builds, as you (not root), in a temporary folder: Windows' embeddable
#     Python (pinned, checksum checked), the Windows libraries as wheels, and a
#     snapshot of this repo's `main` branch (never uncommitted changes).
#     Downloads are kept in ~/.cache/rgbhub-windows/ for the next run.
#  3. Makes the Windows partition writable just for the copy (read-only again
#     afterwards, also if anything fails) and copies:
#       C:\Program Files\RGB-Hub\           Python, the RAM helper, the setup scripts
#       %LOCALAPPDATA%\Programs\RGB-Hub\    Python, launcher.py, versions\<commit>\,
#                                           current.txt, the updater, pip.whl
#       %APPDATA%\rgbhub\                   your Ubuntu settings and pictures (only
#                                           if Windows has no settings yet)
#     and puts RGB-Hub-setup.cmd in your Startup folder. At your next sign-in
#     to Windows it asks once for admin rights (UAC) and finishes the setup
#     (finish-setup.ps1): scheduled tasks, OMEN Gaming Hub uninstalled.
#
# Writing to Windows makes WinBridge start fresh next time (a new iCloud pairing
# code), as bt-share's writes do.
set -euo pipefail

usage="Usage: pkexec /usr/bin/bash $(realpath "$0") [--windows-mount /mnt/windows] [--user jercl]"
mnt=/mnt/windows
user=jercl
while (($#)); do
    case "$1" in
        --windows-mount) mnt="${2:?$usage}"; shift 2 ;;
        --user) user="${2:?$usage}"; shift 2 ;;
        -h | --help) echo "$usage"; exit 0 ;;
        *) echo "$usage" >&2; exit 2 ;;
    esac
done

die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Windows' embeddable Python and pip, pinned in install/windows/pins.json (which
# install/windows/install.ps1 reads too). The checksums are the ones python.org
# and PyPI list for these files.
pins="$(dirname "${BASH_SOURCE[0]}")/windows/pins.json"
# pins: begin
PY_VERSION= PY_URL= PY_SHA256= PY_PTH= PIP_URL= PIP_SHA256=
read -r PY_VERSION PY_URL PY_SHA256 PY_PTH PIP_URL PIP_SHA256 < <(python3 -I -c '
import json, sys
p = json.load(open(sys.argv[1], encoding="ascii"))
print(*(p[a][b] for a, b in (("python", "version"), ("python", "url"), ("python", "sha256"),
      ("python", "pth"), ("pip", "url"), ("pip", "sha256"))))
' "$pins") || true
# pins: end
[[ $PY_SHA256 =~ ^[0-9a-f]{64}$ && $PIP_SHA256 =~ ^[0-9a-f]{64}$ ]] || die "cannot read $pins"
PY_ZIP="${PY_URL##*/}"
MIN_FREE_KB=$((400 * 1024))

if [[ $EUID -ne 0 ]]; then
    die "run this with pkexec: $usage"
fi
owner_uid="${PKEXEC_UID:-${SUDO_UID:-}}"
if [[ -z $owner_uid || $owner_uid == 0 ]]; then
    die "start it with pkexec from your own account (it builds as you, not as root)"
fi
owner="$(getent passwd "$owner_uid" | cut -d: -f1)"
owner_home="$(getent passwd "$owner_uid" | cut -d: -f6)"
[[ -n $owner && -d $owner_home ]] || die "cannot find the account with uid $owner_uid"
if [[ ! $user =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "'$user' does not look like a Windows account folder name"
fi
if [[ ! $mnt =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    die "'$mnt' does not look like a mount point"
fi
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache="$owner_home/.cache/rgbhub-windows"

# Run a command as the owner, with a clean environment.
as_owner() {
    runuser -u "$owner" -- env -i HOME="$owner_home" USER="$owner" LOGNAME="$owner" \
        PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 "$@"
}

# A swap of C:\Program Files\RGB-Hub that stopped half-way leaves the old copy
# as .RGB-Hub.old and none in place: put it back (never delete it then).
restore_program_files() {
    if [[ -n $program && -n $pf_old && -d $pf_old ]]; then
        if [[ ! -e $program ]]; then
            mv "$pf_old" "$program" && echo "    (put the previous C:\Program Files\RGB-Hub back)" >&2
        else
            rm -rf "$pf_old"
        fi
    fi
}

tmp=
mounted_here=
made_rw=
pf_new=
program=
pf_old=
cleanup() {
    local status=$?
    trap - EXIT
    # Under `set -e` one failing step would end the trap early. Every step here
    # must run, above all the one that makes Windows read-only again.
    if [[ -n $pf_new ]]; then
        rm -rf "$pf_new" || true  # a half-built Program Files copy; the old one is untouched
    fi
    restore_program_files || true

    if [[ -n $made_rw ]]; then
        sync || true
        if ! mount -o remount,ro "$mnt"; then
            echo "WARNING: could not make $mnt read-only again; run: sudo mount -o remount,ro $mnt" >&2
        fi
    fi
    if [[ -n $mounted_here ]]; then
        umount "$mnt" || echo "WARNING: could not unmount $mnt" >&2
    fi
    if [[ -n $tmp ]]; then
        rm -rf "$tmp" || true
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- 1. May Windows be written to? (the same checks as bt-share) ---------------

hibernated() {
    # hiberfil.sys starts with "hibr" while Windows is hibernated or was shut
    # down with Fast Startup (which is hibernation too); "wake" or zeros after
    # a full shut down.
    local head
    head="$(head -c 4 "$mnt/hiberfil.sys" 2>/dev/null | tr '[:upper:]' '[:lower:]')" || true
    [[ $head == hibr ]]
}

registry_closed_cleanly() {
    # The SYSTEM hive's two sequence numbers (bytes 4-7 and 8-11) differ while
    # Windows has registry changes still waiting in its logs.
    local hive="$mnt/Windows/System32/config/SYSTEM" magic first second
    magic="$(head -c 4 "$hive" 2>/dev/null)" || return 1
    first="$(od -An -tu4 -j4 -N4 "$hive" | tr -d ' ')"
    second="$(od -An -tu4 -j8 -N4 "$hive" | tr -d ' ')"
    [[ $magic == regf && -n $first && $first == "$second" ]]
}

check_windows() {
    if [[ -e /dev/mapper/winbridge ]]; then
        die "WinBridge's Windows VM is using the Windows partition. Close it, then run this again."
    fi
    if hibernated; then
        die "Windows is hibernated or was shut down with Fast Startup. Start Windows, shut it down fully (hold Shift while clicking Shut down), then run this again."
    fi
    if ! registry_closed_cleanly; then
        die "Windows' registry was not closed cleanly. Start Windows, shut it down fully, then run this again."
    fi
    local free_kb
    free_kb="$(df --output=avail -k "$mnt" | tail -n 1 | tr -d ' ')"
    if ((free_kb < MIN_FREE_KB)); then
        die "too little free space on the Windows partition (need $((MIN_FREE_KB / 1024)) MB)"
    fi
}

if [[ -e /dev/mapper/winbridge ]]; then
    die "WinBridge's Windows VM is using the Windows partition. Close it, then run this again."
fi
if ! mountpoint -q "$mnt"; then
    echo "==> Mounting the Windows partition at $mnt"
    mount "$mnt" || die "could not mount $mnt (it should be in /etc/fstab)"
    mounted_here=1
fi
[[ -f $mnt/Windows/System32/config/SYSTEM ]] || die "$mnt does not look like a Windows partition"
check_windows
win_home="$mnt/Users/$user"
[[ -d $win_home/AppData ]] || die "there is no Windows account folder C:\\Users\\$user (try --user)"

# --- 2. Build, as the owner ------------------------------------------------------

tmp="$(mktemp -d /tmp/rgbhub-windows.XXXXXX)"
chown "$owner:" "$tmp"
as_owner mkdir -p "$cache"

if ! sha="$(as_owner git -C "$repo" rev-parse --verify --quiet 'main^{commit}')"; then
    die "$repo has no 'main' branch to install from"
fi
echo "==> Building RGB-Hub main (${sha:0:7}) for Windows in $tmp"
top="rgbhub-$sha"
as_owner git -C "$repo" archive --format=zip --prefix="$top/" -o "$tmp/rgbhub.zip" "$sha"
as_owner mkdir "$tmp/snap"
as_owner python3 -I -m zipfile -e "$tmp/rgbhub.zip" "$tmp/snap"
snap="$tmp/snap/$top"

echo "    Windows' Python $PY_VERSION"
if [[ ! -f $cache/$PY_ZIP ]]; then
    as_owner curl -fsSL --proto '=https' -o "$cache/$PY_ZIP.part" "$PY_URL" ||
        die "could not download $PY_URL"
    as_owner mv "$cache/$PY_ZIP.part" "$cache/$PY_ZIP"
fi
if ! echo "$PY_SHA256  $cache/$PY_ZIP" | sha256sum --check --quiet --status; then
    rm -f "$cache/$PY_ZIP"
    die "$PY_ZIP is not the file python.org published (checksum differs); it was deleted, run this again"
fi
as_owner python3 -I -m zipfile -e "$cache/$PY_ZIP" "$tmp/python"
# Let it find libraries put next to it (and in its Lib\site-packages).
as_owner sed -i 's/^#import site/import site/' "$tmp/python/$PY_PTH"
grep -q '^import site' "$tmp/python/$PY_PTH" || die "unexpected $PY_PTH in $PY_ZIP"

echo "    The Windows libraries"
mapfile -t requirements < <(as_owner python3 -I -c '
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "service"))
from rgbhub.updater.layouts.windows import windows_requirements
print("\n".join(windows_requirements(Path(sys.argv[1]))))
' "$snap")
((${#requirements[@]})) || die "no Windows libraries listed in service/pyproject.toml"
as_owner python3 -m venv "$tmp/venv" || die "could not make a Python environment (install python3-venv)"
as_owner env PIP_CACHE_DIR="$cache/pip" "$tmp/venv/bin/pip" download --quiet \
    --disable-pip-version-check --only-binary=:all: --platform win_amd64 \
    --python-version 3.13 --implementation cp -d "$tmp/wheels" "${requirements[@]}" ||
    die "could not download the Windows libraries"
pip_whl="$cache/${PIP_URL##*/}"
if [[ ! -f $pip_whl ]]; then
    as_owner curl -fsSL --proto '=https' -o "$pip_whl.part" "$PIP_URL" ||
        die "could not download $PIP_URL"
    as_owner mv "$pip_whl.part" "$pip_whl"
fi
if ! echo "$PIP_SHA256  $pip_whl" | sha256sum --check --quiet --status; then
    rm -f "$pip_whl"
    die "${pip_whl##*/} is not the file PyPI published (checksum differs); it was deleted, run this again"
fi
as_owner cp "$pip_whl" "$tmp/pip.whl"

# The RAM sticks Ubuntu sees, for the helper to fall back on.
sticks="$(as_owner python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from rgbhub.platforms.linux.ram import find_sticks
print(" ".join(f"{a:02x}" for a in find_sticks()))
' "$snap/service")" || sticks=

# Your Ubuntu settings, read with the schema of the version being installed.
values=
schema="$snap/frontends/gnome-shell/rgbhub@konfus-dev.github.io/schemas"
if command -v glib-compile-schemas >/dev/null && command -v gsettings >/dev/null &&
    as_owner mkdir "$tmp/schemas" && as_owner cp "$schema"/*.gschema.xml "$tmp/schemas/" &&
    as_owner glib-compile-schemas "$tmp/schemas" &&
    as_owner python3 -I "$snap/install/windows_settings.py" dump --schema-dir "$tmp/schemas" \
        >"$tmp/values.json"; then
    values="$tmp/values.json"
else
    echo "    NOTE: could not read the Ubuntu settings; Windows starts with the defaults."
fi

# Never move Windows backwards: if it runs a commit this main does not contain
# (the updater took it from GitHub, or this checkout does not know it), keep
# RGB-Hub's program files on Windows as they are.
root="$win_home/AppData/Local/Programs/RGB-Hub"
windows_ahead=
installed="$(head -c 64 "$root/current.txt" 2>/dev/null | tr -d '\r\n ')" || installed=
if [[ $installed =~ ^[0-9a-f]{40}$ && $installed != "$sha" && -d $root/versions/$installed ]] &&
    ! as_owner git -C "$repo" merge-base --is-ancestor "$installed" "$sha" 2>/dev/null; then
    windows_ahead=1
    echo "NOTE: Windows runs RGB-Hub ${installed:0:7}, which main (${sha:0:7}) does not contain."
    echo "      Its program files are kept as they are (no going back); settings and setup are still done."
fi

# --- 3. Copy to Windows ------------------------------------------------------------

check_windows
case ",$(findmnt -n -o OPTIONS "$mnt")," in
    *,ro,*)
        mount -o remount,rw "$mnt" ||
            die "could not make $mnt writable (is Windows hibernated?)"
        made_rw=1
        ;;
esac

# Replace a folder by a fresh copy (written beside it, then swapped in).
replace_folder() {
    local source="$1" dest="$2"
    rm -rf "$dest.new"
    cp -r "$source" "$dest.new"
    rm -rf "$dest"
    mv "$dest.new" "$dest"
}

if [[ -z $windows_ahead ]]; then
    # C:\Program Files\RGB-Hub is built complete beside the old one, made
    # admin-only (Administrators and SYSTEM full control, Users read and run)
    # through ntfs3's system.ntfs_security, then swapped in. Files ntfs3 makes
    # would otherwise let Everyone change them, the RAM helper's Python included,
    # which an existing "RGB-Hub RAM" task runs with full rights at sign-in.
    program="$mnt/Program Files/RGB-Hub"
    pf_new="$mnt/Program Files/.RGB-Hub.new"
    pf_old="$mnt/Program Files/.RGB-Hub.old"
    echo "==> C:\\Program Files\\RGB-Hub (the RAM helper; admin-only)"
    restore_program_files  # left by an earlier run that stopped mid-swap
    rm -rf "$pf_new"
    mkdir "$pf_new"
    if ! python3 -I "$snap/install/ntfs_security.py" "$pf_new"; then
        rmdir "$pf_new"
        pf_new=
        die "could not make C:\\Program Files\\RGB-Hub admin-only from Ubuntu; nothing was copied"
    fi
    mkdir -p "$pf_new/helper" "$pf_new/install/windows"
    cp -r "$tmp/python" "$pf_new/python"
    cp "$snap/helper/rgbhub_ram_helper.py" "$snap/helper/rgbhub_ram_helper_windows.py" "$pf_new/helper/"
    if [[ -n $sticks ]]; then
        printf '%s\n' "$sticks" >"$pf_new/helper/ram-sticks.txt"
    fi
    cp "$snap"/install/windows/*.ps1 "$snap"/install/windows/*.py "$pf_new/install/windows/"
    sed -e 's/$/\r/' "$snap/install/windows/RGB-Hub-setup.cmd" >"$pf_new/install/windows/RGB-Hub-setup.cmd"
    if [[ -f $program/disabled-services.json ]]; then
        cp "$program/disabled-services.json" "$pf_new/"  # HP's start types from before setup
    fi
    python3 -I "$snap/install/ntfs_security.py" "$pf_new" ||
        die "could not make C:\\Program Files\\RGB-Hub admin-only from Ubuntu; the old copy is left as it was"
    if [[ -e $program ]]; then
        mv "$program" "$pf_old"
    fi
    mv "$pf_new" "$program"
    pf_new=
    restore_program_files  # $program is in place: the old copy goes

    echo "==> C:\\Users\\$user\\AppData\\Local\\Programs\\RGB-Hub (the hub and its updates)"
    mkdir -p "$root"
    replace_folder "$tmp/python" "$root/python"
    cp "$tmp/pip.whl" "$root/pip.whl"
    cp "$snap/service/rgbhub/platforms/windows/launcher.py" "$root/launcher.py"
    # The snapshot installs itself with the updater's own code (as install-user.sh
    # does on Ubuntu): versions\<commit>\ with the wheels unpacked into its
    # site-packages, the updater's copy, current.txt and the update state. Its
    # self-check needs Windows, so it runs at the first update instead. A leftover
    # update.lock from Windows is ignored (its pid is Windows'; Windows is off).
    boot='
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from rgbhub.updater import UpdateError
from rgbhub.updater.install import install
from rgbhub.updater.layouts.windows import WindowsLayout
from rgbhub.updater.store import Store
root, wheels, zip_path, sha = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
try:
    install(Store(root), WindowsLayout(root, wheels=wheels), zip_path, sha, "windows", check=False, foreign_lock=True)
except (UpdateError, OSError) as e:
    sys.exit(f"ERROR: could not install RGB-Hub {sha[:7]}: {e}")
'
    python3 -I -c "$boot" "$tmp/rgbhub.zip/$top/service" "$root" "$tmp/wheels" "$tmp/rgbhub.zip" "$sha"
fi

echo "==> Settings"
if [[ -n $values ]]; then
    python3 -I "$snap/install/windows_settings.py" seed --values "$values" \
        --windows-mount "$mnt" --user "$user" --ubuntu-config "$owner_home/.config/rgbhub"
fi

startup="$win_home/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
echo "==> Finishing at the next sign-in: RGB-Hub-setup.cmd in the Startup folder"
mkdir -p "$startup"
sed -e "s/@USER@/$user/g" -e 's/$/\r/' "$snap/install/windows/RGB-Hub-setup.cmd" \
    >"$startup/RGB-Hub-setup.cmd"
sync

echo
shown="${sha:0:7}"
[[ -z $windows_ahead ]] || shown="${installed:0:7}"
echo "Done: RGB-Hub $shown is on Windows. Start Windows and sign in as $user:"
echo "Windows asks once for admin rights (to finish the setup and remove OMEN"
echo "Gaming Hub); after that RGB-Hub starts at every sign-in and updates itself."
