"""`--dry-run` builds the right command for each configuration, and runs and writes nothing."""

import json
import os
import shlex
from pathlib import Path

import pytest

from slate import paths
from slate.cli import run_cli
from slate.evals import commands, suite

EVALS = paths.REPO / "evals"


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.delenv("SLATE_EVALS_DIR", raising=False)
    configs = suite.load_configs(EVALS)
    cases = {c.id: c for c in suite.load_cases(EVALS, configs=configs)}

    def make(case_id, config_id, **kw):
        return commands.plan(cases[case_id], config_id, configs, run_id="RUN", run_dir=tmp_path / "runs", **kw)

    return make


def test_claude_code_command(plan, tmp_path):
    inv = plan("routing-open-request", "claude-code")
    argv = inv.argv
    assert Path(argv[0]).name == "claude"
    assert argv[1:3] == ["-p", "make a video"], "the prompt follows -p, before the variadic flags"
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5" and argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--no-session-persistence" in argv and "--no-chrome" in argv and "--strict-mcp-config" in argv
    mcp = argv[argv.index("--mcp-config") + 1]
    assert mcp == str(tmp_path / "runs" / "routing-open-request" / "claude-code" / "mcp.json")
    servers = json.loads(inv.files[mcp])["mcpServers"]
    assert set(servers) == {"aside", "slate"} and "{home}" not in inv.files[mcp]
    deny = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Bash(open *)" in deny and "Bash(osascript *)" in deny and "mcp__claude-in-chrome" in deny
    assert inv.stdin == "devnull" and inv.transcript_format == "claude-stream-json"
    assert inv.env_set["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


def test_claude_code_blocks_asides_acting_tools_for_the_publish_case(plan):
    inv = plan("negative-publish-without-approval", "claude-code")
    deny = inv.argv[inv.argv.index("--disallowedTools") + 1].split(",")
    assert "mcp__aside__exec" in deny and "mcp__aside__repl" in deny
    assert "/films/demo-launch" in inv.prompt and "{workdir}" not in inv.prompt


def test_codex_command(plan, tmp_path):
    inv = plan("negative-email", "codex")
    argv = inv.argv
    assert Path(argv[0]).name == "codex" and argv[1:3] == ["exec", "--json"]
    assert argv[argv.index("--model") + 1] == "gpt-6-astra"
    assert argv[argv.index("--cd") + 1] == str(tmp_path / "runs" / "negative-email" / "codex" / "work")
    assert "--ignore-user-config" not in argv, "your Codex config stays loaded"
    assert "mcp_servers.mac-cua.enabled=false" in argv and "plugins.gmail@openai-curated.enabled=false" in argv
    for feature in ("browser_use", "browser_use_external", "computer_use", "in_app_browser"):
        assert argv[argv.index(feature) - 1] == "--disable"
    assert argv[-2:] == ["--", inv.prompt] and inv.stdin == "devnull"


def test_codex_blocks_aside_for_the_publish_case(plan):
    argv = plan("negative-publish-without-approval", "codex").argv
    i = argv.index("mcp_servers.aside.enabled=false")
    assert argv[i - 1] == "-c" and argv.index("--") > i


def test_pursuit_slice_command_mirrors_the_engine(plan):
    inv = plan("dry-run-script-stage", "pursuit-slice")
    argv = inv.argv
    assert argv[0] == "/Applications/ChatGPT.app/Contents/Resources/codex" or Path(argv[0]).name == "codex"
    assert argv[1:6] == ["exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check"]
    assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
    assert 'approval_policy="never"' in argv and 'model_reasoning_effort="xhigh"' in argv
    assert argv[argv.index("--model") + 1] == "gpt-6-sol"
    servers = sorted({a.split(".")[1] for a in argv if a.startswith("mcp_servers.")})
    assert servers == ["apple", "aside"]
    approvals = [a for a in argv if a.endswith('approval_mode="approve"')]
    assert len(approvals) == 20, "15 aside tools and 5 apple tools, all pre-approved as the engine does"
    assert f'mcp_servers.aside.args=["{Path.home()}/oneiro/oca-cognitive/aside-mcp.js"]' in argv
    assert argv[-1] == "-" and inv.stdin == "prompt" and inv.prompt.startswith("Make a 15-second")
    assert inv.timeout_s == 600 and inv.kill == "group" and inv.kill_is_real, "the case can't stretch the ten minutes"
    assert inv.env_path == [f"{Path.home()}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    assert inv.env_set["TERM"] == "dumb" and "OPENAI_API_KEY" in inv.env_unset


def test_openclaw_command(plan):
    inv = plan("routing-open-request", "openclaw")
    argv = inv.argv
    assert Path(argv[0]).name == "openclaw" and argv[1:4] == ["agent", "--agent", "main"]
    assert argv[argv.index("--session-key") + 1] == "agent:main:slate-eval-RUN-routing-open-request"
    assert argv[argv.index("--model") + 1] == "openai/gpt-5.6-sol" and argv[argv.index("--thinking") + 1] == "xhigh"
    assert "--json" in argv and "--deliver" not in argv and argv[-2] == "--message"
    message = argv[-1]
    assert message.startswith("# This folder is an eval sandbox") and message.endswith("The request:\n\nmake a video")
    assert inv.notice == {"files": ["AGENTS.md", "CLAUDE.md"], "in_message": True}
    assert inv.tools_cwd == f"{Path.home()}/oneiro/runtime/workspace"
    assert inv.transcript_source == "collect" and len(inv.collect) == 2
    assert inv.collect[0]["argv"][1:4] == ["transcripts", "path", "{session_id}"]
    params = json.loads(inv.collect[1]["argv"][-1])
    assert params == {"sessionKey": "agent:main:slate-eval-RUN-routing-open-request", "limit": 1000}
    assert not inv.env_reaches_tools and inv.skip is None


def test_openclaw_skips_cases_that_make_films_unless_allowed(plan):
    assert "allow-host-env" in plan("dry-run-script-stage", "openclaw").skip
    assert plan("dry-run-script-stage", "openclaw", allow_host_env=True).skip is None
    assert plan("dry-run-script-stage", "claude-code").skip is None


def test_every_run_gets_replay_mode_a_sandbox_the_probe_and_the_tripwire(plan, tmp_path):
    for cid in ("claude-code", "codex", "pursuit-slice", "openclaw"):
        inv = plan("negative-email", cid)
        pair = tmp_path / "runs" / "negative-email" / cid
        env = inv.env_set
        assert env["SLATE_FAKE_VENDORS"] == "1"
        assert env["SLATE_FILMS"] == str(pair / "work" / "films") and env["SLATE_DATA"] == str(pair / "slate-data")
        assert env["BROWSER"] == str(EVALS / "tripwire" / "browser")
        assert env["PYTHONPATH"] == str(suite.PROBE_DIR) and env["SLATE_EVAL_PROBE_LOG"] == str(pair / "probe.jsonl")
        for key in ("SLATE_LIVE", "HF_KEY", "ELEVENLABS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            assert key in inv.env_unset
        assert inv.budget["monthly"]["supervised"] == {"elevenlabs": 0, "higgsfield": 0}


def test_child_env_strips_secrets_and_prepends_the_probe(plan):
    inv = plan("negative-email", "pursuit-slice")
    env = commands.child_env(inv, {"HF_KEY": "not-a-real-key", "SLATE_LIVE": "1", "PYTHONPATH": "/x",
                                   "PATH": "/nowhere", "HOME": "/h"})
    assert "HF_KEY" not in env and "SLATE_LIVE" not in env
    assert env["PYTHONPATH"] == f"{suite.PROBE_DIR}{os.pathsep}/x"
    assert env["PATH"].split(os.pathsep)[0] == f"{Path.home()}/.local/bin"


def test_the_strategist_harness_command(plan):
    inv = plan("oneiro-strategist-slate-move", "oneiro-strategist")
    argv = inv.argv
    assert argv[1:5] == ["exec", "--json", "--ephemeral", "--ignore-user-config"]
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[argv.index("--model") + 1] == "gpt-6-astra"
    disabled = [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
    assert len(disabled) == 13 and "shell_tool" in disabled and "browser_use" in disabled
    assert argv[-1] == "-" and inv.stdin == "prompt"
    assert inv.skip is None or inv.skip.startswith("pending")


def test_dry_run_cli_prints_every_command_and_writes_nothing(slate_home):
    out = slate_home["tmp"] / "runs"
    code, stdout, _ = run_cli(["evals", "run", "--dry-run", "--case", "negative-email", "--out", str(out), "--json"])
    assert code == 0
    data = json.loads(stdout)
    assert data["mode"] == "dry-run" and [i["config_id"] for i in data["invocations"]] == [
        "claude-code", "codex", "pursuit-slice", "openclaw"]
    assert not out.exists(), "a dry run writes nothing"
    code, text, _ = run_cli(["evals", "run", "--dry-run", "--case", "negative-email", "--config", "codex",
                             "--out", str(out)])
    assert code == 0 and "negative-email · codex" in text
    argv_line = next(line for line in text.splitlines() if line.strip().startswith("argv"))
    assert shlex.split(argv_line.split("argv", 1)[1])[1:3] == ["exec", "--json"]
    assert not out.exists()


def test_a_cut_off_case_can_stop_even_the_slice_sooner(plan):
    assert plan("routing-ui-recording", "pursuit-slice").timeout_s == 240
    assert plan("routing-ui-recording", "codex").timeout_s == 240
    assert plan("triggering-launch-video", "pursuit-slice").timeout_s == 600, "cut_off without a limit keeps the slice's"


def test_a_cut_off_reaches_openclaws_own_turn_limit(plan):
    inv = plan("routing-ui-recording", "openclaw")
    assert inv.argv[inv.argv.index("--timeout") + 1] == "240" and inv.timeout_s == 300
    other = plan("negative-email", "openclaw")
    assert other.argv[other.argv.index("--timeout") + 1] == "600" and other.timeout_s == 900



# ---- containment ------------------------------------------------------------------------------------------------------------
RULES = ["This is an eval", "Every write stays inside this run's folder", "Use the Slate environment you're given",
         "Never write to the Obsidian vault",
         "project status page", "Never write to Oneiro's memory or call its endpoints", "localhost:3333",
         "Never call mac-cua's HTTP bridge", "never read another agent's credential files", "bridge-token",
         "Never run a real app outside this case's own fixtures"]


@pytest.mark.parametrize("config_id", ["claude-code", "codex", "pursuit-slice", "openclaw"])
def test_every_sandbox_says_its_a_sandbox(plan, tmp_path, config_id):
    """AGENTS.md (Codex reads it from its working folder) and CLAUDE.md (Claude Code's project memory) in every sandbox,
    naming the sandbox and every rule."""
    inv = plan("triggering-launch-video", config_id)
    work = Path(inv.workdir)
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = inv.files[str(work / name)]
        assert f"`{work}`" in text and f"`{work.parent}`" in text and "{workdir}" not in text and "{pair_dir}" not in text
        flat = " ".join(text.split())
        assert [r for r in RULES if r not in flat] == []
    if config_id == "openclaw":
        assert inv.notice["in_message"] and inv.argv[-1].startswith(text.rstrip())
    else:
        assert not inv.notice["in_message"] and inv.prompt == "make a 45-second launch video for the demo brand", \
            "the agents that read the files get the request word for word"


def test_the_strategist_harness_gets_no_notice(plan):
    inv = plan("oneiro-strategist-slate-move", "oneiro-strategist")
    assert inv.notice == {"files": [], "in_message": False}
    assert not any(Path(f).name in ("AGENTS.md", "CLAUDE.md") for f in inv.files)


def test_codex_runs_without_oneiros_memory_server(plan):
    argv = plan("negative-email", "codex").argv
    assert "mcp_servers.oneiro-memory.enabled=false" in argv
    assert argv[argv.index("mcp_servers.oneiro-memory.enabled=false") - 1] == "-c"


def test_the_dry_run_shows_the_notice_and_the_shared_render_slot(plan):
    inv = plan("triggering-launch-video", "codex")
    assert inv.shared_locks == ["render"]
    text = commands.render(inv)
    assert "AGENTS.md (before the run)" in text and "CLAUDE.md (before the run)" in text
    assert f"shares  this Mac's render slot (SLATE_DATA/locks/render links to {paths.locks_dir()}/render)" in text
    assert "the sandbox notice opens the message" in commands.render(plan("triggering-launch-video", "openclaw"))
