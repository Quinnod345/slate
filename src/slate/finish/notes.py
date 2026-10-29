"""Review notes and the targeted fix plan (docs/PLAN.md §7, "Review and correct").

`slate notes add TEXT [--at TIMECODE]` logs the owner's words exactly as given, as a numbered direction change
(slate.state.production.add_direction, which also renders brief/PRODUCTION.md), and keeps the finish stage's own record
in review/notes.json: the version it's about, and where the timecode lands on the film's clock:

- the **shot** (timing/shots.json) and its act;
- the nearest **beat** within [finish.review] beat_window_s (visual events and the timeline's events);
- the line being **spoken** there, and the word (vo_words.json placed by vo_cues.json);
- the **stage** that fixes it: from the note's own words (src/slate/finish/data/stages.json, per language), or
  `--stage` when the owner says so. A note that asks for a new approach becomes a question instead.

Timecodes are the ones burned into the phone cut (HH:MM:SS:FF on the film's clock), the contact sheet's m:ss.s, plain
seconds, `f123` for a film frame, or a range (`0:12-0:15`).

`slate notes plan` turns a version's notes into the targeted fix plan. Preserve the baseline: every step edits the
approved artifact in place. Re-voice only the lines that changed (the voice stage sends each line's neighbours as
context), re-render only the affected frame range and splice it in with proof, re-mix, then re-run the same gates on
what changed. A note that needs a new approach is asked as a question, never acted on.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from slate import gates, jsonio, log
from slate.errors import UsageError
from slate.finish import deps, settings
from slate.finish.film import NOTES_FILE, Film

STAGES = ("voice", "picture", "sound", "captions", "timing")
STAGE_GATES = {
    "voice": ["sync", "captions", "loudness", "banned_words"],
    "picture": ["frames", "glitch", "boundary", "motion", "access", "readtime", "ocr", "banned_words", "psnr"],
    "sound": ["loudness", "stems_sum", "silent_stems", "bit_depth", "sync"],
    "captions": ["captions", "banned_words"],
    "timing": ["frames", "events_complete", "sync", "captions"],
}
RANGE_RE = re.compile(r"^\s*(.+?)\s*(?:–|—|-|\bto\b)\s*(.+?)\s*$")
FRAME_RE = re.compile(r"^\s*(?:f|frame\s*)(\d+)\s*$", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def _stage_data() -> dict:
    return json.loads((Path(__file__).parent / "data" / "stages.json").read_text(encoding="utf-8"))


def infer_stage(text: str, lang: str = "en") -> tuple[str | None, str | None]:
    """(stage or 'new_approach', the word that decided it), from the film language's word table; (None, None)
    when nothing matches or the language has no table."""
    data = _stage_data()
    table = data.get(lang)
    if not isinstance(table, dict):
        return None, None
    low = f" {str(text).lower()} "
    for stage in data.get("order") or []:
        for w in table.get(stage) or []:
            w = str(w).lower()
            if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low):
                return stage, w
    return None, None


# ---------------------------------------------------------------------------------------------------------------
# Timecodes and where they land


def _one_frame(film: Film, text: str) -> int:
    t = str(text).strip()
    m = FRAME_RE.match(t)
    if m:
        return int(m.group(1))
    try:
        return film.clock.frame_of_timecode(t)
    except (ValueError, IndexError) as exc:
        raise UsageError(f"{text!r} isn't a timecode Slate reads.",
                         hint="Use the phone cut's HH:MM:SS:FF, m:ss.s, seconds, f123 for a film frame, or a range "
                              "like 0:12-0:15.") from exc


def parse_at(film: Film, at: str) -> tuple[int, int | None]:
    """(first film frame, last film frame or None) for a timecode or a range, checked against the film."""
    text = str(at or "").strip()
    if not text:
        raise UsageError("The timecode is empty.")
    m = RANGE_RE.match(text)
    a, b = (m.group(1), m.group(2)) if m else (text, None)
    f_in = _one_frame(film, a)
    f_to = _one_frame(film, b) if b else None
    total = film.total_frames
    for f in (f_in, f_to):
        if f is not None and not 0 <= f < total:
            raise UsageError(f"{at} is outside the film: it runs {film.timecode(0)} to {film.timecode(total - 1)} "
                             f"({total} frames).")
    if f_to is not None and f_to < f_in:
        f_in, f_to = f_to, f_in
    return f_in, f_to


def _tokens(text: str) -> set[str]:
    """Distinct words of four letters or more (short ones like "the" would match every line)."""
    return {w for w in re.findall(r"[^\W\d_]+", str(text).lower()) if len(w) >= 4}


def speech(film: Film) -> list[dict]:
    """Every voiced line as placed in the film: {shot, start, end (film seconds), line, words}."""
    out = []
    words = film.ctx.vo_words or {}
    cues = {str(c.get("shot")): c for c in film.ctx.vo_cues}
    by_id = {str(s.get("id")): s for s in film.shots}
    for sid, w in words.items():
        c, s = cues.get(sid), by_id.get(sid)
        ws = [x for x in (w or {}).get("words") or [] if isinstance(x, dict) and "start" in x and "end" in x]
        if not c or not s or not ws:
            continue
        base = float(s.get("sec_in", film.clock.secs_of(int(s["frame_in"])))) + float(c.get("at") or 0.0)
        out.append({"shot": sid, "start": base + float(ws[0]["start"]), "end": base + float(ws[-1]["end"]),
                    "base": base, "line": w.get("line") or s.get("vo") or "", "words": ws})
    return sorted(out, key=lambda x: x["start"])


def voice_line(film: Film, frame: int, text: str | None = None) -> tuple[dict | None, str | None]:
    """(the voiced line a note at `frame` is about, how it was found).

    The line being spoken at that moment wins; else, near the moment, a line holding a word the note mentions; else
    the nearest line within twice the beat window. None when no line is near.
    """
    lines = speech(film)
    if not lines:
        return None, None
    t = film.clock.secs_of(frame)
    for ln in lines:
        if ln["start"] - 0.05 <= t <= ln["end"] + 0.05:
            return ln, "spoken at that moment"
    reach = 2.0 * float(settings.load()["review"].get("beat_window_s") or 1.0)

    def dist(ln: dict) -> float:
        return 0.0 if ln["start"] <= t <= ln["end"] else min(abs(t - ln["start"]), abs(t - ln["end"]))

    near = [ln for ln in lines if dist(ln) <= reach]
    said = _tokens(text or "")
    named = [ln for ln in near if said & _tokens(ln["line"])]
    if named:
        return min(named, key=dist), "the note names a word in this line"
    if near:
        return min(near, key=dist), "the nearest line to that moment"
    return None, None


def _word_at(ln: dict, t: float) -> str | None:
    rel = t - ln["base"]
    for w in ln["words"]:
        try:
            if float(w["start"]) - 0.05 <= rel <= float(w["end"]) + 0.05:
                return str(w.get("w"))
        except (TypeError, ValueError):
            continue
    return None


def locate(film: Film, frame: int, frame_to: int | None = None, text: str | None = None) -> dict:
    """Where a film frame (or range) lands: shot, act, the nearest beat, the voiced line (and word) it's about."""
    shot = film.shot_at(frame) or {}
    window = film.clock.frame_of(float(settings.load()["review"].get("beat_window_s") or 1.0))
    beat = None
    best = None
    for b in film.beats():
        d = abs(int(b["frame"]) - frame)
        if d <= window and (best is None or d < best or (d == best and b.get("shot") == shot.get("id"))):
            best, beat = d, b
    ln, how = voice_line(film, frame, text)
    word = _word_at(ln, film.clock.secs_of(frame)) if ln else None
    shots_spanned = []
    if frame_to is not None:
        for s in film.shots:
            if int(s["frame_out"]) > frame and int(s["frame_in"]) <= frame_to:
                shots_spanned.append(str(s.get("id")))
    return {
        "frame": frame, "frame_to": frame_to, "timecode": film.timecode(frame),
        "timecode_to": film.timecode(frame_to) if frame_to is not None else None,
        "seconds": round(film.clock.secs_of(frame), 3),
        "shot": str(shot.get("id")) if shot else None, "act": shot.get("act"), "act_name": shot.get("act_name"),
        "shot_frames": [shot.get("frame_in"), shot.get("frame_out")] if shot else None,
        "shots": shots_spanned or ([str(shot.get("id"))] if shot else []),
        "beat": ({"id": beat.get("id"), "kind": beat.get("kind"), "shot": beat.get("shot"), "frame": beat["frame"],
                  "offset_frames": int(beat["frame"]) - frame, "source": beat.get("source")} if beat else None),
        "voice_shot": ln["shot"] if ln else None, "voice_how": how,
        "line": ln["line"] if ln else (shot.get("vo") or None), "word": word,
    }


# ---------------------------------------------------------------------------------------------------------------
# The notes log


def load(film: Film) -> dict:
    data = jsonio.read(film.path(NOTES_FILE), default=None)
    if not isinstance(data, dict):
        data = {"generated": "do not edit by hand: slate notes", "notes": []}
    data.setdefault("notes", [])
    return data


def _save(film: Film, data: dict) -> None:
    jsonio.write_atomic(film.path(NOTES_FILE), data)


def add(film_dir: Path, text: str, *, at: str | None = None, by: str = "owner", stage: str | None = None,
        source_kind: str = "owner", version: int | None = None) -> dict:
    """Log a note verbatim. Owner notes become numbered directions; returns the note record."""
    film = Film(Path(film_dir))
    raw = str(text or "")
    if not raw.strip():
        raise UsageError("The note is empty; log it exactly as it was given.")
    if stage and stage not in STAGES:
        raise UsageError(f"Unknown stage {stage!r}; one of {', '.join(STAGES)}.")
    by = (by or "").strip() or "owner"
    where = None
    if at:
        f_in, f_to = parse_at(film, at)
        where = locate(film, f_in, f_to, raw)
    n = version or film.version()
    if stage:
        found, how, word = stage, "given", None
    else:
        found, word = infer_stage(raw, film.language)
        how = "words" if found else "guess"
        if not found:
            found = "picture" if where else "unsorted"
    new_approach = found == "new_approach"
    direction = None
    if source_kind == "owner":
        kind = f"review note on v{n}" + (f" at {where['timecode']}" if where else "")
        direction = deps.add_direction(film.dir, raw, source=by, kind=kind)
    data = load(film)
    k = 1 + max((int(str(x.get("id", "n0"))[1:]) for x in data["notes"] if str(x.get("id", "")).startswith("n")
                 and str(x.get("id"))[1:].isdigit()), default=0)
    rec = {"id": f"n{k}", "direction": direction, "text": raw, "by": by, "source_kind": source_kind, "version": n,
           "at": at, "where": where, "stage": "picture" if new_approach else found, "stage_how": how,
           "stage_word": word, "new_approach": new_approach, "question": None, "logged_at": gates.now_iso()}
    data["notes"].append(rec)
    _save(film, data)
    return rec


def list_notes(film_dir: Path, *, version: int | None = None, everything: bool = False) -> list[dict]:
    film = Film(Path(film_dir))
    n = version or film.version()
    notes = load(film)["notes"]
    return notes if everything else [x for x in notes if int(x.get("version") or 0) == n]


def describe(note: dict) -> str:
    w = note.get("where") or {}
    where = ""
    if w:
        where = f" at {w['timecode']}" + (f"–{w['timecode_to']}" if w.get("timecode_to") else "")
        where += f" ({', '.join(w.get('shots') or [])}" + (f", act {w['act']}" if w.get("act") else "")
        if w.get("beat"):
            where += f", beat {w['beat']['id']} ({w['beat']['kind']})"
        if w.get("word"):
            where += f", on “{w['word']}”"
        if note.get("stage") == "voice" and w.get("voice_shot"):
            where += f", line {w['voice_shot']}: {w.get('voice_how')}"
        where += ")"
    num = f"direction {note['direction']}" if note.get("direction") else note.get("source_kind", "note")
    stage = "requested new approach" if note.get("new_approach") else f"{note['stage']} ({note['stage_how']})"
    return f"{note['id']} [{num}, v{note['version']}, {stage}]{where}: “{note['text']}” — {note['by']}"


# ---------------------------------------------------------------------------------------------------------------
# The fix plan


def _approved_touching(film: Film, prefixes: list[str]) -> list[dict]:
    """Standing approvals whose path sits under any prefix: the approved work a fix edits in place."""
    out = []
    for rec in deps.approval_records(film.dir):
        path = str(rec.get("path") or "")
        if any(path == p.rstrip("/") or path.startswith(p) for p in prefixes):
            if deps.approval_state(film.dir, rec).get("approved"):
                out.append({"id": rec.get("id"), "what": rec.get("what"), "path": path,
                            "approved_by": rec.get("approved_by")})
    return out


def _revise_cmds(approved: list[dict], notes: list[dict]) -> list[str]:
    text = " / ".join(n["text"].strip().replace('"', "'") for n in notes)
    by = notes[0]["by"] if notes else "owner"
    return [f'slate revise start {a["path"]} --notes "{text}" --by {by}' for a in approved]


def _qa_gates(names: list[str]) -> list[str]:
    known = set(deps.qa_gate_names() or [])
    return [g for g in names if not known or g in known]


def _ask_question(film: Film, note: dict, reason: str) -> dict | None:
    text = (f"{reason} Note {note['id']}" + (f" (direction {note['direction']})" if note.get("direction") else "")
            + f": “{note['text']}”. Clarify the timing scope before invalidating rendered frames.")
    q = deps.ask(film.dir, text, source=f"slate notes ({note['id']})", evidence=[NOTES_FILE],
                 parks=film.mode == "autonomous", for_="direction")
    if q and film.mode == "autonomous":
        from slate import notify  # noqa: PLC0415

        try:
            notify.both(text, title=f"{film.title}: a question for you")
        except Exception as exc:  # noqa: BLE001 - the question is recorded either way
            log.warn(f"the question is recorded, but it couldn't be sent: {exc}")
    return q


def plan(film_dir: Path, *, version: int | None = None, ask: bool = True, write: bool = True) -> dict:
    """The targeted fix plan for a version's notes (the current version by default). Writes review/vN/fix_plan.json
    (and asks about unresolved changes to a locked timeline) unless write/ask are off, as they are for --dry-run."""
    film = Film(Path(film_dir))
    n = version or film.version()
    data = load(film)
    notes = [x for x in data["notes"] if int(x.get("version") or 0) == n]
    timeline_locked = False
    lock_mod = deps.module("slate.state.lock")
    if lock_mod is not None and callable(getattr(lock_mod, "timeline_locked", None)):
        try:
            timeline_locked = bool(lock_mod.timeline_locked(film.dir))
        except Exception:  # noqa: BLE001 - treat an unreadable lock as unlocked; the timing command re-checks
            timeline_locked = False
    steps, questions, all_gates = [], [], []
    changed = False
    # A requested redesign is work. A locked timeline still needs an explicit timing scope.
    for note in notes:
        needs = None
        if note.get("stage") == "timing" and timeline_locked:
            needs = "Changing a shot's length unlocks the timeline: every frame after it moves and gets re-rendered."
        if needs:
            q_id = note.get("question")
            if not q_id and ask:
                q = _ask_question(film, note, needs)
                if q:
                    note["question"] = q_id = q["id"]
                    changed = True
            questions.append({"note": note["id"], "question": q_id, "why": needs, "text": note["text"]})
    asked = {q["note"] for q in questions}
    todo = [x for x in notes if x["id"] not in asked]
    by_stage: dict[str, list[dict]] = {}
    for note in todo:
        by_stage.setdefault(note["stage"], []).append(note)
    # Voice: one step per shot, only the lines that changed.
    voice_by_shot: dict[str, list[dict]] = {}
    unplaced: list[dict] = []
    for note in by_stage.get("voice", []):
        w = note.get("where") or {}
        shots = [w["voice_shot"]] if w.get("voice_shot") else (w.get("shots") or [])
        if not shots:
            unplaced.append(note)
        for sid in shots:
            voice_by_shot.setdefault(sid, []).append(note)
    voiced = [ln["shot"] for ln in speech(film)]
    for sid, ns in voice_by_shot.items():
        if sid in voiced:  # the voice stage sends the neighbouring lines as context, so name those
            i = voiced.index(sid)
            prev_s, next_s = (voiced[i - 1] if i > 0 else None), (voiced[i + 1] if i + 1 < len(voiced) else None)
        else:
            prev_s, next_s = film.neighbours(sid)
        approved = _approved_touching(film, [f"voice/takes/{sid}/", f"voice/takes/{sid}"])
        cmd = f"slate voice tts --only {sid} --force" + (" --approval <spend approval id>" if approved else "")
        cmds = ([f"slate approve spend voice/takes/{sid} --by NAME   (a person's yes to re-voice approved work)"]
                if approved else []) + [cmd, f"slate voice clean --only {sid}", f"slate voice pace --only {sid}",
                                        f"slate voice align --only {sid}", "slate voice captions"]
        g = _qa_gates(STAGE_GATES["voice"])
        all_gates += g
        ctx = ", ".join(x for x in (prev_s, next_s) if x) or "no neighbours"
        steps.append({"stage": "voice", "title": f"Re-voice {sid} only (neighbour context: {ctx})", "shots": [sid],
                      "notes": [x["id"] for x in ns], "edits_approved": approved,
                      "commands": cmds + _revise_cmds(approved, ns), "gates": g})
    # Picture: one step per affected shot range, re-rendered and spliced with proof.
    pic: dict[tuple, list[dict]] = {}
    for note in unplaced + by_stage.get("picture", []) + by_stage.get("unsorted", []):
        w = note.get("where") or {}
        shots = w.get("shots") or []
        if not shots:
            pic.setdefault(("?",), []).append(note)
            continue
        pic.setdefault(tuple(shots), []).append(note)
    by_id = {str(s.get("id")): s for s in film.shots}
    for key, ns in pic.items():
        if key == ("?",):
            steps.append({"stage": "unplaced", "title": "Notes without a timecode: find the moment with the owner",
                          "shots": [], "notes": [x["id"] for x in ns], "edits_approved": [],
                          "commands": ['slate notes add "…" --at HH:MM:SS:FF   (the same words, with where it is)'],
                          "gates": []})
            continue
        spans = [by_id[s] for s in key if s in by_id]
        f_in = min(int(s["frame_in"]) for s in spans)
        f_last = max(int(s["frame_out"]) for s in spans) - 1
        acts = sorted({int(s["act"]) for s in spans})
        approved = _approved_touching(film, [f"renders/act{a}" for a in acts] + [f"scenes/act{a}" for a in acts]
                                      + ["masters/", "deliver/"])
        g = _qa_gates(STAGE_GATES["picture"])
        all_gates += g
        cmds = [f"edit the scene for {', '.join(key)} (act {', '.join(map(str, acts))}) in place: the approved "
                "picture stays as it is everywhere else",
                f"slate render --from {f_in} --to {f_last}   (re-renders film frames {f_in}–{f_last} and splices "
                "them in with proof: frame hashes and PSNR)"]
        steps.append({"stage": "picture", "title": f"Re-render {', '.join(key)}: film frames {f_in}–{f_last} "
                                                   f"({film.timecode(f_in)}–{film.timecode(f_last)})",
                      "shots": list(key), "frames": [f_in, f_last], "acts": acts, "notes": [x["id"] for x in ns],
                      "edits_approved": approved, "commands": cmds + _revise_cmds(approved, ns), "gates": g})
    # Sound, captions, timing.
    for stage, title, cmds in (
            ("sound", "Re-mix", ["slate mix", "slate verify"]),
            ("captions", "Rebuild the captions", ["slate voice captions"]),
            ("timing", "Re-time before any render is registered", ["edit the durations in script/script.json",
                                                                   "slate script build", "slate timing"])):
        ns = by_stage.get(stage, [])
        if not ns:
            continue
        extra = []
        if stage == "sound" and any((x.get("stage_word") or "") in ("music", "score", "swell") for x in ns):
            extra = ["adjust the score plan for the parts in question, then slate score compose (estimate first: it "
                     "spends) and slate score assemble"]
        approved = _approved_touching(film, ["sound/"]) if stage == "sound" else []
        g = _qa_gates(STAGE_GATES[stage])
        all_gates += g
        steps.append({"stage": stage, "title": title, "shots": sorted({s for x in ns for s in
                                                                      ((x.get("where") or {}).get("shots") or [])}),
                      "notes": [x["id"] for x in ns], "edits_approved": approved,
                      "commands": extra + cmds + _revise_cmds(approved, ns), "gates": g})
    # Anything voiced or re-timed changes the mix.
    if (voice_by_shot or by_stage.get("timing")) and not by_stage.get("sound"):
        g = _qa_gates(STAGE_GATES["sound"])
        all_gates += g
        steps.append({"stage": "sound", "title": "Re-mix with the new reads", "shots": [], "notes": [],
                      "edits_approved": [], "commands": ["slate mix", "slate verify"], "gates": g})
    master_cmd = ("slate master --ae --go   (you start the After Effects finish)"
                  if film.mode in ("supervised", "express") and str(settings.load().get("supervised_master") or "ae")
                  == "ae" else "slate master")
    uniq_gates = [g for i, g in enumerate(all_gates) if g not in all_gates[:i]]
    then = []
    if steps:
        then = [master_cmd, "slate qa " + " ".join(uniq_gates) if uniq_gates else "slate qa",
                "slate review   (the next version's packet, with what changed)"]
    if changed and write:
        _save(film, data)
    out = {"generated": "do not edit by hand: slate notes plan", "film": film.id, "version": n,
           "made_at": gates.now_iso(), "notes": len(notes),
           "rule": "Preserve the approved baseline: each step applies the requested change and keeps unrelated work as "
                   "it was; a note that needs a new approach is a question for the owner first.",
           "steps": steps, "questions": questions, "gates": uniq_gates, "then": then}
    if write:
        d = film.review_dir(n)
        d.mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(d / "fix_plan.json", out)
    return out


def plan_text(p: dict) -> str:
    if not p["notes"]:
        return f"No notes on v{p['version']} yet. Log one with: slate notes add \"your words\" --at HH:MM:SS:FF"
    lines = [f"Fix plan for {p['film']} v{p['version']} ({p['notes']} note{'s' if p['notes'] != 1 else ''}).",
             p["rule"], ""]
    for i, s in enumerate(p["steps"], 1):
        lines.append(f"{i}. {s['title']}  [{', '.join(s['notes']) or 'follows from the steps above'}]")
        for a in s.get("edits_approved") or []:
            lines.append(f"   edits approved work: {a['what']} {a['path']} (approved by {a['approved_by']})")
        for c in s["commands"]:
            lines.append(f"   - {c}")
        if s["gates"]:
            lines.append(f"   gates: {' '.join(s['gates'])}")
    if p["questions"]:
        lines += ["", "Questions for the owner first (nothing is changed for these until they answer):"]
        for q in p["questions"]:
            lines.append(f"   {q['question'] or '(not asked yet)'} for {q['note']}: {q['why']} “{q['text']}”")
            if q["question"]:
                lines.append(f"     answer: slate film answer {q['question']} --by NAME --text \"…\"")
    if p["then"]:
        lines += ["", "Then:"] + [f"   - {t}" for t in p["then"]]
    return "\n".join(lines)
