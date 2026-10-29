"""The delivery mix: one WAV and four stems (dialogue, effects, ambience, music) that add up to it, sample for sample.

This is the films' final_mix.py, generalized (its review cousin, mix_with_score.py, normalised with a one-pass
loudnorm that rides the gain, so its stems could never sum to its mix; that's why this one uses fixed gains only):

1. The voice: every take from timing/vo_cues.json, placed and levelled (slate.sound.vo).
2. The music: the assembled score (sound/score/score.wav), set a measured number of LU under the voice (15 by
   default) for the whole film and ducked a few dB (4) under speech by a follower keyed from the voice (80 ms attack,
   650 ms release). Tonal effect stems join the music stem.
3. The effects: whatever effect stems exist in sound/stems/ (ui, fx and their reverb join the effects stem; amb is
   the ambience), each trimmed to the brand palette's level against the voice. There may be none: a film with only
   score and voice mixes the same way, which is all the first reel needs.
4. Everything under 25 Hz comes out first (a zero-phase high-pass on every part, as the films' masters did, so no hit
   moves and the stems still sum): subsonic energy would otherwise spend headroom nobody hears.
5. One linear gain takes the sum to the target loudness (-14 LUFS by default). If the true peak (4x oversampled)
   would go over the ceiling (-1 dBTP), the voice alone is peak-limited (plosives carry the peaks: 0.9 dB under the
   ceiling, or 0.4 when nothing else sounds and nothing can sum on its peaks), then, if needed,
   one linked gain curve comes off every stem alike (so the stems still sum), then whatever's left comes off the
   whole mix inside the loudness tolerance. If that still can't meet both targets, the mix refuses and says so
   rather than hiding it. The voice limiter's touch and measurement basis go in the report (`voice_limiter`).
   `sound.voice_limiter` fails an exact measurement at 5%: past that, the limiter is shaping the read. Waveform-only
   estimates remain UNCHECKED.

Everything is written 48 kHz / 24-bit (the film's rate), exactly the film's length, starting on its first frame.
"""

from __future__ import annotations

import contextlib
import fcntl
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterator

import numpy as np

from slate import gates, jsonio, log
from slate.errors import Busy, MissingPrerequisite, Refused
from slate.sound import audio, dsp, gap, measure, vo
from slate.sound.film import MIX_DIR, SCORE_DIR, STEMS_DIR, Film, load, record_lock

EFFECTS_ROLES = ("ui", "fx", "verb")
MUSIC_ROLES = ("tonal", "tverb")
AMBIENCE_ROLES = ("amb",)
ROLES = EFFECTS_ROLES + MUSIC_ROLES + AMBIENCE_ROLES
DELIVERY = ("dialogue", "effects", "ambience", "music")
HIGHPASS_HZ = 25.0  # every part, zero-phase, before the loudness gain (the films' masters)
VOICE_LIMITER_MAX = 0.05  # the voice limiter may touch under 5% of the speech (sound.voice_limiter)
#: The voice is limited this far under the true-peak ceiling (before the loudness gain), so the other stems summing
#: on its peaks keep the mix under it. A voice heard alone needs only the final check's 0.1 dB and the loudness gain's
#: drift after limiting: 0.4 dB. On one voice-only film, 0.9 had the limiter on 6.1% of the speech, 0.4 on 3.0%.
VOICE_MARGIN_DB = 0.9
VOICE_ALONE_MARGIN_DB = 0.4


# ---------------------------------------------------------------------------------------------------------------
# Inputs


def score_path(film: Film, override: str | Path | None = None) -> Path | None:
    if override:
        p = film.path(override)
        if not p.exists():
            raise MissingPrerequisite(f"No score at {p}.", hint="Run `slate score assemble`, or pass --no-score.")
        return p
    p = film.path(f"{SCORE_DIR}/score.wav")
    return p if p.exists() else None


def stem_manifest(film: Film) -> dict[str, dict]:
    """{role: {file, duck}} from sound/stems/stems.json, or from the role files present (ui.wav, fx.wav, ...)."""
    folder = film.path(STEMS_DIR)
    manifest_path = folder / "stems.json"
    out: dict[str, dict] = {}
    if manifest_path.exists():
        data = jsonio.read(manifest_path)
        for role, entry in (data.get("stems") or {}).items():
            if role not in ROLES:
                raise Refused(f"sound/stems/stems.json names a stem role {role!r}; the roles are {', '.join(ROLES)}.")
            entry = {"file": entry} if isinstance(entry, str) else dict(entry)
            entry.setdefault("file", f"{role}.wav")
            out[role] = entry
    else:
        for role in ROLES:
            if (folder / f"{role}.wav").exists():
                out[role] = {"file": f"{role}.wav", "duck": False}
    return out


def load_stems(film: Film) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    stems, files = {}, {}
    for role, entry in stem_manifest(film).items():
        p = film.path(STEMS_DIR) / entry["file"]
        if not p.exists():
            raise MissingPrerequisite(f"sound/stems/stems.json lists {entry['file']} for {role}, but it isn't there.")
        stems[role] = audio.load(p, film.sr, film.n)
        if entry.get("duck"):
            stems[role] = ("duck", stems[role])  # ducked once the voice is rendered
        files[role] = film.rel(p)
    return stems, files


# ---------------------------------------------------------------------------------------------------------------
# The mix of arrays (what `run` and the golden checks share)


def _peaks(x: np.ndarray) -> float:
    pk = max(abs(float(x.max())), abs(float(x.min()))) if x.size else 0.0
    return round(20 * math.log10(pk), 2) if pk > 0 else -180.0


class EngineStems:
    """Each engine stem (ui, fx, verb, tonal, tverb, amb) at the master gain, made when it's asked for.

    The mix keeps only its sources, their trims and duck curves, the linked limiter's curve and the final gain, so a
    feature-length film never holds a second full-length copy of every stem."""

    def __init__(self, sources: dict[str, np.ndarray], factors: dict[str, float], ducks: dict[str, np.ndarray],
                 curve: np.ndarray | None, gain: float, highpass_hz: float | None = None, sr: int = 48000,
                 silence_masks: dict | None = None, effects_curve: np.ndarray | None = None):
        self.sources, self.factors, self.ducks, self.curve, self.gain = sources, factors, ducks, curve, gain
        self.highpass_hz, self.sr = highpass_hz, sr
        self.silence_masks, self.effects_curve = silence_masks or {}, effects_curve

    def roles(self) -> list[str]:
        return list(self.sources)

    def __contains__(self, role: str) -> bool:
        return role in self.sources

    def get(self, role: str) -> np.ndarray:
        y = self.sources[role] * self.factors.get(role, 1.0)
        if role in self.ducks:
            y *= self.ducks[role][:, None]
        bus = "effects" if role in EFFECTS_ROLES else "music" if role in MUSIC_ROLES else "ambience"
        return self.transform(y, bus)

    def transform(self, samples: np.ndarray, bus: str) -> np.ndarray:
        """Apply the master's exact shared transform to one additive part of a delivery bus.

        Silence restoration uses the complete bus's original mask, so separate families and individual ride deltas
        retain their linear sum even when hits overlap. No part gets its own normalization or limiter.
        """
        y = np.array(samples, dtype=np.float64, copy=True)
        if self.highpass_hz and np.any(y):
            y = dsp.highpass(y, self.highpass_hz, self.sr, zero_phase=True)
            if bus in self.silence_masks:
                y *= self.silence_masks[bus][:, None]
            else:
                y = dsp.keep_silence(y, samples, self.sr)
        if bus == "effects" and self.effects_curve is not None:
            y *= self.effects_curve
        if self.curve is not None:
            y *= self.curve[:, None]
        return y * self.gain

    def transform_window(self, start: int, samples: np.ndarray, bus: str, total_samples: int) -> tuple[int, np.ndarray]:
        """Transform a short hit without allocating a full film for each comparison.

        The second-order master high-pass gets two seconds of zero context on both sides (or the real film edge).
        At the 25 Hz master cutoff its residual is far below float64 precision. Every time-varying curve and silence
        mask still uses its original absolute sample positions.
        """
        padding = int(np.ceil(max(2.0, 50 / self.highpass_hz) * self.sr)) if self.highpass_hz else 0
        left, right = max(0, start-padding), min(total_samples, start+len(samples)+padding)
        y = np.zeros((right-left, 2))
        lo, hi = max(0, start), min(total_samples, start+len(samples))
        if hi > lo:
            y[lo-left:hi-left] = samples[lo-start:hi-start]
        if self.highpass_hz and np.any(y):
            y = dsp.highpass(y, self.highpass_hz, self.sr, zero_phase=True)
            if bus in self.silence_masks:
                y *= self.silence_masks[bus][left:right, None]
        if bus == "effects" and self.effects_curve is not None:
            y *= self.effects_curve[left:right]
        if self.curve is not None:
            y *= self.curve[left:right, None]
        return left, y*self.gain


def prepare_score(score: np.ndarray | None, voice: np.ndarray, sr: int, *, under_lu: float = 18.5,
                  duck_db: float = 6.0, attack_s: float = 0.08, release_s: float = 0.65,
                  words: list | None = None, score_guard: dict | None = None,
                  voice_lufs: float | None = None) -> tuple[np.ndarray, dict]:
    """Prepare the composed score once, before effect guards measure it beside the voice.

    Effects may subsequently make approved local score matches and dips. The master must keep those changes,
    rather than renormalizing and ducking that already prepared bus a second time.
    """
    report = {"score_lufs_pre": None, "music_vs_voice_lu": None}
    if score is None or not np.any(score):
        return np.zeros_like(voice), report
    sc_l = measure.integrated(score, sr)
    report["score_lufs_pre"] = round(sc_l, 2)
    if not np.any(voice):
        report["duck_db"] = 0.0
        return score.copy(), report
    vo_l = voice_lufs if voice_lufs is not None else measure.integrated(voice, sr)
    music = score * dsp.db((vo_l - under_lu) - sc_l)
    music *= dsp.db(-duck_db * dsp.follower(voice, sr, attack_s, release_s))[:, None]
    report.update(music_vs_voice_lu=-under_lu, duck_db=duck_db)
    if score_guard is not None and words:
        from slate.sound import guard  # noqa: PLC0415

        music, report["score_guard"] = guard.apply(music, voice, words, sr, score_guard)
    return music, report


def mix_arrays(vo_stem: np.ndarray, stems: dict[str, np.ndarray], score: np.ndarray | None, sr: int, *,
               targets: dict, under_lu: float = 18.5, duck_db: float = 6.0, music_attack_s: float = 0.08,
               music_release_s: float = 0.65, stem_trim_db: dict | None = None,
               effects_ceiling_dbtp: float | None = None, linked_limit: bool = True, limiter: str = "slate",
               duck_depths: dict | None = None, ducked_roles: tuple = (), words: list | None = None,
               score_guard: dict | None = None, highpass_hz: float | None = HIGHPASS_HZ,
               score_prepared: bool = False, prepared_score_report: dict | None = None,
               voice_margin_db: float | None = None) -> dict:
    """Mix arrays of equal length. Returns {"final": {delivery stems}, "mix": the mix, "engine": EngineStems,
    "report": {...}}. Raises Refused when the targets can't be met with a gain and the limiters. highpass_hz=None
    leaves the sub-bass in (how the films' own final_mix.py ran, for parity checks against their records).

    Memory: arrays the mix makes itself are scaled in place; the caller's arrays are never modified."""
    lufs_t, tol, tp_t = float(targets["lufs"]), float(targets["tol"]), float(targets["true_peak"])
    n = len(vo_stem)
    trims = {r: float((stem_trim_db or {}).get(r, 0.0)) for r in ROLES}
    factors = {r: float(dsp.db(trims[r])) for r in stems}
    lim = dsp.limit_ffmpeg if limiter == "ffmpeg" else dsp.limit
    ducks: dict[str, np.ndarray] = {}
    if ducked_roles and np.any(vo_stem):
        env_voice = dsp.follower(vo_stem, sr, 0.06, 0.45)
        for role in ducked_roles:
            depth = float((duck_depths or {}).get(role, 0.0))
            ducks[role] = 1 - env_voice * (1 - dsp.db(depth))

    def role_sum(roles: tuple) -> np.ndarray | None:
        out = None
        for r in roles:
            if r not in stems:
                continue
            y = stems[r] * factors[r]
            if r in ducks:
                y *= ducks[r][:, None]
            if out is None:
                out = y
            else:
                out += y
        return out

    effects = role_sum(EFFECTS_ROLES)
    ambience = role_sum(AMBIENCE_ROLES)
    tonal = role_sum(MUSIC_ROLES)
    effects = effects if effects is not None else np.zeros((n, 2))
    ambience = ambience if ambience is not None else np.zeros((n, 2))
    dialogue, dialogue_owned = vo_stem, False
    has_voice = bool(np.any(dialogue))
    vo_l = measure.integrated(dialogue, sr) if has_voice else measure.SILENT_FLOOR
    report: dict = {"target": {"lufs": lufs_t, "tol": tol, "true_peak": tp_t}, "sample_rate": sr, "samples": n,
                    "seconds": round(n / sr, 6), "voice_lufs_pre": round(vo_l, 2) if has_voice else None,
                    "stem_trim_db": {r: v for r, v in trims.items() if r in stems}, "limiter": limiter}
    if score_prepared:
        music = score.copy() if score is not None else np.zeros((n, 2))
        report.update(prepared_score_report or {"score_lufs_pre": None, "music_vs_voice_lu": None})
        report["score_prepared"] = True
    else:
        music, score_report = prepare_score(score, dialogue, sr, under_lu=under_lu, duck_db=duck_db,
                                           attack_s=music_attack_s, release_s=music_release_s, words=words,
                                           score_guard=score_guard, voice_lufs=vo_l)
        report.update(score_report)
    if tonal is not None:
        music += tonal
        del tonal
    silence_masks = {}
    if highpass_hz:
        # the films' masters took everything under 25 Hz out before the loudness gain; zero-phase, so no hit moves,
        # and a digital silence stays silent (the filter would otherwise ring a little into a silent last second)
        def cut(x: np.ndarray, bus: str) -> np.ndarray:
            mask = dsp.keep_silence(np.ones(len(x)), x, sr)
            silence_masks[bus] = mask
            return dsp.highpass(x, highpass_hz, sr, zero_phase=True) * mask[:, None] if np.any(x) else x

        if has_voice:
            dialogue, dialogue_owned = cut(dialogue, "dialogue"), True
        effects, ambience, music = cut(effects, "effects"), cut(ambience, "ambience"), cut(music, "music")
        report["highpass_hz"] = float(highpass_hz)
    raw_dialogue, raw_effects = dialogue, effects
    limited = effects_limited = False
    voice_gain: np.ndarray | None = None  # the voice limiter's last gain curve (1.0 wherever it left the voice alone)
    effects_curve = None
    mix = dialogue + effects + ambience + music
    mix_l = measure.integrated(mix, sr)
    if mix_l <= measure.SILENT_FLOOR:
        raise Refused("There's nothing to mix: no voice, no score and no effect stems with any sound in them.",
                      hint="Voice the script (`slate voice tts`), assemble a score (`slate score assemble`), or add stems.")
    mix_tp = measure.true_peak(mix, sr)
    g_db = lufs_t - mix_l
    alone = not (np.any(effects) or np.any(ambience) or np.any(music))
    # the palette may set the shared margin (levels.voice_margin_db) where the linked limiter keeps the sum under the
    # ceiling transparently; the voice heard alone keeps its own
    margin = VOICE_ALONE_MARGIN_DB if alone else float(VOICE_MARGIN_DB if voice_margin_db is None else voice_margin_db)
    for _ in range(4):
        if mix_tp + g_db <= tp_t - 0.1:
            break
        if has_voice:
            # the voice carries the peaks: limit it so that, after the loudness gain, it sits `margin` under the ceiling
            if limiter == "ffmpeg":
                dialogue, voice_gain = lim(raw_dialogue, tp_t - margin - g_db, sr), None
            else:  # Slate's limiter, its gain curve kept for the report (the same call dsp.limit makes)
                voice_gain = dsp.gain_curve(raw_dialogue, tp_t - margin - g_db, sr, look_ms=5.0, release_ms=60.0)
                dialogue = raw_dialogue * voice_gain[:, None]
            dialogue_owned = True
            limited = True
        if effects_ceiling_dbtp is not None and np.any(raw_effects):
            effects = lim(raw_effects, effects_ceiling_dbtp - g_db, sr)
            effects_curve = np.divide(effects, raw_effects, out=np.ones_like(effects), where=raw_effects != 0)
            if np.any((raw_effects == 0) & (effects != 0)):
                raise Refused("The selected effects limiter moves samples, so its family stems cannot sum exactly.",
                              hint="Use the Slate limiter for a mix with family stems.")
            effects_limited = True
        if not has_voice and effects_ceiling_dbtp is None:
            break
        del mix
        mix = dialogue + effects + ambience + music
        mix_l, mix_tp = measure.integrated(mix, sr), measure.true_peak(mix, sr)
        g_db = lufs_t - mix_l
    del raw_effects
    if has_voice:
        report["voice_limiter"] = {**_voice_limiter(raw_dialogue, dialogue if limited else None, voice_gain, sr),
                                   "margin_db": margin, "voice_alone": bool(alone)}
    linked = None
    total_curve = None
    if linked_limit and mix_tp + g_db > tp_t - 0.1:
        # one gain curve on the sum, the same on every stem, then the loudness gain again
        for _ in range(3):
            curve = dsp.linked_gain(mix, tp_t - 0.35 - g_db, sr)
            c2 = curve[:, None]
            if dialogue_owned:
                dialogue *= c2
            else:
                dialogue, dialogue_owned = dialogue * c2, True
            effects *= c2
            ambience *= c2
            music *= c2
            total_curve = curve if total_curve is None else total_curve * curve
            del mix
            mix = dialogue + effects + ambience + music
            mix_l, mix_tp = measure.integrated(mix, sr), measure.true_peak(mix, sr)
            g_db = lufs_t - mix_l
            linked = {"max_reduction_db": round(float(-20 * np.log10(curve.min())), 2),
                      "ms_reduced_over_0.5db": int(np.sum(curve < 10 ** (-0.5 / 20)) // max(1, sr // 1000))}
            if mix_tp + g_db <= tp_t - 0.05:
                break
    del mix
    # whatever is left over comes off everything together, inside the loudness tolerance
    tp_after = mix_tp + g_db
    if tp_after > tp_t:
        g_db -= (tp_after - tp_t) + 0.05
    g = float(dsp.db(g_db))
    if dialogue_owned:
        dialogue *= g
    else:
        dialogue = dialogue * g
    effects *= g
    ambience *= g
    music *= g
    final = {"dialogue": dialogue, "effects": effects, "ambience": ambience, "music": music}
    out_mix = dialogue + effects + ambience + music
    f_l, f_tp = measure.integrated(out_mix, sr), measure.true_peak(out_mix, sr)
    ok = abs(f_l - lufs_t) <= tol and f_tp <= tp_t + 1e-6
    report.update({"gain_db": round(g_db, 2), "dialogue_limited": limited, "effects_ceiling_dbtp": effects_ceiling_dbtp,
                   "effects_limited": effects_limited, "linked_limiter": linked, "final_lufs": round(f_l, 2),
                   "final_true_peak_dbtp": round(f_tp, 2), "pass": ok})
    for k, v in final.items():
        report[f"stem_{k}_peak_dbfs"] = _peaks(v)
    if not ok:
        raise Refused(f"The mix can't meet {lufs_t:g} LUFS ±{tol:g} and {tp_t:g} dBTP with one gain and the limiters "
                      f"(it reached {f_l:.1f} LUFS, {f_tp:.1f} dBTP).",
                      hint="Look at what's peaking in sound/mix/final_mix.json; lower the loudest stem's trim or the "
                           "voice gain, then mix again.", details={"report": report})
    engine = EngineStems(stems, factors, ducks, total_curve, g, highpass_hz, sr, silence_masks, effects_curve)
    return {"final": final, "mix": out_mix, "engine": engine, "report": report}


def _voice_limiter(raw: np.ndarray, limited: np.ndarray | None, gain: np.ndarray | None, sr: int) -> dict:
    """Report the voice limiter's touch and its measurement basis.

    The retained Slate gain curve is exact; a bypass proves zero touch. A limited/raw waveform ratio is only an
    estimate, because a waveform-only backend can resample or otherwise change samples without exposing its gain.
    """
    if limited is None:
        g = np.ones(len(raw))
        basis = "bypassed"
    elif gain is not None:
        g = gain
        basis = "gain_curve"
    else:
        a = np.max(np.abs(raw), axis=1) if raw.ndim == 2 else np.abs(raw)
        b = np.max(np.abs(limited), axis=1) if limited.ndim == 2 else np.abs(limited)
        g = np.where(a > 1e-6, np.minimum(1.0, b / np.maximum(a, 1e-12)), 1.0)
        basis = "waveform_ratio"
    out = dsp.limiter_touch(raw, g, sr)
    out.update({"limited": limited is not None, "measurement_basis": basis,
                "rule": f"under {VOICE_LIMITER_MAX:.0%} of the speech"})
    return out


def quantized_sum_error(final: dict[str, np.ndarray], mix: np.ndarray) -> float:
    """Max |mix - sum(stems)| after each is quantized to 24 bits exactly as the writer quantizes it."""
    def q(x: np.ndarray) -> np.ndarray:
        return np.clip(np.round(x * 8388607.0), -8388608, 8388607).astype(np.int64)

    worst = 0
    step = 480000  # 10 s at 48 kHz: bounded memory for a feature-length film
    for i in range(0, len(mix), step):
        total = sum(q(v[i:i + step]) for v in final.values())
        worst = max(worst, int(np.max(np.abs(q(mix[i:i + step]) - total))))
    return worst / 8388608.0


# ---------------------------------------------------------------------------------------------------------------
# The command


@contextlib.contextmanager
def _mix_lock(film: Film) -> Iterator[None]:
    path = film.path(f"{MIX_DIR}/.mix.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy(f"Another mix of {film.id} is running.", hint="Wait for it, then mix again.") from exc
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _staged_mix(film: Film) -> Iterator[Path]:
    """Publish one complete mix directory only after every audio file and receipt has been written."""
    from slate.sound.effects.engine import _publish

    parent = film.path(MIX_DIR).parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mix-", dir=parent))
    try:
        yield staging
        _publish(staging, film.path(MIX_DIR))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def planned_stops(film: Film) -> list[dict]:
    rec = jsonio.read(film.path(f"{SCORE_DIR}/score.json"), default={}) or {}
    stops = list(rec.get("planned_stops") or [])
    directions = jsonio.read(film.path(f"{SCORE_DIR}/directions.json"), default={}) or {}
    for s in directions.get("planned_stops") or []:
        if s not in stops:
            stops.append(s)
    return stops


def plan(film: Film, *, score: str | Path | None = None, no_score: bool = False) -> dict:
    """What a mix would use, without mixing (for --dry-run and status)."""
    sp = None if no_score else score_path(film, score)
    manifest = stem_manifest(film)
    cues = vo.cues(film)
    return {"film": film.id, "sample_rate": film.sr, "samples": film.n, "seconds": round(film.duration_s, 6),
            "targets": film.targets, "voice_cues": len(cues), "score": film.rel(sp) if sp else None,
            "stems": {r: e["file"] for r, e in manifest.items()}, "palette_levels": film.palette["levels"]}


def run(film_dir: Path, *, score: str | Path | None = None, no_score: bool = False, calibrate: bool = False,
        under: float | None = None, duck: float | None = None) -> Path:
    """Mix the film. Returns the path of sound/mix/final_mix.wav (with its stems and final_mix.json beside it)."""
    film = load(film_dir)
    from slate.jobs import locks  # noqa: PLC0415
    from slate.sound.effects import engine as effects  # noqa: PLC0415
    levels = film.palette["levels"]
    music_cfg = levels.get("music") or {}
    under_lu = float(under if under is not None else music_cfg.get("under_voice_lu", 18.5))
    duck_db = float(duck if duck is not None else music_cfg.get("duck_db", 6.0))
    with locks.hold("render", owner="slate mix"), _mix_lock(film), _staged_mix(film) as out_dir:
        effects._unlocked(film, [f"{MIX_DIR}/final_mix.wav"] +
                          [f"{MIX_DIR}/stems/{k}.wav" for k in DELIVERY])
        def output_rel(path):
            return str(Path(MIX_DIR) / path.relative_to(out_dir))

        log.info(f"mixing {film.id}: {film.duration_s:.3f} s at {film.sr} Hz")
        sp = None if no_score else score_path(film, score)
        generated = (jsonio.read(film.path(f"{STEMS_DIR}/stems.json"), default={}) or {}).get("producer") == effects.PRODUCER
        effect_result = None
        sheet = effects.cue_sheet(film) if generated else None
        before = effects._snapshot(film, sheet, sp) if generated else None
        vo_stem, placed = vo.render_stem(film)
        score_arr = audio.load(sp, film.sr, film.n) if sp else None
        raw_stems, stem_files = ({}, {}) if generated else load_stems(film)
        manifest = stem_manifest(film)
        stems = {r: (v[1] if isinstance(v, tuple) else v) for r, v in raw_stems.items()}
        ducked = tuple(r for r, v in raw_stems.items() if isinstance(v, tuple))
        if not np.any(vo_stem) and score_arr is None and not stems and not generated:
            raise MissingPrerequisite(f"{film.id} has nothing to mix yet: no voice cues, no score and no effect stems.",
                                      hint="Voice the script, then `slate timing`, then `slate score assemble`.")
        trims = dict(levels.get("stem_trim_db") or {})
        calibration = None
        if generated:
            if calibrate:
                from slate.sound.effects import rendering  # noqa: PLC0415

                untreated = rendering.render_parts(sheet["cues"], film.n, film_dir=film.dir, sample_rate=film.sr)
                calibration = _calibrate(film, vo_stem, untreated["stems"])
                trims.update(calibration["stem_trim_db"])
                del untreated
            effect_result = effects.balanced(film, vo_stem, score_arr, sheet=sheet, trims=trims,
                                             under_lu=under_lu, duck_db=duck_db)
            stems, score_arr = effect_result["stems"], effect_result["score"]
            stem_files = {r: CUE_SHEET for r in stems}
            trims, ducked = {}, ()  # the family mixer already applied every stem trim and duck
        elif calibrate:
            calibration = _calibrate(film, vo_stem, stems)
            trims.update(calibration["stem_trim_db"])
        from slate.sound import balance, guard  # noqa: PLC0415
        from slate.sound.film import words_for  # noqa: PLC0415

        result = mix_arrays(
            vo_stem, stems, score_arr, film.sr, targets=film.targets, under_lu=under_lu, duck_db=duck_db,
            music_attack_s=float(music_cfg.get("attack_s", 0.08)), music_release_s=float(music_cfg.get("release_s", 0.65)),
            stem_trim_db=trims, effects_ceiling_dbtp=levels.get("effects_ceiling_dbtp"),
            linked_limit=bool(levels.get("linked_limit", True)), limiter=str(levels.get("limiter", "slate")),
            duck_depths=levels.get("duck_db") or {}, ducked_roles=ducked,
            words=words_for(film) if score_arr is not None else None,
            score_guard=guard.settings_of(balance.palette_balance(film.palette)),
            score_prepared=generated,
            prepared_score_report=effect_result["score_report"] if effect_result else None,
            voice_margin_db=levels.get("voice_margin_db"))
        report = result["report"]
        final, mix_arr, eng = result["final"], result["mix"], result["engine"]
        out_dir.mkdir(parents=True, exist_ok=True)
        mix_path = audio.write_wav(out_dir / "final_mix.wav", mix_arr, film.sr, 24)
        outputs = {"mix": output_rel(mix_path), "stems": {}, "engine_stems": {}}
        for k in DELIVERY:
            p = audio.write_wav(out_dir / "stems" / f"{k}.wav", final[k], film.sr, 24)
            outputs["stems"][k] = output_rel(p)
        vo_l = measure.integrated(final["dialogue"], film.sr) if np.any(final["dialogue"]) else None
        levels_vs = {}
        for role in eng.roles():  # one engine stem in memory at a time
            x = eng.get(role)
            p = audio.write_wav(out_dir / "engine_stems" / f"{role}.wav", x, film.sr, 24)
            outputs["engine_stems"][role] = output_rel(p)
            if vo_l is not None and np.any(x):
                levels_vs[role] = measure.lu_vs(vo_l, measure.integrated(x, film.sr))
            del x
        if effect_result is not None:
            outputs.update(_write_effect_evidence(film, out_dir, effect_result, result, mix_path))
            report["effect_guards"] = effect_result["guard_report"]
        stale = ({p.stem for p in (out_dir / "engine_stems").glob("*.wav")} - set(eng.roles())
                 if (out_dir / "engine_stems").exists() else set())
        for role in stale:
            (out_dir / "engine_stems" / f"{role}.wav").unlink(missing_ok=True)
        if vo_l is not None and np.any(final["music"]):
            levels_vs["music"] = measure.lu_vs(vo_l, measure.integrated(final["music"], film.sr))
        stops = planned_stops(film)
        music_check = gap.check(final["music"], film.sr, stops) if np.any(final["music"]) else None
        if music_check is not None:
            jsonio.write_atomic(out_dir / "music_check.json",
                                jsonio.mark_generated({"kind": "music_check", "stem": outputs["stems"]["music"],
                                                       **music_check}, "slate mix"))
        else:
            (out_dir / "music_check.json").unlink(missing_ok=True)  # an earlier mix's check doesn't describe this one
        report.update({
            "kind": "mix", "film": film.id, "made_at": gates.now_iso(),
            "empty_stems": empty_by_design(placed, sp, stems),
            "inputs": {"voice": {"cues": len(placed), "placed": placed}, "score": film.rel(sp) if sp else None,
                       "stems": stem_files, "stems_manifest": {r: dict(e) for r, e in manifest.items()}},
            "levels_vs_voice_lu": levels_vs, "palette": {"brand": film.palette.get("brand"),
                                                         "from_brand": film.palette.get("from_brand"),
                                                         "lu_vs_voice_targets": levels.get("lu_vs_voice")},
            "calibration": calibration, "stems_sum_max_abs": quantized_sum_error(final, mix_arr),
            "music_check": {k: music_check[k] for k in ("pass", "quietest_outside_stops_dbfs", "failures")} if music_check else None,
            "outputs": outputs, "notes": film.notes,
        })
        jsonio.write_atomic(out_dir / "final_mix.json", jsonio.mark_generated(report, "slate mix"))
        if calibration:
            jsonio.write_atomic(out_dir / "calibration.json", jsonio.mark_generated(calibration, "slate mix --calibrate"))
        inputs = ["timing/shots.json", "timing/vo_cues.json"] + ([film.rel(sp)] if sp else []) + list(stem_files.values())
        if generated:
            effects._unchanged(film, before, sheet=sheet, score=sp)
    record_lock(film, outputs["mix"], producer="slate mix", inputs=[i for i in inputs if film.path(i).exists()],
                tracked=mix_tracked(film))
    return film.path(f"{MIX_DIR}/final_mix.wav")


def _write_effect_evidence(film: Film, out_dir: Path, effects: dict, master: dict, mix_path: Path) -> dict:
    """Save additive families and exact per-hit before/after deltas under the same final master transform."""
    from slate.sound.effects import balance_mix

    eng = master["engine"]
    outputs = {"family_stems": {}}
    total = np.zeros_like(master["mix"])
    for family in balance_mix.FAMILIES:
        if family == "voice":
            samples = master["final"]["dialogue"]
        else:
            samples = np.zeros_like(total)
            for (fam, stem), part in effects["parts"].items():
                if fam == family:
                    bus = "effects" if stem in EFFECTS_ROLES else "music" if stem in MUSIC_ROLES else "ambience"
                    samples += eng.transform(part, bus)
            if family == "score" and effects["score"] is not None:
                samples += eng.transform(effects["score"], "music")
        total += samples
        path = audio.write_wav(out_dir / "family_stems" / f"{family}.wav", samples, film.sr, 24)
        outputs["family_stems"][family] = str(Path(MIX_DIR) / path.relative_to(out_dir))
    error = float(np.max(np.abs(total - master["mix"])))
    if error > 1e-9:
        raise Refused(f"The effect family stems do not sum to this master (maximum error {error:.3g}).")
    rows = []
    for source in effects["ride_rows"]:
        row = {k: v for k, v in source.items() if k != "cue"}
        if row["family"] == "accents" and float(row["depth_db"]) < 0 and not row["pinned"]:
            a, before = effects["solo_hit"](row["hit"], baseline=True)
            b, after = effects["solo_hit"](row["hit"])
            start, end = min(a, b), max(a+len(before), b+len(after))
            delta = np.zeros((end-start, 2))
            delta[a-start:a-start+len(before)] += before
            delta[b-start:b-start+len(after)] -= after
            stem = source["cue"].get("stem", "fx")
            bus = "music" if stem in MUSIC_ROLES else "ambience" if stem in AMBIENCE_ROLES else "effects"
            start, delta = eng.transform_window(start, delta, bus, film.n)
            name = jsonio.sha256_obj(row["hit"])[:20] + ".wav"
            path = audio.write_wav(out_dir / "ride_deltas" / name, delta, film.sr, 32)
            row["delta"] = {"file": str(Path(MIX_DIR) / path.relative_to(out_dir)),
                            "sha256": jsonio.sha256(path), "start_sample": start}
        rows.append(row)
    jsonio.write_atomic(out_dir / "rides.json", jsonio.mark_generated(
        {"rides": rows, "pinned": [{"hit": r["hit"], "t": r["t"], "end": r["end"],
                                    "why": "an approved reference's exact ride"} for r in rows if r["pinned"]],
         "mix_sha256": jsonio.sha256(mix_path), "family_sum_max_abs": error}, "slate mix"))
    jsonio.write_atomic(out_dir / "balance_used.json", {**effects["config"], "rides": effects["rides"]})
    jsonio.write_atomic(out_dir / "cue_sheet.json", {**effects["sheet"], "window": [0, film.duration_s]})
    jsonio.write_atomic(out_dir / "render_log.json", jsonio.mark_generated(
        {"cue_sheet_sha256": jsonio.sha256(film.source(CUE_SHEET)), "mix_sha256": jsonio.sha256(mix_path),
         "sample_rate": film.sr, "cues": effects["applied"],
         "suppressed_by_score": effects["suppressed_by_score"]}, "slate mix"))
    def transform_hit(start, samples, cue):
        stem = cue.get("stem", "fx")
        bus = "effects" if stem in EFFECTS_ROLES else "music" if stem in MUSIC_ROLES else "ambience"
        return eng.transform_window(start, samples, bus, film.n)
    sync = effects["measure_sync"](transform=transform_hit,
                                    sync_frames=float(film.palette["rules"].get("sync_frames", 1)))
    jsonio.write_atomic(out_dir / "effects_sync.json", {**sync, "scope": "master",
                         "mix_sha256": jsonio.sha256(mix_path),
                         "cue_sheet_sha256": jsonio.sha256(film.source(CUE_SHEET))})
    return outputs


# What the mix answers to besides the files it reads: the effect cue sheet (spotted against the palette) and the brand's
# sound palette itself. A palette change re-spots every effect and remakes the mix (the films' rule); recording both
# in the mix's own lock entry lets `slate status` and `slate make` see it by hash from the first mix on.
CUE_SHEET = "sound/sound_cues.json"


def mix_tracked(film: Film) -> list[str]:
    """The inputs slate.state.lock hashes for the mix: sound/sound_cues.json (present or not yet) and, when the film
    names a brand, brand:<name>#sound."""
    brand = str(film.data.get("brand") or "").strip()
    return [CUE_SHEET] + ([f"brand:{brand}#sound"] if brand else [])


def listening_file(film_dir: Path, *, n: int = 9) -> dict:
    """The listening file of the hardest-ridden accents, each as mixed before its ride and now (slate.sound.listen)."""
    from slate.sound import listen  # noqa: PLC0415

    return listen.ridden_accents(film_dir, n=n)


def empty_by_design(placed: list, score: Path | None, stems: dict) -> dict[str, str]:
    """The delivery stems this film has nothing for, and why. A score-and-voice film has no effects or ambience: those
    stems are still written (silent), so every delivery keeps its four stems, and this record is how a check tells a
    stem that's empty on purpose from one that came out silent by mistake."""
    def has(roles) -> bool:  # the effects engine writes every role's stem; a silent one feeds nothing
        return any(r in stems and np.any(stems[r]) for r in roles)

    fed = {"dialogue": (bool(placed), "no voice cues in this film"),
           "effects": (has(EFFECTS_ROLES), "no effect stems in this film"),
           "ambience": (has(AMBIENCE_ROLES), "no ambience stems in this film"),
           "music": (score is not None or has(MUSIC_ROLES), "no score and no tonal stems in this film")}
    return {k: why for k, (has, why) in fed.items() if not has}


def _calibrate(film: Film, vo_stem: np.ndarray, stems: dict[str, np.ndarray]) -> dict:
    """Trims that put each stem with a target at its palette level against the voice (measured, integrated LU)."""
    targets = film.palette["levels"].get("lu_vs_voice") or {}
    if not np.any(vo_stem):
        raise Refused("Calibration measures the effects against the voice, and this film has no voice yet.")
    vo_l = measure.integrated(vo_stem, film.sr)
    trims, measured = {}, {}
    for role, target in targets.items():
        if role not in stems or not np.any(stems[role]):
            continue
        rel = measure.integrated(stems[role], film.sr) - vo_l
        measured[role] = round(rel, 2)
        trims[role] = round(float(target) - rel, 2)
    return {"stem_trim_db": trims, "measured_lu_vs_voice": measured, "targets": targets,
            "keep": "Put stem_trim_db in the brand's sound.palette.levels to use these trims on every film."}


def output_paths(film_dir: Path) -> dict[str, Path]:
    d = Path(film_dir) / MIX_DIR
    return {"mix": d / "final_mix.wav", "report": d / "final_mix.json", "music_check": d / "music_check.json",
            **{k: d / "stems" / f"{k}.wav" for k in DELIVERY}}


def exists(film_dir: Path) -> bool:
    return all(p.exists() for k, p in output_paths(film_dir).items() if k != "music_check")


def remove_scratch(film_dir: Path) -> None:
    for p in Path(film_dir, MIX_DIR).glob(".*.tmp"):
        with contextlib.suppress(OSError):
            os.unlink(p)
