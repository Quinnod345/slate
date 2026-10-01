"""Measured inflection: pitch, breath, sibilance, endings and the Whisper checks on signals whose answers are known,
the A/B comparison against a reference, the spectrogram sheet, and the command. Synthetic audio only."""

import json

import numpy as np
import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.voice import analyze, polish
from slate.voice.film import VoiceFilm

from voice_helpers import rich_take, whisper_doc, write_rich

TEXT = "Slate hears each word, then fits it to the cut."
QUESTION = "Does Slate hear each word? It fits it to the cut."


@pytest.fixture
def takes(tmp_path):
    out = {}
    for name, kw in {"clean": {}, "breathy": {"breath": 0.6}, "whisper": {"whisper": True},
                     "rise": {"last_glide_st": 6.0}, "fall": {"last_glide_st": -5.0}, "cut": {"end_cut": True},
                     "low": {"f0": 140.0}, "plain": {"sibilance": 0.0}}.items():
        p = tmp_path / f"{name}.wav"
        out[name] = (p, write_rich(p, TEXT, seed=1, **kw))
    return out


def m_of(takes, name, **kw):
    path, words = takes[name]
    return analyze.measure_take(path, text=TEXT, words=words, **kw)


def test_pitch_is_measured_where_it_is(takes):
    clean, low = m_of(takes, "clean"), m_of(takes, "low")
    assert clean["line"]["f0_median_hz"] == pytest.approx(180, rel=0.04)
    assert low["line"]["f0_median_hz"] == pytest.approx(140, rel=0.04)
    assert clean["line"]["f0_range_st"] < 1.5
    hz = [v for v in clean["f0_contour"]["hz"] if v]
    assert clean["f0_contour"]["hop_s"] == 0.02 and hz and min(hz) > 150 and max(hz) < 215


def test_breath_and_whisper_are_measured(takes):
    clean, breathy, whisper = m_of(takes, "clean"), m_of(takes, "breathy"), m_of(takes, "whisper")
    assert breathy["line"]["cpps_db"] < clean["line"]["cpps_db"] - 3
    assert breathy["line"]["hnr_db"] < clean["line"]["hnr_db"] - 3
    assert whisper["line"]["voiced_frac"] < 0.15 < clean["line"]["voiced_frac"]
    vs = analyze.ab(clean, breathy)["breath"]
    assert vs["breathier"] and not vs["whisper"]
    vs = analyze.ab(clean, whisper)["breath"]
    assert vs["whisper"] and vs["breathier"]
    assert not analyze.ab(clean, m_of(takes, "clean"))["breath"]["breathier"]


def test_sibilance_and_tilt_are_measured(takes):
    clean, plain = m_of(takes, "clean"), m_of(takes, "plain")
    assert clean["bands"]["sibilance_vs_full_db"] > plain["bands"]["sibilance_vs_full_db"] + 15
    assert clean["bands"]["sibilance_peak_db"] > plain["bands"]["sibilance_peak_db"] + 15
    assert clean["line"]["tilt_db"] is not None and clean["line"]["centroid_hz"] > 0


def test_endings_rise_fall_and_cut(takes):
    rise, fall, clean, cut = m_of(takes, "rise"), m_of(takes, "fall"), m_of(takes, "clean"), m_of(takes, "cut")
    assert rise["last_word_rise_st"] > 2.5 and fall["last_word_rise_st"] < -2.5 and abs(clean["last_word_rise_st"]) < 1
    assert rise["statement_rise_max_st"] > 2.0
    c = fall["last_word_contour"]
    assert c["fall_peak_to_end_st"] > 2.0 and c["end_hz"] < c["start_hz"]
    assert clean["complete"]["ok"] and clean["complete"]["tail_below_speech_db"] >= 35
    assert not cut["complete"]["ok"] and cut["complete"]["tail_below_speech_db"] < 35


def test_question_words_are_measured(tmp_path):
    p = tmp_path / "q.wav"
    words = write_rich(p, QUESTION, seed=2)
    m = analyze.measure_take(p, text=QUESTION, words=words)
    assert list(m["question_words"]) == ["4"]  # "word?" is the fifth script word
    assert m["question_words"]["4"]["word"] == "word"


def test_the_whisper_checks(takes):
    path, words = takes["clean"]
    m = analyze.measure_take(path, text=TEXT, words=words, lexicon=["Slate"])
    assert m["whisper"]["wer"] == 0 and m["whisper"]["lexicon"] == {"Slate": True}
    missing = [w for w in words if w["word"] != "each"]
    m = analyze.measure_take(path, text=TEXT, words=missing)
    assert m["whisper"]["wer"] > 0 and any("each" in e for e in m["whisper"]["word_errors"])
    spoken = words + [{"word": "confident", "start": 3.9, "end": 4.1}]
    m = analyze.measure_take(path, text=TEXT, words=spoken, tagged="[confident] " + TEXT)
    assert m["whisper"]["spoken_tag_words"] == ["confident"]
    m = analyze.measure_take(path, text=TEXT, words=None)
    assert m["whisper"] is None and "never guessed" in m["note"]


def test_the_tag_scope_is_measured_from_its_first_word(takes):
    path, words = takes["clean"]
    tagged = "Slate hears each word, [warmly] then fits it to the cut."
    m = analyze.measure_take(path, text=TEXT, words=words, tagged=tagged)
    assert m["scope_word_index"] == 4 and m["scope_from_s"] == pytest.approx(words[4]["start"] - 0.02, abs=0.01)
    assert m["scope"]["voiced_frames"] < m["line"]["voiced_frames"]


def test_files_compared_on_a_sheet(takes, tmp_path):
    (a, aw), (b, bw) = takes["clean"], takes["whisper"]
    doc = tmp_path / "words.json"
    doc.write_text(json.dumps(whisper_doc(bw)))
    res = analyze.run([str(b)], ab_ref=str(a), sheet=True, text=TEXT, words_file=str(doc), out=tmp_path / "out")
    data = jsonio.read(res["file"])
    assert not jsonio.validate(data, "voice_analysis")
    assert data["takes"][0]["reference"] and data["takes"][1]["vs_reference"]["breath"]["whisper"]
    png = tmp_path / "out" / "sheet.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image

    w, h = Image.open(png).size
    assert w > 900 and h > 900


def test_shots_in_a_film_resolve_to_the_take_before_the_polish(voice_film, fake_whisper):
    set_transcript, _ = fake_whisper
    vf = VoiceFilm(voice_film)
    for i, shot in enumerate(("S01", "S02", "S04")):
        wav, words = rich_take(vf, shot, seed=40 + i)
        set_transcript(wav, whisper_doc(words))
    from slate.voice import align

    align.align(voice_film)
    polish.run(voice_film, only="S01")
    t = analyze.resolve_target("S01", VoiceFilm(voice_film))
    assert t["path"].name == "t01.wav"  # the take before the polish, which voice measurements read
    assert t["words"] and t["words_source"] == "openai-whisper"  # aligned before the polish: the same audio
    t = analyze.resolve_target("S02", VoiceFilm(voice_film))
    assert t["words"] and t["words_source"] == "openai-whisper" and t["text"] == vf.line("S02").text
    code, out, err = run_cli(["voice", "analyze", "S02", "S04", "--ab", "S01", "--sheet", "--film", str(voice_film),
                              "--name", "first", "--json"])
    assert code == 0, err
    res = json.loads(out)
    assert res["reference"].startswith("S01") and len(res["takes"]) == 3
    assert (voice_film / "voice" / "analysis" / "first" / "sheet.png").is_file()
    assert res["takes"][1]["measures"]["whisper"]["wer"] == 0


def test_a_missing_take_is_a_usage_error(tmp_path):
    code, _, err = run_cli(["voice", "analyze", str(tmp_path / "nope.wav")])
    assert code == 2 and "isn't a file" in err
