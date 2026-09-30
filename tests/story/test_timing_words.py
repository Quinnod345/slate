"""Word lookups: alignment, tokens, and word_time, which never raises and records every miss (P062, P084)."""

from __future__ import annotations

import json

import pytest
from storyfix import write_json

from slate.cli import run_cli
from slate.timing import words as W
from slate.timing.words import align, estimate, find, resolve_tokens, word_time


def heard(text: str, step: float = 0.5) -> list[dict]:
    return [{"w": w, "start": round(i * step, 3), "end": round(i * step + step * 0.8, 3)} for i, w in enumerate(text.split())]


# --- alignment --------------------------------------------------------------------------------------------------------------

def test_the_ampersand_lands_on_the_and_that_was_said_for_it():
    line = "Say you're Sam Reed, and you run Salt & Stone."
    words = heard("Say you're Sam Reed and you run Salt and Stone.")
    toks = {(t["t"], t["occ"]): t for t in resolve_tokens(line, words, (0, 5))}
    assert toks[("and", 1)]["i"] == 4 and toks[("&", 1)]["i"] == 8  # the second "and", not the first
    assert all(t["exact"] for t in toks.values())


def test_digits_heard_for_spelled_words_and_the_other_way_round():
    ms = align(["in", "twelve", "months"], heard("in 12 months"))
    assert [(m.s0, m.s1, m.h0, m.h1) for m in ms] == [(0, 1, 0, 1), (1, 2, 1, 2), (2, 3, 2, 3)]
    ms = align(["at", "664."], heard("at six sixty-four."))
    assert [(m.s0, m.s1, m.h0, m.h1) for m in ms] == [(0, 1, 0, 1), (1, 2, 1, 3)]
    ms = align(["at", "six", "sixty-four."], heard("at 664."))
    assert ms[-1].split and (ms[-1].s0, ms[-1].s1, ms[-1].h0) == (1, 3, 1)


def test_a_number_whisper_wrote_in_pieces_still_aligns():
    toks = resolve_tokens("more than twenty-five hundred.", heard("more than 2 ,500,"), (0, 3))
    assert [t["exact"] for t in toks] == [True, True, True, True]
    toks = resolve_tokens("forty-five percent of us", heard("45 % of us"), (0, 3))
    assert [(t["t"], t["i"]) for t in toks] == [("forty-five", 0), ("percent", 1), ("of", 2), ("us", 3)]


def test_unheard_words_are_estimated_between_their_neighbours_and_marked():
    toks = resolve_tokens("the warm bread is here", heard("the bread is here"), (0, 2))
    warm = next(t for t in toks if t["t"] == "warm")
    assert not warm["exact"]
    assert 0.4 <= warm["start"] <= warm["end"] <= 0.5  # between "the" (ends 0.4) and "bread" (starts 0.5)


# --- find (heard words, spoken forms) ------------------------------------------------------------------------------------------

def test_find_counts_occurrences_and_skips_misses():
    words = heard("Same revenue. Same growth. Same story")
    assert find(words, "same", 3) == (4, 5)
    assert find(words, "same", 4) is None
    assert find(words, "zebra") is None
    assert find(words, "same", 0) is None
    assert find([], "same") is None


# --- estimates (film C's cword rule) ------------------------------------------------------------------------------------------

def test_estimate_spreads_the_line_across_the_read():
    line = "one two three four"
    assert estimate(line, "one", 1, "start", (1.0, 3.0)) == 1.0
    assert estimate(line, "three", 1, "start", (1.0, 3.0)) == pytest.approx(1.0 + 2.0 * 8 / 18)
    assert estimate(line, "three", 1, "end", (1.0, 3.0)) == pytest.approx(1.0 + 2.0 * 13 / 18)
    assert estimate(line, "absent", 1, "end", (1.0, 3.0)) == 3.0


# --- word_time on a film ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def timed_film(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    code, out, _ = run_cli(["timing", "--film", str(film), "--json"])
    assert code == 0, out
    return film


def film_seconds(film, shot_id, word_index, which="start"):
    shots = json.loads((film / "timing" / "shots.json").read_text())
    shot = next(s for s in shots["shots"] if s["id"] == shot_id)
    cue = next(c for c in json.loads((film / "timing" / "vo_cues.json").read_text())["cues"] if c["shot"] == shot_id)
    rec = json.loads((film / "timing" / "vo_words.json").read_text())["shots"][shot_id]
    return shot["frame_in"] * shots["fps_den"] / shots["fps_num"] + cue["at"] + rec["words"][word_index][which]


def test_word_time_is_exact_in_film_seconds(timed_film):
    t, exact = word_time(timed_film, "S02", "ovens")
    assert exact and t == pytest.approx(film_seconds(timed_film, "S02", 5))
    t, exact = word_time(timed_film, "S03", "120")  # the line says "a hundred and twenty"
    assert exact and t == pytest.approx(film_seconds(timed_film, "S03", 0))
    t, exact = word_time(timed_film, "S02", "up", which="end")
    assert exact and t == pytest.approx(film_seconds(timed_film, "S02", 7, "end"))
    assert W.load_misses(timed_film) == []


def test_a_miss_returns_an_estimate_and_is_recorded(timed_film):
    t, exact = word_time(timed_film, "S02", "zebra")
    assert not exact and t == pytest.approx(film_seconds(timed_film, "S02", 0))  # not in the line: the read's start
    t2, exact2 = word_time(timed_film, "S02", "wake", occ=2)
    assert not exact2
    misses = W.load_misses(timed_film)
    assert [(m["shot"], m["word"], m["occ"]) for m in misses] == [("S02", "zebra", 1), ("S02", "wake", 2)]
    word_time(timed_film, "S02", "zebra")
    assert len(W.load_misses(timed_film)) == 2  # the same miss is listed once
    code, out, _ = run_cli(["timing", "misses", "--film", str(timed_film), "--json"])
    assert code == 1 and json.loads(out)["count"] == 2


def test_word_time_never_raises(timed_film, tmp_path):
    for args in [(tmp_path / "nowhere", "S01", "x"), (timed_film, "S99", "x"), (timed_film, "S02", ""),
                 (timed_film, "S02", "ovens", -3), (timed_film, "S02", "ovens", "first"),
                 (timed_film, None, None)]:
        t, exact = word_time(*args)
        assert isinstance(t, float) and exact is False
    (timed_film / "timing" / "vo_words.json").write_text("{ not json")
    t, exact = word_time(timed_film, "S02", "ovens")
    assert isinstance(t, float) and exact is False


def test_before_voicing_every_lookup_is_an_estimate_across_the_shot(make_film):
    film = make_film()
    run_cli(["script", "build", "--film", str(film)])
    run_cli(["timing", "--film", str(film)])
    t, exact = word_time(film, "S02", "Every")
    shots = json.loads((film / "timing" / "shots.json").read_text())["shots"]
    s02 = next(s for s in shots if s["id"] == "S02")
    assert not exact and t == pytest.approx(s02["frame_in"] / 25)  # "Every" opens the line: the shot's start


def test_a_timing_rebuild_starts_a_fresh_miss_list(timed_film):
    word_time(timed_film, "S02", "zebra")
    assert W.load_misses(timed_film)
    assert run_cli(["timing", "--film", str(timed_film)])[0] == 0
    assert W.load_misses(timed_film) == []
    doc = json.loads(W.misses_path(timed_film).read_text())
    assert doc["generated"].startswith("do not edit by hand")


def test_timeline_anchor_misses_land_in_the_miss_list(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    script = json.loads((film / "script" / "script.json").read_text())
    script["acts"][0]["shots"][1]["events"] = [{"id": "door", "word": "loaves", "land": "swift:0.5"}]
    write_json(film / "script" / "script.json", script)
    run_cli(["script", "build", "--from-takes", "--film", str(film)])
    code, out, _ = run_cli(["timing", "--film", str(film), "--json"])
    gate = next(g for g in json.loads(out)["gates"] if g["gate"] == "timing.events")
    assert code == 1 and gate["status"] == "FAIL" and "'loaves'" in gate["summary"]
    assert [m["word"] for m in W.load_misses(film)] == ["loaves"]
    tl = json.loads((film / "timing" / "timeline.json").read_text())
    assert tl["shots"]["S02"][0]["estimated"] is True


def test_misses_command_passes_when_empty(timed_film):
    code, out, _ = run_cli(["timing", "misses", "--film", str(timed_film), "--json"])
    assert code == 0 and json.loads(out) == {"status": "PASS", "misses": [], "count": 0}


def test_unvoiced_takes_do_not_count_as_heard(make_film):
    film = make_film(takes={"S02": 3.1})
    run_cli(["script", "build", "--film", str(film)])
    code, out, _ = run_cli(["timing", "--film", str(film), "--json"])
    gates = {g["gate"]: g for g in json.loads(out)["gates"]}
    assert gates["timing.words"]["status"] == "UNCHECKED" and gates["timing.reads"]["status"] == "UNCHECKED"
    assert set(json.loads((film / "timing" / "vo_words.json").read_text())["shots"]) == {"S02"}
