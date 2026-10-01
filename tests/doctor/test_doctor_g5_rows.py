"""Doctor's newer rows: After Effects' launch dialogs (L-ae-048), one copy of each MCP server per client
(L-agents-001), both Claude Code builds against the minimum (L-packaging-017), .env files where Slate works
(L-packaging-052), the OpenClaw gateway's egress proxy explained with both fixes, and the render-stability
conformance result."""

from __future__ import annotations

import json

import pytest

from slate.doctor import aftereffects, agents, core, egress, keys, system, tools
from slate.errors import SlateError

from doctor_helpers import by_id


def ae_rows(denv):
    return by_id(aftereffects.check_ae(denv, core.make_ctx(denv)))


# ---------------------------------------------------------------------------------------------------- AE dialogs
@pytest.mark.parametrize("title,kind", [
    ("Sign In Required", "sign_in"), ("Adobe ID", "sign_in"), ("Your trial has ended", "sign_in"),
    ("Home", "home"),
    ("After Effects quit unexpectedly: restore the project?", "crash_recovery"), ("Auto-Save Recovery", "crash_recovery"),
    ("Missing Fonts", "missing_fonts"),
    ("Missing Footage", "missing_footage"), ("1 file could not be found", "missing_footage"),
])
def test_each_launch_dialog_is_a_red_row_naming_it(denv, title, kind):
    denv.ae_is_running = True
    denv.ae_window_titles = ["Adobe After Effects 2099 - /Users/test/film.aep", title]
    got = ae_rows(denv)
    row = got[f"ae.dialogs.{kind}"]
    assert row["status"] == "red" and title[:40] in row["summary"]
    assert aftereffects.DIALOGS[kind]["name"] in row["summary"] and row["fix"]
    assert "ae.dialogs" not in got  # the dialog rows stand in for the all-clear


def test_no_dialog_open_or_ae_closed_is_green(denv):
    assert ae_rows(denv)["ae.dialogs"]["status"] == "green"  # AE closed: nothing asked, nothing launched
    denv.ae_is_running = True
    denv.ae_window_titles = ["Adobe After Effects 2099 - Untitled Project.aep", "Composition: Act 1", "Timeline"]
    assert ae_rows(denv)["ae.dialogs"]["status"] == "green"


def test_unreadable_windows_are_yellow_never_green(denv):
    denv.ae_is_running = True
    denv.ae_window_titles = None
    row = ae_rows(denv)["ae.dialogs"]
    assert row["status"] == "yellow" and "Accessibility" in row["fix"]


def test_the_bridge_can_report_a_dialog_too(denv):
    denv.bridge = {**denv.bridge, "open_dialogs": [{"kind": "crash_recovery", "title": "Restore project?"}]}
    row = ae_rows(denv)["ae.dialogs.crash_recovery"]
    assert row["status"] == "red" and row["details"]["from"] == "the AE bridge"


def test_launch_dialogs_is_the_check_a_supervised_step_runs_first(denv):
    denv.ae_is_running = True
    denv.ae_window_titles = ["Missing Fonts"]
    st = aftereffects.launch_dialogs(denv)
    assert st["running"] and st["readable"] and [d["kind"] for d in st["dialogs"]] == ["missing_fonts"]


# ---------------------------------------------------------------------------------------------------- MCP copies
def mcp_single(denv):
    return by_id(agents.check_mcp_single(denv, core.make_ctx(denv)))


def test_one_copy_per_client_is_green(denv):
    row = mcp_single(denv)["agents.mcp.single-instance"]
    assert row["status"] == "green" and "2 running across 2 clients" in row["summary"]


def test_two_copies_of_one_server_under_one_client_are_red(denv):
    shim = denv.home / ".local" / "bin" / "slate"
    denv.process_list = [{"pid": 501, "ppid": 500, "command": f"{shim} mcp"},
                         {"pid": 502, "ppid": 500, "command": "/usr/bin/python3 -m slate mcp"}]
    got = mcp_single(denv)
    row = got["agents.mcp.single-instance.slate"]
    assert row["status"] == "red" and "2 copies of the slate MCP server" in row["summary"]
    assert "twice" in row["summary"] and row["details"]["client_pid"] == 500
    assert str(shim) not in json.dumps(row)  # a launch command can carry secrets: it's never in a report


def test_a_node_server_registered_twice_is_caught_and_other_processes_arent_counted(denv):
    bridge = "/opt/homebrew/lib/node_modules/fnf-after-effects-mcp/dist/index.js"
    denv.process_list = [{"pid": 701, "ppid": 700, "command": f"/opt/homebrew/bin/node {bridge}"},
                         {"pid": 702, "ppid": 700, "command": f"node {bridge}"},
                         {"pid": 703, "ppid": 700, "command": "node some-other-tool.js"},
                         {"pid": 704, "ppid": 700, "command": "aside mcp"}]
    got = mcp_single(denv)
    assert got["agents.mcp.single-instance.higgsfield-use-after-effects"]["status"] == "red"
    assert len(got) == 1


def test_an_unreadable_process_list_is_yellow(denv):
    denv.process_list = None
    assert mcp_single(denv)["agents.mcp.single-instance"]["status"] == "yellow"


# ---------------------------------------------------------------------------------------------------- Claude Code
def version_row(denv):
    return by_id(agents.check_claude_version(denv, core.make_ctx(denv)))["agents.claude-code-version"]


def test_both_builds_at_or_above_the_minimum_are_green(denv):
    row = version_row(denv)
    assert row["status"] == "green" and "2.1.282 (terminal)" in row["summary"]
    assert "2.1.284 (desktop app)" in row["summary"] and agents.MIN_CLAUDE_CODE in row["summary"]


def test_either_build_below_the_minimum_is_yellow(denv):
    denv.set_response("claude", "--version", out="1.0.44 (Claude Code)\n")
    row = version_row(denv)
    assert row["status"] == "yellow" and "terminal 1.0.44" in row["summary"] and "claude update" in row["fix"]
    denv.set_response("claude", "--version", out="2.1.282 (Claude Code)\n")
    old = denv.home / "Library" / "Application Support" / "Claude" / "claude-code"
    for d in old.iterdir():
        d.rename(old / "1.9.0")
    row = version_row(denv)
    assert row["status"] == "yellow" and "desktop app 1.9.0" in row["summary"]


def test_no_claude_code_here_is_green_and_an_unreadable_version_is_yellow(denv):
    denv.tools["claude"] = None
    root = denv.home / "Library" / "Application Support" / "Claude" / "claude-code"
    for d in root.iterdir():
        (d / "claude.app").rmdir()
    assert version_row(denv)["status"] == "green"
    denv.tools["claude"] = str(denv.home / ".local" / "bin" / "claude")
    denv.set_response("claude", "--version", code=1, err="boom")
    assert version_row(denv)["status"] == "yellow"


# ---------------------------------------------------------------------------------------------------- .env files
def key_rows(denv):
    return by_id(keys.check_keys(denv, core.make_ctx(denv)))


def test_a_dotenv_in_a_film_folder_or_the_checkout_is_yellow(denv):
    films = denv.tmp / "films"
    films.mkdir()
    denv.films_dir = lambda: films  # the fake Mac's films folder (doctor reads only inside its temp tree)
    assert key_rows(denv)["keys.dotenv"]["status"] == "green"
    film = films / "f1"
    film.mkdir()
    (film / ".env").write_text("ELEVENLABS_API_KEY=not-a-real-key\n")
    row = key_rows(denv)["keys.dotenv"]
    assert row["status"] == "yellow" and "f1/.env" in row["summary"] and "Keychain" in row["fix"]
    assert "not-a-real-key" not in json.dumps(row)  # never read
    (denv.repo_dir / ".env").write_text("X=1\n")
    assert len(key_rows(denv)["keys.dotenv"]["details"]["files"]) == 2


# ---------------------------------------------------------------------------------------------------- egress proxy
TUNNEL = "Couldn't reach api.elevenlabs.io: <urlopen error Tunnel connection failed: 403 Forbidden>"


def test_in_the_gateway_a_blocked_vendor_names_the_proxy_and_both_fixes(denv):
    denv.agent = "openclaw"
    denv.eleven = SlateError(TUNNEL)
    denv.hf_probe = {"ok": False, "status": "unreachable", "http": None, "replay": False,
                     "summary": "Couldn't reach api.higgsfield.ai: <urlopen error Tunnel connection failed: 403 Forbidden>"}
    got = key_rows(denv)
    for rid, host in (("vendor.elevenlabs", "api.elevenlabs.io"), ("key.higgsfield", "api.higgsfield.ai")):
        row = got[rid]
        assert row["status"] == "yellow" and row["details"]["egress_proxy"] is True
        assert "~/.openclaw/openclaw.json secrets.egressProxy.allowedHosts" in row["summary"] and host in row["summary"]
        assert "403 Forbidden" in row["summary"] and "Check the network" not in row["fix"]
        assert "job queue" in row["fix"] and "dev.slate.jobs" in row["fix"] and "--detach" in row["fix"]
        assert "secrets.egressProxy.allowedHosts" in row["fix"] and "never edits that file" in row["fix"]


def test_outside_the_gateway_a_timeout_is_still_the_network(denv):
    denv.eleven = SlateError("Couldn't reach api.elevenlabs.io: timed out")
    row = key_rows(denv)["vendor.elevenlabs"]
    assert row["status"] == "yellow" and "Check the network" in row["fix"]
    denv.eleven = SlateError(TUNNEL)  # a proxy's 403 reads as the proxy anywhere
    assert "egressProxy" in key_rows(denv)["vendor.elevenlabs"]["summary"]


def test_the_higgsfield_cli_row_explains_the_proxy_too(denv):
    denv.agent = "openclaw"
    denv.is_replay = False
    denv.set_response("higgsfield", "account", "status", "--json", code=1,
                      err="Error: request failed (no response received)")
    ctx = core.make_ctx(denv)
    row = by_id(tools.check_higgsfield_cli(denv, ctx))["tool.higgsfield-account"]
    assert row["status"] == "yellow" and "egress proxy" in row["summary"] and "job queue" in row["fix"]


def _gateway_config(denv, hosts, enabled=True):
    (denv.home / ".openclaw" / "openclaw.json").write_text(json.dumps(
        {"secrets": {"egressProxy": {"enabled": enabled, "allowedHosts": hosts}, "providers": {"x": "not read"}}}))


def test_the_egress_row_reads_what_the_proxy_lets_through(denv):
    row = by_id(egress.check_egress(denv, core.make_ctx(denv)))["net.egress-proxy"]
    assert row["status"] == "green" and "reached directly" in row["summary"]  # not in the gateway
    denv.agent = "openclaw"
    _gateway_config(denv, ["pypi.org", "huggingface.co"])
    row = by_id(egress.check_egress(denv, core.make_ctx(denv)))["net.egress-proxy"]
    assert row["status"] == "yellow" and "api.elevenlabs.io, api.higgsfield.ai" in row["summary"]
    assert "job queue" in row["fix"] and "~/.openclaw/openclaw.json" in row["fix"]
    assert "not read" not in json.dumps(row)  # only the proxy's own two settings are read
    _gateway_config(denv, ["api.elevenlabs.io", "api.higgsfield.ai"])
    assert by_id(egress.check_egress(denv, core.make_ctx(denv)))["net.egress-proxy"]["status"] == "green"
    _gateway_config(denv, [], enabled=False)
    assert by_id(egress.check_egress(denv, core.make_ctx(denv)))["net.egress-proxy"]["status"] == "green"


def test_the_routes_own_refusal_leads_the_fix(denv):
    """A vendor client that already refused with the gateway route's EgressBlocked hands doctor the exact queue
    command; doctor shows it, and still names the allowlist."""
    from slate.vendors.egress import EgressBlocked

    denv.agent = "openclaw"
    hint = ("Run it through Slate's job queue, whose launchd runner works outside the gateway: slate job submit "
            "--kind voice --resource elevenlabs_tts -- slate voice take S01")
    denv.eleven = EgressBlocked("The OpenClaw gateway's egress proxy doesn't let api.elevenlabs.io through.",
                                hint=hint, details={"egress_proxy": True, "host": "api.elevenlabs.io"})
    row = key_rows(denv)["vendor.elevenlabs"]
    assert row["status"] == "yellow" and row["fix"].startswith(hint)
    assert "secrets.egressProxy.allowedHosts" in row["fix"] and "Check the network" not in row["fix"]
    assert "dev.slate.jobs) is loaded" in row["fix"]  # what doctor adds: the runner's state on this Mac
    denv.hf_probe = {"ok": False, "status": "unreachable", "http": None, "replay": False,
                     "summary": "The OpenClaw gateway's egress proxy doesn't let api.higgsfield.ai through.",
                     "hint": "slate job submit --kind gen --resource higgsfield -- slate gen run --shot S02",
                     "egress": {"egress_proxy": True, "host": "api.higgsfield.ai"}}
    row = key_rows(denv)["key.higgsfield"]
    assert row["fix"].startswith("slate job submit --kind gen") and "allowedHosts" in row["fix"]


def test_the_fix_says_whether_the_queues_runner_is_loaded(denv):
    denv.agent = "openclaw"
    denv.eleven = SlateError(TUNNEL)
    assert "The runner is loaded" in key_rows(denv)["vendor.elevenlabs"]["fix"]
    denv.jobs = {**denv.jobs, "loaded": False}
    fix = key_rows(denv)["vendor.elevenlabs"]["fix"]
    assert "isn't loaded yet" in fix and "slate jobs agent install" in fix


def test_the_egress_row_reads_the_routes_config_and_its_wildcards(denv):
    denv.agent = "openclaw"
    other = denv.tmp / "elsewhere.json"
    other.write_text(json.dumps({"secrets": {"egressProxy": {"allowedHosts": ["*.elevenlabs.io", ".higgsfield.ai"]}}}))
    denv.envvars["SLATE_OPENCLAW_CONFIG"] = str(other)  # the route reads the same file
    _gateway_config(denv, [])  # the default file says nothing gets through, and isn't the one read
    row = by_id(egress.check_egress(denv, core.make_ctx(denv)))["net.egress-proxy"]
    assert row["status"] == "green", row


# ---------------------------------------------------------------------------------------------------- stability
def stability_row(denv):
    return by_id(system.check_render_stability(denv, core.make_ctx(denv)))["engine.render-stability"]


def _record(status, summary, sha="aaa"):
    return {"spec_sha256": sha, "result": {"gate": "stability.conformance", "status": status, "summary": summary,
                                           "checked_at": "2026-10-01T05:00:00-04:00"}}


def test_without_the_kits_bench_render_stability_is_unchecked_and_green(denv):
    row = stability_row(denv)
    assert row["status"] == "green" and "UNCHECKED" in row["summary"] and row["details"]["status"] == "UNCHECKED"


def test_the_render_stability_row_shows_the_last_conformance(denv):
    denv.stability = {"record": None, "bench": True, "spec_sha256": "aaa"}
    row = stability_row(denv)
    assert row["status"] == "yellow" and "slate qa stability conformance" in row["fix"]
    denv.stability = {"record": _record("PASS", "13 of 13 steps pass"), "bench": True, "spec_sha256": "aaa"}
    row = stability_row(denv)
    assert row["status"] == "green" and "13 of 13 steps pass" in row["summary"]
    denv.stability = {"record": _record("FAIL", "kit-drift wobbles at frame 12"), "bench": True, "spec_sha256": "aaa"}
    row = stability_row(denv)
    assert row["status"] == "red" and "kit-drift wobbles" in row["summary"] and row["fix"]
    denv.stability = {"record": _record("PASS", "all pass", sha="old"), "bench": True, "spec_sha256": "new"}
    row = stability_row(denv)
    assert row["status"] == "yellow" and "changed" in row["summary"]  # a pass on another bench isn't this bench's
    denv.stability = OSError("unreadable")
    assert stability_row(denv)["status"] == "yellow"


def test_the_new_rows_keep_a_set_up_mac_green(denv):
    rows = core.checks(env=denv)
    ids = {r["id"] for r in rows}
    assert {"ae.dialogs", "agents.mcp.single-instance", "agents.claude-code-version", "keys.dotenv",
            "net.egress-proxy", "engine.render-stability"} <= ids
    assert {r["id"]: r["status"] for r in rows if r["status"] != "green"} == {}
