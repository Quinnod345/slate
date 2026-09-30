"""Listening reels: takes level-matched and joined with short gaps, so voices are compared by ear, not by volume.

From the films' `voice_reel.py` and `tag_ab.py`: every take is gained to the same integrated loudness (-20 LUFS)
so neither side of a comparison wins by being louder. The reel is a WAV plus a JSON listing what plays when (the
films' title cards carried brand colours; a listing carries no brand at all).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import jsonio
from slate.voice import audio as audio_mod

TARGET_LUFS = -20.0
SR = 48000


def matched(path: str | Path, target: float = TARGET_LUFS, sr: int = SR) -> tuple[np.ndarray, dict]:
    """A take gained to the target loudness (mono, `sr`), and what was done to it."""
    x, rate = audio_mod.load(path)
    x = audio_mod.resample(x, rate, sr)
    before = audio_mod.loudness(x, sr)
    y, gain = audio_mod.gain_to(x, sr, target)
    after = audio_mod.loudness(y, sr)
    return y, {"source": str(path), "lufs_before": before["lufs"], "gain_db": gain, "lufs_after": after["lufs"],
               "true_peak_dbtp": after["true_peak_dbtp"]}


def build(items: list[dict], out_wav: str | Path, *, target: float = TARGET_LUFS, gap_s: float = 0.9,
          sr: int = SR) -> dict:
    """items: [{file, title, [detail]}]. Writes the reel and <reel>.json; returns the listing."""
    out_wav = Path(out_wav)
    parts, listing, t = [], [], 0.0
    for i, item in enumerate(items):
        y, info = matched(item["file"], target, sr)
        if i:
            parts.append(audio_mod.silence(gap_s, sr))
            t += gap_s
        listing.append({"index": i + 1, "at_s": round(t, 3), "seconds": round(len(y) / sr, 3),
                        **{k: v for k, v in item.items() if k != "file"}, **info})
        parts.append(y)
        t += len(y) / sr
    reel = np.concatenate(parts) if parts else np.zeros(1)
    audio_mod.write_wav(out_wav, reel, sr, 24)
    data = {"reel": str(out_wav), "target_lufs": target, "gap_s": gap_s, "seconds": round(len(reel) / sr, 3),
            "items": listing}
    jsonio.write_atomic(out_wav.with_suffix(".json"), data)
    return data


def ab_clip(a: str | Path, b: str | Path, out_wav: str | Path, *, gap_s: float = 0.8, target: float = TARGET_LUFS,
            sr: int = SR) -> dict:
    """A, then a gap, then B, both at the same loudness."""
    ya, ia = matched(a, target, sr)
    yb, ib = matched(b, target, sr)
    audio_mod.write_wav(out_wav, np.concatenate([ya, audio_mod.silence(gap_s, sr), yb]), sr, 24)
    return {"file": str(out_wav), "A": ia, "B": ib, "gap_s": gap_s}
