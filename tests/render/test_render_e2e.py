"""The three engines together, end to end through `slate` itself, on a tiny neutral film (engine/tests/core/fixtures).

Act 1 is built with the kit (Remotion): `slate render` drives it through slate.engine.bridge (one bundle, lossless
chunks, a proven join into ProRes 422 HQ, the registry, the motion trace refreshed after the render), then
`slate emit-events` and `slate check` hand it in. Act 2 is the open web renderer's template, scaffolded through the
bridge and rendered with `slate render --engine web`. Both acts are 10 frames at 4 samples, 320 x 180.

Real headless Chrome, webpack and ffmpeg, so about a minute on a quiet Mac: opt in with SLATE_TEST_RENDER=1
(`SLATE_TEST_RENDER=1 pytest tests/render/test_render_e2e.py`). Nothing leaves this Mac and nothing is spent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "engine" / "tests" / "core" / "fixtures"

pytestmark = [
    pytest.mark.render,
    pytest.mark.skipif(os.environ.get("SLATE_TEST_RENDER") != "1",
                       reason="real renders (headless Chrome, ffmpeg): opt in with SLATE_TEST_RENDER=1"),
    pytest.mark.skipif(not (REPO / "engine" / "node_modules" / "remotion").is_dir() or shutil.which("ffmpeg") is None,
                       reason="the engine's packages or ffmpeg aren't installed"),
]


def _slate(*args: str, cwd: Path) -> tuple[int, dict, str]:
    proc = subprocess.run([sys.executable, "-m", "slate", "--json", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=900, env={**os.environ, "BROWSER": "none"})
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return proc.returncode, (json.loads(lines[-1]) if lines else {}), proc.stderr


def _stream(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets,codec_name,profile,pix_fmt,width,height,r_frame_rate,color_primaries",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]


@pytest.fixture
def kit_film(slate_home) -> Path:
    film = slate_home["films"] / "kit-e2e"
    shutil.copytree(FIXTURES / "kit-film", film)
    shutil.copytree(FIXTURES / "kit-brand", slate_home["brands"] / "kit-e2e-brand")
    return film


def test_slate_renders_hands_in_and_emits_a_kit_act_and_a_web_act(kit_film):
    film = kit_film

    # ---- act 1, built with the kit: `slate render` drives the Remotion engine through the bridge
    code, out, err = _slate("--film", str(film), "render", "--act", "1", "--samples", "4", cwd=film)
    assert code == 0, (out, err[-3000:])
    [r1] = out["renders"]
    assert r1["registered"] and r1["frame_count"] == 10 and r1["proof"]["status"] == "PASS"
    assert r1["trace"] == {"written": True, "file": "timing/motion/act1.json", "entries": 2}
    gates = {g["gate"]: g["status"] for g in out["gates"]}
    assert gates == {"render.act1.frames": "PASS", "render.act1.fonts": "PASS"}
    entries = {e["act"]: e for e in json.loads((film / "renders" / "registry.json").read_text())["entries"]}
    assert entries[1]["engine"] == "remotion" and entries[1]["samples"] == 4 and entries[1]["frames"] == [0, 10]
    stream = _stream(film / entries[1]["file"])
    assert stream == {**stream, "codec_name": "prores", "profile": "HQ", "pix_fmt": "yuv422p10le", "width": 320,
                      "height": 180, "r_frame_rate": "25/1", "color_primaries": "bt709", "nb_read_packets": "10"}
    # the bridge wrote what '@film/data' needs to hear the reads the way slate.timing does
    assert json.loads((film / ".slate" / "lang.json").read_text())["keep"]
    assert ["sixty-four"] in json.loads((film / ".slate" / "spoken.json").read_text())["64"]

    # ---- the hand-in: its beats and motion trace, then the check
    code, out, err = _slate("--film", str(film), "emit-events", "--act", "1", cwd=film)
    assert code == 0, (out, err[-3000:])
    [a1] = out["acts"]
    assert a1["events"] == 2 and a1["motion"] == {"written": True, "file": "timing/motion/act1.json", "entries": 2}
    code, out, err = _slate("--film", str(film), "check", "1", cwd=film)
    assert code == 0, (out, err[-3000:])
    statuses = {g["gate"]: g["status"] for g in out["gates"]}
    assert set(statuses.values()) == {"PASS"}, statuses
    assert "handin.act1.word_misses" in statuses and "handin.act1.banned_words" in statuses

    # the render's own gates are kept in qa/gates.json, tied to the render they judged
    report = json.loads((film / "qa" / "gates.json").read_text())["gates"]
    assert report["render.act1.frames"]["status"] == "PASS" and report["render.act1.fonts"]["status"] == "PASS"
    assert report["render.act1.fonts"]["inputs"] == {entries[1]["file"]: entries[1]["sha256"]}

    # ---- act 2, the open web renderer's template: started with `slate engine scaffold`, rendered by `slate render`
    code, out, err = _slate("--film", str(film), "engine", "scaffold", "--act", "2", "--engine", "web", cwd=film)
    assert code == 0, (out, err[-3000:])
    assert out["scaffolded"] is True and (film / "scenes" / "act2" / "web" / "scene.ts").is_file()
    code, out, err = _slate("--film", str(film), "render", "--act", "2", "--engine", "web", "--samples", "4", cwd=film)
    assert code == 0, (out, err[-3000:])
    [r2] = out["renders"]
    assert r2["registered"] and r2["frame_count"] == 10
    assert r2["trace"]["written"] is True  # the web engine traces as it renders; slate records it
    gates = {g["gate"]: g["status"] for g in out["gates"]}
    assert gates["render.act2.frames"] == "PASS" and gates["render.act2.web.network"] == "PASS"
    entries = {e["act"]: e for e in json.loads((film / "renders" / "registry.json").read_text())["entries"]}
    assert entries[2]["engine"] == "web" and entries[2]["frames"] == [10, 20]
    assert _stream(film / entries[2]["file"])["nb_read_packets"] == "10"
    web_trace = json.loads((film / "timing" / "motion" / "act2.json").read_text())
    assert {row["id"] for row in web_trace} == {"template.object-turn", "template.line-in.opacity", "template.line-in.y"}

    # ---- a dry run reads both acts (the web act from its page) and writes nothing; then one emit for the whole film
    code, out, err = _slate("--film", str(film), "emit-events", "--dry-run", cwd=film)
    assert code == 0, (out, err[-3000:])
    assert {a["act"]: a["written"] for a in out["acts"]} == {1: False, 2: False} and out["skipped"] == []
    assert not (film / "timing" / "visual_events" / "act2.json").exists()
    code, out, err = _slate("--film", str(film), "emit-events", cwd=film)
    assert code == 0, (out, err[-3000:])
    by_act = {a["act"]: a for a in out["acts"]}
    assert by_act[1]["events"] == 2 and by_act[2]["engine"] == "web"
    assert json.loads((film / "timing" / "visual_events" / "act2.json").read_text())[0]["id"] == "template.line-lands"
    # the page's Chrome kept its profile under Slate's data folder, and it went with the browser
    profile = Path(by_act[2]["browser"]["profile"])
    assert profile.parent == Path(os.environ["SLATE_DATA"]) / "cache" / "chrome" and not profile.exists()

    # ---- the web act's hand-in, through the open web renderer, and every act at once
    code, out, err = _slate("--film", str(film), "check", "2", cwd=film)
    assert code == 0, (out, err[-3000:])
    statuses = {g["gate"]: g["status"] for g in out["gates"]}
    assert set(statuses) == {f"handin.act2.{n}" for n in ("typecheck", "page", "word_misses", "kit_problems",
                                                          "network", "fonts", "banned_words", "events", "contracts")}
    assert set(statuses.values()) == {"PASS"}, statuses
    code, out, err = _slate("--film", str(film), "check", "all", cwd=film)
    assert code == 0, (out, err[-3000:])
    assert out["acts"] == [1, 2] and out["summary"]["status"] == "PASS"

    # ---- a web still: one frame of the web renderer's range, scaled like the Remotion engine's
    code, out, err = _slate("--film", str(film), "render", "stills", "--engine", "web", "--frames", "15", "--scale",
                            "0.5", cwd=film)
    assert code == 0, (out, err[-3000:])
    [still] = out["stills"]
    assert (still["act"], still["frame"], still["film_frame"]) == (2, 5, 15)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
                            still["file"]], capture_output=True, text=True, check=True)
    assert json.loads(probe.stdout)["streams"][0] == {"width": 160, "height": 90}

    # nothing left behind: no bundle in the cache, no chunk folders
    bundles = Path(os.environ["SLATE_DATA"]) / "cache" / "bundles"
    assert not bundles.is_dir() or not [p for p in bundles.iterdir() if not p.name.startswith(".")]
    assert not (film / "renders" / "chunks").exists()
