"""Alignment: local Whisper by default (model recorded as evidence), ElevenLabs Forced Alignment to evaluate, and
replayed takes aligned from their own word times."""

import json

import pytest

from slate import jsonio, ledger
from slate.cli import run_cli
from slate.voice import align
from slate.voice.film import VoiceFilm

from voice_helpers import real_take, whisper_doc


def test_whisper_is_the_default_and_its_model_is_recorded(voice_film, fake_whisper):
    set_transcript, calls = fake_whisper
    vf = VoiceFilm(voice_film)
    wav, words = real_take(vf, "S01", "Every film starts with one line.")
    set_transcript(wav, whisper_doc(words))
    code, out, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01", "--json"])
    assert code == 0, err
    res = json.loads(out)
    assert res["engine"] == "whisper" and res["model"] == "turbo"
    doc = align.load(voice_film, "S01")
    assert doc["engine"]["name"] == "openai-whisper" and doc["engine"]["model"] == "turbo"
    assert doc["sha256"] == jsonio.sha256(wav) and not doc["replay"]
    assert [w["word"] for w in doc["segments"][0]["words"]][:2] == ["Every", "film"]
    assert align.load(voice_film, "S01", "whisper") is not None
    (argv,) = calls()
    assert argv[argv.index("--model") + 1] == "turbo" and argv[argv.index("--language") + 1] == "en"
    assert argv[argv.index("--word_timestamps") + 1] == "True"
    assert VoiceFilm(voice_film).selected("S01")[1]["align"] == "voice/align/S01.json"


def test_forced_alignment_is_evaluation_only(voice_film, fake_whisper):
    set_transcript, _ = fake_whisper
    vf = VoiceFilm(voice_film)
    wav, words = real_take(vf, "S01", "Every film starts with one line.")
    set_transcript(wav, whisper_doc(words))
    align.align(voice_film, only="S01")
    res = align.align(voice_film, only="S01", engine="elevenlabs")
    assert res["model"] == "elevenlabs_forced_alignment"
    fa = align.load(voice_film, "S01", "elevenlabs")
    assert fa["engine"]["name"] == "elevenlabs forced alignment" and fa["engine"]["model"] == "elevenlabs_forced_alignment"
    assert align.load(voice_film, "S01")["engine"]["name"] == "openai-whisper"  # the canonical file stays Whisper's
    assert [r["op"] for r in ledger.rows()] == ["align"]
    cmp = align.compare(voice_film, only="S01")
    shot = cmp["shots"][0]
    assert shot["matched"] == 6 and shot["start_ms_mean"] is not None and shot["start_ms_mean"] < 30
    assert (voice_film / "voice" / "align" / "compare.json").is_file()
    assert "whisper.cpp" not in " ".join(align.ENGINES)


def test_replayed_takes_align_from_their_own_words_through_pacing(voice_film):
    code, _, err = run_cli(["voice", "tts", "--film", str(voice_film), "--only", "S02"])
    assert code == 0, err
    vf = VoiceFilm(voice_film)
    from slate.voice import pace

    pace.pace_take(vf, "S02", no_tempo=True)
    res = align.align(voice_film, only="S02")
    assert res["aligned"][0]["engine"] == "replay"
    doc = align.load(voice_film, "S02")
    assert doc["replay"] and doc["engine"]["name"] == "replay"
    raw_words = vf.take_json("S02", "t01")["replay_words"]
    stage = vf.take_json("S02", "t01")["stages"]["pace"]
    mapped = [w["start"] for w in doc["segments"][0]["words"]]
    assert mapped == [round(pace.map_time(w["start"], stage["cuts"], 1.0), 4) for w in raw_words]


def test_whisper_missing_is_a_missing_prerequisite(voice_film, monkeypatch):
    vf = VoiceFilm(voice_film)
    real_take(vf, "S01", "Every film starts with one line.")
    monkeypatch.setattr(align, "whisper_info", lambda: {"path": None, "version": None})
    code, out, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01"])
    assert code == 3 and "Whisper isn't installed" in err


def test_nothing_to_align(voice_film):
    code, _, err = run_cli(["voice", "align", "--film", str(voice_film)])
    assert code == 3 and "No voiced takes" in err


def test_whisper_model_names():
    assert align.whisper_model("whisper_turbo") == "turbo"
    assert align.whisper_model("large-v3-turbo") == "large-v3-turbo"
    assert align.whisper_model() == "turbo"  # the registry's alignment role
