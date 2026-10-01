"""Generation judges a film by Slate's own record of its mode (slate.state.autonomy): a film made autonomous still
waits for a person's yes before every generation, whatever its film.json says now."""

from __future__ import annotations

import json

from slate import jsonio
from slate.gen import policy


def test_a_film_made_autonomous_still_asks_before_every_generation(make_film):
    film = make_film("held", mode="autonomous")
    fj = jsonio.read(film / "film.json")
    fj["mode"] = "supervised"  # what an agent's edit would do
    (film / "film.json").write_text(json.dumps(fj, indent=1))
    assert policy.mode(fj) == "autonomous" and policy.mode(fj, film) == "autonomous"
    assert policy.requires_approval(fj)


def test_a_supervised_film_stays_supervised(make_film):
    film = make_film("own", mode="supervised")
    fj = jsonio.read(film / "film.json")
    assert policy.mode(fj, film) == "supervised" and not policy.requires_approval(fj)
