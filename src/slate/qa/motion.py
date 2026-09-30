"""The motion gate: the motion law, checked on every act's motion trace and cross-checked on the rendered pixels.

`slate qa motion` (and `slate qa`, which runs every gate) reads the trace each engine writes per act,
`timing/motion/actN.json` (docs/ARCHITECTURE.md §12, "Engine data contracts"), and the brand's motion data through
`slate.brand.pack.motion`. Each rule is a sub-check of one `motion` gate, so the gate passes only when all of them do:

- trace       every act has a well-formed trace. An act without one can't pass: UNCHECKED, with the next step.
- eases       only the brand's named eases, and the samples really follow the curve they name. Overshoot only where
              the brand's overshoot policy allows it, up to its peak. Linear only for drifts that outlast their shot,
              things that show time (progress, a playhead), a constant spin, and the declared exemptions (a reading
              sweep; a line whose steadiness is the meaning). Besides the brand's eases, a rig part may use the rig's
              own rest-to-rest curves (sway, land) and a carry the even travel profile; any move may use one of those
              the brand declares (EXTRA_CURVES).
- durations   every move lasts one of the brand's duration tokens (exits at its exit factor), to within a frame.
              Rig parts, loops, allowed linear moves, reading sweeps and even-profile drifts longer than the
              longest token keep their own timing (the law: anything past the scale is a drift or time-based).
- staggers    a staggered group spreads no wider than the brand's caps, steps on whole frames, and staggers only on
              eases the brand lets stagger. A declared reading sweep is time-based, not a stagger.
- entrances   entrances start from at least 85% scale: nothing scales from zero.
- speed       peak speed and acceleration by what moves (SPEED_CLASSES), at 1080p: a rig part 30 px/frame and
              8 px/frame² (the plan's law), a UI element 60 and 16, a page, card or camera carry 170 and 45; a
              whip past the carry limits goes to a person. Scaled to the film's size.
- loops       no loop or wobble faster than a 16-frame period: a channel's reversals are at least 8 frames apart.
- symmetry    a mark rig's parts move as whole-frame-delayed copies of each other: in unison, in pairs, or as a wave.
- pixels      block matching on each act's render finds fast motion the trace doesn't explain (REVIEW), so a scene
              that skips its trace can't pass silently.

Where the numbers come from: the films' motion law (MOTION.md: named eases with lineage, the duration scale, stagger
caps, the banned list and its reasoned exceptions) and their character-motion spec (the speed and acceleration limits
and the 16-sample shutter behind them, the minimum period, the three phase modes and their acceptance tests). Brand
values live in the brand pack. Only the law that holds for every brand lives here.

Trace entries follow the contract: {id, shot, prop, ease, frame_in, frame_out, from, to, samples: [[frame, value]…],
entrance, loop_period, rig}. Optional keys are read when an engine writes them: `class` (rig, ui, carry or whip: the
speed limits it's held to), `exempt` (reading_sweep or steady, with an optional `reason`), `exception` (a reason,
which turns that entry's violations into REVIEW items a person accepts) and `size` (the element's size in px, so a
scale or rotation can be speed-checked at its edges). An entry whose ease is "measured" (an act sampled in After Effects,
slate.ae.acts.trace_from_samples, from a property's first key to its last) is split into its moves at their rests, and
each is judged by the brand ease its samples follow, by shape.

The limits scale with the frame's short side, which is its height on a landscape or square film: the law was set on
the 1920 × 1080 output, where 30 px/frame keeps the gap between the 16 shutter samples under a pixel for a rig's fine
parts. A 1080 × 1920 reel is a 1080p frame too and keeps those numbers; scaling by its height would allow 53 px/frame
for a rig and bring the echoes back.

Whole-picture work (the pixel scan) is cached in qa/motion/ against the render's hash, so it runs once per render;
`slate qa --quick` reuses a cached scan and never starts one. Every render read is one of the gate's inputs, so a new
render turns the result STALE. A run narrowed to some acts, one render or some checks reports what it finds, but an
UNCHECKED scope sub-check keeps it from being recorded as the gate's PASS.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import dataclasses
import hashlib
import importlib
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from slate import __version__, gates, jsonio, log
from slate.errors import MissingPrerequisite, SlateError, UsageError
from slate.gates import GateResult

GATE = "motion"
TRACE_DIR = "timing/motion"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")

# ---------------------------------------------------------------------------------------------------- the law
#: The rules that hold for every brand (docs/PLAN.md, "Quality"). Speeds are per frame, as fractions of the frame's
#: short side. The law states them at 1080p, where 30 px/frame is a 36th of the frame and 8 px/frame² a 135th: the
#: renderer spreads 16 samples over a 180° shutter, so 30 px/frame keeps the gap between samples under a pixel, and
#: 8 px/frame² keeps a blur's length from jumping more than 4 px between frames. Scaling by the short side keeps that
#: true at any size: a 2160-line film allows 60 and 16, a 720-line one 20 and 5.3, and a 9:16 reel whose short side
#: is the same as 1080p's keeps 30 and 8 (by its long side it would get 53, and the blur would show echoes).
LAW: dict[str, Any] = {
    "speed_per_short_side": 1 / 36,  # 30 px/frame at 1080p
    "accel_per_short_side": 1 / 135,  # 8 px/frame² at 1080p
    # The one exception: up to 48 px/frame at the crest of a flight longer than 570 px (at 1080p), and only when the
    # beat can't afford a slower flight. That's a person's call, so it comes back as REVIEW, never PASS.
    "crest_speed_per_short_side": 1 / 22.5,
    "crest_travel_per_short_side": 0.5278,
    "crest_window": (0.2, 0.8),  # "at the crest": the fastest frame sits in the middle of the flight, not at an end
    "entrance_min_scale": 0.85,  # nothing scales from zero: entrances start from at least 85% of their size
    "loop_min_period_frames": 16,  # a full swing takes at least 16 frames …
    "loop_max_hz": 1.9,  # … and never goes faster than 1.9 Hz, so at 60 fps the floor is 32 frames, not 16
    "ease_tolerance": 0.015,  # samples may stray 1.5% of their travel from the named curve (engines round)
    "overshoot_tolerance": 0.003,
    "duration_tolerance_frames": 1.0,  # tokens are seconds; frames round them
    "symmetry_tolerance": 0.005,  # rig parts match to 0.5% of the size of their motion
    "whole_frame_tolerance": 0.01,
    "stagger_gap_s": 0.25,  # starts further apart than this are separate beats, not one staggered group
    "reversal_threshold": 0.02,  # a reversal must swing back at least 2% of the channel's range (noise isn't a wobble)
}

#: Speed limits by what moves, as fractions of the frame's short side (per frame, and per frame² for acceleration).
#: The plan's 30 px/frame and 8 px/frame² came from the character spec, where a rig's fine parts must keep the gap
#: between the 16 shutter samples under a pixel; one ceiling for everything failed every carry the films approved.
#: At 1080p:
#:
#: - rig    30 px/frame, 8 px/frame²: a character or mark rig's parts (the plan's law; the crest exception is rig-only).
#: - ui     60 px/frame, 16 px/frame²: a UI element moving within the frame (type, a chip, a card settling). Twice the
#:          rig's: a sample gap under 2 px, with the blur's length changing by at most 8 px a frame, so a card that
#:          leaves rest at full speed still fails. Slate's default, not a number the films measured.
#: - carry  170 px/frame, 45 px/frame²: a page, a card or the camera carried across the frame. The films' page and
#:          camera carries ran clean at 136 to 169 px/frame (speed changing by up to 25 px/frame² on the way), and a
#:          carry peaking at 310 streaked in the 16-sample render and was re-timed. The acceleration keeps the rig's
#:          ratio to speed (8 × 170 / 30).
#: - whip   no ceiling of its own: a deliberate whip past the carry limits is a person's call (REVIEW). The films' one
#:          fast pull-back (267 px/frame) read as a pop and was cut.
#:
#: A trace entry says which it is with `class` (aliases: character and mark are rig; element is ui; page and camera
#: are carry). Without one: a rig part is rig, an element whose id or prop starts with camera (or cam) is carry, and
#: anything else is ui, so a page or card carried across the frame says `"class": "carry"`. A brand may set its own
#: limits in its motion data, `speed_limits: {class: {speed, accel}}`, as fractions of the short side or as px with
#: the `short_side` they were measured on.
SPEED_CLASSES: dict[str, dict[str, Any]] = {
    "rig": {"speed": 1 / 36, "accel": 1 / 135, "what": "a character or mark rig's parts",
            "source": "the plan's law (16 samples over a 180° shutter keep the sample gap under a pixel)"},
    "ui": {"speed": 1 / 18, "accel": 2 / 135, "what": "a UI element moving within the frame",
           "source": "Slate's default: twice the rig's (a sample gap under 2 px)"},
    "carry": {"speed": 17 / 108, "accel": 17 / 405, "what": "a page, card or camera carried across the frame",
              "source": "the films' carries: clean at 136 to 169 px/frame at 1080p, streaking at 310"},
    "whip": {"speed": None, "accel": None, "what": "a deliberate whip",
             "source": "a person judges any whip past the carry limits"},
}
CLASS_ALIASES = {"rig": "rig", "character": "rig", "mark": "rig", "ui": "ui", "element": "ui", "carry": "carry",
                 "page": "carry", "camera": "carry", "whip": "whip"}
_CAMERA_WORDS = {"camera", "cam"}

#: Exemptions a trace entry may declare (`exempt`, with an optional `reason`), each a lesson from the films:
#: - reading_sweep  a line resolving items at a constant speed: time-based, so it may run linear, is off the
#:                  duration scale, and a group of them isn't held to the stagger caps;
#: - steady         linear motion whose steady speed is the meaning (a durable line drawing at a constant rate while
#:                  a competitor races and breaks).
EXEMPTIONS = {"reading_sweep": "a reading sweep (declared)", "steady": "steadiness is the meaning (declared)"}

#: Curves the gate knows besides the brand's named eases, generic to every brand (the kit's own curves):
#: - sway     (1 − cos πu) / 2, the kit's `sine`: a rig's rest-to-rest curve for breaths, spreads, leans and flights;
#: - land     cubic-bezier(0.25, 0, 0.25, 1), the shape of a zero-bounce spring: a rig's quick stroke that settles;
#: - spring0  the kit's zero-bounce spring, the same job as land;
#: - even     the even travel profile: rest to rest through a third of the travel at each third of the time (a
#:            monotone spline), so its peak speed is 1.33 times its average where a glide's is about 2.9: for long
#:            carries of type, which strobe at a glide's peak, and for camera drifts.
#: A rig part may use sway, land or spring0 and a carry may use even without saying so; any other move may use one
#: when the brand declares it in its motion data (`extra_curves`). A brand ease of the same name always wins.
EXTRA_CURVES: dict[str, dict[str, Any]] = {
    "sway": {"spec": "sine", "for": "rig", "lineage": "(1 − cos πu) / 2; the kit's sine"},
    "land": {"spec": [0.25, 0, 0.25, 1], "for": "rig", "lineage": "cubic-bezier(0.25, 0, 0.25, 1), a zero-bounce "
                                                                  "spring's shape"},
    "spring0": {"spec": "spring0", "for": "rig", "lineage": "the kit's zero-bounce spring"},
    "even": {"spline": [[0.0, 0.0], [1 / 3, 1 / 3], [2 / 3, 2 / 3], [1.0, 1.0]], "for": "carry",
             "lineage": "the even travel profile (peak speed 1.33 × average)"},
}
EXTRA_ALIASES = {"sine": "sway", "sineinout": "sway", "spring": "spring0", "eventravel": "even", "evenmove": "even"}

# Pixel cross-check: frames are compared at a small size (a short side of 48 px, so one analysis pixel is 22.5 px at
# 1080p) in 4 px blocks, searching 5 times the speed limit each way (10–20 ms a frame pair). A block is "fast" when it
# moves 1.25 times the limit, so measurement error can't flag a legal move. Its best match must beat any match more
# than a pixel away by 30% ("ambiguity"), standing still wins a tie, a match on the search's edge isn't trusted, a
# fast block needs a neighbour moving the same way, and a move must be fast for two frame pairs running. On the
# first film's delivered act 4, a page carry the notes put at 136 px/frame measures 132 (tests: the golden test).
PIXEL = {"short_side": 48, "fast_factor": 1.25, "search_factor": 5.0, "min_blocks": 2, "under_traced": 0.4,
         "texture": 0.004, "ambiguity": 0.7}


def limits(width: int, height: int) -> dict[str, float]:
    """The rig's speed and acceleration limits in px for a film of this size, with its crest exception (see LAW).
    Other kinds of element have their own limits: class_limits()."""
    short = float(min(int(width), int(height)))
    return {
        "short_side": short,
        "speed": short * LAW["speed_per_short_side"],
        "accel": short * LAW["accel_per_short_side"],
        "crest_speed": short * LAW["crest_speed_per_short_side"],
        "crest_travel": short * LAW["crest_travel_per_short_side"],
    }


def _per_short_side(v: Any, measured_on: float | None) -> float | None:
    """A brand's limit as a fraction of the short side: a fraction as given (under 1), or px on the frame whose short
    side the brand names."""
    n = _num(v)
    if n is None or n <= 0:
        return None
    if n < 1.0:
        return n
    return n / measured_on if measured_on else None


def brand_speed_limits(raw: Any) -> tuple[dict[str, dict[str, float]], list[str]]:
    """A brand's own speed limits ({class: {speed, accel}} as fractions of the short side), and what couldn't be read.
    Values are fractions of the short side, or px with the `short_side` they were measured on."""
    out: dict[str, dict[str, float]] = {}
    problems: list[str] = []
    if not isinstance(raw, dict):
        return out, problems
    for name, spec in raw.items():
        cls = CLASS_ALIASES.get(_key(name))
        if cls is None or cls == "whip" or not isinstance(spec, dict):
            problems.append(f"speed_limits.{name} isn't one of the classes with limits (rig, ui, carry)")
            continue
        on = _num(spec.get("short_side"))
        got = {}
        for k in ("speed", "accel"):
            if spec.get(k) is None:
                continue
            f = _per_short_side(spec[k], on)
            if f is None:
                problems.append(f"speed_limits.{name}.{k} needs a positive fraction of the short side, or px with "
                                "the short_side they were measured on")
            else:
                got[k] = f
        if got:
            out[cls] = got
    return out, problems


def class_limits(width: int, height: int, law: "Law | None" = None) -> dict[str, dict[str, Any]]:
    """Each element class's speed and acceleration limits in px for a film of this size ({class: {speed, accel,
    source}}), with the brand's own where its motion data sets them (see SPEED_CLASSES)."""
    short = float(min(int(width), int(height)))
    own = law.speed_limits if law is not None else {}
    out: dict[str, dict[str, Any]] = {}
    for cls, spec in SPEED_CLASSES.items():
        mine = own.get(cls) or {}
        row: dict[str, Any] = {"what": spec["what"], "source": spec["source"]}
        for k in ("speed", "accel"):
            f = mine.get(k, spec[k])
            row[k] = None if f is None else short * float(f)
        if mine:
            row["source"] = f"the brand's speed_limits.{cls} ({law.source})" if law is not None else row["source"]
        out[cls] = row
    return out


def element_class(m: "Move") -> tuple[str, str]:
    """(class, why) for a move: its declared class, else rig for a rig part, else carry for the camera, a page or a
    scroll, else ui."""
    if m.cls:
        return m.cls, "declared"
    if m.rig:
        return "rig", "a rig part"
    firsts = {(re.split(r"[^a-z0-9]+", str(v).lower().strip()) or [""])[0] for v in (m.id, m.prop)}
    if firsts & _CAMERA_WORDS:
        return "carry", "the camera, by its name"
    return "ui", "no class declared"


def min_loop_period(fps: float, brand_frames: float | None = None) -> float:
    """The shortest loop period allowed, in frames: 16 frames, and never faster than 1.9 Hz (a brand may ask for
    slower loops, never faster ones)."""
    floor = max(float(LAW["loop_min_period_frames"]), float(fps) / float(LAW["loop_max_hz"]))
    return max(floor, float(brand_frames or 0.0))


# ---------------------------------------------------------------------------------------------------- helpers
def _np():
    import numpy  # noqa: PLC0415 - keeps `slate --help` fast; every check that needs it imports it here

    return numpy


def _optional(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _vec(v: Any):
    """A value as a 1-D float array (a number is a 1-vector), or None when it isn't numeric."""
    n = _num(v)
    if n is not None:
        return _np().array([n])
    if isinstance(v, (list, tuple)) and v and all(_num(x) is not None for x in v):
        return _np().array([float(x) for x in v])
    return None


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _verb(items, one: str, many: str) -> str:
    """The verb that agrees with a count: "1 move breaks", "2 moves break"."""
    return one if len(items) == 1 else many


def _fmt(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{x:.0f}"


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ---------------------------------------------------------------------------------------------------- props
_AXIS_X = {"x", "tx", "translatex", "left", "cx", "offsetx", "posx", "positionx", "centerx", "centrex", "dx"}
_AXIS_Y = {"y", "ty", "translatey", "top", "cy", "offsety", "posy", "positiony", "centery", "centrey", "dy"}
_VECTOR = {"position", "pos", "translate", "xy", "offset", "center", "centre", "point", "location", "anchor"}
_DEPTH = {"z", "tz", "translatez", "depth", "positionz"}
_TIME_PROPS = {"progress", "playhead", "scrub", "time", "elapsed", "loader", "timeline"}


def prop_kind(prop: Any, unit: Any = None) -> str:
    """What a traced property moves: x, y, vector (a position), px (a length on an unknown axis), scale, rotation,
    opacity, depth or other. Names are matched loosely: "translateX", "position.x" and "camera.x" are all x."""
    text = str(prop or "")
    full = _key(text)
    last = _key(re.split(r"[.\-/:]", text)[-1]) if text else ""
    if "scale" in full or "zoom" in full:
        return "scale"
    if "rotat" in full or "angle" in full or "spin" in full or last in ("turn", "theta", "roll"):
        return "rotation"
    if "opacity" in full or "alpha" in full or last == "fade":
        return "opacity"
    if last in _AXIS_X or full in _AXIS_X:
        return "x"
    if last in _AXIS_Y or full in _AXIS_Y:
        return "y"
    if last in _DEPTH or full in _DEPTH:
        return "depth"
    if last in _VECTOR or full in _VECTOR:
        return "vector"
    if str(unit or "").lower() == "px":
        return "px"
    return "other"


POSITIONAL = ("x", "y", "vector", "px")


# ---------------------------------------------------------------------------------------------------- eases
@dataclasses.dataclass
class Ease:
    """One of the brand's named eases, as the kit reads it (slate.motion.eases.parse)."""

    name: str
    kind: str = "unknown"  # bezier, linear, sine or spring0 (the kit's kinds)
    points: tuple[float, float, float, float] | None = None
    role: str | None = None  # entrance, exit, move or state: from the brand's `roles`, else its own words
    lineage: str | None = None
    peak: float | None = None  # how far past its target it may go (1.046 = 4.6%); None: the policy decides
    fn: Callable | None = None  # progress for u in [0, 1], vectorised
    fn_source: str | None = None


def _vectorised(scalar: Callable[[float], float]) -> Callable:
    np = _np()

    def ease(u):
        arr = np.atleast_1d(np.asarray(u, dtype=float))
        out = np.array([float(scalar(float(x))) for x in arr])
        return out if np.ndim(u) else out[0]

    return ease


def _role(text: Any) -> str | None:
    """A role from the brand's own words ("everything that enters", "exits", "state changes", "camera moves")."""
    t = str(text or "").lower()
    if not t:
        return None
    if re.search(r"\bexit|leav|depart", t):
        return "exit"
    if re.search(r"enter|entrance|arriv|reveal|appear", t):
        return "entrance"
    if re.search(r"state|toggle|flip|commit|snap", t):
        return "state"
    if re.search(r"move|camera|glide|carry|rest-to-rest|travel|layout", t):
        return "move"
    return None


def _peak_value(v: Any) -> float | None:
    """An overshoot allowance as a peak: 1.046, or 0.046 (the overshoot itself), or {"peak": 1.046}."""
    if isinstance(v, dict):
        for k in ("peak", "max_peak", "max", "overshoot"):
            if k in v:
                return _peak_value(v[k])
        return None
    n = _num(v)
    if n is None:
        return None
    return n if n >= 1.0 else 1.0 + n


# ---------------------------------------------------------------------------------------------------- brand law
Span = tuple[str, float]  # ("s", seconds) or ("f", frames)


def _span(v: Any) -> Span | None:
    """A length as ("s", seconds) or ("f", frames), from {seconds|s|ms|frames: n} or a plain number of seconds."""
    if isinstance(v, dict):
        for k, unit in (("seconds", "s"), ("s", "s"), ("ms", "ms"), ("frames", "f"), ("f", "f")):
            n = _num(v.get(k))
            if n is not None:
                return ("s", n / 1000.0) if unit == "ms" else (unit, n)
        return None
    n = _num(v)
    return None if n is None else ("s", n)


def span_frames(span: Span, fps: float) -> float:
    return span[1] if span[0] == "f" else span[1] * float(fps)


@dataclasses.dataclass
class Law:
    """A brand's motion law in the shape the checks use. The eases, duration scale, stagger units and caps, exit
    factor and roles are read by slate.motion.eases.law_from, exactly as the kit reads them to render; the rest
    (overshoot policy, lineage, per-ease peaks, rigs, loops, most-items) is read here from the same `motion` data."""

    eases: dict[str, Ease] = dataclasses.field(default_factory=dict)
    roles: dict[str, str] = dataclasses.field(default_factory=dict)  # role -> ease name
    durations: dict[str, Span] = dataclasses.field(default_factory=dict)
    max_per_act: dict[str, int] = dataclasses.field(default_factory=dict)
    exit_factor: float | None = None
    stagger_units: list[Span] = dataclasses.field(default_factory=list)
    stagger_cap: Span | None = None
    stagger_cap_fraction: float | None = None
    stagger_caps_from: str = "brand"
    stagger_max_items: int | None = None
    stagger_eases: set[str] | None = None
    overshoot: dict[str, float] = dataclasses.field(default_factory=dict)
    overshoot_default: float = 1.0
    overshoot_stated: bool = False
    time_props: set[str] = dataclasses.field(default_factory=set)
    rigs: dict[str, dict] = dataclasses.field(default_factory=dict)
    min_loop_period: Span | None = None
    speed_limits: dict[str, dict[str, float]] = dataclasses.field(default_factory=dict)  # the brand's own, per class
    extra: dict[str, Ease] = dataclasses.field(default_factory=dict)  # EXTRA_CURVES, evaluated the kit's way
    extra_declared: set[str] = dataclasses.field(default_factory=set)  # extra curves the brand says any move may use
    neutral: bool = False  # the brand has no law of its own: the kit's neutral law stood in
    source: str = ""
    notes: list[str] = dataclasses.field(default_factory=list)

    def ease(self, name: str | None) -> Ease | None:
        """A named ease, or the ease a role names ("entrance" → the brand's entrance ease)."""
        if name is None:
            return None
        if name in self.eases:
            return self.eases[name]
        if name in self.roles and self.roles[name] in self.eases:
            return self.eases[self.roles[name]]
        folded = {_key(k): v for k, v in self.eases.items()}
        return folded.get(_key(name))

    def extra_curve(self, name: str | None) -> Ease | None:
        """One of the generic curves the gate knows besides the brand's eases (EXTRA_CURVES), by name or alias."""
        if name is None:
            return None
        k = _key(name)
        return self.extra.get(EXTRA_ALIASES.get(k, k))

    def curve(self, name: str | None) -> Ease | None:
        """The curve a move names: the brand's ease (which always wins), else a generic extra curve."""
        return self.ease(name) or self.extra_curve(name)

    def allowed_peak(self, name: str | None) -> float:
        e = self.ease(name)
        if e is not None and e.peak is not None:
            return e.peak
        if e is not None:
            for k, v in self.overshoot.items():
                if _key(k) == _key(e.name) or (k in self.roles and self.roles[k] == e.name):
                    return v
        return self.overshoot_default


def _first(d: Any, *keys: str) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def law_from(motion: dict, *, source: str = "", fps: float | None = None) -> Law | None:
    """Read a brand's motion data into a Law, through slate.motion.eases (the kit's own reading). None when that
    module isn't installed, because a second reading of the law could disagree with what the kit rendered."""
    kit = _optional("slate.motion.eases")
    if kit is None or not hasattr(kit, "law_from") or not hasattr(kit, "evaluate"):
        return None
    ml = kit.law_from(motion if isinstance(motion, dict) else None, fps)
    motion = motion if isinstance(motion, dict) else {}
    law = Law(source=source, neutral=bool(getattr(ml, "neutral", False)))
    law.notes.extend(str(p) for p in getattr(ml, "problems", []) or [])
    law.roles = {str(k): str(v) for k, v in (getattr(ml, "roles", {}) or {}).items()}
    role_of = {v: k for k, v in law.roles.items()}
    raw_eases = motion.get("eases") if isinstance(motion.get("eases"), dict) else {}
    for name, spec in (ml.eases or {}).items():
        raw = raw_eases.get(name) if isinstance(raw_eases, dict) else None
        rawd = raw if isinstance(raw, dict) else {}
        e = Ease(str(name), kind=str(spec.kind), points=tuple(spec.bezier) if spec.bezier else None)
        e.role = _role(role_of.get(name)) or _role(rawd.get("role")) or _role(rawd.get("job") or rawd.get("use")) \
            or _role(name)
        lineage = _first(rawd, "lineage", "source", "from")
        e.lineage = str(lineage) if lineage else None
        e.peak = _peak_value(_first(rawd, "peak", "max_peak", "overshoot"))
        e.fn = _vectorised(lambda u, spec=spec: kit.evaluate(spec, u))
        e.fn_source = "slate.motion.eases.evaluate"
        law.eases[e.name] = e
    law.durations = {str(k): ("s", float(v)) for k, v in (ml.durations or {}).items()}
    raw_dur = motion.get("durations")
    for name, spec in (raw_dur.items() if isinstance(raw_dur, dict) else
                       ((d.get("name"), d) for d in raw_dur or [] if isinstance(d, dict))):
        cap = _num(_first(spec, "max_per_act", "per_act")) if isinstance(spec, dict) else None
        if name is not None and cap is not None:
            law.max_per_act[str(name)] = int(cap)
    law.exit_factor = _num(getattr(ml, "exit_factor", None))
    law.stagger_units = [("s", float(v)) for v in (ml.staggers or {}).values() if _num(v)]
    law.stagger_cap = ("s", float(ml.stagger_cap_seconds)) if _num(ml.stagger_cap_seconds) else None
    law.stagger_cap_fraction = _num(ml.stagger_cap_fraction)
    cap_raw = motion.get("stagger_cap") if isinstance(motion.get("stagger_cap"), dict) else {}
    law.stagger_caps_from = "brand" if cap_raw else "the kit's default caps (the brand sets none)"
    items = _first(cap_raw, "max_items", "group_over") or _first(motion, "stagger_max_items", "stagger_group_over")
    law.stagger_max_items = int(items) if _num(items) is not None else None
    # Only entrances, exits and state changes stagger; moves travel as one piece (the law's stagger rules).
    staggering = {law.roles[r] for r in ("entrance", "exit", "state") if r in law.roles}
    law.stagger_eases = staggering or None
    ov = _first(motion, "overshoot", "overshoot_policy")
    if ov is not None:
        law.overshoot_stated = True
        if isinstance(ov, bool):
            law.overshoot_default = math.inf if ov else 1.0
        elif isinstance(ov, str):
            if ov.strip().lower() in ("any", "allowed", "yes"):
                law.overshoot_default = math.inf
        elif isinstance(ov, dict):
            peak = _peak_value(_first(ov, "max_peak", "peak", "max"))
            allowed = ov.get("allowed", ov.get("eases", ov.get("only")))
            if isinstance(allowed, bool):
                law.overshoot_default = (peak if peak and peak > 1.0 else math.inf) if allowed else 1.0
            elif isinstance(allowed, (list, tuple)):
                for a in allowed:
                    if isinstance(a, dict) and (a.get("ease") or a.get("name")):
                        law.overshoot[str(a.get("ease") or a.get("name"))] = _peak_value(a) or peak or math.inf
                    elif isinstance(a, str):
                        law.overshoot[a] = peak if peak and peak > 1.0 else math.inf
            elif isinstance(allowed, dict):
                for k, v in allowed.items():
                    law.overshoot[str(k)] = _peak_value(v) or peak or math.inf
    lin = _first(motion, "linear_props", "time_props")
    if isinstance(lin, (list, tuple)):
        law.time_props = {_key(x) for x in lin if isinstance(x, str)}
    rigs = _first(motion, "rigs", "rig")
    if rigs is None:
        rigs = _first(_first(motion, "signature", "motif") or {}, "rigs", "rig")
    if isinstance(rigs, dict):
        law.rigs = {str(k): v for k, v in rigs.items() if isinstance(v, dict)}
    loops = _first(motion, "loops", "loop")
    if isinstance(loops, dict):
        if _num(loops.get("min_period_frames")) is not None:
            law.min_loop_period = ("f", float(loops["min_period_frames"]))
        elif _num(_first(loops, "min_period_s", "min_period_seconds")) is not None:
            law.min_loop_period = ("s", float(_first(loops, "min_period_s", "min_period_seconds")))
        elif loops.get("min_period") is not None:
            law.min_loop_period = _span(loops["min_period"])
    law.speed_limits, problems = brand_speed_limits(motion.get("speed_limits"))
    law.notes.extend(problems)
    law.extra = extra_curves(kit)
    declared = motion.get("extra_curves")
    names = list(declared) if isinstance(declared, (list, tuple, dict)) else []
    for n in names:
        k = _key(n)
        k = EXTRA_ALIASES.get(k, k)
        if k in law.extra:
            law.extra_declared.add(k)
        else:
            law.notes.append(f"extra_curves names {n!r}, which isn't a curve the gate knows ({', '.join(law.extra)})")
    return law


def extra_curves(kit=None) -> dict[str, Ease]:
    """EXTRA_CURVES as Eases, evaluated with the kit's own maths (slate.motion.eases), so the gate reads each curve
    exactly as the kit draws it."""
    kit = kit or _optional("slate.motion.eases")
    if kit is None:
        return {}
    out: dict[str, Ease] = {}
    for name, spec in EXTRA_CURVES.items():
        if "spline" in spec and hasattr(kit, "mono_spline"):
            keys = [list(k) for k in spec["spline"]]
            fn = _vectorised(lambda u, keys=keys: kit.mono_spline(min(max(u, 0.0), 1.0), keys))
            e = Ease(name, kind="spline", role=spec["for"], lineage=spec["lineage"], fn=fn,
                     fn_source="slate.motion.eases.mono_spline")
        elif "spec" in spec and hasattr(kit, "parse"):
            parsed = kit.parse(spec["spec"])
            if parsed is None:
                continue
            e = Ease(name, kind=str(parsed.kind), points=tuple(parsed.bezier) if parsed.bezier else None,
                     role=spec["for"], lineage=spec["lineage"],
                     fn=_vectorised(lambda u, parsed=parsed: kit.evaluate(parsed, u)),
                     fn_source="slate.motion.eases.evaluate")
        else:
            continue
        e.peak = 1.0  # none of them overshoots
        out[name] = e
    return out


def brand_motion(brand: str | None) -> tuple[dict | None, str]:
    """(motion data, where it came from) for a brand, through slate.brand.pack when it's installed, else brand.json.
    (None, reason) when there's no brand or no motion data."""
    from slate.qa import brandinfo  # noqa: PLC0415

    pack, source = brandinfo.load(brand)
    if pack is None:
        return None, source
    mod = _optional("slate.brand.pack")
    if mod is not None and hasattr(mod, "motion"):
        try:
            data = mod.motion(pack)
            if isinstance(data, dict) and data:
                return data, "slate.brand.pack.motion"
            return None, f"the {brand!r} brand pack has no motion data yet (brand onboarding writes it)"
        except SlateError as exc:
            return None, f"slate.brand.pack.motion couldn't read {brand!r}: {exc}"
        except Exception as exc:  # noqa: BLE001 - a broken brand module mustn't take the gate down
            return None, f"slate.brand.pack.motion failed on {brand!r}: {type(exc).__name__}: {exc}"
    data = pack.get("motion")
    if isinstance(data, str):
        folder = brandinfo.find(brand) if brand else None
        path = (folder / data) if folder else Path(data)
        try:
            data = jsonio.read(path)
            source = str(path)
        except SlateError as exc:
            return None, f"the brand's motion file couldn't be read: {exc}"
    if isinstance(data, dict) and data:
        return data, f"{source} (motion)"
    return None, f"the {brand!r} brand pack has no motion data yet (brand onboarding writes it)"


# ---------------------------------------------------------------------------------------------------- traces
@dataclasses.dataclass
class Move:
    """One trace entry: a property of one element moving from `start` to `end` between two film frames."""

    act: int
    index: int
    id: str
    shot: str | None
    prop: str
    kind: str
    ease: str | None
    frame_in: float
    frame_out: float
    start: Any  # numpy vector, or None when `from` isn't numeric
    end: Any
    frames: Any  # sample frames (film frames), sorted
    values: Any  # (N, D) sample values
    entrance: bool = False
    loop_period: float | None = None
    rig: str | None = None
    exception: str | None = None
    size: tuple[float, float] | None = None
    file: str | None = None
    cls: str | None = None  # the element class it declares (rig, ui, carry, whip), for the speed limits
    exempt: str | None = None  # a declared exemption (EXEMPTIONS)
    exempt_reason: str | None = None
    measured: bool = False  # sampled from a render with no ease named (an AE act measured in AE): fitted by shape
    fitted: str | None = None  # the brand ease a measured move follows, when one does
    fit_best: str | None = None  # the closest brand ease, and how far off it is (a fraction of the travel)
    fit_dev: float | None = None

    @property
    def ease_used(self) -> str | None:
        """The ease the rules judge: the named one, or for a measured move the brand ease its samples follow."""
        return self.fitted if self.measured else self.ease

    @property
    def duration(self) -> float:
        return self.frame_out - self.frame_in

    @property
    def label(self) -> str:
        return f"act {self.act} {self.id}.{self.prop} (frames {_fmt(self.frame_in, 2)}–{_fmt(self.frame_out, 2)})"

    @property
    def ref(self) -> str:
        return f"act{self.act}:{self.id}.{self.prop}@{_fmt(self.frame_in, 2)}"

    @property
    def changes(self) -> bool:
        np = _np()
        if self.values is not None and len(self.values) > 1 and float(np.ptp(self.values, axis=0).max()) > 1e-9:
            return True
        if self.start is not None and self.end is not None and self.start.shape == self.end.shape:
            return bool(np.abs(self.end - self.start).max() > 1e-9)
        return False


def _entry_problem(e: Any) -> str | None:
    if not isinstance(e, dict):
        return "isn't an object"
    missing = [k for k in ("id", "prop", "frame_in", "frame_out", "samples") if k not in e]
    if missing:
        return f"is missing {', '.join(missing)}"
    if _num(e["frame_in"]) is None or _num(e["frame_out"]) is None:
        return "has frame_in / frame_out that aren't numbers"
    if float(e["frame_out"]) < float(e["frame_in"]):
        return f"ends (frame {e['frame_out']}) before it starts (frame {e['frame_in']})"
    samples = e["samples"]
    if not isinstance(samples, list):
        return "has samples that aren't a list of [frame, value]"
    dims = set()
    for s in samples:
        if not (isinstance(s, (list, tuple)) and len(s) == 2 and _num(s[0]) is not None):
            return f"has a sample that isn't [frame, value]: {str(s)[:60]}"
        v = _vec(s[1])
        if v is None:
            return f"has a sample value that isn't a number or a list of numbers: {str(s[1])[:60]}"
        dims.add(len(v))
    if len(dims) > 1:
        return "mixes sample values of different lengths"
    if e.get("loop_period") is not None and (_num(e["loop_period"]) is None or float(e["loop_period"]) <= 0):
        return f"has a loop_period that isn't a positive number of frames: {e['loop_period']!r}"
    if "entrance" in e and not isinstance(e["entrance"], bool):
        return "has an entrance flag that isn't true or false"
    if e.get("rig") is not None and not isinstance(e["rig"], str):
        return "has a rig that isn't a name"
    cls = e.get("class")
    if cls is not None and CLASS_ALIASES.get(_key(cls)) is None:
        return (f"declares class {cls!r}, which isn't one of the element classes "
                f"({', '.join(SPEED_CLASSES)}; also character, mark, element, page, camera)")
    ex = e.get("exempt")
    if ex is not None and _key(ex) not in {_key(k) for k in EXEMPTIONS}:
        return f"declares exempt {ex!r}, which isn't one of the law's exemptions ({', '.join(EXEMPTIONS)})"
    return None


def _size(v: Any) -> tuple[float, float] | None:
    n = _num(v)
    if n is not None and n > 0:
        return (n, n)
    vec = _vec(v)
    if vec is not None and len(vec) >= 2 and vec[0] > 0 and vec[1] > 0:
        return (float(vec[0]), float(vec[1]))
    return None


def move_from(e: dict, act: int, index: int, offset: float = 0.0, file: str | None = None) -> Move:
    """A validated trace entry as a Move, its frames shifted by `offset` into film frames."""
    np = _np()
    pairs = sorted(((float(s[0]) + offset, _vec(s[1])) for s in e["samples"]), key=lambda p: p[0])
    frames = np.array([p[0] for p in pairs], dtype=float)
    values = np.array([p[1] for p in pairs], dtype=float) if pairs else np.zeros((0, 1))
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    start, end = _vec(e.get("from")), _vec(e.get("to"))
    if start is None and len(values):
        start = values[0].copy()
    if end is None and len(values):
        end = values[-1].copy()
    lp = _num(e.get("loop_period"))
    cls = CLASS_ALIASES.get(_key(e["class"])) if e.get("class") is not None else None
    ex = e.get("exempt")
    exempt = next((k for k in EXEMPTIONS if _key(k) == _key(ex)), None) if ex is not None else None
    reason = e.get("reason") if isinstance(e.get("reason"), str) and e["reason"].strip() else None
    return Move(act=int(act), index=index, id=str(e["id"]), shot=None if e.get("shot") is None else str(e["shot"]),
                prop=str(e["prop"]), kind=prop_kind(e["prop"], e.get("unit")),
                ease=None if e.get("ease") in (None, "") else str(e["ease"]),
                frame_in=float(e["frame_in"]) + offset, frame_out=float(e["frame_out"]) + offset,
                start=start, end=end, frames=frames, values=values, entrance=bool(e.get("entrance", False)),
                loop_period=lp, rig=e.get("rig") if isinstance(e.get("rig"), str) else None,
                exception=str(e["exception"]).strip() if isinstance(e.get("exception"), str) and
                str(e["exception"]).strip() else None,
                size=_size(e.get("size") if e.get("size") is not None else e.get("size_px")), file=file,
                cls=cls, exempt=exempt, exempt_reason=reason.strip() if reason else None)


#: The ease an engine writes when it measured a move instead of naming its curve (an AE act sampled in AE, where the
#: curve is whatever AE drew): the move is judged by the brand ease its samples follow.
MEASURED = "measured"


def is_measured(e: dict) -> bool:
    return str(e.get("ease") or "").strip().lower() == MEASURED or e.get("measured") is True


def rest_segments(frames, values, rel_tol: float = 1e-7) -> list[tuple[int, int]]:
    """Index ranges (first, last) of the stretches where a sampled channel moves, split at its rests (samples that
    don't change). Each stretch, from the rest before it to the rest after it, is one move."""
    np = _np()
    if len(values) < 2:
        return []
    tol = rel_tol * float(np.ptp(values, axis=0).max()) + 1e-12
    moving = np.abs(np.diff(values, axis=0)).max(axis=1) > tol
    out: list[tuple[int, int]] = []
    k, n = 0, len(moving)
    while k < n:
        if not moving[k]:
            k += 1
            continue
        j = k
        while j + 1 < n and moving[j + 1]:
            j += 1
        out.append((k, j + 1))
        k = j + 1
    return out


def split_measured(m: "Move") -> list["Move"]:
    """A measured entry (a property sampled every frame from its first key to its last) as one Move per stretch of
    motion between rests, so each is judged as the move it is. A measured scale-up is held to the entrance rule
    (for a person to judge, since the samples can't say whether it's an entrance)."""
    np = _np()
    out = []
    for i0, i1 in rest_segments(m.frames, m.values):
        seg = dataclasses.replace(m, frame_in=float(m.frames[i0]), frame_out=float(m.frames[i1]),
                                  start=m.values[i0].copy(), end=m.values[i1].copy(),
                                  frames=m.frames[i0:i1 + 1].copy(), values=m.values[i0:i1 + 1].copy(), measured=True)
        if seg.kind == "scale" and float(np.linalg.norm(seg.end)) > float(np.linalg.norm(seg.start)):
            seg.entrance = True
        out.append(seg)
    return out


def allowed_extras(law: "Law | None", m: "Move") -> dict[str, Ease]:
    """The generic curves (EXTRA_CURVES) this move may use without the brand naming them: a rig part's rest-to-rest
    curves, a carry's even profile, and any the brand's motion data declares (`extra_curves`) for every move."""
    if law is None:
        return {}
    cls, _why = element_class(m)
    return {name: e for name, e in law.extra.items()
            if name in law.extra_declared or e.role == cls}


def fit_measured(m: "Move", law: "Law | None") -> None:
    """Which of the brand's eases a measured move follows, by shape: the one its samples stray from least, when that
    is within LAW['ease_tolerance'] of its travel. Plain linear is a candidate too, so the linear rules still apply,
    and so are the generic curves this move may use (allowed_extras)."""
    np = _np()
    if law is None:
        return
    pq = _progress(m)
    if pq is None:
        return
    u, q = pq
    candidates = {n: e for n, e in law.eases.items() if e.fn is not None}
    for n, e in allowed_extras(law, m).items():
        candidates.setdefault(n, e)
    if not any(e.kind == "linear" for e in candidates.values()):
        candidates["linear"] = Ease("linear", kind="linear", fn=lambda x: np.asarray(x, dtype=float))
    best: tuple[str, float] | None = None
    for name, e in candidates.items():
        dev = float(np.max(np.abs(q - np.asarray(e.fn(u), dtype=float))))
        if best is None or dev < best[1] - 1e-12:
            best = (name, dev)
    if best is not None:
        m.fit_best, m.fit_dev = best
        m.fitted = best[0] if best[1] <= LAW["ease_tolerance"] else None


@dataclasses.dataclass
class MotionData:
    """Everything the checks read: the moves (in film frames), the film's rate and size, the brand's law."""

    moves: list[Move] = dataclasses.field(default_factory=list)
    fps: Fraction | None = None
    size: tuple[int, int] | None = None
    law: Law | None = None
    law_reason: str | None = None
    shots: dict[str, tuple[float, float]] = dataclasses.field(default_factory=dict)
    acts: list[int] = dataclasses.field(default_factory=list)
    traced: list[int] = dataclasses.field(default_factory=list)
    missing: list[int] = dataclasses.field(default_factory=list)
    malformed: list[str] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
    files: dict[int, str] = dataclasses.field(default_factory=dict)
    basis: dict[int, str | None] = dataclasses.field(default_factory=dict)  # act -> trace file hash
    brand_files: list[str] = dataclasses.field(default_factory=list)

    @property
    def fps_float(self) -> float | None:
        return float(self.fps) if self.fps else None


def _trace_entries(doc: Any) -> tuple[list | None, str | None]:
    """The entry list of a trace file, and its frame base when it says ("film" or "act")."""
    if isinstance(doc, list):
        return doc, None
    if isinstance(doc, dict):
        base = doc.get("frame_base") or doc.get("frames")
        base = base if base in ("film", "act") else None
        for k in ("entries", "moves", "trace", "motion"):
            if isinstance(doc.get(k), list):
                return doc[k], base
    return None, None


def _offset_for(entries: list[dict], act_range: tuple[int, int] | None, base: str | None,
                act: int | None = None) -> tuple[float, str | None]:
    """Film-frame offset for an act's trace: 0 when it's already in film frames, the act's first frame when it counts
    from the act's own frame 0 (the way a composition sees it)."""
    if act_range is None:
        return 0.0, None
    a_in, a_out = act_range
    if base == "film":
        return 0.0, None
    if base == "act":
        return float(a_in), None
    frames = [float(e["frame_in"]) for e in entries] + [float(e["frame_out"]) for e in entries]
    if not frames or a_in == 0:
        return 0.0, None
    lo, hi = min(frames), max(frames)
    if lo >= a_in - 1 and hi <= a_out + 1:
        return 0.0, None
    if lo >= -1 and hi <= (a_out - a_in) + 1:
        return float(a_in), None
    return 0.0, (f"act {act}'s trace runs from frame {_fmt(lo)} to {_fmt(hi)}, which fits neither its film frames "
                 f"({a_in}–{a_out}) nor its own frames (0–{a_out - a_in}); read as film frames")


def load_data(film_dir: Path, *, acts: Iterable[int] | None = None, ctx=None) -> MotionData:
    """Read the film's clock, size, shots, brand law and every act's motion trace."""
    from slate.qa.film import context  # noqa: PLC0415

    ctx = ctx or context(film_dir)
    data = MotionData(fps=ctx.fps, size=ctx.size)
    for s in ctx.shots:
        if s.get("frame_in") is not None and s.get("frame_out") is not None:
            data.shots[str(s.get("id"))] = (float(s["frame_in"]), float(s["frame_out"]))
    film_acts = {int(a["index"]): a for a in ctx.acts}
    wanted = {int(a) for a in acts} if acts else None
    folder = ctx.path(TRACE_DIR)
    found: dict[int, Path] = {}
    if folder.is_dir():
        for p in sorted(folder.glob("act*.json")):
            m = re.fullmatch(r"act(\d+)\.json", p.name)
            if m:
                found[int(m.group(1))] = p
    universe = sorted(film_acts) if film_acts else sorted(found)
    if wanted:
        unknown = sorted(a for a in wanted if a not in film_acts and a not in found)
        if unknown:
            known = ", ".join(map(str, universe)) or "none yet"
            raise UsageError(f"There's no act {', '.join(map(str, unknown))} in this film (acts: {known}).")
    data.acts = [a for a in universe if wanted is None or a in wanted]
    if wanted:
        data.acts += sorted(a for a in wanted if a not in data.acts and a in found)
    data.missing = [a for a in data.acts if a not in found]
    for a in data.acts:
        p = found.get(a)
        if p is None:
            continue
        rel = ctx.rel(p)
        data.files[a] = rel
        data.basis[a] = ctx.sha(p)
        try:
            doc = jsonio.read(p)
        except SlateError as exc:
            data.malformed.append(f"{rel}: {exc}")
            continue
        entries, base = _trace_entries(doc)
        if entries is None:
            data.malformed.append(f"{rel}: should be a list of moves (the motion-trace contract), "
                                  f"found {type(doc).__name__}")
            continue
        data.traced.append(a)
        good = []
        for i, e in enumerate(entries):
            problem = _entry_problem(e)
            if problem:
                who = e.get("id", "?") if isinstance(e, dict) else "?"
                data.malformed.append(f"{rel} entry {i + 1} ({who}) {problem}")
            else:
                good.append((i, e))
        fa = film_acts.get(a)
        rng = (int(fa["frame_in"]), int(fa["frame_out"])) if fa and fa.get("frame_in") is not None else None
        offset, note = _offset_for([e for _i, e in good], rng, base, a)
        if note:
            data.notes.append(note)
        for i, e in good:
            m = move_from(e, a, i, offset, rel)
            if is_measured(e):
                data.moves.extend(split_measured(m))
            else:
                data.moves.append(m)
    brand = ctx.brand
    motion, source = brand_motion(brand)
    if motion is None:
        data.law_reason = source
    else:
        data.law = law_from(motion, source=source, fps=float(ctx.fps) if ctx.fps else None)
        if data.law is None:
            data.law_reason = ("slate.motion.eases, the kit's own reading of a brand's motion law, isn't installed, "
                               "so the brand's eases can't be read the way the kit renders them")
    if data.law is not None and not data.law.neutral:
        for m in data.moves:
            if m.measured:
                fit_measured(m, data.law)
    from slate.qa import brandinfo  # noqa: PLC0415

    folder = brandinfo.find(brand) if brand else None
    if folder is not None:
        data.brand_files = [str(p) for p in (folder / "brand.json", folder / "motion.json") if p.is_file()]
    return data


# ---------------------------------------------------------------------------------------------------- results
@dataclasses.dataclass
class Finding:
    """A rule broken by one move. It waits on a person (REVIEW) instead of failing when the move carries a reasoned
    exception, or when only a person can grant the allowance (`person`)."""

    rule: str
    move: Move | None
    text: str
    value: float | None = None
    person: bool = False

    @property
    def for_person(self) -> bool:
        return self.person or bool(self.move is not None and self.move.exception)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"what": self.text}
        if self.move is not None:
            out.update({"act": self.move.act, "id": self.move.id, "prop": self.move.prop,
                        "frames": [round(self.move.frame_in, 3), round(self.move.frame_out, 3)]})
            if self.move.exception:
                out["exception"] = self.move.exception
        if self.value is not None:
            out["value"] = round(float(self.value), 4)
        return out


def _sub(name: str, status: str, summary: str, evidence: list[str] | None = None, **details):
    """A sub-check result. A `hint` (the step that fixes an UNCHECKED) is kept in details and shown as evidence."""
    from slate.qa.checks import Sub  # noqa: PLC0415

    evidence = list(evidence or [])
    if details.get("hint"):
        evidence.append(f"next: {details['hint']}")
    return Sub(name, status, summary, evidence, details)


def _verdict(name: str, data: MotionData, fails: list[Finding], reviews: list[Finding], *, pass_summary: str,
             fail_summary: Callable[[list[Finding]], str], checked: int, extra: dict | None = None, ctx=None):
    """One sub-check from its findings: FAIL when any rule is broken outright, REVIEW when the only problems sit on
    moves with a reasoned exception (a person accepts them, bound to the trace's hash), else PASS."""
    items = []
    seen: dict[str, int] = {}
    for f in reviews:
        m = f.move
        what = f"{m.label} {f.text}" if m else f.text
        if m is not None and m.exception:
            what += f" (stated exception: {m.exception})"
        key = f"{name}:{m.ref if m else _key(f.text)[:40]}"
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key += f"#{seen[key]}"  # one move can break a rule twice (speed and acceleration): one item each
        items.append({"key": key, "what": what, "basis": {m.file: data.basis.get(m.act)} if m and m.file else {}})
    open_items, accepted = _split_review(ctx, items)
    details = {"checked": checked, **(extra or {})}
    if fails:
        details["violations"] = [f.to_dict() for f in fails[:40]]
        details["violations_total"] = len(fails)
    if items:
        details["review_items"] = open_items
        if accepted:
            details["accepted"] = accepted
    evidence = [f"{f.move.label if f.move else ''}: {f.text}".lstrip(": ") for f in fails[:12]]
    if fails:
        return _sub(name, "FAIL", fail_summary(fails), evidence, **details)
    if open_items:
        return _sub(name, "REVIEW", f"{_plural(len(open_items), 'item')} for a person to judge: "
                    f"{open_items[0]['what']}", [i["what"] for i in open_items[:12]], **details)
    return _sub(name, "PASS", pass_summary + (f" ({_plural(len(accepted), 'exception')} accepted)" if accepted else ""),
                [], **details)


def _split_review(ctx, items: list[dict]) -> tuple[list[dict], list[dict]]:
    if not items or ctx is None:
        return items, []
    from slate.qa.checks import review_items  # noqa: PLC0415

    return review_items(ctx, GATE, items)


def _route(f: Finding, fails: list[Finding], reviews: list[Finding]) -> None:
    (reviews if f.for_person else fails).append(f)


def _no_brand_law(data: MotionData, what: str):
    """An UNCHECKED sub-check when the brand's own law can't judge `what`, else None."""
    if data.law is None:
        return _sub(what, "UNCHECKED", f"the brand's motion law can't be read: {data.law_reason}", [],
                    hint="Onboard the brand's motion (`slate brand onboard`), then run `slate qa motion` again.")
    if data.law.neutral:
        return _sub(what, "UNCHECKED", "the brand has no motion law of its own yet (motion.eases), so the kit's "
                    "neutral law stood in; only the brand's own law can pass this check", [],
                    hint="Onboard the brand's motion (`slate brand onboard`): its eases, duration scale and staggers.",
                    law=data.law.source, notes=data.law.notes)
    return None


def _coverage(data: MotionData) -> str:
    if data.missing:
        return f" in {_plural(len(data.traced), 'traced act')} (not yet traced: {', '.join(map(str, data.missing))})"
    return ""


# ---------------------------------------------------------------------------------------------------- series
def _chains(moves: list[Move]) -> list[list[Move]]:
    """Moves of one property split into chains that don't overlap in time (a chain hands one value on to the next;
    moves that overlap are layered and are summed instead)."""
    chains: list[list[Move]] = []
    for m in sorted(moves, key=lambda m: (m.frame_in, m.frame_out)):
        for ch in chains:
            if m.frame_in >= ch[-1].frame_out - 0.5:
                ch.append(m)
                break
        else:
            chains.append([m])
    return chains


def _chain_samples(chain: list[Move]):
    """The merged samples of a chain (later moves win at a shared frame)."""
    np = _np()
    table: dict[float, Any] = {}
    for m in chain:
        for f, v in zip(m.frames, m.values):
            table[round(float(f), 6)] = v
    if not table:
        return np.zeros(0), np.zeros((0, 1))
    keys = sorted(table)
    return np.array(keys), np.array([table[k] for k in keys], dtype=float)


def _on_grid(frames, values, grid):
    """Values sampled on integer film frames, held at the ends (np.interp's edges are holds)."""
    np = _np()
    out = np.zeros((len(grid), values.shape[1]))
    for j in range(values.shape[1]):
        out[:, j] = np.interp(grid, frames, values[:, j])
    return out


def _dense(m: Move, law: Law | None):
    """A move's samples, filled in on every frame from its named ease when the engine sampled it sparsely."""
    np = _np()
    if m.duration <= 0 or len(m.frames) >= m.duration / 1.5 or law is None:
        return m.frames, m.values
    e = law.curve(m.ease_used)
    if e is None or e.fn is None or m.start is None or m.end is None or m.start.shape != m.end.shape:
        return m.frames, m.values
    grid = np.arange(math.floor(m.frame_in), math.ceil(m.frame_out) + 1, dtype=float)
    u = (grid - m.frame_in) / m.duration
    p = np.asarray(e.fn(u), dtype=float).reshape(-1, 1)
    return grid, m.start.reshape(1, -1) + p * (m.end - m.start).reshape(1, -1)


def _chain_dense(chain: list[Move], law: Law | None):
    """A chain's merged samples, each move filled in on every frame when it was sampled sparsely."""
    np = _np()
    table: dict[float, Any] = {}
    for m in chain:
        fr, vals = _dense(m, law)
        for f, v in zip(fr, vals):
            table[round(float(f), 6)] = v
    if not table:
        return np.zeros(0), np.zeros((0, 1))
    keys = sorted(table)
    return np.array(keys), np.array([table[k] for k in keys], dtype=float)


def _absolute_xy(moves: list[Move], grid, law: Law | None):
    """One element's absolute (x, y) on the grid: its first chain on each axis as written, later layers added as
    changes. None when it has no x, y or position moves."""
    np = _np()
    axes: dict[int, Any] = {}
    by_prop: dict[tuple[str, str], list[Move]] = {}
    for m in moves:
        if m.kind in ("x", "y", "vector"):
            by_prop.setdefault((m.kind, m.prop), []).append(m)
    for (kind, _prop), ms in sorted(by_prop.items()):
        for chain in _chains(ms):
            fr, vals = _chain_dense(chain, law)
            if len(fr) == 0:
                continue
            g = _on_grid(fr, vals, grid)
            cols = [(0, g[:, 0])] if kind == "x" else [(1, g[:, 0])] if kind == "y" else \
                [(j, g[:, j]) for j in range(min(2, g.shape[1]))]
            for axis, col in cols:
                axes[axis] = col.copy() if axis not in axes else axes[axis] + (col - col[0])
    if not axes:
        return None
    n = len(grid)
    return np.column_stack([axes.get(0, np.zeros(n)), axes.get(1, np.zeros(n))])


def position_series(moves: list[Move], law: Law | None = None):
    """(integer film frames, positions (N, D)) for one element, from its x, y, vector and px moves. Layered moves
    add; chained moves hand on. None when the element has no positional moves."""
    np = _np()
    pos_moves = [m for m in moves if m.kind in POSITIONAL]
    if not pos_moves:
        return None
    lo = min(float(m.frames.min()) if len(m.frames) else m.frame_in for m in pos_moves)
    hi = max(float(m.frames.max()) if len(m.frames) else m.frame_out for m in pos_moves)
    grid = np.arange(math.floor(lo) - 1, math.ceil(hi) + 2, dtype=float)
    columns: list[Any] = []
    x = np.zeros(len(grid))
    y = np.zeros(len(grid))
    by_prop: dict[tuple[str, str], list[Move]] = {}
    for m in pos_moves:
        by_prop.setdefault((m.kind, m.prop), []).append(m)
    for (kind, _prop), ms in by_prop.items():
        for chain in _chains(ms):
            fr, vals = _chain_dense(chain, law)
            if len(fr) == 0:
                continue
            g = _on_grid(fr, vals, grid)
            g = g - g[0]
            if kind == "x":
                x = x + g[:, 0]
            elif kind == "y":
                y = y + g[:, 0]
            elif kind == "vector":
                x = x + g[:, 0]
                if g.shape[1] > 1:
                    y = y + g[:, 1]
            else:
                columns.append(g[:, 0])
    pos = np.column_stack([x, y] + columns)
    return grid, pos


def _motion_of(grid, pos):
    """Per-frame movement vectors, speed and acceleration. speed[k] is the move from grid[k] to grid[k+1]; accel[k]
    is how much that movement changes between consecutive frames."""
    np = _np()
    v = np.diff(pos, axis=0)
    speed = np.linalg.norm(v, axis=1)
    accel = np.linalg.norm(np.diff(v, axis=0), axis=1) if len(v) > 1 else np.zeros(0)
    return v, speed, accel


# ---------------------------------------------------------------------------------------------------- the checks
def check_trace(data: MotionData, ctx=None):
    if not data.acts and not data.traced:
        return _sub("trace", "UNCHECKED", "there are no acts yet (no timing/shots.json) and no motion traces",
                    [], hint="`slate timing` makes the shots; the engines write timing/motion/actN.json as they render")
    if data.malformed:
        return _sub("trace", "FAIL", f"{_plural(len(data.malformed), 'trace problem')}: {data.malformed[0]}",
                    data.malformed[:20], problems=data.malformed[:60], files=data.files)
    if data.missing:
        missing = ", ".join(str(a) for a in data.missing)
        return _sub("trace", "UNCHECKED",
                    f"{'act' if len(data.missing) == 1 else 'acts'} {missing} "
                    f"{'has' if len(data.missing) == 1 else 'have'}"
                    f" no motion trace yet, so {'its' if len(data.missing) == 1 else 'their'} motion can't be checked",
                    [f"{TRACE_DIR}/act{a}.json is missing" for a in data.missing],
                    hint="Render the act (the kit and the web engine write timing/motion/actN.json as they render); "
                         "an AE act exports its trace with its events.",
                    missing=data.missing, files=data.files)
    return _sub("trace", "PASS", f"every act has a motion trace ({_plural(len(data.moves), 'move')} in "
                f"{_plural(len(data.traced), 'act')})", [], files=data.files, notes=data.notes)


def _progress(m: Move):
    """(u, q): time through the move 0…1 and how far along its travel the value is (0 = from, 1 = to)."""
    np = _np()
    if m.start is None or m.end is None or m.start.shape != m.end.shape or m.duration <= 0:
        return None
    d = m.end - m.start
    dd = float(np.dot(d, d))
    if dd <= 1e-18 or m.values.shape[1] != len(d):
        return None
    inside = (m.frames >= m.frame_in - 1e-9) & (m.frames <= m.frame_out + 1e-9)
    if inside.sum() < 2:
        return None
    u = (m.frames[inside] - m.frame_in) / m.duration
    q = ((m.values[inside] - m.start.reshape(1, -1)) @ d) / dd
    return u, q


def _shot_span(data: MotionData, m: Move) -> tuple[float, float] | None:
    return data.shots.get(m.shot) if m.shot else None


def linear_allowed(data: MotionData, m: Move) -> str | None:
    """Why a linear move is allowed (the banned list's reasoned exceptions, and the declared ones: a reading sweep, a
    line whose steadiness is the meaning), or None when it isn't."""
    if m.exempt in EXEMPTIONS:
        return EXEMPTIONS[m.exempt]
    prop = _key(re.split(r"[.\-/:]", m.prop)[-1])
    extra = data.law.time_props if data.law else set()
    if prop in _TIME_PROPS or _key(m.prop) in _TIME_PROPS or prop in extra or _key(m.prop) in extra:
        return "it shows time"
    if m.kind == "rotation" and m.loop_period:
        return "a constant spin"
    span = _shot_span(data, m)
    if span and m.frame_in <= span[0] + 1e-6 and m.frame_out >= span[1] - 1e-6:
        return "a drift that outlasts its shot"
    return None


def check_eases(data: MotionData, ctx=None):
    moving = [m for m in data.moves if m.changes]
    if not moving:
        return None
    unchecked = _no_brand_law(data, "eases")
    if unchecked is not None:
        return unchecked
    law = data.law
    if not law.eases:
        return _sub("eases", "UNCHECKED", "the brand's motion data names no eases the kit can read, so there's "
                    "nothing to hold the moves to", [], law=law.source, notes=law.notes)
    np = _np()
    tol = LAW["ease_tolerance"]
    fails: list[Finding] = []
    reviews: list[Finding] = []
    worst = 0.0
    jumps = 0
    exempt: dict[str, int] = {}
    extras_used: dict[str, int] = {}
    names = ", ".join(law.eases)
    transforms = POSITIONAL + ("scale", "rotation", "depth")
    for m in moving:
        if m.measured and m.fitted is None:
            best = (f"; the closest, {m.fit_best!r}, is off by {m.fit_dev * 100:.1f}% of its travel"
                    if m.fit_best and m.fit_dev is not None else "")
            _route(Finding("eases", m, f"was measured from its render and follows none of the brand's eases{best} "
                           "(two moves chained without a stop read this way too: give each its own keys)",
                           m.fit_dev), fails, reviews)
            continue
        name = m.ease_used
        if name is None:
            _route(Finding("eases", m, "names no ease"), fails, reviews)
            continue
        low = name.lower()
        if low in ("hold", "step", "none", "cut"):
            jumps += 1
            continue
        e = law.ease(name)
        is_extra = False
        if e is None and law.extra_curve(name) is not None:
            x = law.extra_curve(name)
            if x.name in allowed_extras(law, m):
                e, is_extra = x, True
                extras_used[x.name] = extras_used.get(x.name, 0) + 1
            else:
                _cls, why = element_class(m)
                only = {"rig": "rig parts", "carry": "carries"}.get(str(x.role), "some moves")
                _route(Finding("eases", m, f"uses {name!r} ({x.lineage}), which only {only} use unless the brand "
                               f"declares it in motion.extra_curves; this move is {why}"), fails, reviews)
                continue
        linear = (e.kind == "linear") if e is not None else low == "linear"
        if linear:
            # The banned list: linear transform keys are mechanical starts and stops, except drifts that outlast
            # their shot, things that show time, and a constant spin. Linear on anything else needs the brand to
            # name it as one of its eases.
            why = linear_allowed(data, m)
            if why:
                exempt[why] = exempt.get(why, 0) + 1
                continue
            if m.kind in transforms:
                if m.shot and not data.shots:
                    _route(Finding("eases", m, "is linear, and without timing/shots.json it can't be shown to be a "
                                   "drift that outlasts its shot", person=True), fails, reviews)
                else:
                    _route(Finding("eases", m, f"is linear on {m.prop}: only drifts that outlast their shot, things "
                                   "that show time and a constant spin may be linear"), fails, reviews)
                continue
        if e is None:
            _route(Finding("eases", m, f"uses {name!r}, which isn't one of the brand's eases ({names})"), fails,
                   reviews)
            continue
        pq = _progress(m)
        if pq is None:
            continue
        u, q = pq
        peak = float(max(q.max() - 1.0, -q.min(), 0.0))
        allowed = float(e.peak or 1.0) if is_extra else law.allowed_peak(e.name)
        if peak > LAW["overshoot_tolerance"] and 1.0 + peak > allowed + LAW["overshoot_tolerance"]:
            if is_extra:
                text = f"overshoots by {peak * 100:.1f}% of its travel, and {e.name!r} ({e.lineage}) never does"
            elif allowed <= 1.0:
                text = (f"overshoots by {peak * 100:.1f}% of its travel, and the brand's overshoot policy doesn't "
                        f"allow {e.name!r} to" + ("" if law.overshoot_stated else " (the brand states no overshoot "
                                                                                   "policy, so none is allowed)"))
            else:
                text = f"overshoots to {1 + peak:.3f}, past the {allowed:.3f} the brand allows {e.name!r}"
            _route(Finding("eases", m, text, 1 + peak), fails, reviews)
            continue
        if e.fn is None:
            continue
        expect = np.asarray(e.fn(u), dtype=float)
        dev = np.abs(q - expect)
        k = int(np.argmax(dev))
        worst = max(worst, float(dev[k]))
        if dev[k] > tol:
            frame = m.frame_in + u[k] * m.duration
            _route(Finding("eases", m, f"doesn't follow {e.name!r}: off by {dev[k] * 100:.1f}% of its travel at "
                           f"frame {_fmt(frame, 2)} (it may be another curve under the brand's name)", float(dev[k])),
                   fails, reviews)
    extra = {"law": law.source, "eases": {n: {"kind": e.kind, "bezier": e.points, "role": e.role,
                                               "evaluated_by": e.fn_source} for n, e in law.eases.items()},
             "worst_deviation": round(worst, 4), "linear_exceptions": exempt, "jumps": jumps,
             "extra_curves": extras_used, "extra_curves_declared": sorted(law.extra_declared)}
    others = (f"; generic curves where they apply: " + ", ".join(f"{n} × {c}" for n, c in sorted(extras_used.items()))
              if extras_used else "")
    return _verdict("eases", data, fails, reviews, checked=len(moving), extra=extra, ctx=ctx,
                    pass_summary=f"every move uses one of the brand's eases and follows it (to "
                                 f"{worst * 100:.1f}% at worst{others}){_coverage(data)}",
                    fail_summary=lambda fs: f"{_plural(len(fs), 'move')} {_verb(fs, 'breaks', 'break')} the brand's "
                                            f"eases: {fs[0].move.label} "
                                            f"{fs[0].text}")


def _exempt_from_scale(data: MotionData, m: Move) -> str | None:
    if m.rig:
        return "rig"
    if m.loop_period:
        return "loop"
    if m.exempt == "reading_sweep":
        return "reading sweep"  # time-based: its length is what it reads, not a token
    if m.ease_used and m.ease_used.lower() in ("hold", "step", "none", "cut"):
        return "jump"
    e = data.law.ease(m.ease_used) if data.law else None
    if (e is not None and e.kind == "linear") or (e is None and str(m.ease_used or "").lower() == "linear"):
        return "linear" if linear_allowed(data, m) else None
    x = data.law.extra_curve(m.ease_used) if data.law and e is None else None
    if x is not None and x.name == "even" and x.name in allowed_extras(data.law, m) and data.law.durations and \
            data.fps_float and m.duration > max(span_frames(s, data.fps_float) for s in data.law.durations.values()) + \
            LAW["duration_tolerance_frames"]:
        # The law: anything longer than the longest token is a continuous drift (or time-based). The even travel
        # profile is the films' drift, so an even move past the scale is a drift, not a move off it.
        return "drift (even, longer than the scale)"
    return None


def check_durations(data: MotionData, ctx=None):
    moving = [m for m in data.moves if m.changes and m.duration > 0]
    if not moving:
        return None
    unchecked = _no_brand_law(data, "durations")
    if unchecked is not None:
        return unchecked
    law = data.law
    if not law.durations:
        return _sub("durations", "UNCHECKED", "the brand's motion data has no duration scale, so move lengths can't "
                    "be checked", [], law=law.source)
    fps = data.fps_float
    if not fps:
        return _sub("durations", "UNCHECKED", "film.json has no frame rate, so seconds can't become frames")
    tol = LAW["duration_tolerance_frames"]
    tokens = {name: span_frames(span, fps) for name, span in law.durations.items()}
    listing = ", ".join(f"{n} {_fmt(f)} f" for n, f in tokens.items())
    fails: list[Finding] = []
    reviews: list[Finding] = []
    seen: set[tuple] = set()
    exempt: dict[str, int] = {}
    uses: dict[tuple[int, str], set[tuple[float, float]]] = {}
    checked = 0
    for m in moving:
        why = _exempt_from_scale(data, m)
        if why:
            exempt[why] = exempt.get(why, 0) + 1
            continue
        key = (m.act, m.id, m.frame_in, m.frame_out)
        if key in seen:
            continue
        seen.add(key)
        checked += 1
        e = law.ease(m.ease_used)
        is_exit = bool(e and e.role == "exit") or "exit" in str(m.ease_used or "").lower()
        options = dict(tokens)
        if is_exit and law.exit_factor:
            options.update({f"{n} × {law.exit_factor:g}": f * law.exit_factor for n, f in tokens.items()})
        best = min(options.items(), key=lambda kv: abs(kv[1] - m.duration))
        if abs(best[1] - m.duration) <= tol + 1e-9:
            base = best[0].split(" × ")[0]
            uses.setdefault((m.act, base), set()).add((m.frame_in, m.frame_out))
            continue
        exits = f"; exits × {law.exit_factor:g}" if is_exit and law.exit_factor else ""
        _route(Finding("durations", m, f"lasts {_fmt(m.duration, 2)} frames ({m.duration / fps:.2f} s), which isn't "
                       f"on the brand's scale ({listing}{exits})",
                       m.duration), fails, reviews)
    for (act, token), spans in sorted(uses.items()):
        cap = law.max_per_act.get(token)
        if cap is not None and len(spans) > cap:
            fails.append(Finding("durations", None, f"act {act} has {len(spans)} {token} beats; the brand allows "
                                 f"{cap} per act", len(spans)))
    extra = {"law": law.source, "tokens_frames": {k: round(v, 3) for k, v in tokens.items()},
             "exit_factor": law.exit_factor, "exempt": exempt}
    return _verdict("durations", data, fails, reviews, checked=checked, extra=extra, ctx=ctx,
                    pass_summary=f"every move lasts one of the brand's durations{_coverage(data)}",
                    fail_summary=lambda fs: (f"{_plural(len(fs), 'move')} {_verb(fs, 'falls', 'fall')} off the brand's "
                                             "duration scale: "
                                             + (f"{fs[0].move.label} {fs[0].text}" if fs[0].move else fs[0].text)))


def stagger_groups(data: MotionData) -> list[list[Move]]:
    """Moves that stagger: in one act and shot, the same property on the same ease for the same length, on different
    elements, starting within a stagger step of each other. Rigs are left to the symmetry check, and a declared reading
    sweep (items resolved by a line moving at a constant speed) is time-based, not a stagger."""
    fps = data.fps_float or 0.0
    gap = LAW["stagger_gap_s"] * fps
    if data.law and data.law.stagger_units:
        gap = max(span_frames(u, fps) for u in data.law.stagger_units) + 1.0
    buckets: dict[tuple, list[Move]] = {}
    for m in data.moves:
        if not m.changes or m.rig or m.loop_period or m.duration <= 0 or m.exempt == "reading_sweep":
            continue
        buckets.setdefault((m.act, m.shot, m.kind, _key(m.prop), m.ease_used, round(m.duration * 2) / 2),
                           []).append(m)
    groups: list[list[Move]] = []
    for ms in buckets.values():
        firsts: dict[str, Move] = {}
        for m in sorted(ms, key=lambda m: m.frame_in):
            firsts.setdefault(m.id, m)
        ordered = sorted(firsts.values(), key=lambda m: m.frame_in)
        current: list[Move] = []
        for m in ordered:
            if current and m.frame_in - current[-1].frame_in > gap + 1e-9:
                if len(current) > 1:
                    groups.append(current)
                current = []
            current.append(m)
        if len(current) > 1:
            groups.append(current)
    return [g for g in groups if g[-1].frame_in - g[0].frame_in > 1e-9]


def check_staggers(data: MotionData, ctx=None):
    if not any(m.changes for m in data.moves):
        return None
    if not data.fps:
        return _sub("staggers", "UNCHECKED", "film.json has no frame rate, so stagger spreads can't be measured")
    groups = stagger_groups(data)
    if not groups:
        return None
    unchecked = _no_brand_law(data, "staggers")
    if unchecked is not None:
        return unchecked
    law = data.law
    if law.stagger_cap is None and law.stagger_cap_fraction is None and law.stagger_max_items is None:
        return _sub("staggers", "UNCHECKED", f"{_plural(len(groups), 'staggered group')} but the brand's motion data "
                    "has no stagger caps to hold them to", [], law=law.source,
                    hint="Add the brand's stagger caps (a spread in seconds, a fraction of the group's entrance, a "
                         "most-items count) during onboarding.")
    fps = float(data.fps)
    cap_frames = span_frames(law.stagger_cap, fps) if law.stagger_cap else None
    fails: list[Finding] = []
    reviews: list[Finding] = []
    report = []
    for g in groups:
        starts = [m.frame_in for m in g]
        spread = starts[-1] - starts[0]
        lead = g[0]
        who = f"act {lead.act} {lead.shot or ''} {lead.prop} on {lead.ease_used} ({', '.join(m.id for m in g[:6])}" \
              f"{'…' if len(g) > 6 else ''})".replace("  ", " ")
        problems = []
        if cap_frames is not None and spread > cap_frames + 1e-6:
            problems.append(f"spreads {spread / fps:.2f} s across {len(g)} items, past the brand's "
                            f"{cap_frames / fps:.2f} s cap")
        if law.stagger_cap_fraction is not None and spread > law.stagger_cap_fraction * lead.duration + 1e-6:
            problems.append(f"spreads {_fmt(spread, 2)} frames, more than {law.stagger_cap_fraction:g} of the "
                            f"group's own {_fmt(lead.duration, 2)}-frame move")
        distinct = sorted(set(round(s, 6) for s in starts))
        if law.stagger_max_items is not None and len(distinct) > law.stagger_max_items:
            problems.append(f"staggers {len(distinct)} items one by one; past {law.stagger_max_items} they're "
                            "grouped, never staggered singly")
        if law.stagger_eases is not None and lead.ease_used not in law.stagger_eases:
            problems.append(f"staggers on {lead.ease_used!r}, which the brand keeps together (only "
                            f"{', '.join(sorted(law.stagger_eases))} stagger)")
        steps = [b - a for a, b in zip(distinct, distinct[1:])]
        off = [s for s in steps if abs(s - round(s)) > LAW["whole_frame_tolerance"]]
        if off:
            problems.append(f"steps {_fmt(off[0], 3)} frames apart: stagger steps land on whole frames, so every "
                            "item's blur has the same shape")
        report.append({"group": who, "items": len(g), "spread_frames": round(spread, 3),
                       "steps": [round(s, 3) for s in steps[:12]], "problems": problems})
        for p in problems:
            _route(Finding("staggers", lead, f"{who} {p}", spread), fails, reviews)
    sweeps = sum(1 for m in data.moves if m.exempt == "reading_sweep" and m.changes)
    extra = {"law": law.source, "groups": report[:30], "cap_s": (cap_frames / fps) if cap_frames else None,
             "cap_fraction": law.stagger_cap_fraction, "max_items": law.stagger_max_items,
             **({"reading_sweeps_left_out": sweeps} if sweeps else {})}
    return _verdict("staggers", data, fails, reviews, checked=len(groups), extra=extra, ctx=ctx,
                    pass_summary=f"{_plural(len(groups), 'staggered group')} within the brand's caps, on whole "
                                 f"frames{_coverage(data)}",
                    fail_summary=lambda fs: f"{_plural(len(fs), 'stagger problem')}: {fs[0].text}")


def check_entrances(data: MotionData, ctx=None):
    np = _np()
    entries = [m for m in data.moves if m.entrance and m.kind == "scale"]
    if not entries:
        return None
    floor = LAW["entrance_min_scale"]
    fails: list[Finding] = []
    reviews: list[Finding] = []
    lowest = math.inf
    for m in entries:
        if m.end is None:
            continue
        target = np.where(np.abs(m.end) > 1e-9, m.end, np.nan)
        ratios = []
        if m.start is not None and m.start.shape == m.end.shape:
            ratios.append(np.nanmin(m.start / target))
        if len(m.values) and m.values.shape[1] == len(m.end):
            ratios.append(np.nanmin(m.values / target.reshape(1, -1)))
        ratios = [float(r) for r in ratios if np.isfinite(r)]
        if not ratios:
            continue
        low = min(ratios)
        lowest = min(lowest, low)
        if low < floor - 1e-6:
            if m.measured:
                text = (f"was measured scaling up from {low * 100:.0f}% of its size: if it's an entrance, entrances "
                        f"start from at least {floor * 100:.0f}% (nothing scales from zero)")
            else:
                text = (f"enters from {low * 100:.0f}% of its size; entrances start from at least {floor * 100:.0f}% "
                        "(nothing scales from zero)")
            _route(Finding("entrances", m, text, low, person=m.measured), fails, reviews)
    return _verdict("entrances", data, fails, reviews, checked=len(entries), ctx=ctx,
                    extra={"lowest": None if lowest == math.inf else round(lowest, 4)},
                    pass_summary=f"every scaled entrance starts from at least {floor * 100:.0f}% of its size"
                                 f"{_coverage(data)}",
                    fail_summary=lambda fs: f"{_plural(len(fs), 'entrance')} {_verb(fs, 'starts', 'start')} below "
                                            f"{floor * 100:.0f}% scale: "
                                            f"{fs[0].move.label} {fs[0].text}")


def _edge_series(m: Move, law: Law | None):
    """For a scale or rotation with a known element size: how far its edge or corner moves each frame, in px."""
    np = _np()
    if m.size is None or m.kind not in ("scale", "rotation"):
        return None
    fr, vals = _dense(m, law)
    if len(fr) < 2:
        return None
    grid = np.arange(math.floor(fr.min()) - 1, math.ceil(fr.max()) + 2, dtype=float)
    g = _on_grid(fr, vals, grid)
    w, h = m.size
    if m.kind == "scale":
        edge = g * (max(w, h) / 2.0)
    else:
        edge = np.radians(g) * (math.hypot(w, h) / 2.0)
    return grid, edge


CLASS_LABEL = {"rig": "a rig part", "ui": "a UI element", "carry": "a carry", "whip": "a whip"}


def _strictest(classes: Iterable[str], table: dict[str, dict]) -> str:
    """Of an element's classes, the one with the lowest speed limit (a whip, with none of its own, the loosest)."""
    return min(sorted(classes), key=lambda c: math.inf if table[c]["speed"] is None else table[c]["speed"])


def check_speed(data: MotionData, ctx=None):
    """Peak speed and acceleration per element, each held to its class's limits at the film's size (SPEED_CLASSES):
    a rig part to the plan's 30 px/frame and 8 px/frame² (with the crest exception), a UI element to 60 and 16, a carry
    to 170 and 45; a whip past the carry limits goes to a person."""
    np = _np()
    if not any(m.changes and (m.kind in POSITIONAL or (m.size and m.kind in ("scale", "rotation")))
               for m in data.moves):
        return None
    if not data.size:
        return _sub("speed", "UNCHECKED", "film.json has no width and height, so the px limits can't be scaled")
    lim = limits(*data.size)
    table = class_limits(*data.size, data.law)
    carry = table["carry"]
    lo_c, hi_c = LAW["crest_window"]
    fails: list[Finding] = []
    reviews: list[Finding] = []
    fastest = (0.0, None)
    hardest = (0.0, None)
    by_class: dict[str, dict] = {}
    by_id: dict[tuple[int, str], list[Move]] = {}
    for m in data.moves:
        by_id.setdefault((m.act, m.id), []).append(m)
    checked = 0
    for (act, ident), ms in sorted(by_id.items()):
        series = []
        ps = position_series(ms, data.law)
        if ps is not None:
            series.append(("position", ps[0], ps[1], [m for m in ms if m.kind in POSITIONAL]))
        for m in ms:
            es = _edge_series(m, data.law)
            if es is not None:
                series.append((f"{m.kind} edge", es[0], es[1], [m]))
        for what, grid, pos, members in series:
            checked += 1
            v, speed, accel = _motion_of(grid, pos)
            if not len(speed):
                continue
            cls = _strictest({element_class(m)[0] for m in members}, table)
            why = next(element_class(m)[1] for m in members if element_class(m)[0] == cls)
            row = table[cls]
            whip = row["speed"] is None
            speed_limit = carry["speed"] if whip else row["speed"]
            accel_limit = carry["accel"] if row["accel"] is None else row["accel"]
            label = CLASS_LABEL[cls] + ("" if why in ("declared", "a rig part") else f" ({why})")
            undeclared = cls == "ui" and why != "declared"
            carry_hint = (' (a page or card carried across the frame says "class": "carry" in its trace, and '
                          f"carries go to {_fmt(carry['speed'])} px/frame)") if undeclared else ""
            k = int(np.argmax(speed))
            top = float(speed[k])
            stats = by_class.setdefault(cls, {"elements": 0, "fastest": 0.0, "hardest": 0.0})
            stats["elements"] += 1
            stats["fastest"] = max(stats["fastest"], round(top, 3))
            owner = next((m for m in members if m.frame_in - 1 <= grid[k + 1] <= m.frame_out + 1), members[0])
            if top > fastest[0]:
                fastest = (top, f"act {act} {ident} at frame {int(grid[k + 1])}")
            if top > speed_limit + 1e-6:
                crest = False
                if cls == "rig":
                    moving = speed > max(0.02 * top, 1e-6)
                    a = k
                    while a > 0 and moving[a - 1]:
                        a -= 1
                    b = k
                    while b < len(speed) - 1 and moving[b + 1]:
                        b += 1
                    travel = float(np.linalg.norm(pos[b + 1] - pos[a]))
                    where = (k - a + 0.5) / max(b - a + 1, 1)
                    crest = travel > lim["crest_travel"] and lo_c <= where <= hi_c and top <= lim["crest_speed"] + 1e-6
                if whip:
                    text = (f"whips its {what} at {_fmt(top)} px/frame at frame {int(grid[k + 1])}, past the "
                            f"{_fmt(speed_limit)} px/frame a carry may reach: a whip is a person's call")
                else:
                    text = (f"moves its {what} {_fmt(top)} px/frame at frame {int(grid[k + 1])}, past the "
                            f"{_fmt(speed_limit)} px/frame limit for {label} at this size{carry_hint}")
                if crest:
                    text += (f", at the crest of a {_fmt(travel, 0)} px flight: up to {_fmt(lim['crest_speed'])} is "
                             "allowed there only when the beat can't afford a slower flight")
                _route(Finding("speed", owner, text, top, person=crest or whip), fails, reviews)
            if len(accel):
                j = int(np.argmax(accel))
                hard = float(accel[j])
                stats["hardest"] = max(stats["hardest"], round(hard, 3))
                if hard > hardest[0]:
                    hardest = (hard, f"act {act} {ident} at frame {int(grid[j + 1])}")
                if hard > accel_limit + 1e-6:
                    owner_a = next((m for m in members if m.frame_in - 1 <= grid[j + 1] <= m.frame_out + 1),
                                   members[0])
                    from_rest = speed[j] < 1e-6 < speed[j + 1]
                    hint = " (it leaves rest at full speed, so its blur jumps from nothing to a streak)" \
                        if from_rest else ""
                    past = (f"the {_fmt(accel_limit)} px/frame² a carry may reach: a whip is a person's call" if whip
                            else f"the {_fmt(accel_limit)} px/frame² limit for {label}")
                    text = (f"changes its {what} speed by {_fmt(hard)} px/frame² at frame {int(grid[j + 1])}, past "
                            f"{past}{hint}")
                    _route(Finding("speed", owner_a, text, hard, person=whip), fails, reviews)
    extra = {"limits": {k: round(v, 3) for k, v in lim.items()}, "size": list(data.size),
             "class_limits": {c: {"speed": None if r["speed"] is None else round(r["speed"], 3),
                                  "accel": None if r["accel"] is None else round(r["accel"], 3),
                                  "what": r["what"], "source": r["source"]} for c, r in table.items()},
             "classes": by_class,
             "fastest": {"px_per_frame": round(fastest[0], 3), "where": fastest[1]},
             "hardest": {"px_per_frame2": round(hardest[0], 3), "where": hardest[1]}}
    used = ", ".join(f"{c} {_fmt(table[c]['speed'])} and {_fmt(table[c]['accel'])}" for c in SPEED_CLASSES
                     if c in by_class and table[c]["speed"] is not None)
    return _verdict("speed", data, fails, reviews, checked=checked, extra=extra, ctx=ctx,
                    pass_summary=f"every traced move stays within its class's limits at this size ({used}; "
                                 f"fastest {_fmt(fastest[0])} px/frame, hardest change {_fmt(hardest[0])} "
                                 f"px/frame²){_coverage(data)}",
                    fail_summary=lambda fs: f"{_plural(len(fs), 'move')} {_verb(fs, 'breaks', 'break')} the speed "
                                            "limits: "
                                            f"{fs[0].move.label} {fs[0].text}")


def _refine_extreme(frames, values, i: int) -> float:
    """The time of an extreme between samples: the vertex of a parabola through the sample and its neighbours."""
    if 0 < i < len(values) - 1:
        a, b, c = float(values[i - 1]), float(values[i]), float(values[i + 1])
        denom = a - 2 * b + c
        h0, h1 = float(frames[i] - frames[i - 1]), float(frames[i + 1] - frames[i])
        if abs(denom) > 1e-15 and abs(h0 - h1) < 1e-9:
            return float(frames[i]) + max(-0.5, min(0.5, 0.5 * (a - c) / denom)) * h0
    return float(frames[i])


def reversals(frames, values, threshold: float) -> list[float]:
    """The times where a channel turns back (with hysteresis: it must come back `threshold` from its extreme), to a
    fraction of a frame."""
    out: list[float] = []
    if len(values) < 3:
        return out
    direction = 0
    ext_v, ext_i = float(values[0]), 0
    first = float(values[0])
    for i in range(1, len(values)):
        v = float(values[i])
        if direction >= 0 and v >= ext_v:
            ext_v, ext_i = v, i
            if direction == 0 and v > first + threshold:
                direction = 1
            continue
        if direction <= 0 and v <= ext_v:
            ext_v, ext_i = v, i
            if direction == 0 and v < first - threshold:
                direction = -1
            continue
        if direction == 1 and v < ext_v - threshold:
            out.append(_refine_extreme(frames, values, ext_i))
            direction, ext_v, ext_i = -1, v, i
        elif direction == -1 and v > ext_v + threshold:
            out.append(_refine_extreme(frames, values, ext_i))
            direction, ext_v, ext_i = 1, v, i
    return out


def check_loops(data: MotionData, ctx=None):
    np = _np()
    if not data.moves:
        return None
    if not data.fps:
        return _sub("loops", "UNCHECKED", "film.json has no frame rate, so loop periods can't be judged")
    fps = float(data.fps)
    brand_min = span_frames(data.law.min_loop_period, fps) if data.law and data.law.min_loop_period else None
    period = min_loop_period(fps, brand_min)
    half = period / 2.0
    fails: list[Finding] = []
    reviews: list[Finding] = []
    checked = 0
    for m in data.moves:
        if m.loop_period is not None:
            checked += 1
            if m.loop_period < period - 1e-6:
                _route(Finding("loops", m, f"loops every {_fmt(m.loop_period, 2)} frames "
                               f"({fps / m.loop_period:.1f} Hz); no loop is faster than a {_fmt(period, 1)}-frame "
                               "period", m.loop_period), fails, reviews)
    by_channel: dict[tuple[int, str, str], list[Move]] = {}
    for m in data.moves:
        if m.changes:
            by_channel.setdefault((m.act, m.id, m.prop), []).append(m)
    fastest = math.inf
    for (_act, _ident, _prop), ms in sorted(by_channel.items()):
        for chain in _chains(ms):
            fr, vals = _chain_samples(chain)
            if len(fr) < 3:
                continue
            checked += 1
            for j in range(vals.shape[1]):
                col = vals[:, j]
                rng = float(np.ptp(col))
                if rng <= 1e-9:
                    continue
                turns = reversals(fr, col, LAW["reversal_threshold"] * rng)
                if len(turns) < 2:
                    continue
                gaps = np.diff(turns)
                g = int(np.argmin(gaps))
                fastest = min(fastest, float(gaps[g]))
                if gaps[g] < half - 1e-6:
                    owner = next((m for m in chain if m.frame_in - 1 <= turns[g] <= m.frame_out + 1), chain[0])
                    # the wobble's rate from all its too-short swings (one gap alone is rounded to whole frames)
                    hz = fps / (2 * float(np.mean(gaps[gaps < half - 1e-6])))
                    _route(Finding("loops", owner, f"turns back every {_fmt(float(gaps[g]), 2)} frames around frame "
                                   f"{_fmt(turns[g], 1)} (a {hz:.1f} Hz wobble); reversals stay at least "
                                   f"{_fmt(half, 1)} frames apart, so a full swing takes {_fmt(period, 1)}",
                                   float(gaps[g])), fails, reviews)
                    break
    if not checked:
        return None
    extra = {"min_period_frames": round(period, 3), "min_half_period_frames": round(half, 3),
             "fastest_reversal_gap": None if fastest == math.inf else round(fastest, 3)}
    return _verdict("loops", data, fails, reviews, checked=checked, extra=extra, ctx=ctx,
                    pass_summary=f"nothing loops or wobbles faster than a {_fmt(period, 1)}-frame period"
                                 f"{_coverage(data)}",
                    fail_summary=lambda fs: f"{_plural(len(fs), 'channel')} "
                                            f"{_verb(fs, 'loops or wobbles', 'loop or wobble')} too fast: "
                                            f"{fs[0].move.label} {fs[0].text}")


def _best_delay(ref, other, max_shift: int) -> tuple[int, float]:
    """The whole-frame delay d that best makes other(t) = ref(t − d), and the worst mismatch left."""
    np = _np()
    n = len(ref)
    best = (0, math.inf)
    for d in range(-max_shift, max_shift + 1):
        if d >= 0:
            shifted = np.concatenate([np.full(d, ref[0]), ref[: n - d]]) if d else ref
        else:
            shifted = np.concatenate([ref[-d:], np.full(-d, ref[-1])])
        err = float(np.max(np.abs(other - shifted)))
        if err < best[1] - 1e-12:
            best = (d, err)
    return best


def phase_mode(delays: list[int]) -> tuple[str, dict]:
    """Name the phase relationship of a rig's parts from their whole-frame delays: unison (all together), pairs
    (two equal groups a fixed lag apart) or wave (every part one step after another)."""
    distinct = sorted(set(delays))
    if len(distinct) == 1:
        return "unison", {}
    if len(distinct) == 2:
        counts = [delays.count(d) for d in distinct]
        if counts[0] == counts[1]:
            return "pairs", {"lag": distinct[1] - distinct[0]}
        return "irregular", {"groups": dict(zip(map(str, distinct), counts))}
    if len(distinct) == len(delays):
        steps = {b - a for a, b in zip(distinct, distinct[1:])}
        if len(steps) == 1:
            return "wave", {"step": steps.pop()}
    return "irregular", {"delays": distinct}


def check_symmetry(data: MotionData, ctx=None):
    np = _np()
    rigs: dict[tuple[int, str], list[Move]] = {}
    for m in data.moves:
        if m.rig:
            rigs.setdefault((m.act, m.rig), []).append(m)
    if not rigs:
        return None
    tol_rel = LAW["symmetry_tolerance"]
    fails: list[Finding] = []
    reviews: list[Finding] = []
    report = []
    checked = 0
    for (act, rig), ms in sorted(rigs.items()):
        parts = sorted({m.id for m in ms})
        if len(parts) < 2:
            continue
        lo = min(float(m.frames.min()) if len(m.frames) else m.frame_in for m in ms)
        hi = max(float(m.frames.max()) if len(m.frames) else m.frame_out for m in ms)
        grid = np.arange(math.floor(lo) - 1, math.ceil(hi) + 2, dtype=float)
        max_shift = max(1, min(len(grid) - 1, int(math.ceil(hi - lo)) + 1))
        channels: dict[str, dict[str, Any]] = {}
        # scalar channels (scale, turn, radial offset, opacity…): each part's change from its own rest
        for prop in sorted({m.prop for m in ms if m.kind not in POSITIONAL}):
            per = {}
            for part in parts:
                pm = [m for m in ms if m.id == part and m.prop == prop]
                if not pm:
                    continue
                fr, vals = _chain_dense(sorted(pm, key=lambda m: m.frame_in), data.law)
                if len(fr) == 0:
                    continue
                g = _on_grid(fr, vals, grid)[:, 0]
                per[part] = g - g[0]
            if len(per) >= 2:
                channels[prop] = per
        # positions: each part's distance from the rig's centre (the centre of symmetric parts is their mean)
        pos = {}
        for part in parts:
            xy = _absolute_xy([m for m in ms if m.id == part], grid, data.law)
            if xy is not None:
                pos[part] = xy
        if len(pos) >= 2:
            centre = np.mean(np.stack(list(pos.values())), axis=0)
            radius = {part: np.linalg.norm(q - centre, axis=1) for part, q in pos.items()}
            channels["distance from centre"] = {part: r - r[0] for part, r in radius.items()}
        if not channels:
            continue
        checked += 1

        def starts_at(part: str) -> float:
            firsts = []
            for per in channels.values():
                c = per.get(part)
                if c is None:
                    continue
                amp_c = float(np.max(np.abs(c)))
                if amp_c > 1e-9:
                    firsts.append(float(np.argmax(np.abs(c) > 0.01 * amp_c)))
            return min(firsts) if firsts else math.inf

        ref_part = min(parts, key=lambda p: (starts_at(p), p))
        rig_delays: dict[str, int] = {ref_part: 0}
        rig_problems = []
        for prop, per in channels.items():
            amp = max(float(np.max(np.abs(c))) for c in per.values())
            if amp <= 1e-9:
                continue
            tol = tol_rel * amp + 1e-9
            ref = ref_part if ref_part in per else min(per, key=lambda p: (starts_at(p), p))
            for part, curve in per.items():
                if part == ref:
                    continue
                d, err = _best_delay(per[ref], curve, max_shift)
                if err > tol:
                    rig_problems.append((part, prop, f"part {part}'s {prop} isn't a whole-frame-delayed copy of part "
                                         f"{ref}'s (off by {err / amp * 100:.1f}% of the motion at best)"))
                    continue
                if ref != ref_part:
                    continue  # this channel lacks the rig's lead part, so its delays can't be put on the rig's clock
                if part in rig_delays and rig_delays[part] != d:
                    rig_problems.append((part, prop, f"part {part} runs {d} frames behind on {prop} but "
                                         f"{rig_delays[part]} on another channel: a part moves on one clock"))
                    continue
                rig_delays[part] = d
        mode, info = ("—", {})
        if rig_delays and len(rig_delays) == len(parts):
            base = min(rig_delays.values())
            delays = [rig_delays[p] - base for p in parts]
            mode, info = phase_mode(delays)
            spec = (data.law.rigs.get(rig) if data.law else None) or {}
            modes = spec.get("modes")
            if mode == "irregular":
                rig_problems.append((parts[0], "phase", f"its parts run {sorted(set(delays))} frames apart: the only "
                                     "phase relationships are unison, pairs a fixed lag apart, or a wave of equal "
                                     "steps"))
            elif isinstance(modes, list) and mode not in modes:
                rig_problems.append((parts[0], "phase", f"moves as a {mode}, which the brand's rig doesn't use "
                                     f"({', '.join(map(str, modes))})"))
            elif mode == "pairs" and _num(spec.get("pairs_lag")) is not None and info["lag"] != int(spec["pairs_lag"]):
                rig_problems.append((parts[0], "phase", f"moves in pairs {info['lag']} frames apart; the brand's "
                                     f"rig uses {int(spec['pairs_lag'])}"))
            elif mode == "wave" and _num(spec.get("wave_step")) is not None and info["step"] != int(spec["wave_step"]):
                rig_problems.append((parts[0], "phase", f"waves in {info['step']}-frame steps; the brand's rig uses "
                                     f"{int(spec['wave_step'])}"))
        report.append({"act": act, "rig": rig, "parts": len(parts), "channels": sorted(channels), "mode": mode,
                       **info, "problems": [p[2] for p in rig_problems][:10]})
        for part, _prop, text in rig_problems:
            owner = next((m for m in ms if m.id == part), ms[0])
            _route(Finding("symmetry", owner, f"rig {rig!r}: {text}"), fails, reviews)
    if not checked:
        return None
    return _verdict("symmetry", data, fails, reviews, checked=checked, extra={"rigs": report[:20]}, ctx=ctx,
                    pass_summary=f"{_plural(checked, 'mark rig')} move symmetrically ("
                                 + ", ".join(f"{r['rig']}: {r['mode']}" for r in report[:4]) + f"){_coverage(data)}",
                    fail_summary=lambda fs: f"{_plural(len(fs), 'symmetry problem')}: {fs[0].text}")


CHECKS: dict[str, Callable] = {
    "trace": check_trace,
    "eases": check_eases,
    "durations": check_durations,
    "staggers": check_staggers,
    "entrances": check_entrances,
    "speed": check_speed,
    "loops": check_loops,
    "symmetry": check_symmetry,
}


# ---------------------------------------------------------------------------------------------------- pixels
def analysis_size(width: int, height: int, short_side: int | None = None) -> tuple[int, int]:
    """A small size with the film's aspect whose short side is `short_side` (never larger than the film)."""
    s = min(int(short_side or PIXEL["short_side"]), int(min(width, height)))
    if width <= height:
        return s, max(s, int(round(s * height / width)))
    return max(s, int(round(s * width / height))), s


def read_gray(source: Path, size: tuple[int, int], *, start: int = 0, count: int | None = None) -> Iterator:
    """Frames of a render as float luma 0…1 at `size`: a video through ffmpeg (slate.qa.media), or a folder of
    images in name order."""
    np = _np()
    source = Path(source)
    if source.is_dir():
        from PIL import Image  # noqa: PLC0415

        files = sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."))
        stop = None if count is None else start + count
        for p in files[start:stop]:
            with Image.open(p) as im:
                small = im.convert("L").resize(size, Image.Resampling.BOX)
                yield np.asarray(small, dtype=np.float32) / 255.0
        return
    from slate.qa import media  # noqa: PLC0415

    for frame in media.iter_frames(source, start=start, count=count, size=size, pix_fmt="gray"):
        yield frame.astype(np.float32) / 255.0


def _bandpass(frame):
    from scipy import ndimage as ndi  # noqa: PLC0415

    return ndi.gaussian_filter(frame, 0.8) - ndi.gaussian_filter(frame, 2.5)


def block_motion(a, b, *, block: int, radius: int):
    """Per-block motion from frame a to frame b by exhaustive block matching on band-passed frames.

    Returns (dx, dy, trusted, textured) arrays of shape (rows, cols): the displacement that best maps each block of a
    onto b (sub-pixel from a parabola through the best match), whether it can be trusted, and whether the block has
    texture at all. A match is trusted when the block has texture, its best match is clearly better than any match
    more than a pixel away (a straight edge or a repeating pattern matches in many places), and the move explains the
    change (a fade or a cut isn't a move).
    """
    np = _np()
    from numpy.lib.stride_tricks import sliding_window_view  # noqa: PLC0415

    A = _bandpass(np.asarray(a, dtype=np.float32)).astype(np.float32)
    B = _bandpass(np.asarray(b, dtype=np.float32)).astype(np.float32)
    h, w = A.shape
    rows, cols = h // block, w // block
    if rows == 0 or cols == 0:
        z = np.zeros((max(rows, 1), max(cols, 1)))
        return z, z, z.astype(bool), z.astype(bool)
    Ac = np.ascontiguousarray(A[: rows * block, : cols * block])
    Bp = np.pad(B, radius, mode="edge")
    n = 2 * radius + 1
    windows = sliding_window_view(Bp, (rows * block, cols * block))[:n, :n]
    sad = np.empty((n, n, rows, cols), dtype=np.float32)
    for iy in range(n):  # one row of displacements at a time: each (n, h, w) slab is one numpy operation
        diff = np.abs(windows[iy] - Ac[None])
        # sum each block along x first (contiguous), then along y: much faster than one strided reduction
        sad[iy] = diff.reshape(n, rows * block, cols, block).sum(axis=3).reshape(n, rows, block, cols).sum(axis=2)
    flat = sad.reshape(n * n, rows, cols)
    best = np.argmin(flat, axis=0)
    s0 = sad[radius, radius]
    smin = np.take_along_axis(flat, best[None], axis=0)[0]
    # Prefer standing still: when not moving matches as well as the best shift (a straight edge sliding along
    # itself, a flat field), the block hasn't moved. Without this, ties go to the first shift searched.
    still = s0 <= smin + np.maximum(1e-6, 0.02 * smin)
    best = np.where(still, radius * n + radius, best)
    smin = np.where(still, s0, smin)
    by, bx = np.divmod(best, n)
    iy_g = np.arange(n).reshape(n, 1, 1, 1)
    ix_g = np.arange(n).reshape(1, n, 1, 1)
    near = (np.abs(iy_g - by[None, None]) <= 1) & (np.abs(ix_g - bx[None, None]) <= 1)
    second = np.where(near, np.inf, sad).reshape(n * n, rows, cols).min(axis=0)

    def at(iy, ix):
        idx = np.clip(iy, 0, n - 1) * n + np.clip(ix, 0, n - 1)
        return np.take_along_axis(flat, idx[None], axis=0)[0]

    def vertex(c, m, p):
        # equiangular (V) fit: a sum of absolute differences is V-shaped around its minimum, so a parabola would
        # pull every estimate toward the whole pixel (and over-read speeds); two lines of equal slope don't
        rise = np.maximum(m, p) - c
        with np.errstate(divide="ignore", invalid="ignore"):
            off = np.where(rise > 1e-12, 0.5 * (m - p) / rise, 0.0)
        return np.clip(off, -0.5, 0.5)

    ox = np.where((bx > 0) & (bx < n - 1), vertex(smin, at(by, bx - 1), at(by, bx + 1)), 0.0)
    oy = np.where((by > 0) & (by < n - 1), vertex(smin, at(by - 1, bx), at(by + 1, bx)), 0.0)
    dx = (bx - radius) + ox
    dy = (by - radius) + oy
    texture = np.abs(Ac).reshape(rows, block, cols, block).mean(axis=(1, 3))
    tex_floor = max(PIXEL["texture"], 0.05 * float(texture.max()) if texture.size else 0.0)
    textured = texture >= tex_floor
    moved = (bx != radius) | (by != radius)
    # at the edge of the search the true move may lie beyond it: nothing measured there can be trusted
    edge = (bx == 0) | (bx == n - 1) | (by == 0) | (by == n - 1)
    distinct = smin + 1e-6 * block * block <= PIXEL["ambiguity"] * second  # strictly better: ties never pass
    trusted = textured & distinct & ~edge & (~moved | (smin <= 0.7 * s0))
    return dx, dy, trusted, textured


def coherent(mask, dx, dy, tolerance: float = 1.0):
    """Blocks in `mask` with at least one neighbouring block in `mask` moving the same way (to `tolerance` px): a
    real object covers several blocks and moves as one; a lone block with a stray vector is a false match."""
    np = _np()
    rows, cols = mask.shape
    out = np.zeros_like(mask)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            if oy == 0 and ox == 0:
                continue
            ys = slice(max(0, oy), rows + min(0, oy))
            yd = slice(max(0, -oy), rows + min(0, -oy))
            xs = slice(max(0, ox), cols + min(0, ox))
            xd = slice(max(0, -ox), cols + min(0, -ox))
            agree = (mask[yd, xd] & mask[ys, xs] & (np.abs(dx[yd, xd] - dx[ys, xs]) <= tolerance)
                     & (np.abs(dy[yd, xd] - dy[ys, xs]) <= tolerance))
            out[yd, xd] |= agree
    return out


def fast_pairs(frames: Iterable, *, film_short: float, speed_limit: float, analysis_short: int,
               block: int | None = None) -> tuple[list[dict], dict]:
    """Frame pairs with fast motion: [{pair, speed (film px/frame), blocks, box}] and a summary of the scan.

    `pair` k is the move from frame k to frame k+1 of the source. A pair counts as fast when at least two
    neighbouring trusted blocks move the same way faster than 1.25 times the speed limit. Cuts (most of the picture
    changing with no move to explain it) are skipped.
    """
    np = _np()
    scale = film_short / float(analysis_short)  # film px per analysis px
    limit_a = speed_limit / scale
    block = block or max(3, int(round(analysis_short / 12)))
    radius = int(math.ceil(max(3.0, PIXEL["search_factor"] * limit_a)))
    fast_a = PIXEL["fast_factor"] * limit_a
    out: list[dict] = []
    prev = None
    pairs = cuts = 0
    top = 0.0
    for k, frame in enumerate(frames):
        if prev is not None:
            pairs += 1
            change = float(np.mean(np.abs(frame - prev)))
            if change < 1e-4:
                prev = frame
                continue
            dx, dy, trusted, textured = block_motion(prev, frame, block=block, radius=radius)
            if change > 0.12 and trusted.sum() < 0.3 * max(1, int(textured.sum())):
                cuts += 1  # most of the picture changed and no move explains it: a cut, not motion
                prev = frame
                continue
            speed = np.hypot(dx, dy)
            steady = coherent(trusted, dx, dy)
            if steady.any():  # a high percentile, so one stray block can't set the record
                top = max(top, float(np.percentile(speed[steady], 90)) * scale)
            fast = coherent(trusted & (speed > fast_a), dx, dy)
            if int(fast.sum()) >= PIXEL["min_blocks"]:
                rows, cols = np.nonzero(fast)
                h, w = frame.shape
                box = [float(cols.min() * block / w), float(rows.min() * block / h),
                       float((cols.max() + 1) * block / w), float((rows.max() + 1) * block / h)]
                out.append({"pair": k - 1, "speed": round(float(np.median(speed[fast])) * scale, 2),
                            "blocks": int(fast.sum()), "box": [round(x, 3) for x in box]})
        prev = frame
    # a real move is fast for at least two frame pairs running; a lone fast pair is a false match (or a one-frame
    # pop, which the glitch gate judges)
    flagged = {p["pair"] for p in out}
    lone = [p for p in out if p["pair"] - 1 not in flagged and p["pair"] + 1 not in flagged]
    out = [p for p in out if p not in lone]
    return out, {"pairs": pairs, "cuts": cuts, "fastest_measured": round(top, 2), "block": block, "search": radius,
                 "analysis_short_side": analysis_short, "fast_over": round(fast_a * scale, 2),
                 "measurable_up_to": round(radius * scale, 1), "lone_fast_pairs_ignored": len(lone)}


def traced_activity(data: MotionData) -> tuple[dict[int, bool], dict[int, float], dict[int, bool]]:
    """Per film frame f (the move from f−1 to f): whether any traced visible move is under way, the fastest traced
    positional speed there, and whether a move without a px speed (a scale, a turn) is under way."""
    np = _np()
    moving: dict[int, bool] = {}
    speed_at: dict[int, float] = {}
    other: dict[int, bool] = {}
    by_id: dict[tuple[int, str], list[Move]] = {}
    for m in data.moves:
        if m.changes and m.kind not in ("opacity",):
            by_id.setdefault((m.act, m.id), []).append(m)
    for _key_, ms in by_id.items():
        ps = position_series(ms, data.law)
        if ps is not None:
            grid, pos = ps
            _v, speed, _a = _motion_of(grid, pos)
            for k, s in enumerate(speed):
                if s > 1e-6:
                    f = int(grid[k + 1])
                    moving[f] = True
                    speed_at[f] = max(speed_at.get(f, 0.0), float(s))
        for m in ms:
            if m.kind in POSITIONAL:
                continue
            fr, vals = _chain_samples([m])
            if len(fr) < 2:
                continue
            grid = np.arange(math.floor(fr.min()), math.ceil(fr.max()) + 2, dtype=float)
            g = _on_grid(fr, vals, grid)
            d = np.abs(np.diff(g, axis=0)).max(axis=1)
            for k, s in enumerate(d):
                if s > 1e-9:
                    f = int(grid[k + 1])
                    moving[f] = True
                    other[f] = True
    return moving, speed_at, other


@dataclasses.dataclass
class RenderSource:
    act: int | None
    path: Path
    film_frame: int  # the film frame of the source's first frame
    frames: int | None


def render_sources(ctx, data: MotionData, *, render: str | None = None, act: int | None = None,
                   render_from: int | None = None) -> tuple[list[RenderSource], list[int]]:
    """What to cross-check: an explicit render, else each act's current render. Also the acts that have none."""
    if render:
        p = Path(render).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p) if (Path.cwd() / p).exists() else ctx.path(render)
        a = next((x for x in ctx.acts if act is not None and x["index"] == act), None)
        start = render_from if render_from is not None else (int(a["frame_in"]) if a and a["frame_in"] is not None
                                                             else 0)
        return [RenderSource(act, p, int(start), None)], []
    current = ctx.current_renders()
    out, missing = [], []
    for a in data.acts:
        e = current.get(a)
        if e is None or not Path(e["path"]).exists():
            missing.append(a)
            continue
        n = None if e.get("frame_out") is None else int(e["frame_out"]) - int(e["frame_in"])
        out.append(RenderSource(a, Path(e["path"]), int(e["frame_in"]), n))
    return out, missing


def source_digest(ctx, path: Path) -> str | None:
    """What a picture source is, as a hash: the file's sha256, or for a folder of frames a hash over each frame's
    name and sha256 (so a changed frame is a changed source)."""
    path = Path(path)
    if path.is_file():
        return ctx.sha(path)
    if not path.is_dir():
        return None
    h = hashlib.sha256()
    for p in sorted(x for x in path.iterdir() if x.suffix.lower() in IMAGE_EXTS and not x.name.startswith(".")):
        h.update(f"{p.name}:{jsonio.sha256(p)}\n".encode())
    return h.hexdigest()


def cache_name(ctx, path: Path) -> str:
    """A cache file name for one picture source (its film-relative path, flattened)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", ctx.rel(path)).strip("_")[-120:] or "source"


def cached_scan(ctx, folder: str, path: Path, key: dict, scan: Callable[[], Any], *, what: str, kind: str = "pixels"):
    """A whole-picture scan, cached in qa/<folder>/<kind>_<source>.json against the source's hash and the scan's
    settings, the way the glitch scan is. Under --quick (ctx.quick) a cached scan is reused and a missing one is never
    started: that's a MissingPrerequisite, which the sub-check reports as UNCHECKED."""
    cache = ctx.path(f"qa/{folder}/{kind}_{cache_name(ctx, path)}.json")
    cached = jsonio.read(cache, default=None) if cache.is_file() else None
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached.get("scan")
    if getattr(ctx, "quick", False):
        raise MissingPrerequisite(f"{ctx.rel(path)} hasn't been scanned for {what} in this version, and --quick "
                                  "doesn't start whole-picture work.",
                                  hint=f"Run `slate qa {folder}` without --quick to scan it once; later quick runs "
                                       "reuse the scan.")
    result = scan()
    jsonio.write_atomic(cache, {"generated": f"do not edit by hand: slate qa {folder}", "key": key, "scan": result})
    return result


def check_pixels(ctx, data: MotionData, *, render: str | None = None, render_act: int | None = None,
                 render_from: int | None = None, skip: bool = False, read: list | None = None):
    """Cross-check the traces on the pixels: fast motion in a render that no traced move explains is REVIEW.
    Every picture file it reads is appended to `read` (the gate's inputs, so a new render turns it STALE)."""
    read = read if read is not None else []
    if skip:
        return _sub("pixels", "UNCHECKED", "the pixel cross-check was skipped (--no-pixels), so fast motion outside "
                    "the trace hasn't been looked for", [], hint="Run `slate qa motion` without --no-pixels.")
    if not data.size:
        return _sub("pixels", "UNCHECKED", "film.json has no width and height, so the render can't be measured")
    sources, missing = render_sources(ctx, data, render=render, act=render_act, render_from=render_from)
    if not sources:
        acts = ", ".join(map(str, missing)) or "any act"
        return _sub("pixels", "UNCHECKED", f"there's no render of {'act ' if len(missing) == 1 else 'acts '}"
                    f"{acts} to cross-check the traces against yet" if missing else
                    "there's no render to cross-check the traces against yet", [],
                    hint="`slate render` registers each act's render; then run `slate qa motion` again.",
                    missing=missing)
    from slate.qa import media  # noqa: PLC0415

    lim = limits(*data.size)
    w, h = data.size
    size = analysis_size(w, h)
    moving, traced_speed, other = traced_activity(data)
    items: list[dict] = []
    scans = []
    evidence = []
    for src in sources:
        if not src.path.exists():
            return _sub("pixels", "UNCHECKED", f"{src.path} doesn't exist", [str(src.path)])
        read.append(src.path)
        if src.path.is_file() and not media.have("ffmpeg"):
            return _sub("pixels", "UNCHECKED", "ffmpeg isn't installed, so the render can't be decoded", [],
                        hint="Install it with Homebrew (`brew install ffmpeg`), or run `slate doctor --install`.")
        key = {"file": ctx.rel(src.path), "sha256": source_digest(ctx, src.path), "film_frame": src.film_frame,
               "frames": src.frames, "analysis": list(size), "film_short": min(w, h),
               "speed_limit": round(lim["speed"], 6), "pixel": PIXEL, "version": __version__}

        def scan(src=src):
            pairs, summary = fast_pairs(read_gray(src.path, size, count=src.frames), film_short=min(w, h),
                                        speed_limit=lim["speed"], analysis_short=min(size))
            return {"pairs": pairs, "summary": summary}

        try:
            done = cached_scan(ctx, "motion", src.path, key, scan, what="fast motion")
        except MissingPrerequisite as exc:
            return _sub("pixels", "UNCHECKED", exc.message, [ctx.rel(src.path)], hint=exc.hint)
        except SlateError as exc:
            return _sub("pixels", "UNCHECKED", f"{ctx.rel(src.path)} couldn't be decoded: {exc}", [str(src.path)])
        pairs, summary = done["pairs"], done["summary"]
        scans.append({"act": src.act, "file": ctx.rel(src.path), "film_frame": src.film_frame, **summary})
        evidence.append(f"{ctx.rel(src.path)}: {summary['pairs']} frame pairs, fastest measured "
                        f"{summary['fastest_measured']} px/frame")
        flagged = []
        for p in pairs:
            f = src.film_frame + p["pair"] + 1
            near = range(f - 1, f + 2)
            if not any(moving.get(x) for x in near):
                flagged.append((f, p, "untraced", 0.0))
                continue
            if any(other.get(x) for x in near):
                continue
            traced = max(traced_speed.get(x, 0.0) for x in near)
            if traced < PIXEL["under_traced"] * p["speed"]:
                flagged.append((f, p, "under-traced", traced))
        # One place per stretch of fast motion (gaps of up to 2 frames bridged), whatever the trace says along it: a
        # fast move that runs on past the end of a slow traced one is one thing for a person to judge, not two.
        segments: list[dict] = []
        for f, p, why, traced in flagged:
            if segments and f - segments[-1]["to"] <= 2:
                seg = segments[-1]
                seg["to"] = f
                if p["speed"] > seg["speed"]:
                    seg.update(speed=p["speed"], at=f, box=p["box"])
                seg["traced"] = max(seg["traced"], traced)
                seg["whys"].add(why)
            else:
                segments.append({"from": f, "to": f, "at": f, "speed": p["speed"], "box": p["box"], "whys": {why},
                                 "traced": traced})
        for seg in segments:
            act_txt = f"act {src.act} " if src.act is not None else ""
            span = f"{act_txt}film frames {seg['from']}–{seg['to']}: the render moves {_fmt(seg['speed'])} px/frame"
            if seg["whys"] == {"untraced"}:
                seg["why"] = "untraced"
                what = f"{span} where no traced move is under way (region {seg['box']})"
            elif seg["whys"] == {"under-traced"}:
                seg["why"] = "under-traced"
                what = f"{span} but the trace's fastest move there is {_fmt(seg['traced'])}"
            else:
                seg["why"] = "untraced and under-traced"
                what = (f"{span}; part of it has no traced move under way and the rest is traced at no more than "
                        f"{_fmt(seg['traced'])} (region {seg['box']})")
            items.append({"key": f"pixels:{ctx.rel(src.path)}:{seg['from']}-{seg['to']}", "what": what,
                          "basis": ({ctx.rel(src.path): ctx.sha(src.path)} if src.path.is_file() else
                                    {"renders/registry.json": ctx.sha("renders/registry.json")}),
                          "speed": seg["speed"], "film_frames": [seg["from"], seg["to"]], "why": seg["why"]})
    open_items, accepted = _split_review(ctx, items)
    details = {"scans": scans, "limit": round(lim["speed"], 3), "review_items": open_items}
    if accepted:
        details["accepted"] = accepted
    if missing:
        details["missing"] = missing
    fastest = max((s["fastest_measured"] for s in scans), default=0.0)
    if open_items:
        worst = max(open_items, key=lambda i: i["speed"])
        return _sub("pixels", "REVIEW", f"the render has fast motion the trace doesn't explain in "
                    f"{_plural(len(open_items), 'place')} (worst {_fmt(worst['speed'])} px/frame): does that shot "
                    "trace its moves?", [i["what"] for i in open_items[:12]] + evidence, **details)
    if missing:
        return _sub("pixels", "UNCHECKED", f"{'act' if len(missing) == 1 else 'acts'} "
                    f"{', '.join(map(str, missing))} {'has' if len(missing) == 1 else 'have'} no render to "
                    "cross-check yet; the rest match their traces", evidence, **details)
    return _sub("pixels", "PASS", f"every fast move in the render is in the trace ({sum(s['pairs'] for s in scans)} "
                f"frame pairs, fastest measured {_fmt(fastest)} px/frame)"
                + (f"; {_plural(len(accepted), 'item')} accepted" if accepted else ""), evidence, **details)


# ---------------------------------------------------------------------------------------------------- the gate
SUBCHECKS = (*CHECKS, "pixels")


def scope_sub(gate: str, narrowed: list[str]):
    """An UNCHECKED sub-check for a run that looked at less than the whole film (some acts, an unregistered render,
    some sub-checks). Such a run still reports what it found, and a FAIL stays a FAIL, but it can never be recorded
    as the gate's PASS: only a full run can pass a gate."""
    return _sub("scope", "UNCHECKED", f"this run checked only part of the film ({'; '.join(narrowed)}), so it can't "
                "pass the gate", [], hint=f"Run `slate qa {gate}` with no narrowing options to check the whole film.",
                narrowed=narrowed)


def evaluate(film_dir: Path, *, acts: Iterable[int] | None = None, render: str | None = None,
             render_act: int | None = None, render_from: int | None = None, pixels: bool = True,
             only: Iterable[str] | None = None) -> GateResult:
    """Run the motion sub-checks on a film (every one, or `only` these) and combine them into the one `motion` gate
    result. A narrowed run (some acts, an explicit render, some sub-checks) carries an UNCHECKED scope sub-check."""
    from slate.qa.checks import combine  # noqa: PLC0415
    from slate.qa.film import context  # noqa: PLC0415

    ctx = context(film_dir)
    _ = ctx.film  # a folder without film.json is a usage error, not a gate result
    acts = sorted({int(a) for a in acts}) if acts else None
    wanted = set(only) if only else None
    if wanted is not None and not wanted <= set(SUBCHECKS):
        bad = ", ".join(sorted(wanted - set(SUBCHECKS)))
        raise UsageError(f"The motion gate has no {bad} check.", hint="Its checks: " + ", ".join(SUBCHECKS) + ".")
    data = load_data(ctx.dir, acts=acts, ctx=ctx)
    subs = []
    for name, fn in CHECKS.items():
        if wanted is not None and name not in wanted:
            continue
        s = fn(data, ctx)
        if s is not None:
            subs.append(s)
    read: list = []
    run_pixels = wanted is None or "pixels" in wanted
    if run_pixels:
        subs.append(check_pixels(ctx, data, render=render, render_act=render_act if render_act is not None else
                                 (acts[0] if acts and len(acts) == 1 else None), render_from=render_from,
                                 skip=not pixels, read=read))
    narrowed = []
    film_acts = [int(a["index"]) for a in ctx.acts]
    if acts and film_acts and set(film_acts) - set(acts):
        narrowed.append(f"act{'s' if len(acts) > 1 else ''} {', '.join(map(str, acts))} of "
                        f"{', '.join(map(str, film_acts))} (--act)")
    if render and run_pixels and pixels:
        narrowed.append(f"the pixels of {render} instead of the registered renders (--render)")
    if wanted is not None and wanted != set(SUBCHECKS):
        narrowed.append(f"only {', '.join(s for s in SUBCHECKS if s in wanted)} (--only)")
    if narrowed:
        subs.append(scope_sub(GATE, narrowed))
    law = data.law
    fn_sources = sorted({e.fn_source for e in (law.eases.values() if law else []) if e.fn_source})
    engine = gates.engine("slate qa motion", __version__, eases=", ".join(fn_sources) or None,
                          pixels="block matching on band-passed luma"
                                 + (f" ({_media_version()})" if pixels and run_pixels else ""))
    inputs_paths = ["film.json", "timing/shots.json", "renders/registry.json"]
    inputs_paths += [data.files[a] for a in sorted(data.files)]
    inputs_paths += [f"{TRACE_DIR}/act{a}.json" for a in data.missing]
    inputs_paths += data.brand_files
    inputs_paths += [p for p in read if Path(p).is_file()]
    if render and not read:
        inputs_paths.append(render)
    inputs = ctx.inputs(dict.fromkeys(str(p) for p in inputs_paths))
    details = {"acts": data.acts, "traced": data.traced, "missing": data.missing, "law": law.source if law else None,
               "law_reason": data.law_reason, "law_notes": law.notes if law else [], "notes": data.notes,
               "limits_basis": "the frame's short side (its height on a landscape or square film)",
               **({"narrowed": narrowed} if narrowed else {}), **lifted_review_items(subs)}
    result = combine(GATE, subs, engine=engine, inputs=inputs, details=details,
                     empty_summary="nothing to check yet: no acts and no motion traces")
    if result.status == "PASS":
        result.summary = (f"the motion law holds: {_plural(len(data.moves), 'traced move')} in "
                          f"{_plural(len(data.traced), 'act')}, and the renders move only as traced")
    return result


def lifted_review_items(subs) -> dict:
    """Every sub-check's REVIEW items (and the ones a person accepted), lifted to the gate's own details, where
    `slate qa triage --accept motion:<key>` looks for them."""
    open_items = [i for s in subs for i in (s.details.get("review_items") or [])]
    accepted = [i for s in subs for i in (s.details.get("accepted") or [])]
    out: dict = {}
    if open_items:
        out["review_items"] = open_items
    if accepted:
        out["accepted"] = accepted
    return out


def _media_version() -> str:
    from slate.qa import media  # noqa: PLC0415

    v = media.version("ffmpeg")
    return f"ffmpeg {v}" if v else "ffmpeg not installed"


def gate(film_dir: Path) -> GateResult:
    """The motion law on every act's trace: the brand's eases, durations and stagger caps, entrances from at least
    85% scale, speed and acceleration by what moves (a rig part 30 px/frame and 8 px/frame² at 1080p, a UI element
    60 and 16, a carry 170 and 45), no loop faster than a 16-frame period, symmetric mark rigs; and a pixel
    cross-check that finds fast motion the trace doesn't explain.

    The film folder in, one GateResult out (slate.qa runs it with every other gate; `slate qa motion` alone). Inside a
    `run_options(...)` block it takes that run's options (some acts, an explicit render, --no-pixels, --only)."""
    opts = current_options()
    only = [o for o in opts.get("only") or [] if o in SUBCHECKS] or None
    acts = opts.get("acts")
    return evaluate(Path(film_dir), acts=acts, render=opts.get("render"),
                    render_act=acts[0] if opts.get("render") and acts else None, render_from=opts.get("render_from"),
                    pixels=opts.get("pixels", True), only=only)


GATES: dict[str, Callable[[Path], GateResult]] = {GATE: gate}
#: Gates here that read whole renders. Under `slate qa --quick` they reuse their cached scans and never start one.
SLOW = (GATE,)


# ---------------------------------------------------------------------------------------------------- run options
#: The options of one run of the motion or access gate (`slate qa motion --act 2 --no-pixels`). slate.qa.run calls
#: every gate with the film folder alone, so the options travel beside the call: `with run_options(...)` around the
#: run, and each gate reads the ones it takes. Outside such a block every gate checks the whole film.
_RUN_OPTIONS: contextvars.ContextVar[dict | None] = contextvars.ContextVar("slate_qa_gate_options", default=None)
OPTION_KEYS = ("acts", "render", "render_from", "pixels", "only", "captions", "sheet", "platforms")

#: The command-line flags for those options: flag -> (argparse dest, the gates that take it).
FLAGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "--act": ("act", ("motion", "access")),
    "--render": ("render", ("motion", "access")),
    "--render-from": ("render_from", ("motion", "access")),
    "--no-pixels": ("no_pixels", ("motion",)),
    "--only": ("only", ("motion", "access")),
    "--captions": ("captions", ("access",)),
    "--sheet": ("sheet", ("access",)),
    "--platform": ("platform", ("access",)),
}
OUR_GATES = ("motion", "access")


@contextlib.contextmanager
def run_options(**opts):
    """Give the motion and access gates these options for the run inside the block (see OPTION_KEYS)."""
    unknown = set(opts) - set(OPTION_KEYS)
    if unknown:
        raise TypeError(f"unknown gate options: {', '.join(sorted(unknown))}")
    token = _RUN_OPTIONS.set({k: v for k, v in opts.items() if v is not None and v != [] and v != ()})
    try:
        yield
    finally:
        _RUN_OPTIONS.reset(token)


def current_options() -> dict:
    """The options of the run in progress ({} outside a run_options block)."""
    return dict(_RUN_OPTIONS.get() or {})


def _subchecks(gate_name: str) -> tuple[str, ...]:
    if gate_name == GATE:
        return SUBCHECKS
    from slate.qa import access  # noqa: PLC0415 - access imports this module, so it's read at call time

    return access.SUBCHECKS


def add_qa_options(parser, gates: Iterable[str] = OUR_GATES) -> None:
    """Add the flags of the motion and access gates to a parser: qa-core's `slate qa` (both gates, one set of flags,
    so the shared ones are added once) or one gate's own command."""
    gates = tuple(gates)
    title = ("motion and access options (with `slate qa motion` or `slate qa access`)" if len(gates) > 1 else
             f"{gates[0]} options")
    g = parser.add_argument_group(title)
    both = len(gates) > 1

    def who(name: str) -> str:
        return f"{name}: " if both else ""

    if "motion" in gates:
        g.add_argument("--act", type=int, action="append", metavar="N",
                       help=f"{who('motion')}check only this act (repeat for more); with --render, the act the "
                            "render shows")
    else:
        g.add_argument("--act", type=int, action="append", metavar="N",
                       help="with --render: the act the render shows (sets its first film frame)")
    g.add_argument("--render", metavar="PATH",
                   help="read this render (a video, or a folder of frames) instead of the registered ones"
                        + (": motion cross-checks its pixels, access scans it for flashes" if both else ""))
    g.add_argument("--render-from", dest="render_from", type=int, metavar="FRAME",
                   help="with --render: the film frame of its first frame (default: the first frame of --act)")
    if "motion" in gates:
        g.add_argument("--no-pixels", dest="no_pixels", action="store_true",
                       help=f"{who('motion')}skip the pixel cross-check (reported as skipped, so the gate can't "
                            "pass without it)")
    names = [s for n in gates for s in _subchecks(n)]
    g.add_argument("--only", action="append", metavar="CHECK",
                   help=f"run only this sub-check (repeat for more): {', '.join(names)}. A narrowed run reports "
                        "what it finds but can't pass the gate")
    if "access" in gates:
        g.add_argument("--captions", metavar="PATH",
                       help=f"{who('access')}check this caption file (.srt, .vtt or captions.json)")
        g.add_argument("--sheet", metavar="PATH",
                       help=f"{who('access')}read the text for the safe zones from this contact sheet (the "
                            "engine's sheet with its index JSON, a folder of frames, or one frame)")
        g.add_argument("--platform", action="append", metavar="NAME",
                       help=f"{who('access')}check this platform's safe zones (repeat for more): instagram_reels, "
                            "tiktok, youtube_shorts")


def _given(args: argparse.Namespace) -> dict[str, Any]:
    out = {}
    for flag, (dest, _g) in FLAGS.items():
        v = getattr(args, dest, None)
        if v not in (None, False, []):
            out[flag] = v
    return out


def _exists(film_dir: Path | None, ref: str) -> bool:
    p = Path(ref).expanduser()
    return p.exists() or (not p.is_absolute() and film_dir is not None and (Path(film_dir) / ref).exists())


def options_from(args: argparse.Namespace, targets: Iterable[str], film_dir: Path | None = None) -> dict:
    """The run options in parsed arguments, checked against the gates this run checks (and the film, when given).
    A flag for a gate that isn't running, an unknown sub-check, an act the film doesn't have or a file that isn't
    there is a usage error (exit 2), before any gate runs."""
    given = _given(args)
    if not given:
        return {}
    names = [str(t) for t in targets]
    ours = [n for n in names if n in OUR_GATES]
    flags = ", ".join(given)
    if not names or len(ours) != len(names):
        raise UsageError(f"{flags} {'goes' if len(given) == 1 else 'go'} with `slate qa motion` or `slate qa access` "
                         "on their own, not with other gates.",
                         hint="slate qa motion --act 2  ·  slate qa access --only captions")
    for flag in given:
        takes = FLAGS[flag][1]
        if not any(n in takes for n in ours):
            raise UsageError(f"{flag} is {' and '.join(takes)}'s option; this run checks {', '.join(ours)}.")
    only = given.get("--only")
    if only:
        known = {n: set(_subchecks(n)) for n in ours}
        unknown = [o for o in only if not any(o in k for k in known.values())]
        if unknown:
            raise UsageError(f"There's no {', '.join(unknown)} check in {' or '.join(ours)}.",
                             hint="Checks: " + "; ".join(f"{n}: {', '.join(_subchecks(n))}" for n in ours) + ".")
        for n in ours:
            if not any(o in known[n] for o in only):
                raise UsageError(f"--only names none of {n}'s checks ({', '.join(_subchecks(n))}).")
    acts = given.get("--act")
    render = given.get("--render")
    if render and acts and len(acts) > 1:
        raise UsageError("--render reads one render, so give it one --act (the act it shows).")
    if acts and "motion" not in ours and not render:
        raise UsageError("`slate qa access` checks the whole film; --act only says which act a --render shows.",
                         hint="slate qa access --render take3.mov --act 2")
    if "--render-from" in given and not render:
        raise UsageError("--render-from says where a --render starts, so it needs --render.")
    for flag in ("--render", "--captions", "--sheet"):
        if flag in given and not _exists(film_dir, given[flag]):
            raise UsageError(f"There's nothing at {given[flag]} ({flag}).")
    if acts and film_dir is not None:
        check_acts(film_dir, acts)
    return {"acts": acts, "render": render, "render_from": given.get("--render-from"),
            "pixels": False if given.get("--no-pixels") else None, "only": only, "captions": given.get("--captions"),
            "sheet": given.get("--sheet"), "platforms": given.get("--platform")}


def check_acts(film_dir: Path, acts: Iterable[int]) -> None:
    """UsageError when the film has none of these acts (in timing/shots.json or as a motion trace)."""
    from slate.qa.film import context  # noqa: PLC0415

    ctx = context(film_dir)
    known = {int(a["index"]) for a in ctx.acts}
    folder = ctx.path(TRACE_DIR)
    if folder.is_dir():
        known |= {int(m.group(1)) for p in folder.glob("act*.json") if (m := re.fullmatch(r"act(\d+)\.json", p.name))}
    unknown = sorted(int(a) for a in acts if int(a) not in known)
    if unknown:
        listing = ", ".join(map(str, sorted(known))) or "none yet"
        raise UsageError(f"There's no act {', '.join(map(str, unknown))} in this film (acts: {listing}).")


@contextlib.contextmanager
def qa_options(args: argparse.Namespace, targets: Iterable[str]):
    """The hook for qa-core's `slate qa`: hand the motion and access flags given on the command line to those gates
    for the run inside the block. Refuses flags that don't fit the gates being run (UsageError, exit 2). With no such
    flags it changes nothing, so it can wrap every run.

        with qa_motion.qa_options(args, targets):
            return _run(targets, quick=...)
    """
    targets = list(targets)
    film_dir = None
    if _given(args):
        from slate import cli  # noqa: PLC0415

        try:
            film_dir = cli.film(args)
        except UsageError:
            film_dir = None  # the run itself says there's no film
    opts = options_from(args, targets, film_dir)
    with run_options(**opts):
        yield opts


def qa_option_argv(args: argparse.Namespace) -> list[str]:
    """The motion and access flags in `args` as command-line words (for a detached run's argv)."""
    out: list[str] = []
    for flag, v in _given(args).items():
        if v is True:
            out.append(flag)
        elif isinstance(v, list):
            for x in v:
                out += [flag, str(x)]
        else:
            out += [flag, str(v)]
    return out


# ---------------------------------------------------------------------------------------------------- CLI
def show(result: GateResult) -> str:
    """A result as people read it: the gate's line, then one line per sub-check."""
    lines = [f"{result.status:<9} {result.gate}  {result.summary}"]
    for c in (result.details or {}).get("checks") or []:
        lines.append(f"  {c.get('status', ''):<9} {c.get('check', ''):<11} {c.get('summary', '')}")
    return "\n".join(lines)


def run_command(gate_name: str, args: argparse.Namespace) -> int:
    """`slate qa motion` or `slate qa access` as a command of its own: check the options, run the one gate the way
    slate.qa.run does (recorded in qa/gates.json and qa/history.jsonl; an autonomous film parks on REVIEW), or only
    compute it with --dry-run, and print it."""
    from slate import cli, qa  # noqa: PLC0415
    from slate.qa import park  # noqa: PLC0415
    from slate.qa.film import session  # noqa: PLC0415

    film_dir = cli.film(args)
    opts = options_from(args, [gate_name], film_dir)
    dry = bool(cli.flag(args, "dry_run", False))
    with run_options(**opts):
        if dry:
            with session(film_dir):
                result = GATE_FUNCTIONS[gate_name]()(film_dir)
            results = [result]
        else:
            results = qa.run(film_dir, [gate_name])
    report_path = None if dry else Path(film_dir) / "qa" / "gates.json"
    parked = None if dry else park.status(Path(film_dir))
    human = ["\n".join(show(r) for r in results)]
    human.append("(dry run: qa/gates.json not updated)" if dry else f"recorded in {report_path}")
    if parked:
        human.append(f"PARKED: {parked.get('why')} Release: {parked.get('release')}")
    summary = gates.summarize(results)
    if summary["status"] != "PASS" and not dry:
        human.append("next: slate qa triage   (what isn't passing, with the next step for each)")
    log.result({"gates": [r.to_dict() for r in results], "summary": summary,
                "report": None if report_path is None else str(report_path), "dry_run": dry, "parked": parked,
                "options": {k: v for k, v in opts.items() if v is not None}}, "\n".join(human))
    return gates.exit_code(results)


def _gate_fn(name: str):
    def get():
        if name == GATE:
            return gate
        from slate.qa import access  # noqa: PLC0415

        return access.gate
    return get


GATE_FUNCTIONS = {"motion": _gate_fn("motion"), "access": _gate_fn("access")}


def _cmd(args: argparse.Namespace) -> int:
    return run_command(GATE, args)


HELP = "the motion law on every act's trace, cross-checked on the renders"
DESCRIPTION = """Check the motion law on every act's motion trace (timing/motion/actN.json) and cross-check it on
the renders. Sub-checks: trace, eases, durations, staggers, entrances, speed, loops, symmetry, pixels. The gate
passes only when every one does; REVIEW items wait for a person (`slate qa triage`).

Limits by what moves, at 1080p and scaled to the film's short side: a rig part 30 px/frame and 8 px/frame² (the
plan's law), a UI element 60 and 16, a page, card or camera carry 170 and 45 (a trace entry says which with
"class"; a whip past the carry limits goes to a person). Entrances from at least 85% scale, no loop faster than a
16-frame period (and never faster than 1.9 Hz), rig parts as whole-frame-delayed copies. Eases, durations, staggers
and overshoot come from the brand's motion data; rigs may also use sway and land, carries the even travel profile,
and a trace entry may declare a reading sweep or a steady line (exempt). A run narrowed with --act, --render or
--only reports what it finds but can't pass the gate."""


def register(sub) -> None:
    """Add `motion` to a subparsers object (a stage-style `qa` group: `slate qa motion`). qa-core's `slate qa` runs
    the same gate by name and takes these flags through add_qa_options and qa_options."""
    from slate.cli import add_command  # noqa: PLC0415

    p = add_command(sub, "motion", _cmd, help=HELP, description=DESCRIPTION)
    add_qa_options(p, gates=(GATE,))
