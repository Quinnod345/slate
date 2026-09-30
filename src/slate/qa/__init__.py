"""Film QA: the machine gates a film has to pass before a person judges it (docs/PLAN.md, "Quality").

    from slate import qa
    results = qa.run(film_dir)                 # every gate; writes qa/gates.json (latest per gate) + qa/history.jsonl
    results = qa.run(film_dir, ["loudness"])   # just one
    qa.GATES                                   # {name: callable(film_dir) -> GateResult}
    qa.measure.psnr(a, b)                      # PSNR between two frames
    qa.ocr.available()                         # (True, engine) when Apple Vision OCR can run here

Only PASS counts as passed. A gate that can't run is UNCHECKED and says what to do next; nothing unchecked is ever
reported as passed, and every result names the engine and version that measured it. On autonomous films, a REVIEW
verdict parks the film with its evidence for a person (see slate.qa.park); it never counts as a pass.

The motion and accessibility gates live in slate.qa.motion and slate.qa.access (another module). Their GATES are
merged in here when they're installed; if a name collides with a core gate, the core gate keeps the name and the
other is filed as "<module>.<name>", so nothing is silently replaced.
"""

from __future__ import annotations

import importlib
import inspect
import traceback
from pathlib import Path
from typing import Callable, Iterable

from slate import __version__, gates, jsonio, log
from slate.errors import MissingPrerequisite, SlateError, UsageError
from slate.gates import GateResult
from slate.qa import film as _film
from slate.qa.checks import audio as _audio
from slate.qa.checks import picture as _picture
from slate.qa.checks import structure as _structure
from slate.qa.checks import sync as _sync
from slate.qa.checks import text as _text

GateFn = Callable[[Path], "GateResult | list[GateResult]"]

# Film order: cheap structural checks first, then sound, then text, then the slow picture work.
CORE: dict[str, GateFn] = {
    "schema": _structure.schema,
    "frames": _structure.frames,
    "events_complete": _structure.events_complete,
    "sync": _sync.sync,
    "loudness": _audio.loudness,
    "bit_depth": _audio.bit_depth,
    "silent_stems": _audio.silent_stems,
    "stems_sum": _audio.stems_sum,
    "captions": _text.captions,
    "claims": _text.claims,
    "banned_words": _text.banned_words,
    "ocr": _text.ocr,
    "readtime": _text.readtime,
    "glitch": _picture.glitch,
    "boundary": _picture.boundary,
    "psnr": _picture.psnr,
}
SLOW = ("ocr", "readtime", "glitch", "psnr")  # skipped by --quick (they decode or read the whole picture)
EXTERNAL = ("slate.qa.motion", "slate.qa.access")
# The production stage a gate checks, for `slate status`, where its name doesn't say (details.stage): the others are
# placed by their names (loudness and stems under sound, glitch and frames under render, psnr under master, …).
STAGES = {"bit_depth": "sound", "captions": "voice"}

GATES: dict[str, GateFn] = dict(CORE)
SOURCES: dict[str, str] = {name: fn.__module__ for name, fn in CORE.items()}
EXTERNAL_STATUS: dict[str, str] = {}
_loaded = {"done": False}


def load_external(refresh: bool = False) -> dict[str, str]:
    """Merge the gates of slate.qa.motion and slate.qa.access into GATES. Returns {module: status}."""
    if _loaded["done"] and not refresh:
        return dict(EXTERNAL_STATUS)
    for name in [k for k, src in SOURCES.items() if src in EXTERNAL or src.split(":")[0] in EXTERNAL]:
        GATES.pop(name, None)
        SOURCES.pop(name, None)
    for name, fn in CORE.items():
        GATES[name] = fn
        SOURCES[name] = fn.__module__
    EXTERNAL_STATUS.clear()
    for module_name in EXTERNAL:
        short = module_name.rsplit(".", 1)[-1]
        snapshot = dict(GATES)
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name in (module_name, "slate.qa." + short):
                EXTERNAL_STATUS[module_name] = "not installed yet"
            else:
                EXTERNAL_STATUS[module_name] = f"import failed: {exc}"
            continue
        except Exception as exc:  # noqa: BLE001 - another module's bug must not take the core gates down
            EXTERNAL_STATUS[module_name] = f"import failed: {type(exc).__name__}: {exc}"
            continue
        # A module may have written into GATES on import; put any core gate it replaced back, and keep its version.
        theirs: dict[str, GateFn] = {}
        for k, v in list(GATES.items()):
            if k not in snapshot or snapshot[k] is not v:
                theirs[k] = v
                if k in snapshot:
                    GATES[k] = snapshot[k]
                else:
                    GATES.pop(k)
        exported = getattr(module, "GATES", None)
        if isinstance(exported, dict) and exported is not GATES:
            theirs.update({str(k): v for k, v in exported.items() if callable(v)})
        added = []
        for k, fn in theirs.items():
            key = k if k not in GATES else f"{short}.{k}"
            GATES[key] = fn
            SOURCES[key] = module_name
            added.append(key)
        EXTERNAL_STATUS[module_name] = f"merged: {', '.join(added)}" if added else "installed, but it exports no gates"
    _loaded["done"] = True
    return dict(EXTERNAL_STATUS)


def describe(name: str) -> str:
    """A gate's one-paragraph description (the first paragraph of its docstring)."""
    fn = GATES.get(name)
    doc = inspect.getdoc(fn) if fn else None
    if not doc:
        return ""
    return " ".join(doc.strip().split("\n\n")[0].split())


# ---------------------------------------------------------------------------------------------- running gates
def _coerce(r: GateResult, name: str, fn: GateFn) -> GateResult:
    """Make a result fit the gate-result schema without changing its verdict."""
    r.gate = str(r.gate or name)
    r.summary = str(r.summary or "(no summary)")
    r.evidence = [str(e) for e in (r.evidence or [])]
    eng = dict(r.engine or {})
    if not eng.get("name"):
        eng["name"] = f"{getattr(fn, '__module__', 'gate')}.{getattr(fn, '__name__', name)}"
    eng["version"] = str(eng.get("version") or __version__)
    eng["name"] = str(eng["name"])
    r.engine = eng
    r.inputs = {str(k): (str(v) if v is not None else None) for k, v in (r.inputs or {}).items()}
    r.details = dict(r.details or {})
    if r.gate in STAGES:
        r.details.setdefault("stage", STAGES[r.gate])
    r.checked_at = str(r.checked_at or gates.now_iso())
    return r


def _normalize(out, name: str, fn: GateFn) -> list[GateResult]:
    items = out if isinstance(out, (list, tuple)) else [out]
    results = []
    for item in items:
        if isinstance(item, GateResult):
            results.append(_coerce(item, name, fn))
        elif isinstance(item, dict):
            try:
                results.append(_coerce(GateResult.from_dict({"gate": name, **item}), name, fn))
            except (TypeError, ValueError) as exc:
                results.append(_crashed(name, fn, exc))
        else:
            results.append(_crashed(name, fn, TypeError(f"returned {type(item).__name__}, not a GateResult")))
    if not results:
        results.append(GateResult.unchecked(name, "the gate returned no result", engine=gates.engine(
            f"{fn.__module__}.{getattr(fn, '__name__', name)}", __version__)))
    return results


def _crashed(name: str, fn: GateFn, exc: BaseException) -> GateResult:
    engine = gates.engine(f"{getattr(fn, '__module__', 'gate')}.{getattr(fn, '__name__', name)}", __version__)
    if isinstance(exc, SlateError):
        return GateResult.unchecked(name, exc.message.splitlines()[0], evidence=[f"next: {exc.hint}"] if exc.hint else [],
                                    engine=engine, details={"error": exc.to_dict()})
    tail = traceback.format_exception(type(exc), exc, exc.__traceback__)[-6:]
    return GateResult.unchecked(name, f"the gate couldn't run: {type(exc).__name__}: {exc}", engine=engine,
                                details={"traceback": "".join(tail)[-3000:]})


def _call(name: str, fn: GateFn, film_dir: Path) -> list[GateResult]:
    try:
        return _normalize(fn(film_dir), name, fn)
    except Exception as exc:  # noqa: BLE001 - one gate failing to run is an UNCHECKED result, never a crash
        if not isinstance(exc, SlateError):
            log.warn(f"the {name} gate couldn't run ({type(exc).__name__}: {exc}); it is UNCHECKED")
        return [_crashed(name, fn, exc)]


def valid_result(r: GateResult) -> list[str]:
    """Problems with a result against src/slate/schemas/gates.schema.json (empty when it fits)."""
    return jsonio.validate({"gates": {r.gate: r.to_dict()}}, "gates")


def resolve(names: Iterable[str] | None, *, quick: bool = False) -> list[str]:
    """The gate names to run, in film order. Unknown names are a usage error; the motion gates, when their module
    isn't installed yet, are a missing prerequisite."""
    load_external()
    if not names:
        return [n for n in GATES if not (quick and n in SLOW)]
    wanted, unknown = [], []
    for n in names:
        if n in GATES:
            wanted.append(n)
            continue
        # a group: every gate a module provides (`slate qa motion`), or every "<n>.…" gate
        group = [g for g in GATES if SOURCES.get(g, "").rsplit(".", 1)[-1] == n or g.startswith(f"{n}.")]
        if group:
            wanted += group
            continue
        module = next((m for m in EXTERNAL if n.split(".")[0] == m.rsplit(".", 1)[-1]), None)
        if module and not EXTERNAL_STATUS.get(module, "").startswith("merged"):
            raise MissingPrerequisite(f"The {n} gates come from {module}, which isn't installed yet "
                                      f"({EXTERNAL_STATUS.get(module, 'not loaded')}).",
                                      hint="`slate qa --list` shows the gates that are here now.")
        unknown.append(n)
    if unknown:
        raise UsageError(f"No gate called {', '.join(repr(u) for u in unknown)}.",
                         hint="Gates: " + ", ".join(GATES) + " (`slate qa --list` describes each).")
    order = list(GATES)
    return sorted(dict.fromkeys(wanted), key=order.index)


def run(film_dir: str | Path, names: Iterable[str] | None = None, *, quick: bool = False) -> list[GateResult]:
    """Run gates on a film, record them in qa/gates.json (and qa/history.jsonl), park an autonomous film on REVIEW,
    and return the results."""
    from slate.qa import park  # noqa: PLC0415

    film_dir = Path(film_dir).expanduser().resolve()
    if not (film_dir / "film.json").is_file():
        raise UsageError(f"{film_dir} isn't a film folder (no film.json).",
                         hint="slate new <id> --brand <brand> --format <format> creates one.")
    selected = resolve(names, quick=quick)
    results: list[GateResult] = []
    with _film.session(film_dir) as ctx:
        ctx.quick = quick
        for name in selected:
            log.info(f"qa: {name}")
            for r in _call(name, GATES[name], film_dir):
                problems = valid_result(r)
                if problems:
                    r.details = {**r.details, "schema_problems": problems[:10]}
                    log.warn(f"the {name} gate's result doesn't fit the gate-result schema: {problems[0]}")
                results.append(r)
    if results:
        gates.write_report(film_dir, results)
    mode = (jsonio.read(film_dir / "film.json", default={}) or {}).get("mode")
    reviews = [r for r in results if r.status == "REVIEW"]
    if mode == "autonomous" and reviews:
        park.park(film_dir, reviews)
    return results


def __getattr__(name: str):
    # slate.qa.measure / slate.qa.ocr / … as attributes, without importing them all up front
    if name in ("measure", "ocr", "park", "accept", "triage", "glitch", "media", "wav", "audio", "textmatch",
                "brandinfo"):
        return importlib.import_module(f"slate.qa.{name}")
    raise AttributeError(f"module 'slate.qa' has no attribute {name!r}")


load_external()
