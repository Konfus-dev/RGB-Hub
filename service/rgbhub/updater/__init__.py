# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-updater: keeps an installed RGB-Hub on the latest `main` of GitHub.

Runs as its own process (`python -m rgbhub.updater`) and uses only the standard
library and this folder, so a broken hub version can never break updating and
the folder can run from a stable copy of itself.
"""

import re

_SHA = re.compile(r"[0-9a-f]{40}")


class UpdateError(Exception):
    """Anything that stops an update: network, GitHub, a bad download or zip.

    `status` is the HTTP status when GitHub answered with an error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def is_sha(value: object) -> bool:
    """A full commit id: exactly 40 lowercase hex characters."""
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def check_sha(value: object) -> str:
    """Return `value` if it is a full commit id, else raise UpdateError.

    Every commit id is checked with this before it goes into a path or URL."""
    if not is_sha(value):
        raise UpdateError(f"not a commit id: {str(value)[:60]!r}")
    return value


def short(sha: str | None) -> str:
    return (sha or "unknown")[:7]
