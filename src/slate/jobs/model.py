"""What a job is: its states, priorities, resources and the record the queue keeps for it."""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
import secrets
from pathlib import Path
from typing import Iterable

from slate import paths
from slate.errors import UsageError

STATES = ("queued", "running", "done", "failed", "cancelled")
ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "cancelled")

# Highest first. A film's mode sets its jobs' priority: supervised films outrank autonomous ones.
PRIORITIES = ("supervised", "express", "draft", "autonomous")

# Things only so many jobs may use at once (counted slots; see slate.jobs.locks).
RESOURCES = ("render", "ae", "elevenlabs_tts", "elevenlabs_music", "higgsfield", "whisper")

# A command exits with one of these to ask the queue to try again later, rather than fail:
# 5 is Slate's "busy" (After Effects open, a lock held); 75 is EX_TEMPFAIL (a rate limit, a flaky network).
RETRY_LATER_CODES = (5, 75)
TEMPFAIL = 75
SPEND_REFUSED_CODE = 4

DEFAULT_MAX_ATTEMPTS = 3
HISTORY_LIMIT = 60

# Settings a job carries from whoever submitted it, so it runs against the same folders and in the same replay mode
# however it's started (a one-shot runner, or the launchd agent, whose environment is bare). Never keys: the job reads
# those from the Keychain like everything else.
ENV_KEEP = ("SLATE_DATA", "SLATE_FILMS", "SLATE_BRANDS", "SLATE_FAKE_VENDORS", "SLATE_LIVE", "SLATE_FIXTURES",
            "SLATE_RECORD", "SLATE_CONTEXT", "SLATE_RENDER_SLOTS", "SLATE_MB_SAMPLES", "SLATE_DISK_RESERVE_GB",
            "SLATE_FAKE_DISK_FREE_GB")
ENV_KEEP_PREFIXES = ("SLATE_MODEL_",)

JOB_ID = re.compile(r"^j-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(when: _dt.datetime | None = None, *, precise: bool = False) -> str:
    """UTC ISO-8601; `precise` keeps microseconds (used for queue order, so jobs submitted in the same second stay
    first come, first served)."""
    return (when or now()).isoformat(timespec="microseconds" if precise else "seconds")


def parse_iso(text: str | None) -> _dt.datetime | None:
    if not text:
        return None
    try:
        value = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)


def seconds_since(text: str | None) -> float | None:
    when = parse_iso(text)
    return None if when is None else (now() - when).total_seconds()


def new_id() -> str:
    """Sortable and readable: j-20260930-104212-3f9a (UTC)."""
    return f"j-{now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def check_id(job_id: str) -> str:
    """Refuse anything that isn't a job id (it becomes a file name)."""
    if not isinstance(job_id, str) or not JOB_ID.match(job_id):
        raise UsageError(f"{job_id!r} isn't a job id (they look like j-20260930-104212-3f9a).",
                         hint="slate job list shows the jobs on this Mac.")
    return job_id


def rank(priority: str) -> int:
    return PRIORITIES.index(priority)


def lower_of(a: str, b: str) -> str:
    """The lower of two priorities (a job never outranks its film's mode)."""
    return a if rank(a) >= rank(b) else b


_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def sort_key(job: dict) -> tuple:
    """Queue order: priority first (supervised > express > draft > autonomous), then age, then id."""
    created = parse_iso(job.get("created_at")) or _EPOCH
    return (rank(job.get("priority", "autonomous")), created, job.get("id") or "")


def order(jobs: Iterable[dict]) -> list[dict]:
    return sorted(jobs, key=sort_key)


def check_resources(resources: Iterable[str]) -> list[str]:
    out: list[str] = []
    for r in resources or ():
        if r not in RESOURCES:
            raise UsageError(f"Unknown resource {r!r}. Jobs can hold: {', '.join(RESOURCES)}.")
        if r not in out:
            out.append(r)
    return out


def captured_env(environ: dict | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    out = {k: str(env[k]) for k in ENV_KEEP if env.get(k) not in (None, "")}
    for k, v in env.items():
        if any(k.startswith(p) for p in ENV_KEEP_PREFIXES) and v:
            out[k] = str(v)
    return out


def log_path(job_id: str) -> Path:
    return paths.logs_dir() / "jobs" / f"{job_id}.log"


def new_job(kind: str, argv: list[str], *, cwd: Path, film: str | None, film_dir: Path | None,
            film_mode: str | None, resources: list[str], priority: str, priority_requested: str | None,
            disk_gb: float, disk_path: Path | None, resumable: bool, max_attempts: int, note: str | None,
            context: str | None, live: bool, env: dict[str, str]) -> dict:
    if not KIND.match(kind or ""):
        raise UsageError(f"Job kind {kind!r} should be a short lowercase word (render, master, whisper, tts, ...).")
    if not argv or not all(isinstance(a, str) for a in argv):
        raise UsageError("A job needs a command: an argv list of strings (never a shell string).")
    if priority not in PRIORITIES:
        raise UsageError(f"Unknown priority {priority!r}; one of {', '.join(PRIORITIES)}.")
    if not isinstance(disk_gb, (int, float)) or math.isnan(disk_gb) or math.isinf(disk_gb) or disk_gb < 0:
        raise UsageError(f"--disk-gb must be a number of gigabytes, 0 or more (got {disk_gb!r}).")
    if not 1 <= int(max_attempts) <= 20:
        raise UsageError("max_attempts must be between 1 and 20.")
    job_id = new_id()
    stamp = iso(precise=True)
    return {
        "schema": 1,
        "id": job_id,
        "kind": kind,
        "note": note,
        "argv": list(argv),
        "run_argv": None,
        "cwd": str(cwd),
        "film": film,
        "film_dir": str(film_dir) if film_dir else None,
        "film_mode": film_mode,
        "resources": list(resources),
        "priority": priority,
        "priority_requested": priority_requested,
        "disk_gb": float(disk_gb),
        "disk_path": str(disk_path) if disk_path else None,
        "state": "queued",
        "phase": None,
        "launch": None,
        "pid": None,
        "pgid": None,
        "proc_start": None,
        "log": str(log_path(job_id)),
        "created_at": stamp,
        "queued_at": stamp,
        "started_at": None,
        "launched_at": None,
        "finished_at": None,
        "updated_at": stamp,
        "not_before": None,
        "exit_code": None,
        "signal": None,
        "attempts": 0,
        "max_attempts": int(max_attempts),
        "retries": 0,
        "resumable": bool(resumable),
        "reason": None,
        "waiting": None,
        "stopped": None,
        "holds": [],
        "env": dict(env),
        "context": context,
        "live": bool(live),
        "history": [{"at": stamp, "event": "queued", "detail": f"{kind} at {priority} priority"}],
    }


def note_event(job: dict, event: str, detail: str | None = None) -> None:
    """Append to the job's short history (kept to the last HISTORY_LIMIT events)."""
    job.setdefault("history", []).append({"at": iso(), "event": event, "detail": detail})
    if len(job["history"]) > HISTORY_LIMIT:
        job["history"] = job["history"][-HISTORY_LIMIT:]


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {s:02d}s"
    hours, m = divmod(minutes, 60)
    return f"{hours}h {m:02d}m"
