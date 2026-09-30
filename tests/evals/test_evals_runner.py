"""The runner's live machinery, driven against a stand-in agent (fake_agent.py): no model is ever called.

Covers the environment every run gets, the slice's stdin pipe and its kill, the BROWSER tripwire and the child-process
probe end to end, OpenClaw's collect step, recording with scrubbing, and replay of recorded runs.
"""

import json
import os
import time
from pathlib import Path

import pytest

from slate.errors import UsageError
from slate.evals import commands, runner, suite, transcript


def _one(root, case_id):
    configs = suite.load_configs(root)
    cases = {c.id: c for c in suite.load_cases(root, configs=configs)}
    return cases[case_id], configs


def _checks(result):
    return {c["gate"]: c["status"] for c in result["checks"]}


def test_every_run_gets_the_eval_environment(fake_suite, slate_home, monkeypatch):
    root = fake_suite({"claude-code": "env", "pursuit-slice": "env"}, cases=["negative-email"])
    monkeypatch.setenv("HF_KEY", "not-a-real-key")
    monkeypatch.setenv("SLATE_LIVE", "1")
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    summary = runner.live([(case, "claude-code"), (case, "pursuit-slice")], configs, out=runs, run_id="t1")
    assert summary["overall"] == "PASS", summary["headline"]
    for cid in ("claude-code", "pursuit-slice"):
        pair = runs / "t1" / "negative-email" / cid
        env = json.loads(transcript.load(pair, "claude-stream-json").final)
        assert env["BROWSER"] == str(root / "tripwire" / "browser")
        assert env["SLATE_FAKE_VENDORS"] == "1" and env["SLATE_LIVE"] is None and env["HF_KEY"] is None
        assert env["SLATE_FILMS"] == str(pair / "work" / "films") and env["SLATE_DATA"] == str(pair / "slate-data")
        assert Path(env["cwd"]).resolve() == (pair / "work").resolve()
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(suite.PROBE_DIR)
        assert env["prompt"].startswith("Write a short email")
        budget = json.loads((pair / "slate-data" / "budget.json").read_text())
        assert all(v == {"elevenlabs": 0, "higgsfield": 0} for v in budget["monthly"].values())
        meta = json.loads((pair / "meta.json").read_text())
        assert meta["exit_code"] == 0 and not meta["killed"] and meta["synthetic"] is False
        assert (pair / "grade.json").exists() and (pair / "tripwire.log").read_text() == ""
    slice_env = json.loads(transcript.load(runs / "t1" / "negative-email" / "pursuit-slice", "claude-stream-json").final)
    assert slice_env["TERM"] == "dumb" and slice_env["PATH"].split(os.pathsep)[0] == f"{Path.home()}/.local/bin"
    assert (runs / "t1" / "report.json").exists()


def test_the_slice_is_killed_at_its_limit_and_fails_for_it(fake_suite, slate_home):
    root = fake_suite({"pursuit-slice": "sleep"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "pursuit-slice", configs, run_id="t2", run_dir=slate_home["tmp"] / "runs" / "t2")
    inv.timeout_s, inv.kill_grace_s = 2, 1
    started = time.monotonic()
    result = runner.run_pair(inv, case, configs)
    assert time.monotonic() - started < 20
    meta = json.loads((Path(inv.pair_dir) / "meta.json").read_text())
    assert meta["killed"] is True
    assert _checks(result)["no_stdin_wait"] == "FAIL" and result["status"] == "FAIL"
    assert _checks(result)["no_browser"] == "UNCHECKED", "finding nothing on a killed run proves nothing"


def test_the_tripwire_and_the_probe_work_end_to_end(fake_suite, slate_home):
    root = fake_suite({"claude-code": "tripwire", "codex": "slate"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    summary = runner.live([(case, "claude-code"), (case, "codex")], configs, out=runs, run_id="t3")
    by = {r["config"]: r for r in summary["results"]}
    trip = (runs / "t3" / "negative-email" / "claude-code" / "tripwire.log").read_text()
    assert "BROWSER" in trip and "https://example.com/never-opened" in trip
    assert _checks(by["claude-code"])["no_browser"] == "FAIL"
    probe = [json.loads(line) for line in (runs / "t3" / "negative-email" / "codex" / "probe.jsonl").read_text().splitlines()]
    children = [p for p in probe if p["kind"] == "child"]
    assert children and all(p["browser"] == "none" and p["stdin"] == "devnull" for p in children)
    assert any(p["kind"] == "slate_process" for p in probe)
    assert _checks(by["codex"])["slate_children_browser_none"] == "PASS"
    assert _checks(by["codex"])["slate_not_ran"] == "FAIL", "the stand-in ran slate on an email request"


def test_openclaws_transcript_is_collected_after_the_turn(fake_suite, slate_home):
    root = fake_suite({"openclaw": "envelope"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    summary = runner.live([(case, "openclaw")], configs, out=runs, run_id="t4")
    pair = runs / "t4" / "negative-email" / "openclaw"
    meta = json.loads((pair / "meta.json").read_text())
    assert "collect" in meta["collected_by"] and json.loads((pair / "run.json").read_text())["sessionId"] == "sess-123"
    t = transcript.load(pair, "openclaw")
    assert t.final == "collected sess-123"
    result = summary["results"][0]
    assert "slate_children_browser_none" in result["not_applicable"]


@pytest.mark.parametrize("mode", ["collect-path", "collect-jsonl"])
def test_the_transcript_step_takes_a_path_or_the_jsonl_itself(fake_suite, slate_home, mode):
    root = fake_suite({"openclaw": "envelope"}, cases=["negative-email"])
    configs = json.loads((root / "configs.json").read_text())
    configs["configurations"]["openclaw"]["collect"] = [
        {"argv": ["{binary}", str(Path(__file__).with_name("fake_agent.py")), mode, "{session_id}"],
         "stdout": "path", "needs": ["session_id"]}]
    (root / "configs.json").write_text(json.dumps(configs))
    case, configs = _one(root, "negative-email")
    runner.live([(case, "openclaw")], configs, out=slate_home["tmp"] / "runs", run_id=f"t-{mode}")
    pair = slate_home["tmp"] / "runs" / f"t-{mode}" / "negative-email" / "openclaw"
    assert transcript.load(pair, "openclaw").final == "transcript of sess-123"


def test_openclaw_skips_film_cases_without_allow_host_env(fake_suite, slate_home):
    root = fake_suite({"openclaw": "envelope"}, cases=["dry-run-script-stage"])
    case, configs = _one(root, "dry-run-script-stage")
    summary = runner.live([(case, "openclaw")], configs, out=slate_home["tmp"] / "runs", run_id="t5")
    assert summary["results"][0]["status"] == "UNCHECKED" and "allow-host-env" in summary["results"][0]["summary"]


def test_record_saves_a_scrubbed_fixture_that_replays(fake_suite, slate_home, monkeypatch):
    root = fake_suite({"claude-code": "env"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runner.live([(case, "claude-code")], configs, out=slate_home["tmp"] / "runs", run_id="t6", record=True)
    saved = root / "fixtures" / "recorded" / "negative-email" / "claude-code"
    text = (saved / "transcript.jsonl").read_text()
    assert str(Path.home()) not in text and "~" in text
    meta = json.loads((saved / "meta.json").read_text())
    assert "argv" not in meta and meta["case"] == "negative-email"
    summary = runner.replay([(case, "claude-code")], configs, out=slate_home["tmp"] / "runs", run_id="t6r")
    assert summary["results"][0]["source"] == str(saved) and summary["results"][0]["status"] == "PASS"


def test_scrub_takes_out_key_shapes_and_keeps_json_valid():
    line = json.dumps({"out": "Authorization: Bearer abcdefghijklmnop123456 and sk-proj-ABCDEFGHIJKLMNOPQRST "
                              "HF_KEY=id:secretvalue api_key=0123456789abcdef", "home": str(Path.home())})
    clean = runner.scrub(line)
    assert "abcdefghijklmnop123456" not in clean and "sk-proj" not in clean and "secretvalue" not in clean
    assert "0123456789abcdef" not in clean and str(Path.home()) not in clean
    json.loads(clean)


def test_replay_of_recordings_reports_whats_missing_and_whats_pending(slate_home, monkeypatch):
    monkeypatch.setenv("HOME", str(slate_home["tmp"]))  # no Oneiro here, so the strategist case stays pending
    monkeypatch.delenv("SLATE_EVALS_DIR", raising=False)
    configs = suite.load_configs()
    pairs = suite.select(suite.load_cases(configs=configs))
    summary = runner.replay(pairs, configs, out=slate_home["tmp"] / "runs", run_id="r1")
    statuses = {(r["case"], r["config"]): r["status"] for r in summary["results"]}
    assert statuses[("oneiro-strategist-slate-move", "oneiro-strategist")] == "PENDING"
    assert all(s == "UNCHECKED" for k, s in statuses.items() if k[0] != "oneiro-strategist-slate-move")
    assert summary["overall"] == "UNCHECKED" and summary["pending"] == ["oneiro-strategist-slate-move"]
    assert (slate_home["tmp"] / "runs" / "r1" / "report.json").exists()


def test_modes_are_exclusive():
    with pytest.raises(UsageError):
        runner.require_mode(True, True, False, False)
    with pytest.raises(UsageError):
        runner.require_mode(False, False, True, True)
    assert runner.require_mode(False, False, False, False) == "replay"
    assert runner.require_mode(True, False, False, False) == "dry-run"
