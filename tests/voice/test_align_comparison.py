"""Saved alignment comparisons must bind current inputs and compare the same spoken words."""

import json

import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.voice import align
from slate.voice.film import VoiceFilm
from slate.voice.measure import align_words

from voice_helpers import register_take


def comparison_files(film, *, whisper="alpha beta gamma", forced="alpha beta gamma"):
    story = film / "script" / "storyboard.json"
    data = jsonio.read(story)
    data["acts"][0]["shots"][0]["vo"] = forced
    jsonio.write_atomic(story, data)
    vf = VoiceFilm(film)
    audio = vf.take_dir("S01") / "t01.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"synthetic audio identity; comparison never decodes it")
    register_take(vf, "S01", audio)
    docs = {}
    for engine, text in (("whisper", whisper), ("elevenlabs", forced)):
        words = [{"word": w, "start": float(i), "end": i + 0.5} for i, w in enumerate(text.split())]
        doc = {"audio": str(audio), "sha256": jsonio.sha256(audio), "replay": False,
               "engine": {"name": "openai-whisper" if engine == "whisper" else "elevenlabs forced alignment",
                          "model": "turbo" if engine == "whisper" else "elevenlabs_forced_alignment"},
               "segments": [{"words": words}]}
        if engine == "elevenlabs":
            doc["source_text"] = forced
        jsonio.write_atomic(film / "voice" / "align" / f"S01.{engine}.json", doc)
        docs[engine] = doc
    return audio, docs


def save_doc(film, engine, doc):
    jsonio.write_atomic(film / "voice" / "align" / f"S01.{engine}.json", doc)


def test_substitutions_do_not_count_as_matching_word_times(voice_film):
    _, docs = comparison_files(voice_film, whisper="alpha wrong gamma")
    words = docs["elevenlabs"]["segments"][0]["words"]
    for i in (0, 2):
        words[i]["start"] += 0.1
        words[i]["end"] += 0.2
    save_doc(voice_film, "elevenlabs", docs["elevenlabs"])
    result = align.compare(voice_film, only="S01")
    shot = result["shots"][0]
    assert result["complete"] and shot["comparison_ready"]
    assert shot["matched"] == 2 and shot["start_ms_mean"] == 100.0
    assert shot["end_ms_mean"] == 200.0
    assert shot["coverage"]["whisper_fraction"] == pytest.approx(2 / 3)
    assert shot["coverage"]["fa_fraction"] == pytest.approx(2 / 3)
    assert shot["substitutions"] == [{"fa_index": 1, "whisper_index": 1,
                                        "fa_word": "beta", "whisper_word": "wrong"}]
    assert [w["index"] for w in shot["unmatched_whisper_words"]] == [1]
    assert [w["index"] for w in shot["unmatched_fa_words"]] == [1]


def test_no_equal_words_produces_no_timing_agreement(voice_film):
    comparison_files(voice_film, whisper="banana", forced="durable")
    result = align.compare(voice_film, only="S01")
    shot = result["shots"][0]
    assert shot["matched"] == 0 and shot["start_ms_mean"] is None
    assert result["overall_start_ms_mean"] is None and not result["complete"]
    assert len(shot["substitutions"]) == 1


@pytest.mark.parametrize(("whisper", "field", "word"), [
    ("alpha gamma", "deletions", "beta"),
    ("alpha bonus beta gamma", "insertions", "bonus"),
    ("alpha alpha beta gamma", "insertions", "alpha"),
])
def test_coverage_keeps_missing_extra_and_repeated_occurrences(voice_film, whisper, field, word):
    comparison_files(voice_film, whisper=whisper)
    shot = align.compare(voice_film, only="S01")["shots"][0]
    assert len(shot[field]) == 1 and shot[field][0]["word"] == word
    assert shot["matched"] == (2 if field == "deletions" else 3)
    assert not shot["substitutions"]
    assert len(shot["unmatched_fa_words" if field == "deletions" else "unmatched_whisper_words"]) == 1


@pytest.mark.parametrize(("whisper", "forced", "matched"), [
    ("ALPHA, beta!", "alpha beta", 2),
    ("don't 2,500", "don’t 2500", 2),
    ("cant 664", "can't six sixty four", 0),
])
def test_normalization_preserves_contractions_and_number_spelling(voice_film, whisper, forced, matched):
    comparison_files(voice_film, whisper=whisper, forced=forced)
    shot = align.compare(voice_film, only="S01")["shots"][0]
    assert shot["matched"] == matched


@pytest.mark.parametrize("engine", ["whisper", "elevenlabs"])
@pytest.mark.parametrize("problem", ["wrong_hash", "missing_hash", "wrong_audio", "missing_audio"])
def test_each_engine_must_bind_the_selected_input(voice_film, engine, problem):
    audio, docs = comparison_files(voice_film)
    doc = docs[engine]
    if problem == "wrong_hash":
        doc["sha256"] = "0" * 64
    elif problem == "missing_hash":
        del doc["sha256"]
    elif problem == "wrong_audio":
        other = audio.with_name("other.wav")
        other.write_bytes(audio.read_bytes())
        doc["audio"] = str(other)
    else:
        del doc["audio"]
    save_doc(voice_film, engine, doc)
    result = align.compare(voice_film, only="S01")
    shot = result["shots"][0]
    assert not result["complete"] and not shot["comparison_ready"]
    assert shot["start_ms_mean"] is None and engine in shot["status"]


def test_changed_selected_audio_is_not_compared(voice_film):
    audio, _ = comparison_files(voice_film)
    audio.write_bytes(audio.read_bytes() + b"changed")
    result = align.compare(voice_film, only="S01")
    assert not result["complete"] and result["overall_start_ms_mean"] is None


def test_new_selected_take_cannot_reuse_old_engine_documents(voice_film):
    audio, _ = comparison_files(voice_film)
    new = audio.with_name("t02.wav")
    new.write_bytes(b"another selected take")
    register_take(VoiceFilm(voice_film), "S01", new, take="t02")
    assert not align.compare(voice_film, only="S01")["complete"]


@pytest.mark.parametrize("problem", ["changed_script", "missing_source_text", "wrong_source_text"])
def test_forced_alignment_must_bind_the_current_exact_script(voice_film, problem):
    _, docs = comparison_files(voice_film)
    if problem == "changed_script":
        story = voice_film / "script" / "storyboard.json"
        data = jsonio.read(story)
        data["acts"][0]["shots"][0]["vo"] += "."
        jsonio.write_atomic(story, data)
    elif problem == "missing_source_text":
        del docs["elevenlabs"]["source_text"]
    else:
        docs["elevenlabs"]["source_text"] = "another line"
    save_doc(voice_film, "elevenlabs", docs["elevenlabs"])
    result = align.compare(voice_film, only="S01")
    assert not result["complete"] and "source text" in result["shots"][0]["status"]


def test_relative_audio_bindings_and_replay_keep_matching_behavior(voice_film):
    audio, docs = comparison_files(voice_film)
    for engine, doc in docs.items():
        doc["audio"] = str(audio.relative_to(voice_film))
        doc["replay"] = True
        save_doc(voice_film, engine, doc)
    original = voice_film / "voice" / "align" / "S01.whisper.json"
    replay_file = original.with_name("S01.replay.json")
    replay_doc = jsonio.read(original)
    replay_doc["engine"]["name"] = "replay"
    jsonio.write_atomic(replay_file, replay_doc)
    original.unlink()
    result = align.compare(voice_film, only="S01")
    shot = result["shots"][0]
    assert result["complete"] and shot["matched"] == 3 and shot["replay"]
    assert shot["start_ms_mean"] == 0 and "approval" in result["note"]


@pytest.mark.parametrize("bad_time", [float("nan"), float("inf"), -0.1, "not a time", True])
def test_invalid_word_times_never_enter_numeric_statistics(voice_film, bad_time):
    _, docs = comparison_files(voice_film)
    docs["whisper"]["segments"][0]["words"][0]["start"] = bad_time
    save_doc(voice_film, "whisper", docs["whisper"])
    result = align.compare(voice_film, only="S01")
    assert not result["complete"] and result["overall_start_ms_mean"] is None


def test_word_end_before_start_is_not_ready(voice_film):
    _, docs = comparison_files(voice_film)
    docs["elevenlabs"]["segments"][0]["words"][1]["end"] = 0.5
    save_doc(voice_film, "elevenlabs", docs["elevenlabs"])
    assert not align.compare(voice_film, only="S01")["complete"]


@pytest.mark.parametrize("engine", ["whisper", "elevenlabs"])
@pytest.mark.parametrize("words", [[], [{"word": "...", "start": 0.0, "end": 0.5}]])
def test_empty_or_punctuation_only_engine_has_no_comparison(voice_film, engine, words):
    _, docs = comparison_files(voice_film)
    docs[engine]["segments"][0]["words"] = words
    save_doc(voice_film, engine, docs[engine])
    result = align.compare(voice_film, only="S01")
    assert not result["complete"] and result["shots"][0]["matched"] == 0
    assert result["overall_start_ms_mean"] is None


def test_empty_selection_is_not_vacuously_complete(voice_film):
    result = align.compare(voice_film, only=",")
    assert result["shots"] == [] and not result["complete"]


@pytest.mark.parametrize("engine", ["whisper", "elevenlabs"])
def test_saved_engine_name_must_match_its_role(voice_film, engine):
    _, docs = comparison_files(voice_film)
    docs[engine]["engine"]["name"] = "another engine"
    save_doc(voice_film, engine, docs[engine])
    result = align.compare(voice_film, only="S01")
    assert not result["complete"] and "engine binding" in result["shots"][0]["status"]


def test_missing_engine_is_an_incomplete_cli_result(voice_film):
    comparison_files(voice_film)
    (voice_film / "voice" / "align" / "S01.elevenlabs.json").unlink()
    code, out, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01", "--compare", "--json"])
    assert code == 1, err
    assert not json.loads(out)["complete"]


def test_current_comparison_cli_is_successful_and_leaves_inputs_unchanged(voice_film):
    audio, _ = comparison_files(voice_film)
    folder = voice_film / "voice" / "align"
    before = {p: p.read_bytes() for p in folder.iterdir()}
    before[audio] = audio.read_bytes()
    code, out, err = run_cli(["voice", "align", "--film", str(voice_film), "--only", "S01", "--compare", "--json"])
    assert code == 0, err
    assert json.loads(out)["complete"]
    assert all(p.read_bytes() == contents for p, contents in before.items())


def test_wer_alignment_still_returns_substituted_pairs():
    assert align_words(["alpha"], ["beta"]) == (1, [("sub", "alpha", "beta")], [(0, 0)])
