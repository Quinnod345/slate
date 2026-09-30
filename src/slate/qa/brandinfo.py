"""What the gates need from a brand pack: its banned words, its approved claims and its never-claim list.

There is one banned-word list per brand, and it lives in the brand pack (`truth.banned_words`). The films kept the
list in three places (the storyboard builder, the validator and the asset scanner), and two of them drifted apart;
here every surface (script, captions, OCR of the picture) reads the same list through this module, from the pack.

The brand module (`slate.brand.pack`) is the owner of packs. When it's installed its functions are used; until then
this reads `brand.json` from the brand folders directly, with the same keys (docs/ARCHITECTURE.md §7).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from slate import jsonio, paths
from slate.errors import SlateError


def _pack_module():
    try:
        return importlib.import_module("slate.brand.pack")
    except ImportError:
        return None


def find(name: str) -> Path | None:
    """The folder of a brand pack by name (or a path to a pack folder or its brand.json)."""
    p = Path(str(name)).expanduser()
    if p.name == "brand.json" and p.is_file():
        return p.parent
    if (p / "brand.json").is_file():
        return p
    for d in paths.brand_dirs():
        if (d / str(name) / "brand.json").is_file():
            return d / str(name)
    return None


def load(name: str | None) -> tuple[dict | None, str]:
    """(pack, where it came from). (None, reason) when the film names no brand or the pack can't be found."""
    if not name:
        return None, "film.json names no brand"
    mod = _pack_module()
    if mod is not None and hasattr(mod, "load"):
        try:
            pack = mod.load(name)
            if isinstance(pack, dict):
                return pack, "slate.brand.pack"
        except SlateError as exc:
            return None, f"slate.brand.pack couldn't load {name!r}: {exc}"
        except Exception as exc:  # noqa: BLE001 - a broken brand module must not take the gates down
            return None, f"slate.brand.pack failed on {name!r}: {type(exc).__name__}: {exc}"
    folder = find(name)
    if folder is None:
        return None, f"no brand pack called {name!r} in {', '.join(str(d) for d in paths.brand_dirs())}"
    try:
        return jsonio.read(folder / "brand.json"), str(folder / "brand.json")
    except SlateError as exc:
        return None, str(exc)


def pack_file(name: str | None) -> Path | None:
    """The brand.json a pack was read from, for gate inputs (a changed banned list makes old verdicts STALE)."""
    folder = find(name) if name else None
    return folder / "brand.json" if folder is not None else None


def _truth(pack: dict) -> dict:
    t = pack.get("truth")
    return t if isinstance(t, dict) else {}


def banned_words(pack: dict) -> list[str] | None:
    """The brand's one banned-word list. None when the pack doesn't say (unknown); [] when it says none."""
    mod = _pack_module()
    if mod is not None and hasattr(mod, "banned_words"):
        try:
            words = mod.banned_words(pack)
            if words is not None:
                return [str(w) for w in words if str(w).strip()]
        except Exception:  # noqa: BLE001 - fall back to reading the key
            pass
    truth = _truth(pack)
    if "banned_words" not in truth:
        return None
    return [str(w) for w in (truth.get("banned_words") or []) if str(w).strip()]


def approved_claims(pack: dict) -> list[dict]:
    mod = _pack_module()
    if mod is not None and hasattr(mod, "approved_claims"):
        try:
            return [c if isinstance(c, dict) else {"text": str(c)} for c in mod.approved_claims(pack) or []]
        except Exception:  # noqa: BLE001
            pass
    return [c if isinstance(c, dict) else {"text": str(c)} for c in _truth(pack).get("approved_claims") or []]


def never_claim(pack: dict) -> list[str]:
    items: Any = _truth(pack).get("never_claim") or []
    return [str(x.get("text") if isinstance(x, dict) else x) for x in items if x]


def claim_text(claim: dict) -> str:
    for key in ("text", "claim", "statement", "line"):
        if isinstance(claim.get(key), str):
            return claim[key]
    return ""
