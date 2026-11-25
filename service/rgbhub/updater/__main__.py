# SPDX-License-Identifier: GPL-3.0-or-later
"""`python -m rgbhub.updater --platform {gnome,batocera,windows} [--once | --loop] [--root PATH]`

`--log FILE` also writes the log to FILE (Windows: pythonw.exe has no stderr).

`--once` (the default) checks once; `--loop` checks at start, then about every
hour (3600 s +/- 300 s) and never stops on an error. `--check` only imports the
updater and the platform's layout and exits 0 (how a new stable copy proves it
runs before it replaces the old one). Logs go to stderr.
"""

import argparse
import importlib
import logging
import logging.handlers
import random
import sys
import time
from pathlib import Path

from rgbhub.updater.flow import update_once
from rgbhub.updater.github import GitHub
from rgbhub.updater.store import Store

log = logging.getLogger("rgbhub.updater")

PLATFORMS = ("gnome", "batocera", "windows")
INTERVAL_S = 3600
JITTER_S = 300


class LayoutMissing(Exception):
    pass


def load_layout(platform: str, root: Path | None):
    """Return (layout, root) from `rgbhub.updater.layouts.<platform>`."""
    name = f"rgbhub.updater.layouts.{platform}"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name == name:
            raise LayoutMissing(f"no update layout for {platform!r} in this version") from e
        raise
    root = Path(root) if root else module.default_root()
    return module.build(root), root


def run_once(gh, store, layout) -> str:
    """One check that never raises: unexpected errors are logged and count as failed."""
    try:
        result = update_once(gh, store, layout)
    except Exception:
        log.exception("update check crashed")
        return "failed"
    log.info("update check: %s", result)
    return result


LOG_BYTES = 1_000_000


def _log_to_file(path: Path, fmt: str) -> None:
    """The updater's lines also go to `path` (about 1 MB, one older copy kept)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8"
        )
    except OSError as e:
        log.warning("cannot log to %s: %s", path, e)
        return
    handler.setFormatter(logging.Formatter(fmt))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main(argv=None, *, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(prog="rgbhub.updater", description=__doc__.splitlines()[0])
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="check once (default)")
    mode.add_argument("--loop", action="store_true", help="check now, then every hour")
    mode.add_argument("--check", action="store_true", help="only check that the updater loads")
    parser.add_argument("--root", type=Path, help="install folder (default: the platform's)")
    parser.add_argument(
        "--log", type=Path, help="also log to this file (Windows: pythonw.exe has no stderr)"
    )
    args = parser.parse_args(argv)

    fmt = "%(asctime)s rgbhub-updater: %(message)s"
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=fmt)
    if args.log:
        _log_to_file(args.log, fmt)
    try:
        layout, root = load_layout(args.platform, args.root)
    except LayoutMissing as e:
        print(f"rgbhub-updater: {e}", file=sys.stderr)
        return 2
    if args.check:
        print(f"rgbhub-updater: ok ({args.platform}, {root})")
        return 0

    gh = GitHub()
    store = Store(root)
    if not args.loop:
        return 1 if run_once(gh, store, layout) == "failed" else 0
    while True:
        run_once(gh, store, layout)
        sleep(INTERVAL_S + random.uniform(-JITTER_S, JITTER_S))


if __name__ == "__main__":
    sys.exit(main())
