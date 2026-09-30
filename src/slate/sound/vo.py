"""The voice: every take placed from timing/vo_cues.json, levelled the way the films levelled it, and found again.

A cue is `{shot, at, file, gain, fade_in, fade_out}` (at = seconds after the shot's first frame), or carries an
absolute film time `t`. A take is loaded (trim, reverse, rate, filters, length), faded (sin² in, cos² out), and
levelled by the RMS of its voiced part (samples over 0.02) to `gain` dBFS, -20 by default. That is exactly how the
films' engines rendered the voice, so a take found in a delivered dialogue stem correlates with it almost perfectly.

`find_take()` is the placement check: it slides the take over the dialogue stem ±50 ms around where its cue puts it,
computing the Pearson correlation at every lag (by FFT, so every sample is tried, not every twelfth), and reports the
offset and the correlation. A take within half a millisecond of its cue is exact.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy import signal

from slate import jsonio
from slate.errors import UsageError
from slate.sound import audio, dsp
from slate.sound.film import Film

VOICED = 0.02


def cues(film: Film) -> list[dict]:
    path = film.path("timing/vo_cues.json")
    if not path.exists():
        return []
    return list(jsonio.read(path).get("cues") or [])


def cue_time(film: Film, cue: dict) -> float:
    if "t" in cue:
        return float(cue["t"])
    if "shot" not in cue:
        raise UsageError(f"A voice cue has neither a shot nor a time: {cue}")
    return film.shot_start(cue["shot"]) + float(cue.get("at", 0.0))


def level_by_voiced_rms(y: np.ndarray, gain_db: float) -> np.ndarray:
    voiced = y[np.abs(y).max(axis=1) > VOICED]
    rms = math.sqrt(float(np.mean(voiced ** 2))) if len(voiced) else 1.0
    return y / (rms + 1e-12) * dsp.db(gain_db)


def render_take(film: Film, cue: dict, source: Path | None = None) -> np.ndarray:
    """One take as the mix places it: loaded at the film's rate, trimmed, faded and levelled (stereo float64)."""
    sr = film.sr
    y = audio.load(source or film.resolve_media(cue["file"]), sr)
    if cue.get("trim"):
        a, b = cue["trim"]
        y = y[int(float(a) * sr):int(float(b) * sr)]
    if cue.get("reverse"):
        y = y[::-1].copy()
    rate = float(cue.get("rate", 1.0))
    if rate != 1.0:
        y = signal.resample_poly(y, 1000, int(round(1000 * rate)), axis=0)
    for key, kind in (("hp", "highpass"), ("lp", "lowpass")):
        if cue.get(key):
            y = signal.sosfilt(signal.butter(2, float(cue[key]), kind, fs=sr, output="sos"), y, axis=0)
    if cue.get("dur"):
        y = y[:int(float(cue["dur"]) * sr)]
    y = y * dsp.fade(len(y), float(cue.get("fade_in", 0.005)), float(cue.get("fade_out", 0.05)), sr)[:, None]
    return level_by_voiced_rms(y, float(cue.get("gain", film.palette["levels"]["voice_gain_dbfs"])))


def render_stem(film: Film) -> tuple[np.ndarray, list[dict]]:
    """The whole voice stem (film length) and where each take went."""
    n = film.n
    stem = np.zeros((n, 2))
    placed = []
    for cue in cues(film):
        y = render_take(film, cue)
        t = cue_time(film, cue)
        s = int(round(t * film.sr))
        a, b = max(s, 0), min(s + len(y), n)
        if b > a:
            stem[a:b] += y[a - s:b - s]
        placed.append({"shot": cue.get("shot"), "file": cue.get("file"), "t": round(t, 6), "sample": s,
                       "samples": len(y), "cut_at_film_end": s + len(y) > n})
    return stem, placed


def find_take(dialogue: np.ndarray, take: np.ndarray, expected: int, search: int) -> tuple[int, float]:
    """(lag in samples, Pearson correlation) of the best match of `take` in `dialogue` near sample `expected`."""
    d = audio.mono(np.asarray(dialogue, dtype=np.float64))
    y = audio.mono(np.asarray(take, dtype=np.float64))
    m = len(y)
    if m < 2:
        return 0, 0.0
    lo = expected - search
    seg = np.zeros(m + 2 * search)
    a, b = max(lo, 0), min(lo + len(seg), len(d))
    if b > a:
        seg[a - lo:b - lo] = d[a:b]
    num = signal.correlate(seg, y, mode="valid", method="fft")  # sum(seg[k:k+m] * y) for k = 0 .. 2*search
    c1 = np.concatenate([[0.0], np.cumsum(seg)])
    c2 = np.concatenate([[0.0], np.cumsum(seg ** 2)])
    k = np.arange(len(num))
    s_sum = c1[k + m] - c1[k]
    s_sq = c2[k + m] - c2[k]
    y_mean = y.mean()
    cov = num - s_sum * y_mean
    var_s = np.maximum(s_sq - s_sum ** 2 / m, 0.0)
    var_y = float(np.sum((y - y_mean) ** 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        r = cov / np.sqrt(var_s * var_y)
    r = np.nan_to_num(r, nan=-1.0)
    best = int(np.argmax(r))
    return best - search, float(r[best])
