"""How type arrives: a hero line never enters on opacity alone (L-motion-D048), and type never writes on while the
camera travels (L-motion-D140). Pure checks on trace entries sampled from the neutral law's curves."""

from __future__ import annotations

from qa_motion_helpers import data, entry
from slate.qa import motion as M

SIZE = (1920, 1080)


# ---------------------------------------------------------------------------------------------------- hero entrances
def test_a_hero_line_fading_in_alone_fails():
    fade = entry("headline", "opacity", 0, 15, 0.0, 1.0, ease="enter", entrance=True, role="hero")
    s = M.check_entrances(data([fade], size=SIZE))
    assert s.status == "FAIL", s.summary
    assert "opacity alone" in s.summary and "16 px" in s.details["violations"][0]["what"]


def test_a_hero_line_that_also_rises_16_px_passes():
    fade = entry("headline", "opacity", 0, 15, 0.0, 1.0, ease="enter", entrance=True, role="hero")
    rise = entry("headline", "y", 0, 15, 16.0, 0.0, ease="enter", entrance=True, role="hero")
    s = M.check_entrances(data([fade, rise], size=SIZE))
    assert s.status == "PASS", s.summary
    assert s.details["hero_entrances"] == 1 and "hero line moves or unmasks" in s.summary


def test_the_16_px_scale_with_the_film_and_an_unmask_counts():
    fade = entry("headline", "opacity", 0, 15, 0.0, 1.0, ease="enter", entrance=True, role="hero")
    small_rise = entry("headline", "y", 0, 15, 8.0, 0.0, ease="enter", entrance=True)
    assert M.check_entrances(data([fade, small_rise], size=(960, 540))).status == "PASS"  # 8 px at 540 lines
    assert M.check_entrances(data([fade, small_rise], size=SIZE)).status == "FAIL"  # 8 px at 1080 lines
    unmask = entry("headline", "clip.inset", 0, 15, 1.0, 0.0, ease="enter")
    assert M.check_entrances(data([fade, unmask], size=SIZE)).status == "PASS"


def test_a_secondary_label_may_fade_in_alone():
    fade = entry("label", "opacity", 0, 15, 0.0, 1.0, ease="enter", entrance=True, role="label")
    assert M.check_entrances(data([fade], size=SIZE)) is None  # no scaled or hero entrance: nothing to judge


def test_a_stated_exception_goes_to_a_person():
    fade = entry("headline", "opacity", 0, 15, 0.0, 1.0, ease="enter", entrance=True, role="hero",
                 exception="the line is the logo's own reveal")
    s = M.check_entrances(data([fade], size=SIZE))
    assert s.status == "REVIEW"


# ---------------------------------------------------------------------------------------------------- settled
def _camera(until: int, speed: float) -> dict:
    """The camera carried at `speed` px a frame from frame 0 to `until`, then at rest (linear travel)."""
    return entry("camera", "x", 0, until, 0.0, -speed * until, ease="linear", fn=lambda u: u)


def test_a_write_on_during_a_21_px_a_frame_camera_move_fails():
    write = entry("station", "chars", 10, 30, 0.0, 1.0, ease="enter", entrance=True, role="body")
    s = M.check_settled(data([_camera(40, 21.0), write], size=SIZE))
    assert s.status == "FAIL", s.summary
    v = s.details["violations"][0]["what"]
    assert "21 px/frame" in v and "camera carry" in v and "frames 10–30" in v


def test_a_write_on_after_the_camera_rests_passes():
    write = entry("station", "chars", 45, 65, 0.0, 1.0, ease="enter", entrance=True, text="The next station")
    s = M.check_settled(data([_camera(40, 21.0), write], size=SIZE))
    assert s.status == "PASS", s.summary


def test_a_slow_drift_under_2_px_a_frame_lets_type_arrive():
    write = entry("station", "opacity", 10, 30, 0.0, 1.0, ease="enter", entrance=True, role="h2")
    assert M.check_settled(data([_camera(40, 1.5), write], size=SIZE)).status == "PASS"
    assert M.check_settled(data([_camera(40, 1.5), write], size=(960, 540))).status == "FAIL"  # 1.5 > 1 px at 540


def test_a_page_carry_counts_and_type_on_its_own_page_doesnt():
    page = entry("page", "x", 0, 40, 0.0, -400.0, ease="linear", fn=lambda u: u, **{"class": "carry"})
    write = entry("station", "chars", 10, 30, 0.0, 1.0, ease="enter", entrance=True, role="body")
    assert M.check_settled(data([page, write], size=SIZE)).status == "FAIL"
    own = entry("page", "chars", 10, 30, 0.0, 1.0, ease="enter", entrance=True, role="body")
    assert M.check_settled(data([page, own], size=SIZE)).status == "PASS"


def test_no_type_arriving_leaves_the_check_out():
    assert M.check_settled(data([_camera(40, 21.0)], size=SIZE)) is None


def test_the_trace_schema_names_the_type_keys_the_kit_writes():
    """The kit's defineMotion (and the web runtime's tween) write a text element's role and text on its trace row;
    the motion trace schema names both, so a trace that carries them is valid and one with an empty role isn't."""
    from slate import jsonio

    row = {"id": "title.rise", "shot": "S01", "prop": "y", "ease": "enter", "frame_in": 0, "frame_out": 12,
           "from": 24, "to": 0, "samples": [[0, 24], [12, 0]], "entrance": True, "loop_period": None, "rig": None,
           "role": "hero", "text": "Every frame holds"}
    assert jsonio.validate([row], "motion_trace") == []
    assert jsonio.validate([{**row, "role": ""}], "motion_trace")
    schema = jsonio.schema("motion_trace")["items"]["properties"]
    assert {"role", "text"} <= set(schema)
