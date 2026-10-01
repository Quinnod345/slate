"""The voice gates (stage 4 of the production method).

- voice_policy     the voice obeys the cloning policy for this film's mode
- voice_direction  every tagged line strips back to the script exactly, with no banned tag
- voice_takes      every line has a take, made from the current line and direction; approved takes are unchanged
- voice_words      Whisper hears every word, and every brand word (the lexicon) in full; no repeats, no tag read aloud
- voice_tails      nothing leaked in from a neighbouring line (extra words from the line before or after, or voiced
                   audio after the last word)
- voice_fit        each read fits its shot: it starts `lead` in and ends at least `tail` before the cut
- voice_inflection emotion through a full voice, by measurement: no whisper-like read (the voice dropping out), no
                   tagged read breathier than the voice's own untagged reads of the same words, no statement ending
                   that rises like a question, no read that stops mid-word
- voice_polish     every polished take verified by measurement: length identical, loudness back within 0.5 LU,
                   sibilance and edge reduced within the chain's reach, not over-processed, made with the film's
                   pinned polish (a film that doesn't use the polish passes with that said)

Only PASS counts as passed. A replayed take (SLATE_FAKE_VENDORS=1) is never heard, so the gates that judge the
audio report it as UNCHECKED, however good its stand-in looks. So is a take whose alignment is older than the
take, and a take aligned only by forced alignment (which places every word it's given and can't hear a missing one).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from slate import gates as gates_mod
from slate import jsonio
from slate.gates import GateResult
from slate.voice import align as align_mod
from slate.voice import audio as audio_mod
from slate.voice import lang, measure
from slate.voice import profile as profile_mod
from slate.voice import tags as tags_mod
from slate.voice.film import VoiceFilm

TAIL_MARGIN_S = 0.35      # voice this long after the last word's end is suspicious
TAIL_MIN_S = 0.12         # ...when it lasts at least this long
TAIL_VOICED = 0.3         # ...and a pitch tracker finds a voice in this share of its frames (breaths have none)


def _result(gate: str, *, fails: list[str], unchecked: list[str], checked: int, total: int, passed: str,
            evidence: list[str] | None = None, engine: dict | None = None, inputs: dict | None = None,
            details: dict | None = None) -> GateResult:
    kw = {"evidence": (fails + unchecked + (evidence or []))[:60], "engine": engine or gates_mod.engine("slate voice"),
          "inputs": inputs or {}, "details": {"checked": checked, "lines": total, "fails": fails,
                                               "unchecked": unchecked, **(details or {})}}
    if fails:
        return GateResult.fail(gate, f"{len(fails)} problem(s) across {total} line(s): {fails[0]}", **kw)
    if unchecked or checked == 0:
        why = unchecked[0] if unchecked else "nothing to check yet"
        return GateResult.unchecked(gate, f"{len(unchecked) or total} line(s) unchecked: {why}", **kw)
    return GateResult.pass_(gate, passed, **kw)


def _inputs(vf: VoiceFilm, extra: list[Path]) -> dict:
    paths = [p for p in [vf.dir / "script" / "storyboard.json", vf.direction_path, *extra] if p]
    return gates_mod.inputs_for(vf.dir, paths)


# ---------------------------------------------------------------------------------------------------------------


def policy_gate(vf: VoiceFilm) -> GateResult:
    prof = vf.profile
    problems = profile_mod.policy_problems(prof, mode=vf.mode)
    kw = {"engine": gates_mod.engine("slate voice policy"), "evidence": problems or [profile_mod.summary(prof)],
          "details": {"mode": vf.mode, "source": prof.get("source"), "voice_id": prof.get("voice_id")},
          "inputs": gates_mod.inputs_for(vf.dir, [vf.dir / "film.json"])}
    if problems:
        return GateResult.fail("voice_policy", problems[0], **kw)
    return GateResult.pass_("voice_policy", f"{prof.get('source')} voice allowed in {vf.mode} mode", **kw)


def direction_gate(vf: VoiceFilm) -> GateResult:
    policy = vf.profile.get("tag_policy") or {}
    fails: list[str] = []
    known = {ln.shot for ln in vf.lines}
    for shot in vf.direction():
        if shot not in known:
            fails.append(f"direction for {shot}, which has no line in the storyboard")
    tagged_lines = 0
    for ln in vf.lines:
        tagged = vf.tagged_for(ln.shot)
        tagged_lines += bool(tags_mod.tags_in(tagged))
        fails += [f"{ln.shot}: {p}" for p in tags_mod.check(tagged, ln.text, policy=policy)]
    return _result("voice_direction", fails=fails, unchecked=[], checked=len(vf.lines), total=len(vf.lines),
                   passed=f"{len(vf.lines)} lines strip back to the script ({tagged_lines} tagged, no banned tags)",
                   inputs=_inputs(vf, []), engine=gates_mod.engine("slate voice tags"))


def takes_gate(vf: VoiceFilm) -> GateResult:
    fails, unchecked, files = [], [], []
    checked = 0
    for ln in vf.lines:
        sel = vf.selected(ln.shot)
        if not sel:
            fails.append(f"{ln.shot}: not voiced yet (slate voice tts --only {ln.shot})")
            continue
        take, info = sel
        tj = vf.take_json(ln.shot, take)
        files.append(vf.take_dir(ln.shot) / f"{take}.json")
        if tj.get("text") != ln.text:
            fails.append(f"{ln.shot}: the line changed since {take} was voiced; re-voice it "
                         f"(slate voice tts --only {ln.shot} --force)")
            continue
        if tj.get("sent") != vf.tagged_for(ln.shot):
            fails.append(f"{ln.shot}: the direction changed since {take} was voiced "
                         f"(slate voice tts --only {ln.shot} --force)")
            continue
        approved = vf.approved(ln.shot)
        if approved:
            for rel, digest in (approved.get("sha256") or {}).items():
                full = vf.dir / rel
                if digest and (not full.is_file() or jsonio.sha256(full) != digest):
                    fails.append(f"{ln.shot}: the approved take's {rel} changed on disk after approval")
        if tj.get("replay"):
            unchecked.append(f"{ln.shot}: a replayed stand-in, not a real read (voice it with --live)")
            continue
        checked += 1
    return _result("voice_takes", fails=fails, unchecked=unchecked, checked=checked, total=len(vf.lines),
                   passed=f"all {len(vf.lines)} lines voiced from the current script and direction",
                   inputs=_inputs(vf, files))


def _numbers(language: str) -> Callable | None:
    """Number equivalence through the timing module's spoken forms ("2,500" heard for "twenty-five hundred")."""
    try:
        from slate.timing import spoken  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    if not hasattr(spoken, "forms"):
        return None

    def expand(tokens: list[str], other: list[str]) -> list[str]:
        out = list(tokens)
        k = 0
        while k < len(out):
            tok = out[k]
            if any(ch.isdigit() for ch in tok):
                try:
                    forms = spoken.forms(tok, lang=language) or []
                except Exception:  # noqa: BLE001 - an unreadable number stays as written
                    forms = []
                best, best_edits = None, measure.align_words(out, other)[0]
                for form in forms:
                    words = lang.norm_words(" ".join(form))
                    cand = out[:k] + words + out[k + 1:]
                    edits = measure.align_words(cand, other)[0]
                    if edits < best_edits:
                        best, best_edits = words, edits
                if best is not None:
                    out = out[:k] + best + out[k + 1:]
                    k += len(best)
                    continue
            k += 1
        return out

    def equivalent(ref: list[str], hyp: list[str]) -> tuple[list[str], list[str]]:
        if measure.align_words(ref, hyp)[0] == 0:
            return ref, hyp
        ref2 = expand(ref, hyp)
        return ref2, expand(hyp, ref2)

    return equivalent


def _heard(vf: VoiceFilm, shot: str, audio: Path) -> tuple[dict | None, str | None]:
    """The Whisper alignment for a shot's final take, or why there isn't a usable one."""
    doc = align_mod.load(vf.dir, shot, "whisper")
    canonical = align_mod.load(vf.dir, shot)
    if doc is None and canonical and (canonical.get("engine") or {}).get("name") == "openai-whisper":
        doc = canonical
    if doc is None:
        name = ((canonical or {}).get("engine") or {}).get("name")
        if name == "replay":
            return None, f"{shot}: a replayed stand-in, so nothing was heard (voice it with --live)"
        if name == "elevenlabs forced alignment":
            return None, f"{shot}: only forced alignment so far, which can't hear a missing word (slate voice align)"
        return None, f"{shot}: not aligned yet (slate voice align --only {shot})"
    if doc.get("replay") or (doc.get("engine") or {}).get("name") == "replay":
        return None, f"{shot}: a replayed stand-in, so nothing was heard (voice it with --live)"
    if doc.get("sha256") != jsonio.sha256(audio):
        return None, f"{shot}: the alignment is older than the take (slate voice align --only {shot})"
    return doc, None


def words_gate(vf: VoiceFilm) -> GateResult:
    lexicon = vf.profile.get("lexicon") or []
    equivalent = _numbers(vf.language)
    fails, unchecked, files, engines = [], [], [], set()
    checked = 0
    per_shot = {}
    for ln in vf.lines:
        audio = vf.final_audio(ln.shot)
        if audio is None:
            unchecked.append(f"{ln.shot}: not voiced yet")
            continue
        doc, why = _heard(vf, ln.shot, audio)
        if doc is None:
            unchecked.append(why)
            continue
        take = vf.selected(ln.shot)[0]
        sent = vf.take_json(ln.shot, take).get("sent") or ln.text
        files += [audio, vf.dir / "voice" / "align" / f"{ln.shot}.whisper.json"]
        eng = doc.get("engine") or {}
        engines.add((eng.get("name"), eng.get("version"), eng.get("model")))
        chk = measure.word_check(ln.text, measure.whisper_words(doc), lexicon=lexicon, tagged=sent,
                                 equivalent=equivalent)
        checked += 1
        per_shot[ln.shot] = {k: chk[k] for k in ("heard", "wer", "word_errors", "lexicon", "repeats",
                                                 "spoken_tag_words")}
        if chk["wer"] > 0:
            fails.append(f"{ln.shot}: heard \"{chk['heard']}\" ({', '.join(chk['word_errors'][:4])})")
        for word, heard in chk["lexicon"].items():
            if not heard:
                fails.append(f"{ln.shot}: the brand word \"{word}\" wasn't heard in full")
        if chk["repeats"]:
            fails.append(f"{ln.shot}: repeated \"{'; '.join(chk['repeats'])}\"")
        if chk["spoken_tag_words"]:
            fails.append(f"{ln.shot}: a tag was read aloud ({', '.join(chk['spoken_tag_words'])})")
    name, version, model = sorted(engines, key=str)[0] if engines else ("openai-whisper", None, None)
    return _result("voice_words", fails=fails, unchecked=unchecked, checked=checked, total=len(vf.lines),
                   passed=f"Whisper heard every word of {checked} line(s)"
                          + (f" and every brand word ({', '.join(lexicon)})" if lexicon else ""),
                   engine=gates_mod.engine(name or "openai-whisper", version, model=model),
                   inputs=_inputs(vf, files), details={"shots": per_shot, "engines": [list(e) for e in engines]})


def _voiced_share(x: np.ndarray, sr: int, a: float, b: float) -> float:
    seg = x[int(a * sr): int(b * sr)]
    if len(seg) < int(0.05 * sr):
        return 0.0
    x16 = audio_mod.resample(seg, sr, 16000)
    f0 = measure.yin_f0(x16, 16000)
    return float(np.mean(~np.isnan(f0))) if len(f0) else 0.0


def trailing_voice(audio: Path, last_word_end: float) -> list[dict]:
    """Voiced stretches after the last aligned word (a leaked tail sounds like speech; a breath doesn't)."""
    x, sr = audio_mod.load(audio)
    db, h = measure.frame_rms_db(x, sr)
    va = measure.speech_activity(db, h / sr)
    if va is None:
        return []
    hop = h / sr
    out = []
    for a, b in measure.runs(va["active"], True):
        start, end = a * hop, b * hop
        if start < last_word_end + TAIL_MARGIN_S or end - start < TAIL_MIN_S:
            continue
        share = _voiced_share(x, sr, start, end)
        if share >= TAIL_VOICED:
            out.append({"start": round(start, 3), "end": round(end, 3), "voiced": round(share, 2)})
    return out


def tails_gate(vf: VoiceFilm) -> GateResult:
    fails, unchecked, files = [], [], []
    checked = 0
    for ln in vf.lines:
        audio = vf.final_audio(ln.shot)
        if audio is None:
            unchecked.append(f"{ln.shot}: not voiced yet")
            continue
        doc, why = _heard(vf, ln.shot, audio)
        if doc is None:
            unchecked.append(why)
            continue
        take = vf.selected(ln.shot)[0]
        sent = vf.take_json(ln.shot, take).get("sent") or ln.text
        words = measure.whisper_words(doc)
        files.append(audio)
        checked += 1
        chk = measure.word_check(ln.text, words)
        ops = chk["ops"]
        head = []
        for op in ops:
            if op[0] != "ins":
                break
            head.append(op[2])
        tail = []
        for op in reversed(ops):
            if op[0] != "ins":
                break
            tail.insert(0, op[2])
        prev, nxt = vf.neighbours(ln.shot)
        prev_words = lang.norm_words(prev or "")
        next_words = lang.norm_words(nxt or "")
        if head and set(head) & set(prev_words[-(len(head) + 3):]):
            fails.append(f"{ln.shot}: starts with \"{' '.join(head)}\", from the line before")
        if tail and set(tail) & set(next_words[: len(tail) + 3]):
            fails.append(f"{ln.shot}: ends with \"{' '.join(tail)}\", from the next line")
        ends_with_tag = sent.rstrip().endswith("]")  # a tag after the last word may add a sound there
        if words and not ends_with_tag:
            extra = trailing_voice(audio, max(w["end"] for w in words))
            if extra:
                e = extra[0]
                fails.append(f"{ln.shot}: {e['end'] - e['start']:.2f} s of voice after the last word "
                             f"(at {e['start']:.2f} s): a tail from a neighbouring line may have leaked in")
    return _result("voice_tails", fails=fails, unchecked=unchecked, checked=checked, total=len(vf.lines),
                   passed=f"no leaked tails in {checked} line(s)", inputs=_inputs(vf, files),
                   engine=gates_mod.engine("slate voice tails (Whisper words + YIN voicing)"))


def fit_gate(vf: VoiceFilm) -> GateResult:
    from slate.voice.tts import fit_of  # noqa: PLC0415

    fails, unchecked, files, fits = [], [], [], {}
    checked = 0
    for ln in vf.lines:
        audio = vf.final_audio(ln.shot)
        if audio is None:
            unchecked.append(f"{ln.shot}: not voiced yet")
            continue
        take = vf.selected(ln.shot)[0]
        if vf.take_json(ln.shot, take).get("replay"):
            unchecked.append(f"{ln.shot}: a replayed stand-in; its length says nothing about the real read")
            continue
        fit = fit_of(vf, ln.shot, audio_mod.duration(audio))
        fits[ln.shot] = fit
        files.append(audio)
        if fit["fits"] is None:
            unchecked.append(f"{ln.shot}: the shot has no length yet (slate timing)")
            continue
        checked += 1
        if not fit["fits"]:
            fails.append(f"{ln.shot}: a {fit['read_s']:.2f} s read starting {fit['lead_s']:.2f} s into a "
                         f"{fit['shot_s']:.2f} s shot ends {fit['tail_s'] - fit['slack_s']:.2f} s too late")
    return _result("voice_fit", fails=fails, unchecked=unchecked, checked=checked, total=len(vf.lines),
                   passed=f"all {checked} reads fit their shots", inputs=_inputs(vf, files),
                   engine=gates_mod.engine("slate voice fit"), details={"fits": fits})


# ---------------------------------------------------------------------------------------------------------------
# Inflection: emotion through a full voice, by measurement


def _approved_by_ear(vf: VoiceFilm, shot: str) -> str | None:
    """Who approved this shot's take by ear, when the approval still covers its files (None otherwise)."""
    appr = vf.approved(shot)
    if not appr:
        return None
    for rel, digest in (appr.get("sha256") or {}).items():
        full = vf.dir / rel
        if digest and (not full.is_file() or jsonio.sha256(full) != digest):
            return None
    final = vf.final_audio(shot)
    if final is not None and vf.rel(final) not in (appr.get("sha256") or {}):
        return None  # approved before the polish: the polished take hasn't been heard
    return str(appr.get("by") or "the owner")


def _heard_for(vf: VoiceFilm, shot: str, audio: Path) -> list[dict] | None:
    """Heard words that time `audio` (the unpolished take): its own alignment, or the polished take's (the polish keeps
    every sample where it was). None when nothing aligned matches."""
    from slate.voice import analyze  # noqa: PLC0415

    sel = vf.selected(shot)
    words, source = analyze._film_words(vf, shot, sel[0], audio) if sel else (None, None)  # noqa: SLF001
    if source == "replay":
        return None
    return words


def inflection_gate(vf: VoiceFilm) -> GateResult:
    from slate.voice import analyze, breath  # noqa: PLC0415

    fails, unchecked, reviews, notes, files = [], [], [], [], []
    measured: dict[str, dict] = {}
    tagged: dict[str, bool] = {}
    for ln in vf.lines:
        sel = vf.selected(ln.shot)
        if not sel:
            unchecked.append(f"{ln.shot}: not voiced yet")
            continue
        take = sel[0]
        tj = vf.take_json(ln.shot, take)
        if tj.get("replay"):
            unchecked.append(f"{ln.shot}: a replayed stand-in, not a real read (voice it with --live)")
            continue
        audio = vf.unpolished_audio(ln.shot)
        if audio is None:
            unchecked.append(f"{ln.shot}: its take's audio is missing")
            continue
        sent = tj.get("sent") or ln.text
        words = _heard_for(vf, ln.shot, audio)
        m = analyze.measure_take(audio, text=ln.text, words=words, tagged=sent, language=vf.language,
                                 contour_every=None)
        m["words_known"] = words is not None
        measured[ln.shot] = m
        tagged[ln.shot] = bool(tags_mod.tags_in(sent)) and not all(tags_mod.PAUSE_TAG.match(t)
                                                                    for t in tags_mod.tags_in(sent))
        files.append(audio)
    untagged = [s for s in measured if not tagged[s] and measured[s].get("line")]
    rises = {s: measured[s].get("statement_rise_max_st") for s in untagged
             if measured[s].get("statement_rise_max_st") is not None}
    per_line: dict[str, dict] = {}
    for shot, m in measured.items():
        row: dict = {"tagged": tagged[shot]}
        per_line[shot] = row
        others = [measured[o]["line"] for o in untagged if o != shot]
        line = dict(m.get("line") or {})
        if line:
            line["effort_level_db"] = line.get("level_db")
        verdict = None
        if len(others) >= 2 and line:
            cal = breath.calibrate({"voice": [dict(o, effort_level_db=o.get("level_db")) for o in others]})
            verdict = breath.breathiness(line, "voice", cal)
            row["vs_other_lines"] = verdict
        take = vf.selected(shot)[0]
        rnd = ((vf.take_json(shot, take).get("stages") or {}).get("tag_round") or {})
        same_words = rnd.get("gates") if rnd.get("sha256") and rnd["sha256"] == jsonio.sha256(
            vf.dir / rnd.get("file", "")) else None
        if same_words is not None:
            row["same_words"] = {"round": rnd.get("round"), "key": rnd.get("key"), "gates": same_words}
            if same_words.get("full_voice") is False:
                fails.append(f"{shot}: breathier than the voice's own untagged reads of the same words "
                             f"(tag round {rnd.get('round')}, take {rnd.get('key')})")
        if verdict and verdict.get("whisper"):
            fails.append(f"{shot}: whisper-like: the voice drops out (voicing z {verdict['z'].get('voiced_frac')}, "
                         f"CPPS z {verdict['z'].get('cpps_db')} against the film's other lines)")
        elif verdict and verdict.get("breathier"):
            notes.append(f"{shot}: measures breathier than the film's other lines (index {verdict['index']:+.2f}); "
                         "other words differ, so this is a note, not a verdict")
        if tagged[shot] and same_words is None:
            by = _approved_by_ear(vf, shot)
            if by:
                notes.append(f"{shot}: tagged, approved by ear ({by})")
            else:
                reviews.append(f"{shot}: tagged, and not yet measured against untagged reads of the same words; "
                               f"measure it (slate voice tags ab) or listen and approve it")
        tail = (m.get("complete") or {}).get("tail_below_speech_db")
        row["tail_below_speech_db"] = tail
        if tail is not None and tail < analyze.COMPLETE_DB:
            fails.append(f"{shot}: stops mid-word: its last 20 ms sit only {tail:.0f} dB under the speech "
                         f"(a clean ending is {analyze.COMPLETE_DB:.0f} dB or more)")
        if not m.get("words_known"):
            unchecked.append(f"{shot}: not aligned to this audio, so its endings can't be measured "
                             f"(slate voice align --only {shot})")
            continue
        cap_from = [r for o, r in rises.items() if o != shot]
        cap = (max(max(cap_from), 0.0) if cap_from else 0.0) + 3.0
        rise = m.get("statement_rise_max_st")
        row.update({"statement_rise_max_st": rise, "rise_cap_st": round(cap, 2),
                    "last_word_rise_st": m.get("last_word_rise_st")})
        if rise is not None and rise > cap:
            fails.append(f"{shot}: a statement ending rises {rise:+.1f} st, like a question (the voice's own "
                         f"endings allow up to {cap:+.1f})")
    kw = {"evidence": (fails + reviews + unchecked + notes)[:60], "inputs": _inputs(vf, files),
          "engine": gates_mod.engine("slate voice analyze (YIN, CPPS, HNR, H1-H2, tilt)"),
          "details": {"lines": per_line, "fails": fails, "reviews": reviews, "unchecked": unchecked, "notes": notes,
                      "checked": len(measured), "untagged_baseline": untagged}}
    total = len(vf.lines)
    if fails:
        return GateResult.fail("voice_inflection", f"{len(fails)} problem(s) across {total} line(s): {fails[0]}", **kw)
    if unchecked or not measured:
        why = unchecked[0] if unchecked else "nothing voiced yet"
        return GateResult.unchecked("voice_inflection", f"{len(unchecked) or total} line(s) unchecked: {why}", **kw)
    if reviews:
        return GateResult.review("voice_inflection", f"{len(reviews)} line(s) for a person's ear: {reviews[0]}", **kw)
    return GateResult.pass_("voice_inflection", f"full voice on {len(measured)} line(s): nothing whisper-like, no "
                            "question rises, clean endings", **kw)


# ---------------------------------------------------------------------------------------------------------------
# The studio polish, verified by measurement


def polish_gate(vf: VoiceFilm) -> GateResult:
    from slate.voice import polish as polish_mod  # noqa: PLC0415
    from slate.voice.film import get_pin  # noqa: PLC0415

    pinned = get_pin(vf.dir, polish_mod.PIN_KEY)
    tuning = polish_mod.tuning_for(vf.profile)
    any_polished = any((vf.selected(ln.shot) or (None, {}))[1].get("polished") for ln in vf.lines)
    engine = gates_mod.engine("slate voice polish (BS.1770, band levels at one loudness)")
    if not pinned and not any_polished and not tuning.get("required"):
        return GateResult.pass_("voice_polish", "this film doesn't use the studio polish (nothing polished, nothing "
                                "pinned, and the voice doesn't require it)", engine=engine,
                                inputs=gates_mod.inputs_for(vf.dir, [f"film:pins.{polish_mod.PIN_KEY}"]))
    fails, unchecked, files, per_line = [], [], [], {}
    checked = 0
    for ln in vf.lines:
        sel = vf.selected(ln.shot)
        if not sel:
            unchecked.append(f"{ln.shot}: not voiced yet")
            continue
        take, info = sel
        tj = vf.take_json(ln.shot, take)
        pol = (tj.get("stages") or {}).get("polish") or {}
        out = vf.dir / info["polished"] if info.get("polished") else None
        if out is None or not out.is_file():
            fails.append(f"{ln.shot}: {take} isn't polished (slate voice polish --only {ln.shot})")
            continue
        src = vf.unpolished_audio(ln.shot)
        files += [out, src]
        if pol.get("source_sha256") != (jsonio.sha256(src) if src else None):
            fails.append(f"{ln.shot}: polished from an earlier version of the take (slate voice polish --only {ln.shot})")
            continue
        if pol.get("sha256") != jsonio.sha256(out):
            fails.append(f"{ln.shot}: the polished take changed on disk after the polish")
            continue
        if pinned and pol.get("chain_sha256") != pinned.get("chain_sha256"):
            fails.append(f"{ln.shot}: polished with other settings than the film's pinned polish (strength "
                         f"{pinned.get('strength')}); polish it again (slate voice polish --only {ln.shot})")
            continue
        report = jsonio.read(vf.dir / pol["report"], default=None) if pol.get("report") else None
        if not report:
            fails.append(f"{ln.shot}: the polish report is missing (slate voice polish --only {ln.shot})")
            continue
        files.append(vf.dir / pol["report"])
        if tj.get("replay"):
            # A stand-in voice has no real sibilance or edge to judge; only what holds for any signal is checked.
            v = polish_mod.verify(report["stages"], report["effect"], None, tuning.get("verify"))
            per_line[ln.shot] = {"strength": report.get("strength"), "checks": v["checks"], "problems": v["problems"]}
            fails += [f"{ln.shot}: {p}" for p in v["problems"]]
            unchecked.append(f"{ln.shot}: a replayed stand-in: length and loudness hold, but a stand-in voice says "
                             "nothing about the sibilance or the edge (voice it with --live)")
            continue
        v = polish_mod.verify(report["stages"], report["effect"], report.get("chain"), tuning.get("verify"))
        per_line[ln.shot] = {"strength": report.get("strength"), "checks": v["checks"], "problems": v["problems"]}
        fails += [f"{ln.shot}: {p}" for p in v["problems"]]
        checked += 1
    strength = (pinned or {}).get("strength")
    return _result("voice_polish", fails=fails, unchecked=unchecked, checked=checked, total=len(vf.lines),
                   passed=f"{checked} polished take(s) verified by measurement (strength {strength}): same length, "
                          "loudness held, S and edge down within the chain's reach",
                   engine=engine, inputs=_inputs(vf, files + [f"film:pins.{polish_mod.PIN_KEY}"]),
                   details={"shots": per_line, "pinned": pinned and {k: pinned.get(k) for k in ("strength",
                                                                                              "chain_sha256")}})


GATES: dict[str, Callable[[VoiceFilm], GateResult]] = {
    "voice_policy": policy_gate, "voice_direction": direction_gate, "voice_takes": takes_gate,
    "voice_words": words_gate, "voice_tails": tails_gate, "voice_fit": fit_gate,
    "voice_inflection": inflection_gate, "voice_polish": polish_gate,
}


def run(film_dir: str | Path, names: list[str] | None = None, *, write: bool = True) -> list[GateResult]:
    """Run the voice gates (all, or the named ones) and record them in qa/gates.json."""
    vf = VoiceFilm(film_dir)
    chosen = names or list(GATES)
    unknown = [n for n in chosen if n not in GATES]
    if unknown:
        from slate.errors import UsageError  # noqa: PLC0415
        raise UsageError(f"Unknown voice gate(s): {', '.join(unknown)} (known: {', '.join(GATES)}).")
    results = [GATES[n](vf) for n in chosen]
    for r in results:
        r.details.setdefault("stage", "voice")
    if write:
        gates_mod.write_report(vf.dir, results)
    return results
