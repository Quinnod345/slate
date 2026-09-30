"""Fixtures for the voice tests (the helpers live in voice_helpers.py)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from voice_helpers import BRAND, FAKE_WHISPER, make_film


@pytest.fixture
def brand(monkeypatch):
    """The neutral brand, served to the voice profile without depending on the brand module's pack schema."""
    from slate.voice import profile

    data = json.loads(json.dumps(BRAND))
    monkeypatch.setattr(profile, "_brand_pack", lambda ref: data)
    return data


@pytest.fixture
def voice_film(slate_home, brand, monkeypatch, tmp_path):
    """A neutral film in a fresh Slate home, in replay mode."""
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fixtures"))
    return make_film(slate_home["films"])


@pytest.fixture
def fake_whisper(tmp_path, monkeypatch):
    """Put a `whisper` on PATH that answers from a map; returns (set_transcript(path, doc), calls())."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "whisper"
    exe.write_text(FAKE_WHISPER.replace("{python}", sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    plan_path, log_path = tmp_path / "whisper_map.json", tmp_path / "whisper_calls.jsonl"
    plan_path.write_text("{}")
    log_path.write_text("")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("SLATE_TEST_WHISPER_MAP", str(plan_path))
    monkeypatch.setenv("SLATE_TEST_WHISPER_LOG", str(log_path))

    def set_transcript(path: Path, doc: dict) -> None:
        plan = json.loads(plan_path.read_text())
        plan[os.path.realpath(path)] = doc
        plan_path.write_text(json.dumps(plan))

    def calls() -> list[list[str]]:
        return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    return set_transcript, calls
