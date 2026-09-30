"""The ease maths of Slate's ExtendScript kit, mirrored in Python.

engine/ae/lib/slate_math.jsx is what runs inside After Effects; this module is the same maths for the Python side of
the AE lane: sampling the motion trace of an AE-built act (timing/motion/actN.json), resolving start times so a move
lands on a word, and turning a brand's eases into AE speed and influence. Both are ports of the Remotion kit's motion
law, step for step (the parser, the Newton-then-bisection solver, the T90 scan, the After Effects handle rule), and
the shared cross-language vectors (tests/vectors/eases.json at 1e-12, t90.json at 1e-9) hold the kit, this module
and the ExtendScript file together.

Kinds: "bezier" (CSS cubic-bezier, x1 and x2 in [0, 1]), "linear", "sine" ((1 - cos πu) / 2) and "spring0" (a
critically damped spring normalised to land on 1). "hold" is After Effects' own interpolation for a key, not a curve.
Every ease is exactly 0 at or before the start and exactly 1 at or after the end.
"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

from slate.errors import UsageError

SPRING0_STIFFNESS = 7.0
MIN_INFLUENCE = 0.1  # percent: After Effects' floor


# ---------------------------------------------------------------------------------------------------------------
# Reading an ease


def _bezier(values: Any) -> dict:
    try:
        v = [float(x) for x in values]
    except (TypeError, ValueError):
        raise UsageError(f"A cubic-bezier ease needs four numbers, not {values!r}.") from None
    if len(v) != 4 or not all(math.isfinite(x) for x in v):
        raise UsageError(f"A cubic-bezier ease needs four finite numbers, not {values!r}.")
    if not (0 <= v[0] <= 1 and 0 <= v[2] <= 1):
        raise UsageError(f"cubic-bezier x values must lie in [0, 1] (otherwise it isn't a function of time): {v}.")
    return {"kind": "bezier", "points": v}


def normalize(raw: Any) -> dict:
    """Any ease a brand pack may hold -> {"kind", "points"|"stiffness"} (same reading as the kit and SL.normEase)."""
    if raw is None:
        raise UsageError("An ease needs a definition.")
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("linear", "hold"):
            return {"kind": s}
        if s in ("sine", "sine-in-out", "sway"):
            return {"kind": "sine"}
        if s in ("spring0", "spring"):
            return {"kind": "spring0", "stiffness": SPRING0_STIFFNESS}
        m = re.fullmatch(r"cubic-bezier\(([^)]*)\)", s)
        if m:
            return _bezier(x.strip() for x in m.group(1).split(","))
        raise UsageError(f"Unknown ease {raw!r}.")
    if isinstance(raw, (list, tuple)):
        return _bezier(raw)
    if isinstance(raw, dict):
        if raw.get("kind") == "bezier" and raw.get("points") is not None and raw.get("bezier") is None:
            return _bezier(raw["points"])
        if raw.get("bezier") is not None:
            return normalize(raw["bezier"])
        if raw.get("cubic_bezier") is not None:
            cb = raw["cubic_bezier"]
            return normalize(cb if isinstance(cb, (list, tuple)) else f"cubic-bezier({cb})")
        kind = str(raw.get("kind", raw.get("type", ""))).lower()
        if kind in ("bezier", "cubic-bezier"):
            return normalize(raw.get("points", raw.get("values", raw.get("value"))))
        if kind in ("spring0", "spring"):
            try:
                k = float(raw.get("stiffness", SPRING0_STIFFNESS))
            except (TypeError, ValueError):
                k = SPRING0_STIFFNESS
            return {"kind": "spring0", "stiffness": k if math.isfinite(k) and k > 0 else SPRING0_STIFFNESS}
        if kind:
            return normalize(kind)
    raise UsageError(f"Unknown ease definition {raw!r}.")


def table(defs: dict | None) -> dict[str, dict]:
    """A brand's named eases, normalised."""
    return {str(k): normalize(v) for k, v in (defs or {}).items()}


def resolve(e: Any, eases: dict | None = None) -> dict:
    """A name in `eases` (or a built-in), or a definition."""
    if isinstance(e, str) and eases and e in eases:
        return normalize(eases[e])
    return normalize(e)


# ---------------------------------------------------------------------------------------------------------------
# Evaluating


def _bez(a: float, b: float, s: float) -> float:
    r = 1 - s
    return 3 * r * r * s * a + 3 * r * s * s * b + s * s * s


def _bez_slope(a: float, b: float, s: float) -> float:
    r = 1 - s
    return 3 * r * r * a + 6 * r * s * (b - a) + 3 * s * s * (1 - b)


def solve_bezier_x(x1: float, x2: float, u: float) -> float:
    """The curve parameter s where x(s) = u: Newton from s = u, then bisection when Newton stalls."""
    s = u
    for _ in range(8):
        err = _bez(x1, x2, s) - u
        if abs(err) < 1e-14:
            return s
        d = _bez_slope(x1, x2, s)
        if abs(d) < 1e-9:
            break
        s -= err / d
        if s < 0 or s > 1:
            break
    lo, hi = 0.0, 1.0
    s = u
    for _ in range(64):
        s = (lo + hi) / 2
        x = _bez(x1, x2, s)
        if abs(x - u) < 1e-15:
            return s
        if x < u:
            lo = s
        else:
            hi = s
    return (lo + hi) / 2


def bezier_y(p: Sequence[float], x: float) -> float:
    """A CSS cubic-bezier ease at progress x, with exact endpoints."""
    if not x > 0:
        return 0.0
    if x >= 1:
        return 1.0
    x1, y1, x2, y2 = p
    if x1 == y1 and x2 == y2:
        return x
    return _bez(y1, y2, solve_bezier_x(x1, x2, x))


def spring0_at(u: float, stiffness: float = SPRING0_STIFFNESS) -> float:
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    a = stiffness
    return (1 - (1 + a * u) * math.exp(-a * u)) / (1 - (1 + a) * math.exp(-a))


def value(e: Any, u: float, eases: dict | None = None) -> float:
    """Eased progress at u: exactly 0 at or before 0 (and for NaN), exactly 1 at or after 1."""
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    d = resolve(e, eases)
    kind = d["kind"]
    if kind == "linear":
        return float(u)
    if kind == "hold":
        return 0.0
    if kind == "sine":
        return (1 - math.cos(math.pi * u)) / 2
    if kind == "spring0":
        return spring0_at(u, d["stiffness"])
    return bezier_y(d["points"], u)


def _bisect(d: dict, lo: float, hi: float, level: float) -> float:
    for _ in range(80):
        mid = (lo + hi) / 2
        if value(d, mid) >= level:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def t_at(e: Any, level: float = 0.9, eases: dict | None = None) -> float:
    """The fraction of a move's duration at which an ease first covers `level` of its travel (scan, then bisect)."""
    d = resolve(e, eases)
    if level <= 0:
        return 0.0
    n = 4096
    if level >= 1:
        for i in range(1, n + 1):
            if value(d, i / n) >= 1:
                return _bisect(d, (i - 1) / n, i / n, 1.0)
        return 1.0
    prev = 0.0
    for i in range(1, n + 1):
        u = i / n
        if value(d, u) >= level:
            return _bisect(d, prev, u, level)
        prev = u
    return 1.0


def t90(e: Any, eases: dict | None = None) -> float:
    """T90: where an ease reaches 90% of its travel ("land" everywhere in Slate)."""
    return t_at(e, 0.9, eases)


def start_for(land: float, dur: float, e: Any, eases: dict | None = None) -> float:
    """The start time that lands a move (90% of its travel) on `land`."""
    return land - t90(e, eases) * dur


def leaves_range(e: Any, eases: dict | None = None) -> bool:
    """Whether an ease anticipates or overshoots (its curve leaves 0..1 on the way)."""
    d = resolve(e, eases)
    if d["kind"] != "bezier":
        return False
    p = d["points"]
    return p[1] < 0 or p[1] > 1 or p[3] < 0 or p[3] > 1


# ---------------------------------------------------------------------------------------------------------------
# After Effects


def ae_ease(points: Sequence[float]) -> dict:
    """A cubic-bezier as AE keyframe eases: {out: {influence %, speed x average}, in: {...}} (the kit's rule).

    A handle collapsed onto its keyframe takes AE's smallest influence and the curve's real end tangent, which points
    at the other control point; a vertical handle takes the smallest influence and the steepest speed that reaches it.
    """
    x1, y1, x2, y2 = (float(v) for v in points)

    def handle(dx: float, dy: float, odx: float, ody: float) -> dict:
        if dx > 0:
            return {"influence": max(dx * 100, MIN_INFLUENCE), "speed": dy / dx}
        if dy == 0:
            return {"influence": MIN_INFLUENCE, "speed": ody / odx if odx > 0 else 1.0}
        return {"influence": MIN_INFLUENCE, "speed": dy / (MIN_INFLUENCE / 100)}

    return {"out": handle(x1, y1, x2, y2), "in": handle(1 - x2, 1 - y2, 1 - x1, 1 - y1)}


def seg_ease(points: Sequence[float], dv: float) -> dict:
    """One segment's AE eases for a signed average velocity dv: {out: [speed, infl%], inn: [speed, infl%]}."""
    a = ae_ease(points)
    return {"out": [a["out"]["speed"] * dv, min(100.0, a["out"]["influence"])],
            "inn": [a["in"]["speed"] * dv, min(100.0, a["in"]["influence"])]}


def ae_segment_value(seg: dict, dv: float, u: float) -> float:
    """How AE evaluates an eased segment (normalised to 0..1): proves seg_ease gives back the ease it came from."""
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    p1x = seg["out"][1] / 100
    p2x = 1 - seg["inn"][1] / 100
    p1y = seg["out"][0] * p1x / dv if dv else 0.0
    p2y = 1 - seg["inn"][0] * (seg["inn"][1] / 100) / dv if dv else 1.0
    return _bez(p1y, p2y, solve_bezier_x(p1x, p2x, u))


def exact_bezier_y(p: Sequence[float], x: float) -> float:
    """An independent reference for bezier_y: the cubic x(t) = x solved in closed form (numpy roots), then y(t).

    Used for the local vectors stand-in, so a reference never shares the solver it checks.
    """
    import numpy as np  # noqa: PLC0415

    if not x > 0:
        return 0.0
    if x >= 1:
        return 1.0
    x1, _y1, x2, _y2 = p
    coeffs = [3 * x1 - 3 * x2 + 1, 3 * x2 - 6 * x1, 3 * x1, -x]
    roots = [r.real for r in np.roots(coeffs) if abs(r.imag) < 1e-9 and -1e-9 <= r.real <= 1 + 1e-9]
    if not roots:
        raise ValueError(f"no root for x={x} on {list(p)}")
    t = min(max(roots[0], 0.0), 1.0)
    for _ in range(30):
        s = 1 - t
        fx = 3 * s * s * t * x1 + 3 * s * t * t * x2 + t ** 3 - x
        dfx = 3 * s * s * x1 + 6 * s * t * (x2 - x1) + 3 * t * t * (1 - x2)
        if dfx == 0:
            break
        t = min(max(t - fx / dfx, 0.0), 1.0)
    s = 1 - t
    return 3 * s * s * t * p[1] + 3 * s * t * t * p[3] + t ** 3
