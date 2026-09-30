"""Preconditions: when a pending case becomes runnable.

A pending case says what has to be true before it can run, as read-only checks on this Mac:

- `file_exists` {path}: a file is there (a pursuit's snapshot, say);
- `file_matches` {path, pattern}: a file contains something;
- `strategist_prompt_mentions` {agents_js, pattern}: Oneiro's strategist prompt, read the way the dry run reads it,
  mentions something (the self-want adds Slate to it);
- `http_json` {url, fields}: a local service's JSON, for the record only (`info`). It's asked only when the caller
  opts in, never during tests, and only on this Mac (localhost).

`all` must hold for the case to run; `info` is reported beside it and never decides anything.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from slate.evals import strategist, suite

KINDS = {
    "file_exists": ("path",),
    "file_matches": ("path", "pattern"),
    "strategist_prompt_mentions": ("agents_js", "pattern"),
    "http_json": ("url",),
}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate(pre: dict) -> list[str]:
    problems = []
    for section in ("all", "info"):
        for i, item in enumerate(pre.get(section) or []):
            kind = item.get("check")
            if kind not in KINDS:
                problems.append(f"{section}/{i}: unknown precondition {kind!r}")
                continue
            problems += [f"{section}/{i}: {kind} needs {key!r}" for key in KINDS[kind] if key not in item]
            if "pattern" in item:
                try:
                    re.compile(item["pattern"])
                except re.error as exc:
                    problems.append(f"{section}/{i}: pattern isn't a valid regex ({exc})")
            if kind == "http_json":
                if section == "all":
                    problems.append(f"{section}/{i}: http_json is for the record only; put it under info")
                host = urlparse(str(item.get("url", ""))).hostname
                if host not in _LOCAL_HOSTS:
                    problems.append(f"{section}/{i}: http_json only asks services on this Mac (got {host!r})")
    return problems


def _path(item: dict, key: str, values: dict) -> Path:
    return Path(suite.expand(str(item[key]), values)).expanduser()


def _one(item: dict, values: dict, *, services: bool) -> dict:
    kind = item["check"]
    out: dict[str, Any] = {"check": kind, "why": item.get("why", "")}
    try:
        if kind == "file_exists":
            p = _path(item, "path", values)
            out.update(ok=p.is_file(), detail=f"{p} {'exists' if p.is_file() else 'is missing'}")
        elif kind == "file_matches":
            p = _path(item, "path", values)
            if not p.is_file():
                out.update(ok=False, detail=f"{p} is missing")
            else:
                m = re.search(item["pattern"], p.read_text(encoding="utf-8", errors="replace"))
                out.update(ok=bool(m), detail=f"{p} {'matches' if m else 'does not match'} {item['pattern']}")
        elif kind == "strategist_prompt_mentions":
            p = _path(item, "agents_js", values)
            if not p.is_file():
                out.update(ok=False, detail=f"{p} is missing")
            else:
                prompt, _ = strategist.extract_system_prompt(p.read_text(encoding="utf-8", errors="replace"))
                if prompt is None:
                    out.update(ok=False, detail=f"{p} has no strategize() system prompt this can read")
                else:
                    m = re.search(item["pattern"], prompt)
                    out.update(ok=bool(m), detail=(f"the strategist prompt mentions {m.group(0)!r}" if m else
                                                   f"the strategist prompt doesn't match {item['pattern']} yet"))
        elif kind == "http_json":
            if not services:
                out.update(ok=None, detail="not asked (pass --preconditions to ask local services)")
            else:
                out.update(_http_json(suite.expand(str(item["url"]), values), item.get("fields") or []))
    except Exception as exc:  # noqa: BLE001 - a precondition that can't be read is simply not met
        out.update(ok=False, detail=f"couldn't check: {type(exc).__name__}: {exc}")
    return out


def _dig(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def _http_json(url: str, fields: list[str]) -> dict:
    if urlparse(url).hostname not in _LOCAL_HOSTS:
        return {"ok": None, "detail": "only services on this Mac are asked"}
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - localhost only, read-only GET
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": None, "detail": f"{url} didn't answer ({exc})"}
    found = {f: _dig(data, f) for f in fields}
    return {"ok": None, "detail": ", ".join(f"{k}={v!r}" for k, v in found.items()) or "answered", "values": found}


def evaluate(pre: dict | None, values: dict, *, services: bool = False) -> dict:
    """{met, summary, checks, info}. A case without a precondition is always met."""
    if not pre:
        return {"met": True, "summary": "no precondition", "checks": [], "info": []}
    checks = [_one(item, values, services=False) for item in pre.get("all") or []]
    info = [_one(item, values, services=services) for item in pre.get("info") or []]
    met = all(c.get("ok") is True for c in checks)
    unmet = [c["detail"] for c in checks if c.get("ok") is not True]
    summary = "precondition met" if met else f"pending: {unmet[0]}" if unmet else "pending"
    return {"met": met, "summary": summary, "checks": checks, "info": info, "why": pre.get("summary", "")}
