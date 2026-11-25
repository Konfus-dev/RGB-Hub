# SPDX-License-Identifier: GPL-3.0-or-later
from rgbhub.platforms.batocera.games import find_artwork

GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game>
    <path>./Super Game (USA).zip</path>
    <name>Super Game</name>
    <image>./images/Super Game-image.png</image>
    <thumbnail>./images/Super Game-thumb.png</thumbnail>
    <marquee>./images/Super Game-marquee.png</marquee>
  </game>
  <game>
    <path>./Other &amp; Game.zip</path>
    <image>./images/other.png</image>
  </game>
  <game>
    <path>./No Art.zip</path>
  </game>
</gameList>
"""


def make_roms(tmp_path, *existing_images):
    snes = tmp_path / "roms" / "snes"
    (snes / "images").mkdir(parents=True)
    (snes / "gamelist.xml").write_text(GAMELIST)
    for name in existing_images:
        (snes / "images" / name).write_bytes(b"png")
    return tmp_path / "roms"


def test_prefers_the_logo_then_box_then_screenshot(tmp_path):
    roms = make_roms(
        tmp_path, "Super Game-image.png", "Super Game-thumb.png", "Super Game-marquee.png"
    )
    rom = roms / "snes" / "Super Game (USA).zip"
    assert find_artwork(str(rom), roms) == roms / "snes" / "images" / "Super Game-marquee.png"


def test_skips_artwork_files_that_are_not_there(tmp_path):
    roms = make_roms(tmp_path, "Super Game-image.png")
    rom = roms / "snes" / "Super Game (USA).zip"
    assert find_artwork(str(rom), roms) == roms / "snes" / "images" / "Super Game-image.png"


def test_names_with_special_characters(tmp_path):
    roms = make_roms(tmp_path, "other.png")
    assert find_artwork(str(roms / "snes" / "Other & Game.zip"), roms) is not None


def test_no_artwork_means_none(tmp_path):
    roms = make_roms(tmp_path)
    assert find_artwork(str(roms / "snes" / "No Art.zip"), roms) is None
    assert find_artwork(str(roms / "snes" / "Unlisted.zip"), roms) is None
    assert find_artwork(str(roms / "nes" / "Whatever.zip"), roms) is None  # no gamelist
    assert find_artwork("", roms) is None
    assert find_artwork("/somewhere/else/game.zip", roms) is None


def test_a_broken_gamelist_does_not_raise(tmp_path):
    roms = make_roms(tmp_path)
    (roms / "snes" / "gamelist.xml").write_text("<gameList><game>")
    assert find_artwork(str(roms / "snes" / "Super Game (USA).zip"), roms) is None
