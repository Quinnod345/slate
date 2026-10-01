"""Pinned word times (L-timing-D030): a re-voice moves only the spans it corrects. The delivered times of the words a
beat leans on are kept (timing/pinned_words.json); every other word follows the new take."""

from __future__ import annotations

import json

import pytest
from storyfix import write_wav

from slate import jsonio
from slate.cli import run_cli
from slate.errors import UsageError
from slate.timing import pins
from slate.timing.words import word_time


@pytest.fixture
def timed(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    assert run_cli(["timing", "--film", str(film)])[0] == 0
    return film


def _revoice(film, shot, secs):
    """A new take of another length: the replayed aligner spreads its words over the new read."""
    write_wav(film / "voice" / "takes" / f"{shot}.wav", secs,
              bursts=[(0.1 + i * 0.4, 0.3 + i * 0.4) for i in range(int(secs / 0.4))])
    code, out, err = run_cli(["timing", "--film", str(film), "--json"])
    assert code == 0, (out, err)


def test_a_pinned_word_keeps_its_delivered_time_through_a_re_voice(timed):
    before_loaves = word_time(timed, "S03", "loaves")
    before_baked = word_time(timed, "S03", "baked")
    assert before_loaves[1] and before_baked[1]
    res = pins.pin(timed, "S03", ["loaves"], why="the wall of loaves lands on it in the next shot")
    assert res["pinned"][0]["start"] == pytest.approx(before_loaves[0], abs=1e-3)
    _revoice(timed, "S03", 2.6)  # a quicker read: its words land earlier
    after_loaves, exact = word_time(timed, "S03", "loaves")
    assert exact and after_loaves == pytest.approx(before_loaves[0], abs=1e-3)  # the pinned beat didn't move
    after_baked = word_time(timed, "S03", "baked")[0]
    assert abs(after_baked - before_baked[0]) > 0.05  # an unpinned word follows the new take
    rec = jsonio.read(timed / "timing" / "vo_words.json")
    tok = next(t for t in rec["shots"]["S03"]["tokens"] if t["key"] == "loaves")
    assert tok["pinned"] and tok["exact"]  # the engines' word lookups read the same pinned time
    assert rec["pinned"][0]["w"] == "loaves"
    lock = pytest.importorskip("slate.state.lock")
    entry = lock.load(timed)["files"]["timing/timeline.json"]
    assert "timing/pinned_words.json" in entry["inputs"]  # pins shape the timeline, so changing one makes it stale


def test_only_heard_words_can_be_pinned_and_pins_can_be_let_go(timed):
    with pytest.raises(UsageError, match="wasn't heard saying"):
        pins.pin(timed, "S03", ["croissants"])
    pins.pin(timed, "S03", ["all"])
    assert len(pins.load(timed)["S03"]["words"]) == len(jsonio.read(timed / "timing" / "vo_words.json")["shots"]["S03"]
                                                        ["tokens"])
    assert pins.clear(timed, "S03", ["loaves"])["cleared"] == 1
    assert pins.clear(timed)["cleared"] >= 1 and pins.load(timed) == {}


def test_the_pin_command(timed):
    code, out, _ = run_cli(["timing", "pin", "--shot", "S04", "--words", "early", "--why", "the door opens on it",
                            "--film", str(timed), "--json"])
    assert code == 0 and json.loads(out)["pinned"][0]["w"] == "early"
    code, out, _ = run_cli(["timing", "pin", "--list", "--film", str(timed), "--json"])
    assert code == 0 and json.loads(out)["pinned"]["S04"]["why"] == "the door opens on it"
    code, _, err = run_cli(["timing", "pin", "--shot", "S04", "--film", str(timed)])
    assert code == 2 and "--words" in err
    code, out, _ = run_cli(["timing", "pin", "--clear", "--film", str(timed), "--json"])
    assert code == 0 and json.loads(out)["cleared"] == 1
