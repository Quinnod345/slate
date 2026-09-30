"""`slate job …`, `slate jobs …`, `slate budget …` and `slate ledger …`.

    slate job submit --kind render --resource render --disk-gb 12 -- slate render --act 2
    slate job status <id>          slate job wait <id> --timeout 540      slate job log <id> --follow
    slate job cancel <id>          slate job retry <id>                   slate job list
    slate jobs serve [--once]      slate jobs status | locks | disk | prune
    slate jobs agent install | uninstall | status | plist

Every command answers in plain words, or as one JSON object with --json, and never prompts.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from slate import jsonio, log, paths
from slate.cli import add_command, add_group, flag
from slate.errors import UsageError
from slate.jobs import agent, client, disk, locks, model, runner, store

EPILOG = ("Exit codes: 0 ok, 1 refused or the job failed, 2 usage, 3 missing prerequisite, 4 spend refused, "
          "5 busy (still running, or a slot is taken).")


def _film_arg(args: argparse.Namespace):
    """The film for a job: --film, SLATE_FILM, or the film folder you're in; None when there isn't one."""
    ref = flag(args, "film") or os.environ.get("SLATE_FILM")
    if ref:
        return paths.film_dir(ref)
    return paths.find_film_root()


def _line(job: dict) -> str:
    elapsed = job.get("elapsed_s")
    if elapsed is None and job.get("state") == "running":
        elapsed = model.seconds_since(job.get("started_at"))
    bits = [job["id"], f"{job.get('state', '?'):<9}", f"{job.get('kind', '?'):<10}", f"{job.get('priority', '?'):<10}"]
    if job.get("state") == "running":
        bits.append(model.human_duration(elapsed))
        bits.append(f"attempt {job.get('attempts')}/{job.get('max_attempts')}")
    if job.get("film"):
        bits.append(f"film {job['film']}")
    if job.get("state") == "queued" and job.get("waiting"):
        bits.append(f"waiting: {job['waiting']}")
    elif job.get("state") in ("failed", "cancelled") and job.get("reason"):
        bits.append(job["reason"])
    return "  ".join(bits)


def _describe(job: dict) -> str:
    lines = [_line(job)]
    lines.append(f"  command: {' '.join(job.get('argv') or [])}")
    if job.get("resources"):
        lines.append(f"  holds:   {', '.join(job['resources'])}" + (f" (disk {job['disk_gb']:g} GB)"
                                                                   if job.get("disk_gb") else ""))
    if job.get("reason") and job.get("state") not in ("failed", "cancelled"):
        lines.append(f"  note:    {job['reason']}")
    if job.get("exit_code") is not None:
        lines.append(f"  exit:    {job['exit_code']}" + (f" ({job['signal']})" if job.get("signal") else ""))
    lines.append(f"  log:     {job.get('log')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# slate job …


def cmd_submit(args: argparse.Namespace) -> int:
    argv = list(args.command or [])
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise UsageError("Give the command after --, e.g. slate job submit --kind render -- slate render --act 2")
    film = _film_arg(args)
    job_id = client.submit(args.kind, argv, film=film, resources=args.resource or (), priority=args.priority,
                           disk_gb=args.disk_gb, cwd=args.cwd, resumable=not args.no_resume,
                           max_attempts=args.max_attempts, note=args.note, start_runner=False)
    served = client.ensure_runner()
    job = store.load(job_id)
    out = {"id": job_id, "state": job["state"], "priority": job["priority"], "runner": served, "log": job["log"],
           "poll": f"slate job status {job_id}"}
    human = [f"Queued {job_id} ({job['kind']}, {job['priority']} priority).",
             "  runner: " + ("a runner is already serving the queue" if served == "running"
                             else "started a detached one-shot runner (the launchd agent isn't loaded)"),
             f"  poll:   slate job status {job_id}", f"  log:    slate job log {job_id}"]
    if job.get("priority_requested"):
        human.insert(1, f"  (asked for {job['priority_requested']}; a job never outranks its film's mode)")
    log.result(out, "\n".join(human))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    job = client.status(args.id)
    log.result(job, _describe(job))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    job = client.wait(args.id, timeout=args.timeout, poll=args.poll)
    log.result(job, _describe(job))
    return 0 if job["state"] == "done" else 1


def cmd_log(args: argparse.Namespace) -> int:
    model.check_id(args.id)
    path = client.log_path(args.id)
    if args.path:
        log.result({"id": args.id, "log": str(path)}, str(path))
        return 0
    if not args.follow:
        text = client.tail(args.id, args.lines)
        log.result({"id": args.id, "log": str(path), "text": text}, text or f"(the log at {path} is empty so far)")
        return 0
    if log.json_mode():
        raise UsageError("--follow streams text; drop --json (or poll `slate job log <id> --json`).")
    print(client.tail(args.id, args.lines), flush=True)
    offset = path.stat().st_size if path.exists() else 0
    while True:
        if path.exists() and path.stat().st_size > offset:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset += len(chunk)
            print(chunk.decode("utf-8", "replace"), end="", flush=True)
        state = store.load(args.id).get("state")
        if state in model.FINISHED:
            return 0
        time.sleep(0.5)


def cmd_cancel(args: argparse.Namespace) -> int:
    client.cancel(args.id, grace=args.grace)
    job = store.load(args.id)
    log.result({"id": args.id, "state": job["state"], "reason": job.get("reason")},
               f"{args.id}: {job['state']}" + (f" ({job['reason']})" if job.get("reason") else ""))
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    job = client.retry(args.id)
    log.result({"id": args.id, "state": job["state"]}, f"{args.id}: back in the queue ({job['state']}).")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    film = _film_arg(args) if (flag(args, "film") or os.environ.get("SLATE_FILM")) else None
    film_id = None
    if film:
        film_id = (jsonio.read(film / "film.json", default={}) or {}).get("id") or film.name
    jobs = client.list_jobs(states=args.state or None, film=film_id, limit=None if args.all else args.limit)
    decorated = [client._decorate(j) for j in jobs]
    human = "\n".join(_line(j) for j in decorated) if decorated else "No jobs."
    log.result({"jobs": decorated, "count": len(decorated)}, human)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate jobs …


def cmd_serve(args: argparse.Namespace) -> int:
    return runner.serve(once=args.once, poll=args.poll)


def cmd_jobs_status(args: argparse.Namespace) -> int:
    info = client.summary()
    info["agent"] = agent.status() if not args.no_agent else None
    counts = ", ".join(f"{k} {v}" for k, v in info["counts"].items() if v) or "empty"
    lines = [f"queue: {counts}",
             "runner: " + ("serving" if info["runner"] else "not running (the next `slate job` command starts one)")]
    if info.get("agent"):
        a = info["agent"]
        lines.append(f"launchd agent {a['label']}: " + ("loaded" if a["loaded"] else
                                                          ("installed, not loaded" if a["installed"] else
                                                           "not installed (slate jobs agent install)")))
    if info["next"]:
        lines.append(f"next up: {info['next']}")
    log.result(info, "\n".join(lines))
    return 0


def cmd_locks(args: argparse.Namespace) -> int:
    rows = locks.status()
    lines = []
    for r in rows:
        who = ", ".join(f"#{h['slot']} {h.get('job') or h.get('owner') or 'pid ' + str(h.get('pid'))}"
                        for h in r["holders"])
        lines.append(f"{r['resource']:<17} {r['free']}/{r['slots']} free" + (f"  held: {who}" if who else ""))
    log.result({"resources": rows}, "\n".join(lines))
    return 0


def cmd_disk(args: argparse.Namespace) -> int:
    info = disk.summary(args.path)
    lines = [f"{info['free_gb']:.1f} GB free on the disk holding {info['path']}",
             f"reserved by running jobs: {info['reserved_gb']:.1f} GB",
             f"floor: jobs start only above {info['floor_gb']:g} GB; running jobs stop safely under {info['stop_gb']:g} GB"]
    log.result(info, "\n".join(lines))
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    cutoff = args.days * 86400
    gone = []
    for job in store.all_jobs():
        if job.get("state") not in model.FINISHED:
            continue
        age = model.seconds_since(job.get("finished_at") or job.get("updated_at"))
        if age is None or age < cutoff:
            continue
        gone.append(job["id"])
        if not flag(args, "dry_run"):
            with store.queue_lock():
                store.job_file(job["id"]).unlink(missing_ok=True)
            Path(job.get("log") or model.log_path(job["id"])).unlink(missing_ok=True)
    verb = "would remove" if flag(args, "dry_run") else "removed"
    log.result({"removed": gone, "dry_run": bool(flag(args, "dry_run"))},
               f"{verb} {len(gone)} finished job(s) older than {args.days:g} days")
    return 0


def cmd_agent_plist(args: argparse.Namespace) -> int:
    xml = agent.render_bytes().decode("utf-8")
    log.result({"label": agent.label(), "plist": str(agent.plist_path()), "content": agent.render(), "xml": xml}, xml)
    return 0


def cmd_agent_install(args: argparse.Namespace) -> int:
    out = agent.install(dry_run=bool(flag(args, "dry_run")), yes=args.yes)
    lines = [("Would install" if out["dry_run"] else "Installed") + f" the job agent {out['label']}:",
             f"  plist: {out['plist']}"] + [f"  run:   {c}" for c in out["commands"]]
    if out["dry_run"]:
        lines.append(out["note"])
    log.result(out, "\n".join(lines))
    return 0


def cmd_agent_uninstall(args: argparse.Namespace) -> int:
    out = agent.uninstall(dry_run=bool(flag(args, "dry_run")), yes=args.yes)
    lines = [("Would remove" if out["dry_run"] else "Removed") + f" the job agent {out['label']}:",
             f"  plist: {out['plist']}"] + [f"  run:   {c}" for c in out["commands"]]
    log.result(out, "\n".join(lines))
    return 0


def cmd_agent_status(args: argparse.Namespace) -> int:
    info = agent.status()
    state = "loaded" if info["loaded"] else ("installed, not loaded" if info["installed"] else "not installed")
    lines = [f"{info['label']}: {state}" + (f" (pid {info['pid']})" if info.get("pid") else ""),
             "runner: " + ("serving the queue" if info["runner_serving"] else "none serving right now"),
             f"plist: {info['plist']}", f"log:   {info['log']}"]
    log.result(info, "\n".join(lines))
    return 0


# ---------------------------------------------------------------------------------------------------------------


def register(sub) -> None:
    job = add_group(sub, "job", "one job in the queue: submit, status, wait, log, cancel, retry, list",
                    description="Long work runs as detached jobs: they outlive the agent that started them, run under "
                                "caffeinate, resume after a crash, and wait their turn for render slots, After "
                                "Effects and API limits.", epilog=EPILOG)

    p = add_command(job, "submit", cmd_submit, "queue a command as a detached job; prints its id at once",
                    epilog="Example: slate job submit --kind render --resource render --disk-gb 12 -- "
                           "slate render --act 2\n" + EPILOG)
    p.add_argument("--kind", required=True, help="what it is: render, master, whisper, tts, music, generation, ...")
    p.add_argument("--resource", action="append", choices=model.RESOURCES,
                   help="a resource it needs a slot of (repeatable)")
    p.add_argument("--priority", choices=model.PRIORITIES, default="supervised",
                   help="default supervised; never above the film's mode")
    p.add_argument("--disk-gb", dest="disk_gb", type=float, default=0.0, help="what it will write, in GB")
    p.add_argument("--cwd", help="working folder (default: the film's folder, or here)")
    p.add_argument("--no-resume", dest="no_resume", action="store_true",
                   help="the command can't safely run again after a crash: fail instead of requeueing")
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=model.DEFAULT_MAX_ATTEMPTS)
    p.add_argument("--note", help="a short note shown with the job")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command and its arguments")

    p = add_command(job, "status", cmd_status, "a job's state, attempt, reason and log path", epilog=EPILOG)
    p.add_argument("id")

    p = add_command(job, "wait", cmd_wait, "wait for a job to finish (exit 0 done, 1 failed, 5 still running)",
                    epilog=EPILOG)
    p.add_argument("id")
    p.add_argument("--timeout", type=float, help="give up waiting after this many seconds (the job keeps going)")
    p.add_argument("--poll", type=float, default=1.0, help=argparse.SUPPRESS)

    p = add_command(job, "log", cmd_log, "the end of a job's log", epilog=EPILOG)
    p.add_argument("id")
    p.add_argument("--lines", type=int, default=40)
    p.add_argument("--follow", action="store_true", help="keep printing until the job finishes")
    p.add_argument("--path", action="store_true", help="just print the log's path")

    p = add_command(job, "cancel", cmd_cancel,
                    "cancel a job (SIGTERM to its process group, SIGKILL after the grace period)", epilog=EPILOG)
    p.add_argument("id")
    p.add_argument("--grace", type=float, help="seconds between SIGTERM and SIGKILL (default jobs.kill_grace_s, 10)")

    p = add_command(job, "retry", cmd_retry, "send a failed or cancelled job back to the queue (it resumes)",
                    epilog=EPILOG)
    p.add_argument("id")

    p = add_command(job, "list", cmd_list, "jobs on this Mac: running and queued first, then recent finished ones",
                    epilog=EPILOG)
    p.add_argument("--state", action="append", choices=model.STATES)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--all", action="store_true", help="every job, however old")

    jobs = add_group(sub, "jobs", "the queue itself: its runner, launchd agent, locks and disk",
                     description="The machine-wide job queue: one runner serves it (the launchd agent, or a one-shot "
                                 "runner started on demand).", epilog=EPILOG)
    p = add_command(jobs, "serve", cmd_serve, "run the scheduler (the launchd agent runs this; --once drains the queue)",
                    epilog=EPILOG)
    p.add_argument("--once", action="store_true",
                   help="serve until nothing is queued or running, and only if no other runner is serving")
    p.add_argument("--poll", type=float, help="seconds between passes (default jobs.poll_seconds, 2)")

    p = add_command(jobs, "status", cmd_jobs_status, "the queue at a glance: counts, runner, agent, next up",
                    epilog=EPILOG)
    p.add_argument("--no-agent", dest="no_agent", action="store_true", help=argparse.SUPPRESS)
    add_command(jobs, "locks", cmd_locks, "each resource's slots and who holds them", epilog=EPILOG)
    p = add_command(jobs, "disk", cmd_disk, "free space, reservations, the floor and the stop line", epilog=EPILOG)
    p.add_argument("--path", help="a folder on the disk to check (default: the films folder)")
    p = add_command(jobs, "prune", cmd_prune, "remove finished jobs (and their logs) older than N days",
                    epilog=EPILOG)
    p.add_argument("--days", type=float, default=30.0)

    agent_sub = add_group(jobs, "agent", "the per-user launchd agent that keeps the runner alive",
                          description=agent.__doc__.split("\n\n")[0], epilog=EPILOG)
    p = add_command(agent_sub, "install", cmd_agent_install,
                    "write the plist and load it (launchctl bootstrap gui/<uid>); --dry-run shows it", epilog=EPILOG)
    p.add_argument("--yes", action="store_true", help="the person agreed (needed when an agent runs this)")
    p = add_command(agent_sub, "uninstall", cmd_agent_uninstall,
                    "unload it (launchctl bootout gui/<uid>/<label>) and remove the plist", epilog=EPILOG)
    p.add_argument("--yes", action="store_true", help="the person agreed (needed when an agent runs this)")
    add_command(agent_sub, "status", cmd_agent_status, "installed? loaded? serving?", epilog=EPILOG)
    add_command(agent_sub, "plist", cmd_agent_plist, "print the plist it would install", epilog=EPILOG)

    from slate.jobs import spend_cli  # noqa: PLC0415

    spend_cli.register(sub)
