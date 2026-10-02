"""Render stability: the frames a person would see shake, pop or flicker, found by measurement, triaged, then judged.

The films found these the hard way ("It's so jittery. Things are shaking up and down and jittering… weird shadow
things… There should be automated tests for that", on a film's sixth act, 2026-09-30). This module is the films'
stability tools generalized: any frame size, any rate, any act, and every verdict a GateResult.

What one scan finds, per tile and frame. A flag is (class, frame, tile) plus its measurements:

- glitch    the glitch gate's one- and two-frame outliers (slate.qa.glitch), each sorted by block matching into
            motion (a feature travelling: a shifted neighbour explains it), fade (a change in place with nothing
            moving), odd (the odd one out and not motion: a glitch candidate) and look (anything else). The glitch gate
            judges these frames; here they're categorized as evidence, so a person isn't asked about one frame twice.
- pop       a persistent step: something appearing, vanishing or jumping between two frames with no animation, and
            then holding, which the glitch scan can't see (it only sees blips). Per tile of the same small grey copy,
            step(n) = d(n-1, n) - max(d(n-2, n-1), d(n, n+1)). A step a shifted neighbour explains is motion, a small
            change in place is a fade, and the rest are pops. Frames where most tiles step at once are cuts.
- motion    per 30 px tile at full resolution, from 10-bit luma, the shift from the frame before to about 0.02 px (two
            passes of Lucas-Kanade on lightly blurred frames), trusted only where the tile has texture, moves under
            3 px a frame and the shift explains the change. From that:
              snap      a rigid, slow tile whose move is the odd one out by 0.45-1.6 px while the frames either side
                        agree: whole-pixel text snapping (a line steps 1 px while its card glides 0.03 px);
              relative  two neighbouring tiles that move as one object disagree on one frame (text against its card);
              shake     a slow tile reverses three or more times in seven frames, getting nowhere;
              reversal  a tile travelling one way takes a whole-pixel step back after whole-pixel steps forward, on at
                        least three tiles and not most of the frame: layout rounding under a move. A hard fail;
              wobble    a near-still tile steps about a pixel off its trend and back within four frames (a number
                        re-centring as its digits change width);
              flicker   a tile at rest that is the odd one out in time by 2 levels or more (a flash, a grey face), or
                        a faint one-frame veil over about 1% of the frame; shimmer: half a level, three frames running.
            The floors sit just above the finest placement a browser gives type without resampling it (a quarter
            pixel), so smooth type never reads as motion.

Triage, in order, so what's left is what a person has to look at:

1. Planned beats. A pop within ±3 frames of a planned beat is the film's own (an instant snap, a stamp, a type-on's
   first letter): it's skipped and listed, the glitch gate's rule kept exactly as it is. The motion classes are never
   skipped near a beat: a designed move never steps in whole pixels, and the films' worst snap sat on its own
   landing beat. When the film's format burns its captions into the picture, each caption cue coming and going is
   planned the same way (timing/captions.json: its start, its end, and a quarter second inside each, where the
   kit's settle commits), so a caption appearing on its word isn't a pop for a person to judge.
2. The reference. A flag a reference render of the act shares (same class, within 2 frames and one tile) belongs to
   the act's design or was there before. A reference rendered with one sample has no motion blur, so fast moves flag
   differently there: expect a few flags at such moves in the full render alone.
3. The baseline. Flags a person looked at (strips make it quick) and accepted, with `slate qa stability baseline`.
   A flag matching an accepted one (same class, within one frame and one tile) doesn't count, so the limits catch
   what's new. A baseline belongs to its act's content, not to one render: it survives re-renders of the same act.
4. Whatever's left counts against the limits (0 for every class unless slate.toml says otherwise). A reversal is a
   hard fail: no reference and no baseline excuses it.

Also here, each returning a GateResult: the static-frame regression (a re-render meant to change rendering only,
against the approved render), the morph check (every edge and the content of a morphing card against its true
geometry), the scan's own positive control (known faults injected into a still), the real-fault controls, and the
conformance procedure that runs them all against the engine kit's stability bench.

    from slate.qa import stability
    stability.GATES                      # {"stability": gate}: merged into slate.qa.GATES by the integrator
    stability.scan(path_or_frames)       # every flag in one render, categorized
    stability.triage(flags, beats=…, reference=…, baseline=…)
    stability.morph_check(frames, cards) / selftest(still) / controls(items) / conformance(spec, renders)
    stability.compare_renders(approved, new)  # the static-frame regression for a rendering-only change
    stability.explain_glitches(render, flags) # the glitch gate's flags, each categorized
    stability.register(sub)              # `slate qa stability [run|baseline|reference|strip|compare|selftest|…]`
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import math
import os
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
from scipy import ndimage as ndi

from slate import __version__, gates, jsonio, log, procs
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.gates import GateResult
from slate.qa import glitch as glitchmod
from slate.qa import media

GATE = "stability"

# ---------------------------------------------------------------------------------------------------- classes
GLITCH_CATEGORIES = ("motion", "fade", "odd", "look")
STEP_CATEGORIES = ("motion", "fade", "pop")
MOTION_CLASSES = ("snap", "relative", "shake", "reversal", "wobble", "flicker", "shimmer")
GATED = ("pop", *MOTION_CLASSES)  # what this gate judges; the glitch classes are the glitch gate's
HARD = ("reversal",)  # never excused by a reference or a baseline: always fixed
BEAT_TRIAGED = ("pop",)  # skipped within ±WINDOW frames of a planned beat, like the glitch gate's flags
COUNT_FRAMES = MOTION_CLASSES  # limits count distinct frames for these, flags for the rest
UNITS = {"pop": "levels", "snap": "px", "relative": "px", "shake": "px", "reversal": "px", "wobble": "px",
         "flicker": "levels", "shimmer": "levels"}
LIMITS = {c: 0 for c in GATED}  # the agreed bar for a final: nothing new

REF_FRAMES, REF_TILES = 2, 1  # a reference shares a flag within this
BASE_FRAMES, BASE_TILES = 1, 1  # a baseline accepts a flag within this

# ---------------------------------------------------------------------------------------------------- the small grey copy
SEARCH_PX = 36  # block matching reach at the 480 px analysis size (about 144 px at full HD)
EXPLAINED = 0.6  # a shifted neighbour that removes this much of the difference explains it
OPPOSITE_EXPLAINED = 0.4  # or both neighbours, shifted equal and opposite (the frame halfway between them)
ODD_RATIO = 0.6  # d(n-1, n+1) / min(d(n-1, n), d(n, n+1)) under this: the neighbours are alike, n is odd
FADE_LEVELS = 12.0  # a change in place this small, with nothing moving, is a fade
STEP_THRESHOLD = 4.0  # step score (levels) that flags a boundary
CUT_SHARE = 0.5  # most tiles stepping at once is a cut

# ---------------------------------------------------------------------------------------------------- the motion scan
TILE_PX = 30  # px at full resolution
SIGMA = 1.0  # pre-blur for the fit: extends its linear range and averages the codec's noise
EPS = 1e-3
TEX = 5.0  # mean squared gradient along a tile's strongest direction, (levels/px)², to be measured at all
FAST = 3.0  # px a frame: faster is blurred by the shutter anyway
CALM = 1.0  # levels: flicker counts only where frames n-2 and n+2 agree this well around the tile
VEIL_Q = 0.8  # levels: a one-frame change this faint counts as flicker over a large area
VEIL_SHARE = 0.0104  # that area: about 1% of the frame's tiles (24 tiles at full HD)
VEIL_MIN_TILES = 4
REV_WIN = 8  # frames of travel before a step back that make it a reversal
REV_STEADY = 0.7  # that travel's share of the path: one way, not back and forth
WOB_WIN = 4  # frames within which a step and its step back make a wobble
WOB_SLOW = 0.3  # px a frame: the trend under which a tile is near still
PARAMS = {"snap": 0.45, "relative": 0.35, "shake": 0.15, "flicker": 2.0, "shimmer": 0.5, "slow": 1.5}
SEGMENT = 600  # frames measured per piece (bounded memory; pieces run in parallel)
PAD = 16  # context either side of a piece, so a piece sees what a whole-file pass would

STRIP_LIMIT = 8  # strips written per run for the open flags (the worst first)
SCAN_DIR = "qa/stability/scan"
REFERENCES = "qa/stability/references.json"
VERSION = f"{__version__}+stability.1"


def baseline_rel(act: int) -> str:
    return f"qa/stability/baseline_act{act}.json"


def report_rel(act: int) -> str:
    return f"qa/stability/act{act}.json"


# ---------------------------------------------------------------------------------------------------- sources
def _luma709(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722).astype(np.float32)


class Source:
    """A render to scan: a video file (decoded through slate.qa.media) or frames already in memory.

    In memory, frames are an array (n, h, w) of luma on a 0-255 scale, or (n, h, w, 3) RGB. A file is read as
    10-bit-precise luma (gray16le) for the motion scan, and as the glitch gate's small grey copy for the glitch and
    step scans."""

    def __init__(self, src: Any, *, info: dict | None = None):
        self._info: dict | None = None
        if isinstance(src, Source):
            self.path, self.array, self._info = src.path, src.array, src._info
            return
        if isinstance(src, (str, os.PathLike)):
            self.path: Path | None = Path(src)
            self.array: np.ndarray | None = None
            self._info = info  # probed when first needed: counting a long render's frames reads the whole file
            return
        arr = np.asarray(src)
        if arr.ndim == 4:
            arr = _luma709(arr.astype(np.float32))
        if arr.ndim != 3:
            raise ValueError("frames in memory are (n, h, w) luma or (n, h, w, 3) RGB")
        self.path = None
        self.array = arr.astype(np.float32, copy=False)

    @property
    def info(self) -> dict | None:
        if self.array is None and self._info is None:
            self._info = media.video_info(self.path)
        return self._info

    @property
    def count(self) -> int:
        return int(self.array.shape[0]) if self.array is not None else int(self.info.get("frames") or 0)

    @property
    def size(self) -> tuple[int, int]:
        if self.array is not None:
            return int(self.array.shape[2]), int(self.array.shape[1])
        return int(self.info["width"]), int(self.info["height"])

    @property
    def analysis_size(self) -> tuple[int, int]:
        return media.analysis_size(*self.size, long_side=glitchmod.LONG_SIDE, tile=glitchmod.TILE)

    @property
    def name(self) -> str:
        return str(self.path) if self.path is not None else "(frames in memory)"

    def luma(self, start: int = 0, count: int | None = None) -> Iterator[np.ndarray]:
        """Full-resolution luma on a 0-255 scale, float32 (10-bit precise from a file)."""
        count = self.count - start if count is None else count
        if self.array is not None:
            for f in self.array[start:start + count]:
                yield f
            return
        scale = np.float32(255.0 / 65535.0)
        for f in media.iter_frames(self.path, start=start, count=count, pix_fmt="gray16le", info=self.info):
            yield f.astype(np.float32) * scale

    def _small(self, f: np.ndarray) -> np.ndarray:
        from PIL import Image  # noqa: PLC0415

        w, h = self.analysis_size
        if f.shape == (h, w):
            return f.astype(np.float32)
        return np.asarray(Image.fromarray(np.ascontiguousarray(f, dtype=np.float32)).resize((w, h), Image.BILINEAR),
                          dtype=np.float32)

    def analysis(self, start: int = 0, count: int | None = None) -> Iterator[np.ndarray]:
        """The glitch gate's small grey copy (480 px on the long side, whole 30 px tiles), float32."""
        count = self.count - start if count is None else count
        if self.array is not None:
            for f in self.array[start:start + count]:
                yield self._small(f)
            return
        for f in media.iter_frames(self.path, start=start, count=count, size=self.analysis_size, pix_fmt="gray",
                                   info=self.info):
            yield f.astype(np.float32)

    def analysis_frames(self, indices: Iterable[int]) -> dict[int, np.ndarray]:
        """Some of the small grey frames, as decoded (8-bit from a file; the matcher converts each as it uses it)."""
        wanted = sorted({int(i) for i in indices if 0 <= int(i) < self.count})
        if self.array is not None:
            return {i: self._small(self.array[i]) for i in wanted}
        return media.read_frames(self.path, wanted, size=self.analysis_size, pix_fmt="gray", info=self.info)

    def rgb_frames(self, indices: Iterable[int]) -> dict[int, np.ndarray]:
        wanted = sorted({int(i) for i in indices if 0 <= int(i) < self.count})
        if self.array is not None:
            return {i: np.repeat(np.clip(self.array[i], 0, 255).astype(np.uint8)[..., None], 3, axis=2) for i in wanted}
        return media.read_frames(self.path, wanted, pix_fmt="rgb24", info=self.info)


# ---------------------------------------------------------------------------------------------------- steps (pops)
class _Steps:
    """The step scan, streamed: it keeps only the last three tile-difference maps."""

    def __init__(self, threshold: float = STEP_THRESHOLD, tile: int = glitchmod.TILE):
        self.threshold, self.tile = threshold, tile
        self.prev: np.ndarray | None = None
        self.ds: list[np.ndarray] = []
        self.i = -1
        self.hits: list[dict] = []
        self.cuts: list[int] = []

    def push(self, f: np.ndarray) -> None:
        self.i += 1
        f = np.asarray(f, dtype=np.float32)
        if self.prev is not None:
            gy, gx = f.shape[0] // self.tile, f.shape[1] // self.tile
            self.ds.append(glitchmod._tiles(self.prev, f, gy, gx))  # d(i-1, i)
            if len(self.ds) > 3:
                self.ds.pop(0)
            if len(self.ds) == 3:
                # the boundary between frames i-2 and i-1, against the boundaries either side of it
                s = self.ds[1] - np.maximum(self.ds[0], self.ds[2])
                over = s > self.threshold
                if over.mean() > CUT_SHARE:
                    self.cuts.append(self.i - 1)
                else:
                    k = np.unravel_index(int(np.argmax(s)), s.shape)
                    if s[k] > self.threshold:
                        self.hits.append({"index": self.i - 1, "score": round(float(s[k]), 2),
                                          "tile": [int(k[1]), int(k[0])], "d": round(float(self.ds[1][k]), 2),
                                          "tiles": int(over.sum())})
        self.prev = f


def scan_steps(frames: Iterable[np.ndarray], *, threshold: float = STEP_THRESHOLD,
               tile: int = glitchmod.TILE) -> tuple[list[dict], list[int]]:
    """(steps, cuts) for a sequence of small grey frames. A step's `index` is the first frame after it."""
    s = _Steps(threshold, tile)
    for f in frames:
        s.push(f)
    return s.hits, s.cuts


# ---------------------------------------------------------------------------------------------------- block matching
def _window(tx: int, ty: int, w: int, h: int, tile: int) -> tuple[int, int, int, int]:
    return max(0, (tx - 1) * tile), max(0, (ty - 1) * tile), min(w, (tx + 2) * tile), min(h, (ty + 2) * tile)


def _best(ref: np.ndarray, other: np.ndarray, box: tuple[int, int, int, int],
          radius: int = SEARCH_PX) -> tuple[float, tuple[int, int], float]:
    """The shift of `other` that best explains ref's window: (residual, shift, residual unshifted). A coarse search
    every 4 px, then every pixel within 3 of the best."""
    x0, y0, x1, y1 = box
    a = ref[y0:y1, x0:x1]
    pad = np.pad(other, radius, mode="edge")

    def cost(dx: int, dy: int) -> float:
        return float(np.abs(a - pad[y0 + radius + dy:y1 + radius + dy, x0 + radius + dx:x1 + radius + dx]).mean())

    bc, bd = cost(0, 0), (0, 0)
    for dy in range(-radius, radius + 1, 4):
        for dx in range(-radius, radius + 1, 4):
            c = cost(dx, dy)
            if c < bc:
                bc, bd = c, (dx, dy)
    cx, cy = bd
    for dy in range(cy - 3, cy + 4):
        for dx in range(cx - 3, cx + 4):
            if abs(dx) <= radius and abs(dy) <= radius:
                c = cost(dx, dy)
                if c < bc:
                    bc, bd = c, (dx, dy)
    return bc, bd, cost(0, 0)


def _tile_d(a: np.ndarray, b: np.ndarray, tx: int, ty: int, tile: int) -> float:
    return float(np.abs(a - b)[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile].mean())


def between_share(a: np.ndarray, b: np.ndarray, c: np.ndarray, tx: int, ty: int, tile: int, eps: float = 1.0) -> float:
    """Of the tile's pixels that change around frame n (by more than `eps` levels), the share where n lies between its
    neighbours (within eps): high for a fade or smooth motion, low for a flash, where n is outside them both."""
    sl = (slice(ty * tile, (ty + 1) * tile), slice(tx * tile, (tx + 1) * tile))
    pa, pb, pc = a[sl], b[sl], c[sl]
    changed = (np.abs(pb - pa) > eps) | (np.abs(pc - pb) > eps)
    if not changed.any():
        return 1.0
    inside = (pb >= np.minimum(pa, pc) - eps) & (pb <= np.maximum(pa, pc) + eps)
    return float(inside[changed].mean())


def categorize_glitches(frames: dict[int, np.ndarray], flags: list[dict], *, tile: int = glitchmod.TILE,
                        radius: int = SEARCH_PX) -> list[dict]:
    """The glitch scan's flags, each with a category: motion, fade, odd or look. `frames` holds the small grey frames
    n-1, n and n+1 of each flag (keyed by the same index the flags use). A flag whose frames aren't all there is left
    out, as the films' triage did."""
    out = []
    for g in flags:
        n = int(g.get("index", g.get("local", 0)))
        if n - 1 not in frames or n not in frames or n + 1 not in frames:
            continue
        tx, ty = (int(v) for v in g["tile"])
        a, b, c = (np.asarray(frames[k], dtype=np.float32) for k in (n - 1, n, n + 1))
        h, w = b.shape
        d1, d2, d02 = _tile_d(a, b, tx, ty, tile), _tile_d(b, c, tx, ty, tile), _tile_d(a, c, tx, ty, tile)
        ratio = d02 / max(min(d1, d2), 1e-6)
        box = _window(tx, ty, w, h, tile)
        rp, sp, raw_p = _best(b, a, box, radius)
        rn, sn, raw_n = _best(b, c, box, radius)
        red_p, red_n = 1 - rp / max(raw_p, 1e-6), 1 - rn / max(raw_n, 1e-6)
        opposite = abs(sp[0] + sn[0]) <= 3 and abs(sp[1] + sn[1]) <= 3 and (abs(sp[0]) + abs(sp[1])) >= 2
        if (red_p >= EXPLAINED and red_n >= EXPLAINED) or (opposite and red_p >= OPPOSITE_EXPLAINED
                                                             and red_n >= OPPOSITE_EXPLAINED):
            cat = "motion"
        elif max(raw_p, raw_n) <= FADE_LEVELS and (abs(sp[0]) + abs(sp[1]) + abs(sn[0]) + abs(sn[1])) <= 4:
            cat = "fade"
        elif ratio < ODD_RATIO:
            cat = "odd"
        else:
            cat = "look"
        out.append({**g, "category": cat, "ratio": round(ratio, 2), "shift_prev": list(sp), "shift_next": list(sn),
                    "explained_prev": round(red_p, 2), "explained_next": round(red_n, 2),
                    "between": round(between_share(a, b, c, tx, ty, tile), 2)})
    return out


def categorize_steps(frames: dict[int, np.ndarray], steps: list[dict], *, tile: int = glitchmod.TILE,
                     radius: int = SEARCH_PX) -> list[dict]:
    """The step scan's steps, each with a category: motion (frame n-1, shifted, explains most of n in that window),
    fade (nothing moved and the change is small) or pop (something appeared, vanished or jumped: needs eyes)."""
    out = []
    for s in steps:
        n = int(s.get("index", s.get("local", 0)))
        if n - 1 not in frames or n not in frames:
            continue
        tx, ty = (int(v) for v in s["tile"])
        cur, before = np.asarray(frames[n], dtype=np.float32), np.asarray(frames[n - 1], dtype=np.float32)
        h, w = cur.shape
        r, sh, raw = _best(cur, before, _window(tx, ty, w, h, tile), radius)
        red = 1 - r / max(raw, 1e-6)
        if red >= EXPLAINED:
            cat = "motion"
        elif raw <= FADE_LEVELS and abs(sh[0]) + abs(sh[1]) <= 2:
            cat = "fade"
        else:
            cat = "pop"
        out.append({**s, "category": cat, "shift": list(sh), "explained": round(red, 2)})
    return out


CATEGORIZE_BATCH = 150  # flags categorized per read, so a long act's frames are never all in memory at once


def _in_batches(src: Source, flags: list[dict], fn, offsets: tuple[int, ...]) -> list[dict]:
    out: list[dict] = []
    ordered = sorted(flags, key=lambda f: int(f.get("index", f.get("local", 0))))
    for i in range(0, len(ordered), CATEGORIZE_BATCH):
        batch = ordered[i:i + CATEGORIZE_BATCH]
        frames = src.analysis_frames({int(f.get("index", f.get("local", 0))) + k for f in batch for k in offsets})
        out += fn(frames, batch)
    return out


def explain_glitches(render: Any, flags: list[dict]) -> list[dict]:
    """The glitch gate's flags for one render (each with its `local` frame in the file and its tile), each with a
    category: motion, fade, odd or look. Motion and fade are explained; odd and look need eyes. For the glitch gate
    to drop what block matching explains (most of a film's flags are fast motion)."""
    return _in_batches(Source(render), flags, categorize_glitches, (-1, 0, 1))


# ---------------------------------------------------------------------------------------------------- motion: measure
_GRID: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    g = _GRID.get((h, w))
    if g is None:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        _GRID.clear()  # one frame size at a time
        g = _GRID[(h, w)] = (yy, xx)
    return g


def _tile_sum(a: np.ndarray, t: int = TILE_PX) -> np.ndarray:
    h, w = a.shape
    return a[: h // t * t, : w // t * t].reshape(h // t, t, w // t, t).sum(axis=(1, 3))


def _expand(v: np.ndarray, t: int) -> np.ndarray:
    return np.repeat(np.repeat(v, t, 0), t, 1)


def _lk_tiles(A: np.ndarray, B: np.ndarray, t: int = TILE_PX, iters: int = 2):
    """Per-tile shift d (dx, dy) with B(x) ≈ A(x - d), the fit's residual, the raw change, the structure tensor's
    eigenvalues and its strong direction. A and B are pre-blurred and cropped to whole tiles. Two passes: the second
    re-measures after moving B back by the first pass's shifts."""
    gy, gx = np.gradient(A)
    Sxx, Syy, Sxy = _tile_sum(gx * gx, t), _tile_sum(gy * gy, t), _tile_sum(gx * gy, t)
    tr = Sxx + Syy
    det = Sxx * Syy - Sxy * Sxy
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    l1, l2 = tr / 2 + disc, tr / 2 - disc
    reg = 0.02 * tr + EPS  # a direction without texture is held near 0 rather than guessed
    a11, a22, a12 = Sxx + reg, Syy + reg, Sxy
    dd = a11 * a22 - a12 * a12
    H, W = Sxx.shape
    d = np.zeros((2, H, W), np.float32)
    yy, xx = _grid(H * t, W * t)
    Bw = B
    for k in range(iters):
        It = Bw - A
        bx, by = _tile_sum(gx * It, t), _tile_sum(gy * It, t)
        d[0] += -(a22 * bx - a12 * by) / dd
        d[1] += -(a11 * by - a12 * bx) / dd
        if k + 1 < iters:
            Bw = ndi.map_coordinates(B, [yy + _expand(d[1], t), xx + _expand(d[0], t)], order=1, mode="nearest")
    Bw = ndi.map_coordinates(B, [yy + _expand(d[1], t), xx + _expand(d[0], t)], order=1, mode="nearest")
    res = np.sqrt(_tile_sum((Bw - A) ** 2, t) / (t * t))
    raw = np.sqrt(_tile_sum((B - A) ** 2, t) / (t * t))
    ang = 0.5 * np.arctan2(2 * Sxy, Sxx - Syy)
    return d, res, raw, l1 / (t * t), l2 / (t * t), ang


def _warp(img: np.ndarray, d: np.ndarray, t: int = TILE_PX) -> np.ndarray:
    """img moved by per-tile shifts d: sampled at x - d, so content that moved by d lines up again."""
    yy, xx = _grid(img.shape[0], img.shape[1])
    return ndi.map_coordinates(img, [yy - _expand(d[1], t), xx - _expand(d[0], t)], order=1, mode="nearest")


class _Meter:
    """Per-frame motion and odd-one-out measurements over a run of consecutive frames (the films' chunk worker)."""

    def __init__(self, first: int, t: int = TILE_PX):
        self.first, self.t = first, t
        self.i = -1
        self.prev = self.prevb = self.prev2 = None
        self.d_prev = None
        self.hist: list[np.ndarray] = []
        self.out: dict[str, list] = {k: [] for k in ("frames", "d", "res", "raw", "l1", "l2", "ang", "mad")}
        self.d2: dict[int, np.ndarray] = {}
        self.agree: dict[int, np.ndarray] = {}
        self.wide: dict[int, np.ndarray] = {}

    def push(self, f: np.ndarray) -> None:
        t = self.t
        self.i += 1
        h, w = f.shape
        f = np.ascontiguousarray(f[: h // t * t, : w // t * t], dtype=np.float32)
        self.hist = (self.hist + [f])[-5:]
        if len(self.hist) == 5:
            # frames n-2 and n+2 against each other, as they are: still content leaves them alike, with or without a
            # flash or a shimmer on n; anything that moves or changes over those four frames doesn't
            self.wide[self.first + self.i - 2] = _tile_sum(np.abs(self.hist[4] - self.hist[0]), t) / t ** 2
        fb = ndi.gaussian_filter(f, SIGMA)
        d = None
        if self.prev is not None:
            d, res, raw, l1, l2, ang = _lk_tiles(self.prevb, fb, t)
            o = self.out
            o["frames"].append(self.first + self.i)
            o["d"].append(d)
            o["res"].append(res)
            o["raw"].append(raw)
            o["l1"].append(l1)
            o["l2"].append(l2)
            o["ang"].append(ang)
            o["mad"].append(_tile_sum(np.abs(f - self.prev), t) / t ** 2)
        if self.prev2 is not None:
            # frame i-1 as the odd one out between its neighbours, per pixel: how far it goes past the nearer
            # neighbour and back (0 for any one-way change). The neighbours are first moved onto it by the measured
            # shifts, so something sliding through lines up; a flash or a shimmer in place doesn't. The same is
            # measured without moving them, and the cleaner reading is kept.
            a = _warp(self.prev2, self.d_prev, t) if self.d_prev is not None else self.prev2
            c = _warp(f, -d, t)
            b = self.prev
            odd = np.maximum(0, np.minimum(np.abs(b - a), np.abs(b - c)) - np.abs(c - a) / 2)
            a0, c0 = self.prev2, f
            odd0 = np.maximum(0, np.minimum(np.abs(b - a0), np.abs(b - c0)) - np.abs(c0 - a0) / 2)
            q, q0 = _tile_sum(odd, t) / t ** 2, _tile_sum(odd0, t) / t ** 2
            g, g0 = _tile_sum(np.abs(c - a), t) / t ** 2, _tile_sum(np.abs(c0 - a0), t) / t ** 2
            use0 = g0 / np.maximum(q0, 1e-6) < g / np.maximum(q, 1e-6)
            fr = self.first + self.i - 1
            self.d2[fr] = np.where(use0, q0, q)
            self.agree[fr] = np.where(use0, g0, g)
        self.prev2, self.prev, self.prevb = self.prev, f, fb
        self.d_prev = d

    def result(self) -> dict:
        o = self.out
        if not o["frames"]:
            return {"frames": np.zeros(0, int), "d2": {}, "agree": {}, "wide": {}}
        return {"frames": np.array(o["frames"]), **{k: np.array(o[k]) for k in o if k != "frames"},
                "d2": self.d2, "agree": self.agree, "wide": self.wide}


def measure_motion(frames: Iterable[np.ndarray], first: int = 0, *, tile: int = TILE_PX) -> dict:
    """Per-tile motion for every frame after the first (each against the one before) and the odd-one-out readings."""
    m = _Meter(first, tile)
    for f in frames:
        m.push(f)
    return m.result()


# ---------------------------------------------------------------------------------------------------- motion: detect
def _along_texture(v: np.ndarray, l1: np.ndarray, l2: np.ndarray, ang: np.ndarray) -> np.ndarray:
    """The part of a displacement a tile can vouch for: all of it where it has texture both ways, else only its
    component along the strong direction (a straight edge says nothing about sliding along itself)."""
    c, s_ = np.cos(ang), np.sin(ang)
    par = v[:, 0] * c + v[:, 1] * s_
    one = l2 < 0.15 * l1
    out = v.copy()
    out[:, 0] = np.where(one, par * c, v[:, 0])
    out[:, 1] = np.where(one, par * s_, v[:, 1])
    return out


def analyse_motion(M: dict, *, params: dict | None = None, first_film_frame: int = 0, tile: int = TILE_PX) -> list[dict]:
    """The motion classes from one run's measurements. Each event: {class, local, frame, tile, region, severity, …},
    `local` being the frame in the file and `frame` the film frame (first_film_frame + local)."""
    p = {**PARAMS, **(params or {})}
    snap, relative, shake, flicker, shimmer, slow = (p[k] for k in ("snap", "relative", "shake", "flicker", "shimmer",
                                                                     "slow"))
    frames = M["frames"]
    if len(frames) == 0:
        return []
    d, res, raw, l1, l2, ang = M["d"], M["res"], M["raw"], M["l1"], M["l2"], M["ang"]
    N, _, H, W = d.shape
    mag = np.hypot(d[:, 0], d[:, 1])
    # trusted: textured, not too fast, and the shift explains the change (a textured tile that barely changes is
    # trusted as still: under a slow drift its change is mostly the codec's noise, which no shift explains)
    fit = (l1 >= TEX) & (mag <= FAST) & ((res <= 0.3 * raw) | (raw < 2.0))
    rigid = fit & ((res <= 0.15 * raw) | (raw < 0.3))  # the shift alone explains the change almost entirely
    two = l2 >= 0.15 * l1  # texture in both directions
    cut = (raw > 8).mean(axis=(1, 2)) > 0.5  # most tiles changing a lot at once
    cutmask = np.zeros(N, bool)
    for i in np.flatnonzero(cut):
        cutmask[max(0, i - 2): i + 3] = True

    med = np.full_like(d, np.nan)  # each tile's 7-frame median of trusted motion
    for c in range(2):
        x = np.where(fit, d[:, c], np.nan)
        pad = np.pad(x, ((3, 3), (0, 0), (0, 0)), mode="edge")
        stack = np.stack([pad[k: k + N] for k in range(7)], 0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            med[:, c] = np.nanmedian(stack, axis=0)
    medok = np.isfinite(med).all(axis=1)
    med0 = np.nan_to_num(med)
    speed = np.hypot(med0[:, 0], med0[:, 1])
    # what each frame's move should be if the motion is smooth: the least-squares quadratic through frames n±2 and
    # n±3, at n. Exact for any steady acceleration (an ease's tail scores 0); a spike on n doesn't touch it.
    pred = np.full_like(d, np.nan)
    dt = np.where(fit[:, None], d, np.nan)
    if N >= 7:
        m2 = (dt[1:-5] + dt[5:-1]) / 2
        m3 = (dt[0:-6] + dt[6:]) / 2
        pred[3:-3] = (9 * m2 - 4 * m3) / 5
    predok = np.isfinite(pred).all(axis=1)
    resid = _along_texture(d - np.nan_to_num(pred), l1, l2, ang)
    rmag = np.hypot(resid[:, 0], resid[:, 1])
    ok = rigid & medok & predok & (speed < slow) & ~cutmask[:, None, None]
    events: list[dict] = []

    def ev(kind: str, i: int, ty: int, tx: int, sev: float, **kw) -> None:
        fr = int(frames[i])
        e = {"class": kind, "local": fr, "frame": first_film_frame + fr, "tile": [int(tx), int(ty)],
             "region": [int(tx * tile), int(ty * tile), tile, tile], "severity": round(float(sev), 3)}
        e.update(kw)
        events.append(e)

    # snap: a trusted, slow tile whose move departs from the average of its neighbours' by snap … 1.6 px on one frame
    # (or split over two), while those neighbours agree with each other
    rok = np.where(ok, rmag, 0)
    dv = _along_texture(d, l1, l2, ang)
    dep = np.zeros_like(rmag)
    agree_n = np.full_like(rmag, np.inf)
    if N >= 3:
        dep[1:-1] = np.hypot(*(dv[1:-1] - (dv[:-2] + dv[2:]) / 2).transpose(1, 0, 2, 3))
        agree_n[1:-1] = np.hypot(*(dv[2:] - dv[:-2]).transpose(1, 0, 2, 3))
    for i in range(N):
        for ty, tx in np.argwhere((rok[i] >= snap) & (rok[i] <= 1.6) & (dep[i] >= snap) & (agree_n[i] < 0.5 * snap)):
            nb = [dep[j, ty, tx] for j in range(i - 2, i + 3) if 0 <= j < N and j != i]
            if nb and max(nb) > dep[i, ty, tx]:
                continue  # one flag per event: the largest departure within two frames
            ev("snap", i, ty, tx, rmag[i, ty, tx], dx=round(float(resid[i, 0, ty, tx]), 3),
               dy=round(float(resid[i, 1, ty, tx]), 3), speed=round(float(speed[i, ty, tx]), 3))
    for i in range(N - 1):
        s2 = resid[i] + resid[i + 1]
        same = (resid[i, 0] * resid[i + 1, 0] + resid[i, 1] * resid[i + 1, 1]) > 0
        both = ok[i] & ok[i + 1] & (rok[i] < snap) & (rok[i + 1] < snap) & same
        smag = np.where(both, np.hypot(s2[0], s2[1]), 0)
        for ty, tx in np.argwhere((smag >= snap * 1.4) & (smag <= 1.6)):
            nb = [rok[j, ty, tx] for j in (i - 2, i - 1, i + 2, i + 3) if 0 <= j < N]
            if nb and max(nb) >= snap:
                continue
            if 0 < i < N - 2 and np.hypot(*(dv[i + 2, :, ty, tx] - dv[i - 1, :, ty, tx])) >= 0.5 * snap:
                continue
            ev("snap", i + 1, ty, tx, smag[ty, tx], split=True, dx=round(float(s2[0, ty, tx]), 3),
               dy=round(float(s2[1, ty, tx]), 3), speed=round(float(speed[i, ty, tx]), 3))

    # relative: neighbouring tiles that move as one object (7-frame medians within 0.1 px a frame, texture both ways)
    # disagree on one frame by relative … 2 px, the frames either side agreeing
    for (oy, ox) in ((0, 1), (1, 0), (1, 1), (1, -1), (0, 2), (2, 0)):
        def sh(a, oy=oy, ox=ox):
            y1 = H - oy
            x0, x1 = max(0, -ox), W - max(0, ox)
            return a[..., 0:y1, x0:x1], a[..., oy:y1 + oy, x0 + ox:x1 + ox]
        (ra, rb), (ma, mb), (fa, fb2), (ta, tb), (da, db) = sh(resid), sh(med0), sh(ok), sh(two), sh(dv)
        same = np.abs(ma - mb).max(axis=1) < 0.1
        pair = fa & fb2 & same & ta & tb
        diff = np.where(pair, np.hypot(ra[:, 0] - rb[:, 0], ra[:, 1] - rb[:, 1]), 0)
        rel = da - db
        odd = np.zeros_like(diff)
        if N >= 3:
            either = np.hypot(*(rel[2:] - rel[:-2]).transpose(1, 0, 2, 3))
            depart = np.hypot(*(rel[1:-1] - (rel[:-2] + rel[2:]) / 2).transpose(1, 0, 2, 3))
            odd[1:-1] = np.where(either < 0.5 * relative, depart, 0)
        diff = np.where(pair, odd, 0)
        for i, yy, xx in np.argwhere((diff >= relative) & (diff <= 2.0)):
            ty, tx = yy, xx + max(0, -ox)
            ev("relative", i, ty, tx, diff[i, yy, xx], pair=[int(tx), int(ty), int(tx + ox), int(ty + oy)])

    # shake: on a slow tile, three or more reversals in 7 frames among trusted moves of shake … 1.5 px, going nowhere
    for c in range(2):
        x = np.where(ok, d[:, c], 0)
        sgn = np.sign(x) * ((np.abs(x) >= shake) & (np.abs(x) <= 1.5))
        for i in range(3, N - 3):
            win = sgn[i - 3: i + 4]
            flips = np.zeros((H, W), int)
            last = np.zeros((H, W))
            for k in range(7):
                s_ = win[k]
                flips += ((s_ != 0) & (last != 0) & (s_ != last)).astype(int)
                last = np.where(s_ != 0, s_, last)
            seg = x[i - 3: i + 4]
            path = np.abs(seg).sum(axis=0)
            net = np.abs(seg.sum(axis=0))
            hit = (flips >= 3) & (net < 0.3 * path) & ok[i]
            for ty, tx in np.argwhere(hit):
                ev("shake", i, ty, tx, path[ty, tx] / 7, axis="xy"[c], reversals=int(flips[ty, tx]))

    # reversal: a rigid tile travelling one way (≥ 1 px over the frames before, steadily) takes a whole-pixel step
    # back after at least one such step forward, on at least three tiles and not most of the frame (a camera's hit)
    for c in range(2 if N > REV_WIN else 0):
        net = np.zeros((N, H, W))
        path_c = np.zeros((N, H, W))
        for k in range(1, REV_WIN + 1):
            net[k:] += np.where(fit[:-k], d[:-k, c], 0)
            path_c[k:] += np.where(fit[:-k], np.abs(d[:-k, c]), 0)
        dep_c = d[:, c] - med0[:, c]
        okr = fit & medok & (speed < 3.0) & ~cutmask[:, None, None]
        moving_fit = np.maximum(fit.sum(axis=(1, 2)), 1)
        unit = okr & (np.abs(np.abs(dep_c) - 1.0) <= 0.25)
        lone = np.zeros_like(unit)
        lone[1:-1] = (np.abs(dep_c[:-2]) < 0.35) & (np.abs(dep_c[2:]) < 0.35)
        unit &= lone
        for i in range(REV_WIN, N):
            back = (unit[i] & (np.abs(net[i]) >= 1.0) & (np.abs(net[i]) >= REV_STEADY * path_c[i])
                    & (np.sign(dep_c[i]) == -np.sign(net[i])))
            fwd = np.zeros((H, W), bool)
            for k in range(1, REV_WIN + 1):
                fwd |= unit[i - k] & (np.sign(dep_c[i - k]) == np.sign(net[i])) & (np.sign(dep_c[i - k]) != 0)
            cand = back & fwd
            if int(cand.sum()) >= 3 and (okr[i] & (np.abs(dep_c[i]) >= 0.5)).sum() < 0.6 * moving_fit[i]:
                for ty, tx in np.argwhere(cand):
                    ev("reversal", i, ty, tx, abs(dep_c[i, ty, tx]), axis="xy"[c],
                       step=round(float(dep_c[i, ty, tx]), 3), net=round(float(net[i, ty, tx]), 3))

    # wobble: a near-still rigid tile steps about a whole pixel off its trend and back within WOB_WIN frames, the
    # frames between on the trend, on at least three tiles and not most of the frame. Flagged on the step back.
    for c in range(2):
        dep_c = d[:, c] - med0[:, c]
        okw = rigid & medok & (speed < WOB_SLOW) & ~cutmask[:, None, None]
        stepw = okw & (np.abs(dep_c) >= 0.65) & (np.abs(dep_c) <= 1.6)
        quiet = okw & (np.abs(dep_c) < 0.35)
        moving_fit = np.maximum(fit.sum(axis=(1, 2)), 1)
        for j in range(1, N):
            for k in range(1, WOB_WIN + 1):
                i = j - k
                if i < 0:
                    break
                cand = stepw[i] & stepw[j] & (np.sign(dep_c[i]) == -np.sign(dep_c[j]))
                for m in range(i + 1, j):
                    cand &= quiet[m]
                if int(cand.sum()) >= 3 and (fit[j] & (np.abs(dep_c[j]) >= 0.5)).sum() < 0.6 * moving_fit[j]:
                    for ty, tx in np.argwhere(cand):
                        ev("wobble", j, ty, tx, (abs(dep_c[i, ty, tx]) + abs(dep_c[j, ty, tx])) / 2, axis="xy"[c],
                           out=round(float(dep_c[i, ty, tx]), 3), back=round(float(dep_c[j, ty, tx]), 3), gap=k)

    # flicker and shimmer: a tile at rest that goes past both its neighbours in time and back
    D2 = M["d2"]
    veil_tiles = max(VEIL_MIN_TILES, int(round(VEIL_SHARE * H * W)))
    fidx = {int(f): i for i, f in enumerate(frames)}
    streak = np.zeros((H, W), int)
    for fr in sorted(D2):
        i = fidx.get(fr)
        j = fidx.get(fr + 1)
        if i is None or j is None or cutmask[i] or cutmask[j]:
            streak[:] = 0
            continue
        q = D2[fr]
        big = ndi.maximum_filter(np.maximum(M["mad"][i], M["mad"][j]), size=3) > 30  # glitch_scan's, not a flicker
        wide = M["wide"].get(fr)
        if wide is None:
            streak[:] = 0
            continue
        calm = ndi.maximum_filter(wide, size=3) <= np.maximum(CALM, 0.5 * q)
        still = (M["agree"][fr] <= 0.35 * q) & ~big & (q <= 8) & calm
        for ty, tx in np.argwhere(still & (q >= flicker)):
            ev("flicker", i, ty, tx, q[ty, tx], levels=True)
        veil = still & (q >= VEIL_Q) & (q < flicker)  # a faint one-frame change over a large area
        if veil.sum() >= veil_tiles:
            lab, nlab = ndi.label(veil | (still & (q >= flicker)), structure=np.ones((3, 3)))
            sizes = ndi.sum(np.ones_like(q), lab, range(1, nlab + 1))
            for k, sz in enumerate(np.atleast_1d(sizes), 1):
                if sz >= veil_tiles:
                    for ty, tx in np.argwhere((lab == k) & veil):
                        ev("flicker", i, ty, tx, q[ty, tx], levels=True, veil=int(sz))
        streak = np.where(still & (q >= shimmer), streak + 1, 0)
        for ty, tx in np.argwhere(streak == 3):
            ev("shimmer", i, ty, tx, q[ty, tx], levels=True)
    return events


def _segment_events(task: tuple) -> list[dict]:
    """One piece of a motion scan (a process-pool worker): measure frames lo..hi-1, keep the events in a..b-1.
    A file piece is decoded from frame lo; an in-memory piece arrives already sliced to lo..hi-1."""
    src, info, lo, hi, a, b, params, first_film_frame = task
    if isinstance(src, np.ndarray):
        frames = Source(src).luma(0, hi - lo)
    else:
        frames = Source(src, info=info).luma(lo, hi - lo)
    M = measure_motion(frames, first=lo)
    return [e for e in analyse_motion(M, params=params, first_film_frame=first_film_frame) if a <= e["local"] < b]


def default_jobs() -> int:
    """Worker processes for the motion scan: slate.toml [qa.stability] jobs, else a quarter of the cores (at most 4),
    so a scan leaves the Mac usable."""
    try:
        from slate import config  # noqa: PLC0415

        jobs = ((config.load().get("qa") or {}).get("stability") or {}).get("jobs")
        if jobs:
            return max(1, int(jobs))
    except Exception:  # noqa: BLE001 - a broken settings file falls back to the default
        pass
    return max(1, min(4, (os.cpu_count() or 2) // 4))


def scan_motion(source: Any, *, start: int = 0, count: int | None = None, first_film_frame: int = 0,
                params: dict | None = None, jobs: int | None = 1, segment: int = SEGMENT, pad: int = PAD) -> list[dict]:
    """The motion classes for a render, in pieces of `segment` frames with `pad` frames of context either side (the
    same events a single pass gives, with bounded memory). jobs > 1 measures pieces in parallel."""
    src = Source(source)
    end = src.count if count is None else min(src.count, start + count)
    if end - start < 3:
        return []
    jobs = default_jobs() if jobs is None else max(1, int(jobs))
    pieces = [(a, min(a + segment, end)) for a in range(start, end, segment)]
    tasks = []
    for a, b in pieces:
        lo, hi = max(start, a - pad), min(end, b + pad)
        if src.array is not None:
            tasks.append((src.array[lo:hi], None, lo, hi, a, b, params, first_film_frame))
        else:
            tasks.append((str(src.path), src.info, lo, hi, a, b, params, first_film_frame))
    if jobs == 1 or len(tasks) == 1:
        results = [_segment_events(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as ex:
            results = list(ex.map(_segment_events, tasks))
    return [e for part in results for e in part]


def summarize_motion(events: list[dict]) -> dict:
    out = {}
    for k in MOTION_CLASSES:
        E = [e for e in events if e["class"] == k]
        out[k] = {"flags": len(E), "frames": len({e["local"] for e in E}),
                  "max": max((e["severity"] for e in E), default=0.0)}
    return out


# ---------------------------------------------------------------------------------------------------- one render
def scan(source: Any, *, first_film_frame: int = 0, count: int | None = None, params: dict | None = None,
         jobs: int | None = 1) -> dict:
    """Every flag in one render, categorized: {frames, flags, cuts, glitch, steps, motion}. Each flag has `class`,
    `local` (frame in the file), `frame` (film frame), `tile` and `tile_px` (a tile's size at full resolution)."""
    src = Source(source)
    n = src.count if count is None else min(int(count), src.count)
    aw, _ah = src.analysis_size
    small_tile_px = glitchmod.TILE * src.size[0] / aw
    steps = _Steps()

    def feed() -> Iterator[np.ndarray]:
        for f in src.analysis(0, n):
            steps.push(f)
            yield f

    glitches = glitchmod.scan_frames(feed(), threshold=glitchmod.THRESHOLD, tile=glitchmod.TILE)
    g_cat = _in_batches(src, glitches, categorize_glitches, (-1, 0, 1))
    s_cat = _in_batches(src, steps.hits, categorize_steps, (-1, 0))
    flags: list[dict] = []
    for g in g_cat:
        flags.append({"class": f"glitch_{g['category']}", "local": g["index"], "frame": first_film_frame + g["index"],
                      "tile": g["tile"], "tile_px": round(small_tile_px, 2), "severity": g["score"], "len": g["len"],
                      "ratio": g["ratio"], "explained": [g["explained_prev"], g["explained_next"]]})
    for s in s_cat:
        cls = "pop" if s["category"] == "pop" else f"step_{s['category']}"
        flags.append({"class": cls, "local": s["index"], "frame": first_film_frame + s["index"], "tile": s["tile"],
                      "tile_px": round(small_tile_px, 2), "severity": s["score"], "tiles": s["tiles"],
                      "explained": s["explained"]})
    motion = scan_motion(src, count=n, first_film_frame=first_film_frame, params=params, jobs=jobs)
    for e in motion:
        flags.append({**e, "tile_px": TILE_PX})
    flags.sort(key=lambda f: (f["local"], f["class"], f["tile"][1], f["tile"][0]))
    return {"frames": n, "size": list(src.size), "flags": flags, "cuts": steps.cuts,
            "glitch": {"flags": len(glitches), "categories": {c: sum(1 for g in g_cat if g["category"] == c)
                                                              for c in GLITCH_CATEGORIES}},
            "steps": {"steps": len(steps.hits), "categories": {c: sum(1 for s in s_cat if s["category"] == c)
                                                               for c in STEP_CATEGORIES}},
            "motion": summarize_motion(motion)}


# ---------------------------------------------------------------------------------------------------- triage
def _index(flags: Iterable[dict]) -> dict[str, set]:
    idx: dict[str, set] = {}
    for f in flags:
        idx.setdefault(str(f["class"]), set()).add((int(f["local"]), int(f["tile"][0]), int(f["tile"][1])))
    return idx


def _near(flag: dict, idx: dict[str, set], df: int, dt: int) -> bool:
    pts = idx.get(str(flag["class"]))
    if not pts:
        return False
    f, (x, y) = int(flag["local"]), (int(flag["tile"][0]), int(flag["tile"][1]))
    return any((f + a, x + b, y + c) in pts for a in range(-df, df + 1) for b in range(-dt, dt + 1)
               for c in range(-dt, dt + 1))


def triage(flags: list[dict], *, beats: Iterable[int] = (), reference: list[dict] | None = None,
           baseline: list[dict] | None = None, window: int = glitchmod.WINDOW, beat_classes: Iterable[str] = BEAT_TRIAGED,
           hard: Iterable[str] = HARD) -> dict:
    """Sort flags into near_beats, shared_with_reference, accepted_by_baseline and open (in that order). Beats are
    film frames; the reference's and the baseline's flags are matched by class, local frame and tile. A hard class
    is never excused by a reference or a baseline."""
    beat_classes, hard = set(beat_classes), set(hard)
    beat_set = [f for f in flags if f["class"] in beat_classes]
    rest = [f for f in flags if f["class"] not in beat_classes]
    remaining, near_beats = glitchmod.triage(beat_set, beats, window=window) if beat_set else ([], [])
    remaining = rest + remaining
    ref_idx = _index(reference or [])
    base_idx = _index(baseline or [])
    shared, accepted, open_ = [], [], []
    for f in remaining:
        if f["class"] not in hard and reference and _near(f, ref_idx, REF_FRAMES, REF_TILES):
            shared.append(f)
        elif f["class"] not in hard and baseline and _near(f, base_idx, BASE_FRAMES, BASE_TILES):
            accepted.append(f)
        else:
            open_.append(f)
    key = lambda f: (f["local"], f["class"], f["tile"][1], f["tile"][0])  # noqa: E731
    return {"near_beats": sorted(near_beats, key=key), "shared_with_reference": sorted(shared, key=key),
            "accepted_by_baseline": sorted(accepted, key=key), "open": sorted(open_, key=key)}


CAPTION_SETTLE_S = 0.25  # a burned-in caption settles in and out over the brand's shortest step, under this


def caption_changes(ctx) -> list[int]:
    """Film frames where a burned-in caption comes or goes: each cue's start and end, and CAPTION_SETTLE_S inside
    each, from the film's captions (timing/captions.json first). Only when the film's format burns captions into the
    picture (captions.burn_in); otherwise nothing on screen changes at a cue."""
    from slate.qa import textmatch  # noqa: PLC0415

    fmt = ctx.format() if callable(getattr(ctx, "format", None)) else None
    cap = fmt.get("captions") if isinstance(fmt, dict) else None
    if not (isinstance(cap, dict) and cap.get("burn_in")) or ctx.clock is None:
        return []
    path = ctx.captions_path()
    if path is None:
        return []
    try:
        cues = textmatch.load_cues(path)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out: set[int] = set()
    for c in cues:
        start, end = float(c["start"]), float(c["end"])
        settle = min(CAPTION_SETTLE_S, max(0.0, (end - start) / 2))
        out.update(ctx.frame_of(t) for t in (start, start + settle, end - settle, end))
    return sorted(out)


def counts(flags: Iterable[dict], classes: Iterable[str] = GATED) -> dict[str, int]:
    """What counts against the limits: distinct frames for the motion classes, flags for the rest."""
    flags = list(flags)
    out = {}
    for c in classes:
        own = [f for f in flags if f["class"] == c]
        out[c] = len({f["local"] for f in own}) if c in COUNT_FRAMES else len(own)
    return out


def limits() -> dict[str, int]:
    """LIMITS, with slate.toml [qa.stability.limits] over them (a class's allowance of new frames or flags)."""
    out = dict(LIMITS)
    try:
        from slate import config  # noqa: PLC0415

        extra = (((config.load().get("qa") or {}).get("stability") or {}).get("limits") or {})
        out.update({str(k): int(v) for k, v in extra.items() if str(k) in out})
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------------------------------- baselines
def load_baseline(film_dir: Path, act: int) -> dict:
    return jsonio.read(Path(film_dir) / baseline_rel(act), default={}) or {}


def baseline_flags(film_dir: Path, act: int) -> list[dict]:
    out = []
    for row in load_baseline(film_dir, act).get("flags") or []:
        try:
            cls, local, tile = row[0], int(row[1]), [int(row[2][0]), int(row[2][1])]
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if cls not in HARD:
            out.append({"class": cls, "local": local, "tile": tile})
    return out


def accept_baseline(film_dir: Path, act: int, *, by: str | None, reason: str | None, mode: str = "supervised") -> dict:
    """Accept the act's open flags from its latest stability report into its baseline (never a reversal). The report
    must describe the act's current render: a person accepts what they looked at."""
    film_dir = Path(film_dir)
    if not (by or "").strip() or not (reason or "").strip():
        raise UsageError("Accepting flags into a baseline needs the person's name and their reason: --by NAME "
                         "--reason TEXT (and say why in the act's NOTES.md).")
    if mode == "autonomous" and procs.agent_context():
        raise Refused("This film is autonomous, so its stability flags wait for a person: an agent can't accept them "
                      "for itself.", hint="Accept from your own Terminal after looking at the strips.")
    rep = jsonio.read(film_dir / report_rel(act), default=None)
    if not isinstance(rep, dict):
        raise UsageError(f"There's no stability report for act {act} yet.", hint=f"Run `slate qa stability --act {act}`.")
    render = film_dir / str(rep.get("render") or "")
    if not render.is_file() or jsonio.sha256(render) != rep.get("sha256"):
        raise Refused(f"Act {act}'s stability report doesn't describe its current render (it was re-rendered since).",
                      hint=f"Run `slate qa stability --act {act}`, look at the new flags, then accept them.")
    new = [f for f in rep.get("open") or [] if f.get("class") not in HARD]
    hard_left = [f for f in rep.get("open") or [] if f.get("class") in HARD]
    data = load_baseline(film_dir, act)
    rows = list(data.get("flags") or [])
    have = {(r[0], int(r[1]), int(r[2][0]), int(r[2][1])) for r in rows if isinstance(r, list) and len(r) == 3}
    added = 0
    for f in new:
        k = (f["class"], int(f["local"]), int(f["tile"][0]), int(f["tile"][1]))
        if k not in have:
            rows.append([f["class"], int(f["local"]), [int(f["tile"][0]), int(f["tile"][1])]])
            have.add(k)
            added += 1
    record = {"by": by.strip(), "reason": reason.strip(), "at": gates.now_iso(), "render": rep.get("render"),
              "sha256": rep.get("sha256"), "added": added, "agent": procs.agent_context()}
    data.update({"note": "Stability flags a person looked at and accepted for this act (slate qa stability baseline). "
                         "A flag matching one of these (same class, within one frame and one tile) doesn't count. "
                         "It belongs to the act's content: rewrite it only after triaging, when the act changes.",
                 "act": act, "flags": rows})
    data.setdefault("accepted", []).append(record)
    jsonio.write_atomic(film_dir / baseline_rel(act), data)
    return {**record, "act": act, "baseline": baseline_rel(act), "total": len(rows),
            "reversals_left_out": len(hard_left)}


def drop_from_baseline(film_dir: Path, act: int, frames: tuple[int, int], *, classes: Iterable[str] = (),
                       by: str | None, reason: str | None, mode: str = "supervised") -> dict:
    """Take accepted flags back out of an act's baseline: those in a range of film frames (and of some classes). The
    films did this before re-rendering a fix (B4's 664 snap, its dashes' re-spacing), so the new render proves the fix
    instead of the baseline hiding the fault. Needs the person's name and reason, like accepting."""
    film_dir = Path(film_dir)
    if not (by or "").strip() or not (reason or "").strip():
        raise UsageError("Taking flags out of a baseline needs the person's name and their reason: --by NAME "
                         "--reason TEXT.")
    if mode == "autonomous" and procs.agent_context():
        raise Refused("This film is autonomous, so its baseline is a person's to change.",
                      hint="Change it from your own Terminal.")
    data = load_baseline(film_dir, act)
    rows = list(data.get("flags") or [])
    if not rows:
        raise UsageError(f"Act {act} has no baseline yet, so there's nothing to take out.")
    rep = jsonio.read(film_dir / report_rel(act), default=None) or {}
    if rep.get("first_film_frame") is None:
        raise UsageError(f"There's no stability report for act {act}, so its film frames can't be placed.",
                         hint=f"Run `slate qa stability --act {act}` first.")
    first = int(rep["first_film_frame"])
    lo, hi = sorted((int(frames[0]), int(frames[1])))
    wanted = {str(c) for c in classes}
    keep, dropped = [], []
    for r in rows:
        try:
            film_frame = first + int(r[1])
        except (TypeError, ValueError, IndexError):
            keep.append(r)
            continue
        if lo <= film_frame <= hi and (not wanted or r[0] in wanted):
            dropped.append(r)
        else:
            keep.append(r)
    record = {"by": by.strip(), "reason": reason.strip(), "at": gates.now_iso(), "frames": [lo, hi],
              "classes": sorted(wanted), "dropped": len(dropped), "agent": procs.agent_context()}
    data["flags"] = keep
    data.setdefault("dropped", []).append({**record, "flags": dropped})
    jsonio.write_atomic(film_dir / baseline_rel(act), data)
    return {**record, "act": act, "baseline": baseline_rel(act), "total": len(keep)}


# ---------------------------------------------------------------------------------------------------- references
def set_reference(film_dir: Path, act: int, file: str | Path, *, by: str | None = None, why: str | None = None) -> dict:
    film_dir = Path(film_dir)
    p = Path(file).expanduser()
    full = p if p.is_absolute() else (Path.cwd() / p)
    if not full.is_file():
        raise UsageError(f"No such render: {file}")
    try:
        stored = str(full.resolve().relative_to(film_dir.resolve()))
    except ValueError:
        stored = str(full.resolve())
    data = jsonio.read(film_dir / REFERENCES, default={}) or {}
    entry = {"file": stored, "by": by, "why": why, "at": gates.now_iso()}
    data.setdefault("acts", {})[str(act)] = entry
    data["note"] = ("The render each act's stability flags are compared with (a flag it shares belongs to the design "
                    "or was there before). Without one, the act's previous registered render is used.")
    jsonio.write_atomic(film_dir / REFERENCES, data)
    return {"act": act, **entry}


def reference_for(ctx, act: dict, current: dict) -> tuple[Path | None, str]:
    """The reference render for an act: the one a person set, else the act's previous whole-act registered render."""
    data = jsonio.read(ctx.path(REFERENCES), default={}) or {}
    explicit = (data.get("acts") or {}).get(str(act["index"]))
    if explicit and explicit.get("file"):
        p = ctx.path(explicit["file"])
        if p.is_file():
            return p, f"set by {explicit.get('by') or 'a person'}" + (f": {explicit['why']}" if explicit.get("why") else "")
        return None, f"the reference set for act {act['index']} ({explicit['file']}) isn't there"
    best = None
    for e in ctx.render_entries():
        try:
            same_act = int(e.get("act")) == int(act["index"])
        except (TypeError, ValueError):
            continue
        if not same_act or e["frame_in"] != act["frame_in"] or e["frame_out"] != act["frame_out"]:
            continue
        if e["path"] == current["path"] or not e["path"].is_file() or e["order"] >= current.get("order", 1 << 30):
            continue
        best = e
    if best is None:
        return None, "no earlier render of this act to compare with"
    return best["path"], f"the act's previous render ({ctx.rel(best['path'])})"


# ---------------------------------------------------------------------------------------------------- caching
def _cache_key(ctx, path: Path, first_film_frame: int, count: int, jobs: int | None) -> dict:
    return {"file": ctx.rel(path), "sha256": ctx.sha(path), "film_frame": first_film_frame, "count": count,
            "version": VERSION, "params": PARAMS, "tile_px": TILE_PX, "glitch_threshold": glitchmod.THRESHOLD,
            "step_threshold": STEP_THRESHOLD}


def scan_cached(ctx, name: str, path: Path, *, first_film_frame: int, count: int, quick: bool = False,
                jobs: int | None = None, source: Source | None = None) -> dict | None:
    """scan() for a film's render, cached in qa/stability/scan/<name>.json against the file's hash and the scan's
    settings. With quick, a missing or stale cache returns None instead of starting a scan."""
    key = _cache_key(ctx, path, first_film_frame, count, jobs)
    cache = ctx.path(f"{SCAN_DIR}/{name}.json")
    cached = jsonio.read(cache, default=None) if cache.is_file() else None
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached["scan"]
    if quick:
        return None
    log.info(f"stability: scanning {ctx.rel(path)} ({count} frames)")
    result = scan(source or path, first_film_frame=first_film_frame, count=count, jobs=jobs)
    jsonio.write_atomic(cache, {"generated": "do not edit by hand: slate qa stability", "key": key, "scan": result})
    return result


# ---------------------------------------------------------------------------------------------------- strips
def _font(size: int):
    from PIL import ImageFont  # noqa: PLC0415

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an older Pillow without sized default fonts
        return ImageFont.load_default()


def strip(out: str | Path, rows: list[tuple[str, Any, int]], first: int, count: int, box: tuple[int, int, int, int],
          *, zoom: int | None = None, guides: Iterable[float] = (), title: str = "") -> Path:
    """Before/after strips: the same crop from consecutive frames of one or more renders, enlarged with
    nearest-neighbour pixels (so a whole-pixel step shows as a step), one row per render, each frame labelled with
    its local frame. `rows` are (label, render, offset), offset being the render's frame of local frame 0. Guides draw
    a fixed horizontal line (crop-space y) across every frame, so a line of type stepping against it shows."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    x, y, w, h = (int(v) for v in box)
    z = int(zoom or max(1, round(180 / max(1, h))))
    cw, ch = w * z, h * z
    pad, lab, top = 6, 160, 40 if title else 8
    grabbed = []
    for label, render, offset in rows:
        src = Source(render)
        idx = [first + offset + i for i in range(count)]
        frames = src.rgb_frames(idx)
        grabbed.append((label, [frames.get(i) for i in idx]))
    width = lab + count * (cw + pad) + pad
    height = top + len(grabbed) * (ch + pad + 26) + pad
    sheet = Image.new("RGB", (width, height), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    font, big = _font(16), _font(20)
    if title:
        d.text((pad, 8), title, font=big, fill=(240, 240, 240))
    for r, (label, frames) in enumerate(grabbed):
        y0 = top + r * (ch + pad + 26)
        d.text((pad, y0 + ch // 2), label, font=big, fill=(230, 230, 230))
        for i, f in enumerate(frames):
            x0 = lab + i * (cw + pad)
            d.text((x0 + 2, y0), f"{first + i}", font=font, fill=(200, 200, 200))
            if f is None:
                continue
            crop = f[max(0, y):y + h, max(0, x):x + w]
            im = Image.fromarray(np.ascontiguousarray(crop)).resize((max(1, crop.shape[1] * z), max(1, crop.shape[0] * z)),
                                                                     Image.NEAREST)
            di = ImageDraw.Draw(im)
            for g in guides:
                gy = int(round(float(g) * z))
                di.line([(0, gy), (im.width, gy)], fill=(255, 40, 40), width=1)
            sheet.paste(im, (x0, y0 + 22))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def flag_box(flag: dict, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """A crop around a flag's tile: three tiles wide and high, inside the frame."""
    t = float(flag.get("tile_px") or TILE_PX)
    cx, cy = (flag["tile"][0] + 0.5) * t, (flag["tile"][1] + 0.5) * t
    side = int(round(3 * t))
    x0 = int(min(max(0, cx - side / 2), max(0, size[0] - side)))
    y0 = int(min(max(0, cy - side / 2), max(0, size[1] - side)))
    return x0, y0, min(side, size[0]), min(side, size[1])


# ---------------------------------------------------------------------------------------------------- run options
_OPTIONS: contextvars.ContextVar[dict] = contextvars.ContextVar("slate_qa_stability_options", default={})


@contextlib.contextmanager
def run_options(**opts) -> Iterator[None]:
    """Options for the gate during one run: acts=[…] narrows it (a narrowed run can't pass), jobs=N, strips=False."""
    token = _OPTIONS.set({k: v for k, v in opts.items() if v is not None})
    try:
        yield
    finally:
        _OPTIONS.reset(token)


def current_options() -> dict:
    return dict(_OPTIONS.get())


# ---------------------------------------------------------------------------------------------------- the gate
def _engine() -> dict:
    return gates.engine("slate stability scan", VERSION, decoder=f"ffmpeg {media.version('ffmpeg')}",
                        motion_tile_px=TILE_PX, analysis_long_side=glitchmod.LONG_SIDE, params=PARAMS,
                        beat_window_frames=glitchmod.WINDOW)


def _item(ctx, act: int, cls: str, frame: int, flags: list[dict], basis: dict, strip_rel: str | None) -> dict:
    worst = max(flags, key=lambda f: f.get("severity", 0.0))
    tiles = ", ".join(f"x{f['tile'][0]} y{f['tile'][1]}" for f in flags[:4]) + (" …" if len(flags) > 4 else "")
    what = (f"film frame {frame} ({ctx.shot_of_frame(frame)}, act {act}, local {worst['local']}): {cls} on "
            f"{len(flags)} tile{'s' if len(flags) != 1 else ''}, worst {worst.get('severity')} "
            f"{UNITS.get(cls, '')} at {tiles}" + (f" (strip {strip_rel})" if strip_rel else ""))
    return {"key": f"{cls}:frame:{frame}", "what": what, "basis": basis, "frame": frame, "class": cls,
            "act": act, "severity": worst.get("severity"), "strip": strip_rel}


def gate(film_dir: Path) -> GateResult:
    """Render stability on every act's render: pops and unstable motion (snaps, parts disagreeing, shaking, whole-pixel
    reversals, wobbles, flicker and shimmer), triaged against planned beats, a reference render and the act's baseline.

    What's left is REVIEW for a person (strips in qa/stability/strips/); a whole-pixel reversal is a FAIL that no
    baseline excuses. The glitch gate's flags are categorized here (motion, fade, odd, look) as evidence."""
    from slate.qa.checks import Sub, combine, plural, review_items  # noqa: PLC0415
    from slate.qa.film import context  # noqa: PLC0415

    ctx = context(film_dir)
    opts = current_options()
    engine = _engine()
    if not ctx.render_entries():
        return GateResult.unchecked(GATE, "no act renders are registered yet", evidence=["next: slate render"],
                                    engine=engine, inputs=ctx.inputs([ctx.REGISTRY]))
    if not media.have("ffmpeg") or ctx.size is None:
        why = "ffmpeg isn't installed" if not media.have("ffmpeg") else "film.json has no width / height"
        return GateResult.unchecked(GATE, f"{why}, so the renders can't be scanned", engine=engine,
                                    inputs=ctx.inputs([ctx.REGISTRY, "film.json"]))
    wanted = set(int(a) for a in opts.get("acts") or [])
    acts = [a for a in ctx.acts if not wanted or a["index"] in wanted]
    unknown = sorted(wanted - {a["index"] for a in ctx.acts})
    if unknown:
        raise UsageError(f"The film has no act {', '.join(map(str, unknown))}.")
    current = ctx.current_renders()
    beats = [b["frame"] for b in ctx.beats()] + caption_changes(ctx)
    lim = limits()
    jobs = opts.get("jobs")
    read: list[Any] = ["film.json", ctx.REGISTRY, ctx.SHOTS, "timing/timeline.json", REFERENCES,
                       *[ctx.events_rel(a["index"]) for a in ctx.acts]]
    missing, unscanned, problems, notes = [], [], [], []
    items: list[dict] = []
    hard: list[str] = []
    per_act: dict[str, dict] = {}
    strips_left = STRIP_LIMIT if opts.get("strips", True) else 0
    for a in acts:
        act = a["index"]
        e = current.get(act)
        if e is None or not e["path"].is_file():
            missing.append(act)
            continue
        count = int(e["frame_out"] - e["frame_in"])
        read += [e["path"], baseline_rel(act)]
        act_src = Source(e["path"])  # one probe of the file, shared by the scan and the strips
        try:
            raw = scan_cached(ctx, f"act{act}", e["path"], first_film_frame=e["frame_in"], count=count,
                              quick=ctx.quick, jobs=jobs, source=act_src)
        except SlateError as exc:
            problems.append(f"act {act}: {exc}")
            continue
        if raw is None:
            unscanned.append(act)
            continue
        ref_path, ref_why = reference_for(ctx, a, e)
        ref_src = Source(ref_path) if ref_path is not None else None
        ref_flags = None
        if ref_path is not None:
            read.append(ref_path)
            try:
                ref = scan_cached(ctx, f"reference_act{act}", ref_path, first_film_frame=e["frame_in"], count=count,
                                  quick=ctx.quick, jobs=jobs, source=ref_src)
            except SlateError as exc:
                ref, ref_why = None, f"the reference couldn't be read ({exc})"
            if ref is None:
                notes.append(f"act {act}: the reference isn't scanned yet (--quick); compared without it")
            else:
                ref_flags = ref["flags"]
        tri = triage(raw["flags"], beats=beats, reference=ref_flags, baseline=baseline_flags(ctx.dir, act))
        open_gated = [f for f in tri["open"] if f["class"] in GATED]
        cnt = counts(open_gated)
        over = [c for c in GATED if c not in HARD and cnt[c] > lim.get(c, 0)]
        basis = ctx.inputs([e["path"]])
        groups: dict[tuple[str, int], list[dict]] = {}
        for f in open_gated:
            if f["class"] in over or f["class"] in HARD:
                groups.setdefault((f["class"], int(f["frame"])), []).append(f)
        act_items = []
        for (cls, frame), fl in sorted(groups.items(), key=lambda kv: -max(f.get("severity", 0) for f in kv[1])):
            strip_rel = None
            if strips_left > 0:
                try:
                    worst = max(fl, key=lambda f: f.get("severity", 0.0))
                    out = ctx.path(f"qa/stability/strips/act{act}_{cls}_{frame}.png")
                    rows = [("this render", act_src, 0)] + ([("reference", ref_src, 0)] if ref_src else [])
                    first = max(0, int(worst["local"]) - 3)
                    strip(out, rows, first, 7, flag_box(worst, ctx.size),
                          title=f"act {act}, film frame {frame}: {cls}")
                    strip_rel = ctx.rel(out)
                    strips_left -= 1
                except (SlateError, OSError, ValueError) as exc:
                    notes.append(f"act {act}: a strip couldn't be made ({exc})")
            it = _item(ctx, act, cls, frame, fl, basis, strip_rel)
            if cls in HARD:
                hard.append(it["what"])
            else:
                act_items.append(it)
        items += act_items
        rep = {"generated": "do not edit by hand: slate qa stability", "act": act, "render": ctx.rel(e["path"]),
               "sha256": ctx.sha(e["path"]), "first_film_frame": e["frame_in"], "frames": raw["frames"],
               "reference": {"file": ctx.rel(ref_path), "why": ref_why} if ref_path else {"file": None, "why": ref_why},
               "baseline": baseline_rel(act) if ctx.exists(baseline_rel(act)) else None, "limits": lim, "counts": cnt,
               "over": over, "glitch": raw["glitch"], "steps": raw["steps"], "motion": raw["motion"],
               "cuts": raw["cuts"], "open": tri["open"], "near_beats": tri["near_beats"],
               "shared_with_reference": tri["shared_with_reference"],
               "accepted_by_baseline": tri["accepted_by_baseline"]}
        jsonio.write_atomic(ctx.path(report_rel(act)), rep)
        per_act[str(act)] = {"report": report_rel(act), "counts": cnt, "over": over, "glitch": raw["glitch"],
                             "near_beats": len(tri["near_beats"]),
                             "shared_with_reference": len(tri["shared_with_reference"]),
                             "accepted_by_baseline": len(tri["accepted_by_baseline"]),
                             "reference": rep["reference"]}
    open_items, accepted = review_items(ctx, GATE, items)
    subs = []
    if problems:
        subs.append(Sub("unreadable", "UNCHECKED", problems[0], problems))
    if missing:
        subs.append(Sub("missing", "UNCHECKED", f"acts {', '.join(map(str, missing))} have no whole-act render to scan",
                        ["next: slate render --act N"]))
    if unscanned:
        subs.append(Sub("unscanned", "UNCHECKED", f"acts {', '.join(map(str, unscanned))} haven't been scanned yet "
                        "(--quick never starts a scan)", ["next: slate qa stability"]))
    if hard:
        subs.append(Sub("reversal", "FAIL", f"{plural(len(hard), 'frame')} with a whole-pixel step back near rest "
                        "(layout rounding under a move): always fixed, never accepted", hard[:40]))
    if open_items:
        subs.append(Sub("flags", "REVIEW", f"{plural(len(open_items), 'frame')} with new stability flags to look at",
                        [i["what"] for i in open_items[:40]]))
    elif per_act:
        totals = {k: sum(v[k] for v in per_act.values()) for k in ("near_beats", "shared_with_reference",
                                                                     "accepted_by_baseline")}
        extra = f", {len(accepted)} accepted by a person" if accepted else ""
        subs.append(Sub("flags", "PASS", f"nothing new in {plural(len(per_act), 'act')} ({totals['near_beats']} near "
                        f"planned beats, {totals['shared_with_reference']} shared with the reference, "
                        f"{totals['accepted_by_baseline']} in baselines{extra})",
                        [v["report"] for v in per_act.values()]))
    if wanted:
        subs.append(Sub("scope", "UNCHECKED", f"this run checked only act{'s' if len(wanted) > 1 else ''} "
                        f"{', '.join(map(str, sorted(wanted)))}, so it can't pass the gate",
                        [f"next: slate qa {GATE}   (every act)"]))
    return combine(GATE, subs, engine=engine, inputs=ctx.inputs(read),
                   details={"acts": per_act, "review_items": open_items[:500], "accepted": accepted,
                            "limits": lim, "notes": notes, "stage": "render", **({"narrowed": sorted(wanted)} if wanted else {})})


GATES = {GATE: gate}


# ---------------------------------------------------------------------------------------------------- crispness
def crispness(a: np.ndarray) -> float:
    """The mean of the top 0.5% gradient magnitudes: how sharp the sharpest edges are."""
    gy, gx = np.gradient(np.asarray(a, dtype=np.float64))
    g = np.sort(np.hypot(gx, gy).ravel())
    return float(g[-max(1, len(g) // 200):].mean())


def psnr(a: np.ndarray, b: np.ndarray, peak: float = 255.0) -> float:
    m = float(((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2).mean())
    return 99.0 if m == 0 else 10 * math.log10(peak ** 2 / m)


def blurred_psnr(a: np.ndarray, b: np.ndarray, sigma: float = 2.5) -> float:
    """PSNR after a blur on both: raster phase and sub-pixel placement wash out; a moved card or a missing shadow
    doesn't."""
    return psnr(ndi.gaussian_filter(np.asarray(a, np.float64), sigma), ndi.gaussian_filter(np.asarray(b, np.float64), sigma))


# ---------------------------------------------------------------------------------------------------- regression
REGRESSION = {"every": 3, "struct_levels": 40.0, "blur_px": 2.5, "soft_pct": 5.0, "max_struct": 0, "max_soft": 0,
              "crisp_tile_px": 4 * TILE_PX, "edge_floor": 40.0, "still_levels": 3.0}


def _tile_crisp(a: np.ndarray, t: int) -> np.ndarray:
    """Per tile of t px: the mean of its top 0.5% gradient magnitudes."""
    gy, gx = np.gradient(np.asarray(a, dtype=np.float32))
    g = np.hypot(gx, gy)
    h, w = g.shape
    tiles = g[: h // t * t, : w // t * t].reshape(h // t, t, w // t, t).transpose(0, 2, 1, 3).reshape(h // t, w // t, -1)
    k = max(1, t * t // 200)
    return np.sort(tiles, axis=2)[:, :, -k:].mean(axis=2)


def compare_renders(approved: Any, new: Any, *, offset: int = 0, rest_only: bool = False,
                    ignore: Iterable[tuple] = (), rules: dict | None = None) -> GateResult:
    """The static-frame regression: a new render of an act against its approved render, for a change meant to be
    rendering only (a kit fix, a pipeline change). It proves nothing moved, went missing, doubled, wrapped or changed
    order, and that nothing got softer where it rests. Every third frame, as 10-bit luma:

      structure  after a 2.5 px blur on both, no pixel differs by more than 40 levels. The blur washes out raster
                 phase and sub-pixel placement (a line of the darkest type moved a whole pixel stays under about 32),
                 while a word missing, doubled or wrapped, a card drawn in another order or a shadow gone shows 100
                 and more. With rest_only, only where the approved render holds still (a one-sample reference has no
                 motion blur, so anything moving differs).
      rest       where both hold still, every tile with real edges at least 95% as crisp as it was.
      motion     the same crispness over moving frames, reported, not failed.
      length     whole renders (offset 0) have the same number of frames.

    offset: the new render's frame of the approved render's frame 0 (a window of an act: minus its first frame).
    ignore: (x, y, w, h) regions, or (x, y, w, h, first, last) on the approved render's frames only: a change that is
    meant, left out while everything else is still checked."""
    from slate.qa.checks import Sub, combine  # noqa: PLC0415

    r = {**REGRESSION, **(rules or {})}
    A, N = Source(approved), Source(new)
    na, nb = A.count, N.count
    first, last = max(0, -offset), min(na, nb - offset)
    t, every = int(r["crisp_tile_px"]), int(r["every"])
    regions = [tuple(int(v) for v in g) + ((None, None) if len(g) == 4 else ()) for g in ignore]
    struct, rest, motion = [], [], []
    prev = prevb = None
    if last > first:
        for k, (fa, fb) in enumerate(zip(A.luma(first, last - first), N.luma(first + offset, last - first))):
            f = first + k
            if k % every:
                continue
            h, w = fa.shape
            keep = np.ones((h, w), bool)
            tkeep = np.ones((h // t, w // t), bool)
            for x, y, rw, rh, f0, f1 in regions:
                if f0 is not None and not f0 <= f <= f1:
                    continue
                keep[max(0, y):y + rh, max(0, x):x + rw] = False
                tkeep[max(0, y) // t:(y + rh + t - 1) // t, max(0, x) // t:(x + rw + t - 1) // t] = False
            still = (prev is not None and float(np.abs(fa - prev).max()) < r["still_levels"]
                     and float(np.abs(fb - prevb).max()) < r["still_levels"])
            D = np.abs(ndi.gaussian_filter(fa, r["blur_px"]) - ndi.gaussian_filter(fb, r["blur_px"])) * keep
            over = int((D > r["struct_levels"]).sum())
            if over and (still or not rest_only):
                yy, xx = np.unravel_index(int(np.argmax(D)), D.shape)
                struct.append({"frame": f, "pixels": over, "max": round(float(D.max()), 1), "at": [int(xx), int(yy)]})
            ca, cb = _tile_crisp(fa, t), _tile_crisp(fb, t)
            for ty, tx in np.argwhere((ca > r["edge_floor"]) & tkeep):
                (rest if still else motion).append((float(cb[ty, tx] / ca[ty, tx] - 1), f, int(tx), int(ty)))
            prev, prevb = fa, fb
    soft = sorted(x for x in rest if x[0] < -r["soft_pct"] / 100)
    subs = []
    if last <= first:
        subs.append(Sub("frames", "UNCHECKED", "the two renders share no frames at this offset"))
    if struct:
        s0 = struct[0]
        subs.append(Sub("structure", "FAIL" if len(struct) > r["max_struct"] else "PASS",
                        f"{len(struct)} frame(s) over {r['struct_levels']:g} levels after a {r['blur_px']:g} px blur "
                        f"(first at frame {s0['frame']}: {s0['max']} at x{s0['at'][0]} y{s0['at'][1]})",
                        [f"frame {s['frame']}: {s['max']} at {s['at'][0]},{s['at'][1]} ({s['pixels']} px)" for s in struct[:12]]))
    elif last > first:
        subs.append(Sub("structure", "PASS", "nothing moved, went missing, doubled or changed order"
                        + (" where the reference holds still" if rest_only else "")))
    n_soft = len({(f, tx, ty) for _, f, tx, ty in soft})
    if rest:
        worst = min(x[0] for x in rest)
        subs.append(Sub("rest", "FAIL" if n_soft > r["max_soft"] else "PASS",
                        f"at rest: {len(rest)} edge tiles, {n_soft} more than {r['soft_pct']:g}% softer (worst "
                        f"{100 * worst:+.1f}%)", [f"frame {f}, tile x{tx * t} y{ty * t}: {100 * c:+.1f}%" for c, f, tx, ty in soft[:12]]))
    if motion:
        mr = np.array([x[0] for x in motion])
        subs.append(Sub("motion", "PASS", f"in motion (reported only): {len(motion)} edge tiles, crispness median "
                        f"{100 * float(np.median(mr)):+.1f}%, 5th percentile {100 * float(np.percentile(mr, 5)):+.1f}%"))
    if offset == 0 and na != nb:
        subs.append(Sub("length", "FAIL", f"the renders differ in length: approved {na} frames, new {nb}"))
    return combine("stability.regression", subs, engine=gates.engine("slate stability regression", VERSION, rules=r),
                   inputs={}, details={"frames": [na, nb], "offset": offset, "structure": struct[:50],
                                       "too_soft": [{"frame": f, "tile": [tx * t, ty * t, t, t], "change_pct": round(100 * c, 1)}
                                                    for c, f, tx, ty in soft[:50]]})


# ---------------------------------------------------------------------------------------------------- morph check
def _crossing(p: np.ndarray, inward: bool) -> float:
    """Where a profile from inside a face outward (or reversed) crosses halfway from the face's level to the
    furthest level within 4 px past the edge. Works for a face lighter or darker than what's outside it."""
    p = np.asarray(p, dtype=np.float64)
    if not inward:
        p = p[::-1]
    if p[-3:].mean() > p[:2].mean():  # a dark face on a light ground: measure the mirror image
        p = -p
    face = p[:2].mean()
    below = np.flatnonzero(p < face - 3)
    if not len(below):
        return math.nan
    k0 = int(below[0])
    after = p[k0:k0 + 4].min()
    level = (face + after) / 2
    for k in range(len(p) - 1):
        if p[k] >= level > p[k + 1]:
            pos = k + (p[k] - level) / (p[k] - p[k + 1]) + 0.5
            return pos if inward else len(p) - pos
    return math.nan


def _shift(a: np.ndarray, b: np.ndarray, iters: int = 8, sigma: float = 0.8) -> np.ndarray:
    """How far crop b's content moved from crop a's (dx, dy), by iterative Lucas-Kanade, Hann-weighted so content
    entering or leaving the crop's edges doesn't pull the answer (the films' close-up probe; exact to a few
    thousandths of a pixel on a shifted texture, where an unweighted fit drifts a tenth of a pixel at 4 px)."""
    a = ndi.gaussian_filter(np.asarray(a, np.float64), sigma)
    b = ndi.gaussian_filter(np.asarray(b, np.float64), sigma)
    gy, gx = np.gradient(a)
    w = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    m = np.array([[(w * gx * gx).sum(), (w * gx * gy).sum()], [(w * gx * gy).sum(), (w * gy * gy).sum()]])
    d = np.zeros(2)  # b moved back by -d lines up with a
    for _ in range(iters):
        e = ndi.shift(b, (d[1], d[0]), order=3, mode="nearest") - a
        try:
            step = np.linalg.solve(m, -np.array([(w * gx * e).sum(), (w * gy * e).sum()]))
        except np.linalg.LinAlgError:
            break
        d = d - step
        if np.abs(step).max() < 1e-4:
            break
    return -d


MORPH_RULES = {"spread_px": 0.8, "jump_px": 0.5, "of_control": 0.6, "content_px": 0.1, "control_jump_px": 0.8}


def _frames_of(frames: Any) -> Iterator[np.ndarray]:
    """Frames from an array (n, h, w), a Source, or a render's path, one at a time."""
    if isinstance(frames, np.ndarray):
        return iter(frames)
    if isinstance(frames, (Source, str, os.PathLike)):
        return Source(frames).luma()
    return iter(frames)


def morph_edges(frames: Any, rects: list[list[float]], content: list[int] | None = None,
                truth: list[list[float]] | None = None) -> dict:
    """One card's edges and content against its true geometry, frame by frame. frames: an array (n, h, w), a Source
    or a render; rects: the true [left, top, right, bottom] per frame (sub-pixel); content: [x0, y0, x1, y1], a
    textured box inside the card on frame 0; truth: the content's true shift per frame (default: the card's
    top-left)."""
    err: dict[str, list[float]] = {k: [] for k in ("top", "bottom", "left", "right")}
    cont: list[float] = []
    l0, t0 = rects[0][0], rects[0][1]
    first_crop = None
    for f, a in enumerate(_frames_of(frames)):
        if f >= len(rects):
            break
        a = np.asarray(a, dtype=np.float64)
        left, top, right, bottom = (float(v) for v in rects[f])
        cx, cy = (left + right) / 2, (top + bottom) / 2
        hw, hh = max(4, int(0.2 * (right - left))), max(4, int(0.2 * (bottom - top)))
        xs, ys = slice(int(cx) - hw, int(cx) + hw), slice(int(cy) - hh, int(cy) + hh)
        y0 = int(bottom) - 6
        err["bottom"].append(y0 + _crossing(a[y0:y0 + 12, xs].mean(axis=1), True) - bottom)
        y0 = int(top) - 6
        err["top"].append(y0 + _crossing(a[y0:y0 + 12, xs].mean(axis=1), False) - top)
        x0 = int(right) - 6
        err["right"].append(x0 + _crossing(a[ys, x0:x0 + 12].mean(axis=0), True) - right)
        x0 = int(left) - 6
        err["left"].append(x0 + _crossing(a[ys, x0:x0 + 12].mean(axis=0), False) - left)
        if content is not None:
            bx0, by0, bx1, by1 = (int(v) for v in content)
            if first_crop is None:
                first_crop = a[by0:by1, bx0:bx1].copy()
            d = _shift(first_crop, a[by0:by1, bx0:bx1]) if f else np.zeros(2)
            tx, ty = (truth[f] if truth is not None else (left - l0, top - t0))
            cont.append(float(np.hypot(d[0] - tx, d[1] - ty)))
    spread = {k: float(np.nanmax(v) - np.nanmin(v)) for k, v in err.items()}
    jump = {k: float(np.nanmax(np.abs(np.diff(v)))) if len(v) > 1 else 0.0 for k, v in err.items()}
    return {"spread_px": spread, "jump_px": jump, "worst_jump_px": max(jump.values()),
            "worst_spread_px": max(spread.values()), "content_px": max(cont) if cont else None}


def morph_check(frames: Any, cards: list[dict], rules: dict | None = None) -> GateResult:
    """A morphing card keeps every edge and its content on their true places. cards: [{name, role: subject|control|
    note, rects, content?, truth?}] over the same frames (luma, 0-255). What must hold:
    - each subject: no edge jumps half a pixel or more from one frame to the next, nor more than 60% of the control's
      worst jump; every edge within 0.8 px of a constant offset from its true place; the content within 0.1 px of
      its true place;
    - the control (drawn the old way): its edges step whole pixels (a jump of at least 0.8 px), or the check has gone
      blind. Notes are reported only."""
    from slate.qa.checks import Sub, combine  # noqa: PLC0415

    r = {**MORPH_RULES, **(rules or {})}
    measured = {c["name"]: morph_edges(frames, c["rects"], c.get("content"), c.get("truth")) for c in cards}
    control = next((c for c in cards if c.get("role") == "control"), None)
    control_worst = measured[control["name"]]["worst_jump_px"] if control else None
    subs = []
    for c in cards:
        m = measured[c["name"]]
        line = (", ".join(f"{k} {m['spread_px'][k]:.2f}/{m['jump_px'][k]:.2f}" for k in m["jump_px"])
                + (f"; content within {m['content_px']:.2f} px" if m["content_px"] is not None else ""))
        role = c.get("role", "subject")
        if role == "control":
            ok = m["worst_jump_px"] >= r["control_jump_px"]
            said = "steps whole pixels" if ok else "no longer steps whole pixels: the check has gone blind"
            subs.append(Sub(f"control:{c['name']}", "PASS" if ok else "FAIL",
                            f"{c['name']}, the control, {said} (edges spread/largest jump, px: {line})", [line]))
        elif role == "subject":
            fails = []
            if m["worst_spread_px"] > r["spread_px"]:
                fails.append(f"an edge wanders {m['worst_spread_px']:.2f} px (≤ {r['spread_px']})")
            if m["worst_jump_px"] >= r["jump_px"]:
                fails.append(f"an edge jumps {m['worst_jump_px']:.2f} px (< {r['jump_px']})")
            if control_worst is not None and m["worst_jump_px"] > r["of_control"] * control_worst:
                fails.append(f"its worst jump is over {r['of_control']:.0%} of the control's ({control_worst:.2f} px)")
            if m["content_px"] is not None and m["content_px"] > r["content_px"]:
                fails.append(f"the content strays {m['content_px']:.2f} px (≤ {r['content_px']})")
            subs.append(Sub(f"subject:{c['name']}", "FAIL" if fails else "PASS",
                            f"{c['name']}: " + ("; ".join(fails) if fails else "every edge and the content on their true "
                                                 "places") + f" (edges spread/largest jump, px: {line})", [line]))
    return combine("stability.morph", subs, engine=gates.engine("slate morph check", VERSION, rules=r),
                   inputs={}, details={"cards": measured, "rules": r})


# ---------------------------------------------------------------------------------------------------- the scan's own control
# Where each fault goes, as fractions of the frame (x, y, w, h), and what must flag it. A flash and a grey face go on a
# card's face, the shimmer on a soft shadow, the snap on a line of type, the veil over a large area, the reversal on a
# card: pass regions for your still's own content when you have one.
SELFTEST_REGIONS = {"flash": (0.078, 0.389, 0.146, 0.185), "shimmer": (0.677, 0.731, 0.25, 0.046),
                    "grey": (0.292, 0.389, 0.146, 0.185), "text": (0.688, 0.407, 0.146, 0.12),
                    "veil": (0.292, 0.611, 0.469, 0.278), "card": (0.208, 0.278, 0.365, 0.37)}
# the reversal: a card stepping +1, +1, 0, +1, 0, 0, 0 px and then -1 (layout rounding a morph to whole pixels)
_REVERSAL = {46: 1, 47: 2, 48: 2, 49: 3, 50: 3, 51: 3, 52: 3, 53: 2, 54: 2, 55: 2, 56: 2, 57: 2, 58: 2, 59: 2}


# A snapped line of type and a rounded card are seen on tiles that lie wholly inside them: at least this many
# tiles across, whatever the frame size (at full HD the regions above are already this big).
SELFTEST_MIN_TILES = {"text": 3, "card": 4}


def _px(region: tuple[float, ...], size: tuple[int, int], min_tiles: int = 0) -> tuple[int, int, int, int]:
    w, h = size
    if all(isinstance(v, int) for v in region):  # already pixels
        return tuple(int(v) for v in region)  # type: ignore[return-value]
    x, y, rw, rh = region
    px, py, pw, ph = x * w, y * h, max(2.0, rw * w), max(2.0, rh * h)
    side = min_tiles * TILE_PX
    if pw < side:
        px, pw = px - (side - pw) / 2, float(side)
    if ph < side:
        py, ph = py - (side - ph) / 2, float(side)
    pw, ph = min(pw, w - 4), min(ph, h - 4)
    px, py = min(max(0.0, px), w - pw - 3), min(max(0.0, py), h - ph - 2)
    return int(round(px)), int(round(py)), int(round(pw)), int(round(ph))


def inject_faults(still: np.ndarray, regions: dict | None = None) -> tuple[np.ndarray, dict]:
    """Sixty frames of one still (luma, 0-255) with six known faults, and where each should be flagged:
      frame 10       a flash: a card-sized area 3 levels darker for one frame              -> flicker
      frames 20-25   a shimmer: a soft area alternating ±0.8 levels                        -> shimmer (or flicker)
      frame 30       a grey face: 6 levels darker for one frame                            -> flicker
      frames 35-59   a snap: a text-sized area stepping 1 px down and staying             -> snap (or relative) at 35
      frame 42       a veil: a large area 1.7 levels darker for one frame                  -> flicker
      frames 46-59   a reversal: a card stepping +1, +1, 0, +1, 0, 0, 0 px then -1        -> reversal at 53
    """
    base = np.asarray(still, dtype=np.float64)
    h, w = base.shape
    reg = {k: _px(v, (w, h), SELFTEST_MIN_TILES.get(k, 0)) for k, v in {**SELFTEST_REGIONS, **(regions or {})}.items()}
    frames = []
    for i in range(60):
        f = base.copy()
        if i == 10:
            x, y, rw, rh = reg["flash"]
            f[y:y + rh, x:x + rw] -= 3
        if 20 <= i <= 25:
            x, y, rw, rh = reg["shimmer"]
            f[y:y + rh, x:x + rw] += 0.8 if i % 2 == 0 else -0.8
        if i == 30:
            x, y, rw, rh = reg["grey"]
            f[y:y + rh, x:x + rw] -= 6
        if i >= 35:
            x, y, rw, rh = reg["text"]
            f[y + 1:y + rh, x:x + rw] = base[y:y + rh - 1, x:x + rw]
        if i == 42:
            x, y, rw, rh = reg["veil"]
            f[y:y + rh, x:x + rw] -= 1.7
        if i in _REVERSAL:
            x, y, rw, rh = reg["card"]
            o = _REVERSAL[i]
            f[y:y + rh, x + o:x + rw + o] = base[y:y + rh, x:x + rw]
        frames.append(np.clip(f, 0, 255).astype(np.float32))
    expect = {"flash at 10 (flicker)": (("flicker",), range(9, 12), reg["flash"]),
              "shimmer at 20-25 (shimmer or flicker)": (("shimmer", "flicker"), range(19, 27), reg["shimmer"]),
              "grey face at 30 (flicker)": (("flicker",), range(29, 32), reg["grey"]),
              "snap at 35 (snap)": (("snap", "relative"), range(34, 37), reg["text"]),
              "veil at 42 (flicker)": (("flicker",), range(41, 44), reg["veil"]),
              "reversal at 53 (reversal)": (("reversal",), range(52, 55), reg["card"])}
    return np.stack(frames), expect


SELFTEST_BUSY = set(range(8, 13)) | set(range(18, 28)) | set(range(28, 33)) | set(range(33, 38)) | \
    set(range(41, 44)) | set(range(45, 60))


def selftest(still: np.ndarray, regions: dict | None = None, *, params: dict | None = None) -> GateResult:
    """The motion scan's positive control: known faults injected into a still are each flagged at their frame, and
    nothing else is. Run it on a still of real content (a card at rest, with type) so the faults land on the kind of
    content they're for."""
    from slate.qa.checks import Sub, combine  # noqa: PLC0415

    frames, expect = inject_faults(still, regions)
    events = scan_motion(frames, params=params, jobs=1)
    subs = []
    for name, (classes, span, (x, y, rw, rh)) in expect.items():
        tile = TILE_PX
        found = [e for e in events if e["class"] in classes and e["local"] in span
                 and x - tile <= e["region"][0] <= x + rw and y - tile <= e["region"][1] <= y + rh]
        worst = max((e["severity"] for e in found), default=0.0)
        subs.append(Sub(name.split(" (")[0], "PASS" if found else "FAIL",
                        f"{name}: {len(found)} flag{'s' if len(found) != 1 else ''}" + (f", worst {worst:.2f}" if found else "")))
    quiet = [e for e in events if e["local"] not in SELFTEST_BUSY]
    subs.append(Sub("untouched", "PASS" if not quiet else "FAIL", f"untouched frames: {len(quiet)} flags"
                    + (f" (e.g. {quiet[0]['class']} at {quiet[0]['local']})" if quiet else "")))
    return combine("stability.selftest", subs, engine=gates.engine("slate stability selftest", VERSION, params=PARAMS),
                   inputs={}, details={"events": len(events), "size": list(np.asarray(still).shape[::-1])})


# ---------------------------------------------------------------------------------------------------- real-fault controls
def controls(items: list[dict], *, jobs: int | None = 1) -> GateResult:
    """Positive controls from real renders: clips cut from films before their fixes, each a fault a person saw.
    items: [{file, first (the clip's frame 0 in its act), class, frames: [first, last] act frames, min_tiles (3),
    what}]. Each must be flagged in its class on at least min_tiles tiles inside its frames."""
    from slate.qa.checks import Sub, combine  # noqa: PLC0415

    subs, used = [], []
    for c in items:
        p = Path(str(c["file"])).expanduser()
        what = c.get("what") or p.name
        if not p.is_file():
            subs.append(Sub(what, "UNCHECKED", f"{what}: the clip isn't there ({p})"))
            continue
        used.append(p)
        lo, hi = (int(v) for v in c["frames"])
        events = scan_motion(p, jobs=jobs)
        hits = [e for e in events if e["class"] == c["class"] and lo <= e["local"] + int(c.get("first", 0)) <= hi]
        need = int(c.get("min_tiles", 3))
        worst = max((e["severity"] for e in hits), default=0.0)
        subs.append(Sub(what, "PASS" if len(hits) >= need else "FAIL",
                        f"{what}: {c['class']} at act {lo}-{hi}, {len(hits)} tiles (need ≥ {need}), worst {worst:.2f}"))
    return combine("stability.controls", subs, engine=gates.engine("slate stability controls", VERSION,
                                                                    decoder=f"ffmpeg {media.version('ffmpeg')}"),
                   inputs={str(p): jsonio.sha256(p) for p in used}, empty_summary="no controls given")


# ---------------------------------------------------------------------------------------------------- conformance
BENCH_SPEC_NAME = "stability_bench.json"
CONFORMANCE_RECORD = "qa/stability_conformance.json"  # under Slate's data folder: it's about the pipeline, not a film


def bench_spec_path() -> Path:
    """Where the engine kit keeps its stability bench's spec (engine/remotion/kit/bench/stability_bench.json)."""
    from slate import paths  # noqa: PLC0415

    return Path(paths.ENGINE_SRC) / "remotion" / "kit" / "bench" / BENCH_SPEC_NAME


def conformance(spec: dict, renders: dict[str, Any]) -> GateResult:
    """The render-stability conformance procedure on a bench the engine rendered. `renders` maps each variant to its
    render (a file, or frames in memory). The spec's steps, each a sub-check:

      scan         {render, expect: pass|fail, classes}: the motion scan passes (no flags in those classes) or fails
                   (the scan still sees the old fault)
      crisp_last   {render, reference, frame}: a push-in's last frame at least min_ratio (0.99) as crisp as the same
                   scene rasterised afresh
      crisp_moving {render, reference, box, count, mean, worst}: moving type at least `mean` as crisp on average and
                   never under `worst`
      rest         {render, reference, frame, min_db, crisp_within}: at rest, the same structure (PSNR after a 2.5 px
                   blur) and the same crispness as the pre-fix drawing
      identical    {a, b, count, min_db}: the same variant rendered twice is identical (nothing carried between frames)
      selftest     {render, frame, regions}: the scan's own positive control on a still of the bench
      morph        {render, cards}: the morph check; with from_scene, each card's true rect on every frame comes from
                   the spec's scene.morph (cards shrinking about their centres) and `content` is a box inside each card
                   on frame 0, [left, top, right, bottom] from its top-left
      controls     {items}: real faults the scan must still catch
    Coordinates in the spec are the bench's own (it is one fixed composition); nothing here assumes a frame size."""
    from slate.qa.checks import Sub, combine  # noqa: PLC0415

    subs: list = []

    def source(name: str) -> Source:
        if name not in renders:
            raise UsageError(f"The bench render {name!r} isn't there.")
        return Source(renders[name])

    def frame(name: str, k: int) -> np.ndarray:
        s = source(name)
        k = s.count + k if k < 0 else k
        return next(s.luma(k, 1))

    def pairs(a: str, b: str, n: int | None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        sa, sb = source(a), source(b)
        n = min(sa.count, sb.count) if n is None else min(int(n), sa.count, sb.count)
        return zip(sa.luma(0, n), sb.luma(0, n))

    for i, st in enumerate(spec.get("steps") or [], start=1):
        kind = st.get("step")
        label = f"{i}. {kind} {st.get('render') or st.get('a') or ''}".strip()
        try:
            if kind == "scan":
                ev = scan_motion(source(st["render"]), params=st.get("params"), jobs=st.get("jobs", 1))
                s = summarize_motion(ev)
                bad = [f"{c} {s[c]['frames']}" for c in st.get("classes") or MOTION_CLASSES if s.get(c, {}).get("frames")]
                got = "fail" if bad else "pass"
                want = st.get("expect", "pass")
                found = f" ({', '.join(bad)})" if bad else ""
                subs.append(Sub(label, "PASS" if got == want else "FAIL",
                                f"{st['render']}: {got}{found} (expected {want})"))
            elif kind == "crisp_last":
                k = int(st.get("frame", -1))
                ratio = crispness(frame(st["render"], k)) / max(crispness(frame(st["reference"], k)), 1e-9)
                need = float(st.get("min_ratio", 0.99))
                subs.append(Sub(label, "PASS" if ratio >= need else "FAIL",
                                f"{st['render']}: frame {k} {100 * ratio:.1f}% as crisp as a fresh raster "
                                f"(need ≥ {100 * need:.0f}%)"))
            elif kind == "crisp_moving":
                x, y, w, h = (int(v) for v in st["box"])
                r = np.array([crispness(a[y:y + h, x:x + w]) / max(crispness(b[y:y + h, x:x + w]), 1e-9)
                              for a, b in pairs(st["render"], st["reference"], st.get("count"))])
                mean, worst = float(st.get("mean", 0.99)), float(st.get("worst", 0.96))
                ok = len(r) > 0 and r.mean() >= mean and r.min() >= worst
                subs.append(Sub(label, "PASS" if ok else "FAIL",
                                f"{st['render']} {st.get('name', 'crop')}: {100 * r.mean():.1f}% as crisp on average, "
                                f"{100 * r.min():.1f}% at worst (need ≥ {100 * mean:.0f}% and ≥ {100 * worst:.0f}%)"))
            elif kind == "rest":
                k = int(st.get("frame", 0))
                a, b = frame(st["render"], k), frame(st["reference"], k)
                pb = blurred_psnr(b, a, float(st.get("blur_px", 2.5)))
                c = crispness(a) / max(crispness(b), 1e-9)
                ok = pb >= float(st.get("min_db", 50)) and abs(c - 1) <= float(st.get("crisp_within", 0.02))
                subs.append(Sub(label, "PASS" if ok else "FAIL",
                                f"{st['render']}: frame {k} against the pre-fix drawing at rest: {pb:.1f} dB after a "
                                f"blur (need ≥ {st.get('min_db', 50)}), crispness {100 * (c - 1):+.1f}%"))
            elif kind == "identical":
                vals = [psnr(a, b) for a, b in pairs(st["a"], st["b"], st.get("count"))]
                worst = min(vals) if vals else 0.0
                ok = bool(vals) and worst >= float(st.get("min_db", 90))
                subs.append(Sub(label, "PASS" if ok else "FAIL",
                                f"{st['a']} rendered twice: {worst:.1f} dB on the least alike frame (need identical)"))
            elif kind == "selftest":
                r = selftest(frame(st["render"], int(st.get("frame", 0))), st.get("regions"))
                subs.append(Sub(label, r.status, f"selftest on {st['render']}: {r.summary}", r.evidence))
            elif kind == "morph":
                cards = morph_cards(spec, st) if st.get("from_scene") else st["cards"]
                r = morph_check(source(st["render"]), cards, st.get("rules"))
                subs.append(Sub(label, r.status, f"morph on {st['render']}: {r.summary}", r.evidence))
            elif kind == "controls":
                r = controls(st.get("items") or [])
                subs.append(Sub(label, r.status, r.summary, r.evidence))
            else:
                subs.append(Sub(label, "UNCHECKED", f"unknown step {kind!r}"))
        except (SlateError, KeyError, ValueError, IndexError) as exc:
            subs.append(Sub(label, "UNCHECKED", f"{label}: couldn't run ({type(exc).__name__}: {exc})"))
    return combine("stability.conformance", subs, engine=gates.engine("slate stability conformance", VERSION),
                   inputs={}, empty_summary="the bench spec has no steps",
                   details={"bench": spec.get("bench"), "steps": len(spec.get("steps") or [])})


def morph_cards(spec: dict, step: dict) -> list[dict]:
    """The morph step's cards with their true geometry on every frame, from the bench spec's scene: still cards at
    scene.morph.x (camera px around the frame's centre) and y, w × h shrinking by `shrink` over the bench's frames
    about their centres. Rendered with motion blur centred on each frame, a frame's edges average to the frame's own
    size (the size changes linearly), so frame f's rect is the size at f."""
    film, m = spec["film"], spec["scene"]["morph"]
    n = int(film["frames"])
    cx0, cy0 = film["width"] / 2, film["height"] / 2
    (w0, h0), (dw, dh) = m["size"], m["shrink"]
    box = step.get("content")
    out = []
    for c, x in zip(step["cards"], m["x"]):
        cx, cy = cx0 + float(x), cy0 + float(m["y"])
        rects = []
        for f in range(n):
            w, h = w0 - dw * f / n, h0 - dh * f / n
            rects.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
        card = {**c, "rects": rects}
        if box:
            left, top = int(rects[0][0]), int(rects[0][1])
            card["content"] = [left + int(box[0]), top + int(box[1]), left + int(box[2]), top + int(box[3])]
        out.append(card)
    return out


BENCH_DIR = "qa/stability_bench"  # under Slate's data folder: the scratch film and the renders, until they're checked
BENCH_RENDER_TIMEOUT_S = 1800  # one variant: 60 frames at 16 samples, with room for a busy Mac
BENCH_SCENE_IMPORT = "@slate/kit/bench/stability"


def bench_film(folder: Path, spec: dict) -> Path:
    """The scratch film the bench renders in: the spec's size, rate, brand and length, and one act whose scene is the
    kit's bench (engine/remotion/kit/bench/stability.tsx). Rewritten each time, so it always matches the spec."""
    film = spec["film"]
    num, den, n = int(film["fps_num"]), int(film["fps_den"]), int(film["frames"])
    folder = Path(folder)
    (folder / "scenes" / "act1").mkdir(parents=True, exist_ok=True)
    (folder / "timing").mkdir(parents=True, exist_ok=True)
    note = "do not edit by hand: slate qa stability conformance --render (the kit's stability bench, not a film)"
    jsonio.write_atomic(folder / "film.json", {
        "id": film.get("id", "stability-bench"), "title": film.get("title", "The kit's stability bench"),
        "format": "bench", "language": "en", "fps_num": num, "fps_den": den, "width": int(film["width"]),
        "height": int(film["height"]), "brand": film.get("brand", "demo"), "mode": "draft", "generated": note})
    sec_out = round(n * den / num, 6)
    jsonio.write_atomic(folder / "timing" / "shots.json", {
        "generated": note, "fps_num": num, "fps_den": den, "width": int(film["width"]), "height": int(film["height"]),
        "total_frames": n, "total_seconds": sec_out,
        "acts": [{"index": 1, "name": "Bench", "shots": ["B01"], "frame_in": 0, "frame_out": n, "frames": n}],
        "shots": [{"id": "B01", "act": 1, "act_name": "Bench", "frame_in": 0, "frame_out": n, "frames": n,
                   "sec_in": 0.0, "sec_out": sec_out, "vo": "", "on_screen": ""}]})
    scene = folder / "scenes" / "act1" / "index.tsx"
    body = (f"// {note}\n// The act is the engine kit's stability bench; the render's input props pick its scene and "
            f"raster mode.\nexport {{StabilityBench as default}} from '{BENCH_SCENE_IMPORT}';\n")
    if not scene.is_file() or scene.read_text(encoding="utf-8") != body:
        scene.write_text(body, encoding="utf-8")
    return folder


def render_bench(out_dir: str | Path, spec: dict, *, variants: Iterable[str] | None = None,
                 samples: int | None = None) -> dict[str, Path]:
    """Render the bench's variants through the real pipeline (the engine's render_mb, every tile finished, at the
    spec's samples) into out_dir, one file per variant named after it, from one bundle. Returns {variant: file}."""
    from slate import paths  # noqa: PLC0415
    from slate.engine import bridge  # noqa: PLC0415

    engine = bridge.engine_dir()
    if not (engine / "remotion" / "kit" / "bench" / "stability.tsx").is_file():
        raise MissingPrerequisite(f"The engine at {engine} has no stability bench (it predates the kit's tilt mode).",
                                  hint="Run `slate engine install` so the installed engine matches this checkout, or "
                                       "point SLATE_ENGINE_DIR at an engine folder that has it.")
    film_dir = bench_film(paths.data() / BENCH_DIR / "film", spec)
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    wanted = list(variants or spec.get("variants") or {})
    sub = int(samples or spec["film"].get("samples", 16))
    made = bridge.call("bundle", {"film": str(film_dir)}, timeout=900)
    bundle = next((str(made[k]) for k in ("serve_url", "bundle", "bundle_dir", "dir") if made.get(k)), None)
    renders: dict[str, Path] = {}
    try:
        for i, name in enumerate(wanted, 1):
            v = spec["variants"][name]
            file = out / f"{name}.mov"
            log.info(f"bench {i}/{len(wanted)}: {name} ({v.get('scene')}, {v.get('raster')} mode, {sub} samples)")
            args = {"film": str(film_dir), "act": 1, "sub": sub, "out": str(file),
                    "props": {k: v[k] for k in ("scene", "raster") if k in v}}
            if bundle:
                args["serve_url"] = bundle
            bridge.call("render", args, timeout=BENCH_RENDER_TIMEOUT_S)
            renders[name] = file
    finally:
        if bundle:
            try:
                bridge.call("bundle", {"film": str(film_dir), "serve_url": bundle, "release": True}, timeout=120)
            except SlateError as exc:
                log.warn(f"the engine couldn't release the bench's bundle ({exc.message})")
    return renders


def run_conformance(renders_dir: str | Path | None = None, spec_path: str | Path | None = None, *,
                    render: bool = False, keep: bool = False) -> GateResult:
    """The procedure on this Mac: the engine kit's bench spec, the bench renders (one file per variant, named after
    it), the checks, and the result recorded under Slate's data folder. With `render`, the bench is rendered through
    the engine first (into renders_dir, or a fresh folder under Slate's data folder, removed after the check unless
    `keep`). Refuses with the next step when the engine kit has no stability bench, or the bench hasn't been
    rendered."""
    from slate import paths  # noqa: PLC0415

    sp = Path(spec_path).expanduser() if spec_path else bench_spec_path()
    if not sp.is_file():
        raise MissingPrerequisite(
            "The engine kit has no stability bench yet, so the conformance procedure has nothing to render and check.",
            hint=f"The engine kit needs a bench composition and its spec at {sp} (variants rendered through the real "
                 "pipeline, the steps in slate.qa.stability.conformance). Until then run `slate qa stability selftest` "
                 "for the scan's own control.")
    spec = jsonio.read(sp)
    made_here = False
    rendered: dict | None = None
    if render:
        if renders_dir is None:
            import datetime  # noqa: PLC0415

            renders_dir = paths.data() / BENCH_DIR / "renders" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            made_here = True
        t0 = _time()
        render_bench(renders_dir, spec)
        from slate.engine import bridge  # noqa: PLC0415

        rendered = {"seconds": round(_time() - t0, 1), "engine": str(bridge.engine_dir()),
                    "samples": int(spec["film"].get("samples", 16))}
    if renders_dir is None:
        raise MissingPrerequisite("The conformance procedure checks the bench as the real pipeline renders it.",
                                  hint="Render it through the engine and check it in one go (a long job: add --detach "
                                       "to queue it on the render slot): slate qa stability conformance --render. Or "
                                       "check renders you already have: slate qa stability conformance <folder>.")
    from slate.qa.film import VIDEO_EXT  # noqa: PLC0415

    folder = Path(renders_dir).expanduser()
    renders = {}
    for name in (spec.get("variants") or {}):
        found = sorted(p for p in folder.glob(f"{name}.*") if p.suffix.lower() in VIDEO_EXT)
        if found:
            renders[name] = found[0]
    missing = [n for n in (spec.get("variants") or {}) if n not in renders]
    if missing:
        raise MissingPrerequisite(f"These bench variants aren't rendered in {folder}: {', '.join(missing)}.",
                                  hint="Render them through the engine, one file per variant named after it.")
    result = conformance(spec, renders)
    record = {"generated": "do not edit by hand: slate qa stability conformance", "spec": str(sp),
              "spec_sha256": jsonio.sha256(sp), "renders": {k: str(v) for k, v in renders.items()},
              "render_sha256": {k: jsonio.sha256(v) for k, v in renders.items()},
              "rendered": rendered, "kept": not made_here or keep, "result": result.to_dict()}
    out = paths.data() / CONFORMANCE_RECORD
    jsonio.write_atomic(out, record)
    if made_here and not keep:
        import shutil  # noqa: PLC0415

        shutil.rmtree(folder, ignore_errors=True)
    return result


def _time() -> float:
    import time  # noqa: PLC0415

    return time.monotonic()


def last_conformance() -> GateResult | None:
    """The last recorded conformance result (for doctor), or None when the procedure hasn't run on this Mac."""
    from slate import paths  # noqa: PLC0415

    data = jsonio.read(paths.data() / CONFORMANCE_RECORD, default=None)
    if not isinstance(data, dict) or not data.get("result"):
        return None
    return GateResult.from_dict(data["result"])


# ---------------------------------------------------------------------------------------------------- the command
ACTIONS = ("run", "baseline", "reference", "strip", "compare", "selftest", "controls", "conformance")
HELP = "render stability: pops, snaps, wobbles, flicker; triage, baselines, strips, conformance"
DESCRIPTION = """Render stability on every act's render, triaged so what's left is what a person has to look at.

    slate qa stability                                   the gate on every act (recorded in qa/gates.json)
    slate qa stability --act 3                           one act (reports what it finds; can't pass the gate)
    slate qa stability --quick | --detach                reuse the scans already made | queue the scan as a job
    slate qa stability baseline --act 3 --by NAME --reason TEXT
                                                         accept act 3's open flags (never a reversal)
    slate qa stability baseline --act 3 --drop F[-F] [--drop-class C] --by NAME --reason TEXT
                                                         take accepted flags at those film frames back out, before
                                                         re-rendering a fix
    slate qa stability reference --act 3 RENDER          compare act 3 with this render
    slate qa stability strip --act 3 --frame F [--box x,y,w,h] [--count 7] [--zoom Z] [--out PNG]
                                                         before/after strips for eyes
    slate qa stability compare --act 3 [APPROVED] [--rest-only]
                                                         a re-render meant to change rendering only, against the
                                                         approved render (default: the act's reference): nothing
                                                         moved, missing or doubled, nothing softer at rest
    slate qa stability selftest [--still IMAGE]          the scan's own positive control
    slate qa stability controls CONTROLS.json            real faults the scan must still catch
    slate qa stability conformance RENDERS_FOLDER        the engine kit's stability bench, checked
    slate qa stability conformance --render [--detach] [--keep]
                                                         render the bench through the engine (16 samples, about a
                                                         dozen minutes on a quiet Mac) and check it; --detach queues it
                                                         on the render slot

Flags within ±3 frames of a planned beat (pops only), shared with a reference render (same class, within 2 frames
and one tile) or in the act's baseline don't count. A whole-pixel reversal always fails. Strips for the open flags
are written to qa/stability/strips/.

Before calling a flag a glitch, check it against a perfect render of the designed motion (set it as the act's
reference): a flag that render shares is the design's (the films' B4 "step" was a button's designed 3% press). Before
re-rendering a fix, take the fault's flags out of the baseline (`baseline --act N --drop FRAMES --by NAME --reason
TEXT`), so the new render proves the fix instead of the baseline hiding it."""


def register(sub) -> None:
    """Add `stability` to a subparsers object: `slate qa stability …` when qa is a group, or call it with the
    top-level subparsers for a `stability` command. qa-core's `slate qa stability` runs the same gate by name."""
    from slate.cli import add_command  # noqa: PLC0415

    p = add_command(sub, "stability", _cmd, help=HELP, description=DESCRIPTION)
    p.add_argument("action", nargs="?", default="run", choices=ACTIONS, help="what to do (default: run the gate)")
    p.add_argument("paths", nargs="*", help="reference: the render; controls: a controls file; conformance: the "
                                             "folder of bench renders")
    p.add_argument("--act", type=int, action="append", help="the act (repeat for more)")
    p.add_argument("--by", metavar="NAME", help="the person accepting or setting")
    p.add_argument("--reason", metavar="TEXT", help="why the flags are acceptable (baseline), or why this reference")
    p.add_argument("--drop", metavar="FRAME[-FRAME]",
                   help="baseline: take accepted flags at these film frames back out (before re-rendering a fix, so "
                        "the new render proves it)")
    p.add_argument("--drop-class", dest="drop_class", action="append", metavar="CLASS",
                   help="baseline --drop: only flags of this class (repeat for more)")
    p.add_argument("--frame", type=int, help="strip: the film frame to centre on")
    p.add_argument("--count", type=int, default=7, help="strip: frames in the strip (default 7)")
    p.add_argument("--box", help="strip: the crop, x,y,w,h in pixels (default: around the worst flag at the frame)")
    p.add_argument("--zoom", type=int, help="strip: enlargement (default: about 180 px tall)")
    p.add_argument("--out", help="strip: where to write the PNG")
    p.add_argument("--jobs", type=int, help="worker processes for the motion scan (default: a quarter of the cores)")
    p.add_argument("--still", help="selftest: an image or render to take the still from (default: a made-up still)")
    p.add_argument("--spec", help="conformance: the bench spec (default: the engine kit's)")
    p.add_argument("--no-strips", dest="strips", action="store_false", help="don't write strips for open flags")
    p.add_argument("--rest-only", action="store_true",
                   help="compare: check structure only where the approved render holds still (a one-sample reference)")
    p.add_argument("--quick", action="store_true",
                   help="run: reuse the scans already made, never start one (an act not scanned yet is UNCHECKED)")
    p.add_argument("--detach", action="store_true",
                   help="run, or conformance --render: queue it as a job (it holds the render slot) and return the job "
                        "id at once")
    p.add_argument("--render", action="store_true",
                   help="conformance: render the engine kit's bench through the engine first, then check it")
    p.add_argument("--keep", action="store_true",
                   help="conformance --render: keep the bench renders (removed after the check by default)")


def _film_dir(args: argparse.Namespace) -> Path:
    from slate import cli  # noqa: PLC0415

    return cli.film(args)


def _show(r: GateResult) -> str:
    lines = [f"{r.status:<9} {r.gate}  {r.summary}"]
    for c in (r.details or {}).get("checks") or []:
        lines.append(f"  {c.get('status', ''):<9} {c.get('check', ''):<12} {c.get('summary', '')}")
    return "\n".join(lines)


def _plan(args: argparse.Namespace) -> int:
    """`slate --dry-run qa stability …`: what the action would do, on what, and whether it would be queued. Runs
    nothing (no scan, no strip, no comparison, no render) and writes nothing."""
    from slate import cli  # noqa: PLC0415

    action = args.action or "run"
    acts = [int(a) for a in args.act or []]
    film_dir = None if action in ("selftest", "controls", "conformance") else cli.film(args)
    on: dict = {"acts": acts or "every act", "paths": list(args.paths or [])}
    if film_dir is not None:
        from slate.qa.film import context  # noqa: PLC0415

        ctx = context(film_dir)
        on["film_dir"] = str(film_dir)
        on["renders"] = {str(a): ctx.rel(e["path"]) for a, e in ctx.current_renders().items()
                         if not acts or a in acts}
    queued = bool(args.detach) and action in ("run", "conformance")
    acts_text = f"act{'s' if len(acts) > 1 else ''} {', '.join(map(str, acts))}" if acts else "every act"
    what = {
        "run": f"scan {acts_text}'s render at full resolution"
               + (" (reusing the scans already made, starting none)" if args.quick else "")
               + ", triage the flags" + (", write strips for the open ones in qa/stability/strips/" if args.strips else "")
               + " and record the gate in qa/gates.json",
        "baseline": (f"take the accepted flags at film frames {args.drop} out of {acts_text}'s baseline" if args.drop
                     else f"accept {acts_text}'s open flags into its baseline (qa/stability/)")
                    + f", by {args.by or '(no --by given)'}",
        "reference": f"compare {acts_text} with {', '.join(args.paths) or '(no render given)'} from now on",
        "strip": f"write a before-and-after strip of {acts_text} around film frame {args.frame}"
                 + (f" to {args.out}" if args.out else " in qa/stability/strips/"),
        "compare": f"compare {acts_text}'s render with {', '.join(args.paths) or 'its reference'}",
        "selftest": "run the scan's positive control on " + (args.still or "a made-up still"),
        "controls": f"run the controls in {', '.join(args.paths) or '(no file given)'}",
        "conformance": ("render the engine kit's stability bench through the engine (about a dozen minutes) and "
                        "check it" if args.render else f"check the bench renders in {', '.join(args.paths) or '(no folder given)'}"),
    }[action]
    where = ("as a queued job that holds the render slot" if queued else
             "here, in this process (not queued" + ("; --detach would queue it)" if action in ("run", "conformance")
                                                    else ")"))
    log.result({"dry_run": True, "action": action, "would": what, "on": on, "queued": queued, "where": where},
               f"dry run: slate qa stability {action} would {what}, {where}\nnothing ran, nothing written")
    return 0


def _cmd(args: argparse.Namespace) -> int:
    from slate import cli  # noqa: PLC0415

    action = args.action or "run"
    if cli.flag(args, "dry_run", False):
        return _plan(args)
    if action == "run":
        return _run(args)
    if action == "baseline":
        return _baseline(args)
    if action == "reference":
        return _reference(args)
    if action == "strip":
        return _strip(args)
    if action == "compare":
        return _compare(args)
    if action == "selftest":
        return _selftest(args)
    if action == "controls":
        return _controls(args)
    return _conformance(args)


def _detach(film_dir: Path, args: argparse.Namespace) -> int:
    """Queue the gate as a job: a feature-length act's full-resolution scan outlasts an agent's session."""
    import sys  # noqa: PLC0415

    try:
        from slate.jobs import client  # noqa: PLC0415
    except ImportError as exc:
        raise MissingPrerequisite("The job queue isn't installed, so the scan can't run detached.",
                                  hint="Run `slate qa stability` without --detach.") from exc
    argv = [sys.executable, "-m", "slate", "qa", GATE, "--film", str(film_dir)]
    for a in args.act or []:
        argv += ["--act", str(a)]
    argv += (["--jobs", str(args.jobs)] if args.jobs else []) + ([] if args.strips else ["--no-strips"])
    argv += ["--quick"] if args.quick else []
    job_id = client.submit("qa", argv, film=str(film_dir), resources=("render",), resumable=True,
                           note="slate qa stability" + "".join(f" --act {a}" for a in args.act or []))
    log.result({"job": job_id, "film_dir": str(film_dir), "argv": argv, "next": f"slate job status {job_id}"},
               f"queued as job {job_id}\nnext: slate job status {job_id}   (then slate qa triage)")
    return 0


def _run(args: argparse.Namespace) -> int:
    from slate import qa  # noqa: PLC0415
    from slate.qa import park  # noqa: PLC0415
    from slate.qa.film import session  # noqa: PLC0415

    film_dir = _film_dir(args)
    if args.detach:
        return _detach(film_dir, args)
    with run_options(acts=args.act, jobs=args.jobs, strips=args.strips):  # a dry run never gets here (_plan)
        if GATE not in qa.GATES:
            with session(film_dir) as ctx:
                ctx.quick = bool(args.quick)
                results = [gate(film_dir)]
            gates.write_report(film_dir, results)
        else:
            results = qa.run(film_dir, [GATE], quick=bool(args.quick))
    human = ["\n".join(_show(r) for r in results)]
    human.append(f"recorded in {Path(film_dir) / 'qa' / 'gates.json'}")
    parked = park.status(Path(film_dir))
    if parked:
        human.append(f"PARKED: {parked.get('why')} Release: {parked.get('release')}")
    if gates.overall(results) != "PASS":
        human.append("next: look at the strips in qa/stability/strips/, then fix the act or accept what's by design: "
                     "slate qa stability baseline --act N --by NAME --reason TEXT")
    log.result({"gates": [r.to_dict() for r in results], "summary": gates.summarize(results), "dry_run": False,
                "parked": parked}, "\n".join(human))
    return gates.exit_code(results)


def _one_act(args: argparse.Namespace) -> int:
    if not args.act or len(args.act) != 1:
        raise UsageError(f"`slate qa stability {args.action}` takes one act: --act N.")
    return int(args.act[0])


def _baseline(args: argparse.Namespace) -> int:
    film_dir = _film_dir(args)
    act = _one_act(args)
    mode = (jsonio.read(film_dir / "film.json", default={}) or {}).get("mode", "supervised")
    if getattr(args, "drop", None):
        text = str(args.drop)
        try:
            lo, _, hi = text.partition("-")
            frames = (int(lo), int(hi or lo))
        except ValueError as exc:
            raise UsageError(f"--drop takes film frames, one (1077) or a range (1070-1090), not {text!r}.") from exc
        rec = drop_from_baseline(film_dir, act, frames, classes=args.drop_class or (), by=args.by,
                                 reason=args.reason, mode=mode)
        human = (f"act {act}: {rec['dropped']} accepted flag(s) at film frames {rec['frames'][0]}–{rec['frames'][1]} "
                 f"taken out of {rec['baseline']} by {rec['by']} ({rec['total']} left). Re-render the fix: the scan "
                 f"now judges those frames again.\nnext: slate render --act {act}, then slate qa {GATE}")
        log.result({"baseline": rec}, human)
        return 0
    rec = accept_baseline(film_dir, act, by=args.by, reason=args.reason, mode=mode)
    human = (f"act {act}: {rec['added']} flag(s) accepted into {rec['baseline']} by {rec['by']} ({rec['total']} in all)"
             + (f"; {rec['reversals_left_out']} reversal(s) left out: they're always fixed" if rec["reversals_left_out"]
                else "") + f"\nnext: slate qa {GATE}")
    log.result({"baseline": rec, "next": f"slate qa {GATE}"}, human)
    return 0


def _reference(args: argparse.Namespace) -> int:
    film_dir = _film_dir(args)
    act = _one_act(args)
    if len(args.paths) != 1:
        raise UsageError("`slate qa stability reference` takes one render: reference --act N RENDER.")
    rec = set_reference(film_dir, act, args.paths[0], by=args.by, why=args.reason)
    log.result({"reference": rec}, f"act {act} is compared with {rec['file']}\nnext: slate qa {GATE} --act {act}")
    return 0


def _strip(args: argparse.Namespace) -> int:
    from slate.qa.film import context  # noqa: PLC0415

    film_dir = _film_dir(args)
    act = _one_act(args)
    if args.frame is None:
        raise UsageError("`slate qa stability strip` needs --frame F (a film frame).")
    ctx = context(film_dir)
    cur = ctx.current_renders().get(act)
    if cur is None or not cur["path"].is_file():
        raise MissingPrerequisite(f"Act {act} has no whole-act render.", hint=f"slate render --act {act}")
    local = int(args.frame) - int(cur["frame_in"])
    if args.box:
        try:
            box = tuple(int(v) for v in args.box.split(","))
            assert len(box) == 4
        except (ValueError, AssertionError) as exc:
            raise UsageError(f"--box takes x,y,w,h in pixels, not {args.box!r}.") from exc
    else:
        rep = jsonio.read(ctx.path(report_rel(act)), default={}) or {}
        near = [f for f in rep.get("open") or [] if abs(int(f["local"]) - local) <= 1]
        if not near:
            raise UsageError(f"No open flag at film frame {args.frame} in act {act}'s report: give --box x,y,w,h.")
        box = flag_box(max(near, key=lambda f: f.get("severity", 0)), ctx.size)
    rows = [("this render", cur["path"], 0)]
    ref, _why = reference_for(ctx, next(a for a in ctx.acts if a["index"] == act), cur)
    if ref is not None:
        rows.append(("reference", ref, 0))
    count = max(1, int(args.count))
    out = Path(args.out) if args.out else ctx.path(f"qa/stability/strips/act{act}_frame{args.frame}.png")
    made = strip(out, rows, max(0, local - count // 2), count, box, zoom=args.zoom,
                 title=f"act {act}, film frame {args.frame}")
    log.result({"strip": str(made)}, f"strip: {made}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    from slate.qa.film import context  # noqa: PLC0415

    film_dir = _film_dir(args)
    act = _one_act(args)
    ctx = context(film_dir)
    cur = ctx.current_renders().get(act)
    if cur is None or not cur["path"].is_file():
        raise MissingPrerequisite(f"Act {act} has no whole-act render.", hint=f"slate render --act {act}")
    if args.paths:
        approved = Path(args.paths[0]).expanduser()
        if not approved.is_file():
            raise UsageError(f"No such render: {approved}")
    else:
        approved, why = reference_for(ctx, next(a for a in ctx.acts if a["index"] == act), cur)
        if approved is None:
            raise UsageError(f"There's nothing to compare act {act} with: {why}.",
                             hint=f"slate qa stability compare --act {act} APPROVED_RENDER")
    r = compare_renders(approved, cur["path"], rest_only=bool(args.rest_only))
    log.result({"result": r.to_dict(), "approved": str(approved), "new": str(cur["path"])}, _show(r))
    return gates.exit_code([r])


def _selftest(args: argparse.Namespace) -> int:
    if args.still:
        p = Path(args.still).expanduser()
        if not p.is_file():
            raise UsageError(f"No such file: {p}")
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            from PIL import Image  # noqa: PLC0415

            still = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
        else:
            still = next(Source(p).luma(int(args.frame or 0), 1))
    else:
        still = made_up_still()
    r = selftest(still)
    log.result({"result": r.to_dict()}, _show(r))
    return gates.exit_code([r])


def made_up_still(size: tuple[int, int] = (640, 360), seed: int = 7) -> np.ndarray:
    """A still with texture everywhere (soft cards and type-like detail), for the selftest when no real still is
    given. Real content is better: it brings the codec's own noise."""
    w, h = size
    rng = np.random.default_rng(seed)
    base = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 2.0)
    base = 128 + 24 * base / max(1e-9, float(base.std()))  # the same texture at any size: sharp enough to measure
    yy, xx = np.mgrid[0:h, 0:w]
    base += 12 * np.sin(xx / 37.0) + 8 * np.cos(yy / 23.0)  # a soft ground under the detail
    return np.clip(base, 0, 255).astype(np.float32)


def _controls(args: argparse.Namespace) -> int:
    if len(args.paths) != 1:
        raise UsageError("`slate qa stability controls` takes one controls file (JSON: a list of {file, first, class, "
                         "frames, min_tiles, what}).")
    items = jsonio.read(Path(args.paths[0]).expanduser())
    if isinstance(items, dict):
        items = items.get("controls") or []
    r = controls(items, jobs=args.jobs or 1)
    log.result({"result": r.to_dict()}, _show(r))
    return gates.exit_code([r])


def _conformance(args: argparse.Namespace) -> int:
    renders = args.paths[0] if args.paths else None
    if args.detach:
        if not args.render:
            raise UsageError("--detach queues the bench's renders: give it with --render.",
                             hint="slate qa stability conformance --render --detach")
        import sys  # noqa: PLC0415

        from slate.jobs import client  # noqa: PLC0415

        argv = [sys.executable, "-m", "slate", "qa", GATE, "conformance", "--render"]
        argv += ([renders] if renders else []) + (["--spec", args.spec] if args.spec else [])
        argv += ["--keep"] if args.keep else []
        job_id = client.submit("qa", argv, resources=("render",), resumable=True, disk_gb=2.0,
                               note="slate qa stability conformance --render (the kit's stability bench)")
        log.result({"job": job_id, "argv": argv, "next": f"slate job status {job_id}"},
                   f"queued as job {job_id}\nnext: slate job status {job_id}   (then slate qa stability conformance "
                   "records its result for doctor)")
        return 0
    r = run_conformance(renders, args.spec, render=args.render, keep=args.keep)
    log.result({"result": r.to_dict()}, _show(r))
    return gates.exit_code([r])
