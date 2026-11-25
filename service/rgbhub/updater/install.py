# SPDX-License-Identifier: GPL-3.0-or-later
"""Install one version by hand: what `install-user.sh --release` and `install-batocera.sh` run.

    python3 -I -c "$BOOT" <zip>/<top>/service --platform X --zip <zip> --sha <sha>
        [--root PATH] [--no-check]

(`$BOOT` puts the snapshot's own `service` folder, inside the zip, on
`sys.path`, so the code that installs is the code being installed.)

It unpacks the zip (a `git archive` of one commit) into `versions/<sha>/`,
prepares and checks it, makes it the active version and records it as the
installed commit in `update-state.json`, all through the updater's own Store
and layout, so the updater finds exactly what it would have made itself. It
does not restart the hub; the install scripts do that.
"""

import argparse
import logging
import sys
from pathlib import Path

from rgbhub.updater import UpdateError, is_sha, short
from rgbhub.updater.__main__ import PLATFORMS, LayoutMissing, load_layout
from rgbhub.updater.store import Store

log = logging.getLogger("rgbhub.updater")


def install(store: Store, layout, zip_path: Path, sha: str, check: bool = True) -> None:
    """Raises UpdateError if anything fails; the active version is then unchanged."""
    with store.locked() as got:
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="rgbhub.updater.install", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--root", type=Path, help="install folder (default: the platform's)")
    parser.add_argument("--zip", type=Path, required=True, help="the version as a zip")
    parser.add_argument("--sha", required=True, help="its full commit id")
    parser.add_argument("--no-check", action="store_true", help="skip the self-check")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="    %(message)s")
    if not is_sha(args.sha):
        print(f"not a full commit id: {args.sha!r}", file=sys.stderr)
        return 2
    try:
        layout, root = load_layout(args.platform, args.root)
        install(Store(root), layout, args.zip, args.sha, check=not args.no_check)
    except (UpdateError, LayoutMissing, OSError) as e:
        print(f"Could not install RGB-Hub {short(args.sha)}: {e}", file=sys.stderr)
        return 1
    print(f"    RGB-Hub {short(args.sha)} installed in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
