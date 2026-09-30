"""What each MCP tool does, as plain functions over the command line and the job queue.

Every function returns an envelope, the same shape for every tool:

    {"ok": bool, "exit_code": int, "command": "slate status --film f1", "result": {...}}

`command` is the exact command line an agent could run in a shell to do the same thing (the CLI is the contract), and
`result` is that command's own `--json` output. A refusal carries `error` ({error, message, hint, exit_code}) instead,
with the shared exit codes: 1 refused or a gate failed, 2 usage, 3 missing prerequisite, 4 spend refused, 5 busy.
Anything long comes back with a `job_id` and the next step, never a wait.

The command line runs in-process (`slate.cli.run_cli`), and one tool call runs at a time: while a command runs it
redirects stdout and sets environment variables such as SLATE_FILM, so two calls must never overlap. `guarded` holds
the lock for the whole call.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import io
import json
import re
import shlex
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from slate import cli, jsonio, paths
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.mcp import policy
from slate.secrets import redact

LOCK = threading.RLock()
MAX_TEXT = 60_000  # characters of one result; a bigger one is summarized (the command line prints it all)
MAX_GUIDE = 190_000  # guides are long by design, and the guide tool raises its client's result cap to match
_TAIL = 3_000
SCRIPT = "script/script.json"


# ---------------------------------------------------------------------------------------------------------------
# Envelopes


def display(argv: list[str]) -> str:
    return "slate " + shlex.join([a for a in argv if a != "--json"])


def _tail(text: str, limit: int) -> str:
    text = redact(text.strip())
    return text if len(text) <= limit else "…" + text[-limit:]


def _last_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _job_id(parsed: Any) -> str | None:
    if isinstance(parsed, dict):
        for key in ("job_id", "job"):
            value = parsed.get(key)
            if isinstance(value, str) and policy.JOB_ID.match(value):
                return value
    return None


def envelope(argv: list[str], code: int, stdout: str = "", stderr: str = "", *, result: Any = None,
             shown: str | None = None) -> dict:
    """Wrap one command's outcome. `shown` replaces the displayed command (for work done without the CLI)."""
    payload: dict[str, Any] = {"ok": code == 0, "exit_code": code, "command": shown or display(argv)}
    parsed = result if result is not None else _last_json(stdout)
    if parsed is not None:
        if code != 0 and isinstance(parsed, dict) and {"error", "message"} <= set(parsed):
            payload["error"] = parsed
        else:
            payload["result"] = parsed
    elif stdout.strip():
        payload["output"] = _tail(stdout, _TAIL)
    if code != 0 and stderr.strip():
        payload["log"] = _tail(stderr, _TAIL)
    job = _job_id(parsed)
    if job:
        payload["job_id"] = job
        payload["next"] = f"Poll with job_status (job_id {job}); the job keeps going whether or not anyone waits."
    return payload


def failure(exc: SlateError, shown: str) -> dict:
    return {"ok": False, "exit_code": exc.exit_code, "command": shown, "error": exc.to_dict()}


def guarded(label: str) -> Callable:
    """Run a tool under the lock, and turn Slate's refusals into envelopes with the right exit code."""
    def wrap(fn: Callable[..., dict]) -> Callable[..., dict]:
        @functools.wraps(fn)
        def inner(*args, **kwargs) -> dict:
            with LOCK:
                try:
                    return fn(*args, **kwargs)
                except SlateError as exc:
                    return failure(exc, label)
        return inner
    return wrap


def call(argv: list[str]) -> dict:
    """Run one `slate` command in-process with --json and wrap it."""
    full = [*argv, "--json"]
    with LOCK:
        code, out, err = cli.run_cli(full)
    return envelope(full, code, redact(out), err)


def shrink(payload: dict, limit: int = MAX_TEXT) -> tuple[dict, str]:
    """The payload and its text, cut down when the text is bigger than a chat should hold."""
    text = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return payload, text
    small = {k: v for k, v in payload.items() if k not in ("result", "output", "log")}
    small["truncated"] = {"chars": len(text),
                          "note": "The full result is too long for a chat. Run the command in a shell to see all of "
                                  "it, or narrow the request."}
    if isinstance(payload.get("result"), dict):
        small["result_keys"] = sorted(payload["result"])[:50]
    return small, json.dumps(small, indent=1, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------------------------------------------
# Is a stage part of this install?


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 - argparse has no public way to walk its tree
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    return {}


def registered(words: tuple[str, ...]) -> bool:
    """True when `slate <words...>` exists in this install (a module that isn't built yet adds no commands)."""
    with LOCK:
        parser: argparse.ArgumentParser = cli.build_parser()
    for word in words:
        choices = _subparsers(parser)
        if word not in choices:
            return False
        parser = choices[word]
    return True


def require_built(st: policy.Stage) -> None:
    if not registered(st.words):
        raise MissingPrerequisite(
            f"`{st.cli}` isn't part of this Slate install yet (its module, {st.module}, isn't built).",
            hint="It arrives with a Slate update (`slate update`); until then nothing here can run it.")


def preflight(argv: list[str]) -> None:
    """Parse a command with the real parser before it's queued, so a bad argument fails now, not in the queue."""
    err = io.StringIO()
    with LOCK, contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        try:
            cli.build_parser().parse_args(argv)
        except SystemExit as exc:
            if exc.code not in (0, None):
                lines = [ln for ln in err.getvalue().strip().splitlines() if ln.strip()]
                raise UsageError(lines[-1] if lines else "The command's arguments don't parse.") from None


def film_dir(film: str | None) -> Path:
    """The film a tool acts on: an id under the films folder or a path; the server's own folder when it's a film."""
    return paths.film_dir(policy.check_value("film", film) if film else None)


# ---------------------------------------------------------------------------------------------------------------
# The tools


@guarded("slate status")
def status(film: str | None = None) -> dict:
    """Where a film stands, stage by stage, with the one next command; outside a film, the films there are."""
    argv = ["status"] + (["--film", str(film_dir(film))] if film else [])
    out = call(argv)
    if not out["ok"] and not film and (out.get("error") or {}).get("error") == "usage":
        # Outside a film, with no films yet: that's an answer, not a failure.
        return {"ok": True, "exit_code": 0, "command": "slate status",
                "result": {"films": [], "films_dir": str(paths.films()),
                           "next": "Start one with new_film (slate new <id> --brand <brand> --format <format>)."}}
    return out


@guarded("slate new")
def new_film(film_id: str, brand: str, format: str, *, request: str | None = None, title: str | None = None,
             mode: str = "supervised", generation: str = "rarely", runtime_s: float | None = None,
             aspect: str | None = None, source: str = "requester", git: bool = True) -> dict:
    """A new film folder: brief (the request verbatim), script skeleton, stage folders."""
    if not film_id or not brand or not format:
        raise UsageError("A new film needs its id, a brand and a format.",
                         hint="The neutral brand is 'demo'; `slate new --help` lists the rest.")
    argv = ["new", policy.check_value("film_id", film_id), f"--brand={policy.check_value('brand', brand)}",
            f"--format={policy.check_value('format', format)}", f"--mode={mode}", f"--generation={generation}",
            f"--source={policy.check_value('source', source)}"]
    if title:
        argv.append(f"--title={policy.check_value('title', title)}")
    if request:
        argv.append(f"--request={policy.check_value('request', request)}")
    if runtime_s is not None:
        argv.append(f"--runtime={float(runtime_s):g}")
    if aspect:
        argv.append(f"--aspect={policy.check_value('aspect', aspect)}")
    if not git:
        argv.append("--no-git")
    return call(argv)


def _stage_argv(st: policy.Stage, film_path: Path, args: list[str] | None) -> list[str]:
    checked = policy.check_args(st, args)
    options = [a for a in checked if a.startswith("--")]
    names = [a for a in checked if not a.startswith("--")]
    return [*st.words, "--film", str(film_path), *st.extra, *options, *names]


def _queue(st: policy.Stage, argv: list[str], film_path: Path) -> dict:
    """Submit a stage that has no --detach of its own: the same command, run later by the queue."""
    from slate.jobs import client, store  # noqa: PLC0415

    preflight(argv)
    command = [sys.executable, "-m", "slate", *argv]
    job_id = client.submit(st.kind or st.words[0], command, film=str(film_path), resources=st.resources,
                           note=f"{st.cli} (from the MCP server)")
    record = store.load(job_id)
    result = {"job": job_id, "state": record.get("state"), "kind": record.get("kind"),
              "resources": record.get("resources"), "priority": record.get("priority"), "log": record.get("log")}
    submit = ["job", "submit", "--kind", st.kind or st.words[0]]
    for resource in st.resources:
        submit += ["--resource", resource]
    return envelope(argv, 0, result=result, shown=display(submit + ["--"]) + " " + display(argv))


def _run_stage(table: dict[str, policy.Stage], tool: str, command: str, film: str | None, args: list[str] | None,
               *, spend: bool) -> dict:
    shown = f"slate {command}"
    try:
        st = policy.stage(command, table, tool=tool)
        film_path = film_dir(film)
        argv = _stage_argv(st, film_path, args)
        shown = display(argv)
        policy.require_spend(st, argv, spend)
        require_built(st)
        if st.how == "queue" and "--dry-run" not in argv:
            return _queue(st, argv, film_path)
        return call(argv)  # inline stages, detach stages (they queue themselves) and dry runs
    except SlateError as exc:
        return failure(exc, shown)


@guarded("slate run")
def run(command: str, film: str | None = None, args: list[str] | None = None) -> dict:
    """A quick stage now; a stage that reads or writes media goes to the job queue on its own."""
    return _run_stage(policy.RUN, "run", command, film, args, spend=False)


@guarded("slate job submit")
def job_submit(stage: str, film: str | None = None, args: list[str] | None = None, spend: bool = False) -> dict:
    """A long stage, queued as a detached job; its id comes back at once."""
    return _run_stage(policy.JOBS, "job_submit", stage, film, args, spend=spend)


@guarded("read script/script.json")
def script_get(film: str | None = None) -> dict:
    path = film_dir(film)
    data = jsonio.read(path / SCRIPT)
    return envelope([], 0, result={"film_dir": str(path), "path": str(path / SCRIPT), "script": data},
                    shown=f"cat {shlex.quote(str(path / SCRIPT))}")


def _approved(path: Path) -> bool:
    try:
        from slate.state import approvals  # noqa: PLC0415
    except ImportError:
        return False
    return bool(approvals.is_approved(path, "script", SCRIPT))


def _locked(path: Path) -> bool:
    try:
        from slate.state import lock  # noqa: PLC0415
    except ImportError:
        return False
    return bool(lock.is_locked(path, SCRIPT))


@guarded("write script/script.json, then slate script lint")
def script_put(script: dict, film: str | None = None) -> dict:
    """Write script/script.json (checked against its schema), then lint it.

    Refused when the script is approved or locked: approved work changes only through a person's notes
    (`slate revise`), never by a silent rewrite.
    """
    path = film_dir(film)
    shown = f"write {path / SCRIPT}, then slate script lint --film {shlex.quote(str(path))}"
    if not isinstance(script, dict):
        raise UsageError('The script is a JSON object: {"acts": [...]}.')
    problems = jsonio.validate(script, "script")
    if problems:
        raise UsageError("The script doesn't match script.schema.json:\n  " + "\n  ".join(problems[:20]),
                         hint="Fix those fields and send the whole script again.")
    if _locked(path):
        raise Refused("script/script.json is locked, so it isn't rewritten here.",
                      hint="A person unlocks or revises it with the command line.")
    if (path / SCRIPT).exists() and _approved(path):
        raise Refused("The script is approved, so it isn't rewritten here: approved work changes only through the "
                      "person's notes.",
                      hint='The person starts a revision (slate revise start script/script.json --notes "..." '
                           "--by NAME), then the script is edited against those notes.")
    jsonio.write_atomic(path / SCRIPT, script)
    lint = call(["script", "lint", "--film", str(path)])
    lint["command"] = shown
    lint["written"] = str(path / SCRIPT)
    return lint


@guarded("slate job status")
def job_status(job_id: str | None = None, limit: int = 10) -> dict:
    if job_id:
        return call(["job", "status", policy.check_job_id(job_id)])
    count = max(1, min(int(limit), 50))
    queue = call(["jobs", "status"])
    listing = call(["job", "list", "--limit", str(count)])
    ok = queue["ok"] and listing["ok"]
    return {"ok": ok, "exit_code": 0 if ok else max(queue["exit_code"], listing["exit_code"]),
            "command": f"slate jobs status; slate job list --limit {count}",
            "result": {"queue": queue.get("result"), "jobs": (listing.get("result") or {}).get("jobs", [])}}


@guarded("slate job log")
def job_log(job_id: str, lines: int = 40) -> dict:
    return call(["job", "log", policy.check_job_id(job_id), "--lines", str(max(1, min(int(lines), 200)))])


@guarded("slate job cancel")
def job_cancel(job_id: str) -> dict:
    return call(["job", "cancel", policy.check_job_id(job_id)])


@guarded("slate budget show")
def budget(film: str | None = None, month: str | None = None) -> dict:
    argv = ["budget", "show"]
    if film:
        argv += ["--film", str(film_dir(film))]
    if policy.check_month(month):
        argv += ["--month", month]
    return call(argv)


LEDGER_VIEWS = ("rows", "inflight", "reconcile")
PROVIDERS = ("elevenlabs", "elevenlabs_oneiro", "higgsfield")
SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def _session(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not SESSION.match(value):
        raise UsageError(f"{value!r} isn't a session label: letters, digits, dots, dashes, underscores or colons.")
    return value


def _provider(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if value not in PROVIDERS:
        raise UsageError(f"{value!r} isn't a provider Slate spends with: {', '.join(PROVIDERS)}.")
    return value


@guarded("slate ledger list")
def ledger(film: str | None = None, provider: str | None = None, row_status: str | None = None,
           month: str | None = None, limit: int = 20, view: str = "rows", session: str | None = None) -> dict:
    """Paid calls (rows), the ones still in flight, or how the ledger squares with the vendors' balances (reconcile).

    Generations made through a hosted connector in a chat bypass the ledger; reconciling the balance snapshots taken
    at a session's start and end is how they're caught.
    """
    if view not in LEDGER_VIEWS:
        raise UsageError(f"view must be one of {', '.join(LEDGER_VIEWS)}.")
    if view == "inflight":
        return call(["ledger", "inflight"])
    if view == "reconcile":
        argv = ["ledger", "reconcile"]
        if _provider(provider):
            argv.append(f"--provider={provider}")
        if _session(session):
            argv.append(f"--session={session}")
        return call(argv)
    argv = ["ledger", "list", "--limit", str(max(1, min(int(limit), 200)))]
    if film:
        argv += ["--film", str(film_dir(film))]
    if _provider(provider):
        argv.append(f"--provider={provider}")
    if row_status:
        argv.append(f"--status={policy.check_value('row_status', row_status)}")
    if policy.check_month(month):
        argv += ["--month", month]
    return call(argv)


@guarded("slate ledger snapshot")
def balance_snapshot(provider: str | None = None, balance: float | None = None, session: str | None = None,
                     note: str | None = None) -> dict:
    """Record a vendor balance in the ledger: one Slate can read itself, or one read from a connector or dashboard.

    It records; it never spends. Take one at a session's start and end with the same session label, then reconcile.
    """
    argv = ["ledger", "snapshot"]
    argv += [f"--provider={_provider(provider)}"] if provider else ["--all"]
    if balance is not None:
        if not provider:
            raise UsageError("A balance goes with one provider.")
        argv.append(f"--balance={float(balance):g}")
    if _session(session):
        argv.append(f"--session={session}")
    if note:
        argv.append(f"--note={policy.check_value('note', note)}")
    return call(argv)


@guarded("slate guide")
def guide(topic: str | None = None) -> dict:
    return call(["guide"] + ([policy.check_value("topic", topic)] if topic else []))


__all__ = ["envelope", "failure", "call", "shrink", "registered", "require_built", "preflight", "film_dir", "status",
           "new_film", "run", "job_submit", "script_get", "script_put", "job_status", "job_log", "job_cancel",
           "budget", "ledger", "balance_snapshot", "guide", "LOCK", "MAX_TEXT", "MAX_GUIDE", "LEDGER_VIEWS",
           "PROVIDERS"]
