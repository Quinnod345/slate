"""No company material in the brand module or the neutral packs (docs/ARCHITECTURE.md §1, rules 1 and 2).

The guarded names are held here only as SHA-256 prefixes, the same way the kit's own guard does it
(engine/tests/kit/nobrand.test.ts), so the check keeps company material out of the repo too: every word, and every
pair of neighbouring words, in the brand module's sources and the two neutral packs is hashed and compared. Also
guarded: the films' voice ids, their brand-kit id, their brand colours and font-kit ids.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from slate import paths

GUARDED = {
    # company, product, character and licensed-face names, and a font kit id (the kit's list)
    "2ad60e2919a3ae8f", "e252b79c39aa92dd", "fd73d009d4b851d4", "16149626b0365e04", "ff9b5c834ecab632",
    "531074a5255eac9d", "129eaf4b1219beda", "db4ea2e2c2875a50", "61acc4cab1bfb5d3", "bdd6bed80a39febc",
    "f8d73da6e93bc010",
    # the films' voice ids and brand-kit id, their brand colours and the other font kit
    "2e7dc20ed2ed1d97", "4715650ab9e0c9ba", "4b916c20ce35d6ba", "55dba21b14c7d258", "30c07346a4e77ab1",
    "a30a9e529370f662", "bad936c77484aada", "7fa259c033887a81",
    # the films' other distinctive colours (their teals, oranges, creams and reds)
    "fb2b95447a31b356", "212c6e34978bf31f", "1f2401c1733346fa", "7ba2337ec67b6b43", "4bc7eb0c4824f537",
    "394d55263b21668e", "14cae19d49b9701b", "c52a77f328a6c02c", "934cd28b6e3678a3",
}
OWNED = [paths.PACKAGE / "brand", paths.SCHEMAS_DIR / "brand.schema.json", paths.BRANDS_REPO / "_template",
         paths.BRANDS_REPO / "demo", Path(__file__).parent]
TEXT = (".py", ".json", ".md", ".svg", ".txt")


def _files() -> list[Path]:
    out = []
    for root in OWNED:
        if root.is_file():
            out.append(root)
            continue
        out += [p for p in root.rglob("*") if p.is_file() and p.suffix in TEXT and "__pycache__" not in p.parts]
    return sorted(out)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def test_there_are_sources_to_check():
    assert len(_files()) > 20


def test_no_company_material_in_the_brand_module_or_the_neutral_packs():
    hits = []
    for f in _files():
        words = re.findall(r"[a-z0-9]+", f.read_text(encoding="utf-8", errors="replace").lower())
        for i, w in enumerate(words):
            if _h(w) in GUARDED or (i + 1 < len(words) and _h(f"{w} {words[i + 1]}") in GUARDED):
                hits.append(f"{f.relative_to(paths.REPO)}: word {i + 1}")
    assert not hits, hits


def test_no_fixed_frame_size_or_rate_in_the_brand_logic():
    pattern = re.compile(r"\b(1920|1080|30000|1001|29\.97|23\.976)\b")
    offenders = []
    for f in sorted((paths.PACKAGE / "brand").glob("*.py")):
        for n, line in enumerate(f.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            if pattern.search(code):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_no_font_files_anywhere_in_the_repos_brands():
    from slate.brand.validate import font_files

    assert font_files(paths.BRANDS_REPO) == []
