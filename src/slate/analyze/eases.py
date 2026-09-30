"""Measured eases: follow a moving element in a reference film and fit the curve it moves on.

Generalized from the films' `fit_ease.py`, which measured four hand-picked moves (a lockup's left edge, a window's
width, a band's centroid) and least-squares fitted a cubic-bezier to each. Here the same measurement works on any
film, two ways:

- **found** (`slate analyze clip.mp4 --eases`): moves are runs of frame-to-frame change with stillness on both sides,
  never across a cut. Each is tracked against a background plate (the frame's flat colour when it has one, otherwise
  the two rest poses, told apart by the move's halfway frame) and the channel that travels furthest and most steadily
  is fitted: the centroid, an edge, the width, the height or the area.
- **described** (`--eases moves.json`): each move gives its window [t0, t1), a region, a mask (an RGB range, a colour
  with a tolerance, or "moving") and the measurement: an edge, the centroid, the size, or a "filled" measurement
  that counts only the columns or rows the mask covers by more than `fill` of the region (the films' window width
  and band centre). They keep fit_ease.py's conventions (frames where the mask holds nothing are left out), and
  rerunning its four moves reproduces its measured eases (tests/formats/test_analyze_golden.py). A moves file:

      [{"name": "logo push", "t0": 1.15, "t1": 1.95, "roi": [0, 180, 1280, 240],
        "mask": {"rgb_min": [201, 201, 201], "rgb_max": [255, 255, 255]}, "metric": "left",
        "note": "left edge of the white lockup as it pushes in"},
       {"name": "band wipe", "t0": 75.3, "t1": 76.6, "mask": {"color": "#e07020", "tolerance": 40},
        "metric": "filled_centroid_x", "fill": 0.25}]

The fit itself is slate.motion.fit (the move found at 2% and 98% of its travel, time and travel normalized, a
cubic-bezier least-squares fitted). Each move also gets its nearest named eases (the brand's own first, then public
curves), any overshoot or anticipation, and a lineage record brand onboarding can copy into a motion law.

What the finder doesn't do: follow an element that scales in place on a textured background, or several elements
moving at once with different curves. Describe those moves instead.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Iterable

import numpy as np

from slate import jsonio
from slate.errors import UsageError
from slate.motion import fit as motion_fit
from slate.motion.eases import EaseSpec, evaluate
from slate.qa import media

TRACK_LONG_SIDE = 480  # px: enough for sub-pixel centroids, small enough to stream a whole film
DIFF_TOLERANCE = 24  # 0-255: a pixel is part of the element when it differs from the background by more than this
MIN_PIXELS = 6  # fewer element pixels than this in a frame means nothing is tracked in it
ACTIVE_FLOOR = 0.15  # mean frame-to-frame change (0-255) below which a frame is at rest, at the very least
REST_PAD = 2  # still frames kept on each side of a move, so it starts and ends at rest
MIN_MOVE_FRAMES = 4  # slate.motion.fit needs four samples
MIN_TRAVEL = 0.02  # of the frame: anything smaller is noise, not a move
MIN_STEADINESS = 0.8  # net travel over total travel: a move that wanders back and forth isn't one ease
MAX_MOVES = 12
FLAT_SHARE = 0.5  # a colour covering this much of both rest poses is a flat background
MAX_WINDOW_BYTES = 512 << 20  # described windows bigger than this are measured at half size

X_CHANNELS = ("centroid_x", "left", "right", "width")
Y_CHANNELS = ("centroid_y", "top", "bottom", "height")
CHANNELS = (*X_CHANNELS, *Y_CHANNELS, "area")
# "Filled" measurements count only the columns (or rows) the mask covers by more than a share of the region, the way
# the films measured a window's width (columns at least 90% white) or a band's centre (columns over 25% orange).
FILLED_X = ("filled_width", "filled_left", "filled_right", "filled_centroid_x")
FILLED_Y = ("filled_height", "filled_top", "filled_bottom", "filled_centroid_y")
FILLED = (*FILLED_X, *FILLED_Y)
DEFAULT_FILL = 0.5
PREFERENCE = {name: i for i, name in enumerate(("centroid_x", "centroid_y", "left", "right", "top", "bottom",
                                                "width", "height", "area"))}


# ---------------------------------------------------------------------------------------------------------------
# Finding moves


def tracking_size(width: int, height: int, long_side: int = TRACK_LONG_SIDE) -> tuple[int, int]:
    """An even analysis size with the film's aspect, never larger than the film."""
    scale = min(1.0, long_side / float(max(width, height)))
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return w, h


def motion_energy(video: Path, info: dict, size: tuple[int, int]) -> np.ndarray:
    """Mean absolute change from each frame to the next (grey, 0-255), streamed so a whole film fits in memory.
    energy[i] is the change into frame i; energy[0] is 0."""
    out: list[float] = []
    prev = None
    for frame in media.iter_frames(video, size=size, pix_fmt="gray", info=info):
        cur = frame.astype(np.int16)
        out.append(0.0 if prev is None else float(np.abs(cur - prev).mean()))
        prev = cur
    return np.asarray(out, dtype=float)


def find_moves(energy: Iterable[float], *, cuts: Iterable[int] = (), max_moves: int = MAX_MOVES) -> list[dict]:
    """Moves as frame windows: [{first, last, change_from, change_to, energy}], strongest first then by time.

    A move is a run of change (merged over gaps of two frames or less) extended out to where the picture is still
    again, with at least REST_PAD still frames on each side. A run that touches a cut or the edge of the file is
    dropped: its start or its end isn't visible, so no ease can be fitted to it.
    """
    e = np.asarray(list(energy), dtype=float)
    n = e.size
    if n < MIN_MOVE_FRAMES + 2 * REST_PAD + 1:
        return []
    blocked = np.zeros(n, dtype=bool)
    for c in cuts:
        for k in (c - 1, c, c + 1):
            if 0 <= k < n:
                blocked[k] = True
    floor = float(np.percentile(e[1:], 20)) if n > 1 else 0.0
    active_thr = max(ACTIVE_FLOOR, 4.0 * floor)
    rest_thr = max(0.01, 1.5 * floor)
    active = (e > active_thr) & ~blocked
    active[0] = False
    runs: list[list[int]] = []
    i = 1
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while True:
            nxt = next((k for k in range(j + 1, min(n, j + 4)) if active[k]), None)
            if nxt is None or blocked[j + 1:nxt].any():
                break
            j = nxt
        runs.append([i, j])
        i = j + 1
    moves = []
    for s, t in runs:
        while s - 1 >= 1 and e[s - 1] > rest_thr and not blocked[s - 1]:
            s -= 1
        while t + 1 < n and e[t + 1] > rest_thr and not blocked[t + 1]:
            t += 1
        before = range(s - REST_PAD, s)
        after = range(t + 1, t + 1 + REST_PAD)
        if s - REST_PAD < 1 or t + REST_PAD >= n:
            continue  # the move starts or ends at the file's edge
        if any(blocked[k] or e[k] > rest_thr for k in (*before, *after)):
            continue  # it runs into a cut or another move: no rest pose on that side
        first, last = s - 1 - (REST_PAD - 1), t + REST_PAD
        if last - first + 1 < MIN_MOVE_FRAMES + 2 * REST_PAD:
            continue
        moves.append({"first": int(first), "last": int(last), "change_from": int(s), "change_to": int(t),
                      "energy": round(float(e[s:t + 1].sum()), 3)})
    merged: list[dict] = []
    for m in sorted(moves, key=lambda m: m["first"]):
        if merged and m["first"] <= merged[-1]["last"]:
            continue  # overlapping windows: the earlier one already covers this stretch
        merged.append(m)
    strongest = sorted(merged, key=lambda m: -m["energy"])[:max_moves]
    return sorted(strongest, key=lambda m: m["first"])


# ---------------------------------------------------------------------------------------------------------------
# Following one element


def _dominant_colour(frame: np.ndarray, tol: int = DIFF_TOLERANCE) -> tuple[np.ndarray, float]:
    """The most common colour (quantized to 16 levels a channel) and the share of pixels within `tol` of it."""
    q = (frame // 16).reshape(-1, 3).astype(np.int32)
    keys = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    mode = int(np.bincount(keys).argmax())
    pick = frame.reshape(-1, 3)[keys == mode]
    colour = np.median(pick, axis=0)
    share = float((np.abs(frame.astype(np.int16) - colour).max(axis=-1) <= tol).mean())
    return colour, share


def element_masks(stack: np.ndarray, *, tol: int = DIFF_TOLERANCE) -> tuple[np.ndarray, str]:
    """(masks (n, h, w), how): the moving element's pixels in every frame of a window that starts and ends at rest."""
    frames = stack.astype(np.int16)
    first, last = frames[0], frames[-1]
    colour, share0 = _dominant_colour(stack[0], tol)
    _, share1 = _dominant_colour(stack[-1], tol)
    changed = np.abs(frames - first).max(axis=-1) > tol
    swept = changed.any(axis=0)
    if share0 >= FLAT_SHARE and share1 >= FLAT_SHARE and swept.any():
        # A flat background: the element is everything that isn't the background's colour, near where things moved.
        ys, xs = np.nonzero(swept)
        h, w = swept.shape
        pad_y, pad_x = max(2, h // 25), max(2, w // 25)
        region = np.zeros_like(swept)
        region[max(0, ys.min() - pad_y):ys.max() + pad_y + 1, max(0, xs.min() - pad_x):xs.max() + pad_x + 1] = True
        masks = (np.abs(frames - colour).max(axis=-1) > tol) & region
        return masks, "found: flat background"
    # Otherwise a plate from the two rest poses: where they differ, the pose that looks like the halfway frame is
    # the background there (the element has left its start and not yet reached its end).
    dist0 = np.abs(frames - first).reshape(len(frames), -1).sum(axis=1)
    dist1 = np.abs(frames - last).reshape(len(frames), -1).sum(axis=1)
    half = int(np.argmin(np.abs(dist0 - dist1)))
    mid = frames[half]
    plate = first.copy()
    differ = np.abs(first - last).max(axis=-1) > tol
    closer_to_last = np.abs(mid - last).max(axis=-1) < np.abs(mid - first).max(axis=-1)
    use_last = differ & closer_to_last
    plate[use_last] = last[use_last]
    masks = np.abs(frames - plate).max(axis=-1) > tol
    return masks, "found: two-pose background"


def channels_from_masks(masks: np.ndarray, min_pixels: int = MIN_PIXELS) -> dict[str, np.ndarray]:
    """Per frame: centroid, edges, width, height and area of the mask (NaN where there's too little to measure).
    The finder ignores specks under MIN_PIXELS; a described mask is exact, so it measures from one pixel."""
    n = masks.shape[0]
    out = {c: np.full(n, np.nan) for c in CHANNELS}
    for i, m in enumerate(masks):
        count = int(m.sum())
        if count < max(1, min_pixels):
            continue
        ys, xs = np.nonzero(m)
        left, right, top, bottom = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
        out["centroid_x"][i], out["centroid_y"][i] = float(xs.mean()), float(ys.mean())
        out["left"][i], out["right"][i], out["top"][i], out["bottom"][i] = left, right, top, bottom
        out["width"][i], out["height"][i] = right - left + 1, bottom - top + 1
        out["area"][i] = float(count)
    return out


def filled_channels(masks: np.ndarray, fill: float = DEFAULT_FILL) -> dict[str, np.ndarray]:
    """Per frame, from the columns and rows the mask covers by more than `fill` of the region: how many there are,
    the first and last, and their mean position. A count is 0 when none qualify; a position is NaN."""
    n = masks.shape[0]
    out = {c: np.full(n, np.nan) for c in FILLED}
    for i, m in enumerate(masks):
        cols = np.nonzero(m.mean(axis=0) > fill)[0]
        rows = np.nonzero(m.mean(axis=1) > fill)[0]
        out["filled_width"][i], out["filled_height"][i] = float(cols.size), float(rows.size)
        if cols.size:
            out["filled_left"][i], out["filled_right"][i] = float(cols.min()), float(cols.max())
            out["filled_centroid_x"][i] = float(cols.mean())
        if rows.size:
            out["filled_top"][i], out["filled_bottom"][i] = float(rows.min()), float(rows.max())
            out["filled_centroid_y"][i] = float(rows.mean())
    return out


def _fill_gaps(v: np.ndarray, max_share: float = 0.2) -> np.ndarray | None:
    """Interpolate a few missing samples inside the window; None when the ends or too much are missing."""
    bad = np.isnan(v)
    if not bad.any():
        return v
    if bad[0] or bad[-1] or bad.mean() > max_share:
        return None
    idx = np.arange(v.size)
    out = v.copy()
    out[bad] = np.interp(idx[bad], idx[~bad], v[~bad])
    return out


def pick_channel(channels: dict[str, np.ndarray], width: int, height: int) -> tuple[str, np.ndarray] | None:
    """The channel that travels furthest, steadily, as a share of the frame (ties go to the centroid, then edges)."""
    best = None
    for name, raw in channels.items():
        v = _fill_gaps(np.asarray(raw, dtype=float))
        if v is None:
            continue
        span = width if name in X_CHANNELS else height if name in Y_CHANNELS else width * height
        net = abs(v[-1] - v[0])
        travel = net / float(span)
        total = float(np.abs(np.diff(v)).sum())
        steady = net / total if total else 0.0
        if travel < MIN_TRAVEL or steady < MIN_STEADINESS:
            continue
        score = (round(travel * steady, 4), -PREFERENCE[name])
        if best is None or score > best[0]:
            best = (score, name, v)
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------------------------------------------------------
# Fitting


DENSE = np.linspace(0.0, 1.0, 2001)


def progress(values: np.ndarray, rest: int) -> np.ndarray | None:
    """The signal as progress from its starting rest (0) to its ending rest (1); None when it doesn't travel."""
    v = np.asarray(values, dtype=float)
    k = max(1, min(int(rest), v.size // 4))
    v0, v1 = float(v[:k].mean()), float(v[-k:].mean())
    if abs(v1 - v0) < 1e-9:
        return None
    return (v - v0) / (v1 - v0)


def _placed_rms(t: np.ndarray, p: np.ndarray, curve: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """RMS of the curve placed at every (start, end) against the measured progress, vectorized over the placements."""
    u = np.clip((t[None, :] - starts[:, None]) / (ends - starts)[:, None], 0.0, 1.0)
    model = np.interp(u.ravel(), DENSE, curve).reshape(u.shape)
    return np.sqrt(np.mean((model - p[None, :]) ** 2, axis=1))


def _named(times: np.ndarray, values: np.ndarray, cands: list[dict], *, rest: int = REST_PAD) -> list[dict]:
    """Each named ease placed wherever it fits the whole move best (its own start and end), rest to rest.

    Comparing against the whole signal, with the curve free in time, is fair to every curve: the 2%-98% window the
    bezier fit uses cuts a long decelerating tail short, and a named curve judged inside that window would lose to
    a different one. The placement a curve needs is reported with it (start_s, end_s)."""
    from scipy.optimize import least_squares  # noqa: PLC0415 - heavy; only the fits need it

    t = np.asarray(times, dtype=float)
    p = progress(values, rest)
    if p is None or t.size < MIN_MOVE_FRAMES:
        return []
    span = float(t[-1] - t[0])
    step = max(span / 80.0, 1e-3)
    grid = np.arange(t[0] - step, t[-1] + 2 * step, step)
    a_idx, b_idx = np.triu_indices(grid.size, k=2)
    starts, ends = grid[a_idx], grid[b_idx]
    rows = []
    for c in cands:
        spec: EaseSpec = c["spec"]
        curve = np.array([evaluate(spec, float(u)) for u in DENSE])
        rms = _placed_rms(t, p, curve, starts, ends)
        best = int(np.argmin(rms))
        a0, d0 = float(starts[best]), float(ends[best] - starts[best])

        def resid(q, curve=curve):
            a, d = q[0], math.exp(q[1])
            return np.interp(np.clip((t - a) / d, 0.0, 1.0), DENSE, curve) - p

        fit = least_squares(resid, [a0, math.log(d0)], bounds=([t[0] - span, math.log(step / 4)],
                                                               [t[-1], math.log(4 * span + step)]))
        a, d = float(fit.x[0]), math.exp(float(fit.x[1]))
        err = float(np.sqrt(np.mean(fit.fun ** 2)))
        if err > float(rms[best]):  # the refinement never makes a placement worse than the grid's best
            a, d, err = a0, d0, float(rms[best])
        rows.append({"name": c["name"], "family": c["family"], "curve": c["curve"], "rms": round(err, 5),
                     "start_s": round(a, 4), "end_s": round(a + d, 4), "duration_s": round(d, 4)})
    return sorted(rows, key=lambda r: r["rms"])


def _overshoot(values: np.ndarray, rest: int = REST_PAD) -> tuple[float, float]:
    """(overshoot, anticipation) as shares of the travel: past the ending rest, and back before the starting one."""
    p = progress(values, rest)
    if p is None:
        return 0.0, 0.0
    over = max(0.0, float(p.max()) - 1.0)
    back = max(0.0, -float(p.min()))
    return round(over if over > 0.02 else 0.0, 4), round(back if back > 0.02 else 0.0, 4)


def confidence(rms: float, samples: int) -> str:
    """How far to trust a fit: a low error over a handful of samples proves little, so both count."""
    if rms <= 0.02 and samples >= 8:
        return "good"
    if rms <= 0.05 and samples >= 5:
        return "fair"
    return "rough"


def fit_move(times: np.ndarray, values: np.ndarray, *, cands: list[dict], unit_scale: float = 1.0,
             rest: int = REST_PAD) -> dict:
    """The bezier, its error, the nearest named eases and any overshoot for one measured signal.

    `rest` is how many samples at each end are the rest poses (found moves carry REST_PAD; a described window's first
    and last samples stand in for them)."""
    fitted = motion_fit.fit_bezier(times, values)
    nearest = _named(times, values, cands, rest=rest)
    over, back = _overshoot(values, rest)
    return {
        "bezier": fitted["bezier"], "rms": fitted["rms"], "confidence": confidence(fitted["rms"], fitted["samples"]),
        "start_s": fitted["start_s"], "end_s": fitted["end_s"], "duration_s": fitted["duration_s"],
        "samples": fitted["samples"], "travel": round(fitted["travel"] * unit_scale, 2),
        "overshoot": over, "anticipation": back,
        "nearest": nearest[:3], "best_named": nearest[0] if nearest else None,
    }


# ---------------------------------------------------------------------------------------------------------------
# Described moves


def load_specs(path: str | Path) -> list[dict]:
    """A moves file: a list of {name, t0, t1, roi?, mask?, metric?, note?} (or {"moves": [...]})."""
    data = jsonio.read(path)
    moves = data.get("moves") if isinstance(data, dict) else data
    if not isinstance(moves, list) or not moves:
        raise UsageError(f"{path} should hold a list of moves: [{{name, t0, t1, roi, mask, metric, note}}].")
    out = []
    for i, m in enumerate(moves, 1):
        if not isinstance(m, dict) or "t0" not in m or "t1" not in m:
            raise UsageError(f"Move {i} in {path} needs t0 and t1 (seconds).")
        if float(m["t1"]) <= float(m["t0"]):
            raise UsageError(f"Move {i} in {path} ends before it starts.")
        metric = m.get("metric", "auto")
        if metric != "auto" and metric not in CHANNELS and metric not in FILLED:
            raise UsageError(f"Move {i} in {path} measures {metric!r}; use one of {', '.join(CHANNELS + FILLED)} "
                             "or auto.")
        fill = m.get("fill", DEFAULT_FILL)
        if not isinstance(fill, (int, float)) or isinstance(fill, bool) or not 0 <= float(fill) < 1:
            raise UsageError(f"Move {i} in {path}: fill is the share of the region a column or row must cover, "
                             "from 0 up to (not including) 1.")
        out.append({**m, "name": str(m.get("name") or f"move-{i:02d}"), "metric": metric, "fill": float(fill)})
    return out


def _hex(colour: str) -> np.ndarray:
    text = colour.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise UsageError(f"{colour!r} isn't a #rrggbb colour.")
    return np.array([int(text[k:k + 2], 16) for k in (0, 2, 4)], dtype=np.int16)


def _roi(spec: dict, width: int, height: int, src_w: int, src_h: int) -> tuple[slice, slice, int, int]:
    """(rows, cols, x0, y0) of a move's region at the decode size; the whole frame without one."""
    roi = spec.get("roi")
    if not roi:
        return slice(0, height), slice(0, width), 0, 0
    if not (isinstance(roi, (list, tuple)) and len(roi) == 4):
        raise UsageError(f"Move {spec['name']}: roi is [x, y, width, height] in pixels (or fractions of the frame).")
    x, y, w, h = (float(v) for v in roi)
    if max(x, y, w, h) <= 1.0:
        x, w, y, h = x * src_w, w * src_w, y * src_h, h * src_h
    sx, sy = width / float(src_w), height / float(src_h)
    x0, y0 = max(0, int(round(x * sx))), max(0, int(round(y * sy)))
    x1, y1 = min(width, int(round((x + w) * sx))), min(height, int(round((y + h) * sy)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise UsageError(f"Move {spec['name']}: its roi is outside the frame or too small.")
    return slice(y0, y1), slice(x0, x1), x0, y0


def _spec_masks(stack: np.ndarray, spec: dict) -> tuple[np.ndarray, str]:
    mask = spec.get("mask", "moving")
    frames = stack.astype(np.int16)
    if mask in (None, "moving"):
        return element_masks(stack)
    if isinstance(mask, dict) and "rgb_min" in mask:
        lo = np.asarray(mask["rgb_min"], dtype=np.int16)
        hi = np.asarray(mask.get("rgb_max", [255, 255, 255]), dtype=np.int16)
        return ((frames >= lo) & (frames <= hi)).all(axis=-1), f"described: RGB {list(lo)} to {list(hi)}"
    if isinstance(mask, dict) and "color" in mask:
        tol = int(mask.get("tolerance", DIFF_TOLERANCE))
        return np.abs(frames - _hex(str(mask["color"]))).max(axis=-1) <= tol, f"described: {mask['color']} ±{tol}"
    raise UsageError(f"Move {spec['name']}: mask is \"moving\", {{rgb_min, rgb_max}} or {{color, tolerance}}.")


# ---------------------------------------------------------------------------------------------------------------
# The step


def _frame_time(frame: int, fps: Fraction) -> float:
    return float(frame / fps)


def _window(video: Path, info: dict, first: int, last: int, size: tuple[int, int]) -> np.ndarray:
    frames = list(media.iter_frames(video, start=first, count=last - first + 1, size=size, pix_fmt="rgb24", info=info))
    if len(frames) < MIN_MOVE_FRAMES:
        raise UsageError(f"only {len(frames)} frames could be read between frames {first} and {last}")
    return np.stack(frames)


def _lineage(video_meta: dict, move: dict) -> dict:
    return {"tool": "slate analyze --eases", "video": video_meta.get("name"), "sha256": video_meta.get("sha256"),
            "window_s": move["window_s"], "channel": move.get("channel"), "how": move.get("how")}


def found_moves(video: Path, info: dict, *, cuts: Iterable[int] = (), cands: list[dict], video_meta: dict,
                max_moves: int = MAX_MOVES) -> tuple[list[dict], list[dict]]:
    """(moves, skipped): every move the finder could follow and fit, and the ones it couldn't, with the reason."""
    fps = Fraction(info["fps_num"], info["fps_den"])
    size = tracking_size(info["width"], info["height"])
    scale_x = info["width"] / float(size[0])
    energy = motion_energy(video, info, size)
    moves, skipped = [], []
    for k, win in enumerate(find_moves(energy, cuts=cuts, max_moves=max_moves), 1):
        name = f"move-{k:02d}"
        window_s = [round(_frame_time(win["first"], fps), 4), round(_frame_time(win["last"], fps), 4)]
        try:
            stack = _window(video, info, win["first"], win["last"], size)
            masks, how = element_masks(stack)
            picked = pick_channel(channels_from_masks(masks), size[0], size[1])
            if picked is None:
                skipped.append({"name": name, "window_s": window_s, "reason": "no channel moved far enough, steadily"})
                continue
            channel, values = picked
            times = np.array([_frame_time(win["first"] + i, fps) for i in range(values.size)])
            unit = scale_x if channel in X_CHANNELS else (info["height"] / float(size[1]) if channel in Y_CHANNELS
                                                          else scale_x * info["height"] / float(size[1]))
            row = {"name": name, "window_s": window_s, "frames": [win["first"], win["last"]], "channel": channel,
                   "how": how, **fit_move(times, values, cands=cands, unit_scale=unit)}
        except (ValueError, UsageError) as exc:
            skipped.append({"name": name, "window_s": window_s, "reason": getattr(exc, "message", str(exc))})
            continue
        row["lineage"] = _lineage(video_meta, row)
        moves.append(row)
    return moves, skipped


def described_moves(video: Path, info: dict, specs: list[dict], *, cands: list[dict],
                    video_meta: dict) -> tuple[list[dict], list[dict]]:
    """(moves, skipped) for moves described in a moves file, measured at the film's own size where memory allows."""
    fps = Fraction(info["fps_num"], info["fps_den"])
    moves, skipped = [], []
    for spec in specs:
        # The window is [t0, t1): the first frame at or after t0, up to the last one before t1 (what ffmpeg's
        # -ss t0 -to t1 gave the films' fit_ease.py).
        first = max(0, math.ceil(float(spec["t0"]) * fps - Fraction(1, 10**6)))
        last = math.ceil(float(spec["t1"]) * fps - Fraction(1, 10**6)) - 1
        if info.get("frames"):
            last = min(last, int(info["frames"]) - 1)
        size = (info["width"], info["height"])
        if (last - first + 1) * size[0] * size[1] * 3 > MAX_WINDOW_BYTES:
            size = tracking_size(info["width"], info["height"], long_side=max(info["width"], info["height"]) // 2)
        window_s = [round(_frame_time(first, fps), 4), round(_frame_time(last, fps), 4)]
        try:
            rows, cols, _x0, _y0 = _roi(spec, size[0], size[1], info["width"], info["height"])
            stack = _window(video, info, first, last, size)[:, rows, cols]
            masks, how = _spec_masks(stack, spec)
            tracked = spec.get("mask") in (None, "moving")  # followed against a background, not an exact colour
            chans = channels_from_masks(masks, min_pixels=MIN_PIXELS if tracked else 1)
            h, w = masks.shape[1:]
            if spec["metric"] == "auto":
                picked = pick_channel(chans, w, h)
                if picked is None:
                    raise UsageError("no channel moved far enough, steadily, inside the region")
                channel, values = picked
                times = np.array([_frame_time(first + i, fps) for i in range(values.size)])
                dropped = 0
            else:
                # Frames where the mask holds nothing to measure are left out, as the films' fit_ease.py left them
                # out: an element that appears partway through its window is measured from where it appears.
                channel = spec["metric"]
                raw = (filled_channels(masks, spec.get("fill", DEFAULT_FILL)) if channel in FILLED else chans)[channel]
                keep = ~np.isnan(raw)
                dropped = int((~keep).sum())
                if keep.sum() < MIN_MOVE_FRAMES:
                    raise UsageError(f"the mask held something to measure in only {int(keep.sum())} frames "
                                     f"({channel}); a move needs {MIN_MOVE_FRAMES}")
                values = raw[keep]
                times = np.array([_frame_time(first + i, fps) for i in range(raw.size)])[keep]
            sx, sy = info["width"] / float(size[0]), info["height"] / float(size[1])
            unit = (sx if channel in X_CHANNELS + FILLED_X else sy if channel in Y_CHANNELS + FILLED_Y else sx * sy)
            row = {"name": spec["name"], "window_s": window_s, "frames": [first, last], "channel": channel,
                   "how": how, **fit_move(times, values, cands=cands, unit_scale=unit, rest=1)}
            if channel in FILLED:
                row["fill"] = spec.get("fill", DEFAULT_FILL)
            if dropped:
                row["frames_without_a_measurement"] = dropped
        except (ValueError, UsageError) as exc:
            skipped.append({"name": spec["name"], "window_s": window_s, "reason": getattr(exc, "message", str(exc))})
            continue
        if spec.get("note"):
            row["note"] = str(spec["note"])
        row["lineage"] = _lineage(video_meta, row)
        moves.append(row)
    return moves, skipped


def summary_line(move: dict) -> str:
    best = move.get("best_named") or {}
    bez = ", ".join(f"{v:g}" for v in move["bezier"])
    near = f"; nearest {best['name']} ({best['rms'] * 100:.1f}% off)" if best else ""
    over = f"; overshoots {move['overshoot'] * 100:.0f}%" if move.get("overshoot") else ""
    return (f"{move['name']}: {move['channel']} over {move['duration_s']:.2f} s, cubic-bezier({bez}), "
            f"{move['rms'] * 100:.2f}% RMS ({move['confidence']}){near}{over}")

