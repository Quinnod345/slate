"""The approved cue voices, generalized to a film's clock, palette and installed instruments.

Wood, air, sub and counter recipes retain the approved proof's timbre. Pitched notes and pads always play real
samples. Keys, chords, motifs, motion curves and event choices are supplied as cue or brand data.

Source map: sound_engine's v_wood/v_whoosh/v_swell/v_sub become wood/noise_voice/sub; p3engine's v_air,
v_countup3/count_ticks and v_snote/v_spad become noise_voice/count_ticks and the sampled branches of render.
The retired synthesized notes, pads and hum are intentionally absent. No film event map or musical key is ported.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import numpy as np
from scipy import signal

from slate.errors import MissingPrerequisite, UsageError
from slate.motion import eases
from slate.sound import audio, dsp, instruments
from slate.sound.effects import sampler

WOOD = {
    "block": (1100, [(1.0, 1.0, 0.045), (2.57, 0.45, 0.020), (4.10, 0.22, 0.010)], 0.14),
    "temple": (520, [(1.0, 1.0, 0.120), (1.49, 0.35, 0.070), (2.83, 0.18, 0.040)], 0.08),
    "clave": (2350, [(1.0, 1.0, 0.050), (2.90, 0.30, 0.015)], 0.22),
    "knock": (260, [(1.0, 1.0, 0.060), (2.30, 0.50, 0.030), (3.80, 0.30, 0.015)], 0.35),
    "rim": (1750, [(1.0, 1.0, 0.030), (3.20, 0.40, 0.010)], 0.55),
}
TYPES = ("wood", "whoosh", "swell", "sub", "air", "countup3", "sample", "snote", "spad", "layered")


def parameters(cue: dict) -> dict:
    out = {**cue, **(cue.get("params") or {})}
    if "gain" not in out and "gain_db" in out:
        out["gain"] = out["gain_db"]
    return out


def hz(note) -> float:
    value = float(note) if isinstance(note, (int, float)) else sampler.hz_of_midi(sampler.midi_of(note))
    if not math.isfinite(value) or value <= 0:
        raise UsageError("An effect pitch must be a positive frequency or a musical note.")
    return value


def pan(mono: np.ndarray, position=0.0) -> np.ndarray:
    angle = (np.clip(np.asarray(position), -1, 1) + 1) * np.pi / 4
    return np.stack([mono * np.cos(angle), mono * np.sin(angle)], axis=1)


def balance(y: np.ndarray, position: float = 0.0) -> np.ndarray:
    angle = (np.clip(position, -1, 1) + 1) * np.pi / 4
    return y * np.array([np.cos(angle), np.sin(angle)]) * np.sqrt(2)


def ease(raw, progress: np.ndarray, motion: dict | None = None) -> np.ndarray:
    """The kit's motion law evaluated at every requested sample, including its exact endpoints."""
    spec = eases.parse(raw)
    if spec is None and isinstance(raw, str):
        spec = eases.parse(((motion or {}).get("eases") or {}).get(raw))
    if spec is None:
        raise UsageError(f"Effect ease {raw!r} is not a pinned curve or a named curve in this brand's motion law.",
                         hint="Pin the event's motion curve into its cue parameters.")
    u = np.clip(np.asarray(progress, dtype=float), 0, 1)
    return np.fromiter((eases.evaluate(spec, float(v)) for v in u.flat), float, u.size).reshape(u.shape)


def moving_filter(x, cutoff, kind, progress, sr, *, bw=None):
    out, state = np.zeros_like(x), None
    for start in range(0, len(x), 512):
        center = cutoff(progress[min(start + 256, len(x) - 1)])
        if kind == "band":
            high = min(center * (1 + bw), sr * 0.45)
            low = max(min(center / (1 + bw), high * 0.8), min(20.0, high * 0.5))
            sos = signal.butter(2, [low, high], "bandpass", fs=sr, output="sos")
        else:
            sos = signal.butter(2, min(center, sr * 0.45), kind, fs=sr, output="sos")
        if state is None:
            state = np.zeros((len(sos), 2))
        out[start:start + 512], state = signal.sosfilt(sos, x[start:start + 512], zi=state)
    return out


def _size(c, sr, default=None):
    duration = float(c.get("dur", default) or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise UsageError("Every effect needs a positive duration.")
    return max(1, int(duration * sr))


def wood(c, sr):
    kind = c.get("kind", "block")
    if kind not in WOOD:
        raise UsageError(f"Unknown wood material {kind!r}; choose {', '.join(WOOD)}.")
    base, modes, noise = WOOD[kind]
    frequency = hz(c.get("pitch", base)) * 2 ** (float(c.get("cents", 0)) / 1200)
    n = _size(c, sr, 0.35)
    t = np.arange(n) / sr
    rng = np.random.default_rng(int(c.get("seed", 17)))
    drop = 1 + 0.025 * np.exp(-t / 0.004)
    y = np.zeros(n)
    for ratio, level, decay in modes:
        phase = 2 * np.pi * np.cumsum(frequency * ratio * drop) / sr + rng.uniform(0, 2 * np.pi)
        y += level * np.sin(phase) * np.exp(-t / (decay * float(c.get("ring", 1))))
    burst = rng.standard_normal(n) * np.exp(-t / 0.0015)
    high = min(frequency * 5, sr * 0.45)
    low = min(max(frequency * 0.6, 60), high * 0.8)
    burst = signal.sosfilt(signal.butter(2, [low, high], "bandpass", fs=sr, output="sos"), burst)
    y = (1 - noise) * y + noise * burst / (np.max(np.abs(burst)) + 1e-12)
    return pan(y * (1 - np.exp(-t / 0.0006)), c.get("pan", 0.0))


def noise_voice(c, sr, kind, motion=None):
    n = _size(c, sr)
    u = ease(c.get("ease", "linear"), np.linspace(0, 1, n), motion)
    defaults = (700, 2600) if kind == "air" else ((300, 7000) if kind == "swell" else (300, 3000))
    f0, f1 = float(c.get("f0", defaults[0])), float(c.get("f1", defaults[1]))
    if min(f0, f1) <= 0:
        raise UsageError("An air filter's frequencies must be positive.")
    x = np.random.default_rng(int(c.get("seed", 5 if kind == "swell" else 3))).standard_normal(n)
    y = moving_filter(x, lambda k: f0 * (f1 / f0) ** k, "lowpass" if kind == "swell" else "band", u, sr,
                      bw=float(c.get("bw", 0.9)))
    if kind == "swell":
        y *= u ** 1.5
        k = min(n, max(1, int(0.004 * sr)))
        y[-k:] *= np.linspace(1, 0, k)
        return pan(y)
    if kind == "air":
        speed = np.gradient(u) if n > 1 else np.ones(n)
        speed /= np.max(speed) + 1e-12
        floor = float(c.get("floor", 0.15))
        y *= (floor + (1 - floor) * speed) * dsp.fade(n, 0.012, float(c.get("release", 0.06)), sr)
    else:
        peak, linear = float(c.get("peak", 0.6)), np.linspace(0, 1, n)
        if not 0 < peak < 1:
            raise UsageError("A whoosh peak must be between 0 and 1.")
        y *= np.where(linear < peak, np.sin(np.pi / 2 * linear / peak) ** 2,
                      np.cos(np.pi / 2 * (linear - peak) / (1 - peak + 1e-9)) ** 2)
    return pan(y, c.get("pan_from", 0.0) + (c.get("pan_to", 0.0) - c.get("pan_from", 0.0)) * u)


def sub(c, sr):
    n = _size(c, sr, 1.4)
    t = np.arange(n) / sr
    end, start = hz(c.get("f1", 38)), hz(c.get("f0", 72))
    frequency = end + (start - end) * np.exp(-t / 0.07)
    y = np.sin(2 * np.pi * np.cumsum(frequency) / sr)
    return pan(y * np.exp(-t / float(c.get("decay", 0.45))) * (1 - np.exp(-t / 0.003)))


def count_ticks(cue: dict, sample_rate: int = 48000, motion: dict | None = None) -> list[dict]:
    """The approved counter's exact click schedule; each returned tick can be verified independently."""
    c = parameters(cue)
    dur, first, last = float(c["dur"]), float(c.get("from", 0)), float(c["to"])
    if dur <= 0 or not all(math.isfinite(v) for v in (dur, first, last)):
        raise UsageError("A counter needs finite values and a positive duration.")
    span = abs(last - first)
    if span == 0:
        return []
    if not c.get("scale"):
        raise UsageError("A counter needs a scale from the brand's sound palette.")
    scale = [hz(note) for note in c["scale"]]
    plan = []
    if c.get("hold_frames"):
        fps_num, fps_den = float(c.get("fps_num", 0)), float(c.get("fps_den", 0))
        hold = int(c["hold_frames"])
        if fps_num <= 0 or fps_den <= 0 or hold <= 0:
            raise UsageError("A held counter needs its film's fps_num, fps_den and positive hold_frames.")
        count = int(math.ceil(dur * fps_num / fps_den / hold))
        if count > 100000:
            raise UsageError("The counter exceeds 100000 held figures; shorten its event span.")
        times = np.minimum(np.arange(1, count + 1) * hold * fps_den / fps_num, dur)
        values = np.floor(first + (last - first) * ease(c.get("ease", "linear"), times / dur, motion) + 0.5)
        shown = int(np.floor(first + 0.5))
        for time, value in zip(times, values):
            if value != shown:
                plan.append((float(time), abs(value - first) / span))
                shown = value
    else:
        step = float(c.get("step") or max(1.0, span / max(1, min(span, round(15 * dur)))))
        if step <= 0 or span / step > 100000:
            raise UsageError("A counter needs a positive step and at most 100000 ticks.")
        times = np.linspace(0, 1, 8000)
        progress = ease(c.get("ease", "linear"), times, motion)
        for distance in np.arange(step, span + 1e-9, step):
            index = int(np.searchsorted(progress * span, distance))
            if index < len(times):
                plan.append((float(times[index] * dur), float(distance / span)))
    rng = np.random.default_rng(int(c.get("seed", 0)) + 7919)
    ticks = []
    for index, (time, fraction) in enumerate(plan):
        pitch_index = min(int(fraction * len(scale)), len(scale) - 1)
        pitch = scale[len(scale) - 1 - pitch_index] if last < first else scale[pitch_index]
        gain = (1 - 0.45 * fraction) * dsp.db(float(rng.uniform(-1, 1)) * float(c.get("jitter_db", 0.8)))
        ticks.append({"at": int(time * sample_rate) / sample_rate,
                      "params": {"kind": c.get("kind", "block"), "pitch": pitch, "dur": 0.12,
                                 "pan": 0.22 * float(np.sin(index * 1.7)), "seed": int(c.get("seed", 0)) * 101 + index},
                      "gain": float(20 * np.log10(max(gain, 1e-12)))})
    return ticks


@functools.lru_cache(maxsize=24)
def _instrument(role, palette_json, library, modified):
    return sampler.Instrument(role, json.loads(palette_json))


def instrument(role, palette):
    spec = instruments.require(role, palette)
    stat = Path(spec["path"]).stat()
    return _instrument(role, json.dumps(palette, sort_keys=True), str(instruments.library_base()), stat.st_mtime_ns)


def sample_path(ref: str, film_dir: Path | None = None) -> Path:
    """Resolve a recorded effect in place, including portable Apple-library references and derived-film media."""
    if ref.startswith("FCP:"):
        return instruments.roots()["fcp-sound-effects"] / ref[4:]
    if ref.startswith("LOOPS:"):
        return instruments.roots()["apple-loops"] / ref[6:]
    path = Path(ref).expanduser()
    if path.is_absolute():
        return path
    if film_dir is not None and ref:
        from slate.sound.film import load
        return load(film_dir).resolve_media(ref)
    raise UsageError("A relative sample path needs its film folder.")


def dependencies(cue: dict, film_dir: Path | None = None, palette: dict | None = None) -> set[Path]:
    """Read-only file dependencies, including the selected EXS zones, layer recordings and gesture notes.

    Paths are receipts, not copies. No sample is decoded and no tuning cache is written. The caller hashes these
    files before/after work; a changed selected sample cannot silently reuse another recording's cue anchors.
    """
    if palette is None and film_dir is not None:
        from slate.sound.film import load
        palette = load(film_dir).palette
    result: set[Path] = set()
    def visit(item, depth=0):
        if depth > 8:
            raise UsageError("More than eight nested cue layers.")
        params = parameters(item)
        if params.get("file"):
            result.add(sample_path(str(params["file"]), film_dir))
        if item.get("notes"):
            base = {k: v for k, v in item.items() if k != "notes"}
            for note in item["notes"]:
                if not isinstance(note, dict):
                    raise UsageError("Gesture notes must be cue objects; chord pitches belong in params.notes.")
                visit({**base, **note}, depth + 1)
            return
        if params.get("type") == "layered":
            for layer in params.get("layers") or []:
                visit(layer, depth + 1)
        selections = []
        if params.get("type") == "snote":
            selections.append((params.get("role", "felt_note"),
                               sampler.midi_of(params["pitch"]) + float(params.get("cents", 0)) / 100,
                               int(params.get("vel", 70))))
        elif params.get("type") == "spad":
            for note in params.get("notes") or []:
                midi = sampler.midi_of(note)
                default = next(role for top, role in ((45, "pad_low"), (60, "pad_mid_low"), (67, "pad_mid"),
                                                      (128, "pad_high")) if midi < top)
                selections.append(((params.get("section") or {}).get(str(note), default), midi,
                                   int(params.get("vel", 50))))
        for role, midi, velocity in selections:
            installed = sampler.Instrument(role, palette)
            zone = installed.zone(midi, velocity, int(params.get("take", 0)))
            result.add(installed.path)
            result.add(installed.sample_file(zone)[0])
    visit(cue)
    return result


def render(cue: dict, *, film_dir: Path | None = None, sample_rate: int = 48000,
           palette: dict | None = None, motion: dict | None = None, _depth: int = 0) -> np.ndarray:
    """Unlevelled stereo cue samples. No file is written, and the input cue is never mutated."""
    if sample_rate <= 0 or _depth > 8:
        raise UsageError("Invalid effect sample rate or more than eight nested cue layers.")
    c = parameters(cue)
    kind = c.get("type")
    if kind == "wood":
        return wood(c, sample_rate)
    if kind in ("whoosh", "air", "swell"):
        return noise_voice(c, sample_rate, kind, motion)
    if kind == "sub":
        return sub(c, sample_rate)
    if kind == "countup3":
        out = np.zeros((_size({"dur": float(c["dur"]) + 0.3}, sample_rate), 2))
        for tick in count_ticks(c, sample_rate, motion):
            y = wood(tick["params"], sample_rate)
            y *= dsp.db(tick["gain"]) / (np.max(np.abs(y)) + 1e-12)
            start = int(round(tick["at"] * sample_rate))
            length = min(len(y), len(out) - start)
            if length > 0:
                out[start:start + length] += y[:length]
        return out
    if kind == "sample":
        ref = str(c.get("file", ""))
        path = sample_path(ref, film_dir)
        if not path.is_file():
            raise MissingPrerequisite(f"The effect's recorded sample is missing: {path}",
                                      hint=instruments.DOWNLOAD if ref.startswith(("FCP:", "LOOPS:")) else
                                      "Restore the referenced sample or choose an available approved recording.")
        y = audio.load(path, sample_rate)
        if "trim" in c:
            a, b = (float(v) for v in c["trim"])
            if not 0 <= a < b:
                raise UsageError("A sample trim needs 0 <= start < end.")
            y = y[int(a * sample_rate):int(b * sample_rate)]
        if c.get("reverse"):
            y = y[::-1].copy()
        rate = float(c.get("rate", 1))
        if not math.isfinite(rate) or rate <= 0:
            raise UsageError("A sample playback rate must be positive.")
        if rate != 1:
            y = signal.resample_poly(y, 1000, max(1, int(round(1000 * rate))), axis=0)
        for key, filter_type in (("hp", "highpass"), ("lp", "lowpass")):
            if c.get(key):
                y = signal.sosfilt(signal.butter(2, min(float(c[key]), sample_rate * 0.45), filter_type,
                                                fs=sample_rate, output="sos"), y, axis=0)
        if "dur" in c:
            y = y[:_size(c, sample_rate)]
        return y * dsp.fade(len(y), float(c.get("fade_in", 0.005)), float(c.get("fade_out", 0.05)), sample_rate)[:, None]
    if kind == "snote":
        inst = instrument(c.get("role", "felt_note"), palette)
        pitch = sampler.midi_of(c["pitch"]) + float(c.get("cents", 0)) / 100
        y = inst.play(pitch, vel=int(c.get("vel", 70)), take=int(c.get("take", 0)), dur=float(c.get("dur", 1.2)),
                      sample_rate=sample_rate)
        return balance(y * dsp.fade(len(y), 0, float(c.get("release", 0.15)), sample_rate)[:, None], c.get("pan", 0))
    if kind == "spad":
        if not c.get("notes"):
            raise UsageError("A sampled pad needs chord notes from the brand's palette.")
        n = _size(c, sample_rate)
        out = np.zeros((n, 2))
        for index, note in enumerate(c["notes"]):
            midi = sampler.midi_of(note)
            default_role = next(role for top, role in ((45, "pad_low"), (60, "pad_mid_low"), (67, "pad_mid"),
                                                       (128, "pad_high")) if midi < top)
            role = (c.get("section") or {}).get(str(note), default_role)
            y = instrument(role, palette).play(midi, vel=int(c.get("vel", 50)), take=int(c.get("take", 0)),
                    dur=float(c["dur"]) + 0.05, sustain=True, skip=c.get("skip", "loop"), sample_rate=sample_rate)[:n]
            out[:len(y)] += y * 0.9 ** index
        progress = ease(c.get("ease", "linear"), np.linspace(0, 1, n), motion)
        f0, f1 = float(c.get("lp_from", 900)), float(c.get("lp_to", c.get("lp_from", 900)))
        for ch in range(2):
            out[:, ch] = moving_filter(out[:, ch], lambda k: f0 * (f1 / f0) ** k, "lowpass", progress, sample_rate)
        breath = 1 + float(c.get("breath", 0.12)) * np.sin(2 * np.pi * 0.11 * np.arange(n) / sample_rate
                                                          + float(c.get("breath_phase", 0)))
        return out * (breath * dsp.fade(n, float(c.get("attack", 1.5)), float(c.get("release", 2)), sample_rate))[:, None]
    if kind == "layered":
        layers = c.get("layers") or []
        if not layers:
            raise UsageError("A layered cue needs at least one voice.")
        rendered = []
        for layer in layers:
            y = render(layer, film_dir=film_dir, sample_rate=sample_rate, palette=palette, motion=motion, _depth=_depth + 1)
            y *= dsp.db(float(parameters(layer).get("gain", 0))) / (np.max(np.abs(y)) + 1e-12)
            start = int(round(float(layer.get("offset", 0)) * sample_rate))
            if start < 0:
                raise UsageError("A layer's offset cannot be negative.")
            rendered.append((start, y))
        out = np.zeros((max(start + len(y) for start, y in rendered), 2))
        for start, y in rendered:
            out[start:start + len(y)] += y
        return out
    raise UsageError(f"Unsupported effect voice {kind!r}; use {', '.join(TYPES)}. Tonal roles require real samples.")
