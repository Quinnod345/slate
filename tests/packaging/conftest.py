"""Fixtures for the packaging tests: a neutral skills folder, a neutral brand and format, and a job queue whose runner
never starts (jobs are queued and inspected, never run, so nothing is left running after a test)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def skills_src(tmp_path):
    """A copy of the fixture skills folder, safe to edit."""
    dst = tmp_path / "skills"
    shutil.copytree(FIXTURES / "skills", dst)
    return dst


@pytest.fixture
def build_out(tmp_path):
    out = tmp_path / "plugin"
    out.mkdir()
    return out


@pytest.fixture
def studio(slate_home, monkeypatch):
    """Temporary Slate folders with the neutral brand in the brand store, the fixture format's path, and a queue that
    records jobs without starting a runner."""
    shutil.copytree(FIXTURES / "brands" / "plain-brand", slate_home["brands"] / "plain-brand")
    from slate.jobs import client

    monkeypatch.setattr(client, "ensure_runner", lambda: "running")
    return {**slate_home, "format": str(FIXTURES / "formats" / "plain-reel.json")}
