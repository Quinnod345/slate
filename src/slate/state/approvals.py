"""Approvals: a person's yes, tied to the exact bytes they said yes to.

Two sittings block a film: the pre-production sitting (the checklist, claims, script, voice shortlist and look test)
and the final-cut sitting. Hero moments, plates and sound proofs arrive in review packets and never block. Every
record stores the SHA-256 of exactly what was approved, and `is_approved` compares it with the file as it is now: edit
an approved script and it's no longer approved.

Modes change who sits when (slate.state.modes):
- supervised: both sittings block;
- express and autonomous: every approval is batched into the final-cut sitting (autonomous films are held for the
  person there, and the brand's approved references stand in for claims, voice and look);
- draft: nothing blocks, and a draft is never approved as a final cut.

approvals.json also holds the questions waiting for a person (a question can park the film) and the revisions made
under "perfect, don't replace".
"""

from __future__ import annotations

import functools
import re
import secrets as _secrets
from pathlib import Path
from typing import Any

from slate import jsonio, procs
from slate.errors import Refused, UsageError
from slate.state import _util

APPROVALS = "approvals.json"
SCHEMA = "approvals"
GENERATED = "do not edit by hand: slate approve / slate film answer / slate revise"

KINDS: dict[str, dict[str, Any]] = {
    "checklist": {"sitting": "preproduction", "blocking": True, "locks": False,
                  "about": "the request and every ask in brief/GOAL.md (their words, not their status)"},
    "claims": {"sitting": "preproduction", "blocking": True, "locks": False,
               "about": "every claim the film makes, each on the approved list or sourced"},
    "script": {"sitting": "preproduction", "blocking": True, "locks": False,
               "about": "the script as built (script/storyboard.json)"},
    "voice_shortlist": {"sitting": "preproduction", "blocking": True, "locks": False,
                        "about": "the voices shortlisted for the film"},
    "look_test": {"sitting": "preproduction", "blocking": True, "locks": False,
                  "about": "a moving 10–15 second look and motion test with sound (stills don't count)"},
    "final_cut": {"sitting": "final_cut", "blocking": True, "locks": True,
                  "about": "the exact master that may be delivered and published"},
    "hero": {"sitting": "packet", "blocking": False, "locks": False, "about": "a hero moment, from a review packet"},
    "plate": {"sitting": "packet", "blocking": False, "locks": True, "about": "a generated plate"},
    "sound_proof": {"sitting": "packet", "blocking": False, "locks": False, "about": "a sound proof"},
    "take": {"sitting": "packet", "blocking": False, "locks": True, "about": "a voice take"},
    "score_take": {"sitting": "packet", "blocking": False, "locks": True, "about": "a score take"},
    "spend": {"sitting": None, "blocking": False, "locks": False,
              "about": "a person's yes to spend on this path again (e.g. regenerating a locked asset)"},
    "grant": {"sitting": None, "blocking": False, "locks": False,
              "about": "an autonomy grant: what the owner lets an agent decide on its own, in their words (never "
                       "purchases, upgrades, licences or publishing)"},
}
PREPRODUCTION = ("checklist", "claims", "script", "voice_shortlist", "look_test")
# Other modules qualify an approval with what it's for ("plate-take:S05", "generation:S05"); the part before the colon
# names the kind, through these aliases. A kind Slate doesn't know is kept as "other": recorded, never blocking.
ALIASES = {"plate-take": "plate", "plate_take": "plate", "generation": "spend", "voice-take": "take",
           "voice_take": "take", "score-take": "score_take", "sound-proof": "sound_proof", "look-test": "look_test",
           "final-cut": "final_cut", "voice-shortlist": "voice_shortlist"}
WHAT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}(:[A-Za-z0-9][A-Za-z0-9._/-]{0,119})?$")
# Approvals that belong to Slate rather than one film. They live in DATA/approvals.json, where the module that
# waits on them reads them (proof 3: slate.sound.effects.require_proof3, which gates the effects engine).
DRAFTS = "deliver/drafts/"  # `slate deliver --draft` writes here: never final, so never the approved final cut
GLOBAL_KINDS: dict[str, str] = {
    "proof3": "proof 3 of the sound design; the effects engine starts only after a person approves it",
}


def empty() -> dict:
    return {"schema": 1, "generated": GENERATED, "approvals": [], "questions": [], "revisions": []}


def load(film_dir: Path) -> dict:
    data = jsonio.read(Path(film_dir) / APPROVALS, default=None)
    if not isinstance(data, dict):
        return empty()
    for key in ("approvals", "questions", "revisions"):
        data.setdefault(key, [])
    return data


def save(film_dir: Path, data: dict) -> None:
    data = {**data, "generated": GENERATED}
    jsonio.require_valid(data, SCHEMA, what="approvals.json")
    with _util.state_lock(film_dir):
        jsonio.write_atomic(Path(film_dir) / APPROVALS, data)


def edit(film_dir: Path, change) -> Any:
    with _util.state_lock(film_dir):
        data = load(film_dir)
        out = change(data)
        save(film_dir, data)
        return out


# ---------------------------------------------------------------------------------------------------------------
# What an approval covers


def content_hash(film_dir: Path, what: str, rel: str, hasher: _util.Hasher | None = None) -> tuple[str | None, str]:
    """(sha256, basis) of what an approval of `what` at `rel` covers, as it is now."""
    from slate.state import lock, production  # noqa: PLC0415

    if what == "checklist":
        return jsonio.sha256_bytes(production.checklist_bytes(film_dir)), "asks"
    own = hasher is None
    hasher = hasher or _util.Hasher(film_dir)
    target = lock.resolve(film_dir, rel)
    digest, kind = hasher.any(target)
    if own:
        hasher.save()
    return digest, kind


def kind_of(what: str) -> str:
    """The kind an approval counts as: 'plate-take:S05' is a plate, 'generation:S05' a spend approval."""
    base = str(what or "").split(":", 1)[0]
    base = ALIASES.get(base, base)
    return base if base in KINDS else "other"


def _check_what(what: str) -> str:
    what = str(what or "").strip()
    if not WHAT_RE.match(what):
        raise UsageError(f"{what!r} isn't an approval name.",
                         hint="One of: " + ", ".join(KINDS) + " (optionally qualified, e.g. plate:S05).")
    return what


def _matches(rec: dict, what: str) -> bool:
    """A record answers `what` when it's that exact approval, or (for a bare kind) any approval of that kind."""
    if rec["what"] == what:
        return True
    return ":" not in what and rec.get("kind", kind_of(rec["what"])) == kind_of(what) and kind_of(what) != "other"


def record(film_dir: Path, *, what: str, path: str, approved_by: str, note: str | None = None,
           version: int | None = None) -> dict:
    """Record a person's approval of exactly this content. Returns the record (with its sha256).

    Takes, score takes, plates and the final cut are locked by content hash at the same moment, so approved work is
    never regenerated by accident.
    """
    from slate.state import film, lock, modes, production  # noqa: PLC0415

    film_dir = Path(film_dir)
    what = _check_what(what)
    kind = kind_of(what)
    if not (approved_by or "").strip():
        raise UsageError("An approval needs the person who gave it (--by NAME).")
    modes.check(film_dir, "approve", what=kind)
    rel = _util.film_rel(film_dir, path)
    if kind == "final_cut" and (rel == DRAFTS.rstrip("/") or rel.startswith(DRAFTS)):
        raise Refused(f"{rel} is a draft delivery, and drafts are never final, so it can't be the approved final cut.",
                      hint="Deliver the version for real (`slate deliver`, without --draft) and approve its master.")
    digest, basis = content_hash(film_dir, kind, rel)
    if digest is None and kind != "spend":  # a yes to spend may name something not made yet (a plate to generate)
        raise UsageError(f"{rel} doesn't exist (or is an empty folder), so there's nothing to approve.")
    spec = KINDS.get(kind, {"sitting": None, "blocking": False, "locks": False})
    rec = {
        "id": f"ap_{_secrets.token_hex(5)}",
        "what": what,
        "kind": kind,
        "path": rel,
        "sha256": digest,
        "basis": basis,
        "approved_by": approved_by.strip(),
        "at": _util.now(),
        "note": note,
        "sitting": spec["sitting"],
        "blocking": bool(spec["blocking"]),
        "version": version if version is not None else film.current_version(film_dir),
        "recorded_by": _recorder(),
        "mode": film.mode_of(film_dir),
        "inherited_from": None,
    }
    edit(film_dir, lambda data: data["approvals"].append(rec))
    if spec["locks"] and basis in ("file", "tree"):
        lock.lock_asset(film_dir, rel, reason=f"approved {what} ({approved_by.strip()}, {rec['at'][:10]})",
                        by=approved_by.strip())
    if kind == "final_cut":
        _release_hold(film_dir, rec)
    production.render(film_dir)
    return rec


HOLD_SOURCE = "slate review"  # a held film's question comes from its review packet ("slate review v3")


def _release_hold(film_dir: Path, rec: dict) -> None:
    """A final-cut approval is the answer to the question a held film parked on (finish's review packet asks it), so
    an approved film doesn't stay parked."""
    for q in open_questions(film_dir, parking=True):
        if str(q.get("source") or "").startswith(HOLD_SOURCE):
            answer(film_dir, q["id"], by=rec["approved_by"],
                   text=f"Approved the final cut: {rec['path']} (sha256 {str(rec['sha256'])[:12]}, {rec['id']}).")


# ---------------------------------------------------------------------------------------------------------------
# Autonomy grants


# An autonomy grant is the owner letting an agent decide something on its own ("creative decisions on act 3"), in their
# words. It isn't a sitting and never blocks, but it's recorded like every approval: the SHA-256 of exactly those words,
# kept in brief/grants/gN.txt, so an edit to them shows. Its kind is "grant": recorded, never a sitting, never blocking.
# (Grants recorded before the kind existed were filed as "other"; grants() reads them by what they are, "grant".)
GRANT = "grant"
GRANTS_DIR = "brief/grants"


def grant(film_dir: Path, *, scope: str, approved_by: str, note: str | None = None) -> dict:
    """Record an autonomy grant with its scope in the owner's words. Returns the approval record (its note is the
    scope, verbatim)."""
    film_dir = Path(film_dir)
    words = (scope or "").strip()
    if not words:
        raise UsageError("A grant needs its scope, in the owner's words (--scope \"…\").",
                         hint='For example: slate approve grant --scope "creative decisions on act 3" --by NAME')
    if not (approved_by or "").strip():
        raise UsageError("A grant needs the person who gave it (--by NAME).")
    folder = film_dir / GRANTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    with _util.state_lock(film_dir):
        taken = [int(p.stem[1:]) for p in folder.glob("g*.txt") if p.stem[1:].isdigit()]
        rel = f"{GRANTS_DIR}/g{1 + max(taken, default=0)}.txt"
        jsonio.write_text_atomic(film_dir / rel, words + "\n")
    return record(film_dir, what=GRANT, path=rel, approved_by=approved_by,
                  note=words if not (note or "").strip() else f"{words} ({note.strip()})")


def grants(film_dir: Path) -> list[dict]:
    """Every autonomy grant on this film, oldest first: {id, scope, by, at, path, standing} (standing is False when the
    words on file no longer match what was granted)."""
    out = []
    for rec in load(film_dir)["approvals"]:
        if str(rec.get("what") or "").split(":", 1)[0] != GRANT:
            continue
        p = Path(film_dir) / rec["path"]
        scope = p.read_text(encoding="utf-8").strip() if p.is_file() else (rec.get("note") or "")
        out.append({"id": rec["id"], "scope": scope, "by": rec.get("approved_by"), "at": rec.get("at"),
                    "path": rec["path"], "standing": bool(check(film_dir, rec["what"], rec["path"])["approved"]),
                    "recorded_by": rec.get("recorded_by")})
    return out


def is_global(what: str) -> bool:
    """True for an approval that belongs to Slate, not one film (proof 3)."""
    return str(what or "").strip().lower() in GLOBAL_KINDS


def global_file() -> Path:
    from slate import paths  # noqa: PLC0415

    return paths.data() / APPROVALS


def record_global(*, what: str, path: str, approved_by: str, note: str | None = None) -> dict:
    """Record a person's approval of one of Slate's own gates (GLOBAL_KINDS) in DATA/approvals.json, with the SHA-256 of
    exactly the file approved. The record is appended; the newest one for a gate is the one that counts."""
    from slate import paths  # noqa: PLC0415

    what = str(what or "").strip().lower()
    if what not in GLOBAL_KINDS:
        raise UsageError(f"{what!r} isn't one of Slate's own approvals ({', '.join(GLOBAL_KINDS)}).")
    who = (approved_by or "").strip()
    if not who:
        raise UsageError("An approval needs the person who gave it (--by NAME).")
    target = Path(path).expanduser()
    if not target.is_file():
        raise UsageError(f"{target} isn't a file, so there's nothing to approve.",
                         hint="Name the exact file the person approved (the rendered proof).")
    target = target.resolve()
    rec = {"id": f"ap_{_secrets.token_hex(5)}", "what": what, "path": str(target), "sha256": jsonio.sha256(target),
           "approved_by": who, "approved_at": _util.now(), "note": note, "recorded_by": _recorder()}
    folder = paths.data()
    folder.mkdir(parents=True, exist_ok=True)
    with _util.state_lock(folder):
        records = _global_records(jsonio.read(global_file(), default=None))
        jsonio.write_atomic(global_file(), {"generated": "do not edit by hand: slate approve "
                                                         + " | ".join(GLOBAL_KINDS),
                                            "approvals": records + [rec]})
    return rec


def _global_records(data: Any) -> list[dict]:
    """Every record already in DATA/approvals.json, whichever shape it was written in (a list, {"approvals": [...]}, or
    one record per gate name), so writing a new one never drops an older one."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("approvals"), list):
        return [r for r in data["approvals"] if isinstance(r, dict)]
    out: list[dict] = []
    for key, value in data.items():
        if isinstance(value, dict):
            out.append({"what": key, **value})
        elif isinstance(value, list):
            out.extend({"what": key, **v} for v in value if isinstance(v, dict))
    return out


@functools.lru_cache(maxsize=1)
def _recorder() -> str:
    """Who typed the command: an agent's name, or 'terminal' for a person (asked once per process)."""
    return procs.agent_context() or "terminal"


def inherit(film_dir: Path, rec: dict, *, parent_id: str, parent_dir: Path) -> dict:
    """Carry a parent's approval into a derived film by reference (its path resolves into the parent)."""
    new = {**rec, "id": f"ap_{_secrets.token_hex(5)}", "at": _util.now(),
           "inherited_from": {"film": parent_id, "dir": str(Path(parent_dir).resolve()), "approval": rec["id"]},
           "recorded_by": "slate variant"}
    edit(film_dir, lambda data: data["approvals"].append(new))
    return new


def records(film_dir: Path, what: str | None = None) -> list[dict]:
    return [a for a in load(film_dir)["approvals"] if what is None or _matches(a, what)]


def latest(film_dir: Path, what: str, path: str | None = None) -> dict | None:
    rel = _util.film_rel(film_dir, path) if path else None
    for rec in reversed(load(film_dir)["approvals"]):
        if _matches(rec, what) and (rel is None or rec["path"] == rel):
            return rec
    return None


def check(film_dir: Path, what: str, path: str, hasher: _util.Hasher | None = None) -> dict:
    """{approved, reason, record, current_sha256}: approved only when the latest approval's hash matches now."""
    film_dir = Path(film_dir)
    _check_what(what)
    rel = _util.film_rel(film_dir, path)
    rec = latest(film_dir, what, rel)
    if rec is None:
        return {"approved": False, "reason": f"no {what} approval for {rel}", "record": None, "current_sha256": None}
    current, _basis = content_hash(film_dir, rec.get("kind") or kind_of(rec["what"]), rel, hasher)
    if current is None:
        return {"approved": False, "reason": f"{rel} is gone", "record": rec, "current_sha256": None}
    if current != rec["sha256"]:
        return {"approved": False, "reason": f"{rel} changed since {rec['approved_by']} approved it "
                                             f"({rec['at'][:16]})", "record": rec, "current_sha256": current}
    return {"approved": True, "reason": f"approved by {rec['approved_by']} ({rec['at'][:16]})", "record": rec,
            "current_sha256": current}


def is_approved(film_dir: Path, what: str, path: str) -> bool:
    """True only when a person approved exactly this content: the recorded SHA-256 must match the file now."""
    return bool(check(film_dir, what, path)["approved"])


def kind_state(film_dir: Path, what: str, hasher: _util.Hasher | None = None) -> dict:
    """Whether an approval of this kind stands now: {approved, paths, stale: [paths approved but since changed]}."""
    latest_by_path: dict[str, dict] = {}
    for rec in load(film_dir)["approvals"]:
        if _matches(rec, what):
            latest_by_path[rec["path"]] = rec
    ok, stale = [], []
    for rel, rec in latest_by_path.items():
        result = check(film_dir, rec["what"], rel, hasher)
        (ok if result["approved"] else stale).append(rel)
    return {"approved": bool(ok), "paths": ok, "stale": stale}


def spend_covers(film_dir: Path, approval_id: str, rel_path: str) -> bool:
    """True when `approval_id` is a spend approval for this path (or a folder that holds it)."""
    rel = _util.film_rel(film_dir, rel_path)
    for rec in load(film_dir)["approvals"]:
        if rec["id"] == approval_id and rec.get("kind", kind_of(rec["what"])) == "spend":
            return rel == rec["path"] or rel.startswith(rec["path"].rstrip("/") + "/")
    return False


def spend_approval(film_dir: Path, approval_id: str) -> dict | None:
    for rec in load(film_dir)["approvals"]:
        if rec["id"] == approval_id and rec.get("kind", kind_of(rec["what"])) == "spend":
            return rec
    return None


# ---------------------------------------------------------------------------------------------------------------
# Sittings


def covered_by_brand(film_dir: Path) -> dict[str, str]:
    """Sitting items the brand's approved references already answer (why each is covered)."""
    from slate.state import deps, film  # noqa: PLC0415

    fj = film.load(film_dir)
    try:
        pack = deps.brand_load(fj["brand"])
    except Exception:  # noqa: BLE001 - an unreadable pack covers nothing
        return {}
    out = {}
    if deps.brand_reference_approved(pack, "look_motion"):
        out["look_test"] = "the brand's look and motion reference is approved (no look test needed)"
    if film.mode_of(film_dir) == "autonomous":
        if deps.brand_reference_approved(pack, "truth"):
            out["claims"] = "autonomous films use only the brand's approved claims"
        if deps.brand_reference_approved(pack, "voice"):
            out["voice_shortlist"] = "the brand's approved voice reference"
    return out


def sittings(film_dir: Path, hasher: _util.Hasher | None = None) -> dict:
    """Both sittings for this film's mode: which items each holds, which are approved and which are pending."""
    from slate.state import film  # noqa: PLC0415

    mode = film.mode_of(film_dir)
    covered = covered_by_brand(film_dir)
    pre = list(PREPRODUCTION)
    if mode == "supervised":
        plan = {"preproduction": {"blocking": True, "items": pre, "note": None},
                "final_cut": {"blocking": True, "items": ["final_cut"], "note": None}}
    elif mode in ("express", "autonomous"):
        why = ("express: every approval is batched into the final-cut sitting" if mode == "express" else
               "autonomous: the film runs through and is held for a person at the final cut")
        plan = {"preproduction": {"blocking": False, "items": [], "note": why},
                "final_cut": {"blocking": True, "items": pre + ["final_cut"], "note": why}}
    else:
        plan = {"preproduction": {"blocking": False, "items": [], "note": "draft: nothing blocks"},
                "final_cut": {"blocking": False, "items": [], "note": "draft films are never delivered as final"}}
    for sitting in plan.values():
        approved, pending, stale = [], [], []
        for what in sitting["items"]:
            if what in covered:
                approved.append(what)
                continue
            state = kind_state(film_dir, what, hasher)
            if state["approved"]:
                approved.append(what)
            else:
                pending.append(what)
                if state["stale"]:
                    stale.append(what)
        sitting.update(approved=approved, pending=pending, changed_since_approved=stale,
                       done=not pending and bool(sitting["items"]))
    plan["covered"] = covered
    packets = {}
    for kind, spec in KINDS.items():
        if spec["sitting"] == "packet":
            packets[kind] = len(records(film_dir, kind))
    plan["packets"] = packets
    return plan


def pending(film_dir: Path, sitting: str) -> list[str]:
    plan = sittings(film_dir)
    if sitting not in ("preproduction", "final_cut"):
        raise UsageError(f"Unknown sitting {sitting!r}; preproduction or final_cut.")
    return list(plan[sitting]["pending"])


# ---------------------------------------------------------------------------------------------------------------
# Questions waiting for a person


def ask(film_dir: Path, text: str, *, source: str, evidence: list[str] | tuple = (), parks: bool = False,
        for_: str | None = None, revision: str | None = None) -> dict:
    """Record a question for a person. With parks=True the film waits for the answer (status shows it first)."""
    if not (text or "").strip():
        raise UsageError("The question is empty.")

    def change(data: dict) -> dict:
        n = 1 + max((int(q["id"][1:]) for q in data["questions"]), default=0)
        q = {"id": f"q{n}", "text": text.strip(), "evidence": [str(e) for e in evidence], "source": source,
             "asked_at": _util.now(), "parks": bool(parks), "for": for_, "revision": revision, "answer": None,
             "answered_by": None, "answered_at": None}
        data["questions"].append(q)
        return q

    q = edit(film_dir, change)
    from slate.state import production  # noqa: PLC0415

    production.render(film_dir)
    return q


def answer(film_dir: Path, question_id: str, *, text: str, by: str) -> dict:
    """A person's answer. It's also logged verbatim as a direction change, because it steers the film."""
    if not (text or "").strip() or not (by or "").strip():
        raise UsageError("An answer needs the words (--text) and who gave them (--by).")

    wanted = str(question_id or "").strip().lower()  # "Q1" and "q1" are the same question

    def change(data: dict) -> dict:
        for q in data["questions"]:
            if str(q["id"]).lower() == wanted:
                question_id_ = q["id"]
                if q.get("answered_at"):
                    raise UsageError(f"{question_id_} was already answered by {q['answered_by']}.")
                q.update(answer=text.strip(), answered_by=by.strip(), answered_at=_util.now())
                return q
        raise UsageError(f"There's no question {question_id}.", hint="slate film questions lists them.")

    q = edit(film_dir, change)
    from slate.state import production  # noqa: PLC0415

    production.add_direction(film_dir, text.strip(), source=by.strip(), kind=f"answer to {q['id']}: {q['text']}")
    return q


def open_questions(film_dir: Path, *, parking: bool | None = None) -> list[dict]:
    out = [q for q in load(film_dir)["questions"] if not q.get("answered_at")]
    if parking is not None:
        out = [q for q in out if bool(q.get("parks")) == parking]
    return out
