# SPDX-License-Identifier: GPL-3.0-or-later
"""Starts RGB-Hub on Windows and keeps it running.

Setup copies this file to the install folder (the root):

    <root>\\launcher.py            this file
    <root>\\current.txt            the name of the version to run
    <root>\\python\\pythonw.exe     Python, without a console window
    <root>\\versions\\<v>\\service        the rgbhub package
    <root>\\versions\\<v>\\site-packages  its libraries (hidapi, pystray, Pillow, ...)

At sign-in Windows runs `pythonw.exe launcher.py`. It runs the hub of the
version named in current.txt as a child process and watches it:
- exit code 0 (Quit): stop;
- exit code 75 (restart, asked for by an update): start again after 2 s, with
  the version current.txt names now (if it names none that is usable, the
  version that ran last); more than 5 such requests in a row, each within
  2 minutes of starting, are treated like crashes;
- anything else (a crash): start again after a pause of 5 seconds, doubling
  each time up to 5 minutes, back to 5 seconds once a run lasted 2 minutes.

`python.exe launcher.py --self-check <v>` builds version <v>'s devices on
fakes and returns 0 if it can (the updater asks this before activating a
version).

Only the standard library: this file is not updated together with a version,
so it must not depend on one.
"""

import argparse
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

EXIT_QUIT = 0
EXIT_RESTART = 75
FIRST_PAUSE_S = 5
LONGEST_PAUSE_S = 300
GOOD_RUN_S = 120  # a run this long was not a crash loop: pause from the start again
RESTART_PAUSE_S = 2  # between a restart request and the new start
MAX_QUICK_RESTARTS = 5  # more restart requests than this, each soon after a start: a loop
LOG_BYTES = 1_000_000

# Run in the child: put the version's folders first on the path and start the hub.
CHILD_CODE = (
    "import sys; sys.path[:0] = sys.argv[1:3]; del sys.argv[1:3]; "
    "from rgbhub.__main__ import main; sys.exit(main(sys.argv[1:]))"
)
HUB_ARGS = ["--platform", "windows"]

log = logging.getLogger("rgbhub.launcher")


def read_current(root: Path) -> str | None:
    """The version current.txt names, if it is a plain folder name that exists."""
    try:
        version = (root / "current.txt").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return version if is_version(root, version) else None


def is_version(root: Path, version: str) -> bool:
    """A plain folder name (no way out of versions\\) with a service folder in it."""
    if not version or version in (".", "..") or any(c in version for c in "/\\:"):
        return False
    return (root / "versions" / version / "service").is_dir()


def version_paths(root: Path, version: str) -> list[str]:
    """The folders the version's code and libraries are in, in path order."""
    folder = root / "versions" / version
    return [str(folder / "service"), str(folder / "site-packages")]


def child_command(python: str, paths: list[str], args: list[str]) -> list[str]:
    """The command that runs `rgbhub <args>` from `paths`.

    -I: nothing from the environment or the current folder joins the path;
    the embedded Python's own ._pth file would ignore PYTHONPATH anyway, so
    the folders are passed as arguments."""
    return [python, "-I", "-c", CHILD_CODE, *paths, *args]


def windowless_python(python: str) -> str:
    """pythonw.exe beside `python` if there is one (no console window), else `python`."""
    candidate = Path(python).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else python


def supervise(
    root: Path,
    spawn: Callable[[list[str]], int],
    python: str,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the hub until it quits. Returns 0, or 1 if there is nothing to run."""
    pause = FIRST_PAUSE_S
    last: str | None = None  # the version that ran last
    quick_restarts = 0  # restart requests in a row, each soon after a start
    while True:
        version = read_current(root)
        if version is None:
            if last is None or not is_version(root, last):
                log.error("No version to run: %s is missing or names none", root / "current.txt")
                return 1
            log.warning("%s names no usable version; running %s again", root / "current.txt", last)
            version = last
        log.info("Starting RGB-Hub version %s", version)
        started = clock()
        code = spawn(child_command(python, version_paths(root, version), HUB_ARGS))
        last = version
        long_run = clock() - started >= GOOD_RUN_S
        if code == EXIT_QUIT:
            log.info("RGB-Hub quit")
            return 0
        if long_run:
            pause = FIRST_PAUSE_S
        if code == EXIT_RESTART:
            quick_restarts = 0 if long_run else quick_restarts + 1
            if quick_restarts <= MAX_QUICK_RESTARTS:
                log.info("RGB-Hub asked to be restarted")
                sleep(RESTART_PAUSE_S)
                continue
            log.warning("RGB-Hub asked to be restarted %d times in a row", quick_restarts)
        else:
            quick_restarts = 0
        log.warning("RGB-Hub stopped unexpectedly (exit code %s); again in %d s", code, pause)
        sleep(pause)
        pause = min(pause * 2, LONGEST_PAUSE_S)


def data_dir() -> Path:
    """%LOCALAPPDATA%\\rgbhub: the logs."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "rgbhub"


def run_child(command: list[str]) -> int:
    """Run the hub and wait for it. What it writes to stderr before its own
    log is set up (an import error, say) goes to hub-errors.log."""
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    errors = folder / "hub-errors.log"
    try:
        if errors.stat().st_size > LOG_BYTES:
            errors.unlink()
    except OSError:
        pass
    try:
        with open(errors, "ab") as stderr:
            return subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr
            ).returncode
    except OSError as error:
        log.error("Cannot start %s: %s", command[0], error)
        return 1


def _setup_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                folder / "launcher.log", maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8"
            )
        )
    except OSError:
        pass
    if sys.stderr is not None:  # pythonw.exe has none
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers or [logging.NullHandler()],
    )


def _run_plain(command: list[str]) -> int:
    return subprocess.run(command, stdin=subprocess.DEVNULL).returncode


def main(argv: list[str] | None = None, spawn: Callable[[list[str]], int] | None = None) -> int:
    """`spawn` runs a command and returns its exit code (tests pass a fake)."""
    parser = argparse.ArgumentParser(description="Start RGB-Hub and keep it running.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--self-check", metavar="VERSION", help="check a version, run nothing")
    args = parser.parse_args(argv)
    if args.self_check:
        if not is_version(args.root, args.self_check):
            print(f"No version {args.self_check} in {args.root}", file=sys.stderr)
            return 1
        paths = version_paths(args.root, args.self_check)
        command = child_command(sys.executable, paths, ["--self-check", *HUB_ARGS])
        return (spawn or _run_plain)(command)
    _setup_logging()
    return supervise(args.root, spawn or run_child, windowless_python(sys.executable))


if __name__ == "__main__":
    sys.exit(main())
