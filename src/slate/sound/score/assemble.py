"""`slate score assemble`: the parts' best takes placed on their landings, joined, repaired, checked.

1. Choose each part's take: take QA first (seconds of silence or noise), then how well it fits, measured: the music
   must reach its hand-over (a take that runs out early is marked down by the seconds it's missing); the film's first
   part must be at level on frame one; a part that stops dead must still be at level where it's cut; a lift or an
   arrival needs a clear onset on its boundary. `--pick act2=s21` forces a take.
2. Place it: every part was composed with a pre-roll, so its first real section starts exactly `pre_roll_s` into the
   take; that point goes on the part's landing. A bloom, lift or arrival is nudged (at most 0.3 s) so the take's own
   strongest onset near the boundary lands on the beat.
3. Join: start (the pre-roll cut at frame one, a 20 ms fade so it doesn't click), act (an equal-power crossfade over
   1.2 s centred on the cut), bloom (the bloom enters in 25 ms exactly on its landing; what came before fades in 120 ms),
   lift and arrive (short equal-power handovers), stop (the part stops dead, a 120 ms fade, before the next landing).
4. Repair holes in place, with the music's own material:
   - a take that runs out before its hand-over has its last sustain held (a phase-vocoder freeze, -2 dB, easing down
     1 dB a second) from just before it fell, crossfaded in as the real chord decays;
   - a part whose take enters late gets the previous part's music carried over until it's in (equal power, 0.8 s);
   - the final part's last chord is held to the last frame, and the score fades over its last 2 s to reach silence on
     the final frame, not before it.
5. Level each part on its own body (-16 LUFS, plus any shape in directions.json), keep the peak under 0.95.
6. Check: take QA on the whole score, the landings measured (every bloom, lift and arrival within a frame of its beat),
   and the gap check on the music as the mix will set it (the palette's level under the voice, 18.5 LU to start,
   ducked 6 dB under words, times the mix's loudness gain): no window under -60 dBFS outside the planned stops.

Writes sound/score/score.wav (48 kHz / 24-bit, exactly the film's length) and sound/score/score.json.
"""

from __future__ import annotations

import contextlib
import fcntl
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np

from slate import gates, jsonio, log
from slate.errors import Busy, MissingPrerequisite, UsageError
from slate.sound import audio, dsp, gap, measure, vo
from slate.sound.film import MIX_DIR, SCORE_DIR, Film, load, record_lock
from slate.sound.score import qa
from slate.sound.score.compose import takes_for
from slate.sound.score.plan import load_plan, plan_blocks

JOIN_IN = {"start": (0.0, 0.02), "act": (-0.6, 0.6), "bloom": (0.0, 0.025), "lift": (-0.05, 0.10),
           "arrive": (-0.08, 0.12)}
JOIN_OUT_PREVIOUS = {"act": (-0.6, 0.6), "bloom": (-0.12, 0.0), "lift": (-0.05, 0.35), "arrive": (-0.08, 0.6)}
STOP_FADE_S = 0.12
AFTER_STOP_IN_S = 0.3
NUDGE_S = 0.3
LATE_XFADE_S = 0.8
END_FADE_S = 2.0
SEED = 20260929


@dataclass
class Choice:
    part: dict
    path: Path
    y: np.ndarray
    env: np.ndarray
    offset: float
    shift: float
    qa: dict
    penalty: float
    checks: dict
    gain_db: float = 0.0
    alternatives: list = field(default_factory=list)
    repairs: list = field(default_factory=list)
    win_in: tuple = (0.0, 0.0)
    win_out: tuple = (0.0, 0.0)


def take_slice(y: np.ndarray, a: float, b: float, sr: int) -> np.ndarray:
    """Samples a..b (take time, seconds) of a take, zero-padded outside it."""
    i0, i1 = int(round(a * sr)), int(round(b * sr))
    out = np.zeros((max(0, i1 - i0), 2))
    s0, s1 = max(0, i0), min(len(y), i1)
    if s1 > s0:
        out[s0 - i0:s1 - i0] = y[s0:s1]
    return out


def rms_db(y: np.ndarray, a: float, b: float, sr: int) -> float:
    seg = y[max(0, int(a * sr)):max(0, int(b * sr))]
    return 20 * math.log10(math.sqrt(float(np.mean(seg ** 2))) + 1e-12) if len(seg) else -120.0


def _window_levels(y: np.ndarray, sr: int, a: float, b: float, win_s: float = 0.5, step_s: float = 0.05):
    """(window starts, RMS dB) for win_s windows every step_s from take time a while a window fits before b."""
    p = np.concatenate([[0.0], np.cumsum(np.mean(np.asarray(y, dtype=np.float64) ** 2, axis=1))])
    w = int(win_s * sr)
    xs = np.arange(max(a, 0.0), max(a, b) - 1e-9, step_s)
    i0 = (xs * sr).astype(int)
    ok = i0 + w <= len(p) - 1
    xs, i0 = xs[ok], i0[ok]
    ms = (p[i0 + w] - p[i0]) / w
    return xs, 10 * np.log10(np.maximum(ms, 1e-24))


@contextlib.contextmanager
def _lock(film: Film) -> Iterator[None]:
    p = film.path(f"{SCORE_DIR}/.assemble.lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy("Another score assembly is running for this film.") from exc
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _drift(film: Film, take: Path, part: dict) -> dict | None:
    """How far the part's sections (or chunks) have moved since this take was composed (from the plan saved beside
    it). A take composed from the other plan shape is compared by its blocks' names just the same."""
    snap = take.with_name(f"{take.stem}.plan.json")
    if not snap.exists():
        return None
    then = plan_blocks(jsonio.read(snap))
    now = plan_blocks(part["composition_plan"])

    def starts(blocks: list[tuple[str, int]]) -> dict:
        out, at = {}, 0
        for name, ms in blocks:
            out[name] = at
            at += int(ms)
        out["(end)"] = at
        return out

    a, b = starts(then), starts(now)
    common = [k for k in b if k in a]
    moved = {k: round((b[k] - a[k]) / 1000, 3) for k in common if b[k] != a[k]}
    worst = max((abs(v) for v in moved.values()), default=0.0)
    return {"moved_s": moved, "worst_s": worst, "recompose_advised": worst > 1.0}


def _choose(film: Film, part: dict, is_last: bool, pick: str | None) -> Choice:
    sr = film.sr
    cands = takes_for(film, part["id"])
    if pick:
        cands = [c for c in cands if f"_{pick}." in c.name or c.stem.endswith(f"_{pick}")] or cands
    if not cands:
        raise MissingPrerequisite(f"Score part {part['id']} has no takes yet.", hint="Run `slate score compose`.")
    pre = float(part["pre_roll_s"])
    options = []
    for path in cands:
        y = audio.load(path, sr)
        rep = qa.analyse(y, sr)
        e = dsp.env_db(y, sr)
        offset = float(part["landing_s"]) - pre
        shift, checks = 0.0, {}
        if part["join"] in ("lift", "arrive", "bloom"):
            ons = dsp.flux_onsets(y, sr, pre - NUDGE_S, pre + NUDGE_S)
            if ons:
                tt, z = max(ons, key=lambda o: o[1])
                shift = pre - tt
                checks["boundary_onset"] = {"take_t": round(tt, 3), "strength": round(z, 1)}
            else:
                checks["boundary_onset"] = None
        offset += shift
        entry = dsp.entry_s(e)
        checks["entry_s"] = round(entry, 3)
        pen = 0.0
        if part["join"] == "start":
            late = max(0.0, entry - pre - 0.25)
            checks["late_at_first_frame_s"] = round(late, 3)
            pen += late
        if is_last:
            must = film.duration_s - 0.3 - offset
        elif part["end"] == "stop":
            must = float(part["hand_over_s"]) - float(part["stop_before_s"]) - offset
        else:
            must = float(part["hand_over_s"]) + 0.35 - offset
        missing = max(0.0, must - dsp.last_music_s(e))
        checks["missing_music_s"] = round(missing, 2)
        pen += missing
        if part["end"] == "stop" and not is_last:
            cut_t = float(part["hand_over_s"]) - float(part["stop_before_s"]) - offset
            body = rms_db(y, pre, max(pre + 1.0, cut_t - 0.5), sr)
            at_cut = rms_db(y, cut_t - 0.5, cut_t, sr)
            drop = max(0.0, body - 6.0 - at_cut)
            checks["level_at_cut_db"] = round(at_cut, 1)
            pen += drop / 6
        if part["join"] in ("lift", "arrive") and not checks.get("boundary_onset"):
            pen += 1.0
        options.append(((rep["score"], round(pen, 3), path.name), Choice(part, path, y, e, offset, shift, rep, round(pen, 3), checks)))
    options.sort(key=lambda o: o[0])
    best = options[0][1]
    best.alternatives = [{"file": film.rel(o[1].path), "qa_score": o[0][0], "fit_penalty": o[0][1], "checks": o[1].checks}
                         for o in options[1:]]
    return best


def assemble(film_dir: Path, picks: dict[str, str] | None = None, *, out: str | Path | None = None,
             repairs: bool = True) -> dict:
    """Assemble the score. Returns the assembly record (also written to sound/score/score.json)."""
    film = load(film_dir)
    plan = load_plan(film)
    sr, n, end = film.sr, film.n, film.duration_s
    picks = dict(picks or {})
    unknown = [p for p in picks if p not in {x["id"] for x in plan["parts"]}]
    if unknown:
        raise UsageError(f"--pick names parts that aren't in the plan: {', '.join(unknown)}.")
    with _lock(film):
        parts = plan["parts"]
        chosen = [_choose(film, p, i == len(parts) - 1, picks.get(p["id"])) for i, p in enumerate(parts)]
        level = float(plan.get("level_lufs", -16.0))
        for c in chosen:
            body = take_slice(c.y, float(c.part["landing_s"]) - c.offset, float(c.part["hand_over_s"]) - c.offset, sr)
            ref = body if len(body) > sr else c.y
            c.gain_db = round(level + float(c.part.get("shape_db", 0.0)) - measure.integrated(ref, sr), 3)
        # windows
        for i, c in enumerate(chosen):
            land = float(c.part["landing_s"])
            a, b = JOIN_IN[c.part["join"]]
            # a start is on frame one, or later when the film opens out of silence (directions land it there)
            c.win_in = (land, land + b) if c.part["join"] == "start" else (land + a, land + b)
        for i, c in enumerate(chosen):
            if i + 1 == len(chosen):
                c.win_out = (end, end)
                continue
            nx = chosen[i + 1]
            nland = float(nx.part["landing_s"])
            if c.part["end"] == "stop":
                cut = nland - float(c.part["stop_before_s"])
                c.win_out = (cut - STOP_FADE_S, cut)
                # after a held breath the next part comes in on its landing, never into the silence
                nx.win_in = (nland, nland + (JOIN_IN["bloom"][1] if nx.part["join"] == "bloom" else AFTER_STOP_IN_S))
                continue
            a, b = JOIN_OUT_PREVIOUS[nx.part["join"]]
            c.win_out = (nland + a, nland + b)
            n_on = nx.offset + dsp.entry_s(nx.env)
            if repairs and nx.part["join"] == "act" and n_on > nx.win_in[1] + 0.05:
                # the next take enters late: carry this part's music until it's in, handing over equal power
                nx.win_in = (n_on, n_on + LATE_XFADE_S)
                c.win_out = (n_on, n_on + LATE_XFADE_S)
                c.repairs.append(f"carries on to {n_on + LATE_XFADE_S:.2f} s: {nx.part['id']}'s take only reaches its "
                                 f"level at {n_on:.2f} s")
                nx.repairs.append(f"fades in over {LATE_XFADE_S:g} s from {n_on:.2f} s (its take enters late)")
        mix = np.zeros((n, 2))

        def place(sig: np.ndarray, t0: float) -> None:
            s = int(round(t0 * sr))
            a0 = max(0, -s)
            m = min(len(sig) - a0, n - max(s, 0))
            if m > 0:
                mix[max(s, 0):max(s, 0) + m] += sig[a0:a0 + m]

        for i, c in enumerate(chosen):
            last = i + 1 == len(chosen)
            f0, f1 = c.win_in[0], (end if last else c.win_out[1])
            seg = take_slice(c.y, f0 - c.offset, f1 - c.offset, sr) * dsp.db(c.gain_db)
            g = dsp.ramp(len(seg), f0, c.win_in[0], c.win_in[1], True, sr)
            if not last:
                g = g * dsp.ramp(len(seg), f0, c.win_out[0], c.win_out[1], False, sr)
            seg = seg * g[:, None]
            if repairs and c.part["end"] != "stop":
                seg = _hold_if_running_out(c, seg, f0, f1, last, end, sr, place)
            place(seg, f0)
        # the end: settle and reach silence on the final frame, not before it
        fade = float(plan.get("end_fade_s", END_FADE_S))
        if fade > 0:
            e0 = max(0, int((end - fade) * sr))
            tt = (n - np.arange(e0, n)) / sr
            mix[e0:] *= np.sqrt(np.clip(tt / fade, 0, 1))[:, None]
        pk = float(np.max(np.abs(mix))) if mix.size else 0.0
        if pk > 0.95:
            mix *= 0.95 / pk
        out_path = film.path(out) if out else film.path(f"{SCORE_DIR}/score.wav")
        audio.write_wav(out_path, mix, sr, 24)
        record = _record(film, plan, chosen, mix, out_path)
        jsonio.write_atomic(film.path(f"{SCORE_DIR}/score.json") if not out else out_path.with_suffix(".json"),
                            jsonio.mark_generated(record, "slate score assemble"))
        record_lock(film, film.rel(out_path), producer="slate score assemble",
                    inputs=[f"{SCORE_DIR}/plan.json", *sorted({film.rel(c.path) for c in chosen})])
    return record


def _hold_if_running_out(c: Choice, seg: np.ndarray, f0: float, f1: float, last: bool, end: float, sr: int,
                         place) -> np.ndarray:
    """If the take falls away before it hands over, hold its last sustain from there (C's rule, the films' fix)."""
    y = c.y
    pre = float(c.part["pre_roll_s"])
    end_take = f1 - c.offset
    wins = [rms_db(y, x, x + 0.5, sr) for x in np.arange(pre, pre + 5.0, 0.5) if x + 0.5 <= len(y) / sr]
    if not wins:
        return seg
    body = float(np.median(wins))
    hand = (end if last else c.win_out[0]) - c.offset
    # where the music ends for good: the last 0.5 s window still within fall_db of the body, from which the take never
    # comes back before the hand-over (a musical dip that recovers is left alone). The final part looks only at its last
    # 6 s and holds its chord once it falls 8 dB (the end card's chord holds at full); elsewhere, running out is 20 dB.
    fall_db = 8.0 if last else 20.0
    xs, lv = _window_levels(y, sr, max(pre + 2.0, hand - 6.0) if last else pre + 2.0, end_take)
    if not len(xs):
        return seg
    above = np.flatnonzero(lv >= body - fall_db)
    if len(above) and above[-1] == len(xs) - 1:
        return seg  # still at level when it hands over
    t_fall = float(xs[above[-1] + 1]) if len(above) else float(xs[0])
    if t_fall >= end_take - 0.3 or c.offset + t_fall >= (end if last else c.win_out[0]):
        return seg
    src = take_slice(y, t_fall - 0.8, t_fall - 0.2, sr) * dsp.db(c.gain_db)
    h0 = c.offset + t_fall - 0.4
    held = dsp.freeze(src, f1 - h0, sr, SEED + 99 + int(h0)) * dsp.db(-2.0)
    tt = np.arange(len(held)) / sr
    held = held * dsp.db(-1.0 * np.maximum(0.0, tt - 0.6))[:, None]
    held = held * dsp.ramp(len(held), h0, h0, h0 + 0.6, True, sr)[:, None]
    if not last:
        held = held * dsp.ramp(len(held), h0, c.win_out[0], c.win_out[1], False, sr)[:, None]
    seg = seg * dsp.ramp(len(seg), f0, h0, h0 + 0.6, False, sr)[:, None]
    place(held, h0)
    what = "its final chord held to the last frame" if last else f"its last chord held into the hand-over at {c.win_out[0]:.2f} s"
    c.repairs.append(f"the take falls away at {c.offset + t_fall:.2f} s: {what} (phase-vocoder freeze from {h0:.2f} s, "
                     "-2 dB, easing 1 dB a second)")
    return seg


def _predicted_gain_db(film: Film, voice: np.ndarray | None, score: np.ndarray) -> tuple[float, str]:
    fm = jsonio.read(film.path(f"{MIX_DIR}/final_mix.json"), default={}) or {}
    if fm.get("gain_db") is not None:
        return float(fm["gain_db"]), "the last mix's loudness gain"
    target = float(film.targets["lufs"])
    if voice is not None and np.any(voice):
        return target - 0.4 - measure.integrated(voice, film.sr), "predicted: the voice carries the mix, about 0.4 LU under the target"
    return target - measure.integrated(score, film.sr), "predicted: the score alone at the target"


def _record(film: Film, plan: dict, chosen: list[Choice], mix: np.ndarray, out_path: Path) -> dict:
    sr = film.sr
    whole_qa = qa.analyse(mix, sr)
    cues = vo.cues(film)
    voice = vo.render_stem(film)[0] if cues else None
    gain_db, gain_why = _predicted_gain_db(film, voice, mix)
    music_cfg = film.palette["levels"].get("music") or {}
    predicted = gap.predict_music_stem(mix, voice, sr, under_lu=float(music_cfg.get("under_voice_lu", 18.5)),
                                       duck_db=float(music_cfg.get("duck_db", 6.0)), gain_db=gain_db,
                                       attack_s=float(music_cfg.get("attack_s", 0.08)),
                                       release_s=float(music_cfg.get("release_s", 0.65)))
    stops = list(plan.get("planned_stops") or [])
    gap_res = gap.check(predicted, sr, stops)
    landings = []
    for c in chosen:
        if c.part["join"] not in ("bloom", "lift", "arrive"):
            continue
        land = float(c.part["landing_s"])
        ons = dsp.flux_onsets(mix, sr, land - NUDGE_S, land + NUDGE_S, min_z=6.0)
        if ons:
            t_on = min(ons, key=lambda o: abs(o[0] - land))[0]
            delta = t_on - land
            landings.append({"part": c.part["id"], "join": c.part["join"], "landing_s": round(land, 3),
                             "onset_s": round(t_on, 3), "delta_ms": round(delta * 1000, 1),
                             "within_frame": abs(delta) <= film.frame_s + 0.005})
        else:
            landings.append({"part": c.part["id"], "join": c.part["join"], "landing_s": round(land, 3), "onset_s": None,
                             "delta_ms": None, "within_frame": False})
    parts = {}
    for c in chosen:
        parts[c.part["id"]] = {
            "file": film.rel(c.path), "join": c.part["join"], "end": c.part["end"],
            "landing_s": round(float(c.part["landing_s"]), 3), "hand_over_s": round(float(c.part["hand_over_s"]), 3),
            "section_starts_at_film_s": round(c.offset + float(c.part["pre_roll_s"]), 3), "nudged_s": round(c.shift, 3),
            "plays": [round(c.win_in[0], 3), round(c.win_out[1], 3)], "fade_in": [round(x, 3) for x in c.win_in],
            "fade_out": [round(x, 3) for x in c.win_out], "gain_db": c.gain_db,
            "qa": {"problems": c.qa["problems"], "score": c.qa["score"]}, "fit_penalty": c.penalty, "checks": c.checks,
            "repairs": c.repairs, "drift_since_composed": _drift(film, c.path, c.part), "alternatives": c.alternatives}
    ok = gap_res["pass"] and not whole_qa["problems"] and all(l["within_frame"] for l in landings)
    return {"kind": "assembly", "film": film.id, "file": film.rel(out_path), "sample_rate": sr, "samples": len(mix),
            "seconds": round(len(mix) / sr, 6), "model_id": plan.get("model_id"), "parts": parts,
            "planned_stops": stops, "landings": landings, "assembled_qa": whole_qa,
            "music_gap_check": {**{k: v for k, v in gap_res.items() if k != "windows"}, "predicted_for_music_stem": True,
                                "predicted_mix_gain_db": round(gain_db, 2), "gain_from": gain_why},
            "licence": plan.get("licence"), "made_at": gates.now_iso(), "pass": ok,
            "recompose_advised": [k for k, v in parts.items() if (v.get("drift_since_composed") or {}).get("recompose_advised")]}


def describe(record: dict) -> str:
    lines = []
    for pid, p in record["parts"].items():
        lines.append(f"{pid:8s} {Path(p['file']).name:26s} {p['join']:6s} landing {p['landing_s']:8.3f}  plays "
                     f"{p['plays'][0]:8.3f}-{p['plays'][1]:8.3f}  gain {p['gain_db']:+5.1f} dB  qa {p['qa']['score']}")
        lines += [f"         repair: {r}" for r in p["repairs"]]
    g = record["music_gap_check"]
    lines.append(f"gap check (music as the mix will set it, 0.5 s windows, < {g['threshold_dbfs']:g} dBFS outside "
                 f"{len(record['planned_stops'])} planned stop(s)): " + ("PASS" if g["pass"] else f"FAIL {g['failures'][:3]}")
                 + f"; quietest {g['quietest_outside_stops_dbfs']} dBFS")
    q = record["assembled_qa"]
    lines.append(f"take QA on the whole score: {'clean' if not q['problems'] else q['problems']}")
    for l in record["landings"]:
        lines.append(f"landing {l['part']} ({l['join']}) at {l['landing_s']} s: onset {l['onset_s']} "
                     f"({l['delta_ms']} ms){'' if l['within_frame'] else '  NOT within a frame'}")
    return "\n".join(lines)
