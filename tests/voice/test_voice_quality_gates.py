"""The two measured voice gates: the polish verified by measurement, and inflection (emotion through a full voice).

Synthetic takes (voice_helpers.rich_voice) stand in for real reads; a stand-in Whisper answers with their exact words.
Only PASS counts as passed; a replayed take is UNCHECKED, never passed."""

import pytest

from slate import jsonio
from slate.voice import align, gates, polish
from slate.voice.film import VoiceFilm, get_pin

from voice_helpers import rich_take, whisper_doc

LINES = ("S01", "S02", "S04")


def _film(voice_film, fake_whisper, special=None, sent=None):
    """Real takes for every line (rich synthetic voices), heard word for word by the stand-in Whisper."""
    set_transcript, _ = fake_whisper
    vf = VoiceFilm(voice_film)
    for i, shot in enumerate(LINES):
        kw = dict(seed=20 + i, f0=170 + 8 * i)
        kw.update((special or {}).get(shot, {}))
        wav, words = rich_take(vf, shot, sent=(sent or {}).get(shot), **kw)
        set_transcript(wav, whisper_doc(words))
    return vf


def _gate(film, name):
    return {r.gate: r for r in gates.run(film, [name], write=False)}[name]


# ---------------------------------------------------------------------------------------------------------------
# voice_polish


def test_a_film_that_doesnt_polish_passes_and_says_so(voice_film, fake_whisper):
    _film(voice_film, fake_whisper)
    r = _gate(voice_film, "voice_polish")
    assert r.status == "PASS" and "doesn't use the studio polish" in r.summary


def test_polished_takes_are_verified_by_measurement(voice_film, fake_whisper):
    _film(voice_film, fake_whisper)
    polish.run(voice_film, strength="A")
    r = _gate(voice_film, "voice_polish")
    assert r.status == "PASS", (r.summary, r.evidence)
    assert "verified by measurement" in r.summary and set(r.details["shots"]) == set(LINES)
    checks = r.details["shots"]["S02"]["checks"]
    assert checks["length"] is True and abs(checks["loudness_change_lu"]) <= 0.5


def test_a_line_left_unpolished_or_polished_differently_fails(voice_film, fake_whisper):
    vf = _film(voice_film, fake_whisper)
    polish.run(voice_film, only="S01,S02", strength="A")
    r = _gate(voice_film, "voice_polish")
    assert r.status == "FAIL" and "S04: t01 isn't polished" in r.summary
    polish.run(voice_film, only="S04")
    assert _gate(voice_film, "voice_polish").status == "PASS"
    # a line re-voiced after the polish: its new take isn't polished yet
    rich_take(vf, "S04", take="t02", seed=77)
    r = _gate(voice_film, "voice_polish")
    assert r.status == "FAIL" and "S04: t02 isn't polished" in r.evidence[0]
    polish.run(voice_film, only="S04")
    # a polished file changed on disk afterwards
    out = voice_film / "voice" / "takes" / "S01" / "t01.polished.wav"
    out.write_bytes(out.read_bytes()[:-6] + b"\x00" * 6)
    r = _gate(voice_film, "voice_polish")
    assert r.status == "FAIL" and "changed on disk" in " ".join(r.evidence)


def test_a_take_polished_with_other_settings_than_the_pin_fails(voice_film, fake_whisper):
    vf = _film(voice_film, fake_whisper)
    polish.run(voice_film, strength="A")
    tj = vf.take_json("S02", "t01")
    tj["stages"]["polish"]["chain_sha256"] = "0" * 64  # as if polished with something other than the pin
    vf.save_take_json("S02", "t01", tj)
    r = _gate(voice_film, "voice_polish")
    assert r.status == "FAIL" and "pinned polish" in r.summary


def test_a_voice_that_requires_the_polish_fails_until_it_is_done(voice_film, fake_whisper, brand):
    brand["voice"]["polish"] = {"required": True}
    _film(voice_film, fake_whisper)
    assert _gate(voice_film, "voice_polish").status == "FAIL"
    polish.run(voice_film)
    assert _gate(voice_film, "voice_polish").status == "PASS"


def test_replayed_takes_are_unchecked_not_failed(voice_film):
    from slate.voice import tts

    tts.voice(voice_film)
    polish.run(voice_film)
    r = _gate(voice_film, "voice_polish")
    assert r.status == "UNCHECKED" and "stand-in" in r.summary


# ---------------------------------------------------------------------------------------------------------------
# voice_inflection


def test_full_voice_reads_pass(voice_film, fake_whisper):
    _film(voice_film, fake_whisper)
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "PASS", (r.summary, r.evidence)
    assert set(r.details["untagged_baseline"]) == set(LINES)


def test_a_whisper_like_read_fails_when_this_voice_requires_full_delivery(voice_film, fake_whisper, full_voice_policy):
    _film(voice_film, fake_whisper, special={"S02": {"whisper": True}})
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "FAIL" and "S02: whisper-like" in r.summary


def test_a_quiet_read_is_measured_without_a_universal_delivery_failure(voice_film, fake_whisper):
    _film(voice_film, fake_whisper, special={"S02": {"whisper": True}})
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.details["lines"]["S02"]["vs_other_lines"]["whisper"] is True
    assert not any("S02: whisper-like" in e for e in r.evidence)
    assert "full voice" not in r.summary and "nothing whisper-like" not in r.summary


def test_a_read_that_stops_mid_word_fails(voice_film, fake_whisper):
    _film(voice_film, fake_whisper, special={"S04": {"end_cut": True}})
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "FAIL" and "S04: stops mid-word" in " ".join(r.evidence)


def test_a_statement_that_rises_like_a_question_fails(voice_film, fake_whisper):
    _film(voice_film, fake_whisper, special={"S01": {"last_glide_st": 9.0}})
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "FAIL" and "like a question" in " ".join(r.evidence)


def test_a_tagged_line_needs_a_same_words_measurement_or_an_ear(voice_film, fake_whisper):
    from slate.cli import run_cli

    sent = {"S02": "[confident] Slate hears each word, then fits it to the cut."}
    _film(voice_film, fake_whisper, sent=sent)
    VoiceFilm(voice_film).set_direction("S02", sent["S02"])
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "REVIEW" and "S02: tagged" in r.summary
    code, _, err = run_cli(["voice", "approve", "S02", "--by", "the owner", "--film", str(voice_film)])
    assert code == 0, err
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "PASS", (r.summary, r.evidence)
    assert any("approved by ear" in n for n in r.details["notes"])


def test_unaligned_and_replayed_takes_are_unchecked(voice_film, fake_whisper):
    _film(voice_film, fake_whisper)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "UNCHECKED" and "not aligned" in r.summary


def test_polished_takes_are_measured_on_their_unpolished_source(voice_film, fake_whisper):
    vf = _film(voice_film, fake_whisper)
    polish.run(voice_film, strength="B")
    vf = VoiceFilm(voice_film)
    set_transcript, _ = fake_whisper
    for shot in LINES:  # Whisper hears the polished takes word for word too
        doc = jsonio.read(vf.take_dir(shot) / "t01.json")
        set_transcript(vf.final_audio(shot), whisper_doc([{"word": w, "start": 0.2 + 0.4 * i, "end": 0.5 + 0.4 * i}
                                                          for i, w in enumerate(doc["text"].split())]))
    align.align(voice_film)
    r = _gate(voice_film, "voice_inflection")
    assert r.status == "PASS", (r.summary, r.evidence)  # measured, not unchecked: the polish keeps the timing
    assert r.details["checked"] == 3 and get_pin(voice_film, polish.PIN_KEY)["strength"] == "B"


def test_the_voice_check_includes_both_gates(voice_film, fake_whisper):
    from slate.cli import run_cli

    _film(voice_film, fake_whisper)
    align.align(voice_film)
    code, out, err = run_cli(["voice", "check", "--film", str(voice_film), "--gate", "voice_inflection",
                              "--gate", "voice_polish"])
    assert code == 0, (out, err)
    report = jsonio.read(voice_film / "qa" / "gates.json")["gates"]
    assert report["voice_inflection"]["status"] == "PASS" and report["voice_polish"]["status"] == "PASS"
    assert report["voice_polish"]["details"]["stage"] == "voice"
