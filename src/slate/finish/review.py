"""Review packets (docs/PLAN.md §7, "Review and correct"; review_cuts.sh generalized).

`slate review` builds a packet for the film's current version in review/vN/:

- `<id>_vN_phone.mp4`   a phone cut with the timecode and shot id burned into a bar under the picture (film's fps);
- `sheets/`             contact sheets, a frame every 0.5 s, each tile labelled with its time and shot;
- `packet.json`         the index: source, files with their sha256, what changed since the last version (content,
                        film.lock.json, approvals, gates, directions), the open questions, and the snapshot the next
                        packet compares against;
- `review.md`           the same for a person.

The packet is made from the master when it's current, else straight from the act renders and the mix, so it never
waits for masters. When the film's content has moved on since the last packet or delivery, the packet starts the
next version (versions are numbered and a delivered version is never overwritten).

Where it goes (`--to`, or [finish.review] to):

- **downloads** (the default for the owner's own productions): a folder in ~/Downloads (configurable; SLATE_DOWNLOADS
  wins) with the phone cut, the sheets and review.md. Nothing there is ever replaced.
- **imessage** (the default for Oneiro's autonomous films): the phone cut by iMessage plus a push, through
  slate.notify. For an autonomous film this is the hold: it needs the reviewer's passing grade on this packet and a
  final delivery of this version, parks the film with the question for the owner, and says which exact file to
  approve. Nothing posts without their yes.

Nothing is sent from tests or replay runs, and `--dry-run` prints what would be sent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from slate import gates, jsonio, log, notify
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.finish import changes, cuts, deps, grade, media, settings, sheet, versions
from slate.finish.film import Film

PUBLISHING = "not approved for publishing"
DESTINATIONS = ("downloads", "imessage")


def _previous(film: Film, n: int) -> tuple[int | None, dict | None]:
    for k in range(n - 1, 0, -1):
        rec = versions.packet_record(film, k)
        if rec:
            return k, rec
    return None, None


def _file_entry(folder: Path, path: Path) -> dict:
    return {"file": str(path.relative_to(folder)), "sha256": jsonio.sha256(path), "bytes": path.stat().st_size}


def default_destination(film: Film) -> str:
    configured = str(settings.load()["review"].get("to") or "").strip().lower()
    if configured in DESTINATIONS:
        return configured
    return "imessage" if film.mode == "autonomous" else "downloads"


def packet(film_dir: Path, *, rebuild: bool = False) -> Path:
    """Build (or keep) the current version's packet. Returns review/vN/packet.json."""
    return build(film_dir, rebuild=rebuild)["packet"]


def build(film_dir: Path, *, rebuild: bool = False) -> dict:
    """Build the packet for the film's current content; returns {packet, version, built, started_version, record}."""
    film = Film(Path(film_dir))
    media.require()
    fp = film.fingerprint()
    ver = versions.resolve(film, fingerprint=fp)
    n = ver["version"]
    d = film.review_dir(n)
    existing = versions.packet_record(film, n)
    if existing and existing.get("content_fingerprint") == fp and not rebuild:
        return {"packet": d / "packet.json", "version": n, "built": False, "started_version": False,
                "record": existing}
    prev_n, prev = _previous(film, n)
    src = cuts.source_for(film)
    d.mkdir(parents=True, exist_ok=True)
    log.info(f"review v{n}: the phone cut ({src.kind})")
    phone = cuts.phone_cut(film, d / f"{film.id}_v{n}_phone.mp4", source=src, burn=True, right=f"v{n}")
    log.info(f"review v{n}: contact sheets every {settings.load()['review'].get('sheet_step_s', 0.5)} s")
    sheets_dir = d / "sheets"
    if sheets_dir.is_dir():
        for old in sheets_dir.iterdir():
            if old.is_file():
                old.unlink()
    sh = sheet.build(film, sheets_dir, source=src, prefix=f"{film.id}_v{n}")
    snap = changes.snapshot(film)
    ch = changes.diff((prev or {}).get("snapshot"), snap, film=film, since=prev_n)
    questions = [{"id": q.get("id"), "text": q.get("text"), "parks": bool(q.get("parks")), "source": q.get("source"),
                  "asked_at": q.get("asked_at")} for q in deps.open_questions(film.dir)]
    record = {
        "generated": "do not edit by hand: slate review",
        "schema": 1,
        "film": {"id": film.id, "title": film.title, "brand": film.brand, "mode": film.mode,
                 "fps": film.rate, "width": film.size[0], "height": film.size[1], "frames": film.total_frames,
                 "seconds": round(film.seconds, 3)},
        "version": n,
        "started_version": bool(ver.get("started")),
        "why_new_version": ver.get("why") or [],
        "made_at": gates.now_iso(),
        "source": src.describe(film),
        "content_fingerprint": fp,
        "files": {
            "phone_cut": {**_file_entry(d, Path(phone["file"])), "frames": phone["frames"],
                          "size": [phone["width"], phone["height"]], "burnin": phone.get("burnin"),
                          "encoder": phone.get("encoder"), "sound": phone.get("sound")},
            "contact_sheets": [{**_file_entry(d, p), "tiles": sum(1 for t in sh["tiles"] if t["sheet"] == i)}
                               for i, p in enumerate(sh["paths"])],
            "sheet_index": str(Path(sh["index_file"]).relative_to(d)),
            "review_md": "review.md",
        },
        "sheet": {"step_s": sh["step_s"], "tile": sh["tile"], "grid": sh["grid"], "tiles": len(sh["tiles"])},
        "changes": ch,
        "questions": questions,
        "snapshot": snap,
        "publishing": PUBLISHING,
    }
    problems = jsonio.validate(record, "review")
    if problems:
        raise UsageError("The review packet doesn't match its schema:\n  " + "\n  ".join(problems[:10]))
    (d / "review.md").write_text(markdown(film, record), encoding="utf-8")
    jsonio.write_atomic(d / "packet.json", record)
    deps.lock_record(film.dir, f"review/v{n}/packet.json", producer="slate review",
                     inputs=sorted(snap["content"].keys()))
    return {"packet": d / "packet.json", "version": n, "built": True, "started_version": bool(ver.get("started")),
            "record": record}


def markdown(film: Film, rec: dict) -> str:
    n = rec["version"]
    files = rec["files"]
    lines = [f"# {film.title}, v{n}: review", "",
             f"Made {rec['made_at'][:16].replace('T', ' ')} from the {rec['source']['kind']}. "
             f"{rec['film']['seconds']:.1f} s, {rec['film']['width']}×{rec['film']['height']}, "
             f"{rec['film']['fps']} fps. "
             f"{PUBLISHING.capitalize()}.", "",
             f"- Phone cut: `{files['phone_cut']['file']}` (timecode and shot id under the picture)"]
    for s in files["contact_sheets"]:
        lines.append(f"- Contact sheet: `{s['file']}` ({s['tiles']} frames, one every {rec['sheet']['step_s']} s)")
    lines += ["", f"## What changed{' since v' + str(rec['changes']['since']) if rec['changes'].get('since') else ''}",
              ""]
    lines += [f"- {x}" for x in rec["changes"]["summary"]] or ["- Nothing."]
    lines += ["", "## Open questions", ""]
    lines += [f"- {q['id']}: {q['text']}  (answer: `slate film answer {q['id']} --by NAME --text \"…\"`)"
              for q in rec["questions"]] or ["- None."]
    lines += ["", "## Notes", "",
              "Say it in your own words, with the timecode from the bar under the picture:", "",
              "    slate notes add \"the card lands late\" --at 00:00:12:05", "",
              "Then `slate notes plan` shows the targeted fixes (perfect, don't replace).", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# Where the packet goes


def _deliverable_files(film: Film, n: int) -> list[str]:
    rec = versions.manifest_record(film, n) or {}
    out = []
    for f in rec.get("files") or []:
        if str(f.get("kind") or "").startswith("master"):
            out.append(f"deliver/v{n}/{f['file']}")
    return out


def message(film: Film, rec: dict, *, hold: bool) -> tuple[str, str, str]:
    """(iMessage text, push title, push body) for a packet."""
    n = rec["version"]
    secs = rec["film"]["seconds"]
    first = f"{film.title} v{n} is ready for you" + (" (held: nothing posts without your yes)" if hold else "") + "."
    length = f"{secs:.0f} s" if secs >= 10 else f"{secs:.1f} s"
    parts = [first, f"{length}, {rec['film']['width']}×{rec['film']['height']}. The phone cut is attached, with the "
             "timecode and shot id under the picture."]
    summary = [x for x in rec["changes"]["summary"] if not x.startswith("The first packet")][:2]
    if summary:
        parts.append("What changed: " + " ".join(summary))
    qs = rec.get("questions") or []
    if qs:
        parts.append("Open questions: " + " ".join(f"{q['id']}: {q['text']}" for q in qs[:3]))
    if hold:
        files = _deliverable_files(film, n)
        post = next((f for f in files if f.endswith(".mp4")), files[0] if files else None)
        if post:
            parts.append(f"To approve the exact file to post: slate approve final_cut {post} --by NAME "
                         f"(film {film.id}).")
    parts.append("Notes: reply with your words and a timecode.")
    title = f"{film.title} v{n}" + (" held for you" if hold else " ready for review")
    body = "Watch the phone cut in Messages" + (", then approve the exact file." if hold else ".")
    return " ".join(parts), title, body


def _packet_sha(film: Film, n: int) -> str | None:
    p = film.review_dir(n) / "packet.json"
    return jsonio.sha256(p) if p.is_file() else None


def _log_send(film: Film, n: int, entry: dict) -> None:
    path = film.review_dir(n) / "sent.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(jsonio.dumps({**entry, "packet_sha256": _packet_sha(film, n), "at": gates.now_iso()}, indent=None))


def sent_log(film: Film, n: int) -> list[dict]:
    path = film.review_dir(n) / "sent.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def already_sent(film: Film, n: int, destination: str) -> bool:
    """True when this exact packet already went to `destination` (so a re-run doesn't send it twice)."""
    sha = _packet_sha(film, n)
    return any(e.get("destination") == destination and e.get("packet_sha256") == sha
               and (e.get("sent") or e.get("held")) for e in sent_log(film, n))


def to_downloads(film: Film, n: int, rec: dict, *, dry_run: bool = False) -> dict:
    """Copy the packet (phone cut, sheets, review.md) into a new folder in Downloads."""
    base = settings.downloads_dir()
    dest = media.free_name(base, f"{film.id} v{n} review")
    d = film.review_dir(n)
    files = [d / rec["files"]["phone_cut"]["file"], d / "review.md"] + [d / s["file"] for s in
                                                                      rec["files"]["contact_sheets"]]
    if dry_run:
        return {"destination": "downloads", "folder": str(dest), "files": [str(f) for f in files], "sent": False,
                "dry_run": True, "why": "not copied: dry run"}
    base.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=False)
    for f in files:
        shutil.copy2(f, dest / f.name)
    out = {"destination": "downloads", "folder": str(dest), "files": [f.name for f in files], "sent": True,
           "dry_run": False, "why": "copied"}
    _log_send(film, n, out)
    return out


def hold(film: Film, n: int, rec: dict, *, dry_run: bool = False) -> dict:
    """An autonomous film, held for its owner: needs a passing grade on this packet and a final delivery."""
    g = grade.require_pass(film, n)
    if not versions.delivered(film, n):
        raise Refused(f"Deliver v{n} before holding it: the owner approves the exact delivered file.",
                      hint="slate deliver")
    text, title, body = message(film, rec, hold=True)
    phone = film.review_dir(n) / rec["files"]["phone_cut"]["file"]
    question = None
    if not dry_run:
        question = deps.park(film.dir, reason=text, evidence=[film.rel(phone), f"deliver/v{n}/manifest.json",
                                                              f"review/v{n}/grade.json"],
                             source=f"slate review v{n}")
    sends = notify.both(text, title=title, attachment=phone, dry_run=dry_run)
    out = {"destination": "imessage", "held": not dry_run, "question": (question or {}).get("id"),
           "grade": {"verdict": g["verdict"], "by": g["by"], "at": g["at"]}, "sends": sends,
           "sent": any(s.get("sent") for s in sends), "dry_run": dry_run, "text": text}
    if not dry_run:
        _log_send(film, n, out)
    return out


def to_imessage(film: Film, n: int, rec: dict, *, dry_run: bool = False) -> dict:
    """The phone cut by iMessage plus a push. For an autonomous film this is the hold (see `hold`)."""
    if film.mode == "autonomous":
        return hold(film, n, rec, dry_run=dry_run)
    text, title, _body = message(film, rec, hold=False)
    phone = film.review_dir(n) / rec["files"]["phone_cut"]["file"]
    sends = notify.both(text, title=title, attachment=phone, dry_run=dry_run)
    out = {"destination": "imessage", "held": False, "sends": sends, "sent": any(s.get("sent") for s in sends),
           "dry_run": dry_run, "text": text}
    if not dry_run:
        _log_send(film, n, out)
    return out


def send(film_dir: Path, *, version: int | None = None, to: str | None = None, dry_run: bool = False,
         explicit: bool = True) -> dict:
    """Send packet vN (default: the latest) to its destination."""
    film = Film(Path(film_dir))
    n = version or grade.latest_packet_version(film)
    if n is None:
        raise MissingPrerequisite("There's no review packet yet.", hint="slate review")
    rec = versions.packet_record(film, n)
    if rec is None:
        raise MissingPrerequisite(f"There's no review packet for v{n}.", hint="slate review")
    dest = (to or default_destination(film)).strip().lower()
    if dest not in DESTINATIONS:
        raise UsageError(f"--to is downloads or imessage, not {dest!r}.")
    if dest == "downloads":
        return to_downloads(film, n, rec, dry_run=dry_run)
    if film.mode == "autonomous" and not explicit:
        try:
            grade.require_pass(film, n)
        except Refused as exc:  # the default run reports what it's waiting for; an explicit --to refuses
            return {"destination": "imessage", "sent": False, "held": False, "awaiting_grade": True,
                    "why": "an autonomous film is held for its owner only after a separate reviewer passes it ("
                           + exc.message.rstrip(".") + ")",
                    "next": exc.hint or f"slate review grade-brief --version {n}"}
    return to_imessage(film, n, rec, dry_run=dry_run)


def plan(film_dir: Path, *, to: str | None = None) -> dict:
    """What `slate review` would do now (--dry-run): the version, whether it's built, and what would be sent."""
    film = Film(Path(film_dir))
    ver = versions.resolve(film, start=False)
    n = ver["version"]
    rec = versions.packet_record(film, n)
    out = {"film": film.id, "version": n, "dry_run": True, "would_start_version": bool(ver.get("would_start")),
           "why": ver.get("why") or [], "packet_exists": bool(rec and rec.get("content_fingerprint") ==
                                                              ver["fingerprint"]),
           "destination": (to or default_destination(film))}
    try:
        out["source"] = cuts.source_for(film).describe(film)
    except (MissingPrerequisite, UsageError) as exc:
        out["would"] = "refuse"
        out["problem"] = exc.message
        return out
    out["would"] = "keep the packet" if out["packet_exists"] else "build the packet"
    if rec and out["packet_exists"]:
        try:
            out["send"] = send(film.dir, version=n, to=to, dry_run=True, explicit=to is not None)
        except (MissingPrerequisite, Refused, UsageError) as exc:
            out["send"] = {"sent": False, "why": exc.message, "hint": exc.hint}
    return out
