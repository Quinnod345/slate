"""Every agent on this Mac: Slate's skills listed once each, its MCP server registered, its CLI on PATH.

Where each agent looks (checked against the real configs on 2026-09-30):

- Claude Code reads `~/.claude/skills` (symlinks followed). It doesn't read `~/.agents/skills`.
- Codex reads `~/.agents/skills`, and both of its binaries also read `~/.codex/skills`. A skill in both is listed twice,
  so Slate's skills belong in `~/.agents/skills` only.
- OpenClaw (its default state folder) reads `~/.agents/skills` too, and its gateway's PATH lacks `~/.local/bin`, so
  the CLI is also linked into `/opt/homebrew/bin`.

Doctor only reads. It never edits another agent's settings, including `~/.codex/AGENTS.md`: when that file still tells
Codex that mac-cua's browser tools on Dia are fine, doctor flags it for its owner to fix.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from slate.doctor import clients
from slate.doctor import rows as R
from slate.doctor.common import Ctx, tilde
from slate.doctor.env import Env

G = "agents"
MCP_NAME = "slate"
SHIM_MARKER = "slate-shim v1"  # the line Slate's installer writes into its CLI shims
# The ten skills in the plan (§1), used to name what's missing when this checkout's skills/ is still empty.
PLANNED_SKILLS = ("slate", "slate-story", "slate-motion", "slate-build", "slate-voice", "slate-sound",
                  "slate-generate", "slate-finish", "slate-qa", "slate-brand")
ELEVENLABS_LOCAL = ("elevenlabs-mcp", "elevenlabs_mcp")
CONNECT = "slate connect claude"


def slate_skills(env: Env) -> tuple[list[str], bool]:
    """Slate's skills in this checkout (each folder with a SKILL.md), and whether there are any yet."""
    src = env.repo() / "skills"
    names = [n for n in env.listdir(src)
             if not n.startswith((".", "_")) and env.is_file(src / n / "SKILL.md")]
    return (names, True) if names else (list(PLANNED_SKILLS), False)


def _entry_state(env: Env, path: Path, name: str) -> str:
    """'link' (into this checkout: its skills/ or the built copies under plugins/), 'elsewhere' (a link to another
    place), 'copy' (a real folder) or 'missing'."""
    if env.is_symlink(path):
        target = os.path.normpath(env.realpath(path))
        repo = os.path.normpath(env.realpath(env.repo()))
        inside = target == repo or target.startswith(repo + os.sep)
        return "link" if inside and os.path.basename(target) == name else "elsewhere"
    if env.exists(path):
        return "copy"
    return "missing"


def _present(env: Env, path: Path) -> bool:
    return env.is_symlink(path) or env.exists(path)


def _no_skills_row(rid: str, agent: str) -> dict:
    return R.red(rid, f"This checkout has no skills yet (its skills/ folder is empty), so {agent} has nothing of "
                      "Slate's to find.", f"Update Slate (git pull), then run `{CONNECT}`.", group=G)


def check_skills(env: Env, ctx: Ctx) -> list[dict]:
    names, built = slate_skills(env)
    return [_claude_skills(env, names, built), _codex_skills(env, names, built), _openclaw_skills(env, names, built)]


def _claude_skills(env: Env, names: list[str], built: bool) -> dict:
    rid = "agents.skills.claude-code"
    if not (env.which("claude") or env.is_dir(env.home / ".claude")):
        return R.green(rid, "Claude Code isn't on this Mac, so there's nothing to link.", group=G)
    if not built:
        return _no_skills_row(rid, "Claude Code")
    root = env.home / ".claude" / "skills"
    states = {n: _entry_state(env, root / n, n) for n in names}
    missing = [n for n, s in states.items() if s == "missing"]
    copies = [n for n, s in states.items() if s in ("copy", "elsewhere")]
    plugin = _claude_plugin(env)
    details = {"root": str(root), "states": states, "plugin": plugin}
    if missing:
        return R.red(rid, f"{len(missing)} of Slate's {len(names)} skills aren't in ~/.claude/skills: "
                          f"{', '.join(missing)}.", f"`{CONNECT}` links them from this checkout.", group=G,
                     details=details)
    if plugin:
        return R.yellow(rid, f"Claude Code lists Slate's skills twice: as the {plugin} plugin and as links in "
                             "~/.claude/skills.",
                        "Keep the links (one checkout, no copies) and remove the plugin with /plugin in Claude Code; "
                        "the plugin is for other people's Macs.", group=G, details=details)
    if copies:
        return R.yellow(rid, f"{', '.join(copies)} in ~/.claude/skills {'is' if len(copies) == 1 else 'are'} a copy or a "
                             "link to another checkout, which drifts from this one.",
                        f"`{CONNECT}` replaces them with links (it moves the old ones aside, never deletes them).",
                        group=G, details=details)
    return R.green(rid, f"All {len(names)} of Slate's skills are in ~/.claude/skills once each, linked to this checkout.",
                   group=G, details=details)


def _claude_plugin(env: Env) -> str | None:
    data = env.read_json(env.home / ".claude" / "plugins" / "installed_plugins.json")
    plugins = (data or {}).get("plugins", data) if isinstance(data, dict) else None
    if isinstance(plugins, dict):
        for key in plugins:
            if str(key).split("@", 1)[0] == "slate":
                return str(key)
    return None


def _codex_skills(env: Env, names: list[str], built: bool) -> dict:
    rid = "agents.skills.codex"
    codex_home = Path(env.getenv("CODEX_HOME") or env.home / ".codex")
    if not (env.which("codex") or env.is_dir(codex_home)):
        return R.green(rid, "Codex isn't on this Mac, so there's nothing to link.", group=G)
    if not built:
        return _no_skills_row(rid, "Codex")
    agents_root = env.home / ".agents" / "skills"
    codex_root = codex_home / "skills"
    in_agents = {n for n in names if _present(env, agents_root / n)}
    in_codex = {n for n in names if _present(env, codex_root / n)}
    twice = sorted(in_agents & in_codex)
    only_codex = sorted(in_codex - in_agents)
    missing = sorted(set(names) - in_agents - in_codex)
    copies = sorted(n for n in in_agents if _entry_state(env, agents_root / n, n) in ("copy", "elsewhere"))
    theirs = sorted((set(env.listdir(agents_root)) & set(env.listdir(codex_root))) - set(names) - {".DS_Store"})
    details = {"agents_root": str(agents_root), "codex_root": str(codex_root), "listed_twice": twice,
               "only_in_codex_skills": only_codex, "missing": missing, "copies": copies,
               "other_skills_listed_twice": theirs}
    others = (f" Separately, {len(theirs)} of your own skills are listed twice in Codex ({', '.join(theirs)}); "
              "that's your config.") if theirs else ""
    if twice:
        return R.red(rid, f"Codex lists {len(twice)} of Slate's skills twice, because both Codex binaries read "
                          f"~/.agents/skills and {tilde(env, codex_root)}: {', '.join(twice)}.{others}",
                     f"Delete Slate's entries from {tilde(env, codex_root)} ({', '.join(twice)}); the "
                     "~/.agents/skills links stay. Slate itself never links into ~/.codex/skills.", group=G,
                     details=details)
    if missing:
        return R.red(rid, f"{len(missing)} of Slate's {len(names)} skills aren't in ~/.agents/skills, where Codex looks: "
                          f"{', '.join(missing)}.{others}", f"`{CONNECT}` links them (Codex and OpenClaw share that "
                                                           "folder).", group=G, details=details)
    if only_codex:
        return R.yellow(rid, f"{', '.join(only_codex)} {'is' if len(only_codex) == 1 else 'are'} only in "
                             f"{tilde(env, codex_root)}, the older folder. Slate's skills belong in ~/.agents/skills "
                             f"(OpenClaw reads it too).{others}",
                        f"`{CONNECT}` links them into ~/.agents/skills; then delete the {tilde(env, codex_root)} "
                        "entries so Codex doesn't list them twice.", group=G, details=details)
    if copies:
        return R.yellow(rid, f"{', '.join(copies)} in ~/.agents/skills {'is' if len(copies) == 1 else 'are'} a copy or a "
                             f"link to another checkout, which drifts from this one.{others}",
                        f"`{CONNECT}` replaces them with links.", group=G, details=details)
    return R.green(rid, f"All {len(names)} of Slate's skills are in ~/.agents/skills once each, and none in "
                        f"{tilde(env, codex_root)}, so each Codex binary lists them once.{others}", group=G,
                   details=details)


def _openclaw_skills(env: Env, names: list[str], built: bool) -> dict:
    rid = "agents.skills.openclaw"
    if not (env.which("openclaw") or env.is_dir(env.home / ".openclaw")):
        return R.green(rid, "OpenClaw isn't on this Mac, so there's nothing to link.", group=G)
    if not built:
        return _no_skills_row(rid, "OpenClaw")
    state = env.getenv("OPENCLAW_STATE_DIR")
    default_state = env.home / ".openclaw"
    agents_root = env.home / ".agents" / "skills"
    managed = default_state / "skills"
    missing = sorted(n for n in names if not _present(env, agents_root / n))
    in_managed = sorted(n for n in names if _present(env, managed / n))
    details = {"agents_root": str(agents_root), "missing": missing, "in_managed_root": in_managed,
               "state_dir": state}
    if state and os.path.normpath(os.path.expanduser(state)) != os.path.normpath(default_state):
        return R.yellow(rid, f"OpenClaw's state folder here is {state}, not the default, and then it doesn't read "
                             "~/.agents/skills, so it won't see Slate's skills.",
                        "Run the gateway with its default state folder, or link Slate's skills into its workspace's "
                        "skills folder.", group=G, details=details)
    if missing:
        return R.red(rid, f"{len(missing)} of Slate's {len(names)} skills aren't in ~/.agents/skills, where OpenClaw "
                          f"looks: {', '.join(missing)}.", f"`{CONNECT}` links them.", group=G, details=details)
    if in_managed:
        return R.yellow(rid, f"There's a second copy of {', '.join(in_managed)} in ~/.openclaw/skills. OpenClaw shows "
                             "one, but a copy drifts from this checkout.",
                        "Delete the ~/.openclaw/skills copies; the ~/.agents/skills links stay.", group=G,
                        details=details)
    return R.green(rid, f"All {len(names)} of Slate's skills are in ~/.agents/skills, where OpenClaw looks, once each.",
                   group=G, details=details)


# ---------------------------------------------------------------------------------------------------------------
# MCP


def check_mcp(env: Env, ctx: Ctx) -> list[dict]:
    return [_mcp_server(env), _mcp_claude(env), _mcp_codex(env), _mcp_openclaw(env), _elevenlabs_local(env)]


def _mcp_server(env: Env) -> dict:
    """Does Slate's own MCP server start on stdio and answer? (slate.mcp.cli.stdio_check: tools listed, status called.)"""
    from slate.secrets import redact  # noqa: PLC0415

    rid = "agents.mcp.server"
    try:
        res = env.mcp_stdio_check()
    except ImportError as exc:
        return R.yellow(rid, f"Slate's MCP server isn't in this checkout ({exc}), so no agent can connect to it.",
                        "Update Slate (git pull).", group=G)
    except Exception as exc:  # noqa: BLE001
        return R.red(rid, f"Slate's MCP server couldn't be started for a check: {redact(getattr(exc, 'message', str(exc)))[:200]}",
                     "Run `slate mcp --check` to see why.", group=G)
    details = {k: res.get(k) for k in ("protocol", "missing", "status_answered", "exit_code")}
    details["tools"] = len(res.get("tools") or [])
    if res.get("ok"):
        return R.green(rid, f"Slate's MCP server starts on stdio and answers: {details['tools']} tools listed, and its "
                            "read-only status call came back.", group=G, details=details)
    why = (f"these tools are missing: {', '.join(res['missing'])}" if res.get("missing") else
           "its status call didn't answer" if not res.get("status_answered") else "it didn't initialise")
    # The server's own error output isn't quoted here: it's another process's text, and a report never carries that.
    return R.red(rid, f"Slate's MCP server starts, but {why}.",
                 "Run `slate mcp --check` to see its error in full; an update (git pull) usually fixes a broken server.",
                 group=G, details=details)


def _mcp_claude(env: Env) -> dict:
    rid = "agents.mcp.claude-code"
    claude = env.which("claude")
    if not claude:
        return R.green(rid, "Claude Code isn't on this Mac, so there's no MCP registration to check.", group=G)
    # Starts only Slate's own server for its health check. Nothing it prints is passed on except two short fields.
    ran = env.run([claude, "mcp", "get", MCP_NAME], timeout=60)
    text = ran.text
    if f'No MCP server named "{MCP_NAME}"' in text or (not ran.ok and "No MCP server" in text):
        return R.yellow(rid, "Slate's MCP server isn't registered with Claude Code. The CLI does everything without it; "
                             "the MCP server is a convenience.",
                        f"`{CONNECT}` registers it (claude mcp add --scope user slate …).", group=G)
    if not ran.ok:
        return R.yellow(rid, f"`claude mcp get {MCP_NAME}` failed (exit {ran.returncode}), so the registration couldn't "
                             "be checked.", "Run it yourself to see why; `slate connect claude` registers it again.",
                        group=G)
    status = _field(text, "Status")
    scope = _field(text, "Scope")
    details = {"scope": scope, "status": status}
    low = (status or "").lower()
    if status and ("fail" in low or "✗" in status or "error" in low):
        return R.red(rid, f"Slate's MCP server is registered with Claude Code ({scope or 'scope unknown'}) but doesn't "
                          "start.", "Run `slate mcp --help` to see why, then `slate connect claude` again.", group=G,
                     details=details)
    if status and ("connected" in low or "✓" in status):
        return R.green(rid, f"Slate's MCP server is registered with Claude Code ({scope or 'scope unknown'}) and "
                            "connects.", group=G, details=details)
    return R.green(rid, f"Slate's MCP server is registered with Claude Code ({scope or 'scope unknown'}).", group=G,
                   details=details)


def _field(text: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return None
    value = m.group(1)
    return None if "://" in value else value[:80]  # never pass a URL along


def _mcp_codex(env: Env) -> dict:
    rid = "agents.mcp.codex"
    if not env.which("codex"):
        return R.green(rid, "Codex isn't on this Mac, so there's no MCP registration to check.", group=G)
    servers = clients.codex_servers(env)
    if servers is None:
        return R.yellow(rid, "Couldn't read Codex's MCP servers (`codex mcp list --json` didn't answer).",
                        "Run `codex mcp list` yourself; `slate connect claude` registers Slate there too.", group=G)
    entry = servers.get(MCP_NAME)
    if entry is None:
        return R.yellow(rid, "Slate's MCP server isn't registered with Codex. Codex (and Oneiro's research slices, which "
                             "ignore MCP settings anyway) use the CLI, so this is a convenience.",
                        f"`{CONNECT}` registers it (codex mcp add slate …).", group=G)
    if entry.get("enabled") is False:
        return R.yellow(rid, "Slate's MCP server is registered with Codex but switched off.",
                        "Turn it back on in ~/.codex/config.toml (enabled = true); that's your config.", group=G)
    return R.green(rid, "Slate's MCP server is registered with Codex.", group=G)


def _mcp_openclaw(env: Env) -> dict:
    rid = "agents.mcp.openclaw"
    if not env.which("openclaw"):
        return R.green(rid, "OpenClaw isn't on this Mac, so there's no MCP registration to check.", group=G)
    servers = clients.openclaw_servers(env)
    if servers is None:
        return R.yellow(rid, "Couldn't read OpenClaw's MCP servers (`openclaw mcp list --json` didn't answer).",
                        "Run `openclaw mcp list` yourself; `slate connect claude` registers Slate there too.", group=G)
    if MCP_NAME not in servers:
        return R.yellow(rid, "Slate's MCP server isn't registered with OpenClaw. The gateway can use the CLI without it.",
                        f"`{CONNECT}` registers it (openclaw mcp set slate …).", group=G)
    return R.green(rid, "Slate's MCP server is registered with OpenClaw.", group=G)


def _elevenlabs_local(env: Env) -> dict:
    """ElevenLabs' old local MCP package is archived and carries an outbound-call tool: flag it wherever it's set up."""
    rid = "agents.mcp.elevenlabs-local"
    found: list[str] = []
    for scope, name, spec in clients.claude_all_servers(env):
        if clients.mentions(spec, *ELEVENLABS_LOCAL):
            found.append(f"Claude Code ({scope} scope, as {name!r})")
    for label, servers in (("Codex", clients.codex_servers(env) if env.which("codex") else None),
                           ("OpenClaw", clients.openclaw_servers(env))):
        for name, spec in (servers or {}).items():
            if clients.mentions(spec, *ELEVENLABS_LOCAL):
                found.append(f"{label} (as {name!r})")
    if found:
        return R.yellow(rid, f"ElevenLabs' local MCP server is registered in {', '.join(found)}. It's archived and "
                             "includes a tool that places outbound phone calls; Slate uses its own ElevenLabs client, "
                             "and chat uses ElevenLabs' hosted connector.",
                        "Remove it with that agent's own command (claude mcp remove / codex mcp remove / openclaw mcp "
                        "unset). It's your config, so Slate never edits it.", group=G, details={"found": found})
    return R.green(rid, "ElevenLabs' archived local MCP server isn't registered with any agent.", group=G)


# ---------------------------------------------------------------------------------------------------------------
# The CLI on PATH


def _cli_state(env: Env, path: Path) -> str:
    """'ours' (a link into this checkout, or Slate's shim for it), 'other', 'broken' or 'missing'."""
    if not env.exists(path):
        return "missing"
    if env.is_symlink(path) and not env.exists(env.realpath(path)):
        return "broken"
    repo = os.path.normpath(env.realpath(env.repo()))
    real = os.path.normpath(env.realpath(path))
    if real == repo or real.startswith(repo + os.sep):
        return "ours"
    head = env.read_text(path, 2048) or ""
    if SHIM_MARKER in head or str(env.repo()) in head:
        return "ours" if (str(env.repo()) in head or repo in head) else "other"
    return "other"


def check_cli_path(env: Env, ctx: Ctx) -> list[dict]:
    return [_cli_path(env)]


def _cli_path(env: Env) -> dict:
    rid = "agents.cli-path"
    local = env.home / ".local" / "bin" / "slate"
    brew = Path("/opt/homebrew/bin/slate")
    states = {"~/.local/bin/slate": _cli_state(env, local), "/opt/homebrew/bin/slate": _cli_state(env, brew)}
    gateway = bool(env.which("openclaw"))
    details = {"states": states, "openclaw": gateway}
    via = f"{env.repo()}/.venv/bin/slate"
    if states["~/.local/bin/slate"] in ("missing", "broken"):
        what = ("is a broken link in ~/.local/bin" if states["~/.local/bin/slate"] == "broken" else
                "isn't in ~/.local/bin")
        return R.red(rid, f"The slate command {what}, where Claude Code, Codex and Oneiro's research slices look for "
                          "it.",
                     f"Run `{via} connect claude` (it puts the CLI on PATH, with the skills and the MCP server).",
                     group=G, details=details)
    if gateway and states["/opt/homebrew/bin/slate"] in ("missing", "broken"):
        return R.red(rid, "The slate command isn't in /opt/homebrew/bin, and the OpenClaw gateway's PATH doesn't include "
                          "~/.local/bin, so Oneiro can't run Slate.", f"`{CONNECT}` links it there too.", group=G,
                     details=details)
    others = [k for k, v in states.items() if v == "other"]
    if others:
        return R.yellow(rid, f"{', '.join(others)} runs a different Slate checkout than this one ({env.repo()}).",
                        f"`{CONNECT}` points it at this checkout (the old one is moved aside, not deleted).", group=G,
                        details=details)
    where = "~/.local/bin and /opt/homebrew/bin" if states["/opt/homebrew/bin/slate"] == "ours" else "~/.local/bin"
    return R.green(rid, f"The slate command is on PATH in {where}, from this checkout.", group=G, details=details)


# ---------------------------------------------------------------------------------------------------------------
# ~/.codex/AGENTS.md and Dia

_PROHIBIT = re.compile(r"\b(never|forbidden|off-limits|off limits|not allowed|don'?t|do not|must not|prohibited|"
                       r"banned)\b", re.IGNORECASE)
_BROWSER_TOOL = re.compile(r"browser_|\bCDP\b|mac-cua", re.IGNORECASE)


def dia_permissions(text: str) -> list[int]:
    """Line numbers where the text says mac-cua's browser tools may drive Dia (and doesn't forbid it)."""
    hits: list[int] = []
    lines = text.splitlines()
    para: list[tuple[int, str]] = []

    def flush() -> None:
        if not para:
            return
        block = " ".join(t for _, t in para)
        if re.search(r"\bDia\b", block) and _BROWSER_TOOL.search(block):
            for sentence in re.split(r"(?<=[.!?])\s+", block):
                if re.search(r"\bDia\b", sentence) and not _PROHIBIT.search(sentence):
                    hits.extend(n for n, t in para if re.search(r"\bDia\b", t) and n not in hits)
                    break
        para.clear()

    for number, line in enumerate(lines, 1):
        if line.strip():
            para.append((number, line))
        else:
            flush()
    flush()
    return sorted(hits)


def check_codex_dia(env: Env, ctx: Ctx) -> list[dict]:
    rid = "agents.codex-dia"
    codex_home = Path(env.getenv("CODEX_HOME") or env.home / ".codex")
    if not (env.which("codex") or env.is_dir(codex_home)):
        return [R.green(rid, "Codex isn't on this Mac, so no Codex instructions can send it to Dia.", group=G)]
    target = None
    for name in ("AGENTS.override.md", "AGENTS.md"):  # Codex reads the first non-empty one
        text = env.read_text(codex_home / name)
        if text and text.strip():
            target = (codex_home / name, text)
            break
    if target is None:
        return [R.green(rid, f"There's no {tilde(env, codex_home / 'AGENTS.md')}, so nothing tells Codex to drive Dia.",
                        group=G)]
    path, text = target
    hits = dia_permissions(text)
    label = tilde(env, path)
    if hits:
        where = f"line {hits[0]}" if len(hits) == 1 else f"lines {', '.join(map(str, hits))}"
        return [R.yellow(rid, f"{label} ({where}) still tells Codex that mac-cua's browser tools on Dia are fine, which "
                              "contradicts the Aside-only rule: Dia is off-limits to automation.",
                         f"That file is yours, so Slate only flags it: change that paragraph to say web work goes "
                         f"through Aside, and that mac-cua's browser tools are never used on Dia. Slate never edits "
                         f"{label}.", group=G, details={"file": str(path), "lines": hits})]
    return [R.green(rid, f"{label} doesn't send Codex to Dia.", group=G, details={"file": str(path)})]
