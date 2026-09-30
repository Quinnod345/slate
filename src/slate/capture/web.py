"""Web captures, through Aside only, at a fixed viewport, at the film's frame rate.

Aside is the one browser on Quinn's Macs, so a web capture is an `aside repl` script (openTab, page.evaluate,
page.mouse, page.keyboard, page.screenshot). video-verifier's Playwright path is never used, and nothing else opens a
page. How it works:

- **A fixed viewport.** Aside's window size isn't Slate's to set, so the page is shown in an iframe of exactly the
  spec's viewport (its layout viewport, media queries included), inside a small wrapper page Slate serves on
  127.0.0.1. The wrapper scales the iframe to fit Aside's window; frames come out at the device pixel ratio.
  `"frame": "direct"` opens the page itself instead (for sites that refuse iframes), clipped to the viewport.
- **A virtual clock.** For pages Slate serves (a local folder, or a dev server on this Mac, proxied so it's the same
  origin), a clock shim is injected before the page's own scripts: time moves only when Slate says, one film frame at
  a time, and every screenshot is taken at exactly its film time. A page on another origin can't take the shim in an
  iframe, so it's captured in real time and conformed from its screenshots' timestamps (and the record says so).
- **Steps** (click, type, key, scroll) run at their times; clicks by selector or by viewport coordinates, typing one
  character at a time across `seconds`, scrolls eased across `seconds`.
- A REPL call has a 120 s limit and closes its tabs when it ends, so long virtual-clock captures run in chunks: each
  chunk reopens the page and fast-forwards the clock (without screenshots) to where the last chunk stopped.
- Aside's REPL may only write inside its own session folder, so frames land there and Slate moves them into the film
  folder, then removes only the folder it made.

Nothing is left running: the local server stops, and the REPL closes its tab.
"""

from __future__ import annotations

import functools
import http.client
import json
import os
import re
import shutil
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from slate import jsonio, log, procs, replay
from slate.capture import conform, ios, media, spec as spec_mod, store
from slate.errors import MissingPrerequisite, SlateError, UsageError
from slate.gen import deps

SHIM_PATH = Path(__file__).with_name("clock_shim.js")
ASIDE_DEFAULT = Path.home() / ".local" / "bin" / "aside"
FRAMES_PER_CALL = 300  # virtual-clock frames per REPL call (its limit is 120 s; a frame takes 0.1–0.3 s)
REAL_MAX_SECONDS = 90.0  # a real-clock capture has to fit in one REPL call
CALL_TIMEOUT = 170.0
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade", "content-length", "content-encoding", "x-frame-options",
        "content-security-policy", "content-security-policy-report-only"}


def aside_path() -> str:
    """Aside's CLI (~/.local/bin/aside), or MissingPrerequisite: web captures never fall back to another browser."""
    if ASIDE_DEFAULT.is_file() and os.access(ASIDE_DEFAULT, os.X_OK):
        return str(ASIDE_DEFAULT)
    found = procs.which("aside")
    if found:
        return found
    raise MissingPrerequisite("Web captures go through Aside, and Aside's CLI isn't installed.",
                              hint="Install Aside (https://aside.com) and sign in to its CLI; Slate never uses "
                                   "another browser for captures.")


def shim_source() -> str:
    return SHIM_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
# The local server: the wrapper page, the clock shim, and the page itself (a folder, or a loopback dev server)


def wrapper_html(target: str, width: int, height: int) -> str:
    """The page Aside opens: the target in an iframe of exactly the viewport, scaled later to fit the window."""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>slate capture</title><style>
html,body{{margin:0;padding:0;overflow:hidden;background:#000}}
#stage{{position:absolute;left:0;top:0;width:{width}px;height:{height}px;transform-origin:0 0}}
#target{{display:block;width:{width}px;height:{height}px;border:0;background:#fff}}
</style></head><body><div id="stage"><iframe id="target" src="{target}"></iframe></div></body></html>
"""


def inject_shim(html: bytes) -> bytes:
    """Put the clock shim before anything else in the page's <head> (so it's in place before the page's scripts)."""
    tag = b'<script src="/__slate/clock.js"></script>'
    lower = html.lower()
    head = lower.find(b"<head")
    if head >= 0:
        close = lower.find(b">", head)
        if close >= 0:
            return html[:close + 1] + tag + html[close + 1:]
    doctype = lower.find(b"<!doctype")
    if doctype >= 0:
        close = lower.find(b">", doctype)
        return html[:close + 1] + tag + html[close + 1:]
    return tag + html


class _Handler(BaseHTTPRequestHandler):
    server_version = "slate-capture/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quiet: progress goes through slate.log
        return

    @property
    def cfg(self) -> dict:
        return self.server.slate_cfg  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.cfg.get("upstream"):
            return self._proxy()
        self._send(405, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/__slate/capture.html":
            return self._send(200, self.cfg["wrapper"].encode(), "text/html; charset=utf-8")
        if path == "/__slate/clock.js":
            return self._send(200, self.cfg["shim"].encode(), "application/javascript; charset=utf-8")
        if self.cfg.get("upstream"):
            return self._proxy()
        root: Path | None = self.cfg.get("root")
        if root is None:
            return self._send(404, b"", "text/plain")
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        full = (root / rel).resolve()
        if full.is_dir():
            full = full / "index.html"
        if not full.is_relative_to(root) or not full.is_file():
            return self._send(404, b"not found", "text/plain")
        body = full.read_bytes()
        ctype = _content_type(full.name)
        if ctype.startswith("text/html"):
            body = inject_shim(body)
        self._send(200, body, ctype)

    def _proxy(self) -> None:
        """Forward to the dev server on this Mac, so the page shares the wrapper's origin and can take the shim."""
        upstream = urllib.parse.urlparse(self.cfg["upstream"])
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP and k.lower() != "host"}
        headers["Accept-Encoding"] = "identity"
        conn = http.client.HTTPConnection(upstream.hostname, upstream.port or 80, timeout=30)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            ctype = resp.getheader("Content-Type") or "application/octet-stream"
            if ctype.startswith("text/html"):
                data = inject_shim(data)
            extra = {k: v for k, v in resp.getheaders() if k.lower() not in _HOP and k.lower() != "content-type"
                     and k.lower() != "cache-control"}
            self._send(resp.status, data, ctype, extra)
        except OSError as exc:
            self._send(502, f"slate couldn't reach the dev server: {exc}".encode(), "text/plain")
        finally:
            conn.close()


def _content_type(name: str) -> str:
    import mimetypes  # noqa: PLC0415

    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return f"{ctype}; charset=utf-8" if ctype.startswith("text/") or ctype == "application/javascript" else ctype


class LocalServer:
    """127.0.0.1 only, an ephemeral port, a daemon thread, stopped on exit (nothing is left running)."""

    def __init__(self, *, wrapper: str, root: Path | None = None, upstream: str | None = None):
        self.cfg = {"wrapper": wrapper, "shim": shim_source(), "root": root.resolve() if root else None,
                    "upstream": upstream}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "LocalServer":
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.slate_cfg = self.cfg  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, name="slate-capture-server", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def origin(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"


# ---------------------------------------------------------------------------------------------------------------
# The Aside script


def repl_code(params: dict) -> str:
    """The REPL program for one chunk. Parameters travel as one JSON literal; the program prints SLATE_RESULT."""
    return "const P = " + json.dumps(params, ensure_ascii=False) + ";\n" + _REPL_BODY


_REPL_BODY = r"""
const R = {ok: false, frames: [], stamps: [], steps: [], notes: [], pwd: String(typeof pwd === 'function' ? await pwd() : pwd), dir: P.dir};
let pgS = null;
try {
  pgS = await openTab(P.frame === 'direct' ? P.target : P.wrapper);
  const vp = (pgS.viewportSize && pgS.viewportSize()) || {width: 0, height: 0};
  const dpr = await pgS.evaluate(() => window.devicePixelRatio || 1);
  R.window = vp; R.dpr = dpr;
  let k = 1, clipW = P.W, clipH = P.H;
  const T = P.frame === 'iframe' ? "document.getElementById('target').contentWindow" : 'window';
  if (P.frame === 'iframe') {
    k = Math.min(1, vp.width / P.W, vp.height / P.H);
    if (P.target_px) k = Math.min(k, Math.max(P.target_px.width / (P.W * dpr), P.target_px.height / (P.H * dpr)));
    await pgS.evaluate('document.getElementById("stage").style.transform = "scale(' + k + ')"');
    await pgS.evaluate(`(async () => {
      const f = document.getElementById('target');
      for (let i = 0; i < 500; i++) {
        let state = 'cross';
        try { state = f.contentDocument ? f.contentDocument.readyState : 'cross'; } catch (e) { state = 'cross'; }
        if (state === 'complete' || state === 'cross') break;
        await new Promise((r) => setTimeout(r, 20));
      }
    })()`);
    clipW = P.W * k; clipH = P.H * k;
  } else {
    clipW = Math.min(P.W, vp.width); clipH = Math.min(P.H, vp.height);
    if (P.inject_shim) await pgS.evaluate(P.shim);
    R.layout_viewport = vp;
  }
  R.k = k; R.clip = {width: clipW, height: clipH};
  const hasClock = await pgS.evaluate(`(() => { try { return !!(${T}).__slateClock; } catch (e) { return false; } })()`);
  const virtual = P.clock === 'virtual' && hasClock;
  R.clock = virtual ? 'virtual' : 'real';
  if (P.clock === 'virtual' && !hasClock) R.notes.push('the virtual clock could not reach this page (another origin), so it was captured in real time');
  await pgS.evaluate(`(async () => {
    try {
      const w = ${T}; const d = w.document;
      const st = (window.__slateClock && window.__slateClock.realSetTimeout) || window.setTimeout.bind(window);
      const wait = (ms) => new Promise((r) => st(r, ms));
      if (d.fonts && d.fonts.ready) await Promise.race([d.fonts.ready, wait(5000)]);
      const imgs = Array.from(d.images).filter((i) => !i.complete);
      await Promise.race([Promise.all(imgs.map((i) => new Promise((r) => { i.addEventListener('load', r, {once: true}); i.addEventListener('error', r, {once: true}); }))), wait(5000)]);
    } catch (e) {}
  })()`);
  const at = (x, y) => pgS.mouse.click(x * k, y * k);
  const scrollY = async () => pgS.evaluate(`(() => { try { return (${T}).scrollY; } catch (e) { return 0; } })()`);
  const setScroll = async (y) => pgS.evaluate(`(() => { try { (${T}).scrollTo(0, ${y}); } catch (e) {} })()`);
  const pending = P.steps.slice();
  const active = [];
  const ease = (u) => u * u * (3 - 2 * u);
  const perform = async (tMs) => {
    while (pending.length && pending[0].at * 1000 <= tMs + 0.001) {
      const s = pending.shift();
      try {
        if (s.do === 'click') {
          if (s.selector) {
            const pt = await pgS.evaluate(`(() => { try { const el = (${T}).document.querySelector(${JSON.stringify(s.selector)}); if (!el) return null; const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; } catch (e) { return null; } })()`);
            if (!pt) throw new Error('no element matches ' + s.selector);
            await at(pt.x, pt.y);
          } else {
            await at(s.x, s.y);
          }
        } else if (s.do === 'type') {
          const per = s.seconds ? (s.seconds * 1000) / Math.max(1, s.text.length) : 60;
          active.push({kind: 'type', text: s.text, done: 0, t0: tMs, per});
        } else if (s.do === 'key') {
          await pgS.keyboard.press(s.text);
        } else if (s.do === 'scroll') {
          active.push({kind: 'scroll', from: await scrollY(), to: s.to, t0: tMs, dur: (s.seconds || 0) * 1000});
        }
        R.steps.push({index: s.index, at: s.at, actual: tMs / 1000, ok: true});
      } catch (e) {
        R.steps.push({index: s.index, at: s.at, actual: tMs / 1000, ok: false, note: String(e).slice(0, 300)});
      }
    }
    for (const a of active.slice()) {
      if (a.kind === 'type') {
        const want = Math.min(a.text.length, Math.floor((tMs - a.t0) / a.per) + 1);
        if (want > a.done) { await pgS.keyboard.type(a.text.slice(a.done, want)); a.done = want; }
        if (a.done >= a.text.length) active.splice(active.indexOf(a), 1);
      } else if (a.kind === 'scroll') {
        const u = a.dur > 0 ? Math.min(1, (tMs - a.t0) / a.dur) : 1;
        await setScroll(a.from + (a.to - a.from) * ease(u));
        if (u >= 1) active.splice(active.indexOf(a), 1);
      }
    }
  };
  const shoot = async (name) => {
    const buf = await pgS.screenshot({type: 'jpeg', quality: P.quality, clip: {x: 0, y: 0, width: clipW, height: clipH}});
    await fs.writeFile(P.dir + '/' + name, buf);
  };
  const pad = (i) => String(i).padStart(5, '0');
  await fs.mkdir(P.dir, {recursive: true});
  if (virtual) {
    const frameMs = (i) => (i * 1000 * P.fps_den) / P.fps_num;
    const paint = () => pgS.evaluate(`new Promise((r) => { const raf = (window.__slateClock && window.__slateClock.realRaf) || window.requestAnimationFrame.bind(window); raf(() => raf(r)); })`);
    for (let i = 0; i < P.end; i++) {
      const tMs = frameMs(i);
      await perform(tMs);
      await pgS.evaluate(`(${T}).__slateClock.advanceTo(${tMs})`);
      if (i < P.start) continue;
      await paint();
      await shoot('f' + pad(i) + '.jpg');
      R.frames.push(i);
    }
  } else {
    const t0 = Date.now();
    let i = 0;
    for (;;) {
      const tMs = Date.now() - t0;
      if (tMs > P.duration * 1000) break;
      await perform(tMs);
      const stamp = (Date.now() - t0) / 1000;
      await shoot('r' + pad(i) + '.jpg');
      R.stamps.push(stamp);
      R.frames.push(i);
      i += 1;
    }
  }
  R.ok = true;
} catch (e) {
  R.error = String((e && e.stack) || e).slice(0, 2000);
} finally {
  if (pgS) { try { await closeTab(pgS); } catch (e) {} }
}
console.log('SLATE_RESULT ' + JSON.stringify(R));
"""


def parse_result(stdout: str) -> dict:
    """The SLATE_RESULT line of a REPL run (Aside prints its own lines around it, some in colour)."""
    for line in reversed(_ANSI.sub("", stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("SLATE_RESULT "):
            try:
                return json.loads(line[len("SLATE_RESULT "):])
            except json.JSONDecodeError as exc:
                raise SlateError(f"Aside's capture result wasn't readable JSON: {exc}") from exc
    tail = _ANSI.sub("", stdout or "").strip()[-800:]
    raise SlateError(f"Aside's REPL didn't finish the capture: {tail or '(no output)'}",
                     hint="Is Aside running and signed in? `aside repl \"console.log(1)\"` checks it.")


def run_repl(code: str, *, aside: str | None = None, timeout: float = CALL_TIMEOUT) -> dict:
    """One `aside repl` call (through slate.procs: no shell, BROWSER=none) → its SLATE_RESULT."""
    proc = procs.run([aside or aside_path(), "repl", code], timeout=timeout)
    return parse_result(proc.stdout or "")


def _collect(result: dict, dest: Path) -> list[Path]:
    """Move the frames Aside wrote in its session folder into the film folder, then remove only Slate's folder."""
    session = Path(result.get("pwd") or "")
    src = (session / str(result.get("dir") or "")).resolve()
    if not result.get("dir") or not src.name.startswith("slate-capture-") or not src.is_relative_to(session.resolve()):
        raise SlateError("Aside reported frames somewhere Slate didn't ask it to write; nothing was moved.")
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in sorted(src.glob("*.jpg")):
        target = dest / f.name
        shutil.move(str(f), target)
        moved.append(target)
    shutil.rmtree(src, ignore_errors=True)
    return moved


# ---------------------------------------------------------------------------------------------------------------
# The capture


def _target(spec: dict, film_dir: Path) -> tuple[str, Path | None, str | None]:
    """(what the iframe or tab opens, the local folder served, the loopback upstream proxied)."""
    if spec.get("root"):
        root = spec_mod.resolve(film_dir, spec["root"])
        if not root.is_dir():
            raise UsageError(f"The web capture's root folder isn't there: {root}")
        page = (spec.get("page") or "index.html").lstrip("/")
        return f"/{urllib.parse.quote(page)}", root, None
    url = spec["url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in LOOPBACK and spec.get("frame", "iframe") == "iframe":
        upstream = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path or "/"
        return path + (f"?{parsed.query}" if parsed.query else ""), None, upstream
    return url, None, None


def plan(film_dir: Path, spec: dict) -> dict:
    film = deps.load_film(film_dir)
    clk = deps.film_clock(film)
    count = conform.frame_count(spec["duration"], clk)
    target, root, upstream = _target(spec, film_dir)
    same_origin = bool(root or upstream)
    return {"capture": spec["name"], "frames": count, "chunks": max(1, -(-count // FRAMES_PER_CALL)),
            "viewport": spec["viewport"], "frame": spec["frame"],
            "clock": spec["clock"] if (same_origin or spec["frame"] == "direct") else "real",
            "serves": str(root) if root else (upstream or None), "opens": target if not same_origin else "(local)",
            "aside": aside_path(), "duration": spec["duration"]}


def capture(film_dir: Path, spec: dict, *, aside: str | None = None, runner=None) -> Path:
    """Capture a page through Aside into captures/<name>/ and return its capture.json path (ARCHITECTURE §12).

    `runner(code) -> dict` replaces the Aside call (tests use it); normally it's `run_repl`."""
    film_dir = Path(film_dir)
    film = deps.load_film(film_dir)
    spec = spec_mod.normalize(spec, film)
    if spec["kind"] != "web":
        raise UsageError(f"{spec['name']} is a {spec['kind']} capture, not a web one.")
    clk = deps.film_clock(film)
    count = conform.frame_count(spec["duration"], clk)
    if runner is None:
        binary = aside or aside_path()
        runner = functools.partial(run_repl, aside=binary)
    folder = store.capture_dir(film_dir, spec["name"])
    folder.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(folder / "spec.json", spec)
    shots = folder / "shots"
    if shots.exists():
        shutil.rmtree(shots)
    target, root, upstream = _target(spec, film_dir)
    width, height = spec["viewport"]["width"], spec["viewport"]["height"]
    size = conform.target_size(spec, film)
    same_origin = bool(root or upstream)
    virtual_possible = spec["clock"] == "virtual" and (same_origin or spec["frame"] == "direct")
    if not virtual_possible and spec["duration"] > REAL_MAX_SECONDS:
        raise UsageError(f"A real-time capture has to finish inside one Aside call: keep it under "
                         f"{REAL_MAX_SECONDS:g} s, or serve the page locally so the virtual clock can chunk it.")
    steps = [{**s, "index": i} for i, s in enumerate(spec["steps"])]
    results: list[dict] = []
    with LocalServer(wrapper=wrapper_html(target, width, height), root=root, upstream=upstream) as server:
        wrapper = f"{server.origin}/__slate/capture.html"
        direct_target = spec["url"] if spec.get("url") and not same_origin else f"{server.origin}{target}"
        base = {"wrapper": wrapper, "target": direct_target, "frame": spec["frame"], "W": width, "H": height,
                "target_px": {"width": size[0], "height": size[1]} if size else None,
                "fps_num": clk.fps_num, "fps_den": clk.fps_den, "duration": spec["duration"],
                "quality": int(spec.get("quality", 95)), "clock": spec["clock"], "steps": steps,
                "inject_shim": spec["frame"] == "direct" and spec["clock"] == "virtual",
                "shim": shim_source() if spec["frame"] == "direct" else None}
        chunks = [(a, min(count, a + FRAMES_PER_CALL)) for a in range(0, count, FRAMES_PER_CALL)] \
            if virtual_possible else [(0, count)]
        for a, b in chunks:
            token = uuid.uuid4().hex[:10]
            params = {**base, "start": a, "end": b, "dir": f"slate-capture-{spec['name']}-{token}"}
            log.info(f"capture {spec['name']}: frames {a}–{b - 1} through Aside")
            result = runner(repl_code(params))
            if not result.get("ok"):
                raise SlateError(f"Aside couldn't capture {spec['name']}: {result.get('error') or 'no detail'}")
            _collect(result, shots)
            results.append(result)
            if result.get("clock") == "real":
                break  # a real-time capture is one call
    clock_used = results[0].get("clock", "real")
    if clock_used == "virtual":
        images = sorted(shots.glob("f[0-9][0-9][0-9][0-9][0-9].jpg"))
        if len(images) != count:
            raise SlateError(f"Aside returned {len(images)} frames where {count} were asked for.")
        raw = conform.from_frames(images, film_dir, spec["name"], spec=spec, quality=int(spec.get("quality", 95)))
    else:
        images = sorted(shots.glob("r[0-9][0-9][0-9][0-9][0-9].jpg"))
        stamps = results[0].get("stamps") or []
        raw = conform.from_timestamps(images, stamps, film_dir, spec["name"], duration=spec["duration"], spec=spec,
                                      quality=int(spec.get("quality", 95)))
    first = results[0]
    steps_done = [s for r in results[:1] for s in r.get("steps") or []]
    failed = [s for s in steps_done if not s.get("ok")]
    if failed:
        raise SlateError(f"Step {failed[0].get('index')} failed in Aside: {failed[0].get('note')}")
    captured_fps = None
    if clock_used == "real" and len(results[0].get("stamps") or []) > 1:
        stamps = results[0]["stamps"]
        captured_fps = round((len(stamps) - 1) / max(1e-6, stamps[-1] - stamps[0]), 2)
    notes = [n for r in results for n in r.get("notes") or []]
    if captured_fps is not None and captured_fps < clk.fps * 0.5:
        notes.append(f"real-time capture ran at {captured_fps} screenshots/s against the film's {clk.fps:.3f} fps, "
                     "so motion will step; serve the page locally for the virtual clock")
    web = {"viewport": spec["viewport"], "dpr": first.get("dpr"), "scale": first.get("k"),
           "window": first.get("window"), "clip": first.get("clip"), "mode": spec["frame"], "clock": clock_used,
           "served": "folder" if root else ("loopback proxy" if upstream else "none"),
           "chunks": [[a, b] for a, b in (chunks if clock_used == "virtual" else [(0, count)])],
           "capture_fps": captured_fps, "layout_viewport": first.get("layout_viewport"),
           "browser": "Aside (aside repl)"}
    if clock_used == "real":
        jsonio.write_atomic(shots / "stamps.json", {"stamps": results[0].get("stamps") or []})
    source = _shots_block(film_dir, shots, first, spec, count=len(images), real=clock_used == "real")
    return ios.finish(film_dir, spec, source=None, raw=raw, command="slate capture web",
                      extra={"web": web, "steps": steps_done, "notes": notes, "replay": replay.fake_vendors(),
                             "source": source})


def _shots_block(film_dir: Path, shots: Path, first: dict, spec: dict, *, count: int, real: bool) -> dict:
    """capture.json's source for a web capture: Aside's screenshots, their pixel size and (real time) timestamps."""
    clip, dpr = first.get("clip") or {}, first.get("dpr") or 1
    return {"file": deps.rel(film_dir, shots), "sha256": _dir_hash(shots),
            "width": int(round(clip.get("width", spec["viewport"]["width"]) * dpr)),
            "height": int(round(clip.get("height", spec["viewport"]["height"]) * dpr)),
            "codec": "jpeg", "duration": spec["duration"], "frames": count, "vfr": real,
            "timestamps": deps.rel(film_dir, shots / "stamps.json") if real else None}


def _dir_hash(folder: Path) -> str:
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256()
    for f in sorted(folder.glob("*.jpg")):
        digest.update(f"{f.name} {jsonio.sha256(f)}\n".encode())
    return digest.hexdigest()
