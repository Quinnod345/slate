"""`slate --dry-run qa …` is a dry run: it says which gates it would run, on what, and whether the run would be queued,
then runs nothing and writes nothing. (In a live look test, `slate --dry-run qa` ran the whole-picture
analyses for two minutes outside the queue and wrote their caches into the film's qa/ folder.)"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from slate import gates
from slate.cli import run_cli
from slate.gates import GateResult

from qa_core_helpers import write_json


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file under the folder with its size and modification time."""
    out = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = Path(root) / name
            st = p.stat()
            out[str(p.relative_to(folder))] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture
def film(film_builder):
    """A two-act film with a registered render per act, a master and a mix: what the gates would read."""
    fb = film_builder(shots=[(1, 20), (2, 20)])
    reg = {"entries": []}
    for act, (fin, fout) in ((1, (0, 20)), (2, (20, 40))):
        (fb.path / "renders").mkdir(exist_ok=True)
        (fb.path / "renders" / f"act{act}.mkv").write_bytes(b"not a real render")
        reg["entries"].append({"act": act, "engine": "fixture", "file": f"renders/act{act}.mkv", "frames": [fin, fout],
                               "samples": 1, "made_at": "2026-10-01T00:00:00Z", "bundle_hash": None})
    write_json(fb.path / "renders" / "registry.json", reg)
    (fb.path / "masters").mkdir()
    (fb.path / "masters" / "qa-film-1.mov").write_bytes(b"not a real master")
    return fb.path


@pytest.fixture
def nothing_runs(monkeypatch):
    """Any gate, analysis, comparison or queue submission fails the test."""
    from slate import qa
    from slate.qa import measure

    def refuse(*_a, **_kw):
        raise AssertionError("a dry run ran something")

    monkeypatch.setattr(qa, "run", refuse)
    for name in list(qa.GATES):
        monkeypatch.setitem(qa.GATES, name, refuse)
    monkeypatch.setattr(measure, "video_psnr", refuse)
    monkeypatch.setattr(measure, "psnr", refuse)
    try:
        from slate.jobs import client

        monkeypatch.setattr(client, "submit", refuse)
    except ImportError:
        pass


def qa_dry(film: Path, *argv: str) -> tuple[int, dict]:
    code, out, err = run_cli(["--dry-run", "qa", "--film", str(film), "--json", *argv])
    assert out.strip().startswith("{"), err
    return code, json.loads(out)


def test_a_dry_run_says_which_gates_on_what_and_runs_nothing(film, nothing_runs):
    from slate import qa

    before = _snapshot(film)
    code, out = qa_dry(film)
    assert code == 0 and out["dry_run"] is True
    assert out["would_run"] == [n for n in qa.GATES]  # every gate, in film order
    assert {"ocr", "readtime", "glitch", "cuts", "motion"} <= set(out["whole_picture"])
    assert out["queued"] is False and "here" in out["where"]
    assert out["on"]["renders"] == {"1": "renders/act1.mkv", "2": "renders/act2.mkv"}
    assert out["on"]["masters"] == ["masters/qa-film-1.mov"]
    assert _snapshot(film) == before  # nothing written: no qa/ folder, no caches
    assert not (film / "qa").exists()
    code, text, _ = run_cli(["--dry-run", "qa", "--film", str(film)])
    assert code == 0 and "nothing ran, nothing written" in text and "renders/act1.mkv" in text


def test_a_dry_run_names_its_gates_and_options_and_whether_it_would_queue(film, nothing_runs):
    code, out = qa_dry(film, "motion", "--act", "1", "--no-pixels")
    assert code == 0 and out["would_run"] == ["motion"] and out["options"] == ["--act", "1", "--no-pixels"]
    code, out = qa_dry(film, "--detach", "--quick")
    assert code == 0 and out["queued"] is True and "queued job" in out["where"]
    assert "ocr" not in out["would_run"] and "ocr" in out["not_run_quick"]
    assert not (film / "qa").exists()
    # flags that don't fit the run are refused, as the real run would refuse them
    code, out = qa_dry(film, "loudness", "--captions", "c.srt")
    assert code == 2


def test_a_dry_run_psnr_compares_nothing(film, nothing_runs, tmp_path):
    a, b = tmp_path / "a.mov", tmp_path / "b.mov"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    code, out, _ = run_cli(["--dry-run", "qa", "psnr", str(a), str(b), "--a-frames", "0-9", "--b-frames", "20-29",
                            "--min", "45", "--json"])
    out = json.loads(out)
    assert code == 0 and out["dry_run"] is True and out["kind"] == "video"
    assert out["a_frames"] == [0, 9] and out["b_frames"] == [20, 29] and out["threshold_db"] == 45.0
    code, out, _ = run_cli(["--dry-run", "qa", "psnr", str(a), str(b), "--a-frames", "9-0", "--json"])
    assert code == 2  # a range that can't be is refused, dry run or not


def test_dry_run_accept_and_unpark_record_nothing(film, nothing_runs):
    gates.write_report(film, [GateResult("motion", "REVIEW", "one item", details={
        "review_items": [{"key": "speed:card", "what": "the card whips", "basis": {}}]})])
    before = _snapshot(film)
    code, out = qa_dry(film, "triage", "--accept", "motion:speed:card", "--by", "A Person", "--reason", "by design")
    assert code == 0 and out["accepted"]["dry_run"] is True and out["accepted"]["key"] == "speed:card"
    assert _snapshot(film) == before and not (film / "qa" / "accepted.json").exists()
    write_json(film / "qa" / "parked.json", {"parked": True, "why": "a REVIEW", "reviews": []})
    before = _snapshot(film)
    code, out = qa_dry(film, "unpark", "--by", "A Person")
    assert code == 0 and out["unparked"]["dry_run"] is True
    assert _snapshot(film) == before
    assert json.loads((film / "qa" / "parked.json").read_text())["parked"] is True


def test_a_dry_run_stability_scans_nothing(film, nothing_runs, monkeypatch):
    from slate.qa import stability

    def refuse(*_a, **_kw):
        raise AssertionError("a dry run scanned")

    monkeypatch.setattr(stability, "gate", refuse)
    monkeypatch.setattr(stability, "accept_baseline", refuse)
    before = _snapshot(film)
    for argv in (["qa", "stability"], ["qa", "stability", "--detach"],
                 ["qa", "stability", "baseline", "--act", "1", "--by", "A Person", "--reason", "by design"],
                 ["qa", "stability", "strip", "--act", "1", "--frame", "4"],
                 ["qa", "stability", "conformance", "--render", "--detach"]):
        code, out, err = run_cli(["--dry-run", *argv, "--film", str(film), "--json"])
        doc = json.loads(out)
        assert code == 0 and doc["dry_run"] is True, (argv, out, err)
        assert doc["action"] == (argv[2] if len(argv) > 2 and not argv[2].startswith("--") else "run")
        assert doc["queued"] is ("--detach" in argv), (argv, doc)
    assert _snapshot(film) == before
