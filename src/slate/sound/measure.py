"""Loudness and peaks, measured the ITU-R BS.1770-4 way, so every sound gate names a meter it can defend.

What's implemented here (no ffmpeg needed):

- K-weighting: the two-stage pre-filter (a high shelf at about 1.68 kHz, +4 dB, then the RLB high-pass at about
  38 Hz), with coefficients derived for any sample rate from the analogue prototype, so 44.1 kHz and 96 kHz work too.
  At 48 kHz they reproduce the table in BS.1770-4 to the printed precision.
- Integrated loudness with the standard's gating: 400 ms blocks with 75 % overlap, an absolute gate at -70 LUFS and a
  relative gate 10 LU under the absolute-gated level. Channel weights are 1.0 for left, right and centre and 1.41 for
  the surrounds (the LFE is ignored).
- True peak: the signal oversampled 4x (2x at 96 kHz) through a 193-tap Kaiser-windowed interpolator, then the
  largest absolute value. That's the standard's method with a better filter than its worked example; on steady
  sines up to 21 kHz it reads within 0.005 dB of the analytic peak.
- Momentary (400 ms) and short-term (3 s) loudness, and the loudness range (EBU Tech 3342: short-term values every
  100 ms, gated at -70 LUFS and 20 LU under their power mean, then the 10th to 95th percentile).

`cross_check()` runs ffmpeg's ebur128 meter on the same file when ffmpeg is installed, and gates put both numbers
in their evidence. When the two disagree by more than a hair, the gate says so instead of trusting either quietly.
"""

from __future__ import annotations

import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy import signal

from slate.sound import audio

ABS_GATE_LUFS = -70.0
REL_GATE_LU = -10.0
LRA_REL_GATE_LU = -20.0
SILENT_FLOOR = -120.0  # what a silent (or fully gated) signal reports, so JSON never carries -inf
BLOCK_S, STEP_S, SHORT_S = 0.4, 0.1, 3.0
_CHUNK_S = 20.0

ENGINE = {"name": "slate bs1770-4", "standard": "ITU-R BS.1770-4 / EBU Tech 3341, 3342"}


def engine_info() -> dict:
    from slate import __version__  # noqa: PLC0415

    return {**ENGINE, "version": __version__}


# ---------------------------------------------------------------------------------------------------------------
# Filters


@lru_cache(maxsize=16)
def k_weighting_sos(sr: int) -> np.ndarray:
    """The two K-weighting biquads for a sample rate, as second-order sections (b0 b1 b2 a0 a1 a2)."""
    # Stage 1: the head's acoustic effect, a high shelf (the prototype behind BS.1770-4's 48 kHz table).
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10 ** (gain_db / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    s1 = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
          1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    # Stage 2: the revised low-frequency B-curve (RLB), a high-pass.
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    d = 1 + k / q + k * k
    s2 = [1.0, -2.0, 1.0, 1.0, 2 * (k * k - 1) / d, (1 - k / q + k * k) / d]
    return np.array([s1, s2], dtype=np.float64)


def oversample_factor(sr: int) -> int:
    return 4 if sr <= 48000 else (2 if sr <= 96000 else 1)


@lru_cache(maxsize=8)
def _interp_filter(factor: int) -> np.ndarray:
    half = 24 * factor
    return signal.firwin(2 * half + 1, 1.0 / factor, window=("kaiser", 8.0))


def channel_weights(channels: int) -> np.ndarray:
    """BS.1770 weights: L, R, C at 1.0, the surrounds at 1.41, the LFE (4th of six) ignored."""
    if channels == 6:
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41])
    if channels == 5:
        return np.array([1.0, 1.0, 1.0, 1.41, 1.41])
    return np.ones(channels)


def _as_2d(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x[:, None] if x.ndim == 1 else x


# ---------------------------------------------------------------------------------------------------------------
# Streaming accumulators (a whole film is measured in 20-second chunks, never loaded twice)


class _Meter:
    """Feeds on chunks; keeps K-weighted power per 100 ms step and the running true and sample peaks."""

    def __init__(self, sr: int, channels: int, true_peak: bool = True):
        self.sr, self.channels = sr, channels
        self.step = int(round(STEP_S * sr))
        self.sos = k_weighting_sos(sr)
        self.zi = [signal.sosfilt_zi(self.sos) * 0.0 for _ in range(channels)]
        self.carry = np.zeros((0, channels))
        self.steps: list[np.ndarray] = []  # per-channel sum of squares for each full 100 ms step
        self.sample_peak = 0.0
        self.tp = true_peak
        self.factor = oversample_factor(sr)
        self.tp_peak = 0.0
        # Samples whose interpolation still needs right-hand context wait here (with left context before them), so
        # a chunk's abrupt end is never mistaken for a real one: each sample is counted once, with full filter support.
        self.tp_hold = np.zeros((0, channels))
        self.tp_counted = 0  # how many of tp_hold's leading samples are context only (already counted)
        self.frames = 0

    def feed(self, x: np.ndarray) -> None:
        x = _as_2d(x)
        if x.shape[1] != self.channels:
            raise ValueError("channel count changed mid-stream")
        self.frames += len(x)
        if len(x):
            self.sample_peak = max(self.sample_peak, float(np.max(np.abs(x))))
        y = np.empty_like(x)
        for c in range(self.channels):
            y[:, c], self.zi[c] = signal.sosfilt(self.sos, x[:, c], zi=self.zi[c])
        y = np.concatenate([self.carry, y ** 2])
        full = (len(y) // self.step) * self.step
        if full:
            self.steps.extend(y[:full].reshape(-1, self.step, self.channels).sum(axis=1))
        self.carry = y[full:]
        if self.tp and len(x):
            self._feed_tp(x)

    def _feed_tp(self, x: np.ndarray) -> None:
        if self.factor == 1:
            self.tp_peak = max(self.tp_peak, float(np.max(np.abs(x))))
            return
        self.tp_hold = np.concatenate([self.tp_hold, x])
        self._count_tp(final=False)

    def _count_tp(self, final: bool) -> None:
        """Count the held samples that now have full context (all of them at the end of the signal)."""
        if self.factor == 1 or not len(self.tp_hold):
            return
        h = _interp_filter(self.factor)
        margin = len(h) // self.factor + 2
        block = self.tp_hold
        start = self.tp_counted
        stop = len(block) if final else len(block) - margin
        if stop <= start:
            return
        up = signal.resample_poly(block, self.factor, 1, axis=0, window=h)
        seg = up[start * self.factor: stop * self.factor]
        if len(seg):
            self.tp_peak = max(self.tp_peak, float(np.max(np.abs(seg))))
        keep_from = max(0, stop - margin)  # left context for what's still waiting
        self.tp_hold = block[keep_from:]
        self.tp_counted = stop - keep_from

    def finish(self) -> None:
        if self.tp:
            self._count_tp(final=True)

    def powers(self) -> np.ndarray:
        return np.array(self.steps) if self.steps else np.zeros((0, self.channels))


def _block_loudness(step_pows: np.ndarray, n_steps: int, weights: np.ndarray, step: int) -> np.ndarray:
    """Loudness of windows of n_steps consecutive 100 ms steps (hop 100 ms), in LUFS (-inf for silence)."""
    if len(step_pows) < n_steps:
        return np.zeros(0)
    csum = np.concatenate([np.zeros((1, step_pows.shape[1])), np.cumsum(step_pows, axis=0)])
    win = (csum[n_steps:] - csum[:-n_steps]) / (n_steps * step)
    z = win @ weights
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(np.maximum(z, 0.0))


def _gated_mean_power(z: np.ndarray, loud: np.ndarray, rel_lu: float) -> tuple[float, np.ndarray]:
    """(gated loudness, mask) for block powers z with their loudness, using the absolute and a relative gate."""
    keep = loud > ABS_GATE_LUFS
    if not keep.any():
        return -math.inf, keep
    rel = -0.691 + 10 * math.log10(float(z[keep].mean())) + rel_lu
    keep = keep & (loud > rel)
    if not keep.any():
        return -math.inf, keep
    return -0.691 + 10 * math.log10(float(z[keep].mean())), keep


def _summarize(meter: _Meter, lra: bool) -> dict:
    meter.finish()
    sr, step = meter.sr, meter.step
    pows = meter.powers()
    weights = channel_weights(meter.channels)
    n_block = int(round(BLOCK_S / STEP_S))
    momentary = _block_loudness(pows, n_block, weights, step)
    out: dict = {}
    if len(momentary):
        csum = np.concatenate([np.zeros((1, pows.shape[1])), np.cumsum(pows, axis=0)])
        z = ((csum[n_block:] - csum[:-n_block]) / (n_block * step)) @ weights
        integrated, _ = _gated_mean_power(z, momentary, REL_GATE_LU)
    else:
        integrated = -math.inf
    out["integrated_lufs"] = _floor(integrated)
    out["silent"] = not math.isfinite(integrated)
    out["momentary_max_lufs"] = _floor(float(np.max(momentary))) if len(momentary) else SILENT_FLOOR
    n_short = int(round(SHORT_S / STEP_S))
    short = _block_loudness(pows, n_short, weights, step)
    out["short_term_max_lufs"] = _floor(float(np.max(short))) if len(short) else SILENT_FLOOR
    if lra:
        out["lra_lu"] = _lra(short)
    out["sample_peak_dbfs"] = _floor(20 * math.log10(meter.sample_peak)) if meter.sample_peak > 0 else SILENT_FLOOR
    if meter.tp:
        tp = max(meter.tp_peak, meter.sample_peak)
        out["true_peak_dbtp"] = _floor(20 * math.log10(tp)) if tp > 0 else SILENT_FLOOR
        out["oversampling"] = meter.factor
    out["sample_rate"] = sr
    out["channels"] = meter.channels
    out["duration_s"] = round(meter.frames / sr, 6)
    out["too_short"] = len(momentary) == 0
    return out


def _lra(short: np.ndarray) -> float:
    if not len(short):
        return 0.0
    z = 10 ** ((short + 0.691) / 10)
    _, keep = _gated_mean_power(z, short, LRA_REL_GATE_LU)
    vals = short[keep]
    if len(vals) < 2:
        return 0.0
    lo, hi = np.percentile(vals, [10, 95])
    return round(float(hi - lo), 2)


def _floor(v: float) -> float:
    return float(v) if math.isfinite(v) and v > SILENT_FLOOR else SILENT_FLOOR


# ---------------------------------------------------------------------------------------------------------------
# The public meter


def loudness(source: str | os.PathLike | np.ndarray, sr: int | None = None, *, true_peak: bool = True,
             lra: bool = True) -> dict:
    """Measure a file or a signal: integrated LUFS, true peak dBTP, LRA, momentary and short-term maxima, sample peak.

    `source` is a path (WAV read in chunks; other formats decoded by ffmpeg) or an array shaped (samples, channels)
    with `sr` given. Silence reports -120 with "silent": true, so callers can compare without special cases.
    """
    if isinstance(source, (str, os.PathLike)):
        p = Path(source)
        if audio.is_wav(p):
            info = audio.wav_info(p)
            meter = _Meter(info.sample_rate, info.channels, true_peak)
            for block in audio.iter_wav(p, int(_CHUNK_S * info.sample_rate)):
                meter.feed(block)
        else:
            rate = sr or audio.DEFAULT_SR
            x = audio.load(p, rate)
            meter = _Meter(rate, x.shape[1], true_peak)
            _feed_chunks(meter, x)
        result = _summarize(meter, lra)
        result["file"] = str(p)
    else:
        if sr is None:
            raise ValueError("loudness(array) needs the sample rate: loudness(x, sr)")
        x = _as_2d(source)
        meter = _Meter(int(sr), x.shape[1], true_peak)
        _feed_chunks(meter, x)
        result = _summarize(meter, lra)
    result["engine"] = engine_info()
    return result


def _feed_chunks(meter: _Meter, x: np.ndarray) -> None:
    size = int(_CHUNK_S * meter.sr)
    for i in range(0, max(len(x), 1), size):
        meter.feed(x[i:i + size])


def integrated(x: np.ndarray, sr: int) -> float:
    """Integrated loudness only (fast path for the mix loop). -120 for silence."""
    meter = _Meter(int(sr), _as_2d(x).shape[1], true_peak=False)
    _feed_chunks(meter, _as_2d(x))
    return _summarize(meter, lra=False)["integrated_lufs"]


def peak_envelope(x: np.ndarray, sr: int, factor: int | None = None) -> np.ndarray:
    """Per input sample, the largest |value| over the oversampled points up to the next sample, across channels.

    This is what the limiters work from: a gain that keeps every entry under the ceiling keeps the true peak there.
    """
    x = _as_2d(x)
    factor = factor or oversample_factor(sr)
    n = len(x)
    if factor == 1 or n == 0:
        return np.abs(x).max(axis=1) if n else np.zeros(0)
    h = _interp_filter(factor)
    margin = len(h) // factor + 2
    out = np.empty(n)
    size = int(_CHUNK_S * sr)
    for i in range(0, n, size):
        a, b = max(0, i - margin), min(n, i + size + margin)
        up = signal.resample_poly(x[a:b], factor, 1, axis=0, window=h)
        env = np.abs(up).max(axis=1)
        env = env[: (len(env) // factor) * factor].reshape(-1, factor).max(axis=1)
        j = min(n, i + size)
        out[i:j] = np.maximum(env[i - a:i - a + (j - i)], np.abs(x[i:j]).max(axis=1))
    return out


def true_peak(x: np.ndarray, sr: int) -> float:
    """True peak in dBTP (-120 for silence)."""
    env = peak_envelope(x, sr)
    pk = float(env.max()) if len(env) else 0.0
    return _floor(20 * math.log10(pk)) if pk > 0 else SILENT_FLOOR


def momentary(x: np.ndarray, sr: int) -> np.ndarray:
    """Momentary loudness (400 ms) every 100 ms, LUFS; the first value covers 0-0.4 s."""
    meter = _Meter(int(sr), _as_2d(x).shape[1], true_peak=False)
    _feed_chunks(meter, _as_2d(x))
    vals = _block_loudness(meter.powers(), int(round(BLOCK_S / STEP_S)), channel_weights(meter.channels), meter.step)
    return np.maximum(vals, SILENT_FLOOR)


def short_term(x: np.ndarray, sr: int) -> np.ndarray:
    """Short-term loudness (3 s) every 100 ms, LUFS."""
    meter = _Meter(int(sr), _as_2d(x).shape[1], true_peak=False)
    _feed_chunks(meter, _as_2d(x))
    vals = _block_loudness(meter.powers(), int(round(SHORT_S / STEP_S)), channel_weights(meter.channels), meter.step)
    return np.maximum(vals, SILENT_FLOOR)


def rms_dbfs(x: np.ndarray) -> float:
    """RMS over every sample and channel, in dBFS (the gap check's measure)."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return SILENT_FLOOR
    return max(SILENT_FLOOR, 20 * math.log10(math.sqrt(float(np.mean(x ** 2))) + 1e-12))


def window_rms_dbfs(x: np.ndarray, sr: int, window_s: float = 0.5) -> list[tuple[float, float]]:
    """[(window start s, RMS dBFS)] for consecutive windows (the last partial window is dropped, as the films did)."""
    x = np.asarray(x, dtype=np.float64)
    w = int(round(window_s * sr))
    k = len(x) // w
    if k == 0:
        return []
    sq = (x[: k * w] ** 2).reshape(k, w, -1).mean(axis=(1, 2)) if x.ndim == 2 else (x[: k * w] ** 2).reshape(k, w).mean(axis=1)
    db = 20 * np.log10(np.sqrt(sq) + 1e-12)
    return [(round(i * window_s, 6), float(v)) for i, v in enumerate(db)]


def lu_vs(reference_lufs: float, stem_lufs: float) -> float | None:
    """Loudness of a stem against a reference (the voice), in LU; None when either is silent."""
    if reference_lufs <= SILENT_FLOOR or stem_lufs <= SILENT_FLOOR:
        return None
    return round(stem_lufs - reference_lufs, 2)


# ---------------------------------------------------------------------------------------------------------------
# ffmpeg's meter, for cross-checks


def ffmpeg_version() -> str | None:
    from slate import procs  # noqa: PLC0415

    if not procs.which("ffmpeg"):
        return None
    out = procs.run(["ffmpeg", "-hide_banner", "-version"], timeout=30)
    m = re.search(r"ffmpeg version (\S+)", out.stdout or "")
    return m.group(1) if m else "unknown"


def ffmpeg_ebur128(path: str | os.PathLike) -> dict | None:
    """ffmpeg's EBU R128 meter on a file: {integrated_lufs, lra_lu, true_peak_dbtp}. None when ffmpeg is missing."""
    from slate import procs  # noqa: PLC0415

    if not procs.which("ffmpeg"):
        return None
    proc = procs.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-af",
                      "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], timeout=1800)
    err = proc.stderr or ""
    i = re.findall(r"^\s+I:\s+(-?[\d.]+|-inf) LUFS", err, re.M)
    lra = re.findall(r"^\s+LRA:\s+(-?[\d.]+) LU\s*$", err, re.M)
    tp = re.findall(r"^\s+Peak:\s+(-?[\d.]+|-inf) dBFS", err, re.M)
    if not i:
        return None

    def num(v: str) -> float:
        return SILENT_FLOOR if v == "-inf" else float(v)

    return {"integrated_lufs": num(i[-1]), "lra_lu": float(lra[-1]) if lra else None,
            "true_peak_dbtp": num(tp[-1]) if tp else None,
            "engine": {"name": "ffmpeg ebur128", "version": ffmpeg_version()}}


def cross_check(path: str | os.PathLike, ours: dict | None = None, *, lufs_tol: float = 0.2,
                tp_tol: float = 0.3) -> dict:
    """Slate's meter and ffmpeg's on one file, with the differences and whether they agree within tolerance."""
    ours = ours or loudness(path)
    theirs = ffmpeg_ebur128(path)
    out = {"slate": {k: ours.get(k) for k in ("integrated_lufs", "true_peak_dbtp", "lra_lu")}, "ffmpeg": theirs}
    if theirs is None:
        out.update({"available": False, "agree": None})
        return out
    d_i = round(ours["integrated_lufs"] - theirs["integrated_lufs"], 3)
    d_tp = (round(ours["true_peak_dbtp"] - theirs["true_peak_dbtp"], 3)
            if theirs.get("true_peak_dbtp") is not None and "true_peak_dbtp" in ours else None)
    agree = abs(d_i) <= lufs_tol and (d_tp is None or abs(d_tp) <= tp_tol)
    out.update({"available": True, "delta_integrated_lu": d_i, "delta_true_peak_db": d_tp, "agree": agree,
                "tolerance": {"integrated_lu": lufs_tol, "true_peak_db": tp_tol}})
    return out


def describe(results: Iterable[dict]) -> str:
    """One line per measurement, for people."""
    lines = []
    for r in results:
        lines.append(f"{Path(r.get('file', '(signal)')).name}: {r['integrated_lufs']:.1f} LUFS integrated, "
                     f"{r.get('true_peak_dbtp', float('nan')):.1f} dBTP, LRA {r.get('lra_lu', 0):.1f} LU")
    return "\n".join(lines)
