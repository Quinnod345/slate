"""Helpers for the voice tests: a neutral film, a neutral brand, speech-like audio and a stand-in Whisper.

Nothing here names a real brand, voice or person. The film runs at 25 fps and 720x1280 on purpose: sizes and frame
rates are film data, never constants in the code under test. (A uniquely named module, so it never collides with
another folder's conftest.)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import jsonio

LINES = [
    ("S01", 4.0, "Every film starts with one line."),
    ("S02", 5.5, "Slate hears each word, then fits it to the cut."),
    ("S03", 3.0, ""),
    ("S04", 4.5, "Warm voices, never whispered."),
]

BRAND = {
    "name": "voicetest",
    "voice": {"provider": "elevenlabs", "source": "library", "voice_id": "demo-narrator-01",
              "voice_name": "Demo narrator", "seed": 7, "lexicon": ["Slate"],
              "candidates": [{"voice_id": "demo-narrator-02", "name": "Second narrator", "source": "library"}]},
}

FPS = (25, 1)


def film_json(film_id: str = "demo-reel", *, mode: str = "supervised", voice: dict | None = None,
              language: str = "en") -> dict:
    """A complete film.json (schemas/film.schema.json) for a neutral 9:16 reel."""
    return {
        "schema": 1, "id": film_id, "title": "Voice test film", "format": "test-reel", "format_source": None,
        "language": language, "fps_num": FPS[0], "fps_den": FPS[1], "width": 720, "height": 1280, "aspect": "9:16",
        "brand": BRAND["name"], "brand_dir": None,
        "voice": voice or {"source": "brand", "provider": "elevenlabs", "voice_id": BRAND["voice"]["voice_id"],
                           "model": None, "settings": None, "note": "from the brand pack"},
        "targets": {"runtime_s": 17.0, "runtime_range_s": None, "lufs": -14.0, "true_peak": -1.0},
        "budget": {"elevenlabs": 5000, "higgsfield": 0, "rework_factor": 1.5},
        "mode": mode, "generation": "none", "parent": None, "derivation": None, "pins": {}, "pin_log": [],
        "models": {}, "versions": [{"n": 1, "started_at": "2026-09-30T12:00:00+00:00", "note": "first version",
                                    "delivered_at": None}],
        "created_at": "2026-09-30T12:00:00+00:00", "slate_version": "0.1.0",
    }


def make_film(films: Path, film_id: str = "demo-reel", *, lines=LINES, mode: str = "supervised",
              voice: dict | None = None, timing: bool = True) -> Path:
    film = films / film_id
    (film / "script").mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(film / "film.json", film_json(film_id, mode=mode, voice=voice))
    jsonio.write_atomic(film / "script" / "storyboard.json",
                        {"acts": [{"name": "Act 1", "shots": [{"id": i, "dur": d, "vo": v} for i, d, v in lines]}]})
    if timing:
        t, rows = 0.0, []
        for i, d, _v in lines:
            rows.append({"id": i, "act": 1, "frame_in": int(round(t * FPS[0] / FPS[1])),
                         "frame_out": int(round((t + d) * FPS[0] / FPS[1]))})
            t += d
        jsonio.write_atomic(film / "timing" / "shots.json",
                            {"fps_num": FPS[0], "fps_den": FPS[1], "width": 720, "height": 1280,
                             "total_frames": rows[-1]["frame_out"], "shots": rows})
    return film


def speech(words: list[str], seed: str = "v", sr: int = 48000):
    """Speech-like audio (one harmonic burst per word) and its exact word times."""
    from slate.vendors.elevenlabs import fake_speech

    return fake_speech(" ".join(words), seed, sr=sr)


def write_speech(path: Path, text: str, seed: str = "v", sr: int = 48000, extra: np.ndarray | None = None):
    from slate.voice import audio

    x, words = speech(text.split(), seed, sr)
    if extra is not None:
        x = np.concatenate([x, extra])
    audio.write_wav(path, x, sr, 24)
    return words


def whisper_doc(words: list[dict], text: str | None = None) -> dict:
    """What the Whisper CLI writes (segments -> words with a leading space, as Whisper does)."""
    return {"text": text if text is not None else " ".join(w["word"] for w in words),
            "segments": [{"start": words[0]["start"] if words else 0, "end": words[-1]["end"] if words else 0,
                          "text": text, "avg_logprob": -0.2,
                          "words": [{"word": " " + w["word"], "start": w["start"], "end": w["end"],
                                     "probability": 0.95} for w in words]}],
            "language": "en"}


def register_take(vf, shot: str, wav: Path, *, sent: str | None = None, take: str = "t01") -> None:
    """Register an existing WAV as a non-replayed take (as a real read would land) and select it."""
    from slate.voice.film import now

    vf.save_take_json(shot, take, {"shot": shot, "take": take, "text": vf.line(shot).text,
                                   "sent": sent or vf.line(shot).text, "model": "eleven_v4", "voice_id": "v",
                                   "voice_settings": {}, "seed": 1, "format": "pcm_48000", "request_id": "req_real",
                                   "file": vf.rel(wav), "sha256": jsonio.sha256(wav), "seconds": 1.0,
                                   "replay": False, "created_at": now(), "stages": {}})
    vf.update_take(shot, take, raw=vf.rel(wav), clean=None, paced=None, align=None, replay=False)
    vf.select_take(shot, take)


def real_take(vf, shot: str, text: str, *, seed: str = "real", extra: np.ndarray | None = None,
              sent: str | None = None, take: str = "t01") -> tuple[Path, list[dict]]:
    """A non-replayed take made by hand (as a real read would land), registered and selected in the manifest.
    `text` is what the audio says; `sent` what was sent (default: the script line)."""
    wav = vf.take_dir(shot) / f"{take}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    words = write_speech(wav, text, seed=seed, extra=extra)
    register_take(vf, shot, wav, sent=sent, take=take)
    return wav, words


GUARD_WHISPER = r'''#!{python}
"""Stands where Whisper would be in every voice test that didn't ask for the stand-in: fail loudly, load nothing."""
import sys
sys.stderr.write("a voice test tried to run the real Whisper model; give it the fake_whisper fixture, or mark it "
                 "@pytest.mark.whisper and opt in with -m whisper\n")
sys.exit(97)
'''

FAKE_WHISPER = r'''#!{python}
"""A stand-in for the Whisper CLI in tests: answers from a map of real paths to transcripts."""
import json, os, sys
args = sys.argv[1:]
out = args[args.index("--output_dir") + 1]
files = []
for a in args:
    if a.startswith("--"):
        break
    files.append(a)
plan = json.load(open(os.environ["SLATE_TEST_WHISPER_MAP"]))
with open(os.environ["SLATE_TEST_WHISPER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
for f in files:
    doc = plan.get(os.path.realpath(f))
    if doc is None:
        continue
    stem = os.path.splitext(os.path.basename(f))[0]
    json.dump(doc, open(os.path.join(out, stem + ".json"), "w"))
'''


# ---------------------------------------------------------------------------------------------------------------
# Richer synthetic voices for the polish and analysis tests (numpy only): a formant-shaped glottal pulse train per
# word, sibilant noise on the words that have an S, SH or Z, and optional breath. Nothing here is a real voice.

FORMANTS = ((500.0, 80.0), (1500.0, 120.0), (2500.0, 160.0), (3400.0, 250.0))


def _resonate(x: np.ndarray, sr: int, f: float, bw: float) -> np.ndarray:
    from scipy.signal import lfilter

    r = np.exp(-np.pi * bw / sr)
    a = [1.0, -2 * r * np.cos(2 * np.pi * f / sr), r * r]
    return lfilter([1 - r], a, x)


def _voiced(n: int, sr: int, f0: np.ndarray, rng: np.random.Generator, breath: float, noise_only: bool) -> np.ndarray:
    if noise_only:
        src = rng.standard_normal(n) * 0.25
    else:
        phase = 2 * np.pi * np.cumsum(f0) / sr
        kmax = int(min(7000.0, 0.45 * sr) / max(f0.max(), 60.0))
        src = sum(np.sin(k * phase) / k for k in range(1, kmax + 1))
        src = src + breath * rng.standard_normal(n) * 0.5
    y = sum(_resonate(src, sr, f, bw) for f, bw in FORMANTS)
    return y


def rich_voice(text: str, *, f0: float = 180.0, sr: int = 48000, sibilance: float = 1.0, breath: float = 0.0,
               whisper: bool = False, last_glide_st: float = 0.0, seed: int = 0, end_cut: bool = False,
               level: float = 0.25) -> tuple[np.ndarray, list[dict]]:
    """A speech-like take and its exact word times. `last_glide_st` bends the last word's pitch (+ rises, - falls);
    `whisper` replaces the voice with shaped noise; `breath` mixes noise into the voice; `end_cut` stops the last
    word at full level (a take cut off mid-word)."""
    from scipy import signal as sg

    rng = np.random.default_rng(seed)
    words = text.split()
    pieces, timed, t = [np.zeros(int(0.15 * sr))], [], 0.15
    sib_sos = sg.butter(4, [4500, min(9500, 0.45 * sr)], "bandpass", fs=sr, output="sos")
    for i, w in enumerate(words):
        dur = 0.12 + 0.05 * max(1, sum(ch.isalnum() for ch in w))
        n = int(dur * sr)
        tt = np.arange(n) / sr
        contour = f0 * (1.0 + 0.03 * np.sin(2 * np.pi * 2.0 * tt))
        if i == len(words) - 1 and last_glide_st:
            contour = contour * 2 ** (last_glide_st * (tt / dur) / 12)
        y = _voiced(n, sr, contour, rng, breath, whisper)
        env = np.sin(np.pi * np.clip(tt / dur, 0, 1)) ** 0.5
        if end_cut and i == len(words) - 1:
            env = np.minimum(1.0, 4 * tt / dur)
        y = y / (np.max(np.abs(y)) + 1e-9) * env
        if sibilance and any(c in w.lower() for c in "szx") or (sibilance and "sh" in w.lower()):
            m = int(0.07 * sr)
            burst = sg.sosfilt(sib_sos, rng.standard_normal(m)) * np.hanning(m)
            y[:m] += sibilance * 0.9 * burst / (np.max(np.abs(burst)) + 1e-9)
        pieces.append(level * y)
        timed.append({"word": w, "start": round(t, 4), "end": round(t + n / sr, 4)})
        t += n / sr
        if i + 1 < len(words):
            gap = 0.3 if w[-1] in ".!?" else (0.15 if w[-1] in ",;:" else 0.06)
            pieces.append(np.zeros(int(gap * sr)))
            t += int(gap * sr) / sr
    if not end_cut:
        pieces.append(np.zeros(int(0.2 * sr)))
    return np.concatenate(pieces), timed


def write_rich(path: Path, text: str, **kw) -> list[dict]:
    from slate.voice import audio

    x, words = rich_voice(text, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    audio.write_wav(path, x, kw.get("sr", 48000), 24)
    return words


def rich_take(vf, shot: str, text: str | None = None, *, take: str = "t01", sent: str | None = None, **kw):
    """A non-replayed take of the shot made with rich_voice, registered and selected (as a real read would land)."""
    wav = vf.take_dir(shot) / f"{take}.wav"
    words = write_rich(wav, text or vf.line(shot).text, **kw)
    register_take(vf, shot, wav, sent=sent, take=take)
    return wav, words
