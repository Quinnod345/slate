"""Pure-array coverage of cue identity, frame clocks, placement, returns and planned silence."""
import copy

import numpy as np
import pytest

from slate.errors import MissingPrerequisite, UsageError
from slate.sound import dsp
from slate.sound.effects import rendering, voices


def cue(**extra):
    return {"id": "hit-1", "type": "wood", "kind": "tap", "family": "accents", "stem": "ui", "t": 0.1,
            "gain": -18, "params": {"kind": "block", "seed": 34, "dur": 0.12}, **extra}


def test_cue_is_deterministic_varied_and_does_not_mutate_parameters():
    c = cue()
    before = copy.deepcopy(c)
    one, offset = rendering.render_cue(c, sample_rate=24000)
    two, _ = rendering.render_cue(c, sample_rate=24000)
    assert np.array_equal(one, two) and c == before and offset == 0
    assert one.shape == (2880, 2) and np.max(np.abs(one)) == pytest.approx(dsp.db(-18), abs=1e-10)
    varied, _ = rendering.render_cue(cue(params={"kind": "block", "seed": 35, "dur": 0.12}), sample_rate=24000)
    assert not np.array_equal(one, varied)


@pytest.mark.parametrize("voice", ["tone", "pad", "note", "bell", "pluck", "tick"])
def test_retired_synthetic_tonal_voices_cannot_return(voice):
    with pytest.raises(UsageError, match="real samples"):
        rendering.render_cue({"type": voice, "dur": 0.1, "pitch": "C4"})


def test_missing_recorded_air_does_not_become_a_synthesized_substitute(tmp_path, monkeypatch):
    monkeypatch.setenv("SLATE_APPLE_LIBRARY", str(tmp_path))
    with pytest.raises(MissingPrerequisite, match="recorded sample is missing"):
        rendering.render_cue({"type": "sample", "file": "FCP:Motions & Transitions/Swish 1.caf"})


def test_layered_cue_keeps_relative_gain_and_offsets():
    a = {"type": "wood", "kind": "temple", "dur": 0.05, "gain": -3, "seed": 2}
    b = {"type": "sub", "dur": 0.06, "gain": -12, "offset": 0.03}
    y, _ = rendering.render_cue({"type": "layered", "gain": -10, "params": {"layers": [a, b]}}, sample_rate=24000)
    assert y.shape == (2160, 2) and np.max(np.abs(y)) == pytest.approx(dsp.db(-10), abs=1e-10)
    ya, _ = rendering.render_cue(a, sample_rate=24000)
    yb, _ = rendering.render_cue(b, sample_rate=24000)
    expected = np.zeros_like(y)
    expected[:len(ya)] += ya
    expected[720:720 + len(yb)] += yb
    expected *= dsp.db(-10) / (np.max(np.abs(expected)) + 1e-12)
    assert np.allclose(y, expected, atol=1e-11)


def test_counter_uses_its_film_clock_descends_and_never_exceeds_visual_span():
    c = {"type": "countup3", "params": {"from": 9, "to": 1, "dur": 0.47, "hold_frames": 3,
          "fps_num": 24, "fps_den": 1, "scale": ["C5", "D5", "E5"], "seed": 7, "ease": "linear"}}
    ticks = voices.count_ticks(c, 24000)
    assert [tick["at"] for tick in ticks] == pytest.approx([0.125, 0.25, 0.375, 0.47], abs=1/24000)
    assert ticks[0]["params"]["pitch"] > ticks[-1]["params"]["pitch"]
    assert len({tick["params"]["seed"] for tick in ticks}) == len(ticks)
    with pytest.raises(UsageError, match="film's fps_num"):
        voices.count_ticks({**c, "params": {**c["params"], "fps_num": 0}})


def test_returns_are_split_and_silences_stop_dry_and_wet_together():
    sr, n = 16000, 12000
    c = cue(reverb=-9)
    second = cue(id="note-2", stem="tonal", family="swells", t=0.2, reverb=-6)
    # A wood voice on a tonal bus exercises routing without using the machine's sample library.
    result = rendering.render_parts([c, second], n, sample_rate=sr,
               silences=[{"from": 0.5, "to": 0.75, "ramp": 0.02, "stems": ["ui", "tonal", "verb", "tverb"]}])
    assert np.any(result["stems"]["verb"]) and np.any(result["stems"]["tverb"])
    assert not np.array_equal(result["stems"]["verb"], result["stems"]["tverb"])
    for stem in ("ui", "tonal", "verb", "tverb"):
        assert np.all(result["stems"][stem][8000:] == 0)
    assert np.allclose(sum(result["stems"].values()), sum(result["families"].values()))


def test_processing_order_is_trim_then_hit_then_send_then_bus_once():
    sr, n = 16000, 8000
    c = cue(reverb=-6)
    direct, _ = rendering.render_cue(c, sample_rate=sr)
    calls = []
    def per_hit(cue, segment, start):
        assert np.allclose(segment, direct * dsp.db(-3))
        calls.append(("hit", cue["id"]))
        return segment * 0.5, {"treatment": "test"}
    def per_bus(family, stem, samples):
        calls.append(("bus", stem))
        return samples * 0.25
    result = rendering.render_parts([c], n, sample_rate=sr, trims={"ui": -3},
                                     process_cue=per_hit, process_bus=per_bus)
    assert calls == [("hit", "hit-1"), ("bus", "ui"), ("bus", "verb")]
    start = int(c["t"] * sr)
    assert np.allclose(result["stems"]["ui"][start:start + len(direct)], direct * dsp.db(-3) * 0.5 * 0.25)
    assert result["kept"][0][:4] == ("hit-1", "accents", "ui", start)
    assert result["stem_trim_applied"] == {"ui": -3}


def test_gesture_notes_are_placed_once_with_individual_anchors():
    parent = cue(type="countup3", notes=[{"type": "wood", "t": 0.1, "params": {"kind": "block", "seed": 1, "dur": 0.04}},
                                         {"type": "wood", "t": 0.3, "params": {"kind": "block", "seed": 2, "dur": 0.04}}])
    result = rendering.render_parts([parent], 8000, sample_rate=16000)
    assert [entry[0] for entry in result["kept"]] == ["hit-1.1", "hit-1.2"]
    assert np.all(result["stems"]["ui"][2400:4800] == 0)


def test_negative_start_crops_without_wrapping_and_silence_window_clips():
    c = cue(t=-0.03)
    result = rendering.render_parts([c], 3200, sample_rate=16000)
    dry, _ = rendering.render_cue(c, sample_rate=16000)
    assert np.allclose(result["stems"]["ui"][:len(dry)-480], dry[480:])
    assert np.all(result["stems"]["ui"][len(dry)-480:] == 0)
    mask = rendering.silence_mask([{"from": -2, "to": -1, "stems": ["ui"]}], "ui", 3200, 16000)
    assert np.all(mask == 1)


def test_clip_gain_uses_absolute_film_clock_after_peak_level_and_only_once():
    c = cue(t=2.125, align="end")
    dry, offset = rendering.render_cue(c, sample_rate=16000)
    automation = {"t": [2.02, 2.06, 2.1], "g": [0.25, 0.6, 0], "before": 0.25, "after": 0}
    automated = {**c, "clip_gain": automation}
    actual, actual_offset = rendering.render_cue(automated, sample_rate=16000)
    absolute_time = c["t"] + offset/16000 + np.arange(len(dry))/16000
    expected = dry * np.interp(absolute_time, automation["t"], automation["g"], left=.25, right=0)[:, None]
    assert actual_offset == offset and np.array_equal(actual, expected)
    # A render window starts after the cue's beginning: crop the same film-clock automation, never restart it.
    result = rendering.render_parts([automated], 2400, sample_rate=16000, t0=2.025)
    cropped = int(round((2.025 - (c["t"] + offset/16000))*16000))
    assert np.allclose(result["stems"]["ui"][:len(actual)-cropped], actual[cropped:], atol=1e-14)
    assert np.max(np.abs(actual)) < np.max(np.abs(dry))  # automation is not peak-normalized away


def test_invalid_clip_gain_refuses_instead_of_guessing_a_handoff():
    with pytest.raises(UsageError, match="increasing finite"):
        rendering.render_cue(cue(clip_gain={"t": [.2, .1], "g": [0, 1]}))
