"""Slate's side of the AE bridge's busy lock, so the two never send AE a script at the same time.

After Effects runs one script at a time. A script that arrives while another runs is refused, and AE raises a modal
("Attempt was made to run a second script…") that blocks all scripting until a person clicks OK. Higgsfield's AE
bridge (fnf-after-effects-mcp) guards its own calls with a lock file, and Slate takes part in the same protocol, read
from the installed package (dist/config.js and dist/transport/FileIpcTransport.js, version 0.1.3):

- the lock is `<mailbox>/dispatcher-busy.lock`; the mailbox is `$AE_MCP_RUNTIME_DIR`, or
  `<os temp dir>/mcp-aftereffects/runtime` (Node's os.tmpdir(): TMPDIR, TMP, TEMP, then /tmp), mode 0700;
- it is taken by an exclusive create (`open("wx")`, atomic across processes) holding JSON `{pid, id, startedAt}`;
- its owner refreshes the file's mtime every 15 s while alive; a lock untouched for 90 s has lost its owner and any
  waiter may break it;
- it is released only by its owner (the id inside must still be its own).

Slate holds this lock for as long as its own script runs in AE, refreshing it on the bridge's schedule, so a bridge
call waits for Slate (and times out cleanly) instead of colliding with it, and vice versa. Because the mailbox follows
the temp dir, and an agent's shell can see a different TMPDIR from the bridge's process, Slate takes the lock in every
candidate mailbox: `$AE_MCP_RUNTIME_DIR`, the one named by the bridge's `runtime-dir.txt` pointer, `$TMPDIR`'s, and
macOS's per-user temp dir's.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import sys
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from slate import procs
from slate.errors import Busy

LOCK_NAME = "dispatcher-busy.lock"
STALE_S = 90.0
REFRESH_S = 15.0
POLL_S = 0.1
ROOT_NAME = "mcp-aftereffects"
POINTER_NAME = "runtime-dir.txt"
BRIDGE_PACKAGE = "fnf-after-effects-mcp"


def node_tmpdir() -> Path:
    """What Node's os.tmpdir() returns on macOS and Linux: TMPDIR, TMP, TEMP, else /tmp (trailing slash dropped)."""
    for var in ("TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(var)
        if value:
            return Path(value.rstrip("/") or "/")
    return Path("/tmp")


@lru_cache(maxsize=1)
def darwin_user_temp() -> Path | None:
    """macOS's per-user temp dir (what TMPDIR normally is), even when this shell's TMPDIR was changed."""
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/getconf"):
        return None
    try:
        proc = procs.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], timeout=5)
    except Exception:  # noqa: BLE001 - best effort; the TMPDIR candidate still applies
        return None
    value = (proc.stdout or "").strip()
    return Path(value.rstrip("/")) if proc.returncode == 0 and value else None


def bridge_installed() -> bool:
    """Whether the bridge's npm package is on this Mac (where the bridge module would look first)."""
    try:
        from slate.ae import bridge  # noqa: PLC0415 - avoid an import cycle at module load
    except ImportError:
        return False
    return bridge.package_dir() is not None


def runtime_dirs() -> list[Path]:
    """Every mailbox the bridge could be using right now, most specific first, without duplicates."""
    candidates: list[Path] = []
    env = os.environ.get("AE_MCP_RUNTIME_DIR", "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    roots = [node_tmpdir()]
    dut = darwin_user_temp()
    if dut:
        roots.append(dut)
    for root in roots:
        base = root / ROOT_NAME
        pointer = base / POINTER_NAME
        if pointer.is_file():
            try:
                named = pointer.read_text(encoding="utf-8").strip()
            except OSError:
                named = ""
            if named:
                candidates.append(Path(named).expanduser())
        candidates.append(base / "runtime")
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        key = os.path.realpath(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_owner(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def owns(path: Path, lock_id: str) -> bool:
    info = _read_owner(path)
    return bool(info) and info.get("id") == lock_id


def _try_create(path: Path, lock_id: str, owner: str) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "id": lock_id, "startedAt": _iso_now(), "owner": owner}, fh)
    return True


def _break_if_stale(path: Path, stale_s: float) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return True
    if age > stale_s:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return True
    return False


def acquire(path: Path, lock_id: str, *, deadline: float, owner: str = "slate", stale_s: float = STALE_S) -> None:
    """Take one mailbox's lock, breaking it only when stale; Busy (exit 5) when the deadline passes first."""
    while True:
        if _try_create(path, lock_id, owner):
            return
        _break_if_stale(path, stale_s)
        if time.monotonic() >= deadline:
            holder = _read_owner(path) or {}
            who = "the AE bridge" if not str(holder.get("id", "")).startswith("slate-") else "another Slate run"
            raise Busy(f"After Effects is running a script for {who} (its lock is {path}).",
                       hint="Wait for it to finish and run this again. If nothing is running, check AE for a "
                            "modal dialog; a lock nobody refreshes expires by itself after 90 seconds.")
        time.sleep(POLL_S)


def release(path: Path, lock_id: str) -> None:
    """Drop the lock, but only while it is still ours (a stalled owner may have lost it to a waiter)."""
    if owns(path, lock_id):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def refresh(path: Path, lock_id: str) -> None:
    """Touch the lock so waiters keep seeing a live owner. Never touches a lock that isn't ours."""
    if owns(path, lock_id):
        with contextlib.suppress(OSError):
            os.utime(path, None)


def _ensure_mailbox(d: Path, create: bool) -> bool:
    if d.is_dir():
        return True
    if not create:
        return False
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(d, 0o700)
        with contextlib.suppress(OSError):
            os.chmod(d.parent, 0o700)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def hold(*, timeout: float | None = 60.0, owner: str = "slate", create: bool | None = None,
         refresh_s: float = REFRESH_S) -> Iterator[list[Path]]:
    """Hold the bridge's busy lock in every candidate mailbox for the duration of a Slate script.

    Mailboxes that don't exist are created (mode 0700) only when the bridge is installed; with no bridge on the
    Mac there is nobody to coordinate with and nothing is created. Yields the lock paths held.
    """
    if create is None:
        create = bridge_installed()
    lock_id = f"slate-{uuid.uuid4()}"
    deadline = time.monotonic() + (timeout if timeout is not None else 10 ** 9)
    held: list[Path] = []
    stop = threading.Event()
    thread: threading.Thread | None = None
    try:
        for d in sorted(runtime_dirs(), key=lambda p: str(p)):
            if not _ensure_mailbox(d, create):
                continue
            path = d / LOCK_NAME
            acquire(path, lock_id, deadline=deadline, owner=owner)
            held.append(path)
        if held:
            def _refresher() -> None:
                while not stop.wait(refresh_s):
                    for p in held:
                        refresh(p, lock_id)
            thread = threading.Thread(target=_refresher, name="slate-ae-bridge-lock", daemon=True)
            thread.start()
        yield held
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=2)
        for p in held:
            release(p, lock_id)
