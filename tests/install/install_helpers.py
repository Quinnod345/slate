"""Helpers for the install tests: temp homes, a small fake checkout, and fake agent CLIs.

The fake `claude`, `codex` and `openclaw` record every call in `<HOME>/client-calls.jsonl` and keep just enough state
(in the temp home) to answer the installer's questions the way the real ones would. No test ever runs a real agent
CLI or launchctl; tests/install/conftest.py enforces that.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def make_repo(root: Path, skills: tuple[str, ...] = ("slate", "slate-story"), name: str = "checkout") -> Path:
    """A minimal checkout: a skills/ folder with SKILL.md files (the installer needs nothing else)."""
    repo = root / name
    (repo / "src" / "slate").mkdir(parents=True, exist_ok=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'slate'\nversion = '0.0.0'\n")
    for skill in skills:
        add_skill(repo, skill)
    return repo


def add_skill(repo: Path, skill: str) -> Path:
    folder = repo / "skills" / skill
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: A test skill.\n---\n\nBody.\n")
    return folder


def seed_skill(root: Path, name: str, text: str = "someone else's skill") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {text}\n---\n{text}\n")
    return folder


def snapshot(folder: Path, skip: tuple[str, ...] = ()) -> dict:
    """Everything under a folder, without following links: {relative path: kind and content}."""
    out: dict[str, tuple] = {}
    if not folder.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        base = Path(dirpath)
        for name in list(dirnames) + filenames:
            path = base / name
            rel = str(path.relative_to(folder))
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            if path.is_symlink():
                out[rel] = ("link", os.readlink(path))
            elif path.is_file():
                out[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                out[rel] = ("dir",)
        # don't descend into symlinked folders
        dirnames[:] = [d for d in dirnames if not (base / d).is_symlink()]
    return out


FAKE_CLIENT = r'''#!{python}
"""A stand-in for {name}: records the call, keeps registration state in the temp HOME, and answers like the real CLI."""
import json, os, sys, pathlib

home = pathlib.Path(os.environ["HOME"])
name = "{name}"
args = sys.argv[1:]
with (home / "client-calls.jsonl").open("a") as fh:
    fh.write(json.dumps({{"client": name, "argv": args}}) + "\n")
if os.environ.get("FAKE_CLIENT_FAIL") == name:
    print(f"{{name}}: simulated failure", file=sys.stderr)
    sys.exit(1)


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


if name == "claude":
    cfg = home / ".claude.json"
    data = load(cfg, {{}})
    servers = data.setdefault("mcpServers", {{}})
    if args[:2] == ["mcp", "add"]:
        rest = args[2:]
        scope = rest[rest.index("--scope") + 1] if "--scope" in rest else "local"
        server = [a for a in rest[:rest.index("--")] if a not in ("--scope", scope)][0]
        cmd = rest[rest.index("--") + 1:]
        if server in servers:
            print(f"MCP server {{server}} already exists in user config", file=sys.stderr)
            sys.exit(1)
        servers[server] = {{"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {{}}}}
        cfg.write_text(json.dumps(data))
    elif args[:2] == ["mcp", "remove"]:
        server = args[-1]
        if server not in servers:
            sys.exit(1)
        del servers[server]
        cfg.write_text(json.dumps(data))
    sys.exit(0)

if name == "codex":
    cfg = home / ".codex" / "config.toml"
    state = home / ".codex" / "fake-state.json"
    data = load(state, {{}})
    if args[:2] == ["mcp", "add"]:
        server = args[2]
        cmd = args[args.index("--") + 1:]
        data[server] = {{"command": cmd[0], "args": cmd[1:]}}
    elif args[:2] == ["mcp", "remove"]:
        if args[2] not in data:
            sys.exit(1)
        del data[args[2]]
    elif args[:2] == ["mcp", "get"]:
        if args[2] not in data:
            sys.exit(1)
        print(json.dumps(data[args[2]]))
        sys.exit(0)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(data))
    lines = []
    for server, entry in data.items():
        lines.append(f"[mcp_servers.{{server}}]")
        lines.append(f"command = {{json.dumps(entry['command'])}}")
        lines.append(f"args = {{json.dumps(entry['args'])}}")
    cfg.write_text("\n".join(lines) + "\n")
    sys.exit(0)

if name == "openclaw":
    # Modelled on OpenClaw 2026.9.4, checked by hand against a throwaway state folder on 2026-09-30: `mcp add` saves
    # {{command, args, connectionTimeoutMs}} and refuses a name that exists; `mcp unset` exits 0 even when the name
    # isn't there; `mcp show NAME --json` prints the entry itself, or an error object and exit 1; `mcp list --json`
    # prints {{name: entry}}. `mcp set` still exists in 2026.9.4, but Slate registers with `add`, so this stand-in
    # refuses it: a test that passed through `set` would be testing the wrong command.
    state_file = home / ".openclaw-fake.json"
    data = load(state_file, {{"servers": {{}}, "agents": {{}}, "bundled": []}})

    def save():
        state_file.write_text(json.dumps(data))

    def opt_values(flag):
        return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]

    if args[:2] == ["mcp", "add"]:
        server = args[2]
        if server in data["servers"]:
            print(f'MCP server "{{server}}" already exists.', file=sys.stderr)
            sys.exit(1)
        if os.environ.get("FAKE_OPENCLAW_PROBE_FAILS"):
            print(f'MCP probe failed for "{{server}}": did not complete initialize within 5s', file=sys.stderr)
            sys.exit(1)
        entry = {{"command": opt_values("--command")[0], "args": opt_values("--arg")}}
        if opt_values("--connect-timeout"):
            entry["connectionTimeoutMs"] = int(float(opt_values("--connect-timeout")[0]) * 1000)
        data["servers"][server] = entry
        save()
        print(f'Saved MCP server "{{server}}" to {{home}}/.openclaw/openclaw.json.')
    elif args[:2] == ["mcp", "set"]:
        print("the fake openclaw refuses `mcp set`: Slate registers with `mcp add`", file=sys.stderr)
        sys.exit(2)
    elif args[:2] == ["mcp", "unset"]:
        if args[2] in data["servers"]:
            del data["servers"][args[2]]
            save()
            print(f'Removed MCP server "{{args[2]}}".')
        else:
            print(f'No MCP server named "{{args[2]}}".')
    elif args[:2] == ["mcp", "show"]:
        if args[2] not in data["servers"]:
            print(json.dumps({{"ok": False, "error": {{"type": "cli_error", "message": "No MCP server named"}}}}))
            sys.exit(1)
        print(json.dumps(data["servers"][args[2]]))
    elif args[:2] == ["mcp", "list"]:
        print(json.dumps(data["servers"]))
    elif args[:2] == ["mcp", "reload"]:
        pass
    elif args[:2] == ["config", "get"]:
        path = args[2]
        if path == "agents.entries":
            print(json.dumps(data["agents"]))
        elif path.startswith("agents.entries.") and path.endswith(".skills"):
            agent = path[len("agents.entries."):-len(".skills")]
            skills = data["agents"].get(agent, {{}}).get("skills")
            if skills is None:
                print("Config path not found", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(skills))
        else:
            sys.exit(1)
    elif args[:2] == ["config", "set"]:
        path, value = args[2], json.loads(args[3])
        agent = path[len("agents.entries."):-len(".skills")]
        entry = data["agents"].setdefault(agent, {{}})
        if "--expect-current-absent" in args and "skills" in entry:
            sys.exit(1)
        entry["skills"] = value
        save()
    elif args[:2] == ["skills", "list"]:
        agent = args[args.index("--agent") + 1]
        allow = data["agents"].get(agent, {{}}).get("skills")
        names = list(data.get("bundled", []))
        root = home / ".agents" / "skills"
        if root.is_dir():
            names += [p.name for p in root.iterdir() if (p / "SKILL.md").is_file()]
        rows = [{{"name": n, "source": "fake", "blockedByAgentFilter": allow is not None and n not in allow}}
                for n in sorted(set(names))]
        print(json.dumps({{"workspaceDir": str(home), "skills": rows}}))
    sys.exit(0)
'''


def install_fake_clients(bin_dir: Path, clients: tuple[str, ...] = ("claude", "codex", "openclaw")) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in clients:
        path = bin_dir / name
        path.write_text(FAKE_CLIENT.format(python=sys.executable, name=name))
        path.chmod(0o755)
    return bin_dir


def client_calls(home: Path) -> list[dict]:
    log = home / "client-calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def openclaw_state(home: Path) -> dict:
    path = home / ".openclaw-fake.json"
    return json.loads(path.read_text()) if path.exists() else {}


def set_openclaw_state(home: Path, **state) -> None:
    data = {"servers": {}, "agents": {}, "bundled": []}
    data.update(state)
    (home / ".openclaw-fake.json").write_text(json.dumps(data))


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """git for test setup (never run against the Slate working tree)."""
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=slate tests", "-c",
                           "user.email=tests@localhost", *args], capture_output=True, text=True, check=check,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def run_script(script: Path, *args: str, home: Path, env: dict | None = None, timeout: float = 120,
               stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run one of the repo's bash scripts with macOS's own bash (3.2), a temp HOME and no inherited agent markers."""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("CLAUDE", "CODEX_", "OPENCLAW")) and k not in ("SLATE_CONTEXT",)}
    base.update({"HOME": str(home), "BROWSER": "none", "SLATE_NONINTERACTIVE": "1"})
    base.update(env or {})
    return subprocess.run(["/bin/bash", str(script), *args], capture_output=True, text=True, env=base,
                          timeout=timeout, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)
