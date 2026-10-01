"""A job's supervisor: runs one job detached, holds its slots and space, and writes down how it ended.

    python -m slate.jobs.shim <job-id> <launch-token>        (the runner starts this; never run it by hand)

The runner starts the supervisor in its own session with stdin closed and output going to the job's log, so the job
outlives whoever submitted it: a dying agent, a closed Terminal, a rate-limited session. The supervisor then:

1. takes the job's resource slots and disk reservation (handing the job back to the queue if someone beat it to them);
2. settles any paid request an earlier attempt left in flight (slate.jobs.resume), when this is a resumed attempt;
3. runs the command as `caffeinate -i nice -n 5 <argv>` in its own process group, so the Mac doesn't idle-sleep under
   it and your own work stays responsive; the command learns it's a queued job from SLATE_JOB_ID, SLATE_JOB_ATTEMPT,
   SLATE_JOB_RESUME and SLATE_JOB_HOLDS;
4. watches free disk space while it runs, and stops the command and everything it started (its whole process tree:
   slate.procs gives every child its own group) when the disk nears the stop line or the job is cancelled;
5. records the exit: done, failed with the reason, or back in the queue for later (exit 5 busy or 75 try-later);
6. releases everything and makes sure a runner is serving whatever is still queued.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import shlex
import signal
import sys
import threading
import time
from pathlib import Path

from slate import config, log, paths, procs
from slate.errors import Refused, SlateError
from slate.jobs import disk, locks, model, proc, resume, runner, store

DEFAULT_NICE = 5
MAX_RETRY_AFTER_S = 3600  # a vendor's Retry-After is honoured up to an hour; past that, the backoff is the queue's


def _say(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [slate job] {text}", flush=True)


def _nice_level() -> int:
    value = config.load().get("jobs", {}).get("nice", DEFAULT_NICE)
    try:
        return max(0, min(19, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_NICE


def run_argv(argv: list[str]) -> list[str]:
    """What actually runs: the command under `caffeinate -i` (no idle sleep) and `nice` (the Mac stays usable).

    A leading "slate" runs this checkout's CLI with this Python, so the launchd agent's bare PATH doesn't matter.
    """
    cmd = list(argv)
    if cmd and cmd[0] == "slate":
        cmd = [sys.executable, "-m", "slate", *cmd[1:]]
    level = _nice_level()
    nice = procs.which("nice")
    if level and nice:
        cmd = [nice, "-n", str(level), *cmd]
    caffeinate = procs.which("caffeinate")
    if caffeinate:
        cmd = [caffeinate, "-i", *cmd]
    return cmd


def _hand_back(job_id: str, token: str, reason: str) -> None:
    """Put the job back in the queue without counting an attempt (it never ran)."""
    with store.update(job_id) as job:
        if job.get("launch") != token or job.get("state") != "running":
            return
        job["state"] = "queued"
        job["phase"] = None
        job["pid"] = None
        job["pgid"] = None
        job["holds"] = []
        job["attempts"] = max(0, int(job.get("attempts", 1)) - 1)
        job["queued_at"] = model.iso()
        job["waiting"] = reason
        model.note_event(job, "handed back", reason)
    _say(f"handed back to the queue: {reason}")


def _fail_before_start(job_id: str, token: str, reason: str) -> None:
    with store.update(job_id) as job:
        if job.get("launch") != token or job.get("state") != "running":
            return
        job["state"] = "failed"
        job["phase"] = None
        job["holds"] = []
        job["reason"] = reason
        job["finished_at"] = model.iso()
        model.note_event(job, "failed", reason)
    _say(f"failed before starting: {reason}")


def _child_env(job: dict) -> None:
    """Set this process's environment to the job's captured settings (the child inherits it through procs.run).

    SLATE_DATA stays the folder this job's file lives in: that's the queue the supervisor reports to.
    """
    data_dir = str(paths.data())
    for key in list(os.environ):
        if key in model.ENV_KEEP or any(key.startswith(p) for p in model.ENV_KEEP_PREFIXES):
            del os.environ[key]
    for key, value in (job.get("env") or {}).items():
        os.environ[key] = value
    if job.get("context"):
        os.environ["SLATE_CONTEXT"] = job["context"]
    os.environ["SLATE_JOB_ID"] = job["id"]
    os.environ["SLATE_JOB_ATTEMPT"] = str(job.get("attempts", 1))
    os.environ["SLATE_JOB_RESUME"] = "1" if int(job.get("attempts", 1)) > 1 else "0"
    os.environ["SLATE_JOB_HOLDS"] = ",".join(job.get("resources") or [])
    os.environ["PYTHONUNBUFFERED"] = "1"
    if job.get("film_dir") and not os.environ.get("SLATE_FILM"):
        os.environ["SLATE_FILM"] = job["film_dir"]
    os.environ["SLATE_DATA"] = data_dir


def _record_command(job_id: str, token: str) -> bool:
    """Note the command's pid, group and start time once it's running, so a supervisor that dies can't orphan it: the
    runner and `slate job cancel` find it from the job file. True once it's recorded (or there's nothing to record)."""
    for kid in proc.children(os.getpid()):  # (proc.table leaves out the ps that lists them)
        with store.update(job_id) as job:
            if job.get("launch") == token:
                job["cmd_pid"], job["cmd_pgid"], job["cmd_start"] = kid["pid"], kid["pgid"], kid["start"]
        return True
    return False


def _outcome(job: dict, rc: int) -> None:
    """Decide how a job that ran to its end finished (only when nobody else decided first: cancel, disk stop)."""
    attempts = int(job.get("attempts", 1))
    most = int(job.get("max_attempts", model.DEFAULT_MAX_ATTEMPTS))
    if rc == 0:
        job["state"] = "done"
        job["reason"] = None
    elif rc in model.RETRY_LATER_CODES and attempts < most:
        wait_s = min(60 * 2 ** (attempts - 1), 900)
        noted = job.pop("try_later", None) or {}  # the command's own note (slate.cli, on exit 75): the vendor's wait
        asked = noted.get("retry_after_s") if isinstance(noted, dict) else None
        if isinstance(asked, (int, float)) and asked > wait_s:
            wait_s = int(min(math.ceil(asked), MAX_RETRY_AFTER_S))
        job["state"] = "queued"
        job["queued_at"] = model.iso()
        job["not_before"] = model.iso(model.now() + _dt.timedelta(seconds=wait_s))
        what = "busy" if rc == 5 else (
            "a temporary failure" + (f": {noted['reason']}" if isinstance(noted, dict) and noted.get("reason") else ""))
        job["reason"] = f"exited with {rc} ({what}); back in the queue to try again in {wait_s}s"
    elif rc == model.SPEND_REFUSED_CODE:
        job["state"] = "failed"
        job["reason"] = ("spend refused (exit 4): it's waiting for a person to raise the budget, approve the call or "
                         f"top up the balance; then `slate job retry {job['id']}`")
    elif rc < 0 or proc.signal_name(rc):
        job["state"] = "failed"
        job["reason"] = f"stopped by {proc.signal_name(rc)}"
    else:
        job["state"] = "failed"
        job["reason"] = f"exited with {rc}; the log says why: slate job log {job['id']}"
    if job["state"] in model.FINISHED:
        job["finished_at"] = model.iso()
    model.note_event(job, job["state"], job.get("reason"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m slate.jobs.shim <job-id> <launch-token> (the queue starts this, not people)",
              file=sys.stderr)
        return 2
    job_id, token = args
    got_signal: dict[str, int | None] = {"sig": None}

    def on_signal(signum, _frame) -> None:
        # The group's SIGTERM (cancel, disk stop) is for the command; the supervisor stays to write the result.
        got_signal["sig"] = signum

    try:
        with store.update(job_id) as job:
            if job.get("state") != "running" or job.get("launch") != token:
                _say(f"{job_id}: this launch is stale (the queue moved on); exiting")
                return 0
            job["pid"] = os.getpid()
            job["pgid"] = os.getpgid(0)
            job["proc_start"] = proc.own_stamp()
            snapshot = dict(job)
    except SlateError as exc:
        _say(f"{job_id}: can't read the job ({exc.message}); exiting")
        return 1
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, on_signal)

    held: list[locks.Held] = []
    reservation: disk.Reservation | None = None
    try:
        for resource in snapshot.get("resources") or []:
            h = locks.try_acquire(resource, owner=f"job {job_id} ({snapshot['kind']})", job=job_id)
            if h is None:
                _hand_back(job_id, token, f"the {resource} slot was taken{locks.describe_holders(resource)} just "
                                          "before this job could start")
                return 0
            held.append(h)
        target = snapshot.get("disk_path") or snapshot.get("cwd")
        if float(snapshot.get("disk_gb") or 0) > 0:
            try:
                reservation = disk.acquire(target, float(snapshot["disk_gb"]), owner=f"job {job_id}", job=job_id)
            except Refused as exc:
                _hand_back(job_id, token, exc.message)
                return 0
        cwd = snapshot.get("cwd")
        if not cwd or not Path(cwd).is_dir():
            _fail_before_start(job_id, token, f"its working folder {cwd} no longer exists")
            return 0
        current = store.load(job_id)
        if current.get("state") != "running" or current.get("launch") != token:
            _say(f"{job_id} was {current.get('state')} before it started; not running it")
            return 0
        if int(snapshot.get("attempts", 1)) > 1:
            try:
                for outcome in resume.recover(job=job_id):
                    _say(f"earlier paid request {outcome.get('request_id') or outcome['row']}: {outcome['action']}")
            except Exception as exc:  # noqa: BLE001 - the command's own paid_call adopts whatever is left
                _say(f"couldn't settle earlier paid requests ({type(exc).__name__}: {exc})")
        _child_env(snapshot)
        command = run_argv(snapshot["argv"])
        with store.update(job_id) as job:
            if job.get("launch") != token or job.get("state") != "running":
                _say(f"{job_id} was {job.get('state')} before it started; not running it")
                return 0
            job["phase"] = "running"
            job["run_argv"] = command
            job["holds"] = [h.label for h in held]
            model.note_event(job, "running", f"pid {os.getpid()}, slots: {', '.join(h.label for h in held) or 'none'}")
        _say(f"{job_id} attempt {snapshot.get('attempts')}/{snapshot.get('max_attempts')}: {snapshot['kind']} "
             f"in {cwd}")
        _say("running: " + " ".join(shlex.quote(a) for a in command))

        result: dict = {}

        def work() -> None:
            try:
                result["rc"] = procs.run(command, cwd=cwd, capture=False).returncode
            except SlateError as exc:
                _say(f"couldn't start the command: {exc.message}")
                result["rc"] = 127
            except Exception as exc:  # noqa: BLE001
                _say(f"couldn't start the command: {type(exc).__name__}: {exc}")
                result["rc"] = 127

        worker = threading.Thread(target=work, name=f"job-{job_id}", daemon=True)
        started = time.monotonic()
        worker.start()
        poll = runner.poll_seconds()
        stopping = False
        recorded = False
        for _ in range(20):  # write the command down at once: a supervisor that dies after this can't orphan it
            worker.join(0.05)
            if not worker.is_alive() or (recorded := _record_command(job_id, token)):
                break
        while worker.is_alive():
            worker.join(poll)
            if not worker.is_alive():
                break
            if not recorded:
                recorded = _record_command(job_id, token)
            if stopping:
                continue
            why = None
            if target:
                ok, reason, _free = disk.watch(target)
                if not ok:
                    runner.stop_safely(job_id, reason, spare=os.getpid(), token=token)  # marks it; the stop is ours
                    why = reason
            if why is None:
                state = store.load(job_id)
                if state.get("stopped") and state.get("launch") == token:
                    why = f"{state.get('reason') or state.get('stopped')}"  # cancelled, or stopped by the runner
            if why is not None:
                stopping = True
                _say(f"stopping the command and everything it started: {why}")
                how = proc.stop_tree(os.getpid(), grace=runner.kill_grace(), spare=os.getpid())
                if how == "killed":
                    _say("the command didn't stop on SIGTERM within the grace period; it was killed")
        worker.join()
        rc = int(result.get("rc", 127))
        elapsed = time.monotonic() - started
        with store.update(job_id) as job:
            if job.get("launch") != token:
                return 0
            job["exit_code"] = rc
            job["signal"] = proc.signal_name(rc)
            job["phase"] = None
            job["holds"] = []
            if job.get("state") == "running":
                _outcome(job, rc)
            elif not job.get("finished_at"):
                job["finished_at"] = model.iso()
            # Logged before the new state is saved, so anyone who sees the state also finds this line in the log.
            _say(f"{job_id} {job['state']} after {model.human_duration(elapsed)} (exit {rc})"
                 + (f": {job['reason']}" if job.get("reason") and job["state"] != "done" else ""))
        return 0
    finally:
        for h in held:
            try:
                h.release()
            except OSError:
                pass
        if reservation is not None:
            reservation.release()
        try:
            if store.by_state("queued"):
                from slate.jobs import client  # noqa: PLC0415

                client.ensure_runner()
        except Exception as exc:  # noqa: BLE001 - the next `slate job` command starts a runner anyway
            log.warn(f"couldn't start a runner for the rest of the queue: {exc}")


if __name__ == "__main__":  # pragma: no cover - started by the runner
    sys.exit(main())
