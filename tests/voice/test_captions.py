"""Captions: at most 2 lines of 42 characters, at most 17 characters a second, script-exact, no overlaps, and every
language rule read from the language table (no English literals in the logic)."""

import json
import re
from pathlib import Path

import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.voice import captions, lang

TABLE = lang.load("en")


def st(**kw):
    out = dict(captions.DEFAULTS)
    out.update(kw)
    return out


def words_for(line: str, start: float = 0.0, per_word: float = 0.3, gap: float = 0.05) -> list[dict]:
    out, t = [], start
    for w in line.split():
        out.append({"w": w, "start": round(t, 3), "end": round(t + per_word, 3)})
        t += per_word + gap
    return out


def test_wrap_balances_two_lines_and_refuses_what_cannot_fit():
    text = "Slate hears each word and fits every caption to the cut on time."
    lines = captions.wrap(text, st())
    assert len(lines) == 2 and all(len(x) <= 42 for x in lines)
    assert abs(len(lines[0]) - len(lines[1])) <= 12
    assert captions.wrap("short", st()) == ["short"]
    assert captions.wrap("x" * 43, st()) is None  # one unbreakable word can't fit
    assert captions.wrap(text, st(max_lines=1)) is None
    three = captions.wrap(text + " And one more clause to go.", st(max_lines=3, max_chars=32))
    assert three and len(three) <= 3 and all(len(x) <= 32 for x in three)


def test_chunks_break_at_sentences_then_clauses_then_joining_words():
    line = ("This first sentence is short. The second one runs on, with a clause, and then another clause "
            "that keeps going because it can.")
    chunks = captions.chunks_for(line, st(merge_short=False), TABLE)
    assert chunks[0] == "This first sentence is short."
    assert " ".join(chunks).split() == line.split()
    assert all(captions.wrap(c, st()) for c in chunks)
    long_clause = "The quick measurement of every word in the take matters because captions follow the words exactly"
    parts = captions.chunks_for(long_clause, st(max_chars=30, merge_short=False), TABLE)
    assert len(parts) >= 2 and any(p.split()[0] in TABLE["join_words"] for p in parts[1:])


def test_merge_short_folds_one_word_flashes():
    line = "Yes. The rest of this line carries on for a while."
    assert captions.chunks_for(line, st(merge_short=False), TABLE)[0] == "Yes."
    merged = captions.chunks_for(line, st(merge_short=True), TABLE)
    assert merged[0].startswith("Yes. The")


def _gen(lines: dict[str, tuple[int, str, list[dict]]], fps=25.0, **kw):
    shots = {sid: {"frame_in": fi} for sid, (fi, _l, _w) in lines.items()}
    words = {sid: {"line": ln, "words": ws} for sid, (_fi, ln, ws) in lines.items()}
    return captions.generate(shots, words, {sid: 0.0 for sid in lines}, fps, st(**kw), TABLE)


def test_timing_min_duration_no_overlap_and_script_exact():
    cues, problems = _gen({"A": (0, "Hi there.", words_for("Hi there.", per_word=0.2)),
                           "B": (50, "Next shot line.", words_for("Next shot line.")),
                           "C": (80, "Close behind it.", words_for("Close behind it."))})
    assert problems == []
    assert cues[0]["end"] - cues[0]["start"] == pytest.approx(1.0)  # held to the 1.0 s minimum where there's room
    assert cues[1]["end"] == pytest.approx(cues[2]["start"] - 0.04)  # never overlapping the next cue
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"] - 0.04 + 1e-9
    assert cues[1]["start"] == pytest.approx(50 / 25.0)  # frame_in / fps: the film's own clock
    # a cue squeezed below 0.8 s is reported, never hidden
    cues, problems = _gen({"A": (0, "One. Two words here.", words_for("One. Two words here.", per_word=0.1))},
                          merge_short=False)
    assert any("only" in p for p in problems)


def test_reading_speed_is_held_to_17_characters_a_second():
    fast = "Supercalifragilistic expialidocious words tumble out."
    cues, problems = _gen({"A": (0, fast, words_for(fast, per_word=0.15, gap=0.0)),
                           "B": (250, "Later line.", words_for("Later line."))}, merge_short=False)
    first = cues[0]
    assert first["cps"] <= 17.0 + 1e-6 and not any("characters a second" in p for p in problems)
    assert first["end"] - first["start"] >= len(fast) / 17.0 - 1e-9
    # no room before the next caption: the cue can't be held, and the problem is reported, never hidden
    cues, problems = _gen({"A": (0, fast, words_for(fast, per_word=0.15, gap=0.0)),
                           "B": (30, "Right after.", words_for("Right after."))}, merge_short=False)
    assert any("characters a second" in p for p in problems)
    # parity with the films' rules: no hold
    cues, problems = _gen({"A": (0, fast, words_for(fast, per_word=0.15, gap=0.0)),
                           "B": (250, "Later line.", words_for("Later line."))}, merge_short=False, max_cps=None)
    assert cues[0]["end"] - cues[0]["start"] < len(fast) / 17.0


def test_script_mismatch_is_a_problem():
    shots = {"A": {"frame_in": 0}}
    words = {"A": {"line": "Hello there.", "words": words_for("Hello there.")}}
    cues, problems = captions.generate(shots, words, {}, 25.0, st(), TABLE)
    assert problems == []
    assert captions.to_srt(cues).startswith("1\n00:00:00,400 --> ")  # the default lead places the read
    assert captions.to_vtt(cues).startswith("WEBVTT\n\n1\n00:00:00.400 --> ")


def test_language_rules_come_from_the_table(monkeypatch):
    table = {"sentence_end": ["|"], "clause_end": ["~"], "join_words": ["zz"], "join_bonus": 25}
    line = "alpha beta gamma delta| epsilon zeta eta theta iota kappa lambda mu nu~ xi omicron pi rho"
    chunks = captions.chunks_for(line, st(max_chars=30, merge_short=False), table)
    assert chunks[0] == "alpha beta gamma delta|"
    assert " ".join(chunks).split() == line.split()
    src = Path(captions.__file__).read_text()
    for literal in ('"and"', "'and'", '"to"', '"that"', "r\"(?<=[.!?])", "[.!?]"):
        assert literal not in src
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    assert "30000" not in code and "1001" not in code and "1920" not in code and "1080" not in code


def test_build_writes_srt_vtt_and_json(voice_film):
    for args in (["tts"], ["align"]):
        code, _, err = run_cli(["voice", *args, "--film", str(voice_film)])
        assert code == 0, err
    code, out, err = run_cli(["voice", "captions", "--film", str(voice_film), "--json"])
    assert code == 0, err
    res = json.loads(out)
    paths = [Path(p) for p in res["files"]]
    assert [p.name for p in paths] == ["captions.json", "demo-reel.srt", "demo-reel.vtt"]
    data = jsonio.read(paths[0])
    assert jsonio.validate(data, "captions") == []
    assert data["script_exact"] and data["problems"] == [] and data["fps_num"] == 25
    assert data["settings"]["max_chars"] == 42 and data["settings"]["max_lines"] == 2
    assert data["settings"]["max_cps"] == 17.0
    for c in data["cues"]:
        assert c["lines"] and len(c["lines"]) <= 2 and all(len(x) <= 42 for x in c["lines"])
        assert c["cps"] <= 17.0
    assert paths[1].read_text().startswith("1\n") and paths[2].read_text().startswith("WEBVTT\n\n1\n")


def test_film_settings_override_the_limits(voice_film):
    fj = jsonio.read(voice_film / "film.json")
    fj["captions"] = {"max_chars": 32, "max_cps": 15}
    s = captions.settings_for(fj, TABLE)
    assert s["max_chars"] == 32 and s["max_cps"] == 15
    assert captions.settings_for(fj, TABLE, parity=True)["max_cps"] is None
    assert captions.settings_for(fj, TABLE, parity=True)["warn_s"] == 0.8


def test_captions_need_timing(slate_home, brand):
    from voice_helpers import make_film

    film = make_film(slate_home["films"], "no-timing", timing=False)
    code, _, err = run_cli(["voice", "captions", "--film", str(film)])
    assert code == 3 and "slate timing" in err
