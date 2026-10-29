"""Registering Slate's MCP server with each agent, in the form each one takes.

    claude mcp add --scope user slate -- <shim> mcp
    codex mcp add slate -- <shim> mcp
    openclaw mcp add slate --command <shim> --arg mcp --connect-timeout 30

The server always starts through the CLI shim (`~/.local/bin/slate`), never the checkout's Python directly, so an
update changes what every agent runs without registering anything again. Oneiro's research slices ignore user MCP
config altogether and use the CLI: the CLI is the contract, MCP is the convenience.

OpenClaw's form was checked against OpenClaw 2026.9.4 (the plan's `openclaw mcp set` predates `add`):

- `mcp add` starts the server once and saves it only if that probe answers. OpenClaw's default wait is 5 seconds, and
  `slate mcp` takes about 3 seconds to answer `initialize` on a quiet Mac (it loads every Slate module first), longer
  on a busy one: on 2026-09-30, with the Mac's load around 240, the default probe timed out and nothing was saved.
  `--connect-timeout 30` is saved with the entry (as `connectionTimeoutMs`), so the gateway waits that long too.
- `mcp add` refuses a name that already exists, so replacing an older Slate registration is `mcp unset` then `add`.

What's registered now is read from each client's own settings (`~/.claude.json`, Codex's `config.toml`) or, for
OpenClaw, its read-only `mcp show`. A server called `slate` that isn't Slate's is never replaced: it's reported, with
the command to remove it.

Sandbox homes and dry runs only show the commands. The agents' CLIs act on the logged-in account whatever HOME says,
so a test must never run them for real.
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path

from slate import procs
from slate.install import links
from slate.install import manifest as manifest_mod
from slate.install.layout import Layout
from slate.install.output import first_json

SERVER = "slate"
CLIENTS = ("claude", "codex", "openclaw")
TIMEOUT_S = 60
# How long OpenClaw waits for `slate mcp` to answer, at its `mcp add` probe and afterwards (see the docstring).
OPENCLAW_CONNECT_TIMEOUT_S = 30

# States of one registration.
OK, ADD, UPDATE, CONFLICT, NO_CLIENT, UNKNOWN = "ok", "add", "update", "conflict", "no-client", "unknown"


def spec(shim: Path) -> dict:
    return {"command": str(shim), "args": ["mcp"]}


def add_argv(client: str, shim: Path, binary: str | None = None) -> list[str]:
    exe = binary or client
    if client == "claude":
        return [exe, "mcp", "add", "--scope", "user", SERVER, "--", str(shim), "mcp"]
    if client == "codex":
        return [exe, "mcp", "add", SERVER, "--", str(shim), "mcp"]
    if client == "openclaw":
        return [exe, "mcp", "add", SERVER, "--command", str(shim), "--arg", "mcp",
                "--connect-timeout", str(OPENCLAW_CONNECT_TIMEOUT_S)]
    raise ValueError(client)


def remove_argv(client: str, binary: str | None = None) -> list[str]:
    exe = binary or client
    if client == "claude":
        return [exe, "mcp", "remove", "--scope", "user", SERVER]
    if client == "codex":
        return [exe, "mcp", "remove", SERVER]
    if client == "openclaw":
        return [exe, "mcp", "unset", SERVER]
    raise ValueError(client)


def after_argv(client: str, binary: str | None = None) -> list[str] | None:
    """What makes a running client notice the change, when there is such a thing."""
    if client == "openclaw":
        return [binary or client, "mcp", "reload"]
    return None


def search_path(lay: Layout) -> str:
    """Where to look for the agents' CLIs: the Slate PATH for the real home; the caller's own PATH in a sandbox."""
    return os.environ.get("PATH", "") if lay.sandbox else procs.child_path()


def find(client: str, path: str | None) -> str | None:
    return shutil.which(client, path=path) if path is not None else shutil.which(client)


def _codex_config(lay: Layout) -> Path:
    home = os.environ.get("CODEX_HOME") if not lay.sandbox else None
    return (Path(home).expanduser() if home else lay.home / ".codex") / "config.toml"


def _find_command(obj) -> dict | None:
    """The first dict in a JSON value that has a "command" (OpenClaw's `mcp show --json` shape isn't documented)."""
    if isinstance(obj, dict):
        if "command" in obj:
            return obj
        for value in obj.values():
            found = _find_command(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_command(value)
            if found:
                return found
    return None


def current(client: str, lay: Layout, *, binary: str | None = None, may_run: bool = True) -> dict | None:
    """What's registered as `slate` now: {"command", "args"}, None when nothing is, or {"unknown": why}."""
    try:
        if client == "claude":
            cfg = lay.home / ".claude.json"
            if not cfg.is_file():
                return None
            data = json.loads(cfg.read_text(encoding="utf-8"))
            entry = (data.get("mcpServers") or {}).get(SERVER)
            return {"command": entry.get("command"), "args": list(entry.get("args") or [])} if entry else None
        if client == "codex":
            cfg = _codex_config(lay)
            if not cfg.is_file():
                return None
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
            entry = (data.get("mcp_servers") or {}).get(SERVER)
            return {"command": entry.get("command"), "args": list(entry.get("args") or [])} if entry else None
        if client == "openclaw":
            if not binary or not may_run:
                return {"unknown": "OpenClaw's settings are read through its own CLI, which isn't run here"}
            proc = procs.run([binary, "mcp", "show", SERVER, "--json"], env={"HOME": str(lay.home)},
                             timeout=TIMEOUT_S)
            if proc.returncode != 0:
                return None
            found = _find_command(first_json(proc.stdout or ""))
            if not found:
                return None
            return {"command": found.get("command"), "args": list(found.get("args") or [])}
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return {"unknown": f"couldn't read {client}'s settings: {type(exc).__name__}"}
    return None


def _is_ours(entry: dict, lay: Layout) -> bool:
    command = entry.get("command") or ""
    if not command:
        return False
    path = Path(command).expanduser()
    if os.path.normpath(path) == os.path.normpath(lay.shim):
        return links.is_slate_shim(path, lay)
    if links._inside(path, lay.repo):  # an older registration straight into this checkout
        return True
    return links.is_slate_shim(path, lay)


def plan(clients: list[str], lay: Layout, *, path: str | None = None, may_run: bool = True) -> list[dict]:
    """One entry per client: its binary, what's registered now, and the commands that would make it right."""
    path = search_path(lay) if path is None else path
    out = []
    for client in clients:
        binary = find(client, path)
        entry: dict = {"client": client, "binary": binary, "server": SERVER, "spec": spec(lay.shim),
                       "add": add_argv(client, lay.shim), "remove": remove_argv(client)}
        if not binary:
            entry["state"] = NO_CLIENT
            entry["next"] = (f"{client} isn't on PATH here. Once it is, run: "
                             + " ".join(_quote(a) for a in add_argv(client, lay.shim)))
            out.append(entry)
            continue
        cur = current(client, lay, binary=binary, may_run=may_run)
        entry["current"] = cur
        if cur is None:
            entry["state"] = ADD
        elif "unknown" in cur:
            # Settings that couldn't be read: the add runs, and if the client already has the name it says so (a
            # failed step with the client's own words), which is safer than removing something unseen.
            entry["state"] = UNKNOWN
        elif cur.get("command") == str(lay.shim) and cur.get("args") == ["mcp"]:
            entry["state"] = OK
        elif _is_ours(cur, lay):
            entry["state"] = UPDATE
        else:
            entry["state"] = CONFLICT
            entry["next"] = (f"An MCP server called {SERVER!r} is already registered with {client} and it isn't "
                             f"Slate's ({cur.get('command')}). Remove it yourself with "
                             f"`{' '.join(remove_argv(client))}` if you're sure, then run `slate install` again.")
        out.append(entry)
    return out


def _quote(arg: str) -> str:
    import shlex  # noqa: PLC0415

    return shlex.quote(arg)


def show(argv: list[str]) -> str:
    return " ".join(_quote(a) for a in argv)


def _run(argv: list[str], lay: Layout) -> tuple[int, str]:
    proc = procs.run(argv, env={"HOME": str(lay.home)}, timeout=TIMEOUT_S)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    from slate.secrets import redact  # noqa: PLC0415

    return proc.returncode, redact(text)[-600:]


def apply(entries: list[dict], lay: Layout, *, run: bool) -> list[dict]:
    """Register where needed. With run=False (sandbox or dry run) the commands are only shown."""
    data = manifest_mod.load(lay) if run else None
    results = []
    for entry in entries:
        res = dict(entry)
        state = entry["state"]
        cmds: list[list[str]] = []
        if state == UPDATE:
            # Every client's add refuses a name it already has (OpenClaw's `mcp add` too), so an older Slate
            # registration comes off first. It's Slate's by construction (_is_ours), so nothing else is touched.
            cmds.append(remove_argv(entry["client"], entry["binary"]))
        if state in (ADD, UPDATE, UNKNOWN):
            cmds.append(add_argv(entry["client"], lay.shim, entry["binary"]))
        res["commands"] = [show([Path(c[0]).name, *c[1:]]) for c in cmds]
        if state in (OK, CONFLICT, NO_CLIENT) or not cmds:
            res["did"] = {"ok": "already registered", "conflict": "left alone", "no-client": "skipped"}.get(state, "none")
            results.append(res)
            continue
        if not run:
            res["did"] = "shown"
            results.append(res)
            continue
        failed = None
        for argv in cmds:
            code, text = _run(argv, lay)
            if code != 0:
                failed = f"`{show([Path(argv[0]).name, *argv[1:]])}` exited {code}: {text}"
                break
        if failed:
            res["did"] = "failed"
            res["error"] = failed
        else:
            res["did"] = "registered"
            manifest_mod.put_mcp(data, client=entry["client"], command=add_argv(entry["client"], lay.shim))
            after = after_argv(entry["client"], entry["binary"])
            if after:
                code, text = _run(after, lay)
                res["reload"] = "done" if code == 0 else f"not done ({code}): {text}"
        results.append(res)
    if run and data is not None:
        manifest_mod.save(lay, data)
    return results


def unregister(clients: list[str], lay: Layout, *, run: bool, path: str | None = None) -> list[dict]:
    """Remove Slate's registration from each client, but only where it's Slate's (it runs a Slate shim)."""
    path = search_path(lay) if path is None else path
    data = manifest_mod.load(lay) if run else None
    out = []
    for client in clients:
        binary = find(client, path)
        res: dict = {"client": client, "binary": binary, "command": show(remove_argv(client))}
        if not binary:
            res["did"] = "skipped"
            res["why"] = f"{client} isn't on PATH here"
            out.append(res)
            continue
        cur = current(client, lay, binary=binary, may_run=run)
        res["current"] = cur
        if cur is None:
            res["did"] = "nothing registered"
        elif "unknown" in cur:
            res["did"] = "left alone"
            res["why"] = f"ownership couldn't be confirmed: {cur['unknown']}"
        elif not _is_ours(cur, lay):
            res["did"] = "left alone"
            res["why"] = f"the {SERVER!r} server registered there isn't Slate's ({cur.get('command')})"
        elif not run:
            res["did"] = "shown"
        else:
            code, text = _run(remove_argv(client, binary), lay)
            res["did"] = "removed" if code == 0 else "failed"
            if code != 0:
                res["error"] = text
            elif data is not None:
                manifest_mod.drop_mcp(data, client)
        out.append(res)
    if run and data is not None:
        manifest_mod.save(lay, data)
    return out
