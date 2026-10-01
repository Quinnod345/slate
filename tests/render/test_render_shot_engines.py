"""Per-shot engines (P074): a shot may carry its own engine, by default its act's. A web shot inside a Remotion act is
rendered over exactly its frames in the open web renderer, from its own scene, and spliced into the act with proof
before the act is installed; patches stay on one side of a seam between engines; what isn't built is refused by name.
Against the render fakes."""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from slate import jsonio
from slate.errors import Refused, UsageError
from slate.render import registry, runner, shot_engines
from render_fakes import frame_pixels

WEB_VARIANT = 7


def _declare(film, shot: str, engine: str, *, scene: bool = True, in_timing: bool = True) -> None:
    shots = jsonio.read(film / "timing" / "shots.json")
    if in_timing:
        for s in shots["shots"]:
            if s["id"] == shot:
                s["engine"] = engine
        jsonio.write_atomic(film / "timing" / "shots.json", shots)
    else:
        acts = {}
        for s in shots["shots"]:
            acts.setdefault(s["act"], []).append({"id": s["id"], **({"engine": engine} if s["id"] == shot else {})})
        jsonio.write_atomic(film / "script" / "script.json",
                            {"acts": [{"shots": rows} for _a, rows in sorted(acts.items())]})
    if scene:
        d = film / "scenes" / "shots" / shot / engine
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text("<!doctype html><title>shot</title>\n", encoding="utf-8")


@pytest.fixture
def web_scene_frames(fakes, monkeypatch):
    """The fake web renderer draws a shot's own scene in a variant of its own, so the splice is visible."""
    real = fakes.bridge.call

    def call(command, args, *, timeout=None):
        if command == "web-render" and args.get("scene"):
            act, a, b = int(args["act"]), int(args["from"]), int(args["to"])
            fakes.bridge.calls.append((command, dict(args)))
            frames = [frame_pixels(act, f, WEB_VARIANT) for f in range(a, b + 1)]
            fakes.media.save(__import__("pathlib").Path(args["out"]), np.stack(frames))
            return {"file": args["out"], "frame_count": len(frames), "frames": [a, b], "samples": args.get("sub"),
                    "bundle_hash": "web-shot", "gates": []}
        return real(command, args, timeout=timeout)

    monkeypatch.setattr(sys.modules["slate.engine.bridge"], "call", call)
    return fakes


def test_a_shot_says_its_engine_in_timing_or_in_the_script(film):
    _declare(film, "A1S2", "web")
    shots = jsonio.read(film / "timing" / "shots.json")
    assert shot_engines.declared(film, shots) == {"A1S2": "web"}
    assert shot_engines.problems(film, shots) == []
    # until timing carries it, the script's declaration counts (where a person writes it)
    shots2 = json.loads(json.dumps(shots))
    for s in shots2["shots"]:
        s.pop("engine", None)
    _declare(film, "A1S2", "web", scene=False, in_timing=False)
    assert shot_engines.declared(film, shots2) == {"A1S2": "web"}


def test_declarations_and_scenes_must_agree_and_the_unbuilt_kinds_are_refused(film):
    shots = jsonio.read(film / "timing" / "shots.json")
    _declare(film, "A1S1", "web", scene=False)
    shots = jsonio.read(film / "timing" / "shots.json")
    assert any("says web, but there's no scenes/shots/A1S1/web/index.html" in p
               for p in shot_engines.problems(film, shots))
    _declare(film, "A2S1", "remotion", scene=False)  # its act is Remotion too: nothing to do
    d = film / "scenes" / "shots" / "A2S2" / "web"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html>\n")
    shots = jsonio.read(film / "timing" / "shots.json")
    assert any("scenes/shots/A2S2/web/ is a web scene, but shot A2S2 doesn't say engine: web" in p
               for p in shot_engines.problems(film, shots))
    _declare(film, "A2S2", "ae", scene=False)
    shots = jsonio.read(film / "timing" / "shots.json")
    assert any("After Effects builds and renders whole acts" in p for p in shot_engines.problems(film, shots))
    _declare(film, "A2S2", "warp", scene=False)
    shots = jsonio.read(film / "timing" / "shots.json")
    assert any("names engine 'warp'" in p for p in shot_engines.problems(film, shots))


def test_an_act_render_splices_its_web_shot_in_with_proof_and_records_it(web_scene_frames, film):
    fakes = web_scene_frames
    _declare(film, "A1S2", "web")
    out = runner.run(film, act=1)
    r = out["renders"][0]
    assert r["registered"]
    assert [d["shot"] for d in r["shot_engines"]] == ["A1S2"]
    assert any(g["gate"] == "render.splice" and g["status"] == "PASS" for g in out["gates"])
    # A1S2 is act frames 15-29: drawn by the web renderer from its own scene, at the act's samples, no trace
    web = [a for c, a in fakes.bridge.calls if c == "web-render"]
    assert web and all(a["scene"].endswith("scenes/shots/A1S2/web") and a["trace"] is False for a in web)
    assert {(a["from"], a["to"]) for a in web} == {(15, 24), (25, 29)} or {(a["from"], a["to"]) for a in web} == {(15, 29)}
    assert all(a["sub"] == 16 for a in web)
    rendered = fakes.media.load(film / "renders" / "act1.mov")
    assert np.abs(rendered[3].astype(int) - frame_pixels(1, 3).astype(int)).max() <= 1
    assert np.abs(rendered[20].astype(int) - frame_pixels(1, 20, WEB_VARIANT).astype(int)).max() <= 1
    entry = registry.find(film, 1)
    assert entry["engine"] == "remotion" and entry["shot_engines"][0]["frames"] == [15, 29]
    assert entry["sha256"] == r["sha256"]


def test_a_patch_stays_on_one_side_of_a_seam_between_engines(web_scene_frames, film):
    fakes = web_scene_frames
    _declare(film, "A1S2", "web")
    runner.run(film, act=1)
    with pytest.raises(Refused, match="cross the seam between engines at shot A1S2"):
        runner.run(film, act=1, frames=(10, 20), engine=None)
    # inside the web shot, the patch renders in web from the shot's scene, and splices into the act
    before = len([1 for c, _a in fakes.bridge.calls if c == "web-render"])
    out = runner.run(film, act=1, frames=(18, 22), engine=None)
    r = out["renders"][0]
    assert r["kind"] == "patch" and out["engine"] == "web"
    web = [a for c, a in fakes.bridge.calls if c == "web-render"][before:]
    assert web and all(a["scene"].endswith("scenes/shots/A1S2/web") for a in web)
    with pytest.raises(Refused, match="a patch there renders in web"):
        runner.run(film, act=1, frames=(18, 22), engine="ae")


def test_an_act_with_an_unbuilt_kind_of_shot_is_refused_before_it_renders(fakes, film):
    _declare(film, "A1S2", "ae", scene=False)
    with pytest.raises(Refused, match="After Effects builds and renders whole acts"):
        runner.run(film, act=1)
    assert registry.find(film, 1) is None
