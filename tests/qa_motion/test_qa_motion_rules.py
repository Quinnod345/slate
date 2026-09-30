"""The motion law's rules on synthetic traces: each rule passes a clean trace and fails a broken one, and every motion
bug the films hit comes back as a failure here."""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest

from qa_motion_helpers import NEUTRAL_LAW, curve, data, entry, signal_entry, status
from slate.qa import motion as M


# ---------------------------------------------------------------------------------------------------- the law
def test_limits_are_the_law_at_1080p_and_scale_with_the_frame():
    assert M.limits(1920, 1080) == pytest.approx({"short_side": 1080, "speed": 30, "accel": 8, "crest_speed": 48,
                                                  "crest_travel": 570.0}, abs=0.1)
    assert M.limits(1280, 720)["speed"] == pytest.approx(20)
    assert M.limits(3840, 2160)["speed"] == pytest.approx(60) and M.limits(3840, 2160)["accel"] == pytest.approx(16)
    # a 9:16 reel is "1080p vertical": its short side sets the limit, so it keeps 30 px/frame (not 53)
    assert M.limits(1080, 1920)["speed"] == pytest.approx(30)


def test_loop_floor_is_16_frames_and_never_faster_than_1_9_hz():
    assert M.min_loop_period(30) == 16
    assert M.min_loop_period(24) == 16
    assert M.min_loop_period(60) == pytest.approx(60 / 1.9)
    assert M.min_loop_period(30, brand_frames=44) == 44  # a brand may ask for slower loops, never faster


def test_brand_law_is_read_the_way_the_kit_reads_it():
    law = M.law_from(NEUTRAL_LAW, source="t", fps=30)
    assert set(law.eases) == set(NEUTRAL_LAW["eases"])
    assert law.eases["enter"].role == "entrance" and law.eases["leave"].role == "exit"
    assert law.eases["enter"].fn_source == "slate.motion.eases.evaluate"
    assert law.eases["enter"].fn(0.0) == 0.0 and law.eases["enter"].fn(1.0) == 1.0
    assert law.durations["m"] == ("s", 0.5) and law.exit_factor == 0.75
    assert law.stagger_cap == ("s", 0.6) and law.stagger_cap_fraction == 0.5 and law.stagger_max_items == 8
    assert law.stagger_eases == {"enter", "leave", "state"}
    assert law.allowed_peak("pop") == pytest.approx(1.12) and law.allowed_peak("enter") == 1.0
    assert law.ease("entrance").name == "enter"  # a role names the brand's ease
    assert not law.neutral
    assert M.law_from({}, source="t").neutral  # no eases of its own: the kit's neutral law stands in


def test_prop_kinds():
    kinds = {p: M.prop_kind(p) for p in ("x", "translateX", "position.x", "camera.y", "position", "scale", "scale.x",
                                         "rotation", "turn", "opacity", "translateZ", "progress")}
    assert kinds == {"x": "x", "translateX": "x", "position.x": "x", "camera.y": "y", "position": "vector",
                     "scale": "scale", "scale.x": "scale", "rotation": "rotation", "turn": "rotation",
                     "opacity": "opacity", "translateZ": "depth", "progress": "other"}


# ---------------------------------------------------------------------------------------------------- eases
def test_eases_pass_when_every_move_uses_and_follows_a_brand_ease():
    d = data([entry("card", "y", 0, 15, 40, 0, ease="enter", entrance=True),
              entry("cam", "x", 0, 36, 0, 120, ease="move"),
              entry("chip", "opacity", 3, 9, 0, 1, ease="state")])
    s = M.check_eases(d)
    assert s.status == "PASS", s.summary
    assert s.details["worst_deviation"] < 1e-9


def test_eases_fail_on_a_name_the_brand_does_not_have():
    s = M.check_eases(data([entry("card", "y", 0, 15, 40, 0, ease="enter"),
                            entry("dot", "x", 0, 15, 0, 30, ease="bounceOut", fn=curve("enter"))]))
    assert s.status == "FAIL"
    assert "isn't one of the brand's eases" in s.summary and "bounceOut" in s.summary


def test_eases_fail_on_the_kits_builtin_curve_when_the_brand_does_not_name_it():
    s = M.check_eases(data([entry("dot", "x", 0, 15, 0, 30, ease="sine", fn=curve("sway"))]))
    assert s.status == "FAIL" and "'sine'" in s.summary


def test_eases_fail_when_samples_follow_another_curve_under_the_brands_name():
    # labelled "move" but actually linear: the AE-default-ease kind of mistake
    s = M.check_eases(data([entry("card", "x", 0, 24, 0, 100, ease="move", fn=lambda u: u)]))
    assert s.status == "FAIL" and "doesn't follow 'move'" in s.summary
    assert s.details["violations"][0]["value"] > 0.05


def test_overshoot_only_where_the_policy_allows_it_and_only_up_to_its_peak():
    pop = curve("pop")
    peak = max(pop(u) for u in np.linspace(0, 1, 2001))
    assert 1.05 < peak < 1.12
    assert M.check_eases(data([entry("card", "scale", 0, 15, 0.9, 1.0, ease="pop")])).status == "PASS"
    # the same overshoot under an ease the policy doesn't name
    s = M.check_eases(data([entry("card", "scale", 0, 15, 0.9, 1.0, ease="enter", fn=pop)]))
    assert s.status == "FAIL" and "overshoots" in s.summary
    # a tighter policy
    tight = {**NEUTRAL_LAW, "overshoot": {"allowed": ["pop"], "max": 0.05}}
    s = M.check_eases(data([entry("card", "scale", 0, 15, 0.9, 1.0, ease="pop", law=tight)], law=tight))
    assert s.status == "FAIL" and "past the 1.050" in s.summary
    # no policy at all: nothing may overshoot
    none = {k: v for k, v in NEUTRAL_LAW.items() if k != "overshoot"}
    s = M.check_eases(data([entry("card", "scale", 0, 15, 0.9, 1.0, ease="pop", law=none)], law=none))
    assert s.status == "FAIL" and "states no overshoot policy" in s.summary


def test_linear_only_for_drifts_time_and_spin():
    shots = {"S1": (0.0, 90.0)}
    lin = lambda u: u  # noqa: E731
    inside = entry("card", "x", 10, 40, 0, 60, ease="linear", fn=lin)
    assert M.check_eases(data([inside], shots=shots)).status == "FAIL"
    drift = entry("bg", "x", -10, 100, 0, 40, ease="linear", fn=lin)  # starts before the shot, ends after it
    progress = entry("bar", "progress", 10, 40, 0, 1, ease="linear", fn=lin)
    spin = entry("loader", "rotation", 0, 30, 0, 360, ease="linear", fn=lin, loop_period=30)
    s = M.check_eases(data([drift, progress, spin], shots=shots))
    assert s.status == "PASS", s.summary
    assert sum(s.details["linear_exceptions"].values()) == 3
    # linear opacity is fine only when the brand names linear as one of its eases
    fade = entry("label", "opacity", 0, 15, 0, 1, ease="linear", fn=lin)
    assert M.check_eases(data([fade], shots=shots)).status == "FAIL"
    with_linear = {**NEUTRAL_LAW, "eases": {**NEUTRAL_LAW["eases"], "linear": {"kind": "linear"}}}
    assert M.check_eases(data([fade], shots=shots, law=with_linear)).status == "PASS"
    # without shots.json a linear transform can't be proven to be a drift: a person decides
    assert M.check_eases(data([inside])).status == "REVIEW"


def test_a_stated_exception_turns_a_failure_into_a_review_item():
    e = entry("dot", "x", 0, 15, 0, 30, ease="bounceOut", fn=curve("enter"), exception="the brand's one wink")
    s = M.check_eases(data([e]))
    assert s.status == "REVIEW"
    item = s.details["review_items"][0]
    assert item["key"].startswith("eases:act1:dot.x") and "the brand's one wink" in item["what"]


def test_brand_rules_are_unchecked_without_the_brands_own_law():
    moves = [entry("card", "y", 0, 15, 40, 0, ease="enter")]
    no_law = data(moves, law=None)
    for check in (M.check_eases, M.check_durations):
        assert check(no_law).status == "UNCHECKED"
    neutral = data(moves, law={"durations": {"m": 0.5}})
    s = M.check_eases(neutral)
    assert s.status == "UNCHECKED" and "neutral law stood in" in s.summary


# ---------------------------------------------------------------------------------------------------- durations
def test_durations_on_the_scale_pass_and_off_it_fail():
    assert M.check_durations(data([entry("a", "y", 0, 15, 40, 0, ease="enter"),
                                   entry("b", "x", 0, 24, 0, 90, ease="move")])).status == "PASS"
    s = M.check_durations(data([entry("a", "y", 0, 12, 40, 0, ease="enter")]))  # 0.4 s: between s and m
    assert s.status == "FAIL" and "lasts 12 frames" in s.summary


def test_exits_run_at_the_exit_factor():
    # 0.5 s × 0.75 = 11.25 frames at 30 fps
    assert M.check_durations(data([entry("a", "y", 0, 11, 0, 40, ease="leave")])).status == "PASS"
    assert M.check_durations(data([entry("a", "y", 0, 11, 0, 40, ease="move")])).status == "FAIL"


def test_durations_follow_the_films_frame_rate():
    at24 = data([entry("a", "y", 0, 12, 40, 0, ease="enter")], fps=24)  # 0.5 s at 24 fps
    assert M.check_durations(at24).status == "PASS"
    assert M.check_durations(data([entry("a", "y", 0, 15, 40, 0, ease="enter")], fps=24)).status == "FAIL"


def test_a_token_capped_per_act():
    law = {**NEUTRAL_LAW, "durations": {**NEUTRAL_LAW["durations"], "xl": {"seconds": 1.2, "max_per_act": 1}}}
    one = [entry("cam", "x", 0, 36, 0, 100, ease="move", law=law)]
    assert M.check_durations(data(one, law=law)).status == "PASS"
    two = one + [entry("cam2", "x", 60, 96, 0, 100, ease="move", law=law)]
    s = M.check_durations(data(two, law=law))
    assert s.status == "FAIL" and "act 1 has 2 xl beats" in s.summary


def test_rig_loop_and_jump_moves_follow_their_own_timing():
    moves = [entry("tile0", "spread", 0, 14, 0, 0.16, ease="sway", rig="mark"),
             entry("glow", "opacity", 0, 44, 0.8, 1.0, ease="sway", loop_period=44)]
    s = M.check_durations(data(moves))
    assert s.status == "PASS" and s.details["exempt"] == {"rig": 1, "loop": 1}


# ---------------------------------------------------------------------------------------------------- staggers
def _group(n, step, dur, *, ease="enter", prop="y", start=0.0):
    return [entry(f"item{i}", prop, start + i * step, start + i * step + dur, 24, 0, ease=ease, entrance=True)
            for i in range(n)]


def test_a_stagger_within_the_caps_passes():
    s = M.check_staggers(data(_group(4, 2, 24)))
    assert s.status == "PASS", s.summary
    assert s.details["groups"][0]["spread_frames"] == 6


def test_regression_per_character_spread_without_a_cap():
    # the kit's per-character type had no 0.6 s cap: 20 characters 2 frames apart spread 1.27 s
    s = M.check_staggers(data(_group(20, 2, 15)))
    assert s.status == "FAIL"
    problems = " ".join(s.details["groups"][0]["problems"])
    assert "past the brand's 0.60 s cap" in problems
    assert "more than 0.5 of the group's own 15-frame move" in problems
    assert "staggers 20 items one by one" in problems


def test_a_spread_wider_than_half_the_groups_own_move_fails():
    s = M.check_staggers(data(_group(3, 4, 15)))  # 8 frames of spread on a 15-frame move
    assert s.status == "FAIL" and "more than 0.5" in s.summary


def test_moves_on_a_travel_ease_do_not_stagger():
    s = M.check_staggers(data(_group(3, 2, 24, ease="move", prop="x")))
    assert s.status == "FAIL" and "keeps together" in s.summary


def test_regression_stagger_steps_land_on_whole_frames():
    # 35 ms steps at 30 fps are 1.05 frames: no two items share a phase of the shutter
    s = M.check_staggers(data(_group(4, 1.05, 24)))
    assert s.status == "FAIL" and "whole frames" in s.summary


def test_stagger_caps_missing_are_unchecked_not_passed():
    d = data(_group(4, 2, 24))
    d.law.stagger_cap = d.law.stagger_cap_fraction = d.law.stagger_max_items = None
    assert M.check_staggers(d).status == "UNCHECKED"


def test_no_stagger_is_left_out():
    assert M.check_staggers(data([entry("a", "y", 0, 15, 40, 0, ease="enter")])) is None


# ---------------------------------------------------------------------------------------------------- entrances
def test_entrances_start_from_at_least_85_percent():
    assert M.check_entrances(data([entry("card", "scale", 0, 15, 0.9, 1.0, ease="enter", entrance=True)])).status \
        == "PASS"
    s = M.check_entrances(data([entry("card", "scale", 0, 15, 0.0, 1.0, ease="enter", entrance=True)]))
    assert s.status == "FAIL" and "enters from 0%" in s.summary  # the films' pop-in


def test_an_entrance_that_dips_below_85_percent_fails():
    dip = signal_entry("card", "scale", range(0, 16), [0.9, 0.7, 0.6] + [0.6 + i * 0.04 for i in range(1, 11)]
                       + [1.0, 1.0, 1.0], ease="enter", entrance=True)
    s = M.check_entrances(data([dip]))
    assert s.status == "FAIL" and "60%" in s.summary


def test_vector_scale_entrances_check_each_axis():
    s = M.check_entrances(data([entry("card", "scale", 0, 15, [0.8, 1.0], [1.0, 1.0], ease="enter",
                                     entrance=True)]))
    assert s.status == "FAIL"


def test_scale_changes_that_are_not_entrances_are_left_to_other_rules():
    assert M.check_entrances(data([entry("card", "scale", 0, 15, 1.0, 0.0, ease="leave")])) is None


# ---------------------------------------------------------------------------------------------------- speed
def test_regression_a_fast_carry_streaks():
    # the films' two-token carry peaked at 310 px/frame; limit 30 at 1080p
    s = M.check_speed(data([entry("carry", "x", 0, 10, 0, 1250, ease="move")], size=(1920, 1080)))
    assert s.status == "FAIL" and "px/frame" in s.summary
    assert s.details["fastest"]["px_per_frame"] > 300


def test_regression_a_move_that_starts_at_full_speed_breaks_the_acceleration_limit():
    fast_start = M.check_speed(data([entry("card", "y", 0, 16, 60, 0, ease="enter")], size=(1920, 1080)))
    assert fast_start.status == "FAIL"
    assert "px/frame²" in fast_start.summary and "leaves rest at full speed" in fast_start.summary
    at_rest = M.check_speed(data([entry("card", "y", 0, 16, 60, 0, ease="move")], size=(1920, 1080)))
    assert at_rest.status == "PASS", at_rest.summary


def test_speed_limits_scale_with_the_frame():
    move = [entry("card", "x", 0, 36, 0, 520, ease="move")]  # peaks about 41 px/frame
    peak = M.check_speed(data(move, size=(3840, 2160))).details["fastest"]["px_per_frame"]
    assert 30 < peak < 60
    assert M.check_speed(data(move, size=(3840, 2160))).status == "PASS"  # limit 60
    assert M.check_speed(data(move, size=(1920, 1080))).status == "FAIL"  # limit 30
    assert M.check_speed(data(move, size=(1080, 1920))).status == "FAIL"  # a 9:16 reel keeps 30


def test_diagonal_moves_combine_their_axes():
    x = entry("card", "x", 0, 36, 0, 300, ease="move")
    y = entry("card", "y", 0, 36, 0, 300, ease="move")
    assert M.check_speed(data([x], size=(1920, 1080))).status == "PASS"
    s = M.check_speed(data([x, y], size=(1920, 1080)))
    assert s.status == "FAIL"
    assert s.details["fastest"]["px_per_frame"] == pytest.approx(
        math.sqrt(2) * M.check_speed(data([x], size=(1920, 1080))).details["fastest"]["px_per_frame"], rel=1e-3)


def test_the_crest_of_a_long_flight_goes_to_a_person():
    s = M.check_speed(data([entry("tile", "x", 0, 64, 0, 800, ease="move")], size=(1920, 1080)))
    top = s.details["fastest"]["px_per_frame"]
    assert 30 < top <= 48
    assert s.status == "REVIEW" and "crest" in s.summary
    assert s.details["review_items"][0]["key"].startswith("speed:act1:tile.x")


def test_a_scale_is_speed_checked_at_its_edges_when_its_size_is_known():
    grow = entry("panel", "scale", 0, 6, 1.0, 1.6, ease="move", size=[600, 400])
    s = M.check_speed(data([grow], size=(1920, 1080)))
    assert s.status == "FAIL" and "scale edge" in s.summary
    no_size = entry("panel", "scale", 0, 6, 1.0, 1.6, ease="move")
    assert M.check_speed(data([no_size], size=(1920, 1080))) is None


# ---------------------------------------------------------------------------------------------------- loops
def test_declared_loop_periods():
    ok = entry("halo", "opacity", 0, 44, 0.8, 1.0, ease="sway", loop_period=44)
    assert M.check_loops(data([ok])).status == "PASS"
    s = M.check_loops(data([entry("halo", "opacity", 0, 8, 0.8, 1.0, ease="sway", loop_period=8)]))
    assert s.status == "FAIL" and "loops every 8 frames" in s.summary
    twenty = [entry("halo", "opacity", 0, 20, 0.8, 1.0, ease="sway", loop_period=20)]
    assert M.check_loops(data(twenty, fps=30)).status == "PASS"
    assert M.check_loops(data(twenty, fps=60)).status == "FAIL"  # 3 Hz at 60 fps


def test_regression_a_4_5_hz_wobble_is_too_fast():
    frames = np.arange(0, 31)
    wobble = signal_entry("tile", "spread", frames, 0.06 * np.sin(2 * np.pi * 4.5 * frames / 30.0))
    s = M.check_loops(data([wobble]))
    assert s.status == "FAIL" and "4.5 Hz" in s.summary
    breathe = signal_entry("tile", "spread", np.arange(0, 91), 0.06 * np.sin(2 * np.pi * 1.0 * np.arange(0, 91) / 30))
    assert M.check_loops(data([breathe])).status == "PASS"


def test_keyframe_chains_are_judged_as_one_channel():
    def chain(seg):
        out, v, f = [], 0, 0
        for i in range(6):
            nxt = 10 if v == 0 else 0
            out.append(entry("dot", "x", f, f + seg, v, nxt, ease="sway"))
            v, f = nxt, f + seg
        return out
    assert M.check_loops(data(chain(3))).status == "FAIL"  # reverses every 3 frames
    assert M.check_loops(data(chain(8))).status == "PASS"


def test_a_brand_may_ask_for_slower_loops():
    law = {**NEUTRAL_LAW, "loops": {"min_period_frames": 44}}
    s = M.check_loops(data([entry("halo", "opacity", 0, 30, 0.8, 1.0, ease="sway", loop_period=30, law=law)],
                           law=law))
    assert s.status == "FAIL"


# ---------------------------------------------------------------------------------------------------- symmetry
def _bump(u):
    return 0.16 * math.sin(math.pi * min(max(u, 0.0), 1.0)) ** 2


def _rig(delays, *, n=8, scale=None, length=24, prop="spread", fractional=0.0, rig="mark"):
    out = []
    for k in range(n):
        d = delays[k] + (fractional if k % 2 else 0.0)
        amp = (scale or {}).get(k, 1.0)
        frames = np.arange(0, length + max(delays) + 8)  # every part's gesture completes inside the trace
        vals = [amp * _bump((f - d) / length) for f in frames]
        out.append(signal_entry(f"tile{k}", prop, frames, vals, ease="sway", rig=rig))
    return out


def test_unison_pairs_and_wave_are_symmetric():
    s = M.check_symmetry(data(_rig([0] * 8)))
    assert s.status == "PASS" and s.details["rigs"][0]["mode"] == "unison"
    s = M.check_symmetry(data(_rig([0, 4] * 4)))
    assert s.status == "PASS" and s.details["rigs"][0]["mode"] == "pairs" and s.details["rigs"][0]["lag"] == 4
    s = M.check_symmetry(data(_rig([3 * k for k in range(8)])))
    assert s.status == "PASS" and s.details["rigs"][0]["mode"] == "wave" and s.details["rigs"][0]["step"] == 3


def test_a_part_that_moves_differently_breaks_symmetry():
    s = M.check_symmetry(data(_rig([0] * 8, scale={5: 1.1})))
    assert s.status == "FAIL" and "tile5" in s.summary and "whole-frame-delayed copy" in s.summary


def test_regression_parts_offset_by_fractions_of_a_frame_break_symmetry():
    s = M.check_symmetry(data(_rig([0] * 8, fractional=1.05)))
    assert s.status == "FAIL"


def test_irregular_phases_break_symmetry():
    s = M.check_symmetry(data(_rig([0, 1, 5, 2, 7, 3, 0, 4])))
    assert s.status == "FAIL" and "unison, pairs" in s.summary


def test_the_brands_rig_lag_is_held():
    law4 = {**NEUTRAL_LAW, "rigs": {"mark": {"pairs_lag": 4}}}
    law3 = {**NEUTRAL_LAW, "rigs": {"mark": {"pairs_lag": 3}}}
    assert M.check_symmetry(data(_rig([0, 4] * 4), law=law4)).status == "PASS"
    s = M.check_symmetry(data(_rig([0, 4] * 4), law=law3))
    assert s.status == "FAIL" and "uses 3" in s.summary


def test_a_part_moves_on_one_clock_across_its_channels():
    moves = _rig([0] * 8) + _rig([0, 0, 0, 4, 0, 0, 0, 0], prop="grow")
    s = M.check_symmetry(data(moves))
    assert s.status == "FAIL"


def _ring(n=6, radius_scale=None, r0=100.0, dr=20.0):
    out = []
    frames = np.arange(0, 40)
    for k in range(n):
        th = 2 * math.pi * k / n
        amp = (radius_scale or {}).get(k, 1.0)
        r = [r0 + amp * dr * _bump(f / 30) / 0.16 for f in frames]
        out.append(signal_entry(f"dot{k}", "x", frames, [400 + ri * math.cos(th) for ri in r], ease="sway", rig="ring"))
        out.append(signal_entry(f"dot{k}", "y", frames, [300 + ri * math.sin(th) for ri in r], ease="sway", rig="ring"))
    return out


def test_positional_rigs_are_compared_by_distance_from_their_centre():
    s = M.check_symmetry(data(_ring()))
    assert s.status == "PASS", s.summary
    assert "distance from centre" in s.details["rigs"][0]["channels"]
    assert M.check_symmetry(data(_ring(radius_scale={2: 1.3}))).status == "FAIL"


def test_no_rig_is_left_out():
    assert M.check_symmetry(data([entry("a", "x", 0, 15, 0, 10, ease="enter")])) is None
