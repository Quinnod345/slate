"""Helpers for the QA gate tests: small synthetic films, built fresh in a temp folder for each test.

Pictures are tiny numpy frames encoded losslessly (FFV1) with ffmpeg; audio is numpy written as exact WAVs. Sizes
and frame rates live only in each film's film.json. The brand pack is a neutral fixture in the test's own brand
store. Nothing here reads or writes the real Slate folders, and nothing here spends.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from slate import procs
from slate.qa import wav

FIXTURE_BRAND = "qa-fixture-brand"
HAVE_FFMPEG = shutil.which("ffmpeg", path=procs.child_env()["PATH"]) is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg isn't installed")


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    return path


def moving_square(n: int, size: tuple[int, int], *, speed: float = 1.0, start: int = 4, side: int = 12,
                  bg: int = 30, fg: int = 220, vanish: tuple[int, ...] = ()) -> np.ndarray:
    """n RGB frames of a bright square gliding right `speed` px a frame; frames in `vanish` lose the square."""
    w, h = size
    out = np.full((n, h, w, 3), bg, dtype=np.uint8)
    y0 = (h - side) // 2
    for i in range(n):
        if i in vanish:
            continue
        x0 = int(round(start + speed * i)) % max(1, w - side)
        out[i, y0:y0 + side, x0:x0 + side] = fg
    return out


class FilmBuilder:
    """Writes a film folder the way the architecture lays it out (docs/ARCHITECTURE.md §5)."""

    def __init__(self, root: Path, *, film_id: str = "qa-film", fps: tuple[int, int] = (30, 1),
                 size: tuple[int, int] = (96, 54), mode: str = "supervised", brand: str = FIXTURE_BRAND,
                 shots: list[tuple[int, int]] | None = None, targets: dict | None = None):
        self.path = root / film_id
        self.path.mkdir(parents=True)
        self.fps = fps
        self.size = size
        self.film = {"id": film_id, "title": "QA fixture", "format": "fixture", "language": "en",
                     "fps_num": fps[0], "fps_den": fps[1], "width": size[0], "height": size[1], "brand": brand,
                     "mode": mode, "targets": targets or {"lufs": -14.0, "true_peak": -1.0}}
        write_json(self.path / "film.json", self.film)
        self.shots_spec = shots if shots is not None else [(1, 20), (1, 20), (2, 30)]
        self.write_shots()

    # --- timing
    def secs(self, frame: int) -> float:
        return frame * self.fps[1] / self.fps[0]

    def write_shots(self, **extra) -> Path:
        shots, acts, f = [], {}, 0
        for i, (act, n) in enumerate(self.shots_spec, start=1):
            sid = f"S{i:02d}"
            shots.append({"id": sid, "act": act, "frame_in": f, "frame_out": f + n, "frames": n,
                          "sec_in": round(self.secs(f), 6), "sec_out": round(self.secs(f + n), 6),
                          "on_screen": "", "vo": ""})
            acts.setdefault(act, []).append(sid)
            f += n
        doc = {"fps_num": self.fps[0], "fps_den": self.fps[1], "width": self.size[0], "height": self.size[1],
               "total_frames": f, "acts": [{"index": a, "name": f"Act {a}", "shots": ids} for a, ids in acts.items()],
               "shots": shots, **extra}
        self.shots_doc = doc
        return write_json(self.path / "timing" / "shots.json", doc)

    @property
    def total(self) -> int:
        return self.shots_doc["total_frames"]

    def act_range(self, act: int) -> tuple[int, int]:
        members = [s for s in self.shots_doc["shots"] if s["act"] == act]
        return members[0]["frame_in"], members[-1]["frame_out"]

    def shot(self, sid: str) -> dict:
        return next(s for s in self.shots_doc["shots"] if s["id"] == sid)

    def events(self, act: int, events=None, *, silent: str | None = None, raw=None) -> Path:
        p = self.path / "timing" / "visual_events" / f"act{act}.json"
        if raw is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(raw, encoding="utf-8")
            return p
        return write_json(p, {"silent": True, "reason": silent} if silent is not None else events)

    def beat(self, shot: str, bid: str, frame: int, kind: str = "land", **extra) -> dict:
        return {"shot": shot, "id": bid, "t": round(self.secs(frame), 6), "kind": kind, "size": "s", "note": "", **extra}

    def all_events(self) -> None:
        """One beat per shot, at its first frame + 2, for every act."""
        by_act: dict[int, list] = {}
        for s in self.shots_doc["shots"]:
            by_act.setdefault(s["act"], []).append(self.beat(s["id"], f"{s['id'].lower()}_in", s["frame_in"] + 2))
        for act, evs in by_act.items():
            self.events(act, evs)

    # --- pictures
    def encode(self, rel: str, frames: np.ndarray, codec: str = "ffv1") -> Path:
        out = self.path / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        n, h, w, _ = frames.shape
        argv = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                "-r", f"{self.fps[0]}/{self.fps[1]}", "-i", "-"]
        if codec == "ffv1":
            argv += ["-c:v", "ffv1", "-pix_fmt", "gbrp"]
        elif codec == "prores":
            argv += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        else:
            argv += ["-c:v", codec]
        proc = procs.run([*argv, str(out)], input=frames.tobytes(), text=False)
        assert proc.returncode == 0, proc.stderr
        return out

    def render(self, act: int, frames: np.ndarray, *, codec: str = "ffv1", name: str | None = None,
               frames_range: tuple[int, int] | None = None, register: bool = True) -> Path:
        fin, fout = frames_range or self.act_range(act)
        rel = f"renders/{name or f'act{act}'}.mkv"
        p = self.encode(rel, frames, codec)
        if register:
            reg = self.path / "renders" / "registry.json"
            doc = json.loads(reg.read_text()) if reg.exists() else {"entries": []}
            doc["entries"].append({"act": act, "engine": "fixture", "file": rel, "frames": [fin, fout], "samples": 1,
                                   "made_at": "2026-09-30T00:00:00Z", "bundle_hash": None})
            write_json(reg, doc)
        return p

    def render_all(self, *, speed: float = 1.0, vanish: tuple[int, ...] = ()) -> np.ndarray:
        """Render every act from one continuous film-long picture (so the joins are seamless). Returns it."""
        film = moving_square(self.total, self.size, speed=speed, vanish=vanish)
        for a in sorted({s["act"] for s in self.shots_doc["shots"]}):
            fin, fout = self.act_range(a)
            self.render(a, film[fin:fout])
        return film

    def master(self, frames: np.ndarray, *, codec: str = "ffv1", name: str = "master.mkv") -> Path:
        return self.encode(f"masters/{name}", frames, codec)

    # --- sound
    def wav(self, rel: str, samples: np.ndarray, sr: int = 48000, bits: int = 24, float_format: bool = False) -> Path:
        return wav.write(self.path / rel, samples, sr, bits, float_format=float_format)

    MIX = "sound/mix/final_mix.wav"  # the sound module's layout: the mix, and its delivery stems beside it

    def stem(self, name: str) -> str:
        return f"sound/mix/stems/{name}.wav"

    def mix(self, stems: dict[str, np.ndarray], *, sr: int = 48000, bits: int = 24, mix_bits: int | None = None) -> Path:
        """Write stems and their exact sum as the final mix (sound/mix/final_mix.wav + sound/mix/stems/*.wav)."""
        total = None
        for name, x in stems.items():
            self.wav(self.stem(name), x, sr, bits)
            q, _ = wav.read(self.path / self.stem(name))  # sum what was written, quantised
            total = q if total is None else total + q
        return self.wav(self.MIX, total, sr, mix_bits or bits)

    # --- words
    def script(self, data: dict) -> Path:
        return write_json(self.path / "script" / "script.json", data)

    def storyboard(self, data: dict) -> Path:
        return write_json(self.path / "script" / "storyboard.json", data)

    def captions(self, cues: list[dict]) -> Path:
        return write_json(self.path / "voice" / "captions" / "captions.json", cues)


def tone(seconds: float, sr: int = 48000, freq: float = 997.0, amp: float = 0.2, channels: int = 2,
         start: float = 0.0, total: float | None = None) -> np.ndarray:
    """A sine burst: `amp` peak, from `start` for `seconds`, inside a buffer `total` seconds long."""
    n_total = int(round((total if total is not None else start + seconds) * sr))
    x = np.zeros((n_total, channels))
    a, b = int(round(start * sr)), int(round((start + seconds) * sr))
    t = np.arange(b - a) / sr
    x[a:b, :] = (amp * np.sin(2 * np.pi * freq * t))[:, None]
    return x


def write_brand(store: Path, *, banned=None, approved=None, never=None, name: str = FIXTURE_BRAND,
                omit_banned: bool = False) -> Path:
    truth = {"sources": [], "claim_authority": "fixture", "approved_claims": approved or [], "never_claim": never or [],
             "sample_data": {}}
    if not omit_banned:
        truth["banned_words"] = banned if banned is not None else []
    return write_json(store / name / "brand.json", {"name": name, "palette": {}, "truth": truth,
                                                    "references": {"truth": None, "look_motion": None, "voice": None,
                                                                   "sound": None}})


class ScriptedOCR:
    """A stand-in OCR backend for tests: it "reads" the text whose id is painted into a frame's top-left pixel.

    Frames are encoded losslessly as planar RGB, so the id survives exactly. It names itself in `engine`, as every
    backend must, so no result could ever pass it off as Apple Vision.
    """

    def __init__(self, texts: dict[int, list[str]]):
        self.texts = texts
        self.engine = {"name": "scripted OCR (tests)", "version": "0", "recognition_level": "exact"}
        self.calls = 0

    def recognize(self, image) -> list[dict]:
        self.calls += 1
        code = int(np.asarray(image)[0, 0, 0])
        return [{"text": t, "conf": 1.0, "box": [0.1, 0.1, 0.5, 0.1]} for t in self.texts.get(code, [])]


def paint_codes(frames: np.ndarray, spans: list[tuple[int, int, int]]) -> np.ndarray:
    """Paint text ids into the top-left pixel: spans of (first frame, end frame exclusive, id). Id 0 = no text."""
    out = frames.copy()
    out[:, 0, 0, 0] = 0
    for a, b, code in spans:
        out[a:b, 0, 0, 0] = code
    return out
