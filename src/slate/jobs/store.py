"""The queue on disk: one JSON file per job in DATA/jobs, changed only under the queue lock.

Every change is a read-modify-write under one fcntl lock (DATA/locks/queue.lock) and lands with an atomic rename, so
the runner, a job's supervisor and an agent running `slate job cancel` can all touch the same job without losing a
write, and a crash never leaves half a job file.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import threading
from pathlib import Path
from typing import Iterator

from slate import jsonio, paths
from slate.errors import UsageError
from slate.jobs import model

_local = threading.local()


def jobs_dir() -> Path:
    return paths.jobs_dir()


def job_file(job_id: str) -> Path:
    return jobs_dir() / f"{model.check_id(job_id)}.json"


def _lock_file() -> Path:
    return paths.locks_dir() / "queue.lock"


@contextlib.contextmanager
def queue_lock() -> Iterator[None]:
    """The queue lock (re-entrant within a thread, so helpers can nest)."""
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    lock = _lock_file()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _local.depth = 1
        try:
            yield
        finally:
            _local.depth = 0
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def exists(job_id: str) -> bool:
    return job_file(job_id).is_file()


def load(job_id: str) -> dict:
    path = job_file(job_id)
    if not path.is_file():
        raise UsageError(f"No job {job_id} on this Mac (looked in {jobs_dir()}).",
                         hint="slate job list shows the jobs here.")
    return jsonio.read(path)


def save(job: dict) -> None:
    job["updated_at"] = model.iso()
    problems = jsonio.validate(job, "job")
    if problems:
        raise UsageError(f"Job {job.get('id')} doesn't match the job schema: " + "; ".join(problems[:5]))
    jsonio.write_atomic(job_file(job["id"]), job)


def create(job: dict) -> dict:
    with queue_lock():
        save(job)
    return job


@contextlib.contextmanager
def update(job_id: str) -> Iterator[dict]:
    """Change a job under the queue lock: `with store.update(id) as job: job["state"] = ...` (saved on exit)."""
    with queue_lock():
        job = load(job_id)
        before = jsonio.dumps(job)
        yield job
        if jsonio.dumps(job) != before:
            save(job)


_cache: dict[str, tuple[int, int, dict]] = {}


def all_jobs() -> list[dict]:
    """Every job on this Mac. Files are re-read only when they change (the runner reads the queue every pass, and a
    few months of finished jobs shouldn't cost it anything)."""
    folder = jobs_dir()
    if not folder.is_dir():
        return []
    out = []
    seen = set()
    for path in sorted(folder.glob("j-*.json")):
        key = str(path)
        seen.add(key)
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        cached = _cache.get(key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            out.append(json.loads(json.dumps(cached[2])))  # a copy: callers may change what they get
            continue
        try:
            job = jsonio.read(path)
        except UsageError:
            continue  # a job file being replaced right now; the next read sees it
        _cache[key] = (st.st_mtime_ns, st.st_size, job)
        out.append(json.loads(json.dumps(job)))
    for key in [k for k in _cache if k not in seen and k.startswith(str(folder))]:
        del _cache[key]
    return out


def by_state(*states: str) -> list[dict]:
    return [j for j in all_jobs() if j.get("state") in states]
