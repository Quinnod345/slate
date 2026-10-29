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
import re
import tempfile
from pathlib import Path

from slate import jsonio, log, paths, procs
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError
from slate.render import deps
from slate.render.plan import CHUNK_CODEC


def _judged_mode(film_dir: Path | str | None, film: dict | None) -> str:
    """The mode the film is judged in: Slate's own record (slate.state.autonomy.film_mode) holds a film made
    autonomous autonomous, whatever its film.json says now. film.json's own mode without the state module."""
    own = str((film or {}).get("mode") or "supervised")
    try:
        from slate.state import autonomy  # noqa: PLC0415 - render works without film state installed
    except ImportError:
        return own
    try:
        return autonomy.film_mode(film_dir, film)
    except Exception:  # noqa: BLE001 - an unreadable record never makes a film less strict than its film.json
        return own

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
CODE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".html", ".htm", ".css", ".svg",
                 ".glsl", ".frag", ".vert"}
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


def film_settings_hash(film_dir: Path, film: dict) -> str:
    """The film's content settings, without pins and other bookkeeping a render can write itself."""
    state_lock = deps.optional("slate.state.lock")
    if state_lock is not None and callable(getattr(state_lock, "hash_inputs", None)):
        return state_lock.hash_inputs(film_dir, ["film:*"])["film:*"]
    return jsonio.sha256_obj(film)


def sources_hash(film_dir: Path, film: dict, engine: str, *, include_web: bool = False) -> str:
    """A hash of what a render is drawn from: the film's scenes and timing, its brand pack, and the engine's code.

    It keys the chunks, so a resumed render never reuses a chunk drawn from older sources. Plates and captures are
    content-locked by slate.state and never change in place, so they're not re-hashed here. Files a render itself writes
    (motion traces) are left out, or a render would invalidate its own chunks.
    """
    film_dir = Path(film_dir)
    listing: list[tuple[str, str]] = [("film/settings", film_settings_hash(film_dir, film))]
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
    engine_root = deps.engine_dir()
    # Web scenes import the shared kit, clock and entry code from remotion/. An interrupted web render must not
    # retain chunks from the old kit when the web renderer's own files haven't changed.
    folders = {"remotion": ("remotion", "web") if include_web else ("remotion",),
               "web": ("web", "remotion"), "ae": ("ae",)}[engine]
    for sub in folders:
        for p in _code_files(engine_root / sub):
            listing.append((f"engine/{p.relative_to(engine_root).as_posix()}", jsonio.sha256(p)))
    for name in ("cli.mjs", "package.json", "package-lock.json"):
        p = engine_root / name
        if p.is_file():
            listing.append((f"engine/{name}", jsonio.sha256(p)))
    return jsonio.sha256_obj({"engine": engine, "files": listing})


def _timing_source(film_dir: Path) -> Path:
    """Resolve the same timing input deps.load_shots read, including a derived film's reference."""
    from slate.render import shot_engines  # noqa: PLC0415

    own = Path(film_dir) / "timing" / "shots.json"
    if own.is_file():
        return shot_engines.canonical_source(own)
    state = deps.optional("slate.state.lock")
    if state is not None and callable(getattr(state, "resolve", None)):
        try:
            real = Path(state.resolve(film_dir, "timing/shots.json"))
        except (OSError, SlateError) as exc:
            raise Refused(f"The mixed trace's resolved timing source is unavailable: {exc}.") from exc
        if real.is_file():
            return shot_engines.canonical_source(real)
    raise Refused("The mixed trace has no resolved timing source to capture.")


def _mixed_trace(film_dir: Path, film: dict, shots: dict, act: int, scenes: list, result: dict) -> dict:
    """Verify a flat owning result and its original captured proof before handing it to the runner."""
    from slate.render import shot_engines  # noqa: PLC0415 - this validation is specific to mixed refresh
    from slate.render.plan import film_format  # noqa: PLC0415

    def fail(reason: str) -> None:
        raise Refused(f"Act {act}'s composed Web trace is incomplete: {reason}.",
                      details={"motion_incomplete": True, "act": act})

    expected = [scene.shot for scene in scenes]
    film_id = str(film.get("id") if film.get("id") is not None else Path(film_dir).name)
    if (not isinstance(result, dict) or "acts" in result or result.get("film") != film_id or
            type(result.get("act")) is not int or
            result["act"] != int(act) or result.get("written") is not True or
            result.get("frame_base") != "film" or result.get("base_engine") != "web" or
            result.get("remotion_shots") != expected or type(result.get("entries")) is not int or
            result["entries"] < 0 or result.get("file") != f"timing/motion/act{int(act)}.json"):
        fail("the producer did not return the exact flat owning-act metadata")
    sources, captured = result.get("sources"), result.get("source_hashes")
    if (not isinstance(sources, list) or not sources or
            any(not isinstance(name, str) or not name or not Path(name).is_absolute() for name in sources) or
            sources != sorted(set(sources)) or not isinstance(captured, dict) or not captured):
        fail("the producer did not return a complete absolute captured-source catalogue")
    if any(not isinstance(name, str) or name not in sources or not isinstance(digest, str) or
           not re.fullmatch(r"[0-9a-f]{64}", digest) for name, digest in captured.items()):
        fail("a captured source hash is malformed or absent from the dependency catalogue")
    folder = (Path(film_dir) / "timing" / "motion" / "sources").resolve()
    files = {}
    for key in ("base_file", "composition_file"):
        name = result.get(key)
        if not isinstance(name, str) or not name:
            fail(f"the {key} is missing")
        file = (Path(film_dir) / name).resolve()
        if file.parent != folder or not file.is_file():
            fail(f"the {key} is not an existing owning proof under timing/motion/sources")
        files[key] = file
    if files["base_file"] == files["composition_file"]:
        fail("raw base and composition evidence must be separate files")
    active = Path(film_dir) / result["file"]
    if not active.is_file():
        fail("the producer's active trace file is missing")
    proof = jsonio.read(files["composition_file"])
    raw = jsonio.read(files["base_file"])
    rows = jsonio.read(active)
    origin, end = shot_engines.owner_frames(shots, act)
    _, _, num, den = film_format(film)
    if (not isinstance(proof, dict) or type(proof.get("act")) is not int or proof["act"] != int(act) or
            proof.get("frame_base") != "film" or proof.get("base_engine") != "web" or
            not isinstance(proof.get("act_frames"), list) or
            any(type(value) is not int for value in proof["act_frames"]) or
            proof["act_frames"] != [origin, end] or type(proof.get("fps_num")) is not int or
            type(proof.get("fps_den")) is not int or proof["fps_num"] != num or proof["fps_den"] != den):
        fail("the composition sidecar is not bound to the owning film clock")
    if not isinstance(raw, list) or not isinstance(rows, list) or len(rows) != result["entries"]:
        fail("the raw base or active trace array does not match the written evidence")
    complete = proof.get("source_hashes")
    if not isinstance(complete, dict) or sorted(complete) != sources:
        fail("the sidecar does not account for every observed or deliberately absent dependency")
    observed = {name: digest for name, digest in complete.items() if digest is not None}
    if observed != captured:
        fail("the sidecar's original captured hashes do not match the producer result")
    for name, digest in complete.items():
        file = Path(name)
        if digest is None:
            if file.exists():
                fail(f"routing source {name} appeared after the captured read")
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not file.is_file():
            fail(f"captured source {name} is missing or malformed")
        try:
            current = jsonio.sha256(file)
        except OSError as exc:
            raise Refused(f"Captured source {name} could not be verified: {exc}.") from exc
        if current != digest:
            fail(f"captured source {name} changed after the original read")
    # Captured keys use the Node producer's physical source identity; metadata below keeps its emitted names.
    required = [shot_engines.canonical_source(Path(film_dir) / "film.json"), _timing_source(film_dir),
                shot_engines.canonical_source(Path(film_dir) / "scenes" / f"act{int(act)}" / "web" / "index.html"),
                *[shot_engines.canonical_source(scene.index) for scene in scenes]]
    if any(complete.get(str(file)) is None for file in required):
        fail("the actual film, resolved timing, base page or replacement index was not captured")
    if any(str(file) not in complete for file in shot_engines.routing_paths(film_dir, shots, act)):
        fail("the source catalogue omits an engine or entry-precedence routing candidate")
    replacement = proof.get("remotion_shots")
    if (not isinstance(replacement, list) or len(replacement) != len(scenes) or
            any(not isinstance(row, dict) for row in replacement)):
        fail("replacement proof rows are missing or malformed")
    replacement_entries = 0
    for scene, row in zip(scenes, replacement):
        if (row.get("shot") != scene.shot or row.get("frames") !=
                [scene.record["frame_in"], scene.record["frame_out"]] or
                not isinstance(row.get("frames"), list) or any(type(value) is not int for value in row["frames"]) or
                row.get("scene") != str(scene.scene.absolute()) or row.get("index") != str(scene.index.absolute()) or
                type(row.get("entries")) is not int or row["entries"] < 0):
            fail(f"replacement {scene.shot} is not bound to its source and film interval")
        hashes = row.get("source_hashes")
        if (not isinstance(hashes, dict) or not hashes or hashes.get(str(shot_engines.canonical_source(scene.index))) is None or
                any(name not in complete or complete[name] != digest for name, digest in hashes.items())):
            fail(f"replacement {scene.shot} lacks its original captured import proof")
        replacement_entries += row["entries"]
    if replacement_entries > result["entries"]:
        fail("the active trace omits declared replacement entries")
    return result


class Engine:
    """What a render needs from an engine: open once, render chunks, close whatever happens."""

    name = ""

    def __init__(self, film_dir: Path, film: dict, *, go: bool = False, scene: Path | None = None):
        self.film_dir = Path(film_dir)
        self.film = film
        self.go = go
        # a shot drawn by this engine inside an act of another (slate.render.shot_engines): the scene folder it draws
        self.scene = Path(scene) if scene else None
        self.bundle: str | None = None
        self.bundle_hash: str | None = None
        self._sources: str | None = None
        self._reverse_capability_checked = False

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
        self._reverse_capability_checked = False

    def _require_reverse(self) -> None:
        if not self._reverse_capability_checked:
            from slate.render import shot_engines  # noqa: PLC0415

            shot_engines.require_reverse()
            self._reverse_capability_checked = True

    def sources(self) -> str:
        if self._sources is None:
            if self.name == "remotion" and self.scene is not None:
                self._sources = sources_hash(self.film_dir, self.film, self.name, include_web=True)
            else:
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

    def _shot(self, act: int | None = None):
        if self.scene is None:
            return None
        from slate.render import shot_engines  # noqa: PLC0415

        shots = deps.load_shots(self.film_dir)
        scene = shot_engines.scene_for(self.film_dir, shots, self.scene, self.name, act)
        if scene.base_engine != "web":
            raise Refused(f"Shot {scene.shot}'s Remotion source is not a replacement in a Web act.")
        origin, _ = shot_engines.owner_frames(shots, scene.act)
        return scene, origin

    @staticmethod
    def _shot_range(item, frames: list[int]) -> None:
        scene, origin = item
        first, last = scene.record["frame_in"] - origin, scene.record["frame_out"] - 1 - origin
        if (not frames or any(type(frame) is not int or not first <= frame <= last for frame in frames)):
            raise Refused(f"Shot {scene.shot} owns act frames {first}–{last}; the request is outside that source.")

    def open(self) -> None:
        if self.scene is not None:
            self._shot()  # Resolve source and whole-act clock before the compatibility read or bundle.
            self._require_reverse()
        result = deps.bridge_call("bundle", {"film": str(self.film_dir)}, timeout=BUNDLE_TIMEOUT_S)
        self.bundle = next((str(result[k]) for k in ("serve_url", "bundle", "bundle_dir", "dir") if result.get(k)),
                           None)
        self.bundle_hash = next((str(result[k]) for k in ("bundle_hash", "hash", "source_hash") if result.get(k)),
                                None)

    def _args(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        args = super()._args(act, first, last, out, samples)
        if self.scene is not None:
            item = self._shot(act)
            self._shot_range(item, [first, last])
            if last < first:
                raise Refused("A replacement render needs a forward act-frame range.")
            self._require_reverse()
            args.pop("act")  # render_mb selects args.act before args.comp; this must select the Shot composition.
            args.update(comp=f"Shot-{item[0].shot}", trace=False)
        return args

    def render(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        return deps.bridge_call("render", self._args(act, first, last, out, samples),
                                timeout=chunk_timeout(last - first + 1, samples))

    def stills(self, act: int, frames: list[int], out_dir: Path, *, scale: float, samples: int) -> dict:
        if self.scene is not None:
            item = self._shot(act)
            self._shot_range(item, frames)
            self._require_reverse()
            specs = [{"comp": f"Shot-{item[0].shot}", "frame": f,
                      "name": f"{COMP_PREFIX}{act}_f{f:05d}.png"} for f in frames]
        else:
            specs = [f"{COMP_PREFIX}{act}:{int(f)}" for f in frames]
        args = {"film": str(self.film_dir), "specs": specs, "scale": scale,
                "sub": samples, "out_dir": str(out_dir)}
        if self.bundle:
            args["serve_url"] = self.bundle
        return deps.bridge_call("stills", args, timeout=STILLS_TIMEOUT_S)

    def trace(self, act: int) -> dict | None:
        """An isolated replacement never writes an owning active trace; the runner refreshes its real base engine."""
        if self.scene is not None:
            return None
        return deps.bridge_call("trace", {"film": str(self.film_dir), "act": act}, timeout=TRACE_TIMEOUT_S)

    def close(self) -> None:
        self._reverse_capability_checked = False
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
    """The open web renderer. Pure acts keep their trace while rendering; mixed owning acts defer active coverage
    until composed refresh. Isolated Web shot pages never write an owning active trace."""

    name = "web"

    def _mixed(self, act: int):
        from slate.render import shot_engines  # noqa: PLC0415

        if self.scene is not None or shot_engines.act_engine(self.film_dir, act) != "web":
            return None
        try:
            shots = deps.load_shots(self.film_dir)
        except MissingPrerequisite:
            if shot_engines.remotion_hints(self.film_dir):
                raise Refused(f"Act {act}'s Remotion sources need resolved timing before their owner can be known.")
            return None  # Existing pure Web trace is a no-op even before timing exists.
        scenes = [scene for scene in shot_engines.mixed_scenes(self.film_dir, shots, act)
                  if scene.engine == "remotion"]
        if not scenes:
            return None
        origin, end = shot_engines.owner_frames(shots, act)
        if any(scene.record["frame_in"] < origin or scene.record["frame_out"] > end for scene in scenes):
            raise Refused(f"Act {act}'s replacements fall outside its owning film frames.")
        return shots, scenes, origin, end

    @staticmethod
    def _mixed_range(context, frames: list[int]) -> None:
        _, _, origin, end = context
        if not frames or any(type(frame) is not int or not 0 <= frame < end - origin for frame in frames):
            raise Refused("A mixed owning Web render needs frames inside its whole act.")

    def _args(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        args = super()._args(act, first, last, out, samples)
        if self.scene is not None:
            args.update(scene=str(self.scene), trace=False)
        else:
            context = self._mixed(act)
            if context is not None:
                self._mixed_range(context, [first, last])
                if last < first:
                    raise Refused("A mixed owning Web render needs a forward act-frame range.")
                self._require_reverse()
                args["trace"] = False
        return args

    def render(self, act: int, first: int, last: int, out: Path, samples: int | None) -> dict:
        return deps.bridge_call("web-render", self._args(act, first, last, out, samples),
                                timeout=chunk_timeout(last - first + 1, samples))

    def trace(self, act: int) -> dict | None:
        context = self._mixed(act)
        if context is None:
            return None
        self._require_reverse()
        result = deps.bridge_call("trace", {"film": str(self.film_dir), "act": act}, timeout=TRACE_TIMEOUT_S)
        return _mixed_trace(self.film_dir, self.film, context[0], act, context[1], result)

    def stills(self, act: int, frames: list[int], out_dir: Path, *, scale: float, samples: int) -> dict:
        """PNG stills keep the act's filenames, sample count and scaling; a mixed base draw cannot publish coverage."""
        context = self._mixed(act)
        if context is not None:
            self._mixed_range(context, frames)
            self._require_reverse()
        made = []
        for f in frames:
            file = Path(out_dir) / f"{COMP_PREFIX}{act}_f{int(f):05d}.png"
            args = {"film": str(self.film_dir), "act": act, "from": int(f), "to": int(f), "sub": samples,
                    "out": str(file)}
            if self.scene is not None:
                args.update(scene=str(self.scene), trace=False)
            elif context is not None:
                args["trace"] = False
            result = deps.bridge_call("web-render", args, timeout=chunk_timeout(1, samples))
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
        if _judged_mode(self.film_dir, self.film) == "autonomous":
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


def get(name: str, film_dir: Path, film: dict, *, go: bool = False, scene: Path | None = None) -> Engine:
    return ENGINES[name](film_dir, film, go=go, scene=scene)


def temp_roots() -> list[Path]:
    """Where Remotion may put bundles of its own: the engine's temp folder under Slate's data folder (the engine points
    TMPDIR there, so no other tool's temp cleanup can reach a live render's files), and the system temp folder ($TMPDIR
    on macOS) for engines from before that."""
    roots = [paths.cache_dir() / "tmp", Path(tempfile.gettempdir())]
    env = os.environ.get("TMPDIR")
    if env and Path(env).resolve() not in {r.resolve() for r in roots}:
        roots.append(Path(env))
    return roots
