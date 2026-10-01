"""glitch (scan and triage), psnr and the frame tiles behind them."""

import json

import numpy as np
import pytest

from slate import gates
from slate.qa import accept, glitch, media
from slate.qa.checks import picture

from qa_core_helpers import moving_square, needs_ffmpeg, write_json


def _grey(frames: np.ndarray) -> list[np.ndarray]:
    return [f.mean(axis=2).astype(np.float32) for f in frames]


# ---------------------------------------------------------------------------------------------- the scan
def test_scan_finds_one_and_two_frame_glitches_and_ignores_smooth_motion():
    size = (480, 270)
    smooth = moving_square(40, size, speed=2.0, side=60)
    assert glitch.scan_frames(_grey(smooth)) == []
    one = moving_square(40, size, speed=2.0, side=60, vanish=(20,))
    hits = glitch.scan_frames(_grey(one))
    assert [(h["index"], h["len"]) for h in hits] == [(20, 1)]
    assert hits[0]["score"] > glitch.THRESHOLD
    two = moving_square(40, size, speed=2.0, side=60, vanish=(20, 21))
    hits = glitch.scan_frames(_grey(two))
    assert (20, 2) in [(h["index"], h["len"]) for h in hits]


def test_scan_ignores_a_real_cut():
    size = (480, 270)
    a = moving_square(20, size, speed=1.0, side=60, bg=20)
    b = moving_square(20, size, speed=1.0, side=60, bg=200, fg=40, start=200)
    assert glitch.scan_frames(_grey(np.concatenate([a, b]))) == []


def test_analysis_size_keeps_the_aspect_on_whole_tiles():
    assert media.analysis_size(16, 9) == (480, 270)
    assert media.analysis_size(9, 16) == (270, 480)
    assert media.analysis_size(1, 1) == (480, 480)
    w, h = media.analysis_size(4, 5)
    assert h == 480 and w % 30 == 0


# ---------------------------------------------------------------------------------------------- triage
def test_triage_window_is_plus_minus_three_frames():
    flags = [{"frame": f, "len": 1, "score": 9.0} for f in (7, 10, 16, 17, 40)]
    flags.append({"frame": 22, "len": 2, "score": 8.0})  # covers 22 and 23
    remaining, skipped = glitch.triage(flags, beats=[13, 26])
    assert sorted(f["frame"] for f in skipped) == [10, 16, 22]  # 3 from 13, 3 from 13, 23 is 3 from 26
    assert sorted(f["frame"] for f in remaining) == [7, 17, 40]  # 6, 4 and 14 frames from the nearest beat
    by = {f["frame"]: f for f in skipped}
    assert by[10]["near_beat"] == 13 and by[10]["distance"] == 3
    assert by[22]["near_beat"] == 26 and by[22]["distance"] == 3


def test_triage_without_beats_keeps_everything():
    flags = [{"frame": 5, "len": 1, "score": 7.0}]
    assert glitch.triage(flags, beats=[]) == (flags, [])


# ---------------------------------------------------------------------------------------------- the gate
@needs_ffmpeg
def test_glitch_gate_review_then_skipped_near_a_beat_then_accepted(film_builder, monkeypatch):
    fb = film_builder(size=(96, 54), shots=[(1, 30), (2, 30)])
    fb.all_events()  # beats at frames 2, 32
    fb.render_all(speed=1.0, vanish=(45,))
    r = picture.glitch(fb.path)
    assert r.status == "REVIEW", r.summary
    keys = [i["key"] for i in r.details["review_items"]]
    assert keys == ["frame:45"]
    report = json.loads((fb.path / "qa" / "glitch.json").read_text())
    assert report["open"][0]["frame"] == 45
    # a planned beat two frames away: the flag is the film's own move, skipped and listed
    fb.events(2, [fb.beat("S03", "s03_in", 32), fb.beat("S03", "pop", 47, kind="impact")])
    r = picture.glitch(fb.path)
    assert r.status == "PASS" and r.details["skipped"] == 1 and "within ±3 frames" in r.summary
    # back without the beat, a person accepts the flag on this exact render
    fb.all_events()
    r = picture.glitch(fb.path)
    gates.write_report(fb.path, [r])
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    accept.accept(fb.path, "glitch:frame:45", by="Quinn", reason="the dot blinks on purpose", mode="supervised")
    r = picture.glitch(fb.path)
    assert r.status == "PASS" and r.details["accepted"][0]["accepted_by"] == "Quinn"


@needs_ffmpeg
def test_glitch_scan_is_cached_per_render(film_builder, monkeypatch):
    fb = film_builder(shots=[(1, 30)])
    fb.render_all()
    assert picture.glitch(fb.path).status == "PASS"

    def boom(*a, **k):
        raise AssertionError("scanned again")

    monkeypatch.setattr(glitch, "scan", boom)
    monkeypatch.setattr(glitch, "scan_render", boom)
    assert picture.glitch(fb.path).status == "PASS"  # the cache answered


@needs_ffmpeg
def test_glitch_unchecked_for_missing_acts_and_without_renders(film_builder):
    fb = film_builder(shots=[(1, 30), (2, 30)])
    assert picture.glitch(fb.path).status == "UNCHECKED"
    fb.render(1, moving_square(30, fb.size))
    r = picture.glitch(fb.path)
    assert r.status == "UNCHECKED" and "acts 2" in r.summary


# ---------------------------------------------------------------------------------------------- psnr gate
@needs_ffmpeg
def test_psnr_gate_passes_an_identical_master(film_builder):
    fb = film_builder(shots=[(1, 12), (2, 12)])
    film = fb.render_all()
    fb.master(film)
    r = picture.psnr(fb.path)
    assert r.status == "PASS", r.summary
    assert r.engine["name"] == "ffmpeg psnr" and r.engine["version"] != "not installed"
    assert r.details["summary"]["frames"] == 24
    per_frame = json.loads((fb.path / r.details["report"]).read_text())["per_frame_db"]
    assert len(per_frame) == 24


@needs_ffmpeg
def test_psnr_gate_fails_a_changed_frame_and_a_short_master(film_builder):
    fb = film_builder(shots=[(1, 12), (2, 12)])
    film = fb.render_all()
    bad = film.copy()
    bad[17] = 255 - bad[17]
    fb.master(bad)
    r = picture.psnr(fb.path)
    assert r.status == "FAIL" and "film frame 17" in r.summary
    (fb.path / "masters" / "master.mkv").unlink()
    fb.master(film[:22])
    r = picture.psnr(fb.path)
    assert r.status == "FAIL" and "the master has 22 frames, the film has 24" in r.summary
    assert r.details["summary"]["frames"] == 22  # compared up to the shorter side, never a repeated last frame
    assert r.details["summary"]["below_count"] == 0


@needs_ffmpeg
def test_psnr_gate_unchecked_without_master_or_renders(film_builder):
    fb = film_builder(shots=[(1, 12), (2, 12)])
    assert picture.psnr(fb.path).status == "UNCHECKED"
    film = moving_square(24, fb.size)
    fb.master(film)
    fb.render(1, film[:12])
    r = picture.psnr(fb.path)
    assert r.status == "UNCHECKED" and "acts 2" in r.summary


@needs_ffmpeg
def test_psnr_gate_prefers_the_mezzanine_master(film_builder):
    fb = film_builder(shots=[(1, 12)])
    film = fb.render_all()
    fb.master(film, name="a_delivery.mp4", codec="mpeg4")
    fb.master(film, name="b_master.mkv")
    r = picture.psnr(fb.path)
    assert r.details["master"] == "masters/b_master.mkv"
    assert r.status == "PASS"
