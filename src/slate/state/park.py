"""Parking, as other modules call it: `park(film_dir, *, reason, evidence, source)`.

A parked film waits for a person. The question lives in approvals.json (see `slate.state.modes.park`), `slate status`
shows it before anything else, and production actions refuse until a person answers (`slate film answer`).

This door is idempotent per source: `slate qa` parks on every run that finds REVIEW verdicts, and each run updates the
one open question from qa instead of adding another. When qa's own record (qa/parked.json) says a person released the
film (`slate qa unpark --by NAME`), status answers the question for them, so the two records never disagree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from slate import jsonio
from slate.errors import UsageError
from slate.state import _util, approvals, production

QA_RECORD = "qa/parked.json"


def park(film_dir: Path, *, reason: str, evidence: Iterable[str] = (), source: str) -> dict:
    """Park the film with `reason` as the question for a person. Returns the question (updated when the same source
    already has one open)."""
    film_dir = Path(film_dir)
    if not (reason or "").strip():
        raise UsageError("Parking a film needs the reason, as a question for a person.")
    evidence = [str(e) for e in evidence]
    source = (source or "").strip() or "unknown"

    def change(data: dict) -> dict | None:
        for q in data["questions"]:
            if q.get("parks") and not q.get("answered_at") and q.get("source") == source:
                q.update(text=reason.strip(), evidence=evidence[:40], asked_at=_util.now())
                return q
        return None

    updated = approvals.edit(film_dir, change)
    if updated is not None:
        production.render(film_dir)
        return updated
    return approvals.ask(film_dir, reason.strip(), source=source, evidence=evidence[:40], parks=True, for_="park")


def unpark(film_dir: Path, *, source: str, by: str, note: str | None = None) -> list[dict]:
    """Answer every open parking question from `source` (a person released the film there)."""
    out = []
    for q in approvals.open_questions(film_dir, parking=True):
        if q.get("source") == source:
            out.append(approvals.answer(film_dir, q["id"], text=note or f"released by {by}", by=by))
    return out


def sync_qa(film_dir: Path) -> list[dict]:
    """Close qa's parking question once qa/parked.json records that a person released the film."""
    film_dir = Path(film_dir)
    try:
        record = jsonio.read(film_dir / QA_RECORD, default=None)
    except UsageError:
        return []
    if not isinstance(record, dict) or record.get("parked") or not record.get("released_by"):
        return []
    open_qa = [q for q in approvals.open_questions(film_dir, parking=True) if q.get("source") == "qa"]
    if not open_qa:
        return []
    note = f"released in `slate qa unpark` ({record.get('released_at', '')[:16]})" + (
        f": {record['release_note']}" if record.get("release_note") else "")
    return unpark(film_dir, source="qa", by=str(record["released_by"]), note=note)
