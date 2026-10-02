"""A freshly scaffolded web act keeps the motion law, in a short act and in a long one.

`slate engine scaffold --engine web` starts every web act from engine/web/template. Its moves are sized to the act and
the frame: each lasts a step of the brand's duration scale that fits the act (the kit's durFit) and travels only as far
as the speed limits allow at the film's size (maxTravel). Here the template is scaffolded into the web fixture film in
two shapes, `slate emit-events` reads its page in headless Chrome (no render) and writes the act's trace, and the
hand-in's motion gate (slate.qa.motion.handin_gate, which `slate check` runs as handin.act<N>.motion) judges it:

- 320 x 180 at 25 fps, one 10-frame act: the end-to-end test's shape, shorter than the brand's base entrance plus its
  tick, where the moves take shorter steps of the scale and a shallower drop;
- 1920 x 1080 at 30 fps, one 4-second act: a normal act, where the moves are the brand's own steps, in full.

Real headless Chrome: opt in with SLATE_TEST_RENDER=1. Nothing leaves this Mac.
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
FIXTURES = REPO / "engine" / "tests" / "web" / "fixtures"

pytestmark = [
    pytest.mark.render,
    pytest.mark.skipif(os.environ.get("SLATE_TEST_RENDER") != "1",
                       reason="headless Chrome reads the page: opt in with SLATE_TEST_RENDER=1"),
    pytest.mark.skipif(not (REPO / "engine" / "node_modules" / "puppeteer").is_dir(),
                       reason="the engine's packages aren't installed"),
]


def _film(home: dict, *, size: tuple[int, int], fps: int, frames: int) -> Path:
    """The web fixture film reshaped to one act of `frames` frames at this size and rate, its brand in the store."""
    film = home["films"] / "web-template"
    shutil.copytree(FIXTURES / "film", film)
    doc = json.loads((film / "film.json").read_text())
    doc.update(width=size[0], height=size[1], fps_num=fps, fps_den=1)
    (film / "film.json").write_text(json.dumps(doc, indent=1))
    shots = {"generated": "the web fixture film, reshaped by the template motion test", "fps_num": fps, "fps_den": 1,
             "width": size[0], "height": size[1], "total_frames": frames,
             "acts": [{"index": 1, "name": "Template", "shots": ["W01"]}],
             "shots": [{"id": "W01", "act": 1, "frame_in": 0, "frame_out": frames, "frames": frames,
                        "sec_in": 0.0, "sec_out": frames / fps, "vo": "", "on_screen": ""}]}
    (film / "timing" / "shots.json").write_text(json.dumps(shots, indent=1))
    shutil.copytree(FIXTURES / "brand", home["brands"] / "web-fixture")
    return film


def _slate(film: Path, *args: str) -> dict:
    proc = subprocess.run([sys.executable, "-m", "slate", "--json", "--film", str(film), *args], cwd=film,
                          capture_output=True, text=True, timeout=600, env={**os.environ, "BROWSER": "none"})
    assert proc.returncode == 0, (proc.stdout[-2000:], proc.stderr[-3000:])
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])


@pytest.mark.parametrize("size, fps, frames, want", [
    # 10 frames: the turn takes quick (8 frames, the longest step that fits), the line a tick (the longest that fits
    # half the act), and its drop shrinks from 5 px to the 4 px the entrance ease allows over a tick at 180 lines
    ((320, 180), 25, 10, {"turn": 8, "line": 5, "drop": 4}),
    # 4 s at 1080p: the brand's own steps (signature 1.8 s and base 0.52 s at 30 fps), the full 32 px drop and turn
    ((1920, 1080), 30, 120, {"turn": 54, "line": 16, "drop": 32, "half_turn": 0.7}),
])
def test_a_freshly_scaffolded_web_act_keeps_the_motion_law(slate_home, size, fps, frames, want):
    from slate.qa import motion

    film = _film(slate_home, size=size, fps=fps, frames=frames)
    out = _slate(film, "engine", "scaffold", "--act", "1", "--engine", "web")
    assert out["scaffolded"] is True
    out = _slate(film, "emit-events", "--act", "1")
    [act] = out["acts"]
    assert act["engine"] == "web" and act["events"] == 1 and act["motion"]["written"] is True

    rows = {r["id"]: r for r in json.loads((film / "timing" / "motion" / "act1.json").read_text())}
    turn, line = rows["template.object-turn"], rows["template.line-in.y"]
    assert turn["frame_out"] - turn["frame_in"] == want["turn"]
    assert line["frame_out"] - line["frame_in"] == want["line"] and line["from"] == want["drop"]
    assert max(r["frame_out"] for r in rows.values()) <= frames  # every move lands inside the act
    if "half_turn" in want:
        assert (turn["from"], turn["to"]) == (-want["half_turn"], want["half_turn"])

    gate = motion.handin_gate(film, 1)
    assert gate is not None and gate.gate == "handin.act1.motion"
    assert gate.status == "PASS", (gate.summary, [c.get("summary") for c in gate.details.get("checks") or []])
