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
- Key: every pitched note in the UI and tonal stems on the brand key's pentatonic, measured over each note's first
  60 ms; a stem with more than half its notes on one or two pitches is a person's call (one film's UI stem sat 71% on
  two notes, one of them off-key).
- Timbre: the palette is pads, small soft notes and wood; no chimes. Each effect stem's median spectral centroid at
  or under 1.5 kHz, no high partial still ringing 300 ms after its strike (a chime's tail), and no metallic or glassy
  ring (bright partials that aren't harmonics of one note, sustaining past 300 ms).
- Brightness: the effects' long-term spectral centroid and share of energy above 2 kHz, to compare with the brand's
  approved sound reference (moving from chimes to pads, notes and wood took the films' effects from 583 to 382 Hz
  and from 3.7% to 1.2% above 2 kHz).
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


def bookend(a: dict, b: dict, film_s: float | None = None) -> str | None:
    """Why two identical hits are the same sound on purpose, or None: either is marked a bookend in the cue sheet, or
    both are the brand's sonic logo, one in the film's first third and the other in its last (the film closing the way
    it opened)."""
    if a.get("bookend") or b.get("bookend"):
        return "marked a bookend in sound/sound_cues.json"
    if a.get("sonic_logo") and b.get("sonic_logo"):
        end = float(film_s) if film_s else max(float(a["t"]), float(b["t"]))
        first, last = sorted((float(a["t"]), float(b["t"])))
        if end > 0 and first <= end / 3 and last >= end * 2 / 3:
            return "the brand's sonic logo, closing the film the way it opened"
    return None


def repeats_check(hits: list[dict], *, one_sound_per_kind: bool = True, no_shared: bool = True,
                  film_s: float | None = None) -> dict:
    """hits: [{t, kind, sound, identity, audio?, bookend?, sonic_logo?}] in any order. Checks, in film order:
    - one deliberate sound per kind (every hit of a kind uses the same sound);
    - no sound serves two kinds;
    - no hit identical to the previous hit of its kind, and no two identical hits back to back overall, except a
      deliberate bookend (see bookend()), which is listed with `bookend: true` for the gate to set aside.
    Hits with audio are compared by waveform (same_sound); hits known only by their parameters, by identity."""
    hits = sorted(hits, key=lambda h: h["t"])
    sounds_by_kind: dict[str, set] = {}
    kinds_by_sound: dict[str, set] = {}
    last_by_kind: dict[str, dict] = {}
    identical, prev = [], None
    for h in hits:
        sounds_by_kind.setdefault(h["kind"], set()).add(h["sound"])
        kinds_by_sound.setdefault(h["sound"], set()).add(h["kind"])
        same = last_by_kind.get(h["kind"]) if _identical(last_by_kind.get(h["kind"]), h) else (
            prev if _identical(prev, h) else None)
        if same is not None:
            row = {"t": round(h["t"], 3), "kind": h["kind"], "sound": h["sound"], "after_t": round(same["t"], 3),
                   "why": "same as its kind's previous hit" if same is last_by_kind.get(h["kind"]) else
                          "same as the hit before it"}
            why = bookend(same, h, film_s)
            if why:
                row.update({"bookend": True, "why": why})
            identical.append(row)
        last_by_kind[h["kind"]] = h
        prev = h
    mixed = {k: sorted(v) for k, v in sounds_by_kind.items() if len(v) > 1} if one_sound_per_kind else {}
    shared = {s: sorted(v) for s, v in kinds_by_sound.items() if len(v) > 1} if no_shared else {}
    distinct = len({h["identity"] for h in hits})
    failing = [r for r in identical if not r.get("bookend")]
    return {"hits": len(hits), "distinct": distinct, "kinds": {k: sorted(v) for k, v in sounds_by_kind.items()},
            "identical_repeats": identical, "kinds_with_several_sounds": mixed, "sounds_serving_several_kinds": shared,
            "pass": not failing and not mixed and not shared}


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



# ---------------------------------------------------------------------------------------------------------------
# Key: notes on the brand key's pentatonic

PITCH_CLASSES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
PENTATONIC = {"major": (0, 2, 4, 7, 9), "minor": (0, 3, 5, 7, 10)}
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
NOTE_WINDOW_S = 0.06  # a note's pitch is measured over its first 60 ms
NOTE_SKIP_S = 0.005  # after the strike's first 5 ms (the attack's click has no pitch)
PITCHED_DB = 20.0  # a note is pitched when its strongest partial stands this far over the band's median


def parse_key(key: str | None) -> tuple[int, str] | None:
    """(tonic pitch class, "major" | "minor") from a key name like "A minor", "F# major", "Bb", "Em"; None when it
    can't be read."""
    if not key or not str(key).strip():
        return None
    k = str(key).strip().replace("♯", "#").replace("♭", "b")
    root = k[0].lower()
    if root not in PITCH_CLASSES:
        return None
    pc, rest = PITCH_CLASSES[root], k[1:]
    if rest[:1] == "#":
        pc, rest = pc + 1, rest[1:]
    elif rest[:1] == "b":
        pc, rest = pc - 1, rest[1:]
    rest = rest.strip().lower()
    mode = "minor" if rest.startswith(("m", "min", "minor", "aeolian", "dorian")) and not rest.startswith("maj") else "major"
    return pc % 12, mode


def pentatonic(key: str | None) -> set[int] | None:
    parsed = parse_key(key)
    if parsed is None:
        return None
    pc, mode = parsed
    return {(pc + i) % 12 for i in PENTATONIC[mode]}


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


STRIKE_RISE_DB = 9.0  # a strike: the 5 ms level climbs this much over the quietest of the 50 ms before it
STRIKE_WITHIN_DB = 40.0  # ...to within this much of the stem's loudest moment


def strikes(y: np.ndarray, sr: int, *, min_gap_s: float = 0.08) -> list[float]:
    """Where a stem's sounds start: spectral-flux onsets (dsp.flux_onsets) that are also strikes, the level jumping at
    least 9 dB in 30 ms to within 40 dB of the stem's peak. A ringing note's slow decay makes flux without a strike,
    and a second onset inside one note would count it twice, so those are left out."""
    from slate.sound import dsp  # noqa: PLC0415

    m = audio.mono(np.asarray(y, dtype=np.float64))
    if not np.any(m):
        return []
    hop = max(1, sr // 1000)
    k = len(m) // hop
    p = (m[:k * hop] ** 2).reshape(k, hop).mean(axis=1)
    env = 10 * np.log10(np.convolve(p, np.ones(5) / 5, mode="same") + 1e-20)  # 5 ms level per millisecond
    top = float(env.max())
    out: list[float] = []
    for t, _z in dsp.flux_onsets(m, sr, 0.0, len(m) / sr, min_z=6.0):
        i = int(t * 1000)
        before = env[max(0, i - 60):max(1, i - 10)]
        after = env[i:min(k, i + 30)]
        if not len(before) or not len(after):
            continue
        if float(after.max()) - float(before.min()) >= STRIKE_RISE_DB and float(after.max()) >= top - STRIKE_WITHIN_DB:
            # the strike itself: the first millisecond within 12 dB of its peak (flux frames run a little early)
            lo = max(0, i - 30)
            near = env[lo:min(k, i + 30)]
            t = (lo + int(np.argmax(near >= float(near.max()) - 12.0))) / 1000.0
            if not out or t - out[-1] >= min_gap_s:
                out.append(float(t))
    return out


def _spectrum(seg: np.ndarray, sr: int, n_fft: int = 1 << 16) -> tuple[np.ndarray, np.ndarray]:
    """(frequencies, amplitude spectrum in dB, a sinusoid's amplitude whatever the window's length) of a mono segment."""
    win = np.hanning(len(seg))
    mag = np.abs(np.fft.rfft(seg * win, n=max(n_fft, len(seg)))) * 2 / max(win.sum(), 1e-12)
    return np.fft.rfftfreq(max(n_fft, len(seg)), 1 / sr), 20 * np.log10(mag + 1e-12)


def note_pitch(y: np.ndarray, sr: int, t: float, *, fmin: float = 80.0, fmax: float = 4000.0) -> dict | None:
    """The pitch a note rings at over its first 60 ms (from `t`, the onset): its strongest partial, interpolated.
    None when the segment is too short or nothing in it is pitched (a click, a noise)."""
    m = audio.mono(np.asarray(y, dtype=np.float64))
    a, b = int((t + NOTE_SKIP_S) * sr), int((t + NOTE_SKIP_S + NOTE_WINDOW_S) * sr)
    seg = m[max(0, a):min(len(m), b)]
    if len(seg) < int(0.03 * sr) or not np.any(seg):
        return None
    f, db = _spectrum(seg, sr)
    band = (f >= fmin) & (f <= fmax)
    if not band.any():
        return None
    i = int(np.flatnonzero(band)[np.argmax(db[band])])
    if db[i] - float(np.median(db[band])) < PITCHED_DB:
        return None
    if 0 < i < len(db) - 1:  # parabolic interpolation of the peak
        alpha, beta, gamma = db[i - 1], db[i], db[i + 1]
        p = 0.5 * (alpha - gamma) / (alpha - 2 * beta + gamma) if (alpha - 2 * beta + gamma) else 0.0
        hz = float(f[i] + p * (f[1] - f[0]))
    else:
        hz = float(f[i])
    midi_f = 69 + 12 * math.log2(hz / 440.0)
    midi = int(round(midi_f))
    return {"t": round(t, 3), "hz": round(hz, 1), "midi": midi, "note": note_name(midi),
            "cents": round((midi_f - midi) * 100, 1)}


def key_check(y: np.ndarray, sr: int, key: str | None, *, min_notes_for_variety: int = 8) -> dict:
    """Every pitched note's pitch (onsets by spectral flux), the ones off the key's pentatonic, and how many notes the
    two commonest pitches take (more than half: a person listens to whether it plays one note a million times)."""
    m = audio.mono(np.asarray(y, dtype=np.float64))
    onsets = strikes(m, sr)
    notes = [n for n in (note_pitch(m, sr, t) for t in onsets) if n is not None]
    scale = pentatonic(key)
    off = [n for n in notes if scale is not None and n["midi"] % 12 not in scale]
    counts: dict[int, int] = {}
    for n in notes:
        counts[n["midi"]] = counts.get(n["midi"], 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
    share = sum(c for _, c in top) / len(notes) if notes else 0.0
    return {"onsets": len(onsets), "notes": len(notes), "unpitched": len(onsets) - len(notes), "key": key,
            "pentatonic": [NOTE_NAMES[pc] for pc in sorted(scale)] if scale is not None else None, "off_key": off,
            "top_pitches": [{"note": note_name(k), "notes": c} for k, c in top],
            "top_two_share": round(share, 3),
            "narrow": len(notes) >= min_notes_for_variety and share > 0.5,
            "pitches": [n["note"] for n in notes][:200]}


# ---------------------------------------------------------------------------------------------------------------
# Timbre: no chimes, nothing metallic or glassy, nothing bright

CENTROID_MAX_HZ = 1500.0
RING_FROM_S, RING_TO_S = 0.3, 0.6  # where a strike should have died away
CHIME_DB = -30.0  # a high partial (2 kHz and up) this close to its strike's peak, 300 ms on, is a chime's tail
TONAL_DB = 20.0  # a partial stands this far over the ring window's own median (noise doesn't)
HARMONIC_CENTS = 35.0


def _tonal_peaks(f: np.ndarray, db: np.ndarray, lo: float, hi: float, *, within_db: float = 20.0) -> list[tuple[float, float]]:
    """[(Hz, dB)] of the spectrum's tonal peaks in lo..hi: local maxima standing TONAL_DB over the band's median and
    within within_db of the band's strongest."""
    band = (f >= lo) & (f <= hi)
    if not band.any():
        return []
    idx = np.flatnonzero(band)
    sub = db[idx]
    floor, top = float(np.median(sub)), float(np.max(sub))
    peaks = signal.find_peaks(sub, prominence=TONAL_DB / 2)[0]
    out = [(float(f[idx[k]]), float(sub[k])) for k in peaks
           if sub[k] - floor >= TONAL_DB and sub[k] >= top - within_db]
    return sorted(out, key=lambda p: -p[1])[:8]


def _harmonic(freqs: list[float]) -> bool:
    """True when every frequency is a whole multiple of the lowest (within HARMONIC_CENTS)."""
    if len(freqs) < 2:
        return True
    f1 = min(freqs)
    for fr in freqs:
        r = fr / f1
        n = max(1, round(r))
        if abs(1200 * math.log2(r / n)) > HARMONIC_CENTS:
            return False
    return True


def centroid_hz(seg: np.ndarray, sr: int, lo: float = 50.0, hi: float = 16000.0) -> float | None:
    """The power-weighted spectral centroid of a segment (lo..hi), or None when it's silent."""
    m = audio.mono(np.asarray(seg, dtype=np.float64))
    if len(m) < 64 or not np.any(m):
        return None
    win = np.hanning(len(m))
    pw = np.abs(np.fft.rfft(m * win)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / sr)
    band = (f >= lo) & (f <= min(hi, sr / 2))
    total = float(pw[band].sum())
    return float((f[band] * pw[band]).sum() / total) if total > 0 else None


def timbre_check(y: np.ndarray, sr: int) -> dict:
    """Every hit in a stem (onsets by spectral flux): its centroid over its first 150 ms, and, when nothing else
    starts before 600 ms, what still rings 300-600 ms after it: a high tonal partial close to the strike's peak (a
    chime's tail) or bright tonal partials that aren't harmonics of one note (metal, glass)."""
    m = audio.mono(np.asarray(y, dtype=np.float64))
    dur = len(m) / sr
    onsets = strikes(m, sr)
    hits, centroids, chimes, metallic = [], [], [], []
    for k, t in enumerate(onsets):
        nxt = onsets[k + 1] if k + 1 < len(onsets) else dur
        a = int(t * sr)
        body = m[a:min(len(m), a + int(0.15 * sr), int(nxt * sr))]
        c = centroid_hz(body, sr)
        row = {"t": round(t, 3), "centroid_hz": round(c, 1) if c is not None else None}
        if c is not None:
            centroids.append(c)
        strike = m[a:a + int(0.06 * sr)]
        if nxt - t >= RING_TO_S and len(strike) > 64:
            _f0, sdb = _spectrum(strike, sr)
            peak = float(np.max(sdb))
            ring = m[a + int(RING_FROM_S * sr):a + int(RING_TO_S * sr)]
            f, rdb = _spectrum(ring, sr)
            high = [p for p in _tonal_peaks(f, rdb, 2000.0, 12000.0, within_db=60.0)]
            if high and high[0][1] - peak > CHIME_DB:
                row["chime_tail"] = {"hz": round(high[0][0], 1), "db_vs_strike": round(high[0][1] - peak, 1)}
                chimes.append(row)
            ringing = [p for p in _tonal_peaks(f, rdb, 150.0, 12000.0) if p[1] - peak > -40.0]
            bright = [p for p in ringing if p[0] >= 1000.0]
            if bright and len(ringing) >= 2 and not _harmonic([p[0] for p in ringing]):
                row["metallic"] = {"partials_hz": [round(p[0], 1) for p in ringing[:6]]}
                metallic.append(row)
        hits.append(row)
    med = float(np.median(centroids)) if centroids else None
    return {"hits": len(hits), "median_centroid_hz": round(med, 1) if med is not None else None,
            "brightest": sorted((h for h in hits if h["centroid_hz"] is not None), key=lambda h: -h["centroid_hz"])[:5],
            "chime_tails": chimes, "metallic": metallic}


def brightness(x: np.ndarray, sr: int, *, split_hz: float = 2000.0, window_s: float = 0.1) -> dict | None:
    """A stem's long-term brightness, over the windows where it sounds (within 50 dB of its loudest 100 ms): the
    power-weighted spectral centroid and the share of its energy above split_hz. None for silence."""
    m = audio.mono(np.asarray(x, dtype=np.float64))
    w = max(256, int(window_s * sr))
    k = len(m) // w
    if k == 0 or not np.any(m):
        return None
    frames = m[:k * w].reshape(k, w)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    live = rms >= rms.max() * 10 ** (-50 / 20)
    win = np.hanning(w)
    f = np.fft.rfftfreq(w, 1 / sr)
    pw = np.zeros(len(f))
    for fr in frames[live]:
        pw += np.abs(np.fft.rfft(fr * win)) ** 2
    band = (f >= 20.0) & (f <= sr / 2)
    total = float(pw[band].sum())
    if total <= 0:
        return None
    return {"centroid_hz": round(float((f[band] * pw[band]).sum() / total), 1),
            "above_2k_share": round(float(pw[band & (f >= split_hz)].sum()) / total, 4), "split_hz": split_hz,
            "live_s": round(float(live.sum()) * w / sr, 2)}
