"""The pixel cross-check: block matching on a render finds fast motion the trace doesn't explain, so a scene that
skips its trace can't pass silently. Renders here are generated frames (a folder of PNGs, or a tiny ProRes file
made from them), a few dozen frames at most."""

from __future__ import annotations

import numpy as np
import pytest

from qa_motion_helpers import (NEUTRAL_LAW, entry, make_film, register_render, textured_square_frames,
                               write_frames)
from slate import procs
from slate.qa import motion as M

SIZE = (640, 360)  # limit 10 px/frame (a 36th of the short side), fast over 12.5


def _small(frames, size=SIZE):
    from PIL import Image

    w, h = M.analysis_size(*size)
    return [np.asarray(Image.fromarray(f).resize((w, h), Image.Resampling.BOX), dtype=np.float32) / 255 for f in frames]


def _scan(frames, size=SIZE):
    w, h = M.analysis_size(*size)
    return M.fast_pairs(_small(frames, size), film_short=min(size), speed_limit=M.limits(*size)["speed"],
                        analysis_short=min(w, h))


def test_block_matching_measures_speed():
    pairs, summary = _scan(textured_square_frames(12, size=SIZE, velocity=(24, 0), start=(10, 132), square=96))
    assert len(pairs) >= 10
    assert np.median([p["speed"] for p in pairs]) == pytest.approx(24, rel=0.08)
    assert summary["fastest_measured"] == pytest.approx(24, rel=0.15)


@pytest.mark.parametrize("speed", [0.0, 4.0, 8.0])
def test_legal_motion_is_never_fast(speed):
    pairs, _ = _scan(textured_square_frames(12, size=SIZE, velocity=(speed, 0), start=(10, 132), square=96))
    assert pairs == []


def test_diagonal_and_vertical_frames_are_measured():
    pairs, _ = _scan(textured_square_frames(12, size=(360, 640), velocity=(0, 20), start=(132, 10), square=96),
                     size=(360, 640))
    assert len(pairs) >= 8 and np.median([p["speed"] for p in pairs]) == pytest.approx(20, rel=0.1)


def test_a_cut_is_not_motion():
    a = textured_square_frames(6, size=SIZE, start=(40, 40), square=96, seed=1)
    b = textured_square_frames(6, size=SIZE, start=(400, 200), square=96, seed=9)
    pairs, summary = _scan(a + b)
    assert pairs == []


# ---------------------------------------------------------------------------------------------------- the gate
@pytest.fixture
def film(slate_home, monkeypatch):
    monkeypatch.setattr(M, "brand_motion", lambda brand: (NEUTRAL_LAW, "test law"))
    return slate_home


def _film_with_render(home, trace, velocity=(24, 0), frames=16):
    film = make_film(home["films"], size=SIZE, acts={1: (0, frames)}, traces={1: trace})
    write_frames(film / "renders" / "act1", textured_square_frames(frames, size=SIZE, velocity=velocity,
                                                                   start=(10, 132), square=96))
    register_render(film, 1, "renders/act1", 0, frames)
    return film


def _pixels(result):
    return next(c for c in result.details["checks"] if c["check"] == "pixels")


def test_fast_motion_with_no_trace_goes_to_a_person(film):
    fade = entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter", shot="S1")
    f = _film_with_render(film, [fade])
    result = M.evaluate(f)
    px = _pixels(result)
    assert px["status"] == "REVIEW", px["summary"]
    assert "doesn't explain" in px["summary"]
    item = px["review_items"][0]
    assert item["key"].startswith("pixels:renders/act1:") and item["why"] == "untraced"
    assert result.status == "REVIEW"  # every other sub-check passes; only a person can clear this


def test_traced_fast_motion_is_explained(film):
    # the trace carries the square's move (it breaks the speed limit, which the speed check reports, but it isn't
    # hidden from the trace, so the pixel check is satisfied)
    frames = [[k, 10 + 24 * k] for k in range(16)]
    carry = {"id": "square", "shot": "S1", "prop": "x", "ease": "linear", "frame_in": 0, "frame_out": 15,
             "from": 10, "to": 10 + 24 * 15, "samples": frames, "entrance": False, "loop_period": None, "rig": None}
    f = _film_with_render(film, [carry])
    result = M.evaluate(f)
    assert _pixels(result)["status"] == "PASS"
    speed = next(c for c in result.details["checks"] if c["check"] == "speed")
    assert speed["status"] == "FAIL"


def test_a_trace_that_understates_a_move_goes_to_a_person(film):
    slow = {"id": "square", "shot": "S1", "prop": "x", "ease": "linear", "frame_in": 0, "frame_out": 15, "from": 10,
            "to": 40, "samples": [[k, 10 + 2 * k] for k in range(16)], "entrance": False, "loop_period": None,
            "rig": None}
    f = _film_with_render(film, [slow])
    px = _pixels(M.evaluate(f))
    assert px["status"] == "REVIEW" and px["review_items"][0]["why"] == "under-traced"


def test_slow_motion_passes(film):
    fade = entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter")
    f = _film_with_render(film, [fade], velocity=(4, 0))
    assert _pixels(M.evaluate(f))["status"] == "PASS"


def test_no_render_is_unchecked(film):
    f = make_film(film["films"], size=SIZE, traces={1: [entry("t", "opacity", 0, 15, 0, 1, ease="enter")]})
    px = _pixels(M.evaluate(f))
    assert px["status"] == "UNCHECKED" and "slate render" in px["hint"]


def test_skipping_the_pixels_is_reported_not_passed(film):
    fade = entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter")
    f = _film_with_render(film, [fade], velocity=(4, 0))
    result = M.evaluate(f, pixels=False)
    assert _pixels(result)["status"] == "UNCHECKED" and result.status == "UNCHECKED"


def test_a_render_passed_by_hand_is_mapped_to_its_act(film):
    fade = entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter")
    f = make_film(film["films"], size=SIZE, acts={1: (0, 16), 2: (16, 32)},
                  traces={1: [fade], 2: [entry("t2", "opacity", 16, 31, 0.0, 1.0, ease="enter", shot="S2")]})
    frames = write_frames(film["tmp"] / "loose", textured_square_frames(16, size=SIZE, velocity=(24, 0),
                                                                       start=(10, 132), square=96))
    px = _pixels(M.evaluate(f, render=str(frames), render_act=2))
    assert px["status"] == "REVIEW"
    assert px["review_items"][0]["film_frames"][0] >= 16  # film frames of act 2, not the file's own frame numbers


@pytest.mark.skipif(procs.which("ffmpeg") is None, reason="ffmpeg isn't installed")
def test_a_video_render_is_decoded_through_ffmpeg(film):
    fade = entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter")
    f = make_film(film["films"], size=SIZE, acts={1: (0, 16)}, traces={1: [fade]})
    pngs = write_frames(film["tmp"] / "pngs", textured_square_frames(16, size=SIZE, velocity=(24, 0),
                                                                    start=(10, 132), square=96))
    out = f / "renders" / "act1.mov"
    out.parent.mkdir(parents=True, exist_ok=True)
    procs.run(["ffmpeg", "-v", "error", "-y", "-framerate", "30", "-i", str(pngs / "f%05d.png"), "-c:v", "prores_ks",
               "-profile:v", "3", "-pix_fmt", "yuv422p10le", str(out)], check=True, timeout=120)
    register_render(f, 1, "renders/act1.mov", 0, 16)
    px = _pixels(M.evaluate(f))
    assert px["status"] == "REVIEW", px["summary"]
    assert px["scans"][0]["pairs"] == 15


def test_a_person_accepts_a_review_item_and_the_gate_counts_it(film):
    from slate import gates
    from slate.qa import accept

    fade = entry("title", "opacity", 0, 15, 0.0, 1.0, ease="enter", shot="S1")
    f = _film_with_render(film, [fade])
    result = M.evaluate(f)
    assert result.status == "REVIEW"
    key = result.details["review_items"][0]["key"]  # lifted to the gate, where triage looks
    gates.write_report(f, [result])
    record = accept.accept(f, f"motion:{key}", by="Tester", reason="a deliberate whip, traced on the camera rig")
    assert record["basis"] == result.details["review_items"][0]["basis"]
    again = M.evaluate(f)
    assert _pixels(again)["status"] == "PASS" and again.status == "PASS"
    assert again.details["accepted"][0]["accepted_by"] == "Tester"
    # a new render changes the registry, so the acceptance no longer applies
    register_render(f, 1, "renders/act1", 0, 16)
    assert M.evaluate(f).status == "REVIEW"


@pytest.mark.skipif(procs.which("ffmpeg") is None, reason="ffmpeg isn't installed")
def test_golden_a_real_carry_is_measured_and_a_held_picture_is_quiet(filmroot):
    """Read-only, on the first film's delivered act 4 render: its notes say the page carry into the donut peaks at
    136 px/frame (a T4 glide from film frame 7779 to 7803). The matcher must see it, and see nothing in the second
    and a bit the picture holds after it lands (from film frame 7860 the donut's last segment sweeps in, a real
    fast edge that a trace would carry)."""
    import json

    from slate.qa import media

    build = filmroot / "build"
    shots = json.loads((build / "shots.json").read_text())
    act4 = next(e for e in json.loads((build / "renders.nosync" / "r3f" / "renders.json").read_text())["renders"]
                if e["comp"] == "Act4")
    src = filmroot / act4["file"]
    if not src.is_file():
        pytest.skip("the act 4 render isn't on this machine")
    film_size = (shots["width"], shots["height"])
    info = {"fps_num": shots["fps_num"], "fps_den": shots["fps_den"], "width": film_size[0], "height": film_size[1],
            "frames": act4["frame_out"] - act4["frame_in"]}
    size = M.analysis_size(*film_size)
    limit = M.limits(*film_size)["speed"]

    def scan(start, count=40):
        frames = [f.astype(np.float32) / 255 for f in media.iter_frames(src, start=start, count=count, size=size,
                                                                         pix_fmt="gray", info=info)]
        return M.fast_pairs(frames, film_short=min(film_size), speed_limit=limit, analysis_short=min(size))

    carry_start = 7779 - act4["frame_in"]
    pairs, summary = scan(carry_start - 10)
    peak = max(p["speed"] for p in pairs)
    assert peak == pytest.approx(136, rel=0.1)
    assert len(pairs) >= 8
    held, _ = scan(carry_start + 29, count=36)  # film frames 7808–7843
    assert held == []
