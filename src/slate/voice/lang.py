"""Language tables and language-neutral word handling.

Captions and transcript checks carry no English literals: sentence and clause punctuation, the joining words a
caption prefers to break before, and reading-speed defaults all come from `languages/<code>.json`. Word
normalization works on Unicode categories (letters, marks and digits), not on an a-z range, so it holds for any script.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from slate.errors import UsageError

LANG_DIR = Path(__file__).resolve().parent / "languages"
APOSTROPHES = "'’ʼ"
DASHES = "-‐‑‒–—―"


@lru_cache(maxsize=None)
def load(code: str = "en") -> dict:
    """The table for a language code (e.g. "en"). Unknown codes are a usage error naming the files that exist."""
    path = LANG_DIR / f"{code}.json"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in LANG_DIR.glob("*.json"))) or "none"
        raise UsageError(f"Slate has no language table for {code!r} (known: {known}).",
                         hint=f"Add {path} with sentence_end, clause_end and join_words.")
    return json.loads(path.read_text(encoding="utf-8"))


def _keep(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N", "M")


def norm_token(word: str) -> str:
    """One token reduced to letters, marks and digits, case-folded (what captions align on)."""
    return "".join(ch for ch in word.casefold() if _keep(ch))


def norm_words(text: str) -> list[str]:
    """Words for comparing a script with what was heard: case-folded, punctuation dropped, apostrophes inside words
    kept ("don't"), dashes split words, thousands separators inside numbers dropped ("2,500" -> "2500")."""
    t = text.casefold()
    for ch in APOSTROPHES:
        t = t.replace(ch, "'")
    for ch in DASHES:
        t = t.replace(ch, " ")
    t = re.sub(r"(?<=\d)[,  ](?=\d{3}\b)", "", t)
    out, cur = [], []
    for ch in t:
        if _keep(ch) or ch == "'" or (ch == "." and cur and cur[-1].isdigit()):
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    words = [w.strip("'.") for w in out]
    return [w for w in words if w]


def split_after(text: str, marks: list[str]) -> list[str]:
    """Split text after any of the given punctuation marks followed by whitespace (sentence or clause breaks)."""
    if not marks:
        return [text.strip()] if text.strip() else []
    cls = "".join(re.escape(m) for m in marks)
    return [p for p in re.split(rf"(?<=[{cls}])\s+", text.strip()) if p]


def ends_with(token: str, marks: list[str]) -> bool:
    return bool(token) and token.rstrip("\"'”’)")[-1:] in set(marks)
