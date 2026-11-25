# SPDX-License-Identifier: GPL-3.0-or-later
"""Install one version by hand: what `install-user.sh --release` and `install-batocera.sh` run.

    python3 -I -c "$BOOT" <zip>/<top>/service --platform X --zip <zip> --sha <sha>
        [--root PATH] [--no-check]
    python3 -I -c "$BOOT" <checkout>/service --platform X --from-github [--root PATH] [--no-check]

(`$BOOT` puts a `service` folder on `sys.path`: with `--zip` the snapshot's own,
inside the zip, so the code that installs is the code being installed.)

`--from-github` is for a copy without git history (it cannot name its own
commit): it asks GitHub for the newest `main`, downloads that commit's zip
with the updater's own GitHub code and installs it the same way; without a
network it fails rather than install something that could never update.

It unpacks the zip (a `git archive` of one commit) into `versions/<sha>/`,
prepares and checks it, makes it the active version and records it as the
installed commit in `update-state.json`, all through the updater's own Store
and layout, so the updater finds exactly what it would have made itself. It
does not restart the hub; the install scripts do that.
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from rgbhub.updater import UpdateError, is_sha, short
from rgbhub.updater.__main__ import PLATFORMS, LayoutMissing, load_layout
from rgbhub.updater.github import GitHub
from rgbhub.updater.stable import refresh_updater_copy
from rgbhub.updater.store import Store

log = logging.getLogger("rgbhub.updater")


def install(
    store: Store,
    layout,
    zip_path: Path,
    sha: str,
    platform: str,
    check: bool = True,
    foreign_lock: bool = False,
) -> None:
    """Raises UpdateError if anything fails; the active version is then unchanged.
    `foreign_lock`: installing Windows' folder from Ubuntu, where a lock's pid is Windows'."""
    with store.locked(foreign=foreign_lock) as got:
        if not got:
            raise UpdateError("an update is running right now; try again in a minute")
        store.clean_partials()
        state = store.load()
        active = layout.current_commit()
        version = store.version_dir(sha)
        if not (active == sha and version.is_dir()):
            version = store.unpack(zip_path, sha)
        layout.prepare(version)
        if check and not layout.self_check(version):
            if active != sha:
                store.remove_version(sha)
            raise UpdateError(f"version {short(sha)} failed its self-check; nothing was changed")
        # Its updater must run before anything is switched (raises if it does not).
        refresh_updater_copy(version, store.root, platform)
        layout.activate(version)
        previous = active if is_sha(active) else state["current"]
        if previous == sha or not (is_sha(previous) and store.version_dir(previous).is_dir()):
            previous = state["previous"] if state["previous"] != sha else None
        store.set_current(sha, previous=previous)
        if sha in state["bad"]:  # installed on purpose: give it another chance
            state = store.load()
            state["bad"] = [s for s in state["bad"] if s != sha]
            store.save(state)
        store.prune(keep=3)


def install_from_github(store: Store, layout, platform: str, gh=None, check: bool = True) -> str:
    """Install the newest `main` from GitHub; returns its commit id."""
    gh = gh or GitHub()
    sha = gh.latest("main")
    with tempfile.TemporaryDirectory(prefix="rgbhub-install-") as tmp:
        zip_path = Path(tmp) / f"{sha}.zip"
        gh.download(sha, zip_path)
        install(store, layout, zip_path, sha, platform, check=check)
    return sha


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="rgbhub.updater.install", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--root", type=Path, help="install folder (default: the platform's)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path, help="the version as a zip (with --sha)")
    source.add_argument("--from-github", action="store_true", help="the newest main from GitHub")
    parser.add_argument("--sha", help="the zip's full commit id")
    parser.add_argument("--no-check", action="store_true", help="skip the self-check")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="    %(message)s")
    if args.zip and not is_sha(args.sha):
        print(f"--zip needs --sha with a full commit id, not {args.sha!r}", file=sys.stderr)
        return 2
    sha = args.sha
    try:
        layout, root = load_layout(args.platform, args.root)
        store = Store(root)
        if args.from_github:
            sha = install_from_github(store, layout, args.platform, check=not args.no_check)
        else:
            install(store, layout, args.zip, sha, args.platform, check=not args.no_check)
    except (UpdateError, LayoutMissing, OSError) as e:
        what = short(sha) if sha else "from GitHub"
        print(f"Could not install RGB-Hub {what}: {e}", file=sys.stderr)
        return 1
    print(f"    RGB-Hub {short(sha)} installed in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
