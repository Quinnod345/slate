"""Fixtures for the QA gate tests (the builders live in qa_core_helpers.py, a name no other test folder uses)."""

import pytest

from qa_core_helpers import FilmBuilder


@pytest.fixture
def film_builder(slate_home):
    """Build a synthetic film: film_builder(**kw) -> FilmBuilder (in this test's own films folder)."""
    made = []

    def make(**kw) -> FilmBuilder:
        kw.setdefault("film_id", f"qa-film-{len(made) + 1}")
        fb = FilmBuilder(slate_home["films"], **kw)
        made.append(fb)
        return fb

    return make


@pytest.fixture
def brand_store(slate_home):
    return slate_home["brands"]
