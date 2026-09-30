"""Where a film's generations live, and how their records are kept.

    <film>/plates/
      plan.json                      slate plates plan: every generate block, verdict, model, free estimate
      approvals.json                 (only until the state module lands) recorded yeses, with SHA-256s
      <shot>/
        generation.json              the takes (schema: generation), written before anything waits
        approval_request.json        autonomous films: what the person is asked to approve (never edited)
        takes/<take>/…               the outputs, downloaded at once (Higgsfield keeps them about 7 days)
        judge/contact_sheet.jpg      slate gen judge
        plate/plate.json + frames/   slate plates conform: the approved take as a JPEG sequence at the film's rate
        matte/matte.json + frames/   slate plates matte: a bridge's shape revealing real frames

A take's request id is on disk (and on the ledger's in-flight row) before Slate waits on it, so a restart polls the
same request and never pays for it twice.
"""

from __future__ import annotations

import re
from pathlib import Path

from slate import jsonio
from slate.errors import UsageError
from slate.gen import deps

_SHOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LIVE_STATES = frozenset({"submitted", "queued", "in_progress", "timeout"})
DONE_STATES = frozenset({"completed"})
DEAD_STATES = frozenset({"failed", "nsfw", "canceled", "error"})


def check_shot_id(shot_id: str) -> str:
    if not _SHOT.match(shot_id or ""):
        raise UsageError(f"{shot_id!r} isn't a shot id (letters, digits, dot, dash, underscore).")
    return shot_id


def plates_dir(film_dir: Path) -> Path:
    return Path(film_dir) / "plates"


def shot_dir(film_dir: Path, shot_id: str) -> Path:
    return plates_dir(film_dir) / check_shot_id(shot_id)


def record_path(film_dir: Path, shot_id: str) -> Path:
    return shot_dir(film_dir, shot_id) / "generation.json"


def approval_request_path(film_dir: Path, shot_id: str) -> Path:
    return shot_dir(film_dir, shot_id) / "approval_request.json"


def take_dir(film_dir: Path, shot_id: str, take_id: str) -> Path:
    return shot_dir(film_dir, shot_id) / "takes" / take_id


def load_record(film_dir: Path, shot_id: str) -> dict | None:
    return jsonio.read(record_path(film_dir, shot_id), default=None)


def require_record(film_dir: Path, shot_id: str) -> dict:
    rec = load_record(film_dir, shot_id)
    if rec is None:
        raise UsageError(f"{shot_id} has no generations yet.", hint=f"slate gen run --shot {shot_id}")
    return rec


def save_record(film_dir: Path, shot_id: str, rec: dict, command: str = "slate gen run") -> Path:
    rec = jsonio.mark_generated(rec, command)
    rec["updated_at"] = deps.now_iso()
    deps.require_part(rec, "generation", what=f"{shot_id}'s generation record")
    return jsonio.write_atomic(record_path(film_dir, shot_id), rec)


def next_take_id(rec: dict) -> str:
    used = [int(t["id"][1:]) for t in rec.get("takes", []) if str(t.get("id", "")).startswith("t")]
    return f"t{(max(used) if used else 0) + 1}"


def find_take(rec: dict, take_id: str) -> dict:
    for take in rec.get("takes", []):
        if take.get("id") == take_id:
            return take
    known = ", ".join(t.get("id", "?") for t in rec.get("takes", [])) or "none"
    raise UsageError(f"{rec.get('shot')} has no take {take_id!r} (takes: {known}).")


def live_takes(rec: dict) -> list[dict]:
    return [t for t in rec.get("takes", []) if t.get("status") in LIVE_STATES and t.get("request_id")]


def usable_takes(rec: dict) -> list[dict]:
    """Takes that exist or may still arrive and weren't rejected: completed ones plus those still running."""
    return [t for t in rec.get("takes", []) if t.get("status") in DONE_STATES | LIVE_STATES
            and t.get("verdict") != "rejected"]


def completed_takes(rec: dict) -> list[dict]:
    return [t for t in rec.get("takes", []) if t.get("status") == "completed" and t.get("files")]


def approved_take(rec: dict) -> dict | None:
    for take in rec.get("takes", []):
        if take.get("verdict") == "approved":
            return take
    return None


def take_file(film_dir: Path, take: dict) -> Path:
    """The take's main output (the first file it downloaded)."""
    files = take.get("files") or []
    if not files:
        raise UsageError(f"Take {take.get('id')} has no output file yet.")
    path = Path(files[0])
    return path if path.is_absolute() else Path(film_dir) / path
