"""One OCR engine: Apple Vision, through ocrmac, in Slate's own Python environment.

The films ran two OCR engines (a compiled Swift binary at the `.fast` level for the validator, ocrmac at `accurate`
for the banned-word scanner), and tesseract was installed too. Two engines silently give two answers, so Slate has
one. When it isn't available (ocrmac isn't installed, or Vision can't run at the requested level) every OCR-based
gate is UNCHECKED and says how to fix it. There is no fallback to another engine or another level: a result is only
ever the engine and level its evidence names.

    install:  uv pip install 'slate[ocr]'    (the optional `ocr` extra in pyproject.toml)
    level:    SLATE_OCR_LEVEL=accurate (default) or fast; recorded in every result's engine field

The sweep reads the picture at full width (at 1280 px, letter-spaced caps read as "DURA81LITY") every 0.25 s of
film time and keeps only the text, cached in qa/ocr/ against the hashes of the files it read.

Vision runs in a reader process of its own, never in Slate's. In a long-lived process Vision sometimes never returns
from a frame (0% CPU, forever), though a fresh process reads the same frame in about two seconds; on 2026-10-28 that
froze two films' QA for hours. So a frame gets SLATE_OCR_TIMEOUT seconds (default 45). If it takes longer, the reader
is killed and a fresh one reads the frame again, up to RETRIES times before the run stops with OcrStalled (exit 75).
A reader can also go blind, answering every frame with nothing: a frame read as empty is only trusted when the same
reader still reads a test image ("SLATE 42"); otherwise the reader is replaced and the frame read again. A reader is
also replaced after RECYCLE frames, and only one Slate process reads with Vision at a time.
"""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import importlib
import json
import os
import platform
import select
import subprocess
import sys
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterator, Protocol

from slate import jsonio, paths
from slate.errors import MissingPrerequisite, TryLater
from slate.qa import media

ENGINE_NAME = "Apple Vision (ocrmac)"
EVERY = 0.25  # seconds of film time between OCR samples (read time needs this; banned words reuse it)
BATCH = 24  # frames decoded at once during a sweep
INSTALL_HINT = ("Install Slate's OCR extra in its environment (`uv pip install 'slate[ocr]'`, which adds ocrmac), "
                "then run `slate qa ocr` again.")
LEVELS = ("accurate", "fast")
TIMEOUT = 45.0  # seconds one frame may take before its reader is replaced (SLATE_OCR_TIMEOUT overrides)
RETRIES = 2  # fresh readers tried on a frame that stalls, before the run stops
RECYCLE = 300  # frames one reader reads before a fresh one takes over


class OcrUnavailable(MissingPrerequisite):
    """Vision OCR can't run here. `details["engine"]` names the engine and why."""

    kind = "ocr_unavailable"


class OcrStalled(TryLater):
    """Vision never answered on one frame, in RETRIES + 1 fresh readers in a row. Nothing is wrong with the film."""

    kind = "ocr_stalled"


class Backend(Protocol):
    engine: dict

    def recognize(self, image) -> list[dict]:  # [{text, conf, box: [x, y, w, h]}], box normalised, origin bottom-left
        ...


def level() -> str:
    value = (os.environ.get("SLATE_OCR_LEVEL") or "accurate").strip().lower()
    return value if value in LEVELS else "accurate"


def _macos() -> str:
    return platform.mac_ver()[0] or platform.platform()


def _ocrmac_version() -> str:
    try:
        return metadata.version("ocrmac")
    except metadata.PackageNotFoundError:
        mod = importlib.import_module("ocrmac")
        return str(getattr(mod, "__version__", "unknown"))


def _flush_c_stdio() -> None:
    """Flush the C library's stdio buffers (fflush(NULL)). Vision prints through C stdio, which holds its text in a
    buffer when stdout isn't a terminal and writes it out later, at exit, long after a redirect has ended."""
    try:
        import ctypes  # noqa: PLC0415

        ctypes.CDLL(None).fflush(None)
    except (OSError, AttributeError):  # pragma: no cover - no libc to flush: nothing buffered there either
        pass


@contextlib.contextmanager
def stdout_to_stderr() -> Iterator[None]:
    """Send file descriptor 1 to stderr for the duration of the block.

    Apple Vision prints diagnostics ("Unable to find a valid E5 …") from native code through C stdio, below Python's
    sys.stdout, so a `slate … --json` run that reads the picture would get them in its JSON. Everything the block
    writes to fd 1 lands on stderr instead, where logs go, and the C buffers are flushed before fd 1 comes back (else
    the text would sit in them and reach stdout at exit). stdout carries only the command's own output."""
    try:
        sys.stdout.flush()
    except (AttributeError, ValueError, OSError):
        pass
    try:
        saved = os.dup(1)
    except OSError:  # no fd 1 to protect (a detached process): nothing to redirect
        yield
        return
    try:
        os.dup2(2, 1)
        yield
    finally:
        try:
            sys.stdout.flush()
        except (AttributeError, ValueError, OSError):
            pass
        _flush_c_stdio()
        os.dup2(saved, 1)
        os.close(saved)


def timeout() -> float:
    try:
        value = float(os.environ.get("SLATE_OCR_TIMEOUT") or TIMEOUT)
    except ValueError:
        return TIMEOUT
    return value if value > 0 else TIMEOUT


@contextlib.contextmanager
def _one_at_a_time() -> Iterator[None]:
    """Hold Slate's OCR lock for one frame: two processes reading with Vision at once is when it froze most."""
    lock = paths.ensure_dir(paths.cache_dir()) / "ocr.lock"
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _serve() -> None:
    """The reader process: answer one JSON line per frame. Each frame arrives as a JSON header line ({w, h}) followed
    by w × h × 3 bytes of RGB. Answers go out on a copy of fd 1; fd 1 itself then points at stderr, so whatever Vision
    prints from native code can never land in an answer."""
    lvl = sys.argv[1] if len(sys.argv) > 1 else level()
    answers = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    from ocrmac import ocrmac  # noqa: PLC0415 - optional extra, imported only when OCR actually runs
    from PIL import Image  # noqa: PLC0415

    src = sys.stdin.buffer
    while True:
        head = src.readline()
        if not head:
            return
        spec = json.loads(head)
        size = (int(spec["w"]), int(spec["h"]))
        image = Image.frombytes("RGB", size, src.read(size[0] * size[1] * 3))
        try:
            rows = ocrmac.OCR(image, framework="vision", recognition_level=lvl).recognize()
            msg = {"rows": [[str(r[0]), float(r[1]), [float(v) for v in (r[2] if len(r) > 2 else None) or []]]
                            for r in rows]}
        except Exception as exc:  # noqa: BLE001 - reported to Slate, which raises it there
            msg = {"error": f"{type(exc).__name__}: {exc}"}
        answers.write(json.dumps(msg) + "\n")
        answers.flush()


@dataclass
class _Reader:
    """One reader process, started when a frame needs it. read() returns the rows, or None when the reader stalled
    or died (it's gone by then, and the next read starts a fresh one)."""

    recognition_level: str
    proc: subprocess.Popen | None = None
    frames: int = 0
    started: int = 0

    def _start(self) -> subprocess.Popen:
        cmd = [sys.executable, "-P", "-c", "from slate.qa.ocr import _serve; _serve()", self.recognition_level]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        os.set_blocking(self.proc.stdin.fileno(), False)
        self.frames = 0
        self.started += 1
        return self.proc

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        with contextlib.suppress(OSError):
            proc.stdin.close()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        with contextlib.suppress(OSError):
            proc.stdout.close()

    def read(self, payload: bytes, seconds: float) -> list | None:
        if self.proc is not None and (self.proc.poll() is not None or self.frames >= RECYCLE):
            self.stop()
        proc = self.proc or self._start()
        deadline = time.monotonic() + seconds
        if not self._send(proc, payload, deadline):
            self.stop()
            return None
        answer = self._answer(proc, deadline)
        if answer is None:
            self.stop()
            return None
        self.frames += 1
        if "error" in answer:
            raise RuntimeError(f"Vision couldn't read the frame: {answer['error']}")
        return answer["rows"]

    @staticmethod
    def _send(proc: subprocess.Popen, payload: bytes, deadline: float) -> bool:
        fd, view = proc.stdin.fileno(), memoryview(payload)
        while view:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [fd], [], left)[1]:
                return False
            try:
                view = view[os.write(fd, view):]
            except BlockingIOError:
                continue
            except OSError:  # the reader died
                return False
        return True

    @staticmethod
    def _answer(proc: subprocess.Popen, deadline: float) -> dict | None:
        fd, buf = proc.stdout.fileno(), b""
        while b"\n" not in buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
            chunk = os.read(fd, 1 << 16)
            if not chunk:  # the reader died
                return None
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])


@dataclass
class VisionBackend:
    """Apple Vision's text recognizer through ocrmac, in a reader process (see the module's docstring)."""

    recognition_level: str
    engine: dict
    reader: _Reader | None = field(default=None, repr=False)

    def recognize(self, image) -> list[dict]:
        from PIL import Image  # noqa: PLC0415

        if not isinstance(image, Image.Image):
            image = Image.open(image)
        image = image if image.mode == "RGB" else image.convert("RGB")
        payload = json.dumps({"w": image.width, "h": image.height}).encode() + b"\n" + image.tobytes()
        if self.reader is None:
            self.reader = _Reader(self.recognition_level)
            atexit.register(self.reader.stop)
        seconds = timeout()
        for _attempt in range(RETRIES + 1):
            with _one_at_a_time():
                rows = self.reader.read(payload, seconds)
                if rows == [] and not self._sees_canary(seconds):
                    # It answers, but reads nothing, not even the canary: a blind reader (2026-10-28, the CEO film's
                    # master sweep: one reader returned nothing for its first 300 frames). Replace it, read again.
                    self.reader.stop()
                    rows = None
            if rows is not None:
                break
        else:
            raise OcrStalled(f"Apple Vision never read one {image.width}×{image.height} frame: {RETRIES + 1} fresh "
                             f"readers each stalled for {seconds:g} s or read nothing, not even a test image.",
                             details={"engine": self.engine, "timeout_s": seconds, "readers": RETRIES + 1})
        out = []
        for text, conf, box in rows:
            out.append({"text": str(text), "conf": round(float(conf), 3),
                        "box": [round(float(v), 4) for v in box or []]})
        return out

    def _sees_canary(self, seconds: float) -> bool:
        """Whether this reader still reads the probe's test word: an empty frame is only trusted when it does."""
        rows = self.reader.read(_canary(), seconds)
        return bool(rows) and any(CANARY_MARK in str(r[0]) for r in rows)


CANARY_MARK = "42"
_CANARY: list[bytes] = []


def _canary() -> bytes:
    """The probe's own test image ("SLATE 42" at 720 × 180), as a reader payload, built once."""
    if not _CANARY:
        from PIL import Image, ImageDraw  # noqa: PLC0415

        img = Image.new("RGB", (240, 60), "white")
        ImageDraw.Draw(img).text((12, 20), "SLATE 42", fill="black")
        img = img.resize((720, 180))
        _CANARY.append(json.dumps({"w": img.width, "h": img.height}).encode() + b"\n" + img.tobytes())
    return _CANARY[0]


_STATE: dict = {}
_OVERRIDE: list[Backend] = []


def _engine(lvl: str, version: str) -> dict:
    return {"name": ENGINE_NAME, "version": f"ocrmac {version}", "recognition_level": lvl, "macos": _macos()}


def _probe(lvl: str) -> tuple[Backend | None, dict]:
    """Import ocrmac and read a tiny rendered word, so 'available' means Vision really runs at this level."""
    try:
        with stdout_to_stderr():  # loading Vision can print to fd 1 too
            importlib.import_module("ocrmac.ocrmac")
    except Exception as exc:  # noqa: BLE001 - ImportError, or pyobjc failing to load Vision
        return None, {"name": ENGINE_NAME, "version": "not installed", "recognition_level": lvl, "macos": _macos(),
                      "reason": f"ocrmac isn't importable in Slate's environment ({type(exc).__name__}: {exc})",
                      "hint": INSTALL_HINT}
    engine = _engine(lvl, _ocrmac_version())
    backend = VisionBackend(lvl, engine)
    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415

        img = Image.new("RGB", (240, 60), "white")
        ImageDraw.Draw(img).text((12, 20), "SLATE 42", fill="black")
        backend.recognize(img.resize((720, 180)))
    except Exception as exc:  # noqa: BLE001
        return None, {**engine, "reason": f"Vision couldn't run at the {lvl!r} level ({type(exc).__name__}: {exc})",
                      "hint": "Try SLATE_OCR_LEVEL=fast explicitly (it is recorded in every result), or run "
                              "`slate doctor` to check the OCR extra."}
    return backend, engine


def available(refresh: bool = False) -> tuple[bool, dict]:
    """(True, engine) when Vision OCR runs here at the configured level; (False, engine + reason + hint) when not."""
    if _OVERRIDE:
        return True, dict(_OVERRIDE[-1].engine)
    lvl = level()
    if refresh or _STATE.get("level") != lvl:
        backend, info = _probe(lvl)
        _STATE.update(level=lvl, backend=backend, info=info)
    return _STATE["backend"] is not None, dict(_STATE["info"])


def backend() -> Backend:
    """The active OCR backend, or OcrUnavailable (never another engine)."""
    if _OVERRIDE:
        return _OVERRIDE[-1]
    ok, info = available()
    if not ok:
        raise OcrUnavailable(f"{ENGINE_NAME} isn't available: {info.get('reason')}", hint=info.get("hint"),
                             details={"engine": info})
    return _STATE["backend"]


@contextlib.contextmanager
def use_backend(b: Backend) -> Iterator[Backend]:
    """Run with another backend (tests use a scripted one). It must name itself in `engine`."""
    _OVERRIDE.append(b)
    try:
        yield b
    finally:
        _OVERRIDE.remove(b)


def recognize(image) -> list[dict]:
    return backend().recognize(image)


# ---------------------------------------------------------------------------------------------- the sweep
@dataclass
class Sweep:
    """OCR of the picture: samples [{t, frame, lines}] in film order, what was read, and what couldn't be."""

    engine: dict
    every: float
    source: str  # "master" or "renders"
    files: list[str]
    samples: list[dict]
    missing_acts: list[int]
    cache: str | None
    expected: int

    @property
    def complete(self) -> bool:
        return not self.missing_acts and len(self.samples) >= self.expected

    def text_at(self, sample: dict) -> str:
        return " ".join(str(ln.get("text", "")) for ln in sample.get("lines") or [])


def _sources(ctx) -> tuple[str, list[tuple[Path, int, int]], list[int]]:
    """Where the picture is: the first master (the whole film), else each act's current render."""
    total = ctx.total_frames
    masters = ctx.masters()
    if masters and total:
        return "master", [(masters[0], 0, int(total))], []
    current = ctx.current_renders()
    parts, missing = [], []
    for a in ctx.acts:
        e = current.get(a["index"])
        if e is None or not e["path"].is_file():
            missing.append(a["index"])
            continue
        parts.append((e["path"], a["frame_in"], a["frame_out"] - a["frame_in"]))
    return "renders", parts, missing


def sample_frames(ctx, every: float) -> list[tuple[float, int]]:
    """(film seconds, film frame) for every sample, one per `every` seconds, each frame once."""
    total = ctx.total_frames or 0
    seconds = ctx.secs_of(total) if total else 0.0
    out, seen, k = [], set(), 0
    while True:
        t = round(k * every, 6)
        if t >= seconds - 1e-9:
            break
        f = min(ctx.frame_of(t), total - 1)
        if f not in seen:
            seen.add(f)
            out.append((t, f))
        k += 1
    return out


def sweep(ctx, *, every: float = EVERY, progress: Callable[[str], None] | None = None) -> Sweep:
    """OCR the film's picture every `every` seconds (cached). Raises OcrUnavailable when Vision can't run, and
    MissingPrerequisite when there's no picture at all yet."""
    engine_backend = backend()
    engine = dict(engine_backend.engine)
    if ctx.clock is None or not ctx.total_frames:
        raise MissingPrerequisite("There's no film clock yet (film.json fps and timing/shots.json), so the picture "
                                  "can't be sampled.", hint="Run `slate timing` first.")
    source, parts, missing = _sources(ctx)
    if not parts:
        raise MissingPrerequisite("There's no picture to read yet: no master and no act renders.",
                                  hint="Render the acts (`slate render`) and run `slate qa` again.")
    samples_wanted = sample_frames(ctx, every)
    key = {"source": source, "every": every, "engine": engine,
           "files": [{"file": ctx.rel(p), "sha256": ctx.sha(p), "film_frame": f0, "frames": n} for p, f0, n in parts]}
    cache = ctx.path(f"qa/ocr/{source}_{str(every).replace('.', 'p')}s.json")
    cached = jsonio.read(cache, default=None) if cache.is_file() else None
    if isinstance(cached, dict) and cached.get("key") == key:
        samples = cached.get("samples") or []
    elif getattr(ctx, "quick", False):
        raise MissingPrerequisite("The picture hasn't been read for this version of the film, and --quick doesn't "
                                  "start the OCR sweep.", hint="Run `slate qa ocr` (without --quick) to read it.")
    else:
        from PIL import Image  # noqa: PLC0415

        samples = []
        for path, f0, n in parts:
            local = [(t, f) for t, f in samples_wanted if f0 <= f < f0 + n]
            if not local:
                continue
            if progress:
                progress(f"OCR {ctx.rel(path)}: {len(local)} frames every {every} s")
            info = media.video_info(path, count=False)
            # a few frames at a time: full-width frames are megabytes each, and a film has thousands of samples
            for i in range(0, len(local), BATCH):
                batch = local[i:i + BATCH]
                frames = media.read_frames(path, [f - f0 for _, f in batch], pix_fmt="rgb24", info=info)
                for t, f in batch:
                    arr = frames.get(f - f0)
                    if arr is not None:
                        samples.append({"t": t, "frame": f, "lines": engine_backend.recognize(Image.fromarray(arr))})
        samples.sort(key=lambda s: s["frame"])
        jsonio.write_atomic(cache, {"generated": "do not edit by hand: slate qa ocr", "key": key, "samples": samples})
    expected = sum(1 for _t, f in samples_wanted
                   if any(f0 <= f < f0 + n for _p, f0, n in parts))
    return Sweep(engine=engine, every=every, source=source, files=[ctx.rel(p) for p, _f, _n in parts],
                 samples=samples, missing_acts=missing, cache=ctx.rel(cache), expected=expected)


def unavailable_engine(exc: Exception) -> dict:
    """The engine field for an UNCHECKED result: the engine that would have run, and why it didn't."""
    info = (getattr(exc, "details", None) or {}).get("engine")
    return info if info else available()[1]
