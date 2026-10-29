"""Offline routing-freshness regressions for canonical Slate modules.

These tests call the real routing gate, report writer/readback and inherited-file
resolver. They never compile a scene, call an engine, render media or contact a
service, and do not shadow modules or invent discovery results.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from slate import gates, jsonio, procs
from slate.engine import bridge, cli as engine_cli
from slate.render import deps, shot_engines
from slate.state import lock

GATE = "handin.act2.shots"
WEB = "scenes/shots/W01/web/index.html"
ACT = "scenes/act2/index.tsx"


@pytest.fixture(autouse=True)
def offline_boundary(monkeypatch, tmp_path):
    assert gates is sys.modules["slate.gates"]
    assert engine_cli is sys.modules["slate.engine.cli"]
    assert shot_engines is sys.modules["slate.render.shot_engines"]
    for variable, name in [
        ("SLATE_DATA", "data"),
        ("SLATE_FILMS", "films"),
        ("SLATE_BRANDS", "brands"),
    ]:
        folder = tmp_path / "runtime" / name
        folder.mkdir(parents=True)
        monkeypatch.setenv(variable, str(folder))
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "1")

    def forbidden(*args, **kwargs):
        pytest.fail("Routing freshness must not launch a child or call an engine.")

    monkeypatch.setattr(procs, "run", forbidden)
    monkeypatch.setattr(bridge, "call", forbidden)
    monkeypatch.setattr(deps, "bridge_call", forbidden)


def _put(film, relative, text):
    target = film / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _write(film, relative, value):
    return _put(film, relative, json.dumps(value, sort_keys=True) + "\n")


def _film(tmp_path, name="neutral-child", *, timing=True):
    film = tmp_path / name
    settings = {
        "id": name,
        "title": "Neutral routing fixture",
        "width": 640,
        "height": 360,
        "fps_num": 30000,
        "fps_den": 1001,
        "engine": "remotion",
        "mode": "supervised",
    }
    shots = {"shots": [
        {"id": "W01", "act": 2, "engine": "web", "frame_in": 90, "frame_out": 102},
    ]}
    _write(film, "film.json", settings)
    if timing:
        _write(film, "timing/shots.json", shots)
    _put(film, ACT, "export default function NeutralAct() { return null; }\n")
    _put(film, WEB, "<!doctype html><title>Neutral replacement</title>\n")
    return film


def _gate(film):
    # This is the actual render dependency reader, including parent-only timing.
    shots = deps.load_shots(film)
    results = engine_cli._shot_engine_gates(film, shots, [2])
    assert len(results) == 1
    result = results[0]
    assert result.gate == GATE and result.status == "PASS", result.to_dict()
    return result


def _cache(film, result=None):
    result = _gate(film) if result is None else result
    gates.write_report(film, [result])
    return result


def _read(film, gate=GATE):
    paths = [film / "qa" / "gates.json", film / "qa" / "history.jsonl"]
    before = {path: path.read_bytes() for path in paths}
    result = gates.load_report(film)[gate]
    assert {path: path.read_bytes() for path in paths} == before, (
        "Readback must project freshness without rewriting the original report/history."
    )
    return result


def _assert_input_stale(film):
    result = _read(film)
    assert result.status == "STALE", result.to_dict()
    assert result.details["was"] == "PASS"
    assert result.summary.startswith("inputs changed since this was checked")


def _inherited(tmp_path):
    parent = _film(tmp_path, "neutral-parent")
    child = _film(tmp_path, timing=False)
    source = parent / "timing" / "shots.json"
    lock.add_ref(
        child, "timing/shots.json",
        parent_id="neutral-parent", parent_dir=parent,
        parent_rel="timing/shots.json", sha256=jsonio.sha256(source),
    )
    assert not (child / "timing" / "shots.json").exists()
    assert lock.resolve(child, "timing/shots.json") == source
    assert deps.load_shots(child) == jsonio.read(source)
    return child, parent


def test_undeclared_competing_sources_produce_fail(tmp_path):
    film = _film(tmp_path)
    shots = deps.load_shots(film)
    shots["shots"][0].pop("engine")
    _write(film, "timing/shots.json", shots)
    _put(
        film, "scenes/shots/W01/remotion/index.tsx",
        "export default function NeutralShot() { return null; }\n",
    )
    # Require the real symmetric API, not a fake returning an empty scene map.
    candidates = shot_engines.scene_candidates(film)
    assert set(candidates["W01"]) == {"web", "remotion"}
    assert "W01" not in shot_engines.scene_engines(film)
    results = engine_cli._shot_engine_gates(film, shots, [2])
    assert len(results) == 1
    result = results[0]
    assert result.gate == GATE and result.status == "FAIL"
    assert any("competing" in problem for problem in result.evidence)
    assert result.engine["rule_revision"] == 1
    gates.write_report(film, [result])
    assert _read(film).status == "FAIL"


def test_pure_act_without_declaration_or_shot_source_remains_unaffected(tmp_path):
    film = _film(tmp_path)
    shots = deps.load_shots(film)
    shots["shots"][0].pop("engine")
    (film / WEB).unlink()
    _write(film, "timing/shots.json", shots)
    assert engine_cli._shot_engine_gates(film, shots, [2]) == []


def test_unchanged_fresh_gate_records_complete_missing_candidate_evidence(tmp_path):
    film = _film(tmp_path)
    result = _cache(film)
    expected = {
        "timing/shots.json", "script/script.json", "scenes/ae/act2.json",
        "scenes/act2/web/index.html",
        "scenes/act2/index.tsx", "scenes/act2/index.ts",
        "scenes/act2/index.jsx", "scenes/act2/index.js",
        "scenes/shots/W01/web/index.html",
        "scenes/shots/W01/remotion/index.tsx", "scenes/shots/W01/remotion/index.ts",
        "scenes/shots/W01/remotion/index.jsx", "scenes/shots/W01/remotion/index.js",
    }
    assert set(result.inputs) == expected
    for name in ["timing/shots.json", ACT, WEB]:
        assert result.inputs[name] == jsonio.sha256(film / name)
    for name in expected - {"timing/shots.json", ACT, WEB}:
        assert result.inputs[name] is None
    assert result.engine["rule_revision"] == gates.rule_revision(GATE) == 1
    assert _read(film).status == "PASS"


@pytest.mark.parametrize("layout", ["shots", "legacy_lines"])
def test_script_fallback_records_consumed_declarations(tmp_path, layout):
    film = _film(tmp_path)
    shots = deps.load_shots(film)
    shots["shots"][0].pop("engine")
    _write(film, "timing/shots.json", shots)
    rows = [{"id": "W01", "engine": "web"}]
    act = {"shots": rows} if layout == "shots" else {"shots": [], "lines": rows}
    source = _write(film, "script/script.json", {"acts": [act]})
    result = _cache(film)
    assert result.details["shots"] == {"W01": "web"}
    assert result.inputs["script/script.json"] == jsonio.sha256(source)
    assert _read(film).status == "PASS"


def test_timing_declaration_still_wins_script_fallback(tmp_path):
    film = _film(tmp_path)
    source = _write(film, "script/script.json", {
        "acts": [{"shots": [{"id": "W01", "engine": "remotion"}]}],
    })
    result = _cache(film)
    assert result.details["shots"] == {"W01": "web"}
    assert result.inputs["script/script.json"] == jsonio.sha256(source)
    assert _read(film).status == "PASS"


@pytest.mark.parametrize("change", ["edit", "remove"])
def test_script_fallback_edit_or_removal_stales_cached_result(tmp_path, change):
    film = _film(tmp_path)
    shots = deps.load_shots(film)
    shots["shots"][0].pop("engine")
    _write(film, "timing/shots.json", shots)
    source = _write(film, "script/script.json", {
        "acts": [{"shots": [{"id": "W01", "engine": "web"}]}],
    })
    _cache(film)
    if change == "remove":
        source.unlink()
    else:
        _write(film, "script/script.json", {
            "acts": [{"shots": [{"id": "W01", "engine": "remotion"}]}],
        })
    _assert_input_stale(film)


@pytest.mark.parametrize("relative", [
    "scenes/act2/index.js",
    "scenes/act2/web/index.html",
    "scenes/ae/act2.json",
    "scenes/shots/W01/remotion/index.tsx",
], ids=["own_module", "own_web", "own_ae", "shot_module"])
def test_missing_entry_creation_stales_cached_result(tmp_path, relative):
    film = _film(tmp_path)
    result = _cache(film)
    assert result.inputs[relative] is None
    # This is a file-presence witness only. No module or AE document is loaded.
    _put(film, relative, "Neutral source-presence witness.\n")
    _assert_input_stale(film)


@pytest.mark.parametrize("relative", [ACT, WEB], ids=["own_module", "shot_web"])
def test_existing_entry_removal_stales_cached_result(tmp_path, relative):
    film = _film(tmp_path)
    result = _cache(film)
    assert result.inputs[relative] == jsonio.sha256(film / relative)
    (film / relative).unlink()
    _assert_input_stale(film)


def test_parent_only_timing_is_hashed_and_unchanged_result_stays_fresh(tmp_path):
    child, parent = _inherited(tmp_path)
    result = _cache(child)
    assert result.inputs["timing/shots.json"] == jsonio.sha256(parent / "timing/shots.json")
    assert result.inputs["timing/shots.json"] is not None
    assert _read(child).status == "PASS"
    assert not (child / "timing" / "shots.json").exists()


def test_parent_timing_change_stales_child_routing_result(tmp_path):
    child, parent = _inherited(tmp_path)
    _cache(child)
    shots = jsonio.read(parent / "timing/shots.json")
    shots["shots"][0]["frame_out"] += 1
    _write(parent, "timing/shots.json", shots)
    _assert_input_stale(child)


def test_new_child_timing_override_stales_inherited_result(tmp_path):
    child, parent = _inherited(tmp_path)
    _cache(child)
    shots = jsonio.read(parent / "timing/shots.json")
    shots["shots"][0]["frame_out"] += 2
    own = _write(child, "timing/shots.json", shots)
    assert lock.resolve(child, "timing/shots.json") == own
    _assert_input_stale(child)


def test_removing_child_override_stales_previously_local_result(tmp_path):
    child, parent = _inherited(tmp_path)
    shots = jsonio.read(parent / "timing/shots.json")
    shots["shots"][0]["frame_out"] += 2
    own = _write(child, "timing/shots.json", shots)
    result = _cache(child)
    assert result.inputs["timing/shots.json"] == jsonio.sha256(own)
    own.unlink()
    assert lock.resolve(child, "timing/shots.json") == parent / "timing/shots.json"
    _assert_input_stale(child)


def test_legacy_routing_result_without_revision_is_stale(tmp_path):
    film = _film(tmp_path)
    result = _gate(film)
    result.engine = {key: value for key, value in result.engine.items() if key != "rule_revision"}
    _cache(film, result)
    loaded = _read(film)
    assert loaded.status == "STALE"
    assert loaded.details["was"] == "PASS"
    assert loaded.summary.startswith("check rule revision is missing, invalid or changed")


@pytest.mark.parametrize("revision", [0, "1", True], ids=["older", "string", "boolean"])
def test_malformed_or_mismatched_selected_revision_is_stale(tmp_path, revision):
    film = _film(tmp_path)
    result = _gate(film)
    result.engine = {**result.engine, "rule_revision": revision}
    _cache(film, result)
    loaded = _read(film)
    assert loaded.status == "STALE"
    assert loaded.details["was"] == "PASS"
    assert loaded.summary.startswith("check rule revision is missing, invalid or changed")


@pytest.mark.parametrize("name, revision", [
    ("access", 1),
    ("sound.voice_limiter", 1),
    ("sound.peaks", None),
], ids=["access", "voice_limiter", "unrevised_sound"])
def test_existing_access_sound_and_unrevised_rules_remain_unchanged(tmp_path, name, revision):
    film = _film(tmp_path)
    assert gates.rule_revision(name) == revision
    engine = {"name": "neutral-cache-control", "version": "1"}
    if revision is not None:
        engine["rule_revision"] = revision
    # A synthetic cache fixture: no actual picture, sound or accessibility check is claimed.
    result = gates.GateResult(name, "PASS", "Private cache control only.", engine=engine)
    _cache(film, result)
    loaded = _read(film, name)
    assert loaded.status == "PASS" and loaded.summary == result.summary
    assert loaded.engine == engine
