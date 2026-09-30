"""The probe layer: commands never raise, shared listings run once, nothing can open a browser, dropped tools stay
dropped."""

from __future__ import annotations

import os
import threading
import time

from slate import procs
from slate.doctor import core, keys
from slate.doctor.env import Env
from slate.errors import MissingPrerequisite, SlateError


class P:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_run_never_raises(monkeypatch):
    env = Env()
    monkeypatch.setattr(procs, "run", lambda argv, **kw: (_ for _ in ()).throw(MissingPrerequisite("nope isn't installed")))
    assert env.run(["nope"]).returncode == 127
    monkeypatch.setattr(procs, "run", lambda argv, **kw: (_ for _ in ()).throw(
        SlateError("slow took longer than 5 s and was stopped.")))
    assert env.run(["slow"], cache=False).returncode == 124
    monkeypatch.setattr(procs, "run", lambda argv, **kw: (_ for _ in ()).throw(PermissionError("denied")))
    assert env.run(["locked"], cache=False).returncode == 126


def test_concurrent_identical_runs_share_one_process(monkeypatch):
    env = Env()
    count = []

    def slow(argv, **kw):
        count.append(argv)
        time.sleep(0.2)
        return P(0, '{"slate": {}}')

    monkeypatch.setattr(procs, "run", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(env.run(["openclaw", "mcp", "list", "--json"])))
               for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(count) == 1 and len(results) == 4 and all(r.ok for r in results)


def test_no_open_puts_a_stand_in_open_first_and_cleans_up(monkeypatch):
    env = Env()
    seen = {}

    def capture(argv, **kw):
        seen.update(kw)
        return P(0)

    monkeypatch.setattr(procs, "run", capture)
    env.run(["/opt/homebrew/bin/higgsfield", "account", "status"], no_open=True)
    first = seen["env"]["PATH"].split(os.pathsep)[0]
    shim = os.path.join(first, "open")
    assert os.access(shim, os.X_OK) and "exit 1" in open(shim).read()
    env.cleanup()
    assert not os.path.exists(first)


def test_a_missing_vendor_module_reads_plainly(denv):
    denv.eleven = ModuleNotFoundError("No module named 'slate.vendors.elevenlabs'")
    row = {r["id"]: r for r in keys.check_keys(denv, core.make_ctx(denv))}["vendor.elevenlabs"]
    assert row["status"] == "yellow" and "isn't in this checkout" in row["summary"]


def test_dropped_tools_are_never_looked_for(denv):
    """Appendix A drops mcporter, sag, peekaboo, songsee, ImageMagick and exiftool: doctor never asks for them."""
    core.checks(env=denv)
    dropped = {"mcporter", "sag", "peekaboo", "songsee", "magick", "convert", "exiftool"}
    assert not dropped & set(denv.lookups)
    assert not [c for c in denv.calls if os.path.basename(c[0]) in dropped]


def test_agent_context_is_asked_once(monkeypatch):
    calls = []
    monkeypatch.setattr(procs, "agent_context", lambda: calls.append(1) or "codex")
    env = Env()
    assert env.agent_context() == "codex" and env.agent_context() == "codex" and calls == [1]
