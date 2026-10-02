"""How close a cut-down is to its master, frame for frame: PSNR and SSIM, measured by ffmpeg.

The reference is the master's own frames in the cut's ranges (the same frames both benchmark arms were asked to
make), joined in order. When the cut is the master's size the comparison is direct. When it changes aspect (a 9:16
cut of a 16:9 master) the reference is the master scaled to cover the cut's frame and centre-cropped, and the result
says "reframed": a cut that re-stages its layout scores lower there by design, so for those cuts the person's review
carries the judgement and the numbers are a floor that catches broken picture.

Both sides are compared as 8-bit 4:2:0, the way the social channels deliver them.
"""

from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path

from slate import procs
from slate.errors import UsageError
from slate.qa import measure, media

PIX_FMT = "yuv420p"
PSNR_FLOOR_DB = 35.0  # reported: frames under this are listed (the social route's picture, not the master's 40 dB)
SSIM_FRAME_FLOOR = 0.80
_SSIM = re.compile(r"n:(\d+).*?All:([\d.]+|inf)")


def parse_ssim_stats(text: str) -> list[float]:
    """Per-frame SSIM (the All value) from ffmpeg's ssim stats file, in frame order."""
    vals = []
    for line in text.splitlines():
        m = _SSIM.search(line)
        if m:
            vals.append((int(m.group(1)), 1.0 if m.group(2) == "inf" else float(m.group(2))))
    return [v for _, v in sorted(vals)]


def _graph(ranges: list[list[int]], size: tuple[int, int] | None, metric: str, stats: str) -> str:
    k = len(ranges)
    parts = [f"[1:v]split={k}" + "".join(f"[m{i}]" for i in range(k))] if k > 1 else []
    for i, (a, b) in enumerate(ranges):
        src = f"[m{i}]" if k > 1 else "[1:v]"
        parts.append(f"{src}trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS[r{i}]")
    if k > 1:
        parts.append("".join(f"[r{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=0[ref]")
    ref = "[ref]" if k > 1 else "[r0]"
    if size:
        w, h = size
        parts.append(f"{ref}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[fit]")
        ref = "[fit]"
    # both sides renumbered frame by frame: containers round frame times differently, and pairing by time matched
    # neighbours (slate.qa.measure.INDEX_CLOCK)
    parts.append(f"{ref}{measure.INDEX_CLOCK},format={PIX_FMT}[b]")
    parts.append(f"[0:v]{measure.INDEX_CLOCK},setsar=1,format={PIX_FMT}[a]")
    parts.append(f"[a][b]{metric}=stats_file={stats}:eof_action=endall")
    return ";".join(parts)


def compare(cut: str | Path, master: str | Path, *, ranges: list[list[int]] | None = None,
            timeout: float = 3600) -> dict:
    """PSNR and SSIM of every frame of `cut` against the master's frames in `ranges` ([[in, out), ...], master frames).
    Without ranges, the master's first frames, as many as the cut has."""
    cut, master = Path(cut).resolve(), Path(master).resolve()
    for p in (cut, master):
        if not p.is_file():
            raise UsageError(f"No video at {p}.")
    ci = media.video_info(cut, count=True)
    mi = media.video_info(master, count=True)
    if (ci["fps_num"], ci["fps_den"]) != (mi["fps_num"], mi["fps_den"]):
        raise UsageError(f"The cut runs at {ci['fps_num']}/{ci['fps_den']} fps and the master at "
                         f"{mi['fps_num']}/{mi['fps_den']}; the benchmark compares frame for frame, so both arms "
                         "render on the master's clock.")
    if not ranges:
        ranges = [[0, min(int(ci["frames"] or 0), int(mi["frames"] or 0))]]
    for a, b in ranges:
        if not (0 <= a < b <= int(mi["frames"] or 0)):
            raise UsageError(f"Range {a}-{b} isn't inside the master's {mi['frames']} frames.")
    reference = sum(b - a for a, b in ranges)
    same = (ci["width"], ci["height"]) == (mi["width"], mi["height"])
    size = None if same else (ci["width"], ci["height"])
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="slate-bench-") as tmp:
        for metric in ("psnr", "ssim"):
            stats = f"{metric}.log"
            graph = _graph(ranges, size, metric, stats)
            procs.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(cut), "-i", str(master), "-lavfi", graph,
                       "-an", "-f", "null", "-"], cwd=tmp, check=True, timeout=timeout)
            text = (Path(tmp) / stats).read_text(encoding="utf-8") if (Path(tmp) / stats).exists() else ""
            out[metric] = measure.parse_psnr_stats(text) if metric == "psnr" else parse_ssim_stats(text)
    psnr, ssim = out["psnr"], out["ssim"]
    below = [i for i, v in enumerate(ssim) if v < SSIM_FRAME_FLOOR]
    return {
        "cut": str(cut), "master": str(master), "ranges": ranges,
        "frames_cut": ci["frames"], "frames_reference": reference, "frames_compared": len(ssim),
        "frames_match": ci["frames"] == reference,
        "reference": "same size" if same else "reframed (master scaled to cover the cut and centre-cropped)",
        "psnr": measure.summarize(psnr, threshold=PSNR_FLOOR_DB),
        "ssim": {"mean": round(sum(ssim) / len(ssim), 5) if ssim else None, "min": round(min(ssim), 5) if ssim else None,
                 "frame_floor": SSIM_FRAME_FLOOR, "below_floor": len(below),
                 "below_floor_share": round(len(below) / len(ssim), 5) if ssim else None,
                 "worst": sorted(({"frame": i, "ssim": round(v, 5)} for i, v in enumerate(ssim)),
                                 key=lambda r: r["ssim"])[:10]},
        "engine": media.engine("psnr and ssim", pix_fmt=PIX_FMT),
    }


def finite(v: float | None) -> float | None:
    return None if v is None else (measure.IDENTICAL_DB if math.isinf(v) else v)
