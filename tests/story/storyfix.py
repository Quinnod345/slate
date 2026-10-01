"""Helpers for the story and timing tests: a small, made-up bakery film, its brand pack, truth pack and takes.

Nothing here is company material: the brand ("crumb"), the film and every line are invented for these tests. Test
modules import this by its own name (`storyfix`), never through conftest, which pytest reloads per folder.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

BRAND = {
    "name": "crumb",
    "palette": {"ink": "#222222", "paper": "#fafafa"},
    "type": {},
    "logos": {},
    "motion": {
        "eases": {"settle": [0.2, 0.8, 0.3, 1.0], "swift": {"bezier": [0.6, 0.0, 0.4, 1.0]}},
        "durations": {"quick": 0.3, "base": 0.5, "broad": 0.8},
    },
    "truth": {
        "banned_words": ["cheapest", "guaranteed", "world class"],
        "approved_claims": [{"id": "hours", "text": "Open from 6 am every day"}],
        "never_claim": ["organic"],
        "sample_data": {"loaves_a_day": 120},
    },
    "references": {"truth": None, "look_motion": None, "voice": None, "sound": None},
}

TRUTH = {
    "sources": [{"id": "shop-site", "title": "The shop's own site", "pin": "retrieved 2026-09-30"},
                {"id": "old-flyer", "title": "A flyer nobody pinned"}],
    "claims": [{"id": "since", "text": "Baking since 1987", "source": "shop-site:about"}],
}

SCRIPT = {
    "title": "Morning Loaf",
    "version": "draft-1",
    "defaults": {"enter_token": "base"},
    "acts": [
        {"name": "Act 1 · The oven", "job": "Warmth first",
         "shots": [
             {"id": "S01", "vo": "", "on_screen": "", "picture": "Steam over a dark oven.", "dur": 2.0,
              "events": [{"id": "steam", "t": 0.4, "land": "settle:0.5"}]},
             {"id": "S02", "vo": "Every morning at six, the ovens wake up.", "on_screen": "Opens at 6",
              "picture": "Loaves slide in.", "claims": ["hours"], "hold": 0.5,
              "events": [{"id": "door", "word": "ovens", "land": "swift:0.5"}]}]},
        {"name": "Act 2 · The loaf", "job": "The payoff",
         "shots": [
             {"id": "S03", "vo": "A hundred and twenty loaves, baked since 1987.", "on_screen": "Since 1987",
              "picture": "A wall of loaves.", "claims": [{"text": "Baking since 1987", "source": "shop-site:about"}],
              "hold": 0.8},
             {"id": "S04", "vo": "Come early.", "on_screen": "Come early", "picture": "The door opens.", "hold": 1.5}]},
    ],
}

FILM = {
    "schema": 1, "id": "bakery", "title": "Morning Loaf", "format": "test-reel", "language": "en",
    "fps_num": 25, "fps_den": 1, "width": 720, "height": 1280, "aspect": "9:16", "brand": "crumb",
    "voice": {"source": "library", "provider": "elevenlabs", "voice_id": "voice-demo-1"},
    "targets": {"runtime_s": 15, "lufs": -14, "true_peak": -1}, "mode": "supervised",
}


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_wav(path: Path, seconds: float, *, rate: int = 16000, bursts=(), freq: float = 220.0) -> Path:
    """A 16-bit mono WAV: silence with tone bursts at the given (start, end) seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(seconds * rate))
    frames = bytearray()
    for i in range(n):
        t = i / rate
        amp = 0.3 if any(a <= t < b for a, b in bursts) else 0.0
        frames += struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * t)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def voice_takes(film_dir: Path, lengths: dict[str, float]) -> dict[str, Path]:
    """Flat takes at voice/takes/<shot>.wav with a burst every 0.4 s (so the envelope has onsets)."""
    out = {}
    for sid, secs in lengths.items():
        out[sid] = write_wav(film_dir / "voice" / "takes" / f"{sid}.wav", secs,
                             bursts=[(0.1 + i * 0.4, 0.3 + i * 0.4) for i in range(int(secs / 0.4))])
    return out
