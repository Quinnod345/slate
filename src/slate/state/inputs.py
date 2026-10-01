"""Supplied inputs: an original film, a motion reference, a logo someone sent. `slate film input <file>`.

An input is copied into the film's brief/inputs/ and its SHA-256 is checked against the source before anything reads
it, then recorded in film.lock.json (producer "slate film input") with where it came from and who supplied it, and
locked by its hash. Every tool reads the film's own copy, so a file that changes later in Downloads changes nothing, and
an input is never silently replaced: the same name with different bytes is refused (give it another name with --as).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from slate import jsonio
from slate.errors import Refused, UsageError
from slate.state import _util, lock

INPUTS_DIR = "brief/inputs"
PRODUCER = "slate film input"


def _name(source: Path, as_name: str | None) -> str:
    name = (as_name or source.name).strip()
    if not name or name in (".", "..") or "/" in name or name.startswith("."):
        raise UsageError(f"{name!r} can't be an input's name: use a plain file name (no folders, not hidden).")
    return name


def add(film_dir: Path, file: str | Path, *, source: str | None = None, as_name: str | None = None) -> dict:
    """Copy `file` into brief/inputs/, verify the copy byte for byte, record it and lock it. Returns
    {path, sha256, bytes, from, source, already}; `already` is True when the same bytes were there under that name."""
    film_dir = Path(film_dir)
    src = Path(file).expanduser()
    if not src.is_file():
        raise UsageError(f"{src} isn't a file.", hint="Pass one file at a time (a folder can be zipped first).")
    src = src.resolve()
    name = _name(src, as_name)
    rel = f"{INPUTS_DIR}/{name}"
    dest = film_dir / rel
    digest = jsonio.sha256(src)
    who = (source or "").strip() or "unknown"
    if dest.exists():
        have = jsonio.sha256(dest)
        if have != digest:
            raise Refused(f"{rel} is already an input with different bytes (sha256 {_util.short(have)}, this one "
                          f"{_util.short(digest)}); Slate never replaces a supplied input.",
                          hint=f"Keep both: slate film input {src} --as <another name>",
                          details={"path": rel, "have": have, "new": digest})
        return {"path": rel, "sha256": digest, "bytes": dest.stat().st_size, "from": str(src), "source": who,
                "already": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")
    try:
        shutil.copyfile(src, tmp)
        copied = jsonio.sha256(tmp)
        if copied != digest:
            raise Refused(f"The copy of {src.name} doesn't match its source (sha256 {_util.short(copied)} vs "
                          f"{_util.short(digest)}); nothing was kept.",
                          hint="The source may be changing (still downloading or syncing). Try again once it's done.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    note = f"supplied by {who}, copied from {src} (sha256 {digest})"
    lock.record(film_dir, rel, producer=PRODUCER, inputs={}, note=note)
    lock.lock_asset(film_dir, rel, reason=f"a supplied input ({who}): compared by its hash, never replaced", by=who)
    return {"path": rel, "sha256": digest, "bytes": dest.stat().st_size, "from": str(src), "source": who,
            "already": False}


def listing(film_dir: Path) -> list[dict]:
    """Every supplied input with its recorded hash and whether the file still matches it."""
    out = []
    for rel, entry in sorted(lock.entries(Path(film_dir), producer=PRODUCER).items()):
        path = Path(film_dir) / rel
        now = jsonio.sha256(path) if path.is_file() else None
        out.append({"path": rel, "sha256": entry.get("sha256"), "intact": now == entry.get("sha256"),
                    "note": entry.get("note"), "made_at": entry.get("made_at")})
    return out
