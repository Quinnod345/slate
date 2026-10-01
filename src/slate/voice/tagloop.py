"""The tag-optimization loop: voice candidates, measure them against the voice's own untagged reads, pick, listen.

Generalized from the films' rounds of audio-tag work (`tag_optimize.py`, `scope_eval.py`, `tag_pick.py`,
`c01_confident.py`, `tag_abc_v3.py`, 2026-09-30). What those rounds learned is built in:

- **Full voice only.** No tag may lower effort or add breath (whisper, soft, quiet, gentle and kin are refused before
  any request), and every take is measured: a tagged read breathier than the voice's own untagged reads of the same
  words is out, however good its tag looks. Two tests, both required: the statistical rule (`slate.voice.breath`) and
  the strict one, no span breathier than the breathiest untagged read of the same words.
- **Three spans per take**: the words the tag governs, the words where the line's feeling should show (the same for
  every take of the line), and the whole line. A tag on the last word can whisper that word while the line's average
  barely moves.
- **The seed matters as much as the tag**, so candidates get several seeds and the baseline gets extra untagged seeds.
- **Gates** (a take failing any is out): full voice, words (Whisper word for word, no repeat, no tag read aloud), creak
  (no more than the untagged reads, plus a point), fits its shot, ending (no question rise past the voice's own
  endings; on a rhetorical line, no question word rising past its untagged reads), complete (no mid-word stop).
- **The feeling, measured** on its span against the untagged reads of the same words (`intent`): a smile you can hear,
  reassurance, sincerity, a hopeful or lively fall on the last word, a rhetorical question that doesn't rise, a calmer
  read than a reference take (without going flat), and more.
- **An approved take goes into the film as that exact file** (`slate voice tags adopt`): seeds aren't fully
  deterministic, so the pick is never re-voiced in the hope of getting it back.

Every take is a paid TTS call in live mode, through the one ElevenLabs client (estimate, budget, ledger row); in replay
the stand-in voice keeps the loop running offline, and replayed takes are never picked (a stand-in proves nothing about
a real read). These are measurements, not ears: the owner picks by listening to the ABC reel.

Files, per round: voice/tags/candidates.json (the plan, hand-written) and voice/tags/<round>/ (takes.json, raw/,
clean/, paced/, whisper/, metrics.json, picks.json, ranking.md, abc/<shot>/).
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from slate import jsonio, ledger, log, procs
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.voice import analyze, breath, lang
from slate.voice import tags as tags_mod
from slate.voice.film import VoiceFilm, now, record

GENERATED = "do not edit by hand: slate voice tags"
CONTEXTS = ("neighbours", "no_next", "none")
INTENTS = ("none", "smile", "smile_end", "reassure", "hopeful_end", "sincere", "rhetorical", "plain", "encourage",
           "warm", "alive_fall", "calmer")
GATE_NAMES = ("full_voice", "words", "creak", "fits", "ending", "complete")


def round_dir(vf: VoiceFilm, name: str) -> Path:
    if not name or not str(name).replace("-", "").replace("_", "").isalnum():
        raise UsageError(f"{name!r} isn't a round name: letters, digits, - and _ only.")
    return vf.dir / "voice" / "tags" / name


def candidates_path(vf: VoiceFilm) -> Path:
    return vf.dir / "voice" / "tags" / "candidates.json"


def load_candidates(vf: VoiceFilm) -> dict:
    path = candidates_path(vf)
    if not path.is_file():
        raise MissingPrerequisite("This film has no tag candidates yet.",
                                  hint=f"Write {vf.rel(path)}: per line an intent, tagged candidates with seeds, and "
                                       "baseline seeds (see the slate-voice skill's tag-loop reference).")
    data = jsonio.read(path)
    problems = jsonio.validate(data, "voice_tag_candidates")
    if problems:
        raise UsageError(f"{vf.rel(path)} doesn't match its schema:\n  " + "\n  ".join(problems[:12]))
    return data


# ---------------------------------------------------------------------------------------------------------------
# Planning and voicing


def plan(vf: VoiceFilm, cand: dict, *, only: list[str] | None = None, include_held: bool = False) -> list[dict]:
    """Every take the candidates file asks for. Refuses the whole plan (before anything is spent) when any candidate
    changes the words, carries a banned tag, or breaks the brand's tag policy."""
    policy = vf.profile.get("tag_policy") or {}
    jobs, problems = [], []
    for shot, spec in cand["lines"].items():
        if only and shot not in only:
            continue
        if spec.get("hold") and not include_held:
            continue
        ln = vf.line(shot)
        for c in spec["candidates"]:
            problems += [f"{shot} {c['key']}: {p}" for p in tags_mod.check(c["tagged"], ln.text, policy=policy)]
            ctx = c.get("context", "neighbours")
            role = c.get("role") or ("candidate" if tags_mod.tags_in(c["tagged"]) else "baseline")
            for seed in c["seeds"]:
                jobs.append({"shot": shot, "key": f"{c['key']}s{seed}", "cand": c["key"], "tagged": c["tagged"],
                             "seed": int(seed), "stability": c.get("stability"), "context": ctx, "role": role,
                             "text": ln.text, "intent": spec.get("intent", "none")})
        for seed in spec.get("baseline_seeds") or []:
            jobs.append({"shot": shot, "key": f"Us{seed}", "cand": "U", "tagged": ln.text, "seed": int(seed),
                         "stability": None, "context": "neighbours", "role": "baseline", "text": ln.text,
                         "intent": spec.get("intent", "none")})
    if problems:
        raise Refused("These tag candidates can't be sent:\n  - " + "\n  - ".join(problems),
                      hint="Tags change delivery, never words; and never whisper, soft, quiet or gentle.",
                      details={"problems": problems})
    keys = [(j["shot"], j["key"]) for j in jobs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise UsageError("Two candidates make the same take: " + ", ".join(f"{s} {k}" for s, k in dupes) + ".")
    return jobs


def _manifest(folder: Path) -> dict:
    data = jsonio.read(folder / "takes.json", default=None) or {}
    data.setdefault("takes", {})
    return data


def _save_manifest(folder: Path, data: dict) -> None:
    jsonio.write_atomic(folder / "takes.json", {"generated": GENERATED, **{k: v for k, v in data.items()
                                                                          if k != "generated"}})


def estimate(vf: VoiceFilm, jobs: list[dict], model: str) -> dict:
    chars = sum(len(j["tagged"]) for j in jobs)
    provider = "elevenlabs_oneiro" if vf.mode == "autonomous" else "elevenlabs"
    try:
        est = ledger.estimate(provider, "tts", model=model, chars=chars)
    except UsageError:
        est = ledger.estimate(provider, "tts", amount=chars, basis=f"no registry price for {model}; 1 credit per char")
    return {"takes": len(jobs), "characters": chars, "estimate": est.to_dict()}


def voice_takes(vf: VoiceFilm, folder: Path, jobs: list[dict]) -> list[dict]:
    """Voice the jobs not voiced yet, the film's way (its pinned voice, model and settings, the line's neighbours
    as plain context, then the clean-up and pacing). Resumable: takes.json is saved after every take."""
    from slate.vendors import elevenlabs  # noqa: PLC0415
    from slate.voice import profile as profile_mod  # noqa: PLC0415
    from slate.voice.clean import chain_for, clean_file, diagnose  # noqa: PLC0415
    from slate.voice.pace import pace_file, rubberband_info  # noqa: PLC0415
    from slate.voice.tts import current_voice  # noqa: PLC0415

    man = _manifest(folder)
    todo = [j for j in jobs if f"{j['shot']}_{j['key']}" not in man["takes"]]
    if not todo:
        return []
    prof = vf.profile
    profile_mod.enforce(prof, mode=vf.mode, what=f"{prof.get('brand')}'s voice")
    use = current_voice(vf)
    voice_id = use.get("voice_id") or profile_mod.require_voice(prof)
    client = elevenlabs.Client(mode=vf.mode, film=vf.dir)
    fmt = client.choose_format(prof.get("output_format") or "pcm_48000")
    ffmpeg_ok = bool(procs.which("ffmpeg"))
    rb = rubberband_info()
    pace_cfg = prof.get("pace") or {}
    tempo = float(pace_cfg.get("tempo", 1.06)) if rb["status"] != "missing" else 1.0
    notes = man.setdefault("notes", [])
    if not ffmpeg_ok and "no ffmpeg" not in " ".join(notes):
        notes.append("no ffmpeg: takes measured without the clean-up")
    if tempo == 1.0 and "Rubber Band" not in " ".join(notes):
        notes.append("Rubber Band isn't installed: takes paced by their pauses only")
    for d in ("raw", "clean", "paced"):
        (folder / d).mkdir(parents=True, exist_ok=True)
    made = []
    for j in todo:
        name = f"{j['shot']}_{j['key']}"
        prev, nxt = vf.neighbours(j["shot"])
        if j["context"] == "none":
            prev = nxt = None
        elif j["context"] == "no_next":
            nxt = None
        settings = dict(use.get("settings") or {})
        if j.get("stability") is not None:
            settings["stability"] = float(j["stability"])
        raw, clean, paced = (folder / d / f"{name}.wav" for d in ("raw", "clean", "paced"))
        res = client.tts(voice_id=voice_id, text=j["tagged"], model_id=use.get("model"), voice_settings=settings,
                         seed=j["seed"], previous_text=prev, next_text=nxt, output_format=fmt["format"], out=raw,
                         note=f"tag round {folder.name} {name}")
        if ffmpeg_ok:
            clean_file(res["file"], clean, chain_for(prof, diagnose(res["file"]).get("gap_floor_dbfs")))
        else:
            shutil.copyfile(res["file"], clean)
        pace = pace_file(clean, paced, tempo=tempo, pause_keep=float(pace_cfg.get("pause_keep", 0.8)),
                         min_pause=float(pace_cfg.get("min_pause", 0.25)),
                         threshold_db=float(pace_cfg.get("threshold_db", -45.0)), rb=rb if tempo != 1.0 else None)
        rec = {**j, "raw": vf.rel(raw), "clean": vf.rel(clean), "paced": vf.rel(paced), "voice_id": voice_id,
               "model": res["model"], "voice_settings": settings, "previous_text": prev, "next_text": nxt,
               "format": res["format"], "request_id": res["request_id"], "character_cost": res["character_cost"],
               "ledger_row": res["ledger_row"], "replay": res["replay"], "words": res.get("words"),
               "pace": {"tempo": pace["tempo"], "cuts": pace["cuts"]}, "at": now()}
        man["takes"][name] = rec
        _save_manifest(folder, man)
        for rel_path, src in ((rec["raw"], None), (rec["clean"], rec["raw"]), (rec["paced"], rec["clean"])):
            record(vf.dir, rel_path, producer="slate voice tags optimize", inputs=[src] if src else [],
                   tool_version=res["model"] if src is None else None)
        made.append(rec)
        log.info(f"voiced {name} ({res['character_cost'] or 0} credits{', replay' if res['replay'] else ''})")
    return made


# ---------------------------------------------------------------------------------------------------------------
# Measuring (the scope evaluation)


def _baseline_current(vf: VoiceFilm, shot: str) -> dict | None:
    """The film's own take of the line, as part of the untagged baseline (its clean stage, before pacing)."""
    sel = vf.selected(shot)
    if not sel:
        return None
    take, info = sel
    tj = vf.take_json(shot, take)
    if tags_mod.tags_in(tj.get("sent") or ""):
        return None  # the film's take is tagged: it's no baseline
    clean = info.get("clean") or info.get("raw")
    if not clean or not (vf.dir / clean).is_file():
        return None
    return {"shot": shot, "key": "current", "cand": "current", "tagged": tj.get("sent") or vf.line(shot).text,
            "role": "baseline", "text": vf.line(shot).text, "clean": clean,
            "paced": vf.rel(vf.unpolished_audio(shot)) if vf.unpolished_audio(shot) else clean,
            "replay": bool(tj.get("replay")), "words": tj.get("replay_words") if tj.get("replay") else None,
            "seed": tj.get("seed"), "context": "neighbours"}


def _words_for(vf: VoiceFilm, folder: Path, takes: dict[str, dict]) -> None:
    """Heard words for every take's clean file: a replayed take's own, else one Whisper run over all the others."""
    from slate.voice import align as align_mod  # noqa: PLC0415
    from slate.voice import measure  # noqa: PLC0415

    need = {name: vf.dir / t["clean"] for name, t in takes.items() if t.get("words") is None}
    if not need:
        return
    if not align_mod.whisper_info()["path"]:
        log.warn("Whisper isn't installed, so the word checks are unchecked")
        return
    language = lang.load(vf.language).get("whisper_language", vf.language)
    heard = align_mod.run_whisper(list(need.values()), folder / "whisper", language=language)
    for name, path in need.items():
        doc = heard.get(path)
        if doc:
            takes[name]["words"] = measure.whisper_words(doc)
            takes[name]["words_source"] = "openai-whisper"


def _span_start(words: list[dict] | None, text: str, index: int) -> float | None:
    if not index:
        return None
    if not words:
        return math.nan
    t = analyze.time_of(words, text, index)
    return t[0] if t else math.nan


def _mean_fields(summaries: list[dict | None]) -> dict:
    keys = {k for s in summaries if s for k, v in s.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {k: float(np.mean([s[k] for s in summaries if s and isinstance(s.get(k), (int, float))])) for k in keys}


def prosody(F: breath.Frames, words: list[dict] | None, text: str, base_med: float | None = None) -> dict:
    """The calm measures (the films' round 4): pitch on voiced, creak-free speech frames; the level contour's
    spread; words a second; the last word's rise on its creak-free frames."""
    from scipy.signal import medfilt  # noqa: PLC0415

    f = F.f0.copy()
    v = np.isfinite(f) & F.act
    if not v.any():
        return {}
    med = float(np.median(f[v]))
    v &= f >= 0.6 * med
    fv = f[v]
    st = 12 * np.log2(fv / med)
    lv = F.db[F.act]
    lv100 = np.array([lv[k:k + 10].mean() for k in range(0, len(lv) - 9, 10)]) if len(lv) >= 10 else lv
    on = np.flatnonzero(F.act)
    span = (on[-1] + 1 - on[0]) * breath.HOP if len(on) else 0.0
    nwords = len(lang.norm_words(text))
    out = {"f0_median_hz": round(med, 1), "range_p10_p90_st": round(float(np.percentile(st, 90) - np.percentile(st, 10)), 2),
           "f0_sd_st": round(float(np.std(st)), 2), "p90_hz": round(float(np.percentile(fv, 90)), 1),
           "level_sd_db": round(float(np.std(lv100)), 2), "speech_span_s": round(span, 3),
           "words_per_s": round(nwords / span, 2) if span > 0 else None, "voiced_frames": int(v.sum())}
    if base_med:
        out["peak_lift_st"] = round(12 * math.log2(out["p90_hz"] / base_med), 2)
        out["median_shift_st"] = round(12 * math.log2(med / base_med), 2)
    if words:
        k = len(lang.norm_words(text)) - 1
        tw = analyze.time_of(words, text, k) if k >= 0 else None
        if tw:
            a, b = max(0, int((tw[0] - 0.02) / breath.HOP)), int((tw[1] + 0.05) / breath.HOP)
            seg = f[a:b][v[a:b]]
            if len(seg) >= 4:
                sm = medfilt(seg, 3)
                q = max(1, len(sm) // 3)
                out["last_word_rise_creak_free_st"] = round(float(12 * np.log2(np.median(sm[-q:]) / np.median(sm[:q]))), 2)
    return out


def measure_round(vf: VoiceFilm, name: str, cand: dict, *, only: list[str] | None = None) -> dict:
    """Measure every take of the round (and the film's own untagged take of each line) on its clean file, span by
    span, against the untagged reads of the same words. Writes metrics.json."""
    folder = round_dir(vf, name)
    man = _manifest(folder)
    takes: dict[str, dict] = {}
    for key, t in man["takes"].items():
        if only and t["shot"] not in only:
            continue
        takes[key] = dict(t)
    for shot in cand["lines"]:
        if only and shot not in only:
            continue
        cur = _baseline_current(vf, shot)
        if cur:
            takes[f"{shot}_current"] = cur
    if not takes:
        raise MissingPrerequisite("This round has no takes to measure yet.", hint="slate voice tags optimize")
    _words_for(vf, folder, takes)
    prof = vf.profile
    lexicon = prof.get("lexicon") or []
    frames, metrics, spans = {}, {}, {}
    for key, t in takes.items():
        spec = cand["lines"].get(t["shot"]) or {}
        tagged = t["tagged"] if (t["role"] == "candidate" or tags_mod.tags_in(t["tagged"])) else None
        t["untagged"] = tagged is None and t["role"] == "baseline"
        F = breath.Frames(vf.dir / t["clean"])
        frames[key] = F
        m = analyze.measure_take(vf.dir / t["clean"], text=t["text"], words=t.get("words"), tagged=tagged,
                                 lexicon=lexicon, language=vf.language,
                                 emotion_from_word=spec.get("emotion_from_word"), frames=F, contour_every=None)
        metrics[key] = m
        spans[key] = {"tag": m.get("scope_word_index") or 0, "emotion": int(spec.get("emotion_from_word") or 0),
                      "line": 0}
    by_line: dict[str, list[str]] = {}
    for key, t in takes.items():
        by_line.setdefault(t["shot"], []).append(key)

    def span_of(key: str, idx: int) -> dict | None:
        start = _span_start(takes[key].get("words"), takes[key]["text"], idx)
        if start is not None and math.isnan(start):
            return None
        return breath.span(frames[key], start)

    needed = {(takes[k]["shot"], s) for k in takes for s in spans[k].values()}
    base = {(shot, s): [span_of(b, s) for b in by_line[shot] if takes[b]["untagged"]] for shot, s in needed}
    cal = breath.calibrate({f"{shot}|{s}": [x for x in v if x] for (shot, s), v in base.items()})
    untagged_index: dict[str, dict] = {}
    for (shot, s), _ in base.items():
        untagged_index.setdefault(shot, {})[str(s)] = {
            takes[b]["key"]: breath.breathiness(span_of(b, s), f"{shot}|{s}", cal)["index"]
            for b in by_line[shot] if takes[b]["untagged"] and span_of(b, s) and f"{shot}|{s}" in cal["means"]}
    base_med = {shot: (float(np.median([metrics[k]["line"]["f0_median_hz"] for k in keys
                                         if takes[k]["untagged"] and (metrics[k].get("line") or {}).get("f0_median_hz")]))
                       if any(takes[k]["untagged"] and (metrics[k].get("line") or {}).get("f0_median_hz") for k in keys)
                       else None) for shot, keys in by_line.items()}
    report: dict[str, Any] = {"generated": GENERATED, "round": name, "film": vf.id, "calibration_sds": cal["sds"],
                              "untagged_breath_index": untagged_index, "takes": {}, "at": now()}
    for key, t in takes.items():
        m = metrics[key]
        rec_spans = {}
        for span_name, s in spans[key].items():
            sm = span_of(key, s)
            ok_base = f"{t['shot']}|{s}" in cal["means"]
            rec_spans[span_name] = {"start_word": s, "summary": sm,
                                    "breath": breath.breathiness(sm, f"{t['shot']}|{s}", cal) if sm and ok_base else None,
                                    "baseline": _mean_fields(base.get((t["shot"], s)) or []),
                                    "baseline_n": len([x for x in base.get((t["shot"], s)) or [] if x])}
        paced = vf.dir / t["paced"] if t.get("paced") else None
        from slate.voice import audio as audio_mod  # noqa: PLC0415
        from slate.voice.tts import fit_of  # noqa: PLC0415

        pdur = audio_mod.duration(paced) if paced and paced.is_file() else None
        fit = fit_of(vf, t["shot"], pdur) if pdur is not None else {"fits": None}
        report["takes"][key] = {
            "shot": t["shot"], "key": t["key"], "cand": t.get("cand"), "tagged": t["tagged"] if not t["untagged"] else None,
            "untagged": t["untagged"], "role": t["role"], "intent": (cand["lines"].get(t["shot"]) or {}).get("intent"),
            "seed": t.get("seed"), "context": t.get("context"), "stability": t.get("stability"),
            "replay": bool(t.get("replay")), "request_id": t.get("request_id"),
            "clean": t["clean"], "paced": t.get("paced"), "paced_s": round(pdur, 3) if pdur is not None else None,
            "fits": fit.get("fits"), "whisper": m.get("whisper"), "pacing": m.get("pacing"),
            "creak_words": m.get("creak_words"), "end_drop_db": m.get("end_drop_db"),
            "last_word_rise_st": m.get("last_word_rise_st"), "last_word_contour": m.get("last_word_contour"),
            "question_words": m.get("question_words"),
            "end_tail_below_speech_db": analyze.tail_below_speech_db(paced) if paced and paced.is_file() else None,
            "lufs": m.get("lufs"), "true_peak_dbtp": m.get("true_peak_dbtp"),
            "prosody": prosody(frames[key], t.get("words"), t["text"], base_med.get(t["shot"])),
            "line_clarity_cpps_db": _line_clarity(frames[key]),
            "breathier": any(r["breath"] and r["breath"]["breathier"] for r in rec_spans.values()),
            "spans": rec_spans}
    jsonio.write_atomic(folder / "metrics.json", report)
    record(vf.dir, vf.rel(folder / "metrics.json"), producer="slate voice tags optimize",
           inputs=[t["clean"] for t in takes.values()])
    return report


def _line_clarity(F: breath.Frames) -> float | None:
    c = F.cpps[F.act & F.voiced]
    c = c[np.isfinite(c)]
    return round(float(np.median(c)), 2) if len(c) else None


# ---------------------------------------------------------------------------------------------------------------
# Picking (the films' tag_pick, generalized)


def _st(a: float | None, b: float | None) -> float | None:
    return 12 * math.log2(a / b) if a and b else None


def emotion(intent: str, sm: dict, base: dict, ctx: dict) -> tuple[bool, float, dict]:
    """(present, score, notes) for an emotion span's summary `sm` against the untagged mean `base`. Scores reward a
    moderate, natural shift and stop rewarding past the point where it would turn theatrical."""
    n: dict = {}
    if intent in (None, "none"):
        return True, 0.0, {"intent": "none: the gates only"}
    if not sm or not base:
        return False, -9.0, {"note": "the emotion span couldn't be measured"}
    dlev = (sm.get("effort_level_db") or 0) - (base.get("effort_level_db") or 0)
    if intent == "rhetorical":
        got = {k: w for k, w in (ctx.get("question_words") or {}).items()
               if w.get("rise_st") is not None and (ctx.get("base_q") or {}).get(k)}
        n["question_word_rises_st"] = {w["word"]: w["rise_st"] for w in got.values()}
        if not got:
            return False, -9.0, n
        drop = sum(ctx["base_q"][k]["mean"] - w["rise_st"] for k, w in got.items()) / len(got)
        n["rise_reduction_st"] = round(drop, 2)
        return drop >= 1.0, min(drop, 4.0), n
    if intent == "plain":
        dsd = sm["f0_sd_st"] - base["f0_sd_st"] if sm.get("f0_sd_st") and base.get("f0_sd_st") else None
        n.update({"f0_sd_shift_st": None if dsd is None else round(dsd, 2), "effort_level_shift_db": round(dlev, 2)})
        return dsd is not None and dsd <= 0.0 and dlev >= -1.0, min(-(dsd or 0), 1.5) + 0.2 * dlev, n
    if intent in ("encourage", "warm"):
        df0 = _st(sm.get("f0_median_hz"), base.get("f0_median_hz"))
        dc = (sm["centroid_hz"] / base["centroid_hz"] - 1) if sm.get("centroid_hz") and base.get("centroid_hz") else 0
        n.update({"f0_shift_st": None if df0 is None else round(df0, 2), "centroid_shift_pct": round(100 * dc, 1),
                  "effort_level_shift_db": round(dlev, 2)})
        lo = 0.5 if intent == "encourage" else 0.3
        lift = df0 is not None and lo <= df0 <= 3.0
        present = (lift or (intent == "warm" and dc >= 0.03)) and dlev >= -1.0
        return present, 2 - abs((df0 or 0) - 1.2) + 5 * min(max(dc, 0), 0.1) + 0.2 * dlev, n
    if intent in ("smile", "smile_end"):
        df0 = _st(sm.get("f0_median_hz"), base.get("f0_median_hz"))
        dc = (sm["centroid_hz"] / base["centroid_hz"] - 1) if sm.get("centroid_hz") and base.get("centroid_hz") else 0
        f2_ok = (sm.get("voiced_frames") or 0) >= 80 and sm.get("f2_hz") and base.get("f2_hz")
        df2 = (sm["f2_hz"] / base["f2_hz"] - 1) if f2_ok else None
        n.update({"f0_shift_st": None if df0 is None else round(df0, 2), "centroid_shift_pct": round(100 * dc, 1),
                  "f2_shift_pct": round(100 * df2, 1) if df2 is not None else "n/a (span too short)"})
        brighter = dc >= 0.04 or (df2 is not None and df2 >= 0.02) or df2 is None
        present = df0 is not None and 0.8 <= df0 <= 3.5 and brighter
        score = 3 - abs((df0 or 0) - 1.8) + 5 * min(max(dc, 0), 0.1)
        if intent == "smile_end":
            lift, blift = sm.get("lift_vs_rest_st"), base.get("lift_vs_rest_st")
            n.update({"lift_vs_rest_st": lift, "untagged_lift_st": None if blift is None else round(blift, 2)})
            present = present and lift is not None and blift is not None and lift >= blift
            score += min(lift - blift, 2) if lift is not None and blift is not None else 0
        return present, score, n
    if intent == "reassure":
        dsd = sm["f0_sd_st"] - base["f0_sd_st"] if sm.get("f0_sd_st") and base.get("f0_sd_st") else None
        n.update({"f0_sd_shift_st": None if dsd is None else round(dsd, 2), "effort_level_shift_db": round(dlev, 2)})
        return dsd is not None and dsd <= -0.2 and dlev >= -1.0, min(-(dsd or 0), 1.5) + 0.2 * dlev, n
    if intent == "hopeful_end":
        lift, blift = sm.get("lift_vs_rest_st"), base.get("lift_vs_rest_st")
        dv = sm["voiced_frac"] - base["voiced_frac"] if sm.get("voiced_frac") is not None and \
            base.get("voiced_frac") is not None else 0.0
        n.update({"lift_vs_rest_st": lift, "untagged_lift_st": None if blift is None else round(blift, 2),
                  "voiced_shift": round(dv, 3), "effort_level_shift_db": round(dlev, 2)})
        gain = lift - blift if lift is not None and blift is not None else None
        return gain is not None and gain >= 0.5 and dv >= -0.03 and dlev >= -1.0, min(gain or 0, 4) + 0.2 * dlev, n
    if intent == "sincere":
        dsd = sm["f0_sd_st"] - base["f0_sd_st"] if sm.get("f0_sd_st") and base.get("f0_sd_st") else None
        dw = ctx["wps"] / ctx["base_wps"] - 1 if ctx.get("wps") and ctx.get("base_wps") else 0
        n.update({"f0_sd_shift_st": None if dsd is None else round(dsd, 2), "rate_shift_pct": round(100 * dw, 1),
                  "effort_level_shift_db": round(dlev, 2)})
        present = ((dsd is not None and dsd <= -0.2) or dw <= -0.03) and dlev >= -1.0
        return present, min(-(dsd or 0), 1.5) + min(-10 * dw, 1) + 0.2 * dlev, n
    if intent == "alive_fall":
        c, o = ctx.get("contour") or {}, ctx.get("reference_contour") or {}
        fall, rng, en = c.get("fall_peak_to_end_st"), c.get("range_st"), c.get("peak_vs_rest_p90_db")
        o_rng, o_en = o.get("range_st"), o.get("peak_vs_rest_p90_db")
        if None in (fall, rng, en, o_rng, o_en):
            return False, -9.0, {"last_word": "n/a (too little voicing)"}
        rise = c.get("rise_to_peak_st", 0.0)
        ends_low = c["end_hz"] <= (c.get("line_median_hz") or c["end_hz"]) * 2 ** (-1 / 12)
        clarity, base_clar = c.get("voiced_cpps_median_db"), ctx.get("base_clarity")
        clear = clarity is not None and base_clar is not None and clarity >= base_clar
        n.update({"last_word_rise_to_peak_st": rise, "last_word_fall_st": fall, "last_word_range_st": rng,
                  "reference_range_st": o_rng, "last_word_energy_db": en, "reference_energy_db": o_en,
                  "ends_low": ends_low, "last_word_clarity_db": clarity, "untagged_clarity_min_db": base_clar})
        present = 1.5 <= fall <= 4.0 and rng > o_rng and en >= o_en - 1.0 and clear
        score = (2 - abs(fall - 2.5) + 0.5 * min(max(rng - o_rng, 0), 2) + (0.5 if rise >= 0.5 else 0)
                 + 0.3 * min(en - o_en, 2) + (0.5 if ends_low else 0))
        return present, score, n
    if intent == "calmer":
        p, ref, u = ctx.get("prosody") or {}, ctx.get("reference_prosody") or {}, ctx.get("untagged_prosody") or {}
        need = ("range_p10_p90_st", "peak_lift_st", "level_sd_db", "words_per_s", "median_shift_st")
        if not ref or any(p.get(k) is None for k in need) or any(ref.get(k) is None for k in need) or not u:
            return False, -9.0, {"calmer": "n/a (the reference or this take couldn't be measured)"}
        calmer = {"pitch_range": p["range_p10_p90_st"] <= ref["range_p10_p90_st"] - 1.0,
                  "peak_lift": p["peak_lift_st"] <= ref["peak_lift_st"] - 1.0,
                  "last_word_rise": (p.get("last_word_rise_creak_free_st") is not None and
                                     ref.get("last_word_rise_creak_free_st") is not None and
                                     p["last_word_rise_creak_free_st"] < ref["last_word_rise_creak_free_st"]),
                  "energy_swing": p["level_sd_db"] <= ref["level_sd_db"],
                  "rate_not_rushed": p["words_per_s"] <= max(u["words_per_s"]) * 1.05}
        not_flat = {"range": p["range_p10_p90_st"] >= 0.75 * float(np.median(u["range_p10_p90_st"])),
                    "register": p["median_shift_st"] >= -1.5,
                    "not_dragging": p["words_per_s"] >= min(min(u["words_per_s"]), ref["words_per_s"]) * 0.9}

        def credit(k: str) -> float:
            a, z = ref[k], float(np.median(u[k]))
            return 0.0 if abs(a - z) < 1e-6 else float(min(max((a - p[k]) / (a - z), 0.0), 1.0))
        score = credit("range_p10_p90_st") + credit("peak_lift_st") + credit("level_sd_db")
        n.update({"calmer": calmer, "not_flat": not_flat, "calm_score": round(score, 3)})
        return all(calmer.values()) and all(not_flat.values()), score, n
    return False, 0.0, {"note": f"unknown intent {intent!r}"}


def pick_round(vf: VoiceFilm, name: str) -> dict:
    """Gate and rank every candidate of the round (reads metrics.json). Writes picks.json and ranking.md."""
    folder = round_dir(vf, name)
    rep = jsonio.read(folder / "metrics.json", default=None)
    if not rep:
        raise MissingPrerequisite(f"Round {name} hasn't been measured yet.", hint=f"slate voice tags optimize --round {name}")
    cand = load_candidates(vf)
    T = rep["takes"]
    UB = rep["untagged_breath_index"]
    picks, table = {}, {}
    for shot in sorted({t["shot"] for t in T.values()}):
        spec = cand["lines"].get(shot) or {}
        takes = {k: v for k, v in T.items() if v["shot"] == shot}
        base_takes = [v for v in takes.values() if v["untagged"]]
        rows = []
        if not base_takes:
            picks[shot] = {"pick": None, "runner_up": None, "stays_untagged": True,
                           "why": "no untagged reads of this line to measure against (add baseline_seeds)"}
            table[shot] = []
            continue
        base_wps = np.mean([v["pacing"]["wps"] for v in base_takes if (v.get("pacing") or {}).get("wps")] or [0]) or None
        base_creak = max((v["spans"]["line"]["summary"] or {}).get("creak_frac", 0) for v in base_takes)
        base_rise = max([v["last_word_rise_st"] for v in base_takes if v.get("last_word_rise_st") is not None] or [0.0])
        clar = [(v.get("last_word_contour") or {}).get("voiced_cpps_median_db") for v in base_takes]
        base_clarity = min([c for c in clar if c is not None], default=None)
        base_q: dict[str, list] = {}
        for v in base_takes:
            for k, w in (v.get("question_words") or {}).items():
                if w.get("rise_st") is not None:
                    base_q.setdefault(k, []).append(w["rise_st"])
        base_q = {k: {"max": max(x), "mean": sum(x) / len(x)} for k, x in base_q.items()}
        rise_cap = max(base_rise, 0.0) + 3.0
        u_pros = {k: [v["prosody"][k] for v in base_takes if (v.get("prosody") or {}).get(k) is not None]
                  for k in ("range_p10_p90_st", "peak_lift_st", "level_sd_db", "words_per_s", "median_shift_st")}
        ref_key = spec.get("reference") or "current"
        ref_take = takes.get(f"{shot}_{ref_key}") or next((v for v in takes.values() if v["key"] == ref_key), None)
        for k, v in takes.items():
            if v["untagged"]:
                continue
            w = v.get("whisper")
            vs_untagged = {}
            for n, s in v["spans"].items():
                rng = list((UB.get(shot) or {}).get(str(s["start_word"]), {}).values())
                if s.get("breath") and rng:
                    vs_untagged[n] = {"index": s["breath"]["index"], "untagged_min": min(rng),
                                      "untagged_max": max(rng),
                                      "over_untagged_max": round(s["breath"]["index"] - max(rng), 2)}
            within = bool(vs_untagged) and all(x["over_untagged_max"] <= 0 for x in vs_untagged.values())
            line_sum = v["spans"]["line"]["summary"] or {}
            gates = {
                "full_voice": None if not vs_untagged else (not v["breathier"] and within),
                "words": None if not w else (w["wer"] == 0 and not w["repeats"] and not w["spoken_tag_words"]),
                "creak": None if not line_sum else line_sum.get("creak_frac", 0) <= base_creak + 0.01,
                "fits": v.get("fits"),
                "ending": None if v.get("last_word_rise_st") is None and not w else (
                    (v.get("last_word_rise_st") is None or v["last_word_rise_st"] <= rise_cap) and
                    (not spec.get("rhetorical") or all(q.get("rise_st") is None or qk not in base_q
                                                       or q["rise_st"] <= base_q[qk]["max"] + 1.0
                                                       for qk, q in (v.get("question_words") or {}).items()))),
                "complete": None if v.get("end_tail_below_speech_db") is None
                else v["end_tail_below_speech_db"] >= analyze.COMPLETE_DB,
            }
            if v.get("replay"):  # a stand-in voice proves nothing about the real read
                gates = {g: None for g in gates}
            es = v["spans"]["emotion"]
            ctx = {"question_words": v.get("question_words"), "base_q": base_q,
                   "wps": (v.get("pacing") or {}).get("wps"), "base_wps": base_wps,
                   "contour": v.get("last_word_contour"),
                   "reference_contour": (ref_take or {}).get("last_word_contour"), "base_clarity": base_clarity,
                   "prosody": v.get("prosody"), "reference_prosody": (ref_take or {}).get("prosody"),
                   "untagged_prosody": u_pros}
            intent = v.get("intent") or spec.get("intent") or "none"
            present, score, notes = emotion(intent, es["summary"], es["baseline"], ctx)
            bidx = max((s["breath"]["index"] for s in v["spans"].values() if s.get("breath")), default=0.0)
            passes = all(x is True for x in gates.values())
            rows.append({"take": k, "key": v["key"], "tagged": v["tagged"], "intent": intent, "gates": gates,
                         "passes": passes, "unchecked": [g for g, x in gates.items() if x is None],
                         "emotion_present": bool(present), "emotion": notes, "breath_index_max": round(bidx, 2),
                         "breath_within_untagged": within, "breath_vs_untagged": vs_untagged,
                         "last_word_rise_st": v.get("last_word_rise_st"), "last_word_rise_cap_st": round(rise_cap, 2),
                         "score": round(score - 0.5 * bidx, 3), "paced_s": v.get("paced_s"), "replay": v.get("replay"),
                         "family": [tuple(tags_mod.tags_in(v["tagged"] or "")), v.get("context")]})
        rows.sort(key=lambda r: (not (r["passes"] and r["emotion_present"]), not r["passes"], -r["score"]))
        ok = [r for r in rows if r["passes"] and r["emotion_present"]]
        picks[shot] = {"pick": ok[0]["take"] if ok else None, "runner_up": ok[1]["take"] if len(ok) > 1 else None,
                       "stays_untagged": not ok,
                       "why": None if ok else ("replayed stand-ins can't be judged: voice the round with --live"
                                               if rows and all(r["replay"] for r in rows) else
                                               "no candidate passed every gate with its feeling measured")}
        table[shot] = rows
    out = {"generated": GENERATED, "round": name, "film": vf.id, "picks": picks, "ranking": table, "at": now(),
           "note": "Measurements, not ears: listen to the ABC reel (slate voice tags abc) before adopting a pick."}
    jsonio.write_atomic(folder / "picks.json", out)
    _ranking_md(folder / "ranking.md", out)
    return out


def _ranking_md(path: Path, picks: dict) -> None:
    lines = [f"# Tag round {picks['round']}: the ranking", "",
             f"Written by `slate voice tags pick` for {picks['film']}. Gates: {', '.join(GATE_NAMES)} "
             "(✓ pass, ✗ fail, · unchecked). Measurements, not ears.", ""]
    for shot, rows in picks["ranking"].items():
        p = picks["picks"][shot]
        lines += [f"## {shot}", "", f"Pick: `{p['pick']}`, runner-up: `{p['runner_up']}`" if p["pick"] else
                  f"No pick: {p.get('why')}", "", "| Take | Tagged | Gates | Feeling | Breath index | Score |",
                  "|---|---|---|---|---|---|"]
        for r in rows:
            g = "".join("✓" if r["gates"][x] is True else ("✗" if r["gates"][x] is False else "·") for x in GATE_NAMES)
            lines.append(f"| `{r['take']}` | {r['tagged'] or ''} | {g} | {'yes' if r['emotion_present'] else 'no'} | "
                         f"{r['breath_index_max']:+.2f} | {r['score']} |")
        lines.append("")
    jsonio.write_text_atomic(path, "\n".join(lines))


# ---------------------------------------------------------------------------------------------------------------
# Listening (ABC) and adopting the pick


def _last_word_track(vf: VoiceFilm, t: dict) -> list[tuple[float, float]]:
    from scipy.signal import medfilt  # noqa: PLC0415

    words = t.get("words")
    if not words:
        return []
    F = breath.Frames(vf.dir / t["clean"])
    k = len(lang.norm_words(t["text"])) - 1
    tw = analyze.time_of(words, t["text"], k) if k >= 0 else None
    if not tw:
        return []
    a0, b0 = max(0, int((tw[0] - 0.02) / breath.HOP)), int((tw[1] + 0.25) / breath.HOP)
    f = F.f0[a0:b0]
    idx = np.flatnonzero(np.isfinite(f))
    if len(idx) < 4:
        return []
    v = medfilt(f[idx], 3)
    return [((a0 + i) * breath.HOP * 1000 - tw[0] * 1000, float(hz)) for i, hz in zip(idx, v)]


def abc(vf: VoiceFilm, name: str, shot: str, *, n: int = 3, also: list[str] | None = None,
        sheet: bool = True) -> dict:
    """The listening package for one line: A (the film's take), then the best passing candidates (one per tag
    family first), each level-matched to -20 LUFS, an ABC reel, abc.json, A/B sheets and the last word's pitch."""
    from slate.voice import reel  # noqa: PLC0415
    from slate.voice import sheet as sheet_mod  # noqa: PLC0415

    folder = round_dir(vf, name)
    picks = jsonio.read(folder / "picks.json", default=None)
    if not picks:
        raise MissingPrerequisite(f"Round {name} has no picks yet.", hint=f"slate voice tags pick --round {name}")
    man = _manifest(folder)
    rows = [r for r in picks["ranking"].get(shot) or [] if r["passes"] and r["emotion_present"]]
    chosen, seen = [], set()
    for r in rows:
        fam = tuple(map(tuple, [r["family"][0]])) + (r["family"][1],)
        if fam not in seen:
            chosen.append(r)
            seen.add(fam)
        if len(chosen) == n:
            break
    for r in rows:
        if len(chosen) >= n:
            break
        if r not in chosen:
            chosen.append(r)
    for key in also or []:
        r = next((x for x in picks["ranking"].get(shot) or [] if x["take"] in (key, f"{shot}_{key}")), None)
        if r is None:
            raise UsageError(f"Round {name} has no take {key} for {shot}.")
        if r not in chosen:
            chosen.append(r)
    if not chosen:
        raise Refused(f"{shot}: no take in round {name} passes every gate with its feeling measured, so there's "
                      "nothing to hear against the film's take.",
                      hint="Add --also <take> to hear a near miss (it's labelled with what it missed).")
    out_dir = folder / "abc" / shot
    out_dir.mkdir(parents=True, exist_ok=True)
    current = vf.unpolished_audio(shot)
    if current is None:
        raise MissingPrerequisite(f"{shot} has no film take to hear the candidates against.",
                                  hint=f"slate voice tts --only {shot}")
    items, report, reel_items = [], {"shot": shot, "round": name, "target_lufs": reel.TARGET_LUFS, "gap_s": 0.8,
                                     "takes": {}}, []
    y, info = reel.matched(current)
    from slate.voice import audio as audio_mod  # noqa: PLC0415

    audio_mod.write_wav(out_dir / "A_film.wav", y, reel.SR, 24)
    report["takes"]["A"] = {"take": "the film's take", "file": vf.rel(out_dir / "A_film.wav"), **info}
    reel_items.append({"file": out_dir / "A_film.wav", "title": "A: the film's take"})
    letters = "BCDEFGHIJ"
    for i, r in enumerate(chosen):
        t = man["takes"].get(r["take"])
        if t is None:
            continue
        letter = letters[i]
        y, info = reel.matched(vf.dir / t["paced"])
        path = out_dir / f"{letter}_{t['key']}.wav"
        audio_mod.write_wav(path, y, reel.SR, 24)
        misses = [g for g, v in r["gates"].items() if v is not True] + ([] if r["emotion_present"] else ["feeling"])
        report["takes"][letter] = {"take": r["take"], "tagged": r["tagged"], "passes": r["passes"] and
                                   r["emotion_present"], "misses": misses, "score": r["score"],
                                   "emotion": r["emotion"], "breath_index_max": r["breath_index_max"],
                                   "file": vf.rel(path), **info}
        reel_items.append({"file": path, "title": f"{letter}: {r['tagged']}"})
        items.append((letter, r, t))
    reel_info = reel.build(reel_items, out_dir / "reel.wav", gap_s=0.8)
    report["reel"] = vf.rel(Path(reel_info["reel"]))
    report["order"] = ["A"] + [i[0] for i in items]
    sheets = []
    if sheet:
        cur_m = analyze.measure_take(current, text=vf.line(shot).text, contour_every=None)
        for letter, r, t in items:
            m = analyze.measure_take(vf.dir / t["clean"], text=t["text"], words=t.get("words"), tagged=t["tagged"],
                                     contour_every=None)
            ok = r["passes"] and r["emotion_present"]
            head = (f"{letter} measures: " + ", ".join(f"{k} {v}" for k, v in list(r["emotion"].items())[:4])
                    + f". Breath index {r['breath_index_max']:+.2f}; "
                    + ("passes every gate." if ok else "misses " + ", ".join(report['takes'][letter]['misses'])))
            png = sheet_mod.ab_sheet([{"label": "A · the film's take", "path": current, "measures": cur_m},
                                      {"label": f"{letter} · {t['key']}: {r['tagged']}", "path": vf.dir / t["clean"],
                                       "measures": m, "words": t.get("words"), "vs": analyze.ab(cur_m, m),
                                       "span_start_s": m.get("scope_from_s") or None}],
                                     out_dir / f"{shot}_{letter}_{t['key']}.png",
                                     title=f"{shot}: the film's take against {t['key']}", headline=head, ok=ok)
            sheets.append(vf.rel(png))
        entries = []
        for i, (letter, r, t) in enumerate(items):
            mt = jsonio.read(folder / "metrics.json")["takes"].get(r["take"]) or {}
            entries.append((f"{letter} · {t['key']}", sheet_mod.ACCENTS[i % len(sheet_mod.ACCENTS)],
                            _last_word_track(vf, t), mt.get("last_word_contour")))
        if entries:
            sheets.append(vf.rel(sheet_mod.contour_chart(entries, out_dir / f"{shot}_last_word.png",
                                                         title=f"{shot}: the last word's pitch, every take on one axis",
                                                         note="Smoothed pitch (3-frame median) from the word's start.")))
    report["sheets"] = sheets
    report["note"] = "Level-matched, so nothing wins on loudness. Listen to reel.wav, then adopt the one you pick."
    jsonio.write_atomic(out_dir / "abc.json", {"generated": GENERATED, **report})
    return report


def adopt(vf: VoiceFilm, name: str, shot: str, key: str, *, force: bool = False) -> dict:
    """Put a round's take into the film as that exact file (a new take of the shot, selected), with its tagged text as
    the line's direction and the round's measurements carried along. Seeds aren't fully deterministic, so the pick
    is never re-voiced in the hope of getting it back."""
    folder = round_dir(vf, name)
    man = _manifest(folder)
    t = man["takes"].get(key) or man["takes"].get(f"{shot}_{key}")
    if t is None or t["shot"] != shot:
        raise UsageError(f"Round {name} has no take {key} for {shot}.")
    if vf.locked(shot) and not force:
        raise Refused(f"{shot}'s take is approved; adopting another take replaces approved work.",
                      hint="Only with the person's go: run it again with --force, then approve the new take.")
    policy = vf.profile.get("tag_policy") or {}
    tags_mod.require(t["tagged"], vf.line(shot).text, policy=policy, where=shot)
    picks = jsonio.read(folder / "picks.json", default=None) or {}
    row = next((r for r in (picks.get("ranking") or {}).get(shot) or [] if r["take"] == f"{shot}_{t['key']}"), None)
    take = vf.next_take_id(shot)
    tdir = vf.take_dir(shot)
    tdir.mkdir(parents=True, exist_ok=True)
    files = {"raw": tdir / f"{take}.wav", "clean": tdir / f"{take}.clean.wav", "paced": tdir / f"{take}.paced.wav"}
    for stage, dst in files.items():
        shutil.copyfile(vf.dir / t[stage], dst)
    from slate.voice import audio as audio_mod  # noqa: PLC0415
    from slate.voice.tts import fit_of  # noqa: PLC0415

    seconds = audio_mod.duration(files["raw"])
    data = {"generated": "do not edit by hand: slate voice tags adopt", "shot": shot, "take": take, "film": vf.id,
            "brand": vf.profile.get("brand"), "mode": vf.mode, "text": vf.line(shot).text, "sent": t["tagged"],
            "tags": tags_mod.tags_in(t["tagged"]), "previous_text": t.get("previous_text"),
            "next_text": t.get("next_text"), "model": t.get("model"), "voice_id": t.get("voice_id"),
            "voice_settings": t.get("voice_settings") or {}, "seed": t.get("seed"), "format": t.get("format"),
            "request_id": t.get("request_id"), "character_cost": t.get("character_cost"),
            "ledger_row": t.get("ledger_row"), "replay": bool(t.get("replay")), "file": vf.rel(files["raw"]),
            "sha256": jsonio.sha256(files["raw"]), "seconds": round(seconds, 3),
            "fit": fit_of(vf, shot, audio_mod.duration(files["paced"])), "created_at": now(),
            "stages": {"clean": {"file": vf.rel(files["clean"]), "sha256": jsonio.sha256(files["clean"]),
                                 "source": vf.rel(files["raw"]), "from_round": name},
                       "pace": {"file": vf.rel(files["paced"]), "sha256": jsonio.sha256(files["paced"]),
                                "source": vf.rel(files["clean"]), **(t.get("pace") or {})},
                       "tag_round": {"round": name, "key": t["key"], "file": vf.rel(files["clean"]),
                                     "sha256": jsonio.sha256(files["clean"]),
                                     "gates": (row or {}).get("gates"), "emotion_present": (row or {}).get("emotion_present"),
                                     "breath_index_max": (row or {}).get("breath_index_max"),
                                     "picked": ((picks.get("picks") or {}).get(shot) or {}).get("pick") ==
                                     f"{shot}_{t['key']}"}}}
    if t.get("replay") and t.get("words"):
        data["replay_words"] = t["words"]
    vf.save_take_json(shot, take, data)
    vf.update_take(shot, take, raw=vf.rel(files["raw"]), clean=vf.rel(files["clean"]), paced=vf.rel(files["paced"]),
                   polished=None, align=None, replay=bool(t.get("replay")), seconds=round(seconds, 3),
                   json=vf.rel(tdir / f"{take}.json"))
    vf.select_take(shot, take)
    if vf.tagged_for(shot) != t["tagged"]:
        vf.set_direction(shot, t["tagged"])
    for stage, dst in files.items():
        record(vf.dir, vf.rel(dst), producer="slate voice tags adopt", inputs=[t[stage]],
               note=f"round {name}'s take {t['key']}, adopted as the exact file")
    return {"shot": shot, "take": take, "from": f"{name}/{t['key']}", "tagged": t["tagged"],
            "files": {k: vf.rel(v) for k, v in files.items()},
            "next": f"slate voice align --only {shot}, then slate voice check (and the polish, if the film uses it)"}


def optimize(film_dir: str | Path, *, name: str = "main", only: list[str] | None = None, estimate_only: bool = False,
             generate_only: bool = False, measure_only: bool = False, include_held: bool = False) -> dict:
    """Plan, voice (paid in live mode), transcribe and measure a round. Returns what happened."""
    from slate import config  # noqa: PLC0415
    from slate.voice.tts import current_voice  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    cand = load_candidates(vf)
    jobs = plan(vf, cand, only=only, include_held=include_held)
    folder = round_dir(vf, name)
    man = _manifest(folder)
    new = [j for j in jobs if f"{j['shot']}_{j['key']}" not in man["takes"]]
    model = current_voice(vf).get("model") or config.model("tts")
    est = estimate(vf, new, model)
    out: dict[str, Any] = {"round": name, "planned": len(jobs), "new": len(new), "estimate": est}
    if estimate_only:
        out["estimate_only"] = True
        return out
    if not measure_only:
        out["voiced"] = [f"{r['shot']}_{r['key']}" for r in voice_takes(vf, folder, new)]
    if generate_only:
        return out
    rep = measure_round(vf, name, cand, only=only)
    out["measured"] = len(rep["takes"])
    out["metrics"] = vf.rel(folder / "metrics.json")
    out["next"] = f"slate voice tags pick --round {name}"
    return out
