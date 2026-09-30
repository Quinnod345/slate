"""Disk space: every job reserves what it will write, and nothing fills the disk.

A 6-minute master is about 10 GB, a Remotion bundle is 290 MB, and After Effects' cache once took 60 GB. So:

- A job that writes a lot reserves it first (`disk_gb` on the job, or `with disk.reserve(path, gb)` in code). A
  reservation is a small file in DATA/disk naming its owner's pid; it lapses by itself when that process dies.
- A job starts only if, on its volume, free space minus every live reservation minus its own stays at or above the
  floor (`jobs.disk_reserve_gb` in slate.toml, 5 GB by default). Otherwise it waits in the queue, saying why.
- While a job runs, its supervisor watches free space. Below the stop line (`jobs.disk_stop_gb`, 4 GB by default, the
  films' own watchdog value, and never above the floor) it stops the job safely: SIGTERM to its process group, state
  failed with "stopped safely: disk nearly full", and the job stays resumable for `slate job retry` once there's room.

`SLATE_FAKE_DISK_FREE_GB` pretends a volume has that much free space (a number, or "@file" to read the number from a
file each time). It's for tests and drills; nothing else sets it.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from slate import config, jsonio, paths, procs
from slate.errors import Refused, UsageError
from slate.jobs import model, proc

GB = 1_000_000_000  # decimal gigabytes, the unit Finder shows
DEFAULT_STOP_GB = 4.0


def floor_gb() -> float:
    """Free space Slate always keeps: a job doesn't start if it would leave less than this."""
    value = config.load().get("jobs", {}).get("disk_reserve_gb", 5.0)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        raise UsageError(f"jobs.disk_reserve_gb in slate.toml should be a number of GB (got {value!r}).") from None


def stop_gb() -> float:
    """The watchdog's line: a running job is stopped safely when free space drops below this."""
    value = config.load().get("jobs", {}).get("disk_stop_gb", DEFAULT_STOP_GB)
    try:
        return max(0.0, min(float(value), floor_gb()))
    except (TypeError, ValueError):
        raise UsageError(f"jobs.disk_stop_gb in slate.toml should be a number of GB (got {value!r}).") from None


def _existing(path: str | os.PathLike) -> Path:
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def volume(path: str | os.PathLike) -> int:
    return _existing(path).stat().st_dev


def free_gb(path: str | os.PathLike) -> float:
    fake = os.environ.get("SLATE_FAKE_DISK_FREE_GB")
    if fake:
        text = Path(fake[1:]).read_text(encoding="utf-8").strip() if fake.startswith("@") else fake
        try:
            return float(text)
        except ValueError:
            raise UsageError(f"SLATE_FAKE_DISK_FREE_GB should be a number of GB (got {text!r}).") from None
    return procs.disk_free_bytes(_existing(path)) / GB


def _dir() -> Path:
    return paths.data() / "disk"


@contextlib.contextmanager
def _disk_lock() -> Iterator[None]:
    lock = paths.locks_dir() / "disk.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def reservations(path: str | os.PathLike | None = None) -> list[dict]:
    """Live reservations (their owner is still running), optionally only those on `path`'s volume.

    Reservations whose owner has died are removed as they're found.
    """
    folder = _dir()
    if not folder.is_dir():
        return []
    dev = volume(path) if path is not None else None
    out = []
    for f in sorted(folder.glob("r-*.json")):
        data = jsonio.read(f, default=None)
        if not isinstance(data, dict):
            continue
        if not proc.alive(data.get("pid"), data.get("proc_start")):
            f.unlink(missing_ok=True)
            continue
        if dev is not None and data.get("volume") != dev:
            continue
        out.append(data)
    return out


def outstanding_gb(path: str | os.PathLike, *, exclude: str | None = None, exclude_job: str | None = None) -> float:
    return sum(float(r.get("gb") or 0) for r in reservations(path)
               if r.get("id") != exclude and not (exclude_job and r.get("job") == exclude_job))


def check(path: str | os.PathLike, gb: float, *, pending_gb: float = 0.0, exclude_job: str | None = None,
          reserved_gb: float | None = None) -> tuple[bool, str, dict]:
    """Would reserving `gb` on `path`'s volume keep Slate's floor free? Returns (ok, plain-words reason, numbers).

    `pending_gb` counts reservations about to be made (jobs the queue has launched whose supervisors haven't
    reserved yet), so one scheduling pass never promises the same space twice. `exclude_job` leaves out that job's
    own reservations (a command reserving inside its queued job stands in for the job's reservation, not on top).
    `reserved_gb` passes live reservations the caller already counted (the scheduler counts them once per pass).
    """
    free = free_gb(path)
    live = outstanding_gb(path, exclude_job=exclude_job) if reserved_gb is None else float(reserved_gb)
    held = live + max(0.0, float(pending_gb))
    floor = floor_gb()
    left = free - held - float(gb)
    facts = {"path": str(path), "free_gb": round(free, 2), "reserved_gb": round(held, 2), "needs_gb": float(gb),
             "floor_gb": floor, "left_gb": round(left, 2)}
    if left >= floor:
        return True, f"{gb:g} GB fits: {free:.1f} GB free, {held:.1f} GB reserved by other jobs", facts
    reason = (f"needs {gb:g} GB on the disk holding {path}: {free:.1f} GB is free, {held:.1f} GB is already reserved "
              f"by other jobs, and Slate always keeps {floor:g} GB free")
    return False, reason, facts


@dataclass
class Reservation:
    id: str | None
    path: str
    gb: float
    file: Path | None = None

    def release(self) -> None:
        if self.file is not None:
            self.file.unlink(missing_ok=True)
            self.file = None


def acquire(path: str | os.PathLike, gb: float, *, owner: str | None = None, job: str | None = None) -> Reservation:
    """Reserve `gb` on `path`'s volume for this process, or raise Refused (exit 1) saying what's short."""
    try:
        gb = float(gb)
    except (TypeError, ValueError):
        raise UsageError(f"A disk reservation is a number of GB (got {gb!r}).") from None
    if gb <= 0:
        return Reservation(None, str(path), 0.0)
    # Inside a queued job, the job's supervisor already reserved its space; this reservation stands in for that one.
    inside = os.environ.get("SLATE_JOB_ID") if job is None else None
    with _disk_lock():
        ok, reason, facts = check(path, gb, exclude_job=inside)
        if not ok:
            raise Refused(f"Not enough disk space to start: this {reason}.",
                          hint="Free some space (old renders, archived takes, AE's disk cache), or lower "
                               "jobs.disk_reserve_gb in slate.toml if you're sure.",
                          details=facts)
        rid = f"r-{secrets.token_hex(6)}"
        record = {"id": rid, "path": str(path), "volume": volume(path), "gb": gb, "pid": os.getpid(),
                  "proc_start": proc.own_stamp(), "job": job or os.environ.get("SLATE_JOB_ID"), "owner": owner,
                  "created_at": model.iso()}
        target = _dir() / f"{rid}.json"
        jsonio.write_atomic(target, record)
    return Reservation(rid, str(path), gb, target)


@contextlib.contextmanager
def reserve(path: str | os.PathLike, gb: float, *, owner: str | None = None) -> Iterator[Reservation]:
    """Hold a disk reservation for the duration of the block (refused up front when the space isn't there)."""
    res = acquire(path, gb, owner=owner)
    try:
        yield res
    finally:
        res.release()


def watch(path: str | os.PathLike) -> tuple[bool, str, float]:
    """The running watchdog: (still fine?, reason, free GB). Not fine once free space drops below the stop line."""
    free = free_gb(path)
    line = stop_gb()
    if free < line:
        return False, (f"stopped safely: disk nearly full ({free:.1f} GB free on the disk holding {path}; "
                       f"Slate stops jobs under {line:g} GB)"), free
    return True, "", free


def summary(path: str | os.PathLike | None = None) -> dict:
    target = path or paths.films()
    return {"path": str(target), "free_gb": round(free_gb(target), 2), "floor_gb": floor_gb(), "stop_gb": stop_gb(),
            "reserved_gb": round(outstanding_gb(target), 2), "reservations": reservations(target)}
