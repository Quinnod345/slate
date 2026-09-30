"""Just enough audio for word timings: decode a take, measure its energy, find where words begin.

Whisper's word starts drift early or late by a few tens of milliseconds. The films snapped each start to the take's own
energy onset within ±80 ms (the moment the word's sound actually begins), measured on a 5 ms RMS envelope of the take
decoded to 16 kHz mono. These are the same numbers, so the rebuilt word timings match the delivered ones.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

from slate import procs
from slate.errors import MissingPrerequisite

RATE = 16000  # decode rate for the envelope
HOP = 80  # samples per envelope step: 5 ms at 16 kHz
SNAP_WINDOW = 0.08  # seconds either side of Whisper's start
MIN_RISE_DB = 3.0  # a clear onset: the envelope rises at least this much in one step


def decode_mono(path: str | os.PathLike, rate: int = RATE) -> np.ndarray:
    """A take as float32 mono samples at `rate`, through ffmpeg (the films' decoder), else a plain WAV reader."""
    path = Path(path)
    if procs.which("ffmpeg"):
        proc = procs.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
                         check=True, text=False, timeout=300)
        return np.frombuffer(proc.stdout, np.float32)
    if path.suffix.lower() == ".wav":
        return _read_wav(path, rate)
    raise MissingPrerequisite(f"Reading {path.name} needs ffmpeg, and it isn't installed.",
                              hint="Run `slate doctor --install` to install ffmpeg.")


def _read_wav(path: Path, rate: int) -> np.ndarray:
    """PCM WAV without ffmpeg (tests and bare machines). Resamples with a polyphase filter when needed."""
    with wave.open(str(path), "rb") as w:
        channels, width, src_rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        x = ints.astype(np.float32) / float(1 << 23)
    else:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if src_rate != rate:
        from math import gcd  # noqa: PLC0415

        from scipy.signal import resample_poly  # noqa: PLC0415

        g = gcd(src_rate, rate)
        x = resample_poly(x, rate // g, src_rate // g).astype(np.float32)
    return x


def duration(path: str | os.PathLike) -> float:
    """A take's length in seconds: WAV headers directly, anything else through ffprobe."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            pass  # float or extensible WAVs: ffprobe reads those
    if not procs.which("ffprobe"):
        raise MissingPrerequisite(f"Measuring {path.name} needs ffprobe (part of ffmpeg), and it isn't installed.",
                                  hint="Run `slate doctor --install` to install ffmpeg.")
    proc = procs.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                     check=True, timeout=60)
    return float(proc.stdout.strip())


def envelope(x: np.ndarray, rate: int = RATE, hop: int = HOP) -> tuple[np.ndarray, float]:
    """RMS level in dB per hop, and the hop length in seconds."""
    frames = len(x) // hop
    if frames == 0:
        return np.zeros(1, np.float64) - 180.0, hop / rate
    rms = np.sqrt(np.mean(x[: frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9), hop / rate


def snap_onset(db: np.ndarray, step: float, t: float, window: float = SNAP_WINDOW,
               min_rise: float = MIN_RISE_DB) -> tuple[float, float]:
    """Move a word start to the strongest energy rise within ±window; returns (time, shift). No clear rise: no move."""
    i0, i1 = max(1, int((t - window) / step)), min(len(db) - 1, int((t + window) / step))
    if i1 <= i0:
        return t, 0.0
    rise = db[i0 + 1:i1 + 1] - db[i0:i1]
    k = int(np.argmax(rise))
    if rise[k] < min_rise:
        return t, 0.0
    t2 = (i0 + k + 1) * step
    return t2, t2 - t

