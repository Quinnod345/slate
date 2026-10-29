"""Fixtures for the voice tests (the helpers live in voice_helpers.py).

No test here runs the real Whisper model unless it's marked `whisper` and opted in (`pytest -m whisper`, or
SLATE_WHISPER_LIVE=1): the model takes minutes on a CPU and competes with everything else on the Mac. Every other test
hears through a stand-in that answers from recorded transcripts (`fake_whisper`), and a test that forgets to ask for
it meets a guard that fails loudly instead of quietly loading the model.
"""

from __future__ import annotations

import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from slate import replay
from slate.vendors import elevenlabs

from voice_helpers import BRAND, FAKE_WHISPER, GUARD_WHISPER, make_film

LIVE_WHISPER = "whisper"


@pytest.fixture
def full_voice_policy(voice_film):
    """Opt this test into a voice's explicit full-voice preference; other films keep the open default."""
    path = voice_film / "film.json"
    doc = json.loads(path.read_text())
    doc.setdefault("voice", {}).setdefault("profile", {}).setdefault("tag_policy", {})["avoid_low_effort"] = True
    path.write_text(json.dumps(doc))
    return voice_film


def pytest_configure(config):
    config.addinivalue_line("markers", f"{LIVE_WHISPER}: runs the real Whisper CLI and model (minutes on a CPU); "
                                       "opt in with -m whisper or SLATE_WHISPER_LIVE=1")


def _live_whisper_wanted(config) -> bool:
    return LIVE_WHISPER in (config.getoption("markexpr", default="") or "") or \
        os.environ.get("SLATE_WHISPER_LIVE") == "1"


def pytest_collection_modifyitems(config, items):
    if _live_whisper_wanted(config):
        return
    skip = pytest.mark.skip(reason="runs the real Whisper model; opt in with -m whisper or SLATE_WHISPER_LIVE=1")
    for item in items:
        if item.get_closest_marker(LIVE_WHISPER):
            item.add_marker(skip)


def _script(folder: Path, name: str, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_text(body.replace("{python}", sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


@pytest.fixture(autouse=True)
def _own_fakers(monkeypatch):
    """Replay answers come from the vendor module's own fakers, whatever an earlier test registered in their place
    (the shared registry is process-wide, and a test elsewhere once left a stub for ElevenLabs TTS behind)."""
    for op, faker in elevenlabs.FAKERS.items():
        monkeypatch.setitem(replay._FAKERS, (elevenlabs.PROVIDER, op), faker)  # noqa: SLF001


@pytest.fixture(autouse=True)
def _own_resume_hooks(monkeypatch):
    """The balance readers the client registers with the job queue, whatever an earlier test removed."""
    try:
        resume = importlib.import_module("slate.jobs.resume")
    except ModuleNotFoundError:
        return
    for name, reader in elevenlabs.BALANCE_READERS.items():
        monkeypatch.setitem(resume._BALANCES, name, reader)  # noqa: SLF001


@pytest.fixture(autouse=True)
def _no_real_whisper(request, tmp_path, monkeypatch):
    """Every voice test hears through a stand-in unless it's a marked, opted-in live test."""
    if request.node.get_closest_marker(LIVE_WHISPER):
        monkeypatch.delenv("SLATE_WHISPER", raising=False)
        return
    guard = _script(tmp_path / "guard-bin", "whisper", GUARD_WHISPER)
    monkeypatch.setenv("SLATE_WHISPER", str(guard))


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
    """A Whisper CLI that answers from recorded transcripts; returns (set_transcript(path, doc), calls()).

    It's named through SLATE_WHISPER, because Slate's PATH puts Homebrew's real `whisper` first whatever PATH says."""
    exe = _script(tmp_path / "bin", "whisper", FAKE_WHISPER)
    plan_path, log_path = tmp_path / "whisper_map.json", tmp_path / "whisper_calls.jsonl"
    plan_path.write_text("{}")
    log_path.write_text("")
    monkeypatch.setenv("SLATE_WHISPER", str(exe))
    monkeypatch.setenv("SLATE_TEST_WHISPER_MAP", str(plan_path))
    monkeypatch.setenv("SLATE_TEST_WHISPER_LOG", str(log_path))

    def set_transcript(path: Path, doc: dict) -> None:
        plan = json.loads(plan_path.read_text())
        plan[os.path.realpath(path)] = doc
        plan_path.write_text(json.dumps(plan))

    def calls() -> list[list[str]]:
        return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    return set_transcript, calls
