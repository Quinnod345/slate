"""Finish judges a film by Slate's own record of its mode (slate.state.autonomy): a film made autonomous is reviewed,
held and delivered as one, whatever its film.json says now. Any other film keeps its film.json mode."""

from __future__ import annotations

import json

from slate import jsonio
from slate.finish import review
from slate.finish.film import Film
from slate.state import autonomy


def _flip(d, to: str) -> None:
    fj = jsonio.read(d / "film.json")
    fj["mode"] = to
    (d / "film.json").write_text(json.dumps(fj, indent=1))


def test_a_film_recorded_autonomous_stays_autonomous_in_finish(make_film):
    d = make_film("held", mode="autonomous")
    autonomy.record_created(d, jsonio.read(d / "film.json"), by="the test")
    _flip(d, "supervised")  # what an agent's edit would do
    film = Film(d)
    assert film.data["mode"] == "supervised" and film.mode == "autonomous"
    assert review.default_destination(film) == "imessage"  # the hold's route, not your Downloads


def test_a_film_made_supervised_follows_its_film_json(make_film):
    d = make_film("plain", mode="supervised")
    _flip(d, "draft")  # only leaving autonomous is locked; other modes are film.json's
    film = Film(d)
    assert film.mode == "draft" and review.default_destination(film) == "downloads"
