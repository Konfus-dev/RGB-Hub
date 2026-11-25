# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path

import pytest
from gi.repository import GLib

from rgbhub.core.hub import Hub
from rgbhub.core.image_prep import ImageError
from rgbhub.core.lights import DeviceInfo, EffectOptions
from rgbhub.devices.omen_pc import OmenPc
from rgbhub.platforms.gsettings_store import GSettingsStore as Store


class Rig:
    """A hub with one OMEN-PC device, reached as one object as the old
    Service was: hub calls first, then the device's, then its lights'."""

    def __init__(self, hub, pc):
        object.__setattr__(self, "hub", hub)
        object.__setattr__(self, "pc", pc)

    def __setattr__(self, name, value):
        setattr(self.hub if name == "_looks" else self.pc, name, value)

    def __getattr__(self, name):
        aliases = {
            "flush_lights": self.pc.lights.flush,
            "lights_update_pending": None,
            "animation_running": None,
            "_asleep": None,
        }
        if name == "lights_update_pending":
            return self.pc.lights.update_pending
        if name == "animation_running":
            return self.pc.lights.animation_running
        if name == "_asleep":
            return self.hub.asleep
        if name in aliases:
            return aliases[name]
        if name in ("stop",):
            return lambda: self.hub.stop(show_default=False)
        for target in (self.hub, self.pc):
            if hasattr(target, name):
                return getattr(target, name)
        raise AttributeError(name)


def Service(
    store,
    streamer,
    lights,
    prepare=None,
    ram=None,
    display_names=None,
    looks=None,
    fade=None,
    scheduler=None,
    watch_file=None,
    **clocks,
):
    kwargs = {"prepare": prepare} if prepare is not None else {}
    pc = OmenPc(
        streamer,
        lights,
        ram=ram,
        display_names=display_names,
        fade=fade,
        watch_file=watch_file,
        **kwargs,
    )
    return Rig(Hub(store, [pc], looks=looks, scheduler=scheduler, **clocks), pc)


def set_auto(settings, on=True):
    settings.set_value("auto-devices", GLib.Variant("a{sb}", {"OMEN-PC": on}))


def lights_on(settings, on=True):
    settings.set_value("device-enabled", GLib.Variant("a{sb}", {"OMEN-PC": on}))


def brightness(settings, percent):
    settings.set_value("device-brightness", GLib.Variant("a{si}", {"OMEN-PC": percent}))


class FakeStreamer:
    def __init__(self):
        self.connected = True
        self.images = []
        self.brightness = []
        self.reconnect_ticks = 0

    def set_image(self, jpeg):
        self.images.append(jpeg)

    def set_brightness(self, percent):
        self.brightness.append(percent)

    def tick_reconnect(self):
        self.reconnect_ticks += 1

    def tick_claim(self):
        self.claim_ticks = getattr(self, "claim_ticks", 0) + 1

    def tick_frame(self):
        self.frame_ticks = getattr(self, "frame_ticks", 0) + 1


class FakeLights:
    def __init__(self, available=True, name="Case"):
        self.name = name
        self.available = available  # what try_connect will answer
        self.connected = False
        self.applied = []

    def try_connect(self):
        self.connected = self.available
        return self.connected

    def check(self):
        self.connected = self.connected and self.available
        return self.connected

    animating = False  # tests set this to pretend a breath is running

    forgets = 0  # how many times the service said "the hardware may have forgotten"

    def forget(self):
        self.forgets += 1

    def finish_transitions(self):
        self.finished = True

    def refresh(self):
        self.refreshed = getattr(self, "refreshed", 0) + 1

    def wait_idle(self, timeout_s):
        self.waited = True
        return True

    def tick_animation(self, now):
        self.animation_ticks = getattr(self, "animation_ticks", 0) + 1

    def devices(self):
        if not self.connected:
            return []
        return [
            DeviceInfo(
                self.name,
                ("Static", "Off"),
                ("Logo", "Top Fan"),
                default_mode="Static",
                colourless_modes=("Off",),
            )
        ]

    def apply(self, enabled, brightness, modes, colours, options=None):
        self.applied.append((enabled, brightness, modes, colours))
        self.options = options


class FakePrepare:
    def __init__(self):
        self.calls = []
        self.error = None

    def __call__(self, path, fit):
        self.calls.append((path, fit))
        if self.error:
            raise ImageError(self.error)
        return f"jpeg of {path.name} ({fit}){getattr(self, 'version', '')}".encode()


@pytest.fixture
def parts(settings):
    settings.set_string("lcd-image", "/pics/cat.png")
    streamer, lights, prepare = FakeStreamer(), FakeLights(), FakePrepare()
    service = Service(Store(settings), streamer, lights, prepare)
    yield service, settings, streamer, lights, prepare
    service.stop()


def status(settings):
    """The status report, with the OMEN-PC's part flattened as it was
    before there were several devices."""
    report = json.loads(settings.get_string("status"))
    if not report:
        return report
    pc = report["devices"][0]
    return {
        "lcd": pc["lcd"],
        "openrgb": pc["case"],
        "ram": pc["ram"],
        "error": pc["error"],
        "auto": report["auto"],
        "devices": pc["lights"],
    }


def test_start_applies_everything_once(parts):
    service, settings, streamer, lights, prepare = parts
    service.start()
    assert prepare.calls == [(Path("/pics/cat.png"), "fill")]
    assert streamer.images == [b"jpeg of cat.png (fill)"]
    assert streamer.brightness == [80]
    assert lights.applied == [(True, 100, {}, {})]
    assert status(settings) == {
        "lcd": True,
        "openrgb": True,
        "ram": False,
        "error": "",
        "auto": {"app": "", "colour": ""},
        "devices": [
            {
                "name": "Case",
                "label": "Case",
                "modes": ["Static", "Off"],
                "default_mode": "Static",
                "colourless_modes": ["Off"],
                "speed_modes": [],
                "range_modes": [],
                "zones": ["Logo", "Top Fan"],
                "zone_labels": ["Logo", "Top Fan"],
            }
        ],
    }


def test_image_change_calls_prepare_and_set_image(parts):
    service, settings, streamer, _lights, prepare = parts
    service.start()
    settings.set_string("lcd-image", "/pics/dog.png")
    service.on_changed("lcd-image")
    assert prepare.calls[-1] == (Path("/pics/dog.png"), "fill")
    assert streamer.images[-1] == b"jpeg of dog.png (fill)"


def test_fit_change_reprepares_image(parts):
    service, settings, streamer, _lights, _prepare = parts
    service.start()
    settings.set_string("lcd-fit", "fit")
    service.on_changed("lcd-fit")
    assert streamer.images[-1] == b"jpeg of cat.png (fit)"


def test_bad_image_keeps_previous_and_reports(parts):
    service, settings, streamer, _lights, prepare = parts
    service.start()
    prepare.error = "boom"
    settings.set_string("lcd-image", "/pics/broken.png")
    service.on_changed("lcd-image")
    assert streamer.images == [b"jpeg of cat.png (fill)"]  # nothing new was sent
    assert status(settings)["error"] == "boom"


def test_good_image_clears_error(parts):
    service, settings, _streamer, _lights, prepare = parts
    service.start()
    prepare.error = "boom"
    service.on_changed("lcd-image")
    prepare.error = None
    service.on_changed("lcd-image")
    assert status(settings)["error"] == ""


def test_empty_image_path_clears_image(parts):
    service, settings, streamer, _lights, prepare = parts
    service.start()
    settings.set_string("lcd-image", "")
    service.on_changed("lcd-image")
    assert streamer.images[-1] is None
    assert len(prepare.calls) == 1  # prepare was not asked to open ""


def test_lcd_disabled_means_brightness_zero(parts):
    service, settings, streamer, _lights, _prepare = parts
    service.start()
    settings.set_boolean("lcd-enabled", False)
    service.on_changed("lcd-enabled")
    assert streamer.brightness[-1] == 0
    settings.set_boolean("lcd-enabled", True)
    service.on_changed("lcd-enabled")
    assert streamer.brightness[-1] == 80


def test_lcd_brightness_change(parts):
    service, settings, streamer, _lights, _prepare = parts
    service.start()
    settings.set_int("lcd-brightness", 35)
    service.on_changed("lcd-brightness")
    assert streamer.brightness[-1] == 35


def test_light_keys_trigger_apply(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    lights_on(settings, False)
    brightness(settings, 40)
    settings.set_value("device-modes", GLib.Variant("a{ss}", {"Case": "Off"}))
    settings.set_value("zone-colours", GLib.Variant("a{ss}", {"Case/Logo": "#FF0000"}))
    for key in ("device-enabled", "device-brightness", "device-modes", "zone-colours"):
        before = len(lights.applied)
        service.on_changed(key)
        service.flush_lights()
        assert len(lights.applied) == before + 1
    assert lights.applied[-1] == (False, 40, {"Case": "Off"}, {"Case/Logo": "#FF0000"})


def test_status_key_change_is_ignored(parts):
    service, _settings, streamer, lights, prepare = parts
    service.start()
    service.on_changed("status")
    assert len(lights.applied) == 1 and len(prepare.calls) == 1 and len(streamer.images) == 1


def test_lights_reconnect_reapplies(settings):
    lights = FakeLights(available=False)
    service = Service(Store(settings), FakeStreamer(), lights, FakePrepare())
    service.start()
    assert lights.applied == [] and status(settings)["openrgb"] is False
    lights.available = True
    service.tick_slow()
    assert len(lights.applied) == 1 and status(settings)["openrgb"] is True
    service.tick_slow()
    assert len(lights.applied) == 1  # already connected: nothing to redo
    service.stop()


def test_openrgb_down_does_not_touch_lcd(settings):
    streamer = FakeStreamer()
    service = Service(Store(settings), streamer, FakeLights(available=False), FakePrepare())
    service.start()
    service.tick_slow()
    assert streamer.reconnect_ticks >= 1
    assert status(settings)["lcd"] is True and status(settings)["openrgb"] is False
    service.stop()


def test_status_written_only_when_changed(parts):
    service, settings, _streamer, _lights, _prepare = parts
    service.start()
    writes = []
    settings.connect("changed::status", lambda *_: writes.append(1))
    service.publish_status()
    service.publish_status()
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass
    assert writes == []  # start() already published this exact status


def test_openrgb_going_away_is_noticed_and_recovered(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    lights.available = False  # OpenRGB stops
    service.tick_slow()
    assert status(settings)["openrgb"] is False and status(settings)["devices"] == []
    lights.available = True  # OpenRGB comes back
    service.tick_slow()
    assert status(settings)["openrgb"] is True
    assert len(lights.applied) == 2  # lights were set again after the restart


@pytest.fixture
def with_ram(settings):
    ram = FakeLights(name="RAM")
    service = Service(Store(settings), FakeStreamer(), FakeLights(), FakePrepare(), ram=ram)
    yield service, settings, ram
    service.stop()


def test_ram_is_listed_after_the_case_lights(with_ram):
    service, settings, _ram = with_ram
    service.start()
    assert status(settings)["ram"] is True
    assert [d["name"] for d in status(settings)["devices"]] == ["Case", "RAM"]


def test_ram_gets_the_same_settings_as_the_case_lights(with_ram):
    service, settings, ram = with_ram
    service.start()
    assert ram.applied == [(True, 100, {}, {})]
    brightness(settings, 30)
    service.on_changed("device-brightness")
    service.flush_lights()
    assert ram.applied[-1] == (True, 30, {}, {})


def test_ram_changes_are_batched(with_ram):
    # Dragging a slider fires many changes; the RAM is slow to update, so
    # only the final value is sent.
    service, settings, ram = with_ram
    service.start()
    for value in (90, 80, 70):
        brightness(settings, value)
        service.on_changed("device-brightness")
    assert len(ram.applied) == 1  # nothing sent yet
    service.flush_lights()
    assert len(ram.applied) == 2 and ram.applied[-1][1] == 70


def test_case_light_changes_are_batched_too(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    for value in (90, 80, 70):
        brightness(settings, value)
        service.on_changed("device-brightness")
    assert len(lights.applied) == 1
    service.flush_lights()
    assert len(lights.applied) == 2 and lights.applied[-1][1] == 70


def test_unexpected_image_failure_does_not_crash_the_service(parts):
    service, settings, streamer, _lights, prepare = parts

    def explode(path, fit):
        raise RuntimeError("something odd inside the image library")

    service._prepare = explode
    service.start()  # must not raise, or systemd would restart us forever
    assert streamer.images == [] and "something odd" in status(settings)["error"]


def test_a_failing_tick_does_not_stop_its_timer():
    def bad_tick():
        raise RuntimeError("boom")

    assert Hub.repeat(bad_tick) is True


def test_ram_helper_appearing_later_is_picked_up(settings):
    ram = FakeLights(available=False, name="RAM")
    service = Service(Store(settings), FakeStreamer(), FakeLights(), FakePrepare(), ram=ram)
    service.start()
    assert status(settings)["ram"] is False and ram.applied == []
    ram.available = True
    service.tick_slow()
    assert status(settings)["ram"] is True and len(ram.applied) == 1
    service.stop()


def test_status_uses_friendly_names_where_known(settings):
    service = Service(
        Store(settings),
        FakeStreamer(),
        FakeLights(),
        FakePrepare(),
        display_names={"Case": "Case lights", "Case/Top Fan": "Top fan"},
    )
    service.start()
    device = status(settings)["devices"][0]
    assert device["name"] == "Case" and device["label"] == "Case lights"
    assert device["zones"] == ["Logo", "Top Fan"]  # real names stay for saving settings
    assert device["zone_labels"] == ["Logo", "Top fan"]
    service.stop()


def test_effect_options_reach_both_kinds_of_light(with_ram):
    service, settings, ram = with_ram
    service.start()
    settings.set_value("device-speeds", GLib.Variant("a{si}", {"RAM": 80}))
    settings.set_value("device-min-brightness", GLib.Variant("a{si}", {"RAM": 10}))
    settings.set_value("device-max-brightness", GLib.Variant("a{si}", {"RAM": 90}))
    for key in ("device-speeds", "device-min-brightness", "device-max-brightness"):
        service.on_changed(key)
    service.flush_lights()
    assert ram.options["RAM"] == EffectOptions(speed=80, min_brightness=10, max_brightness=90)
    assert ram.options.get("Case", EffectOptions()) == EffectOptions()  # defaults elsewhere


def test_new_setting_defaults(settings):
    for key in ("device-speeds", "device-min-brightness", "device-max-brightness"):
        assert settings.get_value(key).unpack() == {}


def test_animation_timer_only_runs_while_something_is_animated(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    assert service.animation_running is False  # no 30-times-a-second wake-ups for nothing
    lights.animating = True
    service.flush_lights()
    assert service.animation_running is True
    lights.animating = False
    service.flush_lights()
    assert service.animation_running is False


def test_stopping_clears_the_status_so_the_window_knows(parts):
    service, settings, _streamer, _lights, _prepare = parts
    service.start()
    assert status(settings) != {}
    service.stop()
    assert settings.get_string("status") == "{}"


def test_edited_image_file_is_reloaded(parts):
    service, _settings, streamer, _lights, prepare = parts
    service.start()
    prepare.version = " v2"  # the file now has different contents
    service.on_image_file_changed()  # what the file monitor calls
    assert len(prepare.calls) == 2
    assert streamer.images == [b"jpeg of cat.png (fill)", b"jpeg of cat.png (fill) v2"]


def test_an_unchanged_picture_is_not_sent_to_the_lcd_again(parts):
    service, _settings, streamer, _lights, _prepare = parts
    service.start()
    service.on_image_file_changed()  # touched, but the picture is the same
    assert len(streamer.images) == 1


def test_resume_from_sleep_sends_everything_again(with_ram):
    service, _settings, ram = with_ram
    service.start()
    ram.forgotten = False
    ram.forget = lambda: setattr(ram, "forgotten", True)
    before = len(ram.applied)
    service.on_resume()
    assert ram.forgotten is True and len(ram.applied) == before + 1


# --- auto mode ----------------------------------------------------------------


class FakeLooks:
    """Stands in for AppLooks: firefox is orange, everything else unknown."""

    idle = None  # tests set this to give the OS an idle look

    def look_for(self, app_id):
        from rgbhub.core.look import Look

        return Look("#FF8800", b"firefox icon jpeg") if app_id == "firefox.desktop" else None

    def is_ignored(self, app_id):
        return app_id == "settings.desktop"

    def forget_idle(self):
        pass

    def idle_look(self, image_path=None):
        from rgbhub.core.look import Look

        if image_path:
            return Look("#00FF00", b"jpeg of " + image_path.encode())
        return self.idle


@pytest.fixture
def auto(settings):
    settings.set_string("lcd-image", "/pics/cat.png")
    settings.set_value("zone-colours", GLib.Variant("a{ss}", {"Case/Logo": "#0000FF"}))
    streamer, lights = FakeStreamer(), FakeLights()
    service = Service(Store(settings), streamer, lights, FakePrepare(), looks=FakeLooks())
    service.start()
    yield service, settings, streamer, lights
    service.stop()


def focus(service, settings, app_id):
    settings.set_string("focused-app", app_id)
    service.on_changed("focused-app")
    service.flush_lights()


def test_auto_mode_is_off_by_default(auto):
    service, settings, streamer, lights = auto
    focus(service, settings, "firefox.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_auto_mode_follows_the_focused_app(auto):
    service, settings, streamer, lights = auto
    set_auto(settings, True)
    service.on_changed("auto-devices")
    focus(service, settings, "firefox.desktop")
    assert streamer.images[-1] == b"firefox icon jpeg"
    colours = lights.applied[-1][3]
    assert colours.get("Case/Logo") == "#FF8800" and colours.get("Anything/Else") == "#FF8800"
    assert status(settings)["auto"] == {"app": "firefox.desktop", "colour": "#FF8800"}


def test_unknown_app_falls_back_to_the_normal_settings(auto):
    service, settings, streamer, lights = auto
    set_auto(settings, True)
    focus(service, settings, "firefox.desktop")
    focus(service, settings, "mystery.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}
    assert status(settings)["auto"] == {"app": "", "colour": ""}


def test_lights_and_lcd_can_follow_separately(auto):
    service, settings, streamer, lights = auto
    set_auto(settings, True)
    settings.set_boolean("auto-lcd", False)
    focus(service, settings, "firefox.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3].get("Case/Logo") == "#FF8800"
    settings.set_boolean("auto-lcd", True)
    settings.set_boolean("auto-lights", False)
    service.on_changed("auto-lcd")
    service.on_changed("auto-lights")
    service.flush_lights()
    assert streamer.images[-1] == b"firefox icon jpeg"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_switching_auto_off_restores_the_normal_look(auto):
    service, settings, streamer, lights = auto
    set_auto(settings, True)
    focus(service, settings, "firefox.desktop")
    set_auto(settings, False)
    service.on_changed("auto-devices")
    service.flush_lights()
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_switching_apps_does_not_reload_the_picture_from_disk(auto):
    service, settings, _streamer, _lights = auto
    set_auto(settings, True)
    service.on_changed("auto-devices")
    prepare = service._prepare
    before = len(prepare.calls)
    for app in ("mystery.desktop", "firefox.desktop", "other.desktop"):
        focus(service, settings, app)
    assert len(prepare.calls) == before  # decoding a big photo on every alt-tab would stutter


def test_case_lights_are_not_resent_when_the_auto_colour_is_unchanged(auto):
    service, settings, _streamer, lights = auto
    set_auto(settings, True)
    focus(service, settings, "mystery.desktop")
    settings.set_string("focused-app", "another-unknown.desktop")  # still the normal look
    service.on_changed("focused-app")
    assert service.lights_update_pending is False
    settings.set_string("focused-app", "firefox.desktop")  # now the colour changes
    service.on_changed("focused-app")
    assert service.lights_update_pending is True


def test_a_broken_picture_is_reported_even_while_an_app_icon_is_shown(auto):
    service, settings, _streamer, _lights = auto
    set_auto(settings, True)
    focus(service, settings, "firefox.desktop")
    service._prepare.error = "boom"
    service.on_image_file_changed()
    assert status(settings)["error"] == "boom"


def test_resume_also_makes_the_case_lights_start_over(parts):
    service, _settings, _streamer, lights, _prepare = parts
    service.start()
    lights.forgotten = False
    lights.forget = lambda: setattr(lights, "forgotten", True)
    service.on_resume()
    assert lights.forgotten is True


# --- sleep ----------------------------------------------------------------------


def test_going_to_sleep_turns_everything_off_without_delay(with_ram):
    service, _settings, ram = with_ram
    service.start()
    streamer, lights = service._streamer, service._lights
    service.on_sleep()
    assert streamer.brightness[-1] == 0 and streamer.claim_ticks >= 1  # sent now, not on a timer
    assert lights.applied[-1][0] is False and lights.finished is True  # off, no slow fade
    assert ram.applied[-1][0] is False and ram.waited is True  # off, and really sent


def test_waking_up_turns_everything_back_on(with_ram):
    service, settings, ram = with_ram
    service.start()
    service.on_sleep()
    service.on_resume()
    assert service._streamer.brightness[-1] == settings.get_int("lcd-brightness")
    assert service._lights.applied[-1][0] is True and ram.applied[-1][0] is True


def test_settings_changed_while_asleep_do_not_switch_the_lights_on(with_ram):
    service, settings, ram = with_ram
    service.start()
    service.on_sleep()
    brightness(settings, 40)
    service.on_changed("device-brightness")
    service.flush_lights()
    assert ram.applied[-1][0] is False


# --- the LCD fades from one picture to the next ---------------------------------


def fake_fade(old, new, steps):
    yield b"fade 1"
    yield b"fade 2"
    yield new if new is not None else b"black"


@pytest.fixture
def fading(settings):
    settings.set_string("lcd-image", "/pics/cat.png")
    streamer = FakeStreamer()
    service = Service(Store(settings), streamer, FakeLights(), FakePrepare(), fade=fake_fade)
    service.start()
    service.finish_lcd_fade()
    streamer.images.clear()
    yield service, settings, streamer
    service.stop()


def test_a_new_picture_fades_in_frame_by_frame(fading):
    service, settings, streamer = fading
    settings.set_string("lcd-image", "/pics/dog.png")
    service.on_changed("lcd-image")
    assert service.lcd_fading is True
    assert streamer.images == [b"fade 1"]  # the first frame goes out at once
    before = streamer.frame_ticks
    service.tick_lcd_fade()
    assert streamer.images == [b"fade 1", b"fade 2"] and streamer.frame_ticks == before + 1
    service.tick_lcd_fade()
    assert streamer.images[-1] == b"jpeg of dog.png (fill)"
    service.tick_lcd_fade()
    assert service.lcd_fading is False and len(streamer.images) == 3


def test_a_change_during_a_fade_starts_again_from_what_is_showing(fading):
    service, settings, streamer = fading
    seen = []

    def recording_fade(old, new, steps):
        seen.append(old)
        yield new

    settings.set_string("lcd-image", "/pics/dog.png")
    service.on_changed("lcd-image")  # now showing b"fade 1"
    service._fade = recording_fade
    settings.set_string("lcd-image", "/pics/bird.png")
    service.on_changed("lcd-image")
    assert seen == [b"fade 1"]
    service.finish_lcd_fade()
    assert streamer.images[-1] == b"jpeg of bird.png (fill)"


def test_removing_the_picture_fades_out_then_lets_the_logo_return(fading):
    service, settings, streamer = fading
    settings.set_string("lcd-image", "")
    service.on_changed("lcd-image")
    service.finish_lcd_fade()
    assert streamer.images[-2] == b"black" and streamer.images[-1] is None


@pytest.fixture
def auto_idle(auto):
    from rgbhub.core.look import Look

    service, settings, streamer, lights = auto
    service._looks.idle = Look("#E95420", b"os logo jpeg")
    set_auto(settings, True)
    service.on_changed("auto-devices")
    return service, settings, streamer, lights


def test_nothing_in_focus_shows_the_os_look(auto_idle):
    service, settings, streamer, lights = auto_idle
    focus(service, settings, "firefox.desktop")
    focus(service, settings, "")
    assert streamer.images[-1] == b"os logo jpeg"
    assert lights.applied[-1][3].get("Case/Logo") == "#E95420"
    assert status(settings)["auto"] == {"app": "", "colour": "#E95420"}


def test_an_app_without_an_icon_shows_the_os_look(auto_idle):
    service, settings, streamer, _lights = auto_idle
    focus(service, settings, "mystery.desktop")
    assert streamer.images[-1] == b"os logo jpeg"


def test_our_own_settings_window_still_shows_the_normal_look(auto_idle):
    service, settings, streamer, lights = auto_idle
    focus(service, settings, "settings.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_the_owner_can_choose_the_idle_picture_and_colour(auto_idle):
    service, settings, streamer, lights = auto_idle
    settings.set_string("auto-idle-image", "/pics/xbox.png")
    settings.set_string("auto-idle-colour", "#00AA00")
    service.on_changed("auto-idle-image")
    service.on_changed("auto-idle-colour")
    focus(service, settings, "")
    assert streamer.images[-1] == b"jpeg of /pics/xbox.png"
    assert lights.applied[-1][3].get("Case/Logo") == "#00AA00"


def test_steady_colours_are_sent_again_every_slow_tick(parts):
    # Some boards reset themselves after start-up; this brings the colours back.
    service, _settings, _streamer, lights, _prepare = parts
    service.start()
    service.tick_slow()
    service.tick_slow()
    assert lights.refreshed == 2


# --- animated looks play on the LCD ----------------------------------------------


@pytest.fixture
def animated(auto):
    from rgbhub.core.look import Look

    service, settings, streamer, lights = auto
    service._looks.idle = Look("#00FF00", b"f0", (b"f0", b"f1", b"f2"), 100)
    set_auto(settings, True)
    service.on_changed("auto-devices")
    focus(service, settings, "")
    return service, settings, streamer, lights


def test_an_animated_look_plays_frame_after_frame_and_loops(animated):
    service, _settings, streamer, _lights = animated
    assert service.lcd_animating is True and streamer.images[-1] == b"f0"
    for _ in range(4):
        service.tick_lcd_animation()
    assert streamer.images[-4:] == [b"f1", b"f2", b"f0", b"f1"]


def test_animation_stops_when_something_else_takes_the_lcd(animated):
    service, settings, streamer, _lights = animated
    focus(service, settings, "firefox.desktop")
    assert service.lcd_animating is False and streamer.images[-1] == b"firefox icon jpeg"
    service.tick_lcd_animation()  # a late tick must not put the animation back
    assert streamer.images[-1] == b"firefox icon jpeg"
    focus(service, settings, "")
    assert service.lcd_animating is True  # and it starts again when idle returns


def test_animation_stops_with_auto_mode_or_auto_lcd_off(animated):
    service, settings, _streamer, _lights = animated
    settings.set_boolean("auto-lcd", False)
    service.on_changed("auto-lcd")
    assert service.lcd_animating is False


def test_an_idle_colour_of_the_owners_keeps_the_animation(animated):
    service, settings, streamer, lights = animated
    settings.set_string("auto-idle-colour", "#00AA00")
    service.on_changed("auto-idle-colour")
    service.flush_lights()
    assert lights.applied[-1][3].get("Case/Logo") == "#00AA00"
    assert service.lcd_animating is True
    service.tick_lcd_animation()
    assert streamer.images[-1] == b"f1"


def test_animation_pauses_while_asleep(animated):
    service, _settings, streamer, _lights = animated
    service.on_sleep()
    assert service.lcd_animating is False
    service.on_resume()
    assert service.lcd_animating is True


class Clocks:
    """Stand-ins for "seconds spent asleep" and "seconds running"."""

    def __init__(self):
        self.slept_total = 0.0
        self.now = 0.0

    def make(self, settings, lights):
        return Service(
            Store(settings),
            FakeStreamer(),
            lights,
            FakePrepare(),
            slept=lambda: self.slept_total,
            clock=lambda: self.now,
        )


def test_a_sleep_nobody_announced_is_noticed_and_repaired(settings):
    clocks, lights = Clocks(), FakeLights()
    service = clocks.make(settings, lights)
    service.start()
    service.tick_slow()
    assert not service._asleep
    forgotten = lights.forgets
    clocks.slept_total += 600  # ten minutes asleep, and no hook told us
    service.tick_slow()
    assert lights.forgets == forgotten + 1  # everything is sent to the hardware again


def test_a_lost_wake_message_does_not_leave_things_dark(settings):
    clocks, lights = Clocks(), FakeLights()
    service = clocks.make(settings, lights)
    service.start()
    service.on_sleep()
    clocks.slept_total += 3600  # slept an hour; the "woke up" message never came
    service.tick_slow()
    assert not service._asleep


def test_a_sleep_that_never_happened_is_given_up_on(settings):
    clocks, lights = Clocks(), FakeLights()
    service = clocks.make(settings, lights)
    service.start()
    service.on_sleep()
    clocks.now += 10
    service.tick_slow()
    assert service._asleep  # still plausibly on its way to sleep
    clocks.now += 60
    service.tick_slow()
    assert not service._asleep


def test_an_announced_wake_is_not_handled_twice(settings):
    clocks, lights = Clocks(), FakeLights()
    service = clocks.make(settings, lights)
    service.start()
    service.on_sleep()
    clocks.slept_total += 600
    service.on_resume()  # told properly
    forgotten = lights.forgets
    service.tick_slow()
    assert lights.forgets == forgotten
