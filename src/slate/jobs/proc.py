"""Process helpers for the queue: is this pid still the process we started, and stopping everything a job started.

A pid alone isn't proof: after a crash or a reboot the number can belong to something else. So the queue records each
supervisor's start time (from `ps`) and treats a pid as ours only when both match. Zombies count as dead.

Stopping a job means stopping its whole process tree, not one process group: `slate.procs.run` starts every child in
its own session (so a timeout can stop Chrome's helpers), which puts a job's command, and each tool the command runs,
in groups of their own. `stop_tree` walks the tree from the supervisor (or from an orphaned command), signals every
process in it and every group those processes lead, and checks identities by start time before the final SIGKILL.
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


# ---------------------------------------------------------------------------------------------------------------
# Process trees: a job's command and everything it started, whatever groups they're in


LSTART_WORDS = 5  # `ps -o lstart` prints "Wed Sep 30 17:09:43 2026"


def table() -> dict[int, dict]:
    """Every live process on this Mac: {pid: {ppid, pgid, start, comm}} from one `ps` call ({} if ps can't run).
    Zombies (exited, waiting to be reaped) are left out: they can't be signalled and hold nothing."""
    try:
        out = procs.run(["/bin/ps", "-ax", "-o", "pid=,ppid=,pgid=,stat=,lstart=,comm="], timeout=15).stdout
    except SlateError:
        return {}
    me = os.getpid()
    rows: dict[int, dict] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 + LSTART_WORDS:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if parts[3].startswith("Z"):
            continue
        comm = " ".join(parts[4 + LSTART_WORDS:])
        if ppid == me and comm.rsplit("/", 1)[-1] == "ps":
            continue  # the ps listing this table: our own helper, never part of a job's tree
        rows[pid] = {"ppid": ppid, "pgid": pgid, "start": " ".join(parts[4:4 + LSTART_WORDS]), "comm": comm}
    return rows


def descendants(root: int, tab: dict[int, dict] | None = None) -> list[int]:
    """Every process under `root` (children, grandchildren, ...), nearest first. `root` itself isn't included."""
    tab = table() if tab is None else tab
    kids: dict[int, list[int]] = {}
    for pid, row in tab.items():
        kids.setdefault(row["ppid"], []).append(pid)
    out: list[int] = []
    todo = list(kids.get(root, []))
    while todo:
        pid = todo.pop(0)
        if pid in out or pid == root:
            continue
        out.append(pid)
        todo.extend(kids.get(pid, []))
    return out


def children(root: int, tab: dict[int, dict] | None = None) -> list[dict]:
    """`root`'s direct children as [{pid, pgid, start, comm}] (the queue uses it to find a job's command)."""
    tab = table() if tab is None else tab
    return [{"pid": pid, **row} for pid, row in sorted(tab.items()) if row["ppid"] == root]


def _signal_each(pids, groups, sig: int) -> None:
    """Signal processes and groups, never this process or its own group (whoever is stopping a job must survive it)."""
    me, my_group = os.getpid(), os.getpgid(0)
    for pid in pids:
        if pid == me:
            continue
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    for pgid in groups:
        if pgid == my_group:
            continue
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _started(lstart: str) -> float | None:
    """`ps -o lstart` ("Wed Sep 30 17:09:43 2026", local time) as epoch seconds."""
    try:
        return time.mktime(time.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y"))
    except (ValueError, OverflowError):
        return None


def orphans_of(job: dict) -> list[dict]:
    """A dead supervisor's command that was never recorded (it died in the moment between starting the command and
    writing its pid down): processes launchd adopted (parent pid 1), started after the job launched, whose command
    line is the job's (after caffeinate and nice exec it, the arguments still are). Returns [{pid, start}]."""
    from slate.jobs import model  # noqa: PLC0415

    needles = _job_needles(job)[1:]  # not the supervisor's own line: it's the one that died
    launched = model.parse_iso(job.get("launched_at") or job.get("started_at"))
    if not needles or launched is None:
        return []
    booted = boot_time()
    if booted and launched.timestamp() < booted:
        return []  # it ran before the last reboot: none of its processes can be alive
    try:
        out = procs.run(["/bin/ps", "-ax", "-ww", "-o", "pid=,ppid=,lstart=,command="], timeout=15).stdout
    except SlateError:
        return []
    found = []
    for line in out.splitlines():
        parts = line.split(None, 2 + LSTART_WORDS)
        if len(parts) < 3 + LSTART_WORDS or parts[1] != "1":
            continue
        lstart = " ".join(parts[2:2 + LSTART_WORDS])
        began = _started(lstart)
        if began is None or began < launched.timestamp() - 2:
            continue
        cmd = _norm(parts[-1])
        if any(n in cmd for n in needles):
            found.append({"pid": int(parts[0]), "start": lstart})
    return found


def signal_tree(root: int, sig: int, *, spare: int | None = None) -> int:
    """Send one signal to everything under `root` (and the groups those processes lead) without waiting. Returns how
    many processes it reached."""
    tab = table()
    members = [pid for pid in descendants(root, tab) if pid != spare]
    _signal_each(members, {tab[pid]["pgid"] for pid in members if tab[pid]["pgid"] == pid}, sig)
    return len(members)


def stop_tree(root: int, *, grace: float = 10.0, spare: int | None = None, include_root: bool = False,
              stamp: str | None = None) -> str:
    """SIGTERM a job's whole process tree, wait up to `grace` seconds, then SIGKILL what's left.

    The tree is `root`'s descendants (plus `root` when `include_root`, e.g. an orphaned command whose supervisor died),
    except `spare` (the supervisor, which stays to record the exit). Each process in it that leads its own group gets
    the group signal too, so a tool's helpers go with it even after they're reparented. With `stamp`, `root` is only
    ours if it started then. Returns "gone" (nothing was running), "terminated" or "killed".
    """
    tab = table()
    if include_root and root in tab and stamp and tab[root]["start"] != stamp:
        return "gone"  # the number belongs to someone else now
    members = ([root] if include_root and root in tab else []) + descendants(root, tab)
    known = {pid: tab[pid]["start"] for pid in members if pid not in (spare, os.getpid()) and pid in tab}
    if not known:
        return "gone"
    groups = {tab[pid]["pgid"] for pid in known if tab[pid]["pgid"] == pid}  # the groups our processes lead
    _signal_each(known, groups, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace)

    def still_here() -> list[int]:
        now = table()
        for pid in descendants(root, now) + ([root] if include_root else []):  # anything started since the snapshot
            if pid not in (spare, os.getpid()) and pid in now and pid not in known:
                known[pid] = now[pid]["start"]
                _signal_each([pid], [], signal.SIGTERM)
        return [pid for pid, start in known.items() if pid in now and now[pid]["start"] == start]

    left = still_here()
    while left and time.monotonic() < deadline:
        reap()
        time.sleep(0.2)
        left = still_here()
    if not left:
        return "terminated"
    _signal_each(left, groups, signal.SIGKILL)
    for _ in range(25):
        reap()
        if not still_here():
            break
        time.sleep(0.2)
    return "killed"


def signal_name(returncode: int | None) -> str | None:
    if returncode is None or returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"
