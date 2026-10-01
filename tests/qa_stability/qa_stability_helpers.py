"""Helpers for the stability tests: synthetic frames in numpy, and a small film whose act renders are lossless 16-bit
grey files made with ffmpeg (so the 10-bit motion path is exercised exactly).

Nothing here comes from a real film. Sizes and the frame rate live only in each test film's film.json, and the rate
is 24 fps on purpose, so nothing in the scan can lean on a rate it assumes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage as ndi

from slate import procs

FPS = (24, 1)
HAVE_FFMPEG = shutil.which("ffmpeg", path=procs.child_env()["PATH"]) is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg isn't installed")


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    return path


def textured(size: tuple[int, int], seed: int = 3, spread: float = 24.0) -> np.ndarray:
    """A still with type-like texture everywhere (blurred noise), luma 0-255, float32."""
    w, h = size
    rng = np.random.default_rng(seed)
    base = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 2.0)
    return np.clip(128 + spread * base / base.std(), 0, 255).astype(np.float32)


def drift(still: np.ndarray, n: int, vx: float = 0.0, vy: float = 0.0) -> np.ndarray:
    """n frames of the still gliding (vx, vy) px a frame, sub-pixel (cubic interpolation)."""
    return np.stack([ndi.shift(still, (vy * i, vx * i), order=3, mode="nearest") for i in range(n)]).astype(np.float32)


def card_frames(still: np.ndarray, n: int, box: tuple[int, int, int, int], offsets: dict[int, tuple[int, int]]) -> np.ndarray:
    """n frames of a still where a card (box x, y, w, h) sits at whole-pixel offsets {frame: (dx, dy)} (held after)."""
    x, y, w, h = box
    out, cur = [], (0, 0)
    for i in range(n):
        cur = offsets.get(i, cur)
        f = still.copy()
        dx, dy = cur
        f[y + dy:y + dy + h, x + dx:x + dx + w] = still[y:y + h, x:x + w]
        out.append(f)
    return np.stack(out).astype(np.float32)


def card(size: tuple[int, int], rect: tuple[float, float, float, float], *, face: float = 220.0, ground: float = 40.0,
         texture: np.ndarray | None = None, content_at: tuple[float, float] | None = None, rounded: bool = False) -> np.ndarray:
    """One frame of a light card on a dark ground. rect = (left, top, right, bottom) in pixels, sub-pixel. Drawn by
    exact area coverage (anti-aliased), or with every edge rounded to a whole pixel (the old way). Texture, when
    given, is drawn inside the card with its top-left at content_at (sub-pixel)."""
    w, h = size
    left, top, right, bottom = rect
    if rounded:
        left, top, right, bottom = (float(round(v)) for v in rect)
    xs, ys = np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64)
    cx = np.clip(np.minimum(right, xs + 1) - np.maximum(left, xs), 0, 1)
    cy = np.clip(np.minimum(bottom, ys + 1) - np.maximum(top, ys), 0, 1)
    cov = np.outer(cy, cx)
    img = ground + (face - ground) * cov
    if texture is not None and content_at is not None:
        ox, oy = content_at
        if rounded:
            ox, oy = round(ox), round(oy)
        th, tw = texture.shape
        layer = np.zeros((h, w))
        layer[:th, :tw] = texture
        layer = ndi.shift(layer, (oy, ox), order=3, mode="constant")
        img = img - layer * cov
    return img.astype(np.float32)


class StabilityFilm:
    """A film folder (docs/ARCHITECTURE.md §5) with acts of `frames` frames each and renders encoded losslessly."""

    def __init__(self, root: Path, *, film_id: str = "stab-film", size: tuple[int, int] = (320, 180), acts: int = 2,
                 frames: int = 36, mode: str = "supervised"):
        self.path = root / film_id
        self.path.mkdir(parents=True)
        self.size, self.n = size, frames
        self.film = {"id": film_id, "title": "Stability fixture", "format": "fixture", "language": "en",
                     "fps_num": FPS[0], "fps_den": FPS[1], "width": size[0], "height": size[1], "brand": "fixture",
                     "mode": mode, "targets": {"lufs": -14.0, "true_peak": -1.0}}
        write_json(self.path / "film.json", self.film)
        shots = []
        for a in range(1, acts + 1):
            fin = (a - 1) * frames
            shots.append({"id": f"S{a:02d}", "act": a, "frame_in": fin, "frame_out": fin + frames, "frames": frames,
                          "sec_in": fin * FPS[1] / FPS[0], "sec_out": (fin + frames) * FPS[1] / FPS[0]})
        self.shots = {"fps_num": FPS[0], "fps_den": FPS[1], "width": size[0], "height": size[1],
                      "total_frames": acts * frames,
                      "acts": [{"index": a, "name": f"Act {a}", "shots": [f"S{a:02d}"]} for a in range(1, acts + 1)],
                      "shots": shots}
        write_json(self.path / "timing" / "shots.json", self.shots)
        for a in range(1, acts + 1):
            self.events(a, silent="no planned beats in this fixture act")

    def act_in(self, act: int) -> int:
        return (act - 1) * self.n

    def events(self, act: int, beats: list[int] | None = None, *, silent: str | None = None) -> Path:
        """Planned beats at film frames, or a silent act."""
        p = self.path / "timing" / "visual_events" / f"act{act}.json"
        if silent is not None:
            return write_json(p, {"silent": True, "reason": silent})
        return write_json(p, [{"shot": f"S{act:02d}", "id": f"b{i}", "t": round(f * FPS[1] / FPS[0], 6), "kind": "land",
                               "size": "s", "note": ""} for i, f in enumerate(beats or [])])

    def encode(self, rel: str, frames: np.ndarray) -> Path:
        out = self.path / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        n, h, w = frames.shape
        raw = np.clip(np.round(frames.astype(np.float64) * 257.0), 0, 65535).astype("<u2")
        argv = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray16le", "-s", f"{w}x{h}",
                "-r", f"{FPS[0]}/{FPS[1]}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray16le", str(out)]
        proc = procs.run(argv, input=raw.tobytes(), text=False)
        assert proc.returncode == 0, proc.stderr
        return out

    def render(self, act: int, frames: np.ndarray, *, name: str | None = None) -> Path:
        rel = f"renders/{name or f'act{act}'}.mkv"
        p = self.encode(rel, frames)
        reg = self.path / "renders" / "registry.json"
        doc = json.loads(reg.read_text()) if reg.exists() else {"entries": []}
        fin = self.act_in(act)
        doc["entries"].append({"act": act, "engine": "fixture", "file": rel, "frames": [fin, fin + self.n],
                               "samples": 1, "made_at": "2026-10-01T00:00:00Z", "bundle_hash": None})
        write_json(reg, doc)
        return p
