"""Picture measurements: PSNR between two frames, and frame-for-frame between two videos.

PSNR is how the films proved a master matched the renders it was built from (every frame ≥ 40 dB; the worst on film
A was 51.6 dB) and how a splice is proven. It caught AE converting the colour and a one-frame lag that eyes missed.

    psnr = 10 · log10(peak² / MSE)        identical frames → infinity

`peak` follows the data: 255 for 8-bit, 65535 for 16-bit, 1.0 for floats, unless the caller says otherwise (10-bit
data held in 16-bit words should pass peak=1023).
"""

from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from slate.qa import media

IDENTICAL_DB = 99.0  # how an infinite PSNR is written into JSON evidence (identical frames)


def _as_array(x) -> np.ndarray:
    if isinstance(x, (str, Path)):
        from PIL import Image  # noqa: PLC0415

        with Image.open(x) as im:
            return np.asarray(im.convert("RGB") if im.mode not in ("L", "RGB", "I;16") else im)
    return np.asarray(x)


def default_peak(arr: np.ndarray) -> float:
    if np.issubdtype(arr.dtype, np.integer):
        return float(np.iinfo(arr.dtype).max)
    return 1.0


def mse(a, b) -> float:
    """Mean squared error between two same-shaped arrays (computed in float64)."""
    x, y = _as_array(a), _as_array(b)
    if x.shape != y.shape:
        raise ValueError(f"PSNR needs frames of the same shape: {x.shape} vs {y.shape}")
    d = x.astype(np.float64) - y.astype(np.float64)
    return float(np.mean(d * d))


def psnr(a, b, peak: float | None = None) -> float:
    """PSNR in dB between two frames (arrays, or paths to images). Identical frames return math.inf."""
    x = _as_array(a)
    err = mse(x, b)
    if err == 0.0:
        return math.inf
    p = float(peak) if peak is not None else default_peak(x)
    return 10.0 * math.log10(p * p / err)


def finite(db: float) -> float:
    """A PSNR value that JSON can carry: infinity becomes IDENTICAL_DB."""
    return IDENTICAL_DB if math.isinf(db) else round(float(db), 3)


_STAT = re.compile(r"n:(\d+).*?psnr_avg:(inf|-?[\d.]+)")


def parse_psnr_stats(text: str) -> list[float]:
    """Per-frame psnr_avg values from ffmpeg's psnr stats (inf for identical frames), in frame order."""
    vals: list[tuple[int, float]] = []
    for line in text.splitlines():
        m = _STAT.search(line)
        if m:
            vals.append((int(m.group(1)), math.inf if m.group(2) == "inf" else float(m.group(2))))
    return [v for _, v in sorted(vals)]


# Frame i of each side at i × 40 ms on a 1 ms clock: ffmpeg pairs two inputs by time, so renumbering both by their
# frame count pairs them frame for frame, whatever timestamps their containers kept.
INDEX_CLOCK = "settb=1/1000,setpts=N*40"


def video_psnr(a: str | Path, b: str | Path | Sequence[str | Path], *, pix_fmt: str = "yuv422p10le",
               timeout: float = 7200, a_frames: tuple[int, int] | None = None,
               b_frames: tuple[int, int] | None = None) -> list[float]:
    """PSNR of every frame of `a` against `b`, frame for frame, measured by ffmpeg's psnr filter.

    `b` may be a list of files, joined in order (the act renders a master was built from). Both sides are converted
    to `pix_fmt` first (10-bit 4:2:2 by default, the masters' own format), so the comparison is fair whatever each
    was encoded as. The comparison stops at the shorter input (eof_action=endall: by default ffmpeg would repeat
    the shorter side's last frame, and a missing tail would read as low PSNR instead of missing frames), so compare
    the counts yourself.

    Frames pair by their place in each file, not by their timestamps: both sides are renumbered on one clock
    (`INDEX_CLOCK`) before ffmpeg pairs them. Paired by time, two containers that round the same frame times
    differently (Matroska to the millisecond, QuickTime to 1/30000 s) matched a third of the frames with their
    neighbours, and pairing by the nearest time lost the last frame.

    `a_frames` and `b_frames` (first and last frame, inclusive, counted from 0) compare a stretch of either side, so a
    shot is held against the same frames of a whole delivered act without cutting a file first. Each range is trimmed
    by frame count inside the filter graph, and both sides restart their clocks at 0, so the frames pair one for one.
    """
    media.require("ffmpeg")
    parts = [b] if isinstance(b, (str, Path)) else list(b)
    with tempfile.TemporaryDirectory(prefix="slate-psnr-") as tmp:
        if len(parts) == 1:
            second = ["-i", str(parts[0])]
        else:
            lst = Path(tmp) / "parts.txt"
            lst.write_text("".join(f"file '{_concat_escape(Path(p).resolve())}'\n" for p in parts), encoding="utf-8")
            second = ["-f", "concat", "-safe", "0", "-i", str(lst)]
        stats = Path(tmp) / "psnr.log"
        def side(rng: tuple[int, int] | None) -> str:
            # trim's end_frame is the first frame it drops; the clock then numbers what's left from 0
            trim = f"trim=start_frame={int(rng[0])}:end_frame={int(rng[1]) + 1}," if rng is not None else ""
            return f"{trim}{INDEX_CLOCK},"

        graph = (f"[0:v]{side(a_frames)}format={pix_fmt}[a];[1:v]{side(b_frames)}format={pix_fmt}[b];"
                 f"[a][b]psnr=stats_file='{_filter_escape(stats)}':eof_action=endall")
        media._run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(a), *second, "-lavfi", graph, "-an", "-f", "null",
                    "-"], timeout=timeout)
        return parse_psnr_stats(stats.read_text(encoding="utf-8") if stats.exists() else "")


def _concat_escape(p: Path) -> str:
    return str(p).replace("'", "'\\''")


def _filter_escape(p: Path) -> str:
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def summarize(values: Iterable[float], *, threshold: float = 40.0, first_frame: int = 0, worst: int = 10) -> dict:
    """Count, mean, minimum, the worst frames and every frame under the threshold, JSON-ready."""
    vals = list(values)
    if not vals:
        return {"frames": 0, "mean_db": None, "min_db": None, "worst": [], "below": [], "threshold_db": threshold}
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    finite_vals = [v for v in vals if not math.isinf(v)]
    mean = (sum(finite_vals) / len(finite_vals)) if finite_vals else math.inf
    below = [i for i, v in enumerate(vals) if v < threshold]
    return {
        "frames": len(vals),
        "mean_db": finite(mean),
        "min_db": finite(vals[order[0]]),
        "worst": [{"frame": first_frame + i, "db": finite(vals[i])} for i in order[:worst]],
        "below": [{"frame": first_frame + i, "db": finite(vals[i])} for i in below[:200]],
        "below_count": len(below),
        "threshold_db": threshold,
    }
