"""The child-process probe: in Slate's processes it records each child's BROWSER and stdin; elsewhere it's silent."""

import json
import os
import subprocess
import sys

from slate import procs
from slate.evals import checks, suite
from slate.evals.transcript import Event, Transcript

TRIPWIRE = str(suite.evals_dir() / "tripwire" / "browser")


def _run(code, log, **extra):
    env = dict(os.environ)
    env.update({"SLATE_EVAL_PROBE_LOG": str(log), "BROWSER": TRIPWIRE, **extra})
    env["PYTHONPATH"] = str(suite.PROBE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True, text=True, timeout=60,
                   stdin=subprocess.DEVNULL)
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_slate_procs_children_are_recorded_with_browser_none(tmp_path):
    records = _run("import slate.procs as p; p.run(['/usr/bin/true'])", tmp_path / "probe.jsonl")
    children = [r for r in records if r["kind"] == "child"]
    assert children == [{**children[0], "argv0": "true", "browser": "none", "stdin": "devnull"}]
    assert any(r["kind"] == "slate_process" for r in records)


def test_a_child_started_around_slate_procs_is_caught(tmp_path):
    code = "import slate.procs, subprocess; subprocess.run(['/usr/bin/true'])"
    records = _run(code, tmp_path / "probe.jsonl")
    child = next(r for r in records if r["kind"] == "child")
    assert child["browser"] == TRIPWIRE and child["stdin"] == "inherit"
    t = Transcript("claude-stream-json", events=[Event(0, "command", text="slate status", ok=True, exit_code=0)],
                   final_text="ok", probe=records)
    ctx = checks.Ctx("c", "pursuit-slice", {"env_reaches_tools": True})
    assert checks.CHECKS["slate_children_browser_none"].fn(t, {}, ctx).status == "FAIL"
    assert checks.CHECKS["no_stdin_wait"].fn(t, {}, ctx).status == "FAIL"


def test_the_probe_is_silent_outside_slate_and_without_its_log(tmp_path):
    assert _run("import subprocess; subprocess.run(['/usr/bin/true'])", tmp_path / "probe.jsonl") == []
    env = {k: v for k, v in os.environ.items() if k != "SLATE_EVAL_PROBE_LOG"}
    env["PYTHONPATH"] = str(suite.PROBE_DIR)
    proc = subprocess.run([sys.executable, "-c", "import slate.procs as p; p.run(['/usr/bin/true']); print('ok')"],
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "ok"


def test_procs_gives_every_child_browser_none_whatever_the_caller_asks():
    assert procs.child_env({"BROWSER": "open"})["BROWSER"] == "none"
    out = procs.run(["/usr/bin/env"]).stdout
    assert "BROWSER=none" in out.splitlines()
