"""Fakes for the render tests (imported by conftest.py and the tests): a numpy media backend, and small stand-ins
for the engine bridge, the job queue, the film state and After Effects, installed as the section 12 module names so the
render code calls them exactly as it calls the real ones.

A fake "video" is a .npy stack of frames (one row of 10-bit samples per frame). The fake ProRes encode nudges half the
samples by one level, so an encoded frame measures about 63 dB against its source: high, but not identical, like the
real thing.
"""

from __future__ import annotations

import contextlib
import json
import sys
import types
from pathlib import Path

import numpy as np

from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, UsageError
from slate.render import media as media_mod

SAMPLES_PER_FRAME = 48


def frame_pixels(act: int, frame: int, variant: int = 0) -> np.ndarray:
    """A deterministic frame: neighbours differ by 37 levels a sample, so a one-frame shift is obvious."""
    base = (np.arange(SAMPLES_PER_FRAME, dtype=np.int64) * 11 + frame * 37 + act * 101 + variant * 500) % 1024
    return base.astype(np.uint16)


class FakeMedia:
    name = "fake"

    def __init__(self):
        self.encodes = 0
        self.copies = 0

    @staticmethod
    def load(path: Path) -> np.ndarray:
        with open(path, "rb") as fh:
            return np.load(fh)

    @staticmethod
    def save(path: Path, arr: np.ndarray) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            np.save(fh, arr)

    def version(self) -> str:
        return "test"

    def count_frames(self, path: Path) -> int:
        path = Path(path)
        if not path.is_file():
            return 0
        try:
            return int(self.load(path).shape[0])
        except (ValueError, EOFError, OSError):
            return 0

    def read_frames(self, path: Path, indices) -> dict:
        arr = self.load(path)
        out = {}
        for i in sorted(set(indices)):
            if not 0 <= i < arr.shape[0]:
                raise Refused(f"{Path(path).name}: no frame {i}")
            out[i] = arr[i]
        return out

    def join_encode(self, parts, out, *, frames=None) -> None:
        self.encodes += 1
        arr = np.concatenate([self.load(p) for p in parts])
        enc = arr.copy()
        enc[:, ::2] ^= 1
        self.save(out, enc)

    def join_copy(self, parts, out) -> None:
        self.copies += 1
        self.save(out, np.concatenate([self.load(p) for p in parts]))

    def cut_copy(self, src, first, count, out, clock) -> None:
        arr = self.load(src)
        self.save(out, arr[first:None if count is None else first + count])

    def is_intra(self, path: Path) -> bool:
        return Path(path).suffix == ".mov"


class FakeBridge:
    """engine/cli.mjs as the engine builder wrote it: `bundle` (make, or release with {release: true}), `render` and
    `web-render` (from/to inclusive act frames, `sub`, `out`, `codec`, the shared bundle as `serve_url`) and `stills`
    (`specs` like "Act1:0"). Writes fake videos."""

    def __init__(self, fake_media: FakeMedia, tmp: Path):
        self.media = fake_media
        self.tmp = tmp
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[tuple[int, int], int] = {}  # (act, from) -> failures left
        self.short: set[tuple[int, int]] = set()  # (act, from) -> write one frame too few
        self.variant: dict[int, dict[int, int]] = {}  # act -> {frame: variant}
        self.shift: dict[int, int] = {}  # act -> render every frame from this many frames later (a misnumbered patch)
        self.bundles: list[Path] = []
        self.bundle_hash = "bundle-abc123"
        self.fail_release = False

    def call(self, command: str, args: dict, *, timeout=None) -> dict:
        self.calls.append((command, dict(args)))
        if command == "bundle":
            if args.get("release"):
                if self.fail_release:
                    raise SlateError("node engine/cli.mjs bundle exited with 1")
                target = Path(args["serve_url"])
                released = []
                if target.exists():
                    import shutil  # noqa: PLC0415

                    shutil.rmtree(target)
                    released.append(target.name)
                return {"released": released}
            from slate import paths  # noqa: PLC0415

            folder = paths.cache_dir() / "bundles" / f"{Path(args['film']).name}-{len(self.bundles):04d}"
            folder.mkdir(parents=True)
            (folder / "bundle.js").write_text("x" * 4096)
            (folder / ".slate-bundle.json").write_text("{}")
            self.bundles.append(folder)
            return {"serve_url": str(folder), "bundle_hash": self.source_hash(Path(args["film"])), "reused": False}
        if command in ("render", "web-render"):
            act, a, b = int(args["act"]), int(args["from"]), int(args["to"])
            key = (act, a)
            if self.fail.get(key, 0) > 0:
                self.fail[key] -= 1
                # the engine keeps a short render beside the output for a look
                out = Path(args["out"])
                self.media.save(out.with_name(out.stem + ".short" + out.suffix), np.stack([frame_pixels(act, a)]))
                raise SlateError("node engine/cli.mjs render exited with 1: Chrome crashed")
            shift = self.shift.get(act, 0)
            variant = self.variant.get(act, {})
            frames = [frame_pixels(act, f + shift, variant.get(f + shift, 0)) for f in range(a, b + 1)]
            if key in self.short:
                frames = frames[:-1]
            self.media.save(Path(args["out"]), np.stack(frames))
            return {"file": args["out"], "frame_count": len(frames), "frames": [a, b], "samples": args.get("sub"),
                    "bundle_hash": self.source_hash(Path(args["film"]))}
        if command == "stills":
            out_dir = Path(args["out_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            stills = []
            for spec in args["specs"]:
                comp, frame = spec.split(":")
                p = out_dir / f"{comp}_f{int(frame):05d}.png"
                p.write_bytes(b"\x89PNG fake")
                stills.append({"comp": comp, "frame": int(frame), "file": str(p)})
            return {"out_dir": str(out_dir), "stills": stills, "bundle_hash": self.source_hash(Path(args["film"]))}
        raise SlateError(f"unknown engine command {command}")

    def source_hash(self, film_dir: Path) -> str:
        """Like the engine's: the film's scenes and timing, plus the kit (`self.bundle_hash` stands in for it)."""
        import hashlib  # noqa: PLC0415

        h = hashlib.sha256(self.bundle_hash.encode())
        for p in sorted([*(film_dir / "scenes").rglob("*"), *(film_dir / "timing").glob("*.json")]):
            if p.is_file():
                h.update(str(p.relative_to(film_dir)).encode() + p.read_bytes())
        return h.hexdigest()

    def engine_dir(self) -> Path:
        d = self.tmp / "engine"
        (d / "remotion").mkdir(parents=True, exist_ok=True)
        return d

    def renders(self) -> list[dict]:
        return [a for c, a in self.calls if c in ("render", "web-render")]

    def made_bundles(self) -> int:
        return sum(1 for c, a in self.calls if c == "bundle" and not a.get("release"))

    def releases(self) -> list[dict]:
        return [a for c, a in self.calls if c == "bundle" and a.get("release")]


class FakeJobs:
    def __init__(self):
        self.submitted: list[dict] = []
        self.reserved: list[tuple[str, float]] = []
        self.held: list[tuple[str, str | None]] = []
        self.refuse_disk: SlateError | None = None
        self.busy = False

    def submit(self, kind, argv, *, film=None, resources=(), priority="supervised", disk_gb=0.0, cwd=None):
        self.submitted.append({"kind": kind, "argv": list(argv), "film": film, "resources": tuple(resources),
                               "priority": priority, "disk_gb": disk_gb, "cwd": cwd})
        return f"job-{len(self.submitted)}"

    def wait(self, job_id, timeout=None):
        return {"id": job_id, "state": "done"}

    @contextlib.contextmanager
    def hold(self, resource, *, timeout=None, owner=None):
        if self.busy:
            raise Busy(f"{resource} is held by another job")
        self.held.append((resource, owner))
        yield

    @contextlib.contextmanager
    def reserve(self, path, gb):
        if self.refuse_disk is not None:
            raise self.refuse_disk
        self.reserved.append((str(path), gb))
        yield


class FakeState:
    """slate.state.lock as the real one behaves: record() needs the file in place, and the timeline counts as locked
    once renders/registry.json holds an act render."""

    def __init__(self):
        self.records: list[dict] = []
        self.locked: list[tuple[str, str]] = []
        self.timeline_checks = 0

    def record(self, film_dir, rel_path, *, producer, inputs, tool_version=None):
        if not (Path(film_dir) / rel_path).exists():
            raise UsageError(f"{rel_path} doesn't exist; record a file after writing it.")
        row = {"path": str(rel_path), "producer": producer, "inputs": inputs, "tool_version": tool_version}
        self.records.append(row)
        return row

    def lock_asset(self, film_dir, rel_path, *, reason):
        self.locked.append((str(rel_path), reason))

    def is_locked(self, film_dir, rel_path):
        return any(p == str(rel_path) for p, _r in self.locked)

    def timeline_locked(self, film_dir):
        self.timeline_checks += 1
        reg = Path(film_dir) / "renders" / "registry.json"
        return reg.is_file() and bool(json.loads(reg.read_text()).get("entries"))


class FakeAe:
    """slate.ae as the ae builder wrote it: driver.ae_lock / aerender_path, and acts.render, which renders a whole
    AE-built act (its own go check and AE lock), conforms it, and registers it through slate.render.registry."""

    def __init__(self, fake_media: FakeMedia):
        self.media = fake_media
        self.renders: list[dict] = []
        self.lock_events: list[str] = []
        self.register_itself = True

    @contextlib.contextmanager
    def ae_lock(self, *, timeout=60.0, owner=None, bridge=True):
        self.lock_events.append("enter")
        try:
            yield {"owner": owner}
        finally:
            self.lock_events.append("exit")

    def aerender_path(self) -> str:
        from slate import procs  # noqa: PLC0415

        found = procs.which("aerender")
        if not found:
            raise MissingPrerequisite("aerender (After Effects' command-line renderer) isn't on this Mac.")
        return found

    def render(self, film_dir, act, *, go=False, runner=None, out=None, register=True, cache_off=True):
        self.renders.append({"act": act, "go": go, "out": out, "register": register})
        if not go:
            raise Refused("Rendering an act with aerender starts only on a person's go.")
        from slate import gates, jsonio  # noqa: PLC0415
        from slate.render import registry  # noqa: PLC0415
        from slate.render.plan import acts_from_shots, find_act  # noqa: PLC0415

        film_dir = Path(film_dir)
        a = find_act(acts_from_shots(json.loads((film_dir / "timing" / "shots.json").read_text())), act)
        final = Path(out) if out else film_dir / "renders" / "ae" / f"act{act}.mov"
        with self.ae_lock(bridge=False):
            self.media.save(final, np.stack([frame_pixels(act, f) for f in range(a.frames)]))
        result = {"act": int(act), "engine": "ae",
                  "file": str(final.relative_to(film_dir)) if final.is_relative_to(film_dir) else str(final),
                  "frames": [a.frame_in, a.frame_out], "samples": 16, "sha256": jsonio.sha256(final),
                  "made_at": gates.now_iso(), "bundle_hash": "aep:" + "0" * 64, "registered": False}
        if register and self.register_itself:
            registry.register(film_dir, {k: result[k] for k in ("act", "engine", "file", "frames", "samples",
                                                                "sha256", "made_at", "bundle_hash")})
            result["registered"] = True
        return result


def _package(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # a package, so submodules resolve through sys.modules
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class Fakes:
    def __init__(self, monkeypatch, tmp: Path):
        self.media = FakeMedia()
        self.bridge = FakeBridge(self.media, tmp / "tmpdir")
        self.jobs = FakeJobs()
        self.state = FakeState()
        self.ae = FakeAe(self.media)
        (tmp / "tmpdir").mkdir(exist_ok=True)
        mods = {
            "slate.engine": _package("slate.engine"),
            "slate.engine.bridge": _module("slate.engine.bridge", call=self.bridge.call,
                                           engine_dir=self.bridge.engine_dir),
            "slate.jobs": _package("slate.jobs"),
            "slate.jobs.client": _module("slate.jobs.client", submit=self.jobs.submit, wait=self.jobs.wait),
            "slate.jobs.locks": _module("slate.jobs.locks", hold=self.jobs.hold),
            "slate.jobs.disk": _module("slate.jobs.disk", reserve=self.jobs.reserve),
            "slate.state": _package("slate.state"),
            "slate.state.lock": _module("slate.state.lock", record=self.state.record,
                                        lock_asset=self.state.lock_asset, is_locked=self.state.is_locked,
                                        timeline_locked=self.state.timeline_locked),
            "slate.state.film": _module("slate.state.film", load=lambda d: json.loads(
                (Path(d) / "film.json").read_text())),
            "slate.timing": _package("slate.timing", load_shots=lambda d: json.loads(
                (Path(d) / "timing" / "shots.json").read_text())),
            "slate.ae": _package("slate.ae"),
            "slate.ae.driver": _module("slate.ae.driver", ae_lock=self.ae.ae_lock,
                                       aerender_path=self.ae.aerender_path),
            "slate.ae.acts": _module("slate.ae.acts", render=self.ae.render),
        }
        for name, mod in mods.items():
            monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(media_mod, "_state", {"backend": self.media})
        # the temp folder bundles land in, for the sweeps
        import tempfile  # noqa: PLC0415

        monkeypatch.setattr(tempfile, "tempdir", str(tmp / "tmpdir"))
        monkeypatch.setenv("TMPDIR", str(tmp / "tmpdir"))
        # never let a sweep near the real repo's engine folder
        from slate import paths  # noqa: PLC0415

        self.engine_src = tmp / "engine-src"
        self.engine_src.mkdir(exist_ok=True)
        monkeypatch.setattr(paths, "ENGINE_SRC", self.engine_src)

    def without(self, monkeypatch, *names: str) -> None:
        """Make modules look uninstalled (import raises ModuleNotFoundError)."""
        for name in names:
            monkeypatch.setitem(sys.modules, name, None)


def write_toml(data_dir: Path, text: str) -> None:
    (data_dir / "slate.toml").write_text(text, encoding="utf-8")


def make_film(root: Path, *, acts=((1, 30), (2, 24)), mode="supervised", width=64, height=36, fps=(25, 1),
              film_id="demo-film") -> Path:
    """A neutral film folder: film.json, timing/shots.json and timeline.json, and one scene file per act."""
    film_dir = root / film_id
    (film_dir / "timing").mkdir(parents=True)
    film = {"id": film_id, "title": "Demo film", "format": "reel", "language": "en", "fps_num": fps[0],
            "fps_den": fps[1], "width": width, "height": height, "brand": "demo", "mode": mode,
            "generation": "none"}
    (film_dir / "film.json").write_text(json.dumps(film, indent=1))
    shots, act_rows, pos = [], [], 0
    for index, frames in acts:
        half = frames // 2
        ids = []
        for k, n in enumerate((half, frames - half)):
            sid = f"A{index}S{k + 1}"
            shots.append({"id": sid, "act": index, "frame_in": pos, "frame_out": pos + n, "frames": n})
            ids.append(sid)
            pos += n
        act_rows.append({"index": index, "name": f"Act {index}", "shots": ids})
    (film_dir / "timing" / "shots.json").write_text(json.dumps(
        {"fps_num": fps[0], "fps_den": fps[1], "width": width, "height": height, "total_frames": pos,
         "acts": act_rows, "shots": shots}, indent=1))
    (film_dir / "timing" / "timeline.json").write_text(json.dumps({"events": []}))
    for index, _n in acts:
        scene = film_dir / "scenes" / f"act{index}"
        scene.mkdir(parents=True)
        (scene / "index.tsx").write_text(f"export const Act{index} = () => null;\n")
    return film_dir
