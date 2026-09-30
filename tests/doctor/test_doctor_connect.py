"""`slate connect`: each step safe from an agent. Nothing here types a key, opens a browser or starts After Effects."""

from __future__ import annotations

import json
import sys
import types

import pytest

from slate import secrets
from slate.cli import run_cli
from slate.doctor import connect
from slate.errors import Busy
from slate.gates import GateResult

from doctor_helpers import REAL_STORE_INTERACTIVE, write_brand


def sign_in_attempts(denv):
    return [c for c in denv.calls if "auth" in c and "login" in c]


# ---------------------------------------------------------------------------------------------------------------
# higgsfield


def test_connect_higgsfield_prints_the_sign_in_and_never_runs_it(denv):
    (denv.home / ".config" / "higgsfield" / "credentials.json").unlink()
    code, out, err = run_cli(["connect", "higgsfield"])
    assert code == 3
    assert "higgsfield auth login" in out and "your own Terminal" in out and "Dia" in out
    assert "never runs it" in out
    assert sign_in_attempts(denv) == []


def test_connect_higgsfield_json_names_the_command_it_did_not_run(denv):
    (denv.home / ".config" / "higgsfield" / "credentials.json").unlink()
    code, out, err = run_cli(["connect", "higgsfield", "--json"])
    res = json.loads(out)
    assert res["next"] == "higgsfield auth login" and res["ran_sign_in"] is False
    assert sign_in_attempts(denv) == []


def test_connect_higgsfield_when_signed_in_is_ok(denv):
    code, out, err = run_cli(["connect", "higgsfield"])
    assert code == 0 and "signed in" in out and sign_in_attempts(denv) == []


def test_connect_higgsfield_never_runs_the_sign_in_even_from_a_terminal(denv):
    (denv.home / ".config" / "higgsfield" / "credentials.json").unlink()
    denv.terminal = (True, "")
    code, out, err = run_cli(["connect", "higgsfield"])
    assert code == 3 and "higgsfield auth login" in out and sign_in_attempts(denv) == []


def test_api_key_from_an_agent_is_refused_by_the_real_prompt_guard(denv, monkeypatch):
    """The real slate.secrets.store_interactive refuses in an agent context before anything is asked."""
    monkeypatch.setattr(secrets, "store_interactive", REAL_STORE_INTERACTIVE)
    monkeypatch.setattr(type(denv), "store_key", lambda self, name: secrets.store_interactive(name))
    monkeypatch.setenv("SLATE_CONTEXT", "test-agent")
    code, out, err = run_cli(["connect", "higgsfield", "--api-key"])
    assert code == 1 and "typed by a person" in err and "slate connect higgsfield --api-key" in err


def test_api_key_in_a_terminal_stores_then_probes(denv):
    denv.terminal = (True, "")
    code, out, err = run_cli(["connect", "higgsfield", "--api-key"])
    assert denv.stored == ["higgsfield"]
    assert code == 0 and "stored in your login Keychain" in out and "accepted" in out
    assert "id:secret" in err  # the format is explained at the prompt, on stderr


def test_api_key_rejected_after_storing_exits_1(denv):
    denv.terminal = (True, "")
    denv.hf_probe = {"ok": False, "status": "rejected", "http": 401}
    code, out, err = run_cli(["connect", "higgsfield", "--api-key"])
    assert code == 1 and "rejected" in out


def test_connect_higgsfield_without_the_cli(denv):
    denv.tools["higgsfield"] = None
    code, out, err = run_cli(["connect", "higgsfield"])
    assert code == 3 and "npm install --global @higgsfield/cli@1.1.26" in out


# ---------------------------------------------------------------------------------------------------------------
# elevenlabs


def test_connect_elevenlabs_from_an_agent_with_no_key_exits_3_and_asks_nothing(denv):
    denv.secrets.pop("elevenlabs")
    code, out, err = run_cli(["connect", "elevenlabs"])
    assert code == 3 and "Open Terminal and run: slate connect elevenlabs" in err
    assert denv.stored == []


def test_connect_elevenlabs_oneiro_explains_the_capped_key(denv):
    denv.secrets.pop("elevenlabs_oneiro")
    code, out, err = run_cli(["connect", "elevenlabs", "--oneiro", "--json"])
    res = json.loads(out)
    assert code == 3 and res["hint"] == "Open Terminal and run: slate connect elevenlabs --oneiro"
    how = res["details"]["how"]
    assert "credit quota" in how and "20,000 credits a month" in how
    assert "text to speech, music and alignment" in how
    assert denv.stored == []


def test_connect_elevenlabs_oneiro_in_a_terminal_stores_it_without_a_plan_probe(denv):
    denv.terminal = (True, "")
    code, out, err = run_cli(["connect", "elevenlabs", "--oneiro"])
    assert code == 0 and denv.stored == ["elevenlabs_oneiro"]
    assert "credit quota" in err  # the guide goes to the person at the prompt
    assert denv.eleven_calls == 0  # its narrow permissions can't read the plan


def test_connect_elevenlabs_in_a_terminal_stores_then_reads_the_plan(denv):
    denv.terminal = (True, "")
    denv.eleven = {"tier": "creator", "replay": False}
    code, out, err = run_cli(["connect", "elevenlabs"])
    assert code == 0 and denv.stored == ["elevenlabs"] and "Creator" in out


def test_connect_elevenlabs_check_reports_without_asking(denv):
    code, out, err = run_cli(["connect", "elevenlabs", "--check", "--json"])
    res = json.loads(out)
    assert code == 0 and res["where"] == "keychain" and res["plan"]["status"] == "green"
    assert denv.stored == []


def test_a_failed_store_is_an_error(denv):
    denv.terminal = (True, "")
    denv.store_rc = 45
    code, out, err = run_cli(["connect", "elevenlabs"])
    assert code == 1 and "wasn't stored" in err


def test_connect_never_prints_a_key(denv, monkeypatch):
    from slate.doctor.env import Env

    sentinel = "sk_test_SENTINEL_CONNECT_42"
    monkeypatch.setenv("ELEVENLABS_API_KEY", sentinel)
    monkeypatch.setattr(type(denv), "secret_where", Env.secret_where)
    monkeypatch.setattr(type(denv), "eleven_subscription", Env.eleven_subscription)
    for argv in (["connect", "elevenlabs", "--check"], ["connect", "elevenlabs", "--check", "--json"]):
        code, out, err = run_cli(argv)
        assert code == 0 and sentinel not in out and sentinel not in err


# ---------------------------------------------------------------------------------------------------------------
# claude


def fake_installer(monkeypatch, calls, ok=True):
    def wire(agents, *, home=None, dry_run=False):
        calls.append({"agents": list(agents), "dry_run": dry_run})
        return {"ok": ok, "failures": [] if ok else ["x"], "next_steps": [], "links": {"counts": {"create": 2}},
                "mcp": [{"client": "claude", "state": "add", "did": "added"}]}

    monkeypatch.setattr(connect, "installer", lambda: wire)


def test_connect_claude_without_the_installer_prints_the_steps(denv, monkeypatch):
    monkeypatch.setattr(connect, "installer", lambda: None)
    code, out, err = run_cli(["connect", "claude"])
    assert code == 3 and "claude mcp add --scope user slate" in out and "~/.claude/skills/slate" in out
    assert "~/.agents/skills/slate" in out  # Codex and OpenClaw are on this fake Mac too
    assert ".codex/skills" not in out


def test_connect_claude_from_an_agent_waits_for_a_yes(denv, monkeypatch):
    calls = []
    fake_installer(monkeypatch, calls)
    code, out, err = run_cli(["connect", "claude", "--json"])
    res = json.loads(out)
    assert code == 3 and calls == [] and "--yes" in res["next"]


def test_connect_claude_with_yes_uses_the_installer(denv, monkeypatch):
    calls = []
    fake_installer(monkeypatch, calls)
    code, out, err = run_cli(["connect", "claude", "--yes"])
    assert code == 0 and calls == [{"agents": ["claude", "codex", "openclaw"], "dry_run": False}]
    assert "Connected Slate to Claude Code and Codex and OpenClaw" in out


def test_connect_claude_in_a_terminal_is_the_yes(denv, monkeypatch):
    calls = []
    fake_installer(monkeypatch, calls)
    denv.terminal = (True, "")
    denv.tools.update({"codex": None, "openclaw": None})
    import shutil

    shutil.rmtree(denv.home / ".codex")
    shutil.rmtree(denv.home / ".openclaw")
    code, out, err = run_cli(["connect", "claude"])
    assert code == 0 and calls == [{"agents": ["claude"], "dry_run": False}]


def test_connect_claude_dry_run_changes_nothing(denv, monkeypatch):
    calls = []
    fake_installer(monkeypatch, calls)
    code, out, err = run_cli(["connect", "claude", "--dry-run"])
    assert code == 0 and calls == [{"agents": ["claude", "codex", "openclaw"], "dry_run": True}]


def test_connect_claude_failure_exits_1(denv, monkeypatch):
    calls = []
    fake_installer(monkeypatch, calls, ok=False)
    code, out, err = run_cli(["connect", "claude", "--yes"])
    assert code == 1


def test_connect_claude_without_claude_code(denv):
    denv.tools["claude"] = None
    code, out, err = run_cli(["connect", "claude"])
    assert code == 3 and "Claude Code isn't installed" in err


def test_the_real_installer_hook_is_found_when_present(monkeypatch):
    mod = types.ModuleType("slate.install.core")
    mod.wire = lambda agents, **kw: {"ok": True}
    monkeypatch.setitem(sys.modules, "slate.install.core", mod)
    pkg = sys.modules.get("slate.install") or types.ModuleType("slate.install")
    monkeypatch.setitem(sys.modules, "slate.install", pkg)
    monkeypatch.setattr(pkg, "core", mod, raising=False)
    assert connect.installer() is mod.wire


# ---------------------------------------------------------------------------------------------------------------
# aftereffects


def test_connect_aftereffects_without_ae(denv):
    denv.ae_list = []
    code, out, err = run_cli(["connect", "aftereffects"])
    assert code == 3 and "Creative Cloud" in err


def test_connect_aftereffects_from_an_agent_without_go_starts_nothing(denv):
    denv.bridge = {"installed": False, "pinned": "0.1.3"}
    (denv.home / ".claude.json").write_text('{"mcpServers": {}}')
    code, out, err = run_cli(["connect", "aftereffects", "--json"])
    res = json.loads(out)
    assert code == 1
    assert denv.bridge_installs == 0 and denv.smoke_runs == []
    assert not [c for c in denv.calls if c[1:3] == ["mcp", "add"]]
    steps = {s["step"]: s for s in res["steps"]}
    assert steps["1-second smoke test"]["status"] == "waiting"
    assert steps["install the AE bridge"]["status"] == "waiting"
    assert "--go" in res["next"]


def test_connect_aftereffects_with_go_installs_registers_and_runs_the_smoke_test(denv):
    denv.bridge = {"installed": False, "pinned": "0.1.3", "where": "/opt/homebrew/lib/node_modules/fnf-after-effects-mcp"}
    (denv.home / ".claude.json").write_text('{"mcpServers": {}}')
    denv.set_response("claude", "mcp", out="Added stdio MCP server higgsfield-use-after-effects to user config")
    code, out, err = run_cli(["connect", "aftereffects", "--go"])
    assert code == 0 and "After Effects is connected" in out
    assert denv.bridge_installs == 1 and denv.smoke_runs == [None]
    add = [c for c in denv.calls if c[1:3] == ["mcp", "add"]][0]
    assert add[3:6] == ["--scope", "user", "higgsfield-use-after-effects"]
    assert add[-1].endswith("fnf-after-effects-mcp/dist/index.js")


def test_a_person_in_a_terminal_is_the_go(denv):
    denv.terminal = (True, "")
    code, out, err = run_cli(["connect", "aftereffects"])
    assert code == 0 and denv.smoke_runs == [None] and denv.bridge_installs == 0  # already at the pin


def test_connect_aftereffects_reports_where_the_bridge_is_registered(denv):
    code, out, err = run_cli(["connect", "aftereffects", "--go", "--json"])
    res = json.loads(out)
    how = [s for s in res["steps"] if s["step"] == "the AE bridge: how it's installed"][0]
    assert "registered with Claude Code" in how["summary"] and "fnf-after-effects-mcp" in how["summary"]


def test_a_failed_smoke_test_exits_1(denv):
    denv.smoke_result = GateResult.fail("ae.smoke", "aerender made 12 frames of 30")
    code, out, err = run_cli(["connect", "aftereffects", "--go"])
    assert code == 1 and "12 frames" in out


def test_ae_busy_exits_5(denv):
    denv.smoke_result = Busy("After Effects is open and busy.")
    code, out, err = run_cli(["connect", "aftereffects", "--go"])
    assert code == 5


def test_autonomous_runs_never_touch_ae(denv, monkeypatch):
    monkeypatch.setenv("SLATE_MODE", "autonomous")
    code, out, err = run_cli(["connect", "aftereffects", "--go"])
    assert code == 1 and denv.smoke_runs == [] and "autonomous" in out.lower()


def test_dry_run_starts_nothing(denv):
    denv.bridge = {"installed": False, "pinned": "0.1.3"}
    code, out, err = run_cli(["connect", "aftereffects", "--go", "--dry-run"])
    assert code == 0 and denv.bridge_installs == 0 and denv.smoke_runs == [] and "Dry run" in out


def test_brand_fonts_are_checked_on_the_mac_and_inside_ae(denv, slate_home):
    write_brand(slate_home["brands"], "testbrand", [{"family": "Helvetica Neue", "source": "installed"},
                                                    {"family": "Space Test Grotesk", "source": "google"}])
    denv.font_check_result = {"missing": ["Space Test Grotesk"]}
    code, out, err = run_cli(["connect", "aftereffects", "--go", "--brand", "testbrand", "--json"])
    res = json.loads(out)
    assert denv.font_checks == [["Helvetica Neue", "Space Test Grotesk"]]
    steps = {s["step"]: s for s in res["steps"]}
    assert "Space Test Grotesk" in steps["brand fonts on this Mac"]["summary"]
    assert steps["brand fonts in After Effects"]["status"] == "failed"
    assert code == 1


def test_a_failed_bridge_registration_is_reported_with_the_command(denv):
    (denv.home / ".claude.json").write_text('{"mcpServers": {}}')
    denv.set_response("claude", "mcp", code=1, err="permission denied")
    code, out, err = run_cli(["connect", "aftereffects", "--go", "--json"])
    res = json.loads(out)
    step = [s for s in res["steps"] if s["step"] == "register the AE bridge"][0]
    assert code == 1 and step["status"] == "failed" and "claude mcp add --scope user" in step["fix"]
