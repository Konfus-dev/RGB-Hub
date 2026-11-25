# SPDX-License-Identifier: GPL-3.0-or-later
"""How one system installs, starts and checks a version (Ubuntu, Batocera, Windows).

Each system has a module here (`gnome`, `batocera`, `windows`) providing:

- `default_root() -> Path`: where versions and the update state live.
- `build(root: Path) -> Layout`: the layout for that root.

The updater core (`flow.update_once`) only talks to the `Layout` below.
"""

from pathlib import Path
from typing import Protocol


class Layout(Protocol):
    def prepare(self, version: Path) -> None:
        """Make an unpacked version runnable (install its dependencies)."""

    def self_check(self, version: Path) -> bool:
        """True if the version imports and builds its hub against fakes."""

    def privileged_changes(self, old: Path | None, new: Path) -> list[str]:
        """Files in `new` that need admin rights to install and differ from what's installed."""

    def request_privileged(self, new: Path, changes: list[str]) -> None:
        """Ask the owner to approve the privileged part; never install it silently."""

    def activate(self, version: Path) -> None:
        """Make `version` the one the hub starts from next (atomically)."""

    def restart_hub(self) -> None:
        """Restart the hub so it runs the active version."""

    def hub_healthy(self, wait_s: int) -> bool:
        """After waiting up to `wait_s` seconds: is the restarted hub still running?"""

    def busy(self) -> bool:
        """True when now is a bad time to restart the hub (Batocera: a game is running)."""

    def notify(self, text: str) -> None:
        """Tell the owner something short (desktop notification or log)."""

    def current_commit(self) -> str | None:
        """The commit id of the active version, or None if unknown."""
