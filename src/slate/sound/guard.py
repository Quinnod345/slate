"""The composed score's own clarity guard: the mixer's hand riding the score under a line.

From the films' p3 balance (the score bus's guard, approved with the p3 mix on 2026-09-30): wherever the score on its
own comes within `target_db` of the voice in `band_hz` during a word, the score's top (above `split_hz`) dips under
that word by just enough, never more than `max_dip_db`. A dip holds `hold_s` either side of its word and ramps in and
out over `ramp_s` (a raised cosine), so it moves at a phrase's pace, not a syllable's; where two dips meet, the deeper
one wins. Everywhere else the score plays exactly as composed: the split is exact (low + high == score), and a word
the score already clears is left alone.

Why 19 dB: the clarity rule lets everything other than the voice come to 12 dB under it in 1-5 kHz, and the score keeps
to a quarter of that, so the accents that carry the picture's beats keep the rest. Why 600 Hz: a 1 kHz split leaves too
much of the band's bottom octave. A score is music, not a hit: it has no sync, so the guard only ever lowers its top.

The levels are the brand's sound palette (sound.palette.balance.score_guard, over slate.sound.balance's starting
balance); `slate mix` runs it on the score after the duck, and `sound.balance.clarity`'s score-alone floor checks the
result word by word.

    guard.apply(music, voice, words, sr, settings) -> (music, report)
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy import signal

from slate.sound import measure

HOP_S = 0.01  # the guard's frames: 10 ms
DEFAULT = {"band_hz": [1000.0, 5000.0], "target_db": 19.0, "split_hz": 600.0, "max_dip_db": -20.0, "hold_s": 0.15,
           "ramp_s": 0.3, "within_db": 30.0, "min_word_s": 0.12, "search_s": 0.15}


def _stereo(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x[:, None] if x.ndim == 1 else x


def power_frames(pw: np.ndarray, m: int, hop: int) -> np.ndarray:
    """Per-sample power to its mean per frame (m frames of `hop` samples)."""
    pw = np.pad(pw, (0, max(0, m * hop - len(pw))))[: m * hop]
    return pw.reshape(m, hop).mean(axis=1)


def band_power(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    """Per-sample power (summed over channels) in a band: a zero-phase 4th-order Butterworth band-pass."""
    hi = min(hi, sr / 2 * 0.98)
    sos = signal.butter(4, [lo, hi], "bandpass", fs=sr, output="sos")
    return (signal.sosfiltfilt(sos, _stereo(x), axis=0) ** 2).sum(axis=1)


def split_band(x: np.ndarray, sr: int, hz: float) -> tuple[np.ndarray, np.ndarray]:
    """(low, high) with low + high == x exactly: a zero-phase 2nd-order Butterworth low-pass, and the rest."""
    sos = signal.butter(2, hz, "lowpass", fs=sr, output="sos")
    low = signal.sosfiltfilt(sos, x, axis=0)
    return low, x - low


def speaking(voice: np.ndarray, sr: int, hop: int, *, within_db: float = 30.0) -> np.ndarray:
    """Per frame: the voice is sounding (its 50 ms K-weighted level within within_db of its loud level, the 95th
    percentile), so a word counts only where the voice actually sounds (Whisper's word times run early or late)."""
    z = signal.sosfilt(measure.k_weighting_sos(sr), _stereo(voice), axis=0)
    p = (z ** 2).sum(axis=1)
    m = len(p) // hop
    if m == 0:
        return np.zeros(0, dtype=bool)
    ph = p[: m * hop].reshape(m, hop).mean(axis=1)
    w = max(1, int(round(0.05 / HOP_S)))
    lv = -0.691 + 10 * np.log10(np.maximum(np.convolve(ph, np.ones(w) / w, mode="same"), 1e-20))
    live = lv > -70
    loud = float(np.percentile(lv[live], 95)) if live.any() else 0.0
    return lv >= loud - within_db


def word_frames(spk: np.ndarray, words: Iterable[tuple[str, float, float]], sr: int, hop: int, *,
                min_word_s: float = 0.12, search_s: float = 0.15) -> tuple[list[tuple], list[np.ndarray]]:
    """Each spoken word's frames where the voice sounds: a word shorter than min_word_s is measured over min_word_s
    centred on it, and one whose span misses the voice is looked for search_s either side."""
    rows, frames = [], []
    n = len(spk) * hop
    look = int(round(search_s / HOP_S))
    for w, s, e in words:
        text = str(w)
        if not any(ch.isalnum() for ch in text) and not any(ch in "%&$€£+@#" for ch in text):
            continue  # a dash on its own is a pause in the line, not a word
        a, b = int(round(s * sr)), int(round(e * sr))
        if b - a < int(min_word_s * sr):
            c = (a + b) // 2
            a, b = c - int(min_word_s / 2 * sr), c + int(min_word_s / 2 * sr)
        if a < 0 or b > n:
            continue
        fr = np.arange(a // hop, min(b // hop, len(spk)))
        fr = fr[spk[fr]]
        if len(fr) < 3:
            fr = np.arange(max(0, a // hop - look), min(b // hop + look, len(spk)))
            fr = fr[spk[fr]]
        rows.append((text, round(float(s), 3), round(float(e), 3)))
        frames.append(fr)
    return rows, frames


def settings_of(palette_balance: dict | None) -> dict | None:
    """The guard's settings from a brand's balance (None when it turns the guard off with `score_guard: false`)."""
    g = (palette_balance or {}).get("score_guard", {})
    if g is False or g is None:
        return None
    return {**DEFAULT, **(g if isinstance(g, dict) else {})}


def apply(music: np.ndarray, voice: np.ndarray, words: list[tuple[str, float, float]], sr: int,
          settings: dict | None = None) -> tuple[np.ndarray, dict]:
    """Dip the score's top under the words it would crowd, by just enough. Returns (music, report). The caller's array
    is never modified; with nothing to do, the same samples come back."""
    g = {**DEFAULT, **(settings or {})}
    music = _stereo(music)
    n = len(music)
    lo_hz, hi_hz = (float(v) for v in g["band_hz"])
    target, cap = float(g["target_db"]), float(g["max_dip_db"])
    hold, ramp = float(g["hold_s"]), float(g["ramp_s"])
    hop = max(1, int(round(HOP_S * sr)))
    report = {"target_db": target, "band_hz": [lo_hz, hi_hz], "split_hz": float(g["split_hz"]), "max_dip_db": cap,
              "hold_s": hold, "ramp_s": ramp, "words": 0, "words_dipped": 0, "deepest_db": 0.0, "time_dipped_s": 0.0,
              "alone_worst_before_db": None, "alone_worst_after_db": None, "dips": [], "capped": []}
    if not words or not np.any(music) or not np.any(voice):
        return music, report
    spk = speaking(voice, sr, hop, within_db=float(g["within_db"]))
    m = len(spk)
    rows, frames = word_frames(spk, words, sr, hop, min_word_s=float(g["min_word_s"]), search_s=float(g["search_s"]))
    keep = [i for i, fr in enumerate(frames) if len(fr)]
    if not keep or m == 0:
        return music, report
    v_band = power_frames(band_power(voice, sr, lo_hz, hi_hz), m, hop)
    low, high = split_band(music, sr, float(g["split_hz"]))
    sos = signal.butter(4, [lo_hz, min(hi_hz, sr / 2 * 0.98)], "bandpass", fs=sr, output="sos")
    a_band, b_band = signal.sosfiltfilt(sos, low, axis=0), signal.sosfiltfilt(sos, high, axis=0)

    def margins(gain: np.ndarray) -> dict[int, float]:
        p = ((a_band + b_band * gain[:, None]) ** 2).sum(axis=1)
        bf = power_frames(p, m, hop)
        return {i: float(10 * np.log10((v_band[frames[i]].mean() + 1e-20) / (bf[frames[i]].mean() + 1e-20)))
                for i in keep}

    def curve(dips: dict[int, float]) -> tuple[np.ndarray, np.ndarray]:
        c = np.zeros(m)
        k = int(round(hold / HOP_S))
        for i, d in dips.items():
            fr = frames[i]
            a, b = max(int(fr.min()) - k, 0), min(int(fr.max()) + 1 + k, m)
            c[a:b] = np.minimum(c[a:b], d)
        w = max(3, int(round(ramp / HOP_S)) | 1)
        win = np.hanning(w + 2)[1:-1]
        c = np.convolve(np.pad(c, (w // 2, w // 2), mode="edge"), win / win.sum(), mode="valid")[:m]
        return 10 ** (np.interp(np.arange(n) / hop, np.arange(m), c) / 20), c

    before = margins(np.ones(n))
    dips: dict[int, float] = {}
    gain, c = np.ones(n), np.zeros(m)
    for _ in range(6):  # a dip at the top does a little less in the band (the split is gentle): settle in a few steps
        now = margins(gain) if dips else before
        short = {i: target - v for i, v in now.items() if v < target - 0.05 and dips.get(i, 0.0) > cap}
        if not short:
            break
        for i, s in short.items():
            dips[i] = max(cap, dips.get(i, 0.0) - s - 0.1)
        gain, c = curve(dips)
    after = margins(gain) if dips else before
    out = low + high * gain[:, None] if dips else music
    listed = [{"word": rows[i][0], "s": rows[i][1], "alone_db": round(before[i], 1), "dip_db": round(d, 1),
               "after_db": round(after[i], 1)} for i, d in sorted(dips.items(), key=lambda kv: rows[kv[0]][1])]
    report.update({"words": len(before), "words_dipped": len(dips),
                   "deepest_db": round(float(c.min()), 1) if dips else 0.0,
                   "time_dipped_s": round(float((c < -0.5).sum() * HOP_S), 2),
                   "alone_worst_before_db": round(min(before.values()), 1),
                   "alone_worst_after_db": round(min(after.values()), 1),
                   "dips": listed, "capped": [f"{d['word']}@{d['s']}" for d in listed if d["dip_db"] <= cap + 0.05]})
    if not math.isfinite(report["alone_worst_after_db"]):
        report["alone_worst_after_db"] = None
    return out, report
