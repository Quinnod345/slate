"""Review stills must draw the declared shot scene at the same act-local frame.

The recording engines and bridge produce paths and call records only. These tests
do not start a renderer, read media, launch a browser, or write a motion trace.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from slate import jsonio
from slate.errors import Refused, SlateError
from slate.render import engines, stills


def _declare(film: Path, shot: str, engine: str = "web", *, scene: bool = True,
             in_timing: bool = True) -> Path:
    """A real per-shot declaration and a tiny page, inside the neutral film fixture."""
    shots = jsonio.read(film / "timing" / "shots.json")
    if in_timing:
        for row in shots["shots"]:
            if row["id"] == shot:
                row["engine"] = engine
        jsonio.write_atomic(film / "timing" / "shots.json", shots)
    else:
        jsonio.write_atomic(film / "script" / "script.json",
                            {"acts": [{"shots": [{"id": shot, "engine": engine}]}]})
    folder = film / "scenes" / "shots" / shot / engine
    if scene:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text("<!doctype html><title>shot</title>\n", encoding="utf-8")
    return folder


@pytest.fixture
def recorded_engines(monkeypatch):
    state = SimpleNamespace(instances=[], calls=[], fail=None)

    class RecordingEngine:
        def __init__(self, name, scene):
            self.name = name
            self.scene = Path(scene) if scene is not None else None
            self.opened = False
            self.closed = False
            state.instances.append(self)

        def preflight(self):
            state.calls.append(("preflight", self))

        def open(self):
            self.opened = True
            state.calls.append(("open", self))
            if state.fail == (self.name, self.scene, "open"):
                raise SlateError("Fixture open failed.")

        def stills(self, act, frames, out_dir, *, scale, samples):
            state.calls.append(("stills", self, act, list(frames), Path(out_dir), scale, samples))
            if state.fail == (self.name, self.scene, "stills"):
                raise SlateError("Fixture stills failed.")
            return {"stills": [{"frame": frame,
                                "file": str(Path(out_dir) / f"Act{act}_f{frame:05d}.png")}
                               for frame in frames]}

        def close(self):
            self.closed = True
            state.calls.append(("close", self))

    def get(name, film_dir, film, *, go=False, scene=None):
        return RecordingEngine(name, scene)

    monkeypatch.setattr(engines, "get", get)
    return state


def _draw_calls(state):
    return [row for row in state.calls if row[0] == "stills"]


def _drawn(state, name, scene=None):
    expected = Path(scene) if scene is not None else None
    return [(row[2], frame) for row in _draw_calls(state)
            if row[1].name == name and row[1].scene == expected for frame in row[3]]


@pytest.mark.parametrize("in_timing", [True, False])
def test_stills_route_declared_web_frames_and_preserve_sorted_deduplicated_results(
        film, recorded_engines, in_timing):
    scene = _declare(film, "A1S2", in_timing=in_timing)
    result = stills.stills(film, act=1, frames=[20, 3, 20, 14, 15, 29],
                          scale=0.25, samples=3, out_dir="renders/review")

    assert _drawn(recorded_engines, "remotion") == [(1, 3), (1, 14)]
    assert _drawn(recorded_engines, "web", scene) == [(1, 15), (1, 20), (1, 29)]
    assert [(row["act"], row["frame"], row["film_frame"]) for row in result["stills"]] == [
        (1, 3, 3), (1, 14, 14), (1, 15, 15), (1, 20, 20), (1, 29, 29)]
    out = film / "renders" / "review"
    assert result["out_dir"] == str(out)
    assert [Path(row["file"]).name for row in result["stills"]] == [
        "Act1_f00003.png", "Act1_f00014.png", "Act1_f00015.png", "Act1_f00020.png", "Act1_f00029.png"]
    assert all(row[4:] == (out, 0.25, 3) for row in _draw_calls(recorded_engines))
    assert len(recorded_engines.instances) == 2
    assert all(engine.opened and engine.closed for engine in recorded_engines.instances)


def test_film_frames_use_inclusive_shot_edges_and_nonzero_act_origins(film, recorded_engines):
    first_scene = _declare(film, "A1S2")
    second_scene = _declare(film, "A2S2")
    result = stills.stills(film, frames=[53, 29, 14, 42, 30, 15, 41, 42])

    assert _drawn(recorded_engines, "remotion") == [(1, 14), (2, 0), (2, 11)]
    assert _drawn(recorded_engines, "web", first_scene) == [(1, 15), (1, 29)]
    assert _drawn(recorded_engines, "web", second_scene) == [(2, 12), (2, 23)]
    assert [(row["act"], row["frame"], row["film_frame"]) for row in result["stills"]] == [
        (1, 14, 14), (1, 15, 15), (1, 29, 29), (2, 0, 30),
        (2, 11, 41), (2, 12, 42), (2, 23, 53)]
    assert sum(engine.name == "remotion" for engine in recorded_engines.instances) == 1
    assert all(engine.closed for engine in recorded_engines.instances)


def test_only_web_shots_use_their_distinct_scenes_without_an_unused_base_bundle(film, recorded_engines):
    first_scene = _declare(film, "A1S1")
    second_scene = _declare(film, "A1S2")
    result = stills.stills(film, act=1, frames=[20, 0])

    assert _drawn(recorded_engines, "web", first_scene) == [(1, 0)]
    assert _drawn(recorded_engines, "web", second_scene) == [(1, 20)]
    assert len(recorded_engines.instances) == 2
    assert all(engine.name == "web" and engine.closed for engine in recorded_engines.instances)
    assert [(row["act"], row["frame"]) for row in result["stills"]] == [(1, 0), (1, 20)]


@pytest.mark.parametrize("mixed", [False, True])
def test_explicit_web_base_choice_keeps_its_contract_and_uses_a_declared_shots_own_page(
        film, recorded_engines, mixed):
    scene = _declare(film, "A1S2") if mixed else None
    result = stills.stills(film, act=1, frames=[3, 20], engine="web")

    assert _drawn(recorded_engines, "web") == ([(1, 3)] if mixed else [(1, 3), (1, 20)])
    if mixed:
        assert _drawn(recorded_engines, "web", scene) == [(1, 20)]
    assert all(engine.name == "web" for engine in recorded_engines.instances)
    assert [(row["act"], row["frame"]) for row in result["stills"]] == [(1, 3), (1, 20)]


@pytest.mark.parametrize("engine, message", [
    ("web", "shot A1S2 says web"),
    ("ae", "After Effects builds and renders whole acts"),
])
def test_existing_missing_scene_and_unsupported_engine_refusals_precede_engine_work(
        film, recorded_engines, engine, message):
    _declare(film, "A1S2", engine, scene=False)

    with pytest.raises(Refused, match=message):
        stills.stills(film, act=1, frames=[20])

    assert recorded_engines.instances == []
    assert recorded_engines.calls == []


@pytest.mark.parametrize("failure", ["base_open", "shot_open", "shot_stills"])
def test_every_started_engine_closes_when_open_or_stills_fails(film, recorded_engines, failure):
    scene = _declare(film, "A1S2")
    recorded_engines.fail = {
        "base_open": ("remotion", None, "open"),
        "shot_open": ("web", scene, "open"),
        "shot_stills": ("web", scene, "stills"),
    }[failure]

    with pytest.raises(SlateError, match="Fixture .* failed"):
        stills.stills(film, act=1, frames=[3, 20])

    started = [engine for engine in recorded_engines.instances if engine.opened]
    assert started and all(engine.closed for engine in started)
    assert all(sum(row[0] == "close" and row[1] is engine for row in recorded_engines.calls) == 1
               for engine in started)


def test_scene_specific_web_stills_forward_scene_and_disable_trace_without_a_video_codec(
        tmp_path, monkeypatch):
    calls, scaled = [], []

    def bridge(command, args, *, timeout=None):
        calls.append((command, dict(args)))
        return {"gates": []}

    monkeypatch.setattr(engines.deps, "bridge_call", bridge)
    monkeypatch.setattr(engines, "_scale_png", lambda file, scale: scaled.append((file, scale)))
    film, scene, out = tmp_path / "film", tmp_path / "film" / "scenes" / "shots" / "S2" / "web", tmp_path / "stills"
    result = engines.WebEngine(film, {}, scene=scene).stills(2, [7, 8], out, scale=0.5, samples=3)

    assert [command for command, _args in calls] == ["web-render", "web-render"]
    for (_command, args), frame in zip(calls, [7, 8]):
        assert args == {"film": str(film), "act": 2, "from": frame, "to": frame, "sub": 3,
                        "out": str(out / f"Act2_f{frame:05d}.png"), "scene": str(scene), "trace": False}
    assert scaled == [(out / "Act2_f00007.png", 0.5), (out / "Act2_f00008.png", 0.5)]
    assert [row["frame"] for row in result["stills"]] == [7, 8]


def test_plain_web_stills_keep_the_existing_png_arguments(tmp_path, monkeypatch):
    calls = []

    def bridge(command, args, *, timeout=None):
        calls.append((command, dict(args)))
        return {"gates": []}

    monkeypatch.setattr(engines.deps, "bridge_call", bridge)
    monkeypatch.setattr(engines, "_scale_png", lambda *_args: pytest.fail("Scale 1 must not rescale the PNG."))
    film, out = tmp_path / "film", tmp_path / "stills"
    result = engines.WebEngine(film, {}).stills(2, [7], out, scale=1, samples=1)

    assert calls == [("web-render", {"film": str(film), "act": 2, "from": 7, "to": 7, "sub": 1,
                                    "out": str(out / "Act2_f00007.png")})]
    assert result["stills"][0]["file"] == str(out / "Act2_f00007.png")
