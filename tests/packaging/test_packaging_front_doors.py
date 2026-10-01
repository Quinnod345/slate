"""The front doors other agents read first (README, AGENTS.md, the plugin's and extension's READMEs, the changelog)
cite only commands that exist, and say what the current clients actually take.

tests/skills holds the skills to ARCHITECTURE §4; this holds packaging's own documents to the live CLI, so a renamed
stage or a module that isn't built yet shows up here rather than in an agent's failed command.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from slate import cli

REPO = Path(__file__).resolve().parents[2]
DOCS = ["README.md", "AGENTS.md", "CHANGELOG.md", "plugins/slate/README.md", "mcpb/README.md", "docs/DECISIONS.md"]
# Commands a document may name before their module lands, because it says they're still to come. Each must really be
# missing: once it's built, the exemption goes (the test below says so). `slate demo` landed on 2026-10-01.
NOT_YET: set[str] = set()
GLOBAL_VALUE_FLAGS = {"--film"}
BARE = re.compile(r"^[a-z][a-z0-9-]*$")


def _citation(text: str) -> tuple[str, str | None] | None:
    tokens = text.strip().split()
    if len(tokens) < 2 or tokens[0] != "slate":
        return None
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in GLOBAL_VALUE_FLAGS else 1
    if i >= len(tokens) or not BARE.match(tokens[i]):
        return None
    stage = tokens[i + 1] if i + 1 < len(tokens) and BARE.match(tokens[i + 1]) else None
    return tokens[i], stage


def citations(path: Path) -> list[tuple[int, str, str | None]]:
    """(line, command, stage) for every `slate …` in inline code or a fenced block's command lines."""
    out = []
    fence = False
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        parts = re.split(r"&&|;|\|\|", line.split("#")[0]) if fence else re.findall(r"`([^`\n]+)`", line)
        for part in parts:
            c = _citation(re.sub(r"^\s*\$\s+", "", part))
            if c:
                out.append((n, *c))
    return out


def _registered() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:  # noqa: SLF001 - argparse keeps its subparsers here
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            return set(action.choices)
    return set()


REGISTERED = _registered()


@pytest.mark.parametrize("doc", DOCS)
def test_every_command_a_front_door_cites_exists(doc):
    cites = citations(REPO / doc)
    failures = []
    for line, cmd, stage in cites:
        if cmd in NOT_YET and cmd not in REGISTERED:
            continue
        argv = [cmd] + ([stage] if stage else []) + ["--help"]
        code, _out, err = cli.run_cli(argv)
        if code != 0:
            failures.append(f"{doc}:{line}: `slate {' '.join(argv[:-1])}` doesn't parse (exit {code}): "
                            f"{err.strip()[-160:]}")
    assert not failures, "\n".join(failures)


def test_the_not_yet_list_is_honest():
    """When a command lands, drop it from NOT_YET and from the 'still to come' wording."""
    built = sorted(NOT_YET & REGISTERED)
    assert not built, f"now built: {built}; remove them from NOT_YET and update README's status and CHANGELOG"
    readme = (REPO / "README.md").read_text()
    for cmd in NOT_YET:
        assert f"slate {cmd}" in readme and "still to come" in readme.lower()
    if not NOT_YET:  # nothing a front door names is still to come
        assert "still to come" not in readme.lower()
        assert "demo" in REGISTERED and "`slate demo`" in readme


def test_the_readme_cites_many_commands_and_the_parser_sees_them():
    cites = citations(REPO / "README.md")
    assert len(cites) >= 15, cites
    assert {"doctor", "connect", "status", "guide", "update", "install", "ledger", "mcpb"} <= {c for _, c, _ in cites}


def test_front_doors_register_openclaw_the_current_way():
    """OpenClaw 2026.9.4 registers with `openclaw mcp add … --command … --arg …`; `mcp set` is the plan's older form."""
    for doc in ("README.md", "AGENTS.md", "plugins/slate/README.md", "mcpb/README.md"):
        assert "openclaw mcp set" not in (REPO / doc).read_text(), doc
    assert "openclaw mcp add slate --command slate --arg mcp --connect-timeout 30" in (REPO / "README.md").read_text()


def test_the_readme_tells_sessions_to_snapshot_and_reconcile_the_ledger():
    text = (REPO / "README.md").read_text()
    assert "slate ledger snapshot --all --session <label>" in text
    assert "slate ledger reconcile --session <label>" in text


def test_the_installer_and_openclaw_entries_agree_on_the_wait():
    from slate import skills_build
    from slate.install import mcp_register

    assert skills_build.OPENCLAW_CONNECT_TIMEOUT_MS == mcp_register.OPENCLAW_CONNECT_TIMEOUT_S * 1000
