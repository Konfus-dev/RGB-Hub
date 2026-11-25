# SPDX-License-Identifier: GPL-3.0-or-later
import io

from PIL import Image

from rgbhub.devices.omen_pc.lcd_fade import fade_frames


def jpeg(colour):
    buffer = io.BytesIO()
    Image.new("RGB", (480, 480), colour).save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def centre(data):
    return Image.open(io.BytesIO(data)).getpixel((240, 240))


def close(pixel, colour, tolerance=14):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour, strict=True))


def test_fade_blends_from_old_to_new_and_ends_on_the_new_picture():
    red, blue = jpeg((255, 0, 0)), jpeg((0, 0, 255))
    frames = list(fade_frames(red, blue, 4))
    assert len(frames) == 4
    assert close(centre(frames[0]), (191, 0, 64))  # a quarter of the way
    assert close(centre(frames[1]), (128, 0, 128))
    assert frames[-1] == blue  # the real picture itself, not a re-encoded copy
    assert all(len(frame) <= 60000 for frame in frames)


def test_fading_from_nothing_starts_at_black():
    frames = list(fade_frames(None, jpeg((0, 200, 0)), 4))
    assert close(centre(frames[0]), (0, 50, 0))


def test_fading_to_nothing_ends_at_black():
    frames = list(fade_frames(jpeg((0, 200, 0)), None, 4))
    assert close(centre(frames[-1]), (0, 0, 0))


def test_frames_are_made_one_at_a_time():
    # Each frame costs a few milliseconds; making them lazily keeps the
    # service responsive during a fade.
    frames = fade_frames(jpeg((255, 0, 0)), jpeg((0, 0, 255)), 100)
    assert close(centre(next(frames)), (252, 0, 3))


def test_a_broken_picture_does_not_stop_the_fade():
    frames = list(fade_frames(b"not a jpeg", jpeg((0, 0, 255)), 3))
    assert frames[-1] == jpeg((0, 0, 255))
