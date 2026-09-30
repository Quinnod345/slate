"""Take QA for Eleven Music's two failure modes: holes of silence and stretches of broadband noise.

Per 0.5 s window of the take (mono): its RMS level, its spectral flatness from 200 Hz to 8 kHz (music sits well under
0.2; white noise is near 1) and how peaky it is from 150 Hz to 2 kHz (log10 of max over median; music is tonal and
peaky, 10^5 to 10^7, noise dull, under 10^3.5). Silence is RMS under -45 dBFS; noise is flat (over 0.03) or dull, and
louder than -50 dBFS. A problem is either lasting 1.5 s or more, ignoring the first and last second (a take's fades are
fine there). The score is the seconds of problems: lower is better, and a clean take scores 0.

Its blind spots (holes under 1.5 s, and the last second) are why the gap check exists too.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from slate.sound import audio

WINDOW_S = 0.5
SILENCE_DBFS = -45.0
NOISE_FLOOR_DBFS = -50.0
MIN_SPAN_S = 1.5
EDGE_S = 1.0


def analyse(x: np.ndarray, sr: int) -> dict:
    """{duration, problems[{kind, from, to}], score, rms_median} for a signal (mono or stereo)."""
    m = audio.mono(np.asarray(x, dtype=np.float64))
    win = int(sr * WINDOW_S)
    rows = []
    f = np.fft.rfftfreq(win, 1 / sr)
    band_flat = (f > 200) & (f < 8000)
    band_peak = (f > 150) & (f < 2000)
    hann = np.hanning(win)
    for i in range(0, len(m) - win, win):
        seg = m[i:i + win]
        rms = 20 * math.log10(math.sqrt(float(np.mean(seg ** 2))) + 1e-12)
        p = np.abs(np.fft.rfft(seg * hann)) ** 2 + 1e-18
        band = p[band_flat]
        flat = float(np.exp(np.mean(np.log(band))) / np.mean(band))
        low = p[band_peak]
        peak = float(np.log10(np.max(low) / np.median(low)))
        noisy = 1.0 if (flat > 0.03 or peak < 3.5) else 0.0
        rows.append((i / sr, rms, noisy))
    dur = len(m) / sr
    problems: list[dict] = []

    def spans(flags: list[bool], kind: str) -> None:
        start = None
        for (t, _, _), flag in zip(rows + [(dur, 0.0, 0.0)], flags + [False]):
            if flag and start is None:
                start = t
            if not flag and start is not None:
                if t - start >= MIN_SPAN_S and start >= EDGE_S and t <= dur - EDGE_S:
                    problems.append({"kind": kind, "from": round(start, 2), "to": round(t, 2)})
                start = None

    spans([r < SILENCE_DBFS for _, r, _ in rows], "silence")
    spans([nz > 0.5 and r > NOISE_FLOOR_DBFS for _, r, nz in rows], "noise")
    score = sum(p["to"] - p["from"] for p in problems)
    return {"duration": round(dur, 3), "problems": problems, "score": round(score, 2),
            "rms_median": round(float(np.median([r for _, r, _ in rows])), 1) if rows else None}


def check_file(path: str | Path, sr: int = audio.DEFAULT_SR) -> dict:
    res = analyse(audio.load(path, sr), sr)
    return {"file": str(path), **res}
