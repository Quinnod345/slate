"""Fixtures for the sound tests (the shared slate_home and filmroot fixtures come from tests/conftest.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import sound_helpers as H  # noqa: E402


@pytest.fixture
def sound_film(slate_home):
    """A mixed-ready neutral film: voice, word timings, a score, the neutral test brand."""
    H.brand(slate_home["brands"])
    return H.film(slate_home["films"])


@pytest.fixture
def helpers():
    return H
