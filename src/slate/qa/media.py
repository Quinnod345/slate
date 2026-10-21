"""ffmpeg and ffprobe, the way the gates need them: exact frame counts, exact frames, and the build that measured.

Every child process goes through `slate.procs.run` (no shell, BROWSER=none, stdin closed). Frames are decoded in
bounded chunks rather than one long pipe, so a 13,000-frame act never sits in memory and nothing is left running if a
gate stops early.

Seeking is exact: a chunk that starts at frame n seeks to (n − 0.5) frames, so ffmpeg's accurate seek discards
everything before frame n and keeps frame n itself, whatever the rounding of n × den / num.
"""

from __future__ import annotations

import json
import re
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from slate import gates, procs
from slate.errors import MissingPrerequisite, SlateError

CHUNK_BYTES = 192 << 20  # decode at most this much raw video per ffmpeg call
#: Runs per select expression. ffmpeg's expression parser nests every "+", so a select of a few hundred eq() terms
#: fails to allocate ("Cannot allocate memory"); runs of consecutive frames are one between() each, and a span
#: with more runs than this is read in more than one call.
MAX_SELECT_RUNS = 48
PIX = {"gray": (1, np.uint8), "rgb24": (3, np.uint8), "gray16le": (1, np.dtype("<u2"))}


class MediaError(SlateError):
    """ffmpeg or ffprobe couldn't read a file (the message carries the tail of its stderr)."""


def have(tool: str = "ffmpeg") -> bool:
    return procs.which(tool) is not None


@lru_cache(maxsize=None)
def version(tool: str = "ffmpeg") -> str | None:
    """The tool's version string (e.g. "9.0.2"), or None when it isn't installed."""
    if not have(tool):
        return None
    try:
        out = procs.run([tool, "-hide_banner", "-version"], timeout=30).stdout or ""
    except SlateError:
        return None
    m = re.search(rf"{tool} version (\S+)", out)
    return m.group(1) if m else "unknown"


def engine(what: str, tool: str = "ffmpeg", **extra) -> dict:
    """The `engine` field for a verdict ffmpeg made: e.g. {"name": "ffmpeg ebur128", "version": "9.0.2"}."""
    return gates.engine(f"{tool} {what}".strip(), version(tool) or "not installed", **extra)


def require(tool: str = "ffmpeg") -> str:
    return procs.require(tool, hint="Install it with Homebrew (`brew install ffmpeg`), or run `slate doctor --install`.")


def _run(argv: list[str], *, text: bool = True, timeout: float = 900):
    try:
        proc = procs.run(argv, text=text, timeout=timeout)
    except MissingPrerequisite:
        raise
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode("utf-8", "replace")
        raise MediaError(f"{Path(argv[0]).name} couldn't read the file: {err.strip()[-600:]}")
    return proc


def probe(path: str | Path) -> dict:
    """ffprobe's streams and format for a file, as a dict."""
    require("ffprobe")
    out = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout
    return json.loads(out or "{}")


def _rate(text: str | None) -> Fraction | None:
    if not text or text in ("0/0", "0"):
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def video_info(path: str | Path, *, count: bool = True) -> dict:
    """The first video stream: size, rational frame rate, codec, profile, pixel format and (with count) the exact
    number of frames, from counted packets rather than the container's claim (AE's QuickTime files list the stream
    twice; the first number is the real one)."""
    require("ffprobe")
    argv = ["ffprobe", "-v", "error", "-select_streams", "v:0"]
    if count:
        argv += ["-count_packets"]
    argv += ["-show_entries", "stream=codec_name,profile,width,height,r_frame_rate,avg_frame_rate,pix_fmt,nb_frames"
             + (",nb_read_packets" if count else ""), "-of", "json", str(path)]
    data = json.loads(_run(argv).stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise MediaError(f"{Path(path).name} has no video stream.")
    s = streams[0]
    rate = _rate(s.get("r_frame_rate")) or _rate(s.get("avg_frame_rate"))
    frames = None
    for key in ("nb_read_packets", "nb_frames"):
        if str(s.get(key, "")).isdigit():
            frames = int(s[key])
            break
    return {
        "codec": s.get("codec_name"), "profile": s.get("profile"), "pix_fmt": s.get("pix_fmt"),
        "width": int(s.get("width") or 0), "height": int(s.get("height") or 0),
        "fps_num": rate.numerator if rate else None, "fps_den": rate.denominator if rate else None,
        "frames": frames,
    }


def audio_streams(path: str | Path) -> list[dict]:
    return [s for s in probe(path).get("streams", []) if s.get("codec_type") == "audio"]


def _seek_seconds(frame: int, fps: Fraction) -> float:
    return max(0.0, (frame - 0.5) / float(fps))


def _frame_shape(size: tuple[int, int], pix_fmt: str) -> tuple[int, int, int, np.dtype]:
    if pix_fmt not in PIX:
        raise ValueError(f"unsupported pix_fmt {pix_fmt!r}")
    ch, dtype = PIX[pix_fmt]
    w, h = size
    return w, h, ch, np.dtype(dtype)


def _vf(size: tuple[int, int] | None, extra: str | None = None) -> list[str]:
    parts = []
    if extra:
        parts.append(extra)
    if size:
        parts.append(f"scale={size[0]}:{size[1]}")
    return ["-vf", ",".join(parts)] if parts else []


def iter_frames(path: str | Path, *, start: int = 0, count: int | None = None, size: tuple[int, int] | None = None,
                pix_fmt: str = "gray", info: dict | None = None) -> Iterator[np.ndarray]:
    """Frames start … start+count−1 as numpy arrays (h, w) or (h, w, 3), decoded in bounded chunks.

    `size` scales each frame (width, height); without it the file's own size is used. Stops early (without error) at
    the end of the file; callers that need an exact count check how many they got.
    """
    require("ffmpeg")
    info = info or video_info(path, count=count is None)
    fps = Fraction(info["fps_num"], info["fps_den"]) if info.get("fps_num") else None
    if fps is None:
        raise MediaError(f"{Path(path).name} doesn't declare a frame rate.")
    total = info.get("frames")
    if count is None:
        count = max(0, (total or 0) - start)
    w, h, ch, dtype = _frame_shape(size or (info["width"], info["height"]), pix_fmt)
    frame_bytes = w * h * ch * dtype.itemsize
    per_chunk = max(1, CHUNK_BYTES // frame_bytes)
    done = 0
    while done < count:
        n = min(per_chunk, count - done)
        first = start + done
        argv = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{_seek_seconds(first, fps):.6f}", "-i", str(path),
                "-map", "0:v:0", "-an", "-sn", "-frames:v", str(n), *_vf(size), "-pix_fmt", pix_fmt,
                "-f", "rawvideo", "-"]
        raw = _run(argv, text=False).stdout or b""
        got = len(raw) // frame_bytes
        arr = np.frombuffer(raw[:got * frame_bytes], dtype=dtype)
        shape = (got, h, w) if ch == 1 else (got, h, w, ch)
        for frame in arr.reshape(shape):
            yield frame
        done += got
        if got < n:
            return


def frame_runs(frames: Iterable[int]) -> list[tuple[int, int]]:
    """Sorted frame indices as (first, last) runs of consecutive frames."""
    runs: list[list[int]] = []
    for i in sorted({int(f) for f in frames}):
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(a, b) for a, b in runs]


def select_expr(frames: Iterable[int], base: int = 0) -> str:
    """An ffmpeg select expression for these frames (numbered from `base`): each run of consecutive frames is one
    between(), so even hundreds of wanted frames stay an expression ffmpeg can parse."""
    return "+".join(f"eq(n\\,{a - base})" if a == b else f"between(n\\,{a - base}\\,{b - base})"
                    for a, b in frame_runs(frames))


def frame_groups(wanted: list[int], *, span: int, frame_bytes: int) -> list[list[int]]:
    """Wanted frames (sorted) grouped for one ffmpeg call each: within `span` frames of the group's first, within
    CHUNK_BYTES of decoded video, and at most MAX_SELECT_RUNS runs."""
    groups: list[list[int]] = []
    for i in wanted:
        g = groups[-1] if groups else None
        if g and i - g[0] < span and len(g) * frame_bytes < CHUNK_BYTES and \
                (i == g[-1] + 1 or len(frame_runs(g)) < MAX_SELECT_RUNS):
            g.append(i)
        else:
            groups.append([i])
    return groups


def read_frames(path: str | Path, indices: Iterable[int], *, size: tuple[int, int] | None = None,
                pix_fmt: str = "rgb24", info: dict | None = None, span: int = 240) -> dict[int, np.ndarray]:
    """Specific frames (by index in the file) as arrays, decoding only the spans that hold them.

    Frames are grouped into spans of at most `span` frames; each span is one ffmpeg call that seeks exactly to its
    first wanted frame and keeps only the wanted ones with a select filter.
    """
    require("ffmpeg")
    wanted = sorted({int(i) for i in indices if int(i) >= 0})
    if not wanted:
        return {}
    info = info or video_info(path, count=False)
    fps = Fraction(info["fps_num"], info["fps_den"])
    w, h, ch, dtype = _frame_shape(size or (info["width"], info["height"]), pix_fmt)
    frame_bytes = w * h * ch * dtype.itemsize
    out: dict[int, np.ndarray] = {}
    for group in frame_groups(wanted, span=span, frame_bytes=frame_bytes):
        base = group[0]
        expr = select_expr(group, base)
        argv = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{_seek_seconds(base, fps):.6f}", "-i", str(path),
                "-map", "0:v:0", "-an", "-sn", "-fps_mode", "passthrough",
                *_vf(size, f"select='{expr}'"), "-frames:v", str(len(group)), "-pix_fmt", pix_fmt,
                "-f", "rawvideo", "-"]
        raw = _run(argv, text=False).stdout or b""
        got = len(raw) // frame_bytes
        arr = np.frombuffer(raw[:got * frame_bytes], dtype=dtype)
        shape = (got, h, w) if ch == 1 else (got, h, w, ch)
        for idx, frame in zip(group, arr.reshape(shape)):
            out[idx] = frame
    return out


def analysis_size(width: int, height: int, long_side: int = 480, tile: int = 30) -> tuple[int, int]:
    """A small analysis size with the film's aspect, both sides whole multiples of `tile` (for 16:9 at 480 that is
    480 × 270, a 16 × 9 grid of 30 px tiles; for 9:16 it is 270 × 480)."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if width >= height:
        w = long_side
        h = max(tile, int(round(long_side * height / width / tile)) * tile)
    else:
        h = long_side
        w = max(tile, int(round(long_side * width / height / tile)) * tile)
    return w, h
