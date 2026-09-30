"""Bundle cleanup, everywhere.

Remotion's bundles are a few hundred MB each and nothing removed them on the films: 56 leaked bundles filled 14 GB in
the temp folder, and Remotion's webpack caches (about 170 MB each, keyed on the bundle's random folder, never read
again) did the same in `node_modules/.cache`. The engine keeps Slate's bundles in SLATE_DATA/cache/bundles with a lease
per process using one. A render releases its own bundle when it ends, whatever the ending; every render also sweeps
bundles leaked by earlier runs; and `slate render clean` sweeps on demand:

- leaked bundles (in Slate's bundle cache, or Remotion's own in the temp folder) untouched for three hours and leased
  by no live process (a long render keeps its bundle in use for hours);
- engine webpack caches untouched for ten minutes;
- in a film: finished chunk folders, half-written `.part` chunks, `.new` renders a killed join left behind, splice work
  folders, and `.pre-splice` / `.previous` safety copies (restored when the registry still describes them, removed
  when it doesn't);
- with `--all`: unfinished chunk folders (what `--resume` would have used) and leftover patches too.

Nothing is swept while another render holds the render slot, unless you force it.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from slate import jsonio, paths, procs
from slate.render import chunks as chunks_mod
from slate.render import deps, registry
from slate.render.engines import BUNDLE_PREFIXES, temp_roots

BUNDLE_AGE_MIN = 180.0
CACHE_AGE_MIN = 10.0


def dir_bytes(path: Path) -> int:
    total = 0
    if path.is_file():
        return path.stat().st_size
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(dirpath) / name).lstat().st_size
            except OSError:
                pass
    return total


def age_min(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 60.0
    except OSError:
        return 0.0


def _protected(film_dir: Path | None) -> set[Path]:
    out = {Path("/"), Path.home().resolve(), paths.REPO.resolve(), paths.ENGINE_SRC.resolve(), paths.data().resolve()}
    if film_dir is not None:
        out.add(Path(film_dir).resolve())
    return out


def looks_like_bundle(path: Path) -> bool:
    """A Remotion bundle folder: named like one, or living in Slate's bundle cache."""
    if path.name.startswith(BUNDLE_PREFIXES):
        return True
    try:
        path.resolve().relative_to((paths.cache_dir() / "bundles").resolve())
        return True
    except ValueError:
        return False


def _inside(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            if resolved != root.resolve() and resolved.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def leased(path: Path) -> bool:
    """True when another live process holds a lease on the bundle (the engine's `.leases/<pid>` files)."""
    folder = Path(path) / ".leases"
    if not folder.is_dir():
        return False
    for entry in folder.iterdir():
        if entry.name.isdigit() and int(entry.name) != os.getpid() and procs.pid_alive(int(entry.name)):
            return True
    return False


def remove_bundle(path: Path, *, film_dir: Path | None = None) -> int | None:
    """Delete one bundle folder the engine made. Returns bytes freed, 0 if it was already gone, None if it wasn't safe.

    Safe means: a folder, not one of the places Slate must never delete (the film, the repo, the data folder, home),
    and either named like a bundle or living in the temp folder or Slate's cache, where the engine puts them.
    """
    path = Path(path)
    if not path.exists():
        return 0
    resolved = path.resolve()
    if not path.is_dir() or resolved in _protected(film_dir):
        return None
    if film_dir is not None and _inside(path, [Path(film_dir)]):
        return None
    if not (looks_like_bundle(path) or _inside(path, [*temp_roots(), paths.cache_dir()])):
        return None
    if leased(path):
        return None
    size = dir_bytes(path)
    shutil.rmtree(path, ignore_errors=True)
    return size


def _item(path: Path, why: str, dry_run: bool, action: str = "removed") -> dict:
    size = dir_bytes(path)
    if not dry_run:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    return {"path": str(path), "bytes": size, "why": why, "action": action if not dry_run else f"would be {action}"}


def sweep_bundles(*, older_than_min: float = BUNDLE_AGE_MIN, dry_run: bool = False) -> list[dict]:
    """Leaked Remotion bundles in the temp folder and Slate's bundle cache, older than `older_than_min`."""
    out = []
    candidates: list[Path] = []
    for root in temp_roots():
        if root.is_dir():
            candidates += [p for p in root.iterdir() if p.is_dir() and p.name.startswith(BUNDLE_PREFIXES)]
    cache = paths.cache_dir() / "bundles"
    if cache.is_dir():
        candidates += [p for p in cache.iterdir() if p.is_dir()]
    seen = set()
    for p in candidates:
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        if age_min(p) >= older_than_min and not leased(p):
            out.append(_item(p, f"a leaked bundle, untouched for {age_min(p) / 60:.1f} h", dry_run))
    return out


def sweep_caches(*, older_than_min: float = CACHE_AGE_MIN, dry_run: bool = False) -> list[dict]:
    """Remotion webpack caches in the engine folders (each keyed on a bundle folder that no longer exists)."""
    engines = [paths.ENGINE_SRC]
    if paths.engine_root().is_dir():
        engines += [p for p in paths.engine_root().iterdir() if p.is_dir()]
    out = []
    for engine in engines:
        cache = engine / "node_modules" / ".cache" / "webpack"
        if not cache.is_dir():
            continue
        for group in cache.iterdir():
            if not group.is_dir():
                continue
            for entry in group.iterdir():
                if entry.is_dir() and age_min(entry) >= older_than_min:
                    out.append(_item(entry, "a webpack cache no bundle will read again", dry_run))
    return out


SAFETY_SUFFIXES = (".pre-splice", ".previous")


def _recover_safety_copy(film_dir: Path, copy: Path, dry_run: bool) -> dict | None:
    """A `.pre-splice` or `.previous` copy means a splice or a render stopped between moving files and registering.

    Whichever file the registry describes is the one kept: consistency with the registry beats a newer file that was
    never registered.
    """
    marker = next(m for m in SAFETY_SUFFIXES if m in copy.name)
    base = copy.with_name(copy.name.replace(marker, "", 1))
    entry = registry.find_file(film_dir, base)
    if not base.exists():
        if not dry_run:
            os.replace(copy, base)
        return {"path": str(copy), "bytes": 0, "why": "a render or splice stopped between its moves; the registered "
                                                      "file is back", "action": "restored" if not dry_run else
                "would be restored"}
    if entry is not None and jsonio.sha256(base) != entry.get("sha256") and jsonio.sha256(copy) == entry.get("sha256"):
        if not dry_run:
            os.replace(copy, base)
        return {"path": str(copy), "bytes": 0, "why": "the registry still describes this copy, so it's back",
                "action": "restored" if not dry_run else "would be restored"}
    return _item(copy, "the safety copy of a finished render or splice", dry_run)


def sweep_film(film_dir: Path, *, everything: bool = False, dry_run: bool = False) -> list[dict]:
    film_dir = Path(film_dir)
    renders = film_dir / "renders"
    out: list[dict] = []
    if not renders.is_dir():
        return out
    copies = sorted({*renders.rglob("*.pre-splice.*"), *renders.rglob("*.previous.*")})
    for copy in copies:
        if "chunks" in copy.relative_to(renders).parts:
            continue
        item = _recover_safety_copy(film_dir, copy, dry_run)
        if item:
            out.append(item)
    for leftover in sorted(renders.glob("*.new.*")):
        out.append(_item(leftover, "a render that stopped before it was proven", dry_run))
    for work in sorted(p for p in renders.rglob(".splice-*") if p.is_dir()):
        out.append(_item(work, "a splice's work folder", dry_run))
    for plan in chunks_mod.plans(film_dir):
        folder = Path(plan["dir"])
        if plan.get("status") == "done":
            out.append(_item(folder, "chunks of a finished render", dry_run))
        elif everything:
            out.append(_item(folder, f"chunks of an unfinished render ({plan.get('status')}); --resume can't use them "
                                     "now", dry_run))
        else:
            for part in sorted({*folder.glob("*.part.*"), *folder.glob("*.short.*")}):
                out.append(_item(part, "a half-written or short chunk", dry_run))
    root = film_dir / chunks_mod.CHUNKS
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not (p / "plan.json").exists()):
            out.append(_item(folder, "a chunk folder with no plan", dry_run))
    if everything:
        patches = renders / "patches"
        if patches.is_dir():
            for patch in sorted(patches.iterdir()):
                out.append(_item(patch, "a patch left from a splice that didn't finish", dry_run))
    return out


def sweep(film_dir: Path | None = None, *, older_than_min: float = BUNDLE_AGE_MIN, everything: bool = False,
          dry_run: bool = False, force: bool = False) -> dict:
    """Everything `slate render clean` does. Skipped while another render holds the render slot, unless forced."""
    if not force and deps.render_busy():
        return {"skipped": True, "why": "a render is running; its bundle and chunks are in use", "items": [],
                "freed_bytes": 0}
    items = sweep_bundles(older_than_min=older_than_min, dry_run=dry_run)
    items += sweep_caches(dry_run=dry_run)
    if film_dir is not None:
        items += sweep_film(film_dir, everything=everything, dry_run=dry_run)
    freed = sum(i["bytes"] for i in items if i["action"] == "removed")
    return {"skipped": False, "items": items, "freed_bytes": freed, "dry_run": dry_run}
