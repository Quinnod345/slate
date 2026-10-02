"""The cuts gate (every scene change on a declared transition; no crossfading joins) and the end-hold gate.

Fixture acts are textured pictures written losslessly: a hard cut swaps one texture for another between two frames,
and a crossfade mixes them over six frames. Nothing here reads a real film.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import ndimage as ndi

from slate.qa.checks import picture

from qa_core_helpers import needs_ffmpeg, write_json

SIZE = (192, 108)


def _texture(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = ndi.gaussian_filter(rng.normal(0, 1, (SIZE[1], SIZE[0])), 1.5)
    g = np.clip(128 + 45 * t / t.std(), 0, 255)
    return np.repeat(g[..., None], 3, axis=2)


def _act(n: int, cuts: dict[int, int], fades: list[tuple[int, int, int]] = ()) -> np.ndarray:
    """n frames: a texture per stretch (cuts: {first frame: seed}), and crossfades (start, end, to seed) where frames
    start+1 … end−1 mix linearly from the picture at `start` to the texture `to`."""
    seed = cuts.get(0, 1)
    frames = []
    for i in range(n):
        seed = cuts.get(i, seed)
        frames.append(_texture(seed))
    for start, end, to in fades:
        a, b = frames[start], _texture(to)
        for i in range(start + 1, end):
            al = (i - start) / (end - start)
            frames[i] = (1 - al) * a + al * b
        for i in range(end, n):
            if i not in cuts:
                frames[i] = b
    return np.clip(np.stack(frames), 0, 255).astype(np.uint8)


def _storyboard(fb, transitions: dict[str, str]) -> None:
    shots = [{"id": s["id"], **({"transition": transitions[s["id"]]} if s["id"] in transitions else {})}
             for s in fb.shots_doc["shots"]]
    fb.storyboard({"acts": [{"shots": shots}]})


# ---------------------------------------------------------------------------------------------------------------- cuts
def test_transition_words_sort_into_the_laws_kinds():
    k = picture.transition_kind
    assert [k("carry"), k("a portal through the dot"), k("mask reveal"), k("motion-masked whip")] == \
        ["carry", "portal", "mask", "whip"]
    assert [k("UI state cut, camera locked"), k("cut on the music hit"), k("seam blend")] == \
        ["ui_cut", "music_cut", "seam"]
    assert [k("hard cut"), k("crossfade"), k("dissolve"), k(None), k("")] == ["other", "other", "other", None, None]


def test_a_transition_named_by_its_kind_is_read_as_that_kind():
    """script.json may name the law's kind itself ({kind, note}); anything else it names is not one the law allows."""
    k = picture.transition_kind
    assert [k({"kind": kind, "note": "why"}) for kind in picture.TRANSITIONS] == list(picture.TRANSITIONS)
    assert k({"kind": "Whip"}) == "whip" and k({"kind": "crossfade"}) == "other" and k({"note": "no kind"}) is None


@needs_ffmpeg
def test_a_hard_cut_mid_shot_and_an_undeclared_join_are_review(film_builder):
    """L-qa-013: one hard cut on a join with no transition declared, one landing 10 frames into a shot."""
    fb = film_builder(size=SIZE, shots=[(1, 20), (1, 20), (1, 20)])
    fb.render(1, _act(60, {0: 1, 20: 2, 30: 3}))
    r = picture.cuts(fb.path)
    assert r.status == "REVIEW", r.summary
    items = {i["key"]: i["what"] for i in r.details["review_items"]}
    assert set(items) == {"cut:20", "cut:30"}
    assert "no transition is declared" in items["cut:20"] and "S01→S02" in items["cut:20"]
    assert "10 frames into S02" in items["cut:30"] and "not on a shot start" in items["cut:30"]


@needs_ffmpeg
def test_a_declared_portal_allows_its_cut(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 20), (1, 20)])
    fb.render(1, _act(40, {0: 1, 20: 2}))
    _storyboard(fb, {"S01": "portal"})
    r = picture.cuts(fb.path)
    assert r.status == "PASS", r.summary
    assert r.details["declared"] and "portal" in r.details["declared"][0]
    _storyboard(fb, {"S01": "hard cut"})
    r = picture.cuts(fb.path)
    assert r.status == "REVIEW" and "doesn't allow a cut" in r.details["review_items"][0]["what"]


@needs_ffmpeg
def test_a_portal_named_by_its_kind_in_shots_json_allows_its_cut(film_builder):
    """`slate timing` carries a shot's transition into shots.json as transition_out; the gate reads it there, in the
    {kind, note} form too."""
    fb = film_builder(size=SIZE, shots=[(1, 20), (1, 20)])
    fb.render(1, _act(40, {0: 1, 20: 2}))
    doc = json.loads((fb.path / "timing" / "shots.json").read_text())
    doc["shots"][0]["transition_out"] = {"kind": "portal", "note": "through the dot"}
    (fb.path / "timing" / "shots.json").write_text(json.dumps(doc))
    r = picture.cuts(fb.path)
    assert r.status == "PASS", r.summary
    assert r.details["declared"] == ["film frame 20: S01→S02 (portal: through the dot)"]


@needs_ffmpeg
def test_one_hard_cut_and_one_six_frame_crossfade_at_joins(film_builder):
    """L-motion-D046: a hard cut at one join and a 6-frame crossfade across the next, neither declared."""
    fb = film_builder(size=SIZE, shots=[(1, 20), (1, 20), (1, 24)])
    frames = _act(64, {0: 1, 20: 2}, fades=[(36, 43, 3)])  # frames 37–42 blend: six frames across the join at 40
    fb.render(1, frames)
    r = picture.cuts(fb.path)
    assert r.status == "REVIEW", r.summary
    items = {i["key"]: i["what"] for i in r.details["review_items"]}
    assert set(items) == {"cut:20", "crossfade:40"}
    assert "6-frame crossfade at the join S02→S03" in items["crossfade:40"]
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    assert checks["joins"] == "REVIEW" and checks["scene_changes"] == "REVIEW"
    # declared exceptions: a whip may blend, a portal may cut
    _storyboard(fb, {"S01": "portal", "S02": "whip"})
    assert picture.cuts(fb.path).status == "PASS"
    # a seam blend inside one generated shot may run 4 frames at most
    _storyboard(fb, {"S01": "portal", "S02": "seam blend"})
    r = picture.cuts(fb.path)
    assert r.status == "REVIEW" and [i["key"] for i in r.details["review_items"]] == ["crossfade:40"]


@needs_ffmpeg
def test_a_title_fading_in_isnt_a_crossfade(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 20), (1, 20)])
    base = _texture(1)
    frames = []
    for i in range(40):
        f = base.copy()
        al = min(1.0, max(0.0, (i - 16) / 8))
        f[40:60, 40:120] = (1 - al) * f[40:60, 40:120] + al * 250  # a title box fading in across the join
        frames.append(f)
    fb.render(1, np.clip(np.stack(frames), 0, 255).astype(np.uint8))
    assert picture.cuts(fb.path).status == "PASS"


# ---------------------------------------------------------------------------------------------------------- end hold
def _ending(n: int, still_from: int) -> np.ndarray:
    frames = []
    for i in range(n):
        f = np.full((SIZE[1], SIZE[0], 3), 40, np.uint8)
        x = 10 + min(i, still_from)  # a mark travels, then settles at still_from and holds
        f[40:60, x:x + 20] = 230
        frames.append(f)
    return np.stack(frames)


@needs_ffmpeg
def test_the_end_card_holds_its_last_three_seconds(film_builder):
    """L-finish-D008: at 30 fps, the last 90 frames must be identical to the last frame."""
    fb = film_builder(size=SIZE, shots=[(1, 150)])
    fb.master(_ending(150, still_from=50))
    r = picture.end_hold(fb.path)
    assert r.status == "PASS", r.summary
    assert r.details["hold_frames"] >= 90 and r.details["needed_s"] == 3.0


@needs_ffmpeg
def test_an_end_card_that_moves_fails_with_the_frame(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 150)])
    fb.master(_ending(150, still_from=110))  # still from frame 110: only 40 frames (1.33 s) of hold
    r = picture.end_hold(fb.path)
    assert r.status == "FAIL", r.summary
    assert r.details["first_moving_frame"] == 109 and "film frame 109" in r.summary


@needs_ffmpeg
def test_a_film_or_format_can_say_how_long_its_end_holds(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 150)])
    fb.master(_ending(150, still_from=110))
    film = json.loads((fb.path / "film.json").read_text())
    film["targets"]["end_hold_s"] = 1.0
    write_json(fb.path / "film.json", film)
    assert picture.end_hold(fb.path).status == "PASS"
    film["targets"]["end_hold_s"] = 0
    write_json(fb.path / "film.json", film)
    r = picture.end_hold(fb.path)
    assert r.status == "PASS" and "no end hold" in r.summary


def test_a_seamless_loop_asks_for_no_hold():
    from slate import formats

    need, why = formats.end_hold_for({"name": "loop-format", "loop": {"seamless": True}})
    assert need == 0 and "seamless loop" in why
    assert formats.end_hold_for({"name": "explainer"})[0] == 3.0
    assert formats.end_hold_for({"name": "x", "end_hold_s": 1.5})[0] == 1.5


def test_no_master_is_unchecked(film_builder):
    fb = film_builder(size=SIZE, shots=[(1, 30)])
    r = picture.end_hold(fb.path)
    assert r.status == "UNCHECKED" and "slate master" in " ".join(r.evidence)
