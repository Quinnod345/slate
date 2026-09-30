"""Where a film's captures live, and how capture.json is kept.

    <film>/captures/<name>/
      spec.json         the spec as asked (what was recorded, and how)
      capture.json      the write-up (schema: capture): build or viewport, source, sequences, taps, beat map
      raw.mov           an iOS recording, as simctl wrote it (variable frame rate: it only emits changes)
      shots/            a web capture's screenshots, as Aside took them
      raw/f00000.jpg …  the recording conformed to the film's frame rate (capture time)
      frames/f00000.jpg the sequence the film plays (film time: retimed so taps land on their words)
      beats.json        visual events for the taps (the same shape as timing/visual_events/actN.json)
"""

from __future__ import annotations

import re
from pathlib import Path

from slate import jsonio
from slate.errors import UsageError
from slate.gen import deps

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise UsageError(f"{name!r} isn't a capture name (lowercase letters, digits, dot, dash, underscore).")
    return name


def capture_dir(film_dir: Path, name: str) -> Path:
    return Path(film_dir) / "captures" / check_name(name)


def record_path(film_dir: Path, name: str) -> Path:
    return capture_dir(film_dir, name) / "capture.json"


def spec_path(film_dir: Path, name: str) -> Path:
    return capture_dir(film_dir, name) / "spec.json"


def load(film_dir: Path, name: str) -> dict | None:
    return jsonio.read(record_path(film_dir, name), default=None)


def require(film_dir: Path, name: str) -> dict:
    rec = load(film_dir, name)
    if rec is None:
        raise UsageError(f"No capture called {name!r} in this film.",
                         hint="slate capture ios|web --spec <file> records one; slate capture conform --src <video> "
                              "conforms a recording you already have.")
    return rec


def save(film_dir: Path, name: str, rec: dict, command: str) -> Path:
    """Write capture.json and record it in film.lock.json (made by `command` from its spec and its recording)."""
    rec = jsonio.mark_generated(rec, command)
    deps.require_part(rec, "capture", what=f"capture {name}'s record")
    path = jsonio.write_atomic(record_path(film_dir, name), rec)
    inputs = [p for p in (spec_path(film_dir, name), capture_dir(film_dir, name) / "beats.json") if p.is_file()]
    source = (rec.get("source") or {}).get("file")
    if source:
        full = Path(source) if Path(source).is_absolute() else Path(film_dir) / source
        if full.is_file():
            inputs.append(full)
    deps.record_made(film_dir, deps.rel(film_dir, path), producer=command,
                     inputs={deps.rel(film_dir, p): None for p in inputs})
    return path


def film_block(film: dict) -> dict:
    return {"id": film.get("id"), "fps_num": int(film["fps_num"]), "fps_den": int(film["fps_den"]),
            "width": int(film["width"]), "height": int(film["height"])}
