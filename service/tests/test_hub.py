# SPDX-License-Identifier: GPL-3.0-or-later
"""The hub with several devices: per-device auto, the Default look, status."""

import json

from rgbhub.core.hub import Hub
from rgbhub.core.look import Look
from rgbhub.core.settings_schema import DEFAULT_KEYS, migrate, parse_default
from rgbhub.devices.iqunix_keyboard import IqunixKeyboard


class MemorySettings:
    def __init__(self, **values):
        from rgbhub.core.settings_schema import DEFAULTS

        self.values = {**DEFAULTS, **values}
        self.subscribers = []

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        for callback in list(self.subscribers):
            callback(key)

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def unsubscribe(self, callback):
        self.subscribers.remove(callback)


class FakeLights:
    def __init__(self):
        self.connected = False
        self.applied = []
        self.stored = 0

    def try_connect(self):
        self.connected = True
        return True

    def check(self):
        return self.connected

    def devices(self):
        return []

    def forget(self):
        pass

    def apply(self, enabled, brightness, modes, colours, options=None):
        self.applied.append((enabled, brightness, dict(modes), colours.get("K/All keys")))

    def store(self):
        self.stored += 1


class FakeLooks:
    def is_ignored(self, subject):
        return False

    def look_for(self, subject):
        return Look("#00FF00", b"jpeg") if subject == "game" else None

    def idle_look(self, path):
        return None


def keyboard_hub(**values):
    settings = MemorySettings(**values)
    lights = FakeLights()
    keyboard = IqunixKeyboard(lights)
    hub = Hub(settings, [keyboard], looks=FakeLooks())
    return hub, settings, lights, keyboard


def test_each_device_has_its_own_auto_switch():
    hub, settings, lights, _ = keyboard_hub(
        **{"zone-colours": {"K/All keys": "#800080"}, "focused-app": "game"}
    )
    hub.start()
    assert lights.applied[-1][3] == "#800080"  # auto is off for the keyboard
    settings.set("auto-devices", {"OMEN-PC": True})
    assert not hub.auto_for("IQUNIX-KEYBOARD")
    settings.set("auto-devices", {"OMEN-PC": True, "IQUNIX-KEYBOARD": True})
    assert hub.auto_for("IQUNIX-KEYBOARD")
    for device in hub.devices:
        device.lights.flush()
    assert lights.applied[-1][3] == "#00FF00"  # the game's colour
    hub.stop(show_default=False)


def test_per_device_brightness_and_on_off():
    hub, settings, lights, _ = keyboard_hub(
        **{"device-enabled": {"IQUNIX-KEYBOARD": False}, "device-brightness": {"OMEN-PC": 10}}
    )
    hub.start()
    assert lights.applied[-1][:2] == (False, 100)  # OMEN-PC's brightness is not ours
    hub.stop(show_default=False)


def test_save_and_load_the_default_look():
    hub, settings, _lights, _ = keyboard_hub(**{"device-brightness": {"IQUNIX-KEYBOARD": 40}})
    hub.start()
    settings.set("default-action", "save")
    assert settings.get("default-action") == ""
    saved = json.loads(settings.get("default-look"))
    assert sorted(saved) == sorted(DEFAULT_KEYS) and saved["device-brightness"] == {
        "IQUNIX-KEYBOARD": 40
    }
    settings.set("device-brightness", {"IQUNIX-KEYBOARD": 90})
    settings.set("default-action", "load")
    assert settings.get("device-brightness") == {"IQUNIX-KEYBOARD": 40}
    assert json.loads(settings.get("status"))["has-default"] is True
    hub.stop(show_default=False)


def test_a_request_left_while_the_hub_was_not_running_is_carried_out():
    hub, settings, _lights, _ = keyboard_hub(
        **{"device-brightness": {"IQUNIX-KEYBOARD": 40}, "default-action": "save"}
    )
    hub.start()
    assert settings.get("default-action") == ""
    assert json.loads(settings.get("default-look"))["device-brightness"] == {"IQUNIX-KEYBOARD": 40}
    hub.stop(show_default=False)


def test_stopping_shows_the_default_without_auto_and_stores_it():
    default = {"zone-colours": {"K/All keys": "#800080"}, "device-modes": {"K": "Breathing"}}
    hub, settings, lights, _ = keyboard_hub(
        **{
            "default-look": json.dumps(default),
            "auto-devices": {"IQUNIX-KEYBOARD": True},
            "focused-app": "game",
            "zone-colours": {"K/All keys": "#FFFFFF"},
        }
    )
    hub.start()
    assert lights.applied[-1][3] == "#00FF00"  # following the game
    hub.stop()
    enabled, _brightness, modes, colour = lights.applied[-1]
    assert colour == "#800080" and enabled
    assert modes == {"K": "Static"}  # a breath drawn by us would freeze mid-way
    assert lights.stored == 1
    assert settings.get("status") == "{}"
    assert settings.get("zone-colours") == {"K/All keys": "#FFFFFF"}  # the settings are untouched


def test_stopping_without_a_saved_default_goes_purple():
    from rgbhub.core.hub import FACTORY_COLOUR

    hub, settings, lights, _ = keyboard_hub(
        **{
            "auto-devices": {"IQUNIX-KEYBOARD": True},
            "focused-app": "game",
            "zone-colours": {"K/All keys": "#FFFFFF"},
            "device-enabled": {"IQUNIX-KEYBOARD": False},
            "device-brightness": {"IQUNIX-KEYBOARD": 20},
        }
    )
    hub.start()
    hub.stop()
    enabled, brightness, _modes, colour = lights.applied[-1]
    assert colour == FACTORY_COLOUR and enabled and brightness == 100
    assert lights.stored == 1  # the keyboard keeps it
    assert settings.get("zone-colours") == {"K/All keys": "#FFFFFF"}  # the settings are untouched
    assert settings.get("default-look") == "{}"


def test_the_stop_look_puts_the_bundled_picture_on_the_lcd():
    """The LCD keeps its last frame, so the stop look ends on a picture we ship."""
    from pathlib import Path

    from rgbhub.core.hub import STOP_PICTURE, factory_look

    assert STOP_PICTURE.is_file()
    look = factory_look()
    assert Path(look["lcd-image"]) == STOP_PICTURE
    assert look["lcd-enabled"] and look["lcd-brightness"] == 100


def test_omen_show_default_sends_the_picture_as_the_last_frame():
    from pathlib import Path

    from rgbhub.core.hub import STOP_PICTURE, factory_look
    from rgbhub.devices.omen_pc.device import OmenPc

    class Streamer:
        def __init__(self):
            self.image, self.frames_sent, self.brightness = None, 0, None

        def set_image(self, jpeg):
            self.image = jpeg

        def set_brightness(self, percent):
            self.brightness = percent

        def tick_claim(self):
            pass

        def tick_frame(self):
            self.frames_sent += 1

    class StopHub:
        stopping, asleep = True, False

        def get(self, key):
            return factory_look()[key]

    class Group:
        def finish_now(self):
            self.finished = True

        def keep(self):
            pass

    streamer = Streamer()
    pc = OmenPc(streamer, lights=None, prepare=lambda path, fit: Path(path).read_bytes())
    pc._hub, pc.lights = StopHub(), Group()
    pc.show_default()
    assert streamer.image == STOP_PICTURE.read_bytes() and streamer.frames_sent == 1
    assert streamer.brightness == 100 and pc.lights.finished


def test_stopping_with_no_default_shows_the_ram_steady_purple():
    """The RAM's own default is Rainbow, which ignores colours: it must get Static."""
    from rgbhub.core.hub import FACTORY_COLOUR, factory_look
    from rgbhub.core.light_group import LightGroup

    class Hub_:
        stopping, asleep = True, False

        def get(self, key):
            return factory_look().get(key, {})

    class Slow:
        def devices(self):
            return []

    slow = Slow()
    group = LightGroup(Hub_(), "OMEN-PC", [slow], lambda: None, slow=(slow,))
    enabled, brightness, modes, colours, _options = group._for(slow, group.settings())
    assert enabled and brightness == 100
    assert modes.get("RAM", "Rainbow") == "Static"
    assert colours.get("RAM: Stick 1") == FACTORY_COLOUR


def test_a_failing_device_does_not_stop_the_others():
    class Broken(IqunixKeyboard):
        id = label = "BROKEN"

        def show_default(self):
            raise RuntimeError("boom")

    settings = MemorySettings()
    good = FakeLights()
    hub = Hub(settings, [Broken(FakeLights()), IqunixKeyboard(good)])
    hub.start()
    hub.stop()
    assert good.stored == 1


def test_status_lists_every_device():
    hub, settings, _lights, _ = keyboard_hub()
    hub.start()
    report = json.loads(settings.get("status"))
    assert report["devices"] == [
        {
            "id": "IQUNIX-KEYBOARD",
            "label": "IQUNIX-KEYBOARD",
            "icon": "input-keyboard-symbolic",
            "has-lcd": False,
            "connected": True,
            "lights": [],
        }
    ]
    hub.stop(show_default=False)


def test_old_settings_are_carried_over():
    new = migrate(
        {
            "lights-enabled": False,
            "lights-brightness": 60,
            "auto-enabled": True,
            "lcd-image": "/a.png",
            "status": "{...}",
            "nonsense": 1,
        }
    )
    assert new == {
        "device-enabled": {"OMEN-PC": False},
        "device-brightness": {"OMEN-PC": 60},
        "auto-devices": {"OMEN-PC": True, "IQUNIX-KEYBOARD": True},
        "lcd-image": "/a.png",
    }


def test_a_damaged_default_is_ignored():
    assert parse_default("not json") == {}
    assert parse_default('{"lcd-brightness": "loud", "lcd-fit": "fit", "x": 1}') == {
        "lcd-fit": "fit"
    }
    # Inside tables too: a number where a colour belongs would break the lights.
    assert parse_default('{"zone-colours": {"RAM/Stick 1": 255}, "device-speeds": {"RAM": 9}}') == {
        "device-speeds": {"RAM": 9}
    }


def test_batocera_moves_the_old_folder_and_settings(tmp_path):
    from rgbhub.platforms.batocera import migrate_old_config

    old, new = tmp_path / "omen-lights", tmp_path / "rgb-hub"
    (old / "pictures").mkdir(parents=True)
    (old / "settings.json").write_text(
        json.dumps({"lights-brightness": 50, "lcd-image": f"{old}/pictures/spike.png"})
    )
    migrate_old_config(old, new / "settings.json")
    assert not old.exists() and (new / "pictures").is_dir()
    data = json.loads((new / "settings.json").read_text())
    assert data["device-brightness"] == {"OMEN-PC": 50}
    assert data["lcd-image"] == f"{new}/pictures/spike.png"
    assert json.loads(data["default-look"])["lcd-image"] == f"{new}/pictures/spike.png"


def test_gnome_reads_the_old_dconf_dump():
    from rgbhub.platforms.gnome.gnome_migrate import parse_dump

    dump = "[/]\nauto-enabled=true\ndevice-speeds={'RAM': 35}\nlcd-image='/a b.jpg'\n"
    assert parse_dump(dump) == {
        "auto-enabled": True,
        "device-speeds": {"RAM": 35},
        "lcd-image": "/a b.jpg",
    }


# --- a front end that never reports the focused app is noticed -----------------


def watched_hub(auto=True, **values):
    from rgbhub.core.interfaces import ManualScheduler

    settings = MemorySettings(**{"auto-devices": {"IQUNIX-KEYBOARD": auto}}, **values)
    scheduler = ManualScheduler()
    hub = Hub(
        settings,
        [IqunixKeyboard(FakeLights())],
        looks=FakeLooks(),
        scheduler=scheduler,
        focus_report_wait_s=180,
        focus_report_hint="Is the extension switched on?",
    )
    return hub, settings, scheduler


def focus_watches(scheduler):
    return [callback for ms, callback in scheduler.timers.values() if ms == 180_000]


def run_out_the_wait(scheduler):
    for handle, (ms, callback) in list(scheduler.timers.items()):
        if ms == 180_000 and callback() is False:
            scheduler.cancel(handle)  # as GLib does with a timer that returns False


def test_no_focus_report_after_start_is_warned_about(caplog):
    hub, _settings, scheduler = watched_hub()
    hub.start()
    assert len(focus_watches(scheduler)) == 1
    run_out_the_wait(scheduler)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "focus" in warnings[0].getMessage()
    assert "Is the extension switched on?" in warnings[0].getMessage()
    assert focus_watches(scheduler) == []  # once only
    hub.stop(show_default=False)


def test_a_focus_report_in_time_means_no_warning(caplog):
    hub, settings, scheduler = watched_hub()
    hub.start()
    settings.set("focused-app", "org.gnome.Terminal.desktop")
    assert focus_watches(scheduler) == []
    run_out_the_wait(scheduler)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    hub.stop(show_default=False)


def test_an_empty_report_counts_too(caplog):
    # "Nothing in focus" is a report: the extension is there.
    hub, settings, scheduler = watched_hub(**{"focused-app": "x.desktop"})
    hub.start()
    settings.set("focused-app", "")
    run_out_the_wait(scheduler)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_no_wait_while_auto_mode_is_off_then_one_when_it_comes_on(caplog):
    # The extension writes nothing while no device follows the app.
    hub, settings, scheduler = watched_hub(auto=False)
    hub.start()
    assert focus_watches(scheduler) == []
    settings.set("auto-devices", {"IQUNIX-KEYBOARD": True})
    assert len(focus_watches(scheduler)) == 1
    settings.set("auto-devices", {"IQUNIX-KEYBOARD": True, "OMEN-PC": True})
    assert len(focus_watches(scheduler)) == 1  # not a second one
    run_out_the_wait(scheduler)
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1


def test_auto_switched_off_meanwhile_means_no_warning(caplog):
    hub, settings, scheduler = watched_hub()
    hub.start()
    settings.set("auto-devices", {"IQUNIX-KEYBOARD": False})
    run_out_the_wait(scheduler)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_stopping_ends_the_wait():
    hub, _settings, scheduler = watched_hub()
    hub.start()
    hub.stop(show_default=False)
    assert focus_watches(scheduler) == []


def test_without_a_wait_given_nothing_is_watched():
    hub, _settings, _lights, _ = keyboard_hub(**{"auto-devices": {"IQUNIX-KEYBOARD": True}})
    hub.start()
    assert focus_watches(hub.scheduler) == []


def test_gnome_waits_for_its_extension():
    from rgbhub.platforms import gnome

    assert 30 <= gnome.FOCUS_REPORT_WAIT_S <= 600
    assert f"gnome-extensions enable {gnome.EXTENSION_UUID}" in gnome.FOCUS_REPORT_HINT
    source = __import__("inspect").getsource(gnome.main)
    assert "focus_report_wait_s=FOCUS_REPORT_WAIT_S" in source
    assert "focus_report_hint=FOCUS_REPORT_HINT" in source
