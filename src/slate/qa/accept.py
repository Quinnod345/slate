"""A person's verdict on a REVIEW item, kept with the exact evidence it was given on.

REVIEW means a machine found something a person has to judge: a glitch flag that might be a count-up, an act join
that moves more than its neighbours, a beat that lands off its word by design. A person can accept one item at a
time, with their name and a reason:

    slate qa triage --accept glitch:frame:1234 --by Quinn --reason "the count-up ticks; not a glitch"

An acceptance is bound to the hashes of the files the item was measured on (its `basis`). Re-render that act and the
acceptance no longer applies: the new frames get judged again. That is "perfect, don't replace" for verdicts.

Autonomous films: an agent can't accept REVIEW items on a film it is making (they park the film for a person); a
person accepts them from their own Terminal.
"""

from __future__ import annotations

from pathlib import Path

from slate import gates, jsonio, procs
from slate.errors import Refused, UsageError

FILE = "qa/accepted.json"


def _path(film_dir: Path) -> Path:
    return Path(film_dir) / FILE


def load(film_dir: Path) -> list[dict]:
    data = jsonio.read(_path(film_dir), default={}) or {}
    return [a for a in data.get("accepted") or [] if isinstance(a, dict)]


def match(film_dir: Path, gate: str, key: str, basis: dict | None) -> dict | None:
    """The acceptance that covers this item on this exact evidence, if a person gave one."""
    for a in reversed(load(film_dir)):
        if a.get("gate") == gate and a.get("key") == key and (a.get("basis") or {}) == (basis or {}):
            return a
    return None


def split_ref(ref: str) -> tuple[str, str]:
    gate, sep, key = str(ref).partition(":")
    if not sep or not gate or not key:
        raise UsageError(f"--accept takes GATE:ITEM (for example glitch:frame:1234), not {ref!r}.",
                         hint="`slate qa triage` lists every open REVIEW item with its reference.")
    return gate, key


def accept(film_dir: Path, ref: str, *, by: str | None, reason: str | None, mode: str = "supervised",
           dry_run: bool = False) -> dict:
    """Record a person's acceptance of one REVIEW item from the latest (not stale) result of its gate. With `dry_run`
    every check runs and the record that would be written comes back, but nothing is written."""
    gate, key = split_ref(ref)
    if not (by or "").strip() or not (reason or "").strip():
        raise UsageError("Accepting a REVIEW item needs the person's name and their reason: --by NAME --reason TEXT.")
    if mode == "autonomous" and procs.agent_context():
        raise Refused(
            "This film is autonomous, so its REVIEW items wait for a person: an agent can't accept them for itself.",
            hint="The film is parked with its evidence. Accept from your own Terminal, or run `slate qa triage` to "
                 "see what's waiting.")
    report = gates.load_report(Path(film_dir))
    result = report.get(gate)
    if result is None:
        raise UsageError(f"There's no {gate} result for this film yet.", hint=f"Run `slate qa {gate}` first.")
    if result.status == "STALE":
        raise Refused(f"The {gate} result is stale (its inputs changed since it ran), so there's nothing current to "
                      "accept.", hint=f"Run `slate qa {gate}` again, then look at the new evidence.")
    items = {i.get("key"): i for i in (result.details or {}).get("review_items") or []}
    item = items.get(key)
    if item is None:
        known = ", ".join(sorted(k for k in items if k)) or "none"
        raise UsageError(f"The latest {gate} result has no REVIEW item {key!r} (open items: {known}).")
    record = {"gate": gate, "key": key, "what": item.get("what"), "basis": item.get("basis") or {},
              "by": by.strip(), "reason": reason.strip(), "at": gates.now_iso(), "agent": procs.agent_context()}
    if dry_run:
        return {**record, "dry_run": True}
    data = jsonio.read(_path(film_dir), default={}) or {}
    data.setdefault("accepted", []).append(record)
    data["note"] = "A person's verdicts on REVIEW items, each bound to the file hashes it was given on (slate qa triage)."
    jsonio.write_atomic(_path(film_dir), data)
    return record
