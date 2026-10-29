"""The front doors' manifests and the repository's own docs: valid, complete, in step with Slate, and brand-free."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path

import pytest

from slate import __version__, procs
from slate.cli import run_cli
from slate.errors import Refused
from slate.mcp import mcpb, server

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "slate"


def load(rel: str) -> dict:
    return json.loads((REPO / rel).read_text())


# ---------------------------------------------------------------------------------------------------------------
# The plugin: one folder, two manifests (Claude Code and Agent Plugins)

CLAUDE_FIELDS = {"$schema", "name", "displayName", "version", "description", "author", "homepage", "repository",
                 "license", "keywords", "metadata", "defaultEnabled", "dependencies", "settings", "userConfig",
                 "channels", "skills", "commands", "agents", "hooks", "mcpServers", "lspServers", "outputStyles",
                 "workflows", "experimental"}
AGENT_PLUGIN_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")


def test_claude_plugin_manifest():
    data = load("plugins/slate/.claude-plugin/plugin.json")
    assert data["name"] == "slate" and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", data["name"])
    assert set(data) <= CLAUDE_FIELDS, set(data) - CLAUDE_FIELDS  # an unknown key would be stripped with a warning
    assert data["version"] == __version__
    assert data["description"] and data["author"]["name"] and data["license"]
    assert isinstance(data["repository"], str) and data["homepage"].startswith("https://")
    assert list((PLUGIN / ".claude-plugin").iterdir()) == [PLUGIN / ".claude-plugin" / "plugin.json"]


def test_claude_mcp_config_starts_slate_mcp():
    data = load("plugins/slate/.mcp.json")
    assert list(data) == ["mcpServers"]
    slate = data["mcpServers"]["slate"]
    assert slate["command"] == "slate" and slate["args"] == ["mcp"] and slate["env"]["BROWSER"] == "none"


KEYISH = re.compile(r"api[_-]?key|token|secret|bearer|authorization|xi-api|sk_[a-z0-9]|\$\{", re.I)


def test_the_mcp_configs_bundle_the_hosted_connectors_without_keys():
    """ElevenLabs' and Higgsfield's hosted connectors ship with the plugin as remote servers that sign in with OAuth:
    each is a fixed https URL and nothing else, so no key or header ever goes in the file."""
    from slate import skills_build as sb

    claude = load("plugins/slate/.mcp.json")["mcpServers"]
    agent = load("plugins/slate/mcp.json")["mcpServers"]
    for name, url, _label, _what in sb.HOSTED_CONNECTORS:
        assert url.startswith("https://") and url.endswith("/mcp"), url
        assert claude[name] == {"type": "http", "url": url}  # Claude Code's remote type
        assert agent[name] == {"type": "streamable-http", "url": url}  # Agent Plugins' remote type
    assert set(claude) == set(agent) == {"slate", "elevenlabs", "higgsfield"}
    for rel in ("plugins/slate/.mcp.json", "plugins/slate/mcp.json"):
        assert not KEYISH.search((REPO / rel).read_text()), rel


def test_the_plugins_own_files_on_disk_are_the_builds():
    """The manifests, MCP configs, README and LICENSE in plugins/slate are exactly what `slate skills build` makes,
    so nobody edits them by hand (CI's `slate skills build --check` holds the skill copies the same way)."""
    from slate import skills_build as sb

    for rel, (data, _executable) in sb.plugin_files().items():
        assert (PLUGIN / rel).read_bytes() == data, \
            f"plugins/slate/{rel} differs from the build: run slate skills build"


def test_agent_plugins_manifest_and_mcp_config():
    data = load("plugins/slate/plugin.json")
    assert data["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert AGENT_PLUGIN_NAME.match(data["name"]) and "--" not in data["name"] and ".." not in data["name"]
    assert data["name"] == load("plugins/slate/.claude-plugin/plugin.json")["name"]
    assert data["version"] == __version__
    assert "mcpServers" not in data and "mcp" not in data  # Agent Plugins keeps MCP in mcp.json, never inline
    mcp = load("plugins/slate/mcp.json")
    assert mcp["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    slate = mcp["mcpServers"]["slate"]
    assert slate["type"] == "stdio" and slate["args"] == ["mcp"]
    # command is a bare name or a ./ path, with no placeholders (the spec expands none in command)
    assert "${" not in slate["command"] and ("/" not in slate["command"] or slate["command"].startswith("./"))


def test_no_top_level_bin_folder():
    """claude.ai and Cowork refuse to install a plugin with a top-level bin/."""
    assert not (PLUGIN / "bin").exists()


def test_plugin_licence_matches_the_repositorys():
    assert (PLUGIN / "LICENSE").read_bytes() == (REPO / "LICENSE").read_bytes()


def test_plugin_readme_explains_the_install_order():
    text = (PLUGIN / "README.md").read_text()
    assert len(text.split()) >= 40  # a plugin directory listing needs a real README
    assert "install.sh" in text and "/plugin install slate@slate" in text and "bin/" in text


AUTO_UPDATE = "Turn on auto-update for Slate's marketplace"


def test_the_readmes_say_to_turn_on_auto_update():
    """Auto-update is off by default for a third-party marketplace, so both READMEs say to turn it on, and where."""
    for doc in ("README.md", "plugins/slate/README.md"):
        text = " ".join((REPO / doc).read_text().split())
        assert AUTO_UPDATE in text and "(`/plugin`, Marketplaces)" in text, doc
        assert "off by default for third-party marketplaces" in text, doc


def test_the_plugin_readme_lists_the_permission_rules_per_agent():
    """An unattended run never stalls on a prompt: the plugin's README carries the allowlist for Claude Code's settings
    and Codex's rules, the connector tools Slate denies, and why aerender isn't on the list."""
    from slate import skills_build as sb
    from slate.vendors.higgsfield import DENIED_CONNECTOR_TOOLS

    text = (PLUGIN / "README.md").read_text()
    rules = sb.permission_rules()
    for rule in rules["claude_code"]["permissions"]["allow"] + rules["claude_code"]["permissions"]["deny"]:
        assert f'"{rule}"' in text, rule
    for rule in rules["codex"]:
        assert rule in text, rule
    commands = {" ".join(words) for words, _ in sb.PERMISSIONS}
    assert {"slate", "ffmpeg", "ffprobe", "whisper", ".venv/bin/python", "node engine/cli.mjs"} <= commands
    assert all(f"mcp__plugin_slate_higgsfield__{tool}" in text for tool in DENIED_CONNECTOR_TOOLS)
    assert "aerender isn't on the list on purpose" in text
    assert "Permissions for unattended runs" in (REPO / "README.md").read_text()  # the root README points there


# ---------------------------------------------------------------------------------------------------------------
# The marketplace

RESERVED = {"claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
            "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "inline", "builtin", "skills-dir",
            "synced", "npm", "pip", "uv", "cargo", "github", "gh"}


def test_marketplace_lists_the_plugin():
    data = load(".claude-plugin/marketplace.json")
    assert {"name", "owner", "plugins"} <= set(data) and data["owner"]["name"] and data["description"]
    assert data["name"] not in RESERVED and not data["name"].startswith("claudeai-")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", data["name"]) and ".." not in data["name"]
    [entry] = data["plugins"]
    assert entry["name"] == load("plugins/slate/.claude-plugin/plugin.json")["name"]
    assert entry["source"].startswith("./") and ".." not in entry["source"] and "\\" not in entry["source"]
    assert (REPO / entry["source"] / ".claude-plugin" / "plugin.json").is_file()
    assert "version" not in entry  # plugin.json carries the version; setting both makes plugin.json win silently


# ---------------------------------------------------------------------------------------------------------------
# The desktop extension


def test_the_extension_manifest_matches_slate():
    assert mcpb.validate() == []
    data = mcpb.load()
    assert [t["name"] for t in data["tools"]] == list(server.TOOL_NAMES)
    assert data["manifest_version"] == "0.3" and data["version"] == __version__
    assert data["server"]["type"] == "binary" and data["compatibility"]["platforms"] == ["darwin"]
    assert data["server"]["mcp_config"]["env"]["BROWSER"] == "none"


def test_a_drifted_manifest_is_refused_and_sync_fixes_it(tmp_path):
    src = tmp_path / "mcpb"
    (src / "server").mkdir(parents=True)
    (src / "server" / "slate-mcp").write_bytes((REPO / "mcpb/server/slate-mcp").read_bytes())
    data = mcpb.load()
    data["version"] = "0.0.1"
    data["tools"] = data["tools"][:-1]
    (src / "manifest.json").write_text(json.dumps(data))
    problems = mcpb.validate(src=src)
    assert any("version" in p for p in problems) and any("tools" in p for p in problems)
    with pytest.raises(Refused, match="isn't ready"):
        mcpb.build(tmp_path / "dist", src=src)
    report = mcpb.sync(src)
    assert report["changed"] and mcpb.validate(src=src) == []
    kept = json.loads((src / "manifest.json").read_text())
    assert kept["long_description"] == data["long_description"]  # hand-written fields survive a sync


def test_the_packed_extension(tmp_path):
    report = mcpb.build(tmp_path)
    path = Path(report["path"])
    assert path.name == f"slate-{__version__}.mcpb" and path.is_file()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert {"manifest.json", "server/slate-mcp", "README.md"} <= set(names)
        assert json.loads(zf.read("manifest.json")) == mcpb.load()
        mode = zf.getinfo("server/slate-mcp").external_attr >> 16
        assert mode & stat.S_IXUSR
        assert all(zf.getinfo(n).date_time == (1980, 1, 1, 0, 0, 0) for n in names)
    again = mcpb.build(tmp_path / "second")
    assert again["sha256"] == report["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_mcpb_commands(tmp_path):
    code, out, _ = run_cli(["mcpb", "build", "--check", "--json"])
    assert code == 0 and json.loads(out)["ok"]
    code, out, _ = run_cli(["mcpb", "build", "--out", str(tmp_path), "--json"])
    assert code == 0 and Path(json.loads(out)["path"]).is_file()
    code, out, _ = run_cli(["mcpb", "build", "--out", str(tmp_path / "dry"), "--dry-run", "--json"])
    assert code == 0 and not json.loads(out)["written"] and not (tmp_path / "dry").exists()


def _launcher(env: dict, tmp_path: Path):
    return procs.run(["/bin/sh", str(REPO / "mcpb/server/slate-mcp")], env={"HOME": str(tmp_path), **env},
                     timeout=30)


def test_the_launcher_starts_the_installed_slate(tmp_path):
    assert procs.run(["/bin/sh", "-n", str(REPO / "mcpb/server/slate-mcp")]).returncode == 0
    assert os.access(REPO / "mcpb/server/slate-mcp", os.X_OK)
    fake = tmp_path / "fake-slate"
    seen = tmp_path / "seen.txt"
    fake.write_text(f'#!/bin/sh\necho "$@ BROWSER=$BROWSER" > "{seen}"\n')
    fake.chmod(0o755)
    proc = _launcher({"SLATE_BIN": str(fake)}, tmp_path)
    assert proc.returncode == 0 and seen.read_text().strip() == "mcp BROWSER=none"


def test_the_launcher_says_when_slate_isnt_installed(tmp_path):
    if Path("/opt/homebrew/bin/slate").exists():
        pytest.skip("Slate is linked into /opt/homebrew/bin on this Mac, so the launcher would find it")
    proc = _launcher({"SLATE_BIN": "${user_config.slate_command}"}, tmp_path)
    assert proc.returncode == 3 and "isn't installed" in proc.stderr and "install.sh" in proc.stderr


# ---------------------------------------------------------------------------------------------------------------
# CI, docs and the rules


def _workflow() -> dict:
    node = procs.which("node")
    js_yaml = REPO / "engine" / "node_modules" / "js-yaml"
    if not node or not js_yaml.is_dir():
        pytest.skip("node and js-yaml are needed to read the workflow")
    script = ("const y=require('js-yaml'),fs=require('fs');"
              f"process.stdout.write(JSON.stringify(y.load(fs.readFileSync({json.dumps(str(REPO / '.github/workflows/ci.yml'))},'utf8'))))")
    proc = procs.run([node, "-e", script], cwd=REPO / "engine", timeout=60, check=True)
    return json.loads(proc.stdout)


def test_ci_runs_both_validators_through_the_drift_check(skills_src, tmp_path, monkeypatch):
    """CI runs `slate skills build --check`, and that check runs `claude plugin validate --strict` on the plugin and
    `skills-ref validate` on every skill wherever they're installed (each skipped with a notice where it isn't)."""
    from slate import skills_build as sb

    runs = [step.get("run", "") for job in _workflow()["jobs"].values() for step in job["steps"]]
    [check] = [r for r in runs if "slate skills build --check" in r]
    assert "--no-validate" not in check
    assert [v.label for v in sb.VALIDATORS] == ["claude plugin validate --strict", "skills-ref validate"]
    asked = []
    monkeypatch.setattr(sb, "find_tool", lambda name: asked.append(name) or None)
    code, out, _ = run_cli(["skills", "build", "--check", "--src", str(skills_src), "--out", str(tmp_path / "plugin")])
    assert code == 1 and asked == ["claude", "skills-ref"]  # the default --check looks for both
    assert "skipped: `claude` isn't installed here" in out and "skipped: `skills-ref` isn't installed here" in out


def test_ci_runs_the_tests_and_the_drift_checks():
    flow = _workflow()
    assert flow["env"]["SLATE_FAKE_VENDORS"] == "1" and flow["env"]["BROWSER"] == "none"
    runs = [step.get("run", "") for job in flow["jobs"].values() for step in job["steps"]]
    joined = "\n".join(runs)
    assert "pytest tests" in joined
    assert "vitest run" in joined
    assert "slate skills build --check" in joined
    assert "slate knowledge check" in joined
    assert "slate mcpb build --check" in joined
    assert "npm install" not in joined  # CI installs exactly what the lockfile pins
    assert set(flow["on"]) >= {"push", "pull_request"}


def test_founding_decisions_and_followups_are_sequential_and_dated():
    from datetime import date

    text = (REPO / "docs" / "DECISIONS.md").read_text()
    numbers = re.findall(r"^## ADR-(\d{3}): ", text, re.M)
    assert len(numbers) >= 12
    assert numbers == [f"{n:03d}" for n in range(1, len(numbers) + 1)]
    sections = re.split(r"^## ADR-\d{3}: ", text, flags=re.M)[1:]
    for index, section in enumerate(sections):
        dated = re.search(r"^- \*\*Date:\*\* (\d{4}-\d{2}-\d{2})(?=[.\s]|$)", section, re.M)
        assert dated is not None
        recorded_date = date.fromisoformat(dated.group(1))
        if index < 12:
            assert recorded_date == date(2026, 9, 30)
        else:
            assert recorded_date >= date(2026, 9, 30)
        for part in ("**Context:**", "**Decision:**", "**Consequences:**"):
            assert part in section


def test_the_readme_covers_what_a_new_person_needs():
    text = (REPO / "README.md").read_text()
    for heading in ("## Download", "## Connect it: five steps", "## What you need first", "## Talking to it",
                    "## The Remotion licence", "## The browser guardrail", "## Where things live"):
        assert heading in text, heading
    assert re.search(r"git clone \S+ ~/code/slate && ~/code/slate/install\.sh", text)
    for step in ("**Download.**", "**Connect Claude.**", "**Connect Higgsfield.**", "**Connect ElevenLabs.**",
                 "**Connect After Effects.**"):
        assert step in text
    assert "company of four or more needs its own Remotion company licence" in text
    assert "open web renderer" in text and "After Effects" in text
    assert "BROWSER=none" in text and "Aside" in text
    assert "~/Movies/Slate" in text and "~/Library/Application Support/Slate" in text
    table = text.split("## What you need first", 1)[1].split("##", 1)[0]
    assert table.count("\n|") >= 11  # header, rule and the ten rows the plan lists


def test_changelog_agents_and_licence():
    changelog = (REPO / "CHANGELOG.md").read_text()
    assert changelog.startswith("# Changelog") and "## 0.1.0" in changelog
    agents = (REPO / "AGENTS.md").read_bytes()
    assert len(agents) < 32 * 1024  # Codex reads AGENTS.md up to 32 KiB
    text = agents.decode()
    assert "docs/ARCHITECTURE.md" in text and "pytest tests" in text and "SLATE_FAKE_VENDORS" in text
    licence = (REPO / "LICENSE").read_text()
    assert "Quinn O'Donnell" in licence and "proprietary" in licence.lower()


OWNED = ["README.md", "AGENTS.md", "CHANGELOG.md", "LICENSE", "docs/DECISIONS.md", ".github/workflows/ci.yml",
         ".claude-plugin/marketplace.json", "src/slate/guide.py", "src/slate/skills_build.py", "src/slate/mcp",
         "mcpb", "plugins/slate/.claude-plugin", "plugins/slate/.mcp.json", "plugins/slate/plugin.json",
         "plugins/slate/mcp.json", "plugins/slate/README.md", "plugins/slate/LICENSE", "tests/packaging/fixtures",
         "tests/packaging/client_behaviours.json"]
# Company and brand material that must never enter the core, written as patterns so this file doesn't carry it.
BRAND = (re.compile(r"true\s*space|\bvixa\b|\btrudy\b|inner\s*echo|\bdegular\b|quinnodonnell", re.I),
         re.compile(r"\bEOS\b|/Users/"))


def _owned_files():
    for rel in OWNED:
        path = REPO / rel
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
        for f in files:
            if "__pycache__" not in f.parts:
                yield f


def test_no_company_material_or_personal_paths_in_anything_packaging_owns():
    offenders = []
    for path in _owned_files():
        text = path.read_text(errors="replace")
        for pattern in BRAND:
            offenders += [f"{path.relative_to(REPO)}: {m.group(0)!r}" for m in pattern.finditer(text)]
    assert offenders == []


def test_no_literal_frame_sizes_or_rates_in_packaging_code():
    for path in _owned_files():
        if path.suffix == ".py":
            text = path.read_text()
            assert not re.search(r"\b(1920|1080)\b|30000\s*/\s*1001", text), path


def test_ci_names_both_validators_in_steps_of_their_own():
    """CI runs each client's validator by name, installed, so neither is ever skipped there: Claude Code's
    `plugin validate --strict` on the built plugin, and skills-ref's `validate` on every built skill (L-packaging-080)."""
    steps = [step for job in _workflow()["jobs"].values() for step in job["steps"]]
    claude = [s for s in steps if "plugin validate --strict plugins/slate" in s.get("run", "")]
    ref = [s for s in steps if "skills-ref validate" in s.get("run", "")]
    assert len(claude) == 1 and claude[0]["name"].startswith("claude plugin validate --strict")
    assert len(ref) == 1 and ref[0]["name"].startswith("skills-ref validate")
    assert "plugins/slate/skills/*/" in ref[0]["run"] and 'pip install "skills-ref==' in ref[0]["run"]
    runs = [s.get("run", "") for s in steps]
    assert runs.index(claude[0]["run"]) > next(i for i, r in enumerate(runs) if "slate skills build --check" in r)
