"""The renders' pixels: frame counts, frame hashes, PSNR, joins and cuts.

Everything here works on decoded pixels, never on packets or file bytes, because that's what a viewer sees. A
stream-copied cut can carry an edit list that hides a frame from the decoder (the films learned this splicing act
renders), and two files with different bytes can hold the same picture. Frames are compared in the renders' own format,
10-bit 4:2:2, so nothing is rounded before it's measured.

The real backend drives ffmpeg and ffprobe through `slate.procs` (no shell, `BROWSER=none`). Tests swap in a backend
that keeps frames as numpy arrays, so the render logic can be proven without a video toolchain; a second set of tests
runs this backend on tiny synthetic videos.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Protocol

import numpy as np

from slate import procs
from slate.clock import Clock
from slate.errors import MissingPrerequisite, Refused, SlateError

PIX_FMT = "yuv422p10le"
PEAK = 1023  # 10-bit samples
# ProRes 422 HQ, 10-bit 4:2:2, tagged BT.709: the films' act renders, byte for byte the same settings as render_mb.mjs.
PRORES_HQ = ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", PIX_FMT]
BT709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
# Codecs where every frame stands alone, so a stream-copied cut is exact. A splice refuses anything else.
INTRA_CODECS = {"prores"}


class Media(Protocol):
    """What the render logic needs from a video toolchain. `FFmpegMedia` is the real one."""

    name: str

    def count_frames(self, path: Path) -> int: ...
    def read_frames(self, path: Path, indices: Iterable[int]) -> dict[int, np.ndarray]: ...
    def join_encode(self, parts: list[Path], out: Path, *, frames: int | None = None) -> None: ...
    def join_copy(self, parts: list[Path], out: Path) -> None: ...
    def cut_copy(self, src: Path, first: int, count: int | None, out: Path, clock: Clock) -> None: ...
    def is_intra(self, path: Path) -> bool: ...
    def version(self) -> str: ...


def psnr(a: np.ndarray, b: np.ndarray, peak: float = PEAK) -> float:
    """PSNR in dB over every sample of two decoded frames; `inf` when they're identical."""
    if a.shape != b.shape:
        raise SlateError(f"Can't compare frames of different sizes ({a.shape} and {b.shape}).")
    diff = a.astype(np.float64) - b.astype(np.float64)
    mse = float(np.mean(diff * diff))
    return math.inf if mse == 0 else 10.0 * math.log10(peak * peak / mse)


def db(value: float) -> float | str:
    """A PSNR for a report: 'identical' instead of infinity (JSON has no infinity), otherwise two decimals."""
    return "identical" if math.isinf(value) else round(value, 2)


def frame_hash(frame: np.ndarray) -> str:
    """SHA-256 of a decoded frame's samples: equal hashes mean the same picture, whatever the file bytes."""
    return hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()


def _concat_list(parts: list[Path], folder: Path) -> Path:
    """An ffmpeg concat list. Single quotes in paths are escaped the way the concat demuxer wants."""
    lines = []
    for p in parts:
        quoted = str(Path(p).resolve()).replace("'", "'\\''")
        lines.append(f"file '{quoted}'\n")
    fd, name = tempfile.mkstemp(prefix=".slate-concat-", suffix=".txt", dir=str(folder))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    return Path(name)


def _plane_samples(width: int, height: int) -> int:
    # yuv422p10le: a full-size luma plane and two half-width chroma planes, 2 bytes a sample.
    chroma_w = (width + 1) // 2
    return width * height + 2 * chroma_w * height


class FFmpegMedia:
    """The real backend: ffmpeg and ffprobe, found on the augmented PATH (Homebrew included)."""

    name = "ffmpeg"

    def _ffmpeg(self) -> str:
        return procs.require("ffmpeg", hint="Install it with Homebrew (`slate doctor --install` offers to).")

    def _ffprobe(self) -> str:
        return procs.require("ffprobe", hint="It comes with ffmpeg (`slate doctor --install` offers to).")

    def version(self) -> str:
        proc = procs.run([self._ffmpeg(), "-hide_banner", "-version"], timeout=30)
        first = (proc.stdout or "").splitlines()[:1]
        match = re.search(r"ffmpeg version (\S+)", first[0]) if first else None
        return match.group(1) if match else "unknown"

    def probe(self, path: Path) -> dict:
        """The first video stream's codec, size, pixel format and frame rate."""
        path = Path(path)
        if not path.is_file():
            raise Refused(f"There's no video at {path}.")
        proc = procs.run([self._ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,width,height,pix_fmt,r_frame_rate", "-of", "default=nw=1", str(path)],
                         timeout=60, check=True)
        info: dict = {}
        for line in (proc.stdout or "").splitlines():
            key, _, value = line.partition("=")
            if key and key not in info:  # AE's QuickTime files list the stream twice; the first is the one
                info[key] = value.strip()
        if "width" not in info:
            raise Refused(f"{path.name} has no video stream ffprobe can read.")
        info["width"], info["height"] = int(info["width"]), int(info["height"])
        return info

    def count_frames(self, path: Path) -> int:
        """Decoded frames in the first video stream (not packets, not the header's claim)."""
        path = Path(path)
        if not path.is_file():
            return 0
        proc = procs.run([self._ffprobe(), "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)], timeout=None)
        for line in (proc.stdout or "").splitlines():
            line = line.strip().rstrip(",")
            if line.isdigit():
                return int(line)
        return 0

    def read_frames(self, path: Path, indices: Iterable[int]) -> dict[int, np.ndarray]:
        """Decode the frames at `indices` (0-based) in one pass, as flat arrays of 10-bit samples."""
        wanted = sorted({int(i) for i in indices})
        if not wanted:
            return {}
        if wanted[0] < 0:
            raise SlateError(f"Frame numbers start at 0 (asked for {wanted[0]}).")
        info = self.probe(path)
        samples = _plane_samples(info["width"], info["height"])
        select = "+".join(f"eq(n\\,{i})" for i in wanted)
        proc = procs.run([self._ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", f"select='{select}'",
                          "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", PIX_FMT, "-"],
                         timeout=None, text=False, check=True)
        data = np.frombuffer(proc.stdout, dtype="<u2")
        got = data.size // samples if samples else 0
        if got != len(wanted):
            raise Refused(f"{Path(path).name}: asked for {len(wanted)} frames ({wanted[0]}–{wanted[-1]}) and "
                          f"decoded {got}; the file is shorter than it should be.")
        return {i: data[k * samples:(k + 1) * samples] for k, i in enumerate(wanted)}

    def join_encode(self, parts: list[Path], out: Path, *, frames: int | None = None) -> None:
        """Decode the parts in order and encode them once, as ProRes 422 HQ (the lossless-chunk path)."""
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        listing = _concat_list(parts, out.parent)
        try:
            # nice: a join runs beside whatever the person is doing on this Mac.
            procs.run(["nice", "-n", "5", self._ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                       "-i", str(listing), "-map", "0:v:0", "-an", "-fps_mode", "passthrough", *PRORES_HQ, *BT709,
                       str(out)], timeout=None if frames is None else 300 + frames, check=True)
        finally:
            listing.unlink(missing_ok=True)

    def join_copy(self, parts: list[Path], out: Path) -> None:
        """Join intra-only parts by stream copy: exact, nothing re-encoded."""
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        listing = _concat_list(parts, out.parent)
        try:
            procs.run([self._ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                       "-map", "0:v:0", "-an", "-c", "copy", str(out)], timeout=None, check=True)
        finally:
            listing.unlink(missing_ok=True)

    def cut_copy(self, src: Path, first: int, count: int | None, out: Path, clock: Clock) -> None:
        """Copy frames [first, first + count) of an intra-only file by stream copy (count None: to the end).

        The seek lands mid-frame, so it can't round onto a neighbour, and `make_zero` stops the cut starting half a
        frame before zero, where an edit list would hide its first frame (both from the films' splice tool).
        """
        args = [self._ffmpeg(), "-y", "-v", "error"]
        if first > 0:
            args += ["-ss", f"{clock.secs_of(first) + 0.5 * clock.fps_den / clock.fps_num:.6f}"]
        args += ["-i", str(src), "-map", "0:v:0", "-an", "-c", "copy"]
        if first > 0:
            args += ["-avoid_negative_ts", "make_zero"]
        if count is not None:
            args += ["-frames:v", str(count)]
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        procs.run([*args, str(out)], timeout=None, check=True)

    def is_intra(self, path: Path) -> bool:
        return self.probe(path).get("codec_name") in INTRA_CODECS


_state: dict[str, Media | None] = {"backend": None}


def backend() -> Media:
    """The media backend in use (ffmpeg unless a test swapped it)."""
    current = _state["backend"]
    if current is None:
        current = FFmpegMedia()
        _state["backend"] = current
    return current


def use(media: Media | None) -> None:
    """Swap the backend (tests). `None` goes back to ffmpeg."""
    _state["backend"] = media


def require_toolchain() -> None:
    """Fail early, before a long render, when ffmpeg isn't there to join and prove it."""
    media = backend()
    if isinstance(media, FFmpegMedia) and not (procs.which("ffmpeg") and procs.which("ffprobe")):
        raise MissingPrerequisite("ffmpeg and ffprobe are needed to join and prove renders, and they aren't installed.",
                                  hint="slate doctor --install")
