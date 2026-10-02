"""Higgsfield's AE bridge, wrapped: the status parser, the pinned install, the safe-retry rule for non-transactional
batches, the acceptance run with every refusal, looks fetched at use time, the route choice. No real bridge, npm or
After Effects runs here: sessions and runners are fakes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import procs
from slate.ae import bridge, eases
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, UsageError

from ae_helpers import completed, prefs_bytes

PIN = "0.1.3"


def _pkg(tmp_path: Path, version: str = PIN, looks=bridge.LOOKS) -> Path:
    d = tmp_path / "node_modules" / bridge.PACKAGE
    (d / "dist").mkdir(parents=True)
    (d / "package.json").write_text(json.dumps({"name": bridge.PACKAGE, "version": version,
                                                "license": "SEE LICENSE IN UPSTREAM.md"}))
    for name in looks:
        (d / "skills" / "after-effects" / name).mkdir(parents=True)
    return d


# ---- the status parser ----------------------------------------------------------------------------------------


def test_status_not_installed():
    st = bridge.parse_status(None, pin=PIN, prefs_info={"scripting_on": False})
    assert st["installed"] is False and st["route"] == "slate-es3" and st["finishing_ok"] is True
    assert any("install" in n for n in st["next"])
    assert st["looks_missing"] == list(bridge.LOOKS)


def test_status_pref_off_keeps_scenes_on_slates_own_library():
    st = bridge.parse_status({"name": bridge.PACKAGE, "version": PIN}, pin=PIN, prefs_info={"scripting_on": False},
                             looks_found=bridge.LOOKS)
    assert st["pin_ok"] and not st["can_build_scenes"] and st["route"] == "slate-es3"
    assert st["scripting_pref"] == "off" and st["looks_missing"] == []
    assert any("only you turn it on" in n for n in st["next"])


def test_status_needs_acceptance_on_this_version():
    pkg = {"name": bridge.PACKAGE, "version": PIN}
    st = bridge.parse_status(pkg, pin=PIN, prefs_info={"scripting_on": True})
    assert st["can_build_scenes"] and st["route"] == "slate-es3"
    assert any("acceptance" in n for n in st["next"])
    old = bridge.parse_status(pkg, pin=PIN, prefs_info={"scripting_on": True},
                              acceptance={"version": "0.1.2", "passed": True})
    assert old["acceptance_current"] is False and old["route"] == "slate-es3"
    ok = bridge.parse_status(pkg, pin=PIN, prefs_info={"scripting_on": True}, acceptance={"version": PIN, "passed": True})
    assert ok["route"] == "bridge" and ok["next"] == []


def test_status_pin_mismatch():
    st = bridge.parse_status({"name": bridge.PACKAGE, "version": "0.1.4"}, pin=PIN, prefs_info={"scripting_on": True})
    assert st["installed"] and not st["pin_ok"] and not st["can_build_scenes"]
    assert any("pins 0.1.3" in n for n in st["next"])


def test_status_unknown_prefs():
    st = bridge.parse_status({"name": bridge.PACKAGE, "version": PIN}, pin=PIN, prefs_info=None)
    assert st["scripting_pref"].startswith("unknown") and st["can_build_scenes"] is False


def test_parse_doctor_output():
    text = json.dumps({"ok": False, "checks": [{"name": "node", "ok": True, "detail": "26.8.1"},
                                               {"name": "after-effects", "ok": False, "detail": "not found"}],
                       "liveConnectionTested": False, "nextStep": "Call ae_project_info"}, indent=2)
    assert bridge.parse_doctor("some banner\n" + text) == {"ok": False, "checks": {"node": True, "after-effects": False},
                                                           "live_tested": False, "next": "Call ae_project_info"}
    assert bridge.parse_doctor("not json") is None and bridge.parse_doctor(None) is None


def test_status_reads_the_installed_package_and_the_pref(tmp_path, monkeypatch, live_prefs):
    d = _pkg(tmp_path)
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(d))
    live_prefs(data=prefs_bytes(scripting=b'"1"'))
    seen = []

    def runner(argv, timeout=None, **kw):
        seen.append([str(a) for a in argv])
        return completed(argv, 0, json.dumps({"ok": True, "checks": [{"name": "server", "ok": True}]}))

    monkeypatch.setattr(procs, "which", lambda tool: "/usr/local/bin/node" if tool == "node" else None)
    st = bridge.status(run_doctor=True, runner=runner)
    assert st["installed"] and st["version"] == PIN and st["scripting_pref"] == "on" and st["where"] == str(d)
    assert st["doctor"] == {"ok": True, "checks": {"server": True}, "live_tested": False, "next": None}
    assert seen == [["/usr/local/bin/node", str(d / "dist" / "cli.js"), "doctor"]]


def test_status_reports_aes_open_launch_dialogs(tmp_path, monkeypatch):
    """The bridge's status names AE's launch dialogs (open_dialogs [{kind, title}]), read from AE's window titles only
    while AE is open; doctor and every supervised step read the same."""
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(_pkg(tmp_path)))
    titles = {"running": True, "windows": ["Adobe After Effects 2026 - film.aep", "Home", "Restore project?"],
              "error": None}
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    found = bridge.open_dialogs(lambda: titles)
    assert found == {"open_dialogs": [{"kind": "home", "title": "Home"},
                                      {"kind": "crash_recovery", "title": "Restore project?"}],
                     "dialogs_readable": True}
    monkeypatch.setattr(bridge, "_ae_windows", lambda: titles)
    st = bridge.status()
    assert [d["kind"] for d in st["open_dialogs"]] == ["home", "crash_recovery"] and "launch dialog" in st["next"][0]
    assert "open_dialogs" not in bridge.status(dialogs=False)  # doctor reads the windows itself
    monkeypatch.setattr(bridge, "_ae_windows", lambda: {"running": True, "windows": None, "error": "denied"})
    assert bridge.status()["dialogs_readable"] is False


def test_status_asks_nothing_of_ae_while_it_is_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(_pkg(tmp_path)))
    monkeypatch.setattr(procs, "ae_running", lambda: False)

    def windows():
        raise AssertionError("System Events asked while AE is closed")

    assert bridge.open_dialogs(windows) == {"open_dialogs": [], "dialogs_readable": True}
    assert bridge.status()["open_dialogs"] == []


# ---- install --------------------------------------------------------------------------------------------------


def test_install_plan_is_pinned_and_changes_nothing(monkeypatch):
    calls = []
    res = bridge.install(runner=lambda *a, **k: calls.append(a))
    assert res["ran"] is False and calls == []
    # the package's own install scripts never run (L-packaging-D004); then its doctor and config, then registration
    assert res["plan"]["install"] == ["npm", "install", "--global", "--ignore-scripts", f"{bridge.PACKAGE}@{PIN}"]
    assert res["plan"]["verify"] == ["fnf-after-effects", "doctor"]
    assert res["plan"]["config"] == ["fnf-after-effects", "config"]
    codex = res["plan"]["register"]["codex"]
    assert codex[:4] == ["codex", "mcp", "add", bridge.SERVER_NAME] and codex[-1].endswith("dist/index.js")


def test_every_registration_keeps_eval_off_and_the_categories_limited():
    """The bridge is registered with arbitrary eval off and its operations limited to the categories Slate's scenes
    use, in each agent's own spelling (L-ae-025); the switch names are the pinned version's (dist/policy.js)."""
    plan = bridge.install_plan()
    assert plan["policy"] == {"AE_MCP_ENABLE_EVAL": "0", "AE_MCP_ALLOW_CATEGORIES": ",".join(bridge.SCENE_CATEGORIES)}
    for flag, argv in (("-e", plan["register"]["claude-code"]), ("--env", plan["register"]["codex"]),
                       ("--env", plan["register"]["openclaw"])):
        pairs = [argv[i + 1] for i, a in enumerate(argv) if a == flag]
        assert "AE_MCP_ENABLE_EVAL=0" in pairs, argv
        assert f"AE_MCP_ALLOW_CATEGORIES={','.join(bridge.SCENE_CATEGORIES)}" in pairs, argv
    # never the categories that run arbitrary code, menu commands or touch AE's preferences
    assert not {"eval", "command", "pref"} & set(bridge.SCENE_CATEGORIES)
    # and everything the acceptance run asks for is allowed
    ops = bridge.acceptance_script()["ops"][0]["expect"]["operations"]
    assert {o.split(".")[0] for o in ops} <= set(bridge.SCENE_CATEGORIES)
    assert any("trust boundary" in n and "0700" in n for n in plan["notes"])


def test_each_call_waits_a_little_longer_than_the_bridge_itself(tmp_path):
    """60 s by default, 120 s to render a frame, 10 minutes to import, each with a margin on Slate's side, so a long
    import is never cut off here and left in an unknown state (L-ae-023); `timeout` still overrides every call."""
    d = _pkg(tmp_path)
    s = bridge.BridgeSession(d)
    assert s.timeout_for("ae_do") == 60 + bridge.READ_MARGIN_S
    assert s.timeout_for("ae_render_frame") == 120 + bridge.READ_MARGIN_S
    assert s.timeout_for("ae_project_import_json") == 600 + bridge.READ_MARGIN_S
    assert s.timeout_for("ae_import_footage") == 600 + bridge.READ_MARGIN_S
    assert bridge.BridgeSession(d, timeout=30).timeout_for("ae_project_import_json") == 30


def test_install_with_yes_runs_npm_and_checks_the_pin(tmp_path, monkeypatch):
    d = _pkg(tmp_path)
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(d))
    seen = []
    res = bridge.install(yes=True, runner=lambda argv, timeout=None, **k: seen.append(argv) or completed(argv, 0))
    assert res["ran"] and res["version"] == PIN and seen[0][:3] == ["npm", "install", "--global"]
    (d / "package.json").write_text(json.dumps({"name": bridge.PACKAGE, "version": "9.9.9"}))
    with pytest.raises(Refused, match="not the pin"):
        bridge.install(yes=True, runner=lambda argv, timeout=None, **k: completed(argv, 0))
    with pytest.raises(SlateError, match="couldn't install"):
        bridge.install(yes=True, runner=lambda argv, timeout=None, **k: completed(argv, 1, "", "E404"))


# ---- retries on a non-transactional bridge --------------------------------------------------------------------


class Script:
    """A fake bridge session: answers per (tool, operation) from a queue of (ok, payload)."""

    def __init__(self, answers: dict | None = None, default=(True, {"ok": True})):
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.default = default
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def call(self, tool, args=None):
        args = args or {}
        self.calls.append((tool, args))
        key = f"{tool}:{args.get('operation')}" if tool == "ae_do" else tool
        q = self.answers.get(key)
        if q:
            ans = q.pop(0)
            if isinstance(ans, Exception):
                raise ans
            return ans
        return self.default


def test_retry_returns_the_first_success():
    s = Script()
    out = bridge.call_with_retry(s, "ae_do", {"operation": "comp.create"})
    assert out["ok"] and out["attempts"] == 1 and len(s.calls) == 1


def test_retry_reads_state_first_and_never_resends_what_happened():
    s = Script({"ae_do:comp.create": [(False, {"error": "TIMEOUT"})], "ae_do:comp.info": [(True, {"ok": True})]})
    out = bridge.call_with_retry(s, "ae_do", {"operation": "comp.create"},
                                 verify={"tool": "ae_do", "args": {"operation": "comp.info"}})
    assert out["ok"] and out["recovered"]
    assert [c[1].get("operation") for c in s.calls] == ["comp.create", "comp.info"]


def test_retry_resends_only_after_the_read_shows_it_didnt_happen():
    s = Script({"ae_do:comp.create": [(False, {"error": "boom"}), (True, {"ok": True})],
                "ae_do:comp.info": [(False, {"error": "no comp"})]})
    out = bridge.call_with_retry(s, "ae_do", {"operation": "comp.create"},
                                 verify={"tool": "ae_do", "args": {"operation": "comp.info"}})
    assert out["ok"] and out["attempts"] == 2
    assert [c[1].get("operation") for c in s.calls] == ["comp.create", "comp.info", "comp.create"]


def test_retry_without_a_read_never_resends():
    s = Script({"ae_do:layer.delete": [RuntimeError("timed out")]})
    out = bridge.call_with_retry(s, "ae_do", {"operation": "layer.delete"})
    assert not out["ok"] and len(s.calls) == 1


# ---- acceptance -----------------------------------------------------------------------------------------------


@pytest.fixture
def installed(tmp_path, monkeypatch, live_prefs):
    d = _pkg(tmp_path)
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(d))
    live_prefs(data=prefs_bytes(scripting=b'"1"'))
    return d


def _good_session():
    catalog = {"ok": True, "totalOperations": 197, "categories": [{"category": "all", "operationCount": 197,
               "operations": bridge.acceptance_script()["ops"][0]["expect"]["operations"]}]}
    skills = {"ok": True, "skills": [{"name": n} for n in bridge.LOOKS]}
    return Script({"ae_project_info": [(True, {"ok": True, "file": None, "dirty": False, "numItems": 3}),
                                       (True, {"ok": True, "numItems": 0}),
                                       (True, {"ok": True, "file": "/x/acceptance.aep"})],
                   "ae_catalog": [(True, catalog)], "ae_get_skill": [(True, skills)],
                   "ae_do:layer.info": [(True, {"ok": True, "keyframes": [{"time": 0}, {"time": 1}]}),
                                        (True, {"ok": True, "keyframes": [{"time": 0}, {"time": 1}]}),
                                        (True, {"ok": True, "keyframes": [{"time": 0}, {"time": 1}]})]})


def test_acceptance_passes_and_is_recorded(installed, slate_home, tmp_path):
    s = _good_session()
    rec = bridge.acceptance(go=True, session_factory=lambda: s, scratch_dir=tmp_path / "scratch")
    assert rec["passed"], rec["results"]
    assert rec["version"] == PIN and len(rec["results"]) == len(bridge.acceptance_script()["ops"])
    assert bridge.last_acceptance()["passed"] is True
    ease_out = next(a for t, a in s.calls if a.get("operation") == "keyframe.set_easing" and a["args"]["keyIndex"] == 1)
    seg = eases.seg_ease(bridge.TEST_EASE, 480.0)
    assert ease_out["args"]["outSpeed"] == pytest.approx(seg["out"][0], rel=1e-6)
    assert ease_out["args"]["outInfluence"] == pytest.approx(seg["out"][1], rel=1e-6)
    save = next(a for t, a in s.calls if t == "ae_save_project")
    assert save["path"] == str(tmp_path / "scratch" / "acceptance.aep")


def test_acceptance_stops_at_the_first_failed_mutation(installed, slate_home, tmp_path):
    s = _good_session()
    s.answers["ae_do:shape.add_group"] = [(False, {"error": "OPERATION_FAILED"})]
    rec = bridge.acceptance(go=True, session_factory=lambda: s, scratch_dir=tmp_path / "scratch")
    assert rec["passed"] is False
    assert rec["results"][-1]["id"] == "group" and rec["results"][-1]["ok"] is False
    assert not any(a.get("operation") == "shape.add_ellipse" for _t, a in s.calls)


def test_acceptance_refusals(installed, live_prefs, monkeypatch, tmp_path, slate_home):
    with pytest.raises(Refused, match="go"):
        bridge.acceptance(session_factory=lambda: pytest.fail("no session without a go"))
    live_prefs(data=prefs_bytes(scripting=b'"0"'))
    with pytest.raises(Refused, match="it's off") as exc:
        bridge.acceptance(go=True, session_factory=lambda: pytest.fail("no session with the pref off"))
    assert "Only you turn it on" in exc.value.hint and "never changes it" in exc.value.hint
    live_prefs(data=prefs_bytes(scripting=b'"1"'))
    dirty = Script({"ae_project_info": [(True, {"ok": True, "dirty": True, "numItems": 4})]})
    with pytest.raises(Busy, match="unsaved"):
        bridge.acceptance(go=True, session_factory=lambda: dirty, scratch_dir=tmp_path / "s")
    assert [t for t, _a in dirty.calls] == ["ae_project_info"]  # nothing mutated
    monkeypatch.setenv("SLATE_MODE", "autonomous")
    with pytest.raises(Refused, match="autonomous"):
        bridge.acceptance(go=True, session_factory=lambda: pytest.fail("never"))


def test_acceptance_needs_the_bridge(monkeypatch, tmp_path, slate_home):
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setattr(bridge, "GLOBAL_ROOTS", (str(tmp_path / "none"),))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(MissingPrerequisite):
        bridge.acceptance(go=True, session_factory=lambda: pytest.fail("never"))


# ---- looks and routes -----------------------------------------------------------------------------------------


def test_looks_are_names_fetched_at_use_time():
    assert len(bridge.looks()) == 13 and bridge.ENTRY_LOOK in bridge.looks()
    s = Script({"ae_get_skill": [(True, {"ok": True, "name": "ae-depth-space", "text": "…"})]})
    got = bridge.fetch_look("ae-depth-space", session_factory=lambda: s)
    assert got["name"] == "ae-depth-space" and s.calls == [("ae_get_skill", {"name": "ae-depth-space"})]
    with pytest.raises(UsageError):
        bridge.fetch_look("ae-something-else", session_factory=lambda: s)


def test_no_look_text_lives_in_slate():
    root = Path(bridge.__file__).resolve().parents[3]
    for folder in ("src/slate", "engine/ae", "skills"):
        for f in (root / folder).rglob("*"):
            if f.is_file() and f.suffix in (".md", ".json", ".txt") and "node_modules" not in f.parts:
                assert "ae-clean-rig/references" not in f.read_text(errors="ignore"), f


def test_choose_route():
    st = bridge.parse_status({"name": bridge.PACKAGE, "version": PIN}, pin=PIN, prefs_info={"scripting_on": False})
    r = bridge.choose_route(st)
    assert r["route"] == "slate-es3" and "needs no pref" in r["reason"]
    st = bridge.parse_status({"name": bridge.PACKAGE, "version": PIN}, pin=PIN, prefs_info={"scripting_on": True},
                             acceptance={"version": PIN, "passed": True})
    assert bridge.choose_route(st)["route"] == "bridge"


def test_the_bridge_server_gets_no_keys(monkeypatch, tmp_path):
    d = _pkg(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-should-not-travel")
    monkeypatch.setenv("HF_KEY", "id:secret")
    monkeypatch.setenv("AE_MCP_READONLY", "1")
    env = bridge.BridgeSession(d)._env()
    assert env["BROWSER"] == "none" and env["AE_MCP_READONLY"] == "1"
    assert "ELEVENLABS_API_KEY" not in env and "HF_KEY" not in env


def test_the_environment_never_turns_eval_on_or_widens_the_categories(monkeypatch, tmp_path):
    d = _pkg(tmp_path)
    monkeypatch.setenv("AE_MCP_ENABLE_EVAL", "1")
    monkeypatch.setenv("AE_MCP_ALLOW_CATEGORIES", "eval,command,pref")
    env = bridge.BridgeSession(d)._env()
    assert env["AE_MCP_ENABLE_EVAL"] == "0"
    assert env["AE_MCP_ALLOW_CATEGORIES"] == ",".join(bridge.SCENE_CATEGORIES)


def test_openclaw_registers_the_bridge_with_mcp_add():
    """OpenClaw 2026.9.4 registers a server with `openclaw mcp add <name> --command … --arg …` (it refuses `mcp set`),
    and probes the server first, so the connect timeout is 30 s, as for Slate's own server."""
    from slate.ae import bridge as ae_bridge

    oc = ae_bridge.install_plan()["register"]["openclaw"]
    assert oc[:4] == ["openclaw", "mcp", "add", ae_bridge.SERVER_NAME]
    assert oc[oc.index("--arg") + 1].endswith("dist/index.js") and oc[-2:] == ["--connect-timeout", "30"]
    assert "set" not in oc
