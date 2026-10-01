"""A comp: one word picked up from another take, as in any voice-over edit. No new read, no credits.

When the approved take of a line is right except one word, and another take of the same line says that word better,
the comp keeps the approved take up to the word and takes the other one (the donor) from the word on:

- both are cut inside the silence before the word, a short pre-roll (at most 0.12 s) ahead of its audible onset. The
  onset is found from the audio, not from Whisper: Whisper's word starts land up to 0.3 s early, inside the silence,
  so the silence after the previous word is found first (10 ms frames 40 dB under the take's speech level), then the
  first rise to 35 dB under that level held for 30 ms;
- a 30 ms equal-gain crossfade sits in that pause, centred on the cut, so the donor's word lands exactly where the
  approved take's did;
- the donor is gained so the loud part of its line (the 90th percentile of its 25 ms frame levels before the word)
  matches the approved take's: the word keeps the relation to its line it had in its own take.

The comp is a new take of the shot with both sources recorded (`stages.comp`), made from the takes before pacing (clean,
else raw) the way the films made theirs, then paced and polished like any take. It's selected, and needs its own
approval: the approved take it came from stays as it was.

    comp.comp(film_dir, shot, word=4, donor="t03") -> {take, base, donor, base_cut_s, donor_cut_s, donor_gain_db, ...}
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from slate import jsonio
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.voice import audio as audio_mod
from slate.voice.film import VoiceFilm, now, record

PREROLL_S = 0.12  # the cut sits at most this far ahead of the word's audible onset, inside the pause
CROSSFADE_S = 0.03  # 30 ms in the pause, centred on the cut
SILENCE_DB, ONSET_DB, ONSET_HOLD = 40.0, 35.0, 3  # under the take's speech level; 3 frames of 10 ms


def true_onset(x: np.ndarray, sr: int, after: float) -> float:
    """Seconds into x where the word after `after` (the previous word's end, Whisper's) is first heard."""
    hop = max(1, sr // 100)
    n = len(x) // hop
    if n == 0:
        return after
    lv = 20 * np.log10(np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-12)
    top = float(np.percentile(lv, 95))
    k = max(0, int(after * 100))
    while k < n and lv[k] > top - SILENCE_DB:
        k += 1
    while k + ONSET_HOLD < n and not (lv[k:k + ONSET_HOLD] > top - ONSET_DB).all():
        k += 1
    return k / 100


def loud_level(x: np.ndarray, sr: int, until: float) -> float:
    """The loud part of a line before `until`: the 90th percentile of its 25 ms frame levels (frames within 40 dB of
    its loudest)."""
    w = max(1, int(0.025 * sr))
    k = max(1, int(until * sr) // w)
    fr = x[: k * w].reshape(-1, w) if len(x) >= k * w else x[: (len(x) // w) * w].reshape(-1, w)
    lv = 20 * np.log10(np.sqrt(np.mean(fr ** 2, axis=1)) + 1e-12)
    live = lv[lv > lv.max() - 40]
    return float(np.percentile(live if len(live) else lv, 90))


def splice(base: np.ndarray, donor: np.ndarray, sr: int, *, base_cut: float, donor_cut: float, gain_db: float,
           crossfade_s: float = CROSSFADE_S) -> np.ndarray:
    """base up to base_cut, then donor from donor_cut (gained), joined by a crossfade centred on the cut, so the
    donor's sample at donor_cut lands exactly at base_cut."""
    f = max(2, int(round(crossfade_s * sr)) // 2 * 2)
    h = f // 2
    ca, cb = int(round(base_cut * sr)), int(round(donor_cut * sr))
    if ca - h < 0 or cb - h < 0 or ca + h > len(base) or cb + f > len(donor):
        raise Refused("The cut falls too close to a take's edge to crossfade there.")
    a = base[: ca + h]
    b = donor[cb - h:] * 10 ** (gain_db / 20)
    ramp = np.linspace(0.0, 1.0, f)
    return np.concatenate([a[:-f], a[-f:] * (1 - ramp) + b[:f] * ramp, b[f:]])


def _stage(vf: VoiceFilm, info_a: dict, info_b: dict) -> str:
    """The stage both takes are comped at: clean (before pacing, the films' way), else raw."""
    for stage in ("clean", "raw"):
        if all(i.get(stage) and (vf.dir / i[stage]).is_file() for i in (info_a, info_b)):
            return stage
    raise MissingPrerequisite("Both takes need their clean or raw file to be comped.")


def words_of(vf: VoiceFilm, shot: str, take: str, path: Path) -> list[dict]:
    """The words heard in this take file, timed in it: a replayed take's own words, an alignment of this exact audio,
    or Whisper on it (Slate's one runner, slate.voice.align)."""
    from slate.voice import align as align_mod  # noqa: PLC0415
    from slate.voice import measure  # noqa: PLC0415

    data = vf.take_json(shot, take)
    if data.get("replay") and data.get("replay_words"):
        return [dict(w) for w in data["replay_words"]]  # clean keeps every sample where it was
    sha = jsonio.sha256(path)
    for engine in ("whisper", None):
        doc = align_mod.load(vf.dir, shot, engine)
        if doc and doc.get("sha256") == sha:
            return measure.whisper_words(doc)
    language = vf.profile.get("language") or vf.language
    heard = align_mod.run_whisper([path], vf.dir / "voice" / "comps" / "whisper", language=language)
    if path not in heard:
        raise MissingPrerequisite(f"Whisper heard nothing in {vf.rel(path)}, so its words can't be found.")
    return measure.whisper_words(heard[path])


def comp(film_dir: str | Path, shot: str, *, word: int, donor: str, base: str | None = None,
         force: bool = False) -> dict:
    """A new take of `shot`: the base take (default: the selected one) up to its `word`-th script word (1-based), then
    the donor take from that word on. Returns the new take's record."""
    from slate.voice.analyze import time_of  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    ln = vf.line(shot)
    sel = vf.selected(shot)
    base = base or (sel[0] if sel else None)
    entry = (vf.entry(shot) or {}).get("takes") or {}
    if base not in entry or donor not in entry:
        raise UsageError(f"{shot} needs both takes recorded (base {base}, donor {donor}; takes: "
                         f"{', '.join(entry) or 'none'}).", hint=f"slate voice tts --only {shot} --force for another take")
    if base == donor:
        raise UsageError("The donor is another take of the line, not the base itself.")
    words_in_line = len(ln.text.split())
    if not 2 <= word <= words_in_line:
        raise UsageError(f"--word is the script word the donor starts from: 2 to {words_in_line} for {shot} "
                         f"(\"{ln.text}\").")
    if vf.locked(shot) and not force:
        raise Refused(f"{shot}'s take is approved; selecting a comp replaces approved work.",
                      hint="Only with the person's go: run it again with --force, then approve the comp.")
    stage = _stage(vf, entry[base], entry[donor])
    pa, pb = vf.dir / entry[base][stage], vf.dir / entry[donor][stage]
    xa, sr = audio_mod.load(pa)
    xb, srb = audio_mod.load(pb)
    if srb != sr:
        xb = audio_mod.resample(xb, srb, sr)
    wa, wb = words_of(vf, shot, base, pa), words_of(vf, shot, donor, pb)
    k = word - 1
    prev_a, prev_b = time_of(wa, ln.text, k - 1), time_of(wb, ln.text, k - 1)
    if prev_a is None or prev_b is None:
        raise Refused(f"The word before the cut (\"{ln.text.split()[k - 1]}\") wasn't heard in "
                      f"{'the base' if prev_a is None else 'the donor'}, so the pause can't be found.")
    ea, eb = prev_a[1], prev_b[1]
    ta, tb = true_onset(xa, sr, ea), true_onset(xb, sr, eb)
    pre = min(PREROLL_S, (ta - ea) / 2, (tb - eb) / 2)
    if pre <= CROSSFADE_S / 2:
        raise Refused(f"There's no pause before \"{ln.text.split()[k]}\" wide enough to cut in ({pre * 1000:.0f} ms).")
    gain_db = round(loud_level(xa, sr, ta) - loud_level(xb, sr, tb), 2)
    y = splice(xa, xb, sr, base_cut=ta - pre, donor_cut=tb - pre, gain_db=gain_db)
    take = vf.next_take_id(shot)
    tdir = vf.take_dir(shot)
    raw = audio_mod.write_wav(tdir / f"{take}.wav", y, sr, 24)
    files = {"raw": raw}
    if stage == "clean":
        files["clean"] = tdir / f"{take}.clean.wav"
        shutil.copyfile(raw, files["clean"])
    base_json, donor_json = vf.take_json(shot, base), vf.take_json(shot, donor)
    seconds = round(len(y) / sr, 3)
    detail = {"base": {"take": base, "file": vf.rel(pa), "sha256": jsonio.sha256(pa)},
              "donor": {"take": donor, "file": vf.rel(pb), "sha256": jsonio.sha256(pb)},
              "stage": stage, "word": word, "word_text": ln.text.split()[k],
              "base_onset_s": round(ta, 3), "donor_onset_s": round(tb, 3), "base_cut_s": round(ta - pre, 3),
              "donor_cut_s": round(tb - pre, 3), "preroll_s": round(pre, 3), "crossfade_s": CROSSFADE_S,
              "donor_gain_db": gain_db, "at": now(),
              "note": "the base take up to the pause before the word, then the donor from it: no new read"}
    data = {"generated": "do not edit by hand: slate voice comp", "shot": shot, "take": take, "film": vf.id,
            "brand": base_json.get("brand"), "mode": vf.mode, "text": ln.text,
            "sent": donor_json.get("sent") or base_json.get("sent") or ln.text, "tags": donor_json.get("tags") or [],
            "previous_text": base_json.get("previous_text"), "next_text": base_json.get("next_text"),
            "model": base_json.get("model") or "comp", "voice_id": base_json.get("voice_id") or "comp",
            "voice_settings": base_json.get("voice_settings") or {}, "seed": None,
            "format": base_json.get("format") or "pcm", "request_id": base_json.get("request_id") or f"comp-{take}",
            "character_cost": 0, "replay": bool(base_json.get("replay") or donor_json.get("replay")),
            "file": vf.rel(raw), "sha256": jsonio.sha256(raw), "seconds": seconds, "created_at": now(),
            "stages": {"comp": detail, **({"clean": {"file": vf.rel(files["clean"]), "source": vf.rel(raw),
                                                    "sha256": jsonio.sha256(files["clean"]), "note": "comped from the "
                                                    "clean takes"}} if stage == "clean" else {})}}
    if data["replay"] and base_json.get("replay_words") and donor_json.get("replay_words"):
        shift = (ta - pre) - (tb - pre)
        data["replay_words"] = [w for w in base_json["replay_words"] if w["end"] <= ta - pre] + \
            [{**w, "start": round(w["start"] + shift, 4), "end": round(w["end"] + shift, 4)}
             for w in donor_json["replay_words"] if w["start"] >= tb - pre]
    problems = jsonio.validate(data, "voice_take")
    if problems:  # pragma: no cover - a bug in this module, not in the takes
        raise UsageError("The comp's take record doesn't match its schema: " + "; ".join(problems[:5]))
    vf.save_take_json(shot, take, data)
    vf.update_take(shot, take, raw=vf.rel(raw), clean=vf.rel(files["clean"]) if "clean" in files else None,
                   paced=None, polished=None, align=None, replay=data["replay"], seconds=seconds,
                   json=vf.rel(tdir / f"{take}.json"), comp={"base": base, "donor": donor, "word": word})
    vf.select_take(shot, take)
    manifest = vf.manifest()
    manifest["shots"][shot]["approved"] = None  # the comp needs its own approval
    vf.save_manifest(manifest)
    for rel in [vf.rel(p) for p in files.values()]:
        record(vf.dir, rel, producer="slate voice comp", inputs=[vf.rel(pa), vf.rel(pb)],
               note=f"{base} to the pause before word {word}, then {donor}")
    return {"shot": shot, "take": take, "file": vf.rel(raw), "seconds": seconds, **detail,
            "next": f"slate voice pace --only {shot}, slate voice polish --only {shot}, slate voice align --only {shot}, "
                    f"then listen and slate voice approve {shot} --by NAME"}
