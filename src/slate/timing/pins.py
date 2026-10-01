"""Pinned word times: a re-voice moves only the spans it corrects.

When a line is re-voiced its words land at new times, and every beat keyed to those words would move with them. Where
a beat shapes something that isn't changing (a neighbouring shot that's approved, a contract between acts), the film
keeps its delivered timing instead: `slate timing pin` records the delivered word times (film seconds, from the
timing/vo_words.json in place before the re-voice) in timing/pinned_words.json, and the timing build lays those
times over the new take's tokens, so the timeline, `word_time` and the engines' word lookups all keep them. Only the
words left unpinned follow the new take. (The films kept "a recorded table of the old word times" for exactly this.)

    timing/pinned_words.json
    {"shots": {"S12": {"words": [{"w": "companies", "occ": 1, "start": 41.237, "end": 41.802}],
                       "from": {"take": "voice/takes/S12/t02.polished.wav", "sha256": "…"},
                       "why": "S13's card lands on it, and S13 is approved", "at": "…"}}}

A pin is film time, so it holds wherever the new take sits. Unpin a shot (or some of its words) once the beats that
lean on it are re-timed on purpose.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from slate import jsonio
from slate.errors import UsageError

PINS_FILE = Path("timing") / "pinned_words.json"
OCC = re.compile(r"^(?P<w>.+?)#(?P<n>\d+)$")


def path(film_dir: str | Path) -> Path:
    return Path(film_dir) / PINS_FILE


def load(film_dir: str | Path) -> dict:
    """{shot: {words: [{w, occ, start, end}], from, why, at}} (empty when nothing is pinned)."""
    doc = jsonio.read(path(film_dir), default=None) or {}
    return dict(doc.get("shots") or {})


def _save(film_dir: Path, shots: dict) -> Path:
    return jsonio.write_atomic(path(film_dir), jsonio.mark_generated({
        "note": "Delivered word times (film seconds) a re-voice must not move: the timing build lays them over the "
                "new take's tokens. `slate timing pin --list` shows them; --clear lets them go.",
        "shots": dict(sorted(shots.items()))}, "slate timing pin"))


def _word_spec(spec: str) -> tuple[str, int]:
    m = OCC.match(spec.strip())
    return (m.group("w"), int(m.group("n"))) if m else (spec.strip(), 1)


def _shot_start(shots_doc: dict, shot: str) -> float:
    fps = float(shots_doc["fps_num"]) / float(shots_doc["fps_den"])
    sh = next((s for s in shots_doc.get("shots") or [] if s.get("id") == shot), None)
    if sh is None:
        raise UsageError(f"No shot {shot!r} in timing/shots.json.")
    return float(sh["frame_in"]) / fps


def _cue_at(cues_doc: dict | list, shot: str) -> float:
    cues = cues_doc.get("cues") if isinstance(cues_doc, dict) else cues_doc
    cue = next((c for c in cues or [] if c.get("shot") == shot), None)
    if cue is None:
        raise UsageError(f"{shot} has no voice cue in timing/vo_cues.json, so it has no word times to pin.")
    return float(cue.get("at", 0.0))


def pin(film_dir: str | Path, shot: str, words: list[str], *, why: str | None = None) -> dict:
    """Pin these words of a shot at their delivered film times (from the timing files in place now). A word is
    "word", or "word#2" for its second time in the line; "all" pins every word of the line. Only words the take was
    heard saying can be pinned: an estimate isn't a delivered time."""
    from slate.timing import words as wordlib  # noqa: PLC0415

    film_dir = Path(film_dir)
    timing = film_dir / "timing"
    shots_doc = jsonio.read(timing / "shots.json", default=None)
    words_doc = jsonio.read(timing / "vo_words.json", default=None)
    cues_doc = jsonio.read(timing / "vo_cues.json", default=None)
    if not shots_doc or not words_doc or cues_doc is None:
        raise UsageError("There's no delivered timing to pin yet (timing/shots.json, vo_cues.json, vo_words.json).",
                         hint="Run `slate timing` first.")
    rec = (words_doc.get("shots") or {}).get(shot)
    if not rec:
        raise UsageError(f"{shot} has no word timings in timing/vo_words.json.")
    t0 = _shot_start(shots_doc, shot) + _cue_at(cues_doc, shot)
    tokens = rec.get("tokens") or []
    if [w.strip().lower() for w in words] == ["all"]:
        counts: dict[str, int] = {}
        wanted = []
        for tok in tokens:
            counts[tok["key"]] = counts.get(tok["key"], 0) + 1
            wanted.append((tok["t"], counts[tok["key"]]))
    else:
        wanted = [_word_spec(w) for w in words if w.strip()]
    if not wanted:
        raise UsageError("Name the words to pin (--words companies,growth#2), or all of them (--words all).")
    lang = (jsonio.read(film_dir / "film.json", default={}) or {}).get("language") or "en"
    out, missing = [], []
    for w, occ in wanted:
        tok = wordlib.token_span(tokens, w, occ, lang)
        if tok is None or not tok.get("exact"):
            missing.append(f"{w}#{occ}" if occ > 1 else w)
            continue
        out.append({"w": w, "occ": occ, "start": round(t0 + float(tok["start"]), 4),
                    "end": round(t0 + float(tok["end"]), 4)})
    if missing:
        raise UsageError(f"{shot}'s take wasn't heard saying {', '.join(missing)}, so there's no delivered time to "
                         "pin.", hint=f"slate timing show {shot} lists its words and their times.")
    pins = load(film_dir)
    entry = pins.get(shot) or {"words": []}
    keep = [p for p in entry.get("words") or [] if (p["w"], p["occ"]) not in {(o["w"], o["occ"]) for o in out}]
    take = film_dir / str(rec.get("take") or "")
    pins[shot] = {"words": keep + out, "why": why or entry.get("why"),
                  "from": {"take": rec.get("take"), "sha256": jsonio.sha256(take) if take.is_file() else None},
                  "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
    _save(film_dir, pins)
    return {"shot": shot, "pinned": out, "file": str(PINS_FILE)}


def clear(film_dir: str | Path, shot: str | None = None, words: list[str] | None = None) -> dict:
    """Let pins go: a shot's (or only some of its words), or every pin when no shot is named."""
    film_dir = Path(film_dir)
    pins = load(film_dir)
    if shot is None:
        dropped = sum(len(e.get("words") or []) for e in pins.values())
        pins = {}
    elif words:
        gone = {_word_spec(w) for w in words}
        entry = pins.get(shot) or {"words": []}
        before = len(entry.get("words") or [])
        entry["words"] = [p for p in entry.get("words") or [] if (p["w"], p["occ"]) not in gone]
        dropped = before - len(entry["words"])
        if entry["words"]:
            pins[shot] = entry
        else:
            pins.pop(shot, None)
    else:
        dropped = len((pins.pop(shot, None) or {}).get("words") or [])
    _save(film_dir, pins)
    return {"cleared": dropped, "shot": shot}


def pinned_time(film_dir: str | Path, shot: str, word: str, occ: int = 1, which: str = "start",
                lang: str = "en") -> float | None:
    """A pinned word's film time, or None (word_time prefers it over the take's)."""
    from slate.timing import spoken  # noqa: PLC0415

    entry = load(film_dir).get(shot)
    if not entry:
        return None
    key = spoken.norm(spoken.strip_edges(word, lang), lang)
    for p in entry.get("words") or []:
        if int(p.get("occ", 1)) == int(occ) and spoken.norm(spoken.strip_edges(p["w"], lang), lang) == key:
            return float(p["end" if which == "end" else "start"])
    return None


def apply(film_dir: str | Path, words_doc: dict, shots_doc: dict, cues: dict | list, lang: str = "en") -> list[dict]:
    """Lay the pinned times over the build's tokens, in place (take-relative, wherever the new take sits), so the
    timeline, word_time and the engines' word lookups all keep them. Returns what was applied (and what couldn't be:
    a pinned word the new line no longer has)."""
    from slate.timing import words as wordlib  # noqa: PLC0415

    applied = []
    for shot, entry in load(film_dir).items():
        rec = (words_doc.get("shots") or {}).get(shot)
        if not rec:
            applied.append({"shot": shot, "skipped": "the shot has no take in this build"})
            continue
        t0 = _shot_start(shots_doc, shot) + _cue_at(cues, shot)
        for p in entry.get("words") or []:
            tok = wordlib.token_span(rec.get("tokens") or [], p["w"], int(p.get("occ", 1)), lang)
            if tok is None:
                applied.append({"shot": shot, "w": p["w"], "occ": p.get("occ", 1),
                                "skipped": "the line no longer has this word"})
                continue
            moved = round(float(p["start"]) - t0 - float(tok.get("start") or 0.0), 4)
            tok.update({"start": round(float(p["start"]) - t0, 4), "end": round(float(p["end"]) - t0, 4),
                        "exact": True, "pinned": True})
            applied.append({"shot": shot, "w": p["w"], "occ": p.get("occ", 1), "film_s": p["start"],
                            "kept_from_the_new_take_s": moved})
    return applied
