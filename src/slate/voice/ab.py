"""Tag A/B takes: the untagged line against tagged variants, voiced the film's way, measured, level-matched.

From the films' `tag_samples.py`, `tag_ab.py` and `tag_ab_v2.py` (round 2, after the owner heard whispery tags):

- Every variant must strip back to the line exactly, with no banned tag, before anything is sent.
- Each take is voiced like the film's takes: the film's pinned voice, model and settings, one seed for every
  variant of the line, the neighbouring lines as plain context. Then the film's clean-up and pacing.
- Every take is measured before anyone listens: Whisper word for word (no repeat, no tag read aloud), breath and
  vocal effort against the untagged takes of the same words, creak, and whether the paced take still fits its shot.
  Breathier delivery is a rejection only when this voice explicitly requires full delivery.
- The clips are level-matched to -20 LUFS: `A.wav` (untagged), `<key>.wav` per variant, `A<key>.wav` (A, 0.8 s, then
  the variant) and `reel.wav` with every pair, so neither side wins by being louder.

These are measurements, not ears. The owner picks by listening; `slate voice tags set` then records the pick.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from slate import jsonio, log, procs
from slate.errors import Refused, UsageError
from slate.vendors import elevenlabs
from slate.voice import align as align_mod
from slate.voice import audio as audio_mod
from slate.voice import breath, lang, measure, reel
from slate.voice import profile as profile_mod
from slate.voice import tags as tags_mod
from slate.voice.clean import chain_for, clean_file, diagnose
from slate.voice.film import VoiceFilm, get_pin, now
from slate.voice.pace import pace_file, rubberband_info
from slate.voice.tts import current_voice, fit_of

KEYS = "BCDEFGHIJKLMNOPQRSTUVWXYZ"


def word_start(script: str, words: list[dict], index: int) -> float | None:
    """When script word `index` starts in a take, from its aligned words (None if it wasn't heard)."""
    hyp, owner = [], []
    for k, w in enumerate(words):
        for tok in lang.norm_words(w["word"]):
            hyp.append(tok)
            owner.append(k)
    _, _, pairs = measure.align_words(lang.norm_words(script), hyp)
    for r, h in pairs:
        if r == index:
            return float(words[owner[h]]["start"])
    return None


def run(film_dir: str | Path, shot: str, variants: list[str], *, seed: int | None = None, control: bool = True,
        raw: bool = False) -> dict:
    vf = VoiceFilm(film_dir)
    ln = vf.line(shot)
    prof = vf.profile
    profile_mod.enforce(prof, mode=vf.mode, what=f"{prof.get('brand')}'s voice")
    if not variants:
        raise UsageError("Give at least one tagged variant (--variant \"[tag] the line\").")
    policy = prof.get("tag_policy") or {}
    problems = [f"variant {i}: {p}" for i, v in enumerate(variants, 1) for p in tags_mod.check(v, ln.text,
                                                                                              policy=policy)]
    if problems:
        raise Refused("These variants can't be sent:\n  - " + "\n  - ".join(problems),
                      hint="Keep the script words and follow this voice's explicit tag policy.",
                      details={"problems": problems})
    use = current_voice(vf)
    voice_id = use.get("voice_id") or profile_mod.require_voice(prof)
    pinned_seed = get_pin(vf.dir, f"seed.{shot}")
    seed = seed if seed is not None else (int(pinned_seed) if pinned_seed is not None else vf.seed_for(shot))
    prev, nxt = vf.neighbours(shot)
    folder = vf.dir / "voice" / "ab" / shot
    items = ([("A", ln.text)] if control else []) + [(KEYS[i], v) for i, v in enumerate(variants)]
    client = elevenlabs.Client(mode=vf.mode, film=vf.dir)
    fmt = client.choose_format(prof.get("output_format") or "pcm_48000")
    ffmpeg_ok = bool(procs.which("ffmpeg"))
    rb = rubberband_info()
    pace_cfg = prof.get("pace") or {}
    tempo = float(pace_cfg.get("tempo", 1.06))
    notes = []
    if not raw and not ffmpeg_ok:
        notes.append("ffmpeg isn't installed: takes compared without the clean-up")
    if not raw and rb["status"] == "missing":
        notes.append("Rubber Band isn't installed: takes paced by their pauses only")
        tempo = 1.0

    takes: dict[str, dict] = {}
    for key, text in items:
        res = client.tts(voice_id=voice_id, text=text, model_id=use.get("model"), voice_settings=use.get("settings"),
                         seed=seed, previous_text=prev, next_text=nxt, output_format=fmt["format"],
                         out=folder / f"{key}.raw.wav", note=f"tag ab {vf.id} {shot} {key}")
        t = {"key": key, "sent": text, "tagged": bool(tags_mod.tags_in(text)), "raw": res["file"],
             "request_id": res["request_id"], "replay": res["replay"], "words": res.get("words")}
        clean = folder / f"{key}.clean.wav"
        if raw or not ffmpeg_ok:
            shutil.copyfile(res["file"], clean)
        else:
            clean_file(res["file"], clean, chain_for(prof, diagnose(res["file"]).get("gap_floor_dbfs")))
        paced = folder / f"{key}.paced.wav"
        if raw:
            shutil.copyfile(clean, paced)
            t["pace"] = {"cuts": [], "tempo": 1.0}
        else:
            t["pace"] = pace_file(clean, paced, tempo=tempo, pause_keep=float(pace_cfg.get("pause_keep", 0.8)),
                                  min_pause=float(pace_cfg.get("min_pause", 0.25)),
                                  threshold_db=float(pace_cfg.get("threshold_db", -45.0)), rb=rb)
        t.update({"clean": str(clean), "paced": str(paced)})
        takes[key] = t

    current = vf.unpolished_audio(shot)  # like for like: the variants below aren't polished either
    sel = vf.selected(shot)
    if current and sel and not vf.take_json(shot, sel[0]).get("replay") and not tags_mod.tags_in(
            vf.take_json(shot, sel[0]).get("sent", "")):
        takes["film"] = {"key": "film", "sent": ln.text, "tagged": False, "clean": str(vf.dir / (sel[1].get("clean")
                         or sel[1]["raw"])), "paced": str(current), "replay": False, "words": None,
                         "request_id": vf.take_json(shot, sel[0]).get("request_id")}

    need = [Path(t["clean"]) for t in takes.values() if t.get("words") is None]
    heard = align_mod.run_whisper(need, folder / "whisper", language=prof.get("language") or "en") \
        if need and align_mod.whisper_info()["path"] else {}
    if need and not heard:
        notes.append("Whisper isn't installed: the word checks are unchecked")
    for t in takes.values():
        if t.get("words") is None and Path(t["clean"]) in heard:
            t["words"] = measure.whisper_words(heard[Path(t["clean"])])

    frames = {k: breath.Frames(t["clean"]) for k, t in takes.items()}
    scope_idx = {k: tags_mod.scope_word(t["sent"]) for k, t in takes.items()}
    untagged = [k for k, t in takes.items() if not t["tagged"]]

    def span_at(key: str, index: int | None) -> dict | None:
        if not index:
            return breath.span(frames[key], None)
        words = takes[key].get("words")
        at = word_start(ln.text, words, index) if words else None
        return breath.span(frames[key], at) if at is not None else None

    baselines = {"line": [span_at(k, None) for k in untagged]}
    for k, s in scope_idx.items():
        if s:
            baselines[f"w{s}"] = [span_at(u, s) for u in untagged]
    cal = breath.calibrate(baselines)
    base_creak = max(((span_at(u, None) or {}).get("creak_frac", 0.0) for u in untagged), default=None)
    lexicon = prof.get("lexicon") or []

    report = {}
    for key, t in takes.items():
        words = t.get("words")
        chk = measure.word_check(ln.text, words, lexicon=lexicon, tagged=t["sent"]) if words else None
        line_span = span_at(key, None)
        spans = {"line": line_span}
        if scope_idx[key]:
            spans["tag"] = span_at(key, scope_idx[key])
        verdicts = {}
        for name, sp in spans.items():
            bkey = "line" if name == "line" else f"w{scope_idx[key]}"
            verdicts[name] = breath.breathiness(sp, bkey, cal) if sp and untagged else None
        fit = fit_of(vf, shot, audio_mod.duration(t["paced"]))
        gates = {
            "full_voice": None if not untagged or not t["tagged"] else not any(v and v["breathier"]
                                                                               for v in verdicts.values()),
            "words": None if chk is None else (chk["wer"] == 0 and not chk["repeats"]
                                               and not chk["spoken_tag_words"]),
            "creak": None if base_creak is None or not line_span else line_span["creak_frac"] <= base_creak + 0.01,
            "fits": fit["fits"],
        }
        full_voice_measurement = gates["full_voice"]
        if not policy.get("avoid_low_effort", False):
            gates.pop("full_voice")
        if t.get("replay"):  # a stand-in voice proves nothing about the real read
            full_voice_measurement = None
            gates = {k: None for k in gates}
        verdict = (False if any(v is False for v in gates.values()) else
                   True if all(v is True for v in gates.values()) else None)
        report[key] = {"sent": t["sent"], "tagged": t["tagged"], "request_id": t.get("request_id"),
                       "replay": t.get("replay"), "files": {s: vf.rel(t[s]) for s in ("clean", "paced") if t.get(s)},
                       "words": chk and {k: chk[k] for k in ("heard", "wer", "word_errors", "repeats",
                                                             "spoken_tag_words", "lexicon")},
                       "spans": spans, "breath": verdicts, "fit": fit, "gates": gates,
                       "full_voice_required": bool(policy.get("avoid_low_effort", False)),
                       "full_voice_measurement": full_voice_measurement,
                       "passes": verdict if t["tagged"] else None}

    clips, reel_items = {}, []
    ref = "A" if "A" in takes else ("film" if "film" in takes else None)
    if ref:
        y, info = reel.matched(takes[ref]["paced"])
        audio_mod.write_wav(folder / "A.wav", y, reel.SR, 24)
        clips["A"] = {"file": vf.rel(folder / "A.wav"), **info}
    for key, t in takes.items():
        if not t["tagged"]:
            continue
        y, info = reel.matched(t["paced"])
        audio_mod.write_wav(folder / f"{key}.wav", y, reel.SR, 24)
        clips[key] = {"file": vf.rel(folder / f"{key}.wav"), **info}
        if ref:
            pair = reel.ab_clip(takes[ref]["paced"], t["paced"], folder / f"A{key}.wav")
            clips[f"A{key}"] = {"file": vf.rel(pair["file"])}
            reel_items += [{"file": folder / "A.wav", "title": f"A: {ref}"}, {"file": folder / f"{key}.wav",
                                                                               "title": f"{key}: {t['sent']}"}]
    reel_info = reel.build(reel_items, folder / "reel.wav", gap_s=0.8) if reel_items else None
    out = {"generated": "do not edit by hand: slate voice tags ab", "film": vf.id, "shot": shot, "line": ln.text,
           "voice_id": voice_id, "model": use.get("model"), "seed": seed, "format": fmt, "takes": report,
           "clips": clips, "reel": reel_info["reel"] if reel_info else None, "calibration_sds": cal["sds"],
           "notes": notes, "at": now(),
           "summary": {k: v["passes"] for k, v in report.items() if v["tagged"]},
           "note": "Measurements, not ears: listen to the level-matched clips and pick by ear, then record the pick "
                   "with `slate voice tags set`."}
    jsonio.write_atomic(folder / "ab.json", out)
    log.info(f"tag A/B for {shot}: {len(takes)} takes in {folder}")
    return {"shot": shot, "folder": vf.rel(folder), "summary": out["summary"], "clips": clips, "notes": notes,
            "reel": out["reel"]}
