"""Fit a cubic-bezier to a move measured in a reference film.

Brand onboarding studies two or three reference films and the product's own motion; this is how a measured move
becomes a named ease with its lineage (the films' `fit_ease.py`, generalised: it takes any measured signal, not one
film's hardcoded clips). Measuring the signal (an edge, a centroid, a width per frame) is the analyzer's job; this
takes the samples, finds the move (2% and 98% of its travel on a lightly smoothed signal), normalises time and travel
to 0..1 and least-squares fits cubic-bezier(x1, y1, x2, y2), with x1 and x2 kept within [0, 1].
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from slate.motion.eases import bezier_at


def _bez_y_at_x(p: Sequence[float], xs: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = p
    s = np.linspace(0, 1, 2001)
    bx = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s**2 * x2 + s**3
    by = 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s**2 * y2 + s**3
    return np.interp(xs, bx, by)


def normalise_move(times: Sequence[float], values: Sequence[float], lo: float = 0.02, hi: float = 0.98) -> dict:
    """Find the move in a measured signal and normalise it: {t: 0..1, p: 0..1, start_s, end_s, travel}."""
    t = np.asarray(times, dtype=float)
    v = np.asarray(values, dtype=float)
    if t.size < 4 or t.size != v.size:
        raise ValueError("a move needs at least four samples, one value per time")
    if t.size >= 3:
        sm = np.convolve(v, np.ones(3) / 3, mode="same")
        sm[0], sm[-1] = sm[1], sm[-2]
    else:
        sm = v
    total = sm[-1] - sm[0]
    if abs(total) < 1e-12:
        raise ValueError("the signal doesn't move (its first and last values are the same)")
    p = (sm - sm[0]) / total
    i0 = int(np.argmax(p > lo))
    i1 = int(np.argmax(p > hi))
    i0 = max(i0 - 1, 0)
    i1 = min(i1 + 1, len(p) - 1)
    if i1 <= i0:
        raise ValueError("couldn't find where the move starts and ends")
    tt = (t[i0 : i1 + 1] - t[i0]) / (t[i1] - t[i0])
    pp = (p[i0 : i1 + 1] - p[i0]) / (p[i1] - p[i0])
    return {"t": tt, "p": pp, "start_s": float(t[i0]), "end_s": float(t[i1]), "travel": float(v[-1] - v[0])}


def fit_bezier(times: Sequence[float], values: Sequence[float]) -> dict:
    """Least-squares cubic-bezier for a measured move. Returns {bezier, rms, duration_s, start_s, end_s, samples,
    travel}; rms is in fractions of the travel (0.0013 is 0.13%)."""
    from scipy.optimize import least_squares  # noqa: PLC0415 - scipy is heavy; only the fit needs it

    mv = normalise_move(times, values)
    tt, pp = mv["t"], mv["p"]
    res = least_squares(lambda q: _bez_y_at_x(q, tt) - pp, [0.4, 0.2, 0.2, 1.0], bounds=([0, -0.5, 0, -0.5], [1, 1.5, 1, 1.5]))
    bez = [round(float(x), 3) for x in res.x]
    exact = np.array([bezier_at(bez, float(u)) for u in tt])
    rms = float(np.sqrt(np.mean((exact - pp) ** 2)))
    return {
        "bezier": bez,
        "rms": round(rms, 5),
        "duration_s": round(mv["end_s"] - mv["start_s"], 4),
        "start_s": round(mv["start_s"], 4),
        "end_s": round(mv["end_s"], 4),
        "samples": int(tt.size),
        "travel": round(mv["travel"], 3),
    }
