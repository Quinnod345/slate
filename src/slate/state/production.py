"""The brief: the request verbatim, every ask as a checklist row, and the numbered direction changes.

Two documents a person reads and one file Slate reads:

- `brief/brief.json` is the data: the request exactly as received, each ask in the requester's words with its status
  and evidence, the later messages verbatim, and the direction changes, numbered.
- `brief/GOAL.md` is the goal record: the verbatim request and the checklist. "If a conversation is summarized,
  re-derive the checklist from the verbatim request, not from memory."
- `brief/PRODUCTION.md` is the live tracker: asks in the requester's words, numbered direction changes (the latest
  approval governs), open questions, revisions, approvals and where every stage stands.

Slate owns only the blocks between `<!-- slate:begin … -->` and `<!-- slate:end … -->`; anything written around them
is kept. Done means verified: an ask can't be marked done without evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slate import jsonio
from slate.errors import UsageError
from slate.state import _util

BRIEF = "brief/brief.json"
GOAL = "brief/GOAL.md"
PRODUCTION = "brief/PRODUCTION.md"
ASK_STATUSES = ("open", "in progress", "built", "done", "blocked")
EMPTY = {"schema": 1, "request": None, "asks": [], "messages": [], "directions": []}


# ---------------------------------------------------------------------------------------------------------------
# Data


def load_brief(film_dir: Path) -> dict:
    data = jsonio.read(Path(film_dir) / BRIEF, default=None)
    if not isinstance(data, dict):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY.items()}
    for key, value in EMPTY.items():
        data.setdefault(key, list(value) if isinstance(value, list) else value)
    return data


def _check_brief(data: dict) -> None:
    problems = []
    for key in ("asks", "messages", "directions"):
        if not isinstance(data.get(key), list):
            problems.append(f"{key} must be a list")
    for i, ask in enumerate(data.get("asks") or []):
        if not isinstance(ask, dict) or not ask.get("text") or ask.get("status") not in ASK_STATUSES:
            problems.append(f"asks[{i}] needs text and a status ({', '.join(ASK_STATUSES)})")
    req = data.get("request")
    if req is not None and not (isinstance(req, dict) and isinstance(req.get("text"), str)):
        problems.append("request must be null or {text, source, at}")
    if problems:
        raise UsageError("brief/brief.json is malformed: " + "; ".join(problems))


def save_brief(film_dir: Path, data: dict) -> None:
    _check_brief(data)
    with _util.state_lock(film_dir):
        jsonio.write_atomic(Path(film_dir) / BRIEF, data)


def _language(film_dir: Path) -> str:
    from slate.state import film  # noqa: PLC0415

    return film.language_of(film_dir)


def _new_asks(data: dict, texts: list[str], *, source: str, auto_split: bool) -> list[int]:
    asks = data.setdefault("asks", [])
    numbers = []
    for text in texts:
        n = (max((a["n"] for a in asks), default=0)) + 1
        asks.append({"n": n, "text": text, "source": source, "at": _util.now(), "status": "open", "evidence": None,
                     "auto_split": auto_split})
        numbers.append(n)
    return numbers


def set_request(film_dir: Path, text: str, *, source: str = "requester", split: bool = True) -> list[int]:
    """Record the original request verbatim (once) and add its asks to the checklist. Returns the new ask numbers."""
    text = (text or "").strip("\n")
    if not text.strip():
        raise UsageError("The request is empty; paste it exactly as it was given.")
    with _util.state_lock(film_dir):
        data = load_brief(film_dir)
        if data.get("request"):
            raise UsageError("This film already has its original request; add later messages with `slate film ask`.")
        data["request"] = {"text": text, "source": source, "at": _util.now()}
        items = _util.split_items(text, _language(film_dir)) if split else [text.strip()]
        numbers = _new_asks(data, items, source=f"request ({source})", auto_split=split and len(items) > 1)
        save_brief(film_dir, data)
    render(film_dir)
    return numbers


def add_ask(film_dir: Path, text: str, *, source: str, split: bool = True) -> list[int]:
    """A later message from the requester: kept verbatim, and each ask in it added to the checklist."""
    text = (text or "").strip("\n")
    if not text.strip():
        raise UsageError("The message is empty; paste it exactly as it was given.")
    with _util.state_lock(film_dir):
        data = load_brief(film_dir)
        items = _util.split_items(text, _language(film_dir)) if split else [text.strip()]
        numbers = _new_asks(data, items, source=source, auto_split=split and len(items) > 1)
        messages = data.setdefault("messages", [])
        messages.append({"n": len(messages) + 1, "text": text, "source": source, "at": _util.now(), "asks": numbers})
        save_brief(film_dir, data)
    render(film_dir)
    return numbers


def mark(film_dir: Path, n: int, *, status: str, evidence: str | None = None) -> dict:
    """Set an ask's status. 'done' needs evidence: a file, a measurement or a gate that proves it."""
    if status not in ASK_STATUSES:
        raise UsageError(f"Unknown status {status!r}; one of {', '.join(ASK_STATUSES)}.")
    if status == "done" and not (evidence or "").strip():
        raise UsageError("Done means verified: say what proves it with --evidence (a file, a measurement, a gate).")
    with _util.state_lock(film_dir):
        data = load_brief(film_dir)
        for ask in data["asks"]:
            if ask["n"] == int(n):
                ask["status"] = status
                if evidence is not None:
                    ask["evidence"] = evidence
                ask["updated_at"] = _util.now()
                save_brief(film_dir, data)
                break
        else:
            raise UsageError(f"There's no ask #{n}; the checklist has {len(data['asks'])}.")
    render(film_dir)
    return ask


def add_direction(film_dir: Path, text: str, *, source: str, supersedes: str | None = None,
                  kind: str | None = None) -> int:
    """Log a direction change verbatim, numbered. The latest approval governs, so each one says what it supersedes.

    Returns its number. Review notes, revision notes and mid-production calls all come through here.
    """
    text = (text or "").strip("\n")
    if not text.strip():
        raise UsageError("The direction is empty; log it exactly as it was given.")
    if not (source or "").strip():
        raise UsageError("Say who gave the direction (source).")
    with _util.state_lock(film_dir):
        data = load_brief(film_dir)
        directions = data.setdefault("directions", [])
        n = (max((d["n"] for d in directions), default=0)) + 1
        directions.append({"n": n, "text": text, "source": source, "at": _util.now(), "supersedes": supersedes,
                           "kind": kind})
        save_brief(film_dir, data)
    render(film_dir)
    return n


def checklist_bytes(film_dir: Path) -> bytes:
    """What a checklist approval covers: the request and each ask's words and source, not their status.

    Marking an ask done later doesn't undo the approval of the checklist; adding, dropping or rewording an ask does.
    """
    data = load_brief(film_dir)
    req = data.get("request") or {}
    content = {"request": req.get("text"), "asks": [[a.get("n"), a.get("text"), a.get("source")] for a in data["asks"]]}
    return jsonio.dumps(content, indent=None).encode("utf-8")


def asks_summary(film_dir: Path) -> dict:
    data = load_brief(film_dir)
    asks = data["asks"]
    counts = {s: sum(1 for a in asks if a.get("status") == s) for s in ASK_STATUSES}
    return {"request": bool(data.get("request")), "total": len(asks), "counts": counts,
            "open": [a["n"] for a in asks if a.get("status") != "done"]}


# ---------------------------------------------------------------------------------------------------------------
# Documents


def _settings_rows(fj: dict) -> list[tuple[str, str]]:
    t, b = fj.get("targets") or {}, fj.get("budget") or {}
    runtime = t.get("runtime_s")
    fps = f"{fj.get('fps_num')}/{fj.get('fps_den')}"
    rows = [
        ("F1", f"Format: {fj.get('format')} ({fj.get('width')}×{fj.get('height')}, {fj.get('aspect')}, {fps} fps"
               + (f"; about {runtime:g} s" if isinstance(runtime, (int, float)) else "") + ")"),
        ("F2", f"Brand: {fj.get('brand')}; language: {fj.get('language')}"),
        ("F3", "Budget: ElevenLabs " + _money(b.get("elevenlabs"), "credits") + " · Higgsfield "
               + _money(b.get("higgsfield"), "USD") + f" (×{b.get('rework_factor', 1.5):g} only for marked rework)"),
        ("F4", f"Mode: {fj.get('mode')} · generation: {fj.get('generation')}"),
    ]
    if fj.get("parent"):
        rows.append(("F5", f"Derived from {fj['parent'].get('id')} ({(fj.get('derivation') or {}).get('kind')})"))
    return rows


def _money(v: Any, unit: str) -> str:
    if v is None:
        return "no cap set"
    return f"{v:,.0f} {unit}" if unit == "credits" else f"${v:,.2f}"


def _goal_template(fj: dict) -> str:
    begin_end = {name: _util.block_markers(name) for name in ("request", "checklist", "messages")}
    b = {k: f"{v[0]}\n{v[1]}" for k, v in begin_end.items()}
    return (
        f"# {fj.get('title')}: goal and checklist\n\n"
        f"Film `{fj.get('id')}` · {fj.get('format')} · brand `{fj.get('brand')}` · {fj.get('mode')} mode · "
        f"started {str(fj.get('created_at', ''))[:10]}.\n\n"
        "This file is the source of truth for the goal. If a conversation is summarized, re-derive the checklist from "
        "the verbatim request below, not from memory. Slate keeps the blocks between `slate:` markers current; write "
        "anything else around them.\n\n"
        "## Verbatim request\n\n"
        f"{b['request']}\n\n"
        "## Checklist\n\n"
        "Every ask in the requester's words, plus the brief's settings. **done** means verified, with the evidence "
        "named. Add later messages with `slate film ask \"…\" --source NAME`; mark progress with "
        "`slate film mark N --status done --evidence \"…\"`.\n\n"
        f"{b['checklist']}\n\n"
        "## Later messages (verbatim)\n\n"
        f"{b['messages']}\n"
    )


def _production_template(fj: dict) -> str:
    names = ("asks", "directions", "questions", "revisions", "approvals", "status")
    b = {n: "\n".join(_util.block_markers(n)) for n in names}
    return (
        f"# {fj.get('title')}: production tracker\n\n"
        f"The live tracker for film `{fj.get('id')}`: every ask in the requester's words, every direction change "
        "(numbered, verbatim; the latest approval governs), the questions waiting for a person, revisions, approvals, "
        "and where each stage stands. **done** means verified, with the evidence named. Proofs are not finished "
        "shots.\n\n"
        "Status keys: **done** (verified, with evidence) · **built** (made, not yet through the gates) · "
        "**in progress** · **open** · **blocked** (with the reason).\n\n"
        f"## Asks, in the requester's words\n\n{b['asks']}\n\n"
        f"## Direction changes\n\nEach one is quoted exactly and says what it supersedes. Log one with "
        f"`slate film direction \"…\" --source NAME`.\n\n{b['directions']}\n\n"
        f"## Questions waiting for a person\n\n{b['questions']}\n\n"
        "## Revisions (perfect, don't replace)\n\n"
        "Notes on approved work are changes to that exact artifact, and everything else stays as it was. A note that "
        "needs a new approach is a question first.\n\n"
        f"{b['revisions']}\n\n"
        f"## Approvals\n\n{b['approvals']}\n\n"
        f"## Status\n\n{b['status']}\n"
    )


def _request_block(data: dict) -> str:
    req = data.get("request")
    if not req:
        return ("_No request recorded yet._ Record it exactly as it was given: "
                "`slate film ask --request \"…\" --source NAME`.")
    return f"{_util.quote_block(req['text'])}\n\n_Received {req.get('at', '')[:16]} from {req.get('source')}._"


def _asks_table(data: dict, fj: dict, *, settings: bool) -> str:
    lines = ["| # | Ask (requester's words) | Status | Evidence |", "|---|---|---|---|"]
    for ask in data["asks"]:
        note = " _(split automatically: check the wording)_" if ask.get("auto_split") else ""
        lines.append(f"| {ask['n']} | {_util.md_cell(ask['text'])}{note} | {ask['status']} | "
                     f"{_util.md_cell(ask.get('evidence') or '')} |")
    if settings:
        for key, text in _settings_rows(fj):
            lines.append(f"| {key} | {_util.md_cell(text)} | set | film.json |")
    if len(lines) == 2:
        lines.append("| | _No asks yet._ | | |")
    return "\n".join(lines)


def _messages_block(data: dict) -> str:
    msgs = data.get("messages") or []
    if not msgs:
        return "_None yet._"
    parts = []
    for m in msgs:
        asks = ", ".join(f"#{n}" for n in m.get("asks") or []) or "none"
        parts.append(f"**{m['n']}.** {m.get('source')}, {str(m.get('at', ''))[:16]} (asks {asks}):\n\n"
                     f"{_util.quote_block(m['text'])}")
    return "\n\n".join(parts)


def _directions_block(data: dict) -> str:
    dirs = data.get("directions") or []
    if not dirs:
        return "_None yet._"
    out = []
    for d in dirs:
        extra = []
        if d.get("kind"):
            extra.append(d["kind"])
        if d.get("supersedes"):
            extra.append(f"supersedes {d['supersedes']}")
        tail = f" ({'; '.join(extra)})" if extra else ""
        body = d["text"].replace("\n", " ").strip()
        out.append(f"{d['n']}. **{d.get('source')}, {str(d.get('at', ''))[:16]}**{tail}: “{body}”")
    return "\n".join(out)


def _questions_block(film_dir: Path) -> str:
    from slate.state import approvals  # noqa: PLC0415

    open_q = [q for q in approvals.load(film_dir)["questions"] if not q.get("answered_at")]
    if not open_q:
        return "_None open._"
    lines = []
    for q in open_q:
        park = " **The film is parked until this is answered.**" if q.get("parks") else ""
        ev = "; ".join(q.get("evidence") or [])
        lines.append(f"- **{q['id']}** ({q.get('source')}, {str(q.get('asked_at', ''))[:16]}): {q['text']}{park}"
                     + (f" Evidence: {ev}." if ev else "")
                     + f" Answer with `slate film answer {q['id']} --by NAME --text \"…\"`.")
    return "\n".join(lines)


def _revisions_block(film_dir: Path) -> str:
    from slate.state import approvals  # noqa: PLC0415

    revs = approvals.load(film_dir)["revisions"]
    if not revs:
        return "_None yet._"
    parts = []
    for r in revs:
        head = (f"### {r['id']}: `{r['artifact']}` ({r['status']})\n\n"
                f"Approved version: sha256 `{_util.short(r.get('approved_sha'))}` ({r.get('what')}, approval "
                f"{r.get('approval')}); notes from {r.get('by')}, {str(r.get('started_at', ''))[:16]}"
                + (f", logged as direction {r['direction']}" if r.get("direction") else "") + ".\n\n"
                f"{_util.quote_block(r['notes'])}\n\n"
                "Changes to this exact artifact:\n")
        items = "\n".join(f"- [{'x' if i['status'] == 'done' else ' '}] {i['n']}. {i['text']}"
                          + (f" _(scope: {', '.join(i['scope'])})_" if i.get("scope") else "")
                          + (f" · evidence: {i['evidence']}" if i.get("evidence") else "")
                          for i in r["items"]) or "- (none: every note became a question)"
        tail = [f"\n\n{r.get('keep') or 'Everything else stays as it was.'}"]
        if r.get("questions"):
            tail.append(f" Questions first (a new approach needs a yes): {', '.join(r['questions'])}.")
        if r.get("checks"):
            flagged = [c for c in r["checks"] if c.get("status") in ("FAIL", "REVIEW", "UNCHECKED")]
            tail.append(f" Last check {str(r.get('checked_at', ''))[:16]}: "
                        + ("nothing changed outside the checklist." if not flagged else
                           "; ".join(f"{c['gate']} {c['status']}: {c['summary']}" for c in flagged)))
        parts.append(head + items + "".join(tail))
    return "\n\n".join(parts)


def _approvals_block(film_dir: Path) -> str:
    from slate.state import approvals  # noqa: PLC0415

    recs = approvals.load(film_dir)["approvals"]
    if not recs:
        return "_Nothing approved yet._"
    lines = ["| What | Path | sha256 | By | When | Sitting | Note |", "|---|---|---|---|---|---|---|"]
    for a in recs:
        inherited = f" (inherited from {a['inherited_from'].get('film')})" if a.get("inherited_from") else ""
        lines.append(f"| {a['what']} | `{_util.md_cell(a['path'])}` | `{_util.short(a.get('sha256'))}` | "
                     f"{_util.md_cell(a['approved_by'])}{inherited} | {str(a.get('at', ''))[:16]} | "
                     f"{a.get('sitting') or ''} | {_util.md_cell(a.get('note') or '')} |")
    return "\n".join(lines)


def _status_block(film_dir: Path) -> str:
    from slate.state import status  # noqa: PLC0415

    try:
        st = status.compute(film_dir)
    except Exception as exc:  # noqa: BLE001 - the tracker must still render if a stage can't be read
        return f"_Status couldn't be computed: {exc}_"
    lines = [f"From `slate status`, {st['computed_at'][:16]}.", "",
             "| Stage | State | Notes | Evidence |", "|---|---|---|---|"]
    for s in st["stages"]:
        note = s["problems"][0] if s["problems"] else (s["reasons"][0] if s["reasons"] else s["summary"])
        evidence = ", ".join(f"`{e}`" for e in (s.get("evidence") or [])[:2])
        gates_here = ", ".join(f"{g} {v}" for g, v in list((s.get("gates") or {}).items())[:3])
        lines.append(f"| {s['label']} | {s['state']}{'' if s['ok'] else ' ⚠'} | {_util.md_cell(note)} | "
                     f"{_util.md_cell('; '.join(x for x in (evidence, gates_here) if x))} |")
    nxt = st.get("next") or {}
    lines += ["", f"**Next:** `{nxt.get('command')}`: {nxt.get('why')}" if nxt.get("command") else
              f"**Next:** {nxt.get('why', 'nothing')}"]
    return "\n".join(lines)


def render(film_dir: Path, *, with_status: bool = True) -> None:
    """Rewrite the managed blocks of GOAL.md and PRODUCTION.md from the brief, approvals and status."""
    from slate.state import film  # noqa: PLC0415

    film_dir = Path(film_dir)
    try:
        fj = film.load(film_dir)
    except UsageError:
        return
    data = load_brief(film_dir)
    goal_path, prod_path = film_dir / GOAL, film_dir / PRODUCTION
    with _util.state_lock(film_dir):
        goal = goal_path.read_text(encoding="utf-8") if goal_path.exists() else _goal_template(fj)
        goal = _util.replace_block(goal, "request", _request_block(data))
        goal = _util.replace_block(goal, "checklist", _asks_table(data, fj, settings=True))
        goal = _util.replace_block(goal, "messages", _messages_block(data))
        jsonio.write_text_atomic(goal_path, goal)
        prod = prod_path.read_text(encoding="utf-8") if prod_path.exists() else _production_template(fj)
        prod = _util.replace_block(prod, "asks", _asks_table(data, fj, settings=False))
        prod = _util.replace_block(prod, "directions", _directions_block(data))
        prod = _util.replace_block(prod, "questions", _questions_block(film_dir))
        prod = _util.replace_block(prod, "revisions", _revisions_block(film_dir))
        prod = _util.replace_block(prod, "approvals", _approvals_block(film_dir))
        if with_status:
            prod = _util.replace_block(prod, "status", _status_block(film_dir))
        jsonio.write_text_atomic(prod_path, prod)


def init(film_dir: Path, *, request: str | None = None, request_source: str | None = None,
         asks: list[tuple[str, str]] | None = None, request_asks: bool = True) -> None:
    """Write the first brief: the request (when there is one), its asks, and any extra asks (text, source).

    With request_asks=False the request is kept verbatim but the checklist is only the given asks (a derived film's
    request is written by Slate, and its checklist is the derivation's own list)."""
    data = {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY.items()}
    lang = _language(film_dir)
    if request and request.strip():
        data["request"] = {"text": request.strip("\n"), "source": request_source or "requester", "at": _util.now()}
        if request_asks:
            items = _util.split_items(request, lang)
            _new_asks(data, items, source=f"request ({request_source or 'requester'})", auto_split=len(items) > 1)
    for text, source in asks or []:
        _new_asks(data, [text], source=source, auto_split=False)
    save_brief(film_dir, data)
    render(film_dir, with_status=False)
