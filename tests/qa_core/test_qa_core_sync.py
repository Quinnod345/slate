"""The sync gate: coverage, effects anchors, VO placement by cross-correlation, and word landings."""

import os

import numpy as np
import pytest

from slate import gates
from slate.qa import accept
from slate.qa.checks import sync

from qa_core_helpers import write_json

SR = 48000


def _sub(result, name):
    return next(c for c in result.details["checks"] if c["check"] == name)


def _take(seed: int, seconds: float = 0.6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    env = np.minimum(1.0, np.linspace(0, 8, n)) * np.minimum(1.0, np.linspace(8, 0, n))
    return rng.standard_normal(n) * 0.2 * env


def _voice_film(film_builder, *, late_ms: float = 0.0, other_noise: float = 0.0, seed: int = 3):
    """Two shots with VO: takes written as files, and a dialogue stem with each take where its cue says."""
    fb = film_builder(shots=[(1, 45), (1, 45)])
    fb.all_events()
    cues, total = [], int(fb.secs(fb.total) * SR) + SR
    stem = np.zeros(total)
    for i, sid in enumerate(("S01", "S02")):
        y = _take(seed + i)
        fb.wav(f"voice/takes/{sid}.wav", y[:, None], SR, 24)
        at = 0.1
        cues.append({"shot": sid, "at": at, "file": f"voice/takes/{sid}.wav", "gain": 0, "fade_in": 0, "fade_out": 0})
        start = int(round((fb.secs(fb.shot(sid)["frame_in"]) + at + late_ms / 1000.0) * SR))
        stem[start:start + len(y)] += y
    if other_noise:
        stem += np.random.default_rng(99).standard_normal(total) * other_noise
    fb.wav(fb.stem("dialogue"), np.clip(stem, -1, 1)[:, None], SR, 24)
    fb.wav(fb.MIX, np.clip(stem, -1, 1)[:, None], SR, 24)  # the stems sit beside their mix
    write_json(fb.path / "timing" / "vo_cues.json", {"cues": cues})
    return fb


def test_voice_placement_exact(film_builder):
    fb = _voice_film(film_builder)
    r = sync.sync(fb.path)
    v = _sub(r, "voice")
    assert v["status"] == "PASS", v["summary"]
    assert all(abs(t["offset_ms"]) < 0.5 and t["corr"] > 0.99 for t in v["takes"])
    assert r.engine["voice"].startswith("numpy")


def test_voice_placement_late_take_fails(film_builder):
    fb = _voice_film(film_builder, late_ms=10.0)
    v = _sub(sync.sync(fb.path), "voice")
    assert v["status"] == "FAIL"
    assert all(t["offset_ms"] == pytest.approx(10.0, abs=0.05) for t in v["takes"])


def test_voice_placement_weak_correlation_fails(film_builder):
    fb = _voice_film(film_builder, other_noise=0.2)
    v = _sub(sync.sync(fb.path), "voice")
    assert v["status"] == "FAIL" and "not matching the approved take" in v["summary"], v
    assert all(abs(t["offset_ms"]) < 0.5 for t in v["takes"])


def test_voice_unchecked_without_a_dialogue_stem(film_builder):
    fb = _voice_film(film_builder)
    (fb.path / fb.stem("dialogue")).unlink()
    v = _sub(sync.sync(fb.path), "voice")
    assert v["status"] == "UNCHECKED"


def _words_film(film_builder, beat_frame: int, **beat_extra):
    fb = film_builder(shots=[(1, 60)])
    write_json(fb.path / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "at": 0.2, "file": "voice/takes/S01.wav"}]})
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"take": "voice/takes/S01.wav", "line": "Hello bright world",
                                                                        "words": [{"w": "Hello", "start": 0.1, "end": 0.4},
                                                                                  {"w": "bright", "start": 0.5, "end": 0.8},
                                                                                  {"w": "world,", "start": 0.9, "end": 1.3}]}}})
    # "world" starts at 0.2 + 0.9 = 1.1 s = frame 33 at 30 fps
    fb.events(1, [fb.beat("S01", "lands_on_world", beat_frame, word="World", **beat_extra)])
    return fb


def test_word_landing_on_time(film_builder):
    fb = _words_film(film_builder, 33)
    w = _sub(sync.sync(fb.path), "word_landings")
    assert w["status"] == "PASS"


def test_word_landing_three_frames_late_fails(film_builder):
    fb = _words_film(film_builder, 36)
    w = _sub(sync.sync(fb.path), "word_landings")
    assert w["status"] == "FAIL" and "+100 ms" in w["summary"]


def test_word_landing_missing_word_fails(film_builder):
    fb = _words_film(film_builder, 33)
    fb.events(1, [fb.beat("S01", "x", 33, word="galaxy")])
    w = _sub(sync.sync(fb.path), "word_landings")
    assert w["status"] == "FAIL" and "galaxy" in w["summary"]


def test_word_landing_exception_is_review_until_a_person_accepts_it(film_builder, monkeypatch):
    fb = _words_film(film_builder, 38, sync_exception="the character anticipates the word, by design")
    r = sync.sync(fb.path)
    # the whole gate is UNCHECKED here (no dialogue stem, no effects record); the landing itself waits for a person
    assert _sub(r, "word_landings")["status"] == "REVIEW"
    (item,) = r.details["review_items"]
    assert item["key"] == "word:S01/lands_on_world"
    gates.write_report(fb.path, [r])
    monkeypatch.setenv("SLATE_CONTEXT", "codex")  # an agent relaying a person's decision on a supervised film
    accept.accept(fb.path, "sync:word:S01/lands_on_world", by="Quinn", reason="designed anticipation")
    r2 = sync.sync(fb.path)
    assert _sub(r2, "word_landings")["status"] == "PASS"
    assert "accepted" in _sub(r2, "word_landings")["summary"]
    # change the events file and the acceptance no longer applies: the new beat is judged again
    fb.events(1, [fb.beat("S01", "lands_on_world", 39, word="World", sync_exception="moved again")])
    assert _sub(sync.sync(fb.path), "word_landings")["status"] == "REVIEW"


def _record(fb, **sync_fields):
    base = {"frame_ms": 1000 / 30, "anchors": 20, "max_abs_delta_ms": 3.0, "over_one_frame": [], "pass": True}
    write_json(fb.path / "sound" / "verify.json", {"sync": {**base, **sync_fields}})


def test_effects_record_pass_and_fail(film_builder):
    fb = film_builder()
    fb.all_events()
    _record(fb)
    assert _sub(sync.sync(fb.path), "effects")["status"] == "PASS"
    _record(fb, max_abs_delta_ms=40.0, over_one_frame=[{"family": "snap", "beat_s": 1.0, "delta_ms": 40.0}])
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "FAIL" and "40.0" in e["summary"]
    _record(fb, anchors=0)
    assert _sub(sync.sync(fb.path), "effects")["status"] == "FAIL"


def test_effects_record_on_another_clock_fails(film_builder):
    fb = film_builder(fps=(25, 1))
    fb.all_events()
    _record(fb, frame_ms=33.37)
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "FAIL" and "different clock" in e["summary"]


def test_effects_record_older_than_the_mix_is_unchecked(film_builder):
    fb = film_builder()
    fb.all_events()
    _record(fb)
    mix = fb.wav(fb.MIX, np.zeros((SR, 2)))
    rec = fb.path / "sound" / "verify.json"
    os.utime(rec, (1_000_000, 1_000_000))
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "UNCHECKED" and "older than the mix" in e["summary"]
    assert mix.exists()


def test_effects_record_with_inputs_goes_stale_on_change(film_builder):
    fb = film_builder()
    fb.all_events()
    mix = fb.wav(fb.MIX, np.zeros((SR, 2)))
    from slate import jsonio

    write_json(fb.path / "sound" / "verify.json", {"inputs": {fb.MIX: jsonio.sha256(mix)},
                                                   "sync": {"frame_ms": 1000 / 30, "anchors": 5, "max_abs_delta_ms": 1.0}})
    assert _sub(sync.sync(fb.path), "effects")["status"] == "PASS"
    fb.wav(fb.MIX, np.ones((SR, 2)) * 0.1)
    assert _sub(sync.sync(fb.path), "effects")["status"] == "UNCHECKED"


def test_effects_unchecked_without_any_verification(film_builder):
    fb = film_builder()
    fb.all_events()
    r = sync.sync(fb.path)
    assert _sub(r, "effects")["status"] == "UNCHECKED"
    assert r.status == "UNCHECKED"


def test_effects_from_a_placement_log(film_builder):
    fb = film_builder()
    fb.all_events()  # S01 beat at frame 2, S02 at 22, S03 at 42
    write_json(fb.path / "sound" / "markers.json", {"markers": [
        {"t": fb.secs(2), "shot": "S01", "event": "s01_in"},
        {"t": fb.secs(22) + 0.01, "shot": "S02", "event": "s02_in"}]})
    assert _sub(sync.sync(fb.path), "effects")["status"] == "PASS"
    write_json(fb.path / "sound" / "markers.json", {"markers": [{"t": fb.secs(42) + 0.1, "shot": "S03", "event": "s03_in"},
                                                                {"t": 1.0, "shot": "S01", "event": "nope"}]})
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "FAIL"


def test_sync_unchecked_without_a_clock(film_builder):
    fb = film_builder()
    (fb.path / "timing" / "shots.json").unlink()
    assert sync.sync(fb.path).status == "UNCHECKED"


def test_word_landing_on_a_spoken_number_uses_the_timing_module(film_builder):
    pytest.importorskip("slate.timing.words")
    fb = film_builder(shots=[(1, 90)])
    write_json(fb.path / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "at": 0.2, "file": "voice/takes/S01.wav"}]})
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {
        "take": "voice/takes/S01.wav", "line": "It reads 664 today.",
        "words": [{"w": "It", "start": 0.1, "end": 0.2}, {"w": "reads", "start": 0.25, "end": 0.5},
                  {"w": "six", "start": 0.6, "end": 0.8}, {"w": "sixty-four", "start": 0.8, "end": 1.3},
                  {"w": "today.", "start": 1.4, "end": 1.8}]}}})
    # "six" starts at 0.2 + 0.6 = 0.8 s = frame 24 at 30 fps
    fb.events(1, [fb.beat("S01", "count_lands", 24, word="664")])
    w = _sub(sync.sync(fb.path), "word_landings")
    assert w["status"] == "PASS", w
    assert not (fb.path / "timing" / "word_misses.json").exists()  # a gate never writes into timing/


def _cue_sync_result(fb, status="PASS", **details):
    from slate import jsonio

    cues = write_json(fb.path / "sound" / "sound_cues.json", {"cues": []})
    return {"gate": "sound.cue_sync", "status": status, "summary": f"{status.lower()} by the sound module",
            "evidence": ["sound/sound_cues.json"], "engine": {"name": "slate sound", "version": "0.1.0"},
            "inputs": {"sound/sound_cues.json": jsonio.sha256(cues)}, "checked_at": "2026-09-30T12:00:00+00:00",
            "details": {"frame_ms": 33.333, "anchors": 12, "max_abs_delta_ms": 4.0, **details}}


def test_effects_from_the_sound_modules_verify_record(film_builder):
    fb = film_builder()
    fb.all_events()
    write_json(fb.path / "sound" / "mix" / "verify.json", {"kind": "verify", "gates": {
        "sound.cue_sync": _cue_sync_result(fb)}})
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "PASS" and e["from"] == "sound/mix/verify.json" and e["anchors"] == 12
    # the cue sheet changed after the sound module measured it: its verdict is out of date
    write_json(fb.path / "sound" / "sound_cues.json", {"cues": [{"id": "new"}]})
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "UNCHECKED" and "out of date" in e["summary"]


def test_effects_from_the_sound_verdict_in_the_gate_report(film_builder):
    fb = film_builder()
    fb.all_events()
    write_json(fb.path / "qa" / "gates.json", {"gates": {"sound.cue_sync": _cue_sync_result(fb, "FAIL")}})
    e = _sub(sync.sync(fb.path), "effects")
    assert e["status"] == "FAIL" and e["from"] == "qa/gates.json"
