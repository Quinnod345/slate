"""`slate skills build`: the frontmatter reader, the authoring rules, every client's generated copy, and drift checks.

Everything runs on the neutral fixture skills in tests/packaging/fixtures/skills, copied to a temporary folder, and
builds into a temporary output folder: the real plugins/slate is never written by a test.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

from slate import skills_build as sb
from slate.cli import run_cli
from slate.errors import MissingPrerequisite, UsageError

# ---------------------------------------------------------------------------------------------------------------
# Frontmatter


def test_frontmatter_reads_the_portable_yaml_subset():
    text = """---
name: demo
description: >
  First line of a folded
  description.

  A second paragraph.
license: 'It''s proprietary'
compatibility: "macOS, with \\"slate\\" on PATH"
metadata:
  owner: tests
  version: "2"
allowed-tools: Read Grep
notes: |
  kept
    as written
---
# Body
"""
    meta, front, body = sb.parse_frontmatter(text)
    assert meta["name"] == "demo"
    assert meta["description"] == "First line of a folded description.\nA second paragraph.\n"
    assert meta["license"] == "It's proprietary"
    assert meta["compatibility"] == 'macOS, with "slate" on PATH'
    assert meta["metadata"] == {"owner": "tests", "version": "2"}
    assert meta["allowed-tools"] == "Read Grep"
    assert meta["notes"] == "kept\n  as written\n"
    assert body.strip() == "# Body"
    assert front.startswith("name: demo")


def test_plain_and_quoted_values_continue_on_indented_lines():
    meta, _, _ = sb.parse_frontmatter('---\nname: x\ndescription: "one\n  two"\nlicense: a\n  b\n---\n')
    assert meta["description"] == "one two"
    assert meta["license"] == "a b"


@pytest.mark.parametrize("front, why", [
    ("name: x\n", "never closes"),
    ("name: [x]\n---\n", "flow collections"),
    ("name: x\nname: y\n---\n", "twice"),
    ('name: "x\n---\n', "never closes"),
    ("metadata:\n  deep:\n    more: x\n---\n", "one level deep"),
    ("  name: x\n---\n", "indented"),
    ("name:x\n---\n", "key: value"),
])
def test_frontmatter_refuses_what_some_client_would_misread(front, why):
    with pytest.raises(sb.FrontmatterError, match=why):
        sb.parse_frontmatter("---\n" + front)


def test_a_file_without_frontmatter_is_refused():
    with pytest.raises(sb.FrontmatterError, match="must start"):
        sb.parse_frontmatter("# Just a heading\n")


# ---------------------------------------------------------------------------------------------------------------
# Authoring rules


def _skill(root: Path, name: str, front: str, body: str = "# Body\n", refs: dict[str, str] | None = None) -> Path:
    folder = root / name
    (folder / "references").mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\n{front}---\n\n{body}")
    for rel, text in (refs or {}).items():
        (folder / "references" / rel).write_text(text)
    return folder


def test_fixture_skills_follow_every_rule(skills_src):
    found = sb.discover(skills_src)
    assert [s.name for s in found] == ["demo-craft", "demo-front"]
    assert all(s.problems == [] for s in found), [s.problems for s in found]


@pytest.mark.parametrize("front, body, expected", [
    ("name: x-one\ndescription: d\nargument-hint: '[film]'\n", "", "only name, description"),
    ("name: other\ndescription: d\n", "", "doesn't match its folder"),
    ("name: x-one\n", "", "no description"),
    ("name: x-one\ndescription: " + "d" * 1100 + "\n", "", "limit is 1024"),
    ("name: x-one\ndescription: d\n", "Use the Task tool to fan out.\n", "Claude-only tool name"),
    ("name: x-one\ndescription: d\n", "Scripts live in ${CLAUDE_SKILL_DIR}/scripts.\n", "Claude Code path variable"),
    ("name: x-one\ndescription: d\n", "Scripts live in {baseDir}/scripts.\n", "OpenClaw path placeholder"),
    ("name: x-one\ndescription: d\n", "Call mcp__slate__status first.\n", "MCP tool name"),
    ("name: x-one\ndescription: d\n", "See [the law](references/missing.md).\n", "isn't in the skill"),
    ("name: x-one\ndescription: d\n", "line\n" * 520, "keep it under 500"),
    ("name: x-one\ndescription: d\nmetadata:\n  n: 1\n  m: two\n", "", None),
])
def test_authoring_rules(tmp_path, front, body, expected):
    folder = _skill(tmp_path, "x-one", front, body)
    problems = sb.load_skill(folder).problems
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems


def test_a_bad_name_is_refused_even_when_it_matches_its_folder(tmp_path):
    folder = _skill(tmp_path, "Bad_Name", "name: Bad_Name\ndescription: d\n")
    assert any("lowercase letters" in p for p in sb.load_skill(folder).problems)


def test_references_are_checked_for_portability_and_links_too(tmp_path):
    folder = _skill(tmp_path, "x-one", "name: x-one\ndescription: d\n", refs={
        "a.md": "# A\nUse WebFetch to read it. See [b](b.md) and [c](c.md).\n", "b.md": "# B\n"})
    problems = sb.load_skill(folder).problems
    assert any("references/a.md names a Claude-only tool name" in p for p in problems)
    assert any("links to c.md" in p for p in problems)
    assert not any("links to b.md" in p for p in problems)


# ---------------------------------------------------------------------------------------------------------------
# What gets built


def _zip_text(path: Path, member: str) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read(member).decode()


def test_build_writes_every_clients_copy(skills_src, build_out):
    report = sb.build(skills_src, build_out)
    assert report["problems"] == [] and report["skills"] == ["demo-craft", "demo-front"]

    # The plugin's copies are the skills byte for byte, scripts keep their executable bit.
    for rel in ("demo-front/SKILL.md", "demo-front/references/intake.md", "demo-craft/references/eases.md",
                "demo-craft/scripts/check.sh"):
        assert (build_out / "skills" / rel).read_bytes() == (skills_src / rel).read_bytes()
    assert os.access(build_out / "skills/demo-craft/scripts/check.sh", os.X_OK)
    assert not os.access(build_out / "skills/demo-front/SKILL.md", os.X_OK)
    assert "slate skills build" in (build_out / "skills/README.md").read_text()

    # Codex sidecars.
    sidecar = (build_out / "skills/demo-front/agents/openai.yaml").read_text()
    assert 'display_name: "Demo Front"' in sidecar
    assert "short_description: \"A neutral front-door skill used by Slate's packaging tests.\"" in sidecar
    assert "allow_implicit_invocation: true" in sidecar
    assert "default_prompt" not in sidecar  # only the front door (the skill named "slate") gets one
    assert sidecar.startswith("# do not edit by hand")

    # OpenClaw entries.
    entries = json.loads((build_out / "openclaw/entries.json").read_text())
    assert entries["skills"] == ["demo-craft", "demo-front"]
    assert entries["openclaw_json"]["skills"]["entries"]["demo-front"] == {"enabled": True}
    # The same wait `slate install` registers: OpenClaw probes the server, and `slate mcp` can take over 5 seconds.
    assert entries["openclaw_json"]["mcp"]["servers"]["slate"] == {"command": "slate", "args": ["mcp"],
                                                                   "connectionTimeoutMs": 30000}
    assert "openclaw mcp add slate --command slate --arg mcp --connect-timeout 30" in entries["about"]
    assert "mcp set" not in entries["about"] and "plugins/slate/skills" not in entries["about"], \
        "the installer links ~/.agents/skills to the checkout's skills/ folder, and registers with `mcp add`"
    assert "sync_agents" in entries["denied_connector_tools"]["tools"]


def test_the_claude_ai_zip_is_the_knowledge_only_edition_with_references_inlined(skills_src, build_out):
    sb.build(skills_src, build_out)
    zpath = build_out / "claude-ai" / "demo-front.zip"
    with zipfile.ZipFile(zpath) as zf:
        assert zf.namelist() == ["demo-front/SKILL.md"]  # claude.ai wants the skill folder at the top of the zip
    text = _zip_text(zpath, "demo-front/SKILL.md")
    meta, _, body = sb.parse_frontmatter(text)
    assert set(meta) <= set(sb.ALLOWED_KEYS) and meta["name"] == "demo-front"
    assert meta["metadata"] == {"owner": "tests", "version": "1"}
    assert "Knowledge-only edition" in body and "can't run the Slate command line" in body
    # Both references are inlined, under one outline, and links to them point inside the file.
    assert "## Reference: Intake: the request, word for word" in body
    assert "## Reference: Formats" in body
    assert "### The one question" in body  # the reference's own headings moved down a level
    assert "](references/" not in body and "](formats.md)" not in body
    assert "[intake](#reference-intake-the-request-word-for-word)" in body
    assert "[formats](#reference-formats)" in body
    assert "A reel is short and vertical" in body


def test_the_front_door_gets_codexs_default_prompt(tmp_path, build_out):
    _skill(tmp_path / "src", "slate", "name: slate\ndescription: The front door. It routes.\n")
    sb.build(tmp_path / "src", build_out)
    sidecar = (build_out / "skills/slate/agents/openai.yaml").read_text()
    assert "default_prompt: \"Use $slate to make" in sidecar


def test_a_skills_own_sidecar_wins(skills_src, build_out):
    (skills_src / "demo-craft" / "agents").mkdir()
    (skills_src / "demo-craft" / "agents" / "openai.yaml").write_text("interface:\n  display_name: \"Craft\"\n")
    sb.build(skills_src, build_out)
    assert (build_out / "skills/demo-craft/agents/openai.yaml").read_text() == "interface:\n  display_name: \"Craft\"\n"


def test_builds_are_idempotent_and_deterministic(skills_src, build_out, tmp_path):
    first = sb.build(skills_src, build_out)
    assert first["written"]
    again = sb.build(skills_src, build_out)
    assert again["written"] == [] and again["removed"] == [] and again["clean"]
    other = tmp_path / "other"
    sb.build(skills_src, other)
    for rel in ("claude-ai/demo-front.zip", "openclaw/entries.json", "skills/demo-front/agents/openai.yaml"):
        assert (other / rel).read_bytes() == (build_out / rel).read_bytes()


def test_check_catches_every_kind_of_drift(skills_src, build_out):
    sb.build(skills_src, build_out)
    assert sb.check(skills_src, build_out)["clean"]

    (skills_src / "demo-front/references/formats.md").write_text("# Formats\n\nChanged.\n")
    report = sb.check(skills_src, build_out)
    assert not report["clean"]
    assert "skills/demo-front/references/formats.md" in report["changed"]
    assert "claude-ai/demo-front.zip" in report["changed"]  # the inlined edition changed with it

    sb.build(skills_src, build_out)
    (build_out / "skills/demo-front/references/hand-edit.md").write_text("# Someone edited the copy\n")
    (build_out / "skills/demo-craft/agents/openai.yaml").unlink()
    report = sb.check(skills_src, build_out)
    assert report["extra"] == ["skills/demo-front/references/hand-edit.md"]
    assert report["missing"] == ["skills/demo-craft/agents/openai.yaml"]

    sb.build(skills_src, build_out)
    assert sb.check(skills_src, build_out)["clean"]
    assert not (build_out / "skills/demo-front/references/hand-edit.md").exists()


def test_a_removed_skill_disappears_from_every_copy(skills_src, build_out):
    sb.build(skills_src, build_out)
    import shutil

    shutil.rmtree(skills_src / "demo-craft")
    report = sb.build(skills_src, build_out)
    assert "claude-ai/demo-craft.zip" in report["removed"]
    assert not (build_out / "skills" / "demo-craft").exists()  # emptied folders are pruned too
    assert json.loads((build_out / "openclaw/entries.json").read_text())["skills"] == ["demo-front"]


def test_a_broken_skill_stops_the_whole_build(skills_src, build_out):
    front = skills_src / "demo-front" / "SKILL.md"
    front.write_text(front.read_text().replace("license: Proprietary", "argument-hint: '[film]'"))
    report = sb.build(skills_src, build_out)
    assert report["problems"] and report["written"] == []
    assert not (build_out / "skills").exists()
    code, out, _ = run_cli(["skills", "build", "--src", str(skills_src), "--out", str(build_out), "--json"])
    assert code == 1 and "argument-hint" in json.loads(out)["problems"][0]


def test_the_build_never_writes_over_its_sources(skills_src):
    with pytest.raises(UsageError, match="overlap"):
        sb.build(skills_src, skills_src / "demo-front")
    with pytest.raises(UsageError, match="overlap"):
        sb.build(skills_src, skills_src)


def test_no_skills_folder_is_a_missing_prerequisite(tmp_path):
    with pytest.raises(MissingPrerequisite):
        sb.discover(tmp_path / "nowhere")
    code, out, _ = run_cli(["skills", "list", "--src", str(tmp_path / "nowhere"), "--json"])
    assert code == 3 and json.loads(out)["error"] == "missing_prerequisite"


def test_an_empty_skills_folder_builds_an_empty_tree(tmp_path, build_out):
    (tmp_path / "empty").mkdir()
    report = sb.build(tmp_path / "empty", build_out)
    assert report["skills"] == [] and report["problems"] == []
    assert json.loads((build_out / "openclaw/entries.json").read_text())["skills"] == []


# ---------------------------------------------------------------------------------------------------------------
# The commands


def test_cli_build_check_and_dry_run(skills_src, build_out):
    args = ["--src", str(skills_src), "--out", str(build_out)]
    code, out, _ = run_cli(["skills", "build", *args, "--check", "--json"])
    assert code == 1 and json.loads(out)["missing"]  # nothing built yet: everything is missing

    code, out, _ = run_cli(["skills", "build", *args, "--dry-run", "--json"])
    assert code == 0 and json.loads(out)["dry_run"] and not (build_out / "skills").exists()

    code, out, _ = run_cli(["skills", "build", *args, "--json"])
    assert code == 0 and json.loads(out)["skills"] == ["demo-craft", "demo-front"]

    code, out, _ = run_cli(["skills", "build", *args, "--check"])
    assert code == 0 and "Nothing drifted" in out

    (skills_src / "demo-craft/references/eases.md").write_text("# Eases\n\nEdited.\n")
    code, out, _ = run_cli(["skills", "build", *args, "--check"])
    assert code == 1 and "changed  skills/demo-craft/references/eases.md" in out and "next: slate skills build" in out


def test_cli_list_reports_rules_per_skill(skills_src):
    code, out, _ = run_cli(["skills", "list", "--src", str(skills_src), "--json"])
    rows = json.loads(out)["skills"]
    assert code == 0 and [r["name"] for r in rows] == ["demo-craft", "demo-front"] and all(r["valid"] for r in rows)
    (skills_src / "demo-craft/SKILL.md").write_text("---\nname: demo-craft\n---\n")
    code, out, _ = run_cli(["skills", "list", "--src", str(skills_src)])
    assert code == 1 and "BROKEN" in out and "no description" in out


def test_installed_counts_each_skill_once_per_agent(tmp_path):
    roots = {name: tmp_path / name for name in ("agents", "codex", "claude")}
    for root in roots.values():
        root.mkdir()
    for root in (roots["agents"], roots["codex"]):
        (root / "demo-front").mkdir()
        (root / "demo-front" / "SKILL.md").write_text("---\nname: demo-front\n---\n")
    (roots["claude"] / "demo-front").mkdir()
    (roots["claude"] / "demo-front" / "SKILL.md").write_text("---\nname: demo-front\n---\n")
    table = {"codex": (roots["agents"], roots["codex"]), "openclaw": (roots["agents"],), "claude-code": (roots["claude"],)}
    rows = {(r["skill"], r["agent"]): r for r in sb.installed(["demo-front", "demo-craft"], table)}
    assert rows[("demo-front", "codex")]["status"] == "duplicated"  # Codex would list it twice
    assert rows[("demo-front", "openclaw")]["status"] == "once"
    assert rows[("demo-front", "claude-code")]["status"] == "once"
    assert rows[("demo-craft", "codex")]["status"] == "missing"


def test_the_real_skills_are_read_and_judged_without_crashing():
    """Whatever state the checkout's skills are in, the build reads them and names each problem's skill.

    Holding every real skill to the rules is CI's job (`slate skills build --check`), so a skill that's still being
    written doesn't fail this suite; this proves the build can judge it.
    """
    built = sb.render()
    names = [s.name for s in built.skills]
    assert names == sorted(names)
    for problem in built.problems:
        assert problem.split(":", 1)[0] in [*names, "plugin"]  # a skill's own problem, or one about the whole plugin
    good = [s.name for s in built.skills if not s.problems]
    assert {f"claude-ai/{n}.zip" for n in good} <= set(built.files)


def test_zip_members_are_stored_with_fixed_timestamps(skills_src, build_out):
    sb.build(skills_src, build_out)
    with zipfile.ZipFile(build_out / "claude-ai/demo-craft.zip") as zf:
        info = zf.infolist()[0]
    assert info.date_time == (1980, 1, 1, 0, 0, 0) and info.compress_type == zipfile.ZIP_STORED


def test_short_descriptions_stop_at_a_word(tmp_path):
    long = "Word " * 60 + "end."
    assert len(sb.short_description(long)) <= sb.MAX_SHORT_DESCRIPTION
    assert sb.short_description(long).endswith("…")
    assert sb.short_description("One sentence. Two sentences.") == "One sentence."
    assert sb.display_name("slate-motion") == "Slate Motion"


def test_generated_text_names_no_brand_and_no_personal_path(skills_src, build_out):
    sb.build(skills_src, build_out)
    for path in build_out.rglob("*"):
        if path.is_file() and path.suffix != ".zip":
            text = path.read_text()
            assert "/Users/" not in text and not re.search(r"true\s*space|\bvixa\b|\btrudy\b", text, re.I)
    for path in (build_out / "claude-ai").glob("*.zip"):
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
            for name in zf.namelist():
                assert "/Users/" not in zf.read(name).decode()



# ---------------------------------------------------------------------------------------------------------------
# The plugin's own files are generated too


def test_the_plugins_own_files_are_generated(skills_src, build_out):
    from slate import __version__

    sb.build(skills_src, build_out)
    claude = json.loads((build_out / ".claude-plugin/plugin.json").read_text())
    agent = json.loads((build_out / "plugin.json").read_text())
    assert claude["name"] == agent["name"] == "slate" and claude["version"] == agent["version"] == __version__
    assert "displayName" in claude and "$schema" in agent and "displayName" not in agent
    assert json.loads((build_out / ".mcp.json").read_text()) == sb.mcp_servers("claude")
    assert json.loads((build_out / "mcp.json").read_text()) == sb.mcp_servers("agent-plugins")
    assert (build_out / "LICENSE").read_bytes() == (Path(sb.paths.REPO) / "LICENSE").read_bytes()
    readme = (build_out / "README.md").read_text()
    assert readme.startswith("# Slate plugin") and sb.GENERATED in readme
    assert sb.check(skills_src, build_out)["clean"]


def test_a_hand_edit_or_a_stray_file_in_the_plugin_is_drift(skills_src, build_out):
    sb.build(skills_src, build_out)
    (build_out / ".mcp.json").write_text('{"mcpServers": {}}\n')
    (build_out / "bin").mkdir()
    (build_out / "bin" / "slate").write_text("#!/bin/sh\n")  # claude.ai refuses a plugin with a top-level bin/
    (build_out / "notes.txt").write_text("someone's notes\n")
    report = sb.check(skills_src, build_out)
    assert ".mcp.json" in report["changed"]
    assert {"bin/slate", "notes.txt"} <= set(report["extra"])
    rebuilt = sb.build(skills_src, build_out)
    assert {"bin/slate", "notes.txt"} <= set(rebuilt["removed"]) and not (build_out / "bin").exists()
    assert sb.check(skills_src, build_out)["clean"]


# ---------------------------------------------------------------------------------------------------------------
# The clients' limits: listing cuts, Codex's skill-list budget, claude.ai's caps, no media


def test_a_listing_past_claude_codes_cut_is_refused(tmp_path):
    front = "name: x-one\ndescription: " + "d" * 1000 + "\nwhen_to_use: " + "w" * 600 + "\n"
    problems = sb.load_skill(_skill(tmp_path, "x-one", front)).problems
    assert any("Claude Code cuts a skill's listing at 1536" in p for p in problems), problems
    assert any("when_to_use" in p and "only name, description" in p for p in problems)  # and it isn't a spec key
    ok = sb.load_skill(_skill(tmp_path / "ok", "x-one", "name: x-one\ndescription: " + "d" * 1000 + "\n")).problems
    assert ok == []


def _many(root: Path, count: int, length: int) -> Path:
    for i in range(count):
        name = f"skill-{i:02d}"
        _skill(root, name, f"name: {name}\ndescription: Use it for test {i}. " + "x" * length + "\n")
    return root


def test_an_over_long_skill_list_fails_codexs_budget(tmp_path, build_out):
    src = _many(tmp_path / "src", 12, 700)
    assert sb.codex_list_chars(sb.discover(src)) > sb.CODEX_LIST_BUDGET
    report = sb.build(src, build_out)
    assert any(p.startswith("plugin: the skill list comes to") and "8,000" in p for p in report["problems"])
    assert report["written"] == [] and not (build_out / "skills").exists()


def test_a_list_near_codexs_budget_builds_with_a_note(tmp_path, build_out):
    src = _many(tmp_path / "src", 10, 690)
    listed = sb.codex_list_chars(sb.discover(src))
    assert sb.CODEX_LIST_NOTE * sb.CODEX_LIST_BUDGET <= listed <= sb.CODEX_LIST_BUDGET
    report = sb.build(src, build_out)
    assert report["problems"] == [] and report["written"]
    assert any("of Codex's 8,000 characters" in n for n in report["notes"])


def test_the_real_skill_list_fits_codexs_budget():
    assert sb.codex_list_chars(sb.discover()) <= sb.CODEX_LIST_BUDGET


@pytest.mark.parametrize("rel, kind", [("assets/brand.ttf", "a font"), ("assets/look.cube", "a LUT"),
                                       ("instruments/felt.exs", "a sound bank"), ("sounds/click.wav", "audio")])
def test_media_in_a_skill_is_refused(skills_src, build_out, rel, kind):
    path = skills_src / "demo-craft" / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00\x01\x02")
    report = sb.build(skills_src, build_out)
    assert any(f"demo-craft: {rel} is {kind}" in p and "media stays out" in p for p in report["problems"])
    assert report["written"] == []


def test_the_plugin_stays_under_claude_ais_caps(skills_src, build_out, monkeypatch):
    built = sb.render(skills_src)
    count, size = len(built.files), sum(len(d) for d, _ in built.files.values())
    assert built.problems == [] and count < sb.PLUGIN_MAX_FILES and size < sb.PLUGIN_MAX_BYTES
    monkeypatch.setattr(sb, "PLUGIN_MAX_FILES", count - 1)
    monkeypatch.setattr(sb, "PLUGIN_MAX_BYTES", size - 1)
    report = sb.build(skills_src, build_out)
    assert any(f"plugin: {count:,} files; claude.ai takes at most" in p for p in report["problems"])
    assert any("MB; claude.ai takes at most" in p for p in report["problems"])
    assert report["written"] == []


# ---------------------------------------------------------------------------------------------------------------
# The clients' own validators, run by --check wherever they're installed


def _stand_in(folder: Path, name: str, body: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_check_runs_each_clients_validator_where_installed(skills_src, build_out, tmp_path, monkeypatch):
    sb.build(skills_src, build_out)
    seen = tmp_path / "seen.txt"
    tools = {
        "claude": _stand_in(tmp_path / "bin", "claude", f'echo "claude $*" >> "{seen}"\necho ok\n'),
        "skills-ref": _stand_in(tmp_path / "bin", "skills-ref",
                                f'echo "skills-ref $*" >> "{seen}"\n'
                                'case "$2" in *demo-craft) echo "demo-craft: bad frontmatter" >&2; exit 1;; esac\n'),
    }
    monkeypatch.setattr(sb, "find_tool", lambda name: tools.get(name))
    report = sb.check(skills_src, build_out, validators=True)
    rows = {(r["validator"], Path(r["target"]).name): r for r in report["validators"]}
    assert rows[("claude plugin validate --strict", build_out.name)]["ok"] is True
    assert rows[("skills-ref validate", "demo-front")]["ok"] is True
    assert rows[("skills-ref validate", "demo-craft")]["ok"] is False
    assert "bad frontmatter" in rows[("skills-ref validate", "demo-craft")]["output"]
    assert report["clean"] and report["valid"] is False
    calls = seen.read_text().splitlines()
    assert f"claude plugin validate --strict {build_out}" in calls
    assert f"skills-ref validate {build_out}/skills/demo-front" in calls
    code, out, _ = run_cli(["skills", "build", "--src", str(skills_src), "--out", str(build_out), "--check"])
    assert code == 1 and "FAILED skills-ref validate (demo-craft)" in out and "passed claude plugin validate" in out
    code, out, _ = run_cli(["skills", "build", "--src", str(skills_src), "--out", str(build_out), "--check",
                            "--no-validate"])
    assert code == 0 and "skills-ref" not in out


def test_a_validator_that_isnt_installed_is_skipped_with_a_notice(skills_src, build_out):
    sb.build(skills_src, build_out)
    report = sb.check(skills_src, build_out, validators=True)
    assert report["valid"] and all(r["status"] == "skipped" for r in report["validators"])
    assert any("claude plugin validate --strict was skipped: `claude` isn't installed here" in n
               for n in report["notes"])
    assert any("skills-ref validate was skipped" in n for n in report["notes"])
    code, out, _ = run_cli(["skills", "build", "--src", str(skills_src), "--out", str(build_out), "--check"])
    assert code == 0 and "isn't installed here" in out


def test_validators_never_judge_a_drifted_copy(skills_src, build_out, tmp_path, monkeypatch):
    tools = {"claude": _stand_in(tmp_path / "bin", "claude", "exit 0\n")}
    monkeypatch.setattr(sb, "find_tool", lambda name: tools.get(name))
    report = sb.check(skills_src, build_out, validators=True)  # nothing built yet
    assert not report["clean"]
    [claude] = [r for r in report["validators"] if r["validator"].startswith("claude")]
    assert claude["status"] == "skipped" and "drifted" in claude["why"]
