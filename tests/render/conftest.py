"""Fixtures for the render tests. The fakes themselves live in render_fakes.py (a unique module name, since every
builder's test folder has its own conftest.py)."""

import pytest

from render_fakes import Fakes, make_film, write_toml


@pytest.fixture
def fakes(slate_home, monkeypatch, tmp_path):
    f = Fakes(monkeypatch, tmp_path)
    write_toml(slate_home["data"], "[render]\nchunk_frames = 10\nforeground_max_subframes = 100000\n")
    return f


@pytest.fixture
def film(slate_home):
    return make_film(slate_home["films"])
