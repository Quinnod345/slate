"""ocr, readtime, captions and claims, and the one-engine rule for OCR."""

import json
import sys
import types

import pytest

from slate import procs
from slate.gates import GateResult
from slate.qa import ocr
from slate.qa.checks import text

from qa_core_helpers import ScriptedOCR, moving_square, needs_ffmpeg, paint_codes, write_brand, write_json


def _sub(result, name):
    return next(c for c in result.details["checks"] if c["check"] == name)


@pytest.fixture
def no_vision(monkeypatch):
    """Slate's environment without ocrmac (as on a Mac where the OCR extra isn't installed)."""
    monkeypatch.setitem(sys.modules, "ocrmac", None)
    monkeypatch.setitem(sys.modules, "ocrmac.ocrmac", None)
    ocr._STATE.clear()
    yield
    ocr._STATE.clear()


@pytest.fixture
def spawned(monkeypatch):
    """Every child process argv the code under test starts."""
    calls = []
    real = procs.run

    def spy(argv, **kw):
        calls.append([str(a) for a in argv])
        return real(argv, **kw)

    monkeypatch.setattr(procs, "run", spy)
    return calls


# ---------------------------------------------------------------------------------------------- OCR: one engine
@needs_ffmpeg
def test_ocr_is_unchecked_without_vision_and_never_falls_back(film_builder, brand_store, no_vision, spawned):
    fb = film_builder(shots=[(1, 30)])
    fb.render(1, moving_square(30, fb.size))
    write_brand(brand_store, banned=["forbidden"])
    fb.script({"acts": [{"lines": [{"vo": "A clean line.", "on_screen": "A clean title"}]}]})
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": "A clean title"}]}]})
    ok, info = ocr.available(refresh=True)
    assert not ok
    assert info["name"] == "Apple Vision (ocrmac)" and info["version"] == "not installed"
    assert "slate[ocr]" in info["hint"] and info["recognition_level"] == "accurate"
    with pytest.raises(ocr.OcrUnavailable) as exc:
        ocr.backend()
    assert exc.value.exit_code == 3
    r = text.ocr(fb.path)
    assert r.status == "UNCHECKED" and r.engine["name"] == "Apple Vision (ocrmac)"
    rt = text.readtime(fb.path)
    assert rt.status == "UNCHECKED" and rt.engine["name"] == "Apple Vision (ocrmac)"
    bw = text.banned_words(fb.path)
    assert bw.status == "UNCHECKED"  # the script and captions are clean, but the picture wasn't read
    assert _sub(bw, "picture")["status"] == "UNCHECKED"
    assert bw.engine["ocr"]["name"] == "Apple Vision (ocrmac)"
    # nothing else was asked to read the picture: no tesseract, no compiled Swift OCR binary
    assert not any("tesseract" in " ".join(c) or "ocr_vision" in " ".join(c) for c in spawned)


def test_ocr_level_is_recorded_not_quietly_changed(no_vision, monkeypatch):
    monkeypatch.setenv("SLATE_OCR_LEVEL", "fast")
    ok, info = ocr.available(refresh=True)
    assert not ok and info["recognition_level"] == "fast"
    monkeypatch.setenv("SLATE_OCR_LEVEL", "something-else")
    assert ocr.level() == "accurate"


@needs_ffmpeg
def test_ocr_sweep_with_a_backend_caches_and_covers_the_film(film_builder):
    fb = film_builder(shots=[(1, 30), (2, 30)])
    fb.render_all()
    backend = ScriptedOCR({})
    with ocr.use_backend(backend):
        r = text.ocr(fb.path)
        assert r.status == "PASS", r.summary
        assert r.engine["name"] == "scripted OCR (tests)"
        assert r.details["samples"] == 8  # 2 s of film every 0.25 s
        calls = backend.calls
        assert text.ocr(fb.path).status == "PASS"
        assert backend.calls == calls  # the second run read the cache
    assert (fb.path / r.details["cache"]).is_file()


@needs_ffmpeg
def test_ocr_unchecked_for_acts_without_renders(film_builder):
    fb = film_builder(shots=[(1, 30), (2, 30)])
    fb.render(1, moving_square(30, fb.size))
    with ocr.use_backend(ScriptedOCR({})):
        r = text.ocr(fb.path)
    assert r.status == "UNCHECKED" and "acts 2" in r.summary


@needs_ffmpeg
def test_quick_mode_never_starts_the_sweep(film_builder, brand_store):
    from slate import qa

    write_brand(brand_store, banned=["forbidden"])
    fb = film_builder(shots=[(1, 30)])
    fb.render(1, moving_square(30, fb.size))
    with ocr.use_backend(ScriptedOCR({})) as backend:
        results = {r.gate: r for r in qa.run(fb.path, ["banned_words"], quick=True)}
        assert backend.calls == 0
    assert results["banned_words"].status == "UNCHECKED"
    assert "--quick" in _sub(results["banned_words"], "picture")["summary"]


# ---------------------------------------------------------------------------------------------- readtime
@needs_ffmpeg
def test_readtime_times_each_statement_inside_its_shot(film_builder):
    fb = film_builder(shots=[(1, 60), (1, 120)])  # S01 0–2 s, S02 2–6 s at 30 fps
    fb.storyboard({"acts": [{"shots": [
        {"id": "S01", "on_screen": "Hold this line"},
        {"id": "S02", "on_screen": "Two words · A much longer statement here · Brand™ lockup · 1,200"}]}]})
    frames = paint_codes(moving_square(180, fb.size), [(0, 60, 1), (60, 100, 2), (100, 130, 3)])
    fb.render(1, frames)
    texts = {1: ["Hold this line"], 2: ["Two words"], 3: ["A much longer statement here"]}
    with ocr.use_backend(ScriptedOCR(texts)):
        r = text.readtime(fb.path)
    assert r.status == "FAIL"
    assert r.details["statements"] == 3  # the lockup (™) and the bare number aren't timed
    (short,) = r.details["short"]
    assert "A much longer statement here" in short and "needs 1.90 s" in short
    with ocr.use_backend(ScriptedOCR({1: texts[1], 2: texts[2], 3: texts[3]})):
        frames = paint_codes(moving_square(180, fb.size), [(0, 60, 1), (60, 100, 2), (100, 180, 3)])
        fb.render(1, frames, name="act1_v2")
        r = text.readtime(fb.path)
    assert r.status == "PASS", r.details["rows"]


@needs_ffmpeg
def test_readtime_forgives_ocr_misreads_but_not_half_built_lines(film_builder):
    fb = film_builder(shots=[(1, 90)])
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": "Leader walks durability"}]}]})
    frames = paint_codes(moving_square(90, fb.size), [(0, 45, 1), (45, 90, 2)])
    fb.render(1, frames)
    # frames 0–45: still writing on ("Leader walks dura"); 45–90: built but OCR'd as "Leaderwalks DURA81LITY"
    with ocr.use_backend(ScriptedOCR({1: ["Leader walks dura"], 2: ["Leaderwalks DURA81LITY"]})):
        r = text.readtime(fb.path)
    row = r.details["rows"][0]
    assert "1.50 s from 1.50 s" in row  # only the built half counts, misreads and all
    assert r.status == "PASS"


def test_readtime_unchecked_without_a_storyboard_and_pass_without_statements(film_builder):
    fb = film_builder()
    for s in fb.shots_doc["shots"]:
        s.pop("on_screen")
    write_json(fb.path / "timing" / "shots.json", fb.shots_doc)
    assert text.readtime(fb.path).status == "UNCHECKED"
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": ""}]}]})
    r = text.readtime(fb.path)
    assert r.status == "PASS" and "no on-screen statements" in r.summary


# ---------------------------------------------------------------------------------------------- captions
def _captioned(film_builder, cues, line="Hello there. This is a line."):
    fb = film_builder(shots=[(1, 150)])
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"line": line, "words": []}}})
    fb.captions(cues)
    return fb


def test_captions_pass(film_builder):
    fb = _captioned(film_builder, [
        {"text": "Hello there.", "lines": ["Hello there."], "start": 0.2, "end": 1.4},
        {"text": "This is a line.", "lines": ["This is a line."], "start": 1.5, "end": 3.0}])
    r = text.captions(fb.path)
    assert r.status == "PASS", r.summary
    assert r.details["rules"] == {"max_lines": 2, "max_chars": 42, "max_cps": 17.0, "min_seconds": 1.0}


@pytest.mark.parametrize("cue,why", [
    ({"text": "Hello there.", "lines": ["Hello", "there", "."], "start": 0.0, "end": 2.0}, "3 lines"),
    ({"text": "Hello there.", "lines": ["Hello there." + " " * 31 + "x"], "start": 0.0, "end": 5.0}, "a line of 44"),
    ({"text": "Hello there.", "lines": ["Hello there."], "start": 0.0, "end": 0.6}, "characters a second"),
    ({"text": "Hello there.", "lines": ["Hello there."], "start": 0.0, "end": 0.9}, "on screen 0.90 s"),
])
def test_captions_rules(film_builder, cue, why):
    fb = _captioned(film_builder, [cue, {"text": "This is a line.", "lines": ["This is a line."], "start": 5.5,
                                         "end": 7.0}])
    r = text.captions(fb.path)
    assert r.status == "FAIL" and why in " ".join(r.evidence), r.evidence


def test_captions_seventeen_cps_boundary(film_builder):
    # 17 characters in exactly 1 s is allowed; the same text in 0.99 s isn't
    ok = _captioned(film_builder, [{"text": "Seventeen chars!!", "lines": ["Seventeen chars!!"], "start": 0, "end": 1.0}],
                    line="Seventeen chars!!")
    assert text.captions(ok.path).status == "PASS"
    bad = _captioned(film_builder, [{"text": "Seventeen chars!!", "lines": ["Seventeen chars!!"], "start": 0,
                                     "end": 0.99}], line="Seventeen chars!!")
    r = text.captions(bad.path)
    assert r.status == "FAIL" and "17.2 characters a second" in r.summary


def test_captions_overlap_order_and_script(film_builder):
    fb = _captioned(film_builder, [
        {"text": "Hello there.", "lines": ["Hello there."], "start": 0.2, "end": 1.6},
        {"text": "This is a lie.", "lines": ["This is a lie."], "start": 1.5, "end": 3.0}])
    r = text.captions(fb.path)
    ev = " ".join(r.evidence)
    assert r.status == "FAIL" and "overlaps the next cue" in ev
    assert _sub(r, "script")["status"] == "FAIL" and "“line.”" in _sub(r, "script")["summary"]


def test_captions_from_srt_and_when_absent(film_builder):
    fb = film_builder(shots=[(1, 150)])
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"line": "Hello there.", "words": []}}})
    r = text.captions(fb.path)
    assert r.status == "UNCHECKED" and "no captions yet" in r.summary
    (fb.path / "voice" / "captions").mkdir(parents=True)
    (fb.path / "voice" / "captions" / "film.srt").write_text(
        "1\n00:00:00,200 --> 00:00:01,400\n<i>Hello</i> there.\n\n", encoding="utf-8")
    r = text.captions(fb.path)
    assert r.status == "PASS", r.summary
    silent = film_builder(shots=[(1, 30)])
    silent.storyboard({"acts": [{"shots": [{"id": "S01", "vo": "", "on_screen": "Just a title"}]}]})
    r = text.captions(silent.path)
    assert r.status == "PASS" and "no spoken lines" in r.summary


# ---------------------------------------------------------------------------------------------- claims
TRUTH = {"sources": [{"id": "report-2025", "title": "A public report", "pin": "2025-01-01"}], "claims": [],
         "never_claim": []}


def _claims_film(film_builder, brand_store, shots, *, mode="supervised", approved=None, never=None, truth=None):
    fb = film_builder(mode=mode)
    write_brand(brand_store, approved=approved or [], never=never or [])
    fb.script({"title": "Claims fixture", "acts": [{"name": "One", "shots": shots}]})
    write_json(fb.path / "brief" / "truth.json", truth if truth is not None else TRUTH)
    return fb


@pytest.fixture
def no_story(monkeypatch):
    """Slate without the story module (its gates are the preferred claim check when it's there)."""
    monkeypatch.setitem(sys.modules, "slate.story.gates", None)


def test_claims_fallback_passes_sourced_and_approved(film_builder, brand_store, no_story):
    fb = _claims_film(film_builder, brand_store, [
        {"id": "S01", "vo": "Teams using it ship 40% faster.", "sources": ["report-2025:p4"]},
        {"id": "S02", "on_screen": "Trusted by 1,200 studios", "claims": ["studios"]},
        {"id": "S03", "vo": "No numbers here.", "picture": "a counter ticks to 1,200"}],
        approved=[{"id": "studios", "text": "Trusted by 1,200 studios"}])
    r = text.claims(fb.path)
    assert r.status == "PASS", r.evidence
    assert r.details["approved_claims"] >= 1 and r.engine["name"] == "slate claims check"


def test_claims_fallback_fails_unsourced_numbers_and_unpinned_sources(film_builder, brand_store, no_story):
    fb = _claims_film(film_builder, brand_store, [
        {"id": "S01", "vo": "It saves 3 hours a week."},
        {"id": "S02", "vo": "Up 12% this year.", "sources": ["nowhere"]}])
    r = text.claims(fb.path)
    assert r.status == "FAIL"
    assert _sub(r, "unsourced")["status"] == "FAIL" and "3 hours" in _sub(r, "unsourced")["summary"]
    assert _sub(r, "sources")["status"] == "FAIL" and "nowhere" in _sub(r, "sources")["summary"]


def test_claims_fallback_ignores_script_metadata(film_builder, brand_store, no_story):
    fb = _claims_film(film_builder, brand_store, [{"id": "S01", "vo": "A plain line."}])
    script = json.loads((fb.path / "script" / "script.json").read_text())
    script["version"] = "draft-2 (2026-09-30)"
    write_json(fb.path / "script" / "script.json", script)
    assert text.claims(fb.path).status == "PASS"


def test_claims_fallback_never_claim_fails(film_builder, brand_store, no_story):
    fb = _claims_film(film_builder, brand_store, [{"id": "S01", "vo": "The best studio in the world."}],
                      never=["the best studio"])
    r = text.claims(fb.path)
    assert r.status == "FAIL" and "never claims" in r.summary


def test_claims_fallback_autonomous_films_need_approved_claims(film_builder, brand_store, no_story):
    fb = _claims_film(film_builder, brand_store, [{"id": "S01", "vo": "Teams ship 40% faster.",
                                                    "sources": ["report-2025"]}], mode="autonomous")
    r = text.claims(fb.path)
    assert r.status == "REVIEW" and "approved list" in r.summary


def test_claims_fallback_without_a_brand_pack_is_unchecked_not_failed(film_builder, no_story):
    fb = film_builder(brand="no-such-brand")
    fb.script({"title": "x", "acts": [{"name": "One", "shots": [{"id": "S01", "vo": "It saves 3 hours."}]}]})
    r = text.claims(fb.path)
    assert r.status == "UNCHECKED" and "can't be read" in r.summary


def test_claims_hand_off_to_a_story_module(film_builder, brand_store, monkeypatch):
    fb = _claims_film(film_builder, brand_store, [{"id": "S01", "vo": "It saves 3 hours a week."}])
    seen = {}

    def check_script(script, *, brand, fmt, truth=None, film=None):
        seen["called"] = (script["acts"][0]["shots"][0]["vo"], brand["name"], truth["sources"][0]["id"], film["mode"])
        return [GateResult.pass_("script.structure", "plans"),
                GateResult.pass_("script.claims", "every claim sourced (story's own rules)",
                                 engine={"name": "slate script build", "version": "9"}),
                GateResult.fail("script.numbers", "1 unsourced number", evidence=["S01: 3"])]

    fake = types.ModuleType("slate.story.gates")
    fake.check_script = check_script
    monkeypatch.setitem(sys.modules, "slate.story.gates", fake)
    r = text.claims(fb.path)
    assert seen["called"] == ("It saves 3 hours a week.", "qa-fixture-brand", "report-2025", "supervised")
    assert r.status == "FAIL" and r.engine["via"] == "slate.story.gates"
    assert {c["check"] for c in r.details["checks"]} == {"script.claims", "script.numbers"}


def test_claims_through_the_real_story_module(film_builder, brand_store):
    pytest.importorskip("slate.story.gates")
    fb = _claims_film(film_builder, brand_store, [{"id": "S01", "vo": "It saves 3 hours a week."}])
    r = text.claims(fb.path)
    assert r.engine.get("via") == "slate.story.gates"
    assert r.status == "FAIL"  # an unsourced number, by the story module's own rules
    empty = _claims_film(film_builder, brand_store, [])
    write_json(empty.path / "script" / "script.json", {"title": "x", "acts": []})  # a fresh scaffold's script
    r = text.claims(empty.path)
    assert r.status == "UNCHECKED" and "stopped before the claims" in r.summary


def test_claims_unchecked_without_a_script(film_builder):
    fb = film_builder()
    assert text.claims(fb.path).status == "UNCHECKED"
