"""Every agent: skills listed once each, MCP registered, the CLI on PATH, and the Codex Dia flag."""

from __future__ import annotations

import json
import shutil

from slate.doctor import agents, core
from slate.doctor.env import Ran

from doctor_helpers import by_id, codex_prompt_input


def ctx(denv):
    return core.make_ctx(denv)


def skills(denv):
    return by_id(agents.check_skills(denv, ctx(denv)))


def mcp(denv):
    return by_id(agents.check_mcp(denv, ctx(denv)))


# ---------------------------------------------------------------------------------------------------------------
# Skills listed once each


def test_skills_green_when_linked_once_everywhere(denv):
    got = skills(denv)
    assert {r["status"] for r in got.values()} == {"green"}


def test_a_slate_skill_in_both_codex_roots_is_listed_twice_and_red(denv):
    (denv.home / ".codex" / "skills" / "slate").symlink_to(denv.repo_dir / "skills" / "slate")
    row = skills(denv)["agents.skills.codex"]
    assert row["status"] == "red" and "twice" in row["summary"]
    assert row["details"]["listed_twice"] == ["slate"]
    assert "~/.codex/skills" in row["fix"] and "never links into ~/.codex/skills" in row["fix"]


def test_other_skills_listed_twice_are_reported_as_the_owners_config(denv):
    for root in (denv.home / ".agents" / "skills", denv.home / ".codex" / "skills"):
        (root / "someone-elses-skill").mkdir()
    row = skills(denv)["agents.skills.codex"]
    assert row["status"] == "green"
    assert row["details"]["other_skills_listed_twice"] == ["someone-elses-skill"]
    assert "that's your config" in row["summary"]


def test_missing_skills_are_red_per_agent(denv):
    (denv.home / ".claude" / "skills" / "slate-story").unlink()
    (denv.home / ".agents" / "skills" / "slate").unlink()
    got = skills(denv)
    assert got["agents.skills.claude-code"]["status"] == "red"
    assert "slate-story" in got["agents.skills.claude-code"]["summary"]
    assert got["agents.skills.codex"]["status"] == "red" and got["agents.skills.openclaw"]["status"] == "red"
    assert "slate connect claude" in got["agents.skills.claude-code"]["fix"]


def test_a_copy_instead_of_a_link_is_yellow(denv):
    link = denv.home / ".claude" / "skills" / "slate"
    link.unlink()
    shutil.copytree(denv.repo_dir / "skills" / "slate", link)
    row = skills(denv)["agents.skills.claude-code"]
    assert row["status"] == "yellow" and "copy" in row["summary"]


def test_a_skill_only_in_the_old_codex_root_is_yellow(denv):
    (denv.home / ".agents" / "skills" / "slate-story").unlink()
    (denv.home / ".codex" / "skills" / "slate-story").symlink_to(denv.repo_dir / "skills" / "slate-story")
    row = skills(denv)["agents.skills.codex"]
    assert row["status"] == "yellow" and "older folder" in row["summary"]


def test_plugin_plus_links_is_listed_twice_in_claude_code(denv):
    plugins = denv.home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"slate@slate": [{}]}}))
    row = skills(denv)["agents.skills.claude-code"]
    assert row["status"] == "yellow" and "twice" in row["summary"]


def test_a_second_openclaw_copy_is_yellow_and_a_custom_state_dir_is_flagged(denv):
    managed = denv.home / ".openclaw" / "skills" / "slate"
    managed.parent.mkdir(parents=True)
    managed.mkdir()
    assert skills(denv)["agents.skills.openclaw"]["status"] == "yellow"
    denv.envvars["OPENCLAW_STATE_DIR"] = str(denv.tmp / "elsewhere")
    row = skills(denv)["agents.skills.openclaw"]
    assert row["status"] == "yellow" and "state folder" in row["summary"]


def test_a_checkout_without_skills_is_red(denv):
    shutil.rmtree(denv.repo_dir / "skills")
    got = skills(denv)
    assert got["agents.skills.claude-code"]["status"] == "red" and "no skills yet" in got["agents.skills.claude-code"][
        "summary"]


def test_agents_that_arent_here_are_green(denv):
    denv.tools.update({"claude": None, "codex": None, "openclaw": None})
    for folder in (".claude", ".codex", ".openclaw"):
        shutil.rmtree(denv.home / folder)
    got = skills(denv)
    assert {r["status"] for r in got.values()} == {"green"}
    assert all("isn't on this Mac" in r["summary"] for r in got.values())


# ---------------------------------------------------------------------------------------------------------------
# MCP, never printing a registered URL


def test_mcp_registered_everywhere_is_green(denv):
    got = mcp(denv)
    assert {r["status"] for r in got.values()} == {"green"}
    assert "connects" in got["agents.mcp.claude-code"]["summary"]


def test_mcp_missing_is_yellow_per_client(denv):
    denv.set_response("claude", "mcp", "get", "slate", code=1,
                      out='No MCP server named "slate". Configured servers: aside, linear')
    denv.set_response("codex", "mcp", "list", "--json", out=json.dumps([{"name": "aside", "enabled": True}]))
    denv.set_response("openclaw", "mcp", "list", "--json", out=json.dumps({"aside": {"command": "node"}}))
    got = mcp(denv)
    for rid in ("agents.mcp.claude-code", "agents.mcp.codex", "agents.mcp.openclaw"):
        assert got[rid]["status"] == "yellow" and "slate connect claude" in got[rid]["fix"]


def test_mcp_that_fails_to_start_is_red(denv):
    denv.set_response("claude", "mcp", "get", "slate",
                      out="slate:\n  Scope: User config\n  Status: ✗ Failed to connect\n  Type: stdio\n")
    assert mcp(denv)["agents.mcp.claude-code"]["status"] == "red"


def test_a_registered_url_or_token_never_reaches_the_report(denv):
    secret_url = "https://mcp.example.test/v1?token=SENTINEL_TOKEN_123"
    denv.set_response("claude", "mcp", "get", "slate",
                      out=f"slate:\n  Scope: {secret_url}\n  Status: ✓ Connected\n  Type: http\n  URL: {secret_url}\n"
                          "  Headers: Authorization: Bearer SENTINEL_BEARER\n")
    denv.set_response("codex", "mcp", "list", "--json", out=json.dumps(
        [{"name": "slate", "enabled": True, "transport": {"type": "streamable_http", "url": secret_url}}]))
    rep = core.report(env=denv)
    text = json.dumps(rep)
    assert "SENTINEL_TOKEN_123" not in text and "SENTINEL_BEARER" not in text and "mcp.example.test" not in text


def test_codex_server_switched_off_is_yellow(denv):
    denv.set_response("codex", "mcp", "list", "--json", out=json.dumps([{"name": "slate", "enabled": False}]))
    assert mcp(denv)["agents.mcp.codex"]["status"] == "yellow"


def test_unreadable_client_lists_are_yellow(denv):
    denv.set_response("openclaw", "mcp", "list", "--json", code=1, err="gateway not running")
    assert mcp(denv)["agents.mcp.openclaw"]["status"] == "yellow"


def test_elevenlabs_local_mcp_is_flagged_wherever_it_is(denv):
    cfg = json.loads((denv.home / ".claude.json").read_text())
    cfg["projects"] = {"/some/project": {"mcpServers": {"eleven": {"type": "stdio", "command": "uvx",
                                                                   "args": ["elevenlabs-mcp"],
                                                                   "env": {"ELEVENLABS_API_KEY": "SENTINEL_ENV"}}}}}
    (denv.home / ".claude.json").write_text(json.dumps(cfg))
    row = mcp(denv)["agents.mcp.elevenlabs-local"]
    assert row["status"] == "yellow" and "Claude Code (project scope" in row["summary"]
    assert "SENTINEL_ENV" not in json.dumps(row)
    assert "outbound" in row["summary"] and "never edits" in row["fix"]


def test_hosted_elevenlabs_by_url_is_not_the_local_package(denv):
    cfg = json.loads((denv.home / ".claude.json").read_text())
    cfg["mcpServers"]["elevenlabs"] = {"type": "http", "url": "https://elevenlabs-mcp.example.test/mcp"}
    (denv.home / ".claude.json").write_text(json.dumps(cfg))
    assert mcp(denv)["agents.mcp.elevenlabs-local"]["status"] == "green"


def test_claude_mcp_list_is_never_run(denv):
    core.checks(env=denv)
    assert not [c for c in denv.calls if c[1:3] == ["mcp", "list"] and c[0].endswith("claude")]
    assert ["mcp", "get", "slate"] in [c[1:4] for c in denv.calls if c[0].endswith("claude")]


# ---------------------------------------------------------------------------------------------------------------
# The CLI on PATH


def cli_row(denv):
    return agents.check_cli_path(denv, ctx(denv))[0]


def test_cli_on_path_in_both_places(denv):
    row = cli_row(denv)
    assert row["status"] == "green" and "/opt/homebrew/bin" in row["summary"]


def test_cli_missing_from_local_bin_is_red(denv):
    (denv.home / ".local" / "bin" / "slate").unlink()
    row = cli_row(denv)
    assert row["status"] == "red" and "connect claude" in row["fix"]


def test_cli_missing_from_homebrew_is_red_only_with_openclaw(denv):
    denv.present.discard("/opt/homebrew/bin/slate")
    denv.texts.pop("/opt/homebrew/bin/slate")
    row = cli_row(denv)
    assert row["status"] == "red" and "OpenClaw gateway" in row["summary"]
    denv.tools["openclaw"] = None
    assert cli_row(denv)["status"] == "green"


def test_a_sandboxed_install_is_checked_where_its_manifest_put_the_gateway_link(denv):
    """A sandbox mirrors /opt/homebrew/bin inside its own home; doctor run there reads the install manifest and checks
    the link that install really made, instead of calling the real /opt/homebrew/bin/slate missing."""
    denv.present.discard("/opt/homebrew/bin/slate")
    denv.texts.pop("/opt/homebrew/bin/slate")
    sandboxed = denv.home / "opt" / "homebrew" / "bin" / "slate"
    sandboxed.parent.mkdir(parents=True)
    sandboxed.write_text((denv.home / ".local" / "bin" / "slate").read_text())
    data = denv.tmp / "sandbox-data"
    (data / "install").mkdir(parents=True)
    (data / "install" / "manifest.json").write_text(json.dumps({"version": 1, "links": [
        {"target": str(denv.home / ".local" / "bin" / "slate"), "kind": "shim"},
        {"target": str(sandboxed), "kind": "shim"}]}))
    denv.data_dir = lambda: data
    row = cli_row(denv)
    assert row["status"] == "green" and row["details"]["gateway_link"] == str(sandboxed), row
    assert "~/opt/homebrew/bin" in row["summary"]
    sandboxed.unlink()
    row = cli_row(denv)
    assert row["status"] == "red" and "isn't in ~/opt/homebrew/bin" in row["summary"]


def test_without_a_manifest_the_gateway_link_is_homebrews(denv):
    assert agents.gateway_link(denv) == agents.GATEWAY_LINK


def test_openclaw_registration_fix_names_the_current_command(denv):
    """OpenClaw 2026.9.4 registers with `mcp add … --command … --arg …`; the older `mcp set` form is never suggested."""
    denv.set_response("openclaw", "mcp", "list", "--json", out=json.dumps({}))
    row = mcp(denv)["agents.mcp.openclaw"]
    assert "openclaw mcp add slate --command" in row["fix"] and "mcp set" not in row["fix"]


def test_cli_from_another_checkout_is_yellow(denv):
    shim = denv.home / ".local" / "bin" / "slate"
    shim.write_text("#!/bin/sh\n# slate-shim v1\nSLATE_CHECKOUT=/somewhere/else/slate\n")
    row = cli_row(denv)
    assert row["status"] == "yellow" and "different Slate checkout" in row["summary"]


def test_cli_link_into_this_checkout_counts_as_ours(denv):
    shim = denv.home / ".local" / "bin" / "slate"
    shim.unlink()
    venv_bin = denv.repo_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "slate").write_text("#!/bin/sh\n")
    shim.symlink_to(venv_bin / "slate")
    assert cli_row(denv)["status"] == "green"


# ---------------------------------------------------------------------------------------------------------------
# ~/.codex/AGENTS.md and Dia

DIA_PARAGRAPH = """# Global instructions

## mac-cua

Operating loop: read the app state, find the element, act by index.
For websites prefer `browser_get_state` then act by `element_id` (CDP on Dia — works on
backgrounded tabs; if unreachable, the user can run `mac-cua-dia`).

Rules: never steal focus; ask before sending anything.
"""

FIXED_PARAGRAPH = """# Global instructions

## Browsers

Web work goes through Aside only. Never use mac-cua's browser tools on Dia: `browser_get_state` is off-limits there.
"""


def dia_row(denv):
    return agents.check_codex_dia(denv, ctx(denv))[0]


def test_dia_flag_is_yellow_and_the_file_is_never_edited(denv):
    target = denv.home / ".codex" / "AGENTS.md"
    target.write_text(DIA_PARAGRAPH)
    before = target.read_bytes()
    row = dia_row(denv)
    assert row["status"] == "yellow" and "line 6" in row["summary"]
    assert "Dia" in row["summary"] and "Aside" in row["fix"] and "never edits" in row["fix"]
    core.checks(env=denv)
    assert target.read_bytes() == before


def test_dia_forbidden_is_green(denv):
    (denv.home / ".codex" / "AGENTS.md").write_text(FIXED_PARAGRAPH)
    assert dia_row(denv)["status"] == "green"


def test_no_codex_instructions_is_green(denv):
    assert dia_row(denv)["status"] == "green"


def test_codex_reads_the_override_first(denv):
    (denv.home / ".codex" / "AGENTS.md").write_text(DIA_PARAGRAPH)
    (denv.home / ".codex" / "AGENTS.override.md").write_text(FIXED_PARAGRAPH)
    assert dia_row(denv)["status"] == "green"


def test_dia_permissions_parser():
    assert agents.dia_permissions(DIA_PARAGRAPH) == [6]
    assert agents.dia_permissions(FIXED_PARAGRAPH) == []
    assert agents.dia_permissions("Dia is my browser.\n\nUse mac-cua for Finder.\n") == []  # no browser-tool mention
    assert agents.dia_permissions("Use mac-cua's browser_click on Dia. Never steal focus.\n") == [1]


# ---------------------------------------------------------------------------------------------------------------
# Slate's own MCP server, and agreement with slate.skills_build


def test_mcp_server_paths(denv):
    row = mcp(denv)["agents.mcp.server"]
    assert row["status"] == "green" and "2 tools listed" in row["summary"]
    denv.mcp_check = {"ok": False, "tools": ["status"], "missing": ["doctor"], "status_answered": True,
                      "stderr": "Traceback …\nImportError: token=SENTINEL_SECRET_IN_STDERR"}
    row = mcp(denv)["agents.mcp.server"]
    assert row["status"] == "red" and "doctor" in row["summary"] and "slate mcp --check" in row["fix"]
    assert "SENTINEL_SECRET_IN_STDERR" not in json.dumps(row)
    denv.mcp_check = ModuleNotFoundError("No module named 'slate.mcp'")
    assert mcp(denv)["agents.mcp.server"]["status"] == "yellow"
    denv.mcp_check = RuntimeError("could not start")
    assert mcp(denv)["agents.mcp.server"]["status"] == "red"


def test_links_to_the_built_plugin_copies_count_as_this_checkout(denv):
    built = denv.repo_dir / "plugins" / "slate" / "skills" / "slate"
    built.mkdir(parents=True)
    (built / "SKILL.md").write_text("---\nname: slate\n---\n")
    link = denv.home / ".agents" / "skills" / "slate"
    link.unlink()
    link.symlink_to(built)
    assert skills(denv)["agents.skills.codex"]["status"] == "green"


def test_doctor_agrees_with_skills_build_installed(denv):
    """The same question packaging's slate.skills_build.installed() answers, on the same home, gets the same answer."""
    from slate import skills_build

    (denv.home / ".codex" / "skills" / "slate").symlink_to(denv.repo_dir / "skills" / "slate")
    roots = {a: [str(denv.home / r) for r in rs] for a, rs in
             {"codex": (".agents/skills", ".codex/skills"), "openclaw": (".agents/skills", ".openclaw/skills"),
              "claude-code": (".claude/skills",)}.items()}
    theirs = {(r["skill"], r["agent"]): r["status"] for r in skills_build.installed(["slate", "slate-story"], roots)}
    ours = skills(denv)
    assert theirs[("slate", "codex")] == "duplicated" and ours["agents.skills.codex"]["status"] == "red"
    assert ours["agents.skills.codex"]["details"]["listed_twice"] == ["slate"]
    assert theirs[("slate-story", "codex")] == "once"
    assert theirs[("slate", "claude-code")] == "once" and ours["agents.skills.claude-code"]["status"] == "green"
    assert theirs[("slate", "openclaw")] == "once" and ours["agents.skills.openclaw"]["status"] == "green"


# ---------------------------------------------------------------------------------------------------------------
# What Codex really shows of each skill (codex debug prompt-input)

FRONT = ("Use for any request to make, edit, plan or review a video. Open asks get one question (full production or a "
         "quick generated clip?). Not for screen-recording an app (video-verifier).")
OLD_FRONT = ("Slate's front door for any video work. Use for any request to make, edit, plan or review a video or film, "
             "including explainers, ads, social reels, promos, launch and product videos, brand films, teasers, logo "
             "stings, kinetic type, character-led pieces, cut-downs. An open request gets one question first (a full "
             "production or a quick generated clip?). Not for screen-recording an app (use video-verifier).")


def _front(denv, description: str, shown: str | None = None) -> dict:
    (denv.repo_dir / "skills" / "slate" / "SKILL.md").write_text(f"---\nname: slate\ndescription: {description}\n---\n")
    denv.responses[("codex", "debug", "prompt-input")] = Ran(0, codex_prompt_input(
        {"slate": description[:220] if shown is None else shown, "slate-story": "test skill"}))
    return by_id(agents.check_codex_visible(denv, ctx(denv)))["agents.codex-visible"]


def test_codex_visible_is_green_when_the_front_doors_routing_fits_what_codex_shows(denv):
    row = _front(denv, FRONT)
    assert row["status"] == "green" and "front door's routing is inside" in row["summary"]
    assert row["details"]["skills"]["slate"]["visible"] == FRONT[:220]
    assert ["codex", "debug", "prompt-input"] == [denv.calls[-1][0].rsplit("/", 1)[-1], *denv.calls[-1][1:]]


def test_codex_visible_is_yellow_when_the_cut_drops_the_question_and_the_negatives(denv):
    row = _front(denv, OLD_FRONT)
    assert row["status"] == "yellow"
    assert "the open-request question" in row["summary"] and "Not-for lines" in row["summary"]
    assert "skills/slate/SKILL.md" in row["fix"] and row["details"]["skills"]["slate"]["cut"] is True


def test_codex_visible_names_missing_skills_and_a_failed_read(denv):
    denv.responses[("codex", "debug", "prompt-input")] = Ran(0, codex_prompt_input({"slate": "test skill"}))
    row = by_id(agents.check_codex_visible(denv, ctx(denv)))["agents.codex-visible"]
    assert row["status"] == "yellow" and row["details"]["missing"] == ["slate-story"]
    denv.responses[("codex", "debug", "prompt-input")] = Ran(2, "", "error: unrecognized subcommand 'debug'")
    row = by_id(agents.check_codex_visible(denv, ctx(denv)))["agents.codex-visible"]
    assert row["status"] == "yellow" and "exited 2" in row["summary"]
    denv.tools["codex"] = None
    row = by_id(agents.check_codex_visible(denv, ctx(denv)))["agents.codex-visible"]
    assert row["status"] == "green" and "isn't on this Mac" in row["summary"]


def test_codex_visible_reads_the_skill_list_out_of_the_prompt_input():
    text = codex_prompt_input({"slate": "Use for any request.", "other": "x"})
    assert agents.codex_visible(text) == {"slate": "Use for any request.", "other": "x"}
