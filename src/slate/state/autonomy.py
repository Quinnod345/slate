"""The autonomy lock: a film made autonomous stays autonomous until a person, at their own Terminal, says otherwise.

An autonomous film's limits are the point of autonomous mode: the monthly caps in Slate's budget file, its own capped
ElevenLabs key, a person's yes before every generation, no After Effects, and a hold until the owner approves the exact
file. They can't hang on a value the autonomous caller can change itself, and film.json sits in the film folder, where
any agent can edit it. So:

- `slate new` and `slate variant` record each film's mode in Slate's own data folder (DATA/film_modes.jsonl), outside
  the film folder: one line per event, keyed by the film's folder, with its id and creation stamp so a moved copy is
  still found;
- a change away from autonomous goes through `slate film set mode …` (or `slate variant --mode …` for a child) and is
  refused unless `slate.ledger.person_at_terminal()` (the guard `slate budget set` uses). An allowed change is recorded
  with who made it and why; a refused attempt is recorded too, with who tried;
- whatever decides what a film may do asks `film_mode` (or `slate.ledger.effective_mode`, which asks it): a film the
  record holds autonomous is autonomous, whatever film.json now says. slate.ledger (caps, keys, raises), slate.ae.guard,
  slate.finish.master, slate.jobs and slate.state.modes all do;
- After Effects touches an autonomous film (by its record or its film.json) only through the owner's own finish
  (`ae_finish`): `slate master --ae --go`, typed by a person at their Terminal (never an agent, never a queued job),
  after a final-cut approval of the film's held master.

A film made before this record existed has no entry and keeps its film.json mode. The record is Slate's, never the
film's: nothing here writes inside a film folder. It guards against an agent flipping or editing its own way out; an
agent that rewrites Slate's data folder itself, or copies a film and changes both its id and its creation stamp, is
past what a record can see, which is why the vendors' own caps (Oneiro's capped key) back Slate's.
"""

from __future__ import annotations

import contextlib
import contextvars
import fcntl
import getpass
import json
import os
from pathlib import Path
from typing import Any, Iterator

from slate import jsonio, paths
from slate.errors import Refused, UsageError

MODES_FILE = "film_modes.jsonl"
LOCK_FILE = "film_modes.lock"
AUTONOMOUS = "autonomous"
HELD_MASTER_RECORD = "masters/master.json"  # slate.finish.master writes it; the held master's bytes are listed there
# The After Effects steps the owner's own finish takes (slate master --ae: build, audit, render the masters).
FINISH_ACTIONS = frozenset({"ae", "ae_finish", "ae_build", "ae_project", "ae_master"})
FINISH_COMMAND = "slate master --ae --go"

_FINISHING: contextvars.ContextVar[str | None] = contextvars.ContextVar("slate_owner_ae_finish", default=None)


# ---------------------------------------------------------------------------------------------------------------
# The record (DATA/film_modes.jsonl)


def path() -> Path:
    """Where the record lives: Slate's own data folder, never a film folder."""
    return paths.data() / MODES_FILE


def _key(film_dir: Path | str) -> str:
    return str(Path(film_dir).expanduser().resolve())


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    lock = paths.data() / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _append(event: dict) -> dict:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _locked():
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return event


def events() -> list[dict]:
    """Every recorded event, oldest first. A torn last line (a crash mid-write) is skipped, never fatal."""
    target = path()
    if not target.is_file():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _now() -> str:
    from slate.state import _util  # noqa: PLC0415 (state's own stamp: local time with its offset)

    return _util.now()


def _context() -> str:
    """Who is typing: an agent's name, or 'terminal' for a person."""
    from slate import procs  # noqa: PLC0415

    return procs.agent_context() or "terminal"


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name (a bare launchd job): say so rather than fail
        return "unknown"


def _fold(evs: list[dict]) -> tuple[dict[str, dict], dict[tuple[Any, Any], list[dict]]]:
    """({folder: state}, {(film id, created_at): [states]}) after every event, in order."""
    by_dir: dict[str, dict] = {}
    by_ident: dict[tuple[Any, Any], list[dict]] = {}

    def index(st: dict) -> None:
        if st.get("film") and st.get("created_at"):
            group = by_ident.setdefault((st["film"], st["created_at"]), [])
            if st not in group:
                group.append(st)

    for ev in evs:
        kind, folder = ev.get("event"), ev.get("dir")
        if not folder:
            continue
        if kind == "created":
            st = {"film": ev.get("film"), "dir": folder, "created_at": ev.get("created_at"), "mode": ev.get("mode"),
                  "made_mode": ev.get("mode"), "made_at": ev.get("at"), "since": ev.get("at"), "by": ev.get("by"),
                  "why": ev.get("why"), "parent": ev.get("parent"), "history": [ev], "refused": 0}
            by_dir[folder] = st
            index(st)
        elif kind == "changed":
            st = by_dir.get(folder)
            if st is None:  # a moved copy (or a film older than the record) changed by a person: start from its twin
                twins = by_ident.get((ev.get("film"), ev.get("created_at"))) or []
                base = twins[-1] if twins else {}
                st = {"film": ev.get("film"), "dir": folder, "created_at": ev.get("created_at"),
                      "made_mode": base.get("made_mode", ev.get("from")), "made_at": base.get("made_at"),
                      "parent": base.get("parent"), "history": list(base.get("history") or []), "refused": 0}
                by_dir[folder] = st
            st.update(mode=ev.get("to"), since=ev.get("at"), by=ev.get("by"), why=ev.get("why"))
            st["history"].append(ev)
            index(st)
        elif kind == "refused":
            st = by_dir.get(folder)
            if st is not None:
                st["history"].append(ev)
                st["refused"] = int(st.get("refused") or 0) + 1
    return by_dir, by_ident


def _read_film_json(film_dir: Path | str | None) -> dict | None:
    if not film_dir:
        return None
    try:
        data = jsonio.read(Path(film_dir) / "film.json", default=None)
    except UsageError:
        return None
    return data if isinstance(data, dict) else None


def state(film_dir: Path | str | None = None, fj: dict | None = None) -> dict | None:
    """What Slate's record says about this film: {film, dir, mode, made_mode, since, by, why, history, refused}, or
    None when the record has no entry for it (a film made before the record, or one Slate didn't make).

    Found by the film's folder first; a folder with no entry of its own (a moved or copied film) is matched by the
    film's id and creation stamp, and then any autonomous entry among its twins wins."""
    evs = events()
    if not evs:
        return None
    by_dir, by_ident = _fold(evs)
    if film_dir:
        st = by_dir.get(_key(film_dir))
        if st is not None:
            return st
        if fj is None:
            fj = _read_film_json(film_dir)
    if fj and fj.get("id") and fj.get("created_at"):
        twins = by_ident.get((fj["id"], fj["created_at"])) or []
        held = [s for s in twins if s.get("mode") == AUTONOMOUS]
        if held:
            return held[-1]
        return twins[-1] if twins else None
    return None


def is_locked(film_dir: Path | str | None = None, fj: dict | None = None) -> bool:
    """True when Slate's record holds this film autonomous (whatever its film.json says)."""
    st = state(film_dir, fj)
    return bool(st and st.get("mode") == AUTONOMOUS)


def film_mode(film_dir: Path | str | None = None, fj: dict | None = None) -> str:
    """The mode a film is judged in: autonomous when either film.json or Slate's record says so, else film.json's."""
    if fj is None:
        fj = _read_film_json(film_dir)
    own = str((fj or {}).get("mode") or "supervised")
    if own == AUTONOMOUS or is_locked(film_dir, fj):
        return AUTONOMOUS
    return own


def describe(film_dir: Path | str) -> dict:
    """For `slate status` and `slate film show`: the record, and whether film.json disagrees with it."""
    fj = _read_film_json(film_dir) or {}
    st = state(film_dir, fj)
    own = fj.get("mode")
    if st is None:
        return {"recorded": False, "mode": own, "film_json": own, "mismatch": False, "file": str(path())}
    locked = st.get("mode") == AUTONOMOUS
    return {"recorded": True, "mode": AUTONOMOUS if locked else own, "record_mode": st.get("mode"),
            "film_json": own, "made_mode": st.get("made_mode"), "since": st.get("since"), "by": st.get("by"),
            "why": st.get("why"), "locked": locked, "mismatch": bool(locked and own != AUTONOMOUS),
            "refused_changes": int(st.get("refused") or 0), "file": str(path())}


# ---------------------------------------------------------------------------------------------------------------
# Writing the record


def record_created(film_dir: Path | str, fj: dict, *, parent: dict | None = None, by: str | None = None,
                   why: str | None = None) -> dict:
    """Record a new film's mode, at `slate new` or `slate variant`, before anything else can change it."""
    return _append({"event": "created", "at": _now(), "film": fj.get("id"), "dir": _key(film_dir),
                    "created_at": fj.get("created_at"), "mode": fj.get("mode") or "supervised",
                    "parent": parent, "by": by or _context(), "context": _context(), "user": _user(), "why": why})


def _refusal(film_id: str, to: str, why_not: str, film_dir: Path | str) -> Refused:
    return Refused(
        f"{film_id} was made autonomous, and only a person at their own Terminal can change that ({why_not}). It "
        "keeps its autonomous limits (the monthly caps, its own capped key, a yes before every generation, no After "
        "Effects) whatever film.json says.",
        hint=f"Ask the owner. In their own Terminal: slate film set mode {to} --film {film_dir} --reason \"why\"",
        details={"film": film_id, "from": AUTONOMOUS, "to": to, "record": str(path()), "attempt_recorded": True})


def check_change(film_dir: Path | str, fj: dict, to: str, *, why: str | None = None) -> str:
    """The rule for a mode change, without recording anything: returns the film's mode now. Raises Refused when a
    change away from autonomous doesn't come from a person at their own Terminal, and UsageError when it has no
    reason."""
    from slate import ledger  # noqa: PLC0415

    current = film_mode(film_dir, fj)
    if current == AUTONOMOUS and to != AUTONOMOUS:
        ok, why_not = ledger.person_at_terminal()
        if not ok:
            raise _refusal(str(fj.get("id") or Path(film_dir).name), to, why_not, film_dir)
        if not (why or "").strip():
            raise UsageError("A change away from autonomous is recorded with who made it and why: add "
                             "--reason \"…\" (and --by NAME if it isn't you).")
    return current


def guard_change(film_dir: Path | str, fj: dict, to: str, *, by: str | None = None, why: str | None = None) -> str:
    """`check_change`, with a refused attempt to leave autonomous recorded (who tried, from where) before it's raised.
    Returns the film's mode now."""
    try:
        return check_change(film_dir, fj, to, why=why)
    except Refused as exc:
        _append({"event": "refused", "at": _now(), "film": fj.get("id"), "dir": _key(film_dir),
                 "created_at": fj.get("created_at"), "from": AUTONOMOUS, "to": to, "context": _context(),
                 "user": _user(), "by": by, "why": why, "refusal": exc.message})
        raise


def record_change(film_dir: Path | str, fj: dict, to: str, *, by: str | None = None,
                  why: str | None = None) -> dict | None:
    """Record a mode change that passed `guard_change` (call it before film.json is written). Returns the event, or
    None when the mode Slate judges the film in doesn't change. `by` defaults to the person's login (or the agent)."""
    current = film_mode(film_dir, fj)
    if current == to:
        return None
    who = (by or "").strip() or (_user() if _context() == "terminal" else _context())
    return _append({"event": "changed", "at": _now(), "film": fj.get("id"), "dir": _key(film_dir),
                    "created_at": fj.get("created_at"), "from": current, "to": to, "by": who,
                    "context": _context(), "user": _user(), "why": (why or "").strip() or None})


def change_mode(film_dir: Path | str, fj: dict, to: str, *, by: str | None = None, why: str | None = None) -> dict | None:
    """`guard_change` then `record_change`: the rule and the record for one change of a film's mode."""
    guard_change(film_dir, fj, to, by=by, why=why)
    return record_change(film_dir, fj, to, by=by, why=why)


def check_child(parent_dir: Path | str, pj: dict, mode: str, *, why: str | None = None) -> None:
    """A derived film starts in its parent's mode; a child of an autonomous film in another mode is the same change
    away from autonomous, so it needs the same person at the same Terminal, and a reason."""
    if film_mode(parent_dir, pj) != AUTONOMOUS or mode == AUTONOMOUS:
        return
    from slate import ledger  # noqa: PLC0415

    ok, why_not = ledger.person_at_terminal()
    pid = str(pj.get("id") or Path(parent_dir).name)
    if not ok:
        _append({"event": "refused", "at": _now(), "film": pj.get("id"), "dir": _key(parent_dir),
                 "created_at": pj.get("created_at"), "from": AUTONOMOUS, "to": mode, "context": _context(),
                 "user": _user(), "why": why, "refusal": f"a {mode} child of an autonomous film"})
        raise Refused(f"{pid} was made autonomous, so a film derived from it is autonomous too; only a person at their "
                      f"own Terminal can make it {mode} ({why_not}).",
                      hint=f"Leave out --mode (the child stays autonomous), or ask the owner to run it in their own "
                           f"Terminal with --mode {mode} --reason \"why\".",
                      details={"parent": pid, "to": mode, "record": str(path())})
    if not (why or "").strip():
        raise UsageError(f"A {mode} film derived from an autonomous one is recorded with who made it and why: add "
                         "--reason \"…\".")


# ---------------------------------------------------------------------------------------------------------------
# The owner's own After Effects finish


def held_master_approval(film_dir: Path | str) -> dict | None:
    """The final-cut approval that covers this film's held master: a final_cut approval whose bytes are one of the
    masters masters/master.json lists, and whose file still matches what was approved. None when there isn't one."""
    from slate.state import approvals  # noqa: PLC0415

    film_dir = Path(film_dir)
    try:
        rec = jsonio.read(film_dir / HELD_MASTER_RECORD, default=None)
    except UsageError:
        return None
    masters = (rec or {}).get("masters") if isinstance(rec, dict) else None
    shas = {m.get("sha256") for m in masters or [] if isinstance(m, dict) and m.get("sha256")}
    if not shas:
        return None
    for ap in reversed(approvals.records(film_dir, "final_cut")):
        if ap.get("sha256") in shas and approvals.check(film_dir, ap["what"], ap["path"])["approved"]:
            return ap
    return None


def ae_finish_check(film_dir: Path | str) -> dict:
    """May the owner's own After Effects finish of this autonomous film start now? Returns why it may, or raises
    Refused naming what's missing: a person at their own Terminal (never an agent, never a queued job), and a
    final-cut approval of the film's held master."""
    from slate import ledger  # noqa: PLC0415

    film_dir = Path(film_dir)
    fid = str((_read_film_json(film_dir) or {}).get("id") or film_dir.name)
    ok, why_not = ledger.person_at_terminal()
    if os.environ.get("SLATE_JOB_ID"):
        ok, why_not = False, "this is a queued job"
    if not ok:
        raise Refused(f"Autonomous films never use After Effects on their own: {fid}'s one way in is its owner's own "
                      f"finish, started by a person at their own Terminal ({why_not}).",
                      hint=f"The owner approves the held master, then runs `{FINISH_COMMAND}` in their own Terminal "
                           "(not as a queued job).",
                      details={"film": fid, "needs": "a person at their own Terminal"})
    ap = held_master_approval(film_dir)
    if ap is None:
        raise Refused(f"Autonomous films never use After Effects on their own: {fid}'s AE finish starts only once the "
                      "owner has approved its held master (the final cut).",
                      hint="Master it with ffmpeg (slate master), deliver it (slate deliver), and the owner approves "
                           "that exact file: slate approve final_cut <deliver/vN/…_H264.mp4> --by NAME. Then, in your "
                           f"own Terminal: {FINISH_COMMAND}",
                      details={"film": fid, "needs": "a final-cut approval of the held master"})
    return {"film": fid, "approval": ap.get("id"), "approved_by": ap.get("approved_by"), "path": ap.get("path"),
            "sha256": ap.get("sha256"),
            "why": f"the owner approved its held master ({ap.get('path')}, {ap.get('id')}) and started the After "
                   "Effects finish from their own Terminal"}


@contextlib.contextmanager
def ae_finish(film_dir: Path | str) -> Iterator[dict | None]:
    """Hold the owner's own After Effects finish open for this film while its steps run. A film that isn't autonomous
    (by film.json or the record) needs nothing here (yields None); an autonomous one is checked first
    (`ae_finish_check`)."""
    if film_mode(film_dir) != AUTONOMOUS:
        yield None
        return
    verdict = ae_finish_check(film_dir)
    token = _FINISHING.set(_key(film_dir))
    try:
        yield verdict
    finally:
        _FINISHING.reset(token)


def finishing(film_dir: Path | str | None, action: str | None = None) -> bool:
    """Inside the owner's own After Effects finish of this film, for one of its steps, and still a person at a
    Terminal? slate.ae.guard and slate.state.modes let exactly that through."""
    if not film_dir:
        return False
    current = _FINISHING.get()
    if not current or current != _key(film_dir):
        return False
    if action is not None and str(action).lower() not in FINISH_ACTIONS:
        return False
    from slate import ledger  # noqa: PLC0415

    return bool(ledger.person_at_terminal()[0]) and not os.environ.get("SLATE_JOB_ID")
