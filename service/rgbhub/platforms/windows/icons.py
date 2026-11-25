# SPDX-License-Identifier: GPL-3.0-or-later
"""App icons on Windows, for auto mode: the focused app's colour and LCD picture.

Two kinds of app id come from focus.py:
- a program: its lower-cased path. The icon is read out of the program file by
  the icoextract library (it reads the program's icon group, as Explorer does),
  and the largest picture in it is used.
- a Store app: "<package family>|<program folder>". The package's
  AppxManifest.xml (in that folder or one above it) names its tile logos; the
  largest of their size variants (Logo.scale-200.png, Small.targetsize-256.png,
  ...) is used. High-contrast variants are skipped.
"""

import logging
import ntpath
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

MANIFEST = "AppxManifest.xml"
LOGO_ATTRIBUTES = ("Square150x150Logo", "Square44x44Logo")
_MANIFEST_SEARCH_DEPTH = 4  # how many folders above the program a manifest may be


def load_app_icon(app_id: str) -> Image.Image | None:
    """The app's icon as an RGBA picture, or None if it has none we can read."""
    if not app_id:
        return None
    try:
        if "|" in app_id:
            return _package_icon(Path(app_id.split("|", 1)[1]))
        return _program_icon(app_id)
    except Exception as error:  # a broken file must not stop auto mode
        log.debug("No icon for %s: %s", app_id, error)
        return None


# --- programs ------------------------------------------------------------------------


def _extractor():
    """icoextract's IconExtractor (imported when first needed; Windows extra)."""
    from icoextract import IconExtractor

    return IconExtractor


def _program_icon(exe: str) -> Image.Image | None:
    if not Path(exe).is_file():
        return None
    ico = _extractor()(filename=exe).get_icon(num=0)
    with Image.open(ico) as image:
        largest = max(image.ico.sizes(), key=lambda size: size[0] * size[1])
        return image.ico.getimage(largest).convert("RGBA")


# --- Store apps ----------------------------------------------------------------------


def _package_icon(folder: Path) -> Image.Image | None:
    manifest = _find_manifest(folder)
    if manifest is None:
        return None
    root = manifest.parent
    candidates: list[Path] = []
    for logo in _manifest_logos(manifest):
        candidates.extend(_variants(root, logo))
    best, best_area = None, 0
    for path in candidates:
        try:
            with Image.open(path) as image:
                area = image.size[0] * image.size[1]
        except OSError:
            continue
        if area > best_area:
            best, best_area = path, area
    if best is None:
        return None
    with Image.open(best) as image:
        return image.convert("RGBA")


def _find_manifest(folder: Path) -> Path | None:
    for place in [folder, *folder.parents][: _MANIFEST_SEARCH_DEPTH + 1]:
        if (place / MANIFEST).is_file():
            return place / MANIFEST
    return None


def _manifest_logos(manifest: Path) -> list[str]:
    """The tile logos the manifest names (relative paths, as written there)."""
    logos = []
    for element in ElementTree.parse(manifest).iter():
        if element.tag.rsplit("}", 1)[-1] == "VisualElements":
            logos += [element.get(name) for name in LOGO_ATTRIBUTES if element.get(name)]
            break  # the first app in the package
    return logos


def _variants(root: Path, logo: str) -> list[Path]:
    """The files for one logo: as named, and its scale/targetsize variants."""
    named = root.joinpath(*ntpath.normpath(logo).split("\\"))
    folder, stem, suffix = named.parent, named.stem, named.suffix
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        name = path.name
        if not name.lower().endswith(suffix.lower()) or not path.is_file():
            continue
        if name.lower() == named.name.lower():
            found.append(path)
        elif name.lower().startswith(stem.lower() + ".") and "contrast-" not in name.lower():
            found.append(path)
    return found
