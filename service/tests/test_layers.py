# SPDX-License-Identifier: GPL-3.0-or-later
import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "rgbhub" / "core"
FORBIDDEN = ("devices", "platforms")


def _imports(path: Path):
    """Each import as (level, dotted module, imported names)."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name, ()
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or "", tuple(a.name for a in node.names)


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_never_imports_devices_or_platforms(path):
    for level, module, names in _imports(path):
        parts = module.split(".") if module else []
        if level == 0:  # absolute: rgbhub.devices...
            bad = len(parts) > 1 and parts[0] == "rgbhub" and parts[1] in FORBIDDEN
        elif level == 1:  # from .x import y  /  from . import x
            bad = (parts[:1] and parts[0] in FORBIDDEN) or (
                not parts and set(names) & set(FORBIDDEN)
            )
        else:  # from ..devices import y: up out of core
            bad = level == 2 and (
                (parts and parts[0] in FORBIDDEN) or (not parts and set(names) & set(FORBIDDEN))
            )
        assert not bad, f"{path.name} imports {'.' * level}{module} {names}"


def test_repo_root_lookups_resolve():
    """Every `parents[N]` repo-root lookup in rgbhub must land on a real folder
    (the Batocera RAM helper once pointed one level too high after a move)."""
    import re
    from pathlib import Path

    from rgbhub.platforms import batocera

    repo = Path(__file__).resolve().parents[2]
    assert batocera.RAM_HELPER.exists()
    assert batocera.RAM_HELPER == repo / "helper" / "rgbhub_ram_helper.py"
    pattern = re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]\s*/\s*\"([^\"]+)\"")
    found = 0
    for source in sorted((repo / "service" / "rgbhub").rglob("*.py")):
        for number, text in pattern.findall(source.read_text(encoding="utf-8")):
            found += 1
            assert (source.parents[int(number)] / text).exists(), (source, number, text)
    assert found >= 3
