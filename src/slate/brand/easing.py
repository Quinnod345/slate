"""A product's own motion values, turned into the named eases a brand's motion law is made of.

Brand onboarding reads the motion a product already has (docs/PLAN.md §4, "Look and motion"), because a film feels
like the product only when it moves the way the product does:

- **CSS easing tokens.** `cubic-bezier(x1, y1, x2, y2)` is exactly the curve the kit, the After Effects kit and the
  motion gate use, so it is taken as it is. The keywords (`ease`, `ease-in`, `ease-out`, `ease-in-out`) are the W3C's
  fixed curves. `linear(…)` point lists (the way the web writes springs) are fitted with a cubic-bezier, and the fit's
  error is recorded. `steps()` isn't a curve and is reported, never guessed.
- **SwiftUI animations.** `.timingCurve(…)`, `.easeIn/Out/InOut` and `.linear` are beziers already. A spring
  (`.spring(response:dampingFraction:)`, `.spring(duration:bounce:)`, `.interpolatingSpring(stiffness:damping:)`,
  `Spring(…)`, `.smooth`, `.snappy`, `.bouncy`) is converted from its physics: stiffness k = m·(2π / response)², damping
  c = 4π·m·ζ / response. Its step response is followed until it stays within 0.1% of its travel (about a pixel on a
  1000 px move); that is its duration. A critically damped spring (ζ = 1) is exactly the kit's `spring0` curve with
  stiffness ω₀·T, so it is written as that. Any other spring is fitted with a cubic-bezier, which can hold one
  overshoot, and its peak and fit error are recorded so a person can see what the bezier gave up.

Where SwiftUI's own defaults are used (an argument left out), the ease says so in `assumed`, because the defaults
belong to the SDK the app builds with, and onboarding asks a person to confirm them.

Nothing here knows a brand. Every ease carries its lineage: what it came from, and where (`path:line`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from slate.motion.eases import SPRING0_STIFFNESS, bezier_at

#: W3C CSS Easing's keyword curves (https://www.w3.org/TR/css-easing-1/#cubic-bezier-easing-functions).
CSS_KEYWORDS: dict[str, tuple[float, float, float, float]] = {
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
}

#: SwiftUI's timing curves, the standard ones (the same control points as Core Animation's named curves).
SWIFT_CURVES: dict[str, tuple[float, float, float, float]] = {
    "easeIn": (0.42, 0.0, 1.0, 1.0),
    "easeOut": (0.0, 0.0, 0.58, 1.0),
    "easeInOut": (0.42, 0.0, 0.58, 1.0),
}

#: SwiftUI's documented defaults, used only when an argument is left out, and always reported as assumed.
SWIFT_DEFAULTS: dict[str, dict[str, float]] = {
    "spring": {"response": 0.5, "dampingFraction": 0.825, "blendDuration": 0.0},
    "interactiveSpring": {"response": 0.15, "dampingFraction": 0.86, "blendDuration": 0.25},
    "curve": {"duration": 0.35},
    "smooth": {"duration": 0.5, "bounce": 0.0},
    "snappy": {"duration": 0.5, "bounce": 0.15},
    "bouncy": {"duration": 0.5, "bounce": 0.3},
    "interpolatingSpring": {"mass": 1.0, "stiffness": 170.0, "damping": 15.0},
}

SETTLE = 0.001  # a spring has settled once it stays within 0.1% of its travel
FIT_SAMPLES = 601


class EasingError(ValueError):
    """A motion value that can't become a curve (with the reason, in plain words)."""


# ------------------------------------------------------------------------------------------------------ numbers
def _float(text: Any) -> float:
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        raise EasingError(f"{text!r} isn't a number") from None
    if not math.isfinite(v):
        raise EasingError(f"{text!r} isn't a finite number")
    return v


def css_time(text: str) -> float | None:
    """Seconds from a CSS time (`220ms`, `0.3s`, `.5s`). None when it isn't a plain time."""
    m = re.fullmatch(r"\s*(-?\d*\.?\d+)\s*(ms|s)\s*", str(text), re.IGNORECASE)
    if not m:
        return None
    v = float(m.group(1))
    return v / 1000.0 if m.group(2).lower() == "ms" else v


# ------------------------------------------------------------------------------------------------------ fitting
def _bezier_y_at(p: Sequence[float], u: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = p
    s = np.linspace(0.0, 1.0, 4001)
    bx = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s ** 2 * x2 + s ** 3
    by = 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s ** 2 * y2 + s ** 3
    return np.interp(u, bx, by)


def fit_curve(u: Sequence[float], y: Sequence[float]) -> tuple[list[float], float]:
    """Least-squares cubic-bezier through a normalised curve (u and y both 0…1 at the ends; y may overshoot).

    Returns (bezier rounded to 4 places, RMS error as a fraction of the travel, measured on the exact curve). x1 and x2
    stay within 0…1, so the result is always a function of time.
    """
    from scipy.optimize import least_squares  # noqa: PLC0415 - scipy is heavy; only fits need it

    uu, yy = np.asarray(u, dtype=float), np.asarray(y, dtype=float)
    if uu.size < 4 or uu.size != yy.size:
        raise EasingError("a curve needs at least four samples, one value per time")
    best: tuple[float, np.ndarray] | None = None
    for start in ([0.25, 0.1, 0.25, 1.0], [0.2, 1.0, 0.3, 1.0], [0.4, 0.0, 0.2, 1.0], [0.3, 1.4, 0.4, 1.0]):
        res = least_squares(lambda q: _bezier_y_at(q, uu) - yy, start,
                            bounds=([0.0, -0.5, 0.0, -0.5], [1.0, 2.0, 1.0, 2.0]))
        cost = float(np.mean(res.fun ** 2))
        if best is None or cost < best[0]:
            best = (cost, res.x)
    assert best is not None
    bez = [round(float(v), 4) for v in best[1]]
    exact = np.array([bezier_at(bez, float(x)) for x in uu])
    return bez, float(np.sqrt(np.mean((exact - yy) ** 2)))


def curve_peak(ease: dict) -> float:
    """The highest point the drawn curve reaches (1.0 unless it overshoots), sampled finely."""
    bez = ease.get("bezier") if isinstance(ease, dict) else None
    if not bez:
        return 1.0
    u = np.linspace(0.0, 1.0, 2001)
    return float(max(1.0, max(bezier_at(bez, float(x)) for x in u)))


# ------------------------------------------------------------------------------------------------------------ CSS
def css_easing(text: str) -> dict:
    """Read one CSS easing value.

    Returns {"kind": "bezier", "bezier": [x1, y1, x2, y2]}, {"kind": "linear"}, {"kind": "points", "points": [[x, y]…]}
    (a `linear()` list) or {"kind": "steps", "steps": n, "position": …}. Raises EasingError when it isn't an easing.
    """
    raw = str(text).strip()
    t = raw.lower()
    if t == "linear":
        return {"kind": "linear", "css": raw}
    if t in CSS_KEYWORDS:
        return {"kind": "bezier", "bezier": list(CSS_KEYWORDS[t]), "css": raw}
    if t in ("step-start", "step-end"):
        return {"kind": "steps", "steps": 1, "position": "start" if t == "step-start" else "end", "css": raw}
    m = re.fullmatch(r"cubic-bezier\(\s*([^)]*)\)", t)
    if m:
        parts = [p for p in re.split(r"[\s,]+", m.group(1).strip()) if p]
        if len(parts) != 4:
            raise EasingError(f"{raw} needs four numbers, x1, y1, x2, y2")
        x1, y1, x2, y2 = (_float(p) for p in parts)
        if not (0.0 <= x1 <= 1.0 and 0.0 <= x2 <= 1.0):
            raise EasingError(f"{raw}: x1 and x2 must lie within 0…1 (CSS rejects it too)")
        return {"kind": "bezier", "bezier": [x1, y1, x2, y2], "css": raw}
    m = re.fullmatch(r"steps\(\s*(\d+)\s*(?:,\s*([a-z-]+))?\s*\)", t)
    if m:
        return {"kind": "steps", "steps": int(m.group(1)), "position": m.group(2) or "end", "css": raw}
    m = re.fullmatch(r"linear\(\s*(.+)\)", t, re.DOTALL)
    if m:
        return {"kind": "points", "points": _linear_points(m.group(1), raw), "css": raw}
    raise EasingError(f"{raw!r} isn't a CSS easing (cubic-bezier(), linear(), steps() or a keyword)")


def _linear_points(body: str, raw: str) -> list[list[float]]:
    """CSS `linear()` stops as [x, y] with every missing position filled in the way the spec says."""
    stops: list[tuple[float, list[float]]] = []
    for part in body.split(","):
        bits = part.split()
        if not bits:
            continue
        y = _float(bits[0])
        xs = []
        for b in bits[1:]:
            if not b.endswith("%"):
                raise EasingError(f"{raw}: a linear() stop's position is a percentage")
            xs.append(_float(b[:-1]) / 100.0)
        stops.append((y, xs))
    if len(stops) < 2:
        raise EasingError(f"{raw}: linear() needs at least two stops")
    pts: list[list[float | None]] = []
    for y, xs in stops:
        if not xs:
            pts.append([None, y])
        for x in xs:
            pts.append([x, y])
    if pts[0][0] is None:
        pts[0][0] = 0.0
    if pts[-1][0] is None:
        pts[-1][0] = 1.0
    last = 0.0
    for p in pts:  # a position may never go backwards
        if p[0] is not None:
            p[0] = max(float(p[0]), last)
            last = float(p[0])
    i = 0
    while i < len(pts):
        if pts[i][0] is None:
            j = i
            while pts[j][0] is None:
                j += 1
            a, b = float(pts[i - 1][0]), float(pts[j][0])  # type: ignore[arg-type]
            n = j - i + 1
            for k in range(i, j):
                pts[k][0] = a + (b - a) * (k - i + 1) / n
            i = j
        i += 1
    return [[float(x), float(y)] for x, y in pts]  # type: ignore[arg-type]


def _points_curve(points: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
    xs = np.array([p[0] for p in points])
    ys = np.array([p[1] for p in points])
    u = np.linspace(0.0, 1.0, FIT_SAMPLES)
    return u, np.interp(u, xs, ys)


def ease_from_css(value: str, *, name: str, where: str) -> dict:
    """A named ease (the pack's shape) from one CSS easing value, with its lineage. Raises EasingError."""
    parsed = css_easing(value)
    label = f"CSS {name}: {parsed['css']}" if name else f"CSS {parsed['css']}"
    if parsed["kind"] == "linear":
        return {"kind": "linear", "lineage": f"{label} ({where})", "source": {"kind": "css", "where": where}}
    if parsed["kind"] == "bezier":
        return {"bezier": [round(v, 4) for v in parsed["bezier"]], "lineage": f"{label} ({where})",
                "source": {"kind": "css", "where": where}}
    if parsed["kind"] == "points":
        u, y = _points_curve(parsed["points"])
        bez, rms = fit_curve(u, y)
        return {"bezier": bez, "peak": round(curve_peak({"bezier": bez}), 4), "fit_rms": round(rms, 5),
                "points_peak": round(float(max(1.0, y.max())), 4),
                "lineage": f"{label} ({where}), fitted with a cubic-bezier (RMS {rms:.2%} of the travel)",
                "source": {"kind": "css", "where": where}}
    raise EasingError(f"{parsed['css']} is a stepped function, not a curve; a motion law has no stepped eases")


# ------------------------------------------------------------------------------------------------------- springs
@dataclass(frozen=True)
class Spring:
    """A damped spring from rest at 0 to rest at 1: natural frequency ω₀ (rad/s), damping ratio ζ, mass m."""

    omega0: float
    zeta: float
    mass: float = 1.0

    def __post_init__(self) -> None:
        if not (self.omega0 > 0 and math.isfinite(self.omega0)):
            raise EasingError("a spring needs a positive response (or stiffness)")
        if not (self.zeta > 0 and math.isfinite(self.zeta)):
            raise EasingError("a spring with no damping never settles, so it can't be an ease")
        if not (self.mass > 0 and math.isfinite(self.mass)):
            raise EasingError("a spring needs a positive mass")

    # constructors, one per way SwiftUI writes a spring
    @classmethod
    def from_response(cls, response: float, damping_fraction: float) -> "Spring":
        if response <= 0:
            raise EasingError("a spring's response must be above zero")
        return cls(2 * math.pi / response, damping_fraction)

    @classmethod
    def from_duration(cls, duration: float, bounce: float) -> "Spring":
        """SwiftUI's Spring(duration:bounce:): response = duration; ζ = 1 − bounce, or 1 / (1 + bounce) below zero."""
        if not -1.0 < bounce < 1.0:
            raise EasingError("a spring's bounce lies between -1 and 1 (1 never settles)")
        zeta = 1.0 - bounce if bounce >= 0 else 1.0 / (1.0 + bounce)
        return cls.from_response(duration, zeta)

    @classmethod
    def from_physics(cls, mass: float, stiffness: float, damping: float) -> "Spring":
        if stiffness <= 0 or mass <= 0:
            raise EasingError("a spring needs a positive mass and stiffness")
        omega0 = math.sqrt(stiffness / mass)
        return cls(omega0, damping / (2 * math.sqrt(stiffness * mass)), mass)

    @classmethod
    def from_settling(cls, settling: float, damping_ratio: float, epsilon: float = SETTLE) -> "Spring":
        """Spring(settlingDuration:dampingRatio:epsilon:): the ω₀ whose settle time is exactly `settling`."""
        unit = cls(1.0, damping_ratio)
        return cls(unit.settle_time(epsilon) / settling, damping_ratio)

    # what the spring is
    @property
    def response(self) -> float:
        return 2 * math.pi / self.omega0

    @property
    def stiffness(self) -> float:
        return self.mass * self.omega0 ** 2

    @property
    def damping(self) -> float:
        return 2 * self.zeta * self.omega0 * self.mass

    def position(self, t: Any) -> np.ndarray:
        """Where the spring is at time t (seconds): 0 at rest, travelling to 1."""
        t = np.asarray(t, dtype=float)
        w, z = self.omega0, self.zeta
        if abs(z - 1.0) < 1e-9:
            return 1.0 - np.exp(-w * t) * (1.0 + w * t)
        if z < 1.0:
            wd = w * math.sqrt(1.0 - z * z)
            return 1.0 - np.exp(-z * w * t) * (np.cos(wd * t) + (z * w / wd) * np.sin(wd * t))
        root = math.sqrt(z * z - 1.0)
        r1, r2 = -w * (z - root), -w * (z + root)
        return 1.0 + (r2 * np.exp(r1 * t) - r1 * np.exp(r2 * t)) / (r1 - r2)

    def settle_time(self, epsilon: float = SETTLE) -> float:
        """Seconds until the spring stays within `epsilon` of its travel for good."""
        w, z = self.omega0, self.zeta
        if z >= 1.0 - 1e-9:  # never crosses 1: the first time it is close enough
            hi = 1.0 / w
            while 1.0 - float(self.position(hi)) > epsilon:
                hi *= 2.0
            lo = 0.0
            for _ in range(80):
                mid = (lo + hi) / 2
                if 1.0 - float(self.position(mid)) > epsilon:
                    lo = mid
                else:
                    hi = mid
            return hi
        # rings: past the envelope's bound it can't leave the band; before it, the last sample outside the band
        bound = math.log(1.0 / (epsilon * math.sqrt(1.0 - z * z))) / (z * w)
        t = np.linspace(0.0, bound, 20001)
        outside = np.nonzero(np.abs(self.position(t) - 1.0) > epsilon)[0]
        if outside.size == 0:
            return float(t[1])
        i = int(outside[-1])
        lo, hi = float(t[i]), float(t[min(i + 1, t.size - 1)])
        for _ in range(60):
            mid = (lo + hi) / 2
            if abs(float(self.position(mid)) - 1.0) > epsilon:
                lo = mid
            else:
                hi = mid
        return hi

    def peak(self) -> float:
        """The furthest it travels (1.0 unless it overshoots)."""
        if self.zeta >= 1.0:
            return 1.0
        wd = self.omega0 * math.sqrt(1.0 - self.zeta ** 2)
        return float(self.position(math.pi / wd))  # the first crest is the highest

    def to_dict(self) -> dict:
        return {"response_s": round(self.response, 6), "damping_fraction": round(self.zeta, 6),
                "mass": round(self.mass, 6), "stiffness": round(self.stiffness, 6), "damping": round(self.damping, 6)}


def spring_ease(spring: Spring, *, label: str, where: str, assumed: dict | None = None,
                epsilon: float = SETTLE) -> dict:
    """A named ease from a spring: exact `spring0` when critically damped, else a fitted cubic-bezier."""
    T = spring.settle_time(epsilon)
    u = np.linspace(0.0, 1.0, FIT_SAMPLES)
    y = spring.position(u * T) / float(spring.position(T))
    peak = spring.peak()
    note = f"{label} ({where}): settles within {epsilon:.1%} of its travel in {T:.3f} s"
    if abs(spring.zeta - 1.0) < 1e-6:
        entry: dict = {"kind": "spring0", "stiffness": round(spring.omega0 * T, 6),
                       "lineage": note + "; critically damped, so it is exactly the kit's spring0 curve"}
        rms = 0.0
    else:
        bez, rms = fit_curve(u, y)
        shape = "rings past its target" if spring.zeta < 1 else "is overdamped"
        entry = {"bezier": bez, "lineage": note + f"; it {shape} (ζ {spring.zeta:.3f}), fitted with a cubic-bezier "
                                                   f"(RMS {rms:.2%} of the travel)"}
    drawn = curve_peak(entry)
    entry.update({"duration_s": round(T, 4), "peak": round(drawn, 4), "fit_rms": round(rms, 5),
                  "spring": {**spring.to_dict(), "peak": round(peak, 4)},
                  "source": {"kind": "swiftui", "where": where}})
    if assumed:
        entry["assumed"] = assumed
        entry["lineage"] += "; SwiftUI's defaults assumed for " + ", ".join(f"{k} {v:g}" for k, v in assumed.items()) \
            + " (confirm them against the SDK the app builds with)"
    return entry


# ------------------------------------------------------------------------------------------------------- SwiftUI
_CALL = re.compile(r"^\.?(?:Animation\.)?(?P<fn>[A-Za-z]+)\s*(?:\((?P<args>.*)\))?\s*$", re.DOTALL)
_SPRING_TYPE = re.compile(r"^Spring\s*\((?P<args>.*)\)\s*$", re.DOTALL)


def _args(text: str | None) -> tuple[dict[str, float], list[float]]:
    """SwiftUI arguments: {label: number} and the unlabelled numbers, in order."""
    named: dict[str, float] = {}
    plain: list[float] = []
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        if ":" in part:
            key, _, value = part.partition(":")
            try:
                named[key.strip()] = _float(value)
            except EasingError:
                continue  # a variable or an expression: reported as missing, not guessed
        else:
            try:
                plain.append(_float(part))
            except EasingError:
                continue
    return named, plain


def swift_animation(call: str, *, where: str) -> dict:
    """A named ease from one SwiftUI animation expression (`.spring(response: 0.4, dampingFraction: 0.8)`,
    `Animation.easeOut(duration: 0.3)`, `Spring(duration: 0.5, bounce: 0.2)`, `.snappy`, …). Raises EasingError."""
    text = " ".join(str(call).split())
    m = _SPRING_TYPE.match(text)
    if m:
        named, _ = _args(m.group("args"))
        fn = "Spring"
    else:
        m = _CALL.match(text)
        if not m:
            raise EasingError(f"{call!r} isn't a SwiftUI animation Slate can read")
        fn = m.group("fn")
        named, plain = _args(m.group("args"))
    label = f"SwiftUI {text}"
    if fn == "timingCurve":
        _, plain = _args(m.group("args"))
        if len(plain) < 4:
            raise EasingError(f"{text}: timingCurve needs its four control-point numbers written out")
        x1, y1, x2, y2 = plain[:4]
        if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
            raise EasingError(f"{text}: control points' x must lie within 0…1")
        out = {"bezier": [round(v, 4) for v in (x1, y1, x2, y2)], "lineage": f"{label} ({where})",
               "source": {"kind": "swiftui", "where": where}}
        if "duration" in named:
            out["duration_s"] = named["duration"]
        return out
    if fn in SWIFT_CURVES or fn == "linear":
        out = {"lineage": f"{label} ({where})", "source": {"kind": "swiftui", "where": where}}
        if fn == "linear":
            out["kind"] = "linear"
        else:
            out["bezier"] = list(SWIFT_CURVES[fn])
        if "duration" in named:
            out["duration_s"] = named["duration"]
        else:
            out["duration_s"] = SWIFT_DEFAULTS["curve"]["duration"]
            out["assumed"] = {"duration": SWIFT_DEFAULTS["curve"]["duration"]}
            out["lineage"] += "; SwiftUI's default duration assumed (confirm it against the SDK)"
        return out
    assumed: dict[str, float] = {}

    def pick(key: str, table: str) -> float:
        if key in named:
            return named[key]
        assumed[key] = SWIFT_DEFAULTS[table][key]
        return assumed[key]

    if fn == "Spring":
        if "stiffness" in named:
            spring = Spring.from_physics(named.get("mass", 1.0), named["stiffness"], named.get("damping", 0.0))
        elif "settlingDuration" in named:
            spring = Spring.from_settling(named["settlingDuration"], named.get("dampingRatio", 1.0),
                                          named.get("epsilon", SETTLE))
        elif "response" in named:
            spring = Spring.from_response(named["response"], named.get("dampingRatio", 1.0))
        else:
            spring = Spring.from_duration(named.get("duration", 0.5), named.get("bounce", 0.0))
            for k, d in (("duration", 0.5), ("bounce", 0.0)):
                if k not in named:
                    assumed[k] = d
        return spring_ease(spring, label=label, where=where, assumed=assumed or None)
    if fn in ("spring", "interactiveSpring"):
        if "duration" in named or "bounce" in named:
            spring = Spring.from_duration(named.get("duration", 0.5), named.get("bounce", 0.0))
        else:
            table = "interactiveSpring" if fn == "interactiveSpring" else "spring"
            spring = Spring.from_response(pick("response", table), pick("dampingFraction", table))
        return spring_ease(spring, label=label, where=where, assumed=assumed or None)
    if fn == "interpolatingSpring":
        if "duration" in named or "bounce" in named:
            spring = Spring.from_duration(named.get("duration", 0.5), named.get("bounce", 0.0))
        else:
            spring = Spring.from_physics(pick("mass", "interpolatingSpring"),
                                         pick("stiffness", "interpolatingSpring"),
                                         pick("damping", "interpolatingSpring"))
        return spring_ease(spring, label=label, where=where, assumed=assumed or None)
    if fn in ("smooth", "snappy", "bouncy"):
        duration = pick("duration", fn)
        bounce = SWIFT_DEFAULTS[fn]["bounce"] + named.get("extraBounce", 0.0)
        if fn != "smooth":
            assumed.setdefault("bounce", SWIFT_DEFAULTS[fn]["bounce"])
        return spring_ease(Spring.from_duration(duration, bounce), label=label, where=where, assumed=assumed or None)
    raise EasingError(f"{text}: .{fn} isn't a SwiftUI animation Slate reads")


def kit_spring0(stiffness: float = SPRING0_STIFFNESS) -> dict:
    """The kit's own zero-bounce spring, for a law that wants it by name."""
    return {"kind": "spring0", "stiffness": stiffness, "lineage": "the kit's zero-bounce spring"}
