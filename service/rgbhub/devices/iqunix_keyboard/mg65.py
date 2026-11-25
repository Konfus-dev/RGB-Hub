# SPDX-License-Identifier: GPL-3.0-or-later
"""Facts about the IQUNIX MG65 keyboard that are the same on every system.

USB 320f:5055. It runs VIA-compatible firmware (VIA protocol 11), with its
lights on VIA's "RGB matrix" channel (see via.py). Effect names
and ranges are from IQUNIX's VIA definition and were checked on the board.
"""

from ...core.interfaces import HidMatch
from .via import KeyboardModel

DEVICE_NAME = "IQUNIX MG65"  # the key for its lighting settings

# The effects and their numbers, from IQUNIX's own VIA definition of the
# MG65 (the JSON file their VIA guide links to). 0 is "off", which the hub
# does with brightness instead. "Light" (6) is the steady colour, so it is
# offered as Static. Breathing is called "Breathe" here: "Breathing" is
# the name of the breathing the hub draws itself on the case lights.
EFFECTS = (
    ("Static", 6),
    ("Wave", 1),
    ("Colour cloud", 2),
    ("Vortex", 3),
    ("Mixed colours", 4),
    ("Breathe", 5),
    ("Slowly off", 7),
    ("Stone", 8),
    ("Laser", 9),
    ("Starry", 10),
    ("Flowers opening", 11),
    ("Traverse", 12),
    ("Wave bar", 13),
    ("Meteor", 14),
    ("Rain", 15),
    ("Scan", 16),
    ("Colour on keypress", 17),
    ("Centre spread", 18),
)

# The light strip by the volume wheel ("logo LED") is not reachable from the
# computer: IQUNIX's definition gives it no lighting channel, only two key
# actions inside the keyboard (Fn + { effect on/off, Fn + } colour).

MG65 = KeyboardModel(
    name=DEVICE_NAME,
    match=HidMatch(0x320F, 0x5055, usage_page=0xFF60, usage=0x61),
    effects=EFFECTS,
    moving=tuple(name for name, _ in EFFECTS[1:]),
    max_brightness=9,  # its brightness runs 0..9 (higher values read back as 9)
    max_speed=4,  # effect speed 0..4
    # Its LEDs wash out colours that are not fully vivid: an orange came out
    # pinkish. This makes them more vivid without moving their hue.
    saturation_curve=2.2,
)

DISPLAY_NAMES = {
    DEVICE_NAME: "Keyboard lights",
    f"{DEVICE_NAME}/All keys": "All keys",
}
