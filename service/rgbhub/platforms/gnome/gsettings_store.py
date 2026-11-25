# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings kept in GSettings (dconf), as GNOME does.

The schema lives in the GNOME Shell extension's folder, so it is loaded
from that directory instead of the system-wide schema list.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from gi.repository import Gio, GLib

SCHEMA_ID = "org.gnome.shell.extensions.rgbhub"


def load_settings(schema_dir: Path, backend: Gio.SettingsBackend | None = None) -> Gio.Settings:
    """`backend` is only for tests; leave it out to use the real settings."""
    if not (Path(schema_dir) / "gschemas.compiled").exists():
        raise FileNotFoundError(
            f"No compiled settings schema in {schema_dir}. Run install/install-user.sh first."
        )
    source = Gio.SettingsSchemaSource.new_from_directory(
        str(schema_dir), Gio.SettingsSchemaSource.get_default(), False
    )
    schema = source.lookup(SCHEMA_ID, False)
    if schema is None:
        raise FileNotFoundError(
            f"Schema {SCHEMA_ID} not found in {schema_dir}. Run install/install-user.sh first."
        )
    return Gio.Settings.new_full(schema, backend, None)


class GSettingsStore:
    """A Gio.Settings object in the shape the core expects
    (core.interfaces.Settings): plain Python values in and out."""

    def __init__(self, settings: Gio.Settings):
        self._settings = settings
        self._handlers: dict[Callable[[str], None], int] = {}

    def get(self, key: str) -> Any:
        return self._settings.get_value(key).unpack()

    def set(self, key: str, value: Any) -> None:
        kind = self._settings.get_value(key).get_type_string()
        self._settings.set_value(key, GLib.Variant(kind, value))

    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._handlers[callback] = self._settings.connect(
            "changed", lambda _settings, key: callback(key)
        )

    def unsubscribe(self, callback: Callable[[str], None]) -> None:
        handler = self._handlers.pop(callback, None)
        if handler is not None:
            self._settings.disconnect(handler)
