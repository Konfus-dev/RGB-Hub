# SPDX-License-Identifier: GPL-3.0-or-later
#
# Sourced by install-user.sh and install-batocera.sh: never move an install
# backwards. The updater may have taken a commit from GitHub that the local
# `main` does not contain (yet); installing local main over it would go back.

# behind_installed <repo> <installed commit> <main commit>
# Succeeds (and says why) when main must NOT be installed: the installed commit
# is a full commit id different from main that is not an ancestor of main, or one
# this checkout does not know at all. Fails (install!) otherwise, including when
# nothing is installed yet.
behind_installed() {
    local repo="$1" installed="$2" main="$3"
    [[ $installed =~ ^[0-9a-f]{40}$ && $installed != "$main" ]] || return 1
    if git -c safe.directory="$repo" -C "$repo" merge-base --is-ancestor "$installed" "$main" \
        2>/dev/null; then
        return 1
    fi
    echo "NOTE: RGB-Hub ${installed:0:7} is installed, which main (${main:0:7}) does not contain."
    echo "      It is kept as it is (no going back); the rest of the setup is still done."
    return 0
}
