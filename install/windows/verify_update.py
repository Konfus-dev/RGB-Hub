# SPDX-License-Identifier: GPL-3.0-or-later
"""Check an approved update's admin parts against GitHub before they are installed.

Run with admin rights by finish-setup.ps1 -HelperOnly -From <version folder>,
always as the installed copy in C:\\Program Files\\RGB-Hub (admin-writable only),
with the Python next to it:

    python.exe -I verify_update.py --from <root>\\versions\\<commit> --staging <folder>

The version folder belongs to the owner's normal account, so anything running
there could have changed it. Before anything from it may run with full rights:
- <commit> must be on GitHub's `main` (compare <commit>...main says "ahead"
  or "identical": main contains it);
- every file that needs admin rights (helper\\*.py, install\\windows\\*.ps1 and
  *.py) is downloaded from GitHub at <commit>, and must have the same SHA-256
  as the file in the version folder.
Only then are the files GitHub sent written to the staging folder (which the
caller made under Program Files); finish-setup.ps1 moves them into place.
Nothing is written if any check fails.

Exit status: 0 staged, 1 refused (a file differs, not on main, odd input),
2 GitHub could not be reached (nothing installed; try again later).
Only the standard library.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Konfus-dev/RGB-Hub"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}"
USER_AGENT = "rgbhub-updater"
TIMEOUT_S = 30
COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]+")  # what goes into a GitHub address as a file name
# (folder in a version, file suffix): what finish-setup.ps1 installs into Program Files.
PRIVILEGED = (("helper", ".py"), ("install/windows", ".ps1"), ("install/windows", ".py"))


class Refused(Exception):
    """The update's admin parts must not be installed."""


class Unreachable(Exception):
    """GitHub could not be asked."""


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as reply:
            return reply.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Refused(f"GitHub does not have {url}") from e
        raise Unreachable(f"GitHub answered {e.code} for {url}") from e
    except (OSError, ValueError) as e:
        raise Unreachable(f"cannot reach GitHub: {e}") from e


def fetch_json(url: str):
    try:
        return json.loads(_get(url))
    except ValueError as e:
        raise Unreachable(f"GitHub sent something odd for {url}") from e


def fetch_bytes(url: str) -> bytes:
    return _get(url)


def privileged_files(version: Path) -> list[str]:
    """The version's files that need admin rights, as paths like helper/x.py."""
    found = []
    for folder, suffix in PRIVILEGED:
        for path in sorted((Path(version) / folder).glob(f"*{suffix}")):
            if path.is_file() and not path.is_symlink():
                found.append(f"{folder}/{path.name}")
    return found


def on_main(sha: str, get_json=fetch_json) -> bool:
    data = get_json(f"{API}/compare/{sha}...main")
    return isinstance(data, dict) and data.get("status") in ("ahead", "identical")


def stage(version: Path, staging: Path, get_json=fetch_json, get_bytes=fetch_bytes) -> list[str]:
    """Check the version's admin parts with GitHub and write GitHub's copies to
    `staging` (emptied first). Returns the files staged; raises Refused or Unreachable."""
    version = Path(version)
    sha = version.name
    if not COMMIT.fullmatch(sha):
        raise Refused(f"{version} is not a version folder (its name is not a commit id)")
    names = privileged_files(version)
    odd = [n for n in names if not SAFE_NAME.fullmatch(n.rsplit("/", 1)[1])]
    if odd:
        raise Refused(f"odd file names in {version}: {', '.join(odd)}")
    if not names:
        raise Refused(f"{version} has none of RGB-Hub's admin parts")
    if not on_main(sha, get_json):
        raise Refused(f"version {sha[:7]} is not on GitHub's main branch")
    checked = {}
    for name in names:
        remote = get_bytes(f"{RAW}/{sha}/{name}")
        local = (version / name).read_bytes()
        if hashlib.sha256(remote).digest() != hashlib.sha256(local).digest():
            raise Refused(f"{name} in {version} is not what GitHub has for {sha[:7]}")
        checked[name] = remote
    if staging.exists():
        shutil.rmtree(staging)
    for name, data in checked.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return names


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="version", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        names = stage(args.version, args.staging)
    except Refused as e:
        print(f"Not installed: {e}.", file=sys.stderr)
        return 1
    except Unreachable as e:
        print(f"Not installed: {e}. Nothing was changed; try again later.", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"Not installed: {e}.", file=sys.stderr)
        return 1
    print("Checked with GitHub: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
