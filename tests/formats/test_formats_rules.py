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


# Company, product, character and font names, the films' voice and kit ids and their brand colours, held only as
# SHA-256 prefixes so the guard itself keeps them out of the repo (the same list as tests/brand/test_brand_nobrand.py,
# plus the films' other distinctive colours).
GUARDED = {
    "2ad60e2919a3ae8f", "e252b79c39aa92dd", "fd73d009d4b851d4", "16149626b0365e04", "ff9b5c834ecab632",
    "531074a5255eac9d", "129eaf4b1219beda", "db4ea2e2c2875a50", "61acc4cab1bfb5d3", "bdd6bed80a39febc",
    "f8d73da6e93bc010", "2e7dc20ed2ed1d97", "4715650ab9e0c9ba", "4b916c20ce35d6ba", "55dba21b14c7d258",
    "30c07346a4e77ab1", "a30a9e529370f662", "bad936c77484aada", "7fa259c033887a81",
    "fb2b95447a31b356", "212c6e34978bf31f", "1f2401c1733346fa", "7ba2337ec67b6b43", "4bc7eb0c4824f537",
    "394d55263b21668e", "14cae19d49b9701b", "c52a77f328a6c02c", "934cd28b6e3678a3",
}


def _guarded_hits(text: str) -> list[str]:
    """Every word, and every pair of neighbouring words, whose hash is on the guarded list."""
    import hashlib

    def h(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    words = re.findall(r"[a-z0-9]+", text.lower())
    return [f"word {i + 1}" for i, w in enumerate(words)
            if h(w) in GUARDED or (i + 1 < len(words) and h(f"{w} {words[i + 1]}") in GUARDED)]


def test_no_company_material_in_this_modules_files():
    assert len([*OWN_CODE, *OWN_DATA, *TESTS]) > 20
    for path in [*OWN_CODE, *OWN_DATA, *TESTS]:
        hits = _guarded_hits(path.read_text(encoding="utf-8"))
        assert not hits, f"{path.name}: {hits}"


def test_the_guard_catches_what_it_guards():
    assert _guarded_hits("Made for " + "".join(chr(c) for c in (69, 79, 83)) + " clients")  # a guarded company name
    assert not _guarded_hits("Demo Studio makes short films from data.")


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
