# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from rgbhub.platforms.gnome.gsettings_store import load_settings


def test_defaults(settings):
    assert settings.get_value("device-enabled").unpack() == {}
    assert settings.get_value("auto-devices").unpack() == {}
    assert settings.get_string("default-look") == "{}"
    assert settings.get_boolean("lcd-enabled") is True
    assert settings.get_int("lcd-brightness") == 80
    assert settings.get_string("lcd-image") == ""
    assert settings.get_string("lcd-fit") == "fill"
    assert settings.get_strv("lcd-recent-images") == []
    assert settings.get_string("status") == "{}"
    assert settings.get_value("device-modes").unpack() == {}
    assert settings.get_value("zone-colours").unpack() == {}


def test_missing_compiled_schema(tmp_path):
    with pytest.raises(FileNotFoundError, match="install-user.sh"):
        load_settings(tmp_path)


def test_store_reads_and_writes_plain_python_values(settings):
    from rgbhub.platforms.gnome.gsettings_store import GSettingsStore

    store = GSettingsStore(settings)
    assert store.get("device-enabled") == {} and store.get("zone-colours") == {}
    store.set("zone-colours", {"Case/Logo": "#FF0000"})
    store.set("device-speeds", {"Case": 70})
    store.set("lcd-recent-images", ["/a.png"])
    store.set("auto-devices", {"OMEN-PC": True})
    assert store.get("auto-devices") == {"OMEN-PC": True}
    store.set("status", "{}")
    assert store.get("zone-colours") == {"Case/Logo": "#FF0000"}
    assert store.get("device-speeds") == {"Case": 70} and store.get("lcd-recent-images") == [
        "/a.png"
    ]


def test_store_reports_changes_until_unsubscribed(settings):
    from gi.repository import GLib

    from rgbhub.platforms.gnome.gsettings_store import GSettingsStore

    store, seen = GSettingsStore(settings), []
    store.subscribe(seen.append)
    store.set("lcd-brightness", 10)
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass
    assert seen == ["lcd-brightness"]
    store.unsubscribe(seen.append)
    store.set("lcd-brightness", 20)
    while context.iteration(False):
        pass
    assert seen == ["lcd-brightness"]


def test_gsettings_schema_matches_the_list_of_settings(settings):
    # settings_schema.py is the contract with every front end; the GSettings
    # schema is GNOME's copy of it and must not drift.
    from rgbhub.core.settings_schema import DEFAULTS

    schema = settings.props.settings_schema
    assert sorted(schema.list_keys()) == sorted(DEFAULTS)
    for key, default in DEFAULTS.items():
        assert settings.get_default_value(key).unpack() == default, key


def test_table_value_kinds_match_the_gsettings_schema(settings):
    # The JSON store checks table entries against TABLE_VALUES; GSettings
    # checks them against its schema. Both must agree.
    from rgbhub.core.settings_schema import DEFAULTS, TABLE_VALUES

    letters = {bool: "b", int: "i", str: "s"}
    tables = {key for key, default in DEFAULTS.items() if isinstance(default, dict)}
    assert set(TABLE_VALUES) == tables
    for key, kind in TABLE_VALUES.items():
        assert settings.get_value(key).get_type_string() == "a{s" + letters[kind] + "}", key
