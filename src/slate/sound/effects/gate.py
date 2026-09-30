"""The proof-3 gate: the effects engine starts only once a person has recorded their approval of proof 3.

Where the approval lives: `DATA/approvals.json` (Slate's runtime folder), written by `slate approve proof3` (the state
module) or by hand. Any of these shapes is read:

    {"approvals": [{"what": "proof3", "path": "...", "sha256": "...", "approved_by": "...", "approved_at": "..."}]}
    {"proof3": {"path": "...", "sha256": "...", "approved_by": "...", "approved_at": "..."}}
    [{"what": "proof3", ...}]

A record counts only when all of this holds:

- it names a person in `approved_by` (an agent can prepare the record, never sign it);
- it carries the SHA-256 of the approved file, and that file is on this Mac with exactly that hash (so an approval
  can't drift onto a different proof);
- the latest record for proof 3 isn't a revocation (`"revoked": true`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slate import jsonio, paths

WHAT = "proof3"
ALSO = ("proof_3", "proof-3", "p3")
_AGENT_WORDS = ("claude", "codex", "openclaw", "oneiro", "gpt", "agent", "slate", "bot", "assistant", "llm")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def approvals_file() -> Path:
    return paths.data() / "approvals.json"


def _records(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("approvals"), list):
            return [r for r in data["approvals"] if isinstance(r, dict)]
        out = []
        for key, value in data.items():
            if isinstance(value, dict):
                out.append({"what": key, **value})
            elif isinstance(value, list):
                out.extend({"what": key, **v} for v in value if isinstance(v, dict))
        return out
    return []


def _is_proof3(record: dict) -> bool:
    what = str(record.get("what", "")).strip().lower()
    return what == WHAT or what in ALSO


def looks_like_agent(name: str) -> bool:
    low = name.strip().lower()
    return any(word in low for word in _AGENT_WORDS)


def latest_record() -> dict | None:
    """The newest proof-3 record in DATA/approvals.json (file order; a later record wins), or None."""
    data = jsonio.read(approvals_file(), default=None)
    if data is None:
        return None
    found = [r for r in _records(data) if _is_proof3(r)]
    return found[-1] if found else None


def check(record: dict | None) -> list[str]:
    """Problems with a proof-3 record (empty when it's a valid, current approval by a person)."""
    if record is None:
        return ["no approval of proof 3 is recorded"]
    problems = []
    if record.get("revoked"):
        problems.append("the latest proof-3 record is a revocation")
    who = str(record.get("approved_by") or "").strip()
    if not who:
        problems.append("the record doesn't name who approved it (approved_by)")
    elif looks_like_agent(who):
        problems.append(f"approved_by is {who!r}, which looks like an agent; approvals come from a person")
    sha = str(record.get("sha256") or "").strip().lower()
    if not _SHA.match(sha):
        problems.append("the record has no SHA-256 of the approved file")
    path = record.get("path") or record.get("file")
    if not path:
        problems.append("the record doesn't say which file was approved (path)")
    elif _SHA.match(sha):
        p = Path(str(path)).expanduser()
        if not p.is_file():
            problems.append(f"the approved file isn't on this Mac ({p}), so its SHA-256 can't be checked")
        elif jsonio.sha256(p) != sha:
            problems.append(f"{p.name} has changed since it was approved (its SHA-256 no longer matches)")
    return problems


def status() -> dict:
    record = latest_record()
    problems = check(record)
    return {"what": WHAT, "approved": not problems, "record": record, "problems": problems,
            "approvals_file": str(approvals_file())}
