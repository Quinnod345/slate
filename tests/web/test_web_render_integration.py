"""`slate render --engine web`, end to end: the render module (chunks, joins proven by PSNR, the registry) driving the
open web renderer on a tiny neutral film, through the real engine bridge. Real headless Chrome and ffmpeg; nothing
reaches the network and nothing leaves this Mac. 16 frames at 320 x 180, two samples each.
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
ENGINE = REPO / "engine"
FIXTURES = ENGINE / "tests" / "web" / "fixtures"

pytestmark = pytest.mark.skipif(
    not (ENGINE / "node_modules" / "puppeteer").is_dir() or shutil.which("ffmpeg", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin") is None,
    reason="the engine's packages or ffmpeg aren't installed",
)


def _film(home: dict, frames: int = 16) -> Path:
    """The web fixture film with one act of `frames` frames, its brand in the brand store, 8-frame chunks."""
    film = home["films"] / "web-fixture"
    shutil.copytree(FIXTURES / "film", film)
    shots_file = film / "timing" / "shots.json"
    shots = json.loads(shots_file.read_text())
    half = frames // 2
    shots["total_frames"] = frames
    shots["shots"] = [
        {"id": "W01", "act": 1, "frame_in": 0, "frame_out": half, "frames": half, "vo": "", "on_screen": ""},
        {"id": "W02", "act": 1, "frame_in": half, "frame_out": frames, "frames": frames - half, "vo": "",
         "on_screen": ""},
    ]
    shots_file.write_text(json.dumps(shots, indent=1))
    shutil.copytree(FIXTURES / "brand", home["brands"] / "web-fixture")
    (home["data"] / "slate.toml").write_text("[render]\nchunk_frames = 8\n")
    return film


def _slate(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "slate", *args], cwd=cwd, capture_output=True, text=True, timeout=900,
                          env={**os.environ, "BROWSER": "none"})


def _frames(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets,codec_name,profile", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]


def test_slate_render_engine_web_renders_joins_and_registers_an_act(slate_home):
    film = _film(slate_home)
    from slate.engine import bridge

    made = bridge.call("web-render", {"film": str(film), "act": 1, "scaffold": True}, timeout=120)
    assert made["scaffolded"] is True
    assert (film / "scenes" / "act1" / "web" / "index.html").is_file()

    r = _slate("--json", "--film", str(film), "render", "--act", "1", "--engine", "web", "--samples", "2", cwd=film)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-4000:])
    out = json.loads(r.stdout.strip().splitlines()[-1])
    text = json.dumps(out)
    assert '"engine": "web"' in text

    entries = json.loads((film / "renders" / "registry.json").read_text())["entries"]
    [entry] = [e for e in entries if e["act"] == 1]
    assert entry["engine"] == "web"
    assert entry["samples"] == 2
    assert entry["frames"] == [0, 16]
    master = film / entry["file"]
    stream = _frames(master)
    assert int(stream["nb_read_packets"]) == 16
    assert stream["codec_name"] == "prores"

    # the web engine wrote the act's motion trace while it rendered
    trace = json.loads((film / "timing" / "motion" / "act1.json").read_text())
    assert {row["id"] for row in trace} == {"template.object-turn", "template.line-in.opacity", "template.line-in.y"}


def test_the_web_engine_refuses_through_slate_with_the_shared_exit_codes(slate_home):
    film = _film(slate_home, frames=8)
    # no web scene in act 1 yet: a missing prerequisite (exit 3), with the way to start one
    r = _slate("--json", "--film", str(film), "render", "--act", "1", "--engine", "web", "--samples", "1", cwd=film)
    assert r.returncode == 3, (r.stdout[-2000:], r.stderr[-2000:])
    assert "template" in (r.stdout + r.stderr)
