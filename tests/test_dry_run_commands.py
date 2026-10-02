"""`slate --dry-run <command>` means one thing for every command: say what would happen; run nothing, change nothing.

After `slate --dry-run qa` ran two minutes of whole-picture analysis outside the queue and wrote its caches, an audit
found about sixty commands and flags that ignored --dry-run or half-honoured it: some spent (a casting shootout, Voice
Design, ElevenLabs alignment), some sent (an approval request by iMessage), some prompted for a key, some opened After
Effects, and `--detach` dropped the flag and queued the real, paid job. Each now stops before it acts.

Every case here runs against one fixture film with tripwires on everything a dry run must never touch (a subprocess,
the network, a message, the job queue, the spend ledger, the budget), and the whole scratch home is compared before
and after, so a command that writes anything at all fails."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "voice"))
from voice_helpers import BRAND, make_film, real_take  # noqa: E402

from slate import jsonio, paths  # noqa: E402
from slate.cli import run_cli  # noqa: E402

SPEECH = "Every film starts with one line."


def _snapshot(*folders: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                p = Path(root) / name
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture
def film(slate_home):
    """A voiced film on a brand with a voice: one selected take (S01), its storyboard and its clock."""
    jsonio.write_atomic(slate_home["brands"] / BRAND["name"] / "brand.json", BRAND)
    folder = make_film(slate_home["films"])
    from slate.voice.film import VoiceFilm

    real_take(VoiceFilm(folder), "S01", SPEECH)
    return folder


@pytest.fixture
def tripwires(monkeypatch):
    """Everything a dry run must never do raises. `allow_procs()` lets quick reads (ffprobe) through for a case."""
    from slate import http, ledger, notify, procs
    from slate.jobs import client, runner

    def trip(what):
        def fail(*_a, **_kw):
            raise AssertionError(f"a dry run tried to {what}")
        return fail

    for name in ("imessage", "push"):
        if hasattr(notify, name):
            monkeypatch.setattr(notify, name, trip(f"send a message ({name})"))
    monkeypatch.setattr(http, "request", trip("reach the network"))
    monkeypatch.setattr(client, "submit", trip("queue a job"))
    monkeypatch.setattr(client, "detach_current", trip("queue a job"))
    monkeypatch.setattr(client, "ensure_runner", trip("start the queue's runner"))
    monkeypatch.setattr(runner, "serve", trip("serve the queue"))
    monkeypatch.setattr(runner, "reconcile", trip("settle a job"))
    for name in ("begin", "snapshot", "set_cap", "raise_film_budget"):
        monkeypatch.setattr(ledger, name, trip(f"write the spend ledger ({name})"))
    real_run, real_popen = procs.run, subprocess.Popen
    monkeypatch.setattr(procs, "run", trip("start a process"))
    monkeypatch.setattr(subprocess, "Popen", trip("start a process"))

    def allow_procs():
        monkeypatch.setattr(procs, "run", real_run)
        monkeypatch.setattr(subprocess, "Popen", real_popen)

    return allow_procs


REPO_FILES = [paths.REPO / "knowledge" / "GAPS.md", paths.REPO / "mcpb" / "manifest.json"]

# (argv after `slate --dry-run`, exit codes it may end with). Every case says it's a dry run and changes nothing.
CASES = [
    # voice: the shootout, Voice Design and the isolator spend; the rest write takes, records or files
    (["voice", "cast"], {0}),
    (["voice", "cast", "--detach"], {0}),
    (["voice", "design", "--description", "A clear, bright narrator, mid thirties, steady pace.",
      "--text", "x " * 60], {0}),
    (["voice", "design", "--description", "A clear, bright narrator, mid thirties, steady pace.",
      "--text", "x " * 60, "--detach"], {0}),
    (["voice", "design", "--folder", "{tmp}/design", "--save", "p1", "--voice-name", "Kept"], {0}),
    (["voice", "design", "--folder", "{tmp}/design", "--check"], {0}),
    (["voice", "tts", "--detach"], {0}),
    (["voice", "clean"], {0}),
    (["voice", "clean", "--method", "isolator"], {0}),
    (["voice", "clean", "--remix"], {0}),
    (["voice", "pace"], {0}),
    (["voice", "align"], {0}),
    (["voice", "align", "--engine", "elevenlabs", "--detach"], {0}),
    (["voice", "align", "--compare"], {0}),
    (["voice", "captions"], {0}),
    (["voice", "tags", "set", "S01", f"[warmly] {SPEECH}"], {0}),
    (["voice", "tags", "clear", "S01"], {0}),
    (["voice", "tags", "ab", "S01", "--variant", f"[warmly] {SPEECH}"], {0}),
    (["voice", "tags", "pick"], {0}),
    (["voice", "tags", "abc", "S01"], {0}),
    (["voice", "tags", "adopt", "S01", "k1"], {0}),
    (["voice", "polish", "--try", "0.5,1"], {0}),
    (["voice", "analyze", "S01"], {0}),
    (["voice", "check"], {0}),
    (["voice", "approve", "S01", "--by", "A Person"], {0}),
    (["voice", "comp", "S01", "--word", "2", "--from", "t02"], {0}),
    (["voice", "preview"], {0}),
    (["voice", "account", "--snapshot"], {0}),
    # generation, plates and captures
    (["gen", "cancel", "--shot", "S01"], {0}),
    (["gen", "request-approval", "--shot", "S01"], {0}),
    (["gen", "approve", "--id", "appr_x", "--by", "A Person"], {0}),
    (["gen", "pick", "--shot", "S01", "--take", "t01", "--by", "A Person"], {0}),
    (["gen", "judge", "--shot", "S01"], {0}),
    (["plates", "conform", "--shot", "S01"], {0}),
    (["plates", "matte", "--shot", "S01", "--base", "t01", "--real", "real.mov"], {0}),
    (["plates", "track", "--shot", "S01", "--hue", "200"], {0}),
    (["capture", "conform", "--name", "cap1", "--src", "{tmp}/rec.mov"], {0}),
    (["capture", "beatmap", "--name", "cap1"], {0}),
    # After Effects: nothing opens it on a dry run
    (["ae", "acts", "export", "1"], {0}),
    (["ae", "acts", "measure", "1", "--go"], {0}),
    (["ae", "acts", "render", "1", "--go"], {0}),
    (["ae", "audit", "--go"], {0}),
    (["ae", "masters", "--requeue", "--go"], {0}),
    # the queue and the spend records
    (["job", "cancel", "j-20261001-120000-abcd"], {0}),
    (["job", "retry", "j-20261001-120000-abcd"], {0}),
    (["jobs", "serve", "--once"], {0}),
    (["budget", "set", "--mode", "supervised", "--provider", "elevenlabs", "--cap", "100"], {0}),
    (["budget", "film", "--provider", "elevenlabs", "--raise", "10", "--approved-by", "A Person"], {0}),
    (["ledger", "snapshot", "--provider", "elevenlabs"], {0}),
    (["ledger", "inflight", "--recover"], {0}),
    # sound
    (["verify"], {0}),
    (["sound", "balance"], {0}),
    (["score", "assemble"], {0}),
    (["mix", "--listen", "3"], {0}),
    # timing: a pin, and words that would need transcribing (ElevenLabs alignment spends)
    (["timing", "pin", "--shot", "S01", "--words", "film"], {0}),
    (["timing", "pin", "--clear"], {0}),
    (["timing", "words", "--engine", "elevenlabs"], {0}),
    # the engine and the tools
    (["engine", "golden"], {0}),
    (["engine", "scaffold", "--act", "1", "--engine", "web"], {0}),
    (["bench", "higgsedit", "plan"], {0}),
    (["mcpb", "build", "--sync"], {0}),
    (["knowledge", "gaps", "--write"], {0}),
    (["connect", "elevenlabs"], {0}),
    (["connect", "higgsfield", "--api-key"], {0}),
]


@pytest.mark.parametrize("argv, codes", CASES, ids=[" ".join(a[:3]) + f" #{i}" for i, (a, _c) in enumerate(CASES)])
def test_a_dry_run_says_what_it_would_do_and_does_nothing(film, slate_home, tripwires, argv, codes):
    argv = [a.replace("{tmp}", str(slate_home["tmp"])) for a in argv]
    before = _snapshot(slate_home["tmp"])
    repo_before = {p: p.stat().st_mtime_ns for p in REPO_FILES if p.exists()}
    code, out, err = run_cli(["--dry-run", *argv, "--film", str(film), "--json"])
    assert code in codes, (argv, code, out[-1500:], err[-1500:])
    doc = json.loads(out)
    said = json.dumps(doc).lower()
    assert doc.get("dry_run") is True or "dry run" in said, (argv, doc)
    assert _snapshot(slate_home["tmp"]) == before, argv  # nothing written anywhere in the scratch home
    assert {p: p.stat().st_mtime_ns for p in REPO_FILES if p.exists()} == repo_before, argv


def test_a_dry_run_job_status_or_wait_only_reads(film, slate_home, monkeypatch):
    """`slate job status` settles a dead supervisor and starts a runner; a dry run reads the record as it stands."""
    from slate.jobs import client, runner

    monkeypatch.setattr(client, "ensure_runner", lambda: "running")
    job = client.submit("test", [sys.executable, "-c", "pass"], film=str(film))

    def trip(*_a, **_kw):
        raise AssertionError("a dry run settled the job or started a runner")

    monkeypatch.setattr(client, "ensure_runner", trip)
    monkeypatch.setattr(runner, "reconcile", trip)
    before = _snapshot(slate_home["tmp"])
    for verb in ("status", "wait"):
        code, out, err = run_cli(["--dry-run", "job", verb, job, "--json"])
        doc = json.loads(out)
        assert code == 0 and doc["dry_run"] is True and doc["state"] == "queued", (verb, out, err)
    assert _snapshot(slate_home["tmp"]) == before


def test_a_dry_run_moodboard_copies_nothing_into_the_pack(slate_home, tmp_path):
    """The look step's moodboards are copied into the pack; a dry run names where, and copies nothing."""
    pack = slate_home["brands"] / "drybrand"
    jsonio.write_atomic(pack / "brand.json", {"name": "drybrand"})
    board = tmp_path / "board.png"
    board.write_bytes(b"\x89PNG not really")
    before = _snapshot(pack)
    code, out, err = run_cli(["--dry-run", "brand", "onboard", "drybrand", "--step", "look", "--moodboard", str(board),
                              "--direction", "night", "--json"])
    assert code == 0, (out[-1500:], err[-1500:])
    assert _snapshot(pack) == before and not (pack / "onboarding").exists()


def test_a_dry_run_doctor_saves_no_context(slate_home, monkeypatch):
    from slate.doctor import core

    monkeypatch.setattr(core, "report", lambda name, brand=None, only=None: {"status": "green", "checks": [],
                                                                             "context": name})
    saved = []
    monkeypatch.setattr(core, "save", lambda rep, name: saved.append(name) or slate_home["data"] / f"{name}.json")
    code, out, err = run_cli(["--dry-run", "doctor", "--context", "dry-ctx", "--json"])
    assert saved == [], out
    code, out, err = run_cli(["doctor", "--context", "dry-ctx", "--json"])
    assert saved == ["dry-ctx"]  # without --dry-run it's saved, as before
