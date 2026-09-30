"""Drive After Effects: run ExtendScript through osascript, under one lock, and read the result back.

The route (the film pipeline's run_jsx.sh, generalized): `osascript` tells AE to `DoScriptFile` a small wrapper
script that pins the run's arguments into `$.global.SLATE_ARGS` and `$.global.SLATE_RUN`, then `$.evalFile`s the real
script. AE launches if it isn't running (a cold start took 41 s on the film Mac). This route needs AE, macOS
Automation permission for the calling app, and ES3 discipline, and it does not need "Allow Scripts to Write Files and
Access Network": building comps, saving projects, reading JSON and aerender all work with that setting off.

AE 26.5 returns "0" to AppleScript whatever the script returns, so results come back through `slate.ae.report`.

Everything here runs inside `ae_lock()`, which is Slate's own `ae` resource lock (the job queue's, when the jobs
module is installed) plus the AE bridge's busy lock (`slate.ae.bridgelock`), so no two scripts ever reach AE at once.

Other things the driver owns:
- `purge()` releases AE's memory caches (not the disk cache) before a long render with AE open;
- `set_software_gpu(aep)` switches a project to Mercury Software Only (aerender crashed twice in the GPU compositor
  under memory pressure on a footage-only project);
- `open_project_state()` reports which project AE has open and whether it has unsaved changes.

Dialogs AE shows at launch (sign-in, the Home screen, crash recovery, missing fonts or footage) sit outside
`beginSuppressDialogs` and can stall a run. A person dismisses them; mac-cua (optional, its app-control half only,
never its browser tools) can do it in the background when the person asks.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from slate import config, log, paths, procs
from slate.ae import bridgelock, prefs, report
from slate.errors import Busy, MissingPrerequisite, SlateError, UsageError

ENGINE_AE = paths.ENGINE_SRC / "ae"
OSASCRIPT = "/usr/bin/osascript"
APPS_DIR = Path("/Applications")
DEFAULT_TIMEOUT_S = 600
LOCK_TIMEOUT_S = 60.0

Runner = Callable[..., Any]


# ---------------------------------------------------------------------------------------------------------------
# Finding After Effects


def installs(apps_dir: Path | None = None) -> list[dict]:
    """Every AE install under /Applications, newest first: {name, app, folder, aerender, version}."""
    base = apps_dir or APPS_DIR
    found = []
    for folder in base.glob("Adobe After Effects *"):
        app = folder / f"{folder.name}.app"
        if not app.exists():
            continue
        aerender = folder / "aerender"
        found.append({"name": folder.name, "app": str(app), "folder": str(folder),
                      "aerender": str(aerender) if aerender.exists() else None,
                      "year": prefs.version_key(folder.name)})
    return sorted(found, key=lambda d: d["year"], reverse=True)


def app_name() -> str:
    """The AE application AppleScript talks to: AE_APP_NAME, slate.toml [ae] app, else the newest install."""
    env = os.environ.get("AE_APP_NAME")
    if env:
        return env
    configured = (config.load().get("ae") or {}).get("app")
    if configured:
        return str(configured)
    found = installs()
    if found:
        return found[0]["name"]
    raise MissingPrerequisite("After Effects isn't installed in /Applications.",
                              hint="Install it from Creative Cloud, or set [ae] app in slate.toml; then `slate doctor`.")


def aerender_path() -> str:
    """aerender: slate.toml [ae] aerender, else the newest AE install's, else PATH."""
    configured = (config.load().get("ae") or {}).get("aerender")
    if configured:
        return str(configured)
    for inst in installs():
        if inst["aerender"]:
            return inst["aerender"]
    found = procs.which("aerender")
    if found:
        return found
    raise MissingPrerequisite("aerender (After Effects' command-line renderer) isn't on this Mac.",
                              hint="Install After Effects from Creative Cloud; aerender comes with it.")


# ---------------------------------------------------------------------------------------------------------------
# The lock


@contextlib.contextmanager
def _local_ae_lock(timeout: float | None, owner: str) -> Iterator[Path]:
    """Slate's own `ae` lock when the jobs module isn't installed: an flock on DATA/locks/ae.lock."""
    path = paths.ensure_dir(paths.locks_dir()) / "ae.lock"
    deadline = time.monotonic() + (timeout if timeout is not None else 10 ** 9)
    with path.open("a+") as fh:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("Another Slate step is using After Effects right now.",
                               hint="Wait for it to finish (`slate job status`), then run this again.") from None
                time.sleep(0.2)
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"owner": owner, "pid": os.getpid(), "at": time.time()}))
            fh.flush()
            yield path
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _slate_ae_lock(timeout: float | None, owner: str) -> Iterator[Any]:
    """The job queue's `ae` resource lock (slate.jobs.locks.hold), or a local flock when jobs isn't installed.

    A job the queue runs already holds its resources and says so in SLATE_JOB_HOLDS; the jobs module then lends the
    job's own slot instead of waiting on itself, and the local fallback does the same.
    """
    try:
        from slate.jobs import locks as job_locks  # noqa: PLC0415 - the jobs module is built in parallel
    except ImportError:
        job_locks = None
    if job_locks is None and "ae" in {x.strip() for x in os.environ.get("SLATE_JOB_HOLDS", "").split(",")}:
        yield "held-by-job"
        return
    if job_locks is not None and callable(getattr(job_locks, "hold", None)):
        try:
            cm = job_locks.hold("ae", timeout=timeout, owner=owner)
        except TypeError:
            cm = job_locks.hold("ae")
        with contextlib.ExitStack() as stack:
            try:
                token = stack.enter_context(cm)
            except TimeoutError as exc:
                raise Busy("Another Slate step is using After Effects right now.",
                           hint="Wait for it to finish (`slate job status`), then run this again.") from exc
            yield token
        return
    with _local_ae_lock(timeout, owner) as path:
        yield path


@contextlib.contextmanager
def ae_lock(*, timeout: float | None = LOCK_TIMEOUT_S, owner: str | None = None, bridge: bool = True) -> Iterator[dict]:
    """The one lock for every route into AE: Slate's `ae` resource lock, then the AE bridge's busy lock.

    Busy (exit 5) when either stays taken past `timeout` seconds. Shared with the bridge: while Slate holds it, a
    bridge call waits (and times out cleanly) instead of raising AE's "second script" modal, and vice versa.
    """
    who = owner or os.environ.get("SLATE_JOB_ID") or f"slate-ae-{os.getpid()}"
    with _slate_ae_lock(timeout, who) as token:
        cm = bridgelock.hold(timeout=timeout, owner=who) if bridge else contextlib.nullcontext([])
        with cm as bridge_locks:
            yield {"owner": who, "slate": str(token), "bridge": [str(p) for p in bridge_locks]}


# ---------------------------------------------------------------------------------------------------------------
# Running a script


def applescript_string(value: str) -> str:
    """An AppleScript double-quoted string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def osascript_argv(jsx: Path, *, app: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[str]:
    """The osascript call: DoScriptFile inside a timeout block long enough for the slowest build."""
    return [OSASCRIPT,
            "-e", f"with timeout of {int(timeout_s)} seconds",
            "-e", f"tell application {applescript_string(app)} to DoScriptFile {applescript_string(str(jsx))}",
            "-e", "end timeout"]


def _js_literal(obj: Any) -> str:
    # JSON is an ES3 object literal once U+2028/U+2029 are escaped; ensure_ascii escapes every non-ASCII character.
    return json.dumps(obj, ensure_ascii=True, sort_keys=True)


def wrapper_source(target: Path, args: dict, run: dict) -> str:
    """The per-run wrapper AE executes: pin the arguments and the report channel, then evaluate the real script."""
    return ("// Written by Slate for one run; safe to delete.\n"
            f"$.global.SLATE_ARGS = {_js_literal(args)};\n"
            f"$.global.SLATE_RUN = {_js_literal(run)};\n"
            f"$.evalFile(new File({_js_literal(str(target))}));\n")


def runs_dir() -> Path:
    return paths.data() / "ae" / "runs"


def _scripting_on() -> bool:
    try:
        return bool(prefs.check()["scripting_on"])
    except SlateError:
        return False


def _osascript_error(proc: Any, app: str) -> SlateError:
    text = ((proc.stderr or "") + (proc.stdout or "")).strip()
    if "-1743" in text or "Not authorized" in text:
        return MissingPrerequisite(
            f"macOS didn't let this app control {app} (Automation permission).",
            hint="In System Settings > Privacy & Security > Automation, allow your terminal (or agent app) to "
                 f"control {app}, then run this again.")
    if "-1712" in text or "timed out" in text.lower():
        return SlateError(f"{app} didn't answer in time. It may be showing a dialog (sign-in, crash recovery, "
                          "missing fonts): dismiss it, then run this again.")
    if "-1728" in text or "-2753" in text or "Can’t get application" in text or "Can't get application" in text:
        return MissingPrerequisite(f"AppleScript can't find an application called {app!r}.",
                                   hint="Set AE_APP_NAME or [ae] app in slate.toml to your AE's exact name.")
    tail = text[-600:] if text else "no output"
    return SlateError(f"osascript exited with {proc.returncode} while running a script in {app}: {tail}")


def run_jsx(path: str | os.PathLike, *, args: dict | None = None, timeout: int = DEFAULT_TIMEOUT_S,
            channel: str = "run", runner: Runner | None = None, lock: bool = True,
            lock_timeout: float | None = LOCK_TIMEOUT_S) -> dict:
    """Run an ExtendScript file inside After Effects and return its report.

    Returns {ok, status, run_id, script, channel, report, report_channel, seconds, lock}. `status` is what the script
    reported ("OK", "ERROR …", "ABORT …"), or "MISSING" when no report arrived (AE refused the script, a modal
    stalled it, or it isn't one of Slate's reporting scripts). Callers decide what a status means; raising is kept
    for failures of the route itself (no AE, no Automation permission, a timeout).

    This does not check who started it: the CLI and the build/masters/smoke functions do that (`slate.ae.guard`).
    """
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise UsageError(f"No ExtendScript file at {target}.")
    run_id = f"r{int(time.time() * 1000):x}{uuid.uuid4().hex[:6]}"
    run_dir = paths.ensure_dir(runs_dir() / run_id)
    report_file = run_dir / "report.json" if _scripting_on() else None
    run = {"run_id": run_id, "channel": channel, "section": report.SECTION,
           "report_file": str(report_file) if report_file else None, "engine_ae": str(ENGINE_AE),
           "run_dir": str(run_dir)}
    wrapper = run_dir / "wrapper.jsx"
    wrapper.write_text(wrapper_source(target, args or {}, run), encoding="utf-8")
    app = app_name()
    argv = osascript_argv(wrapper, app=app, timeout_s=timeout)
    run_proc = runner or procs.run
    started = time.monotonic()
    lock_cm = ae_lock(timeout=lock_timeout) if lock else contextlib.nullcontext({})
    with lock_cm as lock_info:
        log.info(f"running {target.name} in {app}")
        proc = run_proc(argv, timeout=timeout + 30)
    seconds = round(time.monotonic() - started, 2)
    if getattr(proc, "returncode", 1) != 0:
        raise _osascript_error(proc, app)
    rep = report.read_run(run_id, channel=channel, report_file=report_file)
    status = str(rep.get("status") or "UNKNOWN")
    return {"ok": status == "OK", "status": status, "run_id": run_id, "script": str(target), "channel": channel,
            "report": rep.get("data") or {}, "report_channel": rep.get("channel"), "seconds": seconds,
            "lock": lock_info, "run_dir": str(run_dir)}


def script(name: str) -> Path:
    """One of Slate's own ExtendScript files in engine/ae/."""
    p = ENGINE_AE / name
    if not p.is_file():
        raise MissingPrerequisite(f"Slate's ExtendScript file {name} is missing from {ENGINE_AE}.",
                                  hint="Reinstall Slate (`slate update`).")
    return p


def expect_ok(result: dict, what: str) -> dict:
    """Raise with the script's own words when a run didn't report OK. ABORT on unsaved work is Busy (exit 5)."""
    status = result.get("status", "")
    if status == "OK":
        return result
    if status.startswith("ABORT") and "unsaved" in status:
        raise Busy(f"{what}: After Effects has a project with unsaved changes, and Slate never discards them.",
                   hint="Save your work in After Effects (or close that project), then run this again.")
    if status == "MISSING":
        raise SlateError(f"{what}: After Effects didn't report back. It may be showing a dialog, or another script "
                         "was running.", hint="Look at After Effects, dismiss any dialog, then run this again.")
    raise SlateError(f"{what} failed in After Effects: {status}")


# ---------------------------------------------------------------------------------------------------------------
# Small operations


def open_project_state(*, runner: Runner | None = None) -> dict:
    """Which project AE has open, whether it has unsaved changes, and the render queue's state."""
    return expect_ok(run_jsx(script("open_project.jsx"), runner=runner), "Reading AE's open project")["report"]


def purge(*, runner: Runner | None = None) -> dict:
    """Release AE's memory caches (not the disk cache) before a long render with AE open."""
    return expect_ok(run_jsx(script("purge.jsx"), runner=runner), "Purging AE's memory caches")["report"]


def set_software_gpu(aep: str | os.PathLike, *, runner: Runner | None = None) -> dict:
    """Switch a project to Mercury Software Only and save it (a dirty open project is preserved first)."""
    preserve = paths.ensure_dir(paths.data() / "ae" / "preserved")
    res = run_jsx(script("software_gpu.jsx"), args={"aep": str(Path(aep).resolve()), "preserve_dir": str(preserve)},
                  runner=runner)
    return expect_ok(res, "Switching the project to software rendering")["report"]


def font_check(families: list[str], *, runner: Runner | None = None) -> dict:
    """Which of these font families AE can see (by family name). Read-only in AE."""
    pattern = "^(" + "|".join(re.escape(f) for f in families) + ")$" if families else ""
    res = run_jsx(script("font_check.jsx"), args={"families": families, "pattern": pattern}, runner=runner)
    return expect_ok(res, "Checking fonts in After Effects")["report"]
