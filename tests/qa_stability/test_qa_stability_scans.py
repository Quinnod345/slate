"""The stability scans on synthetic frames: each class is found where it is and nowhere else, pieces equal one pass,
and the triage sorts flags the way the films agreed (beats for pops only, references and baselines never for a
reversal). Pure numpy: no ffmpeg, no film folder."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage as ndi

from slate.qa import stability as S


# ---------------------------------------------------------------------------------------------------- the motion scan
@pytest.mark.parametrize("size", [(480, 270), (270, 480), (640, 360)])
def test_selftest_flags_each_injected_fault_at_its_frame_and_nothing_else(size):
    r = S.selftest(S.made_up_still(size))
    assert r.status == "PASS", r.summary
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    assert checks == {"flash at 10": "PASS", "shimmer at 20-25": "PASS", "grey face at 30": "PASS",
                      "snap at 35": "PASS", "veil at 42": "PASS", "reversal at 53": "PASS", "untouched": "PASS"}


def test_selftest_fails_loudly_when_the_scan_is_blind(monkeypatch):
    """A scan that can't trust any tile (texture floor set impossibly high) misses the snap and the reversal: the
    positive control says so instead of passing."""
    monkeypatch.setattr(S, "TEX", 1e9)
    r = S.selftest(S.made_up_still((480, 270)))
    assert r.status == "FAIL"
    failed = {c["check"] for c in r.details["checks"] if c["status"] == "FAIL"}
    assert {"snap at 35", "reversal at 53"} <= failed


def test_a_smooth_glide_is_quiet_and_a_whole_pixel_snap_is_not(helpers):
    still = helpers.textured((320, 180), seed=5)
    glide = helpers.drift(still, 40, vx=0.04)
    assert [e for e in S.scan_motion(glide) if e["class"] != "shimmer"] == []
    # a line of type stepping one whole pixel down at frame 20 while everything else glides on
    snapped = glide.copy()
    x, y, w, h = 120, 60, 120, 90
    for i in range(20, 40):
        snapped[i, y + 1:y + h, x:x + w] = glide[i, y:y + h - 1, x:x + w]
    ev = S.scan_motion(snapped)
    snaps = [e for e in ev if e["class"] == "snap"]
    assert snaps and {e["local"] for e in snaps} == {20}
    assert all(x - 30 <= e["region"][0] <= x + w and y - 30 <= e["region"][1] <= y + h for e in snaps)
    assert all(0.8 <= e["severity"] <= 1.2 for e in snaps)


def test_a_number_recentring_wobbles(helpers):
    """A near-still number steps a whole pixel and back two frames later (re-centring as its digits change width)."""
    still = helpers.textured((320, 180), seed=8)
    frames = helpers.card_frames(still, 30, (90, 60, 120, 90), {14: (1, 0), 16: (0, 0)})
    wobbles = [e for e in S.scan_motion(frames) if e["class"] == "wobble"]
    assert len(wobbles) >= 3 and {e["local"] for e in wobbles} == {16}
    assert all(e["gap"] == 2 for e in wobbles)


def test_a_shaking_part_is_flagged_as_shake_with_its_frames_tiles_and_severity(helpers):
    """The jitter pass every act render gets (L-qa-D033): a part shaking back and forth by about a pixel while the
    rest of the frame holds still is `shake`, reported at its own frames and tiles with a severity in px."""
    still = helpers.textured((320, 180), seed=11)
    x, y, w, h = 120, 60, 90, 60
    frames = []
    for i in range(30):
        f = still.copy()
        if 10 <= i < 20:
            f[y:y + h, x:x + w] = ndi.shift(still, (0, 0.6 if i % 2 else -0.6), order=3, mode="nearest")[y:y + h,
                                                                                                         x:x + w]
        frames.append(f)
    shakes = [e for e in S.scan_motion(np.stack(frames).astype(np.float32)) if e["class"] == "shake"]
    assert shakes
    assert {e["local"] for e in shakes} <= set(range(10, 21))
    tile = S.TILE_PX
    assert all(x - tile <= e["region"][0] <= x + w and y - tile <= e["region"][1] <= y + h for e in shakes)
    assert all(0.3 <= e["severity"] <= 1.5 and e["reversals"] >= 3 for e in shakes)


def test_pieces_give_exactly_the_events_of_one_pass():
    frames, _ = S.inject_faults(S.made_up_still((320, 180)))
    one = S.scan_motion(frames, segment=1000)
    pieces = S.scan_motion(frames, segment=23, pad=S.PAD)  # pieces end inside the flash, the shimmer and the reversal
    key = lambda e: (e["class"], e["local"], tuple(e["tile"]), e["severity"])  # noqa: E731
    assert sorted(map(key, pieces)) == sorted(map(key, one)) and len(one) > 20


def test_film_frames_and_local_frames():
    frames, _ = S.inject_faults(S.made_up_still((320, 180)))
    ev = S.scan_motion(frames, first_film_frame=1000)
    assert ev and all(e["frame"] == 1000 + e["local"] for e in ev)


# ---------------------------------------------------------------------------------------------------- pops and glitches
def _square_frames(n: int, size=(320, 180), *, appear: int | None = None, vanish: int | None = None,
                   cut: int | None = None, move: float = 0.0) -> np.ndarray:
    w, h = size
    rng = np.random.default_rng(2)
    ground = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 3.0)
    ground = 90 + 10 * ground / ground.std()
    other = 200 - ground
    out = []
    for i in range(n):
        f = (other if cut is not None and i >= cut else ground).copy()
        x0 = int(140 + move * i)
        if (appear is None or i >= appear) and i != vanish:
            f[70:110, x0:x0 + 40] = 245.0
        out.append(f)
    return np.stack(out).astype(np.float32)


def test_a_card_that_pops_in_and_holds_is_a_pop_not_a_glitch():
    r = S.scan(_square_frames(30, appear=12))
    pops = [f for f in r["flags"] if f["class"] == "pop"]
    assert [f["local"] for f in pops] == [12]
    assert not [f for f in r["flags"] if f["class"].startswith("glitch_")]
    assert r["cuts"] == []


def test_a_cut_is_a_cut_not_a_pop():
    r = S.scan(_square_frames(30, cut=15))
    assert r["cuts"] == [15]
    assert not [f for f in r["flags"] if f["class"] == "pop"]


def test_a_one_frame_flash_is_an_odd_glitch():
    """A large area flashing for one frame: no shift of a neighbour explains it, it isn't a small change in place,
    and the frames either side agree, so it's the odd one out. (A small shape vanishing near the edge of the matcher's
    reach can be explained by a shift, as in the films' triage: that's why "look" and "motion" exist.)"""
    frames = _square_frames(30)
    frames[14, :, :200] += 60.0
    r = S.scan(frames)
    odd = [f for f in r["flags"] if f["class"] == "glitch_odd"]
    assert [f["local"] for f in odd] == [14]
    assert not [f for f in r["flags"] if f["class"] == "pop"]


def test_fast_motion_glitch_flags_are_explained_as_motion(helpers):
    """Type travelling several pixels a frame changes a tile as much as a flash does, so it trips the glitch scan;
    block matching finds the shift that explains it, and the flags are filed as motion."""
    tex = helpers.textured((100, 70), seed=9)
    frames = []
    for i in range(24):
        f = np.full((180, 320), 50.0, np.float32)
        x0 = 30 + 6 * i
        f[55:125, x0:x0 + 100] = tex
        frames.append(f)
    r = S.scan(np.stack(frames))
    glitch = [f for f in r["flags"] if f["class"].startswith("glitch_")]
    assert glitch, "a fast move should trip the glitch scan"
    assert {f["class"] for f in glitch} == {"glitch_motion"}
    assert r["glitch"]["categories"]["motion"] == len(glitch)


# ---------------------------------------------------------------------------------------------------- triage
def _flag(cls: str, local: int, tile=(5, 5), base: int = 100, sev: float = 1.0) -> dict:
    return {"class": cls, "local": local, "frame": base + local, "tile": list(tile), "severity": sev}


def test_triage_beats_skip_pops_but_never_the_motion_classes():
    flags = [_flag("pop", 3), _flag("snap", 3), _flag("pop", 20)]
    t = S.triage(flags, beats=[100])
    assert [(f["class"], f["local"]) for f in t["near_beats"]] == [("pop", 3)]
    assert t["near_beats"][0]["near_beat"] == 100 and t["near_beats"][0]["distance"] == 3
    assert sorted((f["class"], f["local"]) for f in t["open"]) == [("pop", 20), ("snap", 3)]


def test_triage_reference_shares_within_two_frames_and_one_tile():
    flags = [_flag("snap", 10, (5, 5)), _flag("snap", 30, (5, 5)), _flag("flicker", 10, (5, 5))]
    ref = [_flag("snap", 12, (6, 4)), _flag("snap", 33, (5, 5)), _flag("shimmer", 10, (5, 5))]
    t = S.triage(flags, reference=ref)
    assert [(f["class"], f["local"]) for f in t["shared_with_reference"]] == [("snap", 10)]
    assert sorted((f["class"], f["local"]) for f in t["open"]) == [("flicker", 10), ("snap", 30)]


def test_triage_baseline_accepts_within_one_frame_and_one_tile():
    flags = [_flag("wobble", 10, (5, 5)), _flag("wobble", 20, (5, 5))]
    base = [{"class": "wobble", "local": 11, "tile": [4, 6]}, {"class": "wobble", "local": 22, "tile": [5, 5]}]
    t = S.triage(flags, baseline=base)
    assert [f["local"] for f in t["accepted_by_baseline"]] == [10]
    assert [f["local"] for f in t["open"]] == [20]


def test_a_reversal_is_never_excused():
    flags = [_flag("reversal", 10)]
    same = [_flag("reversal", 10)]
    t = S.triage(flags, beats=[110], reference=same, baseline=same)
    assert [f["class"] for f in t["open"]] == ["reversal"]


def test_counts_are_frames_for_motion_classes_and_flags_for_pops():
    flags = [_flag("snap", 5, (1, 1)), _flag("snap", 5, (2, 1)), _flag("snap", 9), _flag("pop", 3), _flag("pop", 3, (9, 9))]
    c = S.counts(flags)
    assert c["snap"] == 2 and c["pop"] == 2 and c["wobble"] == 0


# ---------------------------------------------------------------------------------------------------- morph
def _morph(helpers, n: int = 30, *, subject_rounded: bool = False, control_rounded: bool = True):
    size = (420, 160)
    # type-like detail inside each card, kept well clear of every edge as the card shrinks
    tex = np.clip(ndi.gaussian_filter(np.random.default_rng(4).normal(0, 1, (40, 90)), 1.5) * 60, 0, 120)
    cards, frames = [], []
    centres = {"control": 100.3, "subject": 300.6}
    rects = {k: [] for k in centres}
    for f in range(n):
        u = f / n
        w_, h_ = 140.3 - 8 * u, 100.2 - 5 * u  # shrinking slowly, on a sub-pixel spot
        img = np.full((size[1], size[0]), 40.0, np.float32)
        for name, cx in centres.items():
            cy = 80.4
            rect = (cx - w_ / 2, cy - h_ / 2, cx + w_ / 2, cy + h_ / 2)
            rects[name].append(list(rect))
            rounded = control_rounded if name == "control" else subject_rounded
            one = helpers.card(size, rect, texture=tex, content_at=(rect[0] + 14, rect[1] + 14), rounded=rounded)
            mask = np.zeros((size[1], size[0]), bool)
            mask[:, int(cx - 80):int(cx + 80)] = True
            img = np.where(mask, one, img)
        frames.append(img)
    for name in centres:
        r0 = rects[name][0]
        content = [int(r0[0]) + 16, int(r0[1]) + 16, int(r0[0]) + 100, int(r0[1]) + 52]
        cards.append({"name": name, "role": name, "rects": rects[name], "content": content})
    return np.stack(frames), cards


def test_morph_true_edges_pass_and_the_control_steps_whole_pixels(helpers):
    frames, cards = _morph(helpers)
    r = S.morph_check(frames, cards)
    assert r.status == "PASS", r.summary
    m = r.details["cards"]
    assert m["control"]["worst_jump_px"] >= 0.8 and m["subject"]["worst_jump_px"] < 0.5
    assert m["subject"]["content_px"] <= 0.1


def test_morph_rounded_edges_fail(helpers):
    frames, cards = _morph(helpers, subject_rounded=True)
    r = S.morph_check(frames, cards)
    assert r.status == "FAIL"
    assert any(c["check"] == "subject:subject" and c["status"] == "FAIL" for c in r.details["checks"])


def test_morph_says_when_it_has_gone_blind(helpers):
    frames, cards = _morph(helpers, control_rounded=False)
    r = S.morph_check(frames, cards)
    assert r.status == "FAIL"
    assert any(c["check"] == "control:control" and c["status"] == "FAIL" for c in r.details["checks"])


# ---------------------------------------------------------------------------------------------------- conformance
def test_conformance_refuses_without_a_bench(tmp_path):
    from slate.errors import MissingPrerequisite

    with pytest.raises(MissingPrerequisite) as exc:
        S.run_conformance(spec_path=tmp_path / "missing.json")
    assert "stability bench" in exc.value.message and "selftest" in exc.value.hint
    spec = tmp_path / "bench.json"
    spec.write_text('{"variants": {"base": {}}, "steps": []}')
    with pytest.raises(MissingPrerequisite) as exc:
        S.run_conformance(None, spec)
    assert "slate qa stability conformance --render" in exc.value.hint


def test_conformance_runs_its_steps_on_bench_renders(helpers):
    still = helpers.textured((320, 180), seed=11)
    smooth = helpers.drift(still, 24, vx=0.05)
    snapping = smooth.copy()
    for i in range(12, 24):
        snapping[i, 61:150, 100:220] = smooth[i, 60:149, 100:220]
    frames, cards = _morph(helpers)
    renders = {"base": snapping, "kit": smooth, "kit-2": smooth.copy(), "rest": smooth[:1], "morph": frames,
               "still": S.made_up_still((480, 270))[None]}
    spec = {"bench": "fixture", "steps": [
        {"step": "scan", "render": "base", "expect": "fail", "classes": ["snap"]},
        {"step": "scan", "render": "kit", "expect": "pass", "classes": ["snap", "relative", "shake", "flicker"]},
        {"step": "crisp_last", "render": "kit", "reference": "kit-2", "frame": -1},
        {"step": "crisp_moving", "render": "kit", "reference": "kit-2", "box": [100, 60, 120, 90], "count": 24},
        {"step": "rest", "render": "rest", "reference": "rest", "frame": 0},
        {"step": "identical", "a": "kit", "b": "kit-2"},
        {"step": "selftest", "render": "still", "frame": 0},
        {"step": "morph", "render": "morph", "cards": cards}]}
    r = S.conformance(spec, renders)
    assert r.status == "PASS", [c for c in r.details["checks"] if c["status"] != "PASS"]
    assert len(r.details["checks"]) == 8
    spec["steps"][1]["render"] = "base"  # the snapping variant can't pass as the kit
    bad = S.conformance(spec, renders)
    assert bad.status == "FAIL" and bad.details["checks"][1]["status"] == "FAIL"


def test_conformance_step_that_cant_run_is_unchecked_not_passed():
    r = S.conformance({"steps": [{"step": "identical", "a": "x", "b": "y"}]}, {})
    assert r.status == "UNCHECKED"


# ---------------------------------------------------------------------------------------------------- regression
def _card_scene(helpers, n: int = 16, dx: int = 0) -> np.ndarray:
    """A resting scene: textured ground and a sharp-edged light card (edges a crispness check can read)."""
    still = helpers.textured((320, 180), seed=12)
    still[50:110, 70 + dx:170 + dx] = 230.0
    return np.repeat(still[None], n, axis=0)


def test_regression_passes_a_rendering_only_change(helpers):
    approved = _card_scene(helpers)
    r = S.compare_renders(approved, approved + 0.4)
    assert r.status == "PASS", r.summary
    assert {c["check"] for c in r.details["checks"]} >= {"structure", "rest"}


def test_regression_catches_what_moved_what_softened_and_a_short_render(helpers):
    approved = _card_scene(helpers)
    moved = S.compare_renders(approved, _card_scene(helpers, dx=6))
    assert moved.status == "FAIL" and any(c["check"] == "structure" and c["status"] == "FAIL"
                                         for c in moved.details["checks"])
    softer = S.compare_renders(approved, np.stack([ndi.gaussian_filter(f, 1.2) for f in approved]))
    assert any(c["check"] == "rest" and c["status"] == "FAIL" for c in softer.details["checks"])
    short = S.compare_renders(approved, approved[:-1])
    assert any(c["check"] == "length" and c["status"] == "FAIL" for c in short.details["checks"])
    # a change that's meant: its region left out, everything else still checked
    assert S.compare_renders(approved, _card_scene(helpers, dx=6), ignore=[(50, 30, 150, 100)]).status == "PASS"
