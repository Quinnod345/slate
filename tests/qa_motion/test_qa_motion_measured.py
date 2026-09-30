"""Traces measured in After Effects: slate.ae.acts.trace_from_samples samples every animated property from its first
key to its last and names no ease ("measured"). The motion gate splits each entry into its moves at their rests and
judges each by the brand ease its samples follow, by shape."""

from __future__ import annotations

import numpy as np
import pytest

from qa_motion_helpers import NEUTRAL_LAW, curve, data, make_film, status
from slate.motion.eases import evaluate, parse
from slate.qa import motion as M

acts_mod = pytest.importorskip("slate.ae.acts")

ACT = {"index": 1, "frame_in": 0, "frame_out": 90, "shots": ["S1"]}
SHOTS = {"S1": {"id": "S1", "frame_in": 0, "frame_out": 90}}


def bezier(*b):
    spec = parse({"bezier": list(b)})
    return lambda u: evaluate(spec, float(u))


def keyed(segments, *, hold_before=3, hold_between=6):
    """AE-style samples, one per frame: a hold, then each (from, to, frames, ease) move with holds between."""
    values: list = []
    values += [segments[0][0]] * hold_before
    for i, (a, b, n, fn) in enumerate(segments):
        for k in range(n + 1):
            p = fn(k / n)
            values.append([x + (y - x) * p for x, y in zip(a, b)] if isinstance(a, list) else a + (b - a) * p)
        if i < len(segments) - 1:
            values += [values[-1]] * hold_between
    values += [values[-1]] * 3
    return [[f, v] for f, v in enumerate(values)]


def raw(prop, match_name, samples, layer="card"):
    return {"layer": layer, "prop": prop, "match_name": match_name, "samples": samples}


def measured(raws, law=NEUTRAL_LAW, **kw):
    """MotionData from AE's own trace export, split and fitted the way load_data does it."""
    entries = acts_mod.trace_from_samples(raws, ACT, SHOTS)
    assert entries and all(e["ease"] == M.MEASURED for e in entries)
    d = data([], law=law, **kw)
    for i, e in enumerate(entries):
        d.moves.extend(M.split_measured(M.move_from(e, 1, i)))
    for m in d.moves:
        M.fit_measured(m, d.law)
    return d


def test_rest_segments_split_at_holds():
    values = np.array([[0], [0], [1], [3], [4], [4], [4], [6], [9], [9], [9], [9]], dtype=float)
    assert M.rest_segments(np.arange(12.0), values) == [(1, 4), (6, 8)]
    assert M.rest_segments(np.arange(12.0), np.zeros((12, 1))) == []


def test_keyed_moves_are_judged_one_by_one_on_the_ease_they_follow():
    samples = keyed([([40, 300], [40, 280], 15, curve("move")), ([40, 280], [40, 270], 9, curve("state"))])
    d = measured([raw("Position", "ADBE Position", samples)])
    assert [(m.frame_in, m.frame_out, m.fitted) for m in d.moves] == [(3.0, 18.0, "move"), (25.0, 34.0, "state")]
    assert all(m.fit_dev < 1e-6 for m in d.moves)
    assert status(M.check_eases(d)) == "PASS"
    assert status(M.check_durations(d)) == "PASS"  # 15 frames is m (0.5 s), 9 is s (0.3 s) at 30 fps


def test_a_curve_the_brand_does_not_have_fails_and_names_the_closest():
    samples = keyed([([100, 300], [140, 300], 15, bezier(0.25, 0.1, 0.25, 1))])  # CSS's default "ease"
    d = measured([raw("Position", "ADBE Position", samples)])
    s = M.check_eases(d)
    assert s.status == "FAIL" and "was measured from its render and follows none of the brand's eases" in s.summary
    m = d.moves[0]
    assert m.fitted is None and m.fit_best and m.fit_dev > 0.1
    assert f"the closest, {m.fit_best!r}, is off by {m.fit_dev * 100:.1f}% of its travel" in s.summary


def test_after_effects_easy_ease_reads_as_a_sine_in_out():
    # AE's default easy ease (33% influence, speed 0) sits within 1% of the sine in-out the law calls "sway"
    samples = keyed([([100, 300], [140, 300], 15, bezier(0.333, 0, 0.667, 1))])
    d = measured([raw("Position", "ADBE Position", samples)])
    assert d.moves[0].fitted == "sway" and d.moves[0].fit_dev < 0.015


def test_measured_linear_keys_on_a_transform_break_the_linear_rule():
    samples = keyed([([100, 300], [130, 300], 15, lambda u: u)])
    d = measured([raw("Position", "ADBE Position", samples)], shots={"S1": (0.0, 90.0)})
    assert d.moves[0].fitted == "linear"
    s = M.check_eases(d)
    assert s.status == "FAIL" and "is linear on position" in s.summary
    # without the shots it can't be ruled out as a drift that outlasts its shot: a person judges
    assert M.check_eases(measured([raw("Position", "ADBE Position", samples)])).status == "REVIEW"


def test_a_measured_scale_up_from_zero_goes_to_a_person():
    samples = keyed([([0, 0], [100, 100], 15, curve("enter"))])
    d = measured([raw("Scale", "ADBE Scale", samples)])
    m = d.moves[0]
    assert m.kind == "scale" and m.entrance and m.fitted == "enter"
    s = M.check_entrances(d)
    assert s.status == "REVIEW" and "was measured scaling up from 0% of its size" in s.summary
    shrink = measured([raw("Scale", "ADBE Scale", keyed([([100, 100], [0, 0], 15, curve("leave"))]))])
    assert M.check_entrances(shrink) is None  # an exit isn't an entrance


def test_a_film_with_a_measured_trace_is_checked_like_any_act(slate_home, monkeypatch):
    import json

    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))
    samples = keyed([([40, 300], [40, 280], 15, curve("move")), ([40, 280], [40, 270], 15, curve("move"))])
    trace = acts_mod.trace_from_samples([raw("Position", "ADBE Position", samples)], ACT, SHOTS)
    film = make_film(slate_home["films"], size=(640, 360), acts={1: (0, 90)}, traces={1: trace})
    result = M.evaluate(film, pixels=False)
    checks = {c["check"]: c["status"] for c in result.details["checks"]}
    assert {k: checks[k] for k in ("trace", "eases", "durations", "speed", "loops")} == dict.fromkeys(
        ("trace", "eases", "durations", "speed", "loops"), "PASS")
    assert next(c for c in result.details["checks"] if c["check"] == "eases")["checked"] == 2  # two moves, one entry
    assert json.loads((film / "timing" / "motion" / "act1.json").read_text())[0]["ease"] == "measured"
