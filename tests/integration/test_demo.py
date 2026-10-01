"""`slate demo` end to end: a neutral 15-second 9:16 reel through every stage, the vendors replayed, the deliverables on
disk and the manifest saying why they aren't final.

Everything but the vendors is real: the studio voice polish and its verification, the A/B spectrogram sheet, the film
clock, a placeholder act and a kit-built act rendered by headless Chrome at 4 samples, the score and the mix, every
sound, voice and film gate (OCR on Apple Vision when Slate's ocr extra is installed), the ffmpeg masters, the review
packet and the draft delivery. It takes minutes, so it's opt-in (SLATE_TEST_INTEGRATION=1 or -m integration). It runs
in the test's own folders (tests/conftest.py's slate_home), spends nothing and sends nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from slate import demo, jsonio, paths

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None or not (paths.ENGINE_SRC / "node_modules" / "remotion").is_dir(),
                       reason="the demo renders: it needs ffmpeg and the engine's packages"),
]

VOICE_GATES = {"voice_takes", "voice_words", "voice_tails", "voice_fit", "voice_inflection", "voice_polish"}


def test_the_demo_makes_checks_and_delivers_the_reel_end_to_end(slate_home):
    proc = subprocess.run([sys.executable, "-m", "slate", "--json", "demo", "--keep"], capture_output=True, text=True,
                          timeout=3600, env={**os.environ, "BROWSER": "none"}, cwd=slate_home["tmp"])
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, proc.stderr[-4000:]
    record = json.loads(lines[-1])
    assert proc.returncode == 0 and record["ok"], (record.get("stages"), proc.stderr[-4000:])

    # every stage ran, in order, and passed; one line each on stderr (stdout is the one JSON object)
    assert [s["stage"] for s in record["stages"]] == [s.key for s in demo.STAGES]
    assert all(s["ok"] and s["seconds"] >= 0 and s["result"] for s in record["stages"])
    for s in record["stages"]:
        assert f"{s['stage']}" in proc.stderr
    assert record["replay"] is True and record["kept"] is True and record["removed"] is False

    film = Path(record["dir"])
    assert film.parent == slate_home["films"] and film.name == "slate-demo-1"
    fj = jsonio.read(film / "film.json")
    assert (fj["width"], fj["height"], fj["fps_num"], fj["fps_den"], fj["aspect"]) == (1080, 1920, 30, 1, "9:16")
    assert fj["brand"] == "demo" and fj["mode"] == "draft"

    # the studio polish reached the mix: the cue sheet and the mix play the polished takes, each verified
    cues = jsonio.read(film / "timing" / "vo_cues.json")["cues"]
    assert len(cues) == 4 and all(c["file"].endswith(".polished.wav") for c in cues)
    mix = jsonio.read(film / "sound" / "mix" / "final_mix.json")
    assert all(p["file"].endswith(".polished.wav") for p in mix["inputs"]["voice"]["placed"])
    assert mix["pass"] and mix["empty_stems"] == {"effects": "no effect stems in this film",
                                                  "ambience": "no ambience stems in this film"}
    for shot in ("D01", "D02", "D03", "D04"):
        report = jsonio.read(film / "voice" / "takes" / shot / "t01.polish.json")
        assert report["verified"]["ok"] and jsonio.validate(report, "voice_polish_report") == []
    # measured inflection and the A/B spectrogram sheet, raw against polished
    analysis = jsonio.read(film / "voice" / "analysis" / "demo-polish" / "analysis.json")
    assert len(analysis["takes"]) == 2 and (film / "voice" / "analysis" / "demo-polish" / "sheet.png").is_file()
    assert (film / "voice" / "analysis" / "demo-polish" / "spectrum.png").is_file()
    assert len(analysis["spectrum"]["takes"]) == 2 and jsonio.validate(analysis, "voice_analysis") == []

    # both acts rendered at 4 samples and registered; act 1 the placeholder (silent), act 2 kit-built with two beats
    reg = jsonio.read(film / "renders" / "registry.json")["entries"]
    assert sorted(e["act"] for e in reg) == [1, 2] and {e["samples"] for e in reg} == {4}
    assert jsonio.read(film / "timing" / "visual_events" / "act1.json")["silent"] is True
    assert len(jsonio.read(film / "timing" / "visual_events" / "act2.json")) == 2
    assert len(jsonio.read(film / "timing" / "motion" / "act2.json")) == 4

    # every gate in qa/gates.json is PASS, but for the ones the demo expects unchecked, each with its reason
    report = jsonio.read(film / "qa" / "gates.json")["gates"]
    not_pass = {name: g["status"] for name, g in report.items() if g["status"] != "PASS"}
    assert set(not_pass) <= VOICE_GATES | {"sound.reference"}, not_pass
    assert set(not_pass.values()) <= {"UNCHECKED"}
    assert {g["gate"] for g in record["not_passed"]} == set(not_pass)
    assert all(g["why"] for g in record["not_passed"])
    if demo.vision_available():  # OCR read the picture on Apple Vision
        assert report["ocr"]["status"] == "PASS" and report["readtime"]["status"] == "PASS"
        assert report["access"]["status"] == "PASS"

    # the masters, the review packet (copied to a scratch folder, nothing sent) and the draft delivery
    masters = jsonio.read(film / "masters" / "master.json")
    assert {m["kind"] for m in masters["masters"]} == {"prores_422_hq", "h264"}
    assert all(m["frames"] == 467 for m in masters["masters"])
    assert (film / "review" / "v1" / "packet.json").is_file()
    d = record["deliverables"]
    manifest = jsonio.read(film / d["manifest"])
    assert manifest["final"] is False and manifest["film"]["mode"] == "draft"
    assert any("replayed" in w for w in manifest["why_not_final"]) and len(manifest["replayed"]) >= 4
    folder = film / manifest["folder"]
    assert folder.parent.name == "drafts" and manifest["files"]
    for f in manifest["files"]:
        assert jsonio.sha256(folder / f["file"]) == f["sha256"], f["file"]
    assert {"master", "mix"} <= {f["kind"].split(":")[0] for f in manifest["files"]}
    # the run's own record, kept with the film
    assert jsonio.read(film / "qa" / "demo.json")["ok"] is True
