"""Offline regressions for one full-film render with different mixed code-engine owners.

The renderer, joins, splices, registry, workspaces and queue argument builder are real product functions.
Engines and media use the existing deterministic render fakes; no browser, decoder or child is permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from slate import jsonio, procs
from slate.errors import MissingPrerequisite, Refused
from slate.render import cli as render_cli
from slate.render import deps, engines, media, plan as plans, registry, runner, stills
from render_fakes import frame_pixels


def owner_frame(act, frame, variant=0):
    """Keep neighboring frame identity distinct while adding a small neutral owner marker."""
    return frame_pixels(act, frame) + variant * 3


def put(folder, relative, value):
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def owners(fakes, film, monkeypatch):
    settings = jsonio.read(film / "film.json")
    settings.update(fps_num=30000, fps_den=1001)
    put(film, "film.json", settings)
    rows = [
        {"id": "B01", "act": 1, "frame_in": 0, "frame_out": 4},
        {"id": "W01", "act": 1, "engine": "web", "frame_in": 4, "frame_out": 8},
        {"id": "B02", "act": 1, "frame_in": 8, "frame_out": 12},
        {"id": "B03", "act": 2, "frame_in": 12, "frame_out": 24},
        {"id": "R01", "act": 2, "engine": "remotion", "frame_in": 24, "frame_out": 30},
        {"id": "B04", "act": 2, "frame_in": 30, "frame_out": 42},
        {"id": "R02", "act": 2, "engine": "remotion", "frame_in": 42, "frame_out": 48},
        {"id": "B05", "act": 2, "frame_in": 48, "frame_out": 54},
    ]
    shots = {"shots": rows, "total_frames": 54, "fps_num": 30000, "fps_den": 1001,
             "acts": [{"index": 1, "shots": ["B01", "W01", "B02"]},
                      {"index": 2, "shots": ["B03", "R01", "B04", "R02", "B05"]}]}
    put(film, "timing/shots.json", shots)
    (film / "scenes/act2/index.tsx").unlink()
    put(film, "scenes/act2/web/index.html", "<!doctype html><title>Neutral Web base</title>")
    put(film, "scenes/shots/W01/web/index.html", "<!doctype html><title>Neutral Web replacement</title>")
    for sid in ("R01", "R02"):
        put(film, f"scenes/shots/{sid}/remotion/index.tsx", "export default function Shot() { return null; }")
    config = {"chunk_frames": 4, "motion_blur_samples": 16, "draft_samples": 4,
              "foreground_max_subframes": 100000}
    monkeypatch.setattr(plans, "render_settings", lambda: config)
    monkeypatch.setattr(runner, "render_settings", lambda: config)
    monkeypatch.setattr(media, "require_toolchain", lambda: None)
    calls, made, capabilities = [], [], []
    failures = {"open": None, "installed": None}

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected real process, browser or media boundary")

    monkeypatch.setattr(procs, "run", forbidden)

    def capability(command, args, *, timeout=None):
        assert command == "version" and args == {}
        capabilities.append(command)
        return {"capabilities": {"reverse_web_remotion": {"schema": 1, "enabled": True}}}

    monkeypatch.setattr(deps, "bridge_call", capability)

    class RecordingEngine:
        codec = "lossless"
        whole_acts_only = False

        def __init__(self, name, folder, model, scene):
            self.name, self.film_dir, self.film, self.scene = name, folder, model, scene
            self.bundle_hash = ""
            self.tag = None if scene is None else (scene.parent.name if scene.name in ("web", "remotion") else scene.name)
            made.append(self)

        def sources(self):
            return hashlib.sha256(f"{self.name}:{self.tag}".encode()).hexdigest()

        def rules(self):
            calls.append(("rules", self.name, self.tag))

        def check_installed(self):
            calls.append(("installed", self.name, self.tag))
            if self.tag is None and failures["installed"] == self.name:
                raise MissingPrerequisite("Neutral missing owner engine.")

        def preflight(self):
            self.rules()
            self.check_installed()

        def open(self):
            calls.append(("open", self.name, self.tag))
            if self.tag is None and failures["open"] == self.name:
                raise Refused("Neutral owner open failure.")
            self.bundle_hash = self.sources()

        def close(self):
            calls.append(("close", self.name, self.tag))

        def render(self, act, first, last, out, samples):
            calls.append(("render", self.name, self.tag, act, first, last, samples))
            variant = {"W01": 2, "R01": 3, "R02": 4}.get(self.tag, 1 if self.name == "web" else 0)
            fakes.media.save(Path(out), np.stack([owner_frame(act, frame, variant)
                                                 for frame in range(first, last + 1)]))
            return {"file": str(out), "bundle_hash": self.bundle_hash}

        def trace(self, act):
            assert self.tag is None  # Replacement render/splice evidence refreshes through the base once.
            calls.append(("trace", self.name, act))
            file = f"timing/motion/act{act}.json"
            put(film, file, [])
            return {"written": True, "file": file, "entries": 0, "base_engine": self.name,
                    "sources": ["timing/shots.json"],
                    "source_hashes": {"timing/shots.json": hashlib.sha256(
                        (film / "timing/shots.json").read_bytes()).hexdigest()}}

    def get(name, folder, model, *, go=False, scene=None):
        return RecordingEngine(name, folder, model, scene)

    monkeypatch.setattr(engines, "get", get)
    return {"film": film, "settings": settings, "shots": shots, "fakes": fakes, "calls": calls,
            "made": made, "failures": failures, "capabilities": capabilities}


def parser():
    value = argparse.ArgumentParser()
    render_cli.register(value.add_subparsers())
    return value


def queued_args(data, plan, *, frames=None):
    return runner.job_argv(data["film"], plan, act=None, frames=frames, out=None, go=False,
                           fresh=False, resume=False)


def test_default_full_film_plans_both_mixed_owners_without_a_child(owners):
    _, plan = runner.prepare(owners["film"], samples=3)
    assert [(t.act.index, plan.engine_for(t), t.first, t.last, t.film_frames) for t in plan.targets] == [
        (1, "remotion", 0, 11, [0, 12]), (2, "web", 0, 41, [12, 54])]
    assert plan.engine == "remotion" and plan.samples == 3
    assert plan.to_dict()["engines"] == {"1": "remotion", "2": "web"}
    assert plan.subframes == 54 * 3
    assert owners["calls"] == owners["capabilities"] == []
    assert not (owners["film"] / "renders").exists()


@pytest.mark.parametrize("act,owner", [(1, "remotion"), (2, "web")])
def test_single_owner_default_retains_scalar_plan(owners, act, owner):
    _, plan = runner.prepare(owners["film"], act=act)
    assert plan.engine == owner and plan.targets[0].engine is None
    assert "engines" not in plan.to_dict()
    words = queued_args(owners, plan)
    assert words[words.index("--engine") + 1] == owner


@pytest.mark.parametrize("override", ["remotion", "web"])
def test_explicit_global_conflict_still_refuses_before_engine_access(owners, override):
    with pytest.raises(Refused, match="built .* base"):
        runner.prepare(owners["film"], engine=override)
    assert owners["calls"] == owners["capabilities"] == []


@pytest.mark.parametrize("in_queue", [False, True])
def test_full_film_dispatch_splice_registry_trace_and_resources(owners, monkeypatch, in_queue):
    if in_queue:
        monkeypatch.setenv("SLATE_JOB_ID", "neutral-owned-job")
    _, plan = runner.prepare(owners["film"], samples=3)
    result = runner.execute(owners["film"], owners["settings"], plan, in_queue=in_queue)
    assert result["engines"] == {"1": "remotion", "2": "web"}
    assert [(r["act"], r["engine"], r["samples"], r["film_frames"]) for r in result["renders"]] == [
        (1, "remotion", 3, [0, 12]), (2, "web", 3, [12, 54])]
    assert [(r["act"], r["engine"], r["frames"]) for r in registry.entries(owners["film"])] == [
        (1, "remotion", [0, 12]), (2, "web", [12, 54])]
    assert [[s["shot"] for s in r["shot_engines"]] for r in result["renders"]] == [["W01"], ["R01", "R02"]]
    for act, count in [(1, 12), (2, 42)]:
        actual = owners["fakes"].media.load(owners["film"] / f"renders/act{act}.mov")
        assert len(actual) == count
        for frame in range(count):
            variant = (2 if 4 <= frame < 8 else 0) if act == 1 else (
                3 if 12 <= frame < 18 else 4 if 30 <= frame < 36 else 1)
            assert np.abs(actual[frame].astype(int) - owner_frame(act, frame, variant).astype(int)).max() <= 1
    assert [c for c in owners["calls"] if c[0] == "trace"] == [
        ("trace", "remotion", 1), ("trace", "web", 2)]
    base_opens = [c for c in owners["calls"] if c[0] == "open" and c[2] is None]
    base_closes = [c for c in owners["calls"] if c[0] == "close" and c[2] is None]
    assert base_opens == [("open", "remotion", None), ("open", "web", None)]
    assert sorted(c[1:] for c in base_closes) == sorted(c[1:] for c in base_opens)
    assert len(owners["fakes"].jobs.held) == len(owners["fakes"].jobs.reserved) == (0 if in_queue else 1)


def test_direct_splice_uses_the_target_owner_not_the_scalar_fallback(owners):
    _, plan = runner.prepare(owners["film"], samples=3)
    target = plan.targets[1]
    joined = owners["film"] / "renders/act2.new.mov"
    owners["fakes"].media.save(joined, np.stack([owner_frame(2, f, 1) for f in range(42)]))
    records = runner._splice_shots(owners["film"], owners["settings"], plan, target, joined,
                                  owners["fakes"].media, go=False, fresh=False)
    assert [r["shot"] for r in records] == ["R01", "R02"]
    assert all(r["engine"] == "remotion" for r in records)
    assert owners["fakes"].media.count_frames(joined) == 42


def test_direct_execute_rejects_a_tampered_target_owner_before_engine_access(owners):
    _, plan = runner.prepare(owners["film"])
    plan.targets[1].engine = "remotion"
    with pytest.raises(Refused, match="built web base"):
        runner.execute(owners["film"], owners["settings"], plan)
    assert owners["calls"] == []


def test_second_owner_open_failure_closes_both_started_engines(owners):
    owners["failures"]["open"] = "web"
    _, plan = runner.prepare(owners["film"])
    with pytest.raises(Refused, match="Neutral owner open failure"):
        runner.execute(owners["film"], owners["settings"], plan)
    assert [c for c in owners["calls"] if c[0] == "open"] == [
        ("open", "remotion", None), ("open", "web", None)]
    assert [c for c in owners["calls"] if c[0] == "close"] == [
        ("close", "web", None), ("close", "remotion", None)]
    assert not [c for c in owners["calls"] if c[0] == "render"]


def test_queue_arguments_preserve_default_ownership_through_the_real_parser(owners):
    _, plan = runner.prepare(owners["film"], samples=3)
    words = queued_args(owners, plan)
    assert "--engine" not in words
    args = parser().parse_args(words)
    assert args.engine is None
    _, child = runner.prepare(owners["film"], act=args.act, engine=args.engine, samples=args.samples)
    assert child.to_dict()["engines"] == plan.to_dict()["engines"]
    assert [t.film_frames for t in child.targets] == [[0, 12], [12, 54]]


def test_queue_preflight_checks_every_owner_without_opening_any(owners):
    _, plan = runner.prepare(owners["film"])
    owners["failures"]["installed"] = "web"
    with pytest.raises(MissingPrerequisite, match="Neutral missing owner"):
        render_cli._check_before_queueing(owners["film"], owners["settings"], plan, go=False)
    assert owners["calls"] == [("rules", "remotion", None), ("installed", "remotion", None),
                               ("rules", "web", None), ("installed", "web", None)]


def test_cross_act_patches_inherit_each_registered_engine_and_sample_count(owners, monkeypatch):
    entries = {}
    for act, owner, count, span, samples in [(1, "remotion", 12, [0, 12], 4), (2, "web", 42, [12, 54], 7)]:
        path = owners["film"] / f"renders/act{act}.mov"
        owners["fakes"].media.save(path, np.stack([owner_frame(act, f) for f in range(count)]))
        entries[act] = {"engine": owner, "samples": samples, "file": path.name, "frames": span}
        entries[act]["file"] = f"renders/{path.name}"
    monkeypatch.setattr(runner, "_registered", lambda folder: entries)
    monkeypatch.setattr(registry, "find", lambda folder, act: entries[act])
    _, plan = runner.prepare(owners["film"], frames=(8, 13))
    assert [(plan.engine_for(t), plan.samples_for(t), t.first, t.last) for t in plan.targets] == [
        ("remotion", 4, 8, 11), ("web", 7, 0, 1)]
    assert plan.subframes == 30
    assert runner._options(plan, plan.targets[1])["engine"] == "web"
    assert runner._options(plan, plan.targets[1])["samples"] == 7
    assert runner.check_patch(owners["film"], plan, plan.targets[1]) == entries[2]
    words = queued_args(owners, plan, frames=(8, 13))
    assert "--engine" not in words and "--samples" not in words


def test_explicit_pure_preview_keeps_the_callers_engine(owners):
    document = jsonio.read(owners["film"] / "timing/shots.json")
    for row in document["shots"]:
        row.pop("engine", None)
    put(owners["film"], "timing/shots.json", document)
    for candidate in (owners["film"] / "scenes/shots").rglob("index.*"):
        candidate.unlink()
    _, plan = runner.prepare(owners["film"], engine="web")
    assert plan.engine == "web" and all(t.engine is None for t in plan.targets)
    assert owners["calls"] == []


@pytest.mark.parametrize("choice", [None, "remotion", "web"])
def test_stills_cli_preserves_omission_and_explicit_preview_choice(owners, monkeypatch, choice):
    forwarded = []
    monkeypatch.setattr(render_cli.cli, "film", lambda args: owners["film"])
    monkeypatch.setattr(stills, "stills", lambda folder, **kwargs: forwarded.append(kwargs) or {"stills": []})
    monkeypatch.setattr(render_cli.log, "result", lambda *args, **kwargs: None)
    words = ["render", "stills", "--frames", "0,12"]
    if choice is not None:
        words += ["--engine", choice]
    args = parser().parse_args(words)
    assert render_cli._cmd_stills(args) == 0
    assert len(forwarded) == 1 and forwarded[0]["engine"] == choice
    assert forwarded[0]["frames"] == [0, 12]
