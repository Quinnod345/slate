"""MCP registration: the exact command each agent takes, shown in a sandbox, run (against fakes) otherwise."""

from __future__ import annotations

import json

import pytest

from install_helpers import client_calls, make_repo, openclaw_state, set_openclaw_state
from slate.install import core, layout, mcp_register


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def _install(home, repo, fakebin, **kw):
    kw.setdefault("jobs_agent", False)
    kw.setdefault("quinn", False)
    kw.setdefault("doctor", False)
    return core.install(home=home, repo=repo, agents=["claude", "codex", "openclaw"], client_path=str(fakebin),
                        **kw)


def test_each_agent_gets_its_own_command_form(home, repo):
    shim = layout.resolve(home, repo=repo).shim
    assert mcp_register.add_argv("claude", shim) == ["claude", "mcp", "add", "--scope", "user", "slate", "--",
                                                     str(shim), "mcp"]
    assert mcp_register.add_argv("codex", shim) == ["codex", "mcp", "add", "slate", "--", str(shim), "mcp"]
    # OpenClaw 2026.9.4: `mcp add NAME --command … --arg …` (the plan's `mcp set` is the older form), with a connect
    # timeout long enough for `slate mcp` to answer its probe on a busy Mac.
    assert mcp_register.add_argv("openclaw", shim) == ["openclaw", "mcp", "add", "slate", "--command", str(shim),
                                                       "--arg", "mcp", "--connect-timeout", "30"]
    assert mcp_register.remove_argv("claude") == ["claude", "mcp", "remove", "--scope", "user", "slate"]
    assert mcp_register.remove_argv("codex") == ["codex", "mcp", "remove", "slate"]
    assert mcp_register.remove_argv("openclaw") == ["openclaw", "mcp", "unset", "slate"]


def test_a_sandbox_home_only_shows_the_commands(home, repo, fakebin):
    res = _install(home, repo, fakebin)
    assert [e["did"] for e in res["mcp"]] == ["shown", "shown", "shown"]
    assert client_calls(home) == [], "no agent CLI may run from a sandbox home"
    assert not (home / ".claude.json").exists()


def test_run_mode_registers_with_every_agent(home, repo, fakebin):
    res = _install(home, repo, fakebin, run_clients=True)
    assert [e["did"] for e in res["mcp"]] == ["registered", "registered", "registered"], res["mcp"]
    shim = str(home / ".local/bin/slate")
    calls = [(c["client"], c["argv"]) for c in client_calls(home)]
    assert ("claude", ["mcp", "add", "--scope", "user", "slate", "--", shim, "mcp"]) in calls
    assert ("codex", ["mcp", "add", "slate", "--", shim, "mcp"]) in calls
    assert ("openclaw", ["mcp", "add", "slate", "--command", shim, "--arg", "mcp", "--connect-timeout", "30"]) in calls
    assert ("openclaw", ["mcp", "reload"]) in calls
    assert not [c for c in calls if c[0] == "openclaw" and c[1][:2] == ["mcp", "set"]]
    registered = json.loads((home / ".claude.json").read_text())["mcpServers"]["slate"]
    assert registered["command"] == shim and registered["args"] == ["mcp"]
    assert openclaw_state(home)["servers"]["slate"] == {"command": shim, "args": ["mcp"], "connectionTimeoutMs": 30000}
    manifest = json.loads((layout.resolve(home, repo=repo).manifest_file).read_text())
    assert sorted(m["client"] for m in manifest["mcp"]) == ["claude", "codex", "openclaw"]


def test_a_second_run_registers_nothing_again(home, repo, fakebin):
    _install(home, repo, fakebin, run_clients=True)
    first = len(client_calls(home))
    res = _install(home, repo, fakebin, run_clients=True)
    assert [e["did"] for e in res["mcp"]] == ["already registered", "already registered", "already registered"]
    adds = [c for c in client_calls(home)[first:] if c["argv"][:2] in (["mcp", "add"], ["mcp", "set"])]
    assert adds == []


def test_an_older_slate_registration_is_replaced(home, repo, fakebin):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"slate": {
        "command": str(repo / ".venv/bin/python"), "args": ["-m", "slate", "mcp"]}}}))
    res = _install(home, repo, fakebin, run_clients=True)
    claude = next(e for e in res["mcp"] if e["client"] == "claude")
    assert claude["state"] == mcp_register.UPDATE and claude["did"] == "registered"
    argvs = [c["argv"][:2] for c in client_calls(home) if c["client"] == "claude"]
    assert argvs == [["mcp", "remove"], ["mcp", "add"]]


def test_an_older_openclaw_registration_is_unset_then_added(home, repo, fakebin):
    """OpenClaw's `mcp add` refuses a name it already has, so an older Slate entry comes off first."""
    set_openclaw_state(home, servers={"slate": {"command": str(repo / ".venv/bin/python"),
                                                "args": ["-m", "slate", "mcp"]}})
    res = _install(home, repo, fakebin, run_clients=True)
    oc = next(e for e in res["mcp"] if e["client"] == "openclaw")
    assert oc["state"] == mcp_register.UPDATE and oc["did"] == "registered", oc
    argvs = [c["argv"][:2] for c in client_calls(home) if c["client"] == "openclaw"]
    assert argvs == [["mcp", "show"], ["mcp", "unset"], ["mcp", "add"], ["mcp", "reload"]]
    assert openclaw_state(home)["servers"]["slate"]["command"] == str(home / ".local/bin/slate")


def test_an_openclaw_entry_that_is_already_right_is_left_as_it_is(home, repo, fakebin):
    """OpenClaw keeps extra fields (the connect timeout) next to command and args; the entry still counts as right."""
    shim = str(home / ".local/bin/slate")
    set_openclaw_state(home, servers={"slate": {"command": shim, "args": ["mcp"], "connectionTimeoutMs": 30000}})
    res = _install(home, repo, fakebin, run_clients=True)
    oc = next(e for e in res["mcp"] if e["client"] == "openclaw")
    assert oc["state"] == mcp_register.OK and oc["did"] == "already registered"
    assert [c["argv"][:2] for c in client_calls(home) if c["client"] == "openclaw"] == [["mcp", "show"]]


def test_a_failed_openclaw_probe_is_reported_with_its_own_words(home, repo, fakebin, monkeypatch):
    """`openclaw mcp add` saves nothing when its probe of `slate mcp` fails; the install says so and isn't ok."""
    monkeypatch.setenv("FAKE_OPENCLAW_PROBE_FAILS", "1")
    res = _install(home, repo, fakebin, run_clients=True)
    oc = next(e for e in res["mcp"] if e["client"] == "openclaw")
    assert oc["did"] == "failed" and "probe failed" in oc["error"] and "exited 1" in oc["error"]
    assert "reload" not in oc, "nothing to reload when nothing was saved"
    assert res["ok"] is False and "slate" not in openclaw_state(home).get("servers", {})


def test_someone_elses_slate_server_is_left_alone(home, repo, fakebin):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"slate": {
        "command": "/usr/local/bin/some-other-slate", "args": []}}}))
    res = _install(home, repo, fakebin, run_clients=True)
    claude = next(e for e in res["mcp"] if e["client"] == "claude")
    assert claude["state"] == mcp_register.CONFLICT and claude["did"] == "left alone"
    assert "isn't Slate's" in claude["next"] and any("isn't Slate's" in s for s in res["next_steps"])
    assert not [c for c in client_calls(home) if c["client"] == "claude"]


def test_a_missing_agent_cli_becomes_a_next_step(home, repo, tmp_path):
    empty = tmp_path / "nobin"
    empty.mkdir()
    res = core.install(home=home, repo=repo, agents=["codex"], client_path=str(empty), jobs_agent=False,
                       quinn=False, doctor=False, run_clients=True)
    [codex] = res["mcp"]
    assert codex["state"] == mcp_register.NO_CLIENT and codex["did"] == "skipped"
    assert "codex mcp add slate --" in codex["next"]


def test_a_failing_agent_cli_is_reported_and_fails_the_install(home, repo, fakebin, monkeypatch):
    monkeypatch.setenv("FAKE_CLIENT_FAIL", "codex")
    res = _install(home, repo, fakebin, run_clients=True)
    codex = next(e for e in res["mcp"] if e["client"] == "codex")
    assert codex["did"] == "failed" and "exited 1" in codex["error"]
    assert res["ok"] is False and res["failures"]


def test_uninstall_removes_only_slates_registration(home, repo, fakebin):
    _install(home, repo, fakebin, run_clients=True)
    set_openclaw_state(home, servers={"slate": {"command": "/opt/other/slate-server", "args": []}})
    res = core.uninstall(home=home, repo=repo, client_path=str(fakebin), run_clients=True, jobs_agent=False)
    did = {m["client"]: m["did"] for m in res["mcp"]}
    assert did == {"claude": "removed", "codex": "removed", "openclaw": "left alone"}
    assert "slate" not in json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert openclaw_state(home)["servers"]["slate"]["command"] == "/opt/other/slate-server"


def test_dry_run_on_run_mode_changes_nothing(home, repo, fakebin):
    res = _install(home, repo, fakebin, run_clients=True, dry_run=True)
    assert all(e["did"] == "shown" for e in res["mcp"])
    assert [c for c in client_calls(home) if c["argv"][:2] in (["mcp", "add"], ["mcp", "set"])] == []
