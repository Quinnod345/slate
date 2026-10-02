#!/usr/bin/env python3
"""The agent spikes: ask each real agent context to run one Slate command, and keep what comes back.

Two spikes, one per plan point:

- **doctor** (phase 1's done-when, "doctor runs green from all three contexts"): each agent runs
  `slate doctor --json --context <context>` and replies with its output. The doctor records its run in Slate's data
  folder (doctor/<context>.json), which is the evidence this script reads back.
- **demo** (`--demo`, phase 5's "each agent runs `slate demo`", P201): each agent runs `slate demo`, waits for it, and
  replies with its summary. The demo makes a scratch film in replay mode (nothing is spent; its records stay in its
  scratch folder, removed after a pass), so the evidence is what the agent saw: this script reads the agent's own
  transcript, finds the `slate demo` run and its closing lines, and keeps them with the agent's reply.

The contexts: an Oneiro pursuit slice (`codex exec --ignore-user-config` on gpt-6-sol, stdin a pipe, killed after ten
minutes, built from evals/configs.json exactly as the evals build it) and the OpenClaw gateway (Oneiro main). Claude
Code's shell is the third context; it runs these commands directly. Every run costs model calls only: no vendor
spend, no browser.

Codex marks the folder it runs in as a trusted project in ~/.codex/config.toml (`codex exec` does it even with
--ignore-user-config). The slice's folder is throwaway, so the spike takes that table out again every time: right
after the slice, in a `finally` when anything goes wrong, and from a SIGTERM, SIGHUP or SIGINT handler when the queue
or a person stops it. A SIGKILL can't be caught, so each spike also starts and ends by sweeping up what an earlier
one left: a table for a slate-spike-slice-* folder whose spike isn't running any more (each slice folder names its
spike's pid) or for a demo's slate-demo-* film. Its result ends with the check: no table for a spike, demo or
eval-run folder left.

The demo renders, so run the demo spike through Slate's job queue, holding the one render slot, so it queues behind
any other render instead of running beside it:

    slate job submit --kind demo-spike --resource render --no-resume --max-attempts 1 --cwd <repo> -- \\
        <repo>/.venv/bin/python scripts/doctor_spike.py --demo --out <folder>

A third use checks a client behaviour hands-on (tests/packaging/client_behaviours.json) instead of running Slate:
`--client-check openclaw-bundle-placeholders` installs a scratch Claude bundle in a sealed scratch OpenClaw home
(slate.evals.scratch: its own home, state, config, workspace and gateway port, an empty CODEX_HOME, memory off, no
provider keys) and runs one local agent turn against a model provider on a dead loopback port, so no model is ever
called. The bundle's MCP server records the command line OpenClaw started it with: expanded, or the literal
placeholder. The real ~/.openclaw and ~/.codex are checksummed before and after.

    scripts/doctor_spike.py [--demo] [--context pursuit-slice|openclaw-gateway|all] [--out FOLDER] [--dry-run]
    scripts/doctor_spike.py --client-check openclaw-bundle-placeholders [--out FOLDER] [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from slate import paths  # noqa: E402
from slate.evals import commands, runner, scratch, suite, transcript  # noqa: E402
from slate.jobs import proc as jobproc  # noqa: E402

PROMPT = ("Run exactly this one shell command, then reply with nothing but its output: "
          "slate doctor --json --context {context}")
DEMO_PROMPT = ("Run exactly this one shell command and wait for it to finish (it takes a few minutes and spends "
               "nothing): slate demo\nThen reply with its summary: whether every stage passed, how many stages ran, "
               "how long it took, and what it reported as unchecked. Don't open, send or post anything.")
SLICE_PATH = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
SLICE_TIMEOUT_S = 600  # the engine kills a slice after ten minutes (pursuit-work.js)
GATEWAY_MODEL = "openai/gpt-5.6-sol"
# What `slate demo` prints last: "Every stage passed: 23 of 23 in 3 min 18 s." or "Stopped at stage 12 of 23 (render)".
DEMO_PASSED_RE = re.compile(r"Every stage passed: (\d+) of (\d+) in ([^\n]+?)\.?$", re.M)
DEMO_STOPPED_RE = re.compile(r"Stopped at stage (\d+) of (\d+) \(([^)]+)\) after ([^\n]+?)\.?$", re.M)
DEMO_UNCHECKED_RE = re.compile(r"^\s*unchecked, as expected: (.+)$", re.M)
# Codex trust tables that belong to a spike, a demo's scratch film or an eval's sandbox, never to a real project.
TRUST_HEADER_RE = re.compile(r'^\[projects\."([^"]+)"\]\s*$', re.M)
SPIKE_FOLDER_RE = re.compile(r"/(?:slate-spike-slice-|slate-demo-)[^/]+(?:/|$)")
EVAL_RUN_FOLDER_RE = re.compile(r"/Slate/evals/runs/")


def openclaw_bin() -> str:
    return shutil.which("openclaw") or "/opt/homebrew/bin/openclaw"


def slice_invocation(workdir: Path) -> commands.Invocation:
    """The pursuit slice's exact command, built by the eval runner from evals/configs.json."""
    configs = suite.load_configs()
    case = next(c for c in suite.load_cases(configs=configs) if c.id == "triggering-launch-video")
    return commands.plan(case, "pursuit-slice", configs, run_id="doctor-spike", run_dir=workdir)


def slice_env(inv: commands.Invocation) -> dict[str, str]:
    """The slice's own environment (buildCodexEnvironment): no provider keys, the engine's PATH, TERM=dumb. Not the
    eval sandbox: the spike runs Slate the way a real slice would."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLATE_")}
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL"):
        env.pop(key, None)
    env.update({"PATH": os.pathsep.join(SLICE_PATH), "BROWSER": "none", "TERM": "dumb"})
    return env


def _trust_tables() -> list[str]:
    """The folders Codex's config.toml holds a `[projects."<folder>"]` table for."""
    try:
        return TRUST_HEADER_RE.findall(runner.codex_config_file().read_text(encoding="utf-8"))
    except OSError:
        return []


def prune_trust(folder: Path) -> bool:
    """Take out every trust table Codex added for this folder (it may have used the folder's real path, /private/var
    for /var). Only a table that holds nothing but its trust level goes; see runner.prune_codex_trust."""
    removed = False
    for _ in range(4):
        if not runner.prune_codex_trust(folder):
            break
        removed = True
    return removed


PID_FILE = ".spike-pid"  # in each slice's folder: the spike that owns it, as "<pid> <start time>"


def _spike_running(root: Path) -> bool:
    """Whether the spike that made this slice folder is still running. A pid alone isn't proof (the number can be
    reused), so the file also holds the process's start time, checked the way the job queue checks its own."""
    try:
        pid, _, stamp = (root / PID_FILE).read_text(encoding="utf-8").strip().partition(" ")
        return jobproc.alive(int(pid), stamp or None)
    except (OSError, ValueError):
        return False


def prune_leftover_trust() -> list[str]:
    """Earlier spikes' trust tables, taken out. Only this script makes slate-spike-slice-* folders, so a table for one
    whose spike isn't running any more is a leftover from a spike a SIGKILL cut short; its folder goes too. Slate never
    runs Codex in a demo's slate-demo-* film, so a table there came from an agent, and it goes as well. Only tables
    that hold nothing but their trust level are touched (runner.prune_codex_trust)."""
    removed = []
    for folder in dict.fromkeys(_trust_tables()):
        m = SPIKE_FOLDER_RE.search(folder)
        if not m:
            continue
        root = Path(folder[:m.end()].rstrip("/"))
        is_slice = root.name.startswith("slate-spike-slice-")
        if is_slice and root.exists() and _spike_running(root):
            continue  # a spike running right now
        if prune_trust(Path(folder)):
            removed.append(folder)
            temp = Path(tempfile.gettempdir()).resolve()
            if is_slice and root.exists() and temp in root.resolve().parents:
                shutil.rmtree(root, ignore_errors=True)  # the killed spike's own scratch folder, where mkdtemp put it
    return removed


def trust_left() -> list[str]:
    """Any trust table still there for a spike's, a demo's or an eval run's folder. Should be none."""
    return [f for f in dict.fromkeys(_trust_tables()) if SPIKE_FOLDER_RE.search(f) or EVAL_RUN_FOLDER_RE.search(f)]


_CHILD: list[subprocess.Popen] = []  # the agent running now, so a stop from the queue can take it down too
_CLEANUPS: list = []  # what a stop has to undo (Codex's trust entry, the slice's scratch folder)


def _stop_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            continue


def _on_stop(signum, _frame) -> None:
    """The queue stops a job with SIGTERM (a cancel, or a disk running low); a closed Terminal sends SIGHUP and Ctrl-C
    SIGINT. The agent runs in its own process group, so stop it too, undo what it left, and exit."""
    for proc in _CHILD:
        _stop_group(proc)
    for undo in _CLEANUPS:
        try:
            undo()
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
    sys.exit(128 + signum)


def run_group(argv: list[str], *, stdin_text: str | None, env: dict, cwd: str | None, timeout: float) -> dict:
    """Run a command in its own process group; at the timeout, stop the whole group (the agent and anything it
    started), so nothing is left running."""
    started = time.time()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                stdout=out, stderr=err, env=env, cwd=cwd, start_new_session=True)
        _CHILD[:] = [proc]
        if stdin_text is not None:
            try:
                proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        killed = False
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            killed = True
            _stop_group(proc)
            code = proc.wait()
        _CHILD.clear()
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", errors="replace")
        stderr = err.read().decode("utf-8", errors="replace")
    return {"exit": code, "killed": killed, "seconds": round(time.time() - started, 1), "stdout": stdout,
            "stderr": stderr}


def demo_evidence(t: transcript.Transcript) -> dict:
    """What the agent's own transcript shows of its `slate demo` run: the command, how it ended, its closing lines."""
    runs = [e for e, call in t.slate_calls() if call.sub_is("demo")]
    outputs = [e.output for e in runs if e.output]
    # A long command can come back through a polling tool (OpenClaw backgrounds exec after ten seconds).
    outputs += [e.output for e in t.of_kind("tool") if e.output and (DEMO_PASSED_RE.search(e.output)
                                                                     or DEMO_STOPPED_RE.search(e.output)
                                                                     or "slate demo:" in e.output)]
    text = "\n".join(dict.fromkeys(outputs))
    passed = DEMO_PASSED_RE.search(text)
    stopped = DEMO_STOPPED_RE.search(text)
    stage_lines = [ln.strip() for ln in text.splitlines() if re.match(r"^\s{1,3}\d{1,2}\s{2}\S", ln)]
    finished = [e for e in runs if e.output and (DEMO_PASSED_RE.search(e.output) or DEMO_STOPPED_RE.search(e.output))]
    ev = {"demo_commands": list(dict.fromkeys(e.text for e in runs)),
          "demo_exit": finished[-1].exit_code if finished else None,
          "stages_seen": len({ln.split()[1] for ln in stage_lines if len(ln.split()) > 1}),
          "unchecked": list(dict.fromkeys(DEMO_UNCHECKED_RE.findall(text)))}
    if passed:
        ev.update({"result": "passed", "summary": passed.group(0).strip(), "stages": f"{passed.group(1)} of "
                   f"{passed.group(2)}", "took": passed.group(3)})
    elif stopped:
        ev.update({"result": "stopped", "summary": stopped.group(0).strip(), "stopped_at": stopped.group(3)})
    else:
        ev.update({"result": "not seen", "summary": None})
    return ev


def run_slice(dry_run: bool, *, demo: bool = False, out: Path | None = None) -> dict:
    root = Path(tempfile.mkdtemp(prefix="slate-spike-slice-"))
    # Before Codex can write a table for it: who owns this folder, so a later sweep knows whether it's still in use.
    (root / PID_FILE).write_text(f"{os.getpid()} {jobproc.own_stamp() or ''}\n", encoding="utf-8")
    inv = slice_invocation(root)
    work = Path(inv.workdir)
    work.mkdir(parents=True, exist_ok=True)
    prompt = DEMO_PROMPT if demo else PROMPT.format(context="pursuit-slice")
    record = {"context": "pursuit-slice", "spike": "demo" if demo else "doctor", "argv": shlex.join(inv.argv),
              "prompt": prompt}
    if dry_run:
        shutil.rmtree(root, ignore_errors=True)
        return record
    trusted_before = runner.codex_trusts(work)

    def undo() -> None:
        """Codex's trust table for the slice's folder out first (the part that outlives the run), then the folder."""
        if not trusted_before and prune_trust(work):
            record["codex_trust_pruned"] = True
        shutil.rmtree(root, ignore_errors=True)

    _CLEANUPS.append(undo)
    try:
        res = run_group(inv.argv, stdin_text=prompt, env=slice_env(inv), cwd=str(work), timeout=SLICE_TIMEOUT_S)
        if not trusted_before and prune_trust(work):
            record["codex_trust_pruned"] = True
        record.update({k: res[k] for k in ("exit", "killed", "seconds")})
        t = transcript.parse(res["stdout"], "codex-jsonl")
        record["reply"] = t.final
        if demo:
            record.update(demo_evidence(t))
        else:
            record["stdout_tail"] = res["stdout"][-4000:]
        record["stderr_tail"] = runner.scrub(res["stderr"][-1500:])
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"pursuit-slice-{record['spike']}.jsonl").write_text(runner.scrub(res["stdout"]), encoding="utf-8")
            record["transcript"] = str(out / f"pursuit-slice-{record['spike']}.jsonl")
    finally:
        undo()  # every way out: a finished run, an exception, or a stop that raised SystemExit through here
        _CLEANUPS.clear()
    return record


def run_gateway(dry_run: bool, *, demo: bool = False, out: Path | None = None) -> dict:
    prompt = DEMO_PROMPT if demo else PROMPT.format(context="openclaw-gateway")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    key = f"agent:main:slate-demo-spike-{stamp}" if demo else "agent:main:slate-doctor-spike"
    turn_timeout = 900 if demo else 600  # the demo itself takes minutes; the doctor seconds
    argv = [openclaw_bin(), "agent", "--agent", "main", "--session-key", key, "--model", GATEWAY_MODEL,
            "--thinking", "low", "--json", "--timeout", str(turn_timeout), "--message", prompt]
    record = {"context": "openclaw-gateway", "spike": "demo" if demo else "doctor", "argv": shlex.join(argv)}
    if dry_run:
        return record
    env = {**os.environ, "BROWSER": "none", "PATH": os.pathsep.join(SLICE_PATH)}
    res = run_group(argv, stdin_text=None, env=env, cwd=None, timeout=turn_timeout + 120)
    record.update({k: res[k] for k in ("exit", "killed", "seconds")})
    envelope: dict = {}
    try:
        envelope = json.loads(res["stdout"])
    except json.JSONDecodeError:
        pass
    session_id = transcript._find(envelope, "sessionId") if envelope else None  # noqa: SLF001
    record["session_id"] = session_id
    text, collected = "", "envelope only"
    if session_id:
        got = subprocess.run([openclaw_bin(), "transcripts", "path", str(session_id), "--transcript"],
                             capture_output=True, text=True, env=env, timeout=120)
        candidate = (got.stdout.strip().splitlines() or [""])[-1].strip()
        if got.returncode == 0 and Path(candidate).expanduser().is_file():
            text, collected = Path(candidate).expanduser().read_text(encoding="utf-8", errors="replace"), "transcript"
        elif got.returncode == 0 and got.stdout.lstrip().startswith("{"):
            text, collected = got.stdout, "transcript"
    if not text:
        # OpenClaw 2026.9.4 stores no transcript for agent turns; the Gateway's chat.history carries every tool call
        # and its result (the eval runner reads it the same way).
        params = json.dumps({"sessionKey": key, "limit": 1000})
        got = subprocess.run([openclaw_bin(), "gateway", "call", "chat.history", "--json", "--timeout", "30000",
                              "--params", params], capture_output=True, text=True, env=env, timeout=120)
        if got.returncode == 0 and got.stdout.strip():
            text, collected = got.stdout, "chat.history"
    record["collected"] = collected
    t = transcript.parse(text, "openclaw", envelope=envelope if isinstance(envelope, dict) else None)
    record["reply"] = t.final
    if demo:
        record.update(demo_evidence(t))
    else:
        record["stdout_tail"] = res["stdout"][-4000:]
    record["stderr_tail"] = runner.scrub(res["stderr"][-1500:])
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"openclaw-gateway-{record['spike']}.jsonl").write_text(runner.scrub(text or res["stdout"]),
                                                                       encoding="utf-8")
        record["transcript"] = str(out / f"openclaw-gateway-{record['spike']}.jsonl")
    return record


# ---- client behaviours, checked in a sealed scratch OpenClaw home -----------------------------------------------------------
# A model provider on the discard port: every model call is refused at once, so a check's agent turn costs nothing.
DEAD_PROVIDER = {"models": {"providers": {"dead": {"api": "openai-completions", "baseUrl": "http://127.0.0.1:9/v1",
                                                   "apiKey": "unused", "models": [{"id": "m", "name": "m"}]}}},
                 "agents": {"defaults": {"model": "dead/m"}}}
PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"
# What the leak looked like on 2026-10-01: the memory plugin calling OpenAI's embeddings with the Codex CLI's sign-in.
LEAK_RE = re.compile(r"(?i)embedding|api\.openai\.com|\b429\b|codex[^\n]{0,40}(?:auth|sign-?in|token)")


def _real_state() -> dict:
    """Checksums of the real OpenClaw and Codex configs and the listing of OpenClaw's extensions: a scratch check must
    leave all three as it found them."""
    import hashlib

    out = {}
    for name, path in (("openclaw.json", Path.home() / ".openclaw" / "openclaw.json"),
                       ("codex config.toml", Path.home() / ".codex" / "config.toml")):
        out[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    ext = Path.home() / ".openclaw" / "extensions"
    out["openclaw extensions"] = sorted(p.name for p in ext.iterdir()) if ext.is_dir() else None
    return out


def _scratch_bundle(folder: Path, record: Path) -> Path:
    """A Claude bundle whose one MCP server is a stand-in that writes down how it was started, then exits."""
    (folder / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (folder / "bin").mkdir(exist_ok=True)
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "scratch-bundle", "version": "0.0.1", "description": "Slate's client-behaviour check"}) + "\n")
    (folder / ".mcp.json").write_text(json.dumps({"mcpServers": {"echo-args": {
        "command": f"{PLACEHOLDER}/bin/echo-args", "args": ["--root", PLACEHOLDER]}}}, indent=1) + "\n")
    echo = folder / "bin" / "echo-args"
    echo.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> {shlex.quote(str(record))}\nexit 1\n")
    echo.chmod(0o755)
    return folder


def client_check_bundle_placeholders(out: Path | None = None, keep: bool = False) -> dict:
    """Does OpenClaw expand ${CLAUDE_PLUGIN_ROOT} in a Claude bundle's .mcp.json? One local agent turn in a sealed
    scratch home starts the bundle's MCP server before its (refused) model call; the server writes down its command
    line."""
    root = Path(tempfile.mkdtemp(prefix="slate-client-check-"))
    before = _real_state()
    record: dict = {"check": "openclaw-claude-bundle-placeholders", "scratch": str(root)}
    try:
        home = scratch.openclaw_home(root, config=DEAD_PROVIDER)
        sealed = scratch.problems(home)
        record["sealed"] = not sealed
        if sealed:
            record.update({"result": "refused: the scratch home isn't sealed", "problems": sealed})
            return record
        args_log = root / "echo-args.log"
        bundle = _scratch_bundle(root / "scratch-bundle", args_log)
        steps = {"install": [openclaw_bin(), "plugins", "install", str(bundle), "--force", "--accept-capabilities"],
                 "turn": [openclaw_bin(), "agent", "--local", "--agent", "main", "--session-id", "slate-client-check",
                          "--message", "hi", "--json", "--timeout", "60"]}
        texts = []
        for name, argv in steps.items():
            res = run_group(argv, stdin_text=None, env=home.env, cwd=str(root), timeout=240)
            texts += [res["stdout"], res["stderr"]]
            record[name] = {"exit": res["exit"], "killed": res["killed"], "seconds": res["seconds"],
                            "tail": runner.scrub((res["stdout"] + res["stderr"])[-1200:])}
        started = args_log.read_text(encoding="utf-8").splitlines() if args_log.exists() else []
        logs = [p.read_text(encoding="utf-8", errors="replace") for p in (root / "home").rglob("*.log") if p.is_file()]
        bundle_lines = [ln.strip() for text in logs + texts for ln in text.splitlines() if "bundle-mcp" in ln]
        record["server_started_with"] = [runner.scrub(ln) for ln in started]
        record["openclaw_log"] = [runner.scrub(ln)[:400] for ln in dict.fromkeys(bundle_lines)][:5]
        if not started:
            record["result"] = "not seen: the bundle's server never started"
        elif any(PLACEHOLDER in ln for ln in started):
            record["result"] = "refuted: the placeholder reached the server unexpanded"
        else:
            record["result"] = "confirmed: expanded to the installed bundle's folder"
        # The seal: nothing found the Codex CLI's sign-in or called an embeddings API.
        leaks = [ln.strip() for text in logs + texts for ln in text.splitlines() if LEAK_RE.search(ln)]
        record["seal"] = {"codex_home_empty": not any(home.codex_home.iterdir()),
                          "memory_or_sign_in_lines": [runner.scrub(ln)[:300] for ln in dict.fromkeys(leaks)][:10]}
    finally:
        after = _real_state()
        record["real_state_unchanged"] = before == after
        if before != after:
            record["real_state_changed"] = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / "client-check-openclaw-bundle-placeholders.json").write_text(
                json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
    return record


def recorded(context: str) -> dict | None:
    path = paths.data() / "doctor" / f"{context}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    counts = {s: sum(1 for c in data.get("checks", []) if c.get("status") == s) for s in ("green", "yellow", "red")}
    return {"file": str(path), "status": data.get("status"), "counts": counts,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--context", default="all", choices=["pursuit-slice", "openclaw-gateway", "all"])
    ap.add_argument("--demo", action="store_true", help="ask each agent to run `slate demo` (P201) instead of doctor")
    ap.add_argument("--out", help="a folder for the result and the agents' transcripts (scrubbed)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--client-check", choices=["openclaw-bundle-placeholders"],
                    help="check a client behaviour hands-on in a sealed scratch home instead (no model call)")
    ap.add_argument("--keep", action="store_true", help="with --client-check: keep the scratch folder to look at")
    args = ap.parse_args()
    out = Path(args.out).expanduser() if args.out else None
    if args.client_check:
        result = client_check_bundle_placeholders(out, keep=args.keep)
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 0 if result.get("result", "").startswith(("confirmed", "refuted")) and result["real_state_unchanged"] \
            else 1
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, _on_stop)
    trust = {"leftovers_pruned_before": [] if args.dry_run else prune_leftover_trust()}
    runs = []

    def keep(record: dict) -> None:
        """Each context's result goes to disk as soon as it's in, so a stop later doesn't lose it."""
        runs.append(record)
        if out and not args.dry_run:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{record['context']}-{record['spike']}.json").write_text(
                json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        if args.context in ("pursuit-slice", "all"):
            keep(run_slice(args.dry_run, demo=args.demo, out=out))
        if args.context in ("openclaw-gateway", "all"):
            keep(run_gateway(args.dry_run, demo=args.demo, out=out))
    finally:
        if not args.dry_run:
            # Anything still there for a spike's folder that's gone (an agent can start a Codex of its own) comes out.
            trust["leftovers_pruned_after"] = prune_leftover_trust()
            trust["left"] = trust_left()
            if out:
                out.mkdir(parents=True, exist_ok=True)
                (out / "codex-trust.json").write_text(json.dumps(trust, indent=1) + "\n", encoding="utf-8")
    result = {"spike": "demo" if args.demo else "doctor", "runs": runs, "codex_trust": trust,
              "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if not args.demo:
        result["recorded"] = {c: recorded(c) for c in ("claude-code", "pursuit-slice", "openclaw-gateway")}
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if out and not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{result['spike']}-spike.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.demo and not args.dry_run:
        return 0 if all(r.get("result") == "passed" for r in runs) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
