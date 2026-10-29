"""Caption timing must be measurable before a caption can receive a passing verdict.

These neutral fixtures exercise the shared loader, actual caption producer and saved-result freshness without
recording audio, rendering picture or loading a native model.
"""

from __future__ import annotations

import json
import math

import pytest

from slate import gates
from slate.qa import stability, textmatch
from slate.qa.checks import text
from slate.qa.film import FilmContext


NONFINITE = [math.nan, math.inf, -math.inf, "NaN", "Infinity", "-Infinity"]
NONFINITE_IDS = ["nan", "positive-infinity", "negative-infinity", "nan-string", "infinity-string",
                 "negative-infinity-string"]


def _write(film, relative, data):
    path = film / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")
    return path


def _caption(film, *, start=0.0, end=2.0, line="Hold this line"):
    return _write(film, "timing/captions.json", {"cues": [{"text": line, "start": start, "end": end}]})


@pytest.fixture
def caption_film(slate_home):
    film = slate_home["films"] / "neutral-caption"
    line = "Hold this line"
    _write(film, "film.json", {"id": "neutral-caption", "mode": "draft", "brand": "neutral", "language": "en",
                              "fps_num": 30, "fps_den": 1, "width": 96, "height": 54})
    _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "vo": line}]}]})
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": line, "words": [
        {"w": "Hold", "start": 0.0, "end": 0.4}, {"w": "this", "start": 0.4, "end": 0.8},
        {"w": "line", "start": 0.8, "end": 1.6}]}}})
    _caption(film)
    return film


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("value", NONFINITE, ids=NONFINITE_IDS)
def test_shared_caption_loader_refuses_nonfinite_converted_times(caption_film, field, value):
    path = _caption(caption_film, **{field: value})
    with pytest.raises(ValueError, match="finite"):
        textmatch.load_cues(path)


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("value", NONFINITE, ids=NONFINITE_IDS)
def test_actual_caption_gate_cannot_pass_nonfinite_timing(caption_film, field, value):
    path = _caption(caption_film, **{field: value})
    original = path.read_bytes()
    result = text.captions(caption_film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert "finite" in result.summary
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["json", "srt", "vtt"])
def test_finite_caption_formats_keep_their_times_words_and_passing_verdict(caption_film, kind):
    path = caption_film / "timing/captions.json"
    if kind != "json":
        path.unlink()
        path = caption_film / f"voice/captions/neutral.{kind}"
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "WEBVTT\n\n" if kind == "vtt" else ""
        path.write_text(prefix + "1\n00:00:00,000 --> 00:00:02,000\nHold this line\n")
    cues = textmatch.load_cues(path)
    assert [(c["start"], c["end"], c["text"]) for c in cues] == [(0.0, 2.0, "Hold this line")]
    result = text.captions(caption_film)
    assert result.status == "PASS", result.to_dict()
    assert "word for word" in result.summary


def test_finite_numeric_strings_keep_the_existing_json_conversion(caption_film):
    path = _caption(caption_film, start="0.125", end="2.125")
    cues = textmatch.load_cues(path)
    assert (cues[0]["start"], cues[0]["end"]) == (0.125, 2.125)
    assert text.captions(caption_film).status == "PASS"


def test_invalid_signed_infinity_json_remains_unchecked(caption_film):
    path = caption_film / "timing/captions.json"
    path.write_text('{"cues":[{"text":"Hold this line","start":0,"end":+Infinity}]}\n')
    assert text.captions(caption_film).status == "UNCHECKED"


def test_finite_caption_words_still_have_to_match_the_script(caption_film):
    _caption(caption_film, line="Hold another line")
    result = text.captions(caption_film)
    assert result.status == "FAIL", result.to_dict()
    assert "word for word" in result.summary


@pytest.mark.parametrize("outcome", ["normal", "invalid-time", "wrong-words", "missing-voiced", "no-script",
                                     "silent"])
def test_every_caption_outcome_records_revision_and_original_dependencies(caption_film, outcome):
    if outcome == "invalid-time":
        _caption(caption_film, end=math.inf)
    elif outcome == "wrong-words":
        _caption(caption_film, line="Hold another line")
    elif outcome in {"missing-voiced", "no-script", "silent"}:
        (caption_film / "timing/captions.json").unlink()
        if outcome in {"no-script", "silent"}:
            (caption_film / "timing/vo_words.json").unlink()
            (caption_film / "script/storyboard.json").unlink()
        if outcome == "silent":
            _write(caption_film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "vo": ""}]}]})
    result = text.captions(caption_film)
    assert result.engine["rule_revision"] == gates.rule_revision("captions") == 1
    assert "timing/vo_words.json" in result.inputs
    assert "script/storyboard.json" in result.inputs
    assert "timing/captions.json" in result.inputs
    assert any(key.startswith("film:") for key in result.inputs)


@pytest.mark.parametrize("legacy_revision", [None, 0])
def test_legacy_caption_results_become_stale_without_rewriting_saved_evidence(caption_film, legacy_revision):
    result = text.captions(caption_film)
    result.engine.pop("rule_revision", None)
    if legacy_revision is not None:
        result.engine["rule_revision"] = legacy_revision
    gates.write_report(caption_film, [result])
    report = caption_film / "qa/gates.json"
    history = caption_film / "qa/history.jsonl"
    saved = report.read_bytes(), history.read_bytes()
    loaded = gates.load_report(caption_film)["captions"]
    assert loaded.status == "STALE", loaded.to_dict()
    assert (report.read_bytes(), history.read_bytes()) == saved


@pytest.mark.parametrize("relative", ["timing/captions.json", "timing/vo_words.json", "film.json"])
def test_current_caption_results_still_track_changed_dependencies(caption_film, relative):
    result = text.captions(caption_film)
    gates.write_report(caption_film, [result])
    assert gates.load_report(caption_film)["captions"].status == "PASS"
    path = caption_film / relative
    doc = json.loads(path.read_text())
    if relative == "timing/captions.json":
        doc["cues"][0]["end"] = 3.0
    elif relative == "timing/vo_words.json":
        doc["shots"]["S01"]["line"] = "Hold another line"
    else:
        doc["fps_num"] = 25
    _write(caption_film, relative, doc)
    assert gates.load_report(caption_film)["captions"].status == "STALE"


def test_absent_caption_dependency_still_becomes_stale_when_it_appears(caption_film):
    (caption_film / "timing/captions.json").unlink()
    result = text.captions(caption_film)
    assert result.status == "UNCHECKED"
    gates.write_report(caption_film, [result])
    assert gates.load_report(caption_film)["captions"].status == "UNCHECKED"
    _caption(caption_film)
    assert gates.load_report(caption_film)["captions"].status == "STALE"


def test_burned_caption_settle_boundaries_handle_invalid_shared_input(caption_film, monkeypatch):
    _caption(caption_film, start=math.nan)
    ctx = FilmContext(caption_film)
    monkeypatch.setattr(ctx, "format", lambda: {"captions": {"burn_in": True}})
    assert stability.caption_changes(ctx) == []


def test_burned_caption_settle_boundaries_keep_finite_shared_input(caption_film, monkeypatch):
    ctx = FilmContext(caption_film)
    monkeypatch.setattr(ctx, "format", lambda: {"captions": {"burn_in": True}})
    boundaries = stability.caption_changes(ctx)
    assert boundaries[0] == 0 and boundaries[-1] == 60
