"""Web captures through Aside: the local server and wrapper, the clock shim, chunking, timestamps, and one smoke test.

The smoke test opens one local page in Aside for a few seconds, so it runs only when asked (SLATE_ASIDE_SMOKE=1) and
skips cleanly when Aside isn't installed. Everything else fakes the Aside call.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from slate import procs
from slate.capture import media, web
from slate.errors import MissingPrerequisite, SlateError
from slate.gen import deps


def web_spec(**extra):
    spec = {"kind": "web", "name": "site-demo", "duration": 0.4, "root": "site", "page": "index.html",
            "viewport": {"width": 320, "height": 240},
            "steps": [{"at": 0.2, "do": "click", "selector": "#go", "id": "go", "word": {"shot": "S01", "text": "tap"}}]}
    spec.update(extra)
    return spec


@pytest.fixture
def site(film):
    folder = film / "site"
    folder.mkdir()
    (folder / "index.html").write_text("<!doctype html><html><head><title>t</title></head><body>"
                                       "<button id='go'>Go</button></body></html>")
    (folder / "app.js").write_text("console.log('hi')")
    return folder


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:  # our own 127.0.0.1 server, never the web
        return resp.status, dict(resp.headers.items()), resp.read()


def test_the_shim_goes_before_the_pages_own_scripts():
    tag = b'<script src="/__slate/clock.js"></script>'
    assert web.inject_shim(b"<html><head><script>x()</script></head>").startswith(b"<html><head>" + tag)
    assert web.inject_shim(b"<!doctype html><p>no head</p>").startswith(b"<!doctype html>" + tag)
    assert web.inject_shim(b"<p>bare</p>").startswith(tag)


def test_the_wrapper_fixes_the_viewport():
    html = web.wrapper_html("/index.html", 390, 844)
    assert 'id="target" src="/index.html"' in html and "width:390px;height:844px" in html


def test_the_local_server_serves_the_site_with_the_clock_and_nothing_else(site):
    with web.LocalServer(wrapper=web.wrapper_html("/index.html", 320, 240), root=site) as server:
        status, _, body = _get(f"{server.origin}/index.html")
        assert status == 200 and b"/__slate/clock.js" in body
        status, headers, body = _get(f"{server.origin}/__slate/clock.js")
        assert b"__slateClock" in body and "javascript" in headers["Content-Type"]
        status, _, body = _get(f"{server.origin}/app.js")
        assert body == b"console.log('hi')"  # scripts are served as they are
        assert _get(f"{server.origin}/__slate/capture.html")[2].count(b"iframe") >= 1
        with pytest.raises(urllib.error.HTTPError):
            _get(f"{server.origin}/../../film.json")
        thread = server._thread
    assert not thread.is_alive()  # nothing is left running


def test_a_dev_server_on_this_mac_is_proxied_same_origin_and_frameable(tmp_path):
    class Dev(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def do_GET(self):  # noqa: N802
            body = b"<html><head></head><body>dev</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    dev = ThreadingHTTPServer(("127.0.0.1", 0), Dev)
    threading.Thread(target=dev.serve_forever, daemon=True).start()
    try:
        upstream = f"http://127.0.0.1:{dev.server_address[1]}"
        with web.LocalServer(wrapper="x", upstream=upstream) as server:
            status, headers, body = _get(f"{server.origin}/")
            assert b"/__slate/clock.js" in body and "X-Frame-Options" not in headers
    finally:
        dev.shutdown()
        dev.server_close()


def test_parse_result_finds_the_line_among_asides_own_output():
    out = "\x1b[2m✔︎ Opened a new tab\x1b[0m\nSLATE_RESULT {\"ok\": true, \"frames\": [0]}\n\x1b[2m[ok | 9ms]\x1b[0m\n"
    assert web.parse_result(out) == {"ok": True, "frames": [0]}
    with pytest.raises(SlateError):
        web.parse_result("\x1b[31mError: boom\x1b[0m\n[error | 3ms]")


def test_frames_are_moved_only_from_the_folder_slate_asked_for(tmp_path):
    session = tmp_path / "session"
    (session / "slate-capture-x-1").mkdir(parents=True)
    Image.new("RGB", (4, 4)).save(session / "slate-capture-x-1" / "f00000.jpg")
    moved = web._collect({"pwd": str(session), "dir": "slate-capture-x-1"}, tmp_path / "shots")
    assert [p.name for p in moved] == ["f00000.jpg"] and not (session / "slate-capture-x-1").exists()
    (session / "elsewhere").mkdir()
    with pytest.raises(SlateError):
        web._collect({"pwd": str(session), "dir": "elsewhere"}, tmp_path / "shots")
    with pytest.raises(SlateError):
        web._collect({"pwd": str(session), "dir": "../outside"}, tmp_path / "shots")


class FakeAside:
    """Stands in for `aside repl`: reads the program's parameters and writes frames where Aside would."""

    def __init__(self, root: Path, clock: str = "virtual"):
        self.root, self.clock, self.calls = root, clock, []

    def __call__(self, code: str) -> dict:
        params = json.loads(re.match(r"const P = (.*?);\n", code, re.S).group(1))
        self.calls.append(params)
        folder = self.root / params["dir"]
        folder.mkdir(parents=True)
        dpr = 2
        size = (int(params["W"] * dpr), int(params["H"] * dpr))
        result = {"ok": True, "pwd": str(self.root), "dir": params["dir"], "dpr": dpr, "k": 1,
                  "clip": {"width": params["W"], "height": params["H"]}, "window": {"width": 1440, "height": 900},
                  "clock": self.clock, "notes": [], "frames": [], "stamps": [],
                  "steps": [{"index": s["index"], "at": s["at"], "actual": s["at"], "ok": True} for s in params["steps"]]}
        if self.clock == "virtual":
            for i in range(params["start"], params["end"]):
                Image.new("RGB", size, (i * 10 % 255, 40, 40)).save(folder / f"f{i:05d}.jpg", quality=95)
                result["frames"].append(i)
        else:
            for i, stamp in enumerate((0.0, 0.09, 0.21, 0.33)):
                Image.new("RGB", size, (i * 60, 40, 40)).save(folder / f"r{i:05d}.jpg", quality=95)
                result["stamps"].append(stamp)
        return result


def test_a_virtual_clock_capture_with_a_fake_aside(film, site, tmp_path):
    fake = FakeAside(tmp_path / "aside-session")
    path = web.capture(film, web_spec(), runner=fake)
    rec = json.loads(path.read_text())
    assert deps.validate_part(rec, "capture") == []
    assert rec["raw"]["count"] == 10 and (rec["raw"]["width"], rec["raw"]["height"]) == (640, 480)
    assert rec["web"]["clock"] == "virtual" and rec["web"]["mode"] == "iframe" and rec["web"]["served"] == "folder"
    assert rec["web"]["browser"] == "Aside (aside repl)"
    (params,) = fake.calls
    assert params["wrapper"].startswith("http://127.0.0.1:") and params["fps_num"] == 25 and params["fps_den"] == 1
    assert (params["start"], params["end"]) == (0, 10)
    assert rec["taps"][0]["exact"] and rec["taps"][0]["error_frames"] == 0  # the click lands on "tap"
    assert not any((tmp_path / "aside-session").iterdir())  # Slate cleaned up after itself


def test_long_captures_run_in_chunks(film, site, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "FRAMES_PER_CALL", 4)
    fake = FakeAside(tmp_path / "aside-session")
    web.capture(film, web_spec(), runner=fake)
    assert [(c["start"], c["end"]) for c in fake.calls] == [(0, 4), (4, 8), (8, 10)]


def test_a_real_time_capture_is_conformed_from_its_timestamps(film, site, tmp_path):
    fake = FakeAside(tmp_path / "aside-session", clock="real")
    path = web.capture(film, web_spec(), runner=fake)
    rec = json.loads(path.read_text())
    assert rec["web"]["clock"] == "real" and rec["raw"]["count"] == 10 and rec["source"]["vfr"]
    assert rec["web"]["capture_fps"] and any("step" in n for n in rec["notes"])  # it says the motion will step


def test_without_aside_a_web_capture_refuses(film, site, monkeypatch):
    monkeypatch.setattr(web, "ASIDE_DEFAULT", Path("/nonexistent/aside"))
    monkeypatch.setattr(procs, "which", lambda tool: None)
    with pytest.raises(MissingPrerequisite) as exc:
        web.capture(film, web_spec())
    assert "Aside" in exc.value.message and "never uses another browser" in exc.value.hint


@pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed")
def test_the_shim_is_valid_javascript(tmp_path):
    proc = procs.run(["node", "--check", str(web.SHIM_PATH)])
    assert proc.returncode == 0, proc.stderr


def _aside_available() -> bool:
    return web.ASIDE_DEFAULT.is_file() or bool(procs.which("aside"))


@pytest.mark.skipif(os.environ.get("SLATE_ASIDE_SMOKE") != "1",
                    reason="opens one local page in Aside for a few seconds: set SLATE_ASIDE_SMOKE=1 to run it")
@pytest.mark.skipif(not _aside_available(), reason="Aside isn't installed")
def test_smoke_a_local_page_through_aside_frame_exact(film):
    """The one real capture: a local page whose bar slides 200 CSS px a second, at 25 fps: 8 px a frame, exactly."""
    folder = film / "site"
    folder.mkdir()
    (folder / "index.html").write_text(
        "<!doctype html><html><head><style>html,body{margin:0;background:#ffffff}"
        "#bar{position:absolute;left:0;top:40px;width:40px;height:40px;background:#333;"
        "animation:slide 1s linear forwards}@keyframes slide{to{transform:translateX(200px)}}</style></head>"
        "<body><div id='bar'></div></body></html>")
    path = web.capture(film, web_spec(duration=0.4, steps=[]))
    rec = json.loads(path.read_text())
    assert rec["web"]["clock"] == "virtual" and rec["raw"]["count"] == 10
    import numpy as np

    lefts = []
    for i in range(10):
        img = np.asarray(Image.open(film / rec["raw"]["dir"] / media.frame_name(i)).convert("L"), dtype=float)
        row = img[int(60 * img.shape[0] / 240)]
        lefts.append(int(np.where(row < 100)[0][0]))
    steps = np.diff(lefts)
    per_frame = 8 * img.shape[1] / 320  # 8 CSS px at whatever pixel ratio Aside's display has
    assert np.allclose(steps, per_frame, atol=1)
