"""Valid format files for tests that need a film at a particular size, clock, runtime or budget.

`slate new --format some.json` validates the file through slate.formats.load, exactly as `slate formats check` would,
so a test's format has to be a real format. `write_format` takes the real template that comes in the aspect asked for
and changes only what the test names.
"""

from __future__ import annotations

import copy
import json
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _aspect(width: int, height: int) -> str:
    r = Fraction(int(width), int(height))
    return f"{r.numerator}:{r.denominator}"


def _template(aspect: str) -> dict:
    for path in sorted((REPO / "formats").glob("*.json")):
        if path.name.startswith("_"):
            continue
        fmt = json.loads(path.read_text())
        if aspect in (fmt.get("aspects") or {}):
            return fmt
    raise AssertionError(f"no format in formats/ comes in {aspect}; pick a size with a common aspect")


def _family(elevenlabs: float, higgsfield: float) -> str:
    """The smallest budget family in formats/_shared.json whose caps cover these amounts (the validator holds a
    format's budget to its family's)."""
    families = json.loads((REPO / "formats" / "_shared.json").read_text())["budgets"]
    for name in ("reel_or_ad", "explainer", "brand_film"):
        fam = families[name]
        if higgsfield <= fam["higgsfield"][1] and elevenlabs <= fam["elevenlabs"]["vo"][1]:
            return name
    raise AssertionError(f"no budget family covers elevenlabs {elevenlabs} and higgsfield {higgsfield}")


def write_format(path: Path, *, name: str = "test", width: int, height: int, fps: tuple[int, int] = (25, 1),
                 runtime_s: float = 12, elevenlabs: float = 1000, higgsfield: float = 0, **extra) -> Path:
    """Write a valid format file at `path`: one aspect at width x height, this clock and runtime, and a budget whose
    film defaults are `elevenlabs` credits and `higgsfield` dollars. Extra keys go on top (e.g. generation)."""
    aspect = _aspect(width, height)
    fmt = copy.deepcopy(_template(aspect))
    entry = dict(fmt["aspects"][aspect])
    entry.update(width=int(width), height=int(height))
    fmt.update({
        "name": name, "title": f"{name} (test fixture)", "summary": "A format made for a test.",
        "aspects": {aspect: entry}, "default_aspect": aspect, "fps_num": int(fps[0]), "fps_den": int(fps[1]),
        "runtime_s": {"min": runtime_s, "max": runtime_s, "target": runtime_s},
    })
    fmt.pop("autonomous_default", None)
    budget = dict(fmt.get("budget") or {})
    budget.update({"elevenlabs": {"vo": [0, elevenlabs], "score": [0, 0]}, "higgsfield": [0, higgsfield],
                   "family": _family(elevenlabs, higgsfield)})
    fmt["budget"] = budget
    fmt.update(extra)
    path.write_text(json.dumps(fmt, indent=1), encoding="utf-8")
    return path
