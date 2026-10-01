"""Gesture timing in the sync gate (L-timing-D004): a character beat keyed to a word lands early, never late."""

from __future__ import annotations

from slate.qa.checks import sync

from qa_core_helpers import write_json


def _sub(result, name):
    return next((c for c in result.details["checks"] if c["check"] == name), None)


def _film(film_builder, *beats):
    fb = film_builder(shots=[(1, 60)])
    write_json(fb.path / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "at": 0.2, "file": "voice/takes/S01.wav"}]})
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {
        "take": "voice/takes/S01.wav", "line": "Hello bright world",
        "words": [{"w": "Hello", "start": 0.1, "end": 0.4}, {"w": "bright", "start": 0.5, "end": 0.8},
                  {"w": "world,", "start": 0.9, "end": 1.3}]}}})
    # "world" runs from 0.2 + 0.9 = 1.1 s to 1.5 s: frames 33 to 45 at 30 fps
    fb.events(1, [fb.beat("S01", f"g{i}", frame, kind="character", word="world", gesture="turn", **extra)
                  for i, (frame, extra) in enumerate(beats)])
    return fb


def test_a_gesture_on_its_word_or_early_passes(film_builder):
    fb = _film(film_builder, (35, {}), (24, {}))  # on the syllable (S + 2), and well before it
    r = sync.sync(fb.path)
    g = _sub(r, "gestures")
    assert g["status"] == "PASS", g
    assert _sub(r, "word_landings") is None  # gestures aren't held to the two-frame word rule


def test_a_gesture_after_its_word_fails_with_its_name_and_frames(film_builder):
    fb = _film(film_builder, (48, {}))  # 1.6 s: after "world" ends at 1.5 s
    g = _sub(sync.sync(fb.path), "gestures")
    assert g["status"] == "FAIL"
    assert "the turn lands at film frame 48" in g["summary"] and "frame 45" in g["summary"]


def test_the_stressed_syllable_the_kit_records_wins_over_the_word(film_builder):
    fb = _film(film_builder, (42, {"stress_start": 1.1, "stress_end": 1.3}))  # inside the word, after its syllable
    g = _sub(sync.sync(fb.path), "gestures")
    assert g["status"] == "FAIL" and "stressed syllable ends (frame 39)" in g["summary"]


def test_a_gesture_on_a_word_the_take_never_says_fails(film_builder):
    fb = film_builder(shots=[(1, 60)])
    write_json(fb.path / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "at": 0.2, "file": "voice/S01.wav"}]})
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"take": "voice/S01.wav", "line": "Hello",
                                                                        "words": [{"w": "Hello", "start": 0.1,
                                                                                   "end": 0.4}]}}})
    fb.events(1, [fb.beat("S01", "g0", 10, kind="character", word="galaxy")])
    g = _sub(sync.sync(fb.path), "gestures")
    assert g["status"] == "FAIL" and "galaxy" in g["summary"]
