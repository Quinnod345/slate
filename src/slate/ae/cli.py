"""`slate ae …`: the After Effects lane's commands.

    slate ae prefs check|get|set|restore        AE's preferences, byte-safe (AE closed for any edit)
    slate ae smoke --go                         the 1-second toolchain test, measured in pixels
    slate ae build --go                         the film's editable AE project from its data, masters queued
    slate ae run <jsx> --go                     any ExtendScript file, through the driver and its lock
    slate ae masters --go                       chunked aerender masters (AE closed; disk cache off, then restored)
    slate ae audit [<aep>] [--go]               the project audit as gate results
    slate ae acts export|render|measure <n>     acts built in AE: trace and events, aerender, AE's own curves
    slate ae bridge status|install|acceptance|looks    Higgsfield's AE bridge, pinned and accepted

Everything that starts After Effects or aerender needs --go, the person's own start, and never runs for an
autonomous film (Adobe's enterprise terms bar operations a person didn't start). Slate never quits AE: when AE has
to be closed, it stops with exit 5 and asks you to save your work and quit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from slate import cli as slate_cli
from slate import gates, log
from slate.errors import Refused, UsageError


def _film_or_none(args: argparse.Namespace) -> Path | None:
    try:
        return slate_cli.film(args)
    except UsageError:
        return None


def _dry(args: argparse.Namespace) -> bool:
    return bool(slate_cli.flag(args, "dry_run", False))


def _go(p: argparse.ArgumentParser) -> None:
    p.add_argument("--go", action="store_true",
                   help="you (a person) are starting After Effects now; an agent passes it only on your say-so")


def _gate_text(results) -> str:
    return gates.format_table(results)


def _queue(args: argparse.Namespace, film: Path, kind: str, tail: list[str], *, what: str, action: str) -> int:
    """--detach: refuse now what the job would refuse later (no go, an autonomous film), then queue this same command
    as a job holding the render slot and After Effects, and hand back its id at once. The job runs the command without
    --detach, still with the person's --go; the queue tells it what it holds (SLATE_JOB_HOLDS=render,ae), so
    slate.ae's lock uses the job's `ae` slot instead of waiting on itself.

    It goes through the queue's --detach convention (slate.jobs.client.detach_current) with the command spelled out,
    not this process's argv: the same `slate ae …` from a person's shell, an agent or the MCP server."""
    from slate.ae import guard  # noqa: PLC0415
    from slate.jobs import client  # noqa: PLC0415

    guard.person_start(args.go, what, film_dir=film, action=action)
    command = ["ae", *tail, "--film", str(film), "--json", "--go"]
    job = client.detach_current(kind, ("render", "ae"), film=str(film), priority="supervised", argv=command)
    log.result({"ok": True, "ran": "queue", "job_id": job, "command": ["slate", *command]},
               f"Queued as job {job}. After Effects must be closed when it starts (save your work and quit it); "
               f"follow it with `slate job status {job}`.")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# prefs


def _prefs_check(args: argparse.Namespace) -> int:
    from slate.ae import prefs

    info = prefs.check(args.file, variant=args.variant or "")
    c = info["disk_cache"]
    human = "\n".join([
        f"file: {info['file']}",
        f"line endings: {info['cr']} CR, {info['lf']} LF"
        + ("  <- DAMAGED: AE will reset its prefs on next launch" if info["damaged"] else "  (ok)"),
        f"Allow Scripts to Write Files and Access Network: {info['scripting']}",
        f"disk cache: {'on' if c['on'] else 'off'}, folder {c['folder']}, max {c['max_gb']} GB",
        f"running: {', '.join(info['running']) if info['running'] else 'nothing'}",
    ])
    log.result(info, human)
    return 1 if info["damaged"] else 0


def _prefs_get(args: argparse.Namespace) -> int:
    from slate.ae import prefs

    value = prefs.get(args.section, args.key, file=args.file, variant=args.variant or "")
    log.result({"section": args.section, "key": args.key, "value": value}, value)
    return 0


def _prefs_set(args: argparse.Namespace) -> int:
    from slate.ae import prefs

    if _dry(args):
        current = prefs.get(args.section, args.key, file=args.file, variant=args.variant or "")
        log.result({"dry_run": True, "section": args.section, "key": args.key, "now": current, "would_be": args.value},
                   f'dry run: ["{args.section}"] "{args.key}" is {current}; would become {args.value}')
        return 0
    res = prefs.set(args.section, args.key, args.value, file=args.file, variant=args.variant or "")
    human = (f'["{args.section}"] "{args.key}" is already {args.value}; nothing written.' if not res["changed"] else
             f'["{args.section}"] "{args.key}": {res["old"]} -> {res["new"]} (only that value changed; line endings '
             f"kept)" + (f"\nprevious file kept in {res['backup']}" if res["backup"] else ""))
    log.result(res, human)
    return 0


def _prefs_restore(args: argparse.Namespace) -> int:
    from slate.ae import prefs

    res = prefs.restore(args.src, file=args.file, apply=args.apply and not _dry(args), variant=args.variant or "",
                        skip_sections=tuple(args.skip_section or ()))
    lines = [f"from {res['from']}", f"settings to add back: {len(res['added'])}"]
    lines += [f"  {n}  {c}" for n, c in sorted(res["by_section"].items(), key=lambda x: -x[1])]
    lines.append(f"settings to set back to your value: {len(res['changed'])}")
    lines += [f"  {c['section']} {c['key']}: {c['now'][:40]} -> {c['was'][:40]}" for c in res["changed"]]
    if res["skipped_mru"]:
        lines.append(f"recent-file entries left alone (their lists have newer entries now): {len(res['skipped_mru'])}")
    lines.append(f"unchanged: {res['unchanged']}")
    lines.append(f"restored; previous file kept in {res['backup']}" if res["applied"] and res["backup"] else
                 "restored" if res["applied"] else "dry run: nothing written (add --apply, with AE closed)")
    log.result(res, "\n".join(lines))
    return 0


# ---------------------------------------------------------------------------------------------------------------
# smoke, build, run, masters, audit


def _smoke(args: argparse.Namespace) -> int:
    from slate.ae import guard, smoke

    size = {}
    if args.size:
        try:
            w, h = (int(x) for x in args.size.lower().split("x"))
        except ValueError:
            raise UsageError("--size takes WxH, for example 960x540.") from None
        size.update(width=w, height=h)
    if args.fps:
        num, _, den = args.fps.partition("/")
        size.update(fps_num=int(num), fps_den=int(den or 1))
    if _dry(args):
        p = smoke.params(**{**smoke.DEFAULT, **size})
        log.result({"dry_run": True, "params": p}, f"dry run: would build and render {p['frames']} frames at "
                                                   f"{p['width']}x{p['height']}, then measure the dot")
        return 0
    if not args.go:
        last = smoke.last()
        raise Refused("The smoke test starts After Effects and aerender, so it waits for a person's go.",
                      hint=f"{guard.GO_HINT} Last result: {last.status}, {last.summary}")
    result = smoke.run(args.out, go=True, om=args.om, **size)
    log.result(result.to_dict(), _gate_text([result]))
    return 0 if result.passed else 1


def _build(args: argparse.Namespace) -> int:
    from slate.ae import project, spec

    film = slate_cli.film(args)
    if _dry(args):
        s = spec.build_spec(film, chunks=args.chunks, queue=not args.no_queue)
        summary = {"dry_run": True, "aep": s["paths"]["aep"], "acts": [(a["index"], a["engine"]) for a in s["acts"]],
                   "shots": len(s["shots"]), "renders": len(s["renders"]), "markers": {k: len(v) for k, v in
                                                                                         s["markers"].items()},
                   "masters_chunks": len((s.get("masters") or {}).get("chunks") or []), "warnings": s["warnings"]}
        log.result(summary, "dry run: would build " + s["paths"]["aep"] + "\n" + json.dumps(summary, indent=1))
        return 0
    aep = project.build(film, go=args.go, chunks=args.chunks, queue=not args.no_queue)
    rep = project.last_report(film) or {}
    data = rep.get("report") or {}
    lines = [f"built {aep}"]
    if data.get("preserved_project"):
        lines.append(f"your unsaved work in After Effects was saved first: {data['preserved_project']}")
    built = data.get("built") or {}
    lines.append(f"shots: {len(built.get('rendered') or [])} from renders, "
                 f"{len(built.get('placeholders') or [])} placeholders; acts built in AE: "
                 f"{len(built.get('ae_acts') or [])}")
    for w in rep.get("warnings") or []:
        lines.append(f"warning: {w}")
    lines.append("next: save and quit After Effects, then `slate ae masters --go`; `slate ae audit --go` checks it")
    log.result({"aep": str(aep), "report": rep}, "\n".join(lines))
    return 0


def _run(args: argparse.Namespace) -> int:
    from slate.ae import driver, guard

    try:
        extra = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        raise UsageError(f"--args isn't JSON: {exc.msg}") from exc
    film = _film_or_none(args)
    if _dry(args):
        argv = driver.osascript_argv(Path("<run>/wrapper.jsx"), app=driver.app_name(), timeout_s=args.timeout)
        log.result({"dry_run": True, "argv": argv, "script": str(Path(args.jsx).resolve()), "args": extra},
                   "dry run: " + " ".join(argv))
        return 0
    guard.person_start(args.go, "Running a script in After Effects", film_dir=film)
    res = driver.run_jsx(args.jsx, args=extra, timeout=args.timeout)
    human = f"{Path(args.jsx).name}: {res['status']} ({res['report_channel']}, {res['seconds']} s)"
    if res["report"]:
        human += "\n" + json.dumps(res["report"], indent=1, ensure_ascii=False)[:4000]
    log.result(res, human)
    return 0 if res["ok"] else 1


def _masters(args: argparse.Namespace) -> int:
    from slate.ae import masters, spec

    film = slate_cli.film(args)
    if args.detach and not _dry(args):
        if args.requeue:
            raise UsageError("--requeue opens After Effects for a moment to re-queue the chunks; run it without --detach.")
        tail = ["masters"] + (["--chunks", str(args.chunks)] if args.chunks else []) + \
            [flag for flag, on in (("--keep-chunks", args.keep_chunks), ("--silent", args.silent),
                                   ("--keep-cache", args.keep_cache)) if on]
        return _queue(args, film, "ae_masters", tail, what="Rendering the masters with aerender", action="ae_master")
    if args.requeue:
        rec = masters.queue(film, go=args.go, chunks=args.chunks)
        log.result(rec, f"queued {len(rec['chunks'])} chunks in {rec['aep']}\nnext: save and quit After Effects, then "
                        "`slate ae masters --go`")
        return 0
    if _dry(args):
        s = spec.read_spec(film)
        plan = (s.get("masters") or {}).get("chunks") or []
        log.result({"dry_run": True, "chunks": plan, "aep": s["paths"]["aep"]},
                   f"dry run: would render {len(plan)} chunks of {s['film']['total_frames']} frames from "
                   f"{s['paths']['aep']}")
        return 0
    res = masters.render(film, go=args.go, chunks=args.chunks, keep_chunks=args.keep_chunks,
                         allow_silent=args.silent, cache_off=not args.keep_cache)
    cache = res.get("cache") or {}
    human = "\n".join([f"H.264 master: {res['h264']}", f"ProRes 422 HQ master: {res['prores_422_hq']}",
                       f"{res['frames']} frames each, {len(res['chunks'])} chunks, in {res['seconds']} s",
                       "AE's disk cache: " + ("switched off for the run and restored" if cache.get("toggled") else
                                              "left as it was") +
                       ("" if cache.get("restored") in (True, None) else " (NOT restored: see the log above)")])
    log.result(res, human)
    return 0


def _audit(args: argparse.Namespace) -> int:
    from slate.ae import audit

    film = _film_or_none(args)
    results = audit.run(args.aep, film_dir=film, go=args.go, report=args.report)
    log.result({"gates": [r.to_dict() for r in results], "summary": gates.summarize(results)}, _gate_text(results))
    return gates.exit_code(results)


# ---------------------------------------------------------------------------------------------------------------
# acts


def _acts_export(args: argparse.Namespace) -> int:
    from slate.ae import acts

    film = slate_cli.film(args)
    todo = [args.act] if args.act else acts.ae_acts(film)
    if not todo:
        raise UsageError(f"This film has no acts built in AE (no {acts.SCENE_DIR}/actN.json).")
    out = [acts.export(film, a) for a in todo]
    def plural(n: int, word: str) -> str:
        return f"{n} {word}" + ("" if n == 1 else "s")

    log.result({"exported": out}, "\n".join(f"act {o['act']}: {plural(o['entries'], 'traced move')} -> {o['motion']}; "
                                            + (f"silent -> {o['events']}" if o["silent"] else
                                               f"{plural(o['beats'], 'beat')} -> {o['events']}") for o in out))
    return 0


def _acts_render(args: argparse.Namespace) -> int:
    from slate.ae import acts

    if args.detach:
        return _queue(args, slate_cli.film(args), "ae_render", ["acts", "render", str(args.act)],
                      what="Rendering an act with aerender", action="ae_render")
    res = acts.render(slate_cli.film(args), args.act, go=args.go)
    log.result(res, f"act {res['act']}: {res['file']} ({res['frames'][1] - res['frames'][0]} frames)"
               + (", registered" if res["registered"] else ""))
    return 0


def _acts_measure(args: argparse.Namespace) -> int:
    from slate.ae import acts

    res = acts.export_measured(slate_cli.film(args), args.act, go=args.go)
    log.result(res, f"act {res['act']}: {res['entries']} measured moves -> {res['motion']}")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# bridge


def _bridge_status(args: argparse.Namespace) -> int:
    from slate.ae import bridge

    st = bridge.status(run_doctor=args.doctor)
    route = "the bridge" if st["route"] == "bridge" else "Slate's own ES3 library (needs no pref)"
    lines = [f"{st['package']}: " + (f"{st['version']} installed" if st["installed"] else "not installed")
             + f" (Slate pins {st['pinned']}{', matches' if st['pin_ok'] else ''})",
             f"Allow Scripts to Write Files and Access Network: {st['scripting_pref']} (Slate reads it, never "
             "changes it)",
             "finishing (build, audit, masters): works without the pref",
             f"scenes are built by: {route}",
             f"acceptance: " + (f"passed on {st['acceptance']['version']}" if st["acceptance_current"] else
                                "not passed on this version"),
             f"looks ({len(st['looks'])}): {', '.join(st['looks'])}"]
    if st["installed"] and st["looks_missing"]:
        lines.append(f"looks missing from the installed package: {', '.join(st['looks_missing'])}")
    if st["doctor"]:
        lines.append("bridge doctor: " + ("ok" if st["doctor"]["ok"] else "problems: " + ", ".join(
            k for k, v in st["doctor"]["checks"].items() if not v)))
    lines += [f"next: {n}" for n in st["next"]]
    log.result(st, "\n".join(lines))
    return 0


def _bridge_install(args: argparse.Namespace) -> int:
    from slate.ae import bridge

    res = bridge.install(yes=args.yes and not _dry(args))
    plan = res["plan"]
    lines = ["install: " + " ".join(plan["install"])]
    lines += [f"register ({k}): " + (" ".join(v) if isinstance(v, list) else v) for k, v in plan["register"].items()]
    lines += plan["notes"]
    lines.append(f"installed {res['version']}" if res["ran"] else "nothing ran: add --yes to install (your yes)")
    log.result(res, "\n".join(lines))
    return 0


def _bridge_acceptance(args: argparse.Namespace) -> int:
    from slate.ae import bridge

    if args.plan or _dry(args):
        script = bridge.acceptance_script()
        log.result({"dry_run": True, "script": script},
                   "the acceptance script (runs with AE open, on your go):\n" +
                   "\n".join(f"  {op['id']}: {op['tool']} {op.get('args', {}).get('operation', '')}".rstrip()
                             for op in [script["preflight"]] + script["ops"]))
        return 0
    rec = bridge.acceptance(go=args.go, scratch_dir=args.scratch)
    log.result(rec, f"acceptance on {rec['version']}: {'PASSED' if rec['passed'] else 'FAILED'}\n" +
               "\n".join(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['id']}" + (f": {r['problem']}" if r["problem"] else "")
                         for r in rec["results"]))
    return 0 if rec["passed"] else 1


def _bridge_looks(args: argparse.Namespace) -> int:
    from slate.ae import bridge

    if args.show:
        payload = bridge.fetch_look(args.show, reference=args.reference)
        text = payload.get("text") or payload.get("content") or json.dumps(payload, indent=1, ensure_ascii=False)
        log.result({"look": args.show, "payload": payload}, str(text))
        return 0
    log.result({"looks": bridge.looks(), "entry": bridge.ENTRY_LOOK},
               "the AE bridge's looks (fetched from the bridge when used, never copied):\n  "
               + "\n  ".join(bridge.looks()))
    return 0


# ---------------------------------------------------------------------------------------------------------------
# register


def register(sub) -> None:
    ae = slate_cli.add_group(sub, "ae", help="After Effects: prefs, smoke test, project build, masters, audit, bridge",
                             description=__doc__)

    prefs_g = slate_cli.add_group(ae, "prefs", help="AE's preferences, edited byte by byte (never the scripting "
                                                    "setting)")
    for name, handler, helptext in (("check", _prefs_check, "line endings, the scripting setting, the disk cache"),
                                    ("get", _prefs_get, "print one value"),
                                    ("set", _prefs_set, "change one existing value (AE closed)")):
        p = slate_cli.add_command(prefs_g, name, handler, help=helptext)
        if name in ("get", "set"):
            p.add_argument("section", help='the section, e.g. "Disk Cache Controls"')
            p.add_argument("key", help='the key, e.g. "Enabled 2"')
        if name == "set":
            p.add_argument("value", help="the new value (quoting style is kept)")
        p.add_argument("--file", help="work on this copy instead of AE's own prefs file")
        p.add_argument("--variant", help="another prefs file of the same AE version, e.g. indep-output")
    p = slate_cli.add_command(prefs_g, "restore", _prefs_restore,
                              help="add back settings a reset lost, from an older copy (dry run unless --apply)")
    p.add_argument("--from", dest="src", required=True, help="the older prefs file")
    p.add_argument("--apply", action="store_true", help="write it (AE must be closed)")
    p.add_argument("--file", help="work on this copy instead of AE's own prefs file")
    p.add_argument("--variant", help="another prefs file of the same AE version, e.g. indep-output")
    p.add_argument("--skip-section", action="append", help="another script-report section to leave alone")

    p = slate_cli.add_command(ae, "smoke", _smoke, help="the 1-second toolchain test: build, aerender, measure")
    _go(p)
    p.add_argument("--out", help="where the smoke files go (default: Slate's data folder)")
    p.add_argument("--om", default="High Quality", help="aerender output-module template (default: High Quality)")
    p.add_argument("--size", help="WxH (default 960x540)")
    p.add_argument("--fps", help="rate as N or N/D (default 30)")

    p = slate_cli.add_command(ae, "build", _build, help="build the film's editable AE project from its data")
    _go(p)
    p.add_argument("--chunks", type=int, help="master chunks to queue (default: film.json ae.master_chunks, or 8)")
    p.add_argument("--no-queue", action="store_true", help="don't queue the masters")

    p = slate_cli.add_command(ae, "run", _run, help="run an ExtendScript file in AE (reports come back to Slate)")
    p.add_argument("jsx", help="the .jsx file")
    _go(p)
    p.add_argument("--args", help="JSON the script reads as $.global.SLATE_ARGS")
    p.add_argument("--timeout", type=int, default=600, help="seconds (default 600)")

    p = slate_cli.add_command(ae, "masters", _masters, help="chunked aerender masters: H.264 and ProRes 422 HQ")
    _go(p)
    p.add_argument("--chunks", type=int, help="with --requeue: how many chunks")
    p.add_argument("--requeue", action="store_true", help="re-queue the chunks in the project (opens AE) and stop")
    p.add_argument("--keep-chunks", action="store_true", help="keep the chunk files after the joins")
    p.add_argument("--silent", action="store_true", help="a picture-only master when the film has no mix yet")
    p.add_argument("--keep-cache", action="store_true", help="leave AE's disk cache as it is during the render")
    p.add_argument("--detach", action="store_true",
                   help="queue it as a job (your --go goes with it) and return the job id at once")

    p = slate_cli.add_command(ae, "audit", _audit, help="audit the film's AE project (gate results)")
    p.add_argument("aep", nargs="?", help="the project (default: the film's)")
    _go(p)
    p.add_argument("--report", help="read a saved validate report instead of running AE")

    acts_g = slate_cli.add_group(ae, "acts", help="acts built in AE: trace and events, aerender, AE's own curves")
    p = slate_cli.add_command(acts_g, "export", _acts_export,
                              help="write timing/motion/actN.json and timing/visual_events/actN.json (no AE needed)")
    p.add_argument("act", type=int, nargs="?", help="the act (default: every act built in AE)")
    p = slate_cli.add_command(acts_g, "render", _acts_render, help="render an AE-built act with aerender, register it")
    p.add_argument("act", type=int)
    p.add_argument("--detach", action="store_true",
                   help="queue it as a job (your --go goes with it) and return the job id at once")
    _go(p)
    p = slate_cli.add_command(acts_g, "measure", _acts_measure,
                              help="sample an act's motion as AE evaluates it (for acts built through the bridge)")
    p.add_argument("act", type=int)
    _go(p)

    br = slate_cli.add_group(ae, "bridge", help="Higgsfield's AE bridge (fnf-after-effects-mcp), pinned")
    p = slate_cli.add_command(br, "status", _bridge_status, help="version, pin, the file-write pref, the route")
    p.add_argument("--doctor", action="store_true", help="also run the bridge's own offline doctor")
    p = slate_cli.add_command(br, "install", _bridge_install, help="the pinned install (runs only with --yes)")
    p.add_argument("--yes", action="store_true", help="your yes: install it globally with npm")
    p = slate_cli.add_command(br, "acceptance", _bridge_acceptance,
                              help="the fixed acceptance run on a scratch project (AE open, your go)")
    _go(p)
    p.add_argument("--scratch", help="where the scratch project goes")
    p.add_argument("--plan", action="store_true", help="list the steps without running them")
    p = slate_cli.add_command(br, "looks", _bridge_looks, help="the bridge's 13 looks (fetched when used)")
    p.add_argument("--show", metavar="LOOK", help="fetch one look's text from the bridge now")
    p.add_argument("--reference", help="with --show: one of the look's reference files")


__all__ = ["register"]
