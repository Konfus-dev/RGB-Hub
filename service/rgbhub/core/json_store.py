# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings kept in a plain JSON file.

For systems without a settings service of their own (Batocera now; a good
fit for Windows later). The file holds only what differs from the defaults,
so it stays short and easy to edit by hand:

    {
        "lcd-brightness": 60,
        "auto-idle-colour": "#00AA00"
    }

Fits core.interfaces.Settings.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .settings_schema import DEFAULTS, valid

log = logging.getLogger(__name__)

# These change all the time and are not the owner's choices, so they live in
# memory only and never touch the disk.
VOLATILE = ("status", "focused-app")


class JsonSettings:
    def __init__(self, path: Path, defaults: dict[str, Any] | None = None):
        """`defaults` overrides the standard defaults for this platform."""
        self._path = Path(path)
        self._defaults = {**DEFAULTS, **(defaults or {})}
        self._warned: set[str] = set()  # problems already reported for the file as it is now
        self._last_text: str | None = None
        self._notes: dict[str, Any] = {}  # "_..." keys a front end keeps for itself
        self._saved: dict[str, Any] = self._read({})  # what the file says
        self._volatile: dict[str, Any] = {}
        self._subscribers: list[Callable[[str], None]] = []

    def get(self, key: str) -> Any:
        if key in self._volatile:
            return self._volatile[key]
        return self._saved.get(key, self._defaults[key])

    def set(self, key: str, value: Any) -> None:
        if key not in VOLATILE:
            # A front end may have saved the file a moment ago, before our
            # watch noticed. Writing what we last read would undo its change.
            self.reload()
        if value == self.get(key):
            return
        if key in VOLATILE:
            self._volatile[key] = value
        else:
            self._saved[key] = value
            self._write()
        self._tell(key)

    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def reload(self) -> None:
        """Read the file again (after someone edited it) and report changes."""
        before = {key: self.get(key) for key in self._defaults}
        self._saved = self._read(self._saved)
        for key in self._defaults:
            if self.get(key) != before[key]:
                self._tell(key)

    def _tell(self, key: str) -> None:
        for callback in list(self._subscribers):
            callback(key)

    def _warn(self, message: str, *args) -> None:
        """Log a problem with the file once, not at every re-read of the same contents."""
        text = message % args
        if text not in self._warned:
            self._warned.add(text)
            log.warning("%s: %s", self._path, text)

    def _read(self, fallback: dict[str, Any]) -> dict[str, Any]:
        """The file's contents, keeping only known keys with sensible values.

        A file that cannot be understood at all (caught half-saved, or with a
        typo in it) changes nothing: `fallback`, the last good settings, stays.
        """
        try:
            text = self._path.read_text()
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            self._warn("cannot be read (%s); keeping the previous settings", error)
            return fallback
        if text != self._last_text:
            self._last_text = text
            self._warned.clear()  # a changed file may have new problems, or none
        try:
            data = json.loads(text)
        except ValueError as error:
            self._warn("is not valid JSON (%s); keeping the previous settings", error)
            return fallback
        if not isinstance(data, dict):
            self._warn("should contain one { ... } object; keeping the previous settings")
            return fallback
        good = {}
        self._notes = {k: v for k, v in data.items() if k.startswith("_")}
        for key, value in data.items():
            if key.startswith("_"):
                continue  # a front end's own note (EmuBox's "_seen"): kept, not used
            if key in VOLATILE:
                continue
            if key not in self._defaults:
                self._warn("unknown setting '%s' ignored (check the spelling)", key)
                continue
            if not valid(key, value, self._defaults[key]):
                self._warn("'%s' ignored: wrong kind of value", key)
                continue
            good[key] = value
        return good

    def _write(self) -> None:
        changed = {k: v for k, v in self._saved.items() if v != self._defaults[k]}
        changed.update(self._notes)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(changed, indent=4, sort_keys=True) + "\n")
            os.replace(temporary, self._path)  # never leaves a half-written file
        except OSError as error:
            log.warning("Could not save %s: %s", self._path, error)
