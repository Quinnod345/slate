"""Golden data: captions regenerated from films A, B and C's own inputs match what was delivered.

Reads the film project read-only (its shots, word timings and cue sheets) and writes only to a temp folder. The
films' rules apply unchanged (parity: no reading-speed hold); film A predates merging short flashes, B and C use it.
Tolerance: text, line breaks and shots identical; times within a microsecond; SRT and WebVTT byte for byte.
"""

import json
from pathlib import Path

import pytest

from slate import jsonio
from slate.voice import captions, lang

FILMS = [("film A", "build", False), ("film B", "build/B", True), ("film C", "build/C", True)]
TOLERANCE_S = 1e-6


def _inputs(folder: Path):
    sj = json.loads((folder / "shots.json").read_text())
    shots = {s["id"]: s for s in sj["shots"]}
    words = json.loads((folder / "vo_words.json").read_text())["shots"]
    at = {c["shot"]: c["at"] for c in json.loads((folder / "vo_cues.json").read_text())["cues"]}
    return sj, shots, words, at


@pytest.mark.parametrize("name, rel, merge", FILMS, ids=[f[0] for f in FILMS])
def test_captions_match_the_delivered_captions(filmroot, tmp_path, name, rel, merge):
    folder = filmroot / rel
    delivered = folder / "captions"
    if not (delivered / "captions.json").is_file():
        pytest.skip(f"{name}'s delivered captions aren't there")
    sj, shots, words, at = _inputs(folder)
    settings = dict(captions.DEFAULTS, **captions.PARITY, merge_short=merge)
    cues, problems = captions.generate(shots, words, at, sj["fps_num"] / sj["fps_den"], settings, lang.load("en"))
    want = json.loads((delivered / "captions.json").read_text())
    assert len(cues) == len(want)
    for got, exp in zip(cues, want):
        assert (got["shot"], got["text"], got["lines"]) == (exp["shot"], exp["text"], exp["lines"])
        assert abs(got["start"] - exp["start"]) <= TOLERANCE_S and abs(got["end"] - exp["end"]) <= TOLERANCE_S
    srt = tmp_path / "out.srt"
    srt.write_text(captions.to_srt(cues))
    vtt = tmp_path / "out.vtt"
    vtt.write_text(captions.to_vtt(cues))
    delivered_srt = next(delivered.glob("*.srt"))
    delivered_vtt = next(delivered.glob("*.vtt"))
    assert srt.read_bytes() == delivered_srt.read_bytes()
    assert vtt.read_bytes() == delivered_vtt.read_bytes()
    assert problems == []


def test_a_film_folder_rebuilds_film_a_captions(filmroot, slate_home, brand, tmp_path):
    """The full `captions.build` path, on a Slate film folder holding film A's timing (copied, never moved)."""
    from voice_helpers import film_json

    folder = filmroot / "build"
    if not (folder / "captions" / "captions.json").is_file():
        pytest.skip("film A's delivered captions aren't there")
    sj, shots, words, at = _inputs(folder)
    film = slate_home["films"] / "golden-a"
    fj = film_json("golden-a")
    fj.update({"fps_num": sj["fps_num"], "fps_den": sj["fps_den"],
               "captions": {"merge_short": False}})
    jsonio.write_atomic(film / "film.json", fj)
    lines = [{"id": s["id"], "dur": 1.0, "vo": words[s["id"]]["line"] if s["id"] in words else ""}
             for s in sj["shots"]]
    jsonio.write_atomic(film / "script" / "storyboard.json", {"acts": [{"name": "all", "shots": lines}]})
    jsonio.write_atomic(film / "timing" / "shots.json", sj)
    jsonio.write_atomic(film / "timing" / "vo_words.json", {"shots": words})
    jsonio.write_atomic(film / "timing" / "vo_cues.json", {"cues": [{"shot": k, "at": v} for k, v in at.items()]})
    out, srt, vtt = captions.build(film, parity=True)
    assert srt.read_bytes() == next((folder / "captions").glob("*.srt")).read_bytes()
    assert vtt.read_bytes() == next((folder / "captions").glob("*.vtt")).read_bytes()
    data = jsonio.read(out)
    assert data["script_exact"] and data["problems"] == []
    # Slate's own default (17 characters a second) holds fast cues longer; report how many the films exceeded
    out2, _, _ = captions.build(film)
    fast = [c for c in jsonio.read(out2)["cues"] if c["cps"] and c["cps"] > 17.0]
    assert all(any(c["text"] in p for p in jsonio.read(out2)["problems"]) for c in fast)
