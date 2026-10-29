"""Offline regressions for reverse-engine consumers and captured owning traces.

These tests use canonical Slate modules, public engine calls and an independent fake flat Node producer.
No engine child, browser, media or vendor may run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from slate import procs
from slate.errors import Refused
from slate.render import deps, engines, shot_engines, stills

# Imports must resolve to the same canonical module objects used by the installed consumers.


def put(film, relative, value):
    file = film / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return file


def snapshot(film):
    return {str(file.relative_to(film)): file.read_bytes()
            for file in film.rglob("*") if file.is_file()}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    assert engines is sys.modules["slate.render.engines"]
    assert shot_engines is sys.modules["slate.render.shot_engines"]
    for variable, name in [("SLATE_DATA", "data"), ("SLATE_FILMS", "films"), ("SLATE_BRANDS", "brands")]:
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setenv(variable, str(folder))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "1")

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected child, install, browser or unfaked engine bridge")

    monkeypatch.setattr(procs, "run", forbidden)
    monkeypatch.setattr(deps, "bridge_call", forbidden)


@pytest.fixture
def reverse(tmp_path, monkeypatch):
    film = tmp_path / "neutral-consumers"
    settings = {"id": "neutral-consumers", "width": 640, "height": 360,
                "fps_num": 30000, "fps_den": 1001, "engine": "web", "mode": "supervised"}
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
    put(film, "scenes/act3/web/index.html", "<!doctype html><title>Neutral base</title>\n")
    for sid, extension in [("R01", ".tsx"), ("R02", ".js")]:
        put(film, f"scenes/shots/{sid}/remotion/index{extension}",
            "export default function NeutralShot() { return null; }\n")
    monkeypatch.setattr(deps, "load_film", lambda path: copy.deepcopy(settings))
    # Preserve the real deps.load_shots resolver. Its local JSON read is part of this bounded source contract.
    return {"film": film, "settings": settings, "shots": shots}


@pytest.fixture
def api(monkeypatch):
    state = {"calls": [], "capabilities": {"reverse_web_remotion": {"schema": 1, "enabled": True}},
             "trace": None, "after_trace": None}

    def bridge(command, args, *, timeout=None):
        state["calls"].append((command, copy.deepcopy(args), timeout))
        if command == "version":
            assert args == {}, "A compatibility read must not prepare a film context"
            return {"capabilities": copy.deepcopy(state["capabilities"])}
        if command == "trace":
            if state["after_trace"]:
                state["after_trace"]()
            return copy.deepcopy(state["trace"])
        if command == "bundle":
            return {} if args.get("release") else {"serve_url": "fake-bundle", "bundle_hash": "b" * 64}
        if command == "render":
            return {"file": args["out"], "comp": args.get("comp", f"Act{args.get('act')}"),
                    "frames": [args["from"], args["to"]], "frame_count": args["to"] - args["from"] + 1}
        if command == "stills":
            items = []
            for spec in args["specs"]:
                if isinstance(spec, str):
                    comp, frame = spec.split(":")
                    frame = int(frame)
                    name = f"{comp}_f{frame:05d}.png"
                else:
                    comp, frame, name = spec["comp"], spec["frame"], spec["name"]
                items.append({"comp": comp, "frame": frame, "file": str(Path(args["out_dir"]) / name)})
            return {"stills": items}
        if command == "web-render":
            return {"gates": []}
        pytest.fail(f"Unexpected engine command {command}")

    monkeypatch.setattr(deps, "bridge_call", bridge)
    return state


def node_capture_key(file):
    """Expected producer key from Node's real-parent capture contract; no proposed discovery helper is reused."""
    if file.exists():
        return file.resolve()
    parent = next(item for item in file.parents if item.exists())
    return parent.resolve() / file.relative_to(parent)


def producer(data):
    """Concrete proposed flat producer shape, rooted in the primary CLI's {film, act, ...traceAct} contract."""
    film, shots = data["film"], data["shots"]
    catalogue = {film / "film.json", film / "timing" / "shots.json", film / ".slate" / "refs.json",
                 film / "script" / "script.json", film / "scenes" / "ae" / "act3.json"}
    catalogue.update(film / "scenes" / "act3" / f"index{ext}" for ext in (".tsx", ".ts", ".jsx", ".js"))
    catalogue.add(film / "scenes" / "act3" / "web" / "index.html")
    for sid in ("B03", "R01", "B04", "R02", "B05"):
        catalogue.add(film / "scenes" / "shots" / sid / "web" / "index.html")
        catalogue.update(film / "scenes" / "shots" / sid / "remotion" / f"index{ext}"
                         for ext in (".tsx", ".ts", ".jsx", ".js"))
    complete = {str(node_capture_key(file)): hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
                for file in sorted(catalogue)}
    proofs = []
    for sid, first, end, extension in [("R01", 102, 114, ".tsx"), ("R02", 126, 138, ".js")]:
        folder = film / "scenes" / "shots" / sid / "remotion"
        index = folder / f"index{extension}"
        proofs.append({"shot": sid, "frames": [first, end], "scene": str(folder), "index": str(index),
                       "entries": 0, "source_hashes": {str(node_capture_key(index)): complete[str(node_capture_key(index))]}})
    sidecar = {"act": 3, "frame_base": "film", "base_engine": "web", "act_frames": [90, 150],
               "fps_num": 30000, "fps_den": 1001, "source_hashes": complete, "remotion_shots": proofs}
    put(film, "timing/motion/act3.json", [])
    put(film, "timing/motion/sources/act3.base.json", [])
    put(film, "timing/motion/sources/act3.composition.json", sidecar)
    return {"film": data["settings"]["id"], "act": 3, "written": True, "entries": 0, "frame_base": "film",
            "base_engine": "web", "remotion_shots": ["R01", "R02"], "file": "timing/motion/act3.json",
            "base_file": "timing/motion/sources/act3.base.json",
            "composition_file": "timing/motion/sources/act3.composition.json",
            "sources": sorted(complete), "source_hashes": {key: value for key, value in complete.items()
                                                         if value is not None}}


def save_shots(data):
    put(data["film"], "timing/shots.json", data["shots"])


def edit_sidecar(data, edit):
    file = data["film"] / "timing/motion/sources/act3.composition.json"
    proof = json.loads(file.read_text(encoding="utf-8"))
    edit(proof)
    file.write_text(json.dumps(proof), encoding="utf-8")


@pytest.mark.parametrize("direction", ["reverse", "forward"])
def test_source_descriptors_keep_film_and_whole_act_clocks(reverse, direction):
    data = reverse
    if direction == "forward":
        (data["film"] / "scenes/act3/web/index.html").unlink()
        put(data["film"], "scenes/act3/index.tsx", "export default function NeutralAct() { return null; }")
        for sid in ("R01", "R02"):
            folder = data["film"] / "scenes/shots" / sid / "remotion"
            next(folder.glob("index.*")).unlink()
            put(data["film"], f"scenes/shots/{sid}/web/index.html", "<!doctype html><title>Neutral</title>")
            next(row for row in data["shots"]["shots"] if row["id"] == sid)["engine"] = "web"
    before = snapshot(data["film"])
    rows = shot_engines.descriptors(data["film"], data["shots"], 3, 90)
    assert [(row["shot"], row["act"], row["frames"]) for row in rows] == [
        ("R01", 3, [12, 23]), ("R02", 3, [36, 47])]
    assert [row["shot_record"]["frame_in"] for row in rows] == [102, 126]
    assert [row["shot_record"]["frame_out"] for row in rows] == [114, 138]
    expected = "remotion" if direction == "reverse" else "web"
    assert all(row["engine"] == expected and row["base_engine"] != expected for row in rows)
    assert all(row["comp"] == (f"Shot-{row['shot']}" if direction == "reverse" else None) for row in rows)
    assert all((data["film"] / row["index"]).is_file() and
               (data["film"] / row["scene"]).is_dir() for row in rows)
    assert snapshot(data["film"]) == before


@pytest.mark.parametrize("marker", [None, {"schema": 0, "enabled": True}, {"schema": 1, "enabled": False},
                                    {"schema": True, "enabled": True}, {"schema": 1, "enabled": "true"}],
                         ids=["missing", "old", "off", "bool-schema", "string-enabled"])
def test_reverse_segments_need_implemented_api_before_any_media(reverse, api, marker):
    api["capabilities"] = {} if marker is None else {"reverse_web_remotion": marker}
    with pytest.raises(Refused, match="Remotion"):
        shot_engines.segments(reverse["film"], reverse["shots"], 3, 90, "web")
    assert api["calls"] == [("version", {}, 30)]


def test_reverse_segments_accept_wired_api_without_prior_picture_or_approval(reverse, api):
    result = shot_engines.segments(reverse["film"], reverse["shots"], 3, 90, "web")
    assert [(row.shot, row.engine, row.first, row.last, row.scene.name) for row in result] == [
        ("R01", "remotion", 12, 23, "remotion"), ("R02", "remotion", 36, 47, "remotion")]
    assert api["calls"] == [("version", {}, 30)]


def test_source_only_problems_do_not_probe_and_handin_probes_once(reverse, api):
    assert shot_engines.problems(reverse["film"], reverse["shots"], include_unbuilt=False) == []
    assert api["calls"] == []
    assert shot_engines.problems(reverse["film"], reverse["shots"]) == []
    assert api["calls"] == [("version", {}, 30)]


def test_unrelated_source_failure_does_not_hide_selected_reverse_admission(reverse, api):
    reverse["shots"]["shots"].append({"id": "Q01", "act": 4, "engine": "web",
                                     "frame_in": 150, "frame_out": 160})
    put(reverse["film"], "scenes/act4/index.tsx", "export default function OtherAct() { return null; }")
    # Q01's page is intentionally absent. That other act is not selected by this operation.
    result = shot_engines.segments(reverse["film"], reverse["shots"], 3, 90, "web")
    assert [row.shot for row in result] == ["R01", "R02"]
    assert api["calls"] == [("version", {}, 30)]


@pytest.mark.parametrize("fault", ["missing-index", "competing-source", "bad-owner", "bad-range", "duplicate-id",
                                   "wrong-origin"])
def test_selected_source_and_clock_fail_before_compatibility_or_render(reverse, api, fault):
    if fault == "missing-index":
        (reverse["film"] / "scenes/shots/R01/remotion/index.tsx").unlink()
    elif fault == "competing-source":
        put(reverse["film"], "scenes/shots/R01/web/index.html", "<!doctype html><title>Other engine</title>")
    elif fault == "bad-owner":
        reverse["shots"]["shots"][3]["act"] = True
    elif fault == "bad-range":
        reverse["shots"]["shots"][3]["frame_out"] = 102
    elif fault == "duplicate-id":
        reverse["shots"]["shots"][4]["id"] = "R01"
    origin = 91 if fault == "wrong-origin" else 90
    with pytest.raises(Refused):
        shot_engines.segments(reverse["film"], reverse["shots"], 3, origin, "web")
    assert api["calls"] == []


def test_pure_web_missing_timing_retains_trace_noop_and_original_args(reverse):
    film = reverse["film"]
    (film / "timing/shots.json").unlink()
    for file in film.glob("scenes/shots/*/remotion/index.*"):
        file.unlink()
    eng = engines.WebEngine(film, reverse["settings"])
    assert eng.trace(3) is None
    args = eng._args(3, 0, 59, film / "pure.mkv", 4)
    assert args == {"film": str(film), "act": 3, "from": 0, "to": 59, "sub": 4,
                    "out": str(film / "pure.mkv"), "codec": "lossless"}


@pytest.mark.parametrize("hint", ["module", "script"])
def test_missing_timing_cannot_hide_declared_or_discovered_mixed_owner(reverse, api, hint):
    film = reverse["film"]
    (film / "timing/shots.json").unlink()
    if hint == "script":
        for file in film.glob("scenes/shots/*/remotion/index.*"):
            file.unlink()
        put(film, "script/script.json", {"acts": [{"index": 3, "shots": [{"id": "R01", "engine": "remotion"}]}]})
    with pytest.raises(Refused):
        engines.WebEngine(film, reverse["settings"]).trace(3)
    assert api["calls"] == []


def test_malformed_mixed_timing_is_typed_refusal_before_bridge(reverse, api):
    put(reverse["film"], "timing/shots.json", {"shots": [None]})
    with pytest.raises(Refused):
        engines.WebEngine(reverse["film"], reverse["settings"]).trace(3)
    assert api["calls"] == []


@pytest.mark.parametrize("name", ["remotion", "web"])
def test_isolated_scene_trace_never_writes_owning_active_coverage(reverse, api, name):
    scene = reverse["film"] / "scenes/shots/R01" / name
    eng = engines.get(name, reverse["film"], reverse["settings"], scene=scene)
    assert eng.trace(3) is None
    assert api["calls"] == []


def test_reverse_remotion_source_folder_selects_shot_comp_and_whole_act_range(reverse, api):
    film = reverse["film"]
    eng = engines.RemotionEngine(film, reverse["settings"], scene=film / "scenes/shots/R01/remotion")
    eng.open()
    result = eng.render(3, 12, 23, film / "patch.mkv", 4)
    assert result["comp"] == "Shot-R01" and result["frames"] == [12, 23] and result["frame_count"] == 12
    assert api["calls"] == [
        ("version", {}, 30),
        ("bundle", {"film": str(film)}, engines.BUNDLE_TIMEOUT_S),
        ("render", {"film": str(film), "from": 12, "to": 23, "sub": 4, "out": str(film / "patch.mkv"),
                    "codec": "lossless", "serve_url": "fake-bundle", "comp": "Shot-R01", "trace": False},
         engines.chunk_timeout(12, 4))]
    eng.close()
    assert api["calls"][-1] == ("bundle", {"film": str(film), "serve_url": "fake-bundle", "release": True}, 120)


def test_reverse_stills_keep_act_names_and_samples_but_retain_actual_composition_evidence(reverse, api):
    film = reverse["film"]
    eng = engines.RemotionEngine(film, reverse["settings"], scene=film / "scenes/shots/R01/remotion")
    result = eng.stills(3, [12, 23], film / "stills", scale=0.5, samples=4)
    assert [(row["comp"], row["frame"], Path(row["file"]).name) for row in result["stills"]] == [
        ("Shot-R01", 12, "Act3_f00012.png"), ("Shot-R01", 23, "Act3_f00023.png")]
    assert api["calls"] == [("version", {}, 30), ("stills", {
        "film": str(film), "specs": [{"comp": "Shot-R01", "frame": 12, "name": "Act3_f00012.png"},
                                   {"comp": "Shot-R01", "frame": 23, "name": "Act3_f00023.png"}],
        "scale": 0.5, "sub": 4, "out_dir": str(film / "stills")}, engines.STILLS_TIMEOUT_S)]


@pytest.mark.parametrize("frames", [(11, 23), (12, 24), (12, 36), (23, 12), (True, 23)])
def test_reverse_render_range_cannot_cross_or_reset_shot_clock(reverse, api, frames):
    film = reverse["film"]
    eng = engines.RemotionEngine(film, reverse["settings"], scene=film / "scenes/shots/R01/remotion")
    with pytest.raises(Refused):
        eng.render(3, frames[0], frames[1], film / "wrong.mkv", 4)
    assert api["calls"] == []


def test_generic_stills_routes_reverse_source_folder_and_base_without_new_clock(reverse, api):
    from slate.render.plan import acts_from_shots

    act = next(row for row in acts_from_shots(reverse["shots"]) if row.index == 3)
    work = stills._routes(reverse["film"], reverse["shots"], [(act, [0, 12, 23, 24, 36, 47, 59])], "web")
    assert [(name, scene.name if scene else None, requests[0][1]) for (name, scene), requests in work.items()] == [
        ("web", None, [0, 24, 59]), ("remotion", "remotion", [12, 23]), ("remotion", "remotion", [36, 47])]
    assert [scene.parent.name for name, scene in work if scene is not None] == ["R01", "R02"]


def test_mixed_owning_web_chunks_and_pngs_defer_trace_and_cache_only_api_availability(reverse, api):
    film = reverse["film"]
    eng = engines.WebEngine(film, reverse["settings"])
    eng.render(3, 0, 59, film / "base.mkv", 4)
    assert api["calls"][:2] == [("version", {}, 30), ("web-render", {
        "film": str(film), "act": 3, "from": 0, "to": 59, "sub": 4, "out": str(film / "base.mkv"),
        "codec": "lossless", "trace": False}, engines.chunk_timeout(60, 4))]
    eng.stills(3, [0, 59], film / "pngs", scale=1, samples=1)
    still_calls = api["calls"][2:]
    assert all(command == "web-render" and args["trace"] is False and "codec" not in args
               for command, args, _ in still_calls)
    assert [Path(args["out"]).name for _, args, _ in still_calls] == ["Act3_f00000.png", "Act3_f00059.png"]
    assert len([row for row in api["calls"] if row[0] == "version"]) == 1
    eng.close()
    eng._args(3, 0, 59, film / "next.mkv", 4)
    assert len([row for row in api["calls"] if row[0] == "version"]) == 2


def test_reverse_source_hash_includes_web_runtime_without_changing_pure_remotion_hash(reverse, monkeypatch):
    film = reverse["film"]
    engine = film / "neutral-engine"
    put(engine, "remotion/example.mjs", "export const sample = 1;")
    web = put(engine, "web/runtime.mjs", "export const sample = 1;")
    monkeypatch.setattr(deps, "engine_dir", lambda: engine)
    monkeypatch.setattr(engines, "film_settings_hash", lambda *args: "f" * 64)
    pure = engines.RemotionEngine(film, reverse["settings"]).sources()
    scene = film / "scenes/shots/R01/remotion"
    before = engines.RemotionEngine(film, reverse["settings"], scene=scene).sources()
    web.write_text("export const sample = 2;", encoding="utf-8")
    assert engines.RemotionEngine(film, reverse["settings"]).sources() == pure
    assert engines.RemotionEngine(film, reverse["settings"], scene=scene).sources() != before


def test_flat_trace_returns_owning_metadata_and_original_capture_map_including_explicit_empty_rows(reverse, api):
    report = producer(reverse)
    api["trace"] = report
    before = snapshot(reverse["film"])
    result = engines.WebEngine(reverse["film"], reverse["settings"]).trace(3)
    assert result == report and result["entries"] == 0 and "acts" not in result
    assert api["calls"] == [("version", {}, 30),
                            ("trace", {"film": str(reverse["film"]), "act": 3}, engines.TRACE_TIMEOUT_S)]
    assert snapshot(reverse["film"]) == before


@pytest.mark.parametrize("alias", ["film", "index", "timing"])
def test_real_captured_keys_accept_film_and_individual_source_aliases(reverse, api, alias):
    data = reverse
    film = data["film"]
    if alias == "film":
        link = film.parent / "film-alias"
        link.symlink_to(film, target_is_directory=True)
        data["film"] = link
    else:
        relative = "scenes/shots/R01/remotion/index.tsx" if alias == "index" else "timing/shots.json"
        source = film / relative
        target = film.parent / ("physical-index.tsx" if alias == "index" else "physical-shots.json")
        target.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(target)
    report = producer(data)
    api["trace"] = report
    # Real Node captures normalize actual read keys, while scene/index metadata retains the authored route.
    required = [(data["film"] / relative).resolve() for relative in
                ("film.json", "timing/shots.json", "scenes/act3/web/index.html",
                 "scenes/shots/R01/remotion/index.tsx", "scenes/shots/R02/remotion/index.js")]
    assert all(str(file) in report["source_hashes"] for file in required)
    assert engines.WebEngine(data["film"], data["settings"]).trace(3) == report
    assert api["calls"] == [("version", {}, 30),
                            ("trace", {"film": str(data["film"]), "act": 3}, engines.TRACE_TIMEOUT_S)]


def test_dangling_higher_precedence_candidate_preserves_original_absence_key(reverse, api):
    film = reverse["film"]
    folder = film / "scenes/shots/R02/remotion"
    higher = folder / "index.tsx"
    missing_target = film.parent / "absent-external-index.tsx"
    higher.symlink_to(missing_target)
    # The real index.js remains the selected module. Node records the dangling index.tsx at its authored candidate.
    expected_absent_key = str(folder.resolve() / "index.tsx")
    report = producer(reverse)
    proof = json.loads((film / report["composition_file"]).read_text(encoding="utf-8"))
    assert proof["source_hashes"][expected_absent_key] is None
    assert str(missing_target.resolve()) not in report["sources"]
    api["trace"] = report
    result = engines.WebEngine(film, reverse["settings"]).trace(3)
    assert result == report
    assert [row.index.name for row in shot_engines.mixed_scenes(film, reverse["shots"], 3)] == [
        "index.tsx", "index.js"]
    assert api["calls"] == [("version", {}, 30),
                            ("trace", {"film": str(film), "act": 3}, engines.TRACE_TIMEOUT_S)]


@pytest.mark.parametrize("bad", [None, True, 7, [], {"acts": [{"act": 3, "written": True}]}],
                         ids=["none", "bool", "integer", "array", "invented-envelope"])
def test_malformed_trace_container_is_refused_for_runner_archive_path(reverse, api, bad):
    producer(reverse)
    api["trace"] = bad
    with pytest.raises(Refused):
        engines.WebEngine(reverse["film"], reverse["settings"]).trace(3)


@pytest.mark.parametrize("key,value", [
    ("film", "other-film"), ("act", 2), ("act", True), ("written", False), ("file", "timing/motion/act2.json"),
    ("entries", True), ("entries", -1), ("frame_base", "act"), ("base_engine", "remotion"),
    ("remotion_shots", ["R02", "R01"]), ("source_hashes", None), ("sources", 7),
], ids=["wrong-film", "wrong-owner", "bool-owner", "not-written", "wrong-file", "bool-count", "negative-count",
        "local-clock", "wrong-base", "wrong-shot-order", "missing-hashes", "nonlist-sources"])
def test_trace_cannot_claim_complete_metadata_for_other_owner_or_missing_capture(reverse, api, key, value):
    api["trace"] = producer(reverse)
    api["trace"][key] = value
    with pytest.raises(Refused):
        engines.WebEngine(reverse["film"], reverse["settings"]).trace(3)


@pytest.mark.parametrize("fault", ["missing-sidecar", "missing-base", "wrong-clock", "wrong-fps",
                                   "missing-index-proof", "malformed-base", "wrong-active-count",
                                   "missing-routing-candidate", "missing-film-capture", "uncatalogued-import"])
def test_written_trace_requires_owning_sidecar_and_complete_captured_proof(reverse, api, fault):
    api["trace"] = producer(reverse)
    film = reverse["film"]
    if fault == "missing-sidecar":
        (film / api["trace"]["composition_file"]).unlink()
    elif fault == "missing-base":
        (film / api["trace"]["base_file"]).unlink()
    elif fault == "wrong-clock":
        edit_sidecar(reverse, lambda proof: proof.update(act_frames=[0, 60]))
    elif fault == "wrong-fps":
        edit_sidecar(reverse, lambda proof: proof.update(fps_den=1))
    elif fault == "missing-index-proof":
        edit_sidecar(reverse, lambda proof: proof["remotion_shots"][0].update(source_hashes={}))
    elif fault == "malformed-base":
        put(film, api["trace"]["base_file"], {})
    elif fault == "wrong-active-count":
        api["trace"]["entries"] = 1
    elif fault in ("missing-routing-candidate", "missing-film-capture"):
        key = str((film / ("scenes/shots/R02/remotion/index.tsx" if fault == "missing-routing-candidate"
                           else "film.json")).resolve())
        api["trace"]["sources"].remove(key)
        api["trace"]["source_hashes"].pop(key, None)
        edit_sidecar(reverse, lambda proof: proof["source_hashes"].pop(key))
    elif fault == "uncatalogued-import":
        edit_sidecar(reverse, lambda proof: proof["remotion_shots"][0]["source_hashes"].update(
            {str(film / "not-captured.ts"): "d" * 64}))
    with pytest.raises(Refused):
        engines.WebEngine(film, reverse["settings"]).trace(3)


@pytest.mark.parametrize("drift", ["changed-index", "appeared-competing-page"])
def test_trace_compares_original_capture_instead_of_adopting_postread_changes(reverse, api, drift):
    api["trace"] = producer(reverse)
    film = reverse["film"]
    if drift == "changed-index":
        api["after_trace"] = lambda: put(film, "scenes/shots/R01/remotion/index.tsx", "export default 2;")
    else:
        api["after_trace"] = lambda: put(film, "scenes/shots/R01/web/index.html", "<title>New competing page</title>")
    original = copy.deepcopy(api["trace"]["source_hashes"])
    with pytest.raises(Refused):
        engines.WebEngine(film, reverse["settings"]).trace(3)
    assert api["trace"]["source_hashes"] == original


def make_pure(data, engine):
    for row in data["shots"]["shots"]:
        row.pop("engine", None)
    for file in data["film"].glob("scenes/shots/*/remotion/index.*"):
        file.unlink()
    data["settings"]["engine"] = engine
    if engine == "remotion":
        (data["film"] / "scenes/act3/web/index.html").unlink()
        put(data["film"], "scenes/act3/index.tsx", "export default function NeutralAct() { return null; }")
    put(data["film"], "film.json", data["settings"])
    save_shots(data)


def test_pure_remotion_retains_bundle_render_still_and_flat_trace_contract(reverse, api):
    make_pure(reverse, "remotion")
    film = reverse["film"]
    eng = engines.RemotionEngine(film, reverse["settings"])
    api["trace"] = {"film": "neutral-consumers", "act": 3, "written": True,
                    "file": "timing/motion/act3.json", "entries": 0}
    eng.open()
    eng.render(3, 0, 59, film / "pure.mkv", 4)
    still = eng.stills(3, [0, 59], film / "pure-stills", scale=0.5, samples=1)
    assert eng.trace(3) == api["trace"]
    eng.close()
    assert [command for command, _, _ in api["calls"]] == ["bundle", "render", "stills", "trace", "bundle"]
    render = api["calls"][1][1]
    assert render == {"film": str(film), "act": 3, "from": 0, "to": 59, "sub": 4,
                      "out": str(film / "pure.mkv"), "codec": "lossless", "serve_url": "fake-bundle"}
    assert api["calls"][2][1] == {"film": str(film), "specs": ["Act3:0", "Act3:59"], "scale": 0.5,
                                 "sub": 1, "out_dir": str(film / "pure-stills"), "serve_url": "fake-bundle"}
    assert [Path(row["file"]).name for row in still["stills"]] == ["Act3_f00000.png", "Act3_f00059.png"]


def test_pure_web_keeps_existing_trace_and_png_bridge_arguments(reverse, api):
    make_pure(reverse, "web")
    film = reverse["film"]
    eng = engines.WebEngine(film, reverse["settings"])
    eng.render(3, 0, 59, film / "pure.mkv", 4)
    eng.stills(3, [0, 59], film / "pure-stills", scale=1, samples=1)
    assert eng.trace(3) is None
    assert len(api["calls"]) == 3 and all(command == "web-render" for command, _, _ in api["calls"])
    assert api["calls"][0][1] == {"film": str(film), "act": 3, "from": 0, "to": 59, "sub": 4,
                                 "out": str(film / "pure.mkv"), "codec": "lossless"}
    assert all("trace" not in args and "scene" not in args and "codec" not in args
               for _, args, _ in api["calls"][1:])


def test_forward_web_shot_pngs_keep_scene_trace_false_and_no_codec_change(reverse, api):
    make_pure(reverse, "remotion")
    film = reverse["film"]
    scene = film / "scenes/shots/R01/web"
    put(film, "scenes/shots/R01/web/index.html", "<!doctype html><title>Neutral replacement</title>")
    reverse["shots"]["shots"][3]["engine"] = "web"
    save_shots(reverse)
    eng = engines.WebEngine(film, reverse["settings"], scene=scene)
    result = eng.stills(3, [12, 23], film / "forward-stills", scale=1, samples=4)
    assert [Path(row["file"]).name for row in result["stills"]] == ["Act3_f00012.png", "Act3_f00023.png"]
    assert api["calls"] == [
        ("web-render", {"film": str(film), "act": 3, "from": frame, "to": frame, "sub": 4,
                        "out": str(film / "forward-stills" / f"Act3_f{frame:05d}.png"),
                        "scene": str(scene), "trace": False}, engines.chunk_timeout(1, 4)) for frame in (12, 23)]
