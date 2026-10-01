"""Apple Vision prints diagnostics ("Unable to find a valid E5 …") from native code through C stdio. With --json,
stdout must stay one JSON object, so every OCR call runs with fd 1 sent to stderr and the C buffers flushed before fd 1
comes back (a buffered line would otherwise reach stdout at exit, after the redirect)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

NATIVE = r"""
import ctypes, json, sys
sys.path.insert(0, {src!r})
from slate.qa import ocr

libc = ctypes.CDLL(None)
with ocr.stdout_to_stderr():
    libc.printf(b"Unable to find a valid E5 in provided path (a stand-in for Vision's own line)\n")
print(json.dumps({{"ok": True}}))
"""


def test_native_stdio_inside_an_ocr_call_never_reaches_stdout():
    proc = subprocess.run([sys.executable, "-c", NATIVE.format(src=str(REPO / "src"))], capture_output=True, text=True,
                          timeout=60, env={**os.environ, "BROWSER": "none"})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True}  # exactly one JSON object: nothing else on stdout
    assert "Unable to find a valid E5" in proc.stderr


LIVE = r"""
import json, sys
sys.path.insert(0, {src!r})
from PIL import Image, ImageDraw
from slate.qa import ocr

ok, engine = ocr.available(refresh=True)
img = Image.new("RGB", (600, 120), "white")
ImageDraw.Draw(img).text((20, 40), "SLATE 42", fill="black")
lines = ocr.recognize(img.resize((1800, 360))) if ok else []
print(json.dumps({{"ok": ok, "engine": engine, "text": " ".join(l["text"] for l in lines)}}))
"""


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("SLATE_TEST_VISION") != "1",
                    reason="runs Apple Vision's text model; opt in with SLATE_TEST_VISION=1")
def test_vision_reads_text_and_keeps_stdout_clean():
    proc = subprocess.run([sys.executable, "-c", LIVE.format(src=str(REPO / "src"))], capture_output=True, text=True,
                          timeout=120, env={**os.environ, "BROWSER": "none"})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"], out["engine"]
    assert "SLATE 42" in out["text"] and out["engine"]["name"] == "Apple Vision (ocrmac)"
