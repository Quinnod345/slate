"""Audio measurements for the gates: EBU R128 loudness and true peak (ffmpeg), and silence (exact samples).

Loudness is measured by ffmpeg's ebur128 filter, the meter the films were mixed and verified against, and every
result names the ffmpeg build. Two details from its output matter:

- a stem of digital silence reads "I: -70.0 LUFS": the meter's floor, because every block fell under the absolute
  gate. The films' validator called a stem silent only below −70, which can never happen, so B's silent ambience
  stem passed. Here the floor itself (≤ −70.0) means "nothing above the gate", and silence is also measured
  directly from the samples, so the verdict never rests on a meter quirk;
- true peak of silence is "-inf dBFS", which a number-only pattern doesn't match.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from slate.qa import media, wav

LOUDNESS_FLOOR = -70.0  # ebur128's absolute gate: a signal with no block above it reads exactly this
SILENT_PEAK_DBFS = -90.0  # below this sample peak a stem is silent (under the noise floor of 16-bit dither)

_I = re.compile(r"I:\s+(-?[\d.]+|-?inf)\s+LUFS")
_LRA = re.compile(r"LRA:\s+(-?[\d.]+)\s+LU")
_TP = re.compile(r"Peak:\s+(-?[\d.]+|-?inf)\s+dBFS")


def _num(text: str) -> float:
    t = text.strip().lower()
    if t == "-inf":
        return -math.inf
    if t == "inf":
        return math.inf
    return float(t)


def parse_ebur128(stderr: str) -> dict:
    """The summary block of ffmpeg's ebur128 output: integrated LUFS, LRA and true peak (dBTP)."""
    i, lra, tp = _I.findall(stderr), _LRA.findall(stderr), _TP.findall(stderr)
    return {"integrated_lufs": _num(i[-1]) if i else None, "lra_lu": float(lra[-1]) if lra else None,
            "true_peak_dbtp": _num(tp[-1]) if tp else None}


def ebur128(path: str | Path, *, true_peak: bool = True) -> dict:
    """Measure a file with ffmpeg's EBU R128 meter. True peak (4× oversampled, the slow part) only when asked."""
    media.require("ffmpeg")
    graph = "ebur128=peak=true:framelog=quiet" if true_peak else "ebur128=framelog=quiet"
    proc = media._run(["ffmpeg", "-nostats", "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:a:0",
                       "-af", graph, "-f", "null", "-"])
    out = parse_ebur128(proc.stderr or "")
    if out["integrated_lufs"] is None:
        raise media.MediaError(f"ffmpeg's ebur128 meter gave no reading for {Path(path).name}.")
    return out


def engine() -> dict:
    return media.engine("ebur128")


def db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 0 else -math.inf


def levels(path: str | Path) -> dict:
    """Sample peak and RMS (dBFS) over the whole file, read exactly (no decoder, no meter)."""
    peak, sq, n = 0.0, 0.0, 0
    for block in wav.blocks(path):
        if block.size:
            peak = max(peak, float(np.max(np.abs(block))))
            sq += float(np.sum(block * block))
            n += block.size
    rms = math.sqrt(sq / n) if n else 0.0
    return {"peak_dbfs": db(peak), "rms_dbfs": db(rms), "samples": n, "digital_silence": peak == 0.0}


def rounded(x: float | None, nd: int = 2) -> float | str | None:
    """A measurement JSON can carry: −inf becomes the string "-inf"."""
    if x is None:
        return None
    if math.isinf(x):
        return "-inf" if x < 0 else "inf"
    return round(float(x), nd)
