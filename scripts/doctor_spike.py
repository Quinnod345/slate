#!/usr/bin/env python3
"""Phase 1's spike: run `slate doctor --json` from each real agent context and keep what comes back.

The plan's done-when for phase 1 is "doctor runs green from all three contexts": Claude Code's shell, an Oneiro
pursuit slice (`codex exec --ignore-user-config` on gpt-6-sol, stdin a pipe, killed after ten minutes) and the
OpenClaw gateway (Oneiro main). Each agent is asked to run exactly one command and reply with its output; the
doctor itself records its run in Slate's data folder (doctor/<context>.json), which is the evidence this script
reads back. These runs cost model calls only: no vendor spend, no browser.

    scripts/doctor_spike.py [--context pursuit-slice|openclaw-gateway|all] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from slate import paths  # noqa: E402
from slate.evals import commands, suite  # noqa: E402

PROMPT = ("Run exactly this one shell command, then reply with nothing but its output: "
          "slate doctor --json --context {context}")
SLICE_PATH = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def slice_invocation(workdir: Path) -> list[str]:
    """The pursuit slice's exact argv, built by the eval runner from evals/configs.json."""
    configs = suite.load_configs()
    case = next(c for c in suite.load_cases() if c.id == "triggering-launch-video")
    inv = commands.plan(case, "pursuit-slice", configs, run_id="doctor-spike", run_dir=workdir)
    return inv.argv


def run_slice(dry_run: bool) -> dict:
    work = Path(tempfile.mkdtemp(prefix="slate-spike-slice-"))
    argv = slice_invocation(work)
    (work / "triggering-launch-video" / "pursuit-slice" / "work").mkdir(parents=True, exist_ok=True)
    prompt = PROMPT.format(context="pursuit-slice")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLATE_")}
    env.update({"PATH": os.pathsep.join(SLICE_PATH), "BROWSER": "none"})
    if dry_run:
        return {"context": "pursuit-slice", "argv": shlex.join(argv), "prompt": prompt}
    started = time.time()
    proc = subprocess.run(argv, input=prompt, text=True, capture_output=True, env=env, timeout=600,
                          cwd=str(work), start_new_session=True)
    return {"context": "pursuit-slice", "exit": proc.returncode, "seconds": round(time.time() - started, 1),
            "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-1500:]}


def run_gateway(dry_run: bool) -> dict:
    prompt = PROMPT.format(context="openclaw-gateway")
    argv = ["openclaw", "agent", "--agent", "main", "--session-key", "agent:main:slate-doctor-spike",
            "--model", "openai/gpt-5.6-sol", "--thinking", "low", "--json", "--timeout", "600", "--message", prompt]
    if dry_run:
        return {"context": "openclaw-gateway", "argv": shlex.join(argv)}
    started = time.time()
    proc = subprocess.run(argv, text=True, capture_output=True, timeout=700, stdin=subprocess.DEVNULL,
                          env={**os.environ, "BROWSER": "none"})
    return {"context": "openclaw-gateway", "exit": proc.returncode, "seconds": round(time.time() - started, 1),
            "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-1500:]}


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
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    runs = []
    if args.context in ("pursuit-slice", "all"):
        runs.append(run_slice(args.dry_run))
    if args.context in ("openclaw-gateway", "all"):
        runs.append(run_gateway(args.dry_run))
    out = {"runs": runs, "recorded": {c: recorded(c) for c in ("claude-code", "pursuit-slice", "openclaw-gateway")}}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
