"""The music gap check: no unplanned hole in the music.

The music stem is measured in 0.5 s RMS windows (over both channels). No window may fall under -60 dBFS unless it
overlaps a planned stop (a held breath before a landing, a film that opens out of silence). It runs twice: predicted
at assembly, on the score as the mix will set it, and measured after the mix, on the delivered music stem.

Why it exists: the take QA only sees holes of 1.5 s or more and ignores a take's last second, so two holes in a
delivered score slipped past it (a 1.2 s silence at a join, and an ending that ran out early). This check caught them.
"""

from __future__ import annotations

import numpy as np

from slate.sound import dsp, measure

THRESHOLD_DBFS = -60.0
WINDOW_S = 0.5


def overlaps(t: float, window_s: float, stops: list[dict]) -> bool:
    return any(t < float(s["to"]) and t + window_s > float(s["from"]) for s in stops)


def check(music: np.ndarray, sr: int, stops: list[dict] | None = None, threshold: float = THRESHOLD_DBFS,
          window_s: float = WINDOW_S) -> dict:
    """{threshold_dbfs, window_s, planned_stops, quietest_outside_stops_dbfs, failures[{t, dbfs}], pass}."""
    stops = [dict(s) for s in (stops or [])]
    wins = measure.window_rms_dbfs(music, sr, window_s)
    outside = [(t, v) for t, v in wins if not overlaps(t, window_s, stops)]
    fails = [{"t": round(t, 1), "dbfs": round(v, 1)} for t, v in outside if v < threshold]
    quietest = min(outside, key=lambda w: w[1]) if outside else None
    return {"threshold_dbfs": threshold, "window_s": window_s, "planned_stops": stops,
            "quietest_outside_stops_dbfs": round(quietest[1], 1) if quietest else None,
            "quietest_at_s": round(quietest[0], 1) if quietest else None,
            "windows": len(wins), "failures": fails, "pass": not fails}


def check_file(path, stops: list[dict] | None = None, threshold: float = THRESHOLD_DBFS,
               window_s: float = WINDOW_S) -> dict:
    """check() on a WAV file, streamed in window-aligned blocks (a feature-length stem never loads whole)."""
    from slate.sound import audio  # noqa: PLC0415

    info = audio.wav_info(path)
    w = int(round(window_s * info.sample_rate))
    sums: list[float] = []
    carry = np.zeros((0, info.channels))
    for block in audio.iter_wav(path, w * 40):
        block = np.concatenate([carry, block])
        k = len(block) // w
        if k:
            sums.extend((block[: k * w] ** 2).reshape(k, w, -1).mean(axis=(1, 2)).tolist())
        carry = block[k * w:]
    stops = [dict(s) for s in (stops or [])]
    wins = [(round(i * window_s, 6), float(20 * np.log10(np.sqrt(v) + 1e-12))) for i, v in enumerate(sums)]
    outside = [(t, v) for t, v in wins if not overlaps(t, window_s, stops)]
    fails = [{"t": round(t, 1), "dbfs": round(v, 1)} for t, v in outside if v < threshold]
    quietest = min(outside, key=lambda x: x[1]) if outside else None
    return {"threshold_dbfs": threshold, "window_s": window_s, "planned_stops": stops,
            "quietest_outside_stops_dbfs": round(quietest[1], 1) if quietest else None,
            "quietest_at_s": round(quietest[0], 1) if quietest else None,
            "windows": len(wins), "failures": fails, "pass": not fails}


def predict_music_stem(score: np.ndarray, voice: np.ndarray | None, sr: int, *, under_lu: float, duck_db: float,
                       gain_db: float, attack_s: float = 0.08, release_s: float = 0.65) -> np.ndarray:
    """The music stem as the mix will make it: the score set under_lu under the voice, ducked duck_db under speech,
    times the mix's loudness gain. With no voice, the score is simply set to gain_db."""
    sc_l = measure.integrated(score, sr)
    if voice is None or not np.any(voice):
        return score * dsp.db(gain_db)
    vo_l = measure.integrated(voice, sr)
    if sc_l <= measure.SILENT_FLOOR or vo_l <= measure.SILENT_FLOOR:
        return score * dsp.db(gain_db)
    env = dsp.follower(voice, sr, attack_s, release_s)
    music = score * dsp.db((vo_l - under_lu) - sc_l)
    return music * dsp.db(-duck_db * env)[:, None] * dsp.db(gain_db)
