"""A shot in the open web renderer inside a Remotion act, end to end (P074): its page's beats join the act's events,
`slate check` hands the shot in through the web renderer, and `slate render` draws the act in Remotion, then the shot
over exactly its frames from its own page, and splices it in with proof before the act is registered.

The engine's kit fixture film (act 1 in the kit: shots E01 and E02, 10 frames at 25 fps, 320 x 180), with E02 declared
`engine: web` and its page in scenes/shots/E02/web. Real headless Chrome and ffmpeg: opt in with SLATE_TEST_RENDER=1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from slate import jsonio
from slate.cli import run_cli

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine"
CORE = ENGINE / "tests" / "core" / "fixtures"

pytestmark = [
    pytest.mark.render,
    pytest.mark.skipif(os.environ.get("SLATE_TEST_RENDER") != "1",
                       reason="a real render (headless Chrome, ffmpeg): opt in with SLATE_TEST_RENDER=1"),
    pytest.mark.skipif(not (ENGINE / "node_modules" / "puppeteer").is_dir() or shutil.which(
        "ffmpeg", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin") is None,
        reason="the engine's packages or ffmpeg aren't installed"),
]

SCENE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>shot E02</title>
<style>html, body { margin: 0; overflow: hidden; } #dot { position: absolute; width: 24px; height: 24px; border-radius: 12px; }</style>
</head><body><div id="dot"></div><script type="module" src="./scene.js"></script></body></html>
"""

SCENE_JS = """// Shot E02, drawn in the open web renderer: a dot that slides across, on the film's clock.
import {defineEvent, secOf, shot, tokens} from '@slate/kit/core';
import {defineScene} from '@slate/web';

const s = shot('E02');
const t = tokens();
document.body.style.background = t.color.inverse;
const dot = document.getElementById('dot');
dot.style.background = t.color.accent;
dot.style.top = '78px';
defineEvent({shot: 'E02', id: 'web_dot_taps', t: s.start + 0.04, kind: 'tap', size: 's', note: 'a dot taps in the web shot'});
defineScene({
  seek(_t, info) {
    const u = Math.max(0, Math.min(1, (secOf(info.filmFrame) - s.start) / Math.max(0.001, s.dur)));
    dot.style.left = `${40 + 220 * u}px`;
  },
});
"""


def run(*argv):
    code, out, err = run_cli(list(argv))
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


@pytest.fixture
def film(slate_home, monkeypatch):
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    d = slate_home["films"] / "kit-e2e"
    shutil.copytree(CORE / "kit-film", d)
    shutil.copytree(CORE / "kit-brand", slate_home["brands"] / "kit-e2e-brand")
    shots = jsonio.read(d / "timing" / "shots.json")
    for s in shots["shots"]:
        if s["id"] == "E02":
            s["engine"] = "web"
    jsonio.write_atomic(d / "timing" / "shots.json", shots)
    page = d / "scenes" / "shots" / "E02" / "web"
    page.mkdir(parents=True)
    (page / "index.html").write_text(SCENE_HTML, encoding="utf-8")
    (page / "scene.js").write_text(SCENE_JS, encoding="utf-8")
    (slate_home["data"] / "slate.toml").write_text("[render]\nchunk_frames = 8\n")
    return d


def test_a_web_shot_is_handed_in_and_spliced_into_its_remotion_act(film):
    code, out, err = run("emit-events", "--film", str(film), "--act", "1", "--json")
    assert code == 0, err[-3000:]
    events = jsonio.read(film / "timing" / "visual_events" / "act1.json")
    assert "web_dot_taps" in [e["id"] for e in events]  # the page's beat, inside its shot, with the act's own

    code, out, err = run("check", "1", "--film", str(film), "--json")
    by = {g["gate"]: g for g in out["gates"]}
    for name in ("handin.act1.shots", "handin.act1.shot.E02.page", "handin.act1.shot.E02.network",
                 "handin.act1.shot.E02.banned_words", "handin.act1.shot.E02.literals", "handin.act1.events"):
        assert by[name]["status"] == "PASS", (name, by[name], err[-2000:])

    code, out, err = run("render", "--film", str(film), "--act", "1", "--samples", "2", "--json")
    assert code == 0, err[-3000:]
    r = out["renders"][0]
    assert r["registered"] and r["shot_engines"][0]["shot"] == "E02" and r["shot_engines"][0]["frames"] == [5, 9]
    assert any(g["gate"] == "render.splice" and g["status"] == "PASS" for g in out["gates"])
    entry = next(e for e in jsonio.read(film / "renders" / "registry.json")["entries"] if e["act"] == 1)
    assert entry["engine"] == "remotion" and entry["shot_engines"][0]["engine"] == "web"
    frames = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                             "stream=nb_read_packets", "-of", "csv=p=0", str(film / entry["file"])],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert int(frames.split(",")[0]) == 10
