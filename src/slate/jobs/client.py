"""The queue from the caller's side: submit, status, wait, cancel, retry, list.

    from slate.jobs import client
    job_id = client.submit("render", ["slate", "render", "--act", "2"], film=film_dir, resources=("render",),
                           disk_gb=12)
    client.status(job_id)["state"]        # queued | running | done | failed | cancelled
    client.wait(job_id, timeout=540)      # Busy (exit 5) if it's still going after 540 s

Submitting never blocks and never prompts. If no runner is serving the queue (the launchd agent isn't loaded), it
starts a detached one-shot runner, so a research slice that's killed after ten minutes can submit, exit, and poll
later: the job keeps going without it.

A film's mode sets its jobs' priority (supervised > express > draft > autonomous); a caller can lower it, never raise
it above the film's mode.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable

from slate import paths, procs
from slate.errors import Busy, Refused, UsageError
from slate.jobs import model, proc, runner, store


SUPERVISOR_EXIT_S = 15.0  # after its command stops, a supervisor on a busy Mac gets this long to record the exit


def _resolve_film(film) -> tuple[str | None, Path | None, str | None]:
    """(film id, folder, mode). The mode is the one Slate judges the film in: a film made autonomous stays autonomous
    on Slate's own record whatever its film.json says now (slate.state.autonomy), so its jobs keep autonomous priority
    and never get After Effects."""
    if film is None:
        return None, None, None
    film_dir = paths.film_dir(film)
    from slate import jsonio  # noqa: PLC0415

    fj = jsonio.read(film_dir / "film.json", default={}) or {}
    mode = fj.get("mode") if fj.get("mode") in model.PRIORITIES else None
    try:
        from slate.state import autonomy  # noqa: PLC0415 (state is optional for the queue)
    except ImportError:
        autonomy = None
    if autonomy is not None and autonomy.is_locked(film_dir, fj):
        mode = "autonomous"
    return fj.get("id") or film_dir.name, film_dir, mode


# Job resources that call a vendor, and the host each one reaches.
VENDOR_RESOURCES = {"elevenlabs_tts": "elevenlabs", "elevenlabs_music": "elevenlabs", "higgsfield": "higgsfield"}


def _gateway() -> bool:
    """Is this process inside the OpenClaw gateway (and not a queued job)? There, Slate's vendor work belongs to the
    launchd runner, outside the gateway's egress proxy."""
    try:
        from slate.vendors import egress  # noqa: PLC0415
    except ImportError:
        return False
    return egress.gateway_context() is not None


def _live() -> bool:
    from slate.replay import fake_vendors  # noqa: PLC0415

    return not fake_vendors()


def submit(kind: str, argv: list[str], *, film=None, resources: Iterable[str] = (), priority: str = "supervised",
           disk_gb: float = 0.0, cwd=None, resumable: bool = True, max_attempts: int = model.DEFAULT_MAX_ATTEMPTS,
           note: str | None = None, start_runner: bool = True) -> str:
    """Queue a job and return its id at once. The job runs detached, whoever submitted it.

    `resumable` says the command is safe to run again after a crash (Slate's own long commands resume by frame, line
    or act). `disk_gb` is what the job will write; it won't start unless that fits above the free-space floor.
    """
    if isinstance(argv, (str, bytes)):
        raise UsageError("A job's command is an argv list, never a shell string.")
    argv = [str(a) for a in argv]
    if priority not in model.PRIORITIES:
        raise UsageError(f"Unknown priority {priority!r}; one of {', '.join(model.PRIORITIES)}.")
    film_id, film_dir, film_mode = _resolve_film(film)
    effective = model.lower_of(priority, film_mode) if film_mode else priority
    wanted = model.check_resources(resources)
    if "ae" in wanted and "autonomous" in (effective, film_mode):
        raise Refused(
            "Autonomous films never use After Effects: Adobe's terms bar operations a person didn't start, and a queued "
            "job is never a person.",
            hint="Master autonomous films with ffmpeg (slate master --ffmpeg). Once the owner approves the held master, "
                 "they start its AE finish themselves in their own Terminal: slate master --ae --go (not --detach).",
        )
    workdir = Path(cwd).expanduser().resolve() if cwd else (film_dir or Path.cwd().resolve())
    if not workdir.is_dir():
        raise UsageError(f"The job's working folder {workdir} doesn't exist.")
    job = model.new_job(
        kind, argv, cwd=workdir, film=film_id, film_dir=film_dir, film_mode=film_mode,
        resources=wanted, priority=effective,
        priority_requested=priority if effective != priority else None, disk_gb=float(disk_gb),
        disk_path=film_dir or workdir, resumable=resumable, max_attempts=max_attempts, note=note,
        context=procs.agent_context(), live=_live(), env=model.captured_env(),
    )
    store.create(job)
    if start_runner:
        ensure_runner()
    return job["id"]


def ensure_runner() -> str:
    """Make sure something serves the queue: "running" if a runner (or the launchd agent) already is, "started" if
    this started a detached one-shot runner, "waiting" if the queue waits for the launchd runner instead.

    Inside the OpenClaw gateway a one-shot runner would inherit the gateway's egress proxy, which turns the vendors
    away, so it isn't started there while a queued job holds a vendor's slot: those wait for the launchd runner
    (`slate jobs agent install`, the owner's yes), which runs outside the gateway."""
    if runner.runner_alive():
        return "running"
    if _gateway() and any(set(j.get("resources") or ()) & set(VENDOR_RESOURCES) for j in store.by_state("queued")):
        return "waiting"
    log_file = paths.logs_dir() / "jobs" / "runner.log"
    pid = procs.spawn_detached([sys.executable, "-m", "slate", "jobs", "serve", "--once"], log_path=log_file,
                               cwd=str(paths.data()) if paths.data().is_dir() else None)
    proc.adopt_child(pid)
    return "started"


def _decorate(job: dict) -> dict:
    out = dict(job)
    out["alive"] = bool(job.get("state") == "running" and proc.alive(job.get("pid"), job.get("proc_start")))
    started = job.get("started_at")
    if job.get("state") == "running":
        out["elapsed_s"] = model.seconds_since(started)
    elif started and job.get("finished_at"):
        a, b = model.parse_iso(started), model.parse_iso(job["finished_at"])
        out["elapsed_s"] = (b - a).total_seconds() if a and b else None
    else:
        out["elapsed_s"] = None
    return out


def status(job_id: str) -> dict:
    """The job's record plus `alive` and `elapsed_s`. Notices a dead supervisor (requeues or fails the job) and makes
    sure a runner is serving the queue if the job is waiting."""
    model.check_id(job_id)
    job = store.load(job_id)
    if job.get("state") == "running":
        runner.reconcile([job_id])
        job = store.load(job_id)
    if job.get("state") == "queued":
        ensure_runner()
    return _decorate(job)


def wait(job_id: str, timeout: float | None = None, poll: float = 0.5) -> dict:
    """Wait until the job is done, failed or cancelled, and return it. Raises Busy (exit 5) after `timeout` seconds."""
    deadline = None if timeout is None else time.monotonic() + float(timeout)
    while True:
        job = status(job_id)
        if job["state"] in model.FINISHED:
            return job
        if deadline is not None and time.monotonic() >= deadline:
            raise Busy(f"Job {job_id} is still {job['state']} after {timeout:g} s"
                       + (f" ({job['waiting']})" if job.get("waiting") else "") + ".",
                       hint=f"It keeps going without you: poll with `slate job status {job_id}`.",
                       details={"job": job})
        time.sleep(poll)


def _flag_value(argv: list[str], name: str) -> str | None:
    for i, word in enumerate(argv):
        if word == name and i + 1 < len(argv):
            return argv[i + 1]
        if word.startswith(name + "="):
            return word.split("=", 1)[1]
    return None


def generation_target(job: dict) -> tuple[Path, str] | None:
    """(film folder, shot) when the job is a generation (`slate gen run --detach`), else None."""
    argv = [str(a) for a in job.get("argv") or []]
    is_gen = job.get("kind") == "generation" or any(argv[i:i + 2] == ["gen", "run"] for i in range(len(argv)))
    if not is_gen:
        return None
    shot = _flag_value(argv, "--shot")
    film = _flag_value(argv, "--film") or job.get("film_dir")
    if not shot or not film:
        return None
    return Path(film).expanduser(), shot


def cancel_vendor_requests(job: dict) -> list[dict]:
    """Stopping a command doesn't stop what it asked a vendor for: a generation job's queued Higgsfield requests are
    cancelled at Higgsfield (slate.gen.run.cancel for the shot's live takes), so they're never billed. Best effort:
    anything it can't cancel is returned with the reason, and the stop goes ahead either way."""
    target = generation_target(job)
    if target is None:
        return []
    film_dir, shot = target
    try:
        import importlib  # noqa: PLC0415

        gen_run = importlib.import_module("slate.gen.run")
        out = gen_run.cancel(film_dir, shot_id=shot)
    except UsageError:
        return []  # the shot has no generation record yet: nothing was sent, so nothing can bill
    except Exception as exc:  # noqa: BLE001 - the job still stops; say what couldn't be cancelled
        from slate import log  # noqa: PLC0415

        log.warn(f"{job.get('id')}: couldn't cancel {shot}'s requests at the vendor ({type(exc).__name__}: {exc}); "
                 f"check them with `slate gen takes --shot {shot}`")
        return [{"shot": shot, "canceled": False, "error": f"{type(exc).__name__}: {exc}"[:300]}]
    return list(out.get("results") or [])


def _note_vendor_cancel(job_id: str, results: list[dict]) -> None:
    """Write what the vendor cancel did into the job's history (its record has no other place for it)."""
    if not results:
        return
    done = [str(r.get("take") or r.get("request_id") or "?") for r in results if r.get("canceled")]
    left = [f"{r.get('take') or r.get('shot') or '?'} ({r.get('error') or r.get('summary') or 'not cancelled'})"
            for r in results if not r.get("canceled")]
    detail = (f"cancelled at the vendor before the stop: {', '.join(done) or 'none'}"
              + (f"; not cancelled: {'; '.join(left)}" if left else ""))
    with store.update(job_id) as job:
        model.note_event(job, "vendor-cancel", detail[:500])


def cancel(job_id: str, *, grace: float | None = None) -> None:
    """Cancel a job: a queued one never starts; a running one gets SIGTERM on its process group, then SIGKILL after
    the grace period. Cancelling a finished job does nothing.

    A generation job's requests are cancelled at the vendor first (and once more after the stop, for one sent in
    between): stopping the wait never cancels the request, and a queued Higgsfield request left alone runs and bills."""
    by = procs.agent_context() or "a person"
    before = store.load(job_id)
    if before.get("state") in model.FINISHED:
        return
    ran = before.get("state") == "running" or int(before.get("attempts") or 0) > 0  # only a run can have sent one
    if ran:
        _note_vendor_cancel(job_id, cancel_vendor_requests(before))
    try:
        _stop(job_id, by=by, grace=grace)
    finally:
        if before.get("state") == "running":
            _note_vendor_cancel(job_id, [r for r in cancel_vendor_requests(before) if r.get("canceled")])


def _stop(job_id: str, *, by: str, grace: float | None) -> None:
    with store.update(job_id) as job:
        state = job.get("state")
        if state in model.FINISHED:
            return
        job["reason"] = f"cancelled by {by}"
        job["stopped"] = "cancel"
        if state == "queued":
            job["state"] = "cancelled"
            job["finished_at"] = model.iso()
            model.note_event(job, "cancelled", job["reason"])
            return
        job["state"] = "cancelled"
        job["phase"] = "stopping"
        job["finished_at"] = model.iso()
        model.note_event(job, "cancelled", job["reason"])
        pgid = job.get("pgid") or job.get("pid")
        supervisor = job.get("pid")
        stamp = job.get("proc_start")
        record = dict(job)
    wait_s = runner.kill_grace() if grace is None else grace
    if supervisor and proc.alive(supervisor, stamp):
        # The command and everything it started (each tool in a group of its own); the supervisor stays to record it.
        proc.stop_tree(supervisor, grace=wait_s, spare=supervisor)
        deadline = time.monotonic() + max(wait_s, 1) + SUPERVISOR_EXIT_S
        while time.monotonic() < deadline:  # it writes down how the job ended, then leaves
            if not proc.alive(supervisor, stamp):
                return
            time.sleep(0.1)
        # a wedged supervisor: it's ours (identity checked above), so stop it and what's left under it
        proc.stop_tree(supervisor, grace=0, include_root=True, stamp=stamp)
        return
    if record.get("cmd_pid"):  # the supervisor is gone but its command may not be: it was recorded with its start
        proc.stop_tree(int(record["cmd_pid"]), grace=wait_s, include_root=True, stamp=record.get("cmd_start"))
    if pgid and proc.group_is_job(pgid, record):
        proc.stop_group(pgid, grace=wait_s)


def retry(job_id: str) -> dict:
    """Send a failed or cancelled job back to the queue. It resumes (by frame, line or act) if its command can."""
    with store.update(job_id) as job:
        state = job.get("state")
        if state == "done":
            raise UsageError(f"Job {job_id} already finished fine; submit a new job to run it again.")
        if state in model.ACTIVE:
            return job
        job["state"] = "queued"
        job["queued_at"] = model.iso()
        job["attempts"] = 0
        job["retries"] = int(job.get("retries", 0)) + 1
        job["finished_at"] = None
        job["exit_code"] = None
        job["signal"] = None
        job["stopped"] = None
        job["not_before"] = None
        job["waiting"] = None
        job["reason"] = f"sent back to the queue by {procs.agent_context() or 'a person'}"
        model.note_event(job, "retry", job["reason"])
        snapshot = dict(job)
    ensure_runner()
    return snapshot


def note_try_later(job_id: str, *, retry_after_s: float | None = None, reason: str | None = None) -> None:
    """Called by a job's own command as it exits 75 (slate.errors.TryLater): how long the vendor asked it to wait,
    and why. The supervisor reads it when it puts the job back in the queue, so the job's backoff is never shorter
    than the vendor's Retry-After."""
    with store.update(job_id) as job:
        if job.get("state") != "running":
            return
        job["try_later"] = {"retry_after_s": float(retry_after_s) if retry_after_s is not None else None,
                            "reason": (reason or "")[:300] or None, "at": model.iso()}


def list_jobs(*, states: Iterable[str] | None = None, film: str | None = None, limit: int | None = None) -> list[dict]:
    """Jobs, active first (in queue order), then the most recent finished ones."""
    wanted = set(states) if states else None
    jobs = [j for j in store.all_jobs() if (wanted is None or j.get("state") in wanted)
            and (film is None or j.get("film") == film)]
    active = model.order(j for j in jobs if j.get("state") in model.ACTIVE)
    active.sort(key=lambda j: (0 if j["state"] == "running" else 1, model.sort_key(j)))
    finished = sorted((j for j in jobs if j.get("state") in model.FINISHED),
                      key=lambda j: j.get("finished_at") or j.get("updated_at") or "", reverse=True)
    out = active + finished
    return out[:limit] if limit else out


def log_path(job_id: str) -> Path:
    return Path(store.load(job_id)["log"])


def tail(job_id: str, lines: int = 40) -> str:
    path = log_path(job_id)
    if not path.exists():
        return ""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        chunk = min(size, max(4096, lines * 400))
        fh.seek(size - chunk)
        data = fh.read().decode("utf-8", "replace")
    return "\n".join(data.splitlines()[-lines:]) if lines else data


def detach_current(kind: str, resources: Iterable[str] = (), *, film=None, disk_gb: float = 0.0,
                   priority: str = "supervised", argv: list[str] | None = None, resumable: bool = True) -> str:
    """Run the current `slate …` command as a job instead (the `--detach` convention): the same arguments without
    --detach, submitted to the queue. Returns the job id."""
    args = list(sys.argv[1:] if argv is None else argv)
    args = [a for a in args if a != "--detach"]
    return submit(kind, ["slate", *args], film=film, resources=resources, disk_gb=disk_gb, priority=priority,
                  resumable=resumable)


def summary() -> dict:
    """Counts per state, whether a runner is serving, and the job at the head of the queue."""
    jobs = store.all_jobs()
    counts = {s: 0 for s in model.STATES}
    for j in jobs:
        counts[j.get("state", "queued")] = counts.get(j.get("state", "queued"), 0) + 1
    queued = model.order(j for j in jobs if j.get("state") == "queued")
    from slate import jsonio  # noqa: PLC0415

    return {"runner": runner.runner_alive(), "runner_status": jsonio.read(runner.status_file(), default=None),
            "counts": counts, "next": queued[0]["id"] if queued else None,
            "running": [j["id"] for j in jobs if j.get("state") == "running"]}
