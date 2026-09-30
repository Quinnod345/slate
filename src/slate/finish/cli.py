"""Finishing commands: `slate master`, `slate deliver`, `slate review` (+ grade-brief, grade), `slate notes`
(add, list, plan) and `slate approved`.

Every command takes the global flags (--json, --film, --live, --dry-run, --quiet) and returns the shared exit codes:
0 ok · 1 gate failed or refused · 2 usage · 3 missing prerequisite · 4 spend refused · 5 busy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import cli, log
from slate.finish import approved as approved_mod
from slate.finish import deliver as deliver_mod
from slate.finish import deps
from slate.finish import grade as grade_mod
from slate.finish import master as master_mod
from slate.finish import notes as notes_mod
from slate.finish import review as review_mod
from slate.finish.film import Film


def _dry(args: argparse.Namespace) -> bool:
    return bool(cli.flag(args, "dry_run", False))


def _detach(args: argparse.Namespace, kind: str, resources: tuple[str, ...], film_dir) -> int:
    job = deps.detach(kind, resources, film_dir=film_dir)
    log.result({"job": job, "kind": kind, "detached": True},
               f"Queued as job {job}. Follow it with: slate job status {job}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate master


def cmd_master(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    method = "ae" if args.ae else "ffmpeg" if args.ffmpeg else "auto"
    if _dry(args):
        p = master_mod.plan(film_dir, method=method, go=args.go, rebuild=args.rebuild, silent=args.silent)
        human = [f"Would {p.get('would')}: {p.get('film')} ({p.get('mode')}), method {p.get('method', '—')}"
                 + (f" ({p['why']})" if p.get("why") and isinstance(p.get("why"), str) else "")]
        if p.get("step"):
            human.append(f"  step: {p['step']}")
        for prob in p.get("problems") or []:
            human.append(f"  problem: {prob}")
        if p.get("hint"):
            human.append(f"  next: {p['hint']}")
        log.result(p, "\n".join(human))
        return 0
    if args.detach:
        film = Film(film_dir)
        chosen, _why = master_mod.choose(film, ae=args.ae, ffmpeg=args.ffmpeg)
        if chosen == "ae":
            deps.ae_person_start(args.go, "The After Effects finish", film.dir, "ae_finish")
        return _detach(args, "master", ("ae",) if chosen == "ae" else ("render",), film.dir)
    out = master_mod.run(film_dir, method=method, go=args.go, rebuild=args.rebuild, silent=args.silent,
                         force=args.force, approval=args.approval)
    log.result(out, master_mod.describe(out))
    if out.get("status") == "project_built":
        return 0
    return 0 if out.get("passed") else 1


# ---------------------------------------------------------------------------------------------------------------
# slate deliver


def cmd_deliver(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if args.list:
        rows = deliver_mod.listing(film_dir)
        lines = []
        for r in rows:
            lines.append(f"{r['folder']}  v{r['version']}  {'final' if r['final'] else 'draft'}  "
                         f"{str(r['delivered_at'])[:16]}  {r['files']} files" +
                         (f", {r['missing']} missing" if r["missing"] else ""))
            for m in r["masters"]:
                lines.append(f"    {m['file']}: {m['publishing']}")
        log.result({"deliveries": rows}, "\n".join(lines) or "Nothing delivered yet.")
        return 0
    final = not args.draft
    if _dry(args):
        p = deliver_mod.plan(film_dir, final=final)
        human = [f"Would {p.get('would')}."]
        for w in p.get("why_not_final") or ([] if final else []):
            human.append(f"  not final: {w}")
        if isinstance(p.get("why"), list):
            human += [f"  {w}" for w in p["why"]]
        if p.get("hint"):
            human.append(f"  next: {p['hint']}")
        log.result(p, "\n".join(human))
        return 0
    if args.detach:
        return _detach(args, "deliver", ("render",), film_dir)
    out = deliver_mod.deliver(film_dir, final=final)
    log.result(out, deliver_mod.describe(out))
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate review


def _review_human(res: dict, sent: dict | None, film: Film) -> str:
    rec = res["record"]
    n = res["version"]
    d = film.review_dir(n)
    lines = [(f"Built the review packet for {film.id} v{n}" if res["built"] else
              f"v{n}'s review packet stands (nothing changed since it was made)") +
             (" (a new version: the film changed since the last packet or delivery)" if res.get("started_version")
              else "") + f": {film.rel(d)}/",
             f"  phone cut: {rec['files']['phone_cut']['file']} (timecode and shot id under the picture)",
             f"  contact sheets: {len(rec['files']['contact_sheets'])}, a frame every {rec['sheet']['step_s']} s"]
    lines += [f"  {x}" for x in rec["changes"]["summary"]]
    if rec["questions"]:
        lines.append(f"  open questions: {len(rec['questions'])}")
    if sent:
        if sent.get("awaiting_grade"):
            lines.append(f"Not held yet: {sent['why']}. next: {sent['next']}")
        elif sent.get("destination") == "downloads":
            verb = "Copied" if sent.get("sent") else "Would copy"
            lines.append(f"{verb} to {sent['folder']}")
        else:
            for s in sent.get("sends") or []:
                what = s.get("channel") or "send"
                lines.append(f"{what}: {'sent' if s.get('sent') else s.get('why')}")
            if sent.get("held"):
                lines.append(f"Held for the owner (question {sent.get('question')}): nothing posts without their yes.")
    return "\n".join(lines)


def _send_lines(sent: dict | None) -> list[str]:
    """One line per channel of a (dry-run) send: what would go where, or what's missing for it."""
    lines = []
    for s in (sent or {}).get("sends") or []:
        if s.get("missing"):
            lines.append(f"  not configured: {s.get('why')}" + (f" ({s['hint']})" if s.get("hint") else ""))
            continue
        where = f" to {s['to']}" if s.get("to") else ""
        with_file = f", with {Path(s['attachment']).name}" if s.get("attachment") else ""
        lines.append(f"  {s.get('channel')}{where} via {s.get('route')}{with_file}: "
                     + ("sent" if s.get("sent") else str(s.get("why") or "not sent")))
    return lines


def cmd_review(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry(args):
        if args.version:
            p = review_mod.send(film_dir, version=args.version, to=args.to, dry_run=True, explicit=True)
            log.result(p, "\n".join([f"Would send v{args.version} to {p.get('destination')}: "
                                     + (p.get("text") or p.get("folder") or p.get("why") or ""), *_send_lines(p)]))
            return 0
        p = review_mod.plan(film_dir, to=args.to)
        human = [f"Would {p.get('would')} for v{p['version']}" + (" (a new version)" if p.get("would_start_version")
                                                                   else "") + f", destination {p['destination']}."]
        if p.get("problem"):
            human.append(f"  problem: {p['problem']}")
        snd = p.get("send")
        if snd:
            human.append(f"  would send: {snd.get('text') or snd.get('folder') or snd.get('why')}")
            human += _send_lines(snd)
        log.result(p, "\n".join(human))
        return 0
    if args.detach:
        return _detach(args, "review", ("render",), film_dir)
    film = Film(film_dir)
    if args.version:
        rec = review_mod.versions.packet_record(film, args.version)
        if rec is None:
            from slate.errors import MissingPrerequisite  # noqa: PLC0415

            raise MissingPrerequisite(f"There's no review packet for v{args.version}.", hint="slate review")
        res = {"packet": film.review_dir(args.version) / "packet.json", "version": args.version, "built": False,
               "started_version": False, "record": rec}
    else:
        res = review_mod.build(film_dir, rebuild=args.rebuild)
    n = res["version"]
    sent = None
    film = Film(film_dir)
    dest = (args.to or review_mod.default_destination(film))
    wants_send = bool(args.to) or (not args.version and not args.no_send and
                                   (res["built"] or not review_mod.already_sent(film, n, dest)))
    if wants_send and not args.no_send:
        sent = review_mod.send(film_dir, version=n, to=args.to, explicit=args.to is not None)
    rec = res["record"]
    out = {"film": film.id, "version": n, "packet": film.rel(res["packet"]), "built": res["built"],
           "started_version": res.get("started_version"), "files": rec["files"], "changes": rec["changes"]["summary"],
           "questions": rec["questions"], "sent": sent}
    if sent and sent.get("awaiting_grade"):
        out["next"] = sent["next"]
    log.result(out, _review_human(res, sent, film))
    return 0


def cmd_grade_brief(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry(args):
        film = Film(film_dir)
        n = args.version or grade_mod.latest_packet_version(film)
        log.result({"version": n, "dry_run": True, "would_write": [f"review/v{n}/grade_brief.md",
                                                                   f"review/v{n}/grade_brief.json"]},
                   f"Would write the grading brief for v{n}.")
        return 0
    path = grade_mod.brief(film_dir, version=args.version)
    film = Film(film_dir)
    log.result({"brief": film.rel(path), "json": film.rel(path.with_suffix(".json")), "criteria":
                [c["id"] for c in grade_mod.CRITERIA]},
               f"Wrote the grading brief: {film.rel(path)}\nHand it to a reviewer who didn't make the film (a fresh "
               "sub-agent if you can run one; otherwise a person). They answer with `slate review grade`.")
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry(args):
        log.result({"dry_run": True, "verdict": args.verdict, "notes": args.notes, "by": args.by},
                   f"Would record the verdict {args.verdict} by {args.by}.")
        return 0
    rec = grade_mod.record(film_dir, verdict=args.verdict, notes=args.notes, by=args.by, version=args.version)
    nxt = ("slate review   (holds the film for its owner and sends the packet)" if rec["verdict"] == "pass"
           else "slate notes plan   (the reviewer's notes are in the notes log)")
    log.result({**rec, "next": nxt}, f"Recorded {rec['verdict']} on v{rec['version']} by {rec['by']}.\nnext: {nxt}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate notes


def cmd_notes_add(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    text = args.text
    if text == "-":
        import sys  # noqa: PLC0415

        text = sys.stdin.read()
    if _dry(args):
        film = Film(film_dir)
        where = None
        if args.at:
            f_in, f_to = notes_mod.parse_at(film, args.at)
            where = notes_mod.locate(film, f_in, f_to)
        stage = args.stage or notes_mod.infer_stage(text, film.language)[0]
        log.result({"dry_run": True, "text": text, "where": where, "stage": stage},
                   f"Would log “{text}”" + (f" at {where['timecode']} ({where['shot']})" if where else "")
                   + f", stage {stage or 'unsorted'}.")
        return 0
    rec = notes_mod.add(film_dir, text, at=args.at, by=args.by, stage=args.stage)
    log.result(rec, "Logged " + notes_mod.describe(rec))
    return 0


def cmd_notes_list(args: argparse.Namespace) -> int:
    rows = notes_mod.list_notes(cli.film(args), version=args.version, everything=args.all)
    log.result({"notes": rows}, "\n".join(notes_mod.describe(r) for r in rows) or "No notes yet.")
    return 0


def cmd_notes_plan(args: argparse.Namespace) -> int:
    dry = _dry(args)
    p = notes_mod.plan(cli.film(args), version=args.version, ask=not dry, write=not dry)
    log.result(p, notes_mod.plan_text(p))
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate approved


def cmd_approved(args: argparse.Namespace) -> int:
    out = approved_mod.check(args.file, film=cli.flag(args, "film"))
    a = out["approval"] or {}
    log.result(out, f"Approved for publishing: {out['file']} is {out['delivered_as']} "
                    f"({out['film']} v{out['version']}),"
                    f" approved by {a.get('approved_by')} on {str(a.get('at'))[:16]}.")
    return 0


# ---------------------------------------------------------------------------------------------------------------


MASTER_HELP = """Master the film.

  slate master                  the film's mode decides: After Effects for films a person supervises (needs --go),
                                ffmpeg for autonomous and draft films (held for the owner)
  slate master --ae --go        the After Effects finish, which you start: the first run builds and audits the
                                editable AE project; save and quit AE, then run it again to render the masters
  slate master --ffmpeg         an ffmpeg master (for a supervised film, an interim one: it's finished in AE)

Gates: every frame of the masters against the act renders at 40 dB or better, exact frame counts, the finish
matching the mode, and for After Effects the project audit. Autonomous films never use After Effects."""

DELIVER_HELP = """Deliver the current version to deliver/vN/: masters, the mix and stems, captions, review and phone
cuts, thumbnails, the AE project, and the manifest written last with every file's sha256.

Every film gate must pass first. Drafts are never delivered as final, and any replayed stand-in blocks a final
delivery (--draft makes a non-final set in deliver/drafts/). Every file is "not approved for publishing" until the
owner approves that exact file; a delivered version is never overwritten."""

REVIEW_HELP = """Build the review packet for this version and send it where the owner watches.

  slate review                        the packet (phone cut with timecode and shot id, a contact sheet every 0.5 s,
                                      what changed, open questions), then Downloads for your own films, or for an
                                      autonomous film, iMessage and a push once a reviewer has passed it
  slate review --to imessage          send it by iMessage with a push (for an autonomous film: the hold)
  slate review --version 2 --to downloads   send an earlier packet
  slate review grade-brief            the brief a separate reviewer grades from
  slate review grade --verdict pass --notes "…" --by NAME

Nothing is sent from tests or replay runs; --dry-run prints what would be sent."""

NOTES_HELP = """The owner's notes, verbatim and numbered, mapped to shot, beat and stage; and the targeted fix plan.

  slate notes add "the card lands a beat late" --at 00:00:12:05
  slate notes list
  slate notes plan"""


def register(sub) -> None:
    p = cli.add_command(sub, "master", cmd_master, "master the film (After Effects for supervised films, ffmpeg for "
                                                   "autonomous and draft)", description=MASTER_HELP)
    how = p.add_mutually_exclusive_group()
    how.add_argument("--ae", action="store_true", help="the After Effects finish (a person starts it with --go)")
    how.add_argument("--ffmpeg", action="store_true", help="an ffmpeg master")
    p.add_argument("--go", action="store_true", help="you, a person, are starting After Effects now")
    p.add_argument("--rebuild", action="store_true", help="rebuild the AE project first")
    p.add_argument("--silent", action="store_true", help="a picture-only master (no mix)")
    p.add_argument("--force", action="store_true", help="remake a master that's approved (with --approval)")
    p.add_argument("--approval", metavar="ID", help="the person's spend approval id for --force")
    p.add_argument("--detach", action="store_true", help="run it as a job in the queue")

    p = cli.add_command(sub, "deliver", cmd_deliver, "deliver this version: the set, with the manifest written last",
                        description=DELIVER_HELP)
    p.add_argument("--draft", action="store_true", help="a non-final set in deliver/drafts/ (never final)")
    p.add_argument("--list", action="store_true", help="every delivered set and each master's approval")
    p.add_argument("--detach", action="store_true", help="run it as a job in the queue")

    p = cli.add_command(sub, "review", cmd_review, "the review packet for this version, sent where you watch",
                        description=REVIEW_HELP)
    p.add_argument("--version", type=int, metavar="N", help="an existing packet (default: build the current one)")
    p.add_argument("--to", choices=review_mod.DESTINATIONS, help="where it goes (default: downloads for your own "
                                                                 "films, imessage for autonomous films)")
    p.add_argument("--rebuild", action="store_true", help="make the packet again even if nothing changed")
    p.add_argument("--no-send", dest="no_send", action="store_true", help="build it, send nothing")
    p.add_argument("--detach", action="store_true", help="run it as a job in the queue")
    stages = p.add_subparsers(dest="review_stage", metavar="<stage>")
    g = cli.add_command(stages, "grade-brief", cmd_grade_brief, "the brief a separate reviewer grades the film from")
    g.add_argument("--version", type=int, metavar="N", help="the packet (default: the latest)")
    g = cli.add_command(stages, "grade", cmd_grade, "record the reviewer's verdict (holding the film needs a pass)")
    g.add_argument("--verdict", required=True, choices=grade_mod.VERDICTS)
    g.add_argument("--notes", required=True, help="what holds up, or what to fix, with timecodes")
    g.add_argument("--by", default="reviewer", help="who graded it (default: reviewer)")
    g.add_argument("--version", type=int, metavar="N", help="the packet (default: the latest)")

    grp = cli.add_group(sub, "notes", "the owner's notes, mapped to shot, beat and stage; the fix plan",
                        description=NOTES_HELP)
    a = cli.add_command(grp, "add", cmd_notes_add, "log a note verbatim as a numbered direction")
    a.add_argument("text", help="the words, exactly as given (- reads stdin)")
    a.add_argument("--at", metavar="TIMECODE", help="HH:MM:SS:FF from the phone cut, m:ss.s, seconds, f123, or a range")
    a.add_argument("--by", default="owner", help="whose words (default: owner)")
    a.add_argument("--stage", choices=notes_mod.STAGES, help="which stage fixes it (default: from the words)")
    a = cli.add_command(grp, "list", cmd_notes_list, "the notes on this version")
    a.add_argument("--version", type=int, metavar="N")
    a.add_argument("--all", action="store_true", help="every version's notes")
    a = cli.add_command(grp, "plan", cmd_notes_plan, "the targeted fix plan (perfect, don't replace)")
    a.add_argument("--version", type=int, metavar="N")

    a = cli.add_command(sub, "approved", cmd_approved, "exit 0 only when this exact file is approved for publishing",
                        description="Checks the file's sha256 against the owner's final-cut approvals and the film's "
                                    "final deliveries. Oneiro posts only a file that passes this.")
    a.add_argument("file", help="the file about to be posted")

