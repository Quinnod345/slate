"""`slate evals list|run|report`: Slate's agent evals from the command line.

    slate evals list                      the cases (and, with --configs, the four configurations)
    slate evals run                       grade the recorded transcripts in evals/fixtures/recorded (free)
    slate evals run --fixtures synthetic  grade the hand-written transcripts that prove the graders (free)
    slate evals run --dry-run             print every command exactly as it would run
    slate evals run --live [--record]     run the agents for real: model calls only, vendors stay in replay
    slate evals report [RUN]              a run's matrix and what didn't pass (default: the latest run)

Select with --case, --group and --config (each repeatable). Exit codes: 0 when the run passed (or it was a dry run),
1 when anything failed or couldn't be checked, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slate import cli, log, paths
from slate.errors import UsageError
from slate.evals import commands, preconditions, report, runner, suite


def register(sub) -> None:
    group = cli.add_group(sub, "evals", help="agent evals: triggering, routing, negatives, the dry run, no browsers",
                          description=__doc__)
    p_list = cli.add_command(group, "list", _list, help="list the eval cases, or the configurations with --configs")
    p_list.add_argument("--configs", action="store_true", help="list the four configurations and how each was verified")
    p_list.add_argument("--preconditions", action="store_true",
                        help="also ask local services (Oneiro's engine) about pending cases, read-only")

    p_run = cli.add_command(group, "run", _run, help="grade recorded transcripts (default), print commands "
                                                     "(--dry-run), or run the agents (--live)")
    p_run.add_argument("--case", action="append", metavar="ID", help="only this case (repeatable)")
    p_run.add_argument("--group", action="append", metavar="GROUP",
                       choices=["triggering", "routing", "negative", "dry-run", "oneiro"], help="only this group")
    p_run.add_argument("--config", action="append", metavar="CONFIG", help="only this configuration (repeatable)")
    p_run.add_argument("--replay", action="store_true", help="grade transcripts on disk (the default)")
    p_run.add_argument("--fixtures", choices=["recorded", "synthetic"], default="recorded",
                       help="which transcripts replay grades (default: recorded)")
    p_run.add_argument("--record", action="store_true", help="with --live: save each run as a recorded fixture")
    p_run.add_argument("--allow-host-env", action="store_true",
                       help="run cases that make films even on configurations whose tools can't take the eval "
                            "environment (OpenClaw's Gateway); those films land in your real Slate folders")
    p_run.add_argument("--keep-going", action="store_true",
                       help="with --live: run the rest after a pair gets out of its sandbox (by default the run stops "
                            "there, so the escape isn't repeated)")
    p_run.add_argument("--preconditions", action="store_true",
                       help="ask local services about pending cases' status too (read-only)")
    p_run.add_argument("--out", metavar="DIR", help="where runs are kept (default: Slate's data folder, evals/runs)")
    p_run.add_argument("--run-id", metavar="ID", help="name this run")

    p_report = cli.add_command(group, "report", _report, help="show a run's results (default: the latest run)")
    p_report.add_argument("run", nargs="?", help="a run id or folder")
    p_report.add_argument("--out", metavar="DIR", help="where runs are kept (default: Slate's data folder)")
    p_report.add_argument("--brief", action="store_true", help="the matrix only")


def _list(args: argparse.Namespace) -> int:
    configs = suite.load_configs()
    if args.configs:
        rows = []
        for section in ("configurations", "harnesses"):
            for cid, cfg in (configs.get(section) or {}).items():
                values = {"home": str(Path.home()), "repo": str(paths.REPO), "evals_dir": str(suite.evals_dir()),
                          "probe_dir": str(suite.PROBE_DIR)}
                binary, why = commands.resolve_binary(cfg["binary"], values)
                rows.append({"id": cid, "kind": section[:-1], "label": cfg["label"], "binary": binary,
                             "installed": why is None or "using" in (why or ""), "model": cfg.get("model"),
                             "verified_on": cfg["verified"]["on"], "how": cfg["verified"]["how"],
                             "env_reaches_tools": cfg["env_reaches_tools"]})
        human = "\n".join(
            f"{r['id']:<18} {r['label']}\n{'':<18} {r['binary']}{'' if r['installed'] else '  (not installed)'}"
            f"  · verified {r['verified_on']}" + ("" if r["env_reaches_tools"] else
                                                 "  · its tools don't get the eval environment")
            for r in rows)
        log.result({"configurations": rows}, human)
        return 0
    cases = suite.load_cases(configs=configs)
    values = {"home": str(Path.home()), "repo": str(paths.REPO), "evals_dir": str(suite.evals_dir())}
    rows = []
    for case in cases:
        state = "active"
        pre = None
        if case.pending:
            pre = preconditions.evaluate(case.precondition, values, services=args.preconditions)
            state = "pending-ready" if pre["met"] else "pending"
        rows.append({"id": case.id, "group": case.group, "status": state, "title": case.title,
                     "configs": case.configs, "needs_eval_env": case.needs_eval_env, "plan_points": case.plan_points,
                     "precondition": pre})
    width = max(len(r["id"]) for r in rows) + 2
    lines = [f"{len(rows)} eval cases ({sum(r['status'] == 'active' for r in rows)} active)"]
    for r in rows:
        lines.append(f"{r['id']:<{width}}{r['group']:<12}{' '.join(r['configs'])}")
        lines.append(f"{'':<{width}}{r['title']}")
        if r["precondition"]:
            lines.append(f"{'':<{width}}{r['precondition']['summary']}")
    log.result({"cases": rows}, "\n".join(lines))
    return 0


def _pairs(args: argparse.Namespace, configs: dict):
    cases = suite.load_cases(configs=configs)
    known = set(suite.all_config_ids(configs))
    for cid in args.config or []:
        if cid not in known:
            raise UsageError(f"No eval configuration called {cid!r}.", hint=f"Known: {', '.join(sorted(known))}.")
    pairs = suite.select(cases, ids=args.case, groups=args.group, configs=args.config)
    if not pairs:
        raise UsageError("That selection has no (case, configuration) pairs in it.",
                               hint="`slate evals list` shows every case and the configurations it runs on.")
    return pairs


def _run(args: argparse.Namespace) -> int:
    mode = runner.require_mode(bool(cli.flag(args, "dry_run")), bool(cli.flag(args, "live")), args.replay,
                               args.record)
    configs = suite.load_configs()
    pairs = _pairs(args, configs)
    out = Path(args.out).expanduser() if args.out else None
    if mode == "dry-run":
        result = runner.dry_run(pairs, configs, run_id=args.run_id, out=out, allow_host_env=args.allow_host_env,
                                services=args.preconditions)
        header = (f"evals dry run: {len(pairs)} run(s); nothing runs. Each would live in {result['run_dir']}/"
                  "<case>/<config>/.")
        log.result({k: v for k, v in result.items() if k != "text"}, f"{header}\n\n{result['text']}")
        return 0
    if mode == "live":
        log.warn(f"live evals: {len(pairs)} agent run(s). They cost model calls; vendors stay in replay "
                 "(SLATE_FAKE_VENDORS=1, zero-cap budget).")
        summary = runner.live(pairs, configs, run_id=args.run_id, out=out, record=args.record,
                              allow_host_env=args.allow_host_env, services=args.preconditions,
                              keep_going=args.keep_going)
    else:
        summary = runner.replay(pairs, configs, fixtures=args.fixtures, run_id=args.run_id, out=out,
                                services=args.preconditions)
    log.result(summary, report.format_text(summary))
    return 0 if summary["overall"] == "PASS" else 1


def _report(args: argparse.Namespace) -> int:
    summary = report.load(args.run, Path(args.out).expanduser() if args.out else None)
    log.result(summary, report.format_text(summary, details=not args.brief))
    return 0 if summary["overall"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli.main(["evals", *sys.argv[1:]]))
