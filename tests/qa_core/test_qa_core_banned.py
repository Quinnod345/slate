"""Banned words: every form of an entry with its substitute named, what the voice really said, and a brand's
self-commentary rule.

- L-qa-D035: an entry carries its inflections (`forms`) and an approved substitute (`instead`); every form is matched,
  and a failure names the substitute.
- L-qa-D012: the list covers the voice too: what Whisper heard in every take in use (timing/vo_words.json), not only
  what the script says it should be.
- L-brand-010: a brand that sets truth.self_commentary false has "Illustration" and "Example" captions banned on
  screen (its voice may still say the words).
"""

from __future__ import annotations

import json

from slate.qa import ocr, textmatch
from slate.qa.checks import text

from qa_core_helpers import FIXTURE_BRAND, ScriptedOCR, moving_square, needs_ffmpeg, paint_codes, write_brand, \
    write_json

VERIFIED = {"word": "verified", "forms": ["verifies", "verifying", "verification"], "instead": "trusted"}


def _film(film_builder, *, vo="Every number has a source.", on_screen="Scores you can check", heard=None):
    fb = film_builder(shots=[(1, 30)])
    fb.script({"title": "A fixture", "acts": [{"name": "One", "shots": [
        {"id": "S01", "vo": vo, "on_screen": on_screen, "picture": "a calm chart"}]}]})
    fb.captions([{"text": vo, "lines": [vo], "start": 0.1, "end": 0.9}])
    if heard is not None:
        write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {
            "take": "voice/S01_t1.wav", "line": vo, "heard": heard,
            "words": [{"w": w, "start": 0.1 * i, "end": 0.1 * i + 0.08} for i, w in enumerate(heard.split())]}}})
    return fb


# ---------------------------------------------------------------------------------------------------------- the match
def test_every_form_of_an_entry_matches_and_the_failure_names_the_substitute():
    entries = [VERIFIED, "guaranteed"]
    pattern, forms = textmatch.banned_pattern(entries), textmatch.banned_forms(entries)
    found = textmatch.find_banned("Verification in minutes; nothing guaranteed; a verifiable claim", pattern)
    assert found == ["Verification", "guaranteed"]  # "verifiable" isn't one of the forms
    assert textmatch.name_hit("Verification", forms) == "'Verification' (a form of 'verified') (say 'trusted' instead)"
    assert textmatch.name_hit("guaranteed", forms) == "'guaranteed'"
    assert textmatch.banned_entry({"text": "Free", "forms": ["free", "freely"]}) == \
        {"word": "Free", "forms": ["freely"], "instead": None}


def test_the_brand_pack_hands_over_each_entry_whole(slate_home):
    from slate.brand import pack

    raw = {"truth": {"banned_words": [VERIFIED, "guaranteed", {"word": "Guaranteed"}]}}
    assert pack.banned_entries(raw) == [{"word": "verified", "forms": ["verifies", "verifying", "verification"],
                                         "instead": "trusted"},
                                        {"word": "guaranteed", "forms": [], "instead": None}]
    assert pack.banned_words(raw) == ["verified", "guaranteed", "Guaranteed"]  # the plain list stays as it was
    assert pack.banned_entries({"truth": {}}) is None and pack.self_commentary({"truth": {}}) is True
    assert pack.self_commentary({"truth": {"self_commentary": False}}) is False


def test_a_form_in_the_script_fails_with_its_substitute(film_builder, brand_store):
    fb = _film(film_builder, on_screen="Verification in minutes")
    write_brand(brand_store, banned=[VERIFIED])
    r = text.banned_words(fb.path)
    assert r.status == "FAIL"
    assert any("'Verification' (a form of 'verified') (say 'trusted' instead)" in h for h in r.details["hits"])
    assert "4 forms" in r.details["list"]


# ---------------------------------------------------------------------------------------------------------- the voice
def test_what_the_voice_really_said_is_checked(film_builder, brand_store):
    """The script's line is clean, but the take in use says a banned word (an ad-lib Whisper heard)."""
    fb = _film(film_builder, vo="Every number has a source.", heard="Every number is guaranteed to have a source.")
    write_brand(brand_store, banned=["guaranteed"])
    r = text.banned_words(fb.path)
    assert r.status == "FAIL", r.summary
    speech = [h for h in r.details["hits"] if h.startswith("speech")]
    assert speech and "S01 (take voice/S01_t1.wav)" in speech[0] and "what Whisper heard" in speech[0]
    assert not [h for h in r.details["hits"] if h.startswith(("script", "captions"))]


def test_a_clean_take_passes_and_untranscribed_takes_are_unchecked(film_builder, brand_store):
    fb = _film(film_builder, heard="Every number has a source.")
    write_brand(brand_store, banned=["guaranteed"])
    r = text.banned_words(fb.path)
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    assert checks["speech"] == "PASS" and not r.details["hits"]
    fb2 = _film(film_builder)  # voiced lines, no transcripts yet
    r2 = text.banned_words(fb2.path)
    sub = next(c for c in r2.details["checks"] if c["check"] == "speech")
    assert sub["status"] == "UNCHECKED" and "transcribed" in sub["summary"]


# ---------------------------------------------------------------------------------------------------------- self-commentary
def _brand_without_self_commentary(store, *, allowed: bool):
    path = write_brand(store, banned=[])
    pack = json.loads(path.read_text())
    if not allowed:
        pack["truth"]["self_commentary"] = False
    write_json(path, pack)


def test_a_brand_that_forbids_self_commentary_bans_illustration_captions_on_screen(film_builder, brand_store):
    fb = _film(film_builder, vo="Here's an example of a good week.", on_screen="Illustration",
               heard="Here's an example of a good week.")
    _brand_without_self_commentary(brand_store, allowed=False)
    r = text.banned_words(fb.path)
    assert r.status == "FAIL" and r.details["self_commentary"] is False
    assert [h for h in r.details["hits"] if ".on_screen" in h and "'Illustration'" in h]
    assert not [h for h in r.details["hits"] if ".vo" in h or h.startswith("captions")]  # the voice may say it
    _brand_without_self_commentary(brand_store, allowed=True)
    r = text.banned_words(fb.path)
    assert not r.details["hits"]  # allowed: no hits (the picture is UNCHECKED here: the default run has no OCR)
    assert {c["check"]: c["status"] for c in r.details["checks"]}["script"] == "PASS"


@needs_ffmpeg
def test_self_commentary_on_the_picture_is_read_too(film_builder, brand_store):
    fb = _film(film_builder)
    fb.render(1, paint_codes(moving_square(30, fb.size), [(0, 30, 1)]))
    _brand_without_self_commentary(brand_store, allowed=False)
    with ocr.use_backend(ScriptedOCR({1: ["EXAMPLES ONLY"]})):
        r = text.banned_words(fb.path)
    assert r.status == "FAIL"
    assert any(h.startswith("picture") and "'EXAMPLES' (a form of 'Example')" in h for h in r.details["hits"])
    assert FIXTURE_BRAND in r.details["list"]
