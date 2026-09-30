"""What a film declares on purpose, honoured by the gates and never as a quiet pass: read-time exemptions (a surface
not meant to be read, a voice-matched definition), the caption rules both caption gates share, the stage each gate
checks, and every OCR gate staying UNCHECKED when Apple Vision isn't there."""

from __future__ import annotations

import json
import sys

import pytest

from slate import gates, qa
from slate.qa import ocr
from slate.qa.checks import text
from slate.qa.film import CAPTION_CANDIDATES, FilmContext

from qa_core_helpers import ScriptedOCR, moving_square, needs_ffmpeg, paint_codes, write_brand, write_json


def _sub(result, name):
    return next(c for c in result.details["checks"] if c["check"] == name)


@pytest.fixture
def no_vision(monkeypatch):
    """Slate's environment without ocrmac (as on this Mac until the OCR extra is installed)."""
    monkeypatch.setitem(sys.modules, "ocrmac", None)
    monkeypatch.setitem(sys.modules, "ocrmac.ocrmac", None)
    ocr._STATE.clear()
    yield
    ocr._STATE.clear()


# ---------------------------------------------------------------------------------------------- read-time exemptions
def _board_film(film_builder, on_screen: str, reading: dict | None, *, frames_shown: int, script_extra=None):
    """One 4-second shot whose on-screen statement is on screen (and read by OCR) for `frames_shown` frames."""
    fb = film_builder(shots=[(1, 120)])
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": on_screen}]}]})
    shot = {"id": "S01", "on_screen": on_screen, **({"reading": reading} if reading is not None else {}),
            **(script_extra or {})}
    fb.script({"acts": [{"name": "One", "shots": [shot]}]})
    fb.render(1, paint_codes(moving_square(120, fb.size), [(0, frames_shown, 1)]))
    return fb


@needs_ffmpeg
def test_a_voice_matched_definition_needs_its_floor_not_its_word_count(film_builder):
    line = "Durability is how long a team holds its course"  # 9 words: 0.4 + 2.7 = 3.1 s normally
    plain = _board_film(film_builder, line, None, frames_shown=54)  # 1.8 s on screen
    with ocr.use_backend(ScriptedOCR({1: [line]})):
        r = text.readtime(plain.path)
    assert r.status == "FAIL" and "needs 3.10 s" in r.details["short"][0]
    matched = _board_film(film_builder, line, {"voice_matched": True, "reason": "the VO reads it in full as it lands"},
                          frames_shown=54)
    with ocr.use_backend(ScriptedOCR({1: [line]})):
        r = text.readtime(matched.path)
    assert r.status == "PASS", r.details
    assert "needs 1.50 s, voice-matched" in r.details["rows"][0] and r.details["voice_matched"] == ["S01"]
    short = _board_film(film_builder, line, {"voice_matched": True, "reason": "read along"}, frames_shown=30)
    with ocr.use_backend(ScriptedOCR({1: [line]})):
        assert text.readtime(short.path).status == "FAIL"  # 1.0 s is still under the 1.5 s floor


@needs_ffmpeg
def test_a_surface_not_meant_to_be_read_isnt_timed_and_says_why(film_builder):
    fb = _board_film(film_builder, "Where to focus · Small print on the closing report",
                     {"not_for_reading": ["Small print on the closing report"],
                      "reason": "the report closes at 0.6 scale; only its title must read"}, frames_shown=60)
    with ocr.use_backend(ScriptedOCR({1: ["Where to focus"]})):
        r = text.readtime(fb.path)
    assert r.status == "PASS", r.details
    assert r.details["exempt"] == ["S01 'Small print on the closing report': not for reading (the report closes at "
                                   "0.6 scale; only its title must read)"]
    assert _sub(r, "exempt")["status"] == "PASS"
    assert len(r.details["rows"]) == 1  # the title is still timed


@needs_ffmpeg
def test_a_declaration_that_cant_be_honoured_fails(film_builder):
    cases = [
        ({"not_for_reading": True}, "without a reason"),
        ({"not_for_reading": ["A statement the shot never shows"], "reason": "x"}, "doesn't show"),
        ({"not_for_reading": "yes", "reason": "x"}, "should be true or a list"),
        ({"skip": True, "reason": "x"}, "it takes not_for_reading, voice_matched and reason"),
        ("not for reading", "should be an object"),
    ]
    for reading, why in cases:
        fb = _board_film(film_builder, "Hold this line", reading, frames_shown=120)
        with ocr.use_backend(ScriptedOCR({1: ["Hold this line"]})):
            r = text.readtime(fb.path)
        assert r.status == "FAIL" and why in " ".join(_sub(r, "declarations")["summary"].split()) + " ".join(r.evidence), \
            (reading, r.evidence)


@needs_ffmpeg
def test_a_renumbered_storyboard_finds_the_scripts_declaration_by_its_source_id(film_builder):
    fb = film_builder(shots=[(1, 120)])
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "source_id": "hero", "on_screen": "Tiny footnote text here"}]}]})
    fb.script({"acts": [{"name": "One", "shots": [{"id": "hero", "on_screen": "Tiny footnote text here",
                                                   "reading": {"not_for_reading": True, "reason": "texture only"}}]}]})
    fb.render(1, moving_square(120, fb.size))
    with ocr.use_backend(ScriptedOCR({})):
        r = text.readtime(fb.path)
    assert r.status == "PASS" and r.details["exempt"]


def test_exempt_statements_need_no_ocr_but_the_rest_still_do(film_builder, no_vision):
    fb = film_builder(shots=[(1, 120)])
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": "Tiny footnote text here"}]}]})
    fb.script({"acts": [{"name": "One", "shots": [{"id": "S01", "on_screen": "Tiny footnote text here",
                                                   "reading": {"not_for_reading": True, "reason": "texture only"}}]}]})
    r = text.readtime(fb.path)
    assert r.status == "PASS" and "declared not for reading" in r.summary  # nothing left that needs reading
    both = "Tiny footnote text here · A title to read"
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": both}]}]})
    fb.script({"acts": [{"name": "One", "shots": [{"id": "S01", "on_screen": both, "reading": {
        "not_for_reading": ["Tiny footnote text here"], "reason": "texture only"}}]}]})
    r = text.readtime(fb.path)
    assert r.status == "UNCHECKED" and r.engine["name"] == "Apple Vision (ocrmac)"
    assert _sub(r, "exempt")["status"] == "PASS" and _sub(r, "ocr")["status"] == "UNCHECKED"


# ---------------------------------------------------------------------------------------------- caption rules
def _format_file(tmp, **captions) -> str:
    path = tmp / "formats" / "reel.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "reel", "width": 96, "height": 54, "captions": captions}))
    return str(path)


def test_both_caption_gates_read_one_rule_tightened_never_loosened(film_builder, slate_home):
    from slate.qa import access

    fb = film_builder(shots=[(1, 150)])
    src = _format_file(slate_home["tmp"], max_cps=20, max_chars=32, min_s=0.8)
    film = json.loads((fb.path / "film.json").read_text())
    write_json(fb.path / "film.json", {**film, "format": "reel", "format_source": src})
    ctx = FilmContext(fb.path)
    rules, where = text.caption_rules(ctx)
    assert rules == {"max_lines": 2, "max_chars": 32, "max_cps": 17.0, "min_seconds": 0.8}
    assert where == "the law, tightened by the reel format (max_chars 32)"
    assert access.caption_limits(ctx) == ({"max_cps": 17.0, "max_lines": 2, "max_chars": 32}, where)
    write_json(fb.path / "film.json", {**film, "format": "reel", "format_source": src, "captions": {"max_cps": 15}})
    rules, where = text.caption_rules(FilmContext(fb.path))
    assert rules["max_cps"] == 15.0 and where.endswith("and film.json (max_cps 15)")
    # one cue at 16 characters a second: both gates fail it, for the same reason
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"line": "Sixteen chars ok", "words": []}}})
    write_json(fb.path / "timing" / "captions.json", {"cues": [
        {"shot": "S01", "text": "Sixteen chars ok", "lines": None, "start": 0.0, "end": 1.0}]})
    core = text.captions(fb.path)
    assert core.status == "FAIL" and "16.0 characters a second (max 15)" in " ".join(core.evidence)
    assert core.details["rules_from"].endswith("film.json (max_cps 15)")
    acc = access.check_captions(FilmContext(fb.path))
    assert acc.status == "FAIL" and "the limit is 15" in acc.summary
    assert "film.json" in core.inputs and any(k.endswith("reel.json") for k in core.inputs)


def test_the_voice_modules_captions_are_what_the_gates_read(film_builder):
    """`slate voice captions` writes timing/captions.json (the cues) and voice/captions/<film>.srt and .vtt."""
    vc = pytest.importorskip("slate.voice.captions")
    assert CAPTION_CANDIDATES[0] == "timing/captions.json"
    fb = film_builder(shots=[(1, 150)])
    line = "Hello there. This is a line."
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"line": line, "words": []}}})
    cues = [{"shot": "S01", "text": "Hello there.", "lines": ["Hello there."], "start": 0.2, "end": 1.4, "cps": 10.0},
            {"shot": "S01", "text": "This is a line.", "lines": None, "start": 1.5, "end": 3.0, "cps": 10.0}]
    write_json(fb.path / "timing" / "captions.json", {
        "generated": "do not edit by hand: slate voice captions", "film": fb.path.name, "language": "en",
        "fps_num": 30, "fps_den": 1, "settings": dict(vc.DEFAULTS), "source": "timing/vo_words.json",
        "files": {"srt": f"voice/captions/{fb.path.name}.srt", "vtt": f"voice/captions/{fb.path.name}.vtt"},
        "cues": cues, "problems": [], "script_exact": True})
    folder = fb.path / "voice" / "captions"
    folder.mkdir(parents=True)
    written = [{**c, "lines": c["lines"] or [c["text"]]} for c in cues]
    (folder / f"{fb.path.name}.srt").write_text(vc.to_srt(written), encoding="utf-8")
    (folder / f"{fb.path.name}.vtt").write_text(vc.to_vtt(written), encoding="utf-8")
    core = text.captions(fb.path)
    assert core.status == "PASS", core.evidence
    assert core.details["file"] == "timing/captions.json" and core.details["stage"] == "voice"
    from slate.qa import access

    acc = access.check_captions(FilmContext(fb.path))
    assert acc.status == "PASS", acc.summary
    assert [f["file"] for f in acc.details["files"]] == ["timing/captions.json",
                                                        f"voice/captions/{fb.path.name}.srt",
                                                        f"voice/captions/{fb.path.name}.vtt"]
    assert all(f["cues"] == 2 for f in acc.details["files"])


# ---------------------------------------------------------------------------------------------- stages
def test_gates_whose_names_dont_say_their_stage_name_it(film_builder):
    fb = film_builder()
    results = {r.gate: r for r in qa.run(fb.path, ["bit_depth", "captions"])}
    assert results["bit_depth"].details["stage"] == "sound"
    assert results["captions"].details["stage"] == "voice"
    status = pytest.importorskip("slate.state.status")
    report = gates.load_report(fb.path)
    assert status._gate_stage(report["bit_depth"]) == "sound" and status._gate_stage(report["captions"]) == "voice"


# ---------------------------------------------------------------------------------------------- no Vision, no pass
@needs_ffmpeg
def test_without_vision_no_ocr_gate_passes_and_neither_does_the_film(film_builder, brand_store, no_vision):
    write_brand(brand_store, banned=["forbidden"])
    fb = film_builder(shots=[(1, 30)], size=(54, 96))  # a 9:16 frame, so the safe zones apply
    fb.render(1, moving_square(30, fb.size))
    fb.all_events()
    fb.script({"acts": [{"name": "One", "shots": [{"id": "S01", "vo": "A clean line.", "on_screen": "A clean title"}]}]})
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": "A clean title"}]}]})
    results = {r.gate: r for r in qa.run(fb.path, ["ocr", "readtime", "banned_words", "access"])}
    for name in ("ocr", "readtime", "banned_words", "access"):
        assert results[name].status != "PASS", (name, results[name].summary)
    assert _sub(results["access"], "safe_zones")["status"] == "UNCHECKED"
    assert "ocrmac" in _sub(results["access"], "safe_zones")["summary"]
    assert gates.overall(results.values()) != "PASS"


# ---------------------------------------------------------------------------------------------- Vision's noise
def test_visions_own_prints_never_reach_stdout(monkeypatch, capfd):
    """Vision prints "Unable to find a valid E5" to file descriptor 1 from native code; a --json run must not."""
    import os
    import types

    class OCR:
        def __init__(self, image, framework=None, recognition_level=None):
            self.level = recognition_level

        def recognize(self):
            os.write(1, b"Unable to find a valid E5 (native noise)\n")
            return [("SLATE 42", 0.98, [0.1, 0.2, 0.5, 0.1])]

    fake = types.ModuleType("ocrmac.ocrmac")
    fake.OCR = OCR
    package = types.ModuleType("ocrmac")
    package.ocrmac = fake
    monkeypatch.setitem(sys.modules, "ocrmac", package)
    monkeypatch.setitem(sys.modules, "ocrmac.ocrmac", fake)
    backend = ocr.VisionBackend("accurate", {"name": ocr.ENGINE_NAME, "version": "test"})
    lines = backend.recognize(object())
    print("the command's own output")
    out, err = capfd.readouterr()
    assert lines == [{"text": "SLATE 42", "conf": 0.98, "box": [0.1, 0.2, 0.5, 0.1]}]
    assert "E5" not in out and "the command's own output" in out
    assert "Unable to find a valid E5" in err


def test_a_cut_at_another_aspect_reads_its_formats_captions_at_that_aspect(film_builder):
    formats = pytest.importorskip("slate.formats")
    if "ad" not in formats.names():
        pytest.skip("the formats library has no 'ad' format")
    wide = film_builder(film_id="ad-wide", size=(1920, 1080))
    tall = film_builder(film_id="ad-tall", size=(1080, 1920))
    square = film_builder(film_id="ad-square", size=(1080, 1080))
    for fb, aspect in ((wide, "16:9"), (tall, "9:16"), (square, "1:1")):
        film = json.loads((fb.path / "film.json").read_text())
        write_json(fb.path / "film.json", {**film, "format": "ad", "aspect": aspect})
    assert text.caption_rules(FilmContext(wide.path))[0]["max_chars"] == 42
    assert text.caption_rules(FilmContext(tall.path))[0]["max_chars"] == 32  # a 9:16 cut of a 16:9 format
    assert text.caption_rules(FilmContext(square.path))[0]["max_chars"] == 36
    # film.json without "aspect" still says its shape through its size
    film = json.loads((tall.path / "film.json").read_text())
    write_json(tall.path / "film.json", {k: v for k, v in film.items() if k != "aspect"})
    assert text.caption_rules(FilmContext(tall.path))[0]["max_chars"] == 32


def test_a_renumbered_shot_s_listed_exemption_is_read_once(film_builder, no_vision):
    fb = film_builder(shots=[(1, 120)])
    both = "Tiny footnote text here · A title to read"
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "source_id": "hero", "on_screen": both}]}]})
    fb.script({"acts": [{"name": "One", "shots": [{"id": "hero", "on_screen": both, "reading": {
        "not_for_reading": ["Tiny footnote text here"], "reason": "texture only"}}]}]})
    decls, problems = text.reading_declarations(FilmContext(fb.path))
    assert set(decls) == {"S01"} and not problems
    r = text.readtime(fb.path)
    assert "declarations" not in {c["check"] for c in r.details["checks"]}
    assert r.details["exempt"] and r.status == "UNCHECKED"  # the title still needs Vision
