"""What each agent has registered: MCP servers by name, read without changing anything.

- Claude Code: `~/.claude.json` (its user-scope `mcpServers`, plus each project's), read as a file. Doctor doesn't
  run `claude mcp list`, because that starts every registered server to health-check it; `claude mcp get slate`
  (agents.py) starts only Slate's own.
- Codex: `codex mcp list --json`, a read-only command; its `config.toml` when the command isn't there.
- OpenClaw: `openclaw mcp list --json`, a read-only command.

Registrations can carry secrets (a URL with a token in it, a header, an environment variable), so nothing from them
ever reaches a report: callers get server names and yes/no answers, and `mentions()` searches a server's launch command
without returning it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from slate.doctor.env import Env


def _parse_json_after_banner(text: str) -> Any:
    """Some CLIs print a banner line before their JSON."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    try:
        return json.loads(text[min(starts):])
    except ValueError:
        return None


def claude_config(env: Env) -> dict | None:
    data = env.read_json(env.home / ".claude.json")
    return data if isinstance(data, dict) else None


def claude_servers(env: Env) -> dict[str, dict] | None:
    """Claude Code's user-scope MCP servers ({name: spec}), or None when Claude Code has no settings here."""
    data = claude_config(env)
    if data is None:
        return None
    servers = data.get("mcpServers") or {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in servers.items()}


def claude_all_servers(env: Env) -> list[tuple[str, str, dict]]:
    """Every server Claude Code knows: [(scope, name, spec)], scope 'user' or 'project'."""
    data = claude_config(env) or {}
    out = [("user", str(k), v if isinstance(v, dict) else {}) for k, v in (data.get("mcpServers") or {}).items()]
    for proj in (data.get("projects") or {}).values():
        if isinstance(proj, dict):
            out += [("project", str(k), v if isinstance(v, dict) else {})
                    for k, v in (proj.get("mcpServers") or {}).items()]
    return out


def codex_servers(env: Env) -> dict[str, dict] | None:
    """Codex's MCP servers ({name: its transport and settings}), or None when Codex isn't here or can't be read."""
    codex = env.which("codex")
    if codex:
        ran = env.run([codex, "mcp", "list", "--json"], timeout=30)
        parsed = _parse_json_after_banner(ran.stdout) if ran.ok else None
        if isinstance(parsed, list):
            return {str(s.get("name")): s for s in parsed if isinstance(s, dict) and s.get("name")}
        if isinstance(parsed, dict):
            items = parsed.get("servers", parsed)
            if isinstance(items, dict):
                return {str(k): (v if isinstance(v, dict) else {}) for k, v in items.items()}
    home = env.getenv("CODEX_HOME")
    cfg = env.read_toml((Path(home).expanduser() if home else env.home / ".codex") / "config.toml")
    if cfg is None:
        return None if not codex else {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in (cfg.get("mcp_servers") or {}).items()}


def openclaw_servers(env: Env) -> dict[str, dict] | None:
    """OpenClaw's MCP servers ({name: spec}), or None when OpenClaw isn't here or didn't answer."""
    openclaw = env.which("openclaw")
    if not openclaw:
        return None
    ran = env.run([openclaw, "mcp", "list", "--json"], timeout=45)
    parsed = _parse_json_after_banner(ran.stdout) if ran.ok else None
    if isinstance(parsed, dict):
        items = parsed.get("servers", parsed) if isinstance(parsed.get("servers"), (dict, list)) else parsed
        if isinstance(items, dict):
            return {str(k): (v if isinstance(v, dict) else {}) for k, v in items.items()}
        parsed = items
    if isinstance(parsed, list):
        return {str(s.get("name")): s for s in parsed if isinstance(s, dict) and s.get("name")}
    return None


def mentions(spec: Any, *needles: str, include_urls: bool = False) -> bool:
    """Does a server's launch command (command, args, package) mention one of these? URLs are skipped unless asked:
    a hosted server reached by URL is a different thing from a local package, and URLs are never inspected lightly."""
    parts: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k.lower() in ("env", "headers", "http_headers", "env_http_headers", "bearer_token_env_var"):
                    continue
                if k.lower() == "url" and not include_urls:
                    continue
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str):
            parts.append(value.lower())

    walk(spec)
    return any(n.lower() in p for n in needles for p in parts)


def bridge_dir_from(spec: dict, package: str) -> Path | None:
    """The package folder a registration launches (…/<package>/dist/index.js -> …/<package>)."""
    args = spec.get("args") if isinstance(spec.get("args"), list) else []
    for arg in [spec.get("command"), *args]:
        if not isinstance(arg, str):
            continue
        p = Path(os.path.expanduser(arg))
        for parent in [p, *p.parents]:
            if parent.name == package:
                return parent
    return None
