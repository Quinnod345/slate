"""The film gates, one function per gate. Each takes the film folder and returns one GateResult.

A gate is made of sub-checks (the loudness gate checks integrated loudness and true peak; the sync gate checks
coverage, effects anchors, voice placement and word landings). The gate's status is the worst of its sub-checks,
by the same order the foundation uses: FAIL > STALE > UNCHECKED > REVIEW > PASS. A sub-check that doesn't apply (a
film with no voice has no voice placement) is left out rather than passed.

Two rules hold everywhere:
- a missing input makes a check UNCHECKED, with the command that makes the input, never a quiet PASS;
- every result names the engine and version that measured it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from slate import __version__, gates
from slate.gates import GateResult
from slate.qa import accept
from slate.qa.film import FilmContext

SEVERITY = {"FAIL": 4, "STALE": 3, "UNCHECKED": 2, "REVIEW": 1, "PASS": 0}


@dataclass
class Sub:
    """One sub-check of a gate: its own status, a one-line summary, evidence lines and details."""

    name: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"check": self.name, "status": self.status, "summary": self.summary, **self.details}


def worst(statuses: Iterable[str]) -> str:
    items = list(statuses)
    if not items:
        return "UNCHECKED"
    return max(items, key=lambda s: SEVERITY[s])


def slate_engine(what: str) -> dict:
    """The engine field for checks Slate itself computes from the film's data."""
    return gates.engine(f"slate {what}", __version__)


def combine(gate: str, subs: list[Sub], *, engine: dict, inputs: dict, details: dict | None = None,
            empty_summary: str = "nothing to check yet") -> GateResult:
    """One GateResult from sub-checks: the worst status, its summary first, every sub-check in details."""
    if not subs:
        return GateResult.unchecked(gate, empty_summary, engine=engine, inputs=inputs, details=details or {})
    status = worst(s.status for s in subs)
    lead = [s for s in subs if s.status == status]
    others = [s for s in subs if s.status != status]
    summary = "; ".join(s.summary for s in lead)
    if others and status != "PASS":
        summary += f" (and {len(others)} other check{'s' if len(others) != 1 else ''})"
    evidence: list[str] = []
    for s in sorted(subs, key=lambda s: -SEVERITY[s.status]):
        evidence += [e for e in s.evidence if e not in evidence]
    return GateResult(gate, status, summary, evidence=evidence[:80], engine=engine, inputs=inputs,
                      details={**(details or {}), "checks": [s.to_dict() for s in subs]})


def review_items(ctx: FilmContext, gate: str, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split REVIEW items into (still open, accepted by a person on this exact evidence)."""
    open_items, accepted = [], []
    for item in items:
        a = accept.match(ctx.dir, gate, item["key"], item.get("basis"))
        if a:
            accepted.append({**item, "accepted_by": a.get("by"), "accepted_at": a.get("at"), "reason": a.get("reason")})
        else:
            open_items.append(item)
    return open_items, accepted


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"
