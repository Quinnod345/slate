"""Cue placement and shared-hall stems for the approved effects design.

The adaptive family mixer supplies per-cue and per-bus callbacks. This module applies neither an implicit trim nor
a second duck: trims are explicit inputs and the manifest records them. Sampled libraries remain read-only.
Source map: p3balance.render_cue/render_families provide placement, peak levelling, pre-send treatment and split
returns; sound_engine.make_ir provides the shared hall. Adaptive family rules live in balance_mix, not here.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np
from scipy import signal

from slate.errors import UsageError
from slate.sound import checks, dsp
from slate.sound.effects import voices

STEMS = ("ui", "fx", "tonal", "verb", "tverb", "amb")


def expand_cues(cues: list[dict]) -> list[dict]:
    """Expand an independently anchored gesture into the same hits that sound.verify measures."""
    out = []
    for cue in cues:
        notes = cue.get("notes")
        if notes:
            if not isinstance(notes, list) or not all(isinstance(note, dict) for note in notes):
                raise UsageError("Gesture notes must be cue objects; put a pad's chord pitches in params.notes.")
            base = {k: v for k, v in cue.items() if k != "notes"}
            out.extend({**base, **note, "id": f"{cue.get('id', '?')}.{index + 1}", "gesture": cue.get("id")}
                       for index, note in enumerate(notes))
        else:
            out.append(dict(cue))
    return out


def render_cue(cue: dict, film_dir: Path | None = None, sample_rate: int = 48000,
               *, palette: dict | None = None, motion: dict | None = None,
               clock: tuple[int, int] | None = None) -> tuple[np.ndarray, int]:
    """One dry, peak-levelled cue and its offset from t, in samples. The caller owns the approval gate.

    The tuple matches verify's existing interface. The cue's anchor is measured on these exact samples. The optional
    film context resolves reused media and brand instruments; a cue's pitch, seed and pinned curve remain its own.
    """
    if film_dir is not None and (palette is None or motion is None or clock is None):
        from slate.sound.film import load
        film = load(film_dir)
        palette = film.palette if palette is None else palette
        motion = (film.brand or {}).get("motion") if motion is None else motion
        clock = clock or (film.fps_num, film.fps_den)
    if clock is not None and voices.parameters(cue).get("type") == "countup3":
        params = dict(cue.get("params") or {})
        params.setdefault("fps_num", cue.get("fps_num", clock[0]))
        params.setdefault("fps_den", cue.get("fps_den", clock[1]))
        cue = {**cue, "params": params}
    c = voices.parameters(cue)
    y = voices.render(cue, film_dir=film_dir, sample_rate=sample_rate, palette=palette, motion=motion)
    if not len(y) or not np.isfinite(y).all():
        raise UsageError(f"Effect {cue.get('id', '?')} produced empty or non-finite audio.")
    if c.get("lp_hint"):
        y = signal.sosfilt(signal.butter(2, min(float(c["lp_hint"]), sample_rate * 0.45), "lowpass",
                                        fs=sample_rate, output="sos"), y, axis=0)
    if c.get("pan_hint"):
        y = voices.balance(y, float(c["pan_hint"]))
    gain = float(c.get("gain", -20))
    if not math.isfinite(gain):
        raise UsageError("An effect's peak gain must be finite.")
    y = y / (np.max(np.abs(y)) + 1e-12) * dsp.db(gain)
    offset = -len(y) if c.get("align") == "end" else 0
    if c.get("clip_gain"):
        if "t" not in c:
            raise UsageError("Clip-gain automation needs the cue's absolute film placement time t.")
        y = apply_clip_gain(c, y, float(c["t"]) + offset / sample_rate, sample_rate)
    return np.asarray(y, dtype=np.float64), offset


def apply_clip_gain(cue: dict, y: np.ndarray, start_t: float, sample_rate: int = 48000) -> np.ndarray:
    """The approved same-chord handoff: linear gains at absolute film times, after cue peak levelling."""
    envelope = voices.parameters(cue).get("clip_gain")
    if not envelope:
        return y
    times = np.asarray(envelope.get("t", []), dtype=float)
    gains = np.asarray(envelope.get("g", []), dtype=float)
    before, after = float(envelope.get("before", 1)), float(envelope.get("after", 1))
    if (times.ndim != 1 or gains.ndim != 1 or not len(times) or len(times) != len(gains)
            or not np.isfinite(times).all() or not np.isfinite(gains).all()
            or np.any(np.diff(times) <= 0) or np.any(gains < 0)
            or not all(math.isfinite(v) for v in (before, after, start_t)) or min(before, after) < 0):
        raise UsageError("Clip gain needs increasing finite film times, matching nonnegative gains and finite edge gains.")
    absolute_time = start_t + np.arange(len(y)) / sample_rate
    return y * np.interp(absolute_time, times, gains, left=before, right=after)[:, None]


def make_ir(sample_rate: int = 48000, *, seconds: float = 2.4, predelay: float = 0.02,
            lp: float = 6500.0, seed: int = 11) -> np.ndarray:
    """The approved decorrelated stereo hall, normalized by channel energy."""
    if sample_rate <= 0 or seconds <= 0 or predelay < 0:
        raise UsageError("A hall needs a positive sample rate/duration and nonnegative predelay.")
    n, pre = max(1, int(seconds * sample_rate)), int(predelay * sample_rate)
    t, rng = np.arange(n) / sample_rate, np.random.default_rng(seed)
    ir = np.zeros((n + pre, 2))
    for ch in range(2):
        tail = rng.standard_normal(n) * np.exp(-6.9 * t / seconds)
        ir[pre:, ch] = signal.sosfilt(signal.butter(1, min(lp, sample_rate * 0.45), "lowpass",
                                                  fs=sample_rate, output="sos"), tail)
    return ir / max(float(np.sqrt((ir ** 2).sum(axis=0)).max()), 1e-12)


def silence_mask(silences: list[dict] | tuple, stem: str, n: int, sample_rate: int, t0: float = 0) -> np.ndarray:
    """Exact planned silence after its short incoming ramp; clipped safely to the requested window."""
    mask = np.ones(n)
    for silence in silences:
        if stem not in silence.get("stems", STEMS):
            continue
        start, end = float(silence["from"]), float(silence["to"])
        ramp_s = float(silence.get("ramp", 0.06))
        if not all(math.isfinite(v) for v in (start, end, ramp_s)) or end < start or ramp_s < 0:
            raise UsageError("A planned silence needs finite start/end times and a nonnegative ramp.")
        a, b = int(round((start - t0) * sample_rate)), int(round((end - t0) * sample_rate))
        ramp = int(ramp_s * sample_rate)
        local = np.ones(n)
        local[max(0, a):min(n, max(0, b))] = 0
        if ramp and a > 0 and a - ramp < n:
            low, high = max(0, a - ramp), min(n, a)
            if high > low:
                local[low:high] = np.linspace(1, 0, ramp)[low - (a - ramp):high - (a - ramp)]
        mask = np.minimum(mask, local)
    return mask


def render_parts(cues: list[dict], n: int, *, voice: np.ndarray | None = None,
                 film_dir: Path | None = None, sample_rate: int = 48000, trims: dict | None = None,
                 silences: list[dict] | tuple = (), t0: float = 0.0,
                 process_cue: Callable | None = None, process_bus: Callable | None = None,
                 palette: dict | None = None, motion: dict | None = None) -> dict:
    """Place cues, process each hit before its send, split hall returns, silence, then process each bus.

    process_cue(cue, segment, start_sample) -> (segment, report)
    process_bus(family, stem, full_length_array) -> array

    `kept` holds (id, family, stem, start_sample, processed_dry, reverb_db_or_None) for the family guards. Arrays in
    parts and stems share no input storage. No file is written and no master gain or limiter is applied here.
    """
    if n < 0 or sample_rate <= 0:
        raise UsageError("Stem rendering needs a nonnegative length and a positive sample rate.")
    parts, sends, kept, applied = {}, {}, [], []
    clock = None
    if film_dir is not None:
        from slate.sound.film import load
        film = load(film_dir)
        palette = film.palette if palette is None else palette
        motion = (film.brand or {}).get("motion") or {} if motion is None else motion
        clock = film.fps_num, film.fps_den
    zero = lambda: np.zeros((n, 2), dtype=np.float64)
    used_trims = {}
    for cue in expand_cues(cues):
        stem = str(cue.get("stem", "fx"))
        if stem not in ("ui", "fx", "tonal", "amb"):
            raise UsageError(f"Cue {cue.get('id', '?')} cannot render directly into stem {stem!r}.")
        family = str(cue.get("family") or "accents")
        y, offset = render_cue(cue, film_dir, sample_rate, palette=palette, motion=motion, clock=clock)
        time = float(cue["t"])
        if not math.isfinite(time):
            raise UsageError("Cue placement time must be finite.")
        start = int(round((time - t0) * sample_rate)) + offset
        a, b = max(start, 0), min(start + len(y), n)
        if b <= a:
            continue
        trim = float((trims or {}).get(stem, 0))
        if not math.isfinite(trim):
            raise UsageError("A stem trim must be finite.")
        used_trims[stem] = trim
        segment = y[a - start:b - start] * dsp.db(trim)
        info = {}
        if process_cue is not None:
            segment, info = process_cue(cue, segment, a)
            segment = np.asarray(segment, dtype=np.float64)
            if segment.shape != (b - a, 2) or not np.isfinite(segment).all():
                raise UsageError("The effect treatment changed its cue's length/channels or produced non-finite audio.")
        key = str(cue.get("id") or f"{cue.get('event', '?')}@{time:.6f}")
        reverb = voices.parameters(cue).get("reverb")
        kept.append((key, family, stem, a, segment, reverb))
        if (family, stem) not in parts:
            parts[(family, stem)] = zero()
        parts[(family, stem)][a:b] += segment
        if reverb is not None:
            return_stem = "tverb" if stem == "tonal" else "verb"
            if (family, return_stem) not in sends:
                sends[(family, return_stem)] = zero()
            sends[(family, return_stem)][a:b] += segment * dsp.db(float(reverb))
        identity = voices.parameters(cue)
        applied.append({"hit": key, "family": family, "stem": stem, "t": time,
                        "type": identity.get("type"), "size": cue.get("size"), "shot": cue.get("shot"), "event": cue.get("event"),
                        "params": identity, "waveform_identity": checks.hit_hash(y),
                        "start_sample": start, "samples": len(y), "trim_db": trim,
                        "duck_applied": process_bus is not None, **info})
    if sends and n:
        ir = make_ir(sample_rate)
        for key, send in sends.items():
            parts[key] = np.stack([signal.fftconvolve(send[:, ch], ir[:, ch])[:n] for ch in range(2)], axis=1)
    stems, families = {stem: zero() for stem in STEMS}, {}
    for (family, stem), part in parts.items():
        part *= silence_mask(silences, stem, n, sample_rate, t0)[:, None]
        if process_bus is not None:
            part = np.asarray(process_bus(family, stem, part), dtype=np.float64)
            if part.shape != (n, 2) or not np.isfinite(part).all():
                raise UsageError("The effect bus treatment changed its length/channels or produced non-finite audio.")
        # A zero-phase treatment must not ring back into an explicitly silent span.
        part[silence_mask(silences, stem, n, sample_rate, t0) == 0] = 0
        parts[(family, stem)] = part
        stems[stem] += part
        if family not in families:
            families[family] = zero()
        families[family] += part
    if voice is not None:
        voice = np.asarray(voice, dtype=np.float64)
        if voice.shape != (n, 2):
            raise UsageError("The voice key must be stereo and the same length as the film.")
        families["voice"] = voice.copy()
    return {"stems": stems, "families": families, "parts": parts, "applied": applied, "kept": kept,
            "stem_trim_applied": used_trims, "duck_applied": process_bus is not None,
            "sample_rate": sample_rate, "n": n, "t0": t0}
