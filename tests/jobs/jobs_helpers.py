"""Helpers for the job-queue tests (imported by the tests in this folder)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PY = sys.executable


def write_toml(data: Path, text: str) -> None:
    (data / "slate.toml").write_text(text, encoding="utf-8")


def cleanup(data: Path) -> None:
    """Stop any runner and job process group a test started (a failed assertion mustn't leave processes behind)."""
    from slate import jsonio
    from slate.jobs import proc, store

    status = jsonio.read(data / "jobs" / "runner.json", default=None) or {}
    pid = status.get("pid")
    if pid and pid != os.getpid() and proc.alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for job in store.all_jobs():
        pgid = job.get("pgid") or job.get("pid")
        if job.get("state") == "running" and pgid and pgid != os.getpgid(0):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    proc.reap()


def wait_for(predicate, timeout: float = 30.0, step: float = 0.1, what: str = "condition"):
    """Poll until predicate() is truthy; return its value, or fail the test after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError(f"timed out after {timeout:g}s waiting for {what}")


def job_state(job_id: str) -> str:
    from slate.jobs import store

    return store.load(job_id)["state"]


def hold_in_other_process(resource: str, *, marker: Path, seconds: float = 60.0) -> subprocess.Popen:
    """A separate process that holds one slot of `resource` until it's killed (or `seconds` pass)."""
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from slate.jobs import locks\n"
        f"with locks.hold({resource!r}, owner='other-process'):\n"
        f"    Path({str(marker)!r}).write_text('held')\n"
        f"    time.sleep({seconds})\n"
    )
    proc = subprocess.Popen([PY, "-c", code], env=dict(os.environ), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    wait_for(lambda: marker.exists() or proc.poll() is not None, timeout=30, what=f"another process to hold {resource}")
    if proc.poll() is not None:
        raise AssertionError(f"holder process exited early: {proc.stderr.read().decode()[-500:]}")
    return proc
