"""What a brand-pack edit makes stale. Each stage records the blocks of brand.json it reads (the script its truth and
duration scale, the timing its eases), so approving a reference or editing the look leaves a film's script and timing
fresh, while a change to a block a stage reads still makes that stage stale. A lock recorded before this (the whole
brand.json as one input) keeps comparing the whole file until its stage runs again.

A live look test found it: one corrected source pin in the pack, and `slate brand approve`, made every film on
the brand stale down to its locked timing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from storyfix import write_json

from slate import gates, jsonio
from slate.cli import run_cli
from slate.state import lock
from slate.timing import align as aligner

SCRIPT_KEYS = {"brand:crumb#truth", "brand:crumb#motion.durations", "brand:crumb#motion.duration_scale",
               "brand:crumb#motion.tokens"}
TIMING_FILES = ("timing/shots.json", "timing/timeline.json", "timing/vo_cues.json", "timing/vo_words.json")


@pytest.fixture(autouse=True)
def never_run_whisper(monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("a test tried to run the whisper CLI")

    monkeypatch.setattr(aligner, "run_whisper", refuse)


@pytest.fixture
def timed(make_film):
    """A voiced film with its storyboard and timing built, on the crumb brand."""
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    code, out, err = run_cli(["timing", "--film", str(film), "--json"])
    assert code == 0, out + err
    return film


def _brand_json(film: Path) -> Path:
    return film.parent.parent / "brands" / "crumb" / "brand.json"


def _edit_brand(film: Path, change) -> None:
    path = _brand_json(film)
    doc = json.loads(path.read_text())
    change(doc)
    write_json(path, doc)


def _stale(film: Path) -> dict[str, list[str]]:
    """Every script and timing file that would have to be made again, with why."""
    out = {}
    for rel in ("script/storyboard.json", "script/SCRIPT.md", *TIMING_FILES):
        x = lock.explain(film, rel)
        if x["state"] not in ("fresh", "locked"):
            out[rel] = x["reasons"]
    return out


def _stale_script_gates(film: Path) -> list[str]:
    return sorted(n for n, r in gates.load_report(film).items() if n.startswith("script.") and r.status == "STALE")


def test_each_stage_records_the_brand_blocks_it_reads(timed):
    board = lock.entry(timed, "script/storyboard.json")["inputs"]
    assert SCRIPT_KEYS <= set(board) and "brand:crumb" not in board
    assert not any(k.startswith("brand:crumb#motion.eases") for k in board)  # the script never reads the eases
    for rel in TIMING_FILES:
        keys = {k for k in lock.entry(timed, rel)["inputs"] if k.startswith("brand:")}
        assert keys == {"brand:crumb#motion.eases"}, (rel, keys)
    # the script gates are stamped with the same blocks, not the whole pack
    for name, result in gates.load_report(timed).items():
        if name.startswith("script."):
            stamped = set(result.inputs or {})
            assert not any(k.endswith("brand.json") for k in stamped), (name, stamped)
            assert SCRIPT_KEYS <= stamped, (name, stamped)


def test_approving_a_reference_or_editing_the_look_leaves_script_and_timing_fresh(timed):
    assert _stale(timed) == {}

    def approve_and_restyle(doc):
        doc["references"]["truth"] = {"file": "references/truth.md", "sha256": "0" * 64, "approved_by": "A Person",
                                      "approved_at": "2026-10-01T12:00:00+01:00", "covers": ["truth"],
                                      "covers_sha256": "1" * 64, "method": "slate brand approve"}
        doc["palette"]["ink"] = "#101010"
        doc["type"] = {"families": [{"role": "sans", "family": "Some Sans", "source": "installed",
                                     "licence": "made up for this test"}]}
        doc["look"] = {"id": "flat"}
        doc["motion"]["overshoot"] = {"allowed": ["swift"], "max": 1.02}  # the look's motion law, not its eases
        doc["motion"]["signature"] = {"name": "rise"}

    _edit_brand(timed, approve_and_restyle)
    assert _stale(timed) == {}
    assert _stale_script_gates(timed) == []


def test_a_change_to_what_the_script_reads_still_makes_it_stale(timed):
    original = _brand_json(timed).read_text()
    _edit_brand(timed, lambda d: d["truth"]["banned_words"].append("artisanal"))
    stale = _stale(timed)
    assert "input brand:crumb#truth changed" in stale["script/storyboard.json"]
    assert "input script/storyboard.json is stale" in stale["timing/shots.json"]  # timing follows its storyboard
    assert "script.banned_words" in _stale_script_gates(timed)
    _brand_json(timed).write_text(original)
    assert _stale(timed) == {}
    _edit_brand(timed, lambda d: d["motion"]["durations"].update(base=0.6))  # the read-time gate's entrance scale
    assert "input brand:crumb#motion.durations changed" in _stale(timed)["script/storyboard.json"]


def test_a_change_to_the_eases_makes_the_timing_stale_and_not_the_script(timed):
    _edit_brand(timed, lambda d: d["motion"]["eases"].update(settle=[0.25, 0.8, 0.3, 1.0]))
    stale = _stale(timed)
    assert "script/storyboard.json" not in stale and _stale_script_gates(timed) == []
    for rel in TIMING_FILES:
        assert stale[rel] == ["input brand:crumb#motion.eases changed"], (rel, stale.get(rel))


def test_a_lock_that_recorded_the_whole_pack_compares_the_whole_file_until_rebuilt(timed):
    """An old lock (made before the blocks were recorded) names `brand:crumb`: any pack edit still reads as a change
    for it, as it did, until `slate script build` and `slate timing` record the blocks."""
    for rel in ("script/storyboard.json", "timing/shots.json"):
        e = lock.entry(timed, rel)
        old = {k: v for k, v in e["inputs"].items() if not k.startswith("brand:")}
        old["brand:crumb"] = jsonio.sha256(_brand_json(timed))
        lock.record(timed, rel, producer=e["producer"], inputs=old)
    assert _stale(timed) == {}
    _edit_brand(timed, lambda d: d["references"].update(voice={"file": "references/voice.wav"}))
    stale = _stale(timed)
    assert "input brand:crumb changed" in stale["script/storyboard.json"]
    assert "input brand:crumb changed" in stale["timing/shots.json"]
    assert run_cli(["script", "build", "--film", str(timed)])[0] == 0
    assert run_cli(["timing", "--film", str(timed)])[0] == 0
    assert _stale(timed) == {}
    _edit_brand(timed, lambda d: d["references"].update(sound={"file": "references/sound.wav"}))
    assert _stale(timed) == {}
