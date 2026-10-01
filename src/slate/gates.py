"""Gate results: the one shape every check in Slate returns.

States (docs/PLAN.md, "Quality"):

- PASS       the check ran and the thing is right;
- FAIL       the check ran and the thing is wrong;
- REVIEW     a person has to judge it (the look, hero moments, the final cut);
- UNCHECKED  the check couldn't run (a tool missing, an engine unavailable);
- STALE      an input changed since the check ran.

Only PASS counts as passed. UNCHECKED is never reported as a pass, and no gate quietly falls back to another engine:
each result names the engine and version it used (OCR engine and level, Whisper model, ffmpeg build).

`qa/gates.json` in the film folder keeps the latest result per gate; `qa/history.jsonl` keeps every result.

A result's `inputs` are what it read, each with its hash then: film-relative or absolute paths, and named inputs that
aren't whole files (`film:<dotted.key>`, `film:*`, `brand:<name>`, `format:<name>`, hashed through
slate.state.lock.hash_inputs). Listing `film.json` itself records `film:*`: all of film.json except its bookkeeping
(pins, the pin log, versions, budgets, model overrides), so a new pin, a version number or a budget raise doesn't
turn every gate STALE, while a change to the clock, the size, the targets, the captions or the brand still does.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from slate import jsonio

STATUSES = ("PASS", "FAIL", "REVIEW", "UNCHECKED", "STALE")
_SEVERITY = {"FAIL": 4, "STALE": 3, "UNCHECKED": 2, "REVIEW": 1, "PASS": 0}


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class GateResult:
    gate: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    engine: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    checked_at: str = field(default_factory=now_iso)
    details: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Gate status must be one of {STATUSES}, got {self.status!r}")

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GateResult":
        known = {k: data[k] for k in ("gate", "status", "summary", "evidence", "engine", "inputs", "checked_at",
                                      "details") if k in data}
        return cls(**known)

    # Constructors that read well at the call site: GateResult.fail("loudness", "−12.1 LUFS, target −14 ±0.5", …)
    @classmethod
    def pass_(cls, gate: str, summary: str, **kw) -> "GateResult":
        return cls(gate, "PASS", summary, **kw)

    @classmethod
    def fail(cls, gate: str, summary: str, **kw) -> "GateResult":
        return cls(gate, "FAIL", summary, **kw)

    @classmethod
    def review(cls, gate: str, summary: str, **kw) -> "GateResult":
        return cls(gate, "REVIEW", summary, **kw)

    @classmethod
    def unchecked(cls, gate: str, summary: str, **kw) -> "GateResult":
        return cls(gate, "UNCHECKED", summary, **kw)


def engine(name: str, version: str | None = None, **extra) -> dict:
    """The `engine` field: which tool produced the verdict, and which version (evidence, not decoration)."""
    out = {"name": name, "version": version or "unknown"}
    out.update(extra)
    return out


NAMED_INPUT = re.compile(r"^([a-z][a-z0-9_-]*):([^/].*)$")  # the same shape slate.state.lock reads
FILM_SETTINGS = "film:*"  # film.json without its bookkeeping (slate.state.lock.FILM_BOOKKEEPING)


def is_named(key: str | Path, film_dir: Path | None = None) -> bool:
    """True for a named input (`film:targets.lufs`, `brand:demo`, `format:reel`), not a path."""
    text = str(key)
    if not NAMED_INPUT.match(text):
        return False
    return film_dir is None or not (Path(film_dir) / text).exists()


def _named_hashes(film_dir: Path, keys: list[str]) -> dict[str, str | None]:
    """Hashes for named inputs, through slate.state.lock (one definition of what each scheme means)."""
    if not keys:
        return {}
    try:
        from slate.state import lock  # noqa: PLC0415 - state builds on the foundation, not the other way round
    except ImportError:  # pragma: no cover - the state module is part of every install
        return {k: None for k in keys}
    hashed = lock.hash_inputs(Path(film_dir), keys)
    return {k: hashed.get(k) for k in keys}


def expand_film_json(film_dir: Path, paths: Iterable[str | Path]) -> list[str | Path]:
    """The film's own film.json becomes `film:*` (film.json without its bookkeeping); everything else stays as
    given."""
    own = (Path(film_dir) / "film.json").resolve()
    out: list[str | Path] = []
    for p in paths:
        if not is_named(p, film_dir):
            full = Path(p) if Path(p).is_absolute() else Path(film_dir) / p
            if full.resolve() == own:
                out.append(FILM_SETTINGS)
                continue
        out.append(p)
    return out


def inputs_for(film_dir: Path, paths: Iterable[str | Path]) -> dict:
    """{film-relative path or named input: sha256} for what a gate read, so the result can turn STALE later."""
    film_dir = Path(film_dir)
    out: dict[str, str | None] = {}
    named: list[str] = []
    for p in expand_film_json(film_dir, paths):
        if is_named(p, film_dir):
            named.append(str(p))
            out[str(p)] = None
            continue
        full = Path(p) if Path(p).is_absolute() else film_dir / p
        try:
            key = str(full.resolve().relative_to(film_dir.resolve()))
        except ValueError:
            key = str(full)
        out[key] = jsonio.sha256(full) if full.is_file() else None
    out.update(_named_hashes(film_dir, named))
    return out


def _is_stale(film_dir: Path, result: GateResult) -> bool:
    named = {k: v for k, v in (result.inputs or {}).items() if is_named(k, film_dir)}
    for rel, digest in (result.inputs or {}).items():
        if rel in named:
            continue
        full = Path(rel) if Path(rel).is_absolute() else Path(film_dir) / rel
        current = jsonio.sha256(full) if full.is_file() else None
        if current != digest:
            return True
    if named:
        current = _named_hashes(Path(film_dir), list(named))
        if any(current.get(k) != v for k, v in named.items()):
            return True
    return False


def write_report(film_dir: Path, results: Iterable[GateResult]) -> Path:
    """Record results: latest per gate in qa/gates.json, and every result appended to qa/history.jsonl."""
    film_dir = Path(film_dir)
    report_path = film_dir / "qa" / "gates.json"
    current = jsonio.read(report_path, default={}) or {}
    gates = current.get("gates", {})
    history = film_dir / "qa" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as fh:
        for r in results:
            gates[r.gate] = r.to_dict()
            fh.write(jsonio.dumps(r.to_dict(), indent=None))
    jsonio.write_atomic(report_path, {"generated": "do not edit by hand: slate qa", "gates": gates})
    return report_path


def load_report(film_dir: Path) -> dict[str, GateResult]:
    """The latest result per gate, with any result whose inputs changed since reported as STALE."""
    film_dir = Path(film_dir)
    data = jsonio.read(film_dir / "qa" / "gates.json", default={}) or {}
    out: dict[str, GateResult] = {}
    for name, raw in (data.get("gates") or {}).items():
        result = GateResult.from_dict(raw)
        if result.status != "STALE" and _is_stale(film_dir, result):
            result.details = {**(result.details or {}), "was": result.status}
            result.status = "STALE"
            result.summary = f"inputs changed since this was checked ({result.summary})"
        out[name] = result
    return out


def overall(results: Iterable[GateResult]) -> str:
    """PASS only when there is at least one result and every result is PASS; otherwise the most severe state."""
    items = list(results)
    if not items:
        return "UNCHECKED"
    return max((r.status for r in items), key=lambda s: _SEVERITY[s])


def summarize(results: Iterable[GateResult]) -> dict:
    items = list(results)
    counts = {s: 0 for s in STATUSES}
    for r in items:
        counts[r.status] += 1
    return {
        "status": overall(items),
        "counts": counts,
        "not_passed": [{"gate": r.gate, "status": r.status, "summary": r.summary} for r in items if not r.passed],
    }


def exit_code(results: Iterable[GateResult]) -> int:
    return 0 if overall(results) == "PASS" else 1


def format_table(results: Iterable[GateResult]) -> str:
    items = list(results)
    if not items:
        return "No gates have run yet."
    width = max(len(r.gate) for r in items)
    lines = [f"{r.status:<9} {r.gate:<{width}}  {r.summary}" for r in sorted(items, key=lambda r: (-_SEVERITY[r.status], r.gate))]
    s = summarize(items)
    lines.append(f"overall: {s['status']}  ({', '.join(f'{k} {v}' for k, v in s['counts'].items() if v)})")
    return "\n".join(lines)
