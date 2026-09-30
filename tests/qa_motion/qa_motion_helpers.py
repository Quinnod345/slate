"""Builders for the motion and access gate tests: a neutral brand law, trace entries sampled from real curves, film
folders in a scratch home, and synthetic frames. Every curve here is a standard one (CSS and easings.net); no brand's
motion lives in these tests."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from slate.motion.eases import evaluate, parse
from slate.qa import motion as M

# A neutral motion law in the shape brand packs use (the kit reads it with slate.motion.eases.law_from).
NEUTRAL_LAW: dict = {
    "eases": {
        "enter": {"bezier": [0.22, 1, 0.36, 1], "lineage": "easeOutQuint (easings.net)"},
        "move": {"bezier": [0.65, 0, 0.35, 1], "lineage": "easeInOutCubic (easings.net)"},
        "state": {"bezier": [0.87, 0, 0.13, 1], "lineage": "easeInOutExpo (easings.net)"},
        "leave": {"bezier": [0.32, 0, 0.67, 0], "lineage": "easeInCubic (easings.net)"},
        "sway": {"kind": "sine", "lineage": "sine in-out"},
        "pop": {"bezier": [0.34, 1.56, 0.64, 1], "lineage": "easeOutBack (easings.net)"},
    },
    "roles": {"entrance": "enter", "exit": "leave", "move": "move", "state": "state"},
    "durations": {"xs": 0.2, "s": 0.3, "m": 0.5, "l": 0.8, "xl": 1.2},
    "staggers": {"char": {"frames": 2, "at_fps": 30}, "word": {"frames": 3, "at_fps": 30},
                 "line": {"frames": 4, "at_fps": 30}, "block": {"frames": 6, "at_fps": 30}},
    "stagger_cap": {"seconds": 0.6, "fraction": 0.5, "max_items": 8},
    "exit_factor": 0.75,
    "overshoot": {"allowed": ["pop"], "max": 0.12},
}


def curve(name: str, law: dict = NEUTRAL_LAW) -> Callable[[float], float]:
    """A law ease as progress(u), evaluated exactly the way the kit does."""
    spec = parse(law["eases"][name])
    return lambda u: evaluate(spec, float(u))


def _lerp(a, b, p):
    if isinstance(a, (list, tuple)):
        return [x + (y - x) * p for x, y in zip(a, b)]
    return a + (b - a) * p


def entry(ident: str, prop: str, f0: float, f1: float, v0, v1, *, ease: str = "move", fn: Callable | None = None,
          step: float = 1.0, shot: str = "S1", entrance: bool = False, loop_period=None, rig=None, law: dict = NEUTRAL_LAW,
          **extra) -> dict:
    """One trace entry in the contract's shape, sampled from a curve (the named law ease unless `fn` is given)."""
    fn = fn or curve(ease, law)
    frames = np.arange(f0, f1 + 1e-9, step)
    samples = [[round(float(f), 6), _lerp(v0, v1, fn((f - f0) / (f1 - f0)))] for f in frames]
    out = {"id": ident, "shot": shot, "prop": prop, "ease": ease, "frame_in": f0, "frame_out": f1, "from": v0,
           "to": v1, "samples": samples, "entrance": entrance, "loop_period": loop_period, "rig": rig}
    out.update(extra)
    return out


def signal_entry(ident: str, prop: str, frames: Iterable[float], values: Iterable, *, ease: str = "sway",
                 shot: str = "S1", **extra) -> dict:
    """A trace entry from explicit samples (a wobble, a loop)."""
    fr = [float(f) for f in frames]
    vals = [v if isinstance(v, list) else float(v) for v in values]
    out = {"id": ident, "shot": shot, "prop": prop, "ease": ease, "frame_in": fr[0], "frame_out": fr[-1],
           "from": vals[0], "to": vals[-1], "samples": [[f, v] for f, v in zip(fr, vals)], "entrance": False,
           "loop_period": None, "rig": None}
    out.update(extra)
    return out


def data(entries: list[dict], *, fps: float | Fraction = 30, size=(1280, 720), law: dict | None = NEUTRAL_LAW,
         shots: dict | None = None, act: int = 1) -> M.MotionData:
    """MotionData for the pure checks, without a film folder."""
    fps = Fraction(fps).limit_denominator(100000) if not isinstance(fps, Fraction) else fps
    d = M.MotionData(fps=fps, size=size)
    if law is not None:
        d.law = M.law_from(law, source="test law", fps=float(fps))
    else:
        d.law_reason = "the test film has no brand"
    d.moves = [M.move_from(e, act, i) for i, e in enumerate(entries)]
    d.acts, d.traced = [act], [act]
    d.shots = shots or {}
    return d


def status(sub) -> str:
    return sub.status if sub is not None else "LEFT OUT"


# ---------------------------------------------------------------------------------------------------- film folders
def make_film(films: Path, *, film_id: str = "test-film", fps=(30, 1), size=(640, 360), acts=None,
              traces: dict | None = None, brand: str | None = "neutral-test", fmt: str = "brand-film",
              extra: dict | None = None) -> Path:
    """A film folder: film.json, timing/shots.json (one 60-frame shot per act unless `acts` says otherwise) and a
    motion trace per act in `traces`."""
    film = films / film_id
    (film / "timing" / "motion").mkdir(parents=True)
    acts = acts or {1: (0, 60)}
    doc = {"id": film_id, "title": "Test film", "format": fmt, "language": "en", "fps_num": fps[0], "fps_den": fps[1],
           "width": size[0], "height": size[1], "brand": brand, "mode": "supervised", **(extra or {})}
    (film / "film.json").write_text(json.dumps(doc))
    shots = []
    for a, (f_in, f_out) in sorted(acts.items()):
        shots.append({"id": f"S{a}", "act": a, "frame_in": f_in, "frame_out": f_out, "frames": f_out - f_in})
    total = max(f for _a, (_i, f) in acts.items())
    (film / "timing" / "shots.json").write_text(json.dumps({
        "fps_num": fps[0], "fps_den": fps[1], "width": size[0], "height": size[1], "total_frames": total,
        "acts": [{"index": a, "name": f"Act {a}", "shots": [f"S{a}"]} for a in sorted(acts)], "shots": shots}))
    for a, entries in (traces or {}).items():
        (film / "timing" / "motion" / f"act{a}.json").write_text(json.dumps(entries))
    return film


def register_render(film: Path, act: int, rel: str, frame_in: int, frame_out: int) -> None:
    reg = film / "renders" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(reg.read_text()) if reg.exists() else {"entries": []}
    doc["entries"].append({"act": act, "engine": "remotion", "file": rel, "frames": [frame_in, frame_out],
                           "samples": 4, "sha256": None, "made_at": "2026-09-30T12:00:00+00:00", "bundle_hash": None})
    reg.write_text(json.dumps(doc))


# ---------------------------------------------------------------------------------------------------- frames
def textured_square_frames(n: int, *, size=(320, 180), square: int = 48, start=(20, 66), velocity=(0.0, 0.0),
                           positions: list[tuple[float, float]] | None = None, seed: int = 3):
    """Frames of a blocky textured square on a plain grey ground, moving `velocity` px a frame (or through
    `positions`). Returns uint8 greyscale frames."""
    rng = np.random.default_rng(seed)
    cells = square // 8
    tex = np.where(rng.random((cells, cells)) > 0.5, 225, 35).astype(np.uint8).repeat(8, 0).repeat(8, 1)
    w, h = size
    out = []
    for k in range(n):
        x, y = positions[k] if positions else (start[0] + velocity[0] * k, start[1] + velocity[1] * k)
        frame = np.full((h, w), 110, np.uint8)
        xi, yi = int(round(x)), int(round(y))
        x0, y0 = max(0, xi), max(0, yi)
        x1, y1 = min(w, xi + tex.shape[1]), min(h, yi + tex.shape[0])
        if x1 > x0 and y1 > y0:
            frame[y0:y1, x0:x1] = tex[y0 - yi:y1 - yi, x0 - xi:x1 - xi]
        out.append(frame)
    return out


def write_frames(folder: Path, frames: Iterable[np.ndarray]) -> Path:
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    for k, f in enumerate(frames):
        Image.fromarray(np.asarray(f, dtype=np.uint8)).save(folder / f"f{k:05d}.png")
    return folder


def solid_frames(colours: list, size=(48, 27)) -> list[np.ndarray]:
    """One flat RGB frame per colour (r, g, b)."""
    w, h = size
    return [np.full((h, w, 3), c, np.uint8) for c in colours]
