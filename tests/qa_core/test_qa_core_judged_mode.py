"""QA judges a film by Slate's own record of its mode (slate.state.autonomy): the claims check stays strict for a film
made autonomous, whatever its film.json says now."""

from __future__ import annotations

from slate.qa.film import FilmContext
from slate.state import autonomy

from qa_core_helpers import write_json


def test_a_film_recorded_autonomous_is_judged_autonomous(film_builder):
    fb = film_builder(mode="autonomous")
    autonomy.record_created(fb.path, dict(fb.film), by="the test")
    write_json(fb.path / "film.json", {**fb.film, "mode": "supervised"})  # what an agent's edit would do
    ctx = FilmContext(fb.path)
    assert ctx.film["mode"] == "supervised" and ctx.mode == "autonomous"


def test_a_film_the_record_doesnt_know_keeps_its_own_mode(film_builder):
    fb = film_builder(mode="draft")
    assert FilmContext(fb.path).mode == "draft"
