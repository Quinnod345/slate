"""Casting shootouts, Voice Design and tag A/B takes: measured, ranked, level-matched, and policed before any spend."""

import json

import numpy as np
import pytest

from slate import jsonio, ledger
from slate.cli import run_cli
from slate.errors import Refused
from slate.vendors import elevenlabs
from slate.voice import ab, audio, cast, design, reel
from slate.voice.film import VoiceFilm

from voice_helpers import whisper_doc


def test_cast_shootout_ranks_voices_and_builds_a_reel(voice_film):
    code, out, err = run_cli(["voice", "cast", "--film", str(voice_film), "--voices", "voice-a,voice-b,voice-c",
                              "--shots", "S01,S02", "--name", "first", "--json"])
    assert code == 0, err
    res = json.loads(out)
    assert [r["voice_id"] for r in res["ranking"]] and len(res["ranking"]) == 3 and res["takes"] == 6
    folder = voice_film / "voice" / "cast" / "first"
    shoot = jsonio.read(folder / "shootout.json")
    assert {t["line"] for t in shoot["takes"]} == {"S01", "S02"}
    assert all(t["metrics"]["wer"] == 0 for t in shoot["takes"])  # replay words line up with the script
    assert all(t["metrics"]["lexicon"].get("Slate", True) for t in shoot["takes"])
    listing = jsonio.read(folder / "reel.json")
    assert len(listing["items"]) == 6 and all(abs(i["lufs_after"] - reel.TARGET_LUFS) < 0.2 for i in listing["items"])
    assert (folder / "reel.wav").is_file() and (folder / "ranking.json").is_file()
    assert len(ledger.rows(op="tts")) == 6


def test_cast_candidates_from_the_profile_and_for_a_brand(slate_home, brand, tmp_path, monkeypatch):
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fx"))
    res = cast.run(None, brand="voicetest", text="Every film starts with one line.", name="brand-cast")
    assert {r["voice_id"] for r in res["ranking"]} == {"demo-narrator-01", "demo-narrator-02"}
    assert res["folder"].startswith(str(slate_home["data"]))


def test_cast_refuses_a_clone_candidate_before_any_spend(voice_film, brand):
    brand["voice"]["candidates"] = [{"voice_id": "colleague", "source": "clone", "clone_of": "a colleague",
                                     "clone_kind": "professional", "verified_by_owner": True}]
    with pytest.raises(Refused) as exc:
        cast.run(voice_film, voices=None)
    assert "only clone" in exc.value.message
    assert ledger.rows() == []


def test_cast_checks_elevenlabs_own_record_live(voice_film, monkeypatch):
    monkeypatch.setattr(elevenlabs.Client, "replay", property(lambda self: False))
    monkeypatch.setattr(elevenlabs.Client, "voice_info",
                        lambda self, vid: {"voice_id": vid, "name": "A Famous Voice", "category": "famous"})
    with pytest.raises(Refused) as exc:
        cast.run(voice_film, voices=["famous-one"])
    assert "public figure" in exc.value.message
    assert ledger.rows() == []


PREVIEW = ("Every film starts with one line, and the line has to land. This voice reads it warmly and plainly, "
           "then lets it rest.")


def test_design_previews_are_ranked_saved_and_checked_on_the_production_model(voice_film):
    code, out, err = run_cli(["voice", "design", "--film", str(voice_film), "--name", "d1", "--description",
                              "A warm, clear narrator in her forties, bright and steady.", "--text", PREVIEW,
                              "--seeds", "11,22", "--guidance", "5", "--pitch", "150,260", "--json"])
    assert code == 0, err
    res = json.loads(out)
    folder = voice_film / "voice" / "design" / "d1"
    man = jsonio.read(folder / "previews.json")
    assert res["previews"] == 6 and len(man["previews"]) == 6
    assert all(p["model_id"] == "eleven_ttv_v3" for p in man["previews"])
    assert all(r["estimate"]["details"]["model"] == "eleven_ttv_v3" for r in ledger.rows(op="design"))
    ranking = jsonio.read(folder / "ranking.json")
    assert "main" in ranking["best_by_prompt_key"] and ranking["ranked"][0]["parts"]["pitch_fit"] is not None
    pid = man["previews"][0]["preview_id"]
    code, out, err = run_cli(["voice", "design", "--film", str(voice_film), "--folder", str(folder), "--save", pid,
                              "--voice-name", "Test narrator", "--json"])
    assert code == 0, err
    saved = json.loads(out)
    assert saved["voice_id"].startswith("replay_voice_") and "source" in saved["next"]
    code, out, err = run_cli(["voice", "design", "--film", str(voice_film), "--folder", str(folder), "--check",
                              "--json"])
    assert code == 0, err
    check = jsonio.read(folder / "tts_check" / "tts_check.json")
    assert check["models"] == ["eleven_v4"] and check["renders"][0]["metrics"]["wer"] == 0


def test_design_spec_is_checked_before_any_call(voice_film):
    with pytest.raises(Exception):
        design.run(voice_film, spec=design.quick_spec("too short", PREVIEW, [11], [5.0]))
    with pytest.raises(Exception):
        design.run(voice_film, spec=design.quick_spec("A warm, clear narrator in her forties.", "short", [11], [5.0]))
    bad = design.quick_spec("A warm, clear narrator in her forties.", PREVIEW, [11], [5.0])
    bad["passes"][0]["prompts"] = ["missing"]
    with pytest.raises(Exception):
        design.run(voice_film, spec=bad)
    assert ledger.rows() == []


def test_tag_ab_refuses_banned_variants_before_anything_is_sent(voice_film, full_voice_policy):
    code, out, err = run_cli(["voice", "tags", "ab", "S02", "--film", str(voice_film),
                              "--variant", "[confident] Slate hears each word, then fits it to the cut.",
                              "--variant", "[softly] Slate hears each word, then fits it to the cut."])
    assert code == 1 and "banned tag [softly]" in err
    assert ledger.rows() == []
    code, out, err = run_cli(["voice", "tags", "ab", "S02", "--film", str(voice_film),
                              "--variant", "[confident] Slate hears every word, then fits it to the cut."])
    assert code == 1 and "doesn't strip back" in err


def test_tag_ab_in_replay_makes_level_matched_clips(voice_film):
    code, out, err = run_cli(["voice", "tags", "ab", "S02", "--film", str(voice_film), "--raw", "--json",
                              "--variant", "[confident] Slate hears each word, then fits it to the cut.",
                              "--variant", "[bright] Slate hears each word, then fits it to the cut."])
    assert code == 0, err
    res = json.loads(out)
    assert res["summary"] == {"B": None, "C": None}  # a stand-in voice proves nothing: unchecked, not passed
    folder = voice_film / "voice" / "ab" / "S02"
    for name in ("A.wav", "B.wav", "C.wav", "AB.wav", "AC.wav", "reel.wav", "ab.json"):
        assert (folder / name).is_file(), name
    for name in ("A.wav", "B.wav"):
        x, sr = audio.read_wav(folder / name)
        assert audio.loudness(x, sr)["lufs"] == pytest.approx(-20.0, abs=0.2)
    data = jsonio.read(folder / "ab.json")
    assert data["seed"] == 7 and data["voice_id"] == "demo-narrator-01"
    rows = ledger.rows(op="tts")
    assert len(rows) == 3


def test_tag_ab_rejects_a_breathier_take_by_measurement(voice_film, fake_whisper, monkeypatch, full_voice_policy):
    """A full-voice tag that still comes out whispery is measured out, however good the tag looks."""
    set_transcript, _ = fake_whisper
    rng = np.random.default_rng(5)
    line = "Slate hears each word, then fits it to the cut."

    def fake_tts(self, *, voice_id, text, out=None, **kw):
        from slate.vendors.elevenlabs import fake_speech

        x, words = fake_speech(line, "same-voice")
        if text.startswith("[bright]"):
            x = rng.normal(0, 0.05, len(x)) * (np.abs(x) > 1e-4)  # the same rhythm, whispered
        audio.write_wav(out, x, 48000)
        set_transcript(out.with_name(out.name.replace(".raw.", ".clean.")), whisper_doc(words))  # Whisper hears the clean stage
        return {"file": str(out), "format": "pcm_48000", "request_id": "req_fake", "replay": False, "words": None,
                "seconds": len(x) / 48000}

    monkeypatch.setattr(elevenlabs.Client, "tts", fake_tts)
    monkeypatch.setattr(elevenlabs.Client, "choose_format", lambda self, *a, **k: {"format": "pcm_48000"})
    res = ab.run(voice_film, "S02", [f"[bright] {line}", f"[confident] {line}"], raw=True)
    data = jsonio.read(voice_film / "voice" / "ab" / "S02" / "ab.json")
    assert data["takes"]["B"]["gates"]["full_voice"] is False and res["summary"]["B"] is False
    assert data["takes"]["C"]["gates"]["full_voice"] is True
    assert data["takes"]["C"]["gates"]["words"] is True
