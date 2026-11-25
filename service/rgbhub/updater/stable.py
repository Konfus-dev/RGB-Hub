# SPDX-License-Identifier: GPL-3.0-or-later
"""The updater's stable copy: `<root>/updater/`, which the timer or service runs.

The updater never runs from a version folder, so switching or deleting a
version cannot pull its code away mid-run. Each activation copies the active
version's `rgbhub/updater/` here (with an empty `rgbhub` package around it) and
writes `run.py`, which puts this folder on `sys.path` and starts the updater:

    python3 -I <root>/updater/run.py --platform gnome --once

A new copy is only swapped in after it has run (`run.py --platform X --check`:
imports the updater and that platform's layout), so a broken updater on
`main` can never replace the working one and stop all later updates.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rgbhub.updater import UpdateError

RUN_PY = '''\
# SPDX-License-Identifier: GPL-3.0-or-later
"""Start RGB-Hub's updater from this stable copy (written by rgbhub/updater/stable.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgbhub.updater.__main__ import main  # noqa: E402

sys.exit(main())
'''

PACKAGE_INIT = '''\
# SPDX-License-Identifier: GPL-3.0-or-later
"""Just enough of the rgbhub package to run the updater (see run.py)."""
'''


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


CHECK_TIMEOUT_S = 60


def check_copy(folder: Path, platform: str) -> str | None:
    """Run the copy in `folder` once with `--check`; None if it works, else why not."""
    try:
        done = subprocess.run(
            [sys.executable, "-I", str(Path(folder) / "run.py"), "--platform", platform, "--check"],
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_S,
            cwd=folder,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    if done.returncode != 0:
        lines = (done.stderr or done.stdout or "").strip().splitlines()
        return f"exit {done.returncode}: {lines[-1] if lines else 'no output'}"
    return None


def refresh_updater_copy(version: Path, root: Path, platform: str) -> Path:
    """Replace `<root>/updater/` with the updater of `version`; returns that folder.

    The new copy is built beside the old one, run once with `--check`, and only
    then swapped in by renames; if the check fails the old copy stays and
    UpdateError is raised. A crash leaves either the old or the new copy (plus
    a `.updater.*` leftover that the next refresh deletes)."""
    source = Path(version) / "service" / "rgbhub" / "updater"
    if not (source / "__main__.py").is_file():
        raise UpdateError(f"{version} has no updater to copy")
    root = Path(root)
    dest = root / "updater"
    new = root / ".updater.new"
    old = root / ".updater.old"
    for leftover in (new, old):
        _remove(leftover)
    skip = shutil.ignore_patterns("__pycache__")
    shutil.copytree(source, new / "rgbhub" / "updater", ignore=skip)
    (new / "rgbhub" / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    (new / "run.py").write_text(RUN_PY, encoding="utf-8")
    problem = check_copy(new, platform)
    if problem is not None:
        _remove(new)
        raise UpdateError(f"the updater of {Path(version).name[:7]} does not run ({problem})")
    if dest.exists() or dest.is_symlink():
        os.rename(dest, old)
    os.rename(new, dest)
    _remove(old)
    return dest
