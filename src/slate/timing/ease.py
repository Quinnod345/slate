"""Landing maths: when does a move that "lands on a word" have to start?

An entrance lands when it has covered 90% of its travel (the films' definition; MOTION.md §4 allows ±2 frames from the
word). For a cubic-bezier ease and a duration, T90 is the time at which progress reaches 0.9, and the move starts at
landing − T90. The eases themselves are brand data (the brand pack's named eases); this module only does the curve
maths, with the same bisection the films used so resolved frames match the delivered timelines exactly.
"""

from __future__ import annotations

from typing import Any

from slate.errors import UsageError

LINEAR = (0.0, 0.0, 1.0, 1.0)


def bezier_progress(curve: tuple[float, float, float, float], x: float) -> float:
    """Progress (y) of a CSS-style cubic bezier at time fraction x, by bisection on the curve parameter."""
    x1, y1, x2, y2 = curve
    lo, hi = 0.0, 1.0
    for _ in range(60):
        t = (lo + hi) / 2
        bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3
        lo, hi = (t, hi) if bx < x else (lo, t)
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3


def t_at(curve: tuple[float, float, float, float], dur: float, progress: float = 0.9) -> float:
    """Seconds into a move of length `dur` at which it has covered `progress` of its travel (T90 by default)."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bezier_progress(curve, mid) < progress else (lo, mid)
    return (lo + hi) / 2 * dur


def t90(curve: tuple[float, float, float, float], dur: float) -> float:
    return t_at(curve, dur, 0.9)


def _curve(value: Any) -> tuple[float, float, float, float] | None:
    """A bezier from the shapes a brand pack may use: [x1,y1,x2,y2], {"bezier": [...]}, {"x1":…}, "cubic-bezier(…)"."""
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(float(v) for v in value)  # type: ignore[return-value]
    if isinstance(value, str) and "(" in value:
        inner = value[value.index("(") + 1: value.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 4:
            return tuple(float(p) for p in parts)  # type: ignore[return-value]
        return None
    if isinstance(value, dict):
        for key in ("bezier", "cubic_bezier", "cubicBezier", "curve", "css"):
            if key in value:
                return _curve(value[key])
        if all(k in value for k in ("x1", "y1", "x2", "y2")):
            return (float(value["x1"]), float(value["y1"]), float(value["x2"]), float(value["y2"]))
    return None


def eases_from_motion(motion: dict | None) -> dict[str, tuple[float, float, float, float]]:
    """Named eases from a brand pack's motion block ({"eases": {name: curve}} or a list of {name, …})."""
    out: dict[str, tuple[float, float, float, float]] = {"linear": LINEAR}
    raw = (motion or {}).get("eases") or {}
    if isinstance(raw, list):
        raw = {str(e.get("name") or e.get("id")): e for e in raw if isinstance(e, dict)}
    for name, value in raw.items():
        curve = _curve(value)
        if curve:
            out[str(name)] = curve
    return out


def parse_land(spec: str, eases: dict[str, tuple[float, float, float, float]]) -> tuple[str, float, tuple]:
    """"arrive:0.53" -> ("arrive", 0.53, curve). Unknown eases are a usage error naming the brand's eases."""
    name, _, dur = str(spec).partition(":")
    name = name.strip()
    try:
        seconds = float(dur)
    except ValueError as exc:
        raise UsageError(f"A landing is written ease:seconds (e.g. 'arrive:0.5'); got {spec!r}.") from exc
    if name not in eases:
        known = ", ".join(sorted(eases)) or "none"
        raise UsageError(f"The brand has no ease called {name!r} (it has: {known}).")
    return name, seconds, eases[name]
