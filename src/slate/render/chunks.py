"""Chunked, resumable renders.

A render is cut into chunks of `render.chunk_frames` frames (600 by default). Each chunk renders to its own file, is
counted, and only then gets a small sidecar recording its frame count, its SHA-256 and the settings that made it.

Running the same render again (or `slate render --resume`) keeps every chunk whose file still has the right frame count
and the recorded hash and was made with the same settings, and re-renders the rest: a short chunk (a render killed
mid-write), a missing one, one changed on disk, or one made from other sources, another sample count or another engine.
A chunk only ever reaches its final name after it's been counted, so a half-written file can't pass for a finished one.

Chunks live in `renders/chunks/act<N>_<first>-<last>_<key>/` with a `plan.json` that says what the render was asked
to do and how far it got. Finished renders remove their chunk folder; `slate render clean` sweeps the rest.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from slate import gates, jsonio
from slate.render.media import Media
from slate.render.plan import Target, chunk_ranges

CHUNKS = Path("renders") / "chunks"
COMMAND = "slate render"


def settings_key(settings: dict) -> str:
    """A short, stable id for the settings a chunk was made with."""
    return jsonio.sha256_obj(settings)[:12]


@dataclass(frozen=True)
class Chunk:
    index: int
    first: int  # act frames, inclusive
    last: int
    path: Path

    @property
    def frames(self) -> int:
        return self.last - self.first + 1

    @property
    def sidecar(self) -> Path:
        return self.path.with_name(self.path.name + ".json")

    @property
    def part(self) -> Path:
        """Where the engine writes; renamed to `path` only once the frame count is right."""
        return self.path.with_name(f"{self.path.stem}.part{self.path.suffix}")


class Workspace:
    """One target's chunk folder: its plan, its chunk files and their sidecars."""

    def __init__(self, film_dir: Path, target: Target, settings: dict, chunk_frames: int, ext: str):
        self.film_dir = Path(film_dir)
        self.target = target
        self.settings = settings
        self.key = settings_key(settings)
        self.prefix = f"act{target.act.index}_{target.first:05d}-{target.last:05d}_"
        self.dir = self.film_dir / CHUNKS / f"{self.prefix}{self.key}"
        self.chunks = [Chunk(i, a, b, self.dir / f"c{i:03d}_{a:05d}-{b:05d}{ext}")
                       for i, (a, b) in enumerate(chunk_ranges(target.first, target.last, chunk_frames))]

    @property
    def plan_path(self) -> Path:
        return self.dir / "plan.json"

    def read_plan(self) -> dict:
        return jsonio.read(self.plan_path, default={}) or {}

    def write_plan(self, status: str, **extra) -> dict:
        now = gates.now_iso()
        old = self.read_plan()
        data = jsonio.mark_generated({
            "status": status,
            "target": self.target.to_dict(),
            "settings": self.settings,
            "key": self.key,
            "chunks": [{"index": c.index, "first": c.first, "last": c.last, "file": c.path.name} for c in self.chunks],
            "started_at": old.get("started_at") or now,
            "updated_at": now,
            **{k: v for k, v in old.items() if k in ("options",)},
            **extra,
        }, COMMAND)
        jsonio.write_atomic(self.plan_path, data)
        return data

    def check(self, chunk: Chunk, media: Media) -> tuple[bool, str]:
        """(keep?, why). Kept only with its record, the same settings, the recorded hash and the right frame count."""
        if not chunk.path.is_file():
            return False, "missing"
        record = jsonio.read(chunk.sidecar, default=None)
        if not record:
            return False, "no record (the render stopped before this chunk was counted)"
        if record.get("key") != self.key:
            return False, "made with other settings"
        if jsonio.sha256(chunk.path) != record.get("sha256"):
            return False, "changed on disk since it was counted"
        counted = media.count_frames(chunk.path)
        if counted != chunk.frames:
            return False, f"short: {counted} of {chunk.frames} frames"
        return True, "kept"

    def record(self, chunk: Chunk, frames: int, engine_result: dict | None = None) -> dict:
        data = {"index": chunk.index, "first": chunk.first, "last": chunk.last, "frames": frames,
                "sha256": jsonio.sha256(chunk.path), "key": self.key, "settings": self.settings,
                "made_at": gates.now_iso(), "engine_result": engine_result or {}}
        jsonio.write_atomic(chunk.sidecar, data)
        return data

    def discard(self, chunk: Chunk) -> None:
        """Remove a chunk and everything a failed attempt left beside it (a part file, the engine's short copy)."""
        for p in (chunk.path, chunk.sidecar, chunk.part):
            p.unlink(missing_ok=True)
        if self.dir.is_dir():
            for p in self.dir.glob(f"{chunk.path.stem}.*"):
                if p.is_file():
                    p.unlink(missing_ok=True)

    def siblings(self) -> list[Path]:
        """Chunk folders for the same act range made with other settings (they can't be resumed with these)."""
        root = self.film_dir / CHUNKS
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(self.prefix) and p != self.dir)

    def remove(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def plans(film_dir: Path) -> list[dict]:
    """Every chunk folder's plan in this film, oldest first, each with its folder."""
    root = Path(film_dir) / CHUNKS
    out = []
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            data = jsonio.read(folder / "plan.json", default=None)
            if data:
                out.append({**data, "dir": str(folder)})
    return sorted(out, key=lambda d: (d.get("started_at") or "", d["dir"]))


def unfinished(film_dir: Path) -> list[dict]:
    """Plans that didn't finish: what `slate render --resume` picks up."""
    return [p for p in plans(film_dir) if p.get("status") != "done"]
