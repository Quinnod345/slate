"""The palette and sync measures `slate revise check` runs on a revision of approved work (perfect, don't replace)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from slate.qa import revise_hooks as H

from qa_core_helpers import moving_square, needs_ffmpeg, write_json


def _image(path, rgb, size=(64, 36)):
    arr = np.zeros((size[1], size[0], 3), np.uint8)
    arr[...] = rgb
    arr[8:20, 8:30] = (240, 240, 235)  # a light card on the ground
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)
    return path


def test_the_same_palette_passes_and_a_shifted_one_goes_to_a_person(tmp_path):
    approved = _image(tmp_path / "archive" / "still.png", (30, 60, 90))
    same = _image(tmp_path / "renders" / "still.png", (30, 60, 90))
    r = H.palette(tmp_path, approved, same)
    assert r.status == "PASS" and r.details["mean_de"] == pytest.approx(0, abs=1e-9)
    warmer = _image(tmp_path / "renders" / "warm.png", (70, 60, 60))
    r = H.palette(tmp_path, approved, warmer)
    assert r.status == "REVIEW" and "the palette moved" in r.summary and r.details["mean_de"] > H.MEAN_DE
    assert r.engine["name"].startswith("slate qa palette")


def test_the_palette_measure_needs_the_approved_version(tmp_path):
    current = _image(tmp_path / "renders" / "still.png", (30, 60, 90))
    assert H.palette(tmp_path, None, current).status == "UNCHECKED"
    assert H.palette(tmp_path, tmp_path / "missing.png", current).status == "UNCHECKED"


@needs_ffmpeg
def test_videos_are_compared_at_the_same_points(film_builder):
    fb = film_builder(shots=[(1, 30)])
    frames = moving_square(30, fb.size)
    a = fb.encode("archive/act1.mkv", frames)
    b = fb.encode("renders/act1.mkv", frames)
    r = H.palette(fb.path, a, b)
    assert r.status == "PASS" and len(r.details["samples"]) == H.SAMPLES
    tinted = frames.copy()
    tinted[..., 2] = np.clip(tinted[..., 2].astype(int) + 60, 0, 255).astype(np.uint8)
    c = fb.encode("renders/act1_blue.mkv", tinted)
    assert H.palette(fb.path, a, c).status == "REVIEW"


def test_which_files_each_measure_applies_to():
    from pathlib import Path

    assert H.applies_palette(Path("renders/act1.mov")) and H.applies_palette(Path("review/sheet.png"))
    assert not H.applies_palette(Path("timing/vo_cues.json"))
    for p in ("film/timing/visual_events/act3.json", "timing/vo_cues.json", "sound/mix/final_mix.wav",
              "sound/mix/stems/dialogue.wav"):
        assert H.applies_sync(Path(p)), p
    assert not H.applies_sync(Path("timing/shots.json")) and not H.applies_sync(Path("renders/act1.mov"))


def _beats_film(film_builder):
    fb = film_builder(shots=[(1, 30), (2, 30)])
    fb.all_events()
    approved = fb.path / "archive" / "revisions" / "r1" / "act1.json"
    approved.parent.mkdir(parents=True)
    approved.write_text((fb.path / "timing" / "visual_events" / "act1.json").read_text())
    return fb, approved


def test_unmoved_beats_still_wait_for_the_films_sync_to_be_confirmed(film_builder):
    fb, approved = _beats_film(film_builder)
    r = H.sync(fb.path, approved, fb.path / "timing" / "visual_events" / "act1.json")
    assert r.status == "UNCHECKED" and "nothing moved" in r.summary  # no sound verification yet: never a quiet pass
    assert r.details["film_sync"]["status"] == "UNCHECKED"


def test_a_moved_beat_is_listed_for_a_person(film_builder):
    fb, approved = _beats_film(film_builder)
    current = fb.path / "timing" / "visual_events" / "act1.json"
    beats = json.loads(current.read_text())
    beats[0]["t"] = round(beats[0]["t"] + 3 / 30, 6)  # three frames later
    beats.append(fb.beat("S01", "extra", 20))
    write_json(current, beats)
    r = H.sync(fb.path, approved, current)
    assert r.status == "REVIEW" and len(r.details["moved"]) == 1 and len(r.details["added"]) == 1
    assert "+100 ms" in r.details["moved"][0]


def test_a_revision_that_breaks_the_films_sync_fails(film_builder):
    fb, approved = _beats_film(film_builder)
    (fb.path / "timing" / "visual_events" / "act2.json").unlink()  # an act left without beats
    r = H.sync(fb.path, approved, fb.path / "timing" / "visual_events" / "act1.json")
    assert r.status == "FAIL" and "out of sync" in r.summary


def test_the_hooks_register_with_the_state_module(monkeypatch):
    revise = pytest.importorskip("slate.state.revise")
    monkeypatch.setattr(revise, "HOOKS", {})
    H._register()
    assert set(revise.HOOKS) == {"palette", "sync"}
    assert all(h.owner == "slate.qa" for h in revise.HOOKS.values())
