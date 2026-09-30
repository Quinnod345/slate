"""`slate mcp` and `slate mcpb build`: Slate's MCP server and its desktop extension.

    slate mcp                  serve on stdin/stdout (agents start this themselves; it's what `claude mcp add` runs)
    slate mcp --list-tools     the tools a client would see, with their CLI equivalents
    slate mcp --check          start the server the way a client does, list its tools over stdio, and stop
    slate mcpb build           pack mcpb/ into dist/slate-<version>.mcpb for the Claude desktop app
    slate mcpb build --check   check the manifest against Slate itself; write nothing
    slate mcpb build --sync    rewrite the manifest's version and tool list from Slate itself

Registering the server with each agent (claude mcp add, codex mcp add, openclaw mcp add) is install.sh's job; the
plugin in plugins/slate carries the same server for other people.
"""

from __future__ import annotations

import argparse
import json
import sys

from slate import __version__, cli, log, procs

PROTOCOL = "2025-06-18"  # an initialize-handshake revision every current client speaks


def _probe_lines() -> str:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                    "clientInfo": {"name": "slate-mcp-check", "version": __version__}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
    ]
    return "".join(json.dumps(m) + "\n" for m in msgs)


def stdio_check(timeout: float = 60.0) -> dict:
    """Start `slate mcp` as a client would (a child process on stdio), list its tools, call `status` (read-only),
    and report what came back.

    Every request is written at once and stdin closes straight after, so this also proves the server answers what
    it has read before it hangs up. The child gets Slate's usual environment (BROWSER=none, no shell) through
    slate.procs and exits when its stdin closes, so nothing is left running.
    """
    from slate.mcp.server import TOOL_NAMES  # noqa: PLC0415

    proc = procs.run([sys.executable, "-m", "slate", "mcp"], input=_probe_lines(), timeout=timeout)
    replies = {}
    for line in (proc.stdout or "").splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and "id" in msg:
            replies[msg["id"]] = msg
    init = replies.get(1, {}).get("result") or {}
    listed = [t.get("name") for t in ((replies.get(2, {}).get("result") or {}).get("tools") or [])]
    missing = [n for n in TOOL_NAMES if n not in listed]
    called = replies.get(3, {}).get("result") or {}
    answered = bool(called.get("content"))
    return {"ok": bool(init) and not missing and answered, "server": init.get("serverInfo"),
            "protocol": init.get("protocolVersion"), "tools": listed, "missing": missing,
            "status_answered": answered, "status_is_error": bool(called.get("isError")),
            "exit_code": proc.returncode, "stderr": (proc.stderr or "").strip()[-2000:]}


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.list_tools:
        from slate.mcp import server  # noqa: PLC0415

        tools = server.describe()
        lines = [f"Slate's MCP server: {len(tools)} tools (each maps to a `slate` command)."]
        width = max(len(t["name"]) for t in tools)
        for t in tools:
            kind = "read-only" if t["read_only"] else ("changes things" if not t["destructive"] else "overwrites")
            lines.append(f"  {t['name']:<{width}}  {kind:<15} {t['title']}")
        log.result({"tools": tools}, "\n".join(lines))
        return 0
    if args.check:
        report = stdio_check()
        human = (f"slate mcp answered over stdio ({report['protocol']}) with {len(report['tools'])} tools "
                 f"({', '.join(report['tools'])}), and answered a status call." if report["ok"] else
                 "slate mcp didn't answer as a client expects.\n"
                 + (f"  missing tools: {', '.join(report['missing'])}\n" if report["missing"] else "")
                 + ("" if report["status_answered"] else "  the status call got no answer\n")
                 + (f"  stderr: {report['stderr']}" if report["stderr"] else ""))
        log.result(report, human)
        return 0 if report["ok"] else 1
    from slate.mcp import server  # noqa: PLC0415

    server.serve()
    return 0


def cmd_mcpb_build(args: argparse.Namespace) -> int:
    from slate.mcp import mcpb  # noqa: PLC0415

    if args.sync:
        report = mcpb.sync()
        log.result(report, (f"Updated {report['manifest']}: version {report['version']}, "
                            f"{len(report['tools'])} tools." if report["changed"] else
                            f"{report['manifest']} already matches Slate {report['version']}."))
        return 0
    if args.check:
        problems = mcpb.validate()
        report = {"ok": not problems, "problems": problems, "manifest": str(mcpb.manifest_path())}
        human = ("The desktop extension's manifest matches Slate." if not problems else
                 "The desktop extension's manifest has drifted:\n  " + "\n  ".join(problems)
                 + "\nnext: slate mcpb build --sync")
        log.result(report, human)
        return 0 if not problems else 1
    dry = bool(cli.flag(args, "dry_run", False))
    report = mcpb.build(args.out, write=not dry)
    human = [f"{'Would write' if dry else 'Wrote'} {report['path']} ({report['bytes']:,} bytes, "
             f"sha256 {report['sha256'][:12]}…).",
             "Install it in the Claude desktop app: double-click it, or Settings, Extensions, then drag it in.",
             "It starts the Slate installed on this Mac, so install Slate first (see README.md)."]
    log.result(report, "\n".join(human))
    return 0


EPILOG = "Exit codes: 0 ok, 1 refused or a check failed, 2 usage, 3 missing prerequisite."


def register(sub) -> None:
    p = cli.add_command(sub, "mcp", cmd_mcp, "serve Slate's MCP server on stdio (agents start it themselves)",
                        description=__doc__, epilog=EPILOG)
    p.add_argument("--list-tools", dest="list_tools", action="store_true",
                   help="print the tools a client sees, and exit")
    p.add_argument("--check", action="store_true",
                   help="start the server over stdio as a client would, list its tools, and stop")
    group = cli.add_group(sub, "mcpb", "Slate's desktop extension for the Claude app: build and check it",
                          description="The desktop extension lets a regular Claude chat use the Slate installed on "
                                      "this Mac: plan, write and voice a film, and start renders as background jobs.")
    p = cli.add_command(group, "build", cmd_mcpb_build, "pack mcpb/ into dist/slate-<version>.mcpb",
                        description=__doc__, epilog=EPILOG)
    p.add_argument("--check", action="store_true",
                   help="check the manifest against Slate's version and tools; write nothing (exit 1 on drift)")
    p.add_argument("--sync", action="store_true", help="rewrite the manifest's version and tool list from Slate")
    p.add_argument("--out", metavar="DIR", help="where to write the .mcpb (default: dist/ in this checkout)")


__all__ = ["register", "stdio_check"]
