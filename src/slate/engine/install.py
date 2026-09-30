"""Installing the Node engine: one versioned copy per Remotion pin under Slate's data folder.

`ensure()` copies the engine's sources from this checkout into `DATA/engine/<version>` (the Remotion version Slate is
pinned to, from registry.json) and, when asked, runs `npm ci` there, which installs exactly the lockfile's packages.
It never runs `npm install`, never installs anything globally, and keeps what a copy already has that isn't source:
its `node_modules` and the vendor skills fetched into `skills/`.

A film renders with the engine it was pinned to, so a Remotion upgrade is a new folder, never a change to an old one.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from pathlib import Path

from slate import config, jsonio, log, paths, procs
from slate.errors import MissingPrerequisite, Refused

MARKER = ".slate-engine.json"
# puppeteer's postinstall downloads its own Chrome and headless shell (about 1.5 GB into ~/.cache/puppeteer). The open
# web renderer never uses them: it drives the engine's chrome-headless-shell (Remotion's, `browser-ensure`) by path.
NPM_CI_ENV = {"PUPPETEER_SKIP_DOWNLOAD": "1"}
# What an installed engine is made of. Everything else in the engine folder (tests, caches) stays in the repo.
SOURCE_FILES = ["cli.mjs", "package.json", "package-lock.json", "tsconfig.json", "vitest.config.mjs", "README.md"]
SOURCE_DIRS = ["remotion", "web", "ae"]
SKIP = shutil.ignore_patterns("node_modules", "tests", ".cache", "__pycache__", "*.part", ".DS_Store", ".slate-exports")


def _pinned_remotion(engine_src: Path) -> str | None:
    pkg = jsonio.read(engine_src / "package.json", default={}) or {}
    return (pkg.get("dependencies") or {}).get("remotion")


def is_complete(folder: Path) -> bool:
    """An engine that can run: its command line and its packages are both there."""
    folder = Path(folder)
    return (folder / "cli.mjs").is_file() and (folder / "node_modules" / "remotion" / "package.json").is_file()


def installed_versions() -> list[str]:
    root = paths.engine_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / MARKER).is_file())


def marker(folder: Path) -> dict:
    return jsonio.read(Path(folder) / MARKER, default={}) or {}


def plan(version: str | None = None) -> dict:
    """What `ensure` would do, without doing it (for --dry-run and status)."""
    version = version or config.engine_version()
    src = paths.ENGINE_SRC
    dest = paths.engine_home(version)
    lock = src / "package-lock.json"
    current = marker(dest)
    lock_sha = jsonio.sha256(lock) if lock.is_file() else None
    return {
        "version": version,
        "source": str(src),
        "destination": str(dest),
        "files": [f for f in SOURCE_FILES if (src / f).is_file()],
        "folders": [d for d in SOURCE_DIRS if (src / d).is_dir()],
        "installed": bool(current),
        "packages_installed": (dest / "node_modules" / "remotion").is_dir(),
        "packages_match_lockfile": bool(current) and current.get("npm_ci_lock_sha256") == lock_sha,
    }


def ensure(version: str | None = None, *, npm_ci: bool = False) -> Path:
    """Copy the engine's sources to DATA/engine/<version> (and `npm ci` there when asked). Returns the folder.

    Refuses when this checkout's engine pins another Remotion than the version asked for: the folder name is a
    promise about what's inside.
    """
    version = version or config.engine_version()
    src = paths.ENGINE_SRC
    if not (src / "cli.mjs").is_file():
        raise MissingPrerequisite(f"There's no engine to install at {src}.", hint="Reinstall Slate from its repo.")
    pinned = _pinned_remotion(src)
    if pinned != version:
        raise Refused(
            f"This checkout's engine pins Remotion {pinned}, but the engine asked for is {version}.",
            hint="Remotion moves only after the render-parity check: update registry.json and engine/package.json together.",
        )
    dest = paths.ensure_dir(paths.engine_home(version))
    for name in SOURCE_FILES:
        if (src / name).is_file():
            shutil.copy2(src / name, dest / name)
    for name in SOURCE_DIRS:
        if (dest / name).exists():
            shutil.rmtree(dest / name)  # sources only: node_modules and skills live beside them, untouched
        if (src / name).is_dir():
            shutil.copytree(src / name, dest / name, ignore=SKIP)
    lock = dest / "package-lock.json"
    record = marker(dest)
    record.update({
        "version": version,
        "engine_version": (jsonio.read(src / "package.json", default={}) or {}).get("version"),
        "source": str(src),
        "installed_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "lock_sha256": jsonio.sha256(lock) if lock.is_file() else None,
    })
    if npm_ci:
        log.info(f"npm ci in {dest} (the lockfile's exact packages)")
        # SLATE_NPM names one npm exactly (a particular Node install, or a stand-in under test); otherwise the one
        # slate.procs finds, Homebrew's first.
        npm = os.environ.get("SLATE_NPM") or procs.require("npm", hint="Install Node 24 or newer (`slate doctor --install` offers it).")
        procs.run([npm, "ci", "--no-audit", "--no-fund"], cwd=dest, env=NPM_CI_ENV, timeout=1800, check=True)
        record["npm_ci_lock_sha256"] = record["lock_sha256"]
        record["npm_ci_at"] = record["installed_at"]
    jsonio.write_atomic(dest / MARKER, record)
    return dest
