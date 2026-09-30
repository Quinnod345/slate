"""Conforms: exact frame counts at the film's rate, what was on screen at each frame, holds, fits, timestamps."""

from __future__ import annotations

import os

import pytest
from PIL import Image

from gencap_helpers import cfr_clip, vfr_clip
from slate import clock
from slate.capture import conform, media
from slate.gen import deps

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def _colours(folder, count):
    out = ""
    for i in range(count):
        px = Image.open(folder / media.frame_name(i)).convert("RGB").getpixel((5, 5))
        out += "rgb"[max(range(3), key=lambda k: px[k])]
    return out


def test_a_variable_rate_recording_shows_what_was_on_screen_at_each_frame(tmp_path):
    clip = vfr_clip(tmp_path / "rec.mp4", [(RED, 0.5), (GREEN, 0.8), (BLUE, 0.2)])
    info = media.probe(clip)
    assert info["vfr"]
    seq = media.write_sequence(clip, tmp_path / "seq", fps_num=10, fps_den=1, count=20, info=info)
    assert seq["count"] == 20 == media.count_frames(tmp_path / "seq")
    # the concat muxer rounds its timestamps (0, 0.52, 1.32 s): each film frame shows the frame on screen then
    assert _colours(tmp_path / "seq", 20) == "rrrrrrggggggggbbbbbb"


def test_a_short_recording_holds_its_last_frame_to_the_declared_length(tmp_path):
    clip = vfr_clip(tmp_path / "one.mp4", [(GREEN, 0.04)])  # simctl writes one frame for a still screen
    seq = media.write_sequence(clip, tmp_path / "seq", fps_num=25, fps_den=1, count=50)
    assert seq["count"] == 50 and set(_colours(tmp_path / "seq", 50)) == {"g"}


@pytest.mark.parametrize("method", ["dup", "mci", "blend"])
def test_native_24fps_to_the_films_rate_has_exact_counts(tmp_path, method):
    clip = cfr_clip(tmp_path / "plate.mp4", 2.0, fps=24)
    clk = clock.Clock(25, 1)
    count = clk.frame_of(2.0)
    seq = media.write_sequence(clip, tmp_path / method, fps_num=25, fps_den=1, count=count, method=method)
    assert seq["count"] == count == 50


def test_film_rate_from_film_json_never_a_constant(tmp_path):
    clip = cfr_clip(tmp_path / "plate.mp4", 1.0, fps=24)
    clk = clock.Clock(24000, 1001)  # a fractional rate (23.976), given as data like every rate
    count = clk.frame_of(1.0)
    seq = media.write_sequence(clip, tmp_path / "film", fps_num=clk.fps_num, fps_den=clk.fps_den, count=count)
    assert seq["count"] == count == 24


def test_fits(tmp_path):
    clip = vfr_clip(tmp_path / "tall.mp4", [(RED, 0.2)], size=(40, 80))
    cover = media.write_sequence(clip, tmp_path / "cover", fps_num=10, fps_den=1, count=2, size=(64, 36), fit="cover")
    assert (cover["width"], cover["height"]) == (64, 36)
    contain = media.write_sequence(clip, tmp_path / "contain", fps_num=10, fps_den=1, count=2, size=(64, 36),
                                   fit="contain", background="#00ff00")
    assert (contain["width"], contain["height"]) == (64, 36)
    corner = Image.open(tmp_path / "contain" / media.frame_name(0)).getpixel((1, 1))
    assert corner[1] > 200 and corner[0] < 60  # the pad is the background colour
    native = media.write_sequence(clip, tmp_path / "native", fps_num=10, fps_den=1, count=2)
    assert (native["width"], native["height"]) == (40, 80)


def test_a_rerun_never_mixes_two_runs_frames(tmp_path):
    clip = vfr_clip(tmp_path / "rec.mp4", [(RED, 0.3)])
    media.write_sequence(clip, tmp_path / "seq", fps_num=10, fps_den=1, count=10)
    media.write_sequence(clip, tmp_path / "seq", fps_num=10, fps_den=1, count=4)
    assert media.count_frames(tmp_path / "seq") == 4


def test_to_sequence_uses_the_films_clock(film):
    clip = vfr_clip(film / "refs" / "rec.mp4", [(RED, 0.4), (GREEN, 0.4)])
    raw = conform.to_sequence(clip, film, "demo", duration=2.0)
    fj = deps.load_film(film)
    assert media.count_frames(raw) == clock.from_film(fj).frame_of(2.0) == 50


def test_timestamped_screenshots_map_to_the_latest_one_taken(film, tmp_path):
    shots = []
    for i, colour in enumerate((RED, GREEN, BLUE)):
        p = tmp_path / f"r{i:05d}.jpg"
        Image.new("RGB", (16, 16), colour).save(p)
        shots.append(p)
    raw = conform.from_timestamps(shots, [0.0, 0.13, 0.31], film, "real", duration=0.4)
    # 25 fps: frames at 0, .04, .08, .12 (red), .16….28 (green), .32, .36 (blue)
    assert _colours(raw, 10) == "rrrrggggbb"


def test_frames_already_at_film_times_are_linked_not_re_encoded(film, tmp_path):
    shots = []
    for i in range(3):
        p = tmp_path / f"f{i:05d}.jpg"
        Image.new("RGB", (16, 16), (i * 60, 0, 0)).save(p, quality=95)
        shots.append(p)
    raw = conform.from_frames(shots, film, "virtual")
    assert media.count_frames(raw) == 3
    assert (raw / media.frame_name(1)).read_bytes() == shots[1].read_bytes()
    assert os.stat(raw / media.frame_name(1)).st_ino == os.stat(shots[1]).st_ino
