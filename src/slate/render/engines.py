"""The three engines a render can run on, behind one small interface.

- `remotion`: the Node engine through `slate.engine.bridge`. Slate bundles the film once per render (`bundle`), passes
  that bundle to every chunk (`render`) and every still (`stills`) as `serve_url`, and releases it when the render
  ends, however it ends. Each bundle is a few hundred MB, and the films' bundles were never removed: leaked bundles
  once filled 14 GB.
- `web`: the open web renderer, through the engine's `web-render` command (no Remotion licence needed).
- `ae`: After Effects through `slate.ae.acts`, aerender under the AE lock. It starts only on a person's go (`--go`),
  and never for an autonomous film: Adobe's terms bar operations a person didn't start. Slate never quits AE; if it's
  open, the person saves and quits it.

Chunks from the code engines are lossless intermediates, encoded to ProRes once at the join. aerender renders a whole
act in one pass (slate.ae.acts), so AE acts aren't chunked.

Every render is local. There is no remote backend: renders never leave this Mac.
"""

from __future__ import annotations

import inspect
import os
import tempfile
from pathlib import Path

from slate import jsonio, log, paths, procs
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError
from slate.render import deps
from slate.render.plan import CHUNK_CODEC

BUNDLE_TIMEOUT_S = 900
STILLS_TIMEOUT_S = 900
TRACE_TIMEOUT_S = 300
# Generous: a chunk that takes longer than this has hung. The films measured about 0.03 s per sub-frame at
# concurrency 8; this allows eight times that plus start-up.
SECONDS_PER_SUBFRAME_LIMIT = 0.25
CHUNK_STARTUP_S = 300
# Remotion's own name for a bundle it puts in the temp folder (the films' leaks). The engine keeps Slate's bundles in
# SLATE_DATA/cache/bundles; other temp folders, whatever their names, aren't Slate's to delete.
BUNDLE_PREFIXES = ("remotion-webpack-bundle-",)
COMP_PREFIX = "Act"  # the engine's composition ids are Act1, Act2, …
CODE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css", ".svg", ".glsl", ".frag", ".vert"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "tests", "__pycache__"}


def chunk_timeout(frames: int, samples: int | None) -> float:
    return CHUNK_STARTUP_S + frames * (samples or 16) * SECONDS_PER_SUBFRAME_LIMIT


def _code_files(root: Path) -> list[Path]:
    out = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix in CODE_SUFFIXES:
                out.append(p)
    return out


def sources_hash(film_dir: Path, film: dict, engine: str) -> str:
    """A hash of what a render is drawn from: the film's scenes and timing, its brand pack, and the engine's code.

    It keys the chunks, so a resumed render never reuses a chunk drawn from older sources. Plates and captures are
    content-locked by slate.state and never change in place, so they're not re-hashed here. Files a render itself writes
    (motion traces) are left out, or a render would invalidate its own chunks.
    """
    film_dir = Path(film_dir)
    listing: list[tuple[str, str]] = []
    for p in _code_files(film_dir / "scenes"):
        listing.append((f"film/{p.relative_to(film_dir).as_posix()}", jsonio.sha256(p)))
    timing = film_dir / "timing"
    if timing.is_dir():
        for p in sorted(timing.glob("*.json")):
            listing.append((f"film/timing/{p.name}", jsonio.sha256(p)))
    brand = film.get("brand")
    if brand:
        for folder in paths.brand_dirs():
            pack = Path(folder) / str(brand) / "brand.json"
            if pack.is_file():
                listing.append((f"brand/{brand}/brand.json", jsonio.sha256(pack)))
                break
    sub = {"remotion": "remotion", "web": "web", "ae": "ae"}[engine]
    root = deps.engine_dir() / sub
    for p in _code_files(root):
        listing.append((f"engine/{sub}/{p.relative_to(root).as_posix()}", jsonio.sha256(p)))
    return jsonio.sha256_obj({"engine": engine, "files": listing})


class Engine:
    """What a render needs from an engine: open once, render chunks, close whatever happens."""

    name = ""

    def __init__(self, film_dir: Path, film: dict, *, go: bool = False):
        self.film_dir = Path(film_dir)
        self.film = film
        self.go = go
        self.bundle: str | None = None
        self.bundle_hash: str | None = None
        self._sources: str | None = None

    @property
    def codec(self) -> str:
        return CHUNK_CODEC[self.name]

    def rules(self) -> None:
        """Refusals that no amount of waiting changes (checked before a render is even queued)."""

    def check_installed(self) -> None:
        """Stop with exit 3 when the engine isn't installed."""
        deps.bridge()

    def ready(self) -> None:
        """Refusals about right now (something busy), checked just before rendering."""

    def preflight(self) -> None:
        self.rules()
        self.check_installed()
        self.ready()

    def open(self) -> None:
        pass

    def render(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        raise NotImplementedError

    def trace(self, act: int) -> dict | None:
        """Bring the act's motion trace (timing/motion/actN.json) up to date with what was just rendered, so
        `slate qa motion` reads what the picture does. None when the engine writes it while it renders."""
        return None

    def close(self) -> None:
        pass

    def sources(self) -> str:
        if self._sources is None:
            self._sources = sources_hash(self.film_dir, self.film, self.name)
        return self._sources

    def _args(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        args = {"film": str(self.film_dir), "act": act, "from": first, "to": last, "sub": samples, "out": str(out),
                "codec": self.codec}
        if self.bundle:
            args["serve_url"] = self.bundle
        return args


class RemotionEngine(Engine):
    """The Node engine's Remotion renderer (engine/cli.mjs, through slate.engine.bridge).

    One bundle per render: `bundle` makes (or reuses) the film's bundle for its current sources and hands it over as
    `serve_url`; every chunk and still renders from it; `bundle` with `release` deletes it at the end. The engine keeps
    a bundle another live process still holds a lease on, so releasing never pulls one out from under a render.
    """

    name = "remotion"

    def open(self) -> None:
        result = deps.bridge_call("bundle", {"film": str(self.film_dir)}, timeout=BUNDLE_TIMEOUT_S)
        self.bundle = next((str(result[k]) for k in ("serve_url", "bundle", "bundle_dir", "dir") if result.get(k)),
                           None)
        self.bundle_hash = next((str(result[k]) for k in ("bundle_hash", "hash", "source_hash") if result.get(k)),
                                None)

    def render(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        return deps.bridge_call("render", self._args(act, first, last, out, samples),
                                timeout=chunk_timeout(last - first + 1, samples))

    def stills(self, act: int, frames: list[int], out_dir: Path, *, scale: float, samples: int) -> dict:
        args = {"film": str(self.film_dir), "specs": [f"{COMP_PREFIX}{act}:{int(f)}" for f in frames], "scale": scale,
                "sub": samples, "out_dir": str(out_dir)}
        if self.bundle:
            args["serve_url"] = self.bundle
        return deps.bridge_call("stills", args, timeout=STILLS_TIMEOUT_S)

    def trace(self, act: int) -> dict | None:
        """The kit's motion registry, sampled from the act's modules as they are now (the engine's `trace`)."""
        return deps.bridge_call("trace", {"film": str(self.film_dir), "act": act}, timeout=TRACE_TIMEOUT_S)

    def close(self) -> None:
        if not self.bundle:
            return
        bundle, self.bundle = self.bundle, None
        try:
            deps.bridge_call("bundle", {"film": str(self.film_dir), "serve_url": bundle, "release": True},
                             timeout=120)
            return
        except SlateError as exc:
            log.warn(f"the engine couldn't release its bundle ({exc.message}); removing it directly")
        from slate.render import clean  # noqa: PLC0415 (clean imports this module's constants)

        freed = clean.remove_bundle(Path(bundle), film_dir=self.film_dir)
        if freed is None:
            log.warn(f"left the bundle at {bundle} in place: it's in use or doesn't look like a bundle folder")


class WebEngine(Engine):
    """The open web renderer (engine/web, through the engine's `web-render`). It writes the act's motion trace while it
    renders, so there's nothing to refresh afterwards."""

    name = "web"

    def render(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        return deps.bridge_call("web-render", self._args(act, first, last, out, samples),
                                timeout=chunk_timeout(last - first + 1, samples))

    def stills(self, act: int, frames: list[int], out_dir: Path, *, scale: float, samples: int) -> dict:
        """One PNG per frame: the web renderer draws a still as one frame of its range (`from` = `to`), averaging
        `samples` sub-frames, at the film's own size; ffmpeg scales it after when `scale` isn't 1."""
        made = []
        for f in frames:
            file = Path(out_dir) / f"{COMP_PREFIX}{act}_f{int(f):05d}.png"
            result = deps.bridge_call("web-render", {"film": str(self.film_dir), "act": act, "from": int(f),
                                                     "to": int(f), "sub": samples, "out": str(file)},
                                      timeout=chunk_timeout(1, samples))
            if scale != 1:
                _scale_png(file, scale)
            made.append({"comp": f"{COMP_PREFIX}{act}", "frame": int(f), "file": str(file),
                         "gates": result.get("gates") or []})
        return {"stills": made}


def _scale_png(file: Path, scale: float) -> None:
    """Resize a still in place (Lanczos), so `scale` means the same for web stills as for the Remotion engine's."""
    ffmpeg = procs.require("ffmpeg", hint="Install ffmpeg (`slate doctor --install` offers it).")
    tmp = file.with_name(f".{file.stem}.scaled{file.suffix}")
    vf = f"scale=w='max(1,round(iw*{scale:g}))':h='max(1,round(ih*{scale:g}))':flags=lanczos"
    procs.run([ffmpeg, "-y", "-v", "error", "-i", str(file), "-vf", vf, str(tmp)], timeout=120, check=True)
    os.replace(tmp, file)


class AeEngine(Engine):
    """After Effects, through slate.ae.acts: aerender renders a whole AE-built act in one pass.

    slate.ae owns everything After Effects needs: the person's go and the autonomous refusal again (its own guard),
    After Effects closed, AE's disk cache off for the render (through the byte-safe prefs tool, put back after), the AE
    lock shared with the AE bridge, the conform to ProRes 422 HQ, the frame count, and registering the render here.
    This class adds the checks that let Slate refuse before anything starts or is queued. aerender renders comps, not
    frame ranges, so an AE act is never chunked or patched: it's rendered again whole.
    """

    name = "ae"
    whole_acts_only = True

    def rules(self) -> None:
        if self.film.get("mode") == "autonomous":
            raise Refused("This is an autonomous film, and autonomous films never touch After Effects: Adobe's terms "
                          "bar operations a person didn't start.",
                          hint="Render it with --engine remotion or --engine web. Once the film is approved, a person "
                               "can start its AE finish.")
        if not self.go:
            raise Refused("After Effects steps start only on a person's go.",
                          hint="Run it again with --go once the person has said go.")

    def check_installed(self) -> None:
        deps.ae_acts()
        driver = deps.ae_driver()
        finder = getattr(driver, "aerender_path", None)
        try:
            found = finder() if callable(finder) else procs.which("aerender")
        except MissingPrerequisite:
            found = None
        if not found:
            raise MissingPrerequisite("aerender isn't installed (it comes with After Effects).",
                                      hint="slate connect aftereffects")

    def ready(self) -> None:
        if procs.ae_running():
            raise Busy("After Effects is open. aerender starts a second copy of AE, which swaps this Mac to a crawl.",
                       hint="Save your work and quit After Effects yourself (Slate never quits it), then run this "
                            "again.")
        if procs.aerender_running():
            raise Busy("aerender is already running on this Mac.", hint="Wait for it to finish (slate job status).")

    def render_act(self, act: int, *, out: Path | None = None, register: bool = True) -> dict:
        """Render one whole AE-built act (slate.ae.acts.render). Returns its result, registered when asked."""
        render = deps.ae_acts().render
        try:
            inspect.signature(render).bind(self.film_dir, act, go=self.go, out=out, register=register)
        except TypeError as exc:
            raise MissingPrerequisite(f"slate.ae.acts.render doesn't take (film, act, go=, out=, register=): {exc}.",
                                      hint="Update Slate; until then, render this act with --engine remotion.") from exc
        result = render(self.film_dir, act, go=self.go, out=out, register=register)
        return result if isinstance(result, dict) else {"result": result}


ENGINES: dict[str, type[Engine]] = {"remotion": RemotionEngine, "web": WebEngine, "ae": AeEngine}


def get(name: str, film_dir: Path, film: dict, *, go: bool = False) -> Engine:
    return ENGINES[name](film_dir, film, go=go)


def temp_roots() -> list[Path]:
    """Where Remotion puts bundles: the system temp folder ($TMPDIR on macOS)."""
    roots = [Path(tempfile.gettempdir())]
    env = os.environ.get("TMPDIR")
    if env and Path(env).resolve() not in {r.resolve() for r in roots}:
        roots.append(Path(env))
    return roots
