# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds and reads the byte packets the cooler LCD understands.

Pure functions only: bytes in, bytes out. Nothing here touches hardware.
"""

from . import lcd_constants as c


def _short_report(op: int) -> bytearray:
    report = bytearray(c.SHORT_REPORT_LEN)
    report[0] = c.REPORT_ID_SHORT
    report[1] = op
    return report


def claim_report() -> bytes:
    """Tell the LCD we are driving it. Must be repeated about once a second."""
    report = _short_report(c.OP_CLAIM)
    report[5] = c.CLAIM_REQUEST
    return bytes(report)


def brightness_report(percent: int) -> bytes:
    """Set screen brightness. 0 is black, 100 is full."""
    report = bytearray(c.LONG_REPORT_LEN)
    report[: len(c.BRIGHTNESS_HEAD)] = c.BRIGHTNESS_HEAD
    report[c.BRIGHTNESS_VALUE_OFFSET] = max(0, min(100, int(percent)))
    return bytes(report)


def frame_reports(jpeg: bytes) -> list[bytes]:
    """Cut one 480x480 JPEG into the reports that display it."""
    if not jpeg or len(jpeg) > c.MAX_JPEG_BYTES:
        raise ValueError(f"JPEG must be 1..{c.MAX_JPEG_BYTES} bytes, got {len(jpeg)}")
    total = len(jpeg).to_bytes(2, "big")
    reports = []
    for index, start in enumerate(range(0, len(jpeg), c.CHUNK_DATA_LEN)):
        chunk = jpeg[start : start + c.CHUNK_DATA_LEN]
        report = bytearray(c.LONG_REPORT_LEN)
        report[0] = c.REPORT_ID_LONG
        report[1] = c.OP_FRAME
        report[c.FRAME_TOTAL_LEN_OFFSET : c.FRAME_TOTAL_LEN_OFFSET + 2] = total
        report[c.FRAME_CHUNK_INDEX_OFFSET] = index
        report[c.FRAME_CHUNK_LEN_OFFSET : c.FRAME_CHUNK_LEN_OFFSET + 2] = len(chunk).to_bytes(
            2, "big"
        )
        report[c.CHUNK_DATA_OFFSET : c.CHUNK_DATA_OFFSET + len(chunk)] = chunk
        reports.append(bytes(report))
    return reports
