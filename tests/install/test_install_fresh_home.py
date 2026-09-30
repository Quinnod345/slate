"""The clean-machine test (scripts/fresh_home_test.sh), run for real: a new home, a minimal PATH, `curl | bash`.

It clones a snapshot of this checkout, builds a real environment from uv's cache (offline), installs, installs again,
and uninstalls, all inside a temp folder. That takes a minute or more (71 seconds on a busy Mac on 2026-09-30), so
it's marked slow and runs only with SLATE_TEST_SLOW=1; everything it checks piece by piece is covered by the fast
tests in this folder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from install_helpers import REPO

MUST_PASS = [
    "installer (curl | bash)", "clone", "environment", "shim ~/.local/bin", "shim /opt/homebrew/bin (sandboxed)",
    "skill links", "never ~/.codex/skills", "stale copy moved aside", "other skills untouched",
    "MCP registration commands", "no agent settings written", "job runner (shown)", "second run changes nothing",
    "uninstall", "real Mac untouched",
]
# PASS where they can be checked; UNCHECKED (with the reason) where they can't yet. Never FAIL.
PASS_OR_UNCHECKED = ["ocr extra", "doctor", "doctor's expected reds"]


@pytest.mark.slow
@pytest.mark.skipif(not (shutil.which("uv") or os.path.exists("/opt/homebrew/bin/uv")), reason="uv isn't installed")
def test_fresh_home_install(tmp_path):
    report = tmp_path / "fresh.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX_", "OPENCLAW"))}
    proc = subprocess.run(["/bin/bash", str(REPO / "scripts/fresh_home_test.sh"), "--report", str(report)],
                          capture_output=True, text=True, env=env, timeout=900)
    assert report.is_file(), proc.stdout[-3000:] + proc.stderr[-2000:]
    checks = {c["name"]: c for c in json.loads(report.read_text())["checks"]}
    failed = {n: c["evidence"] for n, c in checks.items() if c["status"] == "FAIL"}
    assert not failed, failed
    for name in MUST_PASS:
        assert checks.get(name, {}).get("status") == "PASS", (name, checks.get(name))
    others = [c for n, c in checks.items() if n not in MUST_PASS]
    assert all(c["name"] in PASS_OR_UNCHECKED for c in others), others
    unchecked = [c for c in checks.values() if c["status"] == "UNCHECKED"]
    assert proc.returncode == (3 if unchecked else 0), proc.stdout[-2000:]
    assert all(c["evidence"] for c in unchecked), "every UNCHECKED says why"
