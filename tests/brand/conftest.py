"""Fixtures for the brand tests: temporary Slate folders (never the real brand store), and packs made in that
temporary store."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def store(slate_home):
    """The temporary private brand store."""
    return slate_home["brands"]


@pytest.fixture
def studio(slate_home):
    """A brand pack in the temporary store, seeded from the demo pack's design (no references)."""
    from slate.brand import create

    return Path(create.init("studio", from_pack="demo", display_name="Studio")["dir"])


@pytest.fixture
def fresh(slate_home):
    """A brand pack made from the template: every key, nothing filled in."""
    from slate.brand import create

    return Path(create.init("fresh", display_name="Fresh")["dir"])
