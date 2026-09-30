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
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from slate import __version__, jsonio, log, paths, procs
from slate.errors import UsageError
from slate.evals import checks, commands, preconditions, report, suite, transcript
from slate.gates import GateResult

_SCRUB = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password|xi-api-key|authorization)\s*[=:]\s*)[^\s\"',;]{8,}"),
     r"\1[redacted]"),
    (re.compile(r"\b(HF_KEY|HF_API_KEY|HF_API_SECRET|ELEVENLABS_API_KEY)=[^\s\"']+"), r"\1=[redacted]"),
]


def scrub(text: str) -> str:
    """Take key-shaped strings and the home folder's path out of a transcript before it's saved in the repo. The
    replacements have no quotes or backslashes, so JSON stays valid."""
    for rx, repl in _SCRUB:
        text = rx.sub(repl, text)
    return text.replace(str(Path.home()), "~")


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


def live(pairs, configs: dict, *, run_id: str | None = None, out: Path | None = None, record: bool = False,
         allow_host_env: bool = False, services: bool = False) -> dict:
    """Run the agents for real (model calls only) and grade what they did."""
    run_id = run_id or new_run_id("live")
    run_dir = runs_root(out) / run_id
    started = _now()
    results: list[dict] = []
    for case, cid in pairs:
        inv = commands.plan(case, cid, configs, run_id=run_id, run_dir=run_dir, allow_host_env=allow_host_env,
                            services=services)
        if inv.skip:
            status = "PENDING" if case.pending else "UNCHECKED"
            results.append(_not_run(case, cid, status, inv.skip))
            continue
        log.info(f"evals: {case.id} on {cid} …")
        res = run_pair(inv, case, configs, host_films_check=not inv.env_reaches_tools)
        if record:
            _record(Path(inv.pair_dir), case.id, cid)
        results.append(res)
    summary = report.summarize(results, run_id=run_id, mode="live", fixtures=None, started=started, finished=_now())
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


def _kill_group(proc: subprocess.Popen, grace: float) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
    proc.wait()


def execute(inv: commands.Invocation, env: dict[str, str]) -> dict:
    """Start the agent in its own process group, feed stdin the way its configuration says, and stop it (group and
    all) when time runs out. Stdout goes straight to disk."""
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
        try:
            code = proc.wait(timeout=inv.timeout_s)
        except subprocess.TimeoutExpired:
            killed = True
            _kill_group(proc, inv.kill_grace_s)
            code = proc.returncode
    err_path.write_text(scrub(err_path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    return {"exit_code": code, "killed": killed, "duration_s": round(time.monotonic() - started, 1)}


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
            "slate_version": __version__, "setup": setup_notes, "timeout_s": inv.timeout_s}
    meta.update(execute(inv, env))
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


def _record(pair: Path, case_id: str, config_id: str) -> Path:
    """Save a live run as a recorded fixture, scrubbed, so later replays can grade it for free."""
    dest = suite.recorded_fixture(case_id, config_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("transcript.jsonl", "run.json", "probe.jsonl", "tripwire.log"):
        src = pair / name
        if src.exists():
            (dest / name).write_text(scrub(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    meta = json.loads((pair / "meta.json").read_text(encoding="utf-8"))
    meta.pop("argv", None)  # the prompt and paths are in the case; the argv would carry this Mac's paths
    (dest / "meta.json").write_text(scrub(json.dumps(meta, indent=1, ensure_ascii=False)) + "\n", encoding="utf-8")
    return dest


def require_mode(dry: bool, live_flag: bool, replay_flag: bool, record: bool) -> str:
    if sum(bool(x) for x in (dry, live_flag, replay_flag)) > 1:
        raise UsageError("Pick one of --dry-run, --replay and --live.")
    if record and not live_flag:
        raise UsageError("--record saves live runs as fixtures, so it needs --live.")
    return "dry-run" if dry else "live" if live_flag else "replay"
