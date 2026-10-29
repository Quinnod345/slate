"""Measured read-time QA must refresh through QA without repeating script production.

The media files are neutral bytes. These tests prove routing and recorded freshness without decoding media,
launching an engine, calling a vendor, or claiming that a film's picture has passed inspection.
"""

from __future__ import annotations

import pytest

from slate import gates, jsonio, procs
from slate.render import deps as render_deps
from slate.render import engines, install_checks, registry
from slate.state import film, lock, status


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Status routing must not launch a process or engine bridge command.")

    monkeypatch.setattr(procs, "run", refuse)
    monkeypatch.setattr(render_deps, "bridge_call", refuse)


@pytest.mark.parametrize("verdict", gates.STATUSES)
def test_bare_measured_readtime_belongs_to_qa_for_every_verdict(verdict):
    result = gates.GateResult("readtime", verdict, "Neutral measured read-time evidence.")
    assert status._gate_stage(result) == "qa"


@pytest.mark.parametrize("declared", tuple(status.BY_KEY))
def test_explicit_readtime_stage_retains_precedence(declared):
    result = gates.GateResult("readtime", "STALE", "Neutral declared-stage evidence.",
                              details={"stage": declared})
    assert status._gate_stage(result) == declared


@pytest.mark.parametrize("name", ["script.read_time", "read_time", "script.readtime"])
def test_script_read_time_gates_keep_the_script_handoff(name):
    assert status._gate_stage(gates.GateResult(name, "FAIL", "Neutral script planning evidence.")) == "script"


@pytest.mark.parametrize("details", [None, {"stage": "unknown-stage"}])
def test_invalid_or_absent_stage_uses_the_bare_gate_default(details):
    assert status._gate_stage(gates.GateResult("READTIME", "STALE", "Neutral read-time evidence.",
                                             details=details)) == "qa"


@pytest.fixture
def current_earlier_stages(new_film, script_writer, tmp_path, monkeypatch):
    """Record a silent one-shot film with current inputs using the existing state and render contracts."""
    fd = new_film("readtime-handoff", mode="autonomous", brand="onboarded-brand",
                  request="Make a short title film.", request_source="Test Person")
    script_writer(fd, shots=1)
    script = jsonio.read(fd / "script" / "script.json")
    script["acts"][0]["shots"][0]["vo"] = ""
    jsonio.write_atomic(fd / "script" / "script.json", script)
    jsonio.write_atomic(fd / "script" / "storyboard.json", script)
    lock.record(fd, "script/storyboard.json", producer="slate script build", inputs=["script/script.json"])

    documents = {
        "timing/shots.json": {"total_frames": 30, "acts": [{"index": 1, "shots": ["S01"]}],
                              "shots": [{"id": "S01", "act": 1, "frame_in": 0, "frame_out": 30}]},
        "timing/timeline.json": {}, "timing/vo_cues.json": {}, "timing/vo_words.json": {},
    }
    for rel, doc in documents.items():
        jsonio.write_atomic(fd / rel, doc)
        lock.record(fd, rel, producer="slate timing", inputs=["script/storyboard.json"])
    scene = fd / "scenes" / "act1" / "index.tsx"
    scene.parent.mkdir(parents=True, exist_ok=True)
    scene.write_text("export const neutralTitle = 1;\n")
    lock.record(fd, "scenes/act1/index.tsx", producer="slate scenes", inputs=["timing/shots.json"])
    (fd / "sound" / "mix.wav").write_bytes(b"neutral sound proof")
    lock.record(fd, "sound/mix.wav", producer="slate mix", inputs=["timing/shots.json"])

    engine = tmp_path / "neutral-engine"
    (engine / "remotion").mkdir(parents=True)
    (engine / "remotion" / "kit.ts").write_text("export const neutralKit = 1;\n")
    monkeypatch.setattr(render_deps, "engine_dir", lambda: engine)
    render = fd / "renders" / "act1.mov"
    render.write_bytes(b"neutral registered picture")
    fj = film.load(fd)
    registry.register(fd, {
        "act": 1, "engine": "remotion", "file": "renders/act1.mov", "frames": [0, 30], "samples": 4,
        "sha256": jsonio.sha256(render), "made_at": "2026-10-07T00:00:00+00:00", "bundle_hash": "neutral-bundle",
        "source_hash": engines.sources_hash(fd, fj, "remotion"),
        "content_hash": install_checks.content_hash(fd, fj),
    })
    (fd / "masters" / "film.mov").write_bytes(b"neutral master proof")
    lock.record(fd, "masters/film.mov", producer="slate master", inputs=["renders/act1.mov", "sound/mix.wav"])
    return fd


@pytest.mark.parametrize("verdict", ["STALE", "FAIL", "UNCHECKED"])
def test_measured_readtime_returns_to_qa_when_earlier_stages_are_current(current_earlier_stages, verdict):
    fd = current_earlier_stages
    # A prior acceptance rule makes the gate stale while the script, picture and master inputs remain current.
    revision = 0 if verdict == "STALE" else gates.rule_revision("readtime")
    result = gates.GateResult("readtime", "PASS" if verdict == "STALE" else verdict,
                              "Neutral measured read-time evidence.", engine={"rule_revision": revision},
                              inputs=gates.inputs_for(fd, ["masters/film.mov"]))
    gates.write_report(fd, [result])
    st = status.compute(fd)
    stages = {row["stage"]: row for row in st["stages"]}
    earlier = status.PIPELINE[:status.PIPELINE.index("qa")]
    assert all(stages[key]["ok"] for key in earlier if not key.startswith("@")), stages
    assert stages["script"]["gates"] == {}
    assert stages["qa"]["gates"] == {"readtime": verdict} and not stages["qa"]["ok"]
    assert st["next"]["stage"] == "qa" and st["next"]["command"] == "slate qa"
    assert not st["ok"]
