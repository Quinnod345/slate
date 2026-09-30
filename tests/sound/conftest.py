"""Fixtures for the sound tests (the shared slate_home and filmroot fixtures come from tests/conftest.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import sound_helpers as H  # noqa: E402

from slate import replay  # noqa: E402
from slate.vendors import elevenlabs  # noqa: E402


@pytest.fixture(autouse=True)
def _own_fakers(monkeypatch):
    """Replayed music comes from the ElevenLabs module's own fakers, whatever an earlier test registered in their
    place (the shared registry is process-wide)."""
    for op, faker in elevenlabs.FAKERS.items():
        monkeypatch.setitem(replay._FAKERS, (elevenlabs.PROVIDER, op), faker)  # noqa: SLF001


@pytest.fixture
def sound_film(slate_home):
    """A mixed-ready neutral film: voice, word timings, a score, the neutral test brand."""
    H.brand(slate_home["brands"])
    return H.film(slate_home["films"])


@pytest.fixture
def helpers():
    return H
