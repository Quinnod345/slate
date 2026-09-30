"""Planning: acts from shots.json, chunk ranges, film-frame segments, samples, disk estimates, here-or-queue."""

import json

import pytest

from slate.errors import MissingPrerequisite, UsageError
from slate.render import plan
from render_fakes import make_film


def test_chunk_ranges_cover_every_frame_once():
    ranges = plan.chunk_ranges(0, 1333, 600)
    assert ranges == [(0, 599), (600, 1199), (1200, 1333)]
    covered = [f for a, b in ranges for f in range(a, b + 1)]
    assert covered == list(range(0, 1334))


def test_chunk_ranges_for_a_patch_start_at_its_first_frame():
    assert plan.chunk_ranges(630, 2711, 1000) == [(630, 1629), (1630, 2629), (2630, 2711)]
    assert plan.chunk_ranges(5, 5, 600) == [(5, 5)]


@pytest.mark.parametrize("first,last,size", [(0, 10, 0), (5, 4, 10), (-1, 3, 10)])
def test_chunk_ranges_refuse_nonsense(first, last, size):
    with pytest.raises(UsageError):
        plan.chunk_ranges(first, last, size)


def _shots(acts):
    shots, rows, pos = [], [], 0
    for index, n in acts:
        sid = f"S{index}"
        shots.append({"id": sid, "frame_in": pos, "frame_out": pos + n})
        rows.append({"index": index, "shots": [sid]})
        pos += n
    return {"acts": rows, "shots": shots, "total_frames": pos}


def test_acts_from_shots_reads_ranges_and_shot_spans(slate_home):
    film_dir = make_film(slate_home["films"], acts=((1, 30), (2, 24), (3, 7)))
    acts = plan.acts_from_shots(json.loads((film_dir / "timing" / "shots.json").read_text()))
    assert [(a.index, a.frame_in, a.frame_out) for a in acts] == [(1, 0, 30), (2, 30, 54), (3, 54, 61)]
    assert acts[0].shots == "A1S1-A1S2"
    assert acts[1].frames == 24


def test_acts_must_be_contiguous():
    shots = _shots([(1, 10), (2, 10)])
    shots["shots"][1]["frame_in"] = 12
    shots["shots"][1]["frame_out"] = 22
    with pytest.raises(UsageError, match="contiguous"):
        plan.acts_from_shots(shots)


def test_acts_must_reach_total_frames():
    shots = _shots([(1, 10)])
    shots["total_frames"] = 11
    with pytest.raises(UsageError, match="total_frames"):
        plan.acts_from_shots(shots)


def test_no_acts_is_a_missing_prerequisite():
    with pytest.raises(MissingPrerequisite):
        plan.acts_from_shots({"acts": [], "shots": []})


def test_segments_split_film_frames_across_acts():
    acts = plan.acts_from_shots(_shots([(1, 10), (2, 10), (3, 10)]))
    segs = [(a.index, lo, hi) for a, lo, hi in plan.segments(8, 21, acts)]
    assert segs == [(1, 8, 9), (2, 0, 9), (3, 0, 1)]
    with pytest.raises(UsageError):
        plan.segments(25, 30, acts)


def test_samples_default_draft_and_explicit():
    settings = {"motion_blur_samples": 16, "draft_samples": 4}
    assert plan.resolve_samples({"mode": "supervised"}, engine="remotion", settings=settings) == (16, False)
    # draft mode renders with 4 samples, whether the film is a draft or the render asks for one
    assert plan.resolve_samples({"mode": "draft"}, engine="remotion", settings=settings) == (4, True)
    assert plan.resolve_samples({"mode": "supervised"}, engine="web", draft=True, settings=settings) == (4, True)
    assert plan.resolve_samples({"mode": "draft"}, engine="remotion", samples=8, settings=settings) == (8, True)
    with pytest.raises(UsageError):
        plan.resolve_samples({}, engine="remotion", samples=0, settings=settings)


def test_ae_has_no_sample_count():
    assert plan.resolve_samples({}, engine="ae", settings={}) == (None, False)
    with pytest.raises(UsageError, match="After Effects"):
        plan.resolve_samples({}, engine="ae", samples=16, settings={})


def test_disk_estimate_scales_with_the_films_own_size():
    # frames x ProRes HQ bytes per frame, from film.json's size (nothing assumes a frame size)
    small = plan.bytes_per_frame(640, 360)
    big = plan.bytes_per_frame(1280, 720)
    assert big == pytest.approx(4 * small)
    assert small == pytest.approx(640 * 360 * plan.PRORES_HQ_BITS_PER_PIXEL / 8)


def test_disk_estimate_counts_chunks_output_and_a_splice(slate_home):
    film_dir = make_film(slate_home["films"], acts=((1, 100),), width=320, height=180)
    film = json.loads((film_dir / "film.json").read_text())
    shots = json.loads((film_dir / "timing" / "shots.json").read_text())
    whole = plan.build(film_dir, film, shots, act=1, settings={"chunk_frames": 50})
    per = plan.bytes_per_frame(320, 180)
    assert whole.disk_gb == pytest.approx(100 * per * 2 / 1e9)
    patch = plan.build(film_dir, film, shots, act=1, frames=(10, 19), settings={"chunk_frames": 50},
                       registered={1: {"frames": [0, 100]}})
    assert patch.targets[0].kind == "patch"
    assert patch.disk_gb == pytest.approx((10 * per * 2 + 2 * 100 * per) / 1e9)


def test_build_whole_film_patch_and_range(slate_home):
    film_dir = make_film(slate_home["films"], acts=((1, 30), (2, 24)))
    film = json.loads((film_dir / "film.json").read_text())
    shots = json.loads((film_dir / "timing" / "shots.json").read_text())
    everything = plan.build(film_dir, film, shots, settings={"chunk_frames": 10})
    assert [(t.kind, t.act.index, t.first, t.last, t.out) for t in everything.targets] == [
        ("act", 1, 0, 29, "renders/act1.mov"), ("act", 2, 0, 23, "renders/act2.mov")]
    assert everything.chunks["act1:0-29"] == [[0, 9], [10, 19], [20, 29]]
    # film frames 25-34 cross from act 1 into act 2: two patches, in each act's own frame numbers
    cross = plan.build(film_dir, film, shots, frames=(25, 34), settings={"chunk_frames": 10})
    assert [(t.kind, t.act.index, t.first, t.last) for t in cross.targets] == [("patch", 1, 25, 29), ("patch", 2, 0, 4)]
    assert cross.targets[1].film_frames == [30, 35]
    # a whole act given as a range is a fresh act render, not a splice of every frame
    whole = plan.build(film_dir, film, shots, act=2, frames=(0, 23), settings={"chunk_frames": 10})
    assert whole.targets[0].kind == "act"
    ranged = plan.build(film_dir, film, shots, act=1, frames=(3, 7), out="review/r.mov", settings={"chunk_frames": 10})
    assert ranged.targets[0].kind == "range" and ranged.targets[0].out == "review/r.mov"
    with pytest.raises(UsageError, match="crosses acts"):
        plan.build(film_dir, film, shots, frames=(25, 34), out="x.mov", settings={"chunk_frames": 10})
    with pytest.raises(UsageError, match="isn't inside it"):
        plan.build(film_dir, film, shots, act=1, frames=(20, 30), settings={"chunk_frames": 10})


def test_build_needs_size_and_rate_from_film_json(slate_home):
    film_dir = make_film(slate_home["films"])
    shots = json.loads((film_dir / "timing" / "shots.json").read_text())
    with pytest.raises(UsageError, match="fps_num"):
        plan.build(film_dir, {"width": 64, "height": 36}, shots, settings={})


def test_build_refuses_timing_made_at_another_rate(slate_home):
    film_dir = make_film(slate_home["films"], fps=(24000, 1001))
    film = json.loads((film_dir / "film.json").read_text())
    shots = json.loads((film_dir / "timing" / "shots.json").read_text())
    shots["fps_num"] = 25
    shots["fps_den"] = 1
    with pytest.raises(UsageError, match="timed at"):
        plan.build(film_dir, film, shots, settings={})


def test_long_renders_go_to_the_queue(slate_home):
    film_dir = make_film(slate_home["films"], acts=((1, 30),))
    film = json.loads((film_dir / "film.json").read_text())
    shots = json.loads((film_dir / "timing" / "shots.json").read_text())
    short = plan.build(film_dir, film, shots, act=1, samples=4,
                       settings={"chunk_frames": 100, "foreground_max_subframes": 200})
    assert short.subframes == 120 and short.runs == "here"
    long = plan.build(film_dir, film, shots, act=1, samples=16,
                      settings={"chunk_frames": 100, "foreground_max_subframes": 200})
    assert long.runs == "queue" and "always go through the queue" in long.why
    # the measure of long is the work (frames x samples), not how the frames are chunked
    chunked = plan.build(film_dir, film, shots, act=1, samples=1,
                         settings={"chunk_frames": 10, "foreground_max_subframes": 100000})
    assert len(chunked.chunks["act1:0-29"]) == 3 and chunked.runs == "here"
    detached = plan.build(film_dir, film, shots, act=1, samples=1, detach=True,
                          settings={"chunk_frames": 100, "foreground_max_subframes": 100000})
    assert detached.runs == "queue" and detached.why == "--detach"
