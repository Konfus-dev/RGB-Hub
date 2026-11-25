# SPDX-License-Identifier: GPL-3.0-or-later
"""The cooler LCD's numbers and the service's timing, in one place.

LCD protocol facts come from the public notes at
https://github.com/Xedecimal/omen-led-lcd (PROTOCOL_NOTES.md) and were
confirmed on an OMEN 45L GT22-3xxx, LCD firmware V3.05.024.
"""

from ...core.constants import LCD_SIZE, MAX_JPEG_BYTES, RECONNECT_INTERVAL_S
from ...core.interfaces import HidMatch

__all__ = ["LCD_SIZE", "MAX_JPEG_BYTES", "RECONNECT_INTERVAL_S"]  # re-exported, see core/constants

# --- Cooler LCD ("LCD-PUMP", USB 03f0:7397) ---------------------------------
# The LCD exposes two HID interfaces. The one we want has this vendor usage
# page; the other is a mouse-class interface that must never be written to.
LCD_MATCH = HidMatch(0x03F0, 0x7397, usage_page=0xFF1A)

SHORT_REPORT_LEN = 64  # report ID 1, including the ID byte
LONG_REPORT_LEN = 1024  # report ID 2 going out, including the ID byte

REPORT_ID_SHORT = 0x01
REPORT_ID_LONG = 0x02

# The only three operations this project ever sends.
OP_CLAIM = 0x41
OP_BRIGHTNESS = 0x6C
OP_FRAME = 0x6E

CLAIM_REQUEST = 0x01  # value of byte 5 when we send a claim

# First 18 bytes of a brightness report, as captured from HP's software.
BRIGHTNESS_HEAD = bytes.fromhex("026c00000001000001000804000000000019")
BRIGHTNESS_VALUE_OFFSET = 12  # 0..100

# Image frames: a JPEG is cut into chunks, one chunk per long report.
FRAME_TOTAL_LEN_OFFSET = 4  # 2 bytes, big-endian: size of the whole JPEG
FRAME_CHUNK_INDEX_OFFSET = 8  # 1 byte, starts at 0 for each JPEG
FRAME_CHUNK_LEN_OFFSET = 9  # 2 bytes, big-endian: size of this chunk
CHUNK_DATA_OFFSET = 11
CHUNK_DATA_LEN = 1013

# --- Timing ------------------------------------------------------------------
CLAIM_INTERVAL_MS = 1000  # HP's software repeats the claim about once a second
FRAME_INTERVAL_MS = 500  # the screen forgets the image unless it is re-sent
