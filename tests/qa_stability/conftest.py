"""Fixtures for the stability tests (slate_home and filmroot come from tests/conftest.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import qa_stability_helpers as H  # noqa: E402


@pytest.fixture
def stab_film(slate_home):
    """make(**kw) -> a StabilityFilm in this test's own films folder."""
    made = []

    def make(**kw) -> H.StabilityFilm:
        kw.setdefault("film_id", f"stab-film-{len(made) + 1}")
        f = H.StabilityFilm(slate_home["films"], **kw)
        made.append(f)
        return f

    return make


@pytest.fixture
def helpers():
    return H
