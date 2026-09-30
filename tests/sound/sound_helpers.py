"""Neutral fixtures for the sound tests: synthetic speech, a pad score, a small film on its own clock, a brand pack.

Nothing here comes from a real film or brand. The film runs at 25 fps on purpose, so nothing in the sound code can lean
on a frame rate it assumes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy import signal

from slate import jsonio
from slate.sound import audio

SR = 48000
FPS = (25, 1)


def sine(db: float, seconds: float, f: float = 1000.0, ch: int = 2, sr: int = SR, phase: float = 0.0,
         fade_s: float = 0.0) -> np.ndarray:
    t = np.arange(int(round(seconds * sr))) / sr
    x = 10 ** (db / 20) * np.sin(2 * np.pi * f * t + phase)
    if fade_s:
        k = int(fade_s * sr)
        ramp = np.sin(np.linspace(0, np.pi / 2, k)) ** 2
        x[:k] *= ramp
        x[-k:] *= ramp[::-1]
    return np.stack([x] * ch, 1) if ch > 1 else x


def speech(seconds: float, seed: int, level: float = 0.35, sr: int = SR, plosives: bool = False) -> tuple[np.ndarray, list[dict]]:
    """Speech-like audio (voiced syllables on a 110-180 Hz fundamental with a little breath noise) and its 'words'."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    y = np.zeros(n)
    words = []
    pos = 0.08
    while pos < seconds - 0.35:
        dur = float(rng.uniform(0.14, 0.32))
        k = np.arange(int(dur * sr)) / sr
        f0 = float(rng.uniform(110, 180))
        env = np.sin(np.pi * k / dur) ** 2
        # voiced harmonics rolling off slowly, as real narration's energy reaches well into 1-4 kHz
        harm = sum(np.sin(2 * np.pi * f0 * h * k + rng.uniform(0, 6.28)) / h ** 0.7 for h in range(1, 25) if f0 * h < 6000)
        harm /= np.max(np.abs(harm)) + 1e-12
        noise = signal.sosfilt(signal.butter(2, [1500, 5000], "bandpass", fs=sr, output="sos"), rng.standard_normal(len(k)))
        syl = level * env * (0.55 * harm + 0.05 * noise)
        if plosives and rng.uniform() < 0.3:
            syl[: int(0.004 * sr)] += 0.4 * np.hanning(int(0.004 * sr) * 2)[: int(0.004 * sr)]
        i = int(pos * sr)
        y[i:i + len(syl)] += syl[: max(0, min(len(syl), n - i))]
        words.append({"w": f"la{len(words) + 1}", "start": round(pos, 3), "end": round(pos + dur, 3)})
        pos += dur + float(rng.uniform(0.04, 0.12)) + (0.3 if rng.uniform() < 0.15 else 0.0)
    return np.stack([y, y], 1), words


def pad(seconds: float, sr: int = SR, level_db: float = -24.0, notes=(146.83, 220.0, 293.66, 369.99), seed: int = 1,
        fade_in: float = 0.0, fade_out: float = 0.0, noise_db: float = -70.0) -> np.ndarray:
    """A soft held chord with a slow breath and a recording's noise floor (stands in for a composed score)."""
    t = np.arange(int(round(seconds * sr))) / sr
    rng = np.random.default_rng(seed)
    left = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) for f in notes)
    right = sum(np.sin(2 * np.pi * f * 1.001 * t + rng.uniform(0, 6.28)) for f in notes)
    breath = 1 + 0.1 * np.sin(2 * np.pi * 0.13 * t)
    x = np.stack([left, right], 1) * breath[:, None]
    x *= 10 ** (level_db / 20) / (np.sqrt(np.mean(x ** 2)) + 1e-12)
    if fade_in:
        k = int(fade_in * sr)
        x[:k] *= np.linspace(0, 1, k)[:, None]
    if fade_out:
        k = int(fade_out * sr)
        x[-k:] *= np.linspace(1, 0, k)[:, None]
    if noise_db is not None:  # a real take has a floor; pure sines make every FFT frame look like an onset
        x = x + 10 ** (noise_db / 20) * rng.standard_normal(x.shape)
    return x


def wood_hit(seed: int, pitch: float = 1100.0, sr: int = SR, dur: float = 0.25) -> np.ndarray:
    """A short modal wood strike (stands in for a designed tap)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * sr)) / sr
    y = sum(lv * np.sin(2 * np.pi * pitch * r * t + rng.uniform(0, 6.28)) * np.exp(-t / d)
            for r, lv, d in ((1.0, 1.0, 0.045), (2.57, 0.45, 0.02), (4.1, 0.22, 0.01)))
    y *= 1 - np.exp(-t / 0.0006)
    return np.stack([y, y], 1) * 0.5


def brand(brands_dir: Path, name: str = "testbrand", *, palette: dict | None = None, reference: dict | None = None,
          sound: dict | None = None) -> Path:
    d = brands_dir / name
    d.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "sound": {"key": "A minor", "tempo": [70, 80], "instrumentation": ["soft piano", "warm strings"],
                                    "materials": ["paper"], "banned_timbres": ["kazoo"], **(sound or {})},
            "references": {"truth": None, "look_motion": None, "voice": None, "sound": reference}}
    if palette:
        data["sound"]["palette"] = palette
    (d / "brand.json").write_text(json.dumps(data, indent=1))
    return d


def film(films_dir: Path, film_id: str = "f1", *, brand_name: str = "testbrand", voice: bool = True, score: bool = True,
         plosives: bool = False, extra: dict | None = None) -> Path:
    """A 14-second film at 25 fps: two acts, four shots, four voice takes, word timings, and (optionally) a score."""
    fd = films_dir / film_id
    (fd / "timing").mkdir(parents=True, exist_ok=True)
    num, den = FPS
    frames = [(0, 100), (100, 200), (200, 275), (275, 350)]
    shots = []
    for i, (a, b) in enumerate(frames, 1):
        shots.append({"id": f"S0{i}", "act": 1 if i <= 2 else 2, "frame_in": a, "frame_out": b, "frames": b - a,
                      "sec_in": a * den / num, "sec_out": b * den / num})
    jsonio.write_atomic(fd / "timing" / "shots.json", {"fps_num": num, "fps_den": den, "width": 720, "height": 1280,
                                                       "total_frames": 350, "acts": [{"index": 1, "shots": ["S01", "S02"]},
                                                                                     {"index": 2, "shots": ["S03", "S04"]}],
                                                       "shots": shots})
    fj = {"schema": 1, "id": film_id, "title": "Sound test film", "format": "reel", "language": "en", "fps_num": num,
          "fps_den": den, "width": 720, "height": 1280, "aspect": "9:16", "brand": brand_name, "voice": None,
          "targets": {"runtime_s": 14, "lufs": -14, "true_peak": -1},
          "budget": {"elevenlabs": 20000, "higgsfield": 0, "rework_factor": 1.5}, "mode": "supervised",
          "generation": "rarely", "parent": None, "derivation": None, "pins": {}, "pin_log": [], "models": {},
          "versions": [{"n": 1, "started_at": "2026-10-01T09:00:00+00:00"}], "created_at": "2026-10-01T09:00:00+00:00",
          "slate_version": "0.1.0", **(extra or {})}
    (fd / "film.json").write_text(json.dumps(fj, indent=1))
    if voice:
        cues, words = [], {}
        for i, (at, secs) in enumerate(((0.4, 3.0), (0.3, 3.2), (0.3, 2.2), (0.2, 2.4)), 1):
            y, w = speech(secs, seed=10 + i, plosives=plosives)
            rel = f"voice/takes/S0{i}.wav"
            audio.write_wav(fd / rel, y, SR, 24)
            cues.append({"shot": f"S0{i}", "at": at, "file": rel, "gain": -20, "fade_in": 0.01, "fade_out": 0.05})
            words[f"S0{i}"] = {"take": rel, "line": " ".join(x["w"] for x in w), "words": w}
        jsonio.write_atomic(fd / "timing" / "vo_cues.json", {"cues": cues})
        jsonio.write_atomic(fd / "timing" / "vo_words.json", {"shots": words})
    if score:
        audio.write_wav(fd / "sound" / "score" / "score.wav", pad(14.0), SR, 24)
    return fd


def read(path: Path) -> np.ndarray:
    x, _ = audio.read_wav(path)
    return audio.to_stereo(x)


def db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))
