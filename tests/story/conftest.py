"""Fixtures for the story and timing tests (helpers live in storyfix.py)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from storyfix import BRAND, FILM, SCRIPT, TRUTH, voice_takes, write_json


@pytest.fixture
def make_film(slate_home):
    """A factory for scratch films under the test's own SLATE_FILMS, with the neutral brand in SLATE_BRANDS."""

    def _make(film_id: str = "bakery", *, script=None, brand=None, truth=None, film=None, takes=None) -> Path:
        brand_doc = copy.deepcopy(BRAND if brand is None else brand)
        write_json(slate_home["brands"] / brand_doc.get("name", "crumb") / "brand.json", brand_doc)
        film_doc = copy.deepcopy(FILM)
        film_doc["id"] = film_id
        film_doc["brand"] = brand_doc.get("name", "crumb")
        film_doc.update(film or {})
        folder = slate_home["films"] / film_id
        write_json(folder / "film.json", film_doc)
        write_json(folder / "script" / "script.json", copy.deepcopy(SCRIPT if script is None else script))
        write_json(folder / "brief" / "truth.json", copy.deepcopy(TRUTH if truth is None else truth))
        if takes:
            voice_takes(folder, takes)
        return folder

    return _make

