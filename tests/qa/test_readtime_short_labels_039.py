"""Declared short labels keep their per-word requirement and use the sourced lower default floor.

These neutral fixtures exercise the public script and master gates. Only the OCR boundary is scripted; the film
clock, declarations, reading policy, measured phrase resolver and result freshness remain real.
"""

from __future__ import annotations

import copy
import json

import pytest

from slate import formats, gates
from slate.qa import ocr
from slate.qa.checks import text
from slate.qa.film import session
from slate.story.gates import check_script


LABELS = {"labels": True, "reason": "These words identify the demo interface's visible controls."}


def _write(film, relative, data):
    path = film / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")
    return path


@pytest.fixture
def label_film(slate_home, monkeypatch):
    index = 0

    def make(line, *, reading=LABELS, visible_for=2.0, settled=2.0, rules=None, fmt=None):
        nonlocal index
        index += 1
        film = slate_home["films"] / f"labels-{index}"
        film_doc = {"id": f"labels-{index}", "mode": "draft", "brand": "demo", "language": "en",
                    "fps_num": 30, "fps_den": 1, "width": 96, "height": 54}
        if fmt:
            film_doc.update(format=fmt["name"], format_source=fmt["path"])
        _write(film, "film.json", film_doc)
        shot = {"id": "S01", "vo": "", "on_screen": line, "picture": "A labeled demo interface.",
                "dur": settled + 0.875}
        if reading is not None:
            shot["reading"] = copy.deepcopy(reading)
        script = {"title": "Label read time", "acts": [{"name": "One", "shots": [shot]}]}
        if rules is not None:
            script["rules"] = {"read_time": rules}
        _write(film, "script/script.json", script)
        _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "on_screen": line}]}]})
        _write(film, "timing/shots.json", {"fps_num": 30, "fps_den": 1, "shots": [
            {"id": "S01", "act": 1, "frame_in": 0, "frame_out": 120, "on_screen": line, "vo": ""}]})
        samples = [{"t": k / 4, "frame": round(k * 30 / 4),
                    "lines": [{"text": line.replace(" · ", " ")}] if k / 4 < visible_for else []}
                   for k in range(16)]
        sweep = ocr.Sweep(engine={"name": "scripted OCR", "version": "fixture"}, every=0.25,
                          source="renders", files=[], samples=samples, missing_acts=[], cache=None,
                          expected=len(samples))
        monkeypatch.setattr(ocr, "available", lambda: (True, sweep.engine))
        monkeypatch.setattr(ocr, "sweep", lambda _ctx: sweep)
        return film, script, sweep

    return make


def _script(script, fmt=None):
    return {r.gate: r for r in check_script(script, brand={}, fmt=fmt or {})}


def _format(tmp_path, read_time=None):
    raw = json.loads((formats.formats_dir() / "product-demo.json").read_text())
    if read_time is not None:
        raw["read_time"] = read_time
    source = _write(tmp_path, "format.json", raw)
    return formats.load(str(source), aspect="16:9")


@pytest.mark.parametrize("line,settled", [("Preview", 1.05), ("Ready now", 1.05), ("Ready for review", 1.35)])
def test_script_short_labels_keep_the_word_formula_with_a_lower_default_floor(label_film, line, settled):
    _film, script, _sweep = label_film(line, settled=settled)
    result = _script(script)["script.read_time"]
    assert result.status == "PASS", result.to_dict()


@pytest.mark.parametrize("line,required", [("Preview", "1.00"), ("Ready now", "1.00"),
                                           ("Ready for review", "1.30")])
def test_master_short_labels_keep_the_word_formula_with_a_lower_default_floor(label_film, line, required):
    film, _script_doc, _sweep = label_film(line, visible_for=1.0)
    result = text.readtime(film)
    assert result.details["statements"] == 1
    assert f"needs {required} s" in result.details["rows"][0]
    assert result.status == ("FAIL" if required == "1.30" else "PASS"), result.to_dict()


@pytest.mark.parametrize("line", ["Preview", "Ready now", "Ready for review"])
def test_short_labels_cannot_bypass_their_minimum(label_film, line):
    film, script, _sweep = label_film(line, visible_for=0.5, settled=0.5)
    assert _script(script)["script.read_time"].status == "FAIL"
    assert text.readtime(film).status == "FAIL"


def test_an_explicit_single_word_label_must_be_visible(label_film):
    film, _script_doc, _sweep = label_film("Preview", visible_for=0.0)
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert result.details["statements"] == 1


@pytest.mark.parametrize("line", ["Preview", "1,200", "Demo™ lockup"])
def test_undeclared_review_only_items_keep_default_extraction(label_film, line):
    film, _script_doc, _sweep = label_film(line, reading=None, visible_for=0.0)
    result = text.readtime(film)
    assert result.status == "PASS" and result.details["statements"] == 0, result.to_dict()


def test_short_ordinary_prose_keeps_the_generic_floor(label_film):
    film, script, _sweep = label_film("Ready now", reading=None, visible_for=1.0, settled=1.05)
    assert _script(script)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL" and "needs 1.50 s" in result.details["rows"][0]


def test_long_labels_keep_the_full_generic_requirement(label_film):
    line = "Durability is how long a team holds its course"
    film, script, _sweep = label_film(line, visible_for=2.0, settled=2.1)
    assert _script(script)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL" and "needs 3.10 s" in result.details["rows"][0]


@pytest.mark.parametrize("reading", [{"labels": True}, {"labels": ["Preview"], "reason": "A control name."},
                                     {"labels": "yes", "reason": "A control name."}])
def test_invalid_label_declarations_gain_no_short_hold(label_film, reading):
    film, script, _sweep = label_film("Preview", reading=reading, visible_for=2.0, settled=1.05)
    assert any(r.status == "FAIL" for r in _script(script).values())
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert any(c["check"] == "declarations" for c in result.details["checks"])


def test_short_label_words_use_the_shared_authored_word_count(label_film):
    film, script, _sweep = label_film("Ready - now", visible_for=1.0, settled=1.05)
    assert _script(script)["script.read_time"].status == "PASS"
    result = text.readtime(film)
    assert result.status == "PASS" and "needs 1.00 s" in result.details["rows"][0], result.to_dict()


def test_not_for_reading_precedes_label_timing(label_film):
    reading = {**LABELS, "not_for_reading": True}
    film, script, _sweep = label_film("Preview", reading=reading, visible_for=0.0, settled=0.1)
    assert _script(script)["script.read_time"].status == "PASS"
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["exempt"] == ["S01 'Preview': not for reading (" + LABELS["reason"] + ")"]


@pytest.mark.parametrize("line", ["120 loaves", "1,200"])
def test_numeric_labels_retain_the_two_second_requirement(label_film, line):
    film, script, _sweep = label_film(line, visible_for=1.25, settled=1.25)
    assert _script(script)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert "needs 2.00 s" in result.details["rows"][0]


@pytest.mark.parametrize("line", ["Preview", "Ready now"])
def test_voice_matched_labels_keep_the_complete_text_floor_and_phrase_end(label_film, line):
    film, script, _sweep = label_film(line, reading={**LABELS, "voice_matched": True},
                                    visible_for=2.0, settled=1.05)
    assert _script(script)["script.read_time"].status == "FAIL"
    words = [{"w": w, "start": k * 0.2, "end": 1.75 if k == len(line.split()) - 1 else (k + 1) * 0.2}
             for k, w in enumerate(line.split())]
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": line, "words": words}}})
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": 0.0, "file": "voice/fixture.wav"}]})
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert "needs 1.50 s, voice-matched" in result.details["rows"][0]
    assert result.details["voice_matched_checks"][0]["voice_end_seconds"] == 1.75


def test_voice_matched_single_word_label_without_measurements_is_unchecked(label_film):
    film, _script_doc, _sweep = label_film("Preview", reading={**LABELS, "voice_matched": True})
    result = text.readtime(film)
    assert result.status == "UNCHECKED", result.to_dict()


def test_each_declared_label_is_timed_without_combining_its_word_count(label_film):
    film, script, _sweep = label_film("Preview · Ready now", visible_for=1.0, settled=1.05)
    assert _script(script)["script.read_time"].status == "PASS"
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    assert result.details["statements"] == 2
    assert all("needs 1.00 s" in row for row in result.details["rows"])


def test_long_statement_among_labels_is_not_granted_the_short_floor(label_film):
    line = "Preview · Keep this longer statement readable"
    film, script, _sweep = label_film(line, visible_for=1.0, settled=1.05)
    assert _script(script)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert any("Keep this longer statement readable" in row and "needs 1.90 s" in row
               for row in result.details["short"])


def test_inherited_format_default_does_not_mask_the_short_label_floor(label_film, tmp_path):
    fmt = _format(tmp_path)
    assert fmt["read_time"]["floor"] == 1.5
    film, script, _sweep = label_film("Ready now", fmt=fmt, visible_for=1.0, settled=1.05)
    assert _script(script, fmt)["script.read_time"].status == "PASS"
    result = text.readtime(film)
    assert result.status == "PASS" and "needs 1.00 s" in result.details["rows"][0], result.to_dict()


@pytest.mark.parametrize("floor", [1.5, 2.0])
def test_explicit_format_floor_still_governs_declared_labels(label_film, tmp_path, floor):
    fmt = _format(tmp_path, {"floor": floor})
    film, script, _sweep = label_film("Ready now", fmt=fmt, visible_for=1.0, settled=1.05)
    assert _script(script, fmt)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert f"needs {floor:.2f} s" in result.details["rows"][0]


def test_script_floor_takes_precedence_over_the_format_floor(label_film, tmp_path):
    fmt = _format(tmp_path, {"floor": 2.0})
    film, script, _sweep = label_film("Ready now", fmt=fmt, rules={"floor": 1.0},
                                    visible_for=1.0, settled=1.05)
    assert _script(script, fmt)["script.read_time"].status == "PASS"
    result = text.readtime(film)
    assert result.status == "PASS" and "needs 1.00 s" in result.details["rows"][0], result.to_dict()


def test_label_policy_preserves_base_and_per_word_overrides(label_film, tmp_path):
    fmt = _format(tmp_path, {"base": 0.8, "per_word": 0.5})
    film, script, _sweep = label_film("Ready for review", fmt=fmt, rules={"per_word": 0.3},
                                    visible_for=2.0, settled=1.8)
    assert _script(script, fmt)["script.read_time"].status == "PASS"
    result = text.readtime(film)
    assert result.status == "PASS" and "needs 1.70 s" in result.details["rows"][0], result.to_dict()


def test_custom_shared_floor_still_governs_labels(label_film, tmp_path, monkeypatch):
    original = formats.formats_dir()
    custom = tmp_path / "formats"
    custom.mkdir()
    for name in ("_shared.json", "_platforms.json", "product-demo.json"):
        (custom / name).write_bytes((original / name).read_bytes())
    shared = json.loads((custom / "_shared.json").read_text())
    shared["read_time"]["floor"] = 2.2
    _write(custom, "_shared.json", shared)
    monkeypatch.setenv("SLATE_FORMATS_DIR", str(custom))
    fmt = formats.load("product-demo", aspect="16:9")
    film, script, _sweep = label_film("Ready now", fmt=fmt, visible_for=1.5, settled=1.6)
    assert _script(script, fmt)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL" and "needs 2.20 s" in result.details["rows"][0], result.to_dict()
    assert str(custom / "_shared.json") in result.inputs


def test_unreadable_format_provenance_preserves_the_resolved_floor(label_film, tmp_path):
    fmt = _format(tmp_path)
    film, script, _sweep = label_film("Ready now", fmt=fmt, visible_for=1.0, settled=1.05)
    with session(film) as ctx:
        assert ctx.format()["read_time"]["floor"] == 1.5
        (tmp_path / "format.json").write_text("{ invalid json\n")
        assert _script(script, fmt)["script.read_time"].status == "FAIL"
        result = text.readtime(film)
    assert result.status == "FAIL" and "needs 1.50 s" in result.details["rows"][0], result.to_dict()
    assert str(tmp_path / "format.json") in result.inputs


def test_null_format_policy_provenance_preserves_the_resolved_floor(label_film, tmp_path):
    fmt = _format(tmp_path)
    film, script, _sweep = label_film("Ready now", fmt=fmt, visible_for=1.0, settled=1.05)
    with session(film) as ctx:
        assert ctx.format()["read_time"]["floor"] == 1.5
        raw = json.loads((tmp_path / "format.json").read_text())
        _write(tmp_path, "format.json", {**raw, "read_time": None})
        assert _script(script, fmt)["script.read_time"].status == "FAIL"
        result = text.readtime(film)
    assert result.status == "FAIL" and "needs 1.50 s" in result.details["rows"][0], result.to_dict()


def test_label_format_dependency_changes_make_a_saved_result_stale(label_film, tmp_path):
    fmt = _format(tmp_path, {"floor": 2.0})
    film, _script_doc, _sweep = label_film("Ready now", fmt=fmt, visible_for=3.0)
    result = text.readtime(film)
    assert result.status == "PASS", result.to_dict()
    gates.write_report(film, [result])
    assert gates.load_report(film)["readtime"].status == "PASS"
    source = _write(tmp_path, "format.json", {**json.loads((tmp_path / "format.json").read_text()),
                                            "read_time": {"floor": 3.0}})
    assert str(source) in result.inputs
    assert gates.load_report(film)["readtime"].status == "STALE"


def test_storyboard_declaration_takes_precedence_over_the_script(label_film):
    film, _script_doc, _sweep = label_film("Ready now", visible_for=1.0)
    _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "on_screen": "Ready now",
        "reading": {"voice_matched": True, "reason": "The narrator reads this phrase."}}]}]})
    result = text.readtime(film)
    assert result.status == "FAIL" and "needs 1.50 s" in result.details["rows"][0], result.to_dict()


def test_renumbered_storyboard_resolves_the_original_label_declaration(label_film):
    film, script, _sweep = label_film("Ready now", visible_for=1.0)
    script["acts"][0]["shots"][0]["id"] = "draft-label"
    _write(film, "script/script.json", script)
    _write(film, "script/storyboard.json", {"acts": [{"shots": [{"id": "S01", "source_id": "draft-label",
                                                                 "on_screen": "Ready now"}]}]})
    result = text.readtime(film)
    assert result.status == "PASS" and "needs 1.00 s" in result.details["rows"][0], result.to_dict()


def test_library_format_source_reads_the_library_policy(label_film):
    """film.json's "format_source": "slate.formats" (what `slate new` writes) names the format library, not a file in
    the film: the label policy reads the library's own file for the format, and isn't reported as damaged."""
    film, _script_doc, _sweep = label_film("Ready now", visible_for=3.0)
    doc = json.loads((film / "film.json").read_text())
    doc.update(format="product-demo", format_source="slate.formats")
    _write(film, "film.json", doc)
    result = text.readtime(film)
    assert not any("format_source" in str(e) for e in result.evidence), result.to_dict()
    assert str(formats.formats_dir() / "product-demo.json") in result.inputs
