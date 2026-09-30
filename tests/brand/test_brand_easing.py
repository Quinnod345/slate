"""The product's own motion values as named eases (CSS easing tokens, SwiftUI springs), and colours and contrast."""

from __future__ import annotations

import math

import numpy as np
import pytest

from slate.brand import colour, easing
from slate.motion import eases as kit


# ---------------------------------------------------------------------------------------------------- CSS easing
def test_css_cubic_bezier_is_taken_exactly():
    assert easing.css_easing("cubic-bezier(0.16, 1, 0.3, 1)")["bezier"] == [0.16, 1.0, 0.3, 1.0]
    assert easing.css_easing(" CUBIC-BEZIER( .2 , .8 ,.2, 1 ) ")["bezier"] == [0.2, 0.8, 0.2, 1.0]
    assert easing.css_easing("cubic-bezier(0.3, -0.4, 0.7, 1.4)")["bezier"] == [0.3, -0.4, 0.7, 1.4]  # y may overshoot


def test_css_keywords_are_the_w3c_curves():
    assert easing.css_easing("ease")["bezier"] == [0.25, 0.1, 0.25, 1.0]
    assert easing.css_easing("ease-in")["bezier"] == [0.42, 0.0, 1.0, 1.0]
    assert easing.css_easing("ease-out")["bezier"] == [0.0, 0.0, 0.58, 1.0]
    assert easing.css_easing("ease-in-out")["bezier"] == [0.42, 0.0, 0.58, 1.0]
    assert easing.css_easing("linear") == {"kind": "linear", "css": "linear"}


@pytest.mark.parametrize("bad", ["cubic-bezier(1.2, 0, 0.3, 1)", "cubic-bezier(0.2, 0, -0.1, 1)",
                                 "cubic-bezier(0.2, 0, 0.3)", "cubic-bezier(a, b, c, d)", "bounce", "var(--x)"])
def test_unreadable_css_easing_is_refused_with_a_reason(bad):
    with pytest.raises(easing.EasingError):
        easing.css_easing(bad)


def test_css_steps_are_reported_not_guessed():
    assert easing.css_easing("steps(4, jump-end)") == {"kind": "steps", "steps": 4, "position": "jump-end",
                                                       "css": "steps(4, jump-end)"}
    with pytest.raises(easing.EasingError, match="stepped"):
        easing.ease_from_css("steps(4)", name="--x", where="a.css:1")


def test_css_ease_carries_its_lineage_and_the_kit_reads_it():
    e = easing.ease_from_css("cubic-bezier(0.22, 1, 0.36, 1)", name="--ease-out-soft", where="src/tokens.css:6")
    assert e["bezier"] == [0.22, 1.0, 0.36, 1.0]
    assert "--ease-out-soft" in e["lineage"] and "src/tokens.css:6" in e["lineage"]
    spec = kit.parse(e)
    assert spec.kind == "bezier" and kit.evaluate(spec, 0.5) == pytest.approx(kit.bezier_at(e["bezier"], 0.5))


def test_css_linear_points_are_filled_in_and_fitted():
    pts = easing.css_easing("linear(0, 0.25, 1)")["points"]
    assert pts == [[0.0, 0.0], [0.5, 0.25], [1.0, 1.0]]  # a stop without a position sits halfway between neighbours
    pts = easing.css_easing("linear(0, 0.3 20%, 0.9 50%, 1.04 70%, 1)")["points"]
    assert pts[1] == [0.2, 0.3] and pts[-1] == [1.0, 1.0]
    e = easing.ease_from_css("linear(0, 0.3 20%, 0.9 50%, 1.04 70%, 1)", name="--ease-spring", where="t.css:9")
    assert e["fit_rms"] < 0.03 and e["peak"] > 1.0 and "fitted" in e["lineage"]
    assert kit.parse(e) is not None


def test_css_time():
    assert easing.css_time("220ms") == pytest.approx(0.22)
    assert easing.css_time(".5s") == 0.5 and easing.css_time("0.3S") == 0.3
    assert easing.css_time("calc(2 * 100ms)") is None and easing.css_time("12px") is None


# ------------------------------------------------------------------------------------------------ SwiftUI springs
def test_spring_physics_from_each_way_swiftui_writes_it():
    s = easing.Spring.from_response(0.5, 0.8)
    assert s.omega0 == pytest.approx(2 * math.pi / 0.5) and s.zeta == 0.8
    assert s.stiffness == pytest.approx((2 * math.pi / 0.5) ** 2) and s.damping == pytest.approx(4 * math.pi * 0.8 / 0.5)
    b = easing.Spring.from_duration(0.5, 0.2)  # SwiftUI: ζ = 1 − bounce
    assert b.zeta == pytest.approx(0.8) and b.response == pytest.approx(0.5)
    o = easing.Spring.from_duration(0.5, -0.5)  # below zero: ζ = 1 / (1 + bounce), overdamped
    assert o.zeta == pytest.approx(2.0)
    p = easing.Spring.from_physics(mass=1.0, stiffness=170.0, damping=15.0)
    assert p.omega0 == pytest.approx(math.sqrt(170)) and p.zeta == pytest.approx(15 / (2 * math.sqrt(170)))
    set_ = easing.Spring.from_settling(0.6, 1.0)
    assert set_.settle_time() == pytest.approx(0.6, rel=1e-6)


def test_spring_step_response_starts_at_rest_and_settles_at_one():
    for zeta in (0.5, 1.0, 1.6):
        s = easing.Spring.from_response(0.4, zeta)
        assert float(s.position(0.0)) == pytest.approx(0.0, abs=1e-12)
        T = s.settle_time()
        tail = s.position(np.linspace(T, T * 3, 400))
        assert np.all(np.abs(tail - 1) <= easing.SETTLE + 1e-9)
        v0 = (float(s.position(1e-6)) - float(s.position(0.0))) / 1e-6
        assert abs(v0) < 1e-2  # starts from rest
    assert easing.Spring.from_response(0.4, 0.5).peak() > 1.1
    assert easing.Spring.from_response(0.4, 1.0).peak() == 1.0


def test_a_critically_damped_spring_is_exactly_the_kits_spring0():
    e = easing.swift_animation(".spring(response: 0.5, dampingFraction: 1.0)", where="Views/Card.swift:12")
    assert e["kind"] == "spring0" and e["fit_rms"] == 0.0 and e["peak"] == 1.0
    s = easing.Spring.from_response(0.5, 1.0)
    T = s.settle_time()
    assert e["duration_s"] == pytest.approx(T, abs=1e-4)
    spec = kit.parse(e)
    assert spec.kind == "spring0"
    for u in np.linspace(0.0, 1.0, 41):
        assert kit.evaluate(spec, float(u)) == pytest.approx(float(s.position(u * T) / s.position(T)), abs=1e-5)
    assert "Views/Card.swift:12" in e["lineage"] and e["spring"]["damping_fraction"] == 1.0


def test_a_ringing_spring_is_fitted_and_keeps_its_peak_and_error():
    e = easing.swift_animation(".spring(response: 0.4, dampingFraction: 0.7)", where="V.swift:2")
    assert "bezier" in e and 0 < e["fit_rms"] < 0.03
    assert e["spring"]["peak"] == pytest.approx(easing.Spring.from_response(0.4, 0.7).peak(), abs=1e-4)
    assert 1.0 < e["peak"] <= e["spring"]["peak"] + 1e-6  # the drawn curve's own peak
    assert "rings" in e["lineage"] and kit.parse(e).kind == "bezier"


def test_swiftui_curves_and_presets():
    tc = easing.swift_animation(".timingCurve(0.2, 0.8, 0.2, 1, duration: 0.4)", where="a:1")
    assert tc["bezier"] == [0.2, 0.8, 0.2, 1.0] and tc["duration_s"] == 0.4
    eo = easing.swift_animation("Animation.easeOut(duration: 0.25)", where="a:2")
    assert eo["bezier"] == [0.0, 0.0, 0.58, 1.0] and eo["duration_s"] == 0.25 and "assumed" not in eo
    bare = easing.swift_animation(".easeInOut", where="a:3")
    assert bare["assumed"] == {"duration": 0.35} and "default" in bare["lineage"]
    snappy = easing.swift_animation(".snappy", where="a:4")
    assert snappy["spring"]["damping_fraction"] == pytest.approx(0.85) and snappy["assumed"]["bounce"] == 0.15
    bouncy = easing.swift_animation(".bouncy(extraBounce: 0.1)", where="a:5")
    assert bouncy["spring"]["damping_fraction"] == pytest.approx(0.6)
    typed = easing.swift_animation("Spring(duration: 0.5, bounce: 0.2)", where="a:6")
    assert typed["spring"]["damping_fraction"] == pytest.approx(0.8) and "assumed" not in typed
    interp = easing.swift_animation(".interpolatingSpring(stiffness: 170, damping: 15)", where="a:7")
    assert interp["spring"]["stiffness"] == pytest.approx(170.0) and interp["assumed"] == {"mass": 1.0}
    default = easing.swift_animation(".spring()", where="a:8")
    assert set(default["assumed"]) == {"response", "dampingFraction"}


def test_springs_that_cant_be_eases_are_refused():
    with pytest.raises(easing.EasingError, match="never settles"):
        easing.Spring.from_response(0.5, 0.0)
    with pytest.raises(easing.EasingError):
        easing.Spring.from_duration(0.5, 1.0)
    with pytest.raises(easing.EasingError):
        easing.swift_animation(".bounceForever(3)", where="a:1")


# --------------------------------------------------------------------------------------------- colour, contrast
def test_colours_in_every_spelling_become_hex():
    assert colour.normalise("#abc") == "#AABBCC"
    assert colour.normalise("#00aacc80") == "#00AACC80"
    assert colour.normalise("rgb(0 170 204 / 80%)") == "#00AACCCC"
    assert colour.normalise("rgba(0, 170, 204, 0.5)") == "#00AACC80"
    assert colour.normalise("hsl(200, 50%, 40%)") == "#337799"
    assert colour.normalise("oklch(62.8% 0.2577 29.23)") == "#FF0000"  # CSS red, written in oklch
    assert colour.normalise([0.1, 0.2, 0.3]) == "#1A334D"
    assert colour.normalise("teal") is None and colour.normalise("var(--x)") is None


def test_contrast_is_wcag_and_agrees_with_the_accessibility_gate():
    from slate.qa import access

    assert colour.ratio(colour.parse("#000"), colour.parse("#FFF")) == pytest.approx(21.0)
    assert colour.ratio(colour.parse("#777777"), colour.parse("#FFFFFF")) == pytest.approx(4.48, abs=0.01)
    for fg, bg in (("#1C1A17", "#F3F0EA"), ("#A4532F", "#F3F0EA"), ("#FFFFFF80", "#336699"), ("#222", "#FFFFFF33")):
        ours = colour.ratio(colour.parse(fg), colour.parse(bg))
        theirs = access.pair_ratio(access.parse_colour(fg), access.parse_colour(bg))
        assert ours == pytest.approx(theirs, rel=1e-9), (fg, bg)
