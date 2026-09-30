"""Resource locks: counted slots, so only so many jobs use a thing at once.

Each resource has N slots, and each slot is an fcntl lock file in DATA/locks/<resource>/slot-<n>.lock with the
holder's details beside it (slot-<n>.json: pid, owner, job, film, agent, since). The operating system drops an fcntl
lock the moment its holder exits, so a crashed agent never leaves a resource locked forever.

    with locks.hold("ae", owner="slate master"):        # waits for the one After Effects slot
        ...
    with locks.hold("render", timeout=0):               # try once; Busy (exit 5) if every slot is taken
        ...

Slots per resource (change them in DATA/slate.toml):

    render            [render] slots = 1      one heavy render at a time keeps the Mac usable (the load once hit 293)
    ae                1, always                AE blocks on a second script
    elevenlabs_tts    [jobs.slots] 4           Creator allows 5 at once and Pro 10; 4 leaves room for your own use
    elevenlabs_music  [jobs.slots] 1
    higgsfield        [jobs.slots] 2           the Cloud API allows 20; two keeps spend easy to follow
    whisper           [jobs.slots] 1           one Whisper pass saturates the GPU

A job the queue runs already holds its resources: its supervisor takes them before the command starts and tells the
command through SLATE_JOB_HOLDS, so `hold()` inside the job's own command uses the job's slot instead of waiting on
itself. Holds are re-entrant within a thread (After Effects' driver can nest them safely).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator

from slate import config, jsonio, log, paths, procs
from slate.errors import Busy, UsageError
from slate.jobs import model

DEFAULT_SLOTS = {"ae": 1, "elevenlabs_tts": 4, "elevenlabs_music": 1, "higgsfield": 2, "whisper": 1}
FIXED_MAX = {"ae": 1}  # AE can't run two scripts at once, whatever the settings say

_thread = threading.local()
_delegated_lock = threading.Lock()
_delegated_in_use: dict[str, int] = {}


def check(resource: str) -> str:
    if resource not in model.RESOURCES:
        raise UsageError(f"Unknown resource {resource!r}. Known: {', '.join(model.RESOURCES)}.")
    return resource


def slots(resource: str) -> int:
    """How many holders a resource allows (0 pauses it: queued jobs wait, and hold() times out)."""
    check(resource)
    cfg = config.load()
    if resource == "render":
        value = cfg.get("render", {}).get("slots", 1)
    else:
        value = (cfg.get("jobs", {}).get("slots") or {}).get(resource, DEFAULT_SLOTS[resource])
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise UsageError(f"Slots for {resource} should be a whole number in slate.toml (got {value!r}).") from None
    n = max(0, n)
    if resource in FIXED_MAX:
        n = min(n, FIXED_MAX[resource])
    return n


def _dir(resource: str) -> Path:
    return paths.locks_dir() / resource


def _slot_file(resource: str, n: int) -> Path:
    return _dir(resource) / f"slot-{n}.lock"


def _meta_file(resource: str, n: int) -> Path:
    return _dir(resource) / f"slot-{n}.json"


@dataclass
class Held:
    """One slot of a resource, held by this process until `release()` (or the process exits)."""

    resource: str
    slot: int | None
    owner: str | None = None
    delegated: bool = False
    _fh: IO | None = field(default=None, repr=False)
    _depth: int = field(default=1, repr=False)

    @property
    def label(self) -> str:
        return f"{self.resource}#{self.slot}" if self.slot is not None else f"{self.resource} (the job's own slot)"

    def release(self) -> None:
        if self.delegated:
            with _delegated_lock:
                _delegated_in_use[self.resource] = max(0, _delegated_in_use.get(self.resource, 1) - 1)
            self.delegated = False
            return
        if self._fh is None:
            return
        try:
            _meta_file(self.resource, self.slot).unlink(missing_ok=True)  # metadata first: the next holder writes its own
        finally:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


def _delegated() -> set[str]:
    return {r for r in os.environ.get("SLATE_JOB_HOLDS", "").split(",") if r}


def _thread_held() -> dict[str, Held]:
    held = getattr(_thread, "held", None)
    if held is None:
        held = _thread.held = {}
    return held


_context_cache: list = []


def _context() -> str | None:
    """Who is holding: the job's submitter inside a job, else the agent running us (cached; it walks ps otherwise)."""
    if os.environ.get("SLATE_JOB_ID"):
        return os.environ.get("SLATE_CONTEXT") or None
    if not _context_cache:
        _context_cache.append(procs.agent_context())
    return _context_cache[0]


def _try_slot(resource: str, n: int, *, owner: str | None, job: str | None) -> Held | None:
    path = _slot_file(resource, n)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    except OSError:
        fh.close()
        raise
    meta = {
        "resource": resource,
        "slot": n,
        "pid": os.getpid(),
        "owner": owner,
        "job": job or os.environ.get("SLATE_JOB_ID"),
        "film": os.environ.get("SLATE_FILM"),
        "context": _context(),
        "since": model.iso(),
    }
    try:
        jsonio.write_atomic(_meta_file(resource, n), meta)
    except OSError:
        pass  # the lock is what matters; missing metadata only makes `slate jobs locks` vaguer
    return Held(resource, n, owner=owner, _fh=fh)


def try_acquire(resource: str, *, owner: str | None = None, job: str | None = None) -> Held | None:
    """Take a free slot now, or return None. Never waits (the job supervisor uses this)."""
    check(resource)
    for n in range(slots(resource)):
        held = _try_slot(resource, n, owner=owner, job=job)
        if held:
            return held
    return None


def acquire(resource: str, *, timeout: float | None = None, owner: str | None = None) -> Held:
    """Wait for a slot (forever when timeout is None; once when it's 0). Raises Busy (exit 5) on timeout."""
    check(resource)
    mine = _thread_held().get(resource)
    if mine is not None:
        mine._depth += 1
        return mine
    if resource in _delegated():
        with _delegated_lock:
            if _delegated_in_use.get(resource, 0) == 0:
                _delegated_in_use[resource] = 1
                held = Held(resource, None, owner=owner, delegated=True)
                _thread_held()[resource] = held
                return held
    start = time.monotonic()
    wait = 0.05
    last_note = start
    while True:
        held = try_acquire(resource, owner=owner)
        if held:
            _thread_held()[resource] = held
            return held
        elapsed = time.monotonic() - start
        if timeout is not None and elapsed >= timeout:
            who = describe_holders(resource)
            raise Busy(
                f"Every {resource} slot is taken ({slots(resource)} allowed){who}.",
                hint=f"Wait for it, or see who holds what: slate jobs locks. Queue long work with slate job submit.",
                details={"resource": resource, "holders": holders(resource)},
            )
        if time.monotonic() - last_note >= 30:
            log.info(f"still waiting for a {resource} slot{describe_holders(resource)}")
            last_note = time.monotonic()
        pause = wait if timeout is None else min(wait, max(0.01, timeout - elapsed))
        time.sleep(pause)
        wait = min(wait * 1.5, 0.5)


def release(held: Held) -> None:
    """Release one level of a hold (the slot itself goes when the outermost hold ends)."""
    mine = _thread_held().get(held.resource)
    if mine is held and held._depth > 1:
        held._depth -= 1
        return
    if mine is held:
        del _thread_held()[held.resource]
    held.release()


@contextlib.contextmanager
def hold(resource: str, *, timeout: float | None = None, owner: str | None = None) -> Iterator[Held]:
    """Hold one slot of `resource` for the duration of the block. See the module docstring for the slot counts."""
    held = acquire(resource, timeout=timeout, owner=owner)
    try:
        yield held
    finally:
        release(held)


def _probe(resource: str, n: int) -> bool:
    """True when slot n is free right now (checked by briefly taking and dropping it)."""
    path = _slot_file(resource, n)
    if not path.exists():
        return True
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return True


def holders(resource: str | None = None) -> list[dict]:
    """Who holds which slot right now (only slots actually locked; stale metadata is ignored)."""
    names = [check(resource)] if resource else list(model.RESOURCES)
    out: list[dict] = []
    for name in names:
        folder = _dir(name)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("slot-*.lock")):
            try:
                n = int(path.stem.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            if _probe(name, n):
                continue
            meta = jsonio.read(_meta_file(name, n), default=None) or {}
            out.append({"resource": name, "slot": n, **{k: v for k, v in meta.items() if k not in ("resource", "slot")}})
    return out


def free(resource: str) -> int:
    """Slots free right now."""
    return sum(1 for n in range(slots(resource)) if _probe(resource, n))


def describe_holders(resource: str) -> str:
    held = holders(resource)
    if not held:
        return ""
    parts = []
    for h in held:
        who = h.get("job") or h.get("owner") or f"pid {h.get('pid')}"
        parts.append(f"{who} (pid {h.get('pid')})" if h.get("pid") and h.get("job") else str(who))
    return ": held by " + ", ".join(parts)


def status() -> list[dict]:
    """Every resource: slots allowed, free now, and holders."""
    return [{"resource": r, "slots": slots(r), "free": free(r), "holders": holders(r)} for r in model.RESOURCES]
