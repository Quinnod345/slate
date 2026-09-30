"""Reading a film's motion traces and its brand's law: frame bases, malformed and missing traces, the brand-motion
adapter with and without slate.brand.pack, and the film's own rate and size driving every limit."""

from __future__ import annotations

import json
import sys
import types

import pytest

from qa_motion_helpers import NEUTRAL_LAW, entry, make_film
from slate.qa import motion as M


@pytest.fixture
def law(monkeypatch):
    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))


def _sub(result, name):
    return next((c for c in result.details["checks"] if c["check"] == name), None)


def test_a_trace_in_the_acts_own_frames_moves_to_film_frames(slate_home, law):
    act2 = [entry("card", "y", 0, 15, 24, 0, ease="move", shot="S2")]  # frames from the act's own 0
    film = make_film(slate_home["films"], acts={1: (0, 60), 2: (60, 120)},
                     traces={1: [entry("t", "opacity", 0, 15, 0, 1, ease="enter")], 2: act2})
    data = M.load_data(film)
    moved = next(m for m in data.moves if m.act == 2)
    assert (moved.frame_in, moved.frame_out) == (60, 75) and moved.frames[0] == 60


def test_a_trace_can_say_its_frame_base(slate_home, law):
    film = make_film(slate_home["films"], acts={1: (0, 60), 2: (60, 120)}, traces={
        1: {"frame_base": "film", "entries": [entry("t", "opacity", 0, 15, 0, 1, ease="enter")]},
        2: {"frame_base": "act", "entries": [entry("card", "y", 70, 85, 24, 0, ease="move", shot="S2")]}})
    data = M.load_data(film)
    card = next(m for m in data.moves if m.id == "card")
    assert card.frame_in == 130  # "act" wins over the guess: 70 frames into act 2


def test_a_trace_that_fits_neither_frame_base_is_noted(slate_home, law):
    film = make_film(slate_home["films"], acts={1: (0, 60), 2: (60, 120)}, traces={
        1: [entry("t", "opacity", 0, 15, 0, 1, ease="enter")],
        2: [entry("card", "y", 200, 215, 24, 0, ease="move", shot="S2")]})
    data = M.load_data(film)
    assert data.notes and "act 2's trace runs from frame 200" in data.notes[0]


def test_malformed_entries_fail_the_trace_check(slate_home, law):
    good = entry("t", "opacity", 0, 15, 0, 1, ease="enter")
    bad = [good, {"id": "a", "prop": "x", "frame_in": 10, "frame_out": 5, "samples": []},
           {"id": "b", "prop": "x", "frame_in": 0, "frame_out": 5},
           {"id": "c", "prop": "x", "frame_in": 0, "frame_out": 5, "samples": [[0, "left"]]},
           {"id": "d", "prop": "x", "frame_in": 0, "frame_out": 5, "samples": [[0, 1]], "loop_period": -4}]
    film = make_film(slate_home["films"], traces={1: bad})
    result = M.evaluate(film, pixels=False)
    trace = _sub(result, "trace")
    assert trace["status"] == "FAIL" and result.status == "FAIL"
    problems = " | ".join(trace["problems"])
    assert "ends (frame 5) before it starts" in problems and "is missing samples" in problems
    assert "isn't a number or a list of numbers" in problems and "loop_period" in problems
    assert len(M.load_data(film).moves) == 1  # the good entry is still checked


def test_a_trace_that_isnt_a_list_is_malformed(slate_home, law):
    film = make_film(slate_home["films"], traces={1: {"hello": 1}})
    assert _sub(M.evaluate(film, pixels=False), "trace")["status"] == "FAIL"


def test_an_act_without_a_trace_keeps_the_gate_from_passing(slate_home, law):
    film = make_film(slate_home["films"], acts={1: (0, 60), 2: (60, 120)},
                     traces={1: [entry("t", "y", 0, 15, 24, 0, ease="move")]})
    result = M.evaluate(film, pixels=False)
    trace = _sub(result, "trace")
    assert trace["status"] == "UNCHECKED" and "act 2 has no motion trace" in trace["summary"]
    assert "not yet traced: 2" in _sub(result, "speed")["summary"]
    assert result.status == "UNCHECKED"


def test_no_acts_and_no_traces_is_unchecked(slate_home, law):
    film = slate_home["films"] / "bare"
    film.mkdir()
    (film / "film.json").write_text(json.dumps({"id": "bare", "fps_num": 25, "fps_den": 1, "width": 640,
                                                "height": 360, "brand": "x"}))
    result = M.evaluate(film)
    assert result.status == "UNCHECKED" and _sub(result, "trace")["status"] == "UNCHECKED"


def test_the_films_rate_and_size_set_every_limit(slate_home, law):
    moves = [entry("card", "x", 0, 12, 0, 200, ease="move")]  # 0.5 s at 24 fps
    film = make_film(slate_home["films"], fps=(24, 1), size=(1280, 720), traces={1: moves})
    result = M.evaluate(film, pixels=False)
    assert _sub(result, "speed")["limits"]["speed"] == pytest.approx(20)
    assert _sub(result, "durations")["tokens_frames"]["m"] == pytest.approx(12)
    assert _sub(result, "durations")["status"] == "PASS"


# ---------------------------------------------------------------------------------------------------- brand law
def _brand(home, pack):
    folder = home["brands"] / "neutral-test"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "brand.json").write_text(json.dumps(pack))
    return folder


def test_brand_motion_through_the_brand_module(slate_home, monkeypatch):
    fake = types.ModuleType("slate.brand.pack")
    fake.load = lambda name: {"name": name}
    fake.motion = lambda pack: NEUTRAL_LAW
    monkeypatch.setitem(sys.modules, "slate.brand.pack", fake)
    data, source = M.brand_motion("neutral-test")
    assert data is NEUTRAL_LAW and source == "slate.brand.pack.motion"
    fake.motion = lambda pack: {}
    assert M.brand_motion("neutral-test")[0] is None


def test_brand_motion_from_brand_json_without_the_module(slate_home, monkeypatch):
    monkeypatch.setitem(sys.modules, "slate.brand.pack", None)  # not installed
    _brand(slate_home, {"name": "neutral-test", "motion": NEUTRAL_LAW})
    data, source = M.brand_motion("neutral-test")
    assert data == NEUTRAL_LAW and source.endswith("brand.json (motion)")
    folder = _brand(slate_home, {"name": "neutral-test", "motion": "motion.json"})
    (folder / "motion.json").write_text(json.dumps(NEUTRAL_LAW))
    data, source = M.brand_motion("neutral-test")
    assert data == NEUTRAL_LAW and source.endswith("motion.json (motion)")
    _brand(slate_home, {"name": "neutral-test"})
    data, source = M.brand_motion("neutral-test")
    assert data is None and "no motion data yet" in source
    assert M.brand_motion(None) == (None, "film.json names no brand")


def test_a_film_without_a_brand_leaves_the_brand_rules_unchecked(slate_home):
    film = make_film(slate_home["films"], brand=None, traces={1: [entry("t", "y", 0, 15, 24, 0, ease="move")]})
    result = M.evaluate(film, pixels=False)
    for name in ("eases", "durations"):
        s = _sub(result, name)
        assert s["status"] == "UNCHECKED" and "names no brand" in s["summary"]
    assert _sub(result, "speed")["status"] == "PASS"  # the universal rules still run
