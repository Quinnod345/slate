"""Oneiro's strategist, dry-run on a pursuit snapshot.

Once Oneiro's self-want lands (it adds a video line to the strategist's capability list), a dry run of the strategist
on a pursuit's snapshot has to propose a Slate move. We don't edit Oneiro's engine and don't import it. The dry run
rebuilds the strategist's one model call from two files:

- the system prompt, read from `reasoning/agents.js` (the `system:` template inside `strategize`), so whatever the
  self-want adds is in it;
- the user message, composed from the pursuit's `pursuit.json` snapshot the way `strategize()` composes it. The parts
  that only the engine's database knows (recent agent work and actions, durable dispositions, charter grants) get the
  engine's own empty defaults.

The call goes through the same route the engine uses for its cloud steps: one answer-only `codex exec` (no shell, no
browser, no tools), configured in `configs.json` under harnesses. The answer is the engine's JSON:
`{"thought": …, "moves": [{"task", "why", "stream"}]}`.

The engine's wording that names its person (a heading, the evidence sources that count as the person speaking) comes
from the harness's `compose` block in configs.json, so this code carries no names.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

_STRATEGIZE_RE = re.compile(r"async\s+function\s+strategize\s*\(")

DEFAULT_COMPOSE = {
    "max_moves": 3,  # the engine asks for max(1, min(3, free agent slots)); a dry run assumes all three are free
    "said_heading": "What the person said:",
    "said_sources": r"stated by the person|observed by the person",
    "stated_sources": r"stated by the person",
    "said_ids": r"^person-",
}


def text(value, limit: int = 300) -> str:
    """The engine's `text()`: collapse whitespace, trim, cut to `limit` characters."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def extract_system_prompt(source: str) -> tuple[str | None, bool]:
    """The strategist's system prompt from agents.js, and whether it interpolates values (`${…}`) a dry run can't
    fill. (None, False) when the file no longer has the shape this reads."""
    m = _STRATEGIZE_RE.search(source)
    if not m:
        return None, False
    start = source.find("system:", m.end())
    if start < 0:
        return None, False
    tick = source.find("`", start)
    if tick < 0 or source[start:tick].strip() != "system:":
        return None, False
    i = tick + 1
    out: list[str] = []
    interpolates = False
    while i < len(source):
        c = source[i]
        if c == "\\" and i + 1 < len(source):
            out.append(source[i + 1])
            i += 2
            continue
        if c == "`":
            return "".join(out), interpolates
        if c == "$" and source[i + 1:i + 2] == "{":
            interpolates = True
        out.append(c)
        i += 1
    return None, False


def compose_user_message(snapshot: dict, *, now: _dt.datetime, compose: dict | None = None) -> str:
    """The strategist's user message for one pursuit, from its snapshot (mirrors strategize() in agents.js)."""
    c = {**DEFAULT_COMPOSE, **(compose or {})}
    s = snapshot
    want = s.get("want") or {}
    evidence = [e for e in s.get("evidence") or [] if isinstance(e, dict)]
    said_rx, stated_rx, ids_rx = (re.compile(c["said_sources"], re.I), re.compile(c["stated_sources"], re.I),
                                  re.compile(c["said_ids"]))
    said = [e for e in evidence if said_rx.search(str(e.get("source") or "")) or ids_rx.search(str(e.get("id") or ""))]
    known = [e for e in evidence if not stated_rx.search(str(e.get("source") or ""))]
    cont = s.get("continuity") or {}
    refused = [r for r in cont.get("lastRefused") or [] if isinstance(r, dict)]
    schedule = [x for x in cont.get("schedule") or [] if isinstance(x, dict)]
    streams: list[str] = []
    for x in (cont.get("board") or {}).get("streams") or []:
        name = x.get("name") if isinstance(x, dict) else None
        if name and name not in streams:
            streams.append(name)
    said_txt = "\n".join(f"- {text(e.get('observation'), 300)}" for e in said[-8:]) or "- (nothing recorded)"
    known_txt = "\n".join(f"- {text(e.get('observation'), 200)}" for e in known[-12:]) or "- (nothing yet)"
    schedule_txt = "\n".join(f"- {x.get('at')}: {text(x.get('task'), 200)}" for x in schedule) or "- (nothing)"
    refused_txt = "\n".join(f"- {text(r.get('task'), 200)} → {text(r.get('why'), 120)}" for r in refused)
    stamp = now.astimezone(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    chain = s.get("chain_id") or s.get("id")
    lines = [
        f"Now: {stamp}",
        f"N = {c['max_moves']}",
        f"Pursuit #{chain}: {text(want.get('description') or s.get('seed'), 600)}",
        f"Done when: {text(want.get('doneWhen') or s.get('doneWhen') or '', 400)}",
        "The engine may act on its own for: unknown",
        "",
        f"{c['said_heading']}\n{said_txt}",
        "",
        f"What is known:\n{known_txt}",
        "",
        f"Scheduled:\n{schedule_txt}",
        "",
        "Durable task dispositions (not a recent-run window):\n- (none)",
    ]
    if refused_txt:
        lines += ["", "Moves refused last cycle, before they started (do not propose these or close variants; propose "
                      f"genuinely different work):\n{refused_txt}"]
    lines += [
        "",
        "Recent agent work (newest first):\n- (none)",
        "",
        "Recent actions:\n- (none)",
        "",
        f"Workstreams so far: {', '.join(streams) or '(none yet — name them)'}",
        "",
        f"Last thought: {text(cont.get('lastThought') or '', 300) or '(none)'}",
    ]
    return "\n".join(lines)


def compose_prompt(agents_js: Path, snapshot_path: Path, *, compose: dict | None = None,
                   now: _dt.datetime | None = None) -> tuple[str, list[str]]:
    """The whole prompt the engine would send (the system prompt, a blank line, then `User: …`), and caveats."""
    caveats: list[str] = []
    system, interpolates = extract_system_prompt(Path(agents_js).read_text(encoding="utf-8"))
    if system is None:
        raise ValueError(f"{agents_js} no longer has a strategize() system prompt this dry run can read")
    if interpolates:
        caveats.append("the strategist's system prompt now interpolates values; the dry run sends it unfilled")
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    user = compose_user_message(snapshot, now=now or _dt.datetime.now(_dt.timezone.utc), compose=compose)
    caveats.append("recent agent work, actions, dispositions and charter grants live in the engine's database; the "
                   "dry run leaves them at the engine's empty defaults")
    return f"{system}\n\nUser: {user}", caveats
