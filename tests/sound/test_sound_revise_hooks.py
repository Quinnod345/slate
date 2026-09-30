"""Perfect, don't replace, for the sound: `slate revise check` measures a revised mix against the approved one."""

import shutil

import numpy as np
import pytest

from slate.sound import audio, mix, revise_hooks

import sound_helpers as H

SR = 48000


@pytest.fixture
def approved_mix(sound_film, tmp_path):
    path = mix.run(sound_film)
    keep = tmp_path / "approved" / "final_mix.wav"
    keep.parent.mkdir()
    shutil.copyfile(path, keep)
    return sound_film, keep, path


def test_an_unchanged_mix_passes(approved_mix):
    film, approved, current = approved_mix
    r = revise_hooks.compare(film, approved, current)
    assert r.status == "PASS" and r.gate == "level_vs_voice"
    assert abs(r.details["balance_shift_lu"]) < 0.05


def test_music_brought_down_is_a_review_with_the_numbers(approved_mix):
    film, approved, current = approved_mix
    mix.run(film, under=18.0)  # the note: "the music a touch lower"
    r = revise_hooks.compare(film, approved, current)
    assert r.status == "REVIEW" and "moved" in r.summary
    assert -3.6 < r.details["balance_shift_lu"] < -2.4  # the score sat 15 LU under the voice, now 18
    assert abs(r.details["balance"]["revised"]["voice_gain_db"] - r.details["balance"]["approved"]["voice_gain_db"]) < 1


def test_a_shorter_mix_or_one_off_its_target_fails(approved_mix, tmp_path):
    film, approved, current = approved_mix
    y, sr = audio.read_wav(current)
    short = tmp_path / "short" / "final_mix.wav"
    audio.write_wav(short, y[: len(y) - SR // 25], sr, 24)  # a frame shorter: every cue after the cut moved
    r = revise_hooks.compare(film, approved, short)
    assert r.status == "FAIL" and "length" in r.summary
    loud = tmp_path / "loud" / "final_mix.wav"
    audio.write_wav(loud, np.clip(y * 10 ** (4 / 20), -1, 1), sr, 24)
    r = revise_hooks.compare(film, approved, loud)
    assert r.status == "FAIL" and "LUFS" in r.summary


def test_a_revised_stem_is_measured_against_the_voice(approved_mix, tmp_path):
    film, _approved, _current = approved_mix
    music = film / "sound/mix/stems/music.wav"
    before = tmp_path / "before" / "music.wav"
    before.parent.mkdir()
    shutil.copyfile(music, before)
    y, sr = audio.read_wav(music)
    audio.write_wav(music, y * 10 ** (-2.5 / 20), sr, 24)
    r = revise_hooks.compare(film, before, music)
    assert r.status == "REVIEW" and -2.7 < r.details["balance_shift_lu"] < -2.3
    assert revise_hooks.applies(music) and not revise_hooks.applies(film / "voice" / "takes.json")


def test_the_hook_is_offered_to_slate_revise():
    revise = pytest.importorskip("slate.state.revise")
    revise._load_hook_modules()  # noqa: SLF001 - what `slate revise check` does first
    assert "level_vs_voice" in revise.HOOKS and revise.HOOKS["level_vs_voice"].owner == "slate.sound"
