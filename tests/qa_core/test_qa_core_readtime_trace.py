"""Read time counts a statement only while it's legible: at least 90% opaque and moving under 8 px a frame at 1080
lines (L-qa-D043), from the act's motion trace, not only where OCR reads it whole."""

from __future__ import annotations

from slate.qa import ocr
from slate.qa.checks import text

from qa_core_helpers import ScriptedOCR, moving_square, needs_ffmpeg, paint_codes, write_json

LINE = "Hold this clean line"  # four words: it must read for 1.6 s


def _film(film_builder):
    fb = film_builder(shots=[(1, 90)])  # S01: 3 s at 30 fps
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": LINE}]}]})
    fb.render(1, paint_codes(moving_square(90, fb.size), [(0, 90, 1)]))
    return fb


def _trace(fb, entries):
    write_json(fb.path / "timing" / "motion" / "act1.json", entries)


def _readtime(fb):
    with ocr.use_backend(ScriptedOCR({1: [LINE]})):
        return text.readtime(fb.path)


@needs_ffmpeg
def test_a_line_still_fading_in_isnt_read_yet(film_builder):
    fb = _film(film_builder)
    r = _readtime(fb)
    assert r.status == "PASS" and r.details["traced"] == 0  # OCR alone: on screen for 3 s
    # the trace: it fades in over two seconds, so it's 90% opaque only from frame 54 (1.8 s): 1.2 s left to read
    _trace(fb, [{"id": "title", "shot": "S01", "prop": "opacity", "ease": "linear", "frame_in": 0, "frame_out": 60,
                 "from": 0, "to": 1, "samples": [[f, round(f / 60, 4)] for f in range(61)], "text": LINE}])
    r = _readtime(fb)
    assert r.status == "FAIL", r.details["rows"]
    assert r.details["traced"] == 1 and "counted only while ≥ 90% opaque and under 8 px a frame" in r.details["short"][0]


@needs_ffmpeg
def test_a_line_still_travelling_isnt_read_until_it_settles(film_builder):
    fb = _film(film_builder)
    h = fb.size[1]  # the limit scales with the short side: 8 px a frame at 1080 lines
    fast = 2.0 * 8 * h / 1080  # twice the limit, in this film's pixels

    def rise(until: int):
        return [{"id": "title", "shot": "S01", "prop": "y", "ease": "linear", "frame_in": 0, "frame_out": until,
                 "from": fast * until, "to": 0.0, "samples": [[f, round(fast * (until - f), 4)] for f in range(until + 1)],
                 "text": LINE},
                {"id": "title", "shot": "S01", "prop": "opacity", "ease": "linear", "frame_in": 0, "frame_out": 2,
                 "from": 0.5, "to": 1.0, "samples": [[0, 0.5], [1, 0.75], [2, 1.0]], "text": LINE}]

    _trace(fb, rise(40))  # settles at 1.33 s: 1.67 s left
    assert _readtime(fb).status == "PASS"
    _trace(fb, rise(60))  # settles at 2 s: 1 s left
    r = _readtime(fb)
    assert r.status == "FAIL" and r.details["traced"] == 1


@needs_ffmpeg
def test_a_trace_that_names_other_words_leaves_the_statement_to_ocr(film_builder):
    fb = _film(film_builder)
    _trace(fb, [{"id": "logo", "shot": "S01", "prop": "opacity", "ease": "linear", "frame_in": 0, "frame_out": 60,
                 "from": 0, "to": 1, "samples": [[0, 0], [60, 1]], "text": "Another line entirely"}])
    r = _readtime(fb)
    assert r.status == "PASS" and r.details["traced"] == 0
