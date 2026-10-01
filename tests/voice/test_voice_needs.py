"""The cross-module requests addressed to voice (docs/build_reports/NEEDS.md): captions at the film's own aspect,
brand onboarding billed against its own budget, one Whisper runner every module can share, Forced Alignment priced at
the registry check's 330 credits a minute; plus a guard that the new voice code names no company material."""

import sys
from pathlib import Path

import pytest

from slate import jsonio, ledger
from slate.voice import align, captions, cast, design, lang

REPO = Path(__file__).resolve().parents[2]


def test_a_vertical_cut_is_captioned_at_the_vertical_line_length():
    table = lang.load("en")
    wide = captions.settings_for({"format": "ad", "aspect": "16:9", "width": 1920, "height": 1080}, table)
    tall = captions.settings_for({"format": "ad", "aspect": "9:16", "width": 1080, "height": 1920}, table)
    assert wide["max_chars"] == 42 and tall["max_chars"] == 32
    # an aspect the format doesn't list takes the shared table's length for its shape
    odd = captions.settings_for({"format": "ad", "aspect": "3:4", "width": 1080, "height": 1440}, table)
    assert odd["max_chars"] == 32
    # film.json's own caption settings still win, and an unknown format leaves the defaults
    own = captions.settings_for({"format": "ad", "aspect": "9:16", "captions": {"max_chars": 30}}, table)
    assert own["max_chars"] == 30
    assert captions.settings_for({"format": "no-such-format"}, table)["max_chars"] == captions.DEFAULTS["max_chars"]


def test_a_format_file_the_film_came_from_is_the_one_read(tmp_path):
    from slate import formats

    fmt = formats.raw("ad")
    fmt["captions"]["max_chars"] = 38
    src = tmp_path / "custom.json"
    jsonio.write_atomic(src, fmt)
    got = captions.format_captions({"format": "ad", "format_source": str(src), "aspect": "16:9"})
    assert got["max_chars"] == 38


def test_brand_casting_and_design_bill_the_brands_onboarding_budget(slate_home, brand, tmp_path, monkeypatch):
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fx"))
    cast.run(None, brand="voicetest", text="Every film starts with one line.", name="onboard-cast", make_reel=False)
    rows = ledger.rows(op="tts")
    assert rows and {r.get("film") for r in rows} == {"onboarding-voicetest"}
    spec = design.quick_spec("A warm, clear narrator in her forties, bright and steady.",
                             "Every film starts with one line, and the line has to land. This voice reads it warmly "
                             "and plainly, then lets it rest.", [11], [5.0])
    design.run(None, brand="voicetest", spec=spec, name="onboard-design")
    assert {r.get("film") for r in ledger.rows(op="design")} == {"onboarding-voicetest"}
    assert cast.onboarding_billing(None) is None


def test_one_whisper_runner_honours_slate_whisper(fake_whisper, tmp_path):
    set_transcript, calls = fake_whisper
    wav = tmp_path / "line.wav"
    wav.write_bytes(b"RIFF")  # the stand-in never reads it
    set_transcript(wav, {"text": " Every film", "segments": [{"words": [{"word": " Every", "start": 0.1, "end": 0.4}]}]})
    import os

    assert align.whisper_executable() == os.environ["SLATE_WHISPER"]
    doc = align.transcribe(wav, model="turbo", language="en")
    assert doc["segments"][0]["words"][0]["word"].strip() == "Every"
    assert calls() and "--model" in calls()[-1]


def test_whisper_named_but_missing_is_a_clear_prerequisite(monkeypatch, tmp_path):
    from slate.errors import MissingPrerequisite

    monkeypatch.setenv("SLATE_WHISPER", str(tmp_path / "no-whisper-here"))
    assert align.whisper_executable() is None
    with pytest.raises(MissingPrerequisite) as exc:
        align.transcribe(tmp_path / "x.wav")
    assert "SLATE_WHISPER" in exc.value.message


def test_forced_alignment_is_priced_at_the_registry_checks_330_a_minute(monkeypatch):
    from slate import config
    from slate.vendors import elevenlabs

    real = config.model_info

    def old_registry(model_id):
        info = dict(real(model_id))
        if model_id == "elevenlabs_forced_alignment":
            info.update({"price": {"unit": "credits", "per_minute": 1000}, "verified_on": "2026-09-30"})
        return info

    monkeypatch.setattr(config, "model_info", old_registry)
    check = elevenlabs.checked_price("elevenlabs_forced_alignment")
    assert check["per_minute"] == 330 and check["registry_per_minute"] == 1000

    def agreeing(model_id):
        info = dict(real(model_id))
        if model_id == "elevenlabs_forced_alignment":
            info.update({"price": {"unit": "credits", "per_minute": 330}})
        return info

    monkeypatch.setattr(config, "model_info", agreeing)
    assert elevenlabs.checked_price("elevenlabs_forced_alignment") is None

    def newer(model_id):
        info = dict(real(model_id))
        if model_id == "elevenlabs_forced_alignment":
            info.update({"price": {"unit": "credits", "per_minute": 500}, "verified_on": "2026-12-01"})
        return info

    monkeypatch.setattr(config, "model_info", newer)
    assert elevenlabs.checked_price("elevenlabs_forced_alignment") is None  # a later registry check wins


def test_a_minute_of_forced_alignment_estimates_330_credits(voice_film, monkeypatch):
    import numpy as np

    from slate import config
    from slate.vendors import elevenlabs
    from slate.voice import audio

    real = config.model_info
    monkeypatch.setattr(config, "model_info", lambda m: {**real(m), "price": {"unit": "credits", "per_minute": 1000}}
                        if m == "elevenlabs_forced_alignment" else real(m))
    wav = voice_film / "minute.wav"
    audio.write_wav(wav, np.zeros(60 * 48000), 48000, 16)
    elevenlabs.Client(film=voice_film).align(audio=wav, text="one minute of words")
    row = ledger.rows(op="align")[-1]
    assert row["estimate"]["amount"] == 330 and "330 credits a minute" in row["estimate"]["basis"]


def test_the_new_voice_code_names_no_company_material(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "tests" / "skills"))
    import skills_lint as L

    files = [REPO / "src" / "slate" / "voice" / n for n in ("dsp.py", "polish.py", "analyze.py", "sheet.py",
                                                             "tagloop.py")]
    files += sorted((REPO / "src" / "slate" / "schemas").glob("voice_*.schema.json"))
    hits = []
    for f in files:
        # neutral colours in the sheet's code are fine; names, ids and personal paths aren't
        hits += [(f.name, n, w) for n, w in L.brand_hits(f.read_text()) if "hex colour" not in w]
    assert hits == []
    sys.modules.pop("skills_lint", None)


def test_slate_never_imports_the_gpl_plugin_host():
    """The films ran their EQ and compressor in a GPL-3.0 plug-in host; Slate's chain is numpy and scipy only."""
    import re

    offenders = [str(f.relative_to(REPO)) for f in (REPO / "src" / "slate").rglob("*.py")
                 if re.search(r"^\s*(?:import|from)\s+pedalboard\b", f.read_text(encoding="utf-8"), re.M)]
    assert offenders == []
