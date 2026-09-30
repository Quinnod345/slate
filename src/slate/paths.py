"""Where Slate keeps things.

Three places matter:

- the repo (this checkout): code, skills, formats, the neutral brand packs, the registry;
- `DATA` (`~/Library/Application Support/Slate`): the engine installs, the job queue, the ledger, budgets, caches;
- `FILMS` (`~/Movies/Slate`): one folder per film, outside iCloud on purpose (heavy media and file watchers).

Real brand packs live in a separate store, `~/code/slate-brands`, never in this repo.

Read the dynamic paths at call time, as `paths.DATA` or `paths.data()`, never with `from slate.paths import DATA`:
they follow `SLATE_DATA`, `SLATE_FILMS` and `SLATE_BRANDS`, which is how tests and demos keep off the real folders.
"""

from __future__ import annotations

import os
from pathlib import Path

from slate.errors import UsageError

REPO = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
ENGINE_SRC = REPO / "engine"
SKILLS_SRC = REPO / "skills"
FORMATS_DIR = REPO / "formats"
KNOWLEDGE_DIR = REPO / "knowledge"
FIXTURES_DIR = REPO / "fixtures"
BRANDS_REPO = REPO / "brands"
SCHEMAS_DIR = PACKAGE / "schemas"
REGISTRY_FILE = REPO / "registry.json"


def _from_env(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value).expanduser() if value else default


def data() -> Path:
    """Slate's runtime folder (engine installs, queue, ledger, budgets, caches)."""
    return _from_env("SLATE_DATA", Path.home() / "Library" / "Application Support" / "Slate")


def films() -> Path:
    """Where new films are created. Configurable, and outside iCloud by default."""
    return _from_env("SLATE_FILMS", Path.home() / "Movies" / "Slate")


def brands_store() -> Path:
    """The private brand store (company packs stay on this Mac only)."""
    return _from_env("SLATE_BRANDS", Path.home() / "code" / "slate-brands")


def brand_dirs() -> list[Path]:
    """Folders searched for brand packs, in order: the neutral packs in the repo, then the private store."""
    return [BRANDS_REPO, brands_store()]


def jobs_dir() -> Path:
    return data() / "jobs"


def locks_dir() -> Path:
    return data() / "locks"


def ledger_file() -> Path:
    return data() / "ledger.jsonl"


def budget_file() -> Path:
    return data() / "budget.json"


def config_file() -> Path:
    return data() / "slate.toml"


def cache_dir() -> Path:
    return data() / "cache"


def logs_dir() -> Path:
    return data() / "logs"


def engine_root() -> Path:
    return data() / "engine"


def engine_home(version: str) -> Path:
    """The installed Node engine for one version (one versioned project per Remotion pin)."""
    return engine_root() / version


_DYNAMIC = {
    "DATA": data,
    "FILMS": films,
    "BRANDS_STORE": brands_store,
    "BRAND_DIRS": brand_dirs,
    "JOBS": jobs_dir,
    "LOCKS": locks_dir,
    "LEDGER": ledger_file,
    "BUDGET": budget_file,
    "CONFIG": config_file,
    "CACHE": cache_dir,
    "LOGS": logs_dir,
    "ENGINE_ROOT": engine_root,
}


def __getattr__(name: str):
    # Module-level attributes that follow the environment at call time (PEP 562).
    if name in _DYNAMIC:
        return _DYNAMIC[name]()
    raise AttributeError(f"module 'slate.paths' has no attribute {name!r}")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_film_root(start: Path | None = None) -> Path | None:
    """The nearest folder at or above `start` (default: the working directory) that holds a film.json."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "film.json").is_file():
            return folder
    return None


def film_dir(ref: str | os.PathLike | None = None) -> Path:
    """Resolve a film from a path, a film id under FILMS, `SLATE_FILM`, or the film folder you're standing in."""
    if ref is None:
        ref = os.environ.get("SLATE_FILM") or None
    if ref is None:
        found = find_film_root()
        if found:
            return found
        raise UsageError(
            "No film here: run this inside a film folder, or pass --film <id or path>.",
            hint="slate status --film <id>  ·  slate new <id> --brand <brand> --format <format>",
        )
    candidate = Path(ref).expanduser()
    if candidate.name == "film.json" and candidate.is_file():
        return candidate.parent.resolve()
    if (candidate / "film.json").is_file():
        return candidate.resolve()
    by_id = films() / str(ref)
    if (by_id / "film.json").is_file():
        return by_id.resolve()
    raise UsageError(
        f"No film called {str(ref)!r}: looked for {candidate} and {by_id}.",
        hint="slate new <id> --brand <brand> --format <format> creates one.",
    )
