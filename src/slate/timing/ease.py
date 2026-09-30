"""Landing maths: when does a move that "lands on a word" have to start?

An entrance lands when it has covered 90% of its travel (the films' definition; MOTION.md §4 allows ±2 frames from the
word). For a cubic-bezier ease and a duration, T90 is the time at which progress reaches 0.9, and the move starts at
landing − T90. The eases themselves are brand data (the brand pack's named eases); this module only does the curve
maths, with the same bisection the films used so resolved frames match the delivered timelines exactly.

A brand's eases aren't all beziers: a zero-bounce spring ("spring0"), "sine" and "linear" are curves too. Those go
through the motion law's own maths (`slate.motion.eases`, the kit's twin, held to the shared T90 vectors), so a move
that lands on a spring lands where the kit draws it.
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


def t90(curve, dur: float) -> float:
    """Seconds into a move of length `dur` at which it has covered 90% of its travel. `curve` is a bezier tuple, or a
    spring, sine or linear curve from the motion law."""
    if isinstance(curve, tuple):
        return t_at(curve, dur, 0.9)
    from slate.motion import eases as motion_eases  # noqa: PLC0415

    return motion_eases.t90(curve) * dur


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


def _other_curve(value: Any):
    """A spring, sine or linear ease (or a bezier in a shape `_curve` doesn't read), through the motion law's parser.
    Returns a bezier tuple, a motion-law curve, or None when nobody can read it."""
    try:
        from slate.motion import eases as motion_eases  # noqa: PLC0415
    except ImportError:
        return None
    spec = motion_eases.parse(value)
    if spec is None:
        return None
    if spec.kind == "bezier" and spec.bezier:
        return tuple(float(v) for v in spec.bezier)
    return spec


def eases_from_motion(motion: dict | None) -> dict[str, Any]:
    """Named eases from a brand pack's motion block ({"eases": {name: curve}} or a list of {name, …}).

    Beziers come back as (x1, y1, x2, y2); springs, sine and linear as the motion law's curves. A name whose value no
    one can read maps to None, so a landing on it says what's wrong instead of claiming the brand lacks it."""
    out: dict[str, Any] = {"linear": LINEAR}
    raw = (motion or {}).get("eases") or {}
    if isinstance(raw, list):
        raw = {str(e.get("name") or e.get("id")): e for e in raw if isinstance(e, dict)}
    for name, value in raw.items():
        curve = _curve(value) or _other_curve(value)
        if str(name) == "linear" and curve is None:
            continue  # linear is always there
        out[str(name)] = curve
    return out


def parse_land(spec: str, eases: dict[str, Any]) -> tuple[str, float, Any]:
    """"arrive:0.53" -> ("arrive", 0.53, curve). Unknown eases are a usage error naming the brand's eases."""
    name, _, dur = str(spec).partition(":")
    name = name.strip()
    try:
        seconds = float(dur)
    except ValueError as exc:
        raise UsageError(f"A landing is written ease:seconds (e.g. 'arrive:0.5'); got {spec!r}.") from exc
    if name not in eases:
        known = ", ".join(sorted(k for k, v in eases.items() if v is not None)) or "none"
        raise UsageError(f"The brand has no ease called {name!r} (it has: {known}).")
    if eases[name] is None:
        raise UsageError(f"The brand's ease {name!r} isn't a curve a landing can be timed with (a bezier, linear, sine "
                         "or a zero-bounce spring); fix it in the brand pack, or land this move on another ease.")
    return name, seconds, eases[name]
