"""Installing the Node engine: one versioned copy per Remotion pin under Slate's data folder.

`ensure()` copies the engine's sources from this checkout into `DATA/engine/<version>` (the Remotion version Slate is
pinned to, from registry.json) and, when asked, runs `npm ci` there, which installs exactly the lockfile's packages.
It never runs `npm install`, never installs anything globally, and keeps what a copy already has that isn't source:
its `node_modules` and the vendor skills fetched into `skills/`.

A film renders with the engine it was pinned to, so a Remotion upgrade is a new folder, never a change to an old one.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import os
import shutil
import tempfile
from pathlib import Path

from slate import config, jsonio, log, paths, procs
from slate.errors import Busy, MissingPrerequisite, Refused

MARKER = ".slate-engine.json"
# puppeteer's postinstall downloads its own Chrome and headless shell (about 1.5 GB into ~/.cache/puppeteer). The open
# web renderer never uses them: it drives the engine's chrome-headless-shell (Remotion's, `browser-ensure`) by path.
NPM_CI_ENV = {"PUPPETEER_SKIP_DOWNLOAD": "1"}
# What an installed engine is made of. Everything else in the engine folder (tests, caches) stays in the repo.
SOURCE_FILES = ["cli.mjs", "package.json", "package-lock.json", "tsconfig.json", "vitest.config.mjs", "README.md",
                ".puppeteerrc.cjs"]
SOURCE_DIRS = ["remotion", "web", "ae"]
SKIP = shutil.ignore_patterns("node_modules", "tests", ".cache", "__pycache__", "*.part", ".DS_Store", ".slate-exports")


def _pinned_remotion(engine_src: Path) -> str | None:
    pkg = jsonio.read(engine_src / "package.json", default={}) or {}
    return (pkg.get("dependencies") or {}).get("remotion")


def is_complete(folder: Path) -> bool:
    """An engine that can run: its command line and its packages are both there."""
    folder = Path(folder)
    return ((folder / "cli.mjs").is_file() and (folder / "node_modules" / "remotion" / "package.json").is_file()
            and marker(folder).get("packages_ready", True))


def installed_versions() -> list[str]:
    root = paths.engine_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / MARKER).is_file())


def marker(folder: Path) -> dict:
    return jsonio.read(Path(folder) / MARKER, default={}) or {}


def source_sha256(folder: Path) -> str:
    """Hash the distributable sources on disk, including missing and extra source files.

    Installation records are receipts, not proof that the installed files still match this checkout. Packages,
    vendor skills and caches aren't sources and never take part in this comparison.
    """
    folder = Path(folder)
    files = [folder / name for name in SOURCE_FILES if (folder / name).is_file()]
    for name in SOURCE_DIRS:
        for root, dirs, names in os.walk(folder / name):
            ignored = SKIP(root, dirs + names)
            dirs[:] = sorted(d for d in dirs if d not in ignored)
            files += [Path(root) / n for n in names if n not in ignored and (Path(root) / n).is_file()]
    return jsonio.sha256_obj({p.relative_to(folder).as_posix(): jsonio.sha256(p) for p in sorted(files)})


def plan(version: str | None = None, *, source: Path | None = None) -> dict:
    """What `ensure` would do, without doing it (for --dry-run and status)."""
    version = version or config.engine_version()
    src = Path(source) if source is not None else paths.ENGINE_SRC
    dest = paths.engine_home(version)
    lock = src / "package-lock.json"
    current = marker(dest)
    lock_sha = jsonio.sha256(lock) if lock.is_file() else None
    source_sha = source_sha256(src)
    installed_sha = source_sha256(dest)
    return {
        "version": version,
        "source": str(src),
        "destination": str(dest),
        "files": [f for f in SOURCE_FILES if (src / f).is_file()],
        "folders": [d for d in SOURCE_DIRS if (src / d).is_dir()],
        "installed": bool(current),
        "packages_installed": (dest / "node_modules" / "remotion").is_dir() and current.get("packages_ready", True),
        "packages_match_lockfile": bool(current) and current.get("npm_ci_lock_sha256") == lock_sha,
        "sources_match": (src / "cli.mjs").is_file() and source_sha == installed_sha,
        "source_sha256": source_sha,
        "installed_source_sha256": installed_sha,
    }


@contextlib.contextmanager
def _idle_engine():
    """Hold every render and AE slot: changing a live engine would mix old and new modules in one process."""
    from slate.jobs import locks  # noqa: PLC0415

    held = []
    try:
        for resource in ("render", "ae"):
            count = locks.slots(resource)
            if count == 0:
                raise Busy(f"Engine installation waits while {resource} slots are paused.",
                           hint="Enable the resource's slots, then run the installation again.")
            for _ in range(count):
                slot = locks.try_acquire(resource, owner="slate engine install")
                if slot is None:
                    raise Busy(f"Engine installation waits for all {resource} work to finish.",
                               hint="Let the active work finish, then run the installation again; slate jobs locks shows it.")
                held.append(slot)
        yield
    finally:
        for slot in reversed(held):
            locks.release(slot)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


@contextlib.contextmanager
def _sources_in_place(src: Path, dest: Path):
    """Stage complete sources, then replace them with rollback if any copy, move or subsequent install step fails."""
    with tempfile.TemporaryDirectory(prefix=".slate-engine-", dir=dest.parent) as tmp:
        staged, backup = Path(tmp) / "new", Path(tmp) / "old"
        staged.mkdir()
        backup.mkdir()
        for name in SOURCE_FILES:
            if (src / name).is_file():
                shutil.copy2(src / name, staged / name)
        for name in SOURCE_DIRS:
            if (src / name).is_dir():
                shutil.copytree(src / name, staged / name, ignore=SKIP)
        changed = []
        try:
            for name in SOURCE_FILES + SOURCE_DIRS:
                old = dest / name
                if old.exists() or old.is_symlink():
                    os.replace(old, backup / name)
                changed.append(name)
                if (staged / name).exists():
                    os.replace(staged / name, old)
            yield
        except BaseException:
            for name in reversed(changed):
                _remove(dest / name)
                if (backup / name).exists() or (backup / name).is_symlink():
                    os.replace(backup / name, dest / name)
            raise


def ensure(version: str | None = None, *, npm_ci: bool = False, source: Path | None = None) -> Path:
    """Copy the engine's sources to DATA/engine/<version> (and `npm ci` there when asked). Returns the folder.

    Refuses when this checkout's engine pins another Remotion than the version asked for: the folder name is a
    promise about what's inside.
    """
    version = version or config.engine_version()
    src = (Path(source) if source is not None else paths.ENGINE_SRC).resolve()
    if not (src / "cli.mjs").is_file():
        raise MissingPrerequisite(f"There's no engine to install at {src}.", hint="Reinstall Slate from its repo.")
    pinned = _pinned_remotion(src)
    if pinned != version:
        raise Refused(
            f"This checkout's engine pins Remotion {pinned}, but the engine asked for is {version}.",
            hint="Remotion moves only after the render-parity check: update registry.json and engine/package.json together.",
        )
    dest = paths.engine_home(version).resolve()
    if src == dest or src.is_relative_to(dest) or dest.is_relative_to(src):
        raise Refused("The engine source and installation must be separate folders.",
                      hint="Install from the checkout's engine folder into Slate's data folder.")
    with _idle_engine():
        paths.ensure_dir(dest)
        with _sources_in_place(src, dest):
            lock = dest / "package-lock.json"
            record = marker(dest)
            record.update({
                "version": version,
                "engine_version": (jsonio.read(src / "package.json", default={}) or {}).get("version"),
                "source": str(src),
                "source_sha256": source_sha256(dest),
                "installed_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
                "lock_sha256": jsonio.sha256(lock) if lock.is_file() else None,
            })
            if npm_ci:
                log.info(f"npm ci in {dest} (the lockfile's exact packages)")
                # Invalidate the receipt before npm can change packages. A failed npm ci must never leave a
                # previous successful dependency check attached to a partly replaced node_modules folder.
                previous = marker(dest)
                previous.pop("npm_ci_lock_sha256", None)
                previous.pop("npm_ci_at", None)
                previous["packages_ready"] = False
                jsonio.write_atomic(dest / MARKER, previous)
                npm = os.environ.get("SLATE_NPM") or procs.require("npm", hint="Install Node 24 or newer (`slate doctor --install` offers it).")
                procs.run([npm, "ci", "--no-audit", "--no-fund"], cwd=dest, env=NPM_CI_ENV, timeout=1800, check=True)
                record["npm_ci_lock_sha256"] = record["lock_sha256"]
                record["npm_ci_at"] = record["installed_at"]
                record["packages_ready"] = True
            jsonio.write_atomic(dest / MARKER, record)
    return dest
