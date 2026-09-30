"""`slate status`: per-stage state and the one next command, on empty, partial and stale films."""

from __future__ import annotations

import json

from slate import gates
from slate.cli import run_cli
from slate.state import approvals, lock, production, status

KEYS = ["brief", "truth", "script", "voice", "timing", "look", "scenes", "captures", "generated", "sound", "render",
        "master", "qa", "deliver", "review"]


def _stages(st: dict) -> dict:
    return {s["stage"]: s for s in st["stages"]}


def test_empty_film(new_film):
    fd = new_film()  # no request recorded
    st = status.compute(fd)
    assert [s["stage"] for s in st["stages"]] == KEYS
    s = _stages(st)
    assert s["brief"]["state"] == "missing"
    assert s["script"]["state"] == "missing" and s["timing"]["state"] == "missing"
    assert s["captures"]["state"] == "skipped" and s["generated"]["state"] == "skipped"
    assert all(x["state"] in ("fresh", "stale", "missing", "locked", "skipped") for x in st["stages"])
    assert st["next"]["command"].startswith("slate film ask --request") and st["next"]["available"]
    assert not st["ok"]
    production.set_request(fd, "Make a short film about the planner.", source="Test Person")
    st = status.compute(fd)
    assert _stages(st)["brief"]["state"] == "fresh"
    assert st["next"]["command"] == "slate script build" and st["next"]["stage"] == "script"


def test_partial_film_then_stale(new_film, script_writer):
    fd = new_film(request="Make a short film.", request_source="Test Person")
    script_writer(fd, shots=3)
    (fd / "script" / "storyboard.json").write_text("{}")
    lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
    (fd / "script" / "SCRIPT.md").write_text("# script\n")
    lock.record(fd, "script/SCRIPT.md", producer="slate script build", inputs=["script/script.json"])
    for rel in ("timing/shots.json", "timing/vo_cues.json", "timing/vo_words.json", "timing/timeline.json"):
        (fd / rel).write_text("{}")
        lock.record(fd, rel, producer="slate timing", inputs=["script/storyboard.json"])
    s = _stages(status.compute(fd))
    assert s["script"]["state"] == "fresh" and "3 shots" in s["script"]["summary"]
    assert s["timing"]["state"] == "fresh"
    assert s["voice"]["state"] == "missing"
    script_writer(fd, shots=4)  # the hand-edited script moved
    s = _stages(status.compute(fd))
    assert s["script"]["state"] == "stale" and "script/storyboard.json" in s["script"]["reasons"][0]
    assert s["timing"]["state"] == "stale"  # made from a storyboard that is itself stale
    st = status.compute(fd)
    assert st["next"]["command"] == "slate script build"


def test_stale_gates_place_themselves(new_film):
    fd = new_film(request="Make a short film.", request_source="Test Person")
    mix = fd / "sound" / "mix.wav"
    mix.write_bytes(b"one")
    gates.write_report(fd, [
        gates.GateResult.pass_("loudness", "-14.0 LUFS", inputs=gates.inputs_for(fd, ["sound/mix.wav"])),
        gates.GateResult.fail("pace_s03", "2.9 words a second", details={"stage": "script"}),
    ])
    s = _stages(status.compute(fd))
    assert s["sound"]["gates"] == {"loudness": "PASS"}
    assert s["script"]["gates"] == {"pace_s03": "FAIL"} and not s["script"]["ok"]
    assert s["qa"]["state"] == "fresh" and not s["qa"]["ok"]  # the FAIL keeps qa from being done
    mix.write_bytes(b"two")
    s = _stages(status.compute(fd))
    assert s["sound"]["state"] == "stale" and "loudness" in s["sound"]["reasons"][0]
    assert s["qa"]["state"] == "stale"


def test_locked_states(new_film, script_writer):
    fd = new_film(request="Make a short film.", request_source="Test Person")
    script_writer(fd)
    (fd / "script" / "storyboard.json").write_text("{}")
    lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
    approvals.record(fd, what="script", path="script/storyboard.json", approved_by="Test Person")
    s = _stages(status.compute(fd))
    assert s["script"]["state"] == "locked"
    (fd / "voice" / "takes").mkdir()
    (fd / "voice" / "takes" / "S01.wav").write_text("take")
    lock.record(fd, "voice/takes/S01.wav", producer="slate voice tts", inputs=["script/storyboard.json"])
    approvals.record(fd, what="take", path="voice/takes/S01.wav", approved_by="Test Person")
    assert _stages(status.compute(fd))["voice"]["state"] == "locked"
    (fd / "timing" / "shots.json").write_text("{}")
    (fd / "renders" / "registry.json").write_text(json.dumps({"entries": [{"act": 1, "file": "renders/a1.mov"}]}))
    st = status.compute(fd)
    assert st["locks"]["timeline"] and _stages(st)["timing"]["state"] == "locked"
    assert st["locks"]["locked_assets"] == 1
    (fd / "voice" / "takes" / "S01.wav").write_text("overwritten!")
    st = status.compute(fd)
    assert st["locks"]["tampered"] == ["voice/takes/S01.wav"]
    assert st["next"]["command"] is None and "put the approved version back" in st["next"]["why"].lower()


def test_sitting_is_the_next_step_when_preproduction_is_done(new_film, state_env, script_writer):
    fd = new_film(request="Make a short film.", request_source="Test Person", brand="onboarded-brand")
    script_writer(fd)
    (fd / "script" / "storyboard.json").write_text("{}")
    lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])
    gates.write_report(fd, [gates.GateResult.pass_("claims", "every claim sourced")])
    (fd / "voice" / "shortlist.json").write_text("{}")
    lock.record(fd, "voice/shortlist.json", producer="slate voice cast", inputs=[])
    st = status.compute(fd)
    assert _stages(st)["look"]["state"] == "skipped"  # the brand's look is approved
    assert st["next"]["who"] == "person" and st["next"]["sitting"] == "preproduction"
    assert st["next"]["command"].startswith("slate approve checklist")
    for what, path in (("checklist", "brief/GOAL.md"), ("claims", "script/script.json"),
                       ("script", "script/storyboard.json"), ("voice_shortlist", "voice/shortlist.json")):
        approvals.record(fd, what=what, path=path, approved_by="Test Person")
    st = status.compute(fd)
    assert st["sittings"]["preproduction"]["done"]
    assert st["next"]["stage"] == "timing" and st["next"]["command"] == "slate timing"


def test_status_cli_json_and_listing(new_film, state_env):
    fd = new_film(request="Make a short film.", request_source="Test Person")
    code, out, err = run_cli(["status", "--film", str(fd), "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["film"] == "f1" and data["next"]["command"] and len(data["stages"]) == 15
    code, out, _ = run_cli(["status", "--film", "f1"])
    assert code == 0 and "next:" in out and "stage" in out
    code, out, _ = run_cli(["status", "--json"])  # not inside a film: list the films
    assert code == 0 and json.loads(out)["films"][0]["film"] == "f1"
    code, out, _ = run_cli(["status", "--film", "no-such-film", "--json"])
    assert code == 2
    run_cli(["status", "--film", str(fd), "--write"])
    assert "From `slate status`" in (fd / "brief" / "PRODUCTION.md").read_text()


def test_unparseable_files_never_crash_status(new_film):
    fd = new_film(request="Make a short film.", request_source="Test Person")
    (fd / "script" / "script.json").write_text("{ not json")
    (fd / "qa" / "gates.json").write_text("{ nope")
    (fd / "renders" / "registry.json").write_text("{ nope")
    st = status.compute(fd)
    s = _stages(st)
    assert "doesn't parse" in " ".join(s["script"]["problems"])
    assert not s["qa"]["ok"]
