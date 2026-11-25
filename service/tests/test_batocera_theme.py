# SPDX-License-Identifier: GPL-3.0-or-later
from rgbhub.platforms.batocera.theme import active_theme, theme_background

ES_SETTINGS = """<?xml version="1.0"?>
<config>
\t<bool name="ShowHelp" value="true" />
\t<string name="ThemeSet" value="classic-dash" />
</config>
"""

THEME = """<theme>
  <view name="system, basic, detailed">
    <image name="shell"><path>./art/shell.png</path></image>
  </view>
  <view name="basic">
    <image name="background"><path>${themePath}/art/bg_list.jpg</path></image>
  </view>
  <view name="system">
    <image name="background" extra="static">
      <path>${themePath}/art/bg_system.jpg</path>
    </image>
  </view>
</theme>
"""


def make(tmp_path, theme_xml=THEME, files=("art/bg_system.jpg", "art/bg_list.jpg")):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = tmp_path / "es_settings.cfg"
    settings.write_text(ES_SETTINGS)
    theme = tmp_path / "themes" / "classic-dash"
    (theme / "art").mkdir(parents=True)
    (theme / "theme.xml").write_text(theme_xml)
    for name in files:
        (theme / name).write_bytes(b"x")
    return settings, [tmp_path / "themes"]


def test_active_theme_folder(tmp_path):
    settings, themes = make(tmp_path)
    assert active_theme(settings, themes) == tmp_path / "themes" / "classic-dash"


def test_uses_the_system_views_background(tmp_path):
    settings, themes = make(tmp_path)
    expected = tmp_path / "themes" / "classic-dash" / "art" / "bg_system.jpg"
    assert theme_background(settings, themes) == expected


def test_relative_paths_and_any_background_as_second_choice(tmp_path):
    xml = (
        '<theme><view name="detailed"><image name="background">'
        "<path>./art/bg_list.jpg</path></image></view></theme>"
    )
    settings, themes = make(tmp_path, xml)
    assert theme_background(settings, themes).name == "bg_list.jpg"


def test_follows_included_files(tmp_path):
    xml = "<theme><include>./inc/views.xml</include></theme>"
    settings, themes = make(tmp_path, xml)
    inc = tmp_path / "themes" / "classic-dash" / "inc"
    inc.mkdir()
    (inc / "views.xml").write_text(
        '<theme><view name="system"><image name="background">'
        "<path>./../art/bg_system.jpg</path></image></view></theme>"
    )
    assert theme_background(settings, themes).name == "bg_system.jpg"


def test_no_usable_background_means_none(tmp_path):
    settings, themes = make(tmp_path, files=())  # picture files missing
    assert theme_background(settings, themes) is None
    settings, themes2 = make(tmp_path / "b", "<theme><view name='system'/></theme>")
    assert theme_background(settings, themes2) is None
    settings, themes3 = make(tmp_path / "c", "<theme><view")  # broken XML
    assert theme_background(settings, themes3) is None
    assert theme_background(tmp_path / "missing.cfg", themes) is None
    settings, _ = make(tmp_path / "d")
    assert theme_background(settings, [tmp_path / "nowhere"]) is None


def test_backgrounds_that_depend_on_theme_variables_are_skipped(tmp_path):
    xml = (
        '<theme><view name="system"><image name="background">'
        "<path>./art/${system.theme}.jpg</path></image></view>"
        '<view name="basic"><image name="background">'
        "<path>./art/bg_list.jpg</path></image></view></theme>"
    )
    settings, themes = make(tmp_path, xml)
    assert theme_background(settings, themes).name == "bg_list.jpg"


def test_the_themes_lcd_folder_is_inside_the_active_theme(tmp_path):
    from rgbhub.platforms.batocera.theme import lcd_folder

    settings, themes = make(tmp_path)
    assert lcd_folder(settings, themes) == tmp_path / "themes" / "classic-dash" / "rgb-hub"
    assert lcd_folder(tmp_path / "missing.cfg", themes) is None
