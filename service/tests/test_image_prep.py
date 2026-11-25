# SPDX-License-Identifier: GPL-3.0-or-later
import io
import os

import pytest
from PIL import Image

from rgbhub.core.image_prep import ImageError, prepare_jpeg

RED, BLUE, BLACK, WHITE = (255, 0, 0), (0, 0, 255), (0, 0, 0), (255, 255, 255)


def check(data):
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG" and im.size == (480, 480) and im.mode == "RGB"
    assert len(data) <= 60000
    return im


def close(pixel, colour, tolerance=12):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour, strict=True))


def save(tmp_path, image, name="in.png", **kwargs):
    path = tmp_path / name
    image.save(path, **kwargs)
    return path


def test_plain_png_fill(tmp_path):
    path = save(tmp_path, Image.new("RGB", (1000, 500), RED))
    im = check(prepare_jpeg(path, "fill"))
    assert close(im.getpixel((240, 240)), RED) and close(im.getpixel((240, 10)), RED)


def test_fit_letterboxes(tmp_path):
    path = save(tmp_path, Image.new("RGB", (1000, 500), RED))
    im = check(prepare_jpeg(path, "fit"))
    assert close(im.getpixel((240, 10)), BLACK) and close(im.getpixel((240, 240)), RED)


def test_transparent_png_on_black(tmp_path):
    path = save(tmp_path, Image.new("RGBA", (300, 300), (255, 255, 255, 0)))
    im = check(prepare_jpeg(path, "fill"))
    assert close(im.getpixel((240, 240)), BLACK)


def test_cmyk_jpeg(tmp_path):
    path = save(tmp_path, Image.new("CMYK", (300, 300), (0, 255, 255, 0)), "in.jpg")
    check(prepare_jpeg(path, "fill"))


def test_exif_rotation(tmp_path):
    # Stored 200x100 with the left half white; orientation 6 means "rotate
    # 90 degrees clockwise to view", which puts the white half on top.
    stored = Image.new("RGB", (200, 100), BLACK)
    stored.paste(WHITE, (0, 0, 100, 100))
    exif = Image.Exif()
    exif[0x0112] = 6
    path = save(tmp_path, stored, "in.jpg", exif=exif, quality=95)
    im = check(prepare_jpeg(path, "fill"))
    assert close(im.getpixel((240, 60)), WHITE, 30) and close(im.getpixel((240, 420)), BLACK, 30)


def test_animated_gif_first_frame(tmp_path):
    frames = [Image.new("RGB", (100, 100), RED), Image.new("RGB", (100, 100), BLUE)]
    path = tmp_path / "in.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    im = check(prepare_jpeg(path, "fill"))
    assert close(im.getpixel((240, 240)), RED, 30)


@pytest.mark.parametrize("size", [(1, 1), (8000, 200)])
@pytest.mark.parametrize("fit", ["fill", "fit"])
def test_tiny_and_panorama(tmp_path, size, fit):
    path = save(tmp_path, Image.new("RGB", size, RED))
    check(prepare_jpeg(path, fit))


def test_noisy_image_is_shrunk(tmp_path):
    noise = Image.frombytes("RGB", (480, 480), os.urandom(480 * 480 * 3))
    check(prepare_jpeg(save(tmp_path, noise), "fill"))


def test_unknown_fit_means_fill(tmp_path):
    path = save(tmp_path, Image.new("RGB", (1000, 500), RED))
    assert prepare_jpeg(path, "banana") == prepare_jpeg(path, "fill")


def test_missing_file(tmp_path):
    with pytest.raises(ImageError):
        prepare_jpeg(tmp_path / "nope.png", "fill")


def test_not_an_image(tmp_path):
    path = tmp_path / "x.png"
    path.write_text("hello, I am not a picture")
    with pytest.raises(ImageError):
        prepare_jpeg(path, "fill")


def test_directory_is_an_error(tmp_path):
    with pytest.raises(ImageError):
        prepare_jpeg(tmp_path, "fill")


@pytest.mark.parametrize("size", [(1, 5000), (5000, 1)])
@pytest.mark.parametrize("fit", ["fill", "fit"])
def test_extremely_thin_images(tmp_path, size, fit):
    # These used to escape as a ValueError and crash the service at startup.
    check(prepare_jpeg(save(tmp_path, Image.new("RGB", size, RED)), fit))


def test_any_failure_inside_pillow_becomes_image_error(tmp_path, monkeypatch):
    from rgbhub.core import image_prep

    def explode(*args, **kwargs):
        raise MemoryError("picture far too big")

    monkeypatch.setattr(image_prep.ImageOps, "fit", explode)
    with pytest.raises(ImageError):
        prepare_jpeg(save(tmp_path, Image.new("RGB", (10, 10), RED)), "fill")
