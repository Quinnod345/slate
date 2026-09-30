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
             {"shot": "S03", "id": "count", "t": 5.0, "frame": 125, "kind": "count", "size": "s", "from": 0, "to": 120}]
    assert jsonio.validate(beats, "visual_events") == []
    assert jsonio.validate({"silent": True, "reason": "a held title card"}, "visual_events") == []


@pytest.mark.parametrize("bad", [
    [{"shot": "S02", "id": "x", "t": 1.0, "kind": "sparkle"}],
    [{"shot": "S02", "id": "x", "kind": "land"}],
    {"silent": True},
    {"silent": False, "reason": "no"},
])
def test_visual_events_schema_rejects_unknown_kinds_and_silent_acts_without_a_reason(bad):
    assert jsonio.validate(bad, "visual_events")


def test_the_films_v2_beats_fit_once_the_character_kind_is_named(filmroot):
    """Films B and C exported their beats in this vocabulary; their character kind is 'character' in Slate."""
    checked = 0
    for film in ("B", "C"):
        for path in sorted((filmroot / "build" / film / "visual_events").glob("act*.json")):
            beats = json.loads(path.read_text(encoding="utf-8"))
            for beat in beats:
                if beat.get("kind") not in {"tap", "type", "land", "snap", "count", "draw", "data", "page", "stamp",
                                            "whoosh", "swell", "impact"}:
                    beat["kind"] = "character"
            assert jsonio.validate(beats, "visual_events") == [], path.name
            checked += 1
    assert checked == 15
