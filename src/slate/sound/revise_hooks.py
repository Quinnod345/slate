"""Perfect, don't replace, for the sound: a revised mix, stem or score is measured against the approved one.

`slate revise check` (slate.state.revise) loads this module and runs `level_vs_voice` on any revision whose artifact
is a sound file: the delivery mix (sound/mix/final_mix.wav), one of its stems, or the assembled score. Notes on an
approved mix ("the music a touch lower under the second act") change that mix; they never swap the approach. So the
hook measures the two versions and says what moved:

- the length: a mix, a stem and a score are all exactly the film's length from its first frame, and a revision that
  changes the length has moved every cue after the change, so that FAILs;
- the delivery targets, for the mix: its loudness within the film's tolerance and its true peak at or under the
  ceiling, or FAIL (the verify gates check it again; this says it at the revision, where it happened);
- the balance against the voice: the voice's own level in the mix, and everything else's level against it. Both are
  measured against the current dialogue stem by least squares, so the approved mix needs no stems of its own. For a
  stem or the score, its level against that same voice. A move of more than 1 LU is REVIEW, with the numbers: only a
  person can hear whether it followed the notes;
- otherwise PASS, with the numbers.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from slate import gates as gates_mod
from slate.gates import GateResult
from slate.sound import audio, measure
from slate.sound.film import MIX_DIR, load

GATE = "level_vs_voice"
MOVE_LU = 1.0  # a move bigger than this is worth a listen against the notes


def applies(path: Path) -> bool:
    p = Path(path)
    return p.suffix.lower() == ".wav" and "sound" in p.parts


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-12))


def _level(x: np.ndarray, sr: int) -> float | None:
    """Integrated loudness, or None for silence (a stem with nothing in it has no level)."""
    if not np.any(x):
        return None
    value = measure.integrated(x, sr)
    return None if value is None or not math.isfinite(value) else round(float(value), 2)


def _balance(y: np.ndarray, voice: np.ndarray, sr: int) -> dict:
    """The voice's gain in `y` (least squares against the dialogue stem) and everything else's level against it."""
    n = min(len(y), len(voice))
    y, v = y[:n], voice[:n]
    vv = float(np.sum(v * v))
    if vv <= 0:
        return {}
    gain = float(np.sum(y * v)) / vv
    rest = y - gain * v
    voice_l = _level(gain * v, sr)
    rest_l = _level(rest, sr)
    return {"voice_gain_db": round(_db(abs(gain)), 2), "voice_lufs": voice_l, "rest_lufs": rest_l,
            "rest_vs_voice_lu": round(rest_l - voice_l, 2) if rest_l is not None and voice_l is not None else None}


def compare(film_dir: Path, approved: Path, current: Path, revision: dict | None = None) -> GateResult:
    film = load(film_dir)
    approved, current = Path(approved), Path(current)
    a, sr_a = audio.read_wav(approved)
    b, sr_b = audio.read_wav(current)
    a, b = audio.to_stereo(a), audio.to_stereo(b)
    engine = gates_mod.engine(f"slate sound revise ({measure.ENGINE['name']})", measure.engine_info()["version"])
    details: dict = {"artifact": film.rel(current), "approved_frames": len(a), "revised_frames": len(b)}
    evidence = [f"approved {approved.name}: {len(a)} samples at {sr_a} Hz", f"revised {current.name}: {len(b)} samples "
                f"at {sr_b} Hz"]
    if sr_a != sr_b or len(a) != len(b):
        return GateResult.fail(GATE, f"the revision changed the {'sample rate' if sr_a != sr_b else 'length'} "
                               f"({len(a)} -> {len(b)} samples): every cue after the change has moved, so this "
                               "replaces the approved sound instead of perfecting it",
                               evidence=evidence, engine=engine, details=details)
    la, lb = measure.loudness(a, sr_a), measure.loudness(b, sr_b)
    details["approved"] = {k: la[k] for k in ("integrated_lufs", "true_peak_dbtp", "lra_lu")}
    details["revised"] = {k: lb[k] for k in ("integrated_lufs", "true_peak_dbtp", "lra_lu")}
    is_mix = current.name == "final_mix.wav"
    problems, moves = [], []
    if is_mix:
        t = film.targets
        i, tp = lb["integrated_lufs"], lb["true_peak_dbtp"]
        if i is None or abs(i - t["lufs"]) > t["tol"] + 1e-9 or tp > t["true_peak"] + 1e-6:
            problems.append(f"the revised mix is {i} LUFS, {tp} dBTP against {t['lufs']:g} ±{t['tol']:g} and "
                            f"≤ {t['true_peak']:g}")
    voice_path = film.path(f"{MIX_DIR}/stems/dialogue.wav")
    voice = audio.to_stereo(audio.read_wav(voice_path)[0]) if voice_path.exists() else None
    if voice is not None and np.any(voice) and len(voice) == len(b):
        if is_mix:
            ba, bb = _balance(a, voice, sr_a), _balance(b, voice, sr_b)
            details["balance"] = {"approved": ba, "revised": bb}
            if ba and bb:
                voice_move = bb["voice_gain_db"] - ba["voice_gain_db"]
                if abs(voice_move) > MOVE_LU:
                    moves.append(f"the voice moved {voice_move:+.1f} dB in the mix")
                if ba["rest_vs_voice_lu"] is not None and bb["rest_vs_voice_lu"] is not None:
                    shift = bb["rest_vs_voice_lu"] - ba["rest_vs_voice_lu"]
                    details["balance_shift_lu"] = round(shift, 2)
                    if abs(shift) > MOVE_LU:
                        moves.append(f"everything but the voice moved {shift:+.1f} LU against it")
        else:
            voice_l = _level(voice, sr_b)
            la_i, lb_i = _level(a, sr_a), _level(b, sr_b)
            if voice_l is not None and la_i is not None and lb_i is not None:
                details["vs_voice_lu"] = {"approved": round(la_i - voice_l, 2), "revised": round(lb_i - voice_l, 2)}
                shift = lb_i - la_i
                details["balance_shift_lu"] = round(shift, 2)
                if abs(shift) > MOVE_LU:
                    moves.append(f"{current.stem} moved {shift:+.1f} LU against the voice")
    else:
        la_i, lb_i = la["integrated_lufs"], lb["integrated_lufs"]
        if la_i is not None and lb_i is not None and abs(lb_i - la_i) > MOVE_LU:
            moves.append(f"its loudness moved {lb_i - la_i:+.1f} LU (no dialogue stem to measure against)")
    evidence += [f"approved: {details['approved']}", f"revised: {details['revised']}"]
    if problems:
        return GateResult.fail(GATE, problems[0], evidence=evidence, engine=engine, details=details)
    if moves:
        return GateResult.review(GATE, "; ".join(moves) + " against the approved version: listen against the notes",
                                 evidence=evidence, engine=engine, details=details)
    return GateResult.pass_(GATE, "same length and the same balance against the voice as the approved version"
                            + (f" (moved {details['balance_shift_lu']:+.2f} LU)" if "balance_shift_lu" in details
                               else ""), evidence=evidence, engine=engine, details=details)


def _register() -> None:
    try:
        from slate.state import revise  # noqa: PLC0415
    except ModuleNotFoundError:
        return
    if hasattr(revise, "register_hook"):
        revise.register_hook(GATE, compare, applies=applies, owner="slate.sound")


_register()
