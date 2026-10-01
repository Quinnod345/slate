"""Plate timing repair: a take that drops one frame and freezes another, measured, re-timed, re-made and installed.

The synthetic take is a textured landscape drifting 2.5 px a source frame at 24 fps (320 × 180, the test film's size),
written losslessly. Source frame 10 shows the scene one frame later than it should (a dropped frame: its step is twice
its neighbours'), and source frame 20 repeats frame 19 (a frozen frame). Everything runs offline in replay mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from gencap_helpers import SIZE, run_json
from slate import jsonio, procs
from slate.capture import media
from slate.gen import flow, repair, store
from slate.vendors import higgsfield

SRC_FPS = 24
SPEED = 2.5  # px per source frame
N_SRC = 48
DROPPED, FROZEN = 10, 20


def _texture(t: float, size=SIZE, speed: float = SPEED, seed: int = 3) -> np.ndarray:
    """The scene at source time t: a band-limited texture (periods 12–60 px) drifting right, with a flat sky above."""
    w, h = size
    rng = np.random.default_rng(seed)
    k = 24
    per = rng.uniform(12, 60, k)
    ang = rng.uniform(0, np.pi, k)
    ph = rng.uniform(0, 2 * np.pi, k)
    amp = rng.uniform(4, 12, k)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    x = xx - speed * t
    img = 120 + sum(amp[i] * np.sin(2 * np.pi * (np.cos(ang[i]) * x + np.sin(ang[i]) * yy) / per[i] + ph[i])
                    for i in range(k))
    img = np.where(yy < h * 0.2, 210.0, img)
    g = np.clip(img, 0, 255)
    return np.dstack([g, g * 0.95 + 8, g * 0.9 + 14]).clip(0, 255).astype(np.uint8)


def source_times(n: int = N_SRC) -> list[float]:
    """Each source frame's real time: frame 10 is a frame late (one dropped), frame 20 repeats 19 (frozen), and the
    take carries on evenly after it."""
    times = []
    t = 0.0
    for i in range(n):
        if i == DROPPED:
            t += 1.0  # the model skipped a frame
        if i == FROZEN:
            t -= 1.0  # the model repeated the last frame
        times.append(t)
        t += 1.0
    return times


def write_hitchy_clip(path: Path) -> Path:
    w, h = SIZE
    raw = b"".join(_texture(t).tobytes() for t in source_times())
    procs.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r",
               str(SRC_FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "yuv444p", "-colorspace", "bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", str(path)],
              input=raw, text=False, check=True)
    return path


# ---------------------------------------------------------------------------------------------------- the pieces
def test_lucas_kanade_measures_a_steps_shift_to_hundredths_of_a_pixel():
    a = _texture(0.0)[..., 0].astype(np.float64)
    b = _texture(1.0)[..., 0].astype(np.float64)
    dx, dy = flow.shift(a[50:150, 40:280], b[50:150, 40:280])
    assert abs(dx - SPEED) < 0.03 and abs(dy) < 0.03


def test_the_dense_flow_interpolates_a_dropped_frame_far_better_than_a_blend():
    a, mid, b = (_texture(t, size=(480, 270)).astype(np.float32) for t in (0.0, 1.0, 2.0))
    la, lb = repair._luma(a), repair._luma(b)  # noqa: SLF001
    ab, ba = flow.dense(la, lb, scale=0.5), flow.dense(lb, la, scale=0.5)
    textured = (slice(80, -12), slice(12, -12))
    err = np.abs(ab[textured] - [2 * SPEED, 0.0]).mean()
    assert err < 0.08  # px: the measured motion is the true motion
    made = flow.between(a, b, ab, ba, 0.5)

    def psnr(x, y):
        return 10 * np.log10(255 ** 2 / float(((x - y) ** 2).mean()))

    assert psnr(made[textured], mid[textured]) > 40
    assert psnr(made[textured], mid[textured]) > psnr(flow.blend(a, b, 0.5)[textured], mid[textured]) + 12


def test_true_time_gives_a_dropped_frame_its_units_and_a_frozen_frame_none():
    steps = np.array([0.0] + [SPEED if i not in (DROPPED, FROZEN) else (2 * SPEED if i == DROPPED else 0.01)
                              for i in range(1, N_SRC)])
    units, u, kinds = repair.true_time(steps)
    assert kinds[DROPPED] == "missing" and units[DROPPED] == pytest.approx(2.0, abs=0.01)
    assert kinds[FROZEN] == "frozen" and units[FROZEN] == 0.0
    assert [i for i, k in enumerate(kinds) if k] == [DROPPED, FROZEN]
    assert u[-1] == pytest.approx(N_SRC - 1, abs=0.01)  # the dropped frame's extra unit is the frozen one's missing one


def test_a_plate_that_barely_moves_is_left_as_it_is():
    units, _u, kinds = repair.true_time(np.array([0.0] + [0.1, 0.25, 0.02, 0.1] * 6))
    assert not any(kinds) and (units[1:] == 1).all()


def test_placements_put_every_film_frame_at_its_even_place_and_skip_the_frozen_copy():
    steps = np.array([0.0] + [2.0] * 9 + [4.0] + [2.0] * 9 + [0.0] + [2.0] * 27)
    _units, u, kinds = repair.true_time(steps)
    plan = repair.placements(u, kinds, src_fps=24, out_fps=25, n_out=50)
    assert [p["k"] for p in plan] == list(range(50))
    assert all(FROZEN not in (p["a"], p["b"]) for p in plan)  # the frozen copy is never used
    assert plan[0]["use"] == "a" and plan[0]["a"] == 0
    assert plan[-1]["use"] == "b" and plan[-1]["b"] == N_SRC - 1  # first and last frames stay where they were
    places = [u[p["a"]] + p["f"] * (u[p["b"]] - u[p["a"]]) for p in plan]
    inside = [k for k in range(50) if k / 25 <= (N_SRC - 1) / 24]  # past the take's last frame, it's held
    gaps = np.diff([places[k] for k in inside])
    assert gaps.max() - gaps.min() < 1e-6  # even on the true-time axis
    assert places[-1] == pytest.approx(u[-1])


def test_the_step_check_finds_a_double_step_and_a_frozen_one():
    m = np.array([0.0] + [2.4] * 10 + [4.9] + [2.4] * 8 + [0.05] + [2.4] * 6)
    over = repair.check(m)
    assert [o["frame"] for o in over] == [11] and over[0]["ratio"] > 1.9
    assert repair.frozen_steps(m) == [20]


def test_the_track_moves_through_the_same_time_warp():
    n = N_SRC
    units = np.ones(n)
    units[0] = 0.0
    units[DROPPED] = 2.0
    units[FROZEN] = 0.0
    u = np.cumsum(units)
    old = [{"t": round(0.25 * i, 3), "frame": round(0.25 * i * 25), "pts": [[0.0, 50 + 4 * 0.25 * i],
                                                                            [319.0, 60 + 4 * 0.25 * i]],
            "span": [0.1, 0.9]} for i in range(8)]
    new = repair.warp_samples(old, u, src_fps=SRC_FPS, every=0.25, count=50, out_fps=25)
    assert [s["t"] for s in new] == [s["t"] for s in old]  # the same grid
    dur, big_u = (n - 1) / SRC_FPS, float(u[-1])
    frames = np.arange(n, dtype=float)
    for s in new[1:-1]:
        t_old = float(np.interp(s["t"] * big_u / dur, u, frames)) / SRC_FPS  # where this moment was before
        assert s["pts"][0][1] == pytest.approx(50 + 4 * t_old, abs=0.05)
        assert s["frame"] == round(s["t"] * 25) and s["span"] == [0.1, 0.9]


def test_band_parsing():
    assert repair.parse_band(None) == repair.BAND
    assert repair.parse_band("0,0.5,1,1") == (0.0, 0.5, 1.0, 1.0)
    from slate.errors import UsageError

    with pytest.raises(UsageError):
        repair.parse_band("0.5,0.5,0.2,1")


# ---------------------------------------------------------------------------------------------------- through a film
@pytest.fixture
def hitchy_take(monkeypatch, tmp_path):
    """Replayed generations download this synthetic take instead of the replay's test clip."""
    clip = write_hitchy_clip(tmp_path / "hitchy.mkv")

    def fake(url, dest, stem):
        out = Path(dest) / f"{stem}.mkv"
        out.write_bytes(clip.read_bytes())
        return out

    monkeypatch.setattr(higgsfield, "fake_media", fake)
    return clip


def _approved_plate(film) -> dict:
    code, out, err = run_json(["gen", "run", "--film", str(film), "--shot", "S02"])
    assert code == 0, err
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    code, out, err = run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--take", "t1", "--by", "Quinn"])
    assert code == 0, err
    code, out, err = run_json(["plates", "conform", "--film", str(film), "--shot", "S02", "--no-audio"])
    assert code == 0, err
    return out


def _write_track(film) -> Path:
    path = store.shot_dir(film, "S02") / "plate" / "track.json"
    samples = [{"t": round(0.25 * i, 3), "frame": round(0.25 * i * 25), "pts": [[0.0, 50 + 4 * 0.25 * i],
                                                                                  [319.0, 60 + 4 * 0.25 * i]],
                "span": [0.1, 0.9]} for i in range(9)]
    doc = jsonio.mark_generated({"schema": 1, "shot": "S02", "fps_num": 25, "fps_den": 1, "width": SIZE[0],
                                 "height": SIZE[1], "hue": [0, 359], "params": {"every": 0.25}, "samples": samples},
                                "slate plates track")
    jsonio.write_atomic(path, doc)
    return path


def test_the_conform_checks_steps_and_names_the_repair(film, hitchy_take):
    out = _approved_plate(film)
    plate = jsonio.read(film / out["plate"])
    steps = plate["steps"]
    assert steps["over_1_3"], steps  # the dropped frame shows as a step over 1.3× its neighbours
    assert any("hitches" in w and "slate plates repair S02" in w for w in out["warnings"])


def test_repair_stages_then_installs_keeping_the_old_sequence(film, hitchy_take):
    out = _approved_plate(film)
    track_path = _write_track(film)
    frames_dir = film / out["frames"]["dir"]
    count = out["frames"]["count"]
    old_first = (frames_dir / media.frame_name(5)).read_bytes()
    stage_dir = store.shot_dir(film, "S02") / "plate" / repair.REPAIR_DIR

    # a dry run measures and plans, and writes nothing
    code, plan, err = run_json(["plates", "repair", "S02", "--dry-run", "--film", str(film)])
    assert code == 0, err
    assert plan["dry_run"] is True and plan["needed"] is True and plan["staged"] is None
    assert [r["step_to_source_frame"] for r in plan["retimed_steps"] if r["kind"] == "missing"] == [DROPPED]
    assert plan["frozen_dropped"] == [FROZEN] and "nothing written" in plan["summary"]
    assert not stage_dir.exists()

    code, rep, err = run_json(["plates", "repair", "S02", "--film", str(film)])
    assert code == 0, err
    assert rep["needed"] is True
    missing = [r for r in rep["retimed_steps"] if r["kind"] == "missing"]
    assert [r["step_to_source_frame"] for r in missing] == [DROPPED]
    assert missing[0]["units"] == pytest.approx(2.0, abs=0.15)
    assert rep["frozen_dropped"] == [FROZEN]
    assert [r for r in rep["retimed_steps"] if r["kind"] == "uneven"] == []  # nothing else was off
    assert rep["step_check"]["over_1_3"] == []  # no step over 1.3× its neighbours after the repair
    assert rep["interpolated"] > 0 and rep["track"] is True
    staged = film / rep["staged"]
    assert media.count_frames(staged) == count
    assert (frames_dir / media.frame_name(5)).read_bytes() == old_first  # nothing in place changed yet
    full = jsonio.read(film / rep["report"])
    assert full["source"]["frames"] == N_SRC and len(full["placements"]) == count

    code, would, err = run_json(["plates", "repair", "S02", "--install", "--dry-run", "--film", str(film)])
    assert code == 0, err
    assert would["dry_run"] is True and would["installed"] is False and "nothing moved" in would["summary"]
    assert not (film / would["kept"]).exists() and (frames_dir / media.frame_name(5)).read_bytes() == old_first

    code, done, err = run_json(["plates", "repair", "S02", "--install", "--film", str(film)])
    assert code == 0, err
    kept = film / done["kept"]
    assert media.count_frames(kept / "frames") == count == media.count_frames(frames_dir)
    assert (kept / "frames" / media.frame_name(5)).read_bytes() == old_first  # the old sequence, kept
    assert (kept / "plate.json").is_file() and (kept / "track.json").is_file()
    plate = jsonio.read(film / done["plate"])
    assert plate["repair"]["retimed_steps"] == 1 and plate["repair"]["frozen_dropped"] == 1
    assert plate["steps"]["over_1_3"] == [] and not any("hitches" in w for w in plate["warnings"])
    track = jsonio.read(track_path)
    assert track["repaired"] and track["samples"] == full["track"]["samples"]
    # the installed frames step evenly: measured again from disk
    after = repair.sequence_steps(frames_dir)
    assert repair.check(after) == [] and repair.frozen_steps(after) == []

    code, again, _ = run_json(["plates", "repair", "S02", "--install", "--film", str(film)])
    assert code == 3 and "no staged repair" in again["message"].lower()  # a stage installs once


def test_an_even_plate_has_nothing_to_repair(film, monkeypatch, tmp_path):
    w, h = SIZE
    clip = tmp_path / "even.mkv"
    raw = b"".join(_texture(float(i)).tobytes() for i in range(N_SRC))
    procs.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r",
               str(SRC_FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "yuv444p", str(clip)], input=raw, text=False,
              check=True)
    monkeypatch.setattr(higgsfield, "fake_media",
                        lambda url, dest, stem: Path(dest, f"{stem}.mkv").write_bytes(clip.read_bytes()) and
                        Path(dest, f"{stem}.mkv"))
    out = _approved_plate(film)
    assert jsonio.read(film / out["plate"])["steps"]["over_1_3"] == []
    code, rep, err = run_json(["plates", "repair", "--shot", "S02", "--film", str(film)])
    assert code == 0, err
    assert rep["needed"] is False and "nothing to repair" in rep["summary"]
    code, again, _ = run_json(["plates", "repair", "S02", "--install", "--film", str(film)])
    assert code == 3


def test_repair_refuses_without_a_plate_or_a_shot(film):
    code, out, _ = run_json(["plates", "repair", "--film", str(film)])
    assert code == 2 and "Name the shot" in out["message"]
    code, out, _ = run_json(["plates", "repair", "S02", "--film", str(film)])
    assert code == 3 and "plates conform" in out["hint"]
    code, out, _ = run_json(["plates", "repair", "S02", "--install", "--blend", "--film", str(film)])
    assert code == 2


def test_a_still_plate_has_no_timing_to_repair(film):
    code, _out, err = run_json(["gen", "run", "--film", str(film), "--shot", "S04"])
    assert code == 0, err
    run_json(["gen", "judge", "--film", str(film), "--shot", "S04"])
    code, _out, err = run_json(["gen", "pick", "--film", str(film), "--shot", "S04", "--take", "t1", "--by", "Quinn"])
    assert code == 0, err
    code, out, err = run_json(["plates", "conform", "--film", str(film), "--shot", "S04", "--frames", "3"])
    assert code == 0, err
    assert jsonio.read(film / out["plate"]).get("steps") is None  # a still has no steps to check
    code, out, _ = run_json(["plates", "repair", "S04", "--film", str(film)])
    assert code == 1 and "still" in out["message"]


def test_the_report_names_everything_a_person_checks(film, hitchy_take):
    _approved_plate(film)
    code, rep, err = run_json(["plates", "repair", "S02", "--blend", "--film", str(film)])
    assert code == 0, err
    full = jsonio.read(film / rep["report"])
    for key in ("source_steps", "retimed_steps", "frozen_dropped", "interpolated_frames", "step_check", "method",
                "placements", "band"):
        assert key in full, key
    assert full["method"]["blend"] is True and "blend" in full["method"]["conform"]
    assert full["generated"].startswith("do not edit by hand: slate plates repair")
    first = np.asarray(Image.open(film / rep["staged"] / media.frame_name(0)).convert("RGB"), dtype=float)
    assert np.abs(first - _texture(0.0).astype(float)).mean() < 3  # frame 0 is the source's own first frame
    json.dumps(rep)  # the CLI's answer is plain JSON
