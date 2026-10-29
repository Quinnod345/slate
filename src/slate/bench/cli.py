"""`slate bench higgsedit [plan|run|measure|review|report]`: the phase 8 benchmark from the command line."""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import cli, log, paths

ACTIONS = ("plan", "run", "measure", "review", "report")


def _film_dir(args: argparse.Namespace) -> Path | None:
    if cli.flag(args, "film"):
        return cli.film(args)
    return paths.find_film_root()


def cmd_higgsedit(args: argparse.Namespace) -> int:
    from slate.bench import higgsedit as bench  # noqa: PLC0415

    film_dir = _film_dir(args)
    folder = bench.bench_dir(film_dir, args.out)
    if args.action == "run":
        bench.refuse_run()
    if cli.dry_stop(args, {"plan": "write the Higgsedit benchmark's protocol and report",
                           "measure": f"measure the {args.arm} cut against the reference (PSNR and SSIM) and record "
                                      "the run",
                           "review": f"record {args.by}'s review of the {args.arm} cut",
                           "report": "write the benchmark's report"}[args.action] + f" in {folder}"):
        return 0
    if args.action == "plan":
        protocol = bench.plan(film_dir=film_dir, channel=args.channel, cut_s=args.length, ranges=args.ranges,
                              master=args.master, out=args.out)
        verdict = bench.report(folder)
        human = (f"Wrote the Higgsedit benchmark's protocol and report to {folder}.\n"
                 f"Channel: {protocol['channel']['label']}, {protocol['format']['name']} at {protocol['format']['aspect']}; "
                 f"cut {protocol['cut']['seconds']:g} s.\n"
                 f"Status: not run. {bench.REFUSAL}\nNext: {bench.REFUSAL_HINT}\nReport: {folder / 'REPORT.md'}")
        log.result({"folder": str(folder), "protocol": str(folder / "protocol.json"), "report": str(folder / "REPORT.md"),
                    "status": protocol["status"], "verdict": verdict["verdict"], "missing": verdict["missing"]}, human)
        return 0
    if args.action == "measure":
        if not args.arm or not args.cut or args.wall_s is None:
            from slate.errors import UsageError  # noqa: PLC0415

            raise UsageError("measure needs --arm, --cut and --wall-s.",
                             hint="slate bench higgsedit measure --arm remotion --cut <file> --wall-s <seconds>")
        run = bench.record_run(folder, arm=args.arm, cut=args.cut, wall_s=args.wall_s, master=args.master,
                               app_credits=args.app_credits, note=args.note)
        verdict = bench.report(folder)
        human = (f"Recorded a {args.arm} run: {run['wall_s']:g} s, mean SSIM {run['ssim']['mean']}, "
                 f"mean PSNR {run['psnr']['mean_db']} dB, frames {'match' if run['frames_match'] else 'DO NOT match'} "
                 f"({run['reference']}).\nVerdict so far: {verdict['verdict']}.")
        log.result({"run": run, "verdict": verdict["verdict"], "missing": verdict["missing"],
                    "reasons": verdict["reasons"]}, human)
        return 0
    if args.action == "review":
        if not args.arm or not args.verdict or not args.by:
            from slate.errors import UsageError  # noqa: PLC0415

            raise UsageError("review needs --arm, --verdict and --by.",
                             hint="slate bench higgsedit review --arm higgsedit --verdict acceptable --by <name>")
        row = bench.record_review(folder, arm=args.arm, verdict=args.verdict, by=args.by, note=args.note, cut=args.cut)
        verdict = bench.report(folder)
        log.result({"review": row, "verdict": verdict["verdict"], "missing": verdict["missing"]},
                   f"Recorded {row['by']}'s review of the {row['arm']} cut: {row['verdict']}. "
                   f"Verdict so far: {verdict['verdict']}.")
        return 0
    verdict = bench.report(folder)
    lines = [f"Verdict: {verdict['verdict']}."] + [f"  missing: {m}" for m in verdict["missing"]] + \
            [f"  {r}" for r in verdict["reasons"]] + [f"Report: {folder / 'REPORT.md'}"]
    log.result({"folder": str(folder), **verdict}, "\n".join(lines))
    return 0


def register(sub) -> None:
    """Add `slate bench higgsedit` (slate.cli.MODULES lists this module for `bench`)."""
    group = cli.add_group(sub, "bench", "benchmarks that decide whether a second engine earns its place",
                          description="Benchmarks. `slate bench higgsedit` is phase 8: Higgsedit against a Remotion "
                                      "cut-down for a social channel, kept only if it's genuinely faster at acceptable "
                                      "quality.")
    p = cli.add_command(group, "higgsedit", cmd_higgsedit,
                        "plan, measure and judge Higgsedit against a Remotion cut-down (running it needs Quinn)",
                        description="Phase 8 (decision 9): is Higgsedit genuinely faster than a Remotion cut-down for a "
                                    "social channel, at acceptable quality?\n\n"
                                    "  plan     write the protocol and the report (the default)\n"
                                    "  run      refuses: the Higgsedit arm bills unfunded Higgsfield app credits and "
                                    "needs Quinn's go\n"
                                    "  measure  record a finished cut: wall time, PSNR and SSIM against the master\n"
                                    "  review   record a person's judgement of an exact measured cut (latest by default)\n"
                                    "  report   recompute the verdict (keep, drop or not decided) and the report")
    p.add_argument("action", nargs="?", choices=ACTIONS, default="plan", help="what to do (default: plan)")
    p.add_argument("--channel", default="tiktok", help="the social channel (tiktok, instagram_reels, youtube_shorts, linkedin)")
    p.add_argument("--length", type=float, help="plan: the cut's length in seconds (default: the channel format's target)")
    p.add_argument("--cut", help="measure: the finished cut; review: the exact measured cut (default: latest in the arm)")
    p.add_argument("--ranges", help="the master's frames for the cut, like 0-900,1800-2250 (default: from the start)")
    p.add_argument("--master", help="the master to compare against (default: the film's newest in masters/)")
    p.add_argument("--out", help="where the benchmark lives (default: <film>/bench/higgsedit)")
    p.add_argument("--arm", choices=("remotion", "higgsedit"), help="measure/review: which arm")
    p.add_argument("--wall-s", dest="wall_s", type=float, help="measure: the run's wall time in seconds")
    p.add_argument("--app-credits", dest="app_credits", type=float, help="measure: Higgsfield app credits the run used")
    p.add_argument("--verdict", choices=("acceptable", "not-acceptable"), help="review: the person's judgement")
    p.add_argument("--by", help="review: who watched it")
    p.add_argument("--note", help="a note to keep with the run or the review")
