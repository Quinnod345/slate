"""Perfect, don't replace, for voice takes: a revised take is measured against the approved one.

`slate revise check` (slate.state.revise) loads this module and runs the hook on any revision whose artifact is a
voice take. Notes on an approved read ("a touch warmer on the last line") should change that read, not the voice
behind it. So the hook compares the approved and the revised take:

- the voice itself (the take's voice id and model): a different voice is a replacement, so it FAILs;
- level (integrated loudness) and pitch (median F0): a move beyond a small tolerance is REVIEW, with the numbers,
  because only a person can hear whether it followed the notes;
- length: reported, so a revision that no longer fits its shot is caught by the fit gate too.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from slate import gates as gates_mod
from slate import jsonio
from slate.gates import GateResult
from slate.voice import audio as audio_mod
from slate.voice import measure

LEVEL_TOLERANCE_LU = 2.0
PITCH_TOLERANCE_ST = 2.0


def applies(path: Path) -> bool:
    p = Path(path)
    return p.suffix.lower() == ".wav" and "voice" in p.parts


def _take_json(path: Path) -> dict:
    """The take record beside a take's stage file (t01.wav, t01.clean.wav and t01.paced.wav share t01.json)."""
    p = Path(path)
    base = p.name.split(".")[0]
    return jsonio.read(p.with_name(f"{base}.json"), default={}) or {}


def _measure(path: Path) -> dict:
    x, sr = audio_mod.load(path)
    x16 = audio_mod.resample(x, sr, 16000)
    f0 = measure.yin_f0(x16, 16000)
    v = f0[~np.isnan(f0)]
    return {"lufs": audio_mod.loudness(x, sr)["lufs"], "seconds": round(len(x) / sr, 3),
            "f0_median_hz": round(float(np.median(v)), 1) if len(v) > 10 else None}


def compare(film_dir: Path, approved: Path, current: Path, revision: dict | None = None) -> GateResult:
    approved, current = Path(approved), Path(current)
    a, b = _measure(approved), _measure(current)
    ja, jb = _take_json(approved), _take_json(current)
    details = {"approved": a, "revised": b}
    evidence = [f"approved {approved.name}: {a}", f"revised {current.name}: {b}"]
    engine = gates_mod.engine("slate voice revise (BS.1770 loudness, YIN pitch)")
    for key in ("voice_id", "model"):
        if ja.get(key) and jb.get(key) and ja[key] != jb[key]:
            return GateResult.fail("voice_revision", f"the revision changed the {key.replace('_', ' ')} "
                                   f"({ja[key]} -> {jb[key]}): that replaces the approved read instead of perfecting it",
                                   evidence=evidence, engine=engine, details=details)
    moves = []
    if a["lufs"] is not None and b["lufs"] is not None and abs(b["lufs"] - a["lufs"]) > LEVEL_TOLERANCE_LU:
        moves.append(f"level moved {b['lufs'] - a['lufs']:+.1f} LU")
    if a["f0_median_hz"] and b["f0_median_hz"]:
        st = 12 * math.log2(b["f0_median_hz"] / a["f0_median_hz"])
        details["pitch_shift_st"] = round(st, 2)
        if abs(st) > PITCH_TOLERANCE_ST:
            moves.append(f"pitch moved {st:+.1f} semitones")
    details["length_change_s"] = round(b["seconds"] - a["seconds"], 3)
    if moves:
        return GateResult.review("voice_revision", "; ".join(moves) + " against the approved take: listen before "
                                 "calling it the same read", evidence=evidence, engine=engine, details=details)
    return GateResult.pass_("voice_revision", "same voice, level and pitch as the approved take "
                            f"(length {details['length_change_s']:+.2f} s)", evidence=evidence, engine=engine,
                            details=details)


def _register() -> None:
    try:
        from slate.state import revise  # noqa: PLC0415
    except ModuleNotFoundError:
        return
    if hasattr(revise, "register_hook"):
        revise.register_hook("voice_revision", compare, applies=applies, owner="slate.voice")


_register()
