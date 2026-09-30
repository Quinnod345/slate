"""`slate make`: a small DAG that runs only stale nodes, in order, through each stage's own command."""

from __future__ import annotations

import json

from slate.cli import run_cli
from slate.state import approvals, make


def _film_with_script(new_film, script_writer):
    fd = new_film(request="Make the planner film.", request_source="Test Person")
    script_writer(fd, shots=2)
    return fd


def test_make_runs_only_stale_nodes(new_film, script_writer, fake_cli):
    runs = fake_cli["runs"]
    fd = _film_with_script(new_film, script_writer)
    (fd / "sound" / "score.wav").write_bytes(b"score take 1")
    res = make.run(fd, "deliver")
    assert res["exit_code"] == 0, res
    assert runs == ["storyboard", "voice", "timing", "events", "render", "mix", "master", "qa", "deliver"]
    assert all(r["action"] == "ran" for r in res["results"])
    runs.clear()
    res = make.run(fd, "all")
    assert runs == [] and all(r["action"] == "skipped" for r in res["results"])  # everything fresh: nothing runs
    (fd / "sound" / "mix" / "final_mix.wav").write_bytes(b"hand edited")  # a generated file edited by hand
    make.run(fd, "deliver")
    assert runs == ["mix"], runs  # remade byte-identical from the same inputs, so nothing downstream is stale
    runs.clear()
    (fd / "sound" / "score.wav").write_bytes(b"score take 2")  # an input only the mix reads
    make.run(fd, "deliver")
    assert runs == ["mix", "master", "qa", "deliver"], runs
    runs.clear()
    script_writer(fd, shots=3)  # the script moved: everything made from it rebuilds, in order
    make.run(fd, "timing")
    assert runs == ["storyboard", "voice", "timing"]
    runs.clear()
    res = make.run(fd, "deliver")
    assert runs == ["events", "render", "mix", "master", "qa", "deliver"]


def test_force_rebuilds_only_the_target(new_film, script_writer, fake_cli):
    runs = fake_cli["runs"]
    fd = _film_with_script(new_film, script_writer)
    make.run(fd, "timing")
    runs.clear()
    make.run(fd, "timing", force=True)
    assert runs == ["timing"]


def test_not_built_commands_are_reported_not_faked(new_film, script_writer, fake_cli):
    fake_cli["module"].NO_MIX = True
    runs = fake_cli["runs"]
    fd = _film_with_script(new_film, script_writer)
    res = make.run(fd, "deliver")
    by = {r["node"]: r for r in res["results"]}
    assert res["exit_code"] == 3
    assert by["mix"]["action"] == "not built" and "isn't built" in by["mix"]["why"]
    assert by["master"]["action"] == "blocked" and by["deliver"]["action"] == "blocked"
    assert "mix" not in runs and "master" not in runs and "render" in runs  # the other branch still ran


def test_locked_outputs_are_never_remade(new_film, script_writer, fake_cli):
    runs = fake_cli["runs"]
    fd = _film_with_script(new_film, script_writer)
    make.run(fd, "voice")
    approvals.record(fd, what="take", path="voice/takes/S01.wav", approved_by="Test Person")
    approvals.record(fd, what="take", path="voice/takes.json", approved_by="Test Person")
    runs.clear()
    script_writer(fd, shots=5)
    res = make.run(fd, "voice")
    by = {r["node"]: r for r in res["results"]}
    assert runs == ["storyboard"] and by["voice"]["action"] == "skipped" and "locked" in by["voice"]["why"]
    res = make.run(fd, "voice", force=True)
    assert {r["node"]: r for r in res["results"]}["voice"]["action"] == "refused" and res["exit_code"] == 1


def test_paid_nodes_need_spend_outside_replay(new_film, script_writer, fake_cli, monkeypatch):
    runs = fake_cli["runs"]
    fd = _film_with_script(new_film, script_writer)
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    res = make.run(fd, "timing")
    by = {r["node"]: r for r in res["results"]}
    assert by["voice"]["action"] == "needs --spend" and res["exit_code"] == 4 and "voice" not in runs
    runs.clear()
    res = make.run(fd, "timing", spend=True)
    assert runs == ["voice", "timing"] and res["exit_code"] == 0


def test_dry_run_and_cli(new_film, script_writer, fake_cli):
    runs = fake_cli["runs"]
    fd = _film_with_script(new_film, script_writer)
    code, out, err = run_cli(["make", "timing", "--dry-run", "--film", str(fd), "--json"])
    assert code == 0, err
    assert [r["action"] for r in json.loads(out)["results"]] == ["would run"] * 3 and runs == []
    code, out, err = run_cli(["make", "timing", "--film", str(fd), "--json"])
    assert code == 0, err
    assert json.loads(out)["plan"] == ["storyboard", "voice", "timing"]
    assert runs == ["storyboard", "voice", "timing"]
    code, out, _ = run_cli(["make", "--list", "--film", str(fd), "--json"])
    nodes = {n["node"]: n for n in json.loads(out)["nodes"]}
    assert nodes["timing"]["state"] == "fresh" and nodes["render"]["state"] == "missing"
    code, out, _ = run_cli(["make", "nonsense", "--film", str(fd), "--json"])
    assert code == 2
    code, out, _ = run_cli(["make", "--film", str(fd), "--json"])
    assert code == 2


def test_failed_node_blocks_downstream(new_film, fake_cli):
    fd = new_film()
    (fd / "script" / "script.json").unlink()  # the fake storyboard command can still run; timing then records it
    res = make.run(fd, "timing", runner=lambda argv: (1, '{"error": "refused", "message": "script gates failed"}',
                                                      ""))
    by = {r["node"]: r for r in res["results"]}
    assert by["storyboard"]["action"] == "failed" and "script gates failed" in by["storyboard"]["why"]
    assert by["voice"]["action"] == "blocked" and by["timing"]["action"] == "blocked" and res["exit_code"] == 1
