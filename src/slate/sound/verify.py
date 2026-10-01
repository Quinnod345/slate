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
                       approves one), and the effects no brighter than it: their spectral centroid and share of energy
                       above 2 kHz within 25% of the reference's (REVIEW past that)
sound.clarity          every word of the voice at least 3 dB over everything else in the speech band, and no effect
                       louder than the voice
sound.music_licence    REVIEW for broadcast use (film, TV, radio) until the film records an Eleven Music Enterprise
                       licence; PASS otherwise
sound.voice_limiter    the voice-only limiter touched under 5% of the speech (measured by `slate mix`)
sound.key              every pitched note in the UI and tonal stems on the brand key's pentatonic; REVIEW a stem with more
                       than half its notes on one or two pitches
sound.timbre           each effect stem's median spectral centroid at or under 1.5 kHz, no chime tails (a high partial
                       still ringing 300 ms after its strike), nothing metallic or glassy
sound.mix_words        Whisper on the delivered mix hears the script word for word (a word heard over the music, where
                       the voice isn't sounding or Whisper is less than 20% sure, is listed, not failed)

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
              "sound.glides", "sound.drone", "sound.levels", "sound.reference", "sound.clarity", "sound.music_licence",
              "sound.voice_limiter", "sound.key", "sound.timbre", "sound.mix_words")
BRIGHTER_BY = 0.25  # the effects may be at most 25% brighter than the brand's approved reference (sound.reference)


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
    if stops:
        # The music fading into a planned silence (and back out of it) is part of that silence, the rule the owner
        # approved on 2026-09-30: each stop is widened by the music's own fade, as sound.planned_stops does. A hole
        # anywhere else is still a hole.
        from slate.sound import balance  # noqa: PLC0415

        st = balance.palette_balance(film.palette).get("stops") or {}
        stops, _ = balance.widen_stops(music, film.sr, stops, under_median_db=float(st.get("under_median_db", 20.0)),
                                       cap_s=float(st.get("fade_cap_s", 2.0)), step_s=float(st.get("step_s", 0.1)))
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
                     "audio": wave, "id": hit.get("id"), "bookend": bool(hit.get("bookend")),
                     "sonic_logo": bool(hit.get("sonic_logo") or hit.get("logo") or hit.get("kind") == "logo")})
    res = checks.repeats_check(hits, one_sound_per_kind=bool(rules.get("one_sound_per_kind", True)),
                               no_shared=bool(rules.get("no_sound_shared_by_kinds", True)), film_s=film.duration_s)
    if not rules.get("no_identical_repeats", True):
        res["identical_repeats"] = []
    # the brand's sonic logo closing the film the way it opened is the same sound on purpose (a bookend): listed
    res["intentional"] = [r for r in res["identical_repeats"] if r.get("bookend")]
    res["identical_repeats"] = [r for r in res["identical_repeats"] if not r.get("bookend")]
    res["pass"] = not res["identical_repeats"] and not res["kinds_with_several_sounds"] and \
        not res["sounds_serving_several_kinds"]
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
    ends = (f"; {len(res['intentional'])} deliberate bookend(s): {res['intentional'][0]['why']}"
            if res["intentional"] else "")
    return gates.GateResult.pass_("sound.repeats", f"{res['hits']} hit(s), {res['distinct']} distinct: one sound per "
                                  f"kind, varied between repeats{ends}", evidence=[CUES_FILE], engine=_engine(),
                                  inputs=inputs, details=res)


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
    bright = _brightness_vs_reference(c, ref)
    details.update({"off": off, "brightness": bright})
    evidence = [film.rel(p) for p in used]
    if off:
        return gates.GateResult.fail("sound.reference", "off the brand's sound reference: " + summary, evidence=evidence,
                                     engine=_engine(), inputs=inputs, details=details)
    if bright.get("brighter"):
        return gates.GateResult.review("sound.reference", f"the levels match the brand's sound reference ({summary}), "
                                       f"but the effects are brighter than it: {bright['summary']}; a person listens",
                                       evidence=evidence, engine=_engine(), inputs=inputs, details=details)
    note = f"; {bright['summary']}" if bright.get("summary") else ""
    return gates.GateResult.pass_("sound.reference", "matches the brand's sound reference: " + summary + note,
                                  evidence=evidence, engine=_engine(), inputs=inputs, details=details)


def reference_brightness(film: Film, ref: dict | None) -> tuple[dict | None, str]:
    """(the approved reference's effect brightness {centroid_hz, above_2k_share}, where it came from): the brand's
    measured proof (brand.json sound.reference_brightness), else measured on the effect stems beside the reference
    (ui, fx and their reverb, as the film's effects stem holds them)."""
    sound = (film.brand or {}).get("sound") or {}
    given = sound.get("reference_brightness") or (ref or {}).get("brightness")
    if isinstance(given, dict) and given.get("centroid_hz") is not None:
        return {"centroid_hz": float(given["centroid_hz"]),
                "above_2k_share": float(given["above_2k_share"]) if given.get("above_2k_share") is not None else None}, \
            "from the brand pack"
    if not ref or not ref.get("file"):
        return None, "the reference has no file to measure"
    base = Path((film.brand or {}).get("_dir") or ".")
    ref_file = Path(str(ref["file"])).expanduser()
    ref_file = ref_file if ref_file.is_absolute() else base / ref_file
    candidates = [ref_file.with_name(ref_file.stem + "_stems")]
    if ref_file.stem.endswith("_mix"):
        candidates.append(ref_file.with_name(ref_file.stem[: -len("_mix")] + "_stems"))
    stems_dir = next((d for d in candidates if d.is_dir()), None)
    if stems_dir is None:
        return None, "no effect stems beside the reference to measure its brightness"
    parts = [stems_dir / f"{r}.wav" for r in ("ui", "fx", "verb") if (stems_dir / f"{r}.wav").exists()]
    if not parts:
        return None, f"{stems_dir.name} has no effect stems (ui, fx, verb)"
    total = None
    sr = film.sr
    for part in parts:
        x, sr = audio.read_wav(part)
        x = audio.to_stereo(x)
        total = x if total is None else total[: min(len(total), len(x))] + x[: min(len(total), len(x))]
    b = checks.brightness(total, sr)
    return (b, f"measured on {stems_dir.name}") if b else (None, f"{stems_dir.name}'s effect stems are silent")


def _brightness_vs_reference(c: _Ctx, ref: dict | None) -> dict:
    """The film's effects against the reference's: REVIEW when either the centroid or the share above 2 kHz is more
    than 25% higher (L-sound-D017: chimes to pads, notes and wood took the films' effects from 583 to 382 Hz)."""
    film = c.film
    ours = None
    fx = c.stem("effects")
    if fx is not None and np.any(fx):
        ours = checks.brightness(fx, film.sr)
    theirs, why = reference_brightness(film, ref)
    out: dict = {"film": ours, "reference": theirs, "reference_from": why, "limit": BRIGHTER_BY}
    if ours is None:
        out["summary"] = "no effects in this film to compare for brightness"
        return out
    if theirs is None:
        out["summary"] = f"brightness not compared ({why})"
        return out
    over = []
    if ours["centroid_hz"] > theirs["centroid_hz"] * (1 + BRIGHTER_BY):
        over.append(f"centroid {ours['centroid_hz']:.0f} Hz against {theirs['centroid_hz']:.0f}")
    if theirs.get("above_2k_share") is not None and ours["above_2k_share"] > theirs["above_2k_share"] * (1 + BRIGHTER_BY):
        over.append(f"{ours['above_2k_share']:.1%} of the energy above 2 kHz against {theirs['above_2k_share']:.1%}")
    out["brighter"] = bool(over)
    out["summary"] = ("; ".join(over) + f" (more than {BRIGHTER_BY:.0%} brighter)") if over else (
        f"the effects' brightness within {BRIGHTER_BY:.0%} of the reference's (centroid {ours['centroid_hz']:.0f} Hz "
        f"against {theirs['centroid_hz']:.0f})")
    return out


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


def gate_voice_limiter(c: _Ctx) -> gates.GateResult:
    """L-sound-022: the voice-only limiter touches under 5% of the speech; past that it's shaping the read."""
    from slate.sound.mix import VOICE_LIMITER_MAX  # noqa: PLC0415

    dlg = c.stem("dialogue")
    if dlg is None or not np.any(dlg):
        return gates.GateResult.pass_("sound.voice_limiter", "no voice in this film", engine=_engine())
    rep = c.report.get("voice_limiter")
    inputs = c.inputs(c.report_path, c.stem_paths["dialogue"])
    if not isinstance(rep, dict) or rep.get("share") is None:
        return gates.GateResult.unchecked("sound.voice_limiter", "this mix was made before Slate measured the voice "
                                          "limiter's touch; mix again to measure it", evidence=["next: slate mix"],
                                          engine=_engine(), inputs=inputs)
    share = float(rep["share"])
    what = (f"the voice limiter touched {share:.1%} of the speech ({rep.get('touched_ms')} of {rep.get('speech_ms')} "
            f"ms, at most {rep.get('max_reduction_db')} dB)")
    if share >= VOICE_LIMITER_MAX:
        return gates.GateResult.fail("sound.voice_limiter", what + f": {VOICE_LIMITER_MAX:.0%} or more shapes the read, "
                                     "not just its plosives", evidence=[c.film.rel(c.report_path),
                                                                        "next: lower the hottest takes' gain in "
                                                                        "timing/vo_cues.json or clean their peaks, then "
                                                                        "slate mix"],
                                     engine=_engine(), inputs=inputs, details=rep)
    return gates.GateResult.pass_("sound.voice_limiter", what + f" (under {VOICE_LIMITER_MAX:.0%})",
                                  evidence=[c.film.rel(c.report_path)], engine=_engine(), inputs=inputs, details=rep)


def _effect_family_stems(c: _Ctx, roles: tuple[str, ...]) -> list[tuple[str, Path]]:
    """The stems a timbre or pitch check reads: the effects engine's family stems when the mix wrote them, else the
    engine stems by role."""
    fam_dir = c.film.path(f"{MIX_DIR}/family_stems")
    if fam_dir.is_dir():
        from slate.sound import balance  # noqa: PLC0415

        effects = set(balance.palette_balance(c.film.palette).get("effects") or [])
        fams = [(p.stem, p) for p in sorted(fam_dir.glob("*.wav")) if p.stem in effects]
        if fams:
            return fams
    return [(r, c.engine_stems[r]) for r in roles if r in c.engine_stems]


def gate_key(c: _Ctx) -> gates.GateResult:
    """L-sound-060: every pitched note in the UI and tonal stems on the brand key's pentatonic (each note's pitch over
    its first 60 ms); a stem with more than half its notes on one or two pitches waits for a person."""
    film = c.film
    stems = [(r, c.engine_stems[r]) for r in ("ui", "tonal") if r in c.engine_stems]
    stems = [(r, p) for r, p in stems if np.any(c.read(p))]
    if not stems:
        return gates.GateResult.pass_("sound.key", "no UI or tonal stems in this film: nothing pitched to check",
                                      engine=_engine())
    key = ((film.brand or {}).get("sound") or {}).get("key") or (film.palette or {}).get("key")
    per, off, narrow = {}, [], []
    for role, path in stems:
        res = checks.key_check(c.read(path), film.sr, key)
        per[role] = res
        off += [{**n, "stem": role} for n in res["off_key"]]
        if res["narrow"]:
            narrow.append(f"{role}: {res['top_two_share']:.0%} of {res['notes']} notes on "
                          f"{' and '.join(t['note'] for t in res['top_pitches'])}")
    inputs = c.inputs(*[p for _r, p in stems])
    details = {"key": key, "stems": per}
    evidence = [film.rel(p) for _r, p in stems]
    if off:
        first = off[0]
        return gates.GateResult.fail("sound.key", f"{len(off)} note(s) off {key}'s pentatonic "
                                     f"({', '.join(per[stems[0][0]]['pentatonic'] or [])}): first {first['note']} at "
                                     f"{first['t']} s in the {first['stem']} stem", evidence=evidence, engine=_engine(),
                                     inputs=inputs, details={**details, "off_key": off})
    if checks.pentatonic(key) is None:
        return gates.GateResult.unchecked("sound.key", "the brand's sound names no key (brand.json sound.key), so the "
                                          "notes can't be checked against its pentatonic", evidence=evidence,
                                          engine=_engine(), inputs=inputs, details=details)
    if narrow:
        return gates.GateResult.review("sound.key", "on the key, but " + "; ".join(narrow) + ": a person listens for "
                                       "one note played over and over", evidence=evidence, engine=_engine(),
                                       inputs=inputs, details=details)
    notes = sum(r["notes"] for r in per.values())
    return gates.GateResult.pass_("sound.key", f"{notes} pitched note(s) in {', '.join(per)} on {key}'s pentatonic",
                                  evidence=evidence, engine=_engine(), inputs=inputs, details=details)


def gate_timbre(c: _Ctx) -> gates.GateResult:
    """L-sound-078: each effect stem (or family) at a median spectral centroid of 1.5 kHz or under, no chime tails,
    nothing metallic or glassy: the palette is pads, small soft notes and wood."""
    film = c.film
    stems = [(r, p) for r, p in _effect_family_stems(c, ("ui", "fx", "tonal")) if np.any(c.read(p))]
    if not stems:
        return gates.GateResult.pass_("sound.timbre", "no effect stems in this film", engine=_engine())
    per, problems = {}, []
    for role, path in stems:
        res = checks.timbre_check(c.read(path), film.sr)
        per[role] = res
        med = res["median_centroid_hz"]
        if med is not None and med > checks.CENTROID_MAX_HZ:
            hot = ", ".join(f"{h['t']} s" for h in res["brightest"][:3])
            problems.append(f"the {role} stem's median centroid is {med / 1000:.2f} kHz, over "
                            f"{checks.CENTROID_MAX_HZ / 1000:g} kHz (brightest hits at {hot})")
        for h in res["chime_tails"][:3]:
            problems.append(f"a {h['chime_tail']['hz'] / 1000:.1f} kHz partial still rings 300 ms after the {role} hit at "
                            f"{h['t']} s ({h['chime_tail']['db_vs_strike']:+.0f} dB against its strike): a chime's tail")
        for h in res["metallic"][:3]:
            problems.append(f"the {role} hit at {h['t']} s rings metallic or glassy past 300 ms (partials at "
                            f"{', '.join(f'{x:.0f}' for x in h['metallic']['partials_hz'])} Hz aren't one note's "
                            "harmonics)")
    inputs = c.inputs(*[p for _r, p in stems])
    evidence = [film.rel(p) for _r, p in stems]
    if problems:
        return gates.GateResult.fail("sound.timbre", problems[0] + (f"; {len(problems) - 1} more" if len(problems) > 1
                                                                    else ""), evidence=evidence + problems[:12],
                                     engine=_engine(), inputs=inputs, details={"stems": per})
    table = ", ".join(f"{r} {v['median_centroid_hz'] / 1000:.2f} kHz" for r, v in per.items()
                      if v["median_centroid_hz"] is not None) or "no hits"
    return gates.GateResult.pass_("sound.timbre", f"no chimes, nothing metallic, every stem's median centroid at or "
                                  f"under {checks.CENTROID_MAX_HZ / 1000:g} kHz ({table})", evidence=evidence,
                                  engine=_engine(), inputs=inputs, details={"stems": per})


def gate_mix_words(c: _Ctx) -> gates.GateResult:
    """L-sound-S019: Whisper on the delivered mix hears the script word for word (slate.sound.hear)."""
    from slate.sound import hear  # noqa: PLC0415

    film = c.film
    lines = hear.script_lines(film)
    inputs = c.inputs(c.mix_path, "timing/vo_words.json", "timing/vo_cues.json")
    if not vo.cues(film):
        return gates.GateResult.pass_("sound.mix_words", "no voice in this film", engine=_engine(), inputs=inputs)
    if not lines or not any(ln["line"].strip() for ln in lines):
        return gates.GateResult.unchecked("sound.mix_words", "no script lines to compare with (timing/vo_words.json)",
                                          evidence=["next: slate timing"], engine=_engine(), inputs=inputs)
    untimed = [ln["shot"] for ln in lines if ln["line"].strip() and ln["end"] <= ln["start"]]
    if untimed:
        return gates.GateResult.unchecked("sound.mix_words", f"{', '.join(untimed[:6])} {'has' if len(untimed) == 1 else 'have'} "
                                          "no word timings, so what's heard can't be placed against the lines",
                                          evidence=["next: slate timing"], engine=_engine(), inputs=inputs)
    lang_code = str(film.data.get("language") or "en")
    try:
        from slate.voice import lang as voice_lang  # noqa: PLC0415

        whisper_lang = voice_lang.load(lang_code).get("whisper_language", lang_code)
    except Exception:  # noqa: BLE001 - a language without a table is heard in its own code
        whisper_lang = lang_code
    result, how = hear.hear(film, c.mix_path, language=whisper_lang)
    engine = gates.engine("openai-whisper", how.get("version"), model=how.get("model"), replay=how.get("replay"))
    if how.get("replay") and not how.get("recorded") and not hear.stand_in(lines):
        return gates.GateResult.unchecked("sound.mix_words", "replay mode doesn't run Whisper, and this mix carries a "
                                          "real voice, so nothing heard it", evidence=["next: slate verify "
                                                                                       "sound.mix_words --live (Whisper "
                                                                                       "is local: it spends nothing)"],
                                          engine=engine, inputs=inputs, details={"heard": how})
    from slate.voice import measure as voice_measure  # noqa: PLC0415

    heard = voice_measure.whisper_words(result)
    dlg = c.stem("dialogue")
    sounding = hear.sounding_at(dlg if dlg is not None else np.zeros((film.n, 2)), film.sr)
    res = hear.compare(lines, heard, sounding, language=lang_code, names=hear.heard_as(film))
    details = {"heard": how, "words_heard": len(heard), "per_shot": res["per_shot"], "errors": res["errors"],
               "over_music": res["over_music"], "probability_min": hear.PROBABILITY_MIN}
    stood_in = " (a replayed stand-in: nothing real was heard)" if how.get("replay") and not how.get("recorded") else ""
    music = (f"; {len(res['over_music'])} word(s) heard over the music, set aside: "
             + ", ".join(f"{w['word']!r} at {w['t']} s" for w in res["over_music"][:3])) if res["over_music"] else ""
    if res["errors"]:
        e = res["errors"][0]
        first = (f"{e['heard']!r} for {e['script']!r}" if e["what"] == "misheard" else
                 f"{e['script']!r} not heard" if e["what"] == "not heard" else f"an extra {e['word']!r}")
        return gates.GateResult.fail("sound.mix_words", f"{len(res['errors'])} word(s) off in the mix: first {first} "
                                     f"at {e['t']} s{music}{stood_in}", evidence=[film.rel(c.mix_path)],
                                     engine=engine, inputs=inputs, details=details)
    words = sum(len(hear.tokens(ln["line"])) for ln in lines)
    return gates.GateResult.pass_("sound.mix_words", f"Whisper heard every one of the {words} script words in the mix"
                                  f"{music}{stood_in}", evidence=[film.rel(c.mix_path)], engine=engine, inputs=inputs,
                                  details=details)


GATES: dict[str, Callable[[_Ctx], gates.GateResult]] = {
    "sound.loudness": gate_loudness, "sound.stems_sum": gate_stems_sum, "sound.gap": gate_gap,
    "sound.voice_sync": gate_voice_sync, "sound.cue_sync": gate_cue_sync, "sound.repeats": gate_repeats,
    "sound.glides": gate_glides, "sound.drone": gate_drone, "sound.levels": gate_levels,
    "sound.reference": gate_reference, "sound.clarity": gate_clarity, "sound.music_licence": gate_music_licence,
    "sound.voice_limiter": gate_voice_limiter, "sound.key": gate_key, "sound.timbre": gate_timbre,
    "sound.mix_words": gate_mix_words,
}

# Gate modules merged into `slate verify` when they're installed: slate.sound.balance (the p3 balance gates) exports
# GATES = {"sound.<name>": fn}, where fn takes the film folder (or, like these, the loaded mix context). A missing
# module is simply not there yet; a core gate's name is never replaced.
EXTERNAL = ("slate.sound.balance",)
EXTERNAL_STATUS: dict[str, str] = {}


def external_gates() -> dict[str, Callable]:
    """{name: gate} from the EXTERNAL modules that are installed (import-guarded: a broken one is reported, not fatal)."""
    import importlib  # noqa: PLC0415

    out: dict[str, Callable] = {}
    for module_name in EXTERNAL:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            EXTERNAL_STATUS[module_name] = ("not installed yet" if exc.name == module_name
                                            else f"import failed: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - another module's bug must not take the core sound gates down
            EXTERNAL_STATUS[module_name] = f"import failed: {type(exc).__name__}: {exc}"
            continue
        exported = getattr(module, "GATES", None)
        added = []
        for name, fn in (exported.items() if isinstance(exported, dict) else ()):
            key = str(name) if str(name).startswith("sound.") else f"sound.{name}"
            if callable(fn) and key not in GATES and key not in out:
                out[key] = fn
                added.append(key)
        EXTERNAL_STATUS[module_name] = f"merged: {', '.join(added)}" if added else "installed, but it exports no gates"
    return out


def gate_names() -> list[str]:
    """Every gate `slate verify` runs: the core sound gates, then any merged in (deliver requires them all)."""
    return [*GATE_NAMES, *[n for n in external_gates() if n not in GATE_NAMES]]


PATH_PARAMS = ("film_dir", "folder", "path", "film_path")


def _call_external(fn: Callable, ctx: _Ctx) -> gates.GateResult:
    """A merged gate gets verify's loaded context (ctx.film is the sound Film), as slate.sound.balance's gates expect,
    unless its first parameter says it wants the film folder (film_dir, folder, path)."""
    import inspect  # noqa: PLC0415

    try:
        params = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        params = []
    return fn(ctx.film.dir if params and params[0] in PATH_PARAMS else ctx)


def run(film_dir: Path, names: list[str] | None = None, *, write: bool = True) -> list[gates.GateResult]:
    """Run the sound gates on the delivered mix, record them in qa/gates.json and sound/mix/verify.json, and return them."""
    film = load(film_dir)
    ctx = _Ctx(film)
    merged = external_gates()
    known = {**GATES, **merged}
    chosen = names or list(known)
    unknown = [n for n in chosen if n not in known and f"sound.{n}" not in known]
    if unknown:
        from slate.errors import UsageError  # noqa: PLC0415

        raise UsageError(f"Unknown sound gate(s): {', '.join(unknown)}. Known: {', '.join(known)}.")
    results = []
    for name in chosen:
        key = name if name in known else f"sound.{name}"
        try:
            if key in GATES:
                results.append(GATES[key](ctx))
            else:
                out = _call_external(known[key], ctx)
                results += [r if isinstance(r, gates.GateResult) else gates.GateResult.from_dict({"gate": key, **r})
                            for r in (out if isinstance(out, (list, tuple)) else [out])]
        except MissingPrerequisite as exc:
            results.append(gates.GateResult.unchecked(key, exc.message, engine=_engine(), details={"hint": exc.hint}))
        except Exception as exc:  # noqa: BLE001 - a merged gate that crashes is UNCHECKED, never a crashed verify
            if key in GATES:
                raise
            results.append(gates.GateResult.unchecked(key, f"the gate couldn't run: {type(exc).__name__}: {exc}",
                                                      engine=_engine(), details={"module": EXTERNAL[0]}))
    for r in results:
        r.details.setdefault("stage", "sound")  # sound.voice_sync would otherwise read as a voice gate in `slate status`
    if write:
        gates.write_report(film.dir, results)
        jsonio.write_atomic(film.path(f"{MIX_DIR}/verify.json"),
                            jsonio.mark_generated({"kind": "verify", "film": film.id, "summary": gates.summarize(results),
                                                   "gates": {r.gate: r.to_dict() for r in results}}, "slate verify"))
    return results
