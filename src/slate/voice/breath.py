"""Breath and vocal effort, measured: is a tagged read breathier or softer than the voice's own untagged reads?

Ported from the films' `voice_metrics.py` (2026-09-30), written after the owner heard whispery tags as "a little
bit scary". A tagged take is scored against untagged takes of the same words, in units of their own spread, and
rejected when it measures breathier, however good its tag looks on paper.

Measures (numpy and scipy only; frames 10 ms apart):
- voiced_frac: share of speech frames with a periodic voice (YIN). A whisper has no pitch, so it collapses.
- cpps_db: smoothed cepstral peak prominence (Hillenbrand; Praat's CPPS recipe). Breath fills the harmonics.
- hnr_db: harmonics-to-noise ratio, autocorrelation method (Boersma), over all speech frames.
- h1h2_db: first minus second harmonic. A glottis that stays open longer (breathy phonation) raises it.
- tilt_db: energy 50-1,000 Hz over 1-5 kHz. A soft, low-effort voice tilts more steeply.
- level_db: the span's level. Softening lowers it.
Plus creak (voiced frames under 115 Hz or 0.6x the median) and pitch statistics for reporting.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from slate.voice import audio as audio_mod
from slate.voice.measure import HOP, clean_f0, runs, yin_f0

# +1: higher means breathier or lower effort; -1: lower means breathier or lower effort.
BREATH = {"voiced_frac": -1, "cpps_db": -1, "hnr_db": -1, "h1h2_db": +1, "tilt_db": +1, "effort_level_db": -1}
CORE = ("voiced_frac", "cpps_db", "h1h2_db", "tilt_db")
# Floors keep a tiny baseline spread from turning noise into a verdict. Whole takes differ by about 1.5 dB of
# output gain with no change of delivery, hence the level floor.
FLOOR = {"voiced_frac": 0.03, "cpps_db": 0.4, "hnr_db": 0.5, "h1h2_db": 1.0, "tilt_db": 0.8, "effort_level_db": 1.5}


def _frames(x: np.ndarray, sr: int, win: float, n: int | None = None) -> np.ndarray:
    w, h = int(round(win * sr)), int(round(HOP * sr))
    if len(x) < w:
        x = np.concatenate([x, np.zeros(w - len(x))])
    count = 1 + max(0, (len(x) - w) // h)
    if n is not None:
        count = min(count, n)
    idx = (np.arange(count) * h)[:, None] + np.arange(w)[None, :]
    return x[idx]


def _speech_mask(db: np.ndarray) -> tuple[np.ndarray, float]:
    thr = max(-55.0, float(np.percentile(db, 95)) - 38.0)
    act = db > thr
    for a, b in runs(act, False):
        if (b - a) * HOP < 0.08 and a > 0 and b < len(act):
            act[a:b] = True
    for a, b in runs(act, True):
        if (b - a) * HOP < 0.05:
            act[a:b] = False
    return act, thr


def _hnr(x24: np.ndarray, sr: int, act: np.ndarray, win: float = 0.040) -> np.ndarray:
    fr = _frames(x24, sr, win, len(act))
    w = np.hanning(fr.shape[1])
    nfft = 1 << int(math.ceil(math.log2(2 * fr.shape[1])))
    rw = np.fft.irfft(np.abs(np.fft.rfft(w, nfft)) ** 2, nfft)[: fr.shape[1]]
    rw = rw / rw[0]
    lo, hi = int(sr / 450), int(sr / 80)
    out = np.full(len(act), np.nan)
    for i in range(len(fr)):
        if not act[i]:
            continue
        s = (fr[i] - fr[i].mean()) * w
        ra = np.fft.irfft(np.abs(np.fft.rfft(s, nfft)) ** 2, nfft)[: fr.shape[1]]
        if ra[0] <= 0:
            continue
        r = ra / ra[0] / np.maximum(rw, 1e-6)
        k = lo + int(np.argmax(r[lo:hi]))
        y0, y1, y2 = r[k - 1], r[k], r[k + 1]
        den = y0 - 2 * y1 + y2
        peak = y1 - 0.125 * (y0 - y2) ** 2 / den if den < -1e-12 else y1
        peak = min(max(peak, 1e-4), 0.9999)
        out[i] = 10 * math.log10(peak / (1 - peak))
    return out


def _cpps(x24: np.ndarray, sr: int, n: int, win: float = 0.040, t_smooth: int = 7, q_smooth: float = 0.001
          ) -> np.ndarray:
    fr = _frames(x24, sr, win, n)
    w = np.hanning(fr.shape[1])
    nfft = 1 << (int(math.ceil(math.log2(fr.shape[1]))) + 1)
    spec = np.abs(np.fft.rfft(fr * w, nfft, axis=1)) ** 2
    ceps = np.abs(np.fft.irfft(np.log(spec + 1e-20), nfft, axis=1)[:, : nfft // 2]) ** 2
    k = np.ones(t_smooth) / t_smooth
    ceps = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, ceps)
    qk = max(1, int(q_smooth * sr))
    ceps = np.apply_along_axis(lambda c: np.convolve(c, np.ones(qk) / qk, mode="same"), 1, ceps)
    cdb = 10 * np.log10(ceps + 1e-30)
    q = np.arange(cdb.shape[1]) / sr
    fit = (q >= 0.001) & (q <= 0.025)
    pk = np.flatnonzero((q >= 1 / 450) & (q <= 1 / 80))
    A = np.vstack([q[fit], np.ones(fit.sum())]).T
    coef = np.linalg.lstsq(A, cdb[:, fit].T, rcond=None)[0]
    j = pk[np.argmax(cdb[:, pk], axis=1)]
    vals = cdb[np.arange(len(cdb)), j] - (coef[0] * q[j] + coef[1])
    full = np.full(n, np.nan)
    full[: len(vals)] = vals[:n]
    return full


class Frames:
    """Every per-frame track of one take, computed once."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        x, sr = audio_mod.load(path)
        x44 = audio_mod.resample(x, sr, 44100)
        self.duration = len(x44) / 44100.0
        fr44 = _frames(x44, 44100, 0.025)
        self.db = 20 * np.log10(np.sqrt(np.mean(fr44 ** 2, axis=1)) + 1e-12)
        n = len(self.db)
        self.act, self.thr = _speech_mask(self.db)
        f0 = yin_f0(audio_mod.resample(x, sr, 16000), 16000, n=n)
        f0[~self.act] = np.nan
        self.f0 = clean_f0(f0)
        self.voiced = ~np.isnan(self.f0)
        x24 = audio_mod.resample(x, sr, 24000)
        self.hnr = _hnr(x24, 24000, self.act)
        self.cpps = _cpps(x24, 24000, n)
        fr = _frames(x44, 44100, 0.040, n)
        mag = np.zeros((n, 4097))
        mag[: len(fr)] = np.abs(np.fft.rfft(fr * np.hanning(fr.shape[1]), 8192, axis=1))
        freqs = np.fft.rfftfreq(8192, 1 / 44100)
        pw = mag ** 2

        def band(lo: float, hi: float) -> np.ndarray:
            return pw[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) + 1e-20

        self.tilt = 10 * np.log10(band(50, 1000) / band(1000, 5000))
        self.hf_noise = 10 * np.log10(band(5000, 11000) / band(50, 5000))
        self.centroid = (mag * freqs[None, :]).sum(axis=1) / (mag.sum(axis=1) + 1e-12)
        self.h1h2 = np.full(n, np.nan)
        for i in np.flatnonzero(self.voiced):
            def peak_db(f: float) -> float:
                sel = (freqs >= f * 0.9) & (freqs <= f * 1.1)
                return 20 * math.log10(mag[i, sel].max() + 1e-12) if sel.any() else -240.0
            self.h1h2[i] = peak_db(self.f0[i]) - peak_db(2 * self.f0[i])


def summarize(F: Frames, mask: np.ndarray) -> dict | None:
    """Every measure over the speech frames in `mask`. None when fewer than five speech frames are selected."""
    sel = mask[: len(F.act)] & F.act
    sv = sel & F.voiced
    if sel.sum() < 5:
        return None
    v = F.f0[sv]
    med = float(np.median(v)) if len(v) else float("nan")
    lv = F.db[sel]
    lv100 = np.array([lv[k:k + 10].mean() for k in range(0, len(lv) - 9, 10)]) if len(lv) >= 10 else lv
    f0_or_big = np.nan_to_num(F.f0, nan=1e9)
    low = (sv & (f0_or_big < min(115.0, 0.6 * med))) if len(v) else (sv & False)

    def med_or_none(a: np.ndarray) -> float | None:
        a = a[np.isfinite(a)]
        return round(float(np.median(a)), 2) if len(a) else None

    return {
        "voiced_frac": round(float(sv.sum() / sel.sum()), 3),
        "cpps_db": med_or_none(F.cpps[sel]),
        "hnr_db": med_or_none(F.hnr[sel]),
        "h1h2_db": med_or_none(F.h1h2[sv]),
        "tilt_db": med_or_none(F.tilt[sv]),
        "level_db": round(float(10 * np.log10(np.mean(10 ** (lv / 10)))), 2),
        "hf_noise_db": med_or_none(F.hf_noise[sv]),
        "f0_median_hz": round(med, 1) if len(v) else None,
        "f0_range_st": round(float(12 * math.log2(np.percentile(v, 95) / np.percentile(v, 5))), 2) if len(v) > 10
        else None,
        "f0_sd_st": round(float(np.std(12 * np.log2(v / med))), 2) if len(v) > 10 else None,
        "centroid_hz": round(float(np.median(F.centroid[sv])), 0) if sv.any() else None,
        "level_sd_db": round(float(np.std(lv100)), 2),
        "creak_frac": round(float(low.sum() / max(1, sv.sum())), 4),
        "voiced_frames": int(sv.sum()),
    }


def span(F: Frames, start_s: float | None) -> dict | None:
    """Measures from `start_s` to the end (None or 0: the whole take), with effort_level_db: the span's level
    against the rest of the same take when it starts mid-line (output gain cancels), else the plain level."""
    n = len(F.db)
    mask = np.zeros(n, bool)
    if not start_s:
        mask[:] = True
        sm = summarize(F, mask)
        if sm:
            sm["effort_level_db"] = sm["level_db"]
        return sm
    cut = max(0, int((start_s - 0.02) / HOP))
    mask[cut:] = True
    sm = summarize(F, mask)
    rest = np.zeros(n, bool)
    rest[:cut] = True
    pre = summarize(F, rest)
    if sm and pre:
        sm["effort_level_db"] = round(sm["level_db"] - pre["level_db"], 2)
        if sm.get("f0_median_hz") and pre.get("f0_median_hz"):
            sm["lift_vs_rest_st"] = round(12 * math.log2(sm["f0_median_hz"] / pre["f0_median_hz"]), 2)
    elif sm:
        sm["effort_level_db"] = sm["level_db"]
    return sm


def calibrate(baselines: dict[str, list[dict | None]]) -> dict:
    """Per-key means and pooled within-key SDs (with floors) from untagged summaries: {key: [summaries]}."""
    sds, means = {}, {}
    for k in BREATH:
        devs: list[float] = []
        dof = 0
        for ms in baselines.values():
            vals = [x[k] for x in ms if x and x.get(k) is not None]
            if len(vals) >= 2:
                devs += list(np.array(vals) - np.mean(vals))
                dof += len(vals) - 1
        sds[k] = max(FLOOR[k], float(np.sqrt(np.sum(np.square(devs)) / max(1, dof)))) if devs else FLOOR[k]
    for key, ms in baselines.items():
        means[key] = {}
        for k in BREATH:
            vals = [x[k] for x in ms if x and x.get(k) is not None]
            if vals:
                means[key][k] = float(np.mean(vals))
    return {"means": means, "sds": sds}


def breathiness(scope: dict, key: str, cal: dict) -> dict:
    """z per measure in the breathier direction against the untagged takes of the same words, the index (their
    mean) and the verdict. Breathier when the index is over 1, when two core measures are each over 1.5, or when
    the voice drops out (voicing more than 3 SDs down, with CPPS or level down too)."""
    base = cal["means"].get(key, {})
    z = {k: round(d * (scope[k] - base[k]) / cal["sds"][k], 2)
         for k, d in BREATH.items() if scope.get(k) is not None and k in base}
    index = round(float(np.mean(list(z.values()))), 2) if z else 0.0
    core_over = [k for k in CORE if z.get(k, 0) > 1.5]
    whisper = z.get("voiced_frac", 0) > 3 and (z.get("cpps_db", 0) > 1.5 or z.get("effort_level_db", 0) > 2)
    return {"z": z, "index": index, "core_over": core_over, "whisper": whisper,
            "breathier": index > 1.0 or len(core_over) >= 2 or whisper}
