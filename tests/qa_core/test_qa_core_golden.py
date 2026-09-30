"""Golden data: the generalized gates rerun on the delivered films must agree with what the films' own tools said.

The film project is read-only: each test builds a small film folder in a temp dir (JSON only) whose records point at
the delivered media by absolute path. Nothing is written into the film project. The tests skip on a machine without
it, and skip a check whose delivered file has since moved.
"""

import json
from pathlib import Path

import pytest

from slate.qa.checks import audio as audio_gates
from slate.qa.checks import picture, structure, sync, text
from slate.qa.film import EVENT_KINDS

from qa_core_helpers import write_json


def _build(filmroot: Path, name: str) -> Path:
    return filmroot / "build" if name == "A" else filmroot / "build" / name


def _film(tmp: Path, name: str, shots: dict, **extra) -> Path:
    f = tmp / f"golden-{name}"
    write_json(f / "film.json", {"id": f"golden-{name}", "fps_num": shots["fps_num"], "fps_den": shots["fps_den"],
                                 "width": shots["width"], "height": shots["height"], "brand": "golden",
                                 "mode": "supervised", **extra})
    write_json(f / "timing" / "shots.json", shots)
    return f


def _copy_events(src: Path, dst: Path, skip: tuple[int, ...] = ()) -> None:
    """The act files, with the films' legacy sound kinds mapped onto the contract's (a data migration, not a fix)."""
    for p in sorted((src / "visual_events").glob("act*.json")):
        if int(p.stem[3:]) in skip:
            continue
        events = json.loads(p.read_text())
        for e in events:
            if e.get("kind") not in EVENT_KINDS:
                e["kind"] = "character"
        write_json(dst / "timing" / "visual_events" / p.name, events)


@pytest.mark.parametrize("name", ["A", "B", "C"])
def test_golden_events_complete_names_exactly_the_acts_without_beats(filmroot, tmp_path, name):
    b = _build(filmroot, name)
    shots = json.loads((b / "shots.json").read_text())
    f = _film(tmp_path, name, shots)
    _copy_events(b, f)
    have = {int(p.stem[3:]) for p in (b / "visual_events").glob("act*.json")}
    missing = [a["index"] for a in shots["acts"] if a["index"] not in have]
    r = structure.events_complete(f)
    assert r.details["missing"] == missing
    if missing:
        assert r.status == "FAIL"


def test_golden_film_c_without_acts_1_and_2_fails_events_and_sync(filmroot, tmp_path):
    """The state the plan describes: C's acts 1 and 2 without beats while its sync record passed."""
    b = _build(filmroot, "C")
    shots = json.loads((b / "shots.json").read_text())
    f = _film(tmp_path, "C", shots)
    _copy_events(b, f, skip=(1, 2))
    frame_ms = 1000 * shots["fps_den"] / shots["fps_num"]
    write_json(f / "sound" / "verify.json", {"sync": {"frame_ms": round(frame_ms, 2), "anchors": 300,
                                                      "max_abs_delta_ms": 4.0, "over_one_frame": [], "pass": True}})
    ev = structure.events_complete(f)
    assert ev.status == "FAIL" and ev.details["missing"] == [1, 2]
    s = sync.sync(f)
    assert s.status == "FAIL"
    cov = next(c for c in s.details["checks"] if c["check"] == "coverage")
    assert cov["missing"] == [1, 2]


def test_golden_boundary_reproduces_film_a_delivered_joins(filmroot, tmp_path):
    """boundary_check.py measured film A's joins (build/qa/boundaries.json). The two joins whose renders are older
    than that report must come out the same, number for number."""
    b = _build(filmroot, "A")
    report = b / "qa" / "boundaries.json"
    renders = json.loads((b / "renders" / "r3f" / "renders.json").read_text())["renders"][:3]
    files = [filmroot / r["file"] for r in renders]
    if not report.is_file() or not all(p.is_file() for p in files):
        pytest.skip("film A's renders or its boundary report have moved")
    if any(p.stat().st_mtime > report.stat().st_mtime for p in files):
        pytest.skip("a render was redone after the boundary report")
    shots = json.loads((b / "shots.json").read_text())
    end = renders[-1]["frame_out"]
    shots["shots"] = [s for s in shots["shots"] if s["frame_in"] < end]
    shots["total_frames"] = end
    acts = []
    for i, r in enumerate(renders, start=1):  # A's renders span its acts differently: acts here are render spans
        ids = [s["id"] for s in shots["shots"] if r["frame_in"] <= s["frame_in"] < r["frame_out"]]
        for s in shots["shots"]:
            if s["id"] in ids:
                s["act"] = i
        acts.append({"index": i, "name": r["comp"], "shots": ids})
    shots["acts"] = acts
    f = _film(tmp_path, "A", shots)
    write_json(f / "renders" / "registry.json", {"entries": [
        {"act": i, "engine": "remotion", "file": str(p), "frames": [r["frame_in"], r["frame_out"]]}
        for i, (r, p) in enumerate(zip(renders, files), start=1)]})
    r = picture.boundary(f)
    delivered = {j["film_frame"]: j for j in json.loads(report.read_text())}
    assert len(r.details["joins"]) == 2
    for j in r.details["joins"]:
        d = delivered[j["film_frame"]]
        assert j["jump"] == pytest.approx(d["jump"], abs=0.002)
        assert j["largest_step"] == pytest.approx(d["largest_step_either_side"], abs=0.002)
        assert j["steps"] == pytest.approx(d["steps"], abs=0.002)
    assert r.status == "PASS"


def _audio_film(filmroot, tmp_path, name, stems):
    a = _build(filmroot, name) / "audio"
    paths = [a / "final_mix.wav", *[a / "stems" / f"{s}.wav" for s in stems]]
    if not all(p.is_file() for p in paths):
        pytest.skip(f"film {name}'s mix or stems have moved")
    shots = json.loads((_build(filmroot, name) / "shots.json").read_text())
    f = _film(tmp_path, name, shots, targets={"lufs": -14.0, "true_peak": -1.0})
    write_json(f / "sound" / "mix.json", {"mix": str(a / "final_mix.wav"),
                                          "stems": {s: str(a / "stems" / f"{s}.wav") for s in stems}})
    return f, a


def test_golden_film_b_silent_ambience_stem_now_fails(filmroot, tmp_path):
    """Film B's ambience stem is digital silence. The films' validator passed it (−70.0 is not < −70)."""
    f, _a = _audio_film(filmroot, tmp_path, "B", ["ambience"])
    r = audio_gates.silent_stems(f)
    if r.details.get("silent") != ["ambience"]:
        pytest.skip("film B's ambience stem has been redone since; nothing to reproduce")
    assert r.status == "FAIL"
    assert "-70.0 LUFS" in r.evidence[0] and "digital silence" in r.evidence[0]


def test_golden_film_a_loudness_and_stems_match_its_mix_records(filmroot, tmp_path):
    f, a = _audio_film(filmroot, tmp_path, "A", ["dialogue", "effects", "ambience", "music"])
    record = json.loads((a / "final_mix.json").read_text())
    verify = json.loads((a / "verify.json").read_text())["loudness"]
    loud = audio_gates.loudness(f)
    assert loud.details["integrated_lufs"] == pytest.approx(record["final_lufs"], abs=0.05)
    assert loud.details["true_peak_dbtp"] == pytest.approx(record["final_true_peak_dbtp"], abs=0.05)
    assert loud.status == "PASS"
    assert audio_gates.bit_depth(f).status == "PASS"
    summed = audio_gates.stems_sum(f)
    assert summed.details["max_abs"] == pytest.approx(verify["stems_sum_vs_mix_max_abs"], abs=1.2e-7)
    assert summed.status == "PASS"


def test_golden_film_a_captions_are_the_script_and_the_new_cps_rule_bites(filmroot, tmp_path):
    """captions.py found 0 problems against the booth script; the word-for-word check must agree. The 17
    characters-a-second rule is new, so the cues over it are exactly the ones counted here from the file."""
    b = _build(filmroot, "A")
    shots = json.loads((b / "shots.json").read_text())
    f = _film(tmp_path, "A", shots)
    write_json(f / "timing" / "vo_words.json", json.loads((b / "vo_words.json").read_text()))
    cues = json.loads((b / "captions" / "captions.json").read_text())
    write_json(f / "voice" / "captions" / "captions.json", cues)
    r = text.captions(f)
    script = next(c for c in r.details["checks"] if c["check"] == "script")
    assert script["status"] == "PASS"
    over = sum(1 for c in cues if len(" ".join(x.strip() for x in c["lines"])) / (c["end"] - c["start"]) > 17)
    cps_problems = [e for e in r.evidence if "characters a second" in e]
    assert len(cps_problems) == over
