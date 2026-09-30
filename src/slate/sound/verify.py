"""`slate verify`: the sound gates, measured on the delivered mix. Nothing here is judged by ear.

Gate (name)            What passes
sound.loudness         the mix at the film's target (-14 LUFS ±0.5) and at or under its ceiling (-1 dBTP), measured
                       with Slate's BS.1770-4 meter; ffmpeg's ebur128 cross-checks it when installed; 48 kHz/24-bit,
                       exactly the film's length
sound.stems_sum        dialogue + effects + ambience + music equal the mix within 1e-6
sound.gap              no music window (0.5 s RMS) under -60 dBFS outside the planned stops
sound.voice_sync       every take found in the dialogue stem within half a millisecond of its cue
sound.cue_sync         every effect's anchor (measured on the effect on its own; a gesture's overlapping notes each on
                       their own) within one frame of its beat
sound.repeats          one deliberate sound per kind of interaction, no sound serving two kinds, no identical repeat
sound.glides           no glide or slide-up in the tonal effects (where the brand's palette rules them out)
sound.drone            no room-tone bed under the effects (where the palette rules it out)
sound.levels           each effect stem at its palette level against the voice (UI about 17 LU and effects about 11 LU
                       under it, from proof 2), within the palette's tolerance
sound.reference        the same levels against the brand's approved sound reference (UNCHECKED until onboarding
                       approves one)
sound.clarity          every word of the voice at least 3 dB over everything else in the speech band, and no effect
                       louder than the voice
sound.music_licence    REVIEW for broadcast use (film, TV, radio) until the film records an Eleven Music Enterprise
                       licence; PASS otherwise

Only PASS counts as passed. A gate that can't run says UNCHECKED and why; it's never a quiet pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from slate import gates, jsonio
from slate.errors import MissingPrerequisite
from slate.sound import audio, checks, dsp, measure, vo
from slate.sound.film import CUES_FILE, MIX_DIR, SCORE_DIR, STEMS_DIR, Film, load, words_for
from slate.sound.mix import DELIVERY, planned_stops, stem_manifest

SUM_TOLERANCE = 1e-6
VOICE_EXACT_MS = 0.5
VOICE_MIN_CORR = 0.95
SEARCH_MS = 50.0

GATE_NAMES = ("sound.loudness", "sound.stems_sum", "sound.gap", "sound.voice_sync", "sound.cue_sync", "sound.repeats",
              "sound.glides", "sound.drone", "sound.levels", "sound.reference", "sound.clarity", "sound.music_licence")


class _Ctx:
    """The delivered mix, loaded once and shared by the gates."""

    def __init__(self, film: Film):
        self.film = film
        d = film.path(MIX_DIR)
        self.mix_path = d / "final_mix.wav"
        self.report_path = d / "final_mix.json"
        if not self.mix_path.exists():
            raise MissingPrerequisite(f"{film.id} hasn't been mixed yet (no {film.rel(self.mix_path)}).",
                                      hint="Run `slate mix` first.")
        self.report = jsonio.read(self.report_path, default={}) or {}
        self.stem_paths = {k: d / "stems" / f"{k}.wav" for k in DELIVERY}
        self._cache: dict[str, np.ndarray] = {}
        self.engine_stems = {p.stem: p for p in sorted((d / "engine_stems").glob("*.wav"))} if (d / "engine_stems").exists() else {}

    def read(self, path: Path) -> np.ndarray:
        key = str(path)
        if key not in self._cache:
            x, _ = audio.read_wav(path)
            # float32 is exact for 24-bit stems and halves what a feature-length film holds in memory
            self._cache[key] = audio.to_stereo(x).astype(np.float32)
        return self._cache[key]

    def stem(self, name: str) -> np.ndarray | None:
        p = self.stem_paths[name]
        return self.read(p) if p.exists() else None

    def inputs(self, *rels: str | Path) -> dict:
        return gates.inputs_for(self.film.dir, [r for r in rels if self.film.path(r).exists()])


def _engine() -> dict:
    return gates.engine("slate sound", measure.engine_info()["version"], meter=measure.ENGINE["name"])


# ---------------------------------------------------------------------------------------------------------------
# Gates


def gate_loudness(c: _Ctx) -> gates.GateResult:
    film = c.film
    t = film.targets
    info = audio.wav_info(c.mix_path)
    ours = measure.loudness(c.mix_path)
    cross = measure.cross_check(c.mix_path, ours)
    i, tp = ours["integrated_lufs"], ours["true_peak_dbtp"]
    fmt_ok = info.sample_rate == film.sr and info.bits == 24 and info.kind == "pcm"
    length_ok = info.frames == film.n
    ok = abs(i - t["lufs"]) <= t["tol"] and tp <= t["true_peak"] + 1e-6 and fmt_ok and length_ok
    engine = gates.engine(measure.ENGINE["name"], measure.engine_info()["version"],
                          cross_check=(cross.get("ffmpeg") or {}).get("engine"))
    details = {"measured": {k: ours[k] for k in ("integrated_lufs", "true_peak_dbtp", "lra_lu", "sample_peak_dbfs")},
               "target": t, "format": {"sample_rate": info.sample_rate, "bits": info.bits, "kind": info.kind,
                                       "frames": info.frames, "expected_frames": film.n}, "cross_check": cross}
    summary = f"{i:.1f} LUFS, {tp:.1f} dBTP (target {t['lufs']:g} ±{t['tol']:g}, ≤ {t['true_peak']:g})"
    evidence = [film.rel(c.mix_path), f"slate meter: {i:.2f} LUFS, {tp:.2f} dBTP"]
    if cross.get("available"):
        evidence.append(f"ffmpeg ebur128: {cross['ffmpeg']['integrated_lufs']} LUFS, {cross['ffmpeg']['true_peak_dbtp']} dBTP")
    if not fmt_ok or not length_ok:
        summary += f"; file is {info.sample_rate} Hz/{info.bits}-bit, {info.frames} frames (want {film.sr}/24, {film.n})"
    inputs = c.inputs(c.mix_path)
    if ok and cross.get("available") and not cross.get("agree"):
        return gates.GateResult.review("sound.loudness", summary + "; ffmpeg's meter disagrees, so a person should look",
                                       evidence=evidence, engine=engine, inputs=inputs, details=details)
    make = gates.GateResult.pass_ if ok else gates.GateResult.fail
    return make("sound.loudness", summary, evidence=evidence, engine=engine, inputs=inputs, details=details)


def gate_stems_sum(c: _Ctx) -> gates.GateResult:
    missing = [k for k, p in c.stem_paths.items() if not p.exists()]
    if missing:
        return gates.GateResult.fail("sound.stems_sum", f"missing delivery stems: {', '.join(missing)}",
                                     evidence=[str(c.film.rel(c.mix_path))], engine=_engine())
    info = audio.wav_info(c.mix_path)
    worst, block = 0.0, 480000
    for start in range(0, info.frames, block):
        m, _ = audio.read_wav(c.mix_path, start, block)
        total = sum(audio.read_wav(p, start, block)[0] for p in c.stem_paths.values())
        if total.shape != m.shape:
            return gates.GateResult.fail("sound.stems_sum", "the stems and the mix have different lengths or channels",
                                         engine=_engine(), inputs=c.inputs(c.mix_path, *c.stem_paths.values()))
        worst = max(worst, float(np.max(np.abs(total - m))) if m.size else 0.0)
    ok = worst <= SUM_TOLERANCE
    make = gates.GateResult.pass_ if ok else gates.GateResult.fail
    return make("sound.stems_sum", f"the four stems sum to the mix within {worst:.2e} (limit {SUM_TOLERANCE:.0e})",
                evidence=[c.film.rel(p) for p in [c.mix_path, *c.stem_paths.values()]], engine=_engine(),
                inputs=c.inputs(c.mix_path, *c.stem_paths.values()), details={"max_abs_difference": worst})


def gate_gap(c: _Ctx) -> gates.GateResult:
    from slate.sound import gap  # noqa: PLC0415

    music = c.stem("music")
    film = c.film
    rels = [c.stem_paths["music"], f"{SCORE_DIR}/score.json", f"{SCORE_DIR}/directions.json"]
    if music is None:
        return gates.GateResult.fail("sound.gap", "no music stem to check", engine=_engine())
    if not np.any(music):
        score_planned = film.path(f"{SCORE_DIR}/score.wav").exists() or film.path(f"{SCORE_DIR}/plan.json").exists()
        if score_planned:
            return gates.GateResult.fail("sound.gap", "the film has a score plan, but its music stem is silent",
                                         engine=_engine(), inputs=c.inputs(*rels))
        return gates.GateResult.pass_("sound.gap", "no score in this film (voice and effects only), so no music gaps",
                                      engine=_engine(), inputs=c.inputs(*rels))
    stops = planned_stops(film)
    res = gap.check(music, film.sr, stops)
    summary = (f"quietest music window outside {len(stops)} planned stop(s): {res['quietest_outside_stops_dbfs']} dBFS "
               f"(floor {res['threshold_dbfs']:g})")
    if not res["pass"]:
        summary = f"{len(res['failures'])} music window(s) under {res['threshold_dbfs']:g} dBFS outside the planned stops, first at {res['failures'][0]['t']} s"
    make = gates.GateResult.pass_ if res["pass"] else gates.GateResult.fail
    return make("sound.gap", summary, evidence=[film.rel(c.stem_paths["music"])], engine=_engine(),
                inputs=c.inputs(*rels), details=res)


def gate_voice_sync(c: _Ctx) -> gates.GateResult:
    film = c.film
    cues = vo.cues(film)
    if not cues:
        return gates.GateResult.pass_("sound.voice_sync", "no voice in this film", engine=_engine(),
                                      inputs=c.inputs("timing/vo_cues.json"))
    dlg = c.stem("dialogue")
    if dlg is None:
        return gates.GateResult.fail("sound.voice_sync", "no dialogue stem", engine=_engine())
    d = audio.mono(dlg)
    search = int(round(SEARCH_MS / 1000 * film.sr))
    takes, bad = [], []
    for cue in cues:
        y = vo.render_take(film, cue)
        expected = int(round(vo.cue_time(film, cue) * film.sr))
        lag, r = vo.find_take(d, y, expected, search)
        off_ms = round(lag / film.sr * 1000, 3)
        row = {"shot": cue.get("shot"), "file": cue.get("file"), "expected_s": round(expected / film.sr, 4),
               "offset_ms": off_ms, "corr": round(r, 5)}
        takes.append(row)
        if abs(off_ms) >= VOICE_EXACT_MS or r < VOICE_MIN_CORR:
            bad.append(row)
    worst = max(abs(t["offset_ms"]) for t in takes)
    min_corr = min(t["corr"] for t in takes)
    summary = (f"{len(takes)} take(s) at their cues: worst offset {worst:.2f} ms, lowest correlation {min_corr:.4f}"
               if not bad else f"{len(bad)} of {len(takes)} take(s) off their cues (first: {bad[0]['shot']}, "
               f"{bad[0]['offset_ms']:+.2f} ms, corr {bad[0]['corr']:.3f})")
    make = gates.GateResult.pass_ if not bad else gates.GateResult.fail
    return make("sound.voice_sync", summary, evidence=[film.rel(c.stem_paths["dialogue"]), "timing/vo_cues.json"],
                engine=_engine(), inputs=c.inputs(c.stem_paths["dialogue"], "timing/vo_cues.json", "timing/shots.json"),
                details={"takes": takes, "off": bad, "exact_ms": VOICE_EXACT_MS, "min_corr": VOICE_MIN_CORR})


# --- effect cues (sound/sound_cues.json)


def load_cues(film: Film) -> dict | None:
    p = film.path(CUES_FILE)
    return jsonio.read(p) if p.exists() else None


def anchor_offset(y: np.ndarray, rule: str, sr: int) -> int | None:
    """Samples from a sound's first sample to what the ear syncs to, measured on the sound on its own."""
    if rule in ("none", "bed", "vo"):
        return None
    if rule in ("onset", "onset12"):
        return dsp.onset_index(y, 12.0)
    if rule == "onset30":
        return dsp.onset_index(y, 30.0)
    if rule == "peak20":
        return dsp.loudest_window_index(y, sr, 0.02)
    if rule == "peak50":
        return dsp.loudest_window_index(y, sr, 0.05)
    if rule in ("end", "end40"):
        return dsp.last_above_index(y, 40.0, sr=sr)
    if rule == "end30":
        return dsp.last_above_index(y, 30.0, sr=sr)
    raise ValueError(f"unknown anchor rule {rule!r}")


def _hit_audio(film: Film, hit: dict, render: Callable | None) -> tuple[np.ndarray | None, str]:
    """A hit's own audio: its file, or the effects engine's render of it. ("", reason) when neither is available."""
    if hit.get("file"):
        p = film.resolve_media(hit["file"])
        if not p.exists():
            return None, f"file missing: {hit['file']}"
        y = audio.load(p, film.sr)
        if hit.get("trim"):
            a, b = hit["trim"]
            y = y[int(float(a) * film.sr):int(float(b) * film.sr)]
        return y, ""
    if render is not None:
        try:
            y, _start = render(hit)
            return y, ""
        except Exception as exc:  # noqa: BLE001 - the engine refusing (gated) is a reason, not a crash
            return None, str(getattr(exc, "message", exc))
    return None, "no file, and the effects engine that could re-render it waits for proof 3"


def _hits(sheet: dict) -> list[dict]:
    """Every hit in a cue sheet: a cue, or each note of a gesture (overlapping notes are measured one by one)."""
    out = []
    for cue in sheet.get("cues") or []:
        base = {k: v for k, v in cue.items() if k != "notes"}
        if cue.get("notes"):
            for i, note in enumerate(cue["notes"]):
                hit = {**base, **note, "id": f"{cue.get('id', '?')}.{i + 1}", "gesture": cue.get("id")}
                out.append(hit)
        else:
            out.append(base)
    return out


def _engine_renderer() -> Callable | None:
    try:
        from slate.sound.effects import engine  # noqa: PLC0415
        from slate.sound.effects import status  # noqa: PLC0415

        return engine.render_cue if status()["approved"] else None
    except ImportError:
        return None


def gate_cue_sync(c: _Ctx) -> gates.GateResult:
    film = c.film
    sheet = load_cues(film)
    effect_roles = [r for r in stem_manifest(film)]
    if sheet is None:
        if effect_roles:
            return gates.GateResult.unchecked("sound.cue_sync", "effect stems are in the mix but there's no cue sheet "
                                              "(sound/sound_cues.json), so their sync can't be measured",
                                              engine=_engine(), inputs=c.inputs(f"{STEMS_DIR}/stems.json"))
        return gates.GateResult.pass_("sound.cue_sync", "no effects in this film (score and voice only)",
                                      engine=_engine(), inputs=c.inputs(CUES_FILE))
    frame = film.frame_s * float(film.palette["rules"].get("sync_frames", 1.0))
    render = _engine_renderer()
    rows, over, unmeasured = [], [], []
    for hit in _hits(sheet):
        rule = hit.get("anchor") or film.palette["kinds"].get(hit.get("kind", ""), {}).get("anchor", "onset")
        if rule in ("none", "bed", "vo") or "beat" not in hit:
            continue
        y, why = _hit_audio(film, hit, render)
        if y is None:
            unmeasured.append({"id": hit.get("id"), "why": why})
            continue
        off = anchor_offset(y, rule, film.sr)
        anchor = float(hit["t"]) + off / film.sr
        delta = anchor - float(hit["beat"])
        row = {"id": hit.get("id"), "kind": hit.get("kind"), "beat": round(float(hit["beat"]), 4),
               "anchor_s": round(anchor, 4), "delta_ms": round(delta * 1000, 2), "rule": rule}
        rows.append(row)
        if abs(delta) > frame + 1e-9:
            over.append(row)
    details = {"frame_ms": round(frame * 1000, 3), "anchors": len(rows),
               "max_abs_delta_ms": round(max((abs(r["delta_ms"]) for r in rows), default=0.0), 2),
               "over_one_frame": over, "unmeasured": unmeasured}
    inputs = c.inputs(CUES_FILE)
    if over:
        return gates.GateResult.fail("sound.cue_sync", f"{len(over)} of {len(rows)} anchor(s) more than a frame "
                                     f"({frame * 1000:.1f} ms) from their beat; worst {details['max_abs_delta_ms']} ms",
                                     evidence=[CUES_FILE], engine=_engine(), inputs=inputs, details=details)
    if unmeasured:
        return gates.GateResult.unchecked("sound.cue_sync", f"{len(unmeasured)} cue(s) couldn't be measured on their own "
                                          f"({unmeasured[0]['why']}); {len(rows)} measured within a frame",
                                          evidence=[CUES_FILE], engine=_engine(), inputs=inputs, details=details)
    return gates.GateResult.pass_("sound.cue_sync", f"{len(rows)} anchor(s) within a frame of their beats "
                                  f"(worst {details['max_abs_delta_ms']} ms, frame {frame * 1000:.1f} ms)",
                                  evidence=[CUES_FILE], engine=_engine(), inputs=inputs, details=details)


def gate_repeats(c: _Ctx) -> gates.GateResult:
    film = c.film
    sheet = load_cues(film)
    rules = film.palette["rules"]
    if sheet is None:
        return gates.GateResult.pass_("sound.repeats", "no effect cues in this film", engine=_engine(),
                                      inputs=c.inputs(CUES_FILE))
    render = _engine_renderer()
    hits, unmeasured = [], []
    for hit in _hits(sheet):
        if hit.get("anchor") in ("none", "bed", "vo") or hit.get("kind") in (None, "bed", "vo"):
            continue
        wave = None
        if hit.get("file") or render is not None:
            y, why = _hit_audio(film, hit, render)
            if y is None:
                unmeasured.append({"id": hit.get("id"), "why": why})
                continue
            wave = audio.mono(y).astype(np.float32)  # compared by waveform (checks.same_sound)
            ident = checks.hit_hash(y)
        elif hit.get("params") is not None:
            ident = checks.params_identity(str(hit.get("sound")), hit["params"])
        else:
            unmeasured.append({"id": hit.get("id"), "why": "no file and no parameters to identify the sound by"})
            continue
        hits.append({"t": float(hit.get("t", hit.get("beat", 0.0))), "kind": str(hit.get("kind")),
                     "sound": str(hit.get("sound") or hit.get("family") or hit.get("kind")), "identity": ident,
                     "audio": wave})
    res = checks.repeats_check(hits, one_sound_per_kind=bool(rules.get("one_sound_per_kind", True)),
                               no_shared=bool(rules.get("no_sound_shared_by_kinds", True)))
    if not rules.get("no_identical_repeats", True):
        res["identical_repeats"] = []
        res["pass"] = not res["kinds_with_several_sounds"] and not res["sounds_serving_several_kinds"]
    res["unmeasured"] = unmeasured
    inputs = c.inputs(CUES_FILE)
    if not res["pass"]:
        parts = []
        if res["identical_repeats"]:
            parts.append(f"{len(res['identical_repeats'])} identical repeat(s)")
        if res["kinds_with_several_sounds"]:
            parts.append("kinds with more than one sound: " + ", ".join(res["kinds_with_several_sounds"]))
        if res["sounds_serving_several_kinds"]:
            parts.append("sounds serving several kinds: " + ", ".join(res["sounds_serving_several_kinds"]))
        return gates.GateResult.fail("sound.repeats", "; ".join(parts), evidence=[CUES_FILE], engine=_engine(),
                                     inputs=inputs, details=res)
    if unmeasured:
        return gates.GateResult.unchecked("sound.repeats", f"{len(unmeasured)} hit(s) couldn't be identified",
                                          evidence=[CUES_FILE], engine=_engine(), inputs=inputs, details=res)
    return gates.GateResult.pass_("sound.repeats", f"{res['hits']} hit(s), {res['distinct']} distinct: one sound per "
                                  "kind, varied between repeats", evidence=[CUES_FILE], engine=_engine(), inputs=inputs,
                                  details=res)


def _role_stem(c: _Ctx, role: str) -> tuple[np.ndarray | None, Path | None]:
    p = c.engine_stems.get(role)
    return (c.read(p), p) if p else (None, None)


def gate_glides(c: _Ctx) -> gates.GateResult:
    film = c.film
    rules, cfg = film.palette["rules"], film.palette.get("glide") or {}
    if not rules.get("no_glides", True) and not rules.get("no_slide_ups", True):
        return gates.GateResult.pass_("sound.glides", "the brand's palette allows glides", engine=_engine())
    x, p = _role_stem(c, "tonal")
    if x is None:
        return gates.GateResult.pass_("sound.glides", "no tonal effects in this film (nothing sustained to glide)",
                                      engine=_engine())
    res = checks.glide_check(x, film.sr, float(cfg.get("drift_cents", 50.0)), float(cfg.get("wander_cents", 60.0)),
                             min_len_s=float(cfg.get("min_len_s", 0.4)))
    bad = res["glides"] if rules.get("no_glides", True) else res["slide_ups"]
    inputs = c.inputs(p)
    if bad:
        g = bad[0]
        return gates.GateResult.fail("sound.glides", f"{len(bad)} glide(s), {len(res['slide_ups'])} of them slide-ups; "
                                     f"first at {g['t0']} s ({g['hz']} Hz, {g['drift_cents']:+.0f} cents)",
                                     evidence=[film.rel(p)], engine=_engine(), inputs=inputs, details=res)
    return gates.GateResult.pass_("sound.glides", f"{res['tracks']} sustained partial(s), none glides "
                                  f"(largest drift {res['max_abs_drift_cents']} cents)", evidence=[film.rel(p)],
                                  engine=_engine(), inputs=inputs, details=res)


def gate_drone(c: _Ctx) -> gates.GateResult:
    film = c.film
    if not film.palette["rules"].get("no_drone", True):
        return gates.GateResult.pass_("sound.drone", "the brand's palette allows a bed under the effects", engine=_engine())
    cfg = film.palette.get("drone") or {}
    sheet = load_cues(film) or {}
    roles = [r for r in ("ui", "fx", "verb", "amb") if r in c.engine_stems]
    if not roles:
        return gates.GateResult.pass_("sound.drone", "no effects or ambience stems in this film", engine=_engine())
    found, per_role, used = [], {}, []
    for role in roles:
        x, p = _role_stem(c, role)
        used.append(p)
        res = checks.drone_spans(x, film.sr, max_continuous_s=float(cfg.get("max_continuous_s", 8.0)),
                                 floor_dbfs=float(cfg.get("floor_dbfs", -60.0)),
                                 stationary_db=float(cfg.get("stationary_db", 6.0)),
                                 window_s=float(cfg.get("window_s", 0.5)), beds=sheet.get("beds") or [])
        per_role[role] = res
        found += [{**d, "stem": role} for d in res["drones"]]
    inputs = c.inputs(*used)
    if found:
        d = found[0]
        return gates.GateResult.fail("sound.drone", f"a steady bed under the {d['stem']} stem from {d['from']} to "
                                     f"{d['to']} s ({d['level_dbfs']} dBFS); the palette rules out room-tone drones",
                                     evidence=[film.rel(p) for p in used], engine=_engine(), inputs=inputs,
                                     details={"drones": found, "per_stem": per_role})
    return gates.GateResult.pass_("sound.drone", f"no drone under {', '.join(roles)}", engine=_engine(), inputs=inputs,
                                  details={"per_stem": per_role})


def _levels_vs_voice(c: _Ctx) -> tuple[dict, list[Path]]:
    film = c.film
    dlg = c.stem("dialogue")
    if dlg is None or not np.any(dlg):
        return {}, []
    vo_l = measure.integrated(dlg, film.sr)
    out, used = {}, [c.stem_paths["dialogue"]]
    for role, p in c.engine_stems.items():
        x = c.read(p)
        if np.any(x):
            out[role] = measure.lu_vs(vo_l, measure.integrated(x, film.sr))
            used.append(p)
    music = c.stem("music")
    if music is not None and np.any(music):
        out["music"] = measure.lu_vs(vo_l, measure.integrated(music, film.sr))
        used.append(c.stem_paths["music"])
    return out, used


def gate_levels(c: _Ctx) -> gates.GateResult:
    film = c.film
    lv = film.palette["levels"]
    targets = {k: float(v) for k, v in (lv.get("lu_vs_voice") or {}).items()}
    tol = float(lv.get("tolerance_lu", 2.0))
    measured, used = _levels_vs_voice(c)
    checked = {r: t for r, t in targets.items() if measured.get(r) is not None}
    inputs = c.inputs(*used)
    details = {"lu_vs_voice": measured, "targets": targets, "tolerance_lu": tol,
               "palette": film.palette.get("brand") or "starting palette (proof 2)"}
    if not checked:
        what = "no effect stems with a level target in this film" if measured.get("music") is not None or not targets else \
            "no effect stems with a level target"
        return gates.GateResult.pass_("sound.levels", f"{what} (music {measured.get('music')} LU against the voice)",
                                      engine=_engine(), inputs=inputs, details=details)
    off = {r: round(measured[r] - t, 2) for r, t in checked.items() if abs(measured[r] - t) > tol}
    summary = ", ".join(f"{r} {measured[r]:+.1f} LU (target {t:+.1f})" for r, t in checked.items())
    make = gates.GateResult.pass_ if not off else gates.GateResult.fail
    return make("sound.levels", ("against the voice: " if not off else "off target against the voice: ") + summary,
                evidence=[film.rel(p) for p in used], engine=_engine(), inputs=inputs, details={**details, "off": off})


def reference_levels(film: Film) -> tuple[dict | None, str, dict | None]:
    """(levels vs voice, why, reference record) from the brand's approved sound reference."""
    brand = film.brand
    if brand is None:
        return None, "the film's brand pack wasn't found", None
    ref = (brand.get("references") or {}).get("sound")
    if not ref:
        return None, f"brand {brand.get('name')!r} has no approved sound reference yet (`slate brand onboard`)", None
    if not ref.get("approved_by"):
        return None, f"brand {brand.get('name')!r}'s sound reference isn't approved yet", ref
    base = Path(brand.get("_dir") or ".")
    ref_file = Path(str(ref.get("file") or "")).expanduser()
    if ref.get("file") and not ref_file.is_absolute():
        ref_file = base / ref_file
    if ref.get("file") and ref_file.is_file() and ref.get("sha256") and jsonio.sha256(ref_file) != ref["sha256"]:
        return None, "the brand's sound reference has changed since it was approved (its SHA-256 no longer matches)", ref
    sound = brand.get("sound") or {}
    levels = sound.get("reference_levels") or ref.get("levels")
    if levels:
        return {k: float(v) for k, v in levels.items() if v is not None}, "from the brand pack", ref
    stems_dir = None
    if ref.get("file"):
        # "<name>_stems/" beside the reference, or "<prefix>_stems/" beside "<prefix>_mix.wav" (how proofs are laid out)
        candidates = [ref_file.with_name(ref_file.stem + "_stems")]
        if ref_file.stem.endswith("_mix"):
            candidates.append(ref_file.with_name(ref_file.stem[: -len("_mix")] + "_stems"))
        stems_dir = next((c for c in candidates if c.is_dir() and (c / "vo.wav").exists()), None)
    if stems_dir is not None:
        vo_x, sr = audio.read_wav(stems_dir / "vo.wav")
        vo_l = measure.integrated(vo_x, sr)
        out = {}
        for p in sorted(stems_dir.glob("*.wav")):
            if p.stem == "vo":
                continue
            x, _ = audio.read_wav(p)
            if np.any(x):
                out[p.stem] = measure.lu_vs(vo_l, measure.integrated(x, sr))
        return out, f"measured from {stems_dir.name}", ref
    return None, "the sound reference has no measured levels (reference_levels in brand.json, or stems beside it)", ref


def gate_reference(c: _Ctx) -> gates.GateResult:
    film = c.film
    ref_levels, why, ref = reference_levels(film)
    if ref_levels is None:
        return gates.GateResult.unchecked("sound.reference", why, engine=_engine(), details={"reference": ref})
    tol = float(film.palette["levels"].get("tolerance_lu", 2.0))
    measured, used = _levels_vs_voice(c)
    common = {r: v for r, v in ref_levels.items() if measured.get(r) is not None and v is not None}
    details = {"reference": ref, "reference_lu_vs_voice": ref_levels, "film_lu_vs_voice": measured, "tolerance_lu": tol,
               "source": why}
    inputs = c.inputs(*used)
    if not common:
        return gates.GateResult.unchecked("sound.reference", "the reference and the film share no stem to compare "
                                          f"(reference has {', '.join(ref_levels) or 'none'}; film has "
                                          f"{', '.join(measured) or 'none'})", engine=_engine(), inputs=inputs,
                                          details=details)
    off = {r: round(measured[r] - v, 2) for r, v in common.items() if abs(measured[r] - v) > tol}
    summary = ", ".join(f"{r} {measured[r]:+.1f} LU vs reference {v:+.1f}" for r, v in common.items())
    make = gates.GateResult.pass_ if not off else gates.GateResult.fail
    return make("sound.reference", ("matches the brand's sound reference: " if not off else "off the brand's sound reference: ")
                + summary, evidence=[film.rel(p) for p in used], engine=_engine(), inputs=inputs,
                details={**details, "off": off})


def gate_clarity(c: _Ctx) -> gates.GateResult:
    film = c.film
    dlg = c.stem("dialogue")
    if dlg is None or not np.any(dlg):
        return gates.GateResult.pass_("sound.clarity", "no voice in this film", engine=_engine())
    rest = sum((c.stem(k) for k in ("effects", "ambience", "music") if c.stem(k) is not None), np.zeros_like(dlg))
    words = words_for(film)
    cfg = film.palette.get("clarity") or {}
    lo, hi = (cfg.get("band_hz") or [300.0, 4000.0])
    min_db = float(cfg.get("min_db", 3.0))
    effects = sum((c.stem(k) for k in ("effects", "ambience") if c.stem(k) is not None), np.zeros_like(dlg))
    over = checks.never_over(dlg, effects, film.sr, float((film.palette.get("never_over") or {}).get("margin_db", 0.0)))
    inputs = c.inputs(*[p for p in c.stem_paths.values() if p.exists()], "timing/vo_words.json")
    if not words:
        if not over["pass"]:
            return gates.GateResult.fail("sound.clarity", f"an effect is louder than the voice "
                                         f"({over['effects_max_momentary_lu_vs_voice']:+.1f} LU at {over['at_s']} s)",
                                         engine=_engine(), inputs=inputs, details={"never_over": over})
        return gates.GateResult.unchecked("sound.clarity", "no word timings (timing/vo_words.json), so per-word "
                                          "clarity can't be measured", engine=_engine(), inputs=inputs,
                                          details={"never_over": over})
    wm = checks.word_masking(dlg, rest, film.sr, words, float(lo), float(hi))
    if not wm:
        return gates.GateResult.unchecked("sound.clarity", "no measurable words", engine=_engine(), inputs=inputs)
    worst = sorted(wm, key=lambda r: r[3])[:5]
    masked = [w for w in wm if w[3] < min_db]
    details = {"words": len(wm), "min_voice_over_rest_db": worst[0][3], "worst_words": worst, "masked": masked,
               "band_hz": [lo, hi], "min_db": min_db, "never_over": over}
    ok = not masked and over["pass"]
    if ok:
        summary = f"{len(wm)} word(s) clear: the voice at least {worst[0][3]:.1f} dB over the rest in the speech band"
    elif masked:
        summary = (f"{len(masked)} word(s) under {min_db:g} dB clear of the rest (worst {masked[0][0]!r} at "
                   f"{masked[0][1]} s, {min(m[3] for m in masked):.1f} dB)")
    else:
        summary = f"an effect is louder than the voice ({over['effects_max_momentary_lu_vs_voice']:+.1f} LU at {over['at_s']} s)"
    make = gates.GateResult.pass_ if ok else gates.GateResult.fail
    return make("sound.clarity", summary, engine=_engine(), inputs=inputs, details=details)


def gate_music_licence(c: _Ctx) -> gates.GateResult:
    film = c.film
    has_score = film.path(f"{SCORE_DIR}/score.wav").exists()
    if not has_score:
        return gates.GateResult.pass_("sound.music_licence", "no composed score in this film", engine=_engine())
    if not film.broadcast():
        return gates.GateResult.pass_("sound.music_licence", "online use: a paid Eleven Music plan covers it",
                                      engine=_engine())
    lic = str(film.music_licence() or "").lower()
    if "enterprise" in lic:
        return gates.GateResult.pass_("sound.music_licence", "broadcast use, with an Eleven Music Enterprise licence "
                                      "recorded in film.json", engine=_engine())
    return gates.GateResult.review("sound.music_licence", "broadcast use (film, TV or radio): Eleven Music's self-serve "
                                   "plans exclude it, so the score needs an Enterprise licence before this film airs",
                                   engine=_engine(), details={"fix": 'record it in film.json: "licences": '
                                                                     '{"eleven_music": "enterprise"}'})


GATES: dict[str, Callable[[_Ctx], gates.GateResult]] = {
    "sound.loudness": gate_loudness, "sound.stems_sum": gate_stems_sum, "sound.gap": gate_gap,
    "sound.voice_sync": gate_voice_sync, "sound.cue_sync": gate_cue_sync, "sound.repeats": gate_repeats,
    "sound.glides": gate_glides, "sound.drone": gate_drone, "sound.levels": gate_levels,
    "sound.reference": gate_reference, "sound.clarity": gate_clarity, "sound.music_licence": gate_music_licence,
}


def run(film_dir: Path, names: list[str] | None = None, *, write: bool = True) -> list[gates.GateResult]:
    """Run the sound gates on the delivered mix, record them in qa/gates.json and sound/mix/verify.json, and return them."""
    film = load(film_dir)
    ctx = _Ctx(film)
    chosen = names or list(GATES)
    unknown = [n for n in chosen if n not in GATES and f"sound.{n}" not in GATES]
    if unknown:
        from slate.errors import UsageError  # noqa: PLC0415

        raise UsageError(f"Unknown sound gate(s): {', '.join(unknown)}. Known: {', '.join(GATES)}.")
    results = []
    for name in chosen:
        key = name if name in GATES else f"sound.{name}"
        try:
            results.append(GATES[key](ctx))
        except MissingPrerequisite as exc:
            results.append(gates.GateResult.unchecked(key, exc.message, engine=_engine(), details={"hint": exc.hint}))
    if write:
        gates.write_report(film.dir, results)
        jsonio.write_atomic(film.path(f"{MIX_DIR}/verify.json"),
                            jsonio.mark_generated({"kind": "verify", "film": film.id, "summary": gates.summarize(results),
                                                   "gates": {r.gate: r.to_dict() for r in results}}, "slate verify"))
    return results
