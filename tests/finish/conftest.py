"""Fixtures for the finish tests: temp Slate folders, the fixture formats and brands behind small fakes, libx264 for
deterministic encodes, Downloads in a temp folder, and a notify transport that records instead of sending.

Nothing here opens After Effects: the AE tests replace slate.finish.deps' AE calls with fakes, and deps itself refuses
to reach the real ones under pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import filmkit  # noqa: E402

SETTINGS = '[finish.review]\nencoder = "libx264"\n[finish.h264]\nencoder = "libx264"\n'


@pytest.fixture
def fin(slate_home, monkeypatch):
    filmkit.install_fakes(slate_home["brands"], setitem=monkeypatch.setitem)
    (slate_home["data"] / "slate.toml").write_text(SETTINGS)
    downloads = slate_home["tmp"] / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("SLATE_DOWNLOADS", str(downloads))
    monkeypatch.delenv("SLATE_NO_NOTIFY", raising=False)
    monkeypatch.delenv("SLATE_NOTIFY_IMESSAGE", raising=False)
    monkeypatch.delenv("SLATE_NOTIFY_PUSH_NODE", raising=False)
    from slate.state import deps as state_deps

    state_deps._WARNED.clear()
    return {**slate_home, "downloads": downloads}


@pytest.fixture
def settings_toml(fin):
    """Append lines to slate.toml (on top of the libx264 defaults)."""

    def write(extra: str) -> None:
        (fin["data"] / "slate.toml").write_text(SETTINGS + extra)

    return write


@pytest.fixture
def make_film(fin):
    def make(film_id: str = "f1", **kw) -> Path:
        return filmkit.make(fin["films"], film_id, **kw)

    return make


@pytest.fixture(autouse=True)
def sends(monkeypatch):
    """Every test: slate.notify's transport records the argv it would run and reports success."""
    calls: list[list[str]] = []

    def fake(argv, *, timeout):
        calls.append(list(argv))
        return {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""}

    monkeypatch.setattr("slate.notify.transport", fake)
    return calls

