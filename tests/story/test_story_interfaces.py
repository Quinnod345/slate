"""Every function docs/ARCHITECTURE.md §12 assigns to slate.story and slate.timing exists with that signature."""

from __future__ import annotations

import inspect

import slate.story
import slate.timing

EMPTY = inspect.Parameter.empty


def _check(fn, positional: list[str], keyword: dict[str, object]) -> None:
    params = inspect.signature(fn).parameters
    names = list(params)
    assert names[: len(positional)] == positional, (fn.__qualname__, names)
    for name in positional:
        assert params[name].kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    for name, default in keyword.items():
        assert name in params, (fn.__qualname__, name)
        if default is not EMPTY:
            assert params[name].default == default, (fn.__qualname__, name, params[name].default)


def test_section_12_signatures_after_a_plain_import():
    _check(slate.story.build, ["film_dir"], {})
    _check(slate.story.gates.check_script, ["script"], {"brand": EMPTY, "fmt": EMPTY})
    _check(slate.timing.build, ["film_dir"], {})
    _check(slate.timing.load_shots, ["film_dir"], {})
    _check(slate.timing.words.word_time, ["film_dir", "shot_id", "word", "occ", "which"], {})
    assert inspect.signature(slate.timing.words.word_time).parameters["occ"].default == 1
    assert inspect.signature(slate.timing.words.word_time).parameters["which"].default == "start"
    _check(slate.timing.spoken.forms, ["token", "lang"], {})
    assert inspect.signature(slate.timing.spoken.forms).parameters["lang"].default == "en"
    # what this module adds for the others: the beat rules (t is the beat) and each number's spoken forms
    _check(slate.timing.beats.problems, ["beats"], {"fps": None})
    _check(slate.timing.spoken.forms_in, ["texts", "lang"], {})
