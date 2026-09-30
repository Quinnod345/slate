"""The hard rules, checked on this module's own files: no frame size or rate literals in logic, no company material,
nothing that depends on fonttools or uharfbuzz (dropped with Higgsedit's Track A), and no browser opened."""

from __future__ import annotations

import re
from pathlib import Path

from slate import paths

SRC = paths.PACKAGE
OWN_CODE = [SRC / "formats.py", *sorted((SRC / "analyze").glob("*.py")), *sorted((SRC / "bench").glob("*.py"))]
OWN_DATA = [*sorted(paths.FORMATS_DIR.glob("*.json")), SRC / "schemas" / "format.schema.json"]
TESTS = sorted(Path(__file__).parent.glob("*.py"))


def _code(path: Path) -> str:
    """The file without comments and docstrings' prose would still count; keep it simple: the whole text."""
    return path.read_text(encoding="utf-8")


def test_no_frame_size_or_rate_literals_in_logic():
    for path in OWN_CODE:
        text = _code(path)
        for literal in ("1920", "1080", "30000/1001", "30000", "1001"):
            assert not re.search(rf"(?<![\d.]){re.escape(literal)}(?![\d.])", text), f"{literal} in {path.name}"


def test_no_company_material_in_this_modules_files():
    banned = re.compile(r"(?i)truespace|vixa|trudy|inner[\s-]?echo|\beos\b|degular|typekit|#00a3c9|#00475f")
    for path in [*OWN_CODE, *OWN_DATA, *TESTS]:
        if path.name == Path(__file__).name:
            continue
        hits = banned.findall(path.read_text(encoding="utf-8"))
        assert not hits, f"{path.name}: {hits}"


def test_fonttools_and_uharfbuzz_are_dropped_with_track_a():
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(fontTools|fonttools|uharfbuzz|harfbuzz)\b", text, re.M), path
    project = (paths.REPO / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "fonttools" not in project and "uharfbuzz" not in project


def test_nothing_here_opens_a_browser_or_shells_out_raw():
    for path in OWN_CODE:
        text = _code(path)
        assert "webbrowser" not in text and "subprocess" not in text and "os.system" not in text, path.name
        assert not re.search(r"['\"]open['\"]\s*,", text), path.name
