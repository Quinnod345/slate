"""The kit's golden stills, proved from Python (L-render-D019), so the knowledge check runs them, not only vitest.

`node remotion/kit/node/golden.mjs --json` from engine/: every kit composition rendered on software GL within 45 dB of
its baseline in engine/tests/kit/golden/, and the font gate failing a face that exists nowhere. A frame counts as
changed only when it differs from both of a still's baselines (the two-baseline rule: on a GPU the raster isn't
bit-exact run to run).

The stills render whenever headless Chrome is installed, as the kit's own vitest does (about 15 s of small software-GL
stills), and skip, saying why, where it isn't; SLATE_SKIP_GOLDEN=1 skips them on purpose. Everything the run makes goes
under the test's own temporary folder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from slate import paths

CHROME = paths.ENGINE_SRC / "node_modules" / ".remotion" / "chrome-headless-shell"


def _node() -> str:
    return shutil.which("node") or "/opt/homebrew/bin/node"


def _has_browser() -> bool:
    return bool(os.environ.get("SLATE_CHROME")) or any(CHROME.glob("*/chrome-headless-shell-*/chrome-headless-shell"))


def test_the_two_baseline_rule_passes_a_still_that_matches_either_baseline(tmp_path):
    """golden.mjs compareStill: a render passes when it matches either baseline, fails when it matches neither, and a
    still with no baseline fails rather than passing quietly."""
    from PIL import Image

    def png(name, value):
        p = tmp_path / name
        Image.new("RGB", (32, 16), (value, 90, 30)).save(p)
        return p

    a, b, render, other = png("a.png", 10), png("b.png", 200), png("render.png", 200), png("other.png", 120)
    script = ("import {compareStill} from './remotion/kit/node/golden.mjs';"
              "const [r, a, b, o] = process.argv.slice(1);"
              "console.log(JSON.stringify({either: compareStill(r, [a, b]), neither: compareStill(o, [a, b]),"
              " one: compareStill(r, [b, '/nowhere.png']), none: compareStill(r, ['/nowhere.png'])}));")
    res = subprocess.run([_node(), "--input-type=module", "-e", script, str(render), str(a), str(b), str(other)],
                         cwd=paths.ENGINE_SRC, capture_output=True, text=True, timeout=60, check=True)
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["either"]["pass"] is True and out["either"]["baselines"] == 2 and "identical" in out["either"]["psnr"]
    assert out["neither"]["pass"] is False
    assert out["one"]["pass"] is True and out["one"]["baselines"] == 1
    assert out["none"] == {"pass": False, "psnr": [], "baselines": 0, "error": "no baseline"}


@pytest.mark.skipif(os.environ.get("SLATE_SKIP_GOLDEN") == "1", reason="SLATE_SKIP_GOLDEN=1 skips the golden stills")
@pytest.mark.skipif(not _has_browser(), reason="no headless Chrome here: `slate doctor --install` sets it up (it asks "
                    "first), or set SLATE_CHROME")
def test_the_kit_golden_stills_pass_from_python(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    res = subprocess.run([_node(), "remotion/kit/node/golden.mjs", "--json"], cwd=paths.ENGINE_SRC,
                         capture_output=True, text=True, timeout=1200,
                         env={**os.environ, "BROWSER": "none", "TMPDIR": str(tmp)})
    line = next((ln for ln in reversed(res.stdout.strip().splitlines()) if ln.startswith("{")), None)
    assert line, res.stderr[-2000:]
    r = json.loads(line)
    assert r["gl"] == "swangle" and r["psnrMin"] == 45
    assert len(r["stills"]) >= 8
    for s in r["stills"]:
        assert s["rendered"] and s["pass"], s
    assert r["fontGate"]["pass"] and r["fontGate"]["status"] == "FAIL", r["fontGate"]
    assert r["pass"] and res.returncode == 0
