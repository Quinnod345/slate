"""The voice gates on fixture audio (short speech-like WAVs made with numpy) and stand-in Whisper transcripts.

Whisper hears every word and every brand word; no leaked tails; each read fits its shot. Only PASS counts, and a
replayed or stale take is UNCHECKED, never passed."""

import json

import numpy as np
import pytest

from slate import gates as gates_mod
from slate import jsonio
from slate.cli import run_cli
from slate.voice import align, gates
from slate.voice.film import VoiceFilm

from voice_helpers import real_take, speech, whisper_doc

SR = 48000
TEXT = {"S01": "Every film starts with one line.", "S02": "Slate hears each word, then fits it to the cut.",
        "S04": "Warm voices, never whispered."}


def _all_real(voice_film, fake_whisper, heard=None, extra=None, sent=None):
    """Real takes for every line; Whisper 'hears' `heard[shot]` (default: exactly the audio's words)."""
    set_transcript, _ = fake_whisper
    vf = VoiceFilm(voice_film)
    for shot, text in TEXT.items():
        wav, words = real_take(vf, shot, text, seed=shot, extra=(extra or {}).get(shot),
                               sent=(sent or {}).get(shot))
        if heard and shot in heard:
            said = heard[shot].split()
            times = [(w["start"], w["end"]) for w in words]
            while len(times) < len(said):
                last = times[-1]
                times.append((last[1] + 0.07, last[1] + 0.35))
            words = [{"word": w, "start": a, "end": b} for w, (a, b) in zip(said, times)]
        set_transcript(wav, whisper_doc(words))
    align.align(voice_film)
    return VoiceFilm(voice_film)


def by_name(results):
    return {r.gate: r for r in results}


def test_real_takes_pass_every_gate(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper)
    res = by_name(gates.run(voice_film))
    for name in ("voice_policy", "voice_direction", "voice_takes", "voice_words", "voice_tails", "voice_fit"):
        assert res[name].status == "PASS", (name, res[name].summary, res[name].evidence)
    assert res["voice_words"].engine["name"] == "openai-whisper" and res["voice_words"].engine["model"] == "turbo"
    assert "Slate" in res["voice_words"].summary
    report = jsonio.read(voice_film / "qa" / "gates.json")
    assert report["gates"]["voice_words"]["status"] == "PASS"
    code, out, _ = run_cli(["voice", "check", "--film", str(voice_film)])
    assert code == 0 and "overall: PASS" in out


def test_a_missing_word_fails(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper, heard={"S01": "Every film starts with line."})
    r = by_name(gates.run(voice_film))["voice_words"]
    assert r.status == "FAIL" and "S01" in r.summary and "del:one->None" in r.summary


def test_a_brand_word_must_be_heard_in_full(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper, heard={"S02": "Slay hears each word, then fits it to the cut."})
    r = by_name(gates.run(voice_film))["voice_words"]
    assert r.status == "FAIL"
    assert any('brand word "Slate"' in e for e in r.evidence)


def test_repeats_and_tags_read_aloud_fail(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper,
              heard={"S02": "Slate hears each word, then fits it to the cut, to the cut.",
                     "S04": "Confident warm voices, never whispered."},
              sent={"S04": "[confident] Warm voices, never whispered."})
    r = by_name(gates.run(voice_film, ["voice_words"]))["voice_words"]
    assert r.status == "FAIL"
    assert any("repeated" in e and "S02" in e for e in r.evidence)
    assert any("read aloud" in e and "confident" in e for e in r.evidence)


def test_a_tail_leaking_in_from_the_next_line_fails(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper, heard={"S02": "Slate hears each word, then fits it to the cut. Warm voices"})
    r = by_name(gates.run(voice_film, ["voice_tails"]))["voice_tails"]
    assert r.status == "FAIL" and any("from the next line" in e and "S02" in e for e in r.evidence)


def test_voice_after_the_last_word_fails_but_a_breath_does_not(voice_film, fake_whisper):
    leak, _ = speech(["leaked", "words"], seed="other")
    rng = np.random.default_rng(3)
    breath = np.concatenate([np.zeros(int(0.5 * SR)), rng.normal(0, 0.01, int(0.3 * SR)), np.zeros(int(0.1 * SR))])
    _all_real(voice_film, fake_whisper, extra={"S01": np.concatenate([np.zeros(int(0.4 * SR)), leak]),
                                               "S04": breath})
    r = by_name(gates.run(voice_film, ["voice_tails"]))["voice_tails"]
    assert r.status == "FAIL"
    assert any(e.startswith("S01:") and "voice after the last word" in e for e in r.evidence)
    assert not any(e.startswith("S04:") for e in r.evidence)


def test_a_read_too_long_for_its_shot_fails_fit(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper, extra={"S04": np.zeros(int(2.5 * SR))})
    r = by_name(gates.run(voice_film, ["voice_fit"]))["voice_fit"]
    assert r.status == "FAIL" and "S04" in r.summary and "too late" in r.summary
    assert r.details["fits"]["S04"]["fits"] is False and r.details["fits"]["S01"]["fits"] is True


def test_replayed_takes_are_unchecked_never_passed(voice_film):
    for args in (["tts"], ["align"]):
        code, _, err = run_cli(["voice", *args, "--film", str(voice_film)])
        assert code == 0, err
    res = by_name(gates.run(voice_film))
    for name in ("voice_takes", "voice_words", "voice_tails", "voice_fit"):
        assert res[name].status == "UNCHECKED", name
        assert "replayed" in res[name].summary
    assert gates_mod.overall(res.values()) == "UNCHECKED"
    code, _, _ = run_cli(["voice", "check", "--film", str(voice_film)])
    assert code == 1


def test_stale_and_forced_alignments_are_unchecked(voice_film, fake_whisper):
    vf = _all_real(voice_film, fake_whisper)
    wav = vf.final_audio("S01")
    x, _ = speech(TEXT["S01"].split(), seed="changed")
    from slate.voice import audio
    audio.write_wav(wav, x, SR)  # the take changed after it was aligned
    r = by_name(gates.run(voice_film, ["voice_words"]))["voice_words"]
    assert r.status == "UNCHECKED" and any("older than the take" in e for e in r.evidence)
    (voice_film / "voice" / "align" / "S01.whisper.json").unlink()
    doc = jsonio.read(voice_film / "voice" / "align" / "S01.json")
    doc["engine"] = {"name": "elevenlabs forced alignment", "model": "elevenlabs_forced_alignment"}
    jsonio.write_atomic(voice_film / "voice" / "align" / "S01.json", doc)
    r = by_name(gates.run(voice_film, ["voice_words"]))["voice_words"]
    assert r.status == "UNCHECKED" and any("forced alignment" in e for e in r.evidence)


def test_takes_gate_catches_changed_lines_and_direction(voice_film, fake_whisper):
    _all_real(voice_film, fake_whisper)
    vf = VoiceFilm(voice_film)
    vf.set_direction("S04", "[bright] Warm voices, never whispered.")
    r = by_name(gates.run(voice_film, ["voice_takes"]))["voice_takes"]
    assert r.status == "FAIL" and "direction changed" in r.summary
    sb = jsonio.read(voice_film / "script" / "storyboard.json")
    sb["acts"][0]["shots"][0]["vo"] = "Every film starts with a different line."
    jsonio.write_atomic(voice_film / "script" / "storyboard.json", sb)
    r = by_name(gates.run(voice_film, ["voice_takes"]))["voice_takes"]
    assert any("line changed" in e for e in r.evidence)


def test_direction_and_policy_gates(voice_film, brand, full_voice_policy):
    path = voice_film / "voice" / "direction.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lines": {"S01": "[whispering] Every film starts with one line.",
                                          "S99": "[bright] Nobody says this."}}))
    r = by_name(gates.run(voice_film, ["voice_direction"]))["voice_direction"]
    assert r.status == "FAIL"
    assert any("banned tag" in e for e in r.evidence) and any("S99" in e for e in r.evidence)
    brand["voice"].update({"source": "clone", "clone_of": "owner", "clone_kind": "professional",
                           "verified_by_owner": False})
    r = by_name(gates.run(voice_film, ["voice_policy"]))["voice_policy"]
    assert r.status == "FAIL" and "verified" in r.summary


def test_unknown_gate_is_a_usage_error(voice_film):
    code, _, err = run_cli(["voice", "check", "--film", str(voice_film), "--gate", "voice_nope"])
    assert code == 2


def test_gates_are_offered_to_qa():
    assert set(gates.GATES) == {"voice_policy", "voice_direction", "voice_takes", "voice_words", "voice_tails",
                                "voice_fit", "voice_inflection", "voice_polish"}
    assert all(callable(fn) for fn in gates.GATES.values())


def test_voice_gates_name_their_stage(voice_film):
    results = gates.run(voice_film, ["voice_policy", "voice_direction"], write=False)
    assert {r.details["stage"] for r in results} == {"voice"}
