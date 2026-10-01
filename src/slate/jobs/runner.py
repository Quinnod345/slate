"""The scheduler behind `slate jobs serve`: requeue what died, start what fits, in priority order.

Exactly one runner serves the queue at a time (an fcntl lock, DATA/locks/runner.lock). Usually that's the per-user
launchd agent (`slate jobs agent install`), which keeps `slate jobs serve` alive. When the agent isn't loaded,
`slate job submit` starts a detached one-shot runner (`slate jobs serve --once`) that serves until the queue is empty,
so the queue works from any agent, including Oneiro's research slices, which are killed after ten minutes with stdin
as a pipe: they submit, poll and never wait on a prompt.

Each pass:

1. Requeue or fail running jobs whose supervisor died (a crash, a reboot, a kill). Resumable jobs go back in the queue
   (their command resumes by frame, line or act); the rest fail with the reason. Any in-flight paid request of theirs
   is settled through the vendor hooks first (slate.jobs.resume), so nothing is paid for twice.
2. Walk the queue in priority order (supervised > express > draft > autonomous, then oldest first) and start each job
   whose resources have a free slot, whose disk reservation fits above the floor, and, for renders, while the Mac isn't
   already overloaded. A job that can't start says why in `waiting`, and keeps its place: a lower-priority job can't
   take a slot a higher-priority job is waiting for.
"""

from __future__ import annotations

import collections
import contextlib
import fcntl
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Iterator

from slate import config, jsonio, log, paths, procs
from slate.errors import SlateError
from slate.jobs import disk, locks, model, proc, resume, store

LAUNCH_GRACE_S = 60.0  # a launched job gets this long for its supervisor to come up (the Mac may be loaded)
DEFAULT_MAX_LOAD_PER_CORE = 3.0
DEFAULT_KILL_GRACE_S = 10.0
DEFAULT_IDLE_EXIT_S = 600.0


def _lock_path() -> Path:
    return paths.locks_dir() / "runner.lock"


def status_file() -> Path:
    return paths.jobs_dir() / "runner.json"


@contextlib.contextmanager
def _runner_lock(block: bool) -> Iterator[bool]:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX if block else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def runner_alive() -> bool:
    """Is some runner serving the queue right now (the launchd agent or a one-shot)?"""
    path = _lock_path()
    if not path.exists():
        return False
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False


def _setting(name: str, default: float) -> float:
    value = config.load().get("jobs", {}).get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def poll_seconds() -> float:
    return max(0.05, _setting("poll_seconds", 2.0))


def kill_grace() -> float:
    return max(0.0, _setting("kill_grace_s", DEFAULT_KILL_GRACE_S))


def load_ok() -> tuple[bool, str]:
    """Renders wait while the Mac is already overloaded (jobs.max_load_per_core; 0 turns this off)."""
    per_core = _setting("max_load_per_core", DEFAULT_MAX_LOAD_PER_CORE)
    if per_core <= 0:
        return True, ""
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return True, ""
    cores = os.cpu_count() or 1
    if load1 > per_core * cores:
        return False, (f"the Mac is overloaded (load {load1:.0f} on {cores} cores); renders wait until it settles "
                       f"below {per_core * cores:.0f}")
    return True, ""


# ---------------------------------------------------------------------------------------------------------------
# Reconcile: running jobs whose supervisor died


def _handle_dead(job: dict, why: str) -> str:
    """Stop anything the dead supervisor left running, settle its paid requests, then requeue or fail the job.

    The command runs in a group of its own (slate.procs starts every child that way), so it outlives its supervisor as
    an orphan: it's found by the pid and start time the supervisor recorded, never by a bare number."""
    if job.get("cmd_pid"):
        proc.stop_tree(int(job["cmd_pid"]), grace=kill_grace(), include_root=True, stamp=job.get("cmd_start"))
    else:  # it died before it could write the command down: find the orphan by its exact command line
        for orphan in proc.orphans_of(job):
            proc.stop_tree(orphan["pid"], grace=kill_grace(), include_root=True, stamp=orphan["start"])
    pgid = job.get("pgid") or job.get("pid")
    if pgid and proc.group_is_job(pgid, job):  # never a stranger's group that reused the number
        proc.stop_group(pgid, grace=kill_grace())
    try:
        for outcome in resume.recover(job=job["id"]):
            log.info(f"{job['id']}: paid request {outcome.get('request_id') or outcome['row']}: {outcome['action']}")
    except Exception as exc:  # noqa: BLE001 - recovery is best effort; the resumed job's paid_call adopts the rest
        log.warn(f"{job['id']}: couldn't settle its in-flight paid requests ({type(exc).__name__}: {exc})")
    with store.update(job["id"]) as j:
        if j.get("state") != "running" or j.get("launch") != job.get("launch"):
            return j.get("state", "")
        j["pid"] = None
        j["phase"] = None
        j["holds"] = []
        if j.get("resumable") and j.get("attempts", 0) < j.get("max_attempts", model.DEFAULT_MAX_ATTEMPTS):
            j["state"] = "queued"
            j["queued_at"] = model.iso()
            j["reason"] = (f"{why}; requeued to resume (attempt {j.get('attempts')} of {j.get('max_attempts')} "
                           "used)")
            model.note_event(j, "requeued", why)
        else:
            j["state"] = "failed"
            j["finished_at"] = model.iso()
            if not j.get("resumable"):
                j["reason"] = f"{why}; it isn't resumable, so it wasn't run again"
            else:
                j["reason"] = f"{why}; gave up after {j.get('attempts')} attempts"
            model.note_event(j, "failed", j["reason"])
        return j["state"]


def stop_safely(job_id: str, reason: str, *, spare: int | None = None, token: str | None = None) -> bool:
    """Stop a running job because the disk is nearly full: mark it failed (resumable later) and stop its command.

    The job's supervisor sees the mark within one poll and stops the command's whole process tree itself, waiting out
    the grace period before SIGKILL. From the runner's watchdog (`spare` None) the tree also gets a SIGTERM now, so a
    busy supervisor doesn't delay it. Returns True when this call did the stopping (False when it was already stopped
    or finished).
    """
    with store.update(job_id) as job:
        if job.get("state") != "running" or job.get("stopped") or (token and job.get("launch") != token):
            return False
        job["state"] = "failed"
        job["stopped"] = "disk"
        job["phase"] = "stopping"
        job["reason"] = f"{reason}. Free some space, then `slate job retry {job_id}` resumes it."
        job["finished_at"] = model.iso()
        model.note_event(job, "stopped", reason)
        supervisor = job.get("pid")
        stamp = job.get("proc_start")
    log.warn(f"{job_id}: {reason}; stopping it safely")
    if spare is None and supervisor and proc.alive(supervisor, stamp):
        proc.signal_tree(supervisor, signal.SIGTERM, spare=supervisor)
    return True


def reconcile(job_ids: list[str] | None = None) -> dict:
    """Deal with running jobs whose supervisor is gone. Cheap enough for `slate job status` to call."""
    requeued, failed = [], []
    for job in store.by_state("running"):
        if job_ids and job["id"] not in job_ids:
            continue
        pid = job.get("pid")
        if pid is None:
            age = model.seconds_since(job.get("launched_at") or job.get("started_at"))
            if age is not None and age < LAUNCH_GRACE_S:
                continue
            why = "its supervisor never came up"
        elif not proc.alive(pid, job.get("proc_start")):
            why = f"its supervisor (pid {pid}) stopped unexpectedly"
        else:
            continue
        state = _handle_dead(job, why)
        (requeued if state == "queued" else failed).append(job["id"])
    return {"requeued": requeued, "failed": failed}


def watchdog() -> list[str]:
    """Belt and braces for the supervisors' own disk checks: stop any running job whose disk is nearly full."""
    stopped = []
    for job in store.by_state("running"):
        if job.get("phase") != "running" or job.get("stopped"):
            continue
        target = job.get("disk_path") or job.get("cwd")
        if not target:
            continue
        ok, reason, _ = disk.watch(target)
        if not ok and stop_safely(job["id"], reason):
            stopped.append(job["id"])
    return stopped


# ---------------------------------------------------------------------------------------------------------------
# Schedule: start what fits, in priority order


def _set_waiting(job: dict, reason: str | None) -> None:
    if job.get("waiting") == reason:
        return
    with store.update(job["id"]) as j:
        if j.get("state") == "queued":
            j["waiting"] = reason


def launch(job_id: str) -> bool:
    """Mark a queued job running and start its supervisor, detached. Returns False if it was no longer queued."""
    token = secrets.token_hex(6)
    with store.update(job_id) as job:
        if job.get("state") != "queued":
            return False
        job["state"] = "running"
        job["phase"] = "starting"
        job["launch"] = token
        job["attempts"] = int(job.get("attempts", 0)) + 1
        stamp = model.iso()
        job["started_at"] = stamp
        job["launched_at"] = stamp
        job["pid"] = None
        job["pgid"] = None
        job["proc_start"] = None
        job["cmd_pid"] = None  # the new attempt's command is recorded by its own supervisor
        job["cmd_pgid"] = None
        job["cmd_start"] = None
        job["waiting"] = None
        job["exit_code"] = None
        job["signal"] = None
        job["stopped"] = None
        job["not_before"] = None
        model.note_event(job, "launched", f"attempt {job['attempts']} of {job.get('max_attempts')}")
        cwd = job.get("cwd")
        log_file = job.get("log")
    workdir = cwd if cwd and Path(cwd).is_dir() else str(paths.data())
    try:
        pid = procs.spawn_detached([sys.executable, "-m", "slate.jobs.shim", job_id, token], log_path=log_file,
                                   cwd=workdir)
    except (OSError, SlateError) as exc:
        with store.update(job_id) as job:
            if job.get("launch") == token:
                job["state"] = "failed"
                job["phase"] = None
                job["finished_at"] = model.iso()
                job["reason"] = f"its supervisor couldn't be started: {exc}"
                model.note_event(job, "failed", job["reason"])
        return False
    proc.adopt_child(pid)
    with store.update(job_id) as job:
        if job.get("launch") == token and job.get("pid") is None:
            job["pid"] = pid
            job["pgid"] = pid
    return True


def inside_gateway() -> bool:
    """Is this runner itself inside the OpenClaw gateway (a one-shot runner an agent there started)? Then its jobs
    inherit the gateway's egress proxy, which turns ElevenLabs and Higgsfield away, so it leaves jobs that hold a
    vendor's slot for the launchd runner, which runs outside the gateway."""
    try:
        from slate.vendors import egress  # noqa: PLC0415
    except ImportError:
        return False
    return egress.gateway_context() is not None


def _vendor_job(job: dict) -> bool:
    from slate.jobs.client import VENDOR_RESOURCES  # noqa: PLC0415

    return bool(set(job.get("resources") or ()) & set(VENDOR_RESOURCES))


def schedule() -> list[str]:
    """One scheduling pass. Returns the ids started."""
    now = model.now()
    in_gateway = inside_gateway()
    running = store.by_state("running")
    # Jobs launched but whose supervisors haven't taken their slots and space yet still count as holding them.
    pending_slots: collections.Counter = collections.Counter()
    pending_disk: collections.Counter = collections.Counter()
    for job in running:
        if job.get("phase") == "starting":
            pending_slots.update(job.get("resources") or [])
            if job.get("disk_gb"):
                pending_disk[disk.volume(job.get("disk_path") or job.get("cwd"))] += float(job["disk_gb"])
    live_disk: collections.Counter = collections.Counter()  # live reservations per volume, counted once per pass
    for res in disk.reservations():
        live_disk[res.get("volume")] += float(res.get("gb") or 0)
    reserved: collections.Counter = collections.Counter()  # slots kept for higher-priority jobs that are waiting
    started: list[str] = []
    load_state: tuple[bool, str] | None = None
    for job in model.order(store.by_state("queued")):
        reason = None
        not_before = model.parse_iso(job.get("not_before"))
        if not_before and not_before > now:
            _set_waiting(job, f"trying again after {not_before.astimezone().strftime('%H:%M:%S')} "
                              f"({job.get('reason') or 'backoff'})")
            continue
        if in_gateway and _vendor_job(job):
            _set_waiting(job, "waiting for the launchd runner: this runner is inside the OpenClaw gateway, whose egress "
                              "proxy turns the vendors away (the owner loads it with `slate jobs agent install`)")
            continue
        for r in job.get("resources") or []:
            total = locks.slots(r)
            if total == 0:
                reason = f"{r} is paused (0 slots in slate.toml)"
                break
            free_now = locks.free(r) - pending_slots[r]
            if free_now - reserved[r] < 1:
                if free_now >= 1:
                    reason = f"waiting behind a higher-priority job for {r}"
                else:
                    reason = f"every {r} slot is busy{locks.describe_holders(r)}"
                break
        target = job.get("disk_path") or job.get("cwd")
        if reason is None and target:
            # Every job clears the floor (free space minus live reservations), even one that reserves nothing.
            vol = disk.volume(target)
            ok, why, _ = disk.check(target, float(job.get("disk_gb") or 0), pending_gb=pending_disk[vol],
                                    reserved_gb=live_disk[vol])
            if not ok:
                reason = f"waiting for disk space: this job {why}"
        if reason is None and "render" in (job.get("resources") or []):
            if load_state is None:
                load_state = load_ok()
            if not load_state[0]:
                reason = load_state[1]
        if reason:
            reserved.update(job.get("resources") or [])
            _set_waiting(job, reason)
            continue
        if launch(job["id"]):
            started.append(job["id"])
            pending_slots.update(job.get("resources") or [])
            if float(job.get("disk_gb") or 0) > 0 and target:
                pending_disk[disk.volume(target)] += float(job["disk_gb"])
    return started


def run_pass() -> dict:
    proc.reap()
    rec = reconcile()
    stopped = watchdog()
    started = schedule()
    return {"started": started, "requeued": rec["requeued"], "failed": rec["failed"], "stopped": stopped}


def _write_status(mode: str, since: str, last: dict | None = None) -> None:
    try:
        jsonio.write_atomic(status_file(), {"pid": os.getpid(), "mode": mode, "since": since,
                                            "last_pass": model.iso(), "last": last or {}})
    except OSError:
        pass


def serve(*, once: bool = False, poll: float | None = None, idle_exit: float | None = None) -> int:
    """Serve the queue. `once`: until nothing is queued or running (or nothing could start for `idle_exit` seconds),
    and only if no other runner is serving; otherwise forever, waiting for the runner lock first (the launchd agent)."""
    mode = "once" if once else "daemon"
    with _runner_lock(block=not once) as mine:
        if not mine:
            log.info("another runner is already serving the queue")
            return 0
        since = model.iso()
        log.info(f"serving the job queue ({mode}, pid {os.getpid()}, data {paths.data()})")
        idle_limit = DEFAULT_IDLE_EXIT_S if idle_exit is None else idle_exit
        last_activity = time.monotonic()
        while True:
            summary = run_pass()
            _write_status(mode, since, summary)
            if any(summary.values()):
                last_activity = time.monotonic()
                for key in ("started", "requeued", "failed", "stopped"):
                    for jid in summary[key]:
                        log.info(f"{jid}: {key}")
            if once:
                active = store.by_state("queued", "running")
                if not active:
                    break
                if not any(j["state"] == "running" for j in active):
                    if time.monotonic() - last_activity > idle_limit:
                        log.info("nothing could start for a while; the next `slate job` command starts a new runner")
                        break
                else:
                    last_activity = time.monotonic()
            time.sleep(poll_seconds() if poll is None else poll)
        log.info("the queue is idle; runner exiting")
    return 0
