"""`slate voice trim`: a leaked tail cut off a take's play length, without touching the take.

A model sometimes reads on past its line into the next one. The voice_tails gate finds it (voice after the last word
that a pitch tracker hears as speech); until now the only fix was a re-voice. When the tail is clearly separate from
the line, it can be trimmed instead, the way film A trimmed two of its takes:

- where the cut goes: after the line's last word is really over (its audible end, found from the audio: Whisper's
  word end, then on while the take stays within 40 dB of its speech level), plus a short release (0.12 s), and never
  later than 0.05 s before the tail. The cut must sit in silence, with at least 0.15 s between the last word and the
  tail; a tail that runs straight on from the line can't be trimmed safely, and the answer stays a re-voice;
- what changes: nothing in the file. The take gets a play length (`dur` in voice/takes.json, with the measurement in
  its take record), which timing carries into the cue sheet's `dur` and the mix plays with the cue's fade-out. The
  polish and the clean-up keep every sample where it was, so they keep the trim; a re-pace moves the words and drops
  it, and a re-voice is a new take;
- its approval: without --go it only shows where each cut would go. An approved take's play length changes only with
  --force and the person who said so (--by); the trim is recorded with who and why, and the hash of the audio it was
  measured on.

    trim.plan(film_dir, only=None) -> [{shot, take, tail, last_word_end_s, cut_s, ok, why}]
    trim.apply(film_dir, only=None, force=False, by=None) -> the same, recorded
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import jsonio
from slate.errors import Refused, UsageError
from slate.voice import audio as audio_mod
from slate.voice.film import VoiceFilm, now, record

RELEASE_S = 0.12  # after the last word's audible end
BEFORE_TAIL_S = 0.05  # the cut never comes closer to the tail than this
MIN_GAP_S = 0.15  # the pause a cut needs between the line and the tail
SILENCE_DB = 40.0  # under the take's speech level is silence


def audible_end(x: np.ndarray, sr: int, word_end: float) -> float:
    """Where the last word is really over: on from Whisper's end while 10 ms frames stay within 40 dB of the take's
    speech level (the 95th percentile of its frames)."""
    hop = max(1, sr // 100)
    n = len(x) // hop
    if n == 0:
        return word_end
    lv = 20 * np.log10(np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-12)
    top = float(np.percentile(lv, 95))
    k = min(n, max(0, int(word_end * 100)))
    while k < n and lv[k] > top - SILENCE_DB:
        k += 1
    return k / 100


def _quiet(x: np.ndarray, sr: int, t: float) -> bool:
    hop = max(1, sr // 100)
    n = len(x) // hop
    lv = 20 * np.log10(np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-12)
    top = float(np.percentile(lv, 95))
    a, b = max(0, int((t - 0.02) * 100)), min(n, int((t + 0.02) * 100) + 1)
    return bool(b > a and (lv[a:b] < top - SILENCE_DB).all())


def _measure(vf: VoiceFilm, shot: str) -> dict:
    from slate.voice import gates, measure  # noqa: PLC0415

    row: dict = {"shot": shot, "ok": False}
    audio = vf.final_audio(shot)
    if audio is None:
        return {**row, "why": "not voiced yet"}
    take = vf.selected(shot)[0]
    row.update({"take": take, "file": vf.rel(audio), "sha256": jsonio.sha256(audio)})
    doc, why = gates._heard(vf, shot, audio)  # noqa: SLF001 - the tails gate's own hearing of this take
    if doc is None:
        return {**row, "why": why}
    words = measure.whisper_words(doc)
    if not words:
        return {**row, "why": "Whisper heard no words in it"}
    last = max(w["end"] for w in words)
    tails = gates.trailing_voice(audio, last)
    if not tails:
        return {**row, "why": "no leaked tail: nothing to trim"}
    tail = tails[0]
    x, sr = audio_mod.load(audio)
    end = audible_end(x, sr, last)
    cut = round(min(end + RELEASE_S, tail["start"] - BEFORE_TAIL_S), 3)
    row.update({"tail": tail, "last_word_end_s": round(last, 3), "audible_end_s": round(end, 3), "cut_s": cut,
                "seconds": round(len(x) / sr, 3)})
    if tail["start"] - end < MIN_GAP_S:
        return {**row, "why": f"the tail starts {max(0.0, tail['start'] - end) * 1000:.0f} ms after the line: no pause "
                              "to cut in safely, so re-voice it (slate voice tts --only " + shot + " --force)"}
    if cut <= end or not _quiet(x, sr, cut):
        return {**row, "why": "the cut wouldn't fall in silence; re-voice it"}
    return {**row, "ok": True, "why": f"cut at {cut:.3f} s: the line ends at {end:.3f} s, the tail starts at "
                                      f"{tail['start']:.3f} s"}


def plan(film_dir: str | Path, *, only: str | list[str] | None = None) -> list[dict]:
    """Where each leaked tail would be cut (or why it can't be). Writes nothing."""
    vf = VoiceFilm(film_dir)
    return [_measure(vf, ln.shot) for ln in vf.select(only)]


def apply(film_dir: str | Path, *, only: str | list[str] | None = None, force: bool = False,
          by: str | None = None, reason: str | None = None) -> list[dict]:
    """Record every safe cut as the take's play length. An approved take needs force and the person (by)."""
    vf = VoiceFilm(film_dir)
    rows = [_measure(vf, ln.shot) for ln in vf.select(only)]
    for row in rows:
        if not row["ok"]:
            continue
        shot, take = row["shot"], row["take"]
        if vf.locked(shot):
            if not force or not (by or "").strip():
                raise Refused(f"{shot}'s take is approved; trimming it changes what was approved.",
                              hint=f"With the person's go: slate voice trim --only {shot} --go --force --by NAME")
        trim = {"dur": row["cut_s"], "file": row["file"], "sha256": row["sha256"], "tail": row["tail"],
                "audible_end_s": row["audible_end_s"], "by": by, "reason": reason, "at": now(),
                "note": "a play length: the take's file is untouched; timing carries it into the cue sheet"}
        data = vf.take_json(shot, take)
        data.setdefault("stages", {})["trim"] = trim
        vf.save_take_json(shot, take, data)
        vf.update_take(shot, take, dur=row["cut_s"], trim_sha256=row["sha256"])
        record(vf.dir, row["file"], producer="slate voice trim", inputs={row["file"]: row["sha256"]},
               note=f"plays to {row['cut_s']:.3f} s (a leaked tail from {row['tail']['start']:.3f} s cut)")
        row["trimmed"] = True
    return rows


def current(vf: VoiceFilm, shot: str) -> float | None:
    """The selected take's trimmed play length, or None. A re-voice is a new take (no trim) and a re-pace drops the
    trim (slate.voice.pace), so a play length is only ever kept on the timing it was measured on; the polish and the
    clean-up keep every sample where it was, so they keep it."""
    sel = vf.selected(shot)
    return float(sel[1]["dur"]) if sel and sel[1].get("dur") else None


def check_args(go: bool, force: bool, by: str | None) -> None:
    if force and not go:
        raise UsageError("--force only means something with --go.")
    if force and not (by or "").strip():
        raise UsageError("Trimming an approved take needs the person who said so: --by NAME.")
