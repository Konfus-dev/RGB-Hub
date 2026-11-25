# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression: app colours came from the wrong icons (a purple terminal
instead of the theme's green one) because the icon theme was fixed when the
service started, before the session had said which theme it uses."""

from types import SimpleNamespace

from PIL import Image

from rgbhub.platforms import gnome
from rgbhub.platforms.gnome import gtk_icons


def test_auto_mode_follows_an_icon_theme_changed_after_start(monkeypatch, tmp_path):
    theme = ["Yaru-purple"]
    colours = {"Yaru-purple": (120, 60, 255, 255), "Yaru-sage": (60, 200, 120, 255)}
    monkeypatch.setattr(gtk_icons, "icon_theme_name", lambda: theme[0])
    monkeypatch.setattr(
        gtk_icons, "load_app_icon", lambda app: Image.new("RGBA", (64, 64), colours[theme[0]])
    )
    looks = gnome.make_looks(SimpleNamespace(wallpaper=lambda: None), tmp_path / "lcd")

    first = looks.look_for("org.gnome.Terminal.desktop")
    theme[0] = "Yaru-sage"  # the session's real theme, known only after start
    second = looks.look_for("org.gnome.Terminal.desktop")
    assert first.colour != second.colour
    red, green, blue = (int(second.colour[i : i + 2], 16) for i in (1, 3, 5))
    assert green > red and green > blue


def test_the_theme_name_is_read_afresh_each_time(monkeypatch):
    names = iter(["Yaru", "Adwaita"])
    monkeypatch.setattr(gtk_icons, "_interface", SimpleNamespace(get_string=lambda k: next(names)))
    assert gtk_icons.icon_theme_name() == "Yaru"
    assert gtk_icons.icon_theme_name() == "Adwaita"


def test_the_settings_window_keeps_the_normal_look(tmp_path):
    looks = gnome.make_looks(SimpleNamespace(wallpaper=lambda: None), tmp_path)
    assert looks.is_ignored("org.gnome.Shell.Extensions.desktop")
