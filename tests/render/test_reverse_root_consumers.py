"""Offline regressions for owning render bases, review stills and isolated patch trace refresh.

These tests use canonical Slate modules. Every real process and engine bridge remains forbidden;
explicit fake capability responses are test fixtures.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate import procs, render
from slate.errors import Refused
from slate.render import deps, engines, runner, shot_engines, stills
from slate.render.plan import Target, acts_from_shots
from slate.state import lock

# Imports must resolve to the canonical module objects used by public render consumers.


def put(film, name, value):
    path = film / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    assert render is sys.modules["slate.render"]
    assert runner is sys.modules["slate.render.runner"]
    assert stills is sys.modules["slate.render.stills"]
    assert stills.engines is engines and stills.shot_engines is shot_engines and stills.deps is deps
    assert runner.engines is engines and runner.shot_engines is shot_engines
    for variable, name in [("SLATE_DATA", "data"), ("SLATE_FILMS", "films"), ("SLATE_BRANDS", "brands")]:
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setenv(variable, str(folder))
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected real process, engine, browser or media boundary")
    monkeypatch.setattr(procs, "run", forbidden)
    monkeypatch.setattr(deps, "bridge_call", forbidden)
    monkeypatch.setattr(engines, "get", forbidden)


@pytest.fixture
def neutral(tmp_path, monkeypatch):
    film = tmp_path / "neutral-owner"
    settings = {"id": "neutral-owner", "width": 640, "height": 360,
                "fps_num": 30000, "fps_den": 1001, "mode": "supervised"}
    rows = [{"id": "A01", "act": 1, "frame_in": 0, "frame_out": 60},
            {"id": "A02", "act": 2, "frame_in": 60, "frame_out": 90},
            {"id": "B03", "act": 3, "frame_in": 90, "frame_out": 102},
            {"id": "R01", "act": 3, "engine": "remotion", "frame_in": 102, "frame_out": 114},
            {"id": "B04", "act": 3, "frame_in": 114, "frame_out": 126},
            {"id": "R02", "act": 3, "engine": "remotion", "frame_in": 126, "frame_out": 138},
            {"id": "B05", "act": 3, "frame_in": 138, "frame_out": 150}]
    shots = {"shots": rows, "total_frames": 150, "fps_num": 30000, "fps_den": 1001,
             "acts": [{"index": 1, "shots": ["A01"]}, {"index": 2, "shots": ["A02"]},
                      {"index": 3, "shots": ["B03", "R01", "B04", "R02", "B05"]}]}
    put(film, "film.json", settings)
    put(film, "timing/shots.json", shots)
    put(film, "scenes/act3/web/index.html", "<!doctype html><title>Neutral base</title>")
    for sid in ("R01", "R02"):
        put(film, f"scenes/shots/{sid}/remotion/index.tsx", "export default function Shot() { return null; }")
    monkeypatch.setattr(deps, "load_film", lambda path: copy.deepcopy(settings))
    monkeypatch.setattr(runner, "render_settings", lambda: {"chunk_frames": 600, "draft_samples": 4})
    return {"film": film, "settings": settings, "shots": shots}


def direction(data, name):
    if name == "forward":
        (data["film"] / "scenes/act3/web/index.html").unlink()
        put(data["film"], "scenes/act3/index.tsx", "export default function Base() { return null; }")
        for sid in ("R01", "R02"):
            (data["film"] / f"scenes/shots/{sid}/remotion/index.tsx").unlink()
            put(data["film"], f"scenes/shots/{sid}/web/index.html", "<!doctype html><title>Neutral replacement</title>")
            next(row for row in data["shots"]["shots"] if row["id"] == sid)["engine"] = "web"
        put(data["film"], "timing/shots.json", data["shots"])
    return "web" if name == "reverse" else "remotion"


def pure(data, name):
    for row in data["shots"]["shots"]:
        row.pop("engine", None)
    for path in data["film"].glob("scenes/shots/*/remotion/index.tsx"):
        path.unlink()
    for path in data["film"].glob("scenes/shots/*/web/index.html"):
        path.unlink()
    base_web = data["film"] / "scenes/act3/web/index.html"
    base_remotion = data["film"] / "scenes/act3/index.tsx"
    base_web.unlink(missing_ok=True)
    base_remotion.unlink(missing_ok=True)
    if name == "web":
        put(data["film"], "scenes/act3/web/index.html", "<!doctype html><title>Pure</title>")
    elif name == "remotion":
        put(data["film"], "scenes/act3/index.tsx", "export default function Pure() { return null; }")
    put(data["film"], "timing/shots.json", data["shots"])


def capability(monkeypatch):
    calls = []
    def bridge(command, args, *, timeout=None):
        calls.append(command)
        assert command == "version" and args == {}
        return {"capabilities": {"reverse_web_remotion": {"schema": 1, "enabled": True}}}
    monkeypatch.setattr(deps, "bridge_call", bridge)
    return calls


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_default_whole_act_uses_built_mixed_owner(neutral, name):
    owner = direction(neutral, name)
    _, plan = runner.prepare(neutral["film"], act=3, draft=True)
    assert plan.engine == owner and plan.targets[0].film_frames == [90, 150]
    assert plan.targets[0].scene is None and plan.targets[0].kind == "act"
    assert plan.samples == 4


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_matching_explicit_mixed_base_is_preserved(neutral, name):
    owner = direction(neutral, name)
    _, plan = runner.prepare(neutral["film"], act=3, engine=owner, samples=7)
    assert plan.engine == owner and plan.samples == 7


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_conflicting_explicit_mixed_base_refuses_before_engine(neutral, name):
    owner = direction(neutral, name)
    wrong = "remotion" if owner == "web" else "web"
    with pytest.raises(Refused, match="built " + owner + " base"):
        runner.prepare(neutral["film"], act=3, engine=wrong)


def test_default_multi_act_plan_preserves_each_visible_base(neutral):
    put(neutral["film"], "scenes/act1/index.tsx", "export default function First() { return null; }")
    _, plan = runner.prepare(neutral["film"])
    assert [(t.act.index, plan.engine_for(t)) for t in plan.targets] == [
        (1, "remotion"), (2, "remotion"), (3, "web")]
    assert plan.engine == "remotion" and plan.targets[2].engine == "web"


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_direct_execute_rechecks_conflicting_plan_before_engine(neutral, name):
    owner = direction(neutral, name)
    _, plan = runner.prepare(neutral["film"], act=3, engine=owner)
    bad = dataclasses.replace(plan, engine="remotion" if owner == "web" else "web")
    with pytest.raises(Refused, match="built " + owner + " base"):
        runner.execute(neutral["film"], neutral["settings"], bad)


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_direct_splice_cannot_omit_replacements_under_wrong_base(neutral, name):
    owner = direction(neutral, name)
    _, plan = runner.prepare(neutral["film"], act=3, engine=owner)
    bad = dataclasses.replace(plan, engine="remotion" if owner == "web" else "web")
    with pytest.raises(Refused, match="built " + owner + " base"):
        runner._splice_shots(neutral["film"], neutral["settings"], bad, bad.targets[0],
                             neutral["film"] / "unused.mov", None, go=False, fresh=False)


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_mixed_stills_keep_real_base_and_each_replacement(neutral, monkeypatch, name):
    owner = direction(neutral, name)
    calls = capability(monkeypatch)
    act = next(a for a in acts_from_shots(neutral["shots"]) if a.index == 3)
    work = stills._routes(neutral["film"], neutral["shots"],
                         [(act, [0, 12, 23, 24, 36, 47, 59])], None)
    actual = [(engine, scene.parent.name if scene else None, rows[0][1]) for (engine, scene), rows in work.items()]
    replacement = "remotion" if owner == "web" else "web"
    assert actual == [(owner, None, [0, 24, 59]), (replacement, "R01", [12, 23]),
                      (replacement, "R02", [36, 47])]
    assert calls == (["version"] if name == "reverse" else [])


@pytest.mark.parametrize("built", ["remotion", "web"])
@pytest.mark.parametrize("caller", ["remotion", "web"])
def test_pure_stills_retain_caller_preview_choice(neutral, built, caller):
    pure(neutral, built)
    act = next(a for a in acts_from_shots(neutral["shots"]) if a.index == 3)
    work = stills._routes(neutral["film"], neutral["shots"], [(act, [0, 12, 59])], caller)
    assert list(work) == [(caller, None)] and next(iter(work.values()))[0][1] == [0, 12, 59]


def test_unbuilt_pure_stills_retain_caller_fallback(neutral):
    pure(neutral, None)
    act = next(a for a in acts_from_shots(neutral["shots"]) if a.index == 3)
    work = stills._routes(neutral["film"], neutral["shots"], [(act, [0, 59])], "web")
    assert list(work) == [("web", None)]


@pytest.mark.parametrize("name", ["reverse", "forward"])
@pytest.mark.parametrize("layout", ["local", "inherited", "override"])
def test_isolated_patch_refreshes_whole_owner_once_with_resolved_timing(neutral, monkeypatch, name, layout):
    owner = direction(neutral, name)
    film = neutral["film"]
    if layout != "local":
        parent = film.parent / "parent"
        put(parent, "film.json", {**neutral["settings"], "id": "neutral-parent"})
        source = put(parent, "timing/shots.json", neutral["shots"])
        lock.add_ref(film, "timing/shots.json", parent_id="neutral-parent", parent_dir=parent,
                     parent_rel="timing/shots.json", sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        if layout == "inherited":
            (film / "timing/shots.json").unlink()
        else:
            changed = copy.deepcopy(neutral["shots"])
            changed["shots"][-1]["frame_out"] = changed["total_frames"] = 180
            put(parent, "timing/shots.json", changed)
    replacement = "remotion" if owner == "web" else "web"
    active = put(film, "timing/motion/act3.json", [{"id": "old"}])
    calls, records = [], []
    isolated = SimpleNamespace(scene=film / f"scenes/shots/R01/{replacement}",
                               film=neutral["settings"], trace=lambda act: None)
    def get(engine, path, settings):
        calls.append((engine, path, settings))
        assert engine == owner
        def trace(act):
            assert act == 3
            put(film, "timing/motion/act3.json", [])
            return {"act": 3, "written": True, "file": "timing/motion/act3.json", "entries": 0,
                    "frame_base": "film", "base_engine": owner, replacement + "_shots": ["R01", "R02"],
                    "base_file": "timing/motion/sources/act3.base.json",
                    "composition_file": "timing/motion/sources/act3.composition.json",
                    "sources": ["timing/shots.json"], "source_hashes": {}}
        return SimpleNamespace(trace=trace)
    factory = "RemotionEngine" if owner == "remotion" else "WebEngine"
    monkeypatch.setattr(engines, factory, lambda path, settings: get(owner, path, settings))
    monkeypatch.setattr(deps, "state_lock", lambda: SimpleNamespace(record=lambda *args, **kwargs: records.append(kwargs)))
    act = next(a for a in acts_from_shots(deps.load_shots(film)) if a.index == 3)
    target = Target("patch", act, 12, 23, "unused.mov", scene=f"scenes/shots/R01/{replacement}")
    result = runner._refresh_trace(film, target, isolated, SimpleNamespace(chunks=[]))
    assert result["written"] and result["base_engine"] == owner
    assert result[replacement + "_shots"] == ["R01", "R02"]
    assert result["base_file"].endswith("act3.base.json") and result["composition_file"].endswith("act3.composition.json")
    assert len(calls) == len(records) == 1 and active.read_bytes() == b"[]"
    assert not (film / "timing/motion/failures").exists()


@pytest.mark.parametrize("name", ["reverse", "forward"])
def test_missing_owner_trace_preserves_prior_bytes_then_invalidates_coverage(neutral, monkeypatch, name):
    owner = direction(neutral, name)
    film = neutral["film"]
    replacement = "remotion" if owner == "web" else "web"
    active = put(film, "timing/motion/act3.json", [{"id": "exact old evidence", "ease": "move"}])
    before = active.read_bytes()
    calls = []
    isolated = SimpleNamespace(scene=film / f"scenes/shots/R01/{replacement}",
                               film=neutral["settings"], trace=lambda act: None)
    def get(engine, path, settings):
        calls.append(engine)
        return SimpleNamespace(trace=lambda act: None)
    factory = "RemotionEngine" if owner == "remotion" else "WebEngine"
    monkeypatch.setattr(engines, factory, lambda path, settings: get(owner, path, settings))
    act = next(a for a in acts_from_shots(neutral["shots"]) if a.index == 3)
    result = runner._refresh_trace(film, Target("patch", act, 12, 23, "unused.mov"),
                                  isolated, SimpleNamespace(chunks=[]))
    assert not result["written"] and calls == [owner] and not active.exists()
    evidence = film / result["failure_evidence"]
    assert (evidence / "previous.json").read_bytes() == before
    assert json.loads((evidence / "failure.json").read_text())["previous_sha256"] == hashlib.sha256(before).hexdigest()


@pytest.mark.parametrize("name", ["reverse", "forward"])
@pytest.mark.parametrize("explicit", [False, True])
def test_public_render_wrapper_preserves_owning_default_and_explicit_matching_engine(neutral, monkeypatch, name, explicit):
    owner = direction(neutral, name)
    forwarded = []
    def fake_run(path, **options):
        forwarded.append(dict(options))
        _, plan = runner.prepare(path, act=options["act"], frames=options["frames"],
                                 engine=options["engine"], samples=options["samples"], out=options["out"],
                                 draft=options["draft"])
        return {"engine": plan.engine, "frames": plan.targets[0].film_frames, "samples": plan.samples}
    monkeypatch.setattr(runner, "run", fake_run)
    options = {"engine": owner} if explicit else {}
    result = render.run(neutral["film"], act=3, samples=7, draft=True, **options)
    assert result == {"engine": owner, "frames": [90, 150], "samples": 7}
    assert len(forwarded) == 1 and forwarded[0]["engine"] == (owner if explicit else None)
