"""Run child processes the Slate way.

Every child gets `BROWSER=none` (Dia is this Mac's default browser, and some tools open it on their own), no shell, a
closed stdin (nothing ever waits on a prompt: Oneiro's research slices run with stdin as a pipe and are killed after
ten minutes), a timeout, and a PATH that includes Homebrew and ~/.local/bin (the OpenClaw gateway's PATH lacks the
latter). Errors are redacted before they are shown.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from slate.errors import MissingPrerequisite, SlateError

# Searched first, in this order, whatever the caller's PATH says. macOS's default PATH (/etc/paths) puts /usr/local/bin
# first, and on this kind of Mac that folder can hold stale Intel builds (an ffmpeg 7.1 and a Node 21 were found there),
# which would fail Slate's floors or change results. Arm64 Homebrew and ~/.local/bin (Slate's own shim) win.
PREFERRED_PATH = ["/opt/homebrew/bin", "/opt/homebrew/sbin", str(Path.home() / ".local" / "bin")]
# Appended when missing, so a bare launchd or gateway PATH still finds the basics.
FALLBACK_PATH = ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def child_path(current: str | None = None) -> str:
    """The PATH every Slate child gets: preferred folders first, then the caller's, then fallbacks, no duplicates."""
    parts: list[str] = []
    for p in [*PREFERRED_PATH, *(current if current is not None else os.environ.get("PATH", "")).split(os.pathsep),
              *FALLBACK_PATH]:
        if p and p not in parts:
            parts.append(p)
    return os.pathsep.join(parts)


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = child_path(env.get("PATH", ""))
    env["BROWSER"] = "none"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    env["BROWSER"] = "none"  # set last: nothing may turn it back on
    return env


def _known(tool: str) -> list[str]:
    if tool == "aerender":
        return sorted(glob.glob("/Applications/Adobe After Effects */aerender"), key=_version_key, reverse=True)
    return []


def _version_key(path: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", path)) or (0,)


def which(tool: str) -> str | None:
    """Find a tool on the augmented PATH or in a known install location (aerender lives inside the AE folder)."""
    if os.path.isabs(tool):
        return tool if os.access(tool, os.X_OK) else None
    found = shutil.which(tool, path=child_env()["PATH"])
    if found:
        return found
    for candidate in _known(tool):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def require(tool: str, hint: str | None = None) -> str:
    path = which(tool)
    if not path:
        raise MissingPrerequisite(f"{tool} isn't installed or isn't on PATH.",
                                  hint=hint or "Run `slate doctor` to see what's missing and how to install it.")
    return path


def run(argv: Sequence[str | os.PathLike], *, cwd: str | os.PathLike | None = None,
        env: Mapping[str, str] | None = None, timeout: float | None = None, check: bool = False,
        capture: bool = True, input: str | bytes | None = None, text: bool = True) -> subprocess.CompletedProcess:
    """Run a command without a shell. With check=True a non-zero exit raises SlateError with the tail of stderr."""
    if isinstance(argv, (str, bytes)):
        raise TypeError("procs.run takes an argv list, never a shell string")
    args = [os.fspath(a) for a in argv]
    exe = which(args[0]) if not os.path.isabs(args[0]) else args[0]
    if not exe:
        raise MissingPrerequisite(f"{args[0]} isn't installed or isn't on PATH.",
                                  hint="Run `slate doctor` to see what's missing.")
    args[0] = exe
    # Each child leads its own process group, so a timeout or an interrupt can stop everything it started (Chrome
    # spawns a tree of helpers; killing only the leader used to orphan them).
    try:
        child = subprocess.Popen(
            args, cwd=os.fspath(cwd) if cwd else None, env=child_env(env), text=text,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise MissingPrerequisite(f"{Path(args[0]).name} couldn't be started: {exc.strerror}.",
                                  hint="Run `slate doctor` to see what's missing.") from exc
    try:
        out, err = child.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stop_group(child)
        raise SlateError(f"{Path(args[0]).name} took longer than {timeout:.0f} s and was stopped.") from exc
    except BaseException:
        stop_group(child)
        raise
    proc = subprocess.CompletedProcess(args, child.returncode, out, err)
    if check and proc.returncode != 0:
        from slate.secrets import redact  # noqa: PLC0415

        tail = (proc.stderr or proc.stdout or "") if capture else ""
        if isinstance(tail, bytes):
            tail = tail.decode("utf-8", "replace")
        tail = redact(tail.strip()[-1500:])
        raise SlateError(f"{Path(args[0]).name} exited with {proc.returncode}." + (f"\n{tail}" if tail else ""))
    return proc


def stop_group(child: subprocess.Popen, grace: float = 5.0) -> None:
    """Stop a child and everything in its process group: SIGTERM, a grace period, then SIGKILL."""
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)  # stragglers still in the group after the leader exits
    except (ProcessLookupError, PermissionError):
        pass
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def spawn_detached(argv: Sequence[str | os.PathLike], *, log_path: str | os.PathLike,
                   cwd: str | os.PathLike | None = None, env: Mapping[str, str] | None = None) -> int:
    """Start a process in its own session (it outlives the agent that started it). Returns its pid."""
    args = [os.fspath(a) for a in argv]
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        proc = subprocess.Popen(args, cwd=os.fspath(cwd) if cwd else None, env=child_env(env),
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)
    return proc.pid


def pgrep(pattern: str, full: bool = False) -> list[int]:
    args = ["/usr/bin/pgrep", "-f" if full else "-x", pattern]
    proc = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return [int(x) for x in proc.stdout.split()] if proc.returncode == 0 else []


def ae_running() -> bool:
    """After Effects' UI app is open (same test the film pipeline's ae_pref.py uses)."""
    return bool(pgrep("After Effects"))


def aerender_running() -> bool:
    """aerender, or the aerendercore it launches, is running. Matched by process name, never by command line: Adobe's
    IPC broker outlives every render with aerendercore's path in its arguments, so a command-line match kept saying
    aerender was running after it had finished (and the master's disk-cache restore was refused every time)."""
    return bool(pgrep("aerender") or pgrep("aerendercore"))


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parent_names(limit: int = 12) -> list[str]:
    names: list[str] = []
    pid = os.getppid()
    for _ in range(limit):
        if pid <= 1:
            break
        proc = subprocess.run(["/bin/ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        line = proc.stdout.strip()
        if not line:
            break
        ppid, _, comm = line.partition(" ")
        names.append(os.path.basename(comm.strip()).lower())
        try:
            pid = int(ppid)
        except ValueError:
            break
    return names


def agent_context() -> str | None:
    """Which agent is running us: 'claude-code', 'codex', 'openclaw', or None for a person at a Terminal.

    Environment markers come first; the parent-process chain is the fallback. `SLATE_CONTEXT` overrides both
    (the doctor spike and the evals set it to label a run).
    """
    env = os.environ
    if env.get("SLATE_CONTEXT"):
        return env["SLATE_CONTEXT"]
    if env.get("CLAUDECODE") == "1" or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude-code"
    if any(k.startswith("OPENCLAW") for k in env):
        return "openclaw"
    if any(k.startswith("CODEX_") for k in env):
        return "codex"
    chain = _parent_names()
    for name in chain:
        if "openclaw" in name:
            return "openclaw"
        if "codex" in name:
            return "codex"
        if name == "claude" or name.startswith("claude"):
            return "claude-code"
    return None


def disk_free_bytes(path: str | os.PathLike) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free
