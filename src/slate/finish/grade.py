"""The reviewer's grade (docs/PLAN.md §7, autonomous mode): before a film is held for its owner, a separate reviewer
grades the contact sheet and the audio against the brand's references, the owner's taste notes and the visionary bar.

- `slate review grade-brief` writes review/vN/grade_brief.md and .json: exactly what to look at and listen to (the
  packet's contact sheets and phone cut, the film's sound, the brand's four approved references, the owner's taste
  notes, the goal and the script) and the criteria, each a question with a yes or no answer.
- `slate review grade --verdict pass|revise --notes TEXT` records the verdict in review/vN/grade.json, tied to the
  exact packet it graded (its sha256). A revise verdict's notes join the notes log (as the reviewer's, not the
  owner's directions) so `slate notes plan` fixes them.
- Holding an autonomous film for its owner (`slate review`) refuses until the current packet has a passing grade.

The reviewer is someone who didn't make the film: if you can run sub-agents, hand the brief to a fresh one;
otherwise ask a person. Nothing here judges the film by itself.
"""

from __future__ import annotations

from pathlib import Path

from slate import gates, jsonio, procs
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.finish import deps, settings
from slate.finish.film import Film

BRIEF_JSON = "grade_brief.json"
BRIEF_MD = "grade_brief.md"
GRADE = "grade.json"
HISTORY = "grades.jsonl"
VERDICTS = ("pass", "revise")

CRITERIA = [
    {"id": "look", "ask": "Does every sheet look like the brand's approved look-and-motion reference: its palette, "
                          "type, layout and feeling? Nothing generic, nothing off-brand."},
    {"id": "motion", "ask": "Do moves land cleanly in the brand's own motion, with no pops, jumps, flicker or "
                            "twisting from one tile to the next?"},
    {"id": "sound", "ask": "Against the brand's sound reference: are the levels right under the voice, the materials "
                           "the brand's own, nothing it bans, and every sound on its beat?"},
    {"id": "voice", "ask": "Is it the brand's approved voice, clear and unrushed, with no clipped or leaked words?"},
    {"id": "truth", "ask": "Is every claim on the brand's approved list (or sourced), and nothing on its never-claim "
                           "list?"},
    {"id": "taste", "ask": "Does it honour every one of the owner's taste notes listed here?"},
    {"id": "legibility", "ask": "Can every line of on-screen text be read in the time it's up, inside the format's "
                                "safe zones?"},
    {"id": "visionary", "ask": "The visionary bar: is there at least one moment a stranger would remember, something "
                               "only this brand would make? Is it distinctive rather than the median version of "
                               "this film?"},
]


def _packet(film: Film, n: int) -> tuple[Path, dict]:
    path = film.review_dir(n) / "packet.json"
    data = jsonio.read(path, default=None)
    if not isinstance(data, dict):
        raise MissingPrerequisite(f"There's no review packet for v{n} to grade.", hint="slate review")
    return path, data


def latest_packet_version(film: Film) -> int | None:
    root = film.path("review")
    best = None
    if root.is_dir():
        for d in root.glob("v*"):
            if d.name[1:].isdigit() and (d / "packet.json").is_file():
                best = max(best or 0, int(d.name[1:]))
    return best


def _references(film: Film) -> list[dict]:
    pack, folder = deps.brand_pack(film.brand)
    out = []
    refs = (pack or {}).get("references") or {}
    for key in ("look_motion", "sound", "voice", "truth"):
        ref = refs.get(key)
        if not isinstance(ref, dict):
            out.append({"reference": key, "file": None, "approved": False, "note": "not in the brand pack"})
            continue
        f = ref.get("file")
        full = (Path(folder) / f) if (folder and f and not Path(f).is_absolute()) else (Path(f) if f else None)
        out.append({"reference": key, "file": str(full) if full else None, "exists": bool(full and full.exists()),
                    "sha256": ref.get("sha256"), "approved": bool(ref.get("approved_by")),
                    "approved_by": ref.get("approved_by"), "approved_at": ref.get("approved_at")})
    return out


def _taste(film: Film) -> list[dict]:
    """The owner's taste notes: files from [finish.review] taste_notes, and any `taste` the brand pack keeps."""
    out = []
    for p in settings.load()["review"].get("taste_notes") or []:
        full = Path(str(p)).expanduser()
        out.append({"file": str(full), "exists": full.is_file()})
    pack, folder = deps.brand_pack(film.brand)
    taste = (pack or {}).get("taste")
    items = taste if isinstance(taste, list) else [taste] if taste else []
    for t in items:
        if isinstance(t, str) and folder and (Path(folder) / t).is_file():
            out.append({"file": str(Path(folder) / t), "exists": True})
        elif isinstance(t, str):
            out.append({"note": t})
    return out


def brief(film_dir: Path, *, version: int | None = None) -> Path:
    """Write the grading brief for version N's packet (default: the latest packet). Returns the .md path."""
    film = Film(Path(film_dir))
    n = version or latest_packet_version(film)
    if n is None:
        raise MissingPrerequisite("There's no review packet yet, so there's nothing to grade.", hint="slate review")
    packet_path, pk = _packet(film, n)
    d = film.review_dir(n)
    files = pk.get("files") or {}
    sheets = [str(d / s["file"]) for s in files.get("contact_sheets") or []]
    phone = str(d / files["phone_cut"]["file"]) if files.get("phone_cut") else None
    master = film.primary_master()
    sound = str(master["path"]) if master and master.get("audio") else (str(film.mix()) if film.mix() else None)
    data = {
        "generated": "do not edit by hand: slate review grade-brief",
        "film": film.id, "title": film.title, "brand": film.brand, "mode": film.mode, "version": n,
        "made_at": gates.now_iso(),
        "reviewer": "someone who didn't make this film: if you can run sub-agents, give this brief to a fresh one; "
                    "otherwise ask a person",
        "packet": str(packet_path), "packet_sha256": jsonio.sha256(packet_path),
        "look_at": {"contact_sheets": sheets, "phone_cut": phone,
                    "sheet_index": str(d / files["sheet_index"]) if files.get("sheet_index") else None},
        "listen_to": {"film_sound": sound, "note": "listen to the whole film, start to finish, with the picture"},
        "brand_references": _references(film),
        "taste_notes": _taste(film),
        "context": {k: str(film.path(v)) for k, v in (("goal", "brief/GOAL.md"), ("script", "script/SCRIPT.md"))
                    if film.path(v).is_file()},
        "criteria": CRITERIA,
        "answer": {"pass": f"slate review grade --verdict pass --notes \"what holds up\" --by NAME --version {n}",
                   "revise": f"slate review grade --verdict revise --notes \"each problem, with its timecode\" "
                             f"--by NAME --version {n}",
                   "rule": "pass only when every criterion holds; otherwise revise, with specific notes tied to a "
                           "timecode (the bar under the phone cut) or a sheet tile's time"},
    }
    jsonio.write_atomic(d / BRIEF_JSON, data)
    md = [f"# Grade {film.title} v{n}", "",
          f"You're the reviewer: {data['reviewer']}. You grade; you don't fix. Nothing is held for the owner until "
          "you pass it.", "", "## Look at", ""]
    md += [f"- Contact sheet: `{s}`" for s in sheets]
    md += [f"- Phone cut (timecode and shot id under the picture): `{phone}`" if phone else "- Phone cut: none", ""]
    md += ["## Listen to", "", f"- The film's sound: `{sound}` ({data['listen_to']['note']})" if sound
           else "- The film has no sound yet: that alone is a revise.", ""]
    md += ["## Against", ""]
    for r in data["brand_references"]:
        state = (f"approved by {r['approved_by']}" if r.get("approved") else "NOT approved")
        md.append(f"- Brand {r['reference'].replace('_', ' and ')} reference: `{r.get('file')}` ({state})")
    for t in data["taste_notes"]:
        md.append(f"- The owner's taste notes: `{t['file']}`" if t.get("file") else f"- The owner's taste: {t['note']}")
    for k, v in data["context"].items():
        md.append(f"- The {k}: `{v}`")
    md += ["", "## Criteria (each must hold for a pass)", ""]
    md += [f"{i}. **{c['id']}**: {c['ask']}" for i, c in enumerate(CRITERIA, 1)]
    md += ["", "## Answer with one command", "", f"    {data['answer']['pass']}", f"    {data['answer']['revise']}",
           "", data["answer"]["rule"] + "."]
    out = d / BRIEF_MD
    jsonio.write_text_atomic(out, "\n".join(md) + "\n")
    return out


def record(film_dir: Path, *, verdict: str, notes: str, by: str, version: int | None = None) -> dict:
    """Record the reviewer's verdict on version N's packet. Returns the grade record."""
    film = Film(Path(film_dir))
    verdict = (verdict or "").strip().lower()
    if verdict not in VERDICTS:
        raise UsageError(f"The verdict is pass or revise, not {verdict!r}.")
    notes = (notes or "").strip("\n")
    if verdict == "revise" and not notes.strip():
        raise UsageError("A revise verdict needs the notes that say what to fix (--notes).")
    by = (by or "").strip()
    if not by:
        raise UsageError("Say who graded it (--by).")
    n = version or latest_packet_version(film)
    if n is None:
        raise MissingPrerequisite("There's no review packet yet, so there's nothing to grade.", hint="slate review")
    packet_path, _pk = _packet(film, n)
    d = film.review_dir(n)
    brief_path = d / BRIEF_JSON
    if not brief_path.is_file():
        raise Refused("Grade from the brief: it names the references and criteria the verdict answers to.",
                      hint=f"slate review grade-brief --version {n}")
    b = jsonio.read(brief_path)
    if b.get("packet_sha256") != jsonio.sha256(packet_path):
        raise Refused(f"v{n}'s packet changed after its grading brief was written.",
                      hint=f"slate review grade-brief --version {n}, then grade again")
    rec = {"generated": "do not edit by hand: slate review grade", "film": film.id, "version": n,
           "verdict": verdict, "notes": notes, "by": by, "recorded_by": procs.agent_context() or "terminal",
           "at": gates.now_iso(), "packet_sha256": jsonio.sha256(packet_path),
           "brief_sha256": jsonio.sha256(brief_path), "criteria": [c["id"] for c in CRITERIA]}
    jsonio.write_atomic(d / GRADE, rec)
    with (d / HISTORY).open("a", encoding="utf-8") as fh:
        fh.write(jsonio.dumps(rec, indent=None))
    if verdict == "revise":
        from slate.finish import notes as notes_mod  # noqa: PLC0415

        notes_mod.add(film.dir, notes, by=by, source_kind="reviewer", version=n)
    return rec


def current(film: Film, n: int) -> dict | None:
    rec = jsonio.read(film.review_dir(n) / GRADE, default=None)
    return rec if isinstance(rec, dict) else None


def require_pass(film: Film, n: int) -> dict:
    """The passing grade of v{n}'s packet as it is now, or Refused: holding a film needs one."""
    rec = current(film, n)
    packet_path = film.review_dir(n) / "packet.json"
    if rec is None:
        raise Refused(f"Holding {film.id} v{n} for its owner needs a reviewer's grade first.",
                      hint=f"slate review grade-brief --version {n}, then a separate reviewer runs "
                           "`slate review grade --verdict pass|revise --notes \"…\" --by NAME`")
    if not packet_path.is_file() or rec.get("packet_sha256") != jsonio.sha256(packet_path):
        raise Refused(f"v{n}'s grade was for an earlier packet; this packet hasn't been graded.",
                      hint=f"slate review grade-brief --version {n}, then grade it again")
    if rec.get("verdict") != "pass":
        raise Refused(f"The reviewer's verdict on v{n} is {rec.get('verdict')}: fix the notes first.",
                      hint="slate notes plan", details={"notes": rec.get("notes")})
    return rec
