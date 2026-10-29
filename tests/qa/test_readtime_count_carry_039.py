"""A count that lands late in its shot may finish its 2 s hold in the next shot, while the same display stays up.

The investor film's 586 lands 1.4 s before its shot ends and stays on screen well into the next shot; the hold used to
be measured inside the count's own shot only, so it could never pass. Only the OCR boundary is scripted here.
"""

from __future__ import annotations

import json

import pytest

from slate.clock import Clock
from slate.qa import ocr
from slate.qa.checks import text

LINE = "72% complete"


def _write(film, relative, value):
    path = film / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")
    return path


@pytest.fixture
def carry_film(slate_home, monkeypatch):
    index = 0

    def make(visible, *, second_act=False, other_count=None):
        """`visible(t)` says whether the display is on screen at film second t. S01 is 0-4 s, S02 4-8 s; the count
        lands at 3.0 s in S01."""
        nonlocal index
        index += 1
        film = slate_home["films"] / f"carry-{index}"
        clock = Clock(30, 1)
        _write(film, "film.json", {"id": f"carry-{index}", "mode": "draft", "brand": "demo", "language": "en",
                                  "fps_num": 30, "fps_den": 1, "width": 96, "height": 54})
        s1 = {"id": "S01", "on_screen": LINE, "vo": "", "dur": 4.0,
              "reading": {"count_events": [{"text": LINE, "events": ["progress-count"]}]}}
        s2 = {"id": "S02", "on_screen": "", "vo": "", "dur": 4.0}
        acts = [{"name": "One", "shots": [s1]}, {"name": "Two", "shots": [s2]}] if second_act else \
            [{"name": "One", "shots": [s1, s2]}]
        _write(film, "script/script.json", {"acts": acts})
        board = [{"name": a["name"], "shots": [{"id": s["id"], "on_screen": s["on_screen"], "dur": 4.0}
                                               for s in a["shots"]]} for a in acts]
        _write(film, "script/storyboard.json", {"acts": board})
        _write(film, "timing/shots.json", {"fps_num": 30, "fps_den": 1, "shots": [
            {"id": "S01", "act": 1, "frame_in": 0, "frame_out": 120, "on_screen": LINE, "vo": ""},
            {"id": "S02", "act": 2 if second_act else 1, "frame_in": 120, "frame_out": 240, "on_screen": "",
             "vo": ""}]})
        events = [{"shot": "S01", "id": "progress-count", "kind": "count", "start": 2.5, "dur": 0.5, "t": 3.0,
                   "from": 0, "to": 72, "size": "m", "note": "The displayed demo statistic reaches its value."}]
        if other_count:
            events.append(other_count)
        _write(film, "timing/visual_events/act1.json", events)
        if second_act:
            _write(film, "timing/visual_events/act2.json", {"silent": True, "reason": "No picture beat."})
        samples = [{"t": k / 4, "frame": clock.frame_of(k / 4),
                    "lines": [{"text": LINE}] if visible(k / 4) else []} for k in range(32)]
        sweep = ocr.Sweep(engine={"name": "scripted OCR", "version": "fixture"}, every=0.25, source="renders",
                          files=[], samples=samples, missing_acts=[], cache=None, expected=len(samples))
        monkeypatch.setattr(ocr, "available", lambda: (True, sweep.engine))
        monkeypatch.setattr(ocr, "sweep", lambda _ctx: sweep)
        return film

    return make


def _hold(result):
    return next(c for c in result.details["checks"] if c["check"] == "count_hold")


def test_a_late_count_holds_on_into_the_next_shot(carry_film):
    film = carry_film(lambda t: 2.5 <= t < 5.5)
    result = text.readtime(film)
    assert _hold(result)["status"] == "PASS", result.to_dict()
    row = result.details["count_checks"][0]
    assert row["required_end_seconds"] == 5.0 and row["readable_end_seconds"] == 5.5


def test_a_late_count_that_leaves_at_the_cut_still_fails(carry_film):
    film = carry_film(lambda t: 2.5 <= t < 4.0)
    result = text.readtime(film)
    assert _hold(result)["status"] == "FAIL", result.to_dict()
    assert result.details["count_checks"][0]["readable_end_seconds"] == 4.0


def test_a_gap_at_the_cut_ends_the_hold(carry_film):
    film = carry_film(lambda t: 2.5 <= t < 4.0 or 4.25 <= t < 6.0)
    assert _hold(text.readtime(film))["status"] == "FAIL"


def test_the_hold_never_carries_into_another_act(carry_film):
    film = carry_film(lambda t: 2.5 <= t < 6.0, second_act=True)
    assert _hold(text.readtime(film))["status"] == "FAIL"
