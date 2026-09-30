"""Fixtures for the job-queue tests: a fast queue in a temp DATA folder, and nothing left running afterwards."""

from __future__ import annotations

import pytest

from jobs_helpers import cleanup, write_toml


@pytest.fixture
def queue_home(slate_home):
    """slate_home, plus a quick queue: 0.1 s passes, a 2 s kill grace, and no load guard (the test Mac is busy)."""
    data = slate_home["data"]
    write_toml(data, "[jobs]\npoll_seconds = 0.1\nkill_grace_s = 2\nmax_load_per_core = 0\n")
    yield slate_home
    cleanup(data)
