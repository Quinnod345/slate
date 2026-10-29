"""`slate gen …` and `slate plates …`: generated shots on the Higgsfield Cloud API, judged before they ship."""

from __future__ import annotations

import argparse
import json
import sys

from slate import cli, log
from slate.errors import MissingPrerequisite, UsageError
from slate.gen import deps, judge, matte, plates, policy, repair, run as gen_run, track
from slate.vendors import higgsfield

GEN_HELP = """Generation runs only on the Higgsfield Cloud API (the one funded lane), only where the film's generation
setting and the shot's reason allow it, estimate first, at least two takes, every take judged by a person.

  slate plates plan                    what may generate, on which model, at what estimated cost
  slate gen estimate --shot S05        the free /estimate for that shot's takes
  slate gen run --shot S05             generate the takes (resumes takes still running; never pays twice)
  slate gen judge --shot S05           contact sheet + a REVIEW gate: a person picks
  slate gen pick --shot S05 --take t2 --by <who>
  slate plates conform --shot S05      the approved take → JPEG sequence at the film's frame rate

Autonomous films: slate gen request-approval --shot S05 asks the person; slate gen approve --id … --by … records
their yes; slate gen run --shot S05 --approval <id> spends."""

PLATES_HELP = """Plates: the plan of generated shots, the conform of an approved take, bridge mattes (a generation's
shape revealing real frames; its pixels never become the picture), edge tracks (so code draws exactly on a plate) and
timing repair (a generation that drops or freezes a frame at its chunk joins, re-timed by its measured motion).

  slate plates conform --shot S05      the approved take → JPEG sequence at the film's rate, its steps checked
  slate plates repair S05              measure, re-time and re-synthesise into a staging folder, with a report
  slate plates repair S05 --install    put the staged frames in place (the old sequence is kept)"""

REPAIR_HELP = """Repair a generated plate whose steps are uneven (a dropped or frozen frame at the model's chunk joins).

Each step of the approved take is measured on the plate's moving band; the frames go on a true-time axis (a missing
frame takes about 1.8 units, its measured share; a frozen frame takes none and its copy is dropped); every film frame
is made at its even place on that axis, interpolated between its two neighbours with optical flow (or --blend); and
the plate's edge track moves through the same warp. Everything is staged in plates/<shot>/plate/repair/ with a report
first. --install puts it in place only when no step is over 1.3× its neighbours, keeping the old sequence in
plates/<shot>/plate/before_repair/. The approved take is never touched."""


def _say_json(obj, human: str) -> int:
    log.result(obj, human)
    return 0


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else "$?"


# ---------------------------------------------------------------------------------------------------------------
# gen


def _estimate(args: argparse.Namespace) -> int:
    film = cli.film(args)
    est = gen_run.estimate(film, args.shot, takes=args.takes)
    human = (f"{args.shot}: {est['takes']} takes on {est['model']} ({est['model_source']}), "
             f"{_money(est['estimate_usd_per_take'])} a take, {_money(est['estimate_usd_total'])} in all"
             + (" (replay: registry prices, nothing quoted)" if est["replay"] else " (Higgsfield /estimate, free)")
             + ("\nautonomous film: it waits for a person's yes (slate gen request-approval)"
                if est["needs_approval"] else ""))
    return _say_json(est, human)


def _run(args: argparse.Namespace) -> int:
    film = cli.film(args)
    dry = cli.dry_run(args)
    if args.detach and not dry:  # a dry run is the estimate below, never a queued job
        return _detach(args, film)
    result = gen_run.run(film, args.shot, takes=args.takes, approval=args.approval, force=args.force,
                         timeout_s=args.timeout, dry_run=dry)
    if result.get("dry_run"):
        result["queued"] = bool(args.detach)
        human = (f"would generate {result['new_takes']} new take(s) of {args.shot} on {result['model']} "
                 f"({_money(result['estimate_usd_new'])}), resuming {len(result['resuming'])}"
                 + (", as a queued job" if args.detach else ""))
        return _say_json(result, human)
    lines = [f"{args.shot} on {result['model']}: {result['completed']} take(s) finished"]
    for t in result.get("takes", []):
        lines.append(f"  {t['id']}  {t['status']:<10} {t.get('request_id') or ''}  "
                     f"{_money(t.get('actual_usd'))}  {', '.join(t.get('files') or []) or t.get('error') or ''}")
    lines.append(f"next: {result['next']}")
    return _say_json(result, "\n".join(lines))


def _detach(args: argparse.Namespace, film) -> int:
    jobs = deps.jobs_client()
    if jobs is None or not hasattr(jobs, "submit"):
        raise MissingPrerequisite("The job queue isn't installed yet, so --detach can't hand this run off.",
                                  hint="Run it without --detach (it resumes safely if it's interrupted).")
    argv = [sys.executable, "-m", "slate", "gen", "run", "--film", str(film), "--shot", args.shot,
            "--timeout", str(args.timeout)]
    if args.takes:
        argv += ["--takes", str(args.takes)]
    if args.approval:
        argv += ["--approval", args.approval]
    if args.force:
        argv.append("--force")
    if cli.flag(args, "live", False):
        argv.append("--live")
    mode = policy.mode(deps.load_film(film))
    if cli.dry_stop(args, f"queue the generation of {args.shot} as a job holding the Higgsfield slot", queued=True,
                    argv=argv):
        return 0  # the job's argv carries no --dry-run: a dry run must never reach submit
    job_id = jobs.submit("generation", argv, film=str(film), resources=("higgsfield",),
                         priority="autonomous" if mode == "autonomous" else "supervised")
    return _say_json({"job": job_id, "shot": args.shot}, f"queued as job {job_id}: slate job status {job_id}")


def _takes(args: argparse.Namespace) -> int:
    film = cli.film(args)
    rec = gen_run.takes(film, args.shot, refresh=args.refresh and not cli.dry_run(args))  # a dry run polls nothing
    lines = [f"{args.shot} on {rec.get('model')} (wants {rec.get('takes_wanted')}):"]
    for t in rec.get("takes", []):
        lines.append(f"  {t['id']}  {t['status']:<10} {t.get('verdict'):<9} {t.get('request_id') or ''}  "
                     f"{_money(t.get('actual_usd'))}  {', '.join(t.get('files') or []) or t.get('error') or ''}")
    return _say_json(rec, "\n".join(lines))


def _judge(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if cli.dry_stop(args, f"decode {args.shot}'s takes into a contact sheet ({args.samples} frames a take) and record "
                          "its REVIEW gate"):
        return 0
    result = judge.judge(film, args.shot, samples=args.samples, width=args.width)
    lines = [f"{args.shot}: contact sheet {result['sheet']} (REVIEW: a person picks)"]
    for m in result["takes"]:
        length = f", {m['duration']} s" if m.get("duration") else ""
        numbers = (f", black {m.get('black_frames', 0)}, flat {m.get('flat_frames', 0)}, longest freeze "
                   f"{m.get('longest_freeze_frames', 0)} frames") if m.get("frames_decoded") is not None else ""
        lines.append(f"  {m['take']}: {m.get('width')}×{m.get('height')}{length}{numbers}")
    lines.append(f"next: {result['next']}")
    log.result(result, "\n".join(lines))
    return 0


def _pick(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if cli.dry_stop(args, f"record {args.by}'s verdict on {args.shot}: "
                          + "; ".join(x for x in (f"approve {args.take} and lock it" if args.take else "",
                                                  f"reject {', '.join(args.reject)}" if args.reject else "") if x)):
        return 0
    result = judge.pick(film, args.shot, take_id=args.take, by=args.by, reject=args.reject, why=args.why,
                        note=args.note)
    gate = result["gate"]
    human = f"{gate['status']}: {gate['summary']}\nnext: {result['next']}"
    log.result(result, human)
    return 0 if gate["status"] in ("PASS", "REVIEW") else 1


def _cancel(args: argparse.Namespace) -> int:
    film = cli.film(args) if (args.shot or not args.request) else None
    what = f"request {args.request}" if args.request else f"{args.shot}'s running takes" + (
        f" ({args.take})" if args.take else "")
    if cli.dry_stop(args, f"cancel {what} on Higgsfield and record it"):
        return 0
    result = gen_run.cancel(film, shot_id=args.shot, take_id=args.take, request_id=args.request)
    lines = [f"{r.get('take', r.get('request_id'))}: "
             + ("canceled" if r.get("canceled") else r.get("summary", "not canceled"))
             for r in result.get("results", [])] or [result.get("summary", "nothing to cancel")]
    return _say_json(result, "\n".join(lines))


def _request_approval(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if cli.dry_stop(args, f"write an approval request for {args.shot}'s generation"
                          + (f" ({args.takes} takes)" if args.takes else "")
                          + " and send it to the owner by iMessage and push", sends=True):
        return 0
    req = gen_run.request_approval(film, args.shot, takes=args.takes)
    sent = req["notify"]
    human = (f"approval {req['id']} written to {req['file']}\n{req['message']}\n"
             + ("sent by iMessage and push" if sent.get("sent")
                else f"not sent: {sent.get('why') or sent.get('error')}")
             + f"\nwhen the yes comes in: slate gen approve --id {req['id']} --by <who>")
    return _say_json(req, human)


def _approve(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if cli.dry_stop(args, f"record {args.by}'s yes to {args.id}"):
        return 0
    result = gen_run.approve(film, args.id, by=args.by, note=args.note)
    return _say_json(result, f"{args.id} approved by {args.by} (recorded as {result.get('yes')}): "
                             f"slate gen run --shot {result['shot']} --approval {args.id}")


def _probe(args: argparse.Namespace) -> int:
    result = higgsfield.Client().probe()
    log.result(result, f"Higgsfield Cloud API: {result['summary']}" + (" (replay)" if result.get("replay") else ""))
    return 0 if result.get("ok") else 3


# ---------------------------------------------------------------------------------------------------------------
# plates


def _plan(args: argparse.Namespace) -> int:
    film = cli.film(args)
    result = plates.plan(film, write=not bool(cli.flag(args, "dry_run", False)))
    lines = [f"generation: {result['setting']} ({result['mode']}), lane: {result['lane']}"]
    for row in result["shots"]:
        mark = "yes" if row["allowed"] else "no "
        cost = f"{row['takes']} × {_money(row['estimate_usd_per_take'])} on {row['model']}" if row["allowed"] else ""
        lines.append(f"  {mark} {row['shot']:<8} {cost}  {row['why'] if not row['allowed'] else ''}"
                     + (f"  [{'; '.join(row['problems'])}]" if row["problems"] else "")
                     + (f"  ({row['status']})" if row.get("status") else ""))
    t = result["totals"]
    lines.append(f"{t['allowed']} of {t['shots']} shot(s) may generate: {t['takes']} takes, about "
                 f"{_money(t['estimate_usd'])}" + (" (replay prices)" if result["replay"] else ""))
    if not result["shots"]:
        lines = ["No shot carries a generate block: code builds the whole film."]
    return _say_json(result, "\n".join(lines))


def _conform(args: argparse.Namespace) -> int:
    film = cli.film(args)
    if cli.dry_stop(args, f"conform {args.shot}'s approved take to a JPEG sequence at the film's rate ({args.method}, "
                          f"fit {args.fit})" + ("" if args.no_audio else ", with its ambience")
                          + (", and a ProRes copy" if args.prores else "")):
        return 0
    result = plates.conform(film, args.shot, method=args.method, fit=args.fit, frames=args.frames,
                            audio=not args.no_audio, prores=args.prores)
    f = result["frames"]
    human = (f"{args.shot}: {f['count']} frames at {f['fps_num']}/{f['fps_den']} fps, {f['width']}×{f['height']} "
             f"→ {f['dir']}\nplate: {result['plate']}" + "".join(f"\nwarning: {w}" for w in result["warnings"]))
    return _say_json(result, human)


def _matte(args: argparse.Namespace) -> int:
    film = cli.film(args)
    params = None
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise UsageError(f"--params isn't JSON: {exc}") from exc
    if cli.dry_stop(args, f"build {args.shot}'s bridge matte frames and record them"):
        return 0
    result = matte.build(film, args.shot, base=args.base, real=args.real, duration=args.duration, params=params,
                         preview=args.preview)
    f = result["frames"]
    return _say_json(result, f"{args.shot}: {f['count']} bridge frames → {f['dir']} (record {result['matte']})")


def _track(args: argparse.Namespace) -> int:
    film = cli.film(args)
    params = {k: v for k, v in (("sat_min", args.sat), ("val_min", args.val), ("points", args.points),
                                ("every", args.every)) if v is not None}
    if cli.dry_stop(args, f"track {args.shot}'s edge and write its track"):
        return 0
    result = track.run(film, args.shot, hue=args.hue, frames_dir=args.frames, params=params, until=args.until)
    lo, hi = result["y_range"]
    return _say_json(result, f"{args.shot}: {result['samples']} samples, edge between y {lo:.0f} and {hi:.0f} px "
                             f"→ {result['track']}")


def _repair(args: argparse.Namespace) -> int:
    film = cli.film(args)
    shot = args.shot_pos or args.shot
    if not shot:
        raise UsageError("Name the shot: slate plates repair <shot> (or --shot <shot>).")
    if args.shot_pos and args.shot and args.shot_pos != args.shot:
        raise UsageError(f"Two shots named ({args.shot_pos} and {args.shot}): give one.")
    if args.install and (args.blend or args.band):
        raise UsageError("--install puts the staged repair in place as it was made; --blend and --band belong to the "
                         "staging run.")
    dry = bool(getattr(args, "dry_run", False))
    result = repair.run(film, shot, blend=args.blend, band=args.band, do_install=args.install, stage_dir=args.stage,
                        dry_run=dry)
    if dry:
        lines = [result["summary"]]
        for r in (result.get("retimed_steps") or [])[:12]:
            lines.append(f"  step into source frame {r['step_to_source_frame']}: {r['kind']}, {r['units']} units "
                         f"({r['motion_px']} px)")
        lines += [f"next: {n}" for n in result.get("next") or []]
        log.result(result, "\n".join(lines))
        return 0
    if args.install:
        human = (f"{shot}: installed {result['count']} repaired frames in {result['frames']}; the old sequence and "
                 f"records are in {result['kept']}" + (f"; the track moved with it ({result['track']})"
                                                       if result.get("track") else ""))
        human += "".join(f"\nnext: {n}" for n in result.get("next") or [])
        return _say_json(result, human)
    lines = [result["summary"]]
    if result.get("needed"):
        for r in (result.get("retimed_steps") or [])[:12]:
            lines.append(f"  step into source frame {r['step_to_source_frame']}: {r['kind']}, {r['units']} units "
                         f"({r['motion_px']} px)")
        if result.get("frozen_dropped"):
            lines.append(f"  frozen frames dropped: {', '.join(map(str, result['frozen_dropped']))}")
        lines.append(f"  staged in {result['staged']}; report {result['report']}")
        lines += [f"next: {n}" for n in result.get("next") or []]
    log.result(result, "\n".join(lines))
    return 0


def register(sub) -> None:
    g = cli.add_group(sub, "gen", "Generated shots on the Higgsfield Cloud API: estimate, run, judge, pick.",
                      description=GEN_HELP)
    p = cli.add_command(g, "estimate", _estimate, "the free /estimate for a shot's takes (nothing is spent)")
    p.add_argument("--shot", required=True, help="the shot whose generate block to price")
    p.add_argument("--takes", type=int, help=f"how many takes (at least {policy.MIN_TAKES})")

    p = cli.add_command(g, "run", _run, "generate a shot's takes (estimate first, ledger, at least two takes)",
                        description="Generate a shot's takes. Refuses before spending when the policy, the brand's "
                                    "upload rule, the budget or (autonomous films) the person's yes says no. "
                                    "A rerun waits on takes still running instead of paying for them again.")
    p.add_argument("--shot", required=True)
    p.add_argument("--takes", type=int,
                   help=f"how many takes in all (default from the block, else {policy.DEFAULT_TAKES})")
    p.add_argument("--approval", help="the approval id a person said yes to (autonomous films)")
    p.add_argument("--force", action="store_true",
                   help="make more takes of the same request even though one is approved; changed requests need a new shot id")
    p.add_argument("--timeout", type=float, default=gen_run.WAIT_TIMEOUT_S, help="seconds to wait per take")
    p.add_argument("--detach", action="store_true", help="run it as a job in the queue and return at once")

    p = cli.add_command(g, "takes", _takes, "list a shot's takes (--refresh polls the running ones once)")
    p.add_argument("--shot", required=True)
    p.add_argument("--refresh", action="store_true")

    p = cli.add_command(g, "judge", _judge, "contact sheet of a shot's takes and a REVIEW gate: a person picks")
    p.add_argument("--shot", required=True)
    p.add_argument("--samples", type=int, default=6, help="frames per take on the sheet")
    p.add_argument("--width", type=int, default=320, help="thumbnail width in pixels")

    p = cli.add_command(g, "pick", _pick, "record a person's verdict: the approved take, rejected takes")
    p.add_argument("--shot", required=True)
    p.add_argument("--take", help="the take a person approved")
    p.add_argument("--by", required=True, help="who judged (a person, never Slate)")
    p.add_argument("--reject", action="append", default=[], metavar="TAKE", help="a take that doesn't ship")
    p.add_argument("--why", help="why the rejected takes don't ship")
    p.add_argument("--note", help="a note on the approved take")

    p = cli.add_command(g, "cancel", _cancel, "cancel a shot's running takes, or one request")
    p.add_argument("--shot")
    p.add_argument("--take")
    p.add_argument("--request", help="a Higgsfield request id")

    p = cli.add_command(g, "request-approval", _request_approval,
                        "ask a person's yes for a shot's generation (autonomous films; iMessage and push)")
    p.add_argument("--shot", required=True)
    p.add_argument("--takes", type=int)

    p = cli.add_command(g, "approve", _approve, "record a person's yes to an approval request")
    p.add_argument("--id", required=True, help="the approval id (appr_…)")
    p.add_argument("--by", required=True, help="who said yes")
    p.add_argument("--note", help="where the yes came from (an iMessage, say)")

    cli.add_command(g, "probe", _probe, "zero-cost check of the Cloud API key (404 with the key, 401 without)")

    pl = cli.add_group(sub, "plates", "Generated plates: the plan, the conform of an approved take, bridge mattes.",
                       description=PLATES_HELP)
    cli.add_command(pl, "plan", _plan, "which shots may generate, on what model, at what estimated cost")
    p = cli.add_command(pl, "conform", _conform, "the approved take → JPEG sequence at the film's frame rate")
    p.add_argument("--shot", required=True)
    p.add_argument("--method", choices=("mci", "blend", "dup"), default="mci",
                   help="frame-rate conversion: motion-compensated (default, as the films did), blend or duplicate")
    p.add_argument("--fit", choices=("cover", "contain", "native"), default="cover")
    p.add_argument("--frames", type=int, help="exactly this many frames (default: the take's length)")
    p.add_argument("--no-audio", action="store_true", help="skip the take's native ambience")
    p.add_argument("--prores", action="store_true", help="also write ProRes 422 HQ at the native rate (for AE)")
    p = cli.add_command(pl, "matte", _matte, "a bridge's shape as a matte revealing real frames over the film's base")
    p.add_argument("--shot", required=True, help="the shot whose approved take is the bridge")
    p.add_argument("--base", required=True, help="the film's own background at the bridge's first frame (an image)")
    p.add_argument("--real", required=True, help="the real frames: a shot id with a plate, capture:<name>, or a folder")
    p.add_argument("--duration", type=float, help="seconds of film the bridge covers (default: its own length)")
    p.add_argument("--params", help="JSON overrides for the matte's look (see slate.gen.matte.DEFAULTS)")
    p.add_argument("--preview", action="store_true", help="also write matte previews")
    p = cli.add_command(pl, "track", _track, "where a plate's land meets its sky, per sample, for code to draw on",
                        description="Track the top edge of the land in a conformed plate (the films' ridge tracker, "
                                    "with the land's colour as data: a hue range, usually from the brand palette).")
    p.add_argument("--shot", required=True)
    p.add_argument("--hue", required=True, help="the land's hue range in degrees, e.g. 165-215")
    p.add_argument("--sat", type=float, help=f"minimum saturation 0–1 (default {track.DEFAULTS['sat_min']})")
    p.add_argument("--val", type=float, help=f"minimum brightness 0–1 (default {track.DEFAULTS['val_min']})")
    p.add_argument("--points", type=int, help=f"points across the frame (default {track.DEFAULTS['points']})")
    p.add_argument("--every", type=float, help=f"seconds between samples (default {track.DEFAULTS['every']})")
    p.add_argument("--until", type=float, help="stop sampling at this many seconds")
    p.add_argument("--frames", help="track these film-rate frames instead of the shot's plate")
    p = cli.add_command(pl, "repair", _repair, "re-time a plate that drops or freezes frames (staged, then --install)",
                        description=REPAIR_HELP)
    p.add_argument("shot_pos", nargs="?", metavar="shot", help="the shot whose plate to repair")
    p.add_argument("--shot", help="the same, as a flag")
    p.add_argument("--blend", action="store_true", help="blend the two neighbouring frames instead of warping them "
                                                        "(for a frame that reads as generated)")
    p.add_argument("--band", metavar="X0,Y0,X1,Y1", help="the moving band to measure, as fractions of the frame "
                                                        f"(default {','.join(map(str, repair.BAND))})")
    p.add_argument("--install", action="store_true", help="put the staged repair in place, keeping the old sequence")
    p.add_argument("--stage", metavar="DIR", help="stage somewhere else (default plates/<shot>/plate/repair/)")
