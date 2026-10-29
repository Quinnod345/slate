"""Declared read-along text stays visible through its measured spoken phrase.

The OCR boundary is scripted so these checks exercise the real film clock, word resolver, gate result and cache
without starting a renderer, loading Apple Vision or creating narration.
"""

from __future__ import annotations

import json
import math

import pytest

from slate import gates
from slate.clock import Clock
from slate.errors import MissingPrerequisite
from slate.qa import ocr
from slate.qa.checks import text


def _write(film, relative, data):
    path = film / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")
    return path


@pytest.fixture
def readtime_film(slate_home, monkeypatch):
    film = slate_home["films"] / "read-along"
    line = "Hold this line"
    _write(film, "film.json", {"id": "read-along", "mode": "draft", "brand": "demo", "language": "en",
                               "fps_num": 30, "fps_den": 1, "width": 96, "height": 54})
    _write(film, "timing/shots.json", {"fps_num": 30, "fps_den": 1, "shots": [
        {"id": "S01", "act": 1, "frame_in": 0, "frame_out": 120, "on_screen": line, "vo": line}]})
    _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "on_screen": line}]}]})
    _write(film, "script/script.json", {"acts": [{"shots": [{"id": "S01", "vo": line, "on_screen": line,
        "reading": {"voice_matched": True, "reason": "The narrator reads the complete statement."}}]}]})
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": 0.0, "file": "voice/line.wav"}]})
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": line, "words": [
        {"w": "Hold", "start": 0.0, "end": 0.4}, {"w": "this", "start": 0.4, "end": 0.8},
        {"w": "line", "start": 0.8, "end": 3.0}]}}})
    samples = [{"t": k / 4, "frame": round(k * 30 / 4),
                "lines": [{"text": line}] if k < 8 else []} for k in range(16)]
    sweep = ocr.Sweep(engine={"name": "scripted OCR", "version": "fixture"}, every=0.25, source="renders",
                      files=[], samples=samples, missing_acts=[], cache=None, expected=len(samples))
    monkeypatch.setattr(ocr, "available", lambda: (True, sweep.engine))
    monkeypatch.setattr(ocr, "sweep", lambda _ctx: sweep)
    return film, sweep


def test_voice_matched_text_cannot_leave_before_last_matching_word(readtime_film):
    film, _sweep = readtime_film
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert "before" in " ".join(result.evidence).lower()


def _last_word_end(film, end):
    path = film / "timing/vo_words.json"
    doc = json.loads(path.read_text())
    doc["shots"]["S01"]["words"][-1]["end"] = end
    return _write(film, "timing/vo_words.json", doc)


def _show(sweep, text_value, start=0.0, end=2.0):
    for sample in sweep.samples:
        sample["lines"] = [{"text": text_value}] if start <= sample["t"] < end else []


def _declaration(film, reading):
    path = film / "script/script.json"
    doc = json.loads(path.read_text())
    shot = doc["acts"][0]["shots"][0]
    if reading is None:
        shot.pop("reading", None)
    else:
        shot["reading"] = reading
    _write(film, "script/script.json", doc)


def test_voice_matched_text_passes_when_phrase_ends_before_picture_leaves(readtime_film):
    film, _sweep = readtime_film
    _last_word_end(film, 1.6)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == 1.6
    assert "needs 1.50 s, voice-matched" in result.details["rows"][0]


def test_voice_matched_text_keeps_the_existing_complete_text_floor(readtime_film):
    film, sweep = readtime_film
    _last_word_end(film, 1.1)
    _show(sweep, "Hold this line", end=1.0)
    result = text.readtime(film)
    assert result.status == "FAIL" and result.details["short"], result.to_dict()


@pytest.mark.parametrize("relative", ["timing/vo_words.json", "timing/vo_cues.json"])
def test_voice_matched_text_without_measured_timing_is_unchecked(readtime_film, relative):
    film, _sweep = readtime_film
    (film / relative).unlink()
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert result.inputs[relative] is None


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, "NaN", True, None])
def test_voice_matched_text_never_passes_nonfinite_or_non_numeric_word_times(readtime_film, invalid):
    film, _sweep = readtime_film
    _last_word_end(film, invalid)
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert "word times" in " ".join(result.evidence)


def test_voice_matched_decreasing_measured_word_ends_are_unchecked(readtime_film):
    film, _sweep = readtime_film
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": "Hold this line", "words": [
        {"w": "Hold", "start": 0.0, "end": 3.0}, {"w": "this", "start": 0.4, "end": 0.8},
        {"w": "line", "start": 0.8, "end": 1.6}]}}})
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert "word times" in " ".join(result.evidence)


def test_voice_matched_overlapping_words_with_ordered_ends_remain_valid(readtime_film):
    film, _sweep = readtime_film
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": "Hold this line", "words": [
        {"w": "Hold", "start": 0.0, "end": 0.6}, {"w": "this", "start": 0.4, "end": 1.0},
        {"w": "line", "start": 0.8, "end": 1.6}]}}})
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == 1.6


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, "Infinity", True, None])
def test_voice_matched_text_never_passes_invalid_voice_placement(readtime_film, invalid):
    film, _sweep = readtime_film
    _last_word_end(film, 1.6)
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": invalid, "file": "voice/line.wav"}]})
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()


def test_voice_matched_text_with_unmapped_words_is_unchecked(readtime_film):
    film, _sweep = readtime_film
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": "Hold another label", "words": [
        {"w": "Hold", "start": 0.0, "end": 0.4}, {"w": "another", "start": 0.4, "end": 0.8},
        {"w": "label", "start": 0.8, "end": 1.6}]}}})
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert "not fully matched" in " ".join(result.evidence)
    assert not (film / "timing/word_misses.json").exists()


def test_voice_matched_statement_does_not_wait_for_unrelated_later_narration(readtime_film):
    film, _sweep = readtime_film
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": "Hold this line then continue", "words": [
        {"w": "Hold", "start": 0.0, "end": 0.4}, {"w": "this", "start": 0.4, "end": 0.8},
        {"w": "line", "start": 0.8, "end": 1.6}, {"w": "then", "start": 1.6, "end": 2.0},
        {"w": "continue", "start": 2.0, "end": 3.0}]}}})
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == 1.6


def test_voice_matched_end_includes_rational_shot_clock_and_take_lead(readtime_film):
    film, sweep = readtime_film
    clock = Clock(30000, 1001)
    start = clock.secs_of(60)
    doc = json.loads((film / "film.json").read_text())
    _write(film, "film.json", {**doc, **clock.to_dict()})
    doc = json.loads((film / "timing/shots.json").read_text())
    doc.update(clock.to_dict())
    doc["shots"][0].update(frame_in=60, frame_out=180)
    _write(film, "timing/shots.json", doc)
    _last_word_end(film, 1.4)
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": 0.5, "file": "voice/line.wav"}]})
    for sample in sweep.samples:
        sample["t"] += start
        sample["frame"] = clock.frame_of(sample["t"])
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == pytest.approx(start + 0.5 + 1.4)
    _last_word_end(film, 1.6)
    assert text.readtime(film).status == "FAIL"  # the lead shifts the spoken end past the readable interval


def test_voice_matched_statement_uses_shared_spoken_number_and_symbol_forms(readtime_film):
    film, sweep = readtime_film
    line = "12 & more"
    for relative in ("script/storyboard.json", "script/script.json"):
        doc = json.loads((film / relative).read_text())
        doc["acts"][0]["shots"][0]["on_screen"] = line
        _write(film, relative, doc)
    _show(sweep, line)
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": "Twelve and more", "words": [
        {"w": "Twelve", "start": 0.0, "end": 0.4}, {"w": "and", "start": 0.4, "end": 0.8},
        {"w": "more", "start": 0.8, "end": 1.6}]}}})
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == 1.6


def test_voice_matched_statement_resolves_repeated_tokens_positionally(readtime_film):
    film, sweep = readtime_film
    line = "Stay and stay"
    for relative in ("script/storyboard.json", "script/script.json"):
        doc = json.loads((film / relative).read_text())
        doc["acts"][0]["shots"][0]["on_screen"] = line
        _write(film, relative, doc)
    _show(sweep, line)
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": line, "words": [
        {"w": "Stay", "start": 0.0, "end": 0.4}, {"w": "and", "start": 0.4, "end": 0.8},
        {"w": "stay", "start": 0.8, "end": 3.0}]}}})
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == 3.0


def test_voice_matched_late_ocr_flash_cannot_borrow_earlier_readable_duration(readtime_film):
    film, sweep = readtime_film
    sweep.samples[-1]["lines"] = [{"text": "Hold this line"}]
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()


def test_voice_matched_reappearance_does_not_erase_early_disappearance(readtime_film):
    film, sweep = readtime_film
    doc = json.loads((film / "timing/shots.json").read_text())
    doc["shots"][0]["frame_out"] = 180
    _write(film, "timing/shots.json", doc)
    sweep.samples = [{"t": k / 4, "frame": round(k * 30 / 4), "lines": [{"text": "Hold this line"}]
                      if k < 8 or 12 <= k < 20 else []} for k in range(24)]
    sweep.expected = len(sweep.samples)
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()


def test_voice_matched_aggregate_time_overflow_is_unchecked(readtime_film):
    film, _sweep = readtime_film
    _last_word_end(film, 1e308)
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": 1e308, "file": "voice/line.wav"}]})
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()


def test_voice_matched_complete_text_first_appearing_after_phrase_end_fails(readtime_film):
    film, sweep = readtime_film
    _last_word_end(film, 1.6)
    _show(sweep, "Hold this line", start=2.0, end=4.0)
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert "after" in " ".join(result.evidence).lower()


def test_voice_matched_progressive_build_keeps_one_sample_of_onset_uncertainty(readtime_film):
    film, sweep = readtime_film
    _last_word_end(film, 1.6)
    _show(sweep, "Hold this line", start=1.75, end=4.0)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()


@pytest.mark.parametrize("flag", [None, "labels", "not_for_reading"])
def test_silent_general_and_not_for_reading_statements_keep_their_behavior(readtime_film, flag):
    film, _sweep = readtime_film
    _declaration(film, {flag: True, "reason": "This treatment is intentional."} if flag else None)
    (film / "timing/vo_words.json").unlink()
    (film / "timing/vo_cues.json").unlink()
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert "timing/vo_words.json" not in result.inputs and "timing/vo_cues.json" not in result.inputs
    assert "voice_matched_checks" not in result.details


def test_listed_voice_matched_statement_leaves_other_statements_unaffected(readtime_film):
    film, sweep = readtime_film
    _declaration(film, {"voice_matched": ["Hold this line"], "reason": "Only this statement is read aloud."})
    _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01",
        "on_screen": "Hold this line · Another quiet title"}]}]})
    _show(sweep, "Hold this line Another quiet title")
    _last_word_end(film, 1.6)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert len(result.details["voice_matched_checks"]) == 1
    assert len(result.details["rows"]) == 2


@pytest.mark.parametrize("relative", ["timing/vo_words.json", "timing/vo_cues.json", "film.json"])
def test_voice_matched_result_becomes_stale_when_timing_or_clock_changes(readtime_film, relative):
    film, _sweep = readtime_film
    _last_word_end(film, 1.6)
    result = text.readtime(film)
    gates.write_report(film, [result])
    assert gates.load_report(film)["readtime"].status == "PASS"
    path = film / relative
    doc = json.loads(path.read_text())
    if relative.endswith("vo_words.json"):
        doc["shots"]["S01"]["words"][-1]["end"] = 3.0
    elif relative.endswith("vo_cues.json"):
        doc["cues"][0]["at"] = 0.5
    else:
        doc["fps_num"] = 25
    _write(film, relative, doc)
    assert gates.load_report(film)["readtime"].status == "STALE"


def test_missing_narration_dependency_becomes_stale_when_it_appears(readtime_film):
    film, _sweep = readtime_film
    path = film / "timing/vo_words.json"
    original = path.read_bytes()
    path.unlink()
    result = text.readtime(film)
    assert result.status == "UNCHECKED"
    gates.write_report(film, [result])
    assert gates.load_report(film)["readtime"].status == "UNCHECKED"
    path.write_bytes(original)
    assert gates.load_report(film)["readtime"].status == "STALE"


@pytest.mark.parametrize("outcome", ["normal", "no-statements", "no-storyboard", "exempt", "no-ocr", "ocr-error"])
def test_every_readtime_outcome_records_the_current_rule_revision(readtime_film, monkeypatch, outcome):
    film, _sweep = readtime_film
    if outcome == "no-statements":
        _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "on_screen": ""}]}]})
    elif outcome == "no-storyboard":
        (film / "script/storyboard.json").unlink()
        doc = json.loads((film / "timing/shots.json").read_text())
        doc["shots"][0].pop("on_screen")
        _write(film, "timing/shots.json", doc)
    elif outcome == "exempt":
        _declaration(film, {"not_for_reading": True, "reason": "Intentional texture."})
    elif outcome == "no-ocr":
        monkeypatch.setattr(ocr, "available", lambda: (False, {"name": ocr.ENGINE_NAME, "hint": "Install OCR."}))
    elif outcome == "ocr-error":
        def unavailable(_ctx):
            raise MissingPrerequisite("OCR is unavailable.", hint="Install OCR.")
        monkeypatch.setattr(ocr, "sweep", unavailable)
    result = text.readtime(film)
    assert result.engine["rule_revision"] == gates.rule_revision("readtime") == 2
