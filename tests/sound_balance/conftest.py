"""Fixtures for the balance tests (slate_home and filmroot come from tests/conftest.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import sound_balance_helpers as H  # noqa: E402


@pytest.fixture
def mix_film(slate_home):
    """make(**kw) -> a MixFilm in this test's own films folder."""
    made = []

    def make(**kw) -> H.MixFilm:
        kw.setdefault("film_id", f"bal-film-{len(made) + 1}")
        f = H.MixFilm(slate_home["films"], **kw)
        made.append(f)
        return f

    return make


@pytest.fixture
def helpers():
    return H


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Each test measures its own mix: the balance cache is per process, so start every test without it."""
    from slate.sound import balance

    monkeypatch.setattr(balance, "_CACHE", {})
