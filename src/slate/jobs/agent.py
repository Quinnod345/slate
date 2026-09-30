"""The per-user launchd agent that keeps the job queue's runner alive.

`slate jobs agent install` writes ~/Library/LaunchAgents/<label>.plist and loads it into your login session with
`launchctl bootstrap gui/<uid>`; `uninstall` unloads it with `launchctl bootout gui/<uid>/<label>` and removes the
file. The agent runs `python -m slate jobs serve` with this checkout's Python, restarts it if it ever exits
(KeepAlive), starts it at login (RunAtLoad), and gives it a PATH with Homebrew and ~/.local/bin (launchd's own PATH has
neither) and BROWSER=none (nothing Slate runs may open a browser).

Running jobs live in their own sessions, so reloading or removing the agent never kills a job in flight; the next
runner picks the queue up where it was.

Installing it adds a login item, so from an agent it needs `--yes` (ask the person first). `--dry-run` shows the file
and the launchctl commands without touching anything; the tests only ever use that.
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from slate import config, jsonio, paths, procs
from slate.errors import Refused, SlateError

DEFAULT_LABEL = "dev.slate.jobs"
PATH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin"), "/usr/bin", "/bin",
             "/usr/sbin", "/sbin")
LAUNCHCTL = "/bin/launchctl"


def label() -> str:
    return str(config.load().get("jobs", {}).get("agent_label") or DEFAULT_LABEL)


def agents_dir() -> Path:
    override = os.environ.get("SLATE_LAUNCH_AGENTS")
    return Path(override).expanduser() if override else Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return agents_dir() / f"{label()}.plist"


def domain() -> str:
    return f"gui/{os.getuid()}"


def service() -> str:
    return f"{domain()}/{label()}"


def log_file() -> Path:
    return paths.logs_dir() / "jobs" / "runner.launchd.log"


def render() -> dict:
    """The agent's plist, as a dict."""
    env = {"PATH": ":".join(PATH_DIRS), "BROWSER": "none", "PYTHONUNBUFFERED": "1", "LANG": "en_US.UTF-8"}
    # Keep a custom Slate location (a scratch folder, a second disk) that was set when the agent was installed.
    for key in ("SLATE_DATA", "SLATE_FILMS", "SLATE_BRANDS"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return {
        "Label": label(),
        "ProgramArguments": [sys.executable, "-m", "slate", "jobs", "serve"],
        "EnvironmentVariables": env,
        "WorkingDirectory": str(Path.home()),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Standard",
        # Jobs run in their own sessions; this also keeps launchd from killing anything left in the runner's group.
        "AbandonProcessGroup": True,
        "StandardOutPath": str(log_file()),
        "StandardErrorPath": str(log_file()),
    }


def render_bytes() -> bytes:
    return plistlib.dumps(render(), fmt=plistlib.FMT_XML, sort_keys=False)


def _launchctl(*args: str) -> tuple[int, str]:
    proc = procs.run([LAUNCHCTL, *args], timeout=30)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def loaded() -> bool:
    code, _ = _launchctl("print", service())
    return code == 0


def _guard(yes: bool, what: str) -> None:
    ctx = procs.agent_context()
    if ctx and not yes:
        raise Refused(
            f"{what} changes a login item on this Mac, so it waits for a person's yes (this is {ctx}).",
            hint=f"Ask the person, then run it again with --yes. `slate jobs agent {what.split()[0].lower()} "
                 "--dry-run` shows exactly what it would do.",
        )


def install(*, dry_run: bool = False, yes: bool = False) -> dict:
    """Write the plist and (re)load it into the login session."""
    target = plist_path()
    commands = []
    if not dry_run:
        _guard(yes, "Install the job agent")
    already = False if dry_run else loaded()
    if already or dry_run:
        commands.append([LAUNCHCTL, "bootout", service()])
    commands.append([LAUNCHCTL, "bootstrap", domain(), str(target)])
    out = {"label": label(), "plist": str(target), "commands": [" ".join(c) for c in commands], "dry_run": dry_run,
           "log": str(log_file())}
    if dry_run:
        out["plist_xml"] = render_bytes().decode("utf-8")
        out["note"] = "dry run: nothing written, nothing loaded (bootout only runs if the agent is already loaded)"
        return out
    target.parent.mkdir(parents=True, exist_ok=True)
    log_file().parent.mkdir(parents=True, exist_ok=True)
    jsonio.write_text_atomic(target, render_bytes())
    os.chmod(target, 0o644)
    for cmd in commands:
        code, text = _launchctl(*cmd[1:])
        if code != 0 and cmd[1] == "bootstrap":
            raise SlateError(f"launchctl bootstrap failed ({code}): {text.strip()[-400:]}",
                             hint=f"Check the plist with `plutil -lint {target}`, then try again.")
    out["loaded"] = loaded()
    return out


def uninstall(*, dry_run: bool = False, yes: bool = False) -> dict:
    """Unload the agent and remove its plist. Running jobs keep going; a one-shot runner serves them from then on."""
    target = plist_path()
    commands = [[LAUNCHCTL, "bootout", service()]]
    out = {"label": label(), "plist": str(target), "commands": [" ".join(c) for c in commands], "dry_run": dry_run}
    if dry_run:
        out["note"] = "dry run: nothing unloaded, nothing removed"
        return out
    _guard(yes, "Uninstall the job agent")
    if loaded():
        code, text = _launchctl("bootout", service())
        if code != 0:
            raise SlateError(f"launchctl bootout failed ({code}): {text.strip()[-400:]}")
    target.unlink(missing_ok=True)
    out["loaded"] = False
    return out


def status() -> dict:
    """Is the agent installed and loaded, and is a runner serving the queue?"""
    from slate.jobs import runner  # noqa: PLC0415

    target = plist_path()
    info: dict = {"label": label(), "plist": str(target), "installed": target.exists(), "loaded": False,
                  "pid": None, "runner_serving": runner.runner_alive(), "log": str(log_file())}
    try:
        code, text = _launchctl("print", service())
    except SlateError:
        code, text = 1, ""
    if code == 0:
        info["loaded"] = True
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                try:
                    info["pid"] = int(line.split("=", 1)[1])
                except ValueError:
                    pass
            elif line.startswith("state = "):
                info["state"] = line.split("=", 1)[1].strip()
            elif line.startswith("last exit code = "):
                info["last_exit"] = line.split("=", 1)[1].strip()
    return info
