"""What t, start and dur mean in timing/visual_events: t is always the beat; a draw or a count ends on it."""

from __future__ import annotations

import pytest

from slate import jsonio
from slate.timing import beats


def _beat(kind: str, t: float, **extra) -> dict:
    return {"shot": "S01", "id": f"{kind}-1", "t": t, "kind": kind, "size": "s", "note": "a beat", **extra}


def test_the_checker_knows_the_same_kinds_as_the_schema():
    assert set(beats.KINDS) == set(jsonio.schema("visual_events")["$defs"]["beat"]["properties"]["kind"]["enum"])
    assert set(beats.SUSTAINED) <= set(beats.KINDS)


def test_good_spans_pass():
    good = [_beat("draw", 2.0, start=1.2, dur=0.8), _beat("count", 3.0, start=2.0, dur=1.0, **{"from": 0, "to": 120}),
            _beat("whoosh", 4.1, start=3.9, dur=0.5), _beat("swell", 6.0, start=4.0, dur=2.6),
            _beat("land", 7.0), _beat("type", 7.5, start=7.2, dur=0.6)]
    assert beats.problems(good, fps=24000 / 1001) == []
    assert jsonio.validate(good, "visual_events") == []


def test_a_silent_act_has_nothing_to_check():
    assert beats.problems({"silent": True, "reason": "a held card"}) == []


@pytest.mark.parametrize("beat, words", [
    (_beat("draw", 2.0), "needs start and dur (it ends on t)"),
    (_beat("count", 2.0, **{"from": 0, "to": 9}), "needs start and dur"),
    (_beat("draw", 1.2, start=1.2, dur=0.8), "ends on its beat"),  # the films' ambiguity: t written at the start
    (_beat("count", 2.5, start=1.0, dur=1.0), "ends on its beat"),
    (_beat("whoosh", 5.0, start=3.0, dur=0.5), "peaks inside its span"),
    (_beat("land", 5.0, start=3.0, dur=0.5), "lands inside its span"),
    (_beat("whoosh", 5.0, dur=0.5), "start and dur go together"),
    (_beat("swell", 5.0, start=4.0), "start and dur go together"),
    (_beat("swell", 5.0, start="soon", dur=1.0), "must be numbers"),
    (_beat("swell", 5.0, start=4.0, dur=-1.0), "isn't a forward stretch"),
])
def test_spans_that_contradict_their_beat_are_named(beat, words):
    found = beats.problems([beat], fps=25)
    assert len(found) == 1 and words in found[0]
    assert beat["id"] in found[0]  # it says which beat


def test_half_a_frame_of_rounding_is_allowed_and_no_more():
    fps = 24000 / 1001
    half = 0.5 / fps
    assert beats.problems([_beat("draw", 2.0 + half * 0.9, start=1.0, dur=1.0)], fps=fps) == []
    assert beats.problems([_beat("draw", 2.0 + half * 1.1, start=1.0, dur=1.0)], fps=fps)
    # without a clock, the millisecond rounding writers use
    assert beats.problems([_beat("draw", 2.0015, start=1.0, dur=1.0)]) == []
    assert beats.problems([_beat("draw", 2.004, start=1.0, dur=1.0)])


def test_span_and_the_sound_side_fallback():
    assert beats.span(_beat("draw", 2.0, start=1.5, dur=0.5)) == (1.5, 2.0)
    assert beats.span(_beat("land", 2.0)) is None
    assert beats.span("not a beat") is None  # type: ignore[arg-type]
    # legacy beats without a span: a reader with a default length ends a draw on t and centres a swell on it
    assert beats.beat_span(_beat("draw", 2.0), default_dur=0.5) == (1.5, 2.0)
    assert beats.beat_span(_beat("swell", 2.0), default_dur=1.0) == (1.5, 2.5)
    assert beats.beat_span(_beat("draw", 0.2), default_dur=0.5) == (0.0, 0.2)
    assert beats.beat_span(_beat("land", 2.0), default_dur=0.5) is None  # a point beat has no span to guess
    assert beats.beat_span(_beat("draw", 2.0)) is None  # no default: no guess
    assert beats.beat_span(_beat("draw", 2.0, start=1.0, dur=1.0), default_dur=0.2) == (1.0, 2.0)


def test_the_module_resolves_after_a_plain_import():
    import slate.timing

    assert slate.timing.beats.problems([]) == []


def test_an_act_lands_at_most_one_impact():
    """L-sound-D068: an impact is the act's one big landing. The kit and the engine refuse a second; so does every
    Python reader (qa's events_complete, sound), through beats.problems."""
    one = [_beat("land", 1.0), _beat("impact", 2.0), _beat("tap", 3.0)]
    assert beats.problems(one) == []
    two = one + [{**_beat("impact", 5.5), "id": "impact-2"}]
    found = beats.problems(two, fps=24)
    assert len(found) == 1 and found[0].startswith("2 impacts in one act")
    assert "impact-1" in found[0] and "impact-2" in found[0] and "5.500 s" in found[0]
    assert jsonio.validate(two, "visual_events") == []  # the schema can't say it; problems() does
