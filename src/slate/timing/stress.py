"""Where a word's stressed syllable starts, measured from the take (lesson L-timing-D006; the films' stress_onset.py).

A gesture keys to S, the frame where its word's stressed syllable starts, and lands on the contact frame C = S + 2
(engine/remotion/kit/core/motif.ts: a beat's `stress_start` puts its contact there). For first-syllable stress S is the
word's start, which the timing already snaps to the take's energy onset. For later stress ("im-PROVED", "se-CURE-ly")
S is measured from the take's loudness envelope: the stop consonant before a stressed syllable leaves a clean gap, and
the syllable starts where that gap ends. Re-measure whenever a take is re-voiced.

The maths is the films' tool's, unchanged: a 10 ms RMS window every 5 ms over the word (from 30 ms after its start, past
its first consonant), smoothed over three steps; a gap is a run at least 20 ms long more than 16 dB under the word's
median level (a voiced /p/ in running speech only drops to about -35 dB, a clean /k/ to -70 or lower). `gap` picks the
Nth gap counting from the end: "ex-CLU-sive" has two (/k/ in "ex", then /k/ in "-clu-"), and the stress follows the last.

Times come back in film seconds on the same path the sync gate uses (the shot's start, its voice cue's `at`, the word's
time in the take), so the beat it gives is the one the gate checks. `stress_end` is the word's end: the tool measures
where the syllable starts, not where it ends, and the gate already holds a gesture to the word's end when nothing
tighter is given.

    measure(film_dir, "S12", "improved") -> {stress_start, stress_end, s_frame, contact_frame, method, beat, ...}
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from slate import jsonio
from slate.errors import MissingPrerequisite, Refused, UsageError

HOP_S = 0.005  # the envelope's step
WIN_S = 0.010  # its RMS window
LEAD_S = 0.03  # start looking just past the word's first consonant
SMOOTH = 3  # steps in the moving average
GAP_UNDER_MEDIAN_DB = 16.0  # a closure sits this far under the word's median level
MIN_GAP_S = 0.02  # and lasts at least this long
CONTACT_LEAD = 2  # C = S + 2 (the kit's CONTACT_LEAD)


def envelope(x: np.ndarray, sr: int, a: float, b: float, hop: float = HOP_S,
             win: float = WIN_S) -> tuple[np.ndarray, np.ndarray]:
    """Loudness in dB every `hop` seconds from `a` to `b`, each a `win`-long RMS, the take normalised to its own peak."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    peak = float(np.abs(x).max()) if x.size else 0.0
    x = x / (peak or 1.0)
    ts = np.arange(a, b, hop)
    out = []
    for t in ts:
        seg = x[max(0, int((t - win / 2) * sr)):max(0, int((t + win / 2) * sr))]
        out.append(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) if len(seg) else -120.0)
    return ts, np.array(out, dtype=np.float64)


def gaps(ts: np.ndarray, db: np.ndarray, floor: float, min_len: float = MIN_GAP_S) -> list[tuple[float, float, float]]:
    """Runs of the envelope below `floor` dB (a stop's closure), at least `min_len` long: (start, end, depth)."""
    found = []
    start = None
    for t, v in zip(ts, db):
        if v < floor and start is None:
            start = t
        elif v >= floor and start is not None:
            if t - start >= min_len:
                seg = db[(ts >= start) & (ts < t)]
                found.append((float(start), float(t), float(seg.min())))
            start = None
    return found


def onset(x: np.ndarray, sr: int, a: float, b: float, *, gap: int = 1) -> dict:
    """The stressed syllable's start inside the word spoken from `a` to `b` (seconds in this audio).

    {found, start (seconds in this audio), offset_s (from the word's start), gap: [start, end], depth_db, gaps, floor_db}.
    No stop gap (or fewer than `gap`) means first-syllable stress: `found` is False and `start` is the word's start."""
    if gap < 1:
        raise UsageError(f"--gap counts gaps from the end of the word, from 1; {gap} isn't one.")
    if b - a <= LEAD_S + MIN_GAP_S:
        return {"found": False, "start": a, "offset_s": 0.0, "gap": None, "depth_db": None, "gaps": 0,
                "floor_db": None, "why": f"the word is only {(b - a) * 1000:.0f} ms long: too short to hold a stop gap"}
    ts, db = envelope(x, sr, a + LEAD_S, b)
    smooth = np.convolve(db, np.ones(SMOOTH) / SMOOTH, mode="same")
    floor = float(np.median(smooth)) - GAP_UNDER_MEDIAN_DB
    found = gaps(ts, smooth, floor)
    if len(found) < gap:
        return {"found": False, "start": a, "offset_s": 0.0, "gap": None, "depth_db": None, "gaps": len(found),
                "floor_db": round(floor, 1), "why": f"no stop gap inside the word ({len(found)} found, gap {gap} asked "
                                                    "for): first-syllable stress, S is the word's start"}
    s, e, depth = found[-gap]
    return {"found": True, "start": e, "offset_s": e - a, "gap": [s, e], "depth_db": depth, "gaps": len(found),
            "floor_db": round(floor, 1), "why": f"gap {s:.3f}-{e:.3f} s (down to {depth:.0f} dB): the stressed syllable "
                                                f"starts where it ends, {e - a:+.3f} s into the word"}


def measure(film_dir: str | Path, shot_id: str, word: str, *, occ: int = 1, gap: int = 1) -> dict:
    """S and C for `word` (its `occ`-th time in the shot's read), in film seconds and frames, measured from the take
    the film's voice cue plays. Raises when the word isn't heard: an estimate would key a gesture to a guess."""
    from slate.clock import Clock  # noqa: PLC0415
    from slate.timing import words as words_mod  # noqa: PLC0415
    from slate.timing.takes import film_file  # noqa: PLC0415
    from slate.voice import audio as audio_mod  # noqa: PLC0415

    film_dir = Path(film_dir)
    if not isinstance(occ, int) or occ < 1:
        raise UsageError(f"--occ is which time the word is said in the line, from 1; {occ!r} isn't one.")
    shots_doc = jsonio.read(film_file(film_dir, Path("timing") / "shots.json"), default=None) or {}
    if not shots_doc.get("fps_num"):
        raise MissingPrerequisite("The film has no clock yet (timing/shots.json).", hint="Run `slate timing` first.")
    clock = Clock(int(shots_doc["fps_num"]), int(shots_doc["fps_den"]))
    shot = next((s for s in shots_doc.get("shots", []) if s.get("id") == shot_id), None)
    if shot is None:
        known = ", ".join(s["id"] for s in shots_doc.get("shots", [])[:40])
        raise UsageError(f"No shot {shot_id!r} in timing/shots.json.", hint=f"Shots: {known}")
    cues = jsonio.read(film_file(film_dir, Path("timing") / "vo_cues.json"), default=None) or {}
    cue = next((c for c in cues.get("cues", []) if c.get("shot") == shot_id), None)
    record = ((jsonio.read(film_file(film_dir, Path("timing") / "vo_words.json"), default=None) or {})
              .get("shots") or {}).get(shot_id) or {}
    if cue is None or not cue.get("file") or not record.get("words"):
        raise MissingPrerequisite(f"{shot_id} has no timed voice take yet, so there's nothing to measure.",
                                  hint="Voice the line, then run `slate timing` (it times every word).")
    lang = str((jsonio.read(film_dir / "film.json", default=None) or {}).get("language") or "en")
    a = words_mod.lookup(record, word, occ, "start", lang)
    b = words_mod.lookup(record, word, occ, "end", lang)
    if a is None or b is None:
        heard = " ".join(str(w.get("w", "")) for w in record.get("words") or [])
        raise Refused(f"{word!r}{f' (time {occ})' if occ > 1 else ''} isn't heard in {shot_id}'s take, so its stress "
                      f"can't be measured.", hint=f"The take says: {heard[:300]}")
    take_path = film_file(film_dir, Path(cue["file"]))
    x, sr = audio_mod.load(take_path)
    found = onset(x, sr, float(a[0]), float(b[0]), gap=gap)
    base = shot["frame_in"] / clock.fps + float(cue.get("at") or 0.0)  # the sync gate's word_span
    stress_start = round(base + float(found["start"]), 4)
    stress_end = round(base + float(b[0]), 4)
    s_frame = clock.frame_of(stress_start)
    beat = {"word": word, **({"occ": occ} if occ > 1 else {}), "stress_start": stress_start, "stress_end": stress_end}
    return {"shot": shot_id, "word": word, "occ": occ, "take": cue["file"], "take_sha256": jsonio.sha256(take_path),
            "word_take_s": [round(float(a[0]), 4), round(float(b[0]), 4)],
            "word_film_s": [round(base + float(a[0]), 4), round(base + float(b[0]), 4)],
            "method": "stop gap" if found["found"] else "first syllable", "measured": found["found"],
            "why": found["why"], "gap_take_s": [round(v, 4) for v in found["gap"]] if found["gap"] else None,
            "depth_db": round(found["depth_db"], 1) if found["depth_db"] is not None else None,
            "gaps_in_word": found["gaps"], "floor_db": found["floor_db"],
            "offset_s": round(float(found["offset_s"]), 4), "stress_start": stress_start, "stress_end": stress_end,
            "s_frame": s_frame, "contact_frame": s_frame + CONTACT_LEAD, "beat": beat}
