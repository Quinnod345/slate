"""`slate evals list|run|report` through the real dispatcher (replay and dry runs only: nothing live)."""

import json

from slate import cli
from slate.cli import run_cli


def test_register_adds_list_run_and_report():
    parser = cli.build_parser()
    assert "slate.evals.cli" not in cli.BROKEN
    for argv in (["evals", "list"], ["evals", "run", "--dry-run"], ["evals", "report", "some-run"]):
        assert callable(parser.parse_args(argv).handler)


def test_list_cases_and_configurations(slate_home, monkeypatch):
    monkeypatch.setenv("HOME", str(slate_home["tmp"]))
    code, out, _ = run_cli(["evals", "list", "--json"])
    assert code == 0
    rows = {r["id"]: r for r in json.loads(out)["cases"]}
    assert len(rows) == 13 and rows["oneiro-strategist-slate-move"]["status"] == "pending"
    assert rows["dry-run-script-stage"]["needs_eval_env"] is True
    code, out, _ = run_cli(["evals", "list", "--configs", "--json"])
    ids = [r["id"] for r in json.loads(out)["configurations"]]
    assert code == 0 and ids == ["claude-code", "codex", "pursuit-slice", "openclaw", "oneiro-strategist"]
    code, text, _ = run_cli(["evals", "list"])
    assert code == 0 and "13 eval cases (12 active)" in text


def test_run_defaults_to_replay_and_report_shows_the_latest(slate_home, monkeypatch):
    monkeypatch.setenv("HOME", str(slate_home["tmp"]))
    out = slate_home["tmp"] / "runs"
    code, stdout, _ = run_cli(["evals", "run", "--fixtures", "synthetic", "--out", str(out), "--run-id", "a", "--json"])
    assert code == 0 and json.loads(stdout)["overall"] == "PASS"
    code, stdout, _ = run_cli(["evals", "run", "--group", "negative", "--out", str(out), "--run-id", "b", "--json"])
    summary = json.loads(stdout)
    assert code == 1 and summary["overall"] == "UNCHECKED" and summary["mode"] == "replay"
    code, text, _ = run_cli(["evals", "report", "--out", str(out)])
    assert code == 1 and "run b" in text and "not run: no recording yet" in text
    code, text, _ = run_cli(["evals", "report", "a", "--out", str(out), "--brief"])
    assert code == 0 and "26 of 26 synthetic transcripts graded as expected" in text


def test_usage_errors(slate_home):
    out = str(slate_home["tmp"] / "runs")
    assert run_cli(["evals", "run", "--live", "--replay", "--out", out])[0] == 2
    assert run_cli(["evals", "run", "--record", "--out", out])[0] == 2
    assert run_cli(["evals", "run", "--case", "no-such-case", "--out", out])[0] == 2
    assert run_cli(["evals", "run", "--config", "no-such-config", "--out", out])[0] == 2
    assert run_cli(["evals", "report", "--out", out])[0] == 2
