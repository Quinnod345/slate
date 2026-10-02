"""`slate timing stress`: S, where a key word's stressed syllable starts, measured from the take (L-timing-D006), and
the contact frame C = S + 2. The maths is the films' stress_onset.py, unchanged."""

from __future__ import annotations

import json
import types

import numpy as np
import pytest
from storyfix import write_wav

from slate import jsonio
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.timing import stress
from slate.voice import audio as audio_mod

# "we im|PROVED it": the word runs 0.10-0.70 s with a stop closure (silence) at 0.35-0.40 s before its stress
WORDS = [{"w": "we", "start": 0.0, "end": 0.09}, {"w": "improved", "start": 0.10, "end": 0.70},
         {"w": "it", "start": 0.75, "end": 0.90}]
BURSTS = [(0.0, 0.09), (0.10, 0.35), (0.40, 0.70), (0.75, 0.90)]


def _take(tmp_path, bursts=BURSTS, name="take.wav"):
    return write_wav(tmp_path / name, 1.0, bursts=bursts)


def _film(tmp_path, *, frame_in=30, at=0.5, bursts=BURSTS):
    """The smallest film the measurement reads: a clock, one voice cue and its take's word times."""
    film = tmp_path / "film"
    jsonio.write_atomic(film / "film.json", {"id": "stress", "language": "en"})
    jsonio.write_atomic(film / "timing" / "shots.json", {"fps_num": 30, "fps_den": 1, "shots": [
        {"id": "S01", "frame_in": 0, "frame_out": frame_in - 1, "frames": frame_in},
        {"id": "S02", "frame_in": frame_in, "frame_out": frame_in + 59, "frames": 60}]})
    write_wav(film / "voice" / "takes" / "S02.wav", 1.0, bursts=bursts)
    jsonio.write_atomic(film / "timing" / "vo_cues.json", {"cues": [{"shot": "S02", "at": at, "type": "vo",
                                                                     "file": "voice/takes/S02.wav"}]})
    jsonio.write_atomic(film / "timing" / "vo_words.json", {"shots": {"S02": {
        "take": "voice/takes/S02.wav", "line": "we improved it", "words": WORDS}}})
    return film


def test_the_stressed_syllable_starts_where_the_stop_gap_before_it_ends(tmp_path):
    x, sr = audio_mod.load(_take(tmp_path))
    found = stress.onset(x, sr, 0.10, 0.70)
    assert found["found"] and found["gaps"] == 1
    assert found["start"] == pytest.approx(0.40, abs=0.011)  # the closure ends at 0.40 s
    assert found["offset_s"] == pytest.approx(0.30, abs=0.011)
    assert found["gap"][0] == pytest.approx(0.35, abs=0.011) and found["depth_db"] < -100


def test_gap_counts_from_the_end_of_the_word_so_ex_clu_sive_takes_the_last(tmp_path):
    two = [(0.10, 0.25), (0.30, 0.50), (0.56, 0.80)]  # two closures: 0.25-0.30 and 0.50-0.56
    x, sr = audio_mod.load(_take(tmp_path, two))
    last, first = stress.onset(x, sr, 0.10, 0.80, gap=1), stress.onset(x, sr, 0.10, 0.80, gap=2)
    assert last["gaps"] == 2 and last["start"] == pytest.approx(0.56, abs=0.011)
    assert first["start"] == pytest.approx(0.30, abs=0.011)
    with pytest.raises(UsageError, match="--gap"):
        stress.onset(x, sr, 0.10, 0.80, gap=0)


def test_no_stop_gap_means_first_syllable_stress_at_the_word_start(tmp_path):
    x, sr = audio_mod.load(_take(tmp_path, [(0.10, 0.70)]))
    found = stress.onset(x, sr, 0.10, 0.70)
    assert not found["found"] and found["start"] == 0.10 and found["offset_s"] == 0.0
    assert "first-syllable stress" in found["why"]
    asked = stress.onset(x, sr, 0.10, 0.70, gap=2)  # asking for more gaps than there are falls back the same way
    assert not asked["found"] and asked["start"] == 0.10


def test_measure_answers_in_film_seconds_and_frames_on_the_sync_gates_own_path(tmp_path):
    film = _film(tmp_path, frame_in=30, at=0.5)
    res = stress.measure(film, "S02", "improved")
    # the shot starts at 1.0 s (frame 30 at 30 fps), the take at +0.5 s, the syllable 0.40 s into the take
    assert res["measured"] and res["method"] == "stop gap"
    assert res["stress_start"] == pytest.approx(1.90, abs=0.011)
    assert res["stress_end"] == pytest.approx(2.20, abs=1e-6)  # the word's end
    assert res["s_frame"] == round(res["stress_start"] * 30) and res["contact_frame"] == res["s_frame"] + 2
    assert res["beat"] == {"word": "improved", "stress_start": res["stress_start"], "stress_end": res["stress_end"]}
    assert res["word_film_s"] == [pytest.approx(1.6), pytest.approx(2.2)]
    assert res["take_sha256"] == jsonio.sha256(film / "voice" / "takes" / "S02.wav")


def test_a_word_the_take_never_says_is_refused_not_guessed(tmp_path):
    film = _film(tmp_path)
    with pytest.raises(Refused, match="isn't heard"):
        stress.measure(film, "S02", "croissants")
    with pytest.raises(UsageError, match="No shot"):
        stress.measure(film, "S99", "improved")
    with pytest.raises(UsageError, match="--occ"):
        stress.measure(film, "S02", "improved", occ=0)


def test_the_stress_command(tmp_path):
    film = _film(tmp_path)
    code, out, err = run_cli(["timing", "stress", "S02", "improved", "--film", str(film), "--json"])
    assert code == 0, err
    doc = json.loads(out)
    assert doc["beat"]["word"] == "improved" and doc["contact_frame"] == doc["s_frame"] + 2
    code, out, err = run_cli(["timing", "stress", "S02", "improved", "--film", str(film)])
    assert code == 0 and "contact frame" in out and "stress_start" in out


def test_the_maths_matches_the_films_own_tool(tmp_path, filmroot):
    """Parity with FILMROOT/pipeline/tools/stress_onset.py, read in place (exec'd from its text: nothing is written)."""
    src = filmroot / "pipeline" / "tools" / "stress_onset.py"
    if not src.is_file():
        pytest.skip("the films' stress_onset.py isn't there")
    films = types.ModuleType("films_stress_onset")
    exec(compile(src.read_text(), str(src), "exec"), films.__dict__)  # noqa: S102 - the films' own tool, read-only
    for bursts, a, b in ((BURSTS, 0.10, 0.70), ([(0.10, 0.25), (0.30, 0.50), (0.56, 0.80)], 0.10, 0.80)):
        path = _take(tmp_path, bursts, name=f"parity-{len(bursts)}.wav")
        ts, db = films.envelope(str(path), a + 0.03, b)
        smooth = np.convolve(db, np.ones(3) / 3, mode="same")
        theirs = films.gaps(ts, smooth, float(np.median(smooth)) - 16)
        x, sr = audio_mod.load(path)
        ours = stress.onset(x, sr, a, b)
        assert ours["gaps"] == len(theirs)
        assert ours["start"] == pytest.approx(theirs[-1][1], abs=1e-9)
        assert ours["gap"][0] == pytest.approx(theirs[-1][0], abs=1e-9)
