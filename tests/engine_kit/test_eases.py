"""The motion law's Python mirror, checked against the shared vectors the kit (vitest) and the AE runner also check."""

from __future__ import annotations

import json
import math

import pytest

from slate import paths
from slate.motion import eases as E
from slate.motion import vectors as V

VEC = paths.REPO / "tests" / "vectors"


def _spec(d: dict) -> E.EaseSpec:
    spec = E.parse(d["bezier"] if d["kind"] == "bezier" else ({"kind": "spring0", "stiffness": d["stiffness"]} if d["kind"] == "spring0" else d["kind"]))
    assert spec is not None
    return spec


def test_vectors_match_every_ease_sample():
    doc = json.loads((VEC / "eases.json").read_text())
    tol = doc["tolerance"]
    assert len(doc["eases"]) >= 10
    for e in doc["eases"]:
        spec = _spec(e["spec"])
        for u, y in e["samples"]:
            assert abs(E.evaluate(spec, u) - y) <= tol, (e["name"], u)


def test_vectors_match_every_t90_and_landing():
    doc = json.loads((VEC / "t90.json").read_text())
    tol = doc["tolerance"]
    for e in doc["t90"]:
        spec = _spec(e["spec"])
        assert abs(E.t90(spec) - e["t90"]) <= tol, e["name"]
        assert abs(E.t_at(spec, 0.5) - e["t50"]) <= tol, e["name"]
        assert abs(E.t_at(spec, 0.99) - e["t99"]) <= tol, e["name"]
    for l in doc["start_for"]:
        assert abs(E.start_for(l["land"], l["dur"], _spec(l["spec"])) - l["start"]) <= tol


def test_vectors_match_ae_and_splines():
    doc = json.loads((VEC / "eases.json").read_text())
    for e in doc["eases"]:
        if "ae" in e:
            got = E.ae_keyframe_ease(e["spec"]["bezier"])
            for side in ("out", "in"):
                assert got[side]["influence"] == pytest.approx(e["ae"][side]["influence"], abs=1e-9)
                assert got[side]["speed"] == pytest.approx(e["ae"][side]["speed"], abs=1e-9)
    for s in doc["splines"]:
        for t, v in s["samples"]:
            assert abs(E.mono_spline(t, s["keys"]) - v) <= 1e-12, (s["name"], t)


def test_vectors_are_fresh():
    """The committed vectors are exactly what the code computes (regenerate with --write when the maths changes)."""
    assert V.main(["--check"]) == 0


@pytest.mark.parametrize("raw", [{"kind": "linear"}, "sine", {"kind": "spring0", "stiffness": 7}, [0.2, 0.7, 0.2, 1], [0.34, 1.56, 0.64, 1], [1, 0, 0, 1]])
def test_exact_endpoints(raw):
    spec = E.parse(raw)
    assert spec is not None
    for u in (-10.0, -1e-12, 0.0, float("nan")):
        assert E.evaluate(spec, u) == 0.0
    for u in (1.0, 1 + 1e-12, 10.0):
        assert E.evaluate(spec, u) == 1.0


def test_parse_refuses_curves_that_are_not_functions_of_time():
    assert E.parse([1.2, 0, 0.5, 1]) is None
    assert E.parse([0.2, 0, -0.1, 1]) is None
    assert E.parse("bouncy") is None
    assert E.parse("cubic-bezier(0.1, 0.2, 0.3, 0.4)") == E.EaseSpec("bezier", (0.1, 0.2, 0.3, 0.4))
    assert E.parse({"cubic_bezier": "0,0,1,1"}) == E.EaseSpec("bezier", (0.0, 0.0, 1.0, 1.0))


def test_t90_of_the_builtins():
    assert E.t90(E.EaseSpec("linear")) == pytest.approx(0.9, abs=1e-12)
    # the sine in-out reaches 90% at 0.795 of its duration
    assert E.t90(E.EaseSpec("sine")) == pytest.approx(math.acos(1 - 2 * 0.9) / math.pi, abs=1e-12)
    # an overshooting curve lands on its first crossing, well before its end
    assert E.t90(E.parse([0.34, 1.56, 0.64, 1])) < 0.35


def test_ae_speed_and_influence_follow_the_handles():
    # a collapsed in-handle takes AE's minimum influence and the curve's real end tangent
    got = E.ae_keyframe_ease([0.42, 0, 1, 1])
    assert got["in"]["influence"] == E.AE_MIN_INFLUENCE
    assert got["in"]["speed"] == pytest.approx(1 / 0.58)
    assert got["out"] == {"influence": pytest.approx(42.0), "speed": 0.0}


def test_mono_spline_never_backtracks():
    """The films' camera once drifted to −274 px between two marks on a Catmull-Rom; the monotone spline can't."""
    keys = [[0, 0], [0.2, 100], [3, 110], [3.2, 300]]
    prev = -1.0
    for i in range(3201):
        t = i / 1000
        v = E.mono_spline(t, keys)
        assert v >= prev - 1e-9
        prev = v
    assert E.mono_spline(0.2, keys) == pytest.approx(100)
    assert E.mono_spline(-5, keys) == 0 and E.mono_spline(50, keys) == 300


def test_law_from_a_pack_and_its_caps():
    motion = {
        "eases": {"enter": [0.2, 0.7, 0.2, 1], "move": "sine", "leave": [0.5, 0, 1, 1]},
        "durations": {"tick": 0.2, "base": 0.52, "broad": 0.8},
        "staggers": {"char": {"frames": 2}, "word": 0.1},
        "roles": {"entrance": "enter", "exit": "leave", "move": "nope"},
    }
    law = E.law_from(motion, fps=25)
    assert list(law.eases) == ["enter", "move", "leave"]
    assert law.staggers["char"] == pytest.approx(0.08)
    # a role naming an unknown ease is refused and guessed from the names instead, as the kit does
    assert law.roles == {"entrance": "enter", "move": "move", "state": "enter", "exit": "leave"}
    assert any("'nope'" in p for p in law.problems)
    guessed = E.law_from({"eases": {"arrive": [0.2, 0.7, 0.2, 1], "glide": "sine", "snap": [0.8, 0, 0.2, 1], "exit": [0.5, 0, 1, 1]}})
    assert guessed.roles == {"entrance": "arrive", "move": "glide", "state": "snap", "exit": "exit"}
    assert law.stagger_step("char", 20) == pytest.approx(0.6 / 19)
    assert law.stagger_step("char", 20, entrance_dur=0.52) == pytest.approx(0.26 / 19)
    assert law.dur("base") == 0.52
    with pytest.raises(KeyError):
        law.dur("forever")
    with pytest.raises(KeyError):
        law.ease("wobble")
    neutral = E.law_from(None)
    assert neutral.neutral and neutral.problems


def test_lands_on_whole_frames_with_pythons_rounding():
    law = E.law_from({"eases": {"enter": [0.2, 0.7, 0.2, 1]}, "durations": {"base": 0.52}})
    spec = law.ease("enter")
    for fps in (25.0, 30000 / 1001, 24.0):
        f_in, f_out = E.lands_on(law, 7.3, "base", "enter", fps)
        assert f_in == int(round(E.start_for(7.3, 0.52, spec) * fps))
        assert f_out - f_in == int(round(0.52 * fps))
        # the move is 90% of the way within a frame of its beat
        land_frame = 7.3 * fps
        assert abs((f_in + E.t90(spec) * (f_out - f_in)) - land_frame) <= 1.0
