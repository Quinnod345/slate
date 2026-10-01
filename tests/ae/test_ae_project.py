"""The build spec, the project build end to end (Python spec -> build_film.jsx in the mock AE -> report), and the
AE-built act's motion trace and visual events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import jsonio
from slate.ae import acts, eases, project, spec
from slate.errors import Busy, MissingPrerequisite, Refused, UsageError

from ae_helpers import FPS_DEN, FPS_NUM, H, SCENE, W, MockAE, make_film, prefs_bytes


@pytest.fixture
def film(slate_home):
    d, media = make_film(slate_home["films"], slate_home["brands"])
    return {"dir": d, "media": media, **slate_home}


# ---- the spec -------------------------------------------------------------------------------------------------


def test_spec_reads_the_film_on_its_own_clock(film):
    s = spec.build_spec(film["dir"])
    assert s["generated"].startswith("do not edit by hand")
    assert s["film"] == {"id": "neutral", "title": "Neutral film", "fps_num": FPS_NUM, "fps_den": FPS_DEN,
                         "width": W, "height": H, "pixel_aspect": 1.0, "total_frames": 264}
    assert s["project"]["working_space"] == "Rec.709 Gamma 2.4" and s["project"]["bpc"] == 16
    assert s["project"]["sample_bias_frames"] == 0.1 and s["project"]["gpu"] == "software"
    assert [(a["index"], a["comp"], a["engine"], a["frame_in"], a["frame_out"]) for a in s["acts"]] == [
        (1, "A1 Opening", "render", 0, 120), (2, "A2 The card", "ae", 120, 192), (3, "A3 Close", "render", 192, 264)]
    assert [(r["act"], r["frame_in"], r["frame_out"]) for r in s["renders"]] == [(1, 0, 120), (3, 192, 264)]
    assert s["audio"]["file"].endswith("sound/mix/final_mix.wav")
    assert s["names"]["master"] == "MASTER neutral"
    assert s["paths"]["aep"] == str((film["dir"] / "ae" / "neutral.aep").resolve())


def test_spec_markers_come_from_the_timeline_and_the_words(film):
    m = spec.build_spec(film["dir"])["markers"]
    assert [x["frame"] for x in m["shots"]] == [0, 48, 120, 192, 240]
    assert m["shots"][2]["label"] == "S3 · Hello"
    fps = FPS_NUM / FPS_DEN
    # S1's take starts at its vo_frame (6); "hello" is 0.1 s in
    assert m["words"][0] == {"frame": round((6 / fps + 0.1) * fps, 4), "label": "S1 hello"}
    assert [(e["frame"], e["label"]) for e in m["events"]] == [(12, "S1/hello"), (144, "S3/card")]


def test_spec_brand_tokens_fonts_eases_and_logos(film):
    b = spec.build_spec(film["dir"])["brand"]
    assert b["palette"] == {"paper": "#F4F1EA", "ink": "#1C1C1C", "accent": "#3366FF"}
    assert b["fonts"] == {"display": {"postscript": "MockSans-Regular", "family": "Mock Sans"}}
    assert b["eases"]["enter"] == {"kind": "bezier", "points": [0.0, 0.0, 0.58, 1.0]}
    assert b["eases"]["move"] == {"kind": "sine"}
    assert b["durations"] == {"quick": 0.33, "base": 0.5}
    logo = b["logos"]["mark"]
    assert logo["viewBox"] == [0.0, 0.0, 100.0, 60.0]
    assert [i["id"] for i in logo["items"]] == ["bar", "swoosh"]
    assert logo["items"][0]["bbox"] == [10.0, 5.0, 70.0, 15.0]  # the group's translate is applied


def test_spec_masters_plan_tiles_the_film(film):
    m = spec.build_spec(film["dir"], chunks=5)["masters"]
    assert [(c["start"], c["end"], c["frames"]) for c in m["chunks"]] == [
        (0, 52, 53), (53, 105, 53), (106, 158, 53), (159, 211, 53), (212, 263, 52)]
    assert sum(c["frames"] for c in m["chunks"]) == 264
    assert m["om_primary"].startswith("H.264") and m["om_alpha"] == "High Quality with Alpha"


def test_chunk_plan_edges():
    assert [c["frames"] for c in spec.chunk_plan(10, 3)] == [4, 4, 2]
    assert [c["frames"] for c in spec.chunk_plan(3, 8)] == [1, 1, 1]
    with pytest.raises(UsageError):
        spec.chunk_plan(0, 4)
    with pytest.raises(UsageError):
        spec.chunk_plan(10, 0)


def test_spec_needs_the_films_rate_and_size(film):
    fj = json.loads((film["dir"] / "film.json").read_text())
    del fj["fps_den"]
    (film["dir"] / "film.json").write_text(json.dumps(fj))
    with pytest.raises(UsageError, match="never assumes a frame rate"):
        spec.build_spec(film["dir"])


def test_spec_without_timing_is_a_missing_prerequisite(film):
    (film["dir"] / "timing" / "shots.json").unlink()
    with pytest.raises(MissingPrerequisite):
        spec.build_spec(film["dir"])


# ---- the AE-built act -----------------------------------------------------------------------------------------


def test_scene_moves_land_on_their_event(film):
    s = spec.build_spec(film["dir"])
    act2 = s["acts"][1]["scene"]
    fps = FPS_NUM / FPS_DEN
    card = next(m for m in act2["moves"] if m["id"] == "card-in")
    # lands on S3/card (film frame 144), T90 x 0.5 s earlier, lasting 0.5 s
    start = round(144 / fps - eases.t90([0, 0, 0.58, 1]) * 0.5, 9)
    assert card["keys"][0][0] == int(round(start * fps))
    assert card["keys"][1][0] - card["keys"][0][0] == int(round(0.5 * fps))
    # the build form counts from the act's own frame 0
    anim = next(a for a in act2["anims"] if a["id"] == "card-in")
    assert anim["keys"][0][0] == card["keys"][0][0] - 120
    fade = next(a for a in act2["anims"] if a["id"] == "title-fade")
    assert fade["keys"] == [[12, 0, "move"], [30, 100, "linear"]]
    grow = next(a for a in act2["anims"] if a["id"] == "card-grow")
    assert grow["keys"][0][:1] == [4] and grow["keys"][1][0] == 4 + round(0.33 * fps)


def test_scene_refuses_what_isnt_the_brands(film):
    bad = dict(SCENE, anims=[dict(SCENE["anims"][0], ease="bouncy")])
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(bad))
    with pytest.raises(UsageError, match="named eases"):
        spec.build_spec(film["dir"])
    bad = dict(SCENE, layers=[dict(SCENE["layers"][0], fill="chartreuse")] + SCENE["layers"][1:])
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(bad))
    with pytest.raises(UsageError, match="palette"):
        spec.build_spec(film["dir"])
    bad = dict(SCENE, anims=[dict(SCENE["anims"][0], land="S3/nope")])
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(bad))
    with pytest.raises(UsageError, match="timeline event"):
        spec.build_spec(film["dir"])
    bad = dict(SCENE, anims=[dict(SCENE["anims"][0], dur="forever")])
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(bad))
    with pytest.raises(UsageError, match="duration"):
        spec.build_spec(film["dir"])


def test_export_writes_the_motion_trace_and_events(film):
    out = acts.export(film["dir"], 2)
    assert out == {"act": 2, "motion": "timing/motion/act2.json", "entries": 3,
                   "events": "timing/visual_events/act2.json", "beats": 1, "silent": False}
    trace = jsonio.read(film["dir"] / "timing" / "motion" / "act2.json")
    assert isinstance(trace, list)
    card = next(t for t in trace if t["id"] == "card-in")
    assert card["prop"] == "position" and card["ease"] == "enter" and card["shot"] == "S3"
    assert card["from"] == [640, 460] and card["to"] == [640, 360]
    f0, f1 = card["frame_in"], card["frame_out"]
    assert [s[0] for s in card["samples"]] == list(range(f0, f1 + 1))
    mid = card["samples"][(f1 - f0) // 2]
    u = (mid[0] - f0) / (f1 - f0)
    assert mid[1][1] == pytest.approx(460 - 100 * eases.value([0, 0, 0.58, 1], u), abs=1e-5)
    grow = next(t for t in trace if t["id"] == "card-grow")
    assert grow["entrance"] is True and grow["size"] == [400, 240]
    fade = next(t for t in trace if t["id"] == "title-fade")
    assert fade["entrance"] is True and fade["ease"] == "move"
    events = jsonio.read(film["dir"] / "timing" / "visual_events" / "act2.json")
    assert events == [{"id": "card-in", "shot": "S3", "t": round(144 / (FPS_NUM / FPS_DEN), 6), "kind": "land",
                       "size": "m", "note": "the card lands"}]


def test_export_notes_what_made_its_files_in_the_lock(film):
    """Derived files are recorded with their producer and inputs, so `slate status` can call them stale."""
    pytest.importorskip("slate.state.lock")
    acts.export(film["dir"], 2)
    files = jsonio.read(film["dir"] / "film.lock.json")["files"]
    for rel in ("timing/motion/act2.json", "timing/visual_events/act2.json"):
        assert files[rel]["producer"] == "slate ae acts export", rel
        assert "scenes/ae/act2.json" in files[rel]["inputs"] and "timing/shots.json" in files[rel]["inputs"]


def test_a_scene_with_no_beats_must_say_why(film):
    quiet = dict(SCENE, anims=[{k: v for k, v in SCENE["anims"][0].items() if k != "event"}])
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(quiet))
    with pytest.raises(Refused, match="silent"):
        acts.export(film["dir"], 2)
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(dict(quiet, silent="a held title card")))
    out = acts.export(film["dir"], 2)
    assert out["silent"] is True
    assert jsonio.read(film["dir"] / "timing" / "visual_events" / "act2.json")["reason"] == "a held title card"


def _scene_with(*anims) -> dict:
    return dict(SCENE, anims=[*SCENE["anims"], *anims])


def test_a_draw_ends_on_its_beat_and_carries_its_span(film):
    """t is always the beat (slate.timing.beats): a draw gives its span and ends on t, whatever `at` says; a whoosh
    gives its span with its beat inside; a land keeps its moment and gives none."""
    from slate.timing import beats

    draw = {"id": "rule-draws", "layer": "card", "prop": "scale", "from": [0, 100], "to": [100, 100], "ease": "move",
            "dur": "base", "start": {"shot": "S3", "frame": 30},
            "event": {"kind": "draw", "size": "s", "note": "the rule draws across", "at": "start"}}
    whoosh = {"id": "card-away", "layer": "card", "prop": "position", "from": [640, 360], "to": [1640, 360],
              "ease": "move", "dur": "quick", "start": {"shot": "S3", "frame": 50},
              "event": {"kind": "whoosh", "size": "m", "note": "the card flies off", "at": "end"}}
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(_scene_with(draw, whoosh)))
    acts.export(film["dir"], 2)
    events = {e["id"]: e for e in jsonio.read(film["dir"] / "timing" / "visual_events" / "act2.json")}
    fps = FPS_NUM / FPS_DEN
    d = events["rule-draws"]
    first, frames = 120 + 30, round(0.5 * fps)
    assert d["start"] == pytest.approx(first / fps, abs=1e-6) and d["dur"] == pytest.approx(frames / fps, abs=1e-6)
    assert d["t"] == pytest.approx(d["start"] + d["dur"], abs=1e-6)  # the end, though `at` said start
    w = events["card-away"]
    assert w["start"] <= w["t"] <= w["start"] + w["dur"] + 1e-6  # a whoosh peaks inside its span
    assert "start" not in events["card-in"] and "dur" not in events["card-in"]
    assert beats.problems(list(events.values()), fps=fps) == []


def test_beats_that_break_their_span_are_refused_before_anything_is_written(film, monkeypatch):
    resolved = {"events": [{"id": "x", "shot": "S3", "t": 6.0, "kind": "draw", "size": "s", "note": "n",
                            "start": 5.0, "dur": 0.5}]}
    with pytest.raises(Refused, match="ends on its beat"):
        acts.events_doc(resolved, {"index": 2}, fps=FPS_NUM / FPS_DEN)


def test_moves_say_which_speed_limits_hold_them(film):
    """`class`, `exempt` and `reason` reach the trace (slate qa motion reads them); a rig part is rig unasked."""
    carry = {"id": "page-carry", "layer": "card", "prop": "position", "from": [640, 360], "to": [240, 360],
             "ease": "move", "dur": "base", "start": {"shot": "S3", "frame": 20}, "class": "page"}
    sweep = {"id": "sweep", "layer": "title", "prop": "position", "from": [0, 200], "to": [1280, 200],
             "ease": "move", "dur": "base", "start": {"shot": "S3", "frame": 40}, "exempt": "Reading Sweep",
             "reason": "the line reads its items at a steady speed"}
    rig = {"id": "mark-sway", "layer": "mark", "prop": "rotation", "from": 0, "to": 8, "ease": "move", "dur": "quick",
           "start": {"shot": "S3", "frame": 10}, "rig": "mark"}
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(_scene_with(carry, sweep, rig)))
    acts.export(film["dir"], 2)
    trace = {t["id"]: t for t in jsonio.read(film["dir"] / "timing" / "motion" / "act2.json")}
    assert trace["page-carry"]["class"] == "carry"
    assert trace["sweep"]["exempt"] == "reading_sweep" and trace["sweep"]["reason"].startswith("the line")
    assert trace["mark-sway"]["class"] == "rig"
    assert "class" not in trace["card-in"]
    bad = dict(carry, id="bad", **{"class": "fast"})
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(_scene_with(bad)))
    with pytest.raises(UsageError, match="class 'fast'"):
        acts.export(film["dir"], 2)


def test_trace_from_measured_samples():
    act = {"index": 2, "frame_in": 120, "frame_out": 192, "shots": ["S3"]}
    shots = {"S3": {"frame_in": 120, "frame_out": 192}}
    raw = [{"layer": "card", "match_name": "ADBE Position_1", "prop": "Y Position", "frame_in": 4, "frame_out": 6,
            "samples": [[4, 460.0], [5, 420.0], [6, 360.0]]},
           {"layer": "card", "match_name": "ADBE Opacity", "samples": [[0, 0.0]]}]
    out = acts.trace_from_samples(raw, act, shots)
    assert out == [{"id": "card.y", "shot": "S3", "prop": "y", "ease": "measured", "frame_in": 124, "frame_out": 126,
                    "from": 460.0, "to": 360.0, "samples": [[124, 460.0], [125, 420.0], [126, 360.0]],
                    "entrance": False, "loop_period": None, "rig": None, "engine": "ae", "layer": "card"}]


def test_a_measured_trace_says_where_every_key_frame_is():
    """Two moves chained without a stop split at their shared key (qa motion reads `keys`, in film frames)."""
    act = {"index": 2, "frame_in": 120, "frame_out": 192, "shots": ["S3"]}
    shots = {"S3": {"frame_in": 120, "frame_out": 192}}
    raw = [{"layer": "card", "match_name": "ADBE Position_0", "keys": [0, 6, 12, 6],
            "samples": [[f, 100.0 + 10 * f] for f in range(13)]}]
    [entry] = acts.trace_from_samples(raw, act, shots)
    assert entry["keys"] == [120, 126, 132]
    assert (entry["frame_in"], entry["frame_out"]) == (120, 132)


# ---- the project build, end to end through the mock AE --------------------------------------------------------


def test_build_needs_go_and_never_runs_for_autonomous_films(film, slate_home):
    with pytest.raises(Refused, match="go"):
        project.build(film["dir"])
    auto, _media = make_film(slate_home["films"], slate_home["brands"], "auto", mode="autonomous")
    with pytest.raises(Refused, match="utonomous"):
        project.build(auto, go=True, runner=lambda *a, **k: pytest.fail("AE must not start"))


def test_build_end_to_end(film, live_prefs):
    ae = MockAE(live_prefs(data=prefs_bytes(scripting=b'"0"')), media=film["media"])
    aep = project.build(film["dir"], go=True, runner=ae)
    assert aep == (film["dir"] / "ae" / "neutral.aep").resolve() and aep.is_file()
    rep = project.last_report(film["dir"])
    data = rep["report"]
    assert data["project"] == {"bpc": 16, "working_space": "Rec.709 Gamma 2.4", "linear_blending": False}
    assert data["built"]["rendered"] == ["S1", "S2", "S4", "S5"] and data["built"]["placeholders"] == []
    assert data["built"]["ae_acts"] == [{"act": 2, "layers": 3, "anims": 3}]
    assert data["markers"] == {"shots": 5, "words": 2, "events": 2}
    assert [q["match"] for q in data["queue"]] == [True] * 8
    assert data["brand_board"] == {"palette": 3, "fonts": 1, "logos": 1}
    assert rep["report_channel"] == "settings"
    queued = jsonio.read(film["dir"] / "ae" / "masters_queue.json")
    assert queued["aep_sha256"] == jsonio.sha256(aep) and len(queued["chunks"]) == 8
    # the AE-built act's trace and events were written with the build
    assert (film["dir"] / "timing" / "motion" / "act2.json").is_file()
    assert (film["dir"] / "timing" / "visual_events" / "act2.json").is_file()
    assert ae.results[-1]["discarded"] == []


def test_build_preserves_unsaved_work(film, live_prefs, slate_home):
    ae = MockAE(live_prefs(), media=film["media"], state={"dirtyProject": True})
    project.build(film["dir"], go=True, runner=ae)
    rep = project.last_report(film["dir"])
    assert rep["report_channel"] == "file"  # the file-write pref is on in this stand-in, so AE wrote report.json
    kept = rep["report"]["preserved_project"]
    assert kept.startswith(str(slate_home["data"] / "ae" / "preserved"))
    assert ae.results[-1]["saves"][0] == kept and ae.results[-1]["discarded"] == []


def test_build_waits_for_the_bridge(film, live_prefs, monkeypatch):
    import os
    import time

    from slate.ae import bridgelock

    monkeypatch.setattr(bridgelock, "bridge_installed", lambda: True)
    box = Path(os.environ["TMPDIR"]) / "mcp-aftereffects" / "runtime"
    box.mkdir(parents=True)
    (box / "dispatcher-busy.lock").write_text(json.dumps({"pid": 1, "id": "bridge", "startedAt": "now"}))
    from slate.ae import driver

    monkeypatch.setattr(driver, "LOCK_TIMEOUT_S", 0.3)
    ae = MockAE(live_prefs(), media=film["media"])
    started = time.monotonic()
    with pytest.raises(Busy):
        driver.run_jsx(driver.script("open_project.jsx"), runner=ae, lock_timeout=0.3)
    assert time.monotonic() - started < 10 and ae.history == []


# ---- rendering and measuring an AE-built act ------------------------------------------------------------------


def test_render_an_ae_built_act_and_register_it(film, live_prefs, monkeypatch):
    from slate import procs
    from ae_helpers import FakeMedia

    pf = live_prefs(data=prefs_bytes(scripting=b'"0"'))
    project.build(film["dir"], go=True, runner=MockAE(pf, media=film["media"]))
    fake = FakeMedia({"A2 The card": 72})
    seen_cache = []

    def runner(argv, timeout=None, **kw):
        if Path(str(argv[0])).name == "aerender":
            from slate.ae import prefs as ae_prefs
            seen_cache.append(ae_prefs.value_of(pf.read_bytes(), *ae_prefs.DISK_CACHE))
        return fake(argv, timeout=timeout, **kw)

    res = acts.render(film["dir"], 2, go=True, runner=runner)
    assert res["file"] == "renders/ae/act2.mov" and res["frames"] == [120, 192] and res["engine"] == "ae"
    assert res["samples"] == 16 and res["bundle_hash"].startswith("aep:") and seen_cache == ["0"]
    aer = next(c for c in fake.calls if Path(c[0]).name == "aerender")
    assert aer[aer.index("-comp") + 1] == "A2 The card"
    assert aer[aer.index("-OMtemplate") + 1] == "High Quality with Alpha"
    conform = next(c for c in fake.calls if "prores_ks" in c)
    assert conform[-1].endswith("renders/ae/act2.mov")
    try:
        from slate.render import registry
    except ImportError:
        registry = None
    if registry is not None:
        assert res["registered"] is True
        entry = registry.find(film["dir"], 2)
        assert entry["engine"] == "ae" and entry["frames"] == [120, 192]
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    with pytest.raises(Busy):
        acts.render(film["dir"], 2, go=True, runner=runner)
    with pytest.raises(Refused):
        acts.render(film["dir"], 2, runner=runner)


def test_render_refuses_a_short_render(film, live_prefs):
    from slate.errors import SlateError
    from ae_helpers import FakeMedia

    pf = live_prefs(data=prefs_bytes(scripting=b'"0"'))
    project.build(film["dir"], go=True, runner=MockAE(pf, media=film["media"]))
    with pytest.raises(SlateError, match="of act 2's 72 frames"):
        acts.render(film["dir"], 2, go=True, runner=FakeMedia({"A2 The card": 70}), register=False)


def test_measure_an_act_as_ae_evaluates_it(film, live_prefs):
    pf = live_prefs(data=prefs_bytes(scripting=b'"0"'))
    ae = MockAE(pf, media=film["media"])
    project.build(film["dir"], go=True, runner=ae)
    res = acts.export_measured(film["dir"], 2, go=True, runner=ae)
    assert res["measured"] is True and res["entries"] >= 3
    trace = jsonio.read(film["dir"] / "timing" / "motion" / "act2.json")
    pos = next(t for t in trace if t["id"] == "card.position")
    assert pos["ease"] == "measured" and pos["frame_in"] >= 120 and pos["to"] == [640, 360]
    exported = acts.trace_entries(spec.read_spec(film["dir"])["acts"][1]["scene"], spec.read_spec(film["dir"])["acts"][1],
                                  {s["id"]: s for s in spec.read_spec(film["dir"])["shots"]},
                                  eases_table=spec.read_spec(film["dir"])["brand"]["eases"])
    planned = next(t for t in exported if t["id"] == "card-in")
    measured = {f: v for f, v in pos["samples"]}
    worst = max(abs(measured[f][1] - v[1]) for f, v in planned["samples"] if f in measured)
    assert worst < 0.005 * 100  # AE's own curve (the mock's model of it) follows the plan to 0.5% of the travel


def test_a_scene_background_must_be_a_brand_colour(film):
    (film["dir"] / "scenes" / "ae" / "act2.json").write_text(json.dumps(dict(SCENE, bg="sunset")))
    with pytest.raises(UsageError, match="palette"):
        spec.build_spec(film["dir"])
