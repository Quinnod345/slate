"""The nine schemas this module owns: they accept what Slate writes and the films' proven shapes, and reject nonsense."""

from __future__ import annotations

import copy
import json

import pytest
from storyfix import SCRIPT, TRUTH

from slate import jsonio
from slate.timing.timeline import collect_events

OWNED = ("script", "storyboard", "shots", "vo_cues", "vo_words", "timeline", "events", "visual_events", "truth")


@pytest.mark.parametrize("name", OWNED)
def test_every_owned_schema_is_a_valid_schema(name):
    import jsonschema

    schema = jsonio.schema(name)
    jsonschema.validators.validator_for(schema).check_schema(schema)
    assert schema.get("description")


def test_script_schema_accepts_the_neutral_script_and_lines():
    assert jsonio.validate(SCRIPT, "script") == []
    as_lines = copy.deepcopy(SCRIPT)
    for act in as_lines["acts"]:
        act["lines"] = act.pop("shots")
    assert jsonio.validate(as_lines, "script") == []


@pytest.mark.parametrize("breakage", [
    lambda s: s["acts"][0]["shots"][0].update(id="1st shot"),
    lambda s: s["acts"][0]["shots"][0].update(dur=0),
    lambda s: s["acts"][0].pop("shots"),
    lambda s: s.update(ids="shuffle"),
    lambda s: s["acts"][0]["shots"][0]["events"][0].pop("t"),
    lambda s: s["acts"][0]["shots"][0]["events"][0].update(land="settle"),
    lambda s: s["acts"][0]["shots"][0].update(transition={"kind": "crossfade"}),  # not one of the law's kinds
    lambda s: s["acts"][0]["shots"][0].update(transition={"note": "a carry, but which kind?"}),
    lambda s: s["acts"][0]["shots"][0].update(engine="blender"),
    lambda s: s.update(timing={"lead": -1}),
])
def test_script_schema_rejects_malformed_scripts(breakage):
    s = copy.deepcopy(SCRIPT)
    breakage(s)
    assert jsonio.validate(s, "script")


def test_truth_schema():
    assert jsonio.validate(TRUTH, "truth") == []
    assert jsonio.validate({"sources": [{"id": "has space"}]}, "truth")
    assert jsonio.validate({"claims": [{"id": "x", "text": "y"}]}, "truth")  # a claim cites its source


def test_events_schema_accepts_collected_events():
    events = collect_events({"version": "v", "acts": [{"name": "A", "shots": SCRIPT["acts"][0]["shots"]}]})
    assert jsonio.validate(events, "events") == []


def test_visual_events_schema_accepts_beats_and_a_reasoned_silence():
    beats = [{"shot": "S02", "id": "door", "t": 3.2, "kind": "land", "size": "m", "note": "the door lands"},
             {"shot": "S03", "id": "count", "t": 5.0, "start": 4.0, "dur": 1.0, "frame": 125, "kind": "count",
              "size": "s", "from": 0, "to": 120},
             {"shot": "S03", "id": "line", "t": 5.5, "start": 4.5, "dur": 1.0, "kind": "draw", "size": "s",
              "note": "the line draws and ends on the beat"},
             {"shot": "S04", "id": "air", "t": 6.0, "start": 5.7, "dur": 0.6, "kind": "whoosh", "pan": -0.4,
              "source": "capture:signup"}]
    assert jsonio.validate(beats, "visual_events") == []
    assert jsonio.validate({"silent": True, "reason": "a held title card"}, "visual_events") == []


@pytest.mark.parametrize("bad", [
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "sparkle"}],
    [{"shot": "S02", "id": "x", "kind": "land"}],
    {"silent": True},
    {"silent": False, "reason": "no"},
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "draw"}],  # a draw names its span
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "count", "from": 0, "to": 9}],  # so does a count
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "whoosh", "dur": 0.5}],  # a length with no start is ambiguous
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "swell", "start": 0.5}],
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "draw", "start": 0.5, "dur": 0}],
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "tap", "pan": 2}],
])
def test_visual_events_schema_rejects_bad_beats_and_silent_acts_without_a_reason(bad):
    assert jsonio.validate(bad, "visual_events")


def test_visual_events_schema_says_which_beat_is_wrong():
    """The problems name the beat and the missing field, so an engine's hand-in check can say what to fix."""
    beats = [{"shot": "S02", "id": "ok", "t": 1.0, "kind": "land"},
             {"shot": "S02", "id": "line", "t": 2.0, "kind": "draw"}]
    problems = jsonio.validate(beats, "visual_events")
    assert problems and all(p.startswith("1: ") for p in problems)
    assert any("'start' is a required property" in p for p in problems)


def test_the_films_v2_beats_fit_except_the_draw_and_count_spans_they_left_implicit(filmroot):
    """Films B and C exported their beats in this vocabulary (their character kind is 'character' in Slate). The one
    thing they left out is the span of a sustained draw or count, the ambiguity this schema removes: one act timed a
    draw's t at its start, the sound handover says a draw ends on t. Every other field fits as delivered."""
    checked, spanless = 0, 0
    for film in ("B", "C"):
        for path in sorted((filmroot / "build" / film / "visual_events").glob("act*.json")):
            beats = json.loads(path.read_text(encoding="utf-8"))
            for beat in beats:
                if beat.get("kind") not in {"tap", "type", "land", "snap", "count", "draw", "data", "page", "stamp",
                                            "whoosh", "swell", "impact"}:
                    beat["kind"] = "character"
                problems = jsonio.validate([beat], "visual_events")
                if beat["kind"] in ("draw", "count") and "start" not in beat:
                    spanless += 1
                    assert problems and all("'start' is a required property" in p or "'dur' is a required property" in p
                                            for p in problems), (path.name, beat, problems)
                else:
                    assert problems == [], (path.name, beat, problems)
            checked += 1
    assert checked == 15
    assert spanless == 102  # B: 72 draws and 8 counts; C: 19 draws and 3 counts


def test_a_scripts_transition_is_words_or_one_of_the_laws_kinds():
    """A shot's transition is the law's kinds when it names one ({kind, note}), and words otherwise, as every
    script so far writes it; a sources entry may quote its source with where it is."""
    from slate.qa.checks.picture import TRANSITIONS
    from slate.story.model import TRANSITION_KINDS

    assert set(TRANSITION_KINDS) == set(TRANSITIONS)  # the schema, the story module and the cuts gate agree
    assert jsonio.schema("script")["$defs"]["transition_kind"]["enum"] == list(TRANSITION_KINDS)
    s = copy.deepcopy(SCRIPT)
    shot = s["acts"][0]["shots"][0]
    for kind in TRANSITION_KINDS:
        shot["transition"] = {"kind": kind, "note": "why"}
        assert jsonio.validate(s, "script") == [], kind
    shot["transition"] = "Whip (motion-masked cut) into the signup screen"
    shot["engine"] = "web"
    shot["sources"] = [{"ref": "app:copy", "quote": "Start free", "where": "src/pages/home.tsx:12"}]
    assert jsonio.validate(s, "script") == []


def test_every_transition_the_films_wrote_still_fits_the_script_schema(filmroot):
    """Checked before the kinds went into the schema: films A, B and C wrote 106 transitions as words with the kind
    first ("Carry: …", "Portal: …", "UI state cut, camera locked"), so the schema keeps words and adds {kind, note}
    rather than an enum a single film's script would fail."""
    seen = 0
    for rel in ("build/shots.json", "build/B/shots.json", "build/C/shots.json"):
        for sh in json.loads((filmroot / rel).read_text())["shots"]:
            words = sh.get("transition_out")
            if not words:
                continue
            seen += 1
            doc = {"acts": [{"name": "A", "shots": [{"id": "S01", "dur": 1.0, "transition": words}]}]}
            assert jsonio.validate(doc, "script") == [], words
    assert seen >= 100
