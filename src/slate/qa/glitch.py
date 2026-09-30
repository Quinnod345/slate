"""The one- and two-frame glitch scan, with triage against the film's planned beats.

Smooth motion changes a frame gradually, so a frame should sit between its neighbours. A glitch doesn't: frame n
differs from both n−1 and n+1 while n−1 and n+1 are close to each other (a flash, a pop, a surface missing for a
frame, two layers swapping order for a frame). Per 30 px tile of a small grey copy of the picture:

    glitch(n) = min(d(n−1, n), d(n, n+1)) − d(n−1, n+1) / 2        (mean absolute difference, 0–255)

It is high only when n is the odd one out; two-frame glitches get the same test with n+2. A frame is flagged when its
worst tile scores over 6.0. Real cuts stay low (the frames after a cut are alike).

Triage: smooth fast moves also trip it, which is why film B had 1,442 flags. A flag within ±3 frames of a planned
beat (a visual event, or a resolved timeline event and its start) is a move the film meant, so it is skipped and
listed as such; what's left is what a person has to look at.
"""

from __future__ import annotations

import bisect
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from slate.qa import media

THRESHOLD = 6.0
WINDOW = 3  # frames either side of a planned beat
TILE = 30  # px at the analysis size
LONG_SIDE = 480


def _tiles(a: np.ndarray, b: np.ndarray, gy: int, gx: int) -> np.ndarray:
    h, w = a.shape
    return np.abs(a - b).reshape(gy, h // gy, gx, w // gx).mean(axis=(1, 3))


def scan_frames(frames: Iterable[np.ndarray], *, threshold: float = THRESHOLD, tile: int = TILE) -> list[dict]:
    """Flags for a sequence of grey frames (float or uint8, all the same shape, sides multiples of `tile`).

    Each flag is {index, len (1 or 2), score, tile: [x, y]}, `index` counting from the first frame given.
    """
    buf: list[np.ndarray] = []
    hits: list[dict] = []
    gy = gx = 0
    for i, f in enumerate(frames):
        f = np.asarray(f, dtype=np.float32)
        if not gy:
            gy, gx = f.shape[0] // tile, f.shape[1] // tile
            if gy * tile != f.shape[0] or gx * tile != f.shape[1]:
                raise ValueError(f"frame {f.shape} isn't a whole number of {tile} px tiles")
        buf.append(f)
        if len(buf) > 4:
            buf.pop(0)
        if len(buf) >= 3:
            p, n, q = buf[-3], buf[-2], buf[-1]
            g = np.minimum(_tiles(p, n, gy, gx), _tiles(n, q, gy, gx)) - _tiles(p, q, gy, gx) / 2
            k = np.unravel_index(int(np.argmax(g)), g.shape)
            if g[k] > threshold:
                hits.append({"index": i - 1, "len": 1, "score": round(float(g[k]), 2), "tile": [int(k[1]), int(k[0])]})
        if len(buf) == 4:
            p, n1, n2, q = buf
            g = (np.minimum(np.minimum(_tiles(p, n1, gy, gx), _tiles(n2, q, gy, gx)), _tiles(p, n2, gy, gx))
                 - _tiles(p, q, gy, gx) / 2)
            k = np.unravel_index(int(np.argmax(g)), g.shape)
            if g[k] > threshold and _tiles(n1, n2, gy, gx)[k] < g[k]:
                hits.append({"index": i - 2, "len": 2, "score": round(float(g[k]), 2), "tile": [int(k[1]), int(k[0])]})
    return hits


def scan(path: str | Path, *, film_size: tuple[int, int], first_film_frame: int = 0, count: int | None = None,
         threshold: float = THRESHOLD) -> list[dict]:
    """Scan a render. Flags carry `frame` (film frame) and `local` (frame in the file)."""
    size = media.analysis_size(*film_size, long_side=LONG_SIDE, tile=TILE)
    frames: Iterator[np.ndarray] = media.iter_frames(path, size=size, pix_fmt="gray", count=count)
    out = []
    for h in scan_frames(frames, threshold=threshold):
        out.append({"frame": first_film_frame + h["index"], "local": h["index"], "len": h["len"], "score": h["score"],
                    "tile": h["tile"]})
    return out


def triage(flags: list[dict], beats: Iterable[int], window: int = WINDOW) -> tuple[list[dict], list[dict]]:
    """Split flags into (remaining, skipped). A flag is skipped when any frame it covers is within `window` frames
    of a planned beat; skipped flags record the nearest beat and how far it was."""
    frames = sorted({int(b) for b in beats})
    remaining, skipped = [], []
    for f in flags:
        covered = range(int(f["frame"]), int(f["frame"]) + int(f.get("len", 1)))
        best = None
        for c in covered:
            k = bisect.bisect_left(frames, c)
            for j in (k - 1, k):
                if 0 <= j < len(frames):
                    d = abs(frames[j] - c)
                    if best is None or d < best[0]:
                        best = (d, frames[j])
        if best is not None and best[0] <= window:
            skipped.append({**f, "near_beat": best[1], "distance": best[0]})
        else:
            remaining.append(f)
    return remaining, skipped
