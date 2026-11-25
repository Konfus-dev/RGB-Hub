# SPDX-License-Identifier: GPL-3.0-or-later
from rgbhub.platforms.batocera import power_handlers


class FakeService:
    def __init__(self):
        self.calls = []

    def on_sleep(self):
        self.calls.append("sleep")

    def on_resume(self):
        self.calls.append("resume")


def test_sleep_switches_off_then_says_so(tmp_path):
    # The suspend hook waits for this file before letting the system sleep.
    marker = tmp_path / "run" / "asleep"
    service = FakeService()
    sleep, wake = power_handlers(service, marker)
    sleep()
    assert service.calls == ["sleep"] and marker.exists()
    wake()
    assert service.calls == ["sleep", "resume"] and not marker.exists()


def test_the_marker_is_written_even_if_switching_off_fails(tmp_path):
    # Otherwise a problem with the lights would hold up every suspend.
    class Broken(FakeService):
        def on_sleep(self):
            raise RuntimeError("lights fell over")

    marker = tmp_path / "asleep"
    sleep, _wake = power_handlers(Broken(), marker)
    sleep()  # must not raise
    assert marker.exists()


def test_waking_without_having_slept_is_harmless(tmp_path):
    service = FakeService()
    _sleep, wake = power_handlers(service, tmp_path / "asleep")
    wake()
    assert service.calls == ["resume"]


def test_subject_reader_copies_the_hooks_file_into_the_settings(tmp_path):
    from rgbhub.core.json_store import JsonSettings
    from rgbhub.platforms.batocera import subject_reader

    now_playing = tmp_path / "now-playing"
    settings = JsonSettings(tmp_path / "settings.json")
    read = subject_reader(now_playing, settings)
    read()  # no file yet
    assert settings.get("focused-app") == ""
    now_playing.write_text("/userdata/roms/snes/Game.smc\n")
    read()
    assert settings.get("focused-app") == "/userdata/roms/snes/Game.smc"
    now_playing.write_text("")
    read()
    assert settings.get("focused-app") == ""
