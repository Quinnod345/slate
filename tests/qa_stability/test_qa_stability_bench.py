"""The engine kit's stability bench: its spec, the scratch film it renders in, the morph check's geometry taken from
the bench itself, and the one command that renders it through the queue and checks it. The renders themselves are
the conformance run on this Mac (`slate qa stability conformance --render`), recorded under Slate's data folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.cli import run_cli
from slate.qa import stability as S

SPEC = S.bench_spec_path()


def _spec() -> dict:
    return jsonio.read(SPEC)


def test_the_kit_has_its_bench_and_spec():
    assert SPEC.is_file() and SPEC.parent == Path(paths.ENGINE_SRC) / "remotion" / "kit" / "bench"
    assert (SPEC.parent / "stability.tsx").is_file()
    spec = _spec()
    assert spec["film"]["samples"] == 16 and spec["film"]["frames"] == 60


def test_every_step_reads_a_variant_the_bench_renders_and_every_variant_is_checked():
    spec = _spec()
    variants = set(spec["variants"])
    used = set()
    for st in spec["steps"]:
        for key in ("render", "reference", "a", "b"):
            if st.get(key):
                assert st[key] in variants, (st["step"], st[key])
                used.add(st[key])
    assert used == variants
    scenes = {"drift", "zoom", "grow", "rest", "land", "arrive", "free", "morph"}
    assert {v["scene"] for v in spec["variants"].values()} == scenes
    assert {v["raster"] for v in spec["variants"].values()} == {"hint", "tilt"}
    # the controls the scan must still fail are the pre-fix drawing (hint mode, no hints): base and free-control
    fails = {st["render"] for st in spec["steps"] if st["step"] == "scan" and st["expect"] == "fail"}
    assert fails == {"base", "free-control"} and all(spec["variants"][v]["raster"] == "hint" for v in fails)


def test_the_bench_film_is_one_act_of_the_specs_shape_whose_scene_is_the_kits_bench(tmp_path):
    spec = _spec()
    film = S.bench_film(tmp_path / "film", spec)
    fj = jsonio.read(film / "film.json")
    shots = jsonio.read(film / "timing" / "shots.json")
    assert (fj["width"], fj["height"], fj["fps_num"], fj["fps_den"]) == (1920, 1080, 30000, 1001)
    assert fj["brand"] == spec["film"]["brand"] and fj["generated"].startswith("do not edit by hand")
    assert shots["total_frames"] == 60 and [s["id"] for s in shots["shots"]] == ["B01"]
    assert jsonio.validate(shots, "shots") == []
    scene = (film / "scenes" / "act1" / "index.tsx").read_text()
    assert "export {StabilityBench as default} from '@slate/kit/bench/stability';" in scene
    before = (film / "scenes" / "act1" / "index.tsx").stat().st_mtime_ns
    S.bench_film(tmp_path / "film", spec)  # unchanged: the scene file isn't rewritten (its bundle stays current)
    assert (film / "scenes" / "act1" / "index.tsx").stat().st_mtime_ns == before


def test_the_morph_cards_take_their_true_geometry_from_the_bench():
    spec = _spec()
    step = next(st for st in spec["steps"] if st["step"] == "morph")
    cards = S.morph_cards(spec, step)
    assert [c["name"] for c in cards] == ["plain", "first morphPose", "morphPose"]
    assert [c["role"] for c in cards] == ["control", "note", "subject"]
    m = spec["scene"]["morph"]
    plain = cards[0]
    left, top, right, bottom = plain["rects"][0]
    assert right - left == pytest.approx(m["size"][0]) and bottom - top == pytest.approx(m["size"][1])
    assert (left + right) / 2 == pytest.approx(960 + m["x"][0]) and (top + bottom) / 2 == pytest.approx(540 + m["y"])
    last = plain["rects"][59]
    assert last[2] - last[0] == pytest.approx(m["size"][0] - m["shrink"][0] * 59 / 60)
    assert len(plain["rects"]) == 60 and plain["content"] == [int(left) + 30, int(top) + 20, int(left) + 300,
                                                              int(top) + 170]


def test_rendering_the_bench_needs_an_engine_that_has_it(tmp_path, monkeypatch, slate_home):
    from slate.engine import bridge
    from slate.errors import MissingPrerequisite

    old = tmp_path / "engine"
    (old / "remotion" / "kit").mkdir(parents=True)
    monkeypatch.setattr(bridge, "engine_dir", lambda: old)
    with pytest.raises(MissingPrerequisite) as exc:
        S.render_bench(tmp_path / "out", _spec())
    assert "has no stability bench" in exc.value.message and "slate engine install" in exc.value.hint


def test_render_then_check_renders_every_variant_from_one_bundle(tmp_path, monkeypatch, slate_home):
    """`--render` renders each variant through the engine (one bundle, released after) and checks the files it made."""
    from slate.engine import bridge

    calls = []

    def call(command, args=None, *, timeout=None):
        calls.append((command, dict(args or {})))
        if command == "bundle" and not (args or {}).get("release"):
            return {"ok": True, "serve_url": str(tmp_path / "bundle")}
        if command == "render":
            Path(args["out"]).write_bytes(b"not really a movie")
        return {"ok": True}

    monkeypatch.setattr(bridge, "call", call)
    monkeypatch.setattr(bridge, "engine_dir", lambda: Path(paths.ENGINE_SRC))
    seen = {}
    monkeypatch.setattr(S, "conformance", lambda spec, renders: seen.update(renders) or S.GateResult.pass_(
        "stability.conformance", "checked", engine={"name": "fake", "version": "0"}))
    r = S.run_conformance(render=True)
    assert r.status == "PASS"
    renders = [a for c, a in calls if c == "render"]
    spec = _spec()
    assert [Path(a["out"]).stem for a in renders] == list(spec["variants"])
    assert all(a["sub"] == 16 and a["serve_url"] == str(tmp_path / "bundle") and a["act"] == 1 for a in renders)
    assert renders[0]["props"] == {"scene": "drift", "raster": "hint"}
    assert calls[0][0] == "bundle" and calls[-1] == ("bundle", {"film": calls[0][1]["film"],
                                                                "serve_url": str(tmp_path / "bundle"), "release": True})
    assert set(seen) == set(spec["variants"])
    record = jsonio.read(paths.data() / S.CONFORMANCE_RECORD)
    assert record["rendered"]["samples"] == 16 and record["kept"] is False
    assert not Path(next(iter(record["renders"].values()))).exists()  # removed after the check unless --keep


def test_detach_queues_the_render_and_check_on_the_render_slot(slate_home, monkeypatch):
    from slate.jobs import client

    seen = {}

    def submit(kind, argv, **kw):
        seen.update(kind=kind, argv=argv, **kw)
        return "j-20261001-000000-abcd"

    monkeypatch.setattr(client, "submit", submit)
    code, out, _ = run_cli(["qa", "stability", "conformance", "--render", "--detach", "--json"])
    assert code == 0 and json.loads(out)["job"] == "j-20261001-000000-abcd"
    assert seen["resources"] == ("render",) and seen["argv"][-3:] == ["stability", "conformance", "--render"]
    code, out, _ = run_cli(["qa", "stability", "conformance", "--detach", "--json"])
    assert code == 2 and "--render" in out
