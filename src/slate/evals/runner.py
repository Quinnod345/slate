"""Running evals: dry run, replay, and live.

- **dry run** (`--dry-run`) builds every command and prints it. Nothing runs, nothing is written.
- **replay** (the default) grades transcripts already on disk: `evals/fixtures/recorded/<case>/<config>/` (real runs
  saved with `--record`), or with `--fixtures synthetic` the hand-written transcripts that prove the graders. It costs
  nothing.
- **live** (`--live`) runs the agents for real. That costs model calls, never vendor credits: every run gets replay
  mode, sandbox film and data folders, and a zero-cap budget, so even a stray `--live` inside the run is refused before
  it spends.

Each live pair runs in its own folder under the run: `work/` (the agent's working folder, with `films/`), the Slate
data folder with its zero-cap budget, the transcript, stderr, `meta.json` (how it ended, what the sandbox held), the
child-process probe's log and the BROWSER tripwire's log. The run's `report.json` is written last.

The runner starts agents itself rather than through `slate.procs.run`: an eval needs a stdin pipe for the slice, a
process group it can kill, and a BROWSER that is a tripwire instead of `none`. (Python's webbrowser treats a failing
`none` as "try the next browser" and falls back to the Mac's default one; the tripwire answers "done" and records the
attempt.) Slate's own child processes still get BROWSER=none from slate.procs, and the probe checks that they do.

Containment. Every sandbox carries the sandbox notice (AGENTS.md, CLAUDE.md; OpenClaw main gets it in its message),
shares this Mac's render slot (its SLATE_DATA/locks/render links to the machine's), and has its own job queue
cancelled when the pair ends. While an agent runs, a watchdog reads its transcript as it streams and stops it at the
first write outside its sandbox or the first reach for another agent's credentials or mac-cua's browser; a live run
stops there too, so one escape isn't repeated by the pairs after it (`--keep-going` runs them anyway).
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from slate import __version__, jsonio, log, paths, procs
from slate.errors import UsageError
from slate.evals import checks, commands, containment, preconditions, report, suite, transcript
from slate.gates import GateResult

# A value never runs into a backslash: in a JSON-escaped transcript the backslash belongs to the next character's
# escape (`\"`), and eating it would end the string early and break the line.
_SCRUB = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password|xi-api-key|authorization)\s*[=:]\s*)[^\s\"',;\\]{8,}"),
     r"\1[redacted]"),
    (re.compile(r"\b(HF_KEY|HF_API_KEY|HF_API_SECRET|ELEVENLABS_API_KEY)=[^\s\"'\\]+"), r"\1=[redacted]"),
    # An email address (an agent's sign-in shows in OpenClaw's session status). It starts after a JSON escape (`\n`)
    # rather than eating the escape's letter. An image's scale suffix (AppIcon60x60@2x.png) and a package's scope or
    # version (@scope/cli, react@18.2.0) aren't addresses.
    (re.compile(r"(?:(?<=\\[nrtbf\"/])|(?<![A-Za-z0-9._%+\\-]))[A-Za-z0-9._%+-]+@(?!\d+x\.)(?:[A-Za-z0-9-]+\.)+"
                r"[A-Za-z]{2,}(?![A-Za-z0-9-])"), "[email]"),
]


def scrub(text: str) -> str:
    """Take key-shaped strings and the home folder's path out of a transcript before it's saved in the repo. The path
    becomes "~"; the account name, which still shows where a folder's name is built from the path
    (~/.claude/projects/-Users-<name>-…, a temp folder named the same way) and in `ls -l` listings, becomes "[user]".
    The replacements have no quotes or backslashes, so JSON stays valid."""
    for rx, repl in _SCRUB:
        text = rx.sub(repl, text)
    home = Path.home()
    text = text.replace(str(home), "~")
    if len(home.name) >= 4:  # a very short account name could be an ordinary word
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(home.name)}(?![A-Za-z0-9])", "[user]", text)
    return text


def new_run_id(mode: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}"


def runs_root(out: Path | None = None) -> Path:
    return Path(out) if out else paths.data() / "evals" / "runs"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _precondition_values() -> dict:
    return {"home": str(Path.home()), "repo": str(paths.REPO), "evals_dir": str(suite.evals_dir())}


# ---- grading one folder ----------------------------------------------------------------------------------------------

def grade_folder(folder: Path, case: suite.Case, config_id: str, configs: dict, *, fmt: str | None = None) -> dict:
    cfg = suite.config_entry(configs, config_id)
    t = transcript.load(folder, fmt or cfg["transcript"]["format"])
    results, skipped = checks.grade(t, case, config_id, configs)
    return _pair_result(case, config_id, results, skipped, source=str(folder), ended=t.ended,
                        problems=t.problems, synthetic=bool(t.meta.get("synthetic")))


def _pair_result(case, config_id: str, results: list[GateResult], skipped: dict, *, source: str, ended: str,
                 problems: list[str] | None = None, synthetic: bool = False) -> dict:
    status = _overall(results)
    not_passed = [r for r in results if r.status != "PASS"]
    summary = "every check passed" if status == "PASS" else "; ".join(
        f"{r.gate}: {r.summary}" for r in not_passed[:3])
    return {"case": case.id, "config": config_id, "group": case.group, "status": status, "summary": summary,
            "checks": [r.to_dict() for r in results], "not_applicable": skipped, "source": source, "ended": ended,
            "problems": problems or [], "synthetic": synthetic}


def _overall(results: list[GateResult]) -> str:
    from slate.gates import overall  # noqa: PLC0415

    return overall(results)


def _not_run(case, config_id: str, status: str, why: str, source: str = "") -> dict:
    return {"case": case.id, "config": config_id, "group": case.group, "status": status, "summary": why,
            "checks": [], "not_applicable": {}, "source": source, "ended": "not run", "problems": [],
            "synthetic": False}


# ---- modes -------------------------------------------------------------------------------------------------------------

def dry_run(pairs, configs: dict, *, run_id: str | None = None, out: Path | None = None,
            allow_host_env: bool = False, services: bool = False) -> dict:
    """Every command, built and returned. Nothing runs and nothing is written."""
    run_id = run_id or new_run_id("dry-run")
    run_dir = runs_root(out) / run_id
    invocations = [commands.plan(case, cid, configs, run_id=run_id, run_dir=run_dir, allow_host_env=allow_host_env,
                                 services=services) for case, cid in pairs]
    return {"run_id": run_id, "mode": "dry-run", "run_dir": str(run_dir),
            "invocations": [inv.to_dict() for inv in invocations],
            "text": "\n\n".join(commands.render(inv) for inv in invocations)}


def replay(pairs, configs: dict, *, fixtures: str = "recorded", run_id: str | None = None, out: Path | None = None,
           save: bool = True, services: bool = False) -> dict:
    """Grade transcripts already on disk."""
    run_id = run_id or new_run_id(f"replay-{fixtures}")
    started = _now()
    results: list[dict] = []
    if fixtures == "synthetic":
        wanted = {(c.id, cid) for c, cid in pairs}
        cases = {c.id: c for c, _ in pairs}
        for folder, meta in suite.synthetic_fixtures():
            if (meta["case"], meta["config"]) not in wanted:
                continue
            res = grade_folder(folder, cases[meta["case"]], meta["config"], configs)
            res["synthetic"] = True
            res["fixture"] = folder.name
            res["expected"] = meta["expect"]
            res["agrees"] = _agrees(res, meta["expect"])
            results.append(res)
    else:
        for case, cid in pairs:
            folder = suite.recorded_fixture(case.id, cid)
            if case.pending:
                pre = preconditions.evaluate(case.precondition, _precondition_values(), services=services)
                if not pre["met"]:
                    results.append(_not_run(case, cid, "PENDING", pre["summary"]))
                    continue
            if not (folder / "transcript.jsonl").exists():
                results.append(_not_run(case, cid, "UNCHECKED",
                                        "no recording yet (`slate evals run --live --record` makes one)", str(folder)))
                continue
            results.append(grade_folder(folder, case, cid, configs))
    summary = report.summarize(results, run_id=run_id, mode="replay", fixtures=fixtures, started=started,
                               finished=_now())
    if save:
        report.save(runs_root(out) / run_id, summary)
    return summary


def _agrees(result: dict, expect: dict) -> bool:
    failed = sorted(c["gate"] for c in result["checks"] if c["status"] == "FAIL")
    unchecked = sorted(c["gate"] for c in result["checks"] if c["status"] == "UNCHECKED")
    return (result["status"] == expect["status"] and failed == sorted(expect["failed"])
            and unchecked == sorted(expect.get("unchecked") or []))


CONTAINMENT_CHECKS = ("stays_in_sandbox", "no_foreign_access")


def escaped(result: dict) -> list[str]:
    """The containment checks a graded pair failed: a write outside its sandbox, another agent's credentials, mac-cua's
    browser."""
    return [c["gate"] for c in result.get("checks") or [] if c["gate"] in CONTAINMENT_CHECKS and c["status"] == "FAIL"]


def live(pairs, configs: dict, *, run_id: str | None = None, out: Path | None = None, record: bool = False,
         allow_host_env: bool = False, services: bool = False, keep_going: bool = False) -> dict:
    """Run the agents for real (model calls only) and grade what they did. The run stops at the first pair that gets
    out of its sandbox (unless keep_going): the pairs after it would only repeat the escape."""
    run_id = run_id or new_run_id("live")
    run_dir = runs_root(out) / run_id
    started = _now()
    results: list[dict] = []
    stopped: str | None = None
    for case, cid in pairs:
        if stopped:
            results.append(_not_run(case, cid, "UNCHECKED", stopped))
            continue
        inv = commands.plan(case, cid, configs, run_id=run_id, run_dir=run_dir, allow_host_env=allow_host_env,
                            services=services)
        if inv.skip:
            status = "PENDING" if case.pending else "UNCHECKED"
            results.append(_not_run(case, cid, status, inv.skip))
            continue
        log.info(f"evals: {case.id} on {cid} …")
        res = run_pair(inv, case, configs, host_films_check=not inv.env_reaches_tools)
        if record:
            _record(Path(inv.pair_dir), case.id, cid, inv.transcript_format)
        results.append(res)
        failed = escaped(res)
        if failed and not keep_going:
            stopped = (f"not run: the run stopped after {case.id} on {cid} failed {', '.join(failed)} "
                       f"(see {inv.pair_dir}); --keep-going runs the rest anyway")
            log.warn(f"evals: {case.id} on {cid} got out of its sandbox ({', '.join(failed)}); stopping the run here. "
                     f"What it did is in {inv.pair_dir}.")
    summary = report.summarize(results, run_id=run_id, mode="live", fixtures=None, started=started, finished=_now())
    if stopped:
        summary["stopped"] = stopped
    report.save(run_dir, summary)
    return summary


# ---- one live pair -------------------------------------------------------------------------------------------------------

def prepare(inv: commands.Invocation, case: suite.Case) -> list[str]:
    """Make the pair's sandbox: working folder, films, Slate data with a zero-cap budget, empty probe and tripwire
    logs, the MCP config, and the case's setup (a scaffolded film, then its files where nothing is yet)."""
    notes: list[str] = []
    pair, work = Path(inv.pair_dir), Path(inv.workdir)
    for folder in (work, work / "films"):
        folder.mkdir(parents=True, exist_ok=True)
    data = Path(inv.env_set.get("SLATE_DATA", str(pair / "slate-data")))
    data.mkdir(parents=True, exist_ok=True)
    Path(inv.env_set.get("SLATE_BRANDS", str(pair / "slate-brands"))).mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(data / "budget.json", inv.budget)
    if inv.shared_locks:
        shared = share_locks(data, inv.shared_locks)
        if shared:
            notes.append("shares this Mac's " + ", ".join(f"{r} slot ({path})" for r, path in shared.items()))
    for key in ("SLATE_EVAL_PROBE_LOG", "SLATE_EVAL_TRIPWIRE_LOG"):
        if inv.env_set.get(key):
            Path(inv.env_set[key]).parent.mkdir(parents=True, exist_ok=True)
            Path(inv.env_set[key]).touch()
    for path, content in inv.files.items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
    film = (case.setup or {}).get("film")
    if film:
        notes.append(_scaffold_film(film, work / "films", inv))
    for rel, content in ((case.setup or {}).get("files") or {}).items():
        target = work / rel
        if target.exists():
            continue  # a scaffolded film's own files win over the case's fallbacks
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(suite.expand(content, {"workdir": str(work), "home": str(Path.home())}), encoding="utf-8")
    return [n for n in notes if n]


def _scaffold_film(film: dict, films_dir: Path, inv: commands.Invocation) -> str:
    """Make the case's film with slate.state when that module can; otherwise the case's static files stand in."""
    saved = {k: os.environ.get(k) for k in ("SLATE_FILMS", "SLATE_DATA", "SLATE_BRANDS", "SLATE_FAKE_VENDORS")}
    try:
        for key in saved:
            if key in inv.env_set:
                os.environ[key] = inv.env_set[key]
        from slate.state import film as film_state  # noqa: PLC0415

        film_state.create(film["id"], brand=film["brand"], format=film["format"],
                          mode=film.get("mode", "supervised"), films_dir=films_dir)
        return f"scaffolded film {film['id']} with slate.state"
    except Exception as exc:  # noqa: BLE001 - the case's static files are the fallback
        return f"slate.state couldn't scaffold {film['id']} ({type(exc).__name__}: {exc}); using the case's files"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---- one render slot for the whole Mac ---------------------------------------------------------------------------------
# A sandbox's SLATE_DATA holds its own locks, so without this a render an eval agent starts would run beside the
# machine's queue. Each shared resource's folder in the sandbox links to this Mac's own (paths.locks_dir() here, in the
# runner, is the machine's), so both hold the same fcntl slot files; the sandbox's slate.toml carries this Mac's render
# slot count. Its runner lock and its queue stay its own.


def share_locks(data: Path, resources: list[str], machine_locks: Path | None = None) -> dict[str, str]:
    """Link the sandbox's lock folder for each resource to this Mac's. Returns {resource: the machine's folder}."""
    from slate.jobs import locks  # noqa: PLC0415 (only live runs need the queue's locks)

    machine_locks = Path(machine_locks or paths.locks_dir())
    shared: dict[str, str] = {}
    if os.path.realpath(machine_locks).startswith(os.path.realpath(data) + os.sep):
        return shared  # run from inside the sandbox itself: there's no machine's slot to share from here
    for resource in resources:
        target = machine_locks / locks.check(resource)
        target.mkdir(parents=True, exist_ok=True)
        link = data / "locks" / resource
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            continue  # a folder of its own from an earlier pass: left as it is
        link.symlink_to(target, target_is_directory=True)
        shared[resource] = str(target)
    if "render" in shared:
        (data / "slate.toml").write_text(f"[render]\nslots = {locks.slots('render')}  # this Mac's, shared\n",
                                         encoding="utf-8")
    return shared


def _lock_held(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False


def _quiet_env(env: dict[str, str]) -> dict[str, str]:
    """The agent's environment for the runner's own Slate commands, without the child-process probe (its log is the
    agent's evidence)."""
    out = {k: v for k, v in env.items() if k != "SLATE_EVAL_PROBE_LOG"}
    probe = str(suite.PROBE_DIR)
    parts = [p for p in (out.get("PYTHONPATH") or "").split(os.pathsep) if p and p != probe]
    if parts:
        out["PYTHONPATH"] = os.pathsep.join(parts)
    else:
        out.pop("PYTHONPATH", None)
    return out


def stop_sandbox_queue(inv: commands.Invocation, env: dict[str, str], wait_s: float = 20.0) -> dict:
    """Cancel whatever the agent left queued or running in its sandbox's own job queue, and make sure the one-shot
    runner `slate job submit` started there (in a session of its own, so it outlives the agent) is gone."""
    data = Path(inv.env_set.get("SLATE_DATA") or "")
    if not data.name or not (data / "jobs").is_dir():
        return {}
    quiet = _quiet_env(env)
    slate = [sys.executable, "-m", "slate"]
    done: dict = {"cancelled": []}
    try:
        listed = subprocess.run(slate + ["job", "list", "--state", "queued", "--state", "running", "--json"],
                                env=quiet, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        jobs = json.loads(listed.stdout or "{}").get("jobs") or []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": f"couldn't list the sandbox's jobs: {exc}"}
    for job in jobs:
        subprocess.run(slate + ["job", "cancel", str(job["id"]), "--grace", "3"], env=quiet, capture_output=True,
                       text=True, timeout=60, stdin=subprocess.DEVNULL)
        done["cancelled"].append(job["id"])
    runner_lock = data / "locks" / "runner.lock"
    deadline = time.monotonic() + wait_s
    while _lock_held(runner_lock) and time.monotonic() < deadline:
        time.sleep(0.5)
    if _lock_held(runner_lock):
        pid = int((jsonio.read(data / "jobs" / "runner.json", default={}) or {}).get("pid") or 0)
        cmdline = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL).stdout if pid else ""
        if pid and "jobs serve" in cmdline:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                done["runner"] = f"stopped (pid {pid})"
            except (ProcessLookupError, PermissionError) as exc:
                done["runner"] = f"couldn't stop pid {pid}: {exc}"
        else:
            done["runner"] = "still held, and no runner of the sandbox's own to stop"
    return done if done["cancelled"] or done.get("runner") else {}


def _descendants(pid: int) -> list[int]:
    """Every process under `pid`, read from ps before anything is stopped (once a parent goes, its children are
    launchd's and the tree can't be followed)."""
    try:
        listing = subprocess.run(["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10,
                                 stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    out, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            out.append(child)
            todo.append(child)
    return out


def _kill_group(proc: subprocess.Popen, grace: float) -> None:
    """Stop the agent, its process group, and anything under it that started a group of its own (a shell command an
    agent runs in a session of its own would otherwise outlive it)."""
    strays = [p for p in _descendants(proc.pid) if _pgid(p) not in (None, proc.pid)]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for stray in strays:
            try:
                os.kill(stray, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    else:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    for stray in strays:  # anything still there gets the hard stop
        try:
            os.kill(stray, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


WATCH_EVERY_S = 5.0
# A renderer an agent can start without saying so in its transcript (a script of its own that pipes frames through
# rsvg-convert into ffmpeg, as the slice did on 2026-10-01): the processes under the agent show it.
_RENDER_PROCESS_RE = re.compile(r"^(?:\S*/)?(?:ffmpeg|rsvg-convert|chrome-headless-shell|headless_shell)(?:\s|$)|"
                                r"Chrome for Testing|/ms-playwright/|\bremotion\b\S*\s+(?:render|still)\b|"
                                r"engine/cli\.mjs\s+(?:web-)?render")


def render_processes(pid: int) -> list[str]:
    """The renderers running under the agent right now (their command lines)."""
    found = []
    under = set(_descendants(pid))
    if not under:
        return found
    try:
        listing = subprocess.run(["/bin/ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10,
                                 stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return found
    for line in listing.splitlines():
        head, _, command = line.strip().partition(" ")
        if head.isdigit() and int(head) in under and _RENDER_PROCESS_RE.search(command.strip()):
            found.append(scrub(command.strip())[:200])
    return found


def watchdog(inv: commands.Invocation, out_path: Path):
    """A check the runner makes every few seconds while the agent runs. It returns why to stop the agent now, or
    None: ("escape", lines) for a write outside the sandbox the disk confirms, another agent's credentials or mac-cua's
    browser, and
    ("render", lines) for the first render of a case that stops its run on purpose (cut_off), whose checks are about a
    production's first moves. Only for agents that stream their transcript (OpenClaw's comes back after the turn)."""
    if inv.transcript_source != "stdout":
        return None
    home = str(Path.home())
    place = containment.Place(home=home, case_id=inv.case_id, config_id=inv.config_id, pair=inv.pair_dir,
                              work=inv.workdir, cwd=inv.tools_cwd or inv.workdir, known=True)

    since = time.time()

    def look(agent_pid: int | None = None) -> tuple[str, list[str], list[int]] | None:
        try:
            text = out_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        t = transcript.parse(text, inv.transcript_format)
        # A write stops the agent once the disk shows it (a misread command never stops a contained run); an endpoint,
        # a credential or a browser stops it at once. The grader still judges every finding from the transcript.
        found = [f for f in containment.escapes(t, place) if containment.on_disk(f, since)]
        found += containment.credential_reads(t, place) + containment.mac_cua_browser(t)
        if found:
            return "escape", [f.line() for f in found], []
        if not inv.cut_off:
            return None
        why = [f.line() for f in containment.render_starts(t)]
        if not why and agent_pid:
            why = [f"the agent started a renderer: {cmd}" for cmd in render_processes(agent_pid)]
        # The commands the cut stops are the harness's doing, not a wait on stdin (no_stdin_wait reads cut_events).
        running = [e.index for e in t.commands() if e.status == "in_progress"]
        return ("render", why, running) if why else None

    return look


def execute(inv: commands.Invocation, env: dict[str, str], *, watch=None) -> dict:
    """Start the agent in its own process group, feed stdin the way its configuration says, and stop it (group and
    all) when time runs out, or as soon as `watch` finds it outside its sandbox. Stdout goes straight to disk."""
    pair = Path(inv.pair_dir)
    out_path = pair / ("run.json" if inv.transcript_source == "collect" else "transcript.jsonl")
    err_path = pair / "stderr.txt"
    started = time.monotonic()
    stdin = subprocess.DEVNULL if inv.stdin == "devnull" else subprocess.PIPE
    with out_path.open("wb") as out, err_path.open("wb") as err:
        try:
            proc = subprocess.Popen(inv.argv, cwd=inv.cwd, env=env, stdin=stdin, stdout=out, stderr=err,
                                    start_new_session=True)
        except OSError as exc:
            return {"exit_code": None, "killed": False, "duration_s": 0.0,
                    "run_error": f"couldn't start {inv.argv[0]}: {exc}"}
        if stdin is subprocess.PIPE:
            def feed() -> None:
                try:
                    if inv.stdin == "prompt":
                        proc.stdin.write(inv.prompt.encode("utf-8"))
                    proc.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

            threading.Thread(target=feed, daemon=True).start()
        killed = False
        stop: tuple[str, list[str], list[int]] | None = None
        deadline = started + inv.timeout_s
        try:
            while True:
                try:
                    code = proc.wait(timeout=max(0.1, min(WATCH_EVERY_S, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    stop = watch(proc.pid) if watch else None
                    if stop or time.monotonic() >= deadline:
                        killed = True
                        _kill_group(proc, inv.kill_grace_s)
                        code = proc.returncode
                        break
        except BaseException:
            # The runner itself was stopped (slate.cli turns SIGTERM and SIGHUP into SystemExit; Ctrl-C is a
            # KeyboardInterrupt). The agent runs in a session of its own and would outlive it, so it goes too.
            _kill_group(proc, inv.kill_grace_s)
            raise
    err_path.write_text(scrub(err_path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    result = {"exit_code": code, "killed": killed, "duration_s": round(time.monotonic() - started, 1)}
    if stop and stop[0] == "escape":
        result["stopped_for_escape"] = stop[1][:25]
    elif stop:
        result["cut_at_render"] = stop[1][:5]
        result["cut_events"] = stop[2]
    return result


def collect(inv: commands.Invocation, meta: dict) -> None:
    """Read a Gateway run's transcript back (OpenClaw): the full transcript by session id first, then chat.history."""
    pair = Path(inv.pair_dir)
    envelope: dict = {}
    run_json = pair / "run.json"
    if run_json.exists():
        try:
            envelope = json.loads(run_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            envelope = {}
    session_id = transcript._find(envelope, "sessionId") if envelope else None  # noqa: SLF001
    tried = []
    for step in inv.collect:
        if "session_id" in (step.get("needs") or []) and not session_id:
            tried.append(f"{step['argv'][1:3]}: no session id in the run's output")
            continue
        argv = [str(session_id) if a == "{session_id}" else a.replace("{session_id}", str(session_id or ""))
                for a in step["argv"]]
        try:
            proc = procs.run(argv, timeout=120)
        except Exception as exc:  # noqa: BLE001 - try the next way
            tried.append(f"{' '.join(argv[:4])}: {exc}")
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            tried.append(f"{' '.join(argv[:4])}: exit {proc.returncode}")
            continue
        text = proc.stdout
        if step["stdout"] == "path":
            # The help says this "prints transcript.jsonl" without saying whether that's the path or the content, so
            # take either: a path to a file, or the JSONL itself.
            candidate = text.strip().splitlines()[-1].strip()
            try:
                candidate = json.loads(text).get("path", candidate)
            except (json.JSONDecodeError, AttributeError):
                pass
            p = Path(str(candidate)).expanduser()
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace")
            elif not text.lstrip().startswith("{"):
                tried.append(f"{' '.join(argv[:4])}: {candidate} isn't a file or a transcript")
                continue
        (pair / "transcript.jsonl").write_text(text, encoding="utf-8")
        meta["collected_by"] = " ".join(argv[:4])
        return
    meta["collect_failed"] = tried


# ---- Codex's trust list --------------------------------------------------------------------------------------------------
# `codex exec` marks the folder it ran in as a trusted project in its config.toml, even with --ignore-user-config and
# --ephemeral: one `[projects."<folder>"]` table with `trust_level = "trusted"` per run (seen on codex-cli 0.153.4 and
# 0.155.0-alpha.16.4; a `-c projects."<folder>".trust_level=…` override doesn't stop it). An eval's sandbox is
# throwaway, so after a Codex run the runner takes out exactly the table that run added for its own sandbox, and nothing
# else: a table that was there before the run stays, and so does any table with more in it than the trust level.


def codex_config_file() -> Path:
    """The config.toml Codex writes its trust list to: $CODEX_HOME's, else ~/.codex's."""
    home = os.environ.get("CODEX_HOME")
    return (Path(home).expanduser() if home else Path.home() / ".codex") / "config.toml"


def _trust_headers(folder: Path) -> list[str]:
    spellings = dict.fromkeys([str(folder), os.path.realpath(folder)])
    return [f'[projects."{s}"]' for s in spellings]


def codex_trusts(folder: Path, config: Path | None = None) -> bool:
    """True when Codex's config.toml has a trust table for exactly this folder."""
    config = config or codex_config_file()
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    headers = set(_trust_headers(folder))
    return any(line.strip() in headers for line in lines)


def prune_codex_trust(folder: Path, config: Path | None = None) -> bool:
    """Remove the `[projects."<folder>"]` table Codex added for an eval's sandbox. Returns True when one was removed.

    Only a table whose sole key is `trust_level = "trusted"` is removed (with the blank line after it); the file is
    otherwise left byte for byte, and replaced atomically with its permissions kept."""
    config = config or codex_config_file()
    try:
        text = config.read_text(encoding="utf-8")
        mode = config.stat().st_mode & 0o777
    except OSError:
        return False
    lines = text.splitlines(keepends=True)
    headers = set(_trust_headers(folder))
    for i, line in enumerate(lines):
        if line.strip() not in headers:
            continue
        j = i + 1
        if j >= len(lines) or lines[j].strip() != 'trust_level = "trusted"':
            return False
        k = j + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k < len(lines) and not lines[k].lstrip().startswith("["):
            return False  # the table holds more than its trust level: not ours to remove
        end = j + 1
        if end < len(lines) and not lines[end].strip():
            end += 1  # the blank line Codex leaves after the table
        new = "".join(lines[:i] + lines[end:])
        tmp = config.with_name(f".{config.name}.slate-evals.{os.getpid()}")
        tmp.write_text(new, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, config)
        return True
    return False


def claude_project_folder(folder: Path) -> Path:
    """The folder Claude Code keeps for a working folder (~/.claude/projects/<the path with every other character
    turned into '-'>); it makes one even with --no-session-persistence."""
    home = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(home).expanduser() if home else Path.home() / ".claude"
    return root / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(folder))


def tidy_claude_project(folder: Path) -> bool:
    """Remove the project folder Claude Code made for an eval's sandbox, when all it holds is Claude Code's own spill of
    long tool output for that session (<session>/tool-results/…) or nothing at all. Anything else, a memory note an
    agent wrote there above all, stays for the owner to see. Returns True when the folder was removed."""
    target = claude_project_folder(folder)
    if not target.is_dir():
        return False
    for p in target.rglob("*"):
        if p.is_symlink() or (p.is_file() and p.relative_to(target).parts[1:2] != ("tool-results",)):
            return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


def list_artifacts(work: Path, limit: int = 4000) -> list[str]:
    out = []
    for p in sorted(work.rglob("*")):
        if p.is_file():
            out.append(str(p.relative_to(work)))
            if len(out) >= limit:
                break
    return out


def _film_folders() -> set[str]:
    root = paths.films()
    return {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()


def run_pair(inv: commands.Invocation, case: suite.Case, configs: dict, *, host_films_check: bool = False) -> dict:
    pair = Path(inv.pair_dir)
    pair.mkdir(parents=True, exist_ok=True)
    setup_notes = prepare(inv, case)
    env = commands.child_env(inv, procs.child_env())
    before = _film_folders() if host_films_check else set()
    meta = {"case": case.id, "config": inv.config_id, "synthetic": False, "argv": inv.argv, "started_at": _now(),
            "slate_version": __version__, "setup": setup_notes, "timeout_s": inv.timeout_s,
            # Where the run may write, for stays_in_sandbox (a recording keeps these, with the home folder as "~").
            "pair_dir": str(pair), "workdir": inv.workdir, "tools_cwd": inv.tools_cwd, "notice": inv.notice}
    is_codex = suite.config_entry(configs, inv.config_id).get("agent") == "codex"
    trusted_before = codex_trusts(Path(inv.workdir)) if is_codex else True
    out_path = pair / ("run.json" if inv.transcript_source == "collect" else "transcript.jsonl")
    try:
        meta.update(execute(inv, env, watch=watchdog(inv, out_path)))
    finally:  # a stopped run's table comes out too, and so does whatever it left in its sandbox's job queue
        if is_codex and not trusted_before and prune_codex_trust(Path(inv.workdir)):
            meta["codex_trust_pruned"] = ("Codex marked the sandbox a trusted project in its config.toml; that entry "
                                          "was taken out again")
        queue = stop_sandbox_queue(inv, env) if inv.env_reaches_tools else {}
        if queue:
            meta["sandbox_queue"] = queue
        if suite.config_entry(configs, inv.config_id).get("agent") == "claude-code" and tidy_claude_project(
                Path(inv.workdir)):
            meta["claude_project_removed"] = "Claude Code's empty project folder for the sandbox was removed"
    if inv.transcript_source == "collect":
        collect(inv, meta)
    meta["artifacts"] = list_artifacts(Path(inv.workdir))
    if host_films_check:
        made = sorted(_film_folders() - before)
        if made:
            meta["host_films_created"] = made
            meta.setdefault("notes", []).append(f"this run made film folders outside the sandbox, in {paths.films()}: "
                                                f"{', '.join(made)} (delete them when you're done)")
    meta["finished_at"] = _now()
    jsonio.write_atomic(pair / "meta.json", meta)
    result = grade_folder(pair, case, inv.config_id, configs)
    jsonio.write_atomic(pair / "grade.json", result)
    return result


# ---- what a recording keeps ----------------------------------------------------------------------------------------------
# A recording lives in the repo, so it keeps what the graders read (the agent's words, its tool calls, their results and
# how they ended) and leaves out what's private or only noise: the agent's own memory and state (Oneiro main reads its
# pulse, recalls past conversations and writes its feelings on every turn; Codex can call the oneiro-memory server),
# hook output, private reasoning, and Claude Code's thinking-token ticks.
REDACTED = "[left out of the recording: the agent's own memory or state]"
# Long tool output keeps its head and its tail (where a prompt or an error shows); the graders read exit codes, the
# commands themselves and those ends, never the middle of a 40 KB file listing.
CLIP_AT, CLIP_KEEP = 6000, 2500


def clip(text: str) -> str:
    if not isinstance(text, str) or len(text) <= CLIP_AT:
        return text
    cut = len(text) - 2 * CLIP_KEEP
    return f"{text[:CLIP_KEEP]}\n[… {cut:,} characters left out of the recording …]\n{text[-CLIP_KEEP:]}"


def _clip_content(content):
    """A tool result's content, clipped, whatever its shape (a string, or a list of text parts)."""
    if isinstance(content, str):
        return clip(content)
    if isinstance(content, list):
        return [{**c, **{k: clip(c[k]) for k in ("text", "content") if isinstance(c.get(k), str)}}
                if isinstance(c, dict) else c for c in content]
    return content
_PRIVATE_COMMAND_RE = re.compile(r"(?:localhost|127\.0\.0\.1):3333\b|/oneiro/runtime/|oneiro-core/|\boca\.log\b|"
                                 r"/\.claude/projects/|/Memory/|\b(?:SOUL|USER|MEMORY|IDENTITY|HEARTBEAT)\.md\b|"
                                 r"(?:^|[\s'\"=/])memory/\d{4}-\d{2}-\d{2}|iCloud~md~obsidian|/Mobile Documents/")
_SESSION_RE = re.compile(r"\(session ([\w.-]+)")  # OpenClaw's "Command still running (session tidy-zephyr, pid …)"
_PRIVATE_SERVERS = ("oneiro-memory", "oneiro_memory")
# OpenClaw's own tools whose results are the agent's memory, its scheduled jobs, its sign-in or its session (a session's
# history repeats every earlier result, private ones included). The graders read the calls, not these results.
_PRIVATE_TOOLS = ("memory_search", "memory_get", "automations", "session_status", "sessions_history", "sessions_list")
# The suite stays brand-neutral (tests/evals: no company specifics), but an agent's own context can name the owner's
# companies (a skill's name in Claude Code's init line, a remark in a reply). Recordings say "[company]" instead. The
# names are spelled with character classes so this file itself stays free of them.
_COMPANY_RE = re.compile(r"(?i)e[o]s[\s_-]?worldwide|tr[u]e[\s_-]?sp[a]ce|v[i]xa\b|tr[u]dy\b|\bE[O]S\b|"
                         r"inn[e]r[\s_-]?ech[o]")
# What Claude Code's init line keeps in a recording: how the run was set up, not the machine's skill and tool lists.
_CLAUDE_INIT_KEEP = ("type", "subtype", "cwd", "model", "permissionMode", "mcp_servers", "apiKeySource",
                     "claude_code_version", "output_style")


def _private_call(name: str, args: object) -> bool:
    """A tool call whose result is the agent's own memory or state."""
    if any(s in (name or "") for s in _PRIVATE_SERVERS) or name in _PRIVATE_TOOLS:
        return True
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    if not isinstance(args, dict):
        return False
    # A shell command, or a file a read tool opened (Claude Code's Read, OpenClaw's read).
    seen = [args.get(k) for k in ("command", "cmd", "file_path", "path", "notebook_path", "paths")]
    text = " ".join(" ".join(str(x) for x in v) if isinstance(v, list) else str(v) for v in seen if v)
    return bool(_PRIVATE_COMMAND_RE.search(text))


def _lines(text: str):
    for line in text.splitlines():
        try:
            yield json.loads(line) if line.strip() else None, line
        except json.JSONDecodeError:
            yield None, line


def _sanitize_claude(text: str) -> str:
    out, private = [], set()
    for obj, line in _lines(text):
        if not isinstance(obj, dict):
            out.append(line)
            continue
        kind, sub = obj.get("type"), obj.get("subtype")
        if kind == "system" and sub == "thinking_tokens":
            continue
        if kind == "system" and sub == "init":
            obj = {k: v for k, v in obj.items() if k in _CLAUDE_INIT_KEEP}
        # Claude Code repeats each tool's result in a structured copy and its raw inputs on the wire; the graders read
        # the message itself, so the copies stay out.
        obj = {k: v for k, v in obj.items() if k not in ("tool_use_result", "wire_tool_inputs")}
        if kind == "system" and sub == "hook_response":
            obj = {**obj, **{k: "[left out of the recording: hook output]" for k in ("output", "stdout", "stderr")
                             if obj.get(k)}}
        content = (obj.get("message") or {}).get("content") if kind in ("assistant", "user") else None
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("thinking", "redacted_thinking"):
                    continue
                if isinstance(part, dict) and part.get("type") == "tool_use" and _private_call(
                        str(part.get("name", "")), part.get("input") or {}):
                    private.add(part.get("id"))
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    content = REDACTED if part.get("tool_use_id") in private else _clip_content(part.get("content"))
                    part = {**part, "content": content}
                parts.append(part)
            obj = {**obj, "message": {**obj["message"], "content": parts}}
        out.append(json.dumps(obj, ensure_ascii=False))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _sanitize_codex(text: str) -> str:
    out = []
    for obj, line in _lines(text):
        item = obj.get("item") if isinstance(obj, dict) else None
        if not isinstance(item, dict):
            out.append(line)
            continue
        kind = item.get("type")
        if kind == "reasoning":
            item = {**item, "text": ""}
        elif kind == "command_execution" and _private_call("shell", {"command": item.get("command")}):
            item = {**item, "aggregated_output": REDACTED}
        elif kind == "command_execution":
            item = {**item, "aggregated_output": clip(item.get("aggregated_output") or "")}
        elif kind == "mcp_tool_call" and _private_call(str(item.get("server", "")), {}):
            item = {**item, "result": {"content": [{"type": "text", "text": REDACTED}]}}
        out.append(json.dumps({**obj, "item": item}, ensure_ascii=False))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _sanitize_openclaw_messages(messages: list) -> list:
    private: set[str] = set()
    sessions: set[str] = set()  # background exec sessions whose output is private (read back with `process`)
    kept = []
    for m in messages:
        if not isinstance(m, dict):
            kept.append(m)
            continue
        inner = m.get("message") if isinstance(m.get("message"), dict) else m  # session JSONL wraps each message
        role, content = inner.get("role"), inner.get("content")
        if role == "assistant" and isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("thinking", "reasoning"):
                    continue
                if isinstance(part, dict) and part.get("type") in ("toolCall", "tool_use", "toolUse", "functionCall"):
                    args = part.get("arguments") if part.get("arguments") is not None else part.get("input")
                    name = str(part.get("name") or part.get("toolName") or "")
                    polled = isinstance(args, dict) and str(args.get("sessionId") or "") in sessions
                    if _private_call(name, args or {}) or (name == "process" and polled):
                        private.add(str(part.get("id") or part.get("toolCallId")))
                parts.append(part)
            inner = {**inner, "content": parts}
        elif role in ("toolResult", "tool") and str(inner.get("toolCallId") or inner.get("tool_call_id")) in private:
            sessions.update(_SESSION_RE.findall(transcript._text_of(content)))  # noqa: SLF001
            inner = {**inner, "content": [{"type": "text", "text": REDACTED}]}
        elif role in ("toolResult", "tool"):
            inner = {**inner, "content": _clip_content(content)}
        kept.append({**m, "message": inner} if inner is not m and m.get("message") is not None else inner)
    return kept


def _sanitize_openclaw(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            whole = json.loads(stripped)
        except json.JSONDecodeError:
            whole = None
        if isinstance(whole, dict) and isinstance(whole.get("messages"), list):
            return json.dumps({**whole, "messages": _sanitize_openclaw_messages(whole["messages"])},
                              ensure_ascii=False) + "\n"
    objs = [obj for obj, _ in _lines(text)]
    if objs and all(isinstance(o, dict) for o in objs if o is not None):
        cleaned = _sanitize_openclaw_messages([o for o in objs if o is not None])
        return "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in cleaned)
    return text


def sanitize_recording(text: str, fmt: str) -> str:
    """What a recorded transcript keeps: everything the graders read, without the agent's private memory, hook output
    or reasoning (see REDACTED above), and with the owner's company names as "[company]". Grading the result gives
    the same verdicts as grading the original."""
    fn = {"claude-stream-json": _sanitize_claude, "codex-jsonl": _sanitize_codex,
          "openclaw": _sanitize_openclaw}.get(fmt)
    return neutral(fn(text) if fn else text)


def neutral(text: str) -> str:
    """The owner's company names out of anything that goes into the repo's recordings."""
    return _COMPANY_RE.sub("[company]", text)


def sanitize_envelope(text: str) -> str:
    """OpenClaw's `agent --json` envelope, without its system-prompt report (the machine's skill and tool lists and
    the workspace files it injected); the session id, the reply and how the run ended stay."""
    try:
        env = json.loads(text)
    except json.JSONDecodeError:
        return neutral(text)
    meta = (env.get("result") or {}).get("meta") if isinstance(env, dict) else None
    if isinstance(meta, dict):
        meta.pop("systemPromptReport", None)
    return neutral(json.dumps(env, ensure_ascii=False, indent=1)) + "\n"


def _record(pair: Path, case_id: str, config_id: str, fmt: str | None = None) -> Path:
    """Save a live run as a recorded fixture, scrubbed and sanitized, so later replays can grade it for free."""
    dest = suite.recorded_fixture(case_id, config_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("transcript.jsonl", "run.json", "probe.jsonl", "tripwire.log"):
        src = pair / name
        if src.exists():
            text = src.read_text(encoding="utf-8", errors="replace")
            if name == "transcript.jsonl" and fmt:
                text = sanitize_recording(text, fmt)
            elif name == "run.json":
                text = sanitize_envelope(text)
            else:
                text = neutral(text)
            (dest / name).write_text(scrub(text), encoding="utf-8")
    meta = json.loads((pair / "meta.json").read_text(encoding="utf-8"))
    meta.pop("argv", None)  # the prompt and paths are in the case; the argv would carry this Mac's paths
    (dest / "meta.json").write_text(neutral(scrub(json.dumps(meta, indent=1, ensure_ascii=False))) + "\n",
                                    encoding="utf-8")
    return dest


def require_mode(dry: bool, live_flag: bool, replay_flag: bool, record: bool) -> str:
    if sum(bool(x) for x in (dry, live_flag, replay_flag)) > 1:
        raise UsageError("Pick one of --dry-run, --replay and --live.")
    if record and not live_flag:
        raise UsageError("--record saves live runs as fixtures, so it needs --live.")
    return "dry-run" if dry else "live" if live_flag else "replay"
