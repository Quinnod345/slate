"""Process helpers for the queue: is this pid still the process we started, and stopping a job's process group.

A pid alone isn't proof: after a crash or a reboot the number can belong to something else. So the queue records each
supervisor's start time (from `ps`) and treats a pid as ours only when both match. Zombies count as dead.
"""

from __future__ import annotations

import functools
import os
import re
import signal
import time

from slate import procs
from slate.errors import SlateError


def _ps(pid: int) -> tuple[str, str] | None:
    """(stat, start time) for a pid, or None when there's no such process."""
    if pid <= 0:
        return None
    try:
        out = procs.run(["/bin/ps", "-o", "stat=,lstart=", "-p", str(pid)], timeout=10).stdout.strip()
    except SlateError:
        return None
    if not out:
        return None
    stat, _, started = out.partition(" ")
    return stat.strip(), " ".join(started.split())


def start_stamp(pid: int) -> str | None:
    info = _ps(pid)
    return info[1] if info else None


@functools.lru_cache(maxsize=1)
def own_stamp() -> str | None:
    """This process's start time (cached: it never changes)."""
    return start_stamp(os.getpid())


def alive(pid: int | None, stamp: str | None = None) -> bool:
    """True when `pid` is running and, if we know when ours started, it's the same process."""
    if not pid:
        return False
    if not procs.pid_alive(pid):
        return False
    info = _ps(pid)
    if info is None:
        # ps couldn't see it (it may have just exited); trust the signal probe once more
        return procs.pid_alive(pid)
    stat, started = info
    if stat.startswith("Z"):
        return False
    if stamp and started and started != stamp:
        return False
    return True


def group_members(pgid: int) -> list[int]:
    try:
        out = procs.run(["/usr/bin/pgrep", "-g", str(pgid)], timeout=10).stdout
    except SlateError:
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


def boot_time() -> float | None:
    """When this Mac last booted (epoch seconds), or None if sysctl can't say."""
    try:
        out = procs.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], timeout=10).stdout
    except SlateError:
        return None
    # "{ sec = 1790498580, usec = 157296 } Sun Sep 27 04:43:00 2026"
    marker = "sec = "
    if marker not in out:
        return None
    try:
        return float(out.split(marker, 1)[1].split(",", 1)[0])
    except ValueError:
        return None


def command_of(pid: int) -> str:
    try:
        return procs.run(["/bin/ps", "-ww", "-o", "command=", "-p", str(pid)], timeout=10).stdout.strip()
    except SlateError:
        return ""


def _norm(text: str) -> str:
    """Compare command lines the way ps prints them: control characters come out as octal escapes (\\012)."""
    text = re.sub(r"\\[0-7]{3}", " ", text)
    return " ".join(text.split())


def _job_needles(job: dict) -> list[str]:
    needles = [f"slate.jobs.shim {job.get('id')}"]
    run = [str(a) for a in (job.get("run_argv") or [])]
    if run:
        needles.append(" ".join(run))  # the caffeinate helper keeps the whole wrapped command line
        tail = list(run)
        if tail and tail[0].endswith("caffeinate"):
            tail = tail[2:]
        if tail and tail[0].endswith("nice"):
            tail = tail[3:]
        # The command itself, after caffeinate and nice exec it. Its first word can change (an interpreter re-execs
        # itself from a framework path), so match the arguments, when they're distinctive enough.
        args = " ".join(tail[1:])
        if len(_norm(args)) >= 8:
            needles.append(args)
    return [_norm(n) for n in needles if _norm(n)]


def group_is_job(pgid: int | None, job: dict) -> bool:
    """Is process group `pgid` still this job's? A dead supervisor's pgid can be reused after its group empties (or
    after a reboot), and stopping a stranger's processes would be far worse than leaving an orphan, so this checks
    the boot time and the members' command lines before anything is signalled."""
    if not pgid or not group_alive(pgid):
        return False
    from slate.jobs import model  # noqa: PLC0415

    launched = model.parse_iso(job.get("launched_at") or job.get("started_at"))
    booted = boot_time()
    if launched and booted and launched.timestamp() < booted:
        return False  # it ran before the last reboot: none of its processes can be alive
    needles = _job_needles(job)
    for member in group_members(pgid):
        cmd = _norm(command_of(member))
        if cmd and any(n in cmd for n in needles):
            return True
    return False


def group_alive(pgid: int | None) -> bool:
    if not pgid:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def signal_group(pgid: int, sig: int, *, spare: int | None = None) -> None:
    """Send a signal to every process in a group, optionally sparing one (the supervisor, so it can write the result)."""
    if spare is None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        return
    for pid in group_members(pgid):
        if pid == spare:
            continue
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def stop_group(pgid: int, *, grace: float = 10.0, spare: int | None = None) -> str:
    """SIGTERM a job's process group, wait up to `grace` seconds, then SIGKILL what's left.

    Returns "gone" (nothing was running), "terminated" (it stopped on SIGTERM) or "killed" (it needed SIGKILL).
    """
    def remaining() -> list[int]:
        members = group_members(pgid)
        return [p for p in members if p != spare] if spare is not None else members

    if not group_alive(pgid) or not remaining():
        return "gone"
    signal_group(pgid, signal.SIGTERM, spare=spare)
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline:
        reap()
        if not remaining():
            return "terminated"
        time.sleep(0.1)
    signal_group(pgid, signal.SIGKILL, spare=spare)
    for _ in range(50):
        reap()
        if not remaining():
            break
        time.sleep(0.1)
    return "killed"


_children: set[int] = set()


def adopt_child(pid: int) -> None:
    """Remember a supervisor this process started, so `reap` can collect it when it exits."""
    _children.add(pid)


def reap() -> None:
    """Collect exited supervisors this process started, so they don't linger as zombies that look alive.

    Only pids registered with `adopt_child` are waited on: a blanket waitpid(-1) would steal the exit status of
    unrelated children (a test's helper process, a Popen someone else is about to wait on).
    """
    for pid in list(_children):
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            _children.discard(pid)
            continue
        except OSError:
            continue
        if done:
            _children.discard(pid)


def signal_name(returncode: int | None) -> str | None:
    if returncode is None or returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"
