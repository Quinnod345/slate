"""Doctor's rows, the overall verdict, and how a report reads in a Terminal.

A row is a plain dict (docs/ARCHITECTURE.md §12):

    {"id": "tool.ffmpeg", "group": "tools", "status": "green|yellow|red", "summary": "one line", "fix": "… or None"}

plus, when useful, `details` (facts for machines), `install` (a free prerequisite `slate doctor --install` can set up,
asking first) and `info: true` (a reminder rather than a finding).

- **red**: something Slate needs is missing or broken, and films will fail without it;
- **yellow**: Slate works, but something should be fixed or finished (an optional tool, a pref only the person can
  turn on, a copy where a link belongs);
- **green**: checked, and right.

The overall status is green only when every row is green. Nothing unchecked is ever reported green: a check that
couldn't run says so and isn't green.
"""

from __future__ import annotations

import textwrap
from typing import Any, Iterable

STATUSES = ("green", "yellow", "red")
# Report order. Each group is also a value for `slate doctor --only`.
GROUPS = ("host", "tools", "engine", "keys", "aftereffects", "sound", "agents", "system", "fonts")
_RANK = {"green": 0, "yellow": 1, "red": 2}


def row(rid: str, status: str, summary: str, fix: str | None = None, *, group: str, details: dict | None = None,
        install: dict | None = None, info: bool = False) -> dict:
    if status not in STATUSES:
        raise ValueError(f"doctor status must be one of {STATUSES}, got {status!r}")
    if group not in GROUPS:
        raise ValueError(f"doctor group must be one of {GROUPS}, got {group!r}")
    out: dict[str, Any] = {"id": rid, "group": group, "status": status, "summary": summary, "fix": fix}
    if details:
        out["details"] = details
    if install and status != "green":
        out["install"] = install
    if info:
        out["info"] = True
    return out


def green(rid: str, summary: str, **kw) -> dict:
    return row(rid, "green", summary, kw.pop("fix", None), **kw)


def yellow(rid: str, summary: str, fix: str | None = None, **kw) -> dict:
    return row(rid, "yellow", summary, fix, **kw)


def red(rid: str, summary: str, fix: str | None = None, **kw) -> dict:
    return row(rid, "red", summary, fix, **kw)


def overall(rows: Iterable[dict]) -> str:
    """green only when there's at least one row and every row is green; otherwise the worst status."""
    items = list(rows)
    if not items:
        return "yellow"
    return max((r.get("status", "red") for r in items), key=lambda s: _RANK.get(s, 2))


def counts(rows: Iterable[dict]) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r.get("status", "red")] = out.get(r.get("status", "red"), 0) + 1
    return out


def exit_code(status: str, *, strict: bool = False) -> int:
    """0 green; 0 yellow (1 with --strict); 3 red (something Slate needs is missing)."""
    if status == "green":
        return 0
    if status == "yellow":
        return 1 if strict else 0
    return 3


# ---------------------------------------------------------------------------------------------------------------
# The Terminal view


def _wrap(text: str, width: int, indent: int) -> str:
    lines = textwrap.wrap(text, width=max(40, width - indent), break_long_words=False, break_on_hyphens=False)
    pad = " " * indent
    return ("\n" + pad).join(lines) if lines else ""


def format_report(rep: dict, *, width: int = 110) -> str:
    """The report as a person reads it: grouped rows, a fix under anything not green, and the verdict last."""
    rows = rep.get("checks") or []
    host = rep.get("host") or {}
    head = f"Slate doctor · context {rep.get('context', '?')}"
    if host.get("macos"):
        head += f" · macOS {host['macos']} ({host.get('arch', '?')})"
    lines = [head]
    if rep.get("replay"):
        lines.append("Replay mode (SLATE_FAKE_VENDORS=1): vendor answers are recorded stand-ins, not your real "
                     "accounts. Run with --live for the real (free) probes.")
    idw = max((len(r["id"]) for r in rows), default=10)
    idw = min(idw, 34)
    body_indent = 2 + 7 + idw + 2
    for group in GROUPS:
        members = [r for r in rows if r.get("group") == group]
        if not members:
            continue
        lines.append("")
        lines.append(group)
        for r in members:
            status = r["status"] if not r.get("info") else "info"
            summary = _wrap(r["summary"], width, body_indent)
            lines.append(f"  {status:<6} {r['id']:<{idw}}  {summary}")
            if r["status"] != "green" and r.get("fix"):
                lines.append(" " * body_indent + _wrap("fix: " + r["fix"], width, body_indent))
    c = rep.get("counts") or counts(rows)
    lines.append("")
    verdict = rep.get("status", overall(rows))
    lines.append(f"overall: {verdict} · {c.get('green', 0)} green, {c.get('yellow', 0)} yellow, {c.get('red', 0)} red")
    if verdict == "green":
        lines.append("Everything Slate needs is here.")
    else:
        todo = c.get("red", 0) + c.get("yellow", 0)
        lines.append(f"Not green yet: {todo} thing{'s' if todo != 1 else ''} to fix, each with its fix above.")
        if any(r.get("install") for r in rows):
            lines.append("`slate doctor --install` sets up the free ones, asking before each (run it in your own "
                         "Terminal).")
    if rep.get("saved"):
        lines.append(f"Saved to {rep['saved']}")
    return "\n".join(lines)
