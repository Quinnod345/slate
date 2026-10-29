"""Declared statistic counts hold their observed final display after the exact visual landing.

Only the OCR boundary is scripted. These neutral films exercise real declarations, canonical event exports,
rational clocks, storyboard/timing propagation, gate composition and result freshness without rendering.
"""

from __future__ import annotations

import copy
import json
import math

import pytest

from slate import gates
from slate.clock import Clock
from slate.qa import ocr
from slate.qa.checks import text
from slate.qa.film import session
from slate.story import builder, model
from slate.timing.shots import make_shots


DEFAULT = object()
LINE = "72% complete"
BINDING = {"count_events": [{"text": LINE, "events": ["progress-count"]}]}


def _write(film, relative, value):
    path = film / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")
    return path


def _event(identity="progress-count", *, shot="S01", landing=1.0):
    return {"shot": shot, "id": identity, "kind": "count", "start": landing - 0.5, "dur": 0.5,
            "t": landing, "from": 0, "to": 72, "size": "m", "note": "The displayed demo statistic reaches its value."}


@pytest.fixture
def count_film(slate_home, monkeypatch):
    index = 0

    def make(line=LINE, *, reading=DEFAULT, events=DEFAULT, start=0.0, end=4.0, rate=(30, 1), frame_in=0):
        nonlocal index
        index += 1
        film = slate_home["films"] / f"count-{index}"
        clock = Clock(*rate)
        base = clock.secs_of(frame_in)
        _write(film, "film.json", {"id": f"count-{index}", "mode": "draft", "brand": "demo", "language": "en",
                                  "fps_num": rate[0], "fps_den": rate[1], "width": 96, "height": 54})
        shot = {"id": "S01", "on_screen": line, "vo": "", "dur": 8.0}
        if reading is DEFAULT:
            reading = copy.deepcopy(BINDING)
            reading["count_events"][0]["text"] = line
        if reading is not None:
            shot["reading"] = copy.deepcopy(reading)
        _write(film, "script/script.json", {"acts": [{"name": "One", "shots": [shot]}]})
        _write(film, "script/storyboard.json", {"acts": [{"name": "One", "shots": [
            {"id": "S01", "on_screen": line, "dur": 8.0}]}]})
        _write(film, "timing/shots.json", {"fps_num": rate[0], "fps_den": rate[1], "shots": [
            {"id": "S01", "act": 1, "frame_in": frame_in, "frame_out": frame_in + clock.frame_of(8.0),
             "on_screen": line, "vo": ""}]})
        if events is DEFAULT:
            events = [_event(landing=base + 1.0)]
        if events is not None:
            _write(film, "timing/visual_events/act1.json", events)
        samples = [{"t": base + k / 4, "frame": clock.frame_of(base + k / 4),
                    "lines": [{"text": line}] if start <= k / 4 < end else []} for k in range(32)]
        sweep = ocr.Sweep(engine={"name": "scripted OCR", "version": "fixture"}, every=0.25, source="renders",
                          files=[], samples=samples, missing_acts=[], cache=None, expected=len(samples))
        monkeypatch.setattr(ocr, "available", lambda: (True, sweep.engine))
        monkeypatch.setattr(ocr, "sweep", lambda _ctx: sweep)
        return film, sweep

    return make


def _change(film, relative, edit):
    path = film / relative
    value = json.loads(path.read_text())
    edit(value)
    return _write(film, relative, value)


def _counts(result):
    return result.details["count_checks"]


def test_count_metadata_alone_needs_no_exemption_reason(count_film):
    film, _sweep = count_film()
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert _counts(result)[0]["landing_seconds"] == 1.0
    assert _counts(result)[0]["events"] == ["progress-count"]


def test_count_hold_starts_at_landing_not_first_appearance_or_shot_start(count_film):
    film, _sweep = count_film(events=[_event(landing=2.0)], end=3.0)
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert _counts(result)[0]["required_end_seconds"] == 4.0
    assert any(c["check"] == "count_hold" and c["status"] == "FAIL" for c in result.details["checks"])


@pytest.mark.parametrize("line", ["72%", "$1,200", "-1.2%"])
def test_explicit_count_binding_times_bare_numeric_displays(count_film, line):
    film, _sweep = count_film(line)
    result = text.readtime(film)
    assert result.status == "PASS" and result.details["statements"] == 1, result.to_dict()
    assert _counts(result)[0]["text"] == line


@pytest.mark.parametrize("line", [LINE, "72%", "$1,200"])
def test_known_readable_count_without_binding_is_unchecked_even_if_default_extraction_skips_it(count_film, line):
    film, _sweep = count_film(line, reading=None)
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert "count_events" in " ".join(result.evidence)
    assert "progress-count" in " ".join(result.evidence)


@pytest.mark.parametrize("events", [None, {"silent": True, "reason": "The static demo has no beats."}])
def test_static_numbers_without_authored_or_exported_counts_keep_existing_outcome(count_film, events):
    film, _sweep = count_film(reading=None, events=events, end=2.0)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert _counts(result) == []
    assert "timing/visual_events/act1.json" in result.inputs
    assert result.details["count_discovery"]["limits"]


@pytest.mark.parametrize("line", ["Ready now", "00:12 elapsed"])
def test_nonnumeric_motion_and_timers_gain_no_automatic_count_obligation(count_film, line):
    film, _sweep = count_film(line, reading=None, end=2.0)
    result = text.readtime(film)
    assert result.status == "PASS" and _counts(result) == [], result.to_dict()


def test_authored_count_with_missing_visual_export_is_unchecked(count_film):
    film, _sweep = count_film(reading=None, events=None)
    _change(film, "script/script.json", lambda d: d["acts"][0]["shots"][0].update(
        events=[{"id": "progress-count", "kind": "count", "t": 1.0}]))
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert result.inputs["timing/visual_events/act1.json"] is None


@pytest.mark.parametrize("events", [None, [], {}, {"silent": True, "reason": "No picture beat."}, "not a list"])
def test_claimed_count_needs_a_usable_canonical_export(count_film, events):
    film, _sweep = count_film(events=events)
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert "timing/visual_events/act1.json" in result.inputs


@pytest.mark.parametrize("field", ["t", "start", "dur"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "1.0", None])
def test_claimed_count_rejects_nonfinite_and_non_numeric_times(count_film, field, value):
    event = _event()
    event[field] = value
    film, _sweep = count_film(events=[event])
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()


@pytest.mark.parametrize("change", [{"start": -1.0}, {"dur": 0}, {"dur": -0.5}, {"t": 7.0},
                                    {"start": 0.75}, {"kind": "draw"}, {"shot": "S02"}, {"id": "other"}])
def test_claimed_count_rejects_invalid_span_identity_and_kind(count_film, change):
    event = {**_event(), **change}
    film, _sweep = count_film(events=[event])
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()


def test_duplicate_canonical_identity_is_unchecked(count_film):
    film, _sweep = count_film(events=[_event(), _event(landing=2.0)])
    assert text.readtime(film).status == "UNCHECKED"


@pytest.mark.parametrize("value", [None, False, {}, [], "progress-count", [{"text": LINE}],
                                    [{"text": LINE, "events": []}], [{"text": LINE, "events": "progress-count"}],
                                    [{"text": LINE, "events": ["progress-count", "progress-count"]}],
                                    [{"text": LINE, "events": [True]}],
                                    [{"text": "Another 72% display", "events": ["progress-count"]}],
                                    [{"text": LINE, "events": ["progress-count"], "skip": True}]])
def test_malformed_or_stray_count_declarations_fail(count_film, value):
    film, _sweep = count_film(reading={"count_events": value})
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert any(c["check"] == "declarations" for c in result.details["checks"])


def test_duplicate_statement_binding_is_not_collapsed(count_film):
    film, _sweep = count_film(reading={"count_events": [BINDING["count_events"][0], BINDING["count_events"][0]]})
    assert text.readtime(film).status == "FAIL"


def test_repeated_literal_display_is_ambiguous(count_film):
    film, _sweep = count_film(LINE + " · " + LINE, reading=BINDING)
    assert text.readtime(film).status == "FAIL"


def test_all_declared_counts_resolve_before_using_latest_landing(count_film):
    reading = {"count_events": [{"text": LINE, "events": ["early", "late"]}]}
    film, _sweep = count_film(reading=reading, events=[_event("early"), _event("late", landing=2.0)], end=4.0)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert _counts(result)[0]["landing_seconds"] == 2.0
    assert _counts(result)[0]["events"] == ["early", "late"]
    _change(film, "timing/visual_events/act1.json", lambda es: es.pop())
    with session(film):
        assert text.readtime(film).status == "UNCHECKED"


def test_incomplete_binding_cannot_ignore_another_visible_metric(count_film):
    line = LINE + " · 4 minutes saved"
    film, _sweep = count_film(line, reading=BINDING, events=[_event(), _event("saved-count", landing=2.0)])
    assert text.readtime(film).status == "UNCHECKED"


def test_same_id_in_another_shot_cannot_supply_the_claimed_landing(count_film):
    film, _sweep = count_film(events=[_event(shot="S02")])
    _change(film, "timing/shots.json", lambda d: d["shots"].append(
        {"id": "S02", "act": 1, "frame_in": 120, "frame_out": 240, "on_screen": "", "vo": ""}))
    assert text.readtime(film).status == "UNCHECKED"


def test_same_event_id_in_separate_acts_is_resolved_per_owning_shot(count_film):
    film, sweep = count_film()
    _change(film, "timing/shots.json", lambda d: d["shots"].append(
        {"id": "S02", "act": 2, "frame_in": 240, "frame_out": 480, "on_screen": LINE, "vo": ""}))
    _change(film, "script/storyboard.json", lambda d: d["acts"].append(
        {"name": "Two", "shots": [{"id": "S02", "on_screen": LINE, "reading": BINDING}]}))
    _write(film, "timing/visual_events/act2.json", [_event(shot="S02", landing=9.0)])
    sweep.samples += [{"t": 8.0 + k / 4, "frame": 240 + round(k * 30 / 4),
                       "lines": [{"text": LINE}] if k < 16 else []} for k in range(32)]
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert {row["shot"]: row["landing_seconds"] for row in _counts(result)} == {"S01": 1.0, "S02": 9.0}


def test_count_landing_uses_film_seconds_on_a_rational_shifted_clock(count_film):
    film, _sweep = count_film(rate=(30000, 1001), frame_in=157)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert _counts(result)[0]["landing_seconds"] == pytest.approx(Clock(30000, 1001).secs_of(157) + 1.0)


@pytest.mark.parametrize("delta,expected", [(0.49 / 30, "PASS"), (0.51 / 30, "UNCHECKED")])
def test_count_span_agreement_uses_half_a_film_frame(count_film, delta, expected):
    event = _event()
    event["t"] += delta
    film, _sweep = count_film(events=[event])
    assert text.readtime(film).status == expected


def test_count_cannot_use_an_inconsistent_clock(count_film):
    film, _sweep = count_film()
    _change(film, "timing/shots.json", lambda d: d.update(fps_num=60))
    assert text.readtime(film).status == "UNCHECKED"


def test_later_reappearance_cannot_erase_an_early_short_final_count_exposure(count_film):
    film, sweep = count_film(start=0.75, end=1.25)
    for sample in sweep.samples:
        if 2.0 <= sample["t"] < 5.0:
            sample["lines"] = [{"text": LINE}]
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert _counts(result)[0]["readable_end_seconds"] == 1.25


def test_late_settle_does_not_borrow_elapsed_time_since_count_landing(count_film):
    film, _sweep = count_film(start=2.0, end=3.75)
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert _counts(result)[0]["required_end_seconds"] == 4.0


@pytest.mark.parametrize("end,expected", [(2.75, "FAIL"), (3.0, "PASS")])
def test_count_readable_end_uses_one_sample_interval_only(count_film, end, expected):
    film, _sweep = count_film(end=end)
    result = text.readtime(film)
    assert result.status == expected, result.to_dict()
    assert _counts(result)[0]["sample_slack_seconds"] == 0.25
    assert _counts(result)[0]["readable_end_seconds"] == end


@pytest.mark.parametrize("line,wrong", [(LINE, "71% complete"), ("-1.2% complete", "1.2% complete"),
                                      ("1.2% complete", "12% complete"), ("$1,200 saved", "$1,300 saved")])
def test_wrong_or_intermediate_final_values_never_gain_count_credit(count_film, line, wrong):
    film, sweep = count_film(line)
    for sample in sweep.samples:
        sample["lines"] = [{"text": wrong}]
    result = text.readtime(film)
    assert result.status != "PASS", result.to_dict()
    assert _counts(result)[0]["readable_end_seconds"] is None


def test_a_correct_number_elsewhere_does_not_complete_the_wrong_count_statement(count_film):
    film, sweep = count_film()
    for sample in sweep.samples:
        sample["lines"] = [{"text": "71% complete"}, {"text": "72% used"}]
    result = text.readtime(film)
    assert result.status != "PASS", result.to_dict()
    assert _counts(result)[0]["readable_end_seconds"] is None


def test_ambiguous_bare_number_location_is_unchecked(count_film):
    film, sweep = count_film("72%")
    for sample in sweep.samples:
        sample["lines"] = [{"text": "71%"}, {"text": "72%"}]
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()


def test_unknown_exact_ocr_evidence_is_unchecked(count_film):
    film, sweep = count_film()
    for sample in sweep.samples:
        sample["lines"] = [{"text": LINE}, {"text": LINE}]
    assert text.readtime(film).status == "UNCHECKED"


def test_declared_count_uses_existing_legibility_filter(count_film):
    film, _sweep = count_film()
    _write(film, "timing/motion/act1.json", [{"id": "statistic", "shot": "S01", "text": LINE,
        "prop": "opacity", "ease": "linear", "frame_in": 0, "frame_out": 240,
        "from": 0, "to": 1, "samples": [[0, 0], [90, 0], [91, 1], [240, 1]]}])
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert _counts(result)[0]["readable_start_seconds"] >= 3.0


def test_not_for_reading_exempts_only_its_explicitly_bound_counted_display(count_film):
    other = "4 minutes saved"
    reading = {"count_events": [{"text": LINE, "events": ["progress-count"]},
                                {"text": other, "events": ["saved-count"]}],
               "not_for_reading": [LINE], "reason": "Only the small preview statistic is intentionally unreadable."}
    film, sweep = count_film(LINE + " · " + other, reading=reading,
                             events=[_event(), _event("saved-count")])
    for sample in sweep.samples:
        sample["lines"] = [{"text": other}] if sample["t"] < 4.0 else []
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert len(result.details["exempt"]) == 1 and LINE in result.details["exempt"][0]
    assert [row["text"] for row in _counts(result)] == [other]


def test_count_exemption_cannot_collapse_distinct_numeric_punctuation(count_film):
    reading = {"count_events": [{"text": "1.2%", "events": ["fraction"]},
                                {"text": "12%", "events": ["whole"]}],
               "not_for_reading": ["12%"], "reason": "Only the small whole-number preview is not for reading."}
    film, sweep = count_film("1.2% · 12%", reading=reading, events=[_event("fraction"), _event("whole")])
    for sample in sweep.samples:
        sample["lines"] = [{"text": "1.2%"}] if sample["t"] < 1.25 else []
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert [row["text"] for row in _counts(result)] == ["1.2%"]


def test_existing_exemption_still_needs_its_reason(count_film):
    film, _sweep = count_film(reading={**BINDING, "not_for_reading": True})
    assert text.readtime(film).status == "FAIL"


def test_count_voice_matched_text_keeps_its_measured_phrase_end(count_film):
    reading = {**BINDING, "voice_matched": True, "reason": "The narrator reads this statistic."}
    film, _sweep = count_film(reading=reading)
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"words": [
        {"w": "seventy", "start": 0.0, "end": 0.25}, {"w": "two", "start": 0.25, "end": 0.5},
        {"w": "percent", "start": 0.5, "end": 0.75}, {"w": "complete", "start": 0.75, "end": 4.5}]}}})
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": 0.0}]})
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert any(c["check"] == "voice_end" and c["status"] == "FAIL" for c in result.details["checks"])
    assert _counts(result)[0]["readable_end_seconds"] == 4.0


def test_missing_event_is_hashed_before_ocr_refusal(count_film, monkeypatch):
    film, _sweep = count_film(events=None)
    monkeypatch.setattr(ocr, "available", lambda: (False, {"reason": "No fixture backend", "hint": "Use a backend"}))
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()
    assert result.inputs["timing/visual_events/act1.json"] is None


def test_missing_event_appearance_stales_a_static_no_events_result(count_film):
    film, _sweep = count_film("72%", reading=None, events=None)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.inputs["timing/visual_events/act1.json"] is None
    gates.write_report(film, [result])
    assert gates.load_report(film)["readtime"].status == "PASS"
    _write(film, "timing/visual_events/act1.json", [_event()])
    assert gates.load_report(film)["readtime"].status == "STALE"


def test_changed_count_landing_stales_its_prior_pass(count_film):
    film, _sweep = count_film()
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    gates.write_report(film, [result])
    _write(film, "timing/visual_events/act1.json", [_event(landing=3.0)])
    assert gates.load_report(film)["readtime"].status == "STALE"


@pytest.mark.parametrize("event_id", ["progress-count", "draft"])
def test_count_identity_survives_storyboard_shot_renumbering_and_timing_propagation(count_film, event_id):
    film, _sweep = count_film()
    reading = {"count_events": [{"text": "draft has 72% complete", "events": [event_id]}]}
    script = {"ids": "renumber", "id_prefix": "S", "acts": [{"name": "One", "shots": [
        {"id": "draft", "vo": "", "on_screen": "draft has 72% complete", "dur": 8.0, "reading": reading}]}]}
    plan = model.plan(script, timing={"lead": 0, "tail": 0, "act_tail": 0})
    board = builder.storyboard_doc(plan, film={"id": "demo"}, results=[], previous=None, target={}, made_from={})
    shot = board["acts"][0]["shots"][0]
    assert shot["id"] == "S01" and shot["source_id"] == "draft"
    assert shot["on_screen"] == "S01 has 72% complete"
    assert shot["reading"]["count_events"] == [{"text": "S01 has 72% complete", "events": [event_id]}]
    timed = make_shots(board, clock=Clock(), width=96, height=54)
    assert timed["shots"][0]["reading"] == shot["reading"]
    assert script["acts"][0]["shots"][0]["reading"] == reading


def test_shot_list_only_fallback_retains_explicit_count_metadata(count_film):
    film, _sweep = count_film()
    _change(film, "timing/shots.json", lambda d: d["shots"][0].update(reading=copy.deepcopy(BINDING)))
    (film / "script/script.json").unlink()
    (film / "script/storyboard.json").unlink()
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert _counts(result)[0]["events"] == ["progress-count"]


def _two_counts(count_film, b_range, b_landing):
    a = {**_event("a", landing=1.0), "from": 400, "to": 748}
    b = {**_event("b", landing=b_landing), "from": b_range[0], "to": b_range[1]}
    reading = {"count_events": [{"text": "748", "events": ["a"]}, {"text": "586", "events": ["b"]}]}
    film, sweep = count_film("748 · 586", reading=reading, events=[a, b])
    for s in sweep.samples:
        at, lines = s["t"], [{"text": "400"}, {"text": "900"}]  # a static scale beside the two counters
        if 0.5 <= at < 1.0:
            lines.append({"text": "612"})
        elif at >= 1.0:
            lines.append({"text": "748"})
        if b_landing - 0.5 <= at < b_landing:
            lines.append({"text": "500"})
        elif at >= b_landing:
            lines.append({"text": "586"})
        s["lines"] = lines
    return film


def test_bare_counts_beside_other_numbers_resolve_by_their_declared_ranges(count_film):
    # two businesses' scores and a static 400–900 scale: neither counter can pass through the other's final value
    result = text.readtime(_two_counts(count_film, (400, 586), 2.5))
    assert result.status == "PASS", result.to_dict()
    assert {c["text"] for c in _counts(result)} == {"748", "586"}


def test_a_counter_mid_count_across_the_value_keeps_it_ambiguous(count_film):
    # the second counter sweeps 400 → 900 through 748 just after the first lands: which display is which is unproved
    result = text.readtime(_two_counts(count_film, (400, 900), 1.4))
    assert result.status == "UNCHECKED", result.to_dict()
