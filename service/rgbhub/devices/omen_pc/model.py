# SPDX-License-Identifier: GPL-3.0-or-later
"""Facts about the HP OMEN 45L that are the same on every operating system."""

# OpenRGB calls the 45L's case-light board "HP Omen 30L"; our own driver
# (devices/omen_pc/case_board.py) uses the same name so settings work with either.
CASE_DEVICE = "HP Omen 30L"

# Light zones OpenRGB lists that have nothing attached on the 45L
# ("Device name/Zone name"). They are never shown or written to.
HIDDEN_ZONES = (
    f"{CASE_DEVICE}/Light Bar",
    f"{CASE_DEVICE}/Front Fan",
    f"{CASE_DEVICE}/CPU Cooler",
)

# How strongly colours are curved before they go to the LEDs, so that what
# you pick on screen is what you see on the hardware. 1.0 means no curve;
# raise it if dark colours look pale, lower it if they look too dim.
LED_GAMMA = 2.2

# Friendlier names for front ends to show. Anything not listed is shown
# under its real name. Keys are "Device name" or "Device name/Zone name".
DISPLAY_NAMES = {
    CASE_DEVICE: "Case lights",
    f"{CASE_DEVICE}/Omen Logo": "Front logo",
    f"{CASE_DEVICE}/Front Top Fan": "Top fan",
    f"{CASE_DEVICE}/Front Middle Fan": "Middle fan",
    f"{CASE_DEVICE}/Front Bottom Fan": "Bottom fan",
    "RAM": "Memory (RAM)",
    "RAM/Stick 1": "Left stick",
    "RAM/Stick 2": "Right stick",
}
