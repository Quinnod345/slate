"""shots.json: make_shots parity, drift-free rounding at any rate, and the frame-exact gate (P062, P116)."""

from __future__ import annotations

import copy
import json
import random

import pytest
from storyfix import write_json

from slate.cli import run_cli
from slate.clock import Clock
from slate.timing.shots import check_frames, make_shots

RATES = [(24, 1), (25, 1), (30, 1), (50, 1), (60, 1), (24000, 1001), (60000, 1001)]


def reference_make_shots(sb: dict, num: int, den: int, width: int, height: int) -> dict:
    """FILMROOT pipeline/tools/make_shots.py's frame maths, verbatim except the rate and size are parameters."""
    fps = num / den

    def frame_of(seconds):
        return int(round(seconds * fps))

    def secs_of(frame):
        return frame * den / num

    shots, t = [], 0.0
    for act_i, act in enumerate(sb["acts"], 1):
        for sh in act["shots"]:
            d = float(sh["dur"])
            f_in, f_out = frame_of(t), frame_of(t + d)
            shots.append({
                "id": sh["id"], "act": act_i, "act_name": act["name"],
                "frame_in": f_in, "frame_out": f_out, "frames": f_out - f_in,
                "sec_in": round(secs_of(f_in), 6), "sec_out": round(secs_of(f_out), 6),
                "storyboard_sec_in": round(t, 6), "storyboard_dur": d,
                "rounding_in_sec": round(secs_of(f_in) - t, 6),
                "rounding_dur_sec": round(secs_of(f_out - f_in) - d, 6),
            })
            t += d
    total_f = frame_of(t)
    return {"fps_num": num, "fps_den": den, "width": width, "height": height, "storyboard_seconds": round(t, 6),
            "total_frames": total_f, "total_seconds": round(secs_of(total_f), 6), "shots": shots}


def random_storyboard(rng: random.Random, n_acts: int = 4) -> dict:
    k = 0
    acts = []
    for a in range(n_acts):
        shots = []
        for _ in range(rng.randint(1, 9)):
            k += 1
            shots.append({"id": f"S{k:02d}", "dur": round(rng.uniform(0.4, 12.0), rng.choice([1, 2, 3])),
                          "vo": "", "on_screen": ""})
        acts.append({"name": f"Act {a + 1}", "shots": shots})
    return {"version": "v", "acts": acts}


@pytest.mark.parametrize("num,den", RATES)
def test_make_shots_matches_the_films_algorithm_at_any_rate(num, den):
    rng = random.Random(num * 7 + den)
    for _ in range(25):
        sb = random_storyboard(rng)
        mine = make_shots(sb, clock=Clock(num, den), width=720, height=1280)
        ref = reference_make_shots(sb, num, den, 720, 1280)
        for key in ("fps_num", "fps_den", "width", "height", "storyboard_seconds", "total_frames", "total_seconds"):
            assert mine[key] == ref[key]
        for a, b in zip(mine["shots"], ref["shots"], strict=True):
            assert {k: a[k] for k in b} == b
        assert check_frames(mine, Clock(num, den)).passed


@pytest.mark.parametrize("num,den", RATES)
def test_rounding_is_drift_free(num, den):
    clock = Clock(num, den)
    sb = {"acts": [{"name": "A", "shots": [{"id": f"S{i:03d}", "dur": 1.2345} for i in range(1, 301)]}]}
    doc = make_shots(sb, clock=clock, width=720, height=1280)
    t = 0.0
    for s in doc["shots"]:
        assert s["frame_in"] == clock.frame_of(t)  # every boundary is the cumulative time, rounded once
        assert abs(s["rounding_in_sec"]) <= 0.5 / clock.fps + 5e-7  # recorded to 6 decimals
        t += 1.2345
    assert doc["total_frames"] == clock.frame_of(t)  # the cumulative sum, as the films rounded it (370.35 s is a tie at 30)
    assert sum(s["frames"] for s in doc["shots"]) == doc["total_frames"]


def test_every_rounding_is_recorded_per_shot():
    doc = make_shots({"acts": [{"name": "A", "shots": [{"id": "S01", "dur": 1.01}, {"id": "S02", "dur": 1.01}]}]},
                     clock=Clock(24, 1), width=720, height=1280)
    s2 = doc["shots"][1]
    assert s2["frame_in"] == 24 and s2["storyboard_sec_in"] == 1.01
    assert s2["rounding_in_sec"] == round(24 / 24 - 1.01, 6)
    assert s2["rounding_dur_sec"] == round((48 - 24) / 24 - 1.01, 6)


def test_shots_carry_voice_placement_words_and_events():
    sb = {"version": "v2", "acts": [{"name": "A", "shots": [{"id": "S01", "dur": 2.0, "vo": "Hello there."},
                                                            {"id": "S02", "dur": 2.0}]}]}
    cues = {"cues": [{"shot": "S01", "at": 0.3, "file": "voice/takes/S01.wav"}]}
    words = {"shots": {"S01": {"line": "Hello there.", "words": [{"w": "Hello", "start": 0.0, "end": 0.4}]}}}
    events = {"shots": {"S02": [{"id": "pop", "t": 0.5}]}}
    doc = make_shots(sb, clock=Clock(25, 1), width=720, height=1280, cues=cues, words=words, events=events)
    s1, s2 = doc["shots"]
    assert (s1["vo_take"], s1["vo_lead_sec"], s1["vo_frame"]) == ("voice/takes/S01.wav", 0.3, 8)
    assert s1["vo_words"]["line"] == "Hello there." and s2["events"] == [{"id": "pop", "t": 0.5}]
    assert doc["acts"][0] == {"index": 1, "name": "A", "shots": ["S01", "S02"], "frame_in": 0, "frame_out": 100,
                              "frames": 100}
    assert doc["storyboard_version"] == "v2"


# --- the stage 5 gate -------------------------------------------------------------------------------------------------------

def good_doc():
    return make_shots({"acts": [{"name": "A", "shots": [{"id": "S01", "dur": 2.0}, {"id": "S02", "dur": 3.0}]}]},
                      clock=Clock(25, 1), width=720, height=1280)


@pytest.mark.parametrize("breakage,phrase", [
    (lambda d: d["shots"][1].update(frame_in=51, frames=74), "gap between S01"),
    (lambda d: d["shots"][1].update(frame_in=49, frames=76), "overlap between S01"),
    (lambda d: d["shots"][0].update(frame_in=1, frames=49), "starts at frame 1"),
    (lambda d: d.update(total_frames=126), "total_frames says 126"),
    (lambda d: d["shots"][1].update(id="S01"), "duplicate shot ids"),
    (lambda d: d["shots"][0].update(frame_out=0, frames=0), "a shot needs at least one frame"),
    (lambda d: d["shots"][0].update(frames=50.0), "fractional frames"),
    (lambda d: d["shots"][0].update(sec_in=0.01), "sec_in doesn't match"),
])
def test_the_frame_gate_fails_on_anything_not_exact(breakage, phrase):
    doc = copy.deepcopy(good_doc())
    breakage(doc)
    result = check_frames(doc, Clock(25, 1))
    assert result.status == "FAIL" and any(phrase in e for e in result.evidence), result.evidence


def test_the_frame_gate_passes_contiguous_frames():
    result = check_frames(good_doc(), Clock(25, 1))
    assert result.passed and result.gate == "timing.frames" and "125 frames" in result.summary


# --- fps and size come from film.json -----------------------------------------------------------------------------------------

def test_rate_and_size_come_from_film_json(make_film):
    film = make_film(film={"fps_num": 24000, "fps_den": 1001, "width": 1280, "height": 720})
    assert run_cli(["script", "build", "--film", str(film)])[0] == 0
    code, out, _ = run_cli(["timing", "--film", str(film), "--json"])
    assert code == 0, out
    doc = json.loads((film / "timing" / "shots.json").read_text())
    assert (doc["fps_num"], doc["fps_den"], doc["width"], doc["height"]) == (24000, 1001, 1280, 720)
    tl = json.loads((film / "timing" / "timeline.json").read_text())
    assert (tl["fps_num"], tl["fps_den"]) == (24000, 1001) and tl["fps"] == 24000 / 1001


def test_a_film_without_a_rate_is_refused_not_guessed(make_film):
    film = make_film()
    assert run_cli(["script", "build", "--film", str(film)])[0] == 0
    doc = json.loads((film / "film.json").read_text())
    del doc["fps_num"]
    write_json(film / "film.json", doc)
    code, out, _ = run_cli(["timing", "--film", str(film), "--json"])
    assert code == 2 and "fps_num" in json.loads(out)["message"]
    assert not (film / "timing").exists()


def test_timing_writes_nothing_when_the_frames_fail(make_film, monkeypatch):
    from slate import timing
    from slate.errors import Refused
    from slate.timing import builder

    film = make_film()
    assert run_cli(["script", "build", "--film", str(film)])[0] == 0
    real = builder.make_shots

    def broken(*a, **kw):
        doc = real(*a, **kw)
        doc["shots"][1]["frame_in"] += 1
        return doc

    monkeypatch.setattr(builder, "make_shots", broken)
    with pytest.raises(Refused, match="Timing not written"):
        timing.build(film)
    code, out, _ = run_cli(["timing", "--film", str(film), "--json"])
    assert code == 1 and json.loads(out)["written"] == []
    assert not (film / "timing").exists()
