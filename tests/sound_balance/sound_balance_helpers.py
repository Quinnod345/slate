"""Neutral fixtures for the balance tests: speech-like words, a pad score, wood accents, noise textures, and a small
mixed film folder. Nothing here comes from a real film or brand; the film runs at 25 fps so nothing can lean on a
rate it assumes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import signal

from slate import jsonio
from slate.sound import audio

SR = 48000
FPS = (25, 1)


def db(x: float) -> float:
    return 10 ** (x / 20)


def words_at(starts: list[float], dur: float = 0.24) -> list[tuple[str, float, float]]:
    return [(f"w{i + 1}", round(s, 3), round(s + dur, 3)) for i, s in enumerate(starts)]


def speech(n: int, words: list[tuple[str, float, float]], *, sr: int = SR, level: float = 0.3, seed: int = 1) -> np.ndarray:
    """Voiced syllables (harmonics reaching well into 1-5 kHz, a little breath) inside each word's span. Stereo."""
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    for _w, s, e in words:
        k = np.arange(int((e - s) * sr)) / sr
        f0 = float(rng.uniform(110, 170))
        env = np.sin(np.pi * k / (e - s)) ** 2
        harm = sum(np.sin(2 * np.pi * f0 * h * k + rng.uniform(0, 6.28)) / h ** 0.6 for h in range(1, 40)
                   if f0 * h < min(7000, sr / 2 - 500))
        harm /= np.max(np.abs(harm)) + 1e-12
        noise = signal.sosfilt(signal.butter(2, [1500, 6000], "bandpass", fs=sr, output="sos"), rng.standard_normal(len(k)))
        i = int(s * sr)
        seg = level * env * (0.6 * harm + 0.08 * noise)
        y[i:i + len(seg)] += seg[: max(0, n - i)]
    return np.stack([y, y], 1)


def pad(n: int, *, sr: int = SR, level_db: float = -30.0, seed: int = 2, bright: bool = False) -> np.ndarray:
    """A soft held chord with a little air on top (a score). bright lifts that air into a shimmer that crowds the
    consonant band."""
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    x = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) for f in (146.83, 220.0, 293.66, 369.99))
    sh = signal.sosfilt(signal.butter(4, [1200, 4500], "bandpass", fs=sr, output="sos"), rng.standard_normal(n))
    x = x + (1.6 if bright else 0.08) * sh / (sh.std() + 1e-12)
    x = x * db(level_db) / (np.sqrt(np.mean(x ** 2)) + 1e-12)
    return np.stack([x, x * 0.98], 1)


def wood(sr: int = SR, *, pitch: float = 1800.0, dur: float = 0.12, amp: float = 0.3) -> np.ndarray:
    """A short modal wood strike (an accent)."""
    t = np.arange(int(dur * sr)) / sr
    y = sum(lv * np.sin(2 * np.pi * pitch * r * t) * np.exp(-t / d) for r, lv, d in ((1.0, 1.0, 0.03), (2.4, 0.5, 0.015)))
    y *= 1 - np.exp(-t / 0.0008)
    return np.stack([y, y], 1) * amp


def air(n_s: float, *, sr: int = SR, amp: float = 0.05, seed: int = 4) -> np.ndarray:
    """Noise in 2-6 kHz with a soft swell (a write-on texture)."""
    n = int(n_s * sr)
    rng = np.random.default_rng(seed)
    y = signal.sosfilt(signal.butter(4, [2000, 6000], "bandpass", fs=sr, output="sos"), rng.standard_normal(n))
    y = amp * y / (y.std() + 1e-12) * np.sin(np.pi * np.arange(n) / n) ** 2
    return np.stack([y, y], 1)


def place(n: int, clip: np.ndarray, at: float, sr: int = SR) -> np.ndarray:
    out = np.zeros((n, 2))
    i = int(round(at * sr))
    k = max(0, min(len(clip), n - i))
    out[i:i + k] = clip[:k]
    return out


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    return path


class MixFilm:
    """A film folder the sound stages would leave after `slate mix`: film.json, shots, word timings, the four delivery
    stems summing to the mix, and optionally family stems, a cue sheet, a rides record and a score plan."""

    def __init__(self, root: Path, *, film_id: str = "bal-film", seconds: float = 4.0, palette: dict | None = None,
                 brand: str = "nobrand"):
        self.path = root / film_id
        (self.path / "timing").mkdir(parents=True, exist_ok=True)
        num, den = FPS
        self.frames = int(round(seconds * num / den))
        self.seconds = self.frames * den / num
        self.n = int(round(self.seconds * SR))
        shots = [{"id": "S01", "act": 1, "frame_in": 0, "frame_out": self.frames, "frames": self.frames, "sec_in": 0.0,
                  "sec_out": self.seconds}]
        jsonio.write_atomic(self.path / "timing" / "shots.json",
                            {"fps_num": num, "fps_den": den, "width": 720, "height": 1280, "total_frames": self.frames,
                             "acts": [{"index": 1, "shots": ["S01"]}], "shots": shots})
        fj = {"schema": 1, "id": film_id, "title": "Balance test film", "format": "reel", "language": "en",
              "fps_num": num, "fps_den": den, "width": 720, "height": 1280, "aspect": "9:16", "brand": brand,
              "voice": None, "targets": {"runtime_s": seconds, "lufs": -14, "true_peak": -1},
              "budget": {"elevenlabs": 0, "higgsfield": 0, "rework_factor": 1.5}, "mode": "supervised",
              "generation": "rarely", "parent": None, "derivation": None, "pins": {}, "pin_log": [], "models": {},
              "versions": [{"n": 1, "started_at": "2026-10-01T09:00:00+00:00"}],
              "created_at": "2026-10-01T09:00:00+00:00", "slate_version": "0.1.0"}
        if palette:
            fj["sound"] = {"palette": palette}
        (self.path / "film.json").write_text(json.dumps(fj, indent=1))

    def words(self, words: list[tuple[str, float, float]]) -> None:
        """Word timings at film time: one take placed at 0 s, its words where they fall."""
        jsonio.write_atomic(self.path / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "t": 0.0, "file": "x.wav"}]})
        jsonio.write_atomic(self.path / "timing" / "vo_words.json", {"shots": {"S01": {
            "take": "x.wav", "line": " ".join(w for w, _s, _e in words),
            "words": [{"w": w, "start": s, "end": e} for w, s, e in words]}}})

    def mix(self, *, dialogue: np.ndarray, music: np.ndarray | None = None, effects: np.ndarray | None = None,
            ambience: np.ndarray | None = None, families: dict[str, np.ndarray] | None = None,
            tonal: np.ndarray | None = None) -> Path:
        zero = np.zeros((self.n, 2))
        stems = {"dialogue": dialogue, "effects": effects if effects is not None else zero,
                 "ambience": ambience if ambience is not None else zero,
                 "music": (music if music is not None else zero) + (tonal if tonal is not None else zero)}
        d = self.path / "sound" / "mix"
        total = zero.copy()
        for k, x in stems.items():
            audio.write_wav(d / "stems" / f"{k}.wav", x, SR, 24)
            q, _ = audio.read_wav(d / "stems" / f"{k}.wav")
            total += q
        if tonal is not None:
            audio.write_wav(d / "engine_stems" / "tonal.wav", tonal, SR, 24)
        for name, x in (families or {}).items():
            audio.write_wav(d / "family_stems" / f"{name}.wav", x, SR, 24)
        return audio.write_wav(d / "final_mix.wav", total, SR, 24)

    def cues(self, hits: list[dict]) -> Path:
        return write_json(self.path / "sound" / "sound_cues.json", {"cues": hits})

    def rides(self, record: dict) -> Path:
        return write_json(self.path / "sound" / "mix" / "rides.json", record)

    def score(self, x: np.ndarray, *, stops: list[dict] | None = None) -> Path:
        p = audio.write_wav(self.path / "sound" / "score" / "score.wav", x, SR, 24)
        write_json(self.path / "sound" / "score" / "score.json", {"planned_stops": stops or []})
        return p
