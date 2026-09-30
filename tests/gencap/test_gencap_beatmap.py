"""Beat maps: taps land on the voice's words (retimed only in idle time) and become visual events that make sounds."""

from __future__ import annotations

import json

import pytest

from gencap_helpers import run_json, vfr_clip
from slate import gates, jsonio
from slate.capture import beatmap, ios, media, spec as spec_mod, store
from slate.errors import MissingPrerequisite
from slate.gen import deps


def anchor(step_id, raw, target):
    return beatmap.Anchor(step={"id": step_id}, raw_frame=raw, target_frame=target, exact=True)


def test_a_later_word_holds_the_stillest_idle_frame():
    diffs = [5.0] * 10 + [0.0] * 5 + [5.0] * 5  # frames 10–15 are still
    result = beatmap.plan(21, [anchor("a", 18, 25)], film_in=0, response_frames=3, diffs=diffs)
    assert result.mapping.index(18) == 25  # the tap now happens on the word's frame
    (edit,) = result.edits
    assert edit["kind"] == "hold" and edit["frames"] == 7 and edit["static"]
    assert 10 <= result.mapping[edit["at_frame"]] <= 15  # held where the screen was still


def test_a_sooner_word_cuts_idle_frames_never_the_response():
    diffs = [0.0] * 30
    anchors = [anchor("a", 5, 5), anchor("b", 20, 15)]
    result = beatmap.plan(30, anchors, film_in=0, response_frames=4, diffs=diffs)
    assert result.mapping.index(5) == 5 and result.mapping.index(20) == 15
    cut = next(e for e in result.edits if e["kind"] == "cut")
    assert cut["frames"] == 5
    kept = result.mapping[:15]
    assert all(f in kept for f in range(5, 9))  # the response to tap a (4 frames) plays untouched


def test_when_there_is_no_idle_time_the_miss_is_reported():
    anchors = [anchor("a", 5, 5), anchor("b", 8, 6)]  # b's word is 2 frames sooner, inside a's response
    result = beatmap.plan(20, anchors, film_in=0, response_frames=6, diffs=[0.0] * 20)
    assert result.residuals == {"b": 2}


def test_the_first_tap_places_the_capture_and_a_head_before_the_film_is_cut():
    result = beatmap.plan(40, [anchor("a", 30, 10)], film_in=None, response_frames=3, diffs=[0.0] * 40)
    assert result.film_in == 0 and result.edits[0]["kind"] == "cut" and result.edits[0]["frames"] == 20
    assert result.mapping.index(30) == 10


def _file_capture(film, steps, name="rec"):
    clip = vfr_clip(film / "refs" / f"{name}.mp4", [((200, 200, 200), 1.2), ((60, 110, 190), 0.8),
                                                   ((50, 160, 80), 0.1)], size=(60, 120))
    spec = {"kind": "file", "name": name, "source": str(clip), "duration": 4, "place": {"shot": "S01"},
            "steps": steps}
    return ios.finish(film, spec_mod.normalize(spec), source=clip, command="test")


def test_taps_land_on_words_through_the_real_timing_module(film):
    path = _file_capture(film, [{"at": 1.2, "do": "mark", "id": "tap1", "word": {"shot": "S01", "text": "tap"},
                                 "size": "small", "note": "the save button"},
                                {"at": 2.0, "do": "mark", "id": "save", "word": {"shot": "S01", "text": "save"}}])
    rec = jsonio.read(path)
    assert deps.validate_part(rec, "capture") == []
    assert [t["error_frames"] for t in rec["taps"]] == [0, 0] and all(t["exact"] for t in rec["taps"])
    beats = jsonio.read(film / rec["beats"]["file"])
    assert beats["generated"].startswith("do not edit by hand")
    first = beats["events"][0]
    assert {k: first[k] for k in ("shot", "kind", "size", "source")} == {"shot": "S01", "kind": "tap",
                                                                         "size": "small", "source": "capture:rec"}
    assert first["id"] == "rec.tap1" and first["note"] == "the save button"
    # the word "tap" starts 0.4 s (cue) + 1.3 s into S01, which starts the film: the event is on that frame
    assert first["t"] == pytest.approx(1.72, abs=0.021)
    assert gates.load_report(film)["capture_beats:rec"].status == "PASS"


def test_a_word_the_voice_never_says_is_a_miss_and_fails(film):
    path = _file_capture(film, [{"at": 1.2, "do": "mark", "id": "x", "word": {"shot": "S01", "text": "banana"}}],
                         name="miss")
    rec = jsonio.read(path)
    assert rec["misses"] and "never says 'banana'" in rec["misses"][0]
    assert gates.load_report(film)["capture_beats:miss"].status == "FAIL"
    code, out, _ = run_json(["capture", "beatmap", "--film", str(film), "--name", "miss"])
    assert code == 1 and out["gate"]["status"] == "FAIL"


def test_steps_out_of_order_with_their_words_are_a_miss(film):
    def fake(film_dir, shot, word, occ=1, which="start"):
        return {"first": 3.0, "second": 1.0}[word], True

    _file_capture(film, [{"at": 1.0, "do": "mark", "id": "a", "word": {"shot": "S01", "text": "first"}},
                         {"at": 2.0, "do": "mark", "id": "b", "word": {"shot": "S01", "text": "second"}}], name="order")
    result = beatmap.run(film, "order", word_time=fake)
    assert any("comes before" in m for m in result["misses"])


def test_unplaced_captures_are_unchecked_not_passed(film):
    clip = vfr_clip(film / "refs" / "plain.mp4", [((10, 10, 10), 0.5)])
    ios.finish(film, spec_mod.normalize({"kind": "file", "name": "plain", "source": str(clip), "duration": 1}),
               source=clip, command="test")
    result = beatmap.run(film, "plain", word_time=lambda *a, **k: (0.0, True))
    assert result["gate"]["status"] == "UNCHECKED" and result["events"] == 0
    code, out, err = run_json(["capture", "beatmap", "--film", str(film), "--name", "plain"])
    assert code == 1 and out["gate"]["status"] == "UNCHECKED"  # unchecked is never passed, so never exit 0


def test_without_word_timings_the_beat_map_waits(film, monkeypatch):
    def missing(*a, **k):
        raise MissingPrerequisite("no timings")

    monkeypatch.setattr(deps, "word_time", missing)
    path = _file_capture(film, [{"at": 1.2, "do": "mark", "id": "t", "word": {"shot": "S01", "text": "tap"}}],
                         name="waits")
    rec = jsonio.read(path)
    assert any("beat map waits" in n for n in rec["notes"])
    assert rec["frames"]["count"] == rec["raw"]["count"]  # placed as recorded until the words exist
    code, out, _ = run_json(["capture", "beatmap", "--film", str(film), "--name", "waits"])
    assert code == 3


def test_conform_cli_makes_a_file_capture(film):
    clip = vfr_clip(film / "refs" / "hand.mp4", [((90, 90, 90), 0.3), ((200, 50, 50), 0.3)])
    code, out, err = run_json(["capture", "conform", "--film", str(film), "--name", "hand", "--src", str(clip),
                               "--duration", "1.0"])
    assert code == 0, err
    assert out["kind"] == "file" and out["frames"]["count"] == 25 and out["raw"]["count"] == 25
    code, again, err = run_json(["capture", "conform", "--film", str(film), "--name", "hand"])
    assert code == 0, err
    assert again["frames"]["count"] == 25
    assert json.loads((store.spec_path(film, "hand")).read_text())["source"] == str(clip)
    assert media.count_frames(store.capture_dir(film, "hand") / "frames") == 25
