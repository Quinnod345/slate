"""Measured inflection: how a read moves, breathes and lands, so takes, tags and polish are judged by measurement.

Ported from the films' `voice_metrics.py` and `spectro_ab.py` (2026-09-30). The owner, on tagged reads that came out
whispery: "Never use tags for whispering, talking, or softly, because that makes it sound like whisper and it kind of
sounds scary… maybe analyzing the waveforms and frequencies, like a spectrogram, might help you gauge the emotion."
So every take can be measured before anyone listens, and the measures keep the films' meanings exactly:

- **Pitch (F0)**: YIN, 70-500 Hz, octave jumps removed; median, mean, range (p5-p95, semitones), movement (SD), and the
  contour itself (for the sheets).
- **Breath and effort** (`slate.voice.breath`): voiced fraction, CPPS, HNR, H1-H2, spectral tilt, level. A whisper has
  no pitch, breath fills the harmonics, a soft voice tilts steeply.
- **Brightness and smile**: spectral centroid and F1/F2 (a smile lifts F2); only meaningful between takes of the same
  words.
- **Sibilance and edge**: the 5-9 kHz and 2-5 kHz bands against the whole sound, and their loudest 5 ms moments, at a
  common working loudness.
- **Energy**: BS.1770 loudness and true peak, the level contour's spread, the last word's level against the rest.
- **Creak**: voiced frames under 115 Hz or 0.6x the median, irregular pitch, and the words they fall on.
- **Pacing**: words a second over the speech span, articulation rate without pauses, the pauses, the quiet at each
  sentence end.
- **Endings**: the last word's pitch across it (a rise past the voice's own endings reads as a question), its contour
  (start, peak, end, the fall), and every question word's rise.
- **The Whisper checks**: every word heard (WER against the script), no repeat, no tag read aloud, every lexicon word in
  full, and no voiced audio after the last word (a leaked tail).
- **The tag scope**: the same measures from the first word an emotion tag governs to the end, because a tag on the last
  word can change that word while the line's average barely moves.

These are proxies, not ears. They catch a read that's breathy, whispered, creaky, rising, rushed or misheard; the owner
still picks by listening.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import numpy as np

from slate.voice import audio as audio_mod
from slate.voice import breath, lang, measure
from slate.voice import tags as tags_mod
from slate.voice.measure import HOP

SIB_BAND = (5000.0, 9000.0)
EDGE_BAND = (2000.0, 5000.0)
COMPLETE_DB = 35.0  # a take whose last 20 ms sit less than this under its speech stopped mid-word (the films' rule)


def _sound_loudness(path: Path) -> dict:
    from slate.sound import measure as sound_measure  # noqa: PLC0415

    m = sound_measure.loudness(path)

    def r(v: float | None) -> float | None:
        return None if v is None else round(float(v), 2)

    return {"lufs": r(m.get("integrated_lufs")), "true_peak_dbtp": r(m.get("true_peak_dbtp")),
            "lra_lu": r(m.get("lra_lu")), "silent": bool(m.get("silent"))}


def band_levels(x: np.ndarray, sr: int, speech: np.ndarray | None = None) -> dict:
    """The sibilance and edge bands at a working loudness of -20 LUFS: each band's level against the whole sound over
    the speech frames (dB), and its loudest 5 ms moment (the 99th percentile of the band's level, dBFS)."""
    from slate.voice import dsp, polish  # noqa: PLC0415

    lufs = polish._lufs(x, sr)  # noqa: SLF001 - the same BS.1770 meter the polish uses
    if lufs <= polish.SILENT_LUFS:
        return {"sibilance_vs_full_db": None, "sibilance_peak_db": None, "edge_vs_full_db": None,
                "edge_peak_db": None}
    x = x * dsp.db_to_gain(polish.WORK_LUFS - lufs)
    full = dsp.env_db(x, sr, 5.0)
    hop = max(1, sr // 100)
    full_h = full[::hop]
    sp = full_h > np.percentile(full_h, 95) - 35 if speech is None else speech[: len(full_h)]
    out = {}
    for name, (lo, hi) in (("sibilance", SIB_BAND), ("edge", EDGE_BAND)):
        b, _ = dsp.band_split(x, sr, lo, hi)
        e = dsp.env_db(b, sr, 5.0)[::hop][: len(sp)]
        sel = sp[: len(e)]
        if not sel.any():
            out[f"{name}_vs_full_db"], out[f"{name}_peak_db"] = None, None
            continue
        p_band = np.mean(10 ** (e[sel] / 10))
        p_full = np.mean(10 ** (full_h[: len(e)][sel] / 10))
        out[f"{name}_vs_full_db"] = round(float(10 * np.log10(p_band / p_full + 1e-20)), 2)
        out[f"{name}_peak_db"] = round(float(np.percentile(e[sel], 99)), 2)
    return out


def tail_below_speech_db(path: str | Path) -> float | None:
    """How far the take's last 20 ms sit under its speech level (25 ms frames, the loud level at their 95th
    percentile). A take that stops mid-word ends only 10 to 30 dB down; a clean ending is 35 dB or more."""
    x, sr = audio_mod.load(path)
    w = int(0.025 * sr)
    n = len(x) // w * w
    if n == 0:
        return None
    lv = 20 * np.log10(np.sqrt(np.mean(x[:n].reshape(-1, w) ** 2, axis=1)) + 1e-12)
    tail = x[-int(0.02 * sr):]
    tail_db = max(-120.0, 20 * float(np.log10(np.sqrt(np.mean(tail ** 2)) + 1e-12)))  # digital silence: -120 dBFS
    return round(float(np.percentile(lv, 95)) - tail_db, 1)


def contour(F: breath.Frames, every: int = 2) -> dict:
    """The F0 contour for reports: Hz every `every` frames (None where unvoiced)."""
    hz = [None if not np.isfinite(v) else round(float(v), 1) for v in F.f0[::every]]
    return {"hop_s": round(HOP * every, 4), "hz": hz}


def _word_times(words: list[dict], text: str) -> tuple[list[str], dict[int, int]]:
    """Script tokens and {script index: heard word index}, through the Levenshtein alignment."""
    hyp, owner = [], []
    for k, w in enumerate(words):
        for tok in lang.norm_words(w["word"]):
            hyp.append(tok)
            owner.append(k)
    ref = lang.norm_words(text)
    _, _, pairs = measure.align_words(ref, hyp)
    return ref, {r: owner[h] for r, h in pairs}


def time_of(words: list[dict], text: str, index: int) -> tuple[float, float] | None:
    """When script word `index` was said (its heard word's start and end), or None if it wasn't heard."""
    _, m = _word_times(words, text)
    k = m.get(index)
    return (float(words[k]["start"]), float(words[k]["end"])) if k is not None else None


def question_indices(text: str, question_marks: Iterable[str]) -> list[int]:
    """Script-word indices of the words a question mark ends ("Four problems?" -> "problems")."""
    out, k = [], 0
    marks = set(question_marks)
    for tok in text.split():
        n = len(lang.norm_words(tok))
        if n and any(m in tok for m in marks):
            out.append(k + n - 1)
        k += n
    return out


def _voiced_cpps(F: breath.Frames, start: float, end: float) -> float | None:
    """A word's clarity: median CPPS over its voiced frames (start - 20 ms to end + 250 ms: Whisper's ends run
    early). Falling words that turned rough and breathy showed up here when the line's averages missed them."""
    a0, b0 = max(0, int((start - 0.02) / HOP)), int((end + 0.25) / HOP)
    c = F.cpps[a0:b0][F.voiced[a0:b0]]
    c = c[np.isfinite(c)]
    return round(float(np.median(c)), 2) if len(c) >= 4 else None


def last_word(F: breath.Frames, words: list[dict], text: str, line_median_hz: float | None) -> dict:
    """The last word's pitch movement (last third of its voiced frames against the first third, semitones) and its
    shape: start, peak and end pitch on a 3-frame median track, rise into the peak, fall to the end, range, where the
    peak sits, and the word's level and clarity against the rest of the take."""
    from scipy.signal import medfilt  # noqa: PLC0415

    out: dict = {"rise_st": None, "contour": None}
    ref = lang.norm_words(text)
    if not ref:
        return out
    tw = time_of(words, text, len(ref) - 1)
    if not tw:
        return out
    seg = F.f0[int(tw[0] / HOP): int(tw[1] / HOP) + 5]
    seg = seg[np.isfinite(seg)]
    if len(seg) >= 4:
        q = max(1, len(seg) // 3)
        out["rise_st"] = round(float(12 * np.log2(np.median(seg[-q:]) / np.median(seg[:q]))), 2)
    a0, b0 = max(0, int((tw[0] - 0.02) / HOP)), int((tw[1] + 0.05) / HOP)
    f = F.f0[a0:b0]
    idx = np.flatnonzero(np.isfinite(f))
    if len(idx) < 4:
        return out
    v = medfilt(f[idx], 3)
    start, end = float(np.median(v[:3])), float(np.median(v[-3:]))
    pk = int(np.argmax(v))
    peak = float(v[pk])
    act = F.act[a0:b0]
    word_db = float(10 * np.log10(np.mean(10 ** (F.db[a0:b0][act] / 10)))) if act.any() else None
    before = F.db[:a0][F.act[:a0]]
    rest_db = float(10 * np.log10(np.mean(10 ** (before / 10)))) if len(before) else None
    peak_db = float(F.db[a0:b0][act].max() - np.percentile(before, 90)) if act.any() and len(before) else None
    out["contour"] = {
        "voiced_frames": int(len(idx)), "start_hz": round(start, 1), "peak_hz": round(peak, 1), "end_hz": round(end, 1),
        "median_hz": round(float(np.median(v)), 1), "rise_to_peak_st": round(float(12 * np.log2(peak / start)), 2),
        "fall_peak_to_end_st": round(float(12 * np.log2(peak / end)), 2),
        "range_st": round(float(12 * np.log2(v.max() / v.min())), 2), "peak_pos": round(pk / max(1, len(v) - 1), 2),
        "level_vs_rest_db": round(word_db - rest_db, 2) if word_db is not None and rest_db is not None else None,
        "peak_vs_rest_p90_db": round(peak_db, 2) if peak_db is not None else None,
        "voiced_cpps_median_db": _voiced_cpps(F, *tw), "line_median_hz": line_median_hz}
    return out


def measure_take(path: str | Path, *, text: str | None = None, words: list[dict] | None = None,
                 tagged: str | None = None, lexicon: Iterable[str] = (), language: str = "en",
                 emotion_from_word: int | None = None, frames: breath.Frames | None = None,
                 contour_every: int | None = 2) -> dict:
    """Every measure for one take. `words` are the take's heard words (Whisper-shaped: word, start, end); without
    them the word-dependent measures are None (never guessed). `text` is the script line; `tagged` what was sent."""
    path = Path(path)
    table = lang.load(language)
    F = frames or breath.Frames(path)
    n = len(F.db)
    everything = np.ones(n, bool)
    m: dict = {"file": str(path), "duration_s": round(F.duration, 3), **_sound_loudness(path)}
    m["line"] = breath.summarize(F, everything, formants_too=True)
    if m["line"]:
        m["line"]["effort_level_db"] = m["line"]["level_db"]
    x, sr = audio_mod.load(path, mono=True)
    m["bands"] = band_levels(x, sr)
    m["complete"] = {"tail_below_speech_db": tail_below_speech_db(path)}
    m["complete"]["ok"] = (m["complete"]["tail_below_speech_db"] is None
                           or m["complete"]["tail_below_speech_db"] >= COMPLETE_DB)
    if contour_every:
        m["f0_contour"] = contour(F, contour_every)
    on = np.flatnonzero(F.act)
    ref = lang.norm_words(text) if text else []
    if len(on):
        span = (on[-1] + 1 - on[0]) * HOP
        pauses = [(a, b) for a, b in measure.runs(F.act[on[0]: on[-1] + 1], False) if (b - a) * HOP >= 0.25]
        ptot = sum(b - a for a, b in pauses) * HOP
        m["pacing"] = {"speech_span_s": round(span, 3), "pauses_ge_250ms": len(pauses), "pause_total_s": round(ptot, 3),
                       "words": len(ref) or None,
                       "wps": round(len(ref) / span, 3) if ref and span > 0 else None,
                       "wpm": round(60 * len(ref) / span, 1) if ref and span > 0 else None,
                       "articulation_wps": round(len(ref) / max(1e-6, span - ptot), 3) if ref else None}
    else:
        m["pacing"] = None
    scope = tags_mod.scope_word(tagged) if tagged else None
    m.update({"whisper": None, "last_word_rise_st": None, "last_word_contour": None, "question_words": {},
              "creak_words": {}, "end_drop_db": None, "sentence_pause_mean_s": None, "scope_word_index": scope,
              "scope_from_s": 0.0, "scope": m["line"], "emotion_from_word": emotion_from_word, "emotion": None})
    if words is None or not text:
        m["note"] = "no heard words, so the Whisper checks and the word-timed measures are left out (never guessed)"
        return m
    chk = measure.word_check(text, words, lexicon=lexicon, tagged=tagged)
    m["whisper"] = {k: chk[k] for k in ("heard", "wer", "word_errors", "lexicon", "repeats", "spoken_tag_words")}
    med = (m["line"] or {}).get("f0_median_hz")
    lw = last_word(F, words, text, med)
    m["last_word_rise_st"], m["last_word_contour"] = lw["rise_st"], lw["contour"]
    for k in question_indices(text, table.get("question_end") or []):
        tw = time_of(words, text, k)
        if not tw:
            continue
        seg = F.f0[int(tw[0] / HOP): int(tw[1] / HOP) + 5]
        seg = seg[np.isfinite(seg)]
        third = max(1, len(seg) // 3)
        m["question_words"][str(k)] = {
            "word": ref[k] if k < len(ref) else None, "voiced_frames": int(len(seg)),
            "rise_st": round(float(12 * np.log2(np.median(seg[-third:]) / np.median(seg[:third]))), 2)
            if len(seg) >= 4 else None}
    low = F.voiced & (np.nan_to_num(F.f0, nan=1e9) < min(115.0, 0.6 * (med or 200.0)))
    cw: dict[str, int] = {}
    for k in np.flatnonzero(low):
        t = k * HOP
        wd = next((w["word"].strip() for w in words if w["start"] - 0.05 <= t <= w["end"] + 0.05), "(gap)")
        cw[wd] = cw.get(wd, 0) + 1
    m["creak_words"] = cw

    def energy_db(a: int, b: int) -> float:
        seg = F.db[max(0, a):max(a + 1, b)]
        return float(10 * np.log10(np.mean(10 ** (seg / 10)) + 1e-20))

    wl = [energy_db(int(w["start"] / HOP), int(w["end"] / HOP)) for w in words if w["end"] > w["start"]]
    m["end_drop_db"] = round(wl[-1] - float(np.median(wl)), 2) if len(wl) >= 2 else None
    spk, flo = float(np.percentile(F.db, 95)), float(np.percentile(F.db, 3))
    qthr = max(spk - 35.0, flo + 6.0)
    ends = table.get("sentence_end") or []
    bp = [measure.quiet_run(F.db, HOP, words[k]["end"] - 0.35, words[k + 1]["start"] + 0.35, qthr)
          for k in range(len(words) - 1) if lang.ends_with(words[k]["word"].strip(), ends)]
    m["sentence_pause_mean_s"] = round(float(np.mean(bp)), 3) if bp else None
    moves = measure._ending_moves(F.f0, HOP, words, ends, table.get("question_end") or [])  # noqa: SLF001
    m["sentence_endings"] = moves
    statements = [mv["move_st"] for mv in moves if not mv["question"]]
    m["statement_rise_max_st"] = round(max(statements), 2) if statements else None
    if scope:
        tw = time_of(words, text, scope)
        if tw:
            m["scope"] = breath.span(F, tw[0])
            m["scope_from_s"] = round(tw[0] - 0.02, 2)
    if emotion_from_word:
        tw = time_of(words, text, emotion_from_word)
        m["emotion"] = breath.span(F, tw[0]) if tw else None
    else:
        m["emotion"] = m["line"]
    return m


# ---------------------------------------------------------------------------------------------------------------
# Comparing takes


AB_KEYS = ("f0_median_hz", "f0_range_st", "f0_sd_st", "voiced_frac", "cpps_db", "hnr_db", "h1h2_db", "tilt_db",
           "level_db", "centroid_hz", "f2_hz", "creak_frac")


def ab(reference: dict, other: dict) -> dict:
    """`other` against `reference`: each line measure's change (pitch in semitones), and the breath verdict with the
    reference as the baseline (its SDs at the floors, since one take has no spread of its own)."""
    a, b = reference.get("line") or {}, other.get("line") or {}
    out: dict = {}
    for k in AB_KEYS:
        if a.get(k) is None or b.get(k) is None:
            out[k] = None
        elif k == "f0_median_hz":
            out["f0_shift_st"] = round(12 * math.log2(b[k] / a[k]), 2)
        else:
            out[k] = round(b[k] - a[k], 3)
    ref_line = dict(a, effort_level_db=a.get("level_db"))
    oth_line = dict(b, effort_level_db=b.get("level_db"))
    cal = breath.calibrate({"ref": [ref_line]})
    out["breath"] = breath.breathiness(oth_line, "ref", cal) if a and b else None
    for band in ("sibilance_vs_full_db", "sibilance_peak_db", "edge_vs_full_db", "edge_peak_db"):
        av, bv = (reference.get("bands") or {}).get(band), (other.get("bands") or {}).get(band)
        out[band] = None if av is None or bv is None else round(bv - av, 2)
    for k in ("lufs", "duration_s"):
        out[k] = None if reference.get(k) is None or other.get(k) is None else round(other[k] - reference[k], 3)
    return out


# ---------------------------------------------------------------------------------------------------------------
# The stage: `slate voice analyze <takes…> [--ab other] [--sheet]`

STAGES = ("polished", "paced", "clean", "raw")


def _slug(text: str) -> str:
    import re  # noqa: PLC0415

    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()[:60] or "take"


def resolve_target(ref: str, vf=None, *, stage: str | None = None) -> dict:
    """A take to analyze: a WAV path, or (in a film) a shot's take: `S01` (the selected take), `S01/t02`, with
    `stage` choosing the file (default: the take before the polish, which is what voice measurements read)."""
    from slate.errors import UsageError  # noqa: PLC0415

    p = Path(ref).expanduser()
    if p.is_file():
        return {"ref": ref, "label": p.stem, "path": p, "shot": None, "take": None, "text": None, "tagged": None,
                "words": None, "words_source": None}
    if vf is None:
        raise UsageError(f"{ref!r} isn't a file, and there's no film to find a shot in.",
                         hint="Pass WAV paths, or run inside a film (or with --film) and name shots like S01 or S01/t02.")
    shot, _, take = str(ref).partition("/")
    ln = vf.line(shot)
    if take:
        if take not in vf.take_ids(shot):
            raise UsageError(f"{shot} has no take {take} (takes: {', '.join(vf.take_ids(shot)) or 'none'}).")
        info = (vf.entry(shot) or {}).get("takes", {}).get(take) or {}
    else:
        sel = vf.selected(shot)
        if not sel:
            raise UsageError(f"{shot} isn't voiced yet.", hint=f"slate voice tts --only {shot}")
        take, info = sel
    order = [stage] if stage else ["paced", "clean", "raw"]
    path = next((vf.dir / info[s] for s in order if info.get(s) and (vf.dir / info[s]).is_file()), None)
    if path is None:
        raise UsageError(f"{shot} {take} has no {stage or 'audio'} file.")
    tj = vf.take_json(shot, take) if (vf.take_dir(shot) / f"{take}.json").is_file() else {}
    words, source = _film_words(vf, shot, take, path)
    return {"ref": ref, "label": f"{shot} {take} ({next(s for s in order if info.get(s) and vf.dir / info[s] == path)})",
            "path": path, "shot": shot, "take": take, "text": ln.text, "tagged": tj.get("sent") or ln.text,
            "words": words, "words_source": source, "replay": bool(tj.get("replay"))}


def _film_words(vf, shot: str, take: str, path: Path) -> tuple[list[dict] | None, str | None]:
    """The take's heard words when they time this file: the alignment of the same audio, or of a polish of it (the
    polish keeps every sample where it was). Replayed takes carry their own stand-in words."""
    from slate import jsonio  # noqa: PLC0415
    from slate.voice import align as align_mod  # noqa: PLC0415

    sha = jsonio.sha256(path)
    sel = vf.selected(shot)
    for engine in ("whisper", None, "replay"):
        doc = align_mod.load(vf.dir, shot, engine)
        if not doc:
            continue
        same = doc.get("sha256") == sha
        if not same and sel and sel[0] == take:
            pol = ((vf.take_json(shot, take).get("stages") or {}).get("polish") or {})
            same = (pol.get("sha256") == doc.get("sha256") and pol.get("source_sha256") == sha) or \
                   (pol.get("source_sha256") == doc.get("sha256") and pol.get("sha256") == sha)
        if same:
            name = (doc.get("engine") or {}).get("name") or engine or "alignment"
            return measure.whisper_words(doc), name
    return None, None


def run(targets: list[str], *, film_dir: str | Path | None = None, ab_ref: str | None = None, sheet: bool = False,
        text: str | None = None, tagged: str | None = None, words_file: str | None = None, whisper: bool = False,
        stage: str | None = None, out: str | Path | None = None, name: str | None = None) -> dict:
    """Measure takes (and compare them with a reference). Writes analysis.json (and sheet.png with `sheet`)."""
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    from slate import jsonio  # noqa: PLC0415
    from slate.errors import UsageError  # noqa: PLC0415
    from slate.voice import gates as gates_mod  # noqa: PLC0415
    from slate.voice import sheet as sheet_mod  # noqa: PLC0415
    from slate.voice.film import VoiceFilm, now  # noqa: PLC0415

    if not targets:
        raise UsageError("Name at least one take to analyze (a WAV, or a shot like S01 in a film).")
    vf = VoiceFilm(film_dir) if film_dir else None
    items = ([resolve_target(ab_ref, vf, stage=stage)] if ab_ref else []) + \
        [resolve_target(t, vf, stage=stage) for t in targets]
    for it in items:
        it["text"] = text or it["text"]
        it["tagged"] = tagged or it["tagged"]
    if words_file:
        doc = json.loads(Path(words_file).read_text(encoding="utf-8"))
        for it in items[1 if ab_ref else 0:]:
            it["words"], it["words_source"] = measure.whisper_words(doc), str(words_file)
    if whisper:
        from slate.voice import align as align_mod  # noqa: PLC0415

        need = [it for it in items if it["words"] is None]
        if need:
            folder = Path(out or (vf.dir / "voice" / "analysis" if vf else Path.cwd() / "voice-analysis")) / "whisper"
            language = (vf.profile.get("language") if vf else None) or "en"
            heard = align_mod.run_whisper([it["path"] for it in need], folder, language=language)
            for it in need:
                doc = heard.get(it["path"])
                if doc:
                    it["words"], it["words_source"] = measure.whisper_words(doc), "openai-whisper"
    prof = vf.profile if vf else {}
    lexicon = prof.get("lexicon") or []
    language = prof.get("language") or (vf.language if vf else "en")
    folder = Path(out) if out else ((vf.dir / "voice" / "analysis" / _slug(name or time.strftime("%Y%m%d-%H%M%S")))
                                    if vf else Path.cwd() / "voice-analysis" / _slug(name or time.strftime("%Y%m%d-%H%M%S")))
    reference = None
    rows = []
    for i, it in enumerate(items):
        frames = breath.Frames(it["path"])
        m = measure_take(it["path"], text=it["text"], words=it["words"], tagged=it["tagged"], lexicon=lexicon,
                         language=language, frames=frames)
        if it["words"]:
            m["tails"] = gates_mod.trailing_voice(it["path"], max(w["end"] for w in it["words"]))
        row = {"label": it["label"], "ref": it["ref"], "file": str(it["path"]), "shot": it["shot"], "take": it["take"],
               "words_source": it["words_source"], "replay": it.get("replay", False), "measures": m,
               "_frames": frames, "_words": it["words"]}
        if ab_ref and i == 0:
            row["reference"] = True
            reference = m
        elif reference is not None:
            row["vs_reference"] = ab(reference, m)
        rows.append(row)
    sheet_path = None
    if sheet:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        entries = [{"label": f"{letters[i % 26]} · {r['label']}", "path": r["file"], "frames": r["_frames"],
                    "measures": r["measures"],
                    "words": r["_words"], "vs": r.get("vs_reference"),
                    "span_start_s": r["measures"].get("scope_from_s") or None} for i, r in enumerate(rows)]
        title = "A/B: " + " against ".join(r["label"] for r in rows[:3]) + (" …" if len(rows) > 3 else "")
        sheet_path = sheet_mod.ab_sheet(entries, folder / "sheet.png", title=title)
    for r in rows:
        r.pop("_frames", None)
        r.pop("_words", None)
    doc = {"generated": "do not edit by hand: slate voice analyze", "film": vf.id if vf else None,
           "reference": rows[0]["label"] if ab_ref else None, "takes": rows,
           "sheet": str(sheet_path) if sheet_path else None, "at": now(),
           "note": "Proxies, not ears: these measure breath, pitch, endings, sibilance and what Whisper heard. Listen "
                   "before you pick."}
    problems = jsonio.validate(doc, "voice_analysis")
    if problems:  # pragma: no cover - a bug in this module, not in the takes
        raise UsageError("The analysis doesn't match its schema: " + "; ".join(problems[:5]))
    path = jsonio.write_atomic(folder / "analysis.json", doc)
    return {"file": str(path), "sheet": doc["sheet"], "takes": rows, "reference": doc["reference"]}
