"""Every call the finish stage makes into another Slate module, in one place, each one guarded.

Modules are built in parallel, so finishing must work (and say plainly what's missing) when a neighbour isn't
installed yet. The calls follow docs/ARCHITECTURE.md §12; the few extra state helpers used here (versions, questions,
parking) are looked up by name and fall back to film-local records when absent.

Two safety rails live here too:

- nothing in this file ever starts After Effects from a test run: the AE calls refuse under pytest unless a test has
  replaced them with fakes (Slate never launches AE without a person's go, and tests never do at all);
- every AE call goes through slate.ae's own guard first (autonomous films never touch AE; `--go` is required).
"""

from __future__ import annotations

import contextlib
import importlib
import os
from pathlib import Path
from typing import Any, Callable, Iterator

from slate import gates, jsonio, log
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.gates import GateResult

_WARNED: set[str] = set()
MODES = ("supervised", "express", "draft", "autonomous")


def _warn_once(key: str, message: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warn(message)


def module(name: str):
    """Import a Slate module, or None when it isn't installed yet. A broken module is warned about once."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (name == exc.name or name.startswith(exc.name + ".") or exc.name.startswith(name)):
            return None
        _warn_once(name, f"{name} failed to import ({exc}); finishing carries on without it")
        return None
    except Exception as exc:  # noqa: BLE001 - one neighbour's bug must not stop finishing
        _warn_once(name, f"{name} failed to import ({type(exc).__name__}: {exc}); finishing carries on without it")
        return None


def _fn(module_name: str, attr: str) -> Callable | None:
    mod = module(module_name)
    fn = getattr(mod, attr, None) if mod is not None else None
    return fn if callable(fn) else None


def in_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


# ---------------------------------------------------------------------------------------------------------------
# state: film.json, versions, directions, questions, approvals, locks


def film_load(film_dir: Path) -> dict:
    fn = _fn("slate.state.film", "load")
    if fn is not None:
        return fn(Path(film_dir))
    data = jsonio.read(Path(film_dir) / "film.json", default=None)
    if not isinstance(data, dict):
        raise UsageError(f"{film_dir} isn't a Slate film (no film.json).",
                         hint="slate new <id> --brand <brand> --format <format> makes one.")
    return data


def current_version(film_dir: Path) -> int:
    fn = _fn("slate.state.film", "current_version")
    if fn is not None:
        try:
            return int(fn(Path(film_dir)))
        except SlateError:
            pass
    versions = (jsonio.read(Path(film_dir) / "film.json", default={}) or {}).get("versions") or [{"n": 1}]
    return int(versions[-1].get("n", 1))


def new_version(film_dir: Path, note: str | None = None) -> int:
    fn = _fn("slate.state.film", "new_version")
    if fn is None:
        raise MissingPrerequisite("Starting a new version needs the film-state module (slate.state), which isn't "
                                  "installed yet.", hint="slate doctor names what's missing.")
    return int(fn(Path(film_dir), note))


def mode_settings(mode: str) -> dict:
    """What a mode means (slate.state.modes.settings when installed; the same rules here otherwise)."""
    fn = _fn("slate.state.modes", "settings")
    if fn is not None:
        try:
            return dict(fn(mode))
        except SlateError:
            pass
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    return {"mode": mode, "master": "ffmpeg" if mode in ("draft", "autonomous") else "ae",
            "after_effects": mode != "autonomous", "deliver_final": mode != "draft",
            "held_for_person": mode == "autonomous"}


def modes_check(film_dir: Path, action: str, **ctx: Any) -> dict | None:
    """slate.state.modes.check: raises Refused/SpendRefused when the film's mode (or a parked film) forbids it."""
    fn = _fn("slate.state.modes", "check")
    if fn is None:
        return None
    return fn(Path(film_dir), action, **ctx)


def add_direction(film_dir: Path, text: str, *, source: str, kind: str | None = None) -> int | None:
    """Log the owner's words verbatim as a numbered direction change. None when film state isn't installed."""
    fn = _fn("slate.state.production", "add_direction")
    if fn is None:
        _warn_once("add_direction", "slate.state isn't installed, so notes are kept in review/notes.json only "
                                    "(not yet in brief/PRODUCTION.md)")
        return None
    try:
        return int(fn(Path(film_dir), text, source=source, kind=kind))
    except TypeError:
        return int(fn(Path(film_dir), text, source=source))


def directions(film_dir: Path) -> list[dict]:
    fn = _fn("slate.state.production", "load_brief")
    if fn is not None:
        try:
            return list((fn(Path(film_dir)) or {}).get("directions") or [])
        except SlateError:
            return []
    data = jsonio.read(Path(film_dir) / "brief" / "brief.json", default={}) or {}
    return list(data.get("directions") or []) if isinstance(data, dict) else []


def approvals_data(film_dir: Path) -> dict:
    fn = _fn("slate.state.approvals", "load")
    if fn is not None:
        try:
            return fn(Path(film_dir))
        except SlateError:
            pass
    data = jsonio.read(Path(film_dir) / "approvals.json", default=None)
    if not isinstance(data, dict):
        data = {}
    for key in ("approvals", "questions", "revisions"):
        data.setdefault(key, [])
    return data


def approval_records(film_dir: Path) -> list[dict]:
    return [a for a in approvals_data(film_dir).get("approvals") or [] if isinstance(a, dict)]


def approval_state(film_dir: Path, rec: dict) -> dict:
    """{approved, reason, current_sha256} for one approval record (does the approved content still stand?)."""
    fn = _fn("slate.state.approvals", "check")
    if fn is not None:
        try:
            return fn(Path(film_dir), rec["what"], rec["path"])
        except SlateError as exc:
            return {"approved": False, "reason": exc.message, "current_sha256": None}
    full = Path(rec["path"]) if Path(rec["path"]).is_absolute() else Path(film_dir) / rec["path"]
    current = jsonio.sha256(full) if full.is_file() else None
    return {"approved": current is not None and current == rec.get("sha256"),
            "reason": "unchanged" if current == rec.get("sha256") else "changed or gone", "current_sha256": current}


def open_questions(film_dir: Path) -> list[dict]:
    fn = _fn("slate.state.approvals", "open_questions")
    if fn is not None:
        try:
            return list(fn(Path(film_dir)))
        except SlateError:
            return []
    return [q for q in approvals_data(film_dir).get("questions") or [] if not q.get("answered_at")]


def ask(film_dir: Path, text: str, *, source: str, evidence: list[str], parks: bool, for_: str) -> dict | None:
    """Record a question for a person (it parks the film when `parks`). None when film state isn't installed."""
    fn = _fn("slate.state.approvals", "ask")
    if fn is None:
        return None
    return fn(Path(film_dir), text, source=source, evidence=evidence, parks=parks, for_=for_)


def park(film_dir: Path, *, reason: str, evidence: list[str], source: str) -> dict | None:
    """Park the film with a question for its owner (slate.state.park, idempotent per source)."""
    fn = _fn("slate.state.park", "park")
    if fn is not None:
        return fn(Path(film_dir), reason=reason, evidence=evidence, source=source)
    fn = _fn("slate.state.modes", "park")
    if fn is not None:
        return fn(Path(film_dir), reason, evidence=evidence, source=source, for_="final_cut")
    return None


def lock_entries(film_dir: Path) -> dict[str, dict]:
    fn = _fn("slate.state.lock", "load")
    if fn is not None:
        try:
            return dict((fn(Path(film_dir)) or {}).get("files") or {})
        except SlateError:
            pass
    data = jsonio.read(Path(film_dir) / "film.lock.json", default={}) or {}
    return dict(data.get("files") or {}) if isinstance(data, dict) else {}


def lock_record(film_dir: Path, rel: str, *, producer: str, inputs: Any, tool_version: str | None = None,
                note: str | None = None) -> None:
    """Note what made a file in film.lock.json, so `slate status` knows when it goes stale.

    A lock refusal (approved content) is real and propagates; anything else is bookkeeping and only warns.
    """
    fn = _fn("slate.state.lock", "record")
    if fn is None:
        return
    try:
        fn(Path(film_dir), rel, producer=producer, inputs=inputs, tool_version=tool_version, note=note)
    except Refused:
        raise
    except SlateError as exc:
        log.warn(f"couldn't note {rel} in film.lock.json ({exc.message})")


def guard_regenerate(film_dir: Path, rel: str, *, force: bool, approval_id: str | None) -> dict:
    """Refuse to remake an approved, locked file without force and a person's spend approval (state's rule)."""
    fn = _fn("slate.state.lock", "guard_regenerate")
    if fn is not None:
        return fn(Path(film_dir), rel, force=force, approval_id=approval_id)
    if (lock_entries(film_dir).get(rel) or {}).get("locked") and not (force and approval_id):
        raise Refused(f"{rel} is locked (approved work); Slate never remakes it on its own.",
                      hint=f"slate approve spend {rel} --by NAME, then run this with --force --approval <id>.")
    return {"path": rel, "allowed": True}


def format_platforms(film_dir: Path, fmt_name: str | None) -> list[str]:
    fmt_load = _fn("slate.state.deps", "format_load")
    platforms = _fn("slate.state.deps", "format_platforms")
    if fmt_load is None or platforms is None or not fmt_name:
        return []
    try:
        fmt, _src = fmt_load(fmt_name)
        return [str(p) for p in platforms(fmt)]
    except Exception:  # noqa: BLE001 - platforms are advice for the labels record, never a blocker
        return []


# ---------------------------------------------------------------------------------------------------------------
# qa


def qa_gate_names() -> list[str] | None:
    """Every gate slate.qa knows (core, motion and access), or None when qa isn't installed."""
    qa = module("slate.qa")
    if qa is None:
        return None
    loader = getattr(qa, "load_external", None)
    if callable(loader):
        try:
            loader()
        except Exception as exc:  # noqa: BLE001
            _warn_once("qa_external", f"slate.qa couldn't load its motion and access gates ({exc})")
    names = getattr(qa, "GATES", None)
    return list(names) if isinstance(names, dict) else None


def _no_ae_in_tests(what: str) -> None:
    if in_tests():
        raise RuntimeError(f"a test reached the real After Effects call ({what}); tests must fake slate.finish.deps")


def ae_person_start(go: bool, what: str, film_dir: Path, action: str) -> None:
    """slate.ae.guard.person_start: never for autonomous films, and only with the person's --go."""
    fn = _fn("slate.ae.guard", "person_start")
    if fn is not None:
        fn(go, what, film_dir=film_dir, action=action)
        return
    if (film_load(film_dir).get("mode") or "") == "autonomous" or os.environ.get("SLATE_MODE") == "autonomous":
        raise Refused("Autonomous films never use After Effects: Adobe's terms bar operations a person didn't start.",
                      hint="The film is mastered with ffmpeg and held; a person can start the AE finish once they "
                           "approve it.")
    if not go:
        raise Refused(f"{what} starts After Effects, so it waits for a person's go.",
                      hint="Add --go when you (a person) want After Effects to start.")


def ae_open() -> bool:
    fn = _fn("slate.procs", "ae_running")
    return bool(fn()) if fn is not None else False


def ae_require_closed(what: str) -> None:
    fn = _fn("slate.ae.guard", "require_ae_closed")
    if fn is not None:
        fn(what)
        return
    from slate.errors import Busy  # noqa: PLC0415

    if ae_open():
        raise Busy(f"After Effects is open, and {what} needs it closed: save your work, quit it, then run this again.")


def ae_aep(film_dir: Path) -> Path | None:
    """The film's AE project, if one has been built (the build report names it)."""
    fn = _fn("slate.ae.project", "last_report")
    report = fn(Path(film_dir)) if fn is not None else jsonio.read(Path(film_dir) / "ae" / "build_report.json",
                                                                   default=None)
    if isinstance(report, dict) and report.get("aep"):
        p = Path(report["aep"])
        return p if p.is_file() else None
    found = sorted((Path(film_dir) / "ae").glob("*.aep")) if (Path(film_dir) / "ae").is_dir() else []
    return found[0] if found else None


def ae_build(film_dir: Path) -> Path:
    _no_ae_in_tests("slate.ae.project.build")
    fn = _fn("slate.ae.project", "build")
    if fn is None:
        raise MissingPrerequisite("The After Effects lane (slate.ae) isn't installed.", hint="slate doctor")
    return Path(fn(Path(film_dir), go=True))


def ae_audit_run(film_dir: Path, aep: Path | None) -> list[GateResult]:
    _no_ae_in_tests("slate.ae.audit.run")
    fn = _fn("slate.ae.audit", "run")
    if fn is None:
        raise MissingPrerequisite("The After Effects lane (slate.ae) isn't installed.", hint="slate doctor")
    return list(fn(aep, film_dir=Path(film_dir), go=True))


def ae_audit_last(film_dir: Path, aep: Path | None) -> list[GateResult]:
    """The last audit of the project (STALE where it changed since), without starting AE."""
    fn = _fn("slate.ae.audit", "last")
    if fn is None:
        return [GateResult.unchecked("ae.audit", "the After Effects lane (slate.ae) isn't installed",
                                     engine=gates.engine("slate.ae", None))]
    return list(fn(Path(film_dir), aep))


def ae_masters(film_dir: Path, *, silent: bool = False) -> dict:
    _no_ae_in_tests("slate.ae.masters.render")
    fn = _fn("slate.ae.masters", "render")
    if fn is None:
        raise MissingPrerequisite("The After Effects lane (slate.ae) isn't installed.", hint="slate doctor")
    return dict(fn(Path(film_dir), go=True, allow_silent=silent))


# ---------------------------------------------------------------------------------------------------------------
# jobs: the render slot, disk reservations, detaching


@contextlib.contextmanager
def hold(resource: str, *, owner: str, timeout: float | None) -> Iterator[None]:
    """Hold a machine-wide resource slot (the render slot keeps one heavy process at a time)."""
    fn = _fn("slate.jobs.locks", "hold")
    if fn is None:
        yield
        return
    with fn(resource, timeout=timeout, owner=owner):
        yield


@contextlib.contextmanager
def reserve_disk(path: Path, gb: float, *, owner: str) -> Iterator[None]:
    fn = _fn("slate.jobs.disk", "reserve")
    if fn is None or gb <= 0:
        yield
        return
    Path(path).mkdir(parents=True, exist_ok=True)
    with fn(path, gb, owner=owner):
        yield


def detach(kind: str, resources: tuple[str, ...], *, film_dir: Path, disk_gb: float = 0.0) -> str:
    """Send the current `slate …` command to the job queue and return its id."""
    fn = _fn("slate.jobs.client", "detach_current")
    if fn is None:
        raise MissingPrerequisite("The job queue (slate.jobs) isn't installed, so this can't be detached.",
                                  hint="Run it without --detach.")
    return str(fn(kind, resources, film=film_dir, disk_gb=disk_gb))


# ---------------------------------------------------------------------------------------------------------------
# brand packs


def brand_pack(name: str | None) -> tuple[dict | None, Path | None]:
    """(brand.json, its folder) through slate.brand.pack when installed, else the brand folders directly."""
    if not name:
        return None, None
    find, load = _fn("slate.brand.pack", "find"), _fn("slate.brand.pack", "load")
    if find is not None and load is not None:
        try:
            folder = Path(find(name))
            return load(name), (folder if folder.is_dir() else folder.parent)
        except SlateError:
            pass
    from slate import paths  # noqa: PLC0415

    for base in paths.brand_dirs():
        candidate = Path(base) / name / "brand.json"
        if candidate.is_file():
            data = jsonio.read(candidate, default=None)
            return (data if isinstance(data, dict) else None), candidate.parent
    return None, None
