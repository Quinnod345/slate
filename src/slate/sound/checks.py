"""Measurements behind the sound gates. Nothing here changes a sound; every rule is one the owner stated out loud.

- Glides and slide-ups: a partial tracker follows every sustained spectral peak frame by frame (10 ms hop, 170 ms
  window, vibrato smoothed out with a 300 ms median). A track that drifts more than a quarter tone (50 cents) from
  start to end, or wanders more than 60 cents, is a glide; a rising one is a slide-up. A held tone that slides in
  pitch "sounds like an air raid siren", and the owner asked for the slide-ups to be reworked entirely. Proof 2's own
  tonal stem (its hum glided A3 to E4, D4 to A4, A4 to D5) is the positive control the tracker was tuned on.
- Drone: a room-tone bed ("a weird droning… like a microwave"). Any stretch of the effects or ambience that never
  drops under the floor for longer than the palette allows, at a steady level (the 10th-to-90th percentile spread of
  its 0.5 s windows under a few dB), is a drone, unless the cue sheet plans it as a bed. A swell rises, so it isn't one.
- Repeats: "the same tone that plays a million times in a row… is bad". Two hits are the same sound when their
  peak-normalised waveforms match (to -54 dB), so a level change doesn't make a new sound and a re-seeded strike, a
  few cents of detune or another take does. A kind has one deliberate sound, repeats vary, and no sound serves two kinds.
- Clarity: every word of the voice against everything else in the speech band (300 Hz to 4 kHz). The narration is
  always understood: the voice stays a few dB over the rest on every word.
- Never over: no effect louder than the voice. The effects' loudest 400 ms never passes the voice's own loudness.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
from scipy import signal
from scipy.ndimage import median_filter

from slate.sound import audio, measure

# ---------------------------------------------------------------------------------------------------------------
# Glides


def pitch_tracks(x: np.ndarray, sr: int, fmin: float = 60.0, fmax: float = 2500.0, win: int = 8192, hop: int | None = None,
                 rel_db: float = -30.0, abs_db: float = -70.0, link_cents: float = 20.0, min_len_s: float = 0.4,
                 smooth_s: float = 0.3) -> list[dict]:
    """Tracks of sustained spectral peaks: [{t0, t1, hz0, cents}] (cents relative to the track's first value)."""
    y = audio.mono(np.asarray(x, dtype=np.float64))
    hop = hop or max(1, sr // 100)
    if len(y) < win + hop:
        return []
    f, t, z = signal.stft(y, fs=sr, window="hann", nperseg=win, noverlap=win - hop, boundary=None, padded=False)
    mag = np.abs(z)
    band = (f >= fmin) & (f <= fmax)
    idx = np.flatnonzero(band)
    if not len(idx):
        return []
    gmax = mag[band].max() + 1e-12
    open_tracks: list[dict] = []
    done: list[dict] = []
    df = f[1] - f[0]
    for j in range(mag.shape[1]):
        col = mag[idx, j]
        peaks: list[float] = []
        if col.max() > 0:
            lim = max(col.max() * 10 ** (rel_db / 20), gmax * 10 ** (abs_db / 20))
            pk, _ = signal.find_peaks(col, height=lim)
            for p in pk:
                if 0 < p < len(col) - 1:
                    a, b, c = np.log(col[p - 1:p + 2] + 1e-12)
                    den = a - 2 * b + c
                    d = 0.5 * (a - c) / den if den != 0 else 0.0
                    peaks.append(f[idx[p]] + d * df)
        used: set[int] = set()
        still = []
        for tr in open_tracks:
            last = tr["f"][-1]
            best, bd = None, link_cents
            for k, pf in enumerate(peaks):
                if k in used or pf <= 0:
                    continue
                dc = abs(1200 * math.log2(pf / last))
                if dc < bd:
                    best, bd = k, dc
            if best is not None:
                used.add(best)
                tr["f"].append(peaks[best])
                tr["j"].append(j)
                still.append(tr)
            else:
                done.append(tr)
        for k, pf in enumerate(peaks):
            if k not in used and pf > 0:
                still.append({"f": [pf], "j": [j]})
        open_tracks = still
    done += open_tracks
    out = []
    dt = hop / sr
    for tr in done:
        if len(tr["f"]) * dt < min_len_s:
            continue
        fr = np.array(tr["f"])
        c = 1200 * np.log2(fr / fr[0])
        k = max(3, int(smooth_s / dt) | 1)
        cs = median_filter(c, size=k, mode="nearest")
        out.append({"t0": float(t[tr["j"][0]]), "t1": float(t[tr["j"][-1]]), "hz0": float(np.median(fr[:k])), "cents": cs})
    return out


def glide_check(x: np.ndarray, sr: int, drift_cents: float = 50.0, wander_cents: float = 60.0, t_offset: float = 0.0,
                min_len_s: float = 0.4) -> dict:
    """Every sustained partial: a glide if it drifts > drift_cents (median of its last quarter against its first) or
    wanders > wander_cents (10th to 90th percentile of the smoothed track). Rising drifts are slide-ups."""
    trs = pitch_tracks(x, sr, min_len_s=min_len_s)
    worst, glides = 0.0, []
    for tr in trs:
        c = tr["cents"]
        q = max(1, len(c) // 4)
        drift = float(np.median(c[-q:]) - np.median(c[:q]))
        wander = float(np.percentile(c, 90) - np.percentile(c, 10))
        worst = max(worst, abs(drift))
        if abs(drift) > drift_cents or wander > wander_cents:
            glides.append({"t0": round(tr["t0"] + t_offset, 3), "t1": round(tr["t1"] + t_offset, 3),
                           "hz": round(tr["hz0"], 1), "drift_cents": round(drift, 1), "wander_cents": round(wander, 1),
                           "slide_up": drift > drift_cents})
    return {"tracks": len(trs), "max_abs_drift_cents": round(worst, 1), "glides": glides,
            "slide_ups": [g for g in glides if g["slide_up"]], "pass": not glides,
            "rule": (f"no sustained partial (at least {min_len_s:g} s, within 30 dB of the loudest) drifts more than "
                     f"{drift_cents:g} cents or wanders more than {wander_cents:g} cents; 300 ms median smoothing, so "
                     "vibrato and a string section's intonation don't count")}


# ---------------------------------------------------------------------------------------------------------------
# Drone


def drone_spans(x: np.ndarray, sr: int, *, max_continuous_s: float = 8.0, floor_dbfs: float = -60.0,
                stationary_db: float = 6.0, window_s: float = 0.5, beds: list[dict] | None = None) -> dict:
    """Continuous, steady stretches longer than max_continuous_s (outside planned beds)."""
    wins = measure.window_rms_dbfs(x, sr, window_s)
    beds = beds or []
    spans, run = [], []

    def close(run_: list) -> None:
        if not run_:
            return
        t0, t1 = run_[0][0], run_[-1][0] + window_s
        if t1 - t0 <= max_continuous_s:
            return
        levels = np.array([v for _, v in run_])
        spread = float(np.percentile(levels, 90) - np.percentile(levels, 10))
        if spread <= stationary_db:
            planned = any(t0 >= float(b["from"]) - window_s and t1 <= float(b["to"]) + window_s for b in beds)
            spans.append({"from": round(t0, 2), "to": round(t1, 2), "seconds": round(t1 - t0, 2),
                          "level_dbfs": round(float(np.median(levels)), 1), "spread_db": round(spread, 1),
                          "planned_bed": planned})

    for t, v in wins:
        if v >= floor_dbfs:
            run.append((t, v))
        else:
            close(run)
            run = []
    close(run)
    drones = [s for s in spans if not s["planned_bed"]]
    return {"spans": spans, "drones": drones, "pass": not drones,
            "rule": (f"nothing under the effects runs unbroken for more than {max_continuous_s:g} s at a steady level "
                     f"(within {stationary_db:g} dB, above {floor_dbfs:g} dBFS), unless the cue sheet plans it as a bed")}


# ---------------------------------------------------------------------------------------------------------------
# Repeats


def hit_hash(y: np.ndarray) -> str:
    """A coarse fingerprint of a hit's shape (peak-normalised, 8-bit), for counting distinct sounds in reports."""
    y = np.asarray(y, dtype=np.float64)
    m = np.max(np.abs(y)) + 1e-12 if y.size else 1.0
    q = np.round(y / m * 127).astype(np.int8)
    return hashlib.sha1(q.tobytes()).hexdigest()[:16]


SAME_SOUND_DB = -54.0  # two hits whose peak-normalised waveforms differ by less than this are the same sound


def same_sound(a: np.ndarray, b: np.ndarray, tol_db: float = SAME_SOUND_DB) -> bool:
    """True when two hits are the same sound at most at different levels: the same length (within two samples) and
    peak-normalised waveforms that never differ by more than tol_db of full scale. A re-seeded strike, a few cents of
    detune or another take all differ by far more; a gain change or a 24-bit round trip doesn't."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if abs(len(a) - len(b)) > 2 or not a.size or not b.size:
        return False
    n = min(len(a), len(b))
    pa, pb = np.max(np.abs(a)), np.max(np.abs(b))
    if pa <= 0 or pb <= 0:
        return pa == pb
    return float(np.max(np.abs(a[:n] / pa - b[:n] / pb))) < 10 ** (tol_db / 20)


LEVEL_KEYS = {"gain", "gain_db", "t", "beat", "id", "note", "shot", "act", "kind", "stem", "anchor", "event", "notes",
              "at", "start", "film_t", "src", "why", "stems", "family"}


def params_identity(sound: str, params: dict) -> str:
    """Identity of a parametric hit: its sound and the parameters that shape it (never its time or level)."""
    shaping = {k: v for k, v in sorted(params.items()) if k not in LEVEL_KEYS}
    return hashlib.sha1(json.dumps([sound, shaping], sort_keys=True, default=str).encode()).hexdigest()[:16]


def _identical(a: dict | None, b: dict) -> bool:
    if a is None:
        return False
    if a.get("audio") is not None and b.get("audio") is not None:
        return same_sound(a["audio"], b["audio"])
    return a.get("identity") is not None and a.get("identity") == b.get("identity")


def repeats_check(hits: list[dict], *, one_sound_per_kind: bool = True, no_shared: bool = True) -> dict:
    """hits: [{t, kind, sound, identity, audio?}] in any order. Checks, in film order:
    - one deliberate sound per kind (every hit of a kind uses the same sound);
    - no sound serves two kinds;
    - no hit identical to the previous hit of its kind, and no two identical hits back to back overall.
    Hits with audio are compared by waveform (same_sound); hits known only by their parameters, by identity."""
    hits = sorted(hits, key=lambda h: h["t"])
    sounds_by_kind: dict[str, set] = {}
    kinds_by_sound: dict[str, set] = {}
    last_by_kind: dict[str, dict] = {}
    identical, prev = [], None
    for h in hits:
        sounds_by_kind.setdefault(h["kind"], set()).add(h["sound"])
        kinds_by_sound.setdefault(h["sound"], set()).add(h["kind"])
        if _identical(last_by_kind.get(h["kind"]), h):
            identical.append({"t": round(h["t"], 3), "kind": h["kind"], "sound": h["sound"], "why": "same as its kind's previous hit"})
        elif _identical(prev, h):
            identical.append({"t": round(h["t"], 3), "kind": h["kind"], "sound": h["sound"], "why": "same as the hit before it"})
        last_by_kind[h["kind"]] = h
        prev = h
    mixed = {k: sorted(v) for k, v in sounds_by_kind.items() if len(v) > 1} if one_sound_per_kind else {}
    shared = {s: sorted(v) for s, v in kinds_by_sound.items() if len(v) > 1} if no_shared else {}
    distinct = len({h["identity"] for h in hits})
    return {"hits": len(hits), "distinct": distinct, "kinds": {k: sorted(v) for k, v in sounds_by_kind.items()},
            "identical_repeats": identical, "kinds_with_several_sounds": mixed, "sounds_serving_several_kinds": shared,
            "pass": not identical and not mixed and not shared}


# ---------------------------------------------------------------------------------------------------------------
# Clarity


def band(x: np.ndarray, sr: int, lo: float = 300.0, hi: float = 4000.0) -> np.ndarray:
    sos = signal.butter(4, [lo, hi], "bandpass", fs=sr, output="sos")
    return signal.sosfilt(sos, audio.mono(np.asarray(x, dtype=np.float64)))


def word_masking(voice: np.ndarray, rest: np.ndarray, sr: int, words: list[tuple[str, float, float]],
                 lo: float = 300.0, hi: float = 4000.0) -> list[tuple[str, float, float, float]]:
    """[(word, start, end, voice-over-rest dB in the speech band)] for every word at least 5 ms long."""
    bv, br = band(voice, sr, lo, hi), band(rest, sr, lo, hi)
    out = []
    for w, s, e in words:
        a, b = int(round(s * sr)), int(round(e * sr))
        a, b = max(0, a), min(len(bv), b)
        if b - a < int(0.005 * sr):
            continue
        pv = float(np.mean(bv[a:b] ** 2)) + 1e-20
        pr = float(np.mean(br[a:b] ** 2)) + 1e-20
        out.append((w, round(s, 3), round(e, 3), round(10 * math.log10(pv / pr), 1)))
    return out


def never_over(voice: np.ndarray, effects: np.ndarray, sr: int, margin_db: float = 0.0) -> dict:
    """The effects' loudest momentary (400 ms) loudness against the voice's integrated loudness."""
    if not np.any(voice) or not np.any(effects):
        return {"pass": True, "effects_max_momentary_lu_vs_voice": None, "skipped": "no voice or no effects"}
    vo_l = measure.integrated(voice, sr)
    mom = measure.momentary(effects, sr)
    worst = float(np.max(mom)) if len(mom) else measure.SILENT_FLOOR
    rel = round(worst - vo_l, 2)
    at = round(float(np.argmax(mom)) * measure.STEP_S, 2) if len(mom) else None
    return {"pass": rel <= margin_db, "effects_max_momentary_lu_vs_voice": rel, "at_s": at, "margin_db": margin_db}
