"""Parking: an autonomous film stops at a REVIEW verdict and waits for a person, with its evidence.

On autonomous films (made by an agent on its own), a REVIEW never counts as a pass and the agent never decides it.
`slate qa` parks the film instead: qa/parked.json records every REVIEW result with its evidence, and the film stays
parked until a person looks and releases it (`slate qa unpark --by NAME`, from their own Terminal). The state module's
park hook is told too (slate.state.park), so `slate status` shows the hold and production actions wait for a person.

Supervised films aren't parked: their REVIEW items are exactly what the person sees at the next sitting.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from slate import gates, jsonio, log, procs
from slate.errors import Refused, UsageError
from slate.gates import GateResult

FILE = "qa/parked.json"
# Where the state module may offer a park hook: park(film_dir, *, reason, evidence, source) (first one found wins).
STATE_HOOKS = (("slate.state.park", "park"), ("slate.state.status", "park"), ("slate.state.film", "park"))


def _path(film_dir: Path) -> Path:
    return Path(film_dir) / FILE


def status(film_dir: Path) -> dict | None:
    """The park record when the film is parked, else None."""
    data = jsonio.read(_path(film_dir), default=None)
    return data if isinstance(data, dict) and data.get("parked") else None


def _state_hook():
    for module, name in STATE_HOOKS:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        fn = getattr(mod, name, None)
        if callable(fn):
            return f"{module}.{name}", fn
    return None, None


def park(film_dir: Path, reviews: list[GateResult]) -> dict:
    """Park the film on these REVIEW results (merging with an existing hold). Returns the park record."""
    film_dir = Path(film_dir)
    current = status(film_dir) or {}
    items = {r["gate"]: r for r in current.get("reviews") or []}
    for r in reviews:
        items[r.gate] = {"gate": r.gate, "summary": r.summary, "evidence": list(r.evidence)[:20],
                         "checked_at": r.checked_at}
    reason = "; ".join(f"{g}: {v['summary']}" for g, v in sorted(items.items()))
    record = {
        "parked": True,
        "since": current.get("since") or gates.now_iso(),
        "updated": gates.now_iso(),
        "by": "slate qa",
        "why": "A REVIEW verdict on an autonomous film waits for a person; it never counts as a pass.",
        "reviews": [items[g] for g in sorted(items)],
        "release": "A person looks at the evidence, accepts or fixes each item (`slate qa triage`), then runs "
                   "`slate qa unpark --by NAME` in their own Terminal.",
    }
    hook_name, hook = _state_hook()
    if hook is not None:
        try:
            hook(film_dir, reason=reason, evidence=[e for v in record["reviews"] for e in v["evidence"][:5]],
                 source="qa")
            record["state_hook"] = hook_name
        except Exception as exc:  # noqa: BLE001 - our own record still holds the film
            record["state_hook"] = f"{hook_name} failed: {type(exc).__name__}: {exc}"
            log.warn(f"the state module's park hook failed ({exc}); the film is parked in {FILE} all the same")
    jsonio.write_atomic(_path(film_dir), record)
    return record


def unpark(film_dir: Path, *, by: str | None, note: str | None = None, mode: str = "supervised",
           dry_run: bool = False) -> dict:
    """Release a parked film. Only a person does this; an agent can't release an autonomous film it is making. With
    `dry_run` every check runs and the record that would be written comes back, but nothing changes."""
    film_dir = Path(film_dir)
    current = status(film_dir)
    if current is None:
        raise UsageError("This film isn't parked.")
    if not (by or "").strip():
        raise UsageError("Releasing a parked film needs the person's name: --by NAME.")
    if mode == "autonomous" and procs.agent_context():
        raise Refused("An agent can't release an autonomous film it is making; a person does, after looking at the "
                      "evidence.", hint="Run `slate qa unpark --by NAME` in your own Terminal.")
    record = {**current, "parked": False, "released_by": by.strip(), "released_at": gates.now_iso(),
              "release_note": (note or "").strip() or None}
    if dry_run:
        return {**record, "dry_run": True}
    jsonio.write_atomic(_path(film_dir), record)
    try:  # close the state module's parking question too (its status also reconciles from this record)
        state_park = importlib.import_module("slate.state.park")
        answered = state_park.unpark(film_dir, source="qa", by=by.strip(), note=record["release_note"])
        record["state_questions_answered"] = len(answered or [])
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - our record already says released; state reconciles from it
        log.warn(f"the state module couldn't close its parking question ({exc}); `slate status` will reconcile it")
    return record
