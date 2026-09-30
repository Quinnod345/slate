"""Alignment: local Whisper by default (model recorded as evidence), ElevenLabs Forced Alignment to evaluate, and
replayed takes aligned from their own word times."""

import json
import os

import pytest

from slate import jsonio, ledger, procs
from slate.cli import run_cli
from slate.voice import align
from slate.voice.film import VoiceFilm

from voice_helpers import real_take, register_take, whisper_doc


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


def test_slate_whisper_names_the_cli_and_a_broken_one_is_named(voice_film, monkeypatch, tmp_path):
    vf = VoiceFilm(voice_film)
    real_take(vf, "S01", "Every film starts with one line.")
    missing = tmp_path / "no-such-whisper"
    monkeypatch.setenv("SLATE_WHISPER", str(missing))
    assert align.whisper_info()["path"] is None
    code, _, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01"])
    assert code == 3 and "SLATE_WHISPER" in err and str(missing) in err


def test_a_test_without_the_stand_in_never_loads_the_real_model(voice_film):
    """The guard answers for Whisper in every unmarked test: loud failure, nothing loaded."""
    assert "guard-bin" in os.environ["SLATE_WHISPER"]
    vf = VoiceFilm(voice_film)
    real_take(vf, "S01", "Every film starts with one line.")
    code, _, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01"])
    assert code == 1 and "tried to run the real Whisper" in err


@pytest.mark.whisper
def test_live_whisper_hears_a_line_the_mac_speaks(voice_film):
    """Opt-in (`pytest -m whisper`): the real Whisper CLI hears a line spoken by macOS `say`, and the alignment
    records the model and the build. SLATE_TEST_WHISPER_MODEL picks the model (default base, the small one)."""
    say = procs.which("say")
    if not say:
        pytest.skip("macOS `say` isn't here to speak the line")
    if not align.whisper_info()["path"]:
        pytest.skip("Whisper isn't installed")
    vf = VoiceFilm(voice_film)
    wav = vf.take_dir("S01") / "t01.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    procs.run([say, "-o", str(wav), "--file-format=WAVE", "--data-format=LEI16@48000",
               "Every film starts with one line."], check=True, timeout=60)
    register_take(vf, "S01", wav)
    model = os.environ.get("SLATE_TEST_WHISPER_MODEL", "base")
    res = align.align(voice_film, only="S01", model=model)
    assert res["aligned"] and res["aligned"][0]["engine"] == f"whisper {model}"
    doc = align.load(voice_film, "S01")
    assert doc["engine"]["name"] == "openai-whisper" and doc["engine"]["model"] == model and not doc["replay"]
    heard = [w["word"].strip(".,").lower() for s in doc["segments"] for w in s["words"]]
    assert {"film", "starts", "line"} <= set(heard), heard
    starts = [w["start"] for s in doc["segments"] for w in s["words"]]
    assert starts == sorted(starts)
