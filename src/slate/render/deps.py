"""The render module's calls into other Slate modules, all in one place.

Every call here is one of the cross-module interfaces in docs/ARCHITECTURE.md §12, looked up when it's used (not at
import), so `slate render --help` works on a partly installed Slate and tests can stand in small fakes.

When a module isn't installed, each call does the one honest thing:

- film.json and shots.json are read straight from the film folder (read-only; the same files `slate.state` and
  `slate.timing` load);
- the render lock and the disk reservation fall back to a local file lock and a free-space check;
- the engine bridge, the job queue, the timeline lock and After Effects have no stand-in: the command stops with
  exit 3 and says what's missing.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import importlib
import os
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

from slate import config, jsonio, paths, procs
from slate.errors import Busy, MissingPrerequisite, Refused, UsageError


def _missing(exc: ModuleNotFoundError, name: str) -> bool:
    """True when `name` itself (or a parent package) isn't installed, as opposed to a bug inside it."""
    missing = exc.name or ""
    return bool(missing) and (name == missing or name.startswith(missing + "."))


def module(name: str, what: str, hint: str = "slate doctor") -> ModuleType:
    """Import a Slate module, or stop with exit 3 naming what isn't installed."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if _missing(exc, name):
            raise MissingPrerequisite(f"{what} ({name}) isn't installed in this Slate yet.", hint=hint) from exc
        raise


def optional(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if _missing(exc, name):
            return None
        raise


# ---- film data -------------------------------------------------------------------------------------------------

def _partly_installed(exc: ModuleNotFoundError, package: str) -> bool:
    """A module of `package` that its own code imports isn't there yet (builders land in parallel)."""
    return bool(exc.name) and (exc.name == package or exc.name.startswith(package + "."))


def load_film(film_dir: Path) -> dict:
    """film.json, through slate.state when it's there, otherwise read as it is."""
    state_film = optional("slate.state.film")
    if state_film is not None and hasattr(state_film, "load"):
        try:
            return state_film.load(Path(film_dir))
        except ModuleNotFoundError as exc:
            if not _partly_installed(exc, "slate.state"):
                raise
    path = Path(film_dir) / "film.json"
    if not path.is_file():
        raise UsageError(f"{Path(film_dir)} isn't a Slate film (no film.json).",
                         hint="slate new <id> --brand <brand> --format <format> makes one.")
    return jsonio.read(path)


def load_shots(film_dir: Path) -> dict:
    """timing/shots.json, through slate.timing when it's there, otherwise read as it is. A derived film may hold it only
    by reference (slate.state.derive): then it's the parent's file, found through slate.state.lock.resolve."""
    path = Path(film_dir) / "timing" / "shots.json"
    state_lock_mod = optional("slate.state.lock")
    if not path.exists() and state_lock_mod is not None and hasattr(state_lock_mod, "resolve"):
        try:
            real = Path(state_lock_mod.resolve(Path(film_dir), "timing/shots.json"))
        except Exception:  # noqa: BLE001 - an unreadable lock file means no reference
            real = path
        if real.is_file():
            return jsonio.read(real)
    if not path.exists():
        raise MissingPrerequisite("This film has no timing yet (timing/shots.json), so there are no acts to render.",
                                  hint="slate timing")
    timing = optional("slate.timing")
    if timing is not None and hasattr(timing, "load_shots"):
        try:
            return timing.load_shots(Path(film_dir))
        except ModuleNotFoundError as exc:
            if not _partly_installed(exc, "slate.timing"):
                raise
    return jsonio.read(path)


# ---- engine ----------------------------------------------------------------------------------------------------

def bridge() -> ModuleType:
    return module("slate.engine.bridge", "The engine bridge", hint="slate doctor (the Node engine installs with it)")


def bridge_call(command: str, args: dict, *, timeout: float | None = None) -> dict:
    """`node engine/cli.mjs <command> --json '<args>'`, through slate.engine.bridge."""
    result = bridge().call(command, args, timeout=timeout)
    return result if isinstance(result, dict) else {"result": result}


def engine_dir() -> Path:
    """The Node engine in use: the bridge's answer when it has one, otherwise the repo's engine/."""
    b = optional("slate.engine.bridge")
    if b is not None and hasattr(b, "engine_dir"):
        try:
            return Path(b.engine_dir())
        except Exception:  # noqa: BLE001 - an engine that isn't installed yet still has a source folder
            pass
    return paths.ENGINE_SRC


# ---- jobs ------------------------------------------------------------------------------------------------------

def submit(kind: str, argv: list[str], **kw: Any) -> str:
    client = module("slate.jobs.client", "The job queue", hint="slate doctor (install.sh sets up the queue)")
    return str(client.submit(kind, argv, **kw))


def job_wait(job_id: str, timeout: float | None = None) -> dict:
    client = module("slate.jobs.client", "The job queue")
    return client.wait(job_id, timeout=timeout)


@contextlib.contextmanager
def _file_lock(name: str, timeout: float | None, owner: str | None) -> Iterator[None]:
    """A plain flock, used only when slate.jobs isn't installed."""
    folder = paths.ensure_dir(paths.locks_dir())
    path = folder / f"{name}.lock"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    raise Busy(f"Another {name} is running on this Mac (one at a time keeps it usable).",
                               hint="Wait for it, or follow it with `slate job status`.") from exc
                time.sleep(0.25)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {owner or ''}\n".encode())
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def render_lock(*, owner: str | None = None, timeout: float | None = 5.0) -> Iterator[None]:
    """Hold the machine-wide render slot (slate.jobs.locks) while this process renders."""
    locks = optional("slate.jobs.locks")
    if locks is None:
        with _file_lock("render", timeout, owner):
            yield
        return
    with locks.hold("render", timeout=timeout, owner=owner):
        yield


def render_busy() -> bool:
    """True when some other process holds the render slot right now."""
    try:
        with render_lock(owner="slate render clean (probe)", timeout=0):
            return False
    except Busy:
        return True


@contextlib.contextmanager
def disk_reserve(path: Path, gb: float) -> Iterator[None]:
    """Reserve disk for a render before it starts (slate.jobs.disk.reserve)."""
    disk = optional("slate.jobs.disk")
    if disk is not None:
        with disk.reserve(Path(path), gb):
            yield
        return
    floor = float(config.load().get("jobs", {}).get("disk_reserve_gb", 5.0))
    free_gb = procs.disk_free_bytes(path) / 1e9
    if free_gb - gb < floor:
        raise Refused(f"Not enough disk: this render needs about {gb:.1f} GB and {free_gb:.1f} GB is free, which "
                      f"would leave less than the {floor:.0f} GB Slate keeps spare.",
                      hint="slate render clean frees leftover bundles and chunks; then run it again.")
    yield


# ---- state -----------------------------------------------------------------------------------------------------

def state_lock() -> ModuleType:
    return module("slate.state.lock", "The film state module that locks the timeline",
                  hint="slate doctor (slate.state records and locks film files)")


def require_state_lock() -> None:
    """Checked before a render starts, so a render never finishes without a way to lock the timeline."""
    state_lock()


# ---- After Effects ---------------------------------------------------------------------------------------------

def ae_driver() -> ModuleType:
    return module("slate.ae.driver", "The After Effects driver", hint="slate connect aftereffects")


def ae_acts() -> ModuleType:
    return module("slate.ae.acts", "The After Effects act renderer", hint="slate connect aftereffects")
