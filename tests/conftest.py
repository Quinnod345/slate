"""Test isolation: every test runs against temporary Slate folders and in replay mode.

Nothing here may touch the real ~/Library/Application Support/Slate, ~/Movies/Slate or ~/code/slate-brands, and no
test may spend on a vendor. The film project (FILMROOT) is read-only for golden-data tests.
"""

import os
import tempfile
from pathlib import Path

import pytest

_SESSION = Path(tempfile.mkdtemp(prefix="slate-tests-"))
os.environ.setdefault("SLATE_DATA", str(_SESSION / "data"))
os.environ.setdefault("SLATE_FILMS", str(_SESSION / "films"))
os.environ.setdefault("SLATE_BRANDS", str(_SESSION / "brands"))
os.environ["SLATE_FAKE_VENDORS"] = "1"
os.environ.pop("SLATE_LIVE", None)
os.environ["BROWSER"] = "none"

FILMROOT = Path("~/Documents/TrueSpace/website-build-2026-09-28")


@pytest.fixture
def slate_home(tmp_path, monkeypatch):
    """Fresh DATA, FILMS and brand-store folders for one test."""
    data, films, brands = tmp_path / "data", tmp_path / "films", tmp_path / "brands"
    for p in (data, films, brands):
        p.mkdir()
    monkeypatch.setenv("SLATE_DATA", str(data))
    monkeypatch.setenv("SLATE_FILMS", str(films))
    monkeypatch.setenv("SLATE_BRANDS", str(brands))
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "1")
    monkeypatch.delenv("SLATE_LIVE", raising=False)
    monkeypatch.delenv("SLATE_FILM", raising=False)
    return {"data": data, "films": films, "brands": brands, "tmp": tmp_path}


@pytest.fixture
def filmroot():
    """The film project, read-only. Skips when it isn't on this machine (e.g. CI)."""
    if not FILMROOT.exists():
        pytest.skip("film project (golden data) not on this machine")
    return FILMROOT
