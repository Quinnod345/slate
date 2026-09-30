"""Film-state commands: new, status, make, approve, film, variant, revise.

Every command prints a person's answer by default and one JSON object with --json. Refusals carry the shared exit
codes (1 refused, 2 usage, 3 missing prerequisite, 4 spend refused, 5 busy) and the one next step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from slate import cli, jsonio, log, paths
from slate.errors import Refused, UsageError
from slate.state import (_util, approvals, derive, film, lock, make, modes, pins, production, revise, scaffold,
                         status)

EPILOG = "Exit codes: 0 ok, 1 refused, 2 usage, 3 missing prerequisite, 4 spend refused, 5 busy."


def _read_text(value: str | None, file: str | None, what: str) -> str | None:
    if file:
        p = Path(file).expanduser()
        if not p.is_file():
            raise UsageError(f"{what} file {p} doesn't exist.")
        return p.read_text(encoding="utf-8")
    if value == "-":
        return sys.stdin.read()
    return value


def _dry(args: argparse.Namespace) -> bool:
    return bool(cli.flag(args, "dry_run", False))


def _dry_stop(args: argparse.Namespace, would: str, **details) -> bool:
    """With --dry-run: say what the command would do, change nothing, and tell the caller to stop."""
    if not _dry(args):
        return False
    log.result({"dry_run": True, "would": would, **details}, f"Dry run: would {would}. Nothing changed.")
    return True


# ---------------------------------------------------------------------------------------------------------------
# slate new


def cmd_new(args: argparse.Namespace) -> int:
    request = _read_text(args.request, args.request_file, "Request")
    derivation = None
    if args.derivation:
        try:
            derivation = json.loads(args.derivation)
        except json.JSONDecodeError as exc:
            raise UsageError(f"--derivation isn't valid JSON: {exc.msg}.",
                             hint='For example: --derivation \'{"kind": "aspect", "aspect": "9:16"}\'') from exc
        if not isinstance(derivation, dict):
            raise UsageError("--derivation must be a JSON object.")
    if _dry(args):
        if args.parent:
            raise UsageError("--dry-run doesn't preview derived films yet; `slate variant` explains what it reuses.")
        pack = None
        from slate.state import deps  # noqa: PLC0415

        brand_dir = deps.brand_find(args.brand)
        pack = deps.brand_load(args.brand)
        modes.check_new(args.mode, args.brand, pack)
        fmt_name = args.format or (modes.default_format(args.mode) if args.mode == "autonomous" else None)
        if not fmt_name:
            raise UsageError("A film needs a format (--format).")
        data = film.build(args.id, brand=args.brand, format=fmt_name, mode=args.mode, title=args.title,
                          generation=args.generation, runtime_s=args.runtime, aspect=args.aspect, pack=pack,
                          brand_dir=brand_dir)
        jsonio.require_valid(data, film.SCHEMA, what="film.json")
        target = (Path(args.dir).expanduser() if args.dir else paths.films()) / args.id
        log.result({"dry_run": True, "dir": str(target), "film_json": data},
                   f"Would create {target}\n{jsonio.dumps(data)}")
        return 0
    target = film.create(args.id, brand=args.brand, format=args.format, mode=args.mode, title=args.title,
                         parent=args.parent, derivation=derivation, generation=args.generation,
                         films_dir=args.dir, request=request, request_source=args.source, runtime_s=args.runtime,
                         aspect=args.aspect, git=False if args.no_git else None)
    st = status.compute(target)
    fj = film.load(target)
    git = scaffold.git_state(target)
    human = (f"Created {fj['id']} at {target}\n"
             f"  {fj['mode']} · {fj['format']} · {fj['width']}×{fj['height']} ({fj['aspect']}) · "
             f"{fj['fps_num']}/{fj['fps_den']} fps · brand {fj['brand']} · {fj['language']}\n"
             f"  brief/GOAL.md holds the request verbatim and {st['asks']['total']} asks; git "
             + (f"started at {git.get('head', '')[:10]}" if git.get("repo") else "not started")
             + f"\nnext: {st['next'].get('command') or ''}\n      {st['next'].get('why')}")
    log.result({"film": fj["id"], "dir": str(target), "film_json": fj, "git": git, "next": st["next"]}, human)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate status


def cmd_status(args: argparse.Namespace) -> int:
    ref = cli.flag(args, "film")
    try:
        film_dir = paths.film_dir(ref)
    except UsageError:
        if ref:
            raise
        films = status.list_films()
        if not films:
            raise
        lines = [f"No film here. Films in {paths.films()}:"]
        for f in films:
            lines.append(f"  {f['film']:<28} {f.get('mode', ''):<11} {'PARKED ' if f.get('parked') else ''}"
                         f"next: {f.get('next') or f.get('why') or f.get('error')}")
        lines.append("Pick one: slate status --film <id>")
        log.result({"films": films, "films_dir": str(paths.films())}, "\n".join(lines))
        return 0
    st = status.compute(film_dir)
    if args.write and not _dry(args):
        production.render(film_dir)
    log.result(st, status.format_human(st))
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate make


def cmd_make(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if args.list:
        rows = []
        with _util.Hasher(film_dir) as hasher:
            for name in make.ORDER:
                st = make.node_state(film_dir, make.NODES[name], hasher=hasher)
                rows.append({**st, "command": "slate " + " ".join(make.NODES[name].cmd),
                             "available": status.command_available(make.NODES[name].cmd)})
        human = "\n".join(f"{r['node']:<11} {r['state']:<8} {r['command']:<22} "
                          f"{'' if r['available'] else '(not built yet) '}{'; '.join(r['reasons'][:1])}" for r in rows)
        log.result({"film": film.film_id(film_dir), "nodes": rows}, human)
        return 0
    if not args.target:
        raise UsageError("Name a target.", hint="Targets: " + ", ".join(make.ORDER) + " (or all). --list shows them.")
    res = make.run(film_dir, args.target, force=args.force, dry_run=_dry(args), spend=args.spend)
    log.result(res, make.format_human(res))
    return int(res["exit_code"])


# ---------------------------------------------------------------------------------------------------------------
# slate approve


def cmd_approve(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry(args):
        rel = _util.film_rel(film_dir, args.path)
        digest, basis = approvals.content_hash(film_dir, approvals.kind_of(args.what), rel)
        log.result({"dry_run": True, "what": args.what, "path": rel, "sha256": digest, "basis": basis},
                   f"Would record {args.what} for {rel} (sha256 {_util.short(digest)}) by {args.by}")
        return 0
    rec = approvals.record(film_dir, what=args.what, path=args.path, approved_by=args.by, note=args.note)
    extra = ""
    if approvals.KINDS.get(rec["kind"], {}).get("locks"):
        extra = "\n  locked by content hash: Slate won't regenerate it without --force and a spend approval"
    if rec["kind"] == "spend":
        extra = f"\n  pass --approval {rec['id']} to the command that spends"
    log.result(rec, f"Approved {rec['what']}: {rec['path']} by {rec['approved_by']} "
                    f"(sha256 {_util.short(rec['sha256'])}, id {rec['id']}){extra}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate film …


def cmd_film_show(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    fj = film.load(film_dir)
    lk = lock.load(film_dir)
    ap = approvals.load(film_dir)
    out = {"film": fj, "dir": str(film_dir), "pins": fj.get("pins") or {}, "approvals": ap["approvals"],
           "questions": ap["questions"], "revisions": [{k: r[k] for k in ("id", "artifact", "status")}
                                                       for r in ap["revisions"]],
           "lock": {"entries": len(lk["files"]), "locked": lock.locked_assets(film_dir),
                    "shot_ids_frozen": lock.frozen_shot_ids(film_dir), "timeline": lk["frozen"].get("timeline")}}
    human = [jsonio.dumps(fj).rstrip()]
    human.append(f"\n{len(ap['approvals'])} approvals · {len(ap['questions'])} questions · "
                 f"{len(lk['files'])} lock entries ({len(out['lock']['locked'])} locked)")
    for a in ap["approvals"][-10:]:
        human.append(f"  {a['id']}  {a['what']:<15} {a['path']}  by {a['approved_by']}  {a['at'][:16]}")
    log.result(out, "\n".join(human))
    return 0


def cmd_film_set(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    value = _util.parse_value(args.value)
    if _dry(args):
        log.result({"dry_run": True, "key": args.key, "value": value}, f"Would set {args.key} to {value!r}")
        return 0
    change = film.set_value(film_dir, args.key, value)
    production.render(film_dir)
    log.result(change, f"{change['key']}: {change['old']!r} → {change['new']!r}")
    return 0


def cmd_film_pin(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, f"pin {args.key} to {args.value!r} (unless it is already pinned)"):
        return 0
    value = _util.parse_value(args.value)
    before = pins.get(film_dir, args.key)
    if before is not None and before != value:
        raise Refused(f"{args.key} is already pinned to {before!r}, and the first pin wins.",
                      hint=f"Re-pin on purpose: slate film repin {args.key} VALUE --approved-by NAME --reason \"…\"")
    got = pins.pin(film_dir, args.key, value, source=args.source or "slate film pin")
    log.result({"key": args.key, "value": got, "first_use": before is None},
               f"{args.key} pinned to {got!r}" + ("" if before is None else " (already pinned)"))
    return 0


def cmd_film_repin(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, f"re-pin {args.key} to {args.value!r}"):
        return 0
    value = _util.parse_value(args.value)
    before = pins.get(film_dir, args.key)
    got = pins.repin(film_dir, args.key, value, approved_by=args.approved_by, reason=args.reason,
                     evidence=args.evidence)
    log.result({"key": args.key, "value": got, "previous": before, "history": pins.history(film_dir, args.key)},
               f"{args.key}: {before!r} → {got!r} (re-pinned by {args.approved_by}: {args.reason})")
    return 0


def cmd_film_ask(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, "record the message verbatim and add its asks to the checklist"):
        return 0
    text = _read_text(args.text, args.file, "Message")
    if not text:
        raise UsageError("Give the message's words (or --file PATH, or - for stdin).")
    if args.request:
        numbers = production.set_request(film_dir, text, source=args.source, split=not args.no_split)
        what = "request"
    else:
        numbers = production.add_ask(film_dir, text, source=args.source, split=not args.no_split)
        what = "message"
    log.result({"asks": numbers, "kind": what},
               f"Recorded the {what} verbatim; checklist asks {', '.join(f'#{n}' for n in numbers)} "
               "(check the split in brief/GOAL.md)")
    return 0


def cmd_film_mark(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, f"mark ask #{args.n} {args.status}"):
        return 0
    ask = production.mark(film_dir, args.n, status=args.status, evidence=args.evidence)
    log.result(ask, f"Ask #{ask['n']} is {ask['status']}" + (f": {ask.get('evidence')}" if ask.get("evidence") else ""))
    return 0


def cmd_film_direction(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, "log a numbered direction change"):
        return 0
    text = _read_text(args.text, args.file, "Direction")
    n = production.add_direction(film_dir, text or "", source=args.source, supersedes=args.supersedes)
    log.result({"direction": n}, f"Logged direction change {n} in brief/PRODUCTION.md")
    return 0


def cmd_film_questions(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    qs = approvals.load(film_dir)["questions"]
    if not args.all:
        qs = [q for q in qs if not q.get("answered_at")]
    human = "\n".join(f"{q['id']}{' (parks the film)' if q.get('parks') else ''}: {q['text']}"
                      + (f"\n    answered by {q['answered_by']}: {q['answer']}" if q.get("answered_at") else "")
                      for q in qs) or "No open questions."
    log.result({"questions": qs}, human)
    return 0


def cmd_film_answer(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, f"record the answer to {args.question}"):
        return 0
    q = approvals.answer(film_dir, args.question, text=args.text, by=args.by)
    left = modes.parked(film_dir)
    log.result({"question": q, "still_parked": left},
               f"{q['id']} answered by {q['answered_by']}; logged as a direction change."
               + (f" Still parked on {left[0]['id']}." if left else ""))
    return 0


def cmd_film_park(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, "park the film with this question"):
        return 0
    q = modes.park(film_dir, args.text, evidence=args.evidence or [], source=args.source)
    log.result(q, f"Parked on {q['id']}: {q['text']}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate variant


def cmd_variant(args: argparse.Namespace) -> int:
    parent_dir = paths.film_dir(args.parent)
    if _dry(args):
        log.result({"dry_run": True, "parent": str(parent_dir), "child": args.child},
                   f"Would derive {args.child} from {parent_dir} (nothing written)")
        return 0
    child = derive.create(parent_dir, args.child, format=args.format, aspect=args.aspect, language=args.language,
                          cut=args.cut, title=args.title, mode=args.mode, films_dir=args.dir,
                          git=False if args.no_git else None)
    fj = film.load(child)
    d = fj["derivation"]
    reuse = d.get("reuse") or {}
    human = (f"Derived {fj['id']} from {fj['parent']['id']} ({'+'.join(d['kinds'])}) at {child}\n"
             f"  {fj['width']}×{fj['height']} ({fj['aspect']}) on the parent's clock "
             f"{fj['fps_num']}/{fj['fps_den']}\n"
             f"  reuses {reuse.get('count', 0)} parent files by reference (nothing copied): "
             + ", ".join(f"{k} {v}" for k, v in (reuse.get("by_folder") or {}).items()) + "\n"
             f"  to do: {'; '.join(d['redo'])}")
    log.result({"film": fj["id"], "dir": str(child), "parent": fj["parent"], "derivation": d}, human)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate revise …


def cmd_revise_start(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, f"start a revision of {args.artifact}"):
        return 0
    notes = _read_text(args.notes, args.notes_file, "Notes")
    rev = revise.start(film_dir, args.artifact, notes=notes or "", by=args.by, what=args.what,
                       approved_by=args.approved_by, scopes=args.scope, questions=args.question)
    lines = [f"Revision {rev['id']} of {rev['artifact']} (approved sha256 {_util.short(rev['approved_sha'])}), "
             f"snapshot in {rev['snapshot']}:"]
    lines += [f"  {i['n']}. {i['text']}" for i in rev["items"]] or ["  (no changes: every note became a question)"]
    if rev["questions"]:
        lines.append(f"  questions first (a new approach needs a yes): {', '.join(rev['questions'])}")
    lines.append(f"  {rev['keep']}")
    lines.append("next: make those changes to that exact file, then `slate revise check`")
    log.result(rev, "\n".join(lines))
    return 0


def cmd_revise_check(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, "measure the open revision and record the result"):
        return 0
    res = revise.check(film_dir, args.revision)
    human = [f"{res['revision']} of {res['artifact']}: {res['status']}"]
    human += [f"  {r['status']:<9} {r['gate']}: {r['summary']}" for r in res["results"]]
    if not res["measures"]:
        human.append("  (no comparison hooks are registered yet: levels, sync and palette measures come from the "
                     "sound and qa modules)")
    log.result(res, "\n".join(human))
    return 1 if res["status"] == "FAIL" else 0


def cmd_revise_done(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    if _dry_stop(args, "close the open revision"):
        return 0
    rev = revise.done(film_dir, args.revision, evidence=args.evidence, force=args.force, reason=args.reason,
                      by=args.by)
    log.result(rev, f"{rev['id']} closed ({rev['closing']['check']}). The revised {rev['artifact']} needs the "
                    f"person's approval again:\nnext: {rev['next']}")
    return 0


def cmd_revise_list(args: argparse.Namespace) -> int:
    film_dir = cli.film(args)
    revs = revise.revisions(film_dir)
    human = "\n".join(f"{r['id']}  {r['status']:<5} {r['artifact']}  ({len(r['items'])} changes, "
                      f"{len(r['questions'])} questions)" for r in revs) or "No revisions yet."
    log.result({"revisions": revs}, human)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# Registration


def register(sub) -> None:
    p = cli.add_command(sub, "new", cmd_new, "start a film folder (brief, script skeleton, stage folders, git)",
                        epilog=EPILOG, description=(
                            "Scaffold a film: film.json from the format's defaults, brief/GOAL.md with the request "
                            "verbatim and a checklist of every ask, brief/PRODUCTION.md, script/script.json, every "
                            "stage folder, a .gitignore for heavy media, and git (no remote, ever). Films live in "
                            "~/Movies/Slate/<id> unless --dir says otherwise."))
    p.add_argument("id", help="the film's id (its folder name): lowercase letters, digits, dots, dashes")
    p.add_argument("--brand", help="brand pack name (brands/<name>); the neutral one is 'demo'")
    p.add_argument("--format", help="format name (formats/<name>.json) or a path to a format file")
    p.add_argument("--mode", default="supervised", choices=film.MODES)
    p.add_argument("--title")
    p.add_argument("--generation", default="rarely", choices=film.GENERATION,
                   help="how much generated footage the requester wants (default: rarely)")
    p.add_argument("--request", help="the request, verbatim (- reads stdin)")
    p.add_argument("--request-file", help="read the request from a file")
    p.add_argument("--source", default="requester", help="who made the request (e.g. a name)")
    p.add_argument("--runtime", type=float, help="target runtime in seconds (default: the format's)")
    p.add_argument("--aspect", help="for formats that come in several aspects, e.g. 9:16")
    p.add_argument("--dir", help="make the film in this folder instead of ~/Movies/Slate")
    p.add_argument("--no-git", action="store_true", help="don't start a git history")
    p.add_argument("--parent", help="derive from this film (id or path); see also `slate variant`")
    p.add_argument("--derivation", help='JSON, e.g. {"aspect": "9:16"}, {"cut_s": 15} or {"language": "fr"}')

    p = cli.add_command(sub, "status", cmd_status, "where the film stands, per stage, and the next command",
                        epilog=EPILOG, description=(
                            "Per stage (brief, truth, script, voice, timing, look, scenes, captures, generated shots, "
                            "sound, render, master, qa, deliver, review): fresh, stale, missing, locked or skipped, "
                            "from film.lock.json, qa/gates.json and the files present; the sittings, locks, questions "
                            "and the one next command. Outside a film it lists your films."))
    p.add_argument("--write", action="store_true", help="also refresh the status block in brief/PRODUCTION.md")

    p = cli.add_command(sub, "make", cmd_make, "rebuild only what's stale, in order", epilog=EPILOG, description=(
        "Targets: " + ", ".join(make.ORDER) + " (all = deliver). Runs each stale node's own command in-process; a "
        "command that isn't built yet is reported (exit 3), locked work is never remade, and paid stages need "
        "--spend (unless vendors are replayed)."))
    p.add_argument("target", nargs="?", help="what to bring up to date")
    p.add_argument("--force", action="store_true", help="rebuild the target even if it looks fresh")
    p.add_argument("--spend", action="store_true", help="allow paid stages (voicing) to run")
    p.add_argument("--list", action="store_true", help="show every node's state without running anything")

    p = cli.add_command(sub, "approve", cmd_approve, "record a person's approval of exactly this file",
                        epilog=EPILOG, description=(
                            "Records the SHA-256 of what was approved. Sittings: preproduction (checklist, claims, "
                            "script, voice_shortlist, look_test) and final_cut block; packets (hero, plate, "
                            "sound_proof, take, score_take) don't. Takes, score takes, plates and the final cut are "
                            "locked by content hash. 'spend' records a yes to spend again and prints its id."))
    p.add_argument("what", help=", ".join(approvals.KINDS) + " (a qualifier may follow a colon, e.g. plate:S05)")
    p.add_argument("path", help="the file or folder approved (film-relative)")
    p.add_argument("--by", required=True, help="the person who approved it")
    p.add_argument("--note", help="their words, or where they said it")

    grp = cli.add_group(sub, "film", "film.json, pins, the brief and questions")
    cli.add_command(grp, "show", cmd_film_show, "print film.json, approvals and lock summary", epilog=EPILOG)
    p = cli.add_command(grp, "set", cmd_film_set, "change one film.json key on purpose", epilog=EPILOG, description=(
        "Dotted keys for nested values (targets.runtime_s, budget.elevenlabs). Values are JSON when they parse. "
        "The id, pins, parent and language have their own commands; the clock and size are fixed once shot ids "
        "freeze."))
    p.add_argument("key")
    p.add_argument("value")
    p = cli.add_command(grp, "pin", cmd_film_pin, "pin a value at first use (first use wins)", epilog=EPILOG)
    p.add_argument("key", help="e.g. tts, voice_id, voice_settings, seed.S01, request_id.S01, music, remotion")
    p.add_argument("value")
    p.add_argument("--source", help="where the value came from")
    p = cli.add_command(grp, "repin", cmd_film_repin, "move a pin on purpose (keeps history)", epilog=EPILOG)
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--approved-by", dest="approved_by", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--evidence", help="the A/B test behind it")
    p = cli.add_command(grp, "ask", cmd_film_ask, "record the request or a later message verbatim, with its asks",
                        epilog=EPILOG)
    p.add_argument("text", nargs="?", help="the words, exactly as given (- reads stdin)")
    p.add_argument("--file", help="read the words from a file")
    p.add_argument("--source", required=True, help="who said it")
    p.add_argument("--request", action="store_true", help="this is the original request")
    p.add_argument("--no-split", action="store_true", help="keep it as one ask")
    p = cli.add_command(grp, "mark", cmd_film_mark, "set an ask's status (done needs evidence)", epilog=EPILOG)
    p.add_argument("n", type=int)
    p.add_argument("--status", required=True, choices=production.ASK_STATUSES)
    p.add_argument("--evidence")
    p = cli.add_command(grp, "direction", cmd_film_direction, "log a direction change verbatim (numbered)",
                        epilog=EPILOG)
    p.add_argument("text", nargs="?")
    p.add_argument("--file")
    p.add_argument("--source", required=True)
    p.add_argument("--supersedes", help="what it replaces (the latest approval governs)")
    p = cli.add_command(grp, "questions", cmd_film_questions, "questions waiting for a person", epilog=EPILOG)
    p.add_argument("--all", action="store_true", help="include answered ones")
    p = cli.add_command(grp, "answer", cmd_film_answer, "record a person's answer (unparks the film)", epilog=EPILOG)
    p.add_argument("question", help="e.g. q3")
    p.add_argument("--by", required=True)
    p.add_argument("--text", required=True)
    p = cli.add_command(grp, "park", cmd_film_park, "park the film with a question for a person", epilog=EPILOG)
    p.add_argument("text")
    p.add_argument("--source", required=True, help="what raised it (a gate, a claim check, an agent)")
    p.add_argument("--evidence", action="append", help="a path or measurement (repeatable)")

    if "variant" not in sub.choices:
        p = cli.add_command(sub, "variant", cmd_variant, "derive a cut-down, a new aspect or a new language",
                            epilog=EPILOG, description=(
                                "Makes a derived film that reuses the parent's approved takes, timings, beats and "
                                "assets by reference (nothing is copied), keeps the parent's clock, records the "
                                "re-staging and sound re-plan it owes, runs every gate and needs its own approval."))
        p.add_argument("parent", help="the parent film (id or path)")
        p.add_argument("child", help="the new film's id")
        p.add_argument("--aspect", help="e.g. 9:16, 1:1, 4:5")
        p.add_argument("--cut", type=float, help="a cut-down's length in seconds")
        p.add_argument("--language", help="a new language (BCP 47, e.g. fr); v1 speaks English")
        p.add_argument("--format", help="the derived film's format (size, loudness, safe zones)")
        p.add_argument("--title")
        p.add_argument("--mode", choices=film.MODES)
        p.add_argument("--dir", help="make it in this folder instead of ~/Movies/Slate")
        p.add_argument("--no-git", action="store_true")

    grp = cli.add_group(sub, "revise", "perfect, don't replace: revise approved work by its notes")
    p = cli.add_command(grp, "start", cmd_revise_start, "notes on an approved artifact become its checklist",
                        epilog=EPILOG)
    p.add_argument("artifact", help="the approved file or folder (film-relative)")
    p.add_argument("--notes", help="the person's notes, verbatim (- reads stdin)")
    p.add_argument("--notes-file")
    p.add_argument("--by", required=True, help="whose notes they are")
    p.add_argument("--what", help="which approval it's under (when it has several, or with --approved-by), e.g. "
                                  "sound_proof, script, hero")
    p.add_argument("--approved-by", dest="approved_by", help="record the approval now (the notes came with a yes)")
    p.add_argument("--scope", action="append", help="what may change, e.g. a JSON key like shots.S19 (repeatable)")
    p.add_argument("--question", action="append", help="record a note as a question instead (repeatable)")
    p = cli.add_command(grp, "check", cmd_revise_check, "measure the revision against the approved version",
                        epilog=EPILOG)
    p.add_argument("revision", nargs="?", help="e.g. rv1 (default: the open one)")
    p = cli.add_command(grp, "done", cmd_revise_done, "close the revision (needs a clean check)", epilog=EPILOG)
    p.add_argument("revision", nargs="?")
    p.add_argument("--evidence")
    p.add_argument("--by")
    p.add_argument("--force", action="store_true")
    p.add_argument("--reason")
    cli.add_command(grp, "list", cmd_revise_list, "every revision of this film", epilog=EPILOG)
