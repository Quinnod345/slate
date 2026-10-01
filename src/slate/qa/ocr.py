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
"""

from __future__ import annotations

import contextlib
import importlib
import os
import platform
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterator, Protocol

from slate import jsonio
from slate.errors import MissingPrerequisite
from slate.qa import media

ENGINE_NAME = "Apple Vision (ocrmac)"
EVERY = 0.25  # seconds of film time between OCR samples (read time needs this; banned words reuse it)
BATCH = 24  # frames decoded at once during a sweep
INSTALL_HINT = ("Install Slate's OCR extra in its environment (`uv pip install 'slate[ocr]'`, which adds ocrmac), "
                "then run `slate qa ocr` again.")
LEVELS = ("accurate", "fast")


class OcrUnavailable(MissingPrerequisite):
    """Vision OCR can't run here. `details["engine"]` names the engine and why."""

    kind = "ocr_unavailable"


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


@dataclass
class VisionBackend:
    """Apple Vision's text recognizer through ocrmac."""

    recognition_level: str
    engine: dict

    def recognize(self, image) -> list[dict]:
        from ocrmac import ocrmac  # noqa: PLC0415 - optional extra, imported only when OCR actually runs

        with stdout_to_stderr():  # Vision's own diagnostics must never reach a --json stdout
            rows = ocrmac.OCR(image, framework="vision", recognition_level=self.recognition_level).recognize()
        out = []
        for row in rows:
            text, conf, box = row[0], float(row[1]), list(row[2]) if len(row) > 2 else None
            out.append({"text": str(text), "conf": round(conf, 3), "box": [round(float(v), 4) for v in box or []]})
        return out


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
