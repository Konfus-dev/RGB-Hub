# SPDX-License-Identifier: GPL-3.0-or-later
"""Sizes and timing the core itself needs (the pictures it makes, the slow tick)."""

LCD_SIZE = 480  # the cooler LCD is 480x480 pixels; auto-look pictures are made this size
MAX_JPEG_BYTES = 60000  # the LCD's frame length field holds this with margin
RECONNECT_INTERVAL_S = 5  # how often the hub's slow tick runs
