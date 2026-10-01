"""The studio vocal chain's building blocks, in numpy and scipy only.

Every function here keeps a signal's length sample for sample, because the polish runs on takes the film is already cut
to: if a stage moved a word by one sample, the captions, the cue sheet and the picture would all be wrong.

- **Biquads** are the RBJ Audio EQ Cookbook peak and shelf filters (the same textbook formulas the films' chain used,
  so a tuning carries over unchanged). They run causally through `lfilter` (transposed direct form II).
- **The compressor** is a feed-forward peak compressor: a one-pole peak follower (attack when the level rises,
  release when it falls) and a power-law gain above the threshold. Its time constants follow the convention the films'
  chain used, a coefficient of exp(-2π·1000 / (rate · ms)), so "15 ms attack" means the same thing here as there.
  Checked against that chain on test signals: the same output to float32 precision.
- **The dynamic band cut** (the 2-5 kHz edge and the de-esser) splits the band out with a zero-phase filter and its
  exact complement (band + rest == input, sample for sample), measures the band's own 5 ms level, and turns only the
  band down while it runs hot, read a few milliseconds ahead the way a mastering de-esser does. With `relative_db` it
  acts only while the band dominates the whole sound, so consonants (S, SH, T) are cut and vowels are left alone.
- **The room** is a synthetic small room: early reflections from 3 to 24 ms and a short diffuse tail that darkens as
  it decays, seeded so every render of a take is identical, band-limited so it adds space but no mud and no hiss.

Nothing here knows about films, voices or brands; `slate.voice.polish` puts the chain together from a tuning.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal


def db_to_gain(value_db: float) -> float:
    return 10.0 ** (value_db / 20.0)


# ---------------------------------------------------------------------------------------------------------------
# Static filters


def highpass(x: np.ndarray, sr: int, hz: float, order: int = 2) -> np.ndarray:
    """A Butterworth high-pass (order 2 is 12 dB an octave): rumble and low thump out, nothing of the voice."""
    if not hz or hz <= 0 or hz >= sr / 2:
        return x.copy()
    sos = signal.butter(order, hz, "highpass", fs=sr, output="sos")
    return signal.sosfilt(sos, x)


def _clamp_hz(hz: float, sr: int) -> float:
    return float(min(max(hz, 2.0), 0.49 * sr))


def peak_coeffs(sr: int, hz: float, gain_db: float, q: float) -> tuple[np.ndarray, np.ndarray]:
    """RBJ peaking EQ: a bell of `gain_db` at `hz`, width `q`."""
    a_lin = math.sqrt(db_to_gain(gain_db))
    w = 2 * math.pi * _clamp_hz(hz, sr) / sr
    alpha = math.sin(w) / (2 * q)
    cs = math.cos(w)
    b = np.array([1 + alpha * a_lin, -2 * cs, 1 - alpha * a_lin])
    a = np.array([1 + alpha / a_lin, -2 * cs, 1 - alpha / a_lin])
    return b / a[0], a / a[0]


def shelf_coeffs(sr: int, hz: float, gain_db: float, q: float = 0.707, *, high: bool) -> tuple[np.ndarray, np.ndarray]:
    """RBJ low or high shelf of `gain_db` from `hz` (q 0.707 is the cookbook's maximally flat slope)."""
    a_lin = math.sqrt(db_to_gain(gain_db))
    w = 2 * math.pi * _clamp_hz(hz, sr) / sr
    cs = math.cos(w)
    beta = math.sin(w) * math.sqrt(a_lin) / q
    am, ap = a_lin - 1, a_lin + 1
    if not high:
        b = [a_lin * (ap - am * cs + beta), a_lin * 2 * (am - ap * cs), a_lin * (ap - am * cs - beta)]
        a = [ap + am * cs + beta, -2 * (am + ap * cs), ap + am * cs - beta]
    else:
        b = [a_lin * (ap + am * cs + beta), a_lin * -2 * (am + ap * cs), a_lin * (ap + am * cs - beta)]
        a = [ap - am * cs + beta, 2 * (am - ap * cs), ap - am * cs - beta]
    b, a = np.array(b), np.array(a)
    return b / a[0], a / a[0]


def peak(x: np.ndarray, sr: int, hz: float, gain_db: float, q: float) -> np.ndarray:
    if not gain_db:
        return x.copy()
    b, a = peak_coeffs(sr, hz, gain_db, q)
    return signal.lfilter(b, a, x)


def shelf(x: np.ndarray, sr: int, hz: float, gain_db: float, *, high: bool, q: float = 0.707) -> np.ndarray:
    if not gain_db:
        return x.copy()
    b, a = shelf_coeffs(sr, hz, gain_db, q, high=high)
    return signal.lfilter(b, a, x)


# ---------------------------------------------------------------------------------------------------------------
# Levels


def moving_mean(p: np.ndarray, n: int) -> np.ndarray:
    """`np.convolve(p, ones(n)/n, mode="same")`, computed with running sums so a long take stays fast.

    The window for sample i is [i - n//2, i + (n-1)//2], zero outside the signal, exactly as numpy's "same" mode
    aligns it. Tiny negative values from the subtraction are clamped to zero (power can't be negative)."""
    n = max(1, int(n))
    c = np.concatenate([[0.0], np.cumsum(p, dtype=np.float64)])
    idx = np.arange(len(p))
    hi = np.minimum(len(p), idx + (n - 1) // 2 + 1)
    lo = np.maximum(0, idx - n // 2)
    return np.maximum((c[hi] - c[lo]) / n, 0.0)


def env_db(y: np.ndarray, sr: int, win_ms: float) -> np.ndarray:
    """The signal's power over a centred `win_ms` window, in dB (per sample)."""
    n = max(1, int(win_ms * sr / 1000))
    return 10 * np.log10(moving_mean(y * y, n) + 1e-20)


# ---------------------------------------------------------------------------------------------------------------
# The compressor


def compressor(x: np.ndarray, sr: int, threshold_db: float, ratio: float, attack_ms: float,
               release_ms: float) -> np.ndarray:
    """A peak compressor: gain = (envelope / threshold) ** (1/ratio - 1) above the threshold, 1 below it.

    The envelope is a one-pole peak follower, coefficient exp(-2π·1000 / (sr · ms)) (attack while the level rises,
    release while it falls). A ratio of 1 is a straight copy."""
    if ratio <= 1.0:
        return x.copy()
    k = -2.0 * math.pi * 1000.0 / sr
    c_att = math.exp(k / attack_ms) if attack_ms >= 1e-3 else 0.0
    c_rel = math.exp(k / release_ms) if release_ms >= 1e-3 else 0.0
    level = np.abs(x).tolist()
    env = [0.0] * len(level)
    y = 0.0
    for i, v in enumerate(level):  # sample by sample: the branch makes this recursion non-linear
        y = v + (c_att if v > y else c_rel) * (y - v)
        env[i] = y
    e = np.asarray(env)
    thr = db_to_gain(threshold_db)
    gain = np.ones_like(e)
    over = e >= thr
    gain[over] = (e[over] / thr) ** (1.0 / ratio - 1.0)
    return x * gain


# ---------------------------------------------------------------------------------------------------------------
# The dynamic band cut (the 2-5 kHz edge and the de-esser)


def band_split(x: np.ndarray, sr: int, lo: float, hi: float, order: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """A zero-phase band and its exact complement: band + rest == x, sample for sample."""
    hi = min(hi, 0.45 * sr)
    if lo >= hi:
        return np.zeros_like(x), x.copy()
    sos = signal.butter(order, [lo, hi], "bandpass", fs=sr, output="sos")
    try:
        b = signal.sosfiltfilt(sos, x)
    except ValueError:  # shorter than the filter's padding: too short to split, so nothing is cut
        return np.zeros_like(x), x.copy()
    return b, x - b


def smooth(red: np.ndarray, sr: int, attack_ms: float, release_ms: float, lookahead_ms: float = 2.0,
           rate: float = 1000.0) -> np.ndarray:
    """A gain-reduction contour (dB, >= 0) at a 1 kHz control rate: it rises at the attack time and falls at the
    release time, read `lookahead_ms` early so a cut is in place by the start of the sound that triggers it."""
    hop = max(1, int(sr / rate))
    whole = len(red) // hop * hop
    if whole == 0:
        return np.zeros_like(red)
    r = red[:whole].reshape(-1, hop).max(axis=1)
    a = math.exp(-1 / (attack_ms * rate / 1000)) if attack_ms > 0 else 0.0
    rl = math.exp(-1 / (release_ms * rate / 1000)) if release_ms > 0 else 0.0
    out = np.zeros_like(r)
    g = 0.0
    for i, v in enumerate(r.tolist()):
        g = a * g + (1 - a) * v if v > g else rl * g + (1 - rl) * v
        out[i] = g
    la = int(round(lookahead_ms * rate / 1000))
    if la:
        out = np.concatenate([out[la:], np.full(la, out[-1] if len(out) else 0.0)])
    t = (np.arange(len(out)) + 0.5) * hop
    return np.interp(np.arange(len(red)), t, out)


def dynamic_band(x: np.ndarray, sr: int, p: dict, stats: dict | None = None) -> np.ndarray:
    """Turn one band down while it runs hot. `p`: band_hz [lo, hi], threshold_db (dBFS on the take at the chain's
    working level), ratio, max_cut_db, attack_ms, release_ms, and optionally relative_db (act only while the band
    climbs from relative_db to relative_db + 6 dB against the whole sound). `stats` collects what it did."""
    lo, hi = p["band_hz"]
    if not p.get("max_cut_db") or p.get("ratio", 1.0) <= 1.0:
        if stats is not None:
            stats.update({"max_cut_db": 0.0, "time_cutting_pct": 0.0, "mean_cut_while_cutting_db": 0.0})
        return x.copy()
    b, rest = band_split(x, sr, lo, hi)
    lv = env_db(b, sr, 5.0)
    over = np.maximum(lv - p["threshold_db"], 0.0)
    red = np.minimum(over * (1 - 1 / p["ratio"]), p["max_cut_db"])
    full = env_db(x, sr, 5.0)
    if p.get("relative_db") is not None:
        red = red * np.clip((lv - full - p["relative_db"]) / 6.0, 0.0, 1.0)
    red = smooth(red, sr, p["attack_ms"], p["release_ms"], lookahead_ms=float(p.get("lookahead_ms", 2.0)))
    if stats is not None:
        speech = full > np.percentile(full, 95) - 35
        cutting = red > 0.5
        stats.update({"max_cut_db": round(float(red.max()), 2) if len(red) else 0.0,
                      "time_cutting_pct": round(100 * float(np.mean(cutting[speech])), 1) if speech.any() else 0.0,
                      "mean_cut_while_cutting_db": round(float(red[cutting].mean()), 2) if cutting.any() else 0.0})
    return rest + b * (10.0 ** (-red / 20.0))


# ---------------------------------------------------------------------------------------------------------------
# The room

REFLECTIONS = ((3.1, -6), (5.3, -8), (7.9, -9), (11.2, -11), (14.6, -12), (18.3, -14), (23.7, -16))  # (ms, dB)


def room_ir(sr: int, rt60_s: float, seed: int = 7, length_s: float = 0.5) -> np.ndarray:
    """A small, treated room's impulse response: early reflections (3-24 ms) and a diffuse tail that decays faster in
    the highs. Unit energy, and deterministic (seeded), so every render of a take is identical."""
    rng = np.random.default_rng(seed)
    n = int(length_s * sr)
    ir = np.zeros(n)
    for t_ms, g_db in REFLECTIONS:
        ir[int(t_ms * sr / 1000)] += db_to_gain(g_db) * rng.choice((-1.0, 1.0))
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    tail = np.zeros(n)
    for (lo, hi), k in (((None, 1500), 1.15), ((1500, 5000), 1.0), ((5000, None), 0.55)):
        if lo is None:
            sos = signal.butter(4, hi, "lowpass", fs=sr, output="sos")
        elif hi is None:
            sos = signal.butter(4, min(lo, 0.45 * sr), "highpass", fs=sr, output="sos")
        else:
            sos = signal.butter(4, [lo, min(hi, 0.45 * sr)], "bandpass", fs=sr, output="sos")
        tail += signal.sosfilt(sos, noise) * np.exp(-6.91 * t / (rt60_s * k))
    onset = int(0.012 * sr)
    ramp = np.clip((np.arange(n) - onset) / (0.010 * sr), 0, 1)
    ir += tail * ramp * db_to_gain(-14)
    return ir / np.sqrt(np.sum(ir ** 2))


def room(x: np.ndarray, sr: int, wet_db: float, rt60_s: float, band_hz: tuple[float, float] = (220.0, 6500.0),
         seed: int = 7) -> np.ndarray:
    """The dry take plus the room `wet_db` under it (band-limited, so space without mud or hiss)."""
    if wet_db is None or wet_db <= -120:
        return x.copy()
    ir = room_ir(sr, rt60_s, seed=seed)
    wet = signal.fftconvolve(x, ir)[: len(x)]
    sos = signal.butter(2, [band_hz[0], min(band_hz[1], 0.45 * sr)], "bandpass", fs=sr, output="sos")
    wet = signal.sosfilt(sos, wet)
    return x + wet * db_to_gain(wet_db)
