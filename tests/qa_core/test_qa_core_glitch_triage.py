"""The glitch gate's measurement triage and its blank-frame pass.

After the beat triage, every flag is measured (slate.qa.stability.explain_glitches): its ratio r, the share of its
changed pixels that sit between its neighbours', and a block match against each neighbour. Motion with r ≥ 0.55 and
fades that block matching explains are listed, not sent to a person (film B's 1,442 flags were mostly fast motion);
a flash stays open with its numbers in the evidence. Runs of blank frames are REVIEW items, triaged like the flags.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import ndimage as ndi

from slate.qa import glitch
from slate.qa.checks import picture

from qa_core_helpers import needs_ffmpeg

SIZE = (320, 180)


def _texture(w: int, h: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 2.0)
    return np.clip(128 + 40 * t / t.std(), 0, 255)


def _fast_block(n: int, *, speed: float = 6.0, flash: int | None = None) -> np.ndarray:
    """A textured card travelling `speed` px a frame across a flat ground (type moving fast trips the glitch scan)."""
    tex = _texture(100, 70, seed=9)
    out = []
    for i in range(n):
        f = np.full((SIZE[1], SIZE[0]), 50.0)
        x0 = int(30 + speed * i)
        f[55:125, x0:x0 + 100] = tex[:, : max(0, min(100, SIZE[0] - x0))]
        if i == flash:
            f[:, :200] += 70.0
        out.append(np.clip(f, 0, 255))
    g = np.stack(out).astype(np.uint8)
    return np.repeat(g[..., None], 3, axis=3)


def _still_picture(n: int, *, blank: range | None = None, level: int = 90) -> np.ndarray:
    tex = _texture(*SIZE, seed=4)
    out = []
    for i in range(n):
        out.append(np.full(tex.shape, float(level)) if blank is not None and i in blank else tex)
    g = np.stack(out).astype(np.uint8)
    return np.repeat(g[..., None], 3, axis=3)


# ---------------------------------------------------------------------------------------------------- the rules
def test_explained_needs_the_match_and_the_ratio():
    assert glitch.explained({"category": "motion", "ratio": 0.8}) == "motion"
    assert glitch.explained({"category": "motion", "ratio": 0.4}) is None  # layers passing through each other
    assert glitch.explained({"category": "fade", "ratio": 1.4, "between": 0.9}) == "fade"
    assert glitch.explained({"category": "fade", "ratio": 1.4, "between": 0.2}) is None  # n outside its neighbours
    assert glitch.explained({"category": "fade", "ratio": 0.15, "between": 0.9}) is None  # a flash scores r ≤ 0.21
    assert glitch.explained({"category": "odd", "ratio": 0.1}) is None
    assert glitch.explained({"ratio": 2.0}) is None  # never measured: a person looks


def test_blank_runs_need_a_picture_on_both_sides():
    spreads = [0.5, 0.4, 80, 82, 1.0, 1.2, 0.8, 79, 81, 2.0]
    runs = glitch.blank_runs(spreads)
    assert [(r["index"], r["len"]) for r in runs] == [(4, 3)]  # the opening fade from flat and the closing frame aren't


# ---------------------------------------------------------------------------------------------------- the gate
@needs_ffmpeg
def test_fast_motion_is_explained_by_block_matching_and_not_sent_to_a_person(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 24)])
    fb.events(1, silent="a card crossing the frame, no beats")
    fb.render(1, _fast_block(24))
    r = picture.glitch(fb.path)
    assert r.status == "PASS", r.summary
    assert r.details["flags"] > 0 and r.details["explained"]["motion"] == r.details["flags"]
    assert "motion" in r.summary and "explained by block matching" in r.summary
    report = json.loads((fb.path / "qa" / "glitch.json").read_text())
    assert report["explained"] and all(f["explained"] == "motion" and f["ratio"] >= glitch.MOTION_RATIO
                                       for f in report["explained"])
    assert all("between" in f and "explained_prev" in f for f in report["explained"])


@needs_ffmpeg
def test_a_flash_stays_open_with_its_ratio_and_match_in_the_evidence(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 30)])
    fb.events(1, silent="a still picture")
    fb.render(1, _fast_block(30, speed=0.0, flash=14))
    r = picture.glitch(fb.path)
    assert r.status == "REVIEW", r.summary
    items = r.details["review_items"]
    assert [i["key"] for i in items] == ["frame:14"]
    assert "r 0." in items[0]["what"] and "block match" in items[0]["what"] and items[0]["ratio"] < 0.55


@needs_ffmpeg
def test_a_run_of_blank_frames_between_pictures_is_flagged(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 40)])
    fb.events(1, silent="a still picture")
    fb.render(1, _still_picture(40, blank=range(20, 24)))
    r = picture.glitch(fb.path)
    assert r.status == "REVIEW", r.summary
    keys = [i["key"] for i in r.details["review_items"]]
    assert keys == ["blank:20"] and "4 blank frames" in r.details["review_items"][0]["what"]


@needs_ffmpeg
def test_blank_frames_on_a_planned_beat_or_opening_the_act_are_the_films_own(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 40)])
    fb.events(1, [fb.beat("S01", "dip", 21, kind="page")])
    fb.render(1, _still_picture(40, blank=range(20, 24)))
    r = picture.glitch(fb.path)
    assert r.status == "PASS", r.summary
    report = json.loads((fb.path / "qa" / "glitch.json").read_text())
    assert report["blanks"]["found"] == 1 and report["blanks"]["skipped_near_beats"][0]["frame"] == 20
    fb2 = film_builder(size=SIZE, shots=[(1, 40)])
    fb2.events(1, silent="opens on a flat ground")
    fb2.render(1, _still_picture(40, blank=range(0, 6)))  # a fade from a flat colour at the start
    assert picture.glitch(fb2.path).status == "PASS"
