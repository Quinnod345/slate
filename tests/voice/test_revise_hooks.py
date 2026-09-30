"""Perfect, don't replace: a revised take is measured against the approved one."""

import numpy as np

from slate import jsonio
from slate.voice import audio, revise_hooks

from voice_helpers import speech

SR = 48000


def _take(folder, name, *, seed="v", gain=1.0, voice_id="voice-one"):
    folder.mkdir(parents=True, exist_ok=True)
    x, _ = speech("One line read the same way twice.".split(), seed)
    audio.write_wav(folder / f"{name}.wav", x * gain, SR)
    jsonio.write_atomic(folder / f"{name}.json", {"voice_id": voice_id, "model": "eleven_v4"})
    return folder / f"{name}.wav"


def test_the_same_read_passes(tmp_path):
    folder = tmp_path / "voice" / "takes" / "S01"
    a, b = _take(folder, "t01"), _take(folder, "t02", gain=0.9)
    r = revise_hooks.compare(tmp_path, a, b, {})
    assert r.status == "PASS" and r.gate == "voice_revision"


def test_a_big_level_or_pitch_move_asks_for_ears(tmp_path):
    folder = tmp_path / "voice" / "takes" / "S01"
    a, b = _take(folder, "t01"), _take(folder, "t02", gain=0.3)
    r = revise_hooks.compare(tmp_path, a, b, {})
    assert r.status == "REVIEW" and "level moved" in r.summary


def test_a_different_voice_is_a_replacement(tmp_path):
    folder = tmp_path / "voice" / "takes" / "S01"
    a, b = _take(folder, "t01"), _take(folder, "t02", voice_id="another-voice")
    r = revise_hooks.compare(tmp_path, a, b, {})
    assert r.status == "FAIL" and "replaces the approved read" in r.summary


def test_the_hook_registers_with_film_state():
    assert revise_hooks.applies(__import__("pathlib").Path("voice/takes/S01/t01.paced.wav"))
    assert not revise_hooks.applies(__import__("pathlib").Path("sound/mix.wav"))
    try:
        from slate.state import revise
    except ModuleNotFoundError:
        return
    assert "voice_revision" in revise.HOOKS
