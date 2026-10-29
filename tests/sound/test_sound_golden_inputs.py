"""Delivery provenance chooses voice inputs; waveform measurements still establish alignment."""

import json

import pytest

from test_sound_golden import _delivered_voice_inputs


def _write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _revision(root, folder, *, frames=48, start=0.0, archived=False):
    _write(root, f"{folder}/shots.json", {"total_frames": frames, "fps_num": 24, "fps_den": 1,
                                        "shots": [{"id": "C01", "sec_in": start}]})
    _write(root, f"{folder}/vo_cues.json", {"cues": [{"shot": "C01", "at": 0.1,
                                                      "file": "sound/vo/narrator/C01.wav"}]})
    take = root / (f"{folder.rsplit('/', 1)[0]}/narrator/C01.wav" if archived else "sound/vo/narrator/C01.wav")
    take.parent.mkdir(parents=True, exist_ok=True)
    take.write_bytes(b"a take whose waveform will be checked separately")
    return take


def _delivery(root):
    _write(root, "build/C/audio/final_mix.json", {"samples": 96000})
    _write(root, "build/C/audio/verify.json", {"voice": {"per_take": [{"shot": "C01", "expected_s": 0.1}]}})


def test_voice_oracle_uses_preserved_clock_and_takes_after_a_revoice(tmp_path):
    _delivery(tmp_path)
    _revision(tmp_path, "build/C", frames=47)
    folder = "sound/vo/narrator_before_revision/build_C"
    take = _revision(tmp_path, folder, archived=True)
    source, shots, cues = _delivered_voice_inputs(tmp_path, "C")
    assert source == tmp_path / folder and shots["total_frames"] == 48
    assert cues["cues"][0]["file"] == str(take)


def test_wrong_clock_or_cue_never_becomes_the_delivered_oracle(tmp_path):
    _delivery(tmp_path)
    _revision(tmp_path, "build/C", frames=47)
    _revision(tmp_path, "sound/vo/narrator_before_revision/build_C", start=0.01, archived=True)
    with pytest.raises(AssertionError, match="need one preserved voice input"):
        _delivered_voice_inputs(tmp_path, "C")


def test_ambiguous_archives_refuse_instead_of_choosing_a_convenient_take(tmp_path):
    _delivery(tmp_path)
    for revision in ("one", "two"):
        _revision(tmp_path, f"sound/vo/narrator_before_{revision}/build_C", archived=True)
    with pytest.raises(AssertionError, match="need one preserved voice input"):
        _delivered_voice_inputs(tmp_path, "C")


def test_delivered_verifier_names_the_polished_take_even_when_the_clock_is_unchanged(tmp_path):
    _delivery(tmp_path)
    raw = _revision(tmp_path, "build/C")
    polished = tmp_path / "sound/vo/narrator_polished/C01.wav"
    polished.parent.mkdir(parents=True)
    polished.write_bytes(b"independent delivered polished take")
    _write(tmp_path, "build/C/audio/verify.json", {"voice": {"per_take": [
        {"shot": "C01", "expected_s": 0.1, "take": "sound/vo/narrator_polished/C01.wav"}]}})
    _, _, cues = _delivered_voice_inputs(tmp_path, "C")
    assert cues["cues"][0]["file"] == str(polished)
    assert raw.read_bytes() == b"a take whose waveform will be checked separately"


def test_missing_explicit_delivered_take_never_falls_back_to_the_raw_take(tmp_path):
    _delivery(tmp_path)
    _revision(tmp_path, "build/C")
    _write(tmp_path, "build/C/audio/verify.json", {"voice": {"per_take": [
        {"shot": "C01", "expected_s": 0.1, "take": "sound/vo/narrator_polished/C01.wav"}]}})
    with pytest.raises(AssertionError, match="need one preserved voice input"):
        _delivered_voice_inputs(tmp_path, "C")
