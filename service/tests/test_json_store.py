# SPDX-License-Identifier: GPL-3.0-or-later
import json

from rgbhub.core.json_store import JsonSettings
from rgbhub.core.settings_schema import DEFAULTS


def test_missing_file_gives_defaults(tmp_path):
    store = JsonSettings(tmp_path / "settings.json")
    assert store.get("lcd-brightness") == 80 and store.get("zone-colours") == {}
    assert all(store.get(key) == default for key, default in DEFAULTS.items())


def test_platform_can_have_its_own_defaults(tmp_path):
    store = JsonSettings(tmp_path / "settings.json", defaults={"auto-lcd": False})
    assert store.get("auto-lcd") is False


def test_values_come_from_the_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lcd-brightness": 30, "auto-idle-colour": "#00AA00"}))
    store = JsonSettings(path)
    assert store.get("lcd-brightness") == 30 and store.get("auto-idle-colour") == "#00AA00"


def test_set_saves_and_tells_subscribers(tmp_path):
    path = tmp_path / "dir" / "settings.json"  # the folder is created as needed
    store, seen = JsonSettings(path), []
    store.subscribe(seen.append)
    store.set("lcd-brightness", 55)
    assert seen == ["lcd-brightness"]
    assert json.loads(path.read_text()) == {"lcd-brightness": 55}  # only what differs from defaults
    store.set("lcd-brightness", 55)
    assert seen == ["lcd-brightness"]  # unchanged: nobody is told again
    store.unsubscribe(seen.append)
    store.set("lcd-brightness", 60)
    assert seen == ["lcd-brightness"]


def test_status_and_focus_are_kept_in_memory_not_in_the_file(tmp_path):
    # They change constantly; the file is for the owner's choices.
    path = tmp_path / "settings.json"
    store, seen = JsonSettings(path), []
    store.subscribe(seen.append)
    store.set("status", '{"lcd": true}')
    store.set("focused-app", "/userdata/roms/snes/Game.zip")
    assert store.get("focused-app") == "/userdata/roms/snes/Game.zip"
    assert seen == ["status", "focused-app"] and not path.exists()


def test_reload_picks_up_edits_made_by_hand(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lcd-brightness": 30}))
    store, seen = JsonSettings(path), []
    store.subscribe(seen.append)
    path.write_text(json.dumps({"lcd-brightness": 30, "device-brightness": {"OMEN-PC": 40}}))
    store.reload()
    assert seen == ["device-brightness"] and store.get("device-brightness") == {"OMEN-PC": 40}


def test_bad_files_and_bad_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json")
    assert JsonSettings(path).get("lcd-brightness") == 80
    path.write_text(json.dumps({"lcd-brightness": "loud", "no-such-setting": 1, "lcd-fit": "fit"}))
    store = JsonSettings(path)
    assert store.get("lcd-brightness") == 80 and store.get("lcd-fit") == "fit"
    path.write_text(json.dumps(["not", "an", "object"]))
    assert JsonSettings(path).get("lcd-brightness") == 80


def test_a_broken_file_keeps_the_previous_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lcd-brightness": 30}))
    store, seen = JsonSettings(path), []
    store.subscribe(seen.append)
    path.write_text('{"lcd-brightness": 3')  # caught half-saved, or a typo
    store.reload()
    assert store.get("lcd-brightness") == 30 and seen == []  # nothing snapped back to defaults
    path.write_text(json.dumps({"lcd-brightness": 40}))
    store.reload()
    assert store.get("lcd-brightness") == 40 and seen == ["lcd-brightness"]


def test_a_deleted_file_means_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lcd-brightness": 30}))
    store = JsonSettings(path)
    path.unlink()
    store.reload()
    assert store.get("lcd-brightness") == 80


def test_values_inside_tables_are_checked_too(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "zone-colours": {"RAM/Stick 1": 255},  # a number where a colour belongs
                "device-modes": {"RAM": ["Static"]},  # a list where a name belongs
                "device-brightness": {"OMEN-PC": "loud"},  # text where a number belongs
                "device-enabled": {"OMEN-PC": 1},  # a number where on/off belongs
                "auto-devices": {"OMEN-PC": False},
                "lcd-recent-images": ["a.png", 3],
                "device-speeds": {"RAM": 50},
            }
        )
    )
    store = JsonSettings(path)
    for key in ("zone-colours", "device-modes", "device-brightness", "device-enabled"):
        assert store.get(key) == {}, key
    assert store.get("lcd-recent-images") == []
    assert store.get("device-speeds") == {"RAM": 50}
    assert store.get("auto-devices") == {"OMEN-PC": False}


def test_problems_are_reported_once_per_version_of_the_file(tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lcd-brightnes": 30}))  # misspelt
    store = JsonSettings(path)
    for _ in range(5):
        store.reload()
    assert sum("lcd-brightnes" in r.getMessage() for r in caplog.records) == 1
    path.write_text(json.dumps({"lcd-brightnes": 31}))
    store.reload()
    assert sum("lcd-brightnes" in r.getMessage() for r in caplog.records) == 2


def test_a_front_ends_own_notes_are_kept_when_writing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"_seen": {"omen_x": "1"}, "lcd-brightness": 30}))
    store = JsonSettings(path)
    store.set("default-action", "save")
    data = json.loads(path.read_text())
    assert data["_seen"] == {"omen_x": "1"} and data["default-action"] == "save"


def test_saving_keeps_a_front_ends_edit_not_yet_reloaded(tmp_path):
    # A front end saves the file; before our watch notices, the hub saves a
    # setting of its own. The front end's change must survive, and be heard.
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lcd-brightness": 30}))
    store, seen = JsonSettings(path), []
    store.subscribe(seen.append)
    path.write_text(json.dumps({"lcd-brightness": 30, "device-brightness": {"OMEN-PC": 40}}))
    store.set("default-action", "")  # unchanged: nothing written, but the edit is read
    store.set("default-look", '{"lcd-brightness": 30}')
    data = json.loads(path.read_text())
    assert data["device-brightness"] == {"OMEN-PC": 40} and "default-look" in data
    assert seen == ["device-brightness", "default-look"]
