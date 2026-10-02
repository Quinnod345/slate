"""Speed limits by what moves, the generic curves the eases gate knows, and the exemptions a trace may declare.

The plan's 30 px/frame and 8 px/frame² came from the character rig; the films' page and camera carries ran clean at
136 to 169 px/frame while a carry at 310 streaked. These tests hold each class to its own limits, and check that the
rig's own curves (sway, land), the even travel profile, a reading sweep and a steady line pass where the films'
motion law says they may, and fail everywhere else.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from qa_motion_helpers import NEUTRAL_LAW, curve, data, entry, make_film
from slate.motion.eases import evaluate, mono_spline, parse
from slate.qa import motion as M

HD = (1920, 1080)
LAW_WITHOUT_SWAY = {**NEUTRAL_LAW, "eases": {k: v for k, v in NEUTRAL_LAW["eases"].items() if k != "sway"}}


def cls(name: str) -> dict:
    return {"class": name}


def glide_carry(ident: str, peak: float, frames: int = 24, **extra) -> dict:
    """A rest-to-rest carry on the neutral law's in-out ease, scaled so its fastest frame is `peak` px."""
    fn = curve("move")
    steps = np.diff([fn(k / frames) for k in range(frames + 1)])
    travel = peak / float(steps.max())
    return entry(ident, "x", 0, frames, 0, travel, ease="move", **extra)


def even(u: float) -> float:
    return mono_spline(min(max(u, 0.0), 1.0), [[0, 0], [1 / 3, 1 / 3], [2 / 3, 2 / 3], [1, 1]])


# ---------------------------------------------------------------------------------------------------- the classes
def test_each_class_has_its_own_limits_at_1080p_and_scales_with_the_frame():
    t = M.class_limits(*HD)
    assert (t["rig"]["speed"], t["rig"]["accel"]) == pytest.approx((30, 8))  # the plan's law is the rig's
    assert (t["ui"]["speed"], t["ui"]["accel"]) == pytest.approx((60, 16))
    assert (t["carry"]["speed"], t["carry"]["accel"]) == pytest.approx((170, 170 * 8 / 30))
    assert t["whip"]["speed"] is None and t["whip"]["accel"] is None
    assert M.class_limits(1280, 720)["carry"]["speed"] == pytest.approx(170 * 720 / 1080)
    assert M.class_limits(1080, 1920)["ui"]["speed"] == pytest.approx(60)  # a 9:16 reel is a 1080p frame
    assert M.limits(*HD)["speed"] == pytest.approx(30)  # limits() stays the rig's, crest exception and all


def test_classes_come_from_the_trace_then_the_rig_then_the_cameras_name():
    moves = {e["id"]: M.move_from(e, 1, 0) for e in (
        entry("card", "x", 0, 10, 0, 10, **cls("carry")),
        entry("tile", "x", 0, 10, 0, 10, rig="mark"),
        entry("camera", "x", 0, 10, 0, 10),
        entry("stage", "cam.y", 0, 10, 0, 10),
        entry("label", "x", 0, 10, 0, 10),
        entry("hero", "x", 0, 10, 0, 10, **cls("page")),
        entry("wink", "x", 0, 10, 0, 10, **cls("character")))}
    got = {k: M.element_class(m)[0] for k, m in moves.items()}
    assert got == {"card": "carry", "tile": "rig", "camera": "carry", "stage": "carry", "label": "ui",
                   "hero": "carry", "wink": "rig"}
    assert M.element_class(moves["label"])[1] == "no class declared"


def test_a_carry_at_the_films_approved_speed_passes_and_the_streaking_one_fails():
    approved = M.check_speed(data([glide_carry("page", 136, **cls("carry"))], size=HD))
    assert approved.status == "PASS", approved.summary
    assert approved.details["classes"]["carry"]["fastest"] == pytest.approx(136, abs=0.5)
    assert approved.details["classes"]["carry"]["hardest"] < approved.details["class_limits"]["carry"]["accel"]
    truck = M.check_speed(data([glide_carry("camera", 169, frames=36)], size=HD))  # the camera: a carry by name
    assert truck.status == "PASS", truck.summary
    streak = M.check_speed(data([glide_carry("page", 310, frames=12, **cls("carry"))], size=HD))
    assert streak.status == "FAIL" and "limit for a carry" in streak.summary


def test_an_undeclared_element_is_held_to_the_ui_limits_and_told_how_to_say_it_is_a_carry():
    s = M.check_speed(data([glide_carry("report", 136)], size=HD))
    assert s.status == "FAIL"
    assert "limit for a UI element" in s.summary and '"class": "carry"' in s.summary
    ok = M.check_speed(data([glide_carry("chip", 55)], size=HD))
    assert ok.status == "PASS", ok.summary


def test_a_rig_part_keeps_the_plans_30_px_where_a_ui_element_may_go_faster():
    ui = M.check_speed(data([glide_carry("chip", 45)], size=HD))
    rig = M.check_speed(data([glide_carry("tile", 45, rig="mark")], size=HD))
    assert ui.status == "PASS" and rig.status == "FAIL" and "limit for a rig part" in rig.summary


def test_an_element_with_two_classes_is_held_to_the_stricter():
    moves = [glide_carry("card", 45, **cls("carry")), entry("card", "y", 0, 24, 0, 1, ease="move", **cls("rig"))]
    s = M.check_speed(data(moves, size=HD))
    assert s.status == "FAIL" and "rig part" in s.summary


def test_a_whip_past_the_carry_limits_goes_to_a_person():
    s = M.check_speed(data([glide_carry("pullback", 267, frames=24, **cls("whip"))], size=HD))
    assert s.status == "REVIEW" and "a whip is a person's call" in s.summary
    assert s.details["review_items"][0]["key"].startswith("speed:act1:pullback.x")
    calm = M.check_speed(data([glide_carry("pullback", 120, frames=24, **cls("whip"))], size=HD))
    assert calm.status == "PASS"


def test_a_brand_may_set_its_own_limits():
    law = {**NEUTRAL_LAW, "speed_limits": {"ui": {"speed": 48, "short_side": 1080}, "carry": {"speed": 0.1}}}
    d = data([glide_carry("chip", 55)], size=HD, law=law)
    t = M.class_limits(*HD, d.law)
    assert t["ui"]["speed"] == pytest.approx(48) and t["ui"]["accel"] == pytest.approx(16)  # accel stays the default
    assert t["carry"]["speed"] == pytest.approx(108)
    assert "the brand's speed_limits.ui" in t["ui"]["source"]
    assert M.check_speed(d).status == "FAIL"


def test_a_brand_limit_that_cant_be_read_is_a_note_not_a_quiet_default():
    law = {**NEUTRAL_LAW, "speed_limits": {"ui": {"speed": 48}, "sparkle": {"speed": 0.5}}}
    parsed = M.law_from(law, source="t", fps=30)
    assert "ui" not in parsed.speed_limits
    notes = " ".join(parsed.notes)
    assert "speed_limits.ui.speed needs" in notes and "speed_limits.sparkle isn't one of the classes" in notes


# ---------------------------------------------------------------------------------------------------- generic curves
def test_the_generic_curves_are_the_kits_own_maths():
    law = M.law_from(LAW_WITHOUT_SWAY, source="t", fps=30)
    assert set(law.extra) == {"sway", "land", "spring0", "even"}
    for u in np.linspace(0, 1, 41):
        assert law.extra["sway"].fn(u) == pytest.approx(evaluate(parse("sine"), u), abs=1e-12)
        assert law.extra["land"].fn(u) == pytest.approx(evaluate(parse([0.25, 0, 0.25, 1]), u), abs=1e-12)
        assert law.extra["even"].fn(u) == pytest.approx(even(u), abs=1e-12)
    steps = np.diff([law.extra["even"].fn(k / 600) for k in range(601)]) * 600
    assert steps.max() == pytest.approx(4 / 3, abs=0.01)  # peak speed 1.33 × average, at rest at both ends
    assert steps[0] < 0.02 and steps[-1] < 0.02
    assert law.extra_curve("sine").name == "sway" and law.curve("move").name == "move"


def test_a_rig_part_may_use_the_rigs_own_curves():
    moves = [entry("tile0", "spread", 0, 14, 0, 0.16, ease="sine", fn=curve("sway"), rig="mark", law=LAW_WITHOUT_SWAY),
             entry("tile1", "turn", 0, 12, 0, 8, ease="land", fn=lambda u: evaluate(parse([0.25, 0, 0.25, 1]), u),
                   rig="mark", law=LAW_WITHOUT_SWAY)]
    s = M.check_eases(data(moves, law=LAW_WITHOUT_SWAY))
    assert s.status == "PASS", s.summary
    assert s.details["extra_curves"] == {"sway": 1, "land": 1}


def test_the_rigs_curves_elsewhere_need_the_brand_to_declare_them():
    move = entry("dot", "x", 0, 15, 0, 30, ease="sine", fn=curve("sway"), law=LAW_WITHOUT_SWAY)
    s = M.check_eases(data([move], law=LAW_WITHOUT_SWAY))
    assert s.status == "FAIL" and "only rig parts use" in s.summary and "motion.extra_curves" in s.summary
    declared = {**LAW_WITHOUT_SWAY, "extra_curves": ["sway"]}
    assert M.check_eases(data([move], law=declared)).status == "PASS"


def test_a_carry_may_use_the_even_travel_profile_and_must_follow_it():
    carry = entry("card", "x", 0, 36, 0, 900, ease="even", fn=even, **cls("carry"))
    s = M.check_eases(data([carry]))
    assert s.status == "PASS", s.summary
    assert s.details["extra_curves"] == {"even": 1}
    wrong = entry("card", "x", 0, 36, 0, 900, ease="even", fn=curve("move"), **cls("carry"))
    s = M.check_eases(data([wrong]))
    assert s.status == "FAIL" and "doesn't follow 'even'" in s.summary
    ui = entry("label", "x", 0, 36, 0, 900, ease="even", fn=even)
    s = M.check_eases(data([ui]))
    assert s.status == "FAIL" and "only carries use" in s.summary
    assert M.check_eases(data([ui], law={**NEUTRAL_LAW, "extra_curves": {"even": "long type carries"}})).status \
        == "PASS"


def test_a_brand_ease_of_the_same_name_wins_over_a_generic_curve():
    own = {**NEUTRAL_LAW, "eases": {**NEUTRAL_LAW["eases"], "land": {"bezier": [0.22, 1, 0.36, 1]}}}
    move = entry("card", "y", 0, 15, 40, 0, ease="land", fn=curve("enter"), law=own)
    assert M.check_eases(data([move], law=own)).status == "PASS"  # the brand's "land", not the rig's


def test_an_unknown_extra_curve_in_the_brand_is_a_note():
    parsed = M.law_from({**NEUTRAL_LAW, "extra_curves": ["wobble"]}, source="t", fps=30)
    assert any("'wobble'" in n for n in parsed.notes) and not parsed.extra_declared


def test_a_measured_rig_move_is_fitted_to_the_rigs_curves():
    frames = list(range(0, 21))
    sway = curve("sway")
    e = {"id": "tile", "shot": "S1", "prop": "spread", "ease": "measured", "frame_in": 0, "frame_out": 20,
         "from": 0, "to": 0.2, "samples": [[f, 0.2 * sway(f / 20)] for f in frames], "entrance": False,
         "loop_period": None, "rig": "mark"}
    d = data([], law=LAW_WITHOUT_SWAY)
    moves = M.split_measured(M.move_from(e, 1, 0))
    for m in moves:
        M.fit_measured(m, d.law)
    assert [m.fitted for m in moves] == ["sway"]


# ---------------------------------------------------------------------------------------------------- exemptions
def test_a_declared_reading_sweep_runs_linear_off_the_scale_and_outside_the_stagger_caps():
    lin = lambda u: u  # noqa: E731
    sweep = [entry(f"row{i}", "x", 2 * i, 2 * i + 50, 0, 600, ease="linear", fn=lin, exempt="reading_sweep",
                   reason="the line reads each row as it passes", entrance=True) for i in range(12)]
    d = data(sweep, shots={"S1": (0.0, 120.0)})
    eases = M.check_eases(d)
    assert eases.status == "PASS", eases.summary
    assert eases.details["linear_exceptions"] == {"a reading sweep (declared)": 12}
    durations = M.check_durations(d)
    assert durations is not None and durations.status == "PASS"
    assert durations.details["exempt"] == {"reading sweep": 12}
    assert M.check_staggers(d) is None  # time-based, not a stagger
    # the same rows without the declaration: linear transforms, off the scale, and a stagger past its caps
    plain = data([{k: v for k, v in e.items() if k not in ("exempt", "reason")} for e in sweep],
                 shots={"S1": (0.0, 120.0)})
    assert M.check_eases(plain).status == "FAIL"
    assert M.check_staggers(plain).status == "FAIL"


def test_a_line_whose_steadiness_is_the_meaning_may_run_linear():
    lin = lambda u: u  # noqa: E731
    line = entry("durable", "x", 10, 70, 0, 900, ease="linear", fn=lin, exempt="steady",
                 reason="it draws at a constant rate while the other races and breaks")
    s = M.check_eases(data([line], shots={"S1": (0.0, 120.0)}))
    assert s.status == "PASS" and s.details["linear_exceptions"] == {"steadiness is the meaning (declared)": 1}
    bare = {k: v for k, v in line.items() if k not in ("exempt", "reason")}
    assert M.check_eases(data([bare], shots={"S1": (0.0, 120.0)})).status == "FAIL"


def test_unknown_classes_and_exemptions_are_trace_problems(slate_home, monkeypatch):
    from slate.qa import brandinfo

    monkeypatch.setattr(brandinfo, "load", lambda name: ({"motion": NEUTRAL_LAW}, "test brand"))
    film = make_film(slate_home["films"], traces={1: [
        entry("a", "x", 0, 15, 0, 30, **cls("sprite")),
        entry("b", "x", 0, 15, 0, 30, exempt="because"),
        entry("c", "x", 0, 15, 0, 30, **cls("Camera"), exempt="Reading_Sweep")]})
    result = M.evaluate(film, pixels=False)
    trace = next(c for c in result.details["checks"] if c["check"] == "trace")
    problems = " | ".join(trace["problems"])
    assert trace["status"] == "FAIL"
    assert "class 'sprite', which isn't one of the element classes" in problems
    assert "exempt 'because', which isn't one of the law's exemptions" in problems
    moves = M.load_data(film).moves
    assert [(m.id, m.cls, m.exempt) for m in moves] == [("c", "carry", "reading_sweep")]  # spellings are forgiven


def test_the_gate_reports_limits_by_class(slate_home, monkeypatch):
    from slate.qa import brandinfo

    monkeypatch.setattr(brandinfo, "load", lambda name: ({"motion": NEUTRAL_LAW}, "test brand"))
    film = make_film(slate_home["films"], size=HD, acts={1: (0, 60)},
                     traces={1: [glide_carry("page", 136, **cls("carry"), shot="S1")]})
    result = M.evaluate(film, pixels=False)
    speed = next(c for c in result.details["checks"] if c["check"] == "speed")
    assert speed["status"] == "PASS" and "carry 170 and 45.3" in speed["summary"]
    assert set(speed["class_limits"]) == {"rig", "ui", "carry", "whip"}
    json.dumps(result.to_dict())  # the details stay JSON


def test_an_even_drift_longer_than_the_scale_is_a_drift_and_a_shorter_one_keeps_to_the_scale():
    # the neutral law's longest token is 1.2 s (36 frames at 30 fps)
    drift = entry("camera", "x", 0, 96, 0, 300, ease="even", fn=even)  # 3.2 s, the films' slow camera settle
    s = M.check_durations(data([drift]))
    assert s.status == "PASS" and s.details["exempt"] == {"drift (even, longer than the scale)": 1}
    off_scale = entry("camera", "x", 0, 20, 0, 300, ease="even", fn=even)  # 0.67 s: between tokens
    assert M.check_durations(data([off_scale])).status == "FAIL"
    on_scale = entry("camera", "x", 0, 24, 0, 300, ease="even", fn=even)  # 0.8 s: the "l" token
    assert M.check_durations(data([on_scale])).status == "PASS"
    label = entry("label", "x", 0, 96, 0, 300, ease="even", fn=even)  # a UI element may not use even undeclared
    assert M.check_durations(data([label])).status == "FAIL"


def test_a_rotation_traced_in_radians_is_held_to_the_same_limits_as_degrees():
    """three.js records rotation in radians (the web template's turn): read as degrees, a whole radian a frame looked
    like one degree and passed the edge-speed limit 57 times over."""
    size = {"size": [200, 200], **cls("ui")}
    deg = entry("turn-deg", "rotation", 0, 12, 0, 360, ease="move", unit="deg", **size)
    rad = entry("turn-rad", "rotation.y", 0, 12, 0, 2 * math.pi, ease="move", unit="rad", **size)
    slow_rad = entry("nudge-rad", "rotation.y", 0, 24, 0, 0.02, ease="move", unit="rad", **size)
    assert M.check_speed(data([deg], size=HD)).status == "FAIL"
    assert M.check_speed(data([rad], size=HD)).status == "FAIL"  # the same turn in radians fails the same way
    assert M.check_speed(data([slow_rad], size=HD)).status == "PASS"
    moves = [M.move_from(e, 1, i) for i, e in enumerate([deg, rad])]
    assert np.allclose(moves[0].values[-1], moves[1].values[-1]) and np.isclose(moves[1].end[0], 360.0)
