"""Synthetic test clips for the analyzer and the benchmark: flat backgrounds, hard cuts, and squares that move on
known eases, drawn with exact sub-pixel coverage and encoded losslessly, so a fitted curve can be checked against the
curve that drew it. Sizes and rates here are deliberately small and unlike any real film's."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import procs
from slate.motion.eases import evaluate, parse

WIDTH, HEIGHT, FPS = 320, 180, 25
SQUARE = 24

# The reference clip: three shots, two hard cuts, two moves on known curves.
MOVE_1 = {"ease": [0.05, 0.7, 0.1, 1.0], "name": "m3-emphasized-decelerate", "frames": (8, 33), "x": (40.0, 240.0),
          "y": 78.0}
CUT_1, CUT_2 = 50, 80
MOVE_2 = {"ease": [0.42, 0.0, 0.58, 1.0], "name": "ease-in-out", "frames": (84, 104), "y": (20.0, 140.0), "x": 148.0}
TOTAL = 110
BG_A, FG_A = (40, 40, 40), (255, 255, 255)
BG_B, BAR_B = (20, 60, 140), (240, 150, 40)
BG_C, FG_C = (230, 230, 225), (30, 30, 30)


def coverage(start: float, size: float, n: int) -> np.ndarray:
    """How much of each pixel 0..n-1 the interval [start, start + size) covers (0 to 1)."""
    edges = np.arange(n + 1, dtype=float)
    lo = np.clip(edges[:-1], start, start + size)
    hi = np.clip(edges[1:], start, start + size)
    return np.clip(hi - lo, 0.0, 1.0)


def square(bg, fg, x: float, y: float, size: float = SQUARE, w: int = WIDTH, h: int = HEIGHT) -> np.ndarray:
    alpha = np.outer(coverage(y, size, h), coverage(x, size, w))[..., None]
    frame = np.asarray(bg, float) * (1 - alpha) + np.asarray(fg, float) * alpha
    return np.clip(np.round(frame), 0, 255).astype(np.uint8)


def eased(move: dict, frame: int, key: str) -> float:
    a, b = move["frames"]
    lo, hi = move[key]
    u = (frame - a) / float(b - a)
    return lo + (hi - lo) * evaluate(parse(move["ease"]), u)


def reference_frames() -> list[np.ndarray]:
    frames = []
    for i in range(TOTAL):
        if i < CUT_1:
            frames.append(square(BG_A, FG_A, eased(MOVE_1, i, "x"), MOVE_1["y"]))
        elif i < CUT_2:
            f = np.zeros((HEIGHT, WIDTH, 3), np.uint8) + np.array(BG_B, np.uint8)
            f[70:110, 60:260] = BAR_B
            frames.append(f)
        else:
            frames.append(square(BG_C, FG_C, MOVE_2["x"], eased(MOVE_2, i, "y")))
    return frames


def write_clip(frames: list[np.ndarray], path: Path, *, fps: int = FPS, tone_hz: float | None = None) -> Path:
    """Encode frames losslessly (FFV1 in Matroska), optionally with a sine tone as the soundtrack."""
    h, w = frames[0].shape[:2]
    argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
            "-r", str(fps), "-i", "-"]
    if tone_hz:
        argv += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={len(frames) / fps}",
                 "-c:a", "pcm_s16le", "-shortest"]
    argv += ["-c:v", "ffv1", str(path)]
    procs.run(argv, input=np.stack(frames).astype(np.uint8).tobytes(), text=False, check=True, timeout=120)
    return path


def reference_clip(folder: Path, *, tone_hz: float | None = None) -> Path:
    return write_clip(reference_frames(), Path(folder) / ("reference_tone.mkv" if tone_hz else "reference.mkv"),
                      tone_hz=tone_hz)


def pattern_master(folder: Path, *, seconds: float = 2.0, fps: int = 24, size: tuple[int, int] = (160, 90)) -> Path:
    """A moving test pattern (ffmpeg's testsrc2) as a stand-in master, encoded losslessly."""
    out = Path(folder) / "master.mkv"
    procs.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
               "-i", f"testsrc2=size={size[0]}x{size[1]}:rate={fps}:duration={seconds}", "-c:v", "ffv1", str(out)],
              check=True, timeout=120)
    return out


def excerpt(master: Path, out: Path, first: int, last: int, *, vf: str | None = None) -> Path:
    """Frames first..last-1 of a master, optionally filtered (noise, blur, a reframe)."""
    chain = f"trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS" + (f",{vf}" if vf else "")
    procs.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(master), "-vf", chain, "-c:v", "ffv1", str(out)],
              check=True, timeout=120)
    return out
