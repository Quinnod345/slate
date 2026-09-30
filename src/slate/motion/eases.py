"""The motion law's maths in Python: eases with exact endpoints, T90, landing a move on a beat, monotone splines,
staggers, and the After Effects speed/influence of a cubic-bezier.

This mirrors the Remotion kit (engine/remotion/kit/core/eases.ts, spline.ts, motion.ts) step for step: the same
solver, the same order of operations, so both give the same numbers to about 1e-15. The two are held together by
the shared vectors (tests/vectors/eases.json and t90.json), which pytest, vitest and the After Effects ExtendScript
runner all check. Change one side and the vectors fail until the other matches.

A brand's named eases live in its pack (`motion.eases`); nothing here knows which curves a brand uses. Every ease
returns exactly 0 at or before the start and exactly 1 at or after the end: bisection-solved beziers used to leave
1e-14 before a move, and "has it started?" checks opened early all over the films.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

SPRING0_STIFFNESS = 7.0
AE_MIN_INFLUENCE = 0.1  # percent; After Effects' smallest influence


@dataclass(frozen=True)
class EaseSpec:
    """One curve: kind is "bezier" (with `bezier`), "linear", "sine" or "spring0" (with `stiffness`)."""

    kind: str
    bezier: tuple[float, float, float, float] | None = None
    stiffness: float = SPRING0_STIFFNESS

    def to_dict(self) -> dict:
        if self.kind == "bezier":
            return {"kind": "bezier", "bezier": list(self.bezier or ())}
        if self.kind == "spring0":
            return {"kind": "spring0", "stiffness": self.stiffness}
        return {"kind": self.kind}


# --------------------------------------------------------------------------------------------------------- bezier
def _bez(a: float, b: float, s: float) -> float:
    r = 1 - s
    return 3 * r * r * s * a + 3 * r * s * s * b + s * s * s


def _bez_slope(a: float, b: float, s: float) -> float:
    r = 1 - s
    return 3 * r * r * a + 6 * r * s * (b - a) + 3 * s * s * (1 - b)


def solve_bezier_x(x1: float, x2: float, u: float) -> float:
    """The curve parameter s where x(s) = u: Newton from s = u, then bisection when Newton stalls (as the kit does)."""
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


def bezier_at(b: Sequence[float], u: float) -> float:
    """A CSS-style cubic-bezier ease at progress u, with exact endpoints."""
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    x1, y1, x2, y2 = b
    if x1 == y1 and x2 == y2:
        return u
    s = solve_bezier_x(x1, x2, u)
    return _bez(y1, y2, s)


def sine_at(u: float) -> float:
    """(1 − cos πu) ÷ 2: at rest at both ends."""
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    return (1 - math.cos(math.pi * u)) / 2


def spring0_at(u: float, stiffness: float = SPRING0_STIFFNESS) -> float:
    """A critically damped spring's shape, normalised to land exactly on 1: quick stroke, no overshoot."""
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    a = stiffness

    def raw(x: float) -> float:
        return 1 - (1 + a * x) * math.exp(-a * x)

    return raw(u) / raw(1)


def evaluate(spec: EaseSpec, u: float) -> float:
    """An ease at progress u: exactly 0 at or before 0 (and for NaN), exactly 1 at or after 1."""
    if not u > 0:
        return 0.0
    if u >= 1:
        return 1.0
    if spec.kind == "linear":
        return u
    if spec.kind == "sine":
        return sine_at(u)
    if spec.kind == "spring0":
        return spring0_at(u, spec.stiffness)
    if spec.kind == "bezier" and spec.bezier is not None:
        return bezier_at(spec.bezier, u)
    raise ValueError(f"unknown ease kind {spec.kind!r}")


def _bezier_spec(values: Iterable[Any]) -> EaseSpec | None:
    try:
        v = [float(x) for x in values]
    except (TypeError, ValueError):
        return None
    if len(v) != 4 or any(not math.isfinite(x) for x in v):
        return None
    x1, y1, x2, y2 = v
    if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
        return None  # not a function of time
    return EaseSpec("bezier", (x1, y1, x2, y2))


def parse(raw: Any) -> EaseSpec | None:
    """Read an ease from a brand pack the way the kit does: [x1, y1, x2, y2], "cubic-bezier(…)", {bezier: […]},
    {cubic_bezier: …}, "linear", "sine", {kind: "spring0", stiffness}. None when it can't."""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == "linear":
            return EaseSpec("linear")
        if s in ("sine", "sine-in-out", "sway"):
            return EaseSpec("sine")
        if s in ("spring0", "spring"):
            return EaseSpec("spring0", stiffness=SPRING0_STIFFNESS)
        m = re.fullmatch(r"cubic-bezier\(([^)]*)\)", s)
        if m:
            return _bezier_spec(x.strip() for x in m.group(1).split(","))
        return None
    if isinstance(raw, (list, tuple)):
        return _bezier_spec(raw)
    if isinstance(raw, dict):
        if raw.get("bezier") is not None:
            return parse(raw["bezier"])
        if raw.get("cubic_bezier") is not None:
            cb = raw["cubic_bezier"]
            return parse(cb if isinstance(cb, (list, tuple)) else f"cubic-bezier({cb})")
        kind = str(raw.get("kind", raw.get("type", ""))).lower()
        if kind in ("bezier", "cubic-bezier"):
            return parse(raw.get("points", raw.get("values", raw.get("value"))))
        if kind in ("spring0", "spring"):
            try:
                k = float(raw.get("stiffness", SPRING0_STIFFNESS))
            except (TypeError, ValueError):
                k = SPRING0_STIFFNESS
            return EaseSpec("spring0", stiffness=k if math.isfinite(k) and k > 0 else SPRING0_STIFFNESS)
        if kind:
            return parse(kind)
    return None


# ---------------------------------------------------------------------------------------------------------- T90
def _bisect(spec: EaseSpec, lo: float, hi: float, level: float) -> float:
    for _ in range(80):
        mid = (lo + hi) / 2
        if evaluate(spec, mid) >= level:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def t_at(spec: EaseSpec, level: float = 0.9) -> float:
    """The fraction of a move's duration at which an ease first covers `level` of its travel (scan, then bisect)."""
    if level <= 0:
        return 0.0
    n = 4096
    if level >= 1:
        for i in range(1, n + 1):
            if evaluate(spec, i / n) >= 1:
                return _bisect(spec, (i - 1) / n, i / n, 1.0)
        return 1.0
    prev = 0.0
    for i in range(1, n + 1):
        u = i / n
        if evaluate(spec, u) >= level:
            return _bisect(spec, prev, u, level)
        prev = u
    return 1.0


def t90(spec: EaseSpec) -> float:
    """T90: where an ease reaches 90% of its travel. "Land" means 90% of travel everywhere in Slate."""
    return t_at(spec, 0.9)


def start_for(land: float, dur: float, spec: EaseSpec) -> float:
    """The start time that lands a move (90% of travel) on `land`."""
    return land - t90(spec) * dur


# -------------------------------------------------------------------------------------------------- After Effects
def ae_keyframe_ease(b: Sequence[float]) -> dict:
    """A cubic-bezier as AE keyframe eases: {out: {influence %, speed ×v}, in: {…}} (speed as a multiple of the
    segment's average speed). A handle collapsed onto its keyframe takes AE's minimum influence and the curve's real
    end tangent, which points at the other control point."""
    x1, y1, x2, y2 = (float(v) for v in b)

    def handle(dx: float, dy: float, other_dx: float, other_dy: float) -> dict:
        if dx > 0:
            return {"influence": max(dx * 100, AE_MIN_INFLUENCE), "speed": dy / dx}
        if dy == 0:
            return {"influence": AE_MIN_INFLUENCE, "speed": other_dy / other_dx if other_dx > 0 else 1.0}
        return {"influence": AE_MIN_INFLUENCE, "speed": dy / (AE_MIN_INFLUENCE / 100)}

    return {"out": handle(x1, y1, x2, y2), "in": handle(1 - x2, 1 - y2, 1 - x1, 1 - y1)}


# -------------------------------------------------------------------------------------------------------- splines
def _sorted_keys(keys: Iterable[Sequence[float]]) -> list[tuple[float, float]]:
    ks = sorted(((float(t), float(v)) for t, v in keys if math.isfinite(float(t))), key=lambda k: k[0])
    out: list[tuple[float, float]] = []
    for k in ks:
        if out and out[-1][0] == k[0]:
            out[-1] = k  # two keys at one time: the later one wins
        else:
            out.append(k)
    return out


def _tangents(ts: list[float], vs: list[float]) -> list[float]:
    n = len(ts)
    h = [ts[i + 1] - ts[i] for i in range(n - 1)]
    d = [(vs[i + 1] - vs[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            continue
        w1 = 2 * h[i] + h[i - 1]
        w2 = h[i] + 2 * h[i - 1]
        m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    return m


def mono_spline(t: float, keys: Iterable[Sequence[float]]) -> float:
    """A monotone cubic through timed keys [t, v] (Fritsch–Carlson tangents, Butland's mean): never leaves the range
    of the two keys it's between, at rest at the first and last key, exact at every key."""
    k = _sorted_keys(keys)
    n = len(k)
    if not n:
        return 0.0
    if n == 1 or t <= k[0][0]:
        return k[0][1]
    if t >= k[-1][0]:
        return k[-1][1]
    ts = [x[0] for x in k]
    vs = [x[1] for x in k]
    m = _tangents(ts, vs)
    i = 0
    while i < n - 2 and t >= ts[i + 1]:
        i += 1
    h = ts[i + 1] - ts[i]
    u = (t - ts[i]) / h
    h00 = 2 * u * u * u - 3 * u * u + 1
    h10 = u * u * u - 2 * u * u + u
    h01 = -2 * u * u * u + 3 * u * u
    h11 = u * u * u - u * u
    return h00 * vs[i] + h10 * h * m[i] + h01 * vs[i + 1] + h11 * h * m[i + 1]


# ------------------------------------------------------------------------------------------------ the brand's law
NEUTRAL_MOTION: dict = {
    "eases": {
        "enter": {"bezier": [0, 0, 0.58, 1], "lineage": "CSS ease-out (W3C)"},
        "move": {"kind": "sine", "lineage": "sine in-out"},
        "state": {"bezier": [0.42, 0, 0.58, 1], "lineage": "CSS ease-in-out (W3C)"},
        "leave": {"bezier": [0.42, 0, 1, 1], "lineage": "CSS ease-in (W3C)"},
        "linear": {"kind": "linear", "lineage": "time and progress only"},
    },
    "durations": {"tick": 0.2, "quick": 0.33, "base": 0.5, "broad": 0.8, "camera": 1.2, "signature": 1.8},
    "staggers": {"char": 0.05, "word": 0.1, "line": 0.133, "block": 0.2},
    "stagger_cap": {"seconds": 0.6, "fraction": 0.5},
    "exit_factor": 0.75,
    "roles": {"entrance": "enter", "move": "move", "state": "state", "exit": "leave"},
    "overshoot": {"allowed": False, "max": 0},
}


@dataclass
class MotionLaw:
    """A brand's motion law, read from its pack's `motion` (see the kit's motionLawFrom for the same reading)."""

    eases: dict[str, EaseSpec]
    durations: dict[str, float]
    staggers: dict[str, float]
    stagger_cap_seconds: float
    stagger_cap_fraction: float
    exit_factor: float
    roles: dict[str, str]
    neutral: bool
    problems: list[str]

    def ease(self, name: str) -> EaseSpec:
        if name in self.eases:
            return self.eases[name]
        if name in ("linear", "sine", "spring0"):
            return EaseSpec(name, stiffness=SPRING0_STIFFNESS) if name == "spring0" else EaseSpec(name)
        role = self.roles.get(name)
        if role and role in self.eases:
            return self.eases[role]
        raise KeyError(f"{name!r} isn't one of the brand's named eases ({', '.join(self.eases) or 'none'})")

    def dur(self, token: str | float) -> float:
        if isinstance(token, (int, float)):
            return float(token)
        if token not in self.durations:
            raise KeyError(f"duration {token!r} isn't on the brand's scale ({', '.join(self.durations)})")
        return self.durations[token]

    def stagger_step(self, unit: str | float, n: int, entrance_dur: float | None = None) -> float:
        """The step between staggered items, capped so the group's spread stays within the brand's caps."""
        base = float(unit) if isinstance(unit, (int, float)) else self.staggers[unit]
        if n <= 1:
            return 0.0
        cap = self.stagger_cap_seconds
        if entrance_dur is not None and entrance_dur > 0:
            cap = min(cap, self.stagger_cap_fraction * entrance_dur)
        return min(base, cap / (n - 1))


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


ROLE_GUESSES: dict[str, tuple[str, ...]] = {
    "entrance": ("entrance", "enter", "arrive", "in", "appear", "reveal"),
    "move": ("move", "glide", "carry", "travel", "camera"),
    "state": ("state", "snap", "toggle", "switch"),
    "exit": ("exit", "leave", "out", "depart"),
    "relayout": ("relayout", "lift", "make-room", "shift"),
}


def _roles(given: dict, names: list[str], neutral: bool, problems: list[str]) -> dict[str, str]:
    """Which named ease does each job: the pack's roles, else a guess from the names (the kit's motionLawFrom does
    exactly this, so both sides agree which ease an entrance uses)."""
    roles: dict[str, str] = {}
    for role in ("entrance", "move", "state", "exit", "relayout"):
        pick = given.get(role)
        if pick and pick in names:
            roles[role] = pick
            continue
        if pick:
            problems.append(f"role {role!r} names ease {pick!r}, which the brand doesn't define")
        guess = next((n for n in names if n.lower() in ROLE_GUESSES[role]), None)
        if guess:
            roles[role] = guess
        elif role != "relayout":
            roles[role] = names[0] if names else "sine"
            if not neutral:
                problems.append(f"no ease for the {role} role; add motion.roles.{role}")
    for role, pick in given.items():
        if role not in roles and pick in names:
            roles[role] = pick
    return roles


def law_from(motion: dict | None, fps: float | None = None) -> MotionLaw:
    """Read a brand pack's `motion` into a law (the neutral law stands in, noted as a problem, when it has none)."""
    problems: list[str] = []
    neutral = not motion or not motion.get("eases")
    if neutral:
        problems.append("the brand has no motion law (motion.eases); the kit's neutral law stands in")
        m = {**NEUTRAL_MOTION, **(motion or {}), "eases": NEUTRAL_MOTION["eases"]}
    else:
        m = motion  # type: ignore[assignment]
    eases: dict[str, EaseSpec] = {}
    for name, raw in (m.get("eases") or {}).items():
        spec = parse(raw)
        if spec is None:
            problems.append(f"ease {name!r} isn't a curve the kit can read")
        else:
            eases[name] = spec
    durations: dict[str, float] = {}
    raw_dur = m.get("durations") or NEUTRAL_MOTION["durations"]
    items = [(d.get("name"), d.get("seconds")) for d in raw_dur] if isinstance(raw_dur, list) else list(raw_dur.items())
    for name, v in items:
        s = _num(v)
        if s is None and isinstance(v, dict):
            s = _num(v.get("seconds", v.get("s")))
        if s is None or s <= 0:
            problems.append(f"duration {name!r} has no positive seconds")
        else:
            durations[str(name)] = s
    staggers: dict[str, float] = {}
    for name, v in (m.get("staggers") or NEUTRAL_MOTION["staggers"]).items():
        s = _num(v)
        if s is None and isinstance(v, dict):
            s = _num(v.get("seconds"))
            if s is None:
                frames = _num(v.get("frames"))
                at = _num(v.get("at_fps")) or fps
                s = frames / at if frames is not None and at else None
        if s is None:
            problems.append(f"stagger {name!r} needs seconds, or frames and a rate")
        else:
            staggers[name] = s
    cap = m.get("stagger_cap") or {}
    roles = _roles(m.get("roles") or {}, list(eases), neutral, problems)
    return MotionLaw(
        eases=eases,
        durations=durations,
        staggers=staggers,
        stagger_cap_seconds=_num(cap.get("seconds")) or 0.6,
        stagger_cap_fraction=_num(cap.get("fraction")) or 0.5,
        exit_factor=_num(m.get("exit_factor")) or 0.75,
        roles=roles,
        neutral=neutral,
        problems=problems,
    )


def lands_on(law: MotionLaw, land: float, duration: str | float, ease: str, fps: float) -> tuple[int, int]:
    """Whole film frames (frame_in, frame_out) for a move that lands 90% of its travel on `land` (film seconds),
    rounded like slate.clock (Python's round), the same as the kit's landOn()."""
    d = law.dur(duration)
    frame_in = int(round(start_for(land, d, law.ease(ease)) * fps))
    return frame_in, frame_in + max(1, int(round(d * fps)))
