"""After Effects rows: installed, aerender, the scripting pref, the bridge, Automation, the smoke test.

Doctor never launches AE or aerender: no test here lets a real process run, and the fake records every command, so
each test can also say what was never attempted.
"""

from __future__ import annotations

from slate.doctor import aftereffects, core
from slate.errors import MissingPrerequisite
from slate.gates import GateResult

from doctor_helpers import BRIDGE_DIR, by_id


def rows(denv):
    return by_id(aftereffects.check_ae(denv, core.make_ctx(denv)))


def never_touched_ae(denv):
    return not [c for c in denv.calls if "aerender" in c[0] or c[0].endswith("osascript")]


def test_ae_all_green_and_nothing_run(denv):
    got = rows(denv)
    assert {r["status"] for r in got.values()} == {"green"}
    assert "26.5.0" in got["ae.app"]["summary"]
    assert never_touched_ae(denv)


def test_no_after_effects_is_red(denv):
    denv.ae_list = []
    got = rows(denv)
    assert got["ae.app"]["status"] == "red" and "Creative Cloud" in got["ae.app"]["fix"]
    assert got["ae.aerender"]["status"] == "red"
    assert "never buys" in got["ae.app"]["fix"]


def test_a_different_major_version_is_yellow(denv):
    denv.plists[f"{denv.ae_list[0]['app']}/Contents/Info.plist"] = {"CFBundleShortVersionString": "27.0.0"}
    row = rows(denv)["ae.app"]
    assert row["status"] == "yellow" and "slate connect aftereffects" in row["fix"]


def test_aerender_missing_is_red(denv):
    denv.aerender = MissingPrerequisite("aerender (After Effects' command-line renderer) isn't on this Mac.")
    assert rows(denv)["ae.aerender"]["status"] == "red"


def test_scripting_pref_on_off_unreadable_damaged(denv):
    assert rows(denv)["ae.scripting-pref"]["status"] == "green"
    denv.prefs = {**denv.prefs, "scripting_on": False, "scripting": "off"}
    row = rows(denv)["ae.scripting-pref"]
    assert row["status"] == "yellow"
    assert "Finishing works without it" in row["summary"] and "building scenes" in row["summary"]
    assert "Only you turn it on" in row["fix"] and "never changes it" in row["fix"]
    denv.prefs = {**denv.prefs, "scripting": "not set (off)"}
    assert rows(denv)["ae.scripting-pref"]["status"] == "yellow"
    denv.prefs = MissingPrerequisite("No After Effects prefs file under /x.")
    assert rows(denv)["ae.scripting-pref"]["status"] == "yellow"
    denv.prefs = {"damaged": True, "lf": 12, "scripting_on": True}
    row = rows(denv)["ae.scripting-pref"]
    assert row["status"] == "red" and "reset every preference" in row["summary"]


def test_scripting_pref_reads_through_the_byte_safe_module(tmp_path, monkeypatch):
    """The real Env reads the pref through slate.ae.prefs.check, here on a stand-in file (CR line endings)."""
    from slate.doctor.env import Env

    prefs = tmp_path / "Prefs.txt"
    prefs.write_bytes(b'["Main Pref Section v2"]\r\t"Pref_SCRIPTING_FILE_NETWORK_SECURITY" = "0"\r')
    monkeypatch.setenv("SLATE_AE_PREFS_FILE", str(prefs))
    monkeypatch.setattr("slate.ae.guard.ae_open", lambda: [])
    before = prefs.read_bytes()
    info = Env().ae_prefs()
    assert info["scripting_on"] is False and info["damaged"] is False
    assert prefs.read_bytes() == before  # read only


def test_bridge_paths(denv):
    assert rows(denv)["ae.bridge"]["status"] == "green"
    denv.bridge = {**denv.bridge, "acceptance_current": False}
    row = rows(denv)["ae.bridge"]
    assert row["status"] == "yellow" and "acceptance --go" in row["fix"]
    denv.bridge = {**denv.bridge, "pin_ok": False, "version": "0.1.5"}
    row = rows(denv)["ae.bridge"]
    assert row["status"] == "yellow" and "0.1.5" in row["summary"] and "0.1.3" in row["summary"]
    denv.bridge = {"installed": False, "pinned": "0.1.3"}
    row = rows(denv)["ae.bridge"]
    assert row["status"] == "yellow" and row["install"]["id"] == "ae-bridge"


def test_bridge_registration_is_read_from_claude_json(denv):
    regs = aftereffects.bridge_registrations(denv)
    assert regs["claude-code"]["package_dir"] == BRIDGE_DIR
    assert regs["claude-code"]["launcher"] == "node"
    (denv.home / ".claude.json").write_text('{"mcpServers": {}}')
    denv.bridge = {**denv.bridge, "acceptance_current": True}
    row = rows(denv)["ae.bridge"]
    assert row["status"] == "yellow" and "not registered with Claude Code" in row["summary"]


def test_automation_is_deferred_when_ae_is_closed_and_green_only_once_a_smoke_test_proved_it(denv):
    row = rows(denv)["ae.automation"]  # the fake's last smoke test passed
    assert row["status"] == "green" and row["details"]["deferred"] is True
    assert row["details"]["proven_by"] == "the last smoke test" and "never launches it" in row["summary"]
    assert never_touched_ae(denv)


def test_an_unproven_automation_permission_is_never_green(denv):
    """AE closed and no smoke test yet: nothing checked the permission, and doctor won't launch AE to find out."""
    for smoke in (GateResult.unchecked("ae.smoke", "the smoke test hasn't run on this Mac yet"),
                  GateResult.fail("ae.smoke", "aerender made 12 frames of 30")):
        denv.smoke = smoke
        row = rows(denv)["ae.automation"]
        assert row["status"] == "yellow" and row["details"]["deferred"] is True, row
        assert "isn't checked yet" in row["summary"] and "slate connect aftereffects" in row["fix"]
    assert never_touched_ae(denv)


def test_ae_closing_while_doctor_looks_is_still_unchecked(denv):
    denv.ae_is_running = True
    denv.set_response("osascript", "-e", out="not running\n")
    denv.smoke = GateResult.unchecked("ae.smoke", "not run yet")
    row = rows(denv)["ae.automation"]
    assert row["status"] == "yellow" and "closed while doctor looked" in row["summary"]


def test_automation_is_asked_only_when_ae_is_already_open(denv):
    denv.ae_is_running = True
    denv.agent = "claude-code"
    denv.envvars["__CFBundleIdentifier"] = "com.anthropic.claudefordesktop"
    row = rows(denv)["ae.automation"]
    assert row["status"] == "green" and row["details"]["deferred"] is False
    script = [c for c in denv.calls if c[0].endswith("osascript")][0][-1]
    assert "is running" in script and "com.adobe.AfterEffects.application" in script
    assert "Claude Code in Claude (the desktop app)" in row["summary"]


def test_automation_denied_or_busy(denv):
    denv.ae_is_running = True
    denv.set_response("osascript", "-e", code=1, err="execution error: Not authorized to send Apple events to "
                                                     "After Effects. (-1743)")
    row = rows(denv)["ae.automation"]
    assert row["status"] == "red" and "Privacy & Security > Automation" in row["fix"]
    denv.set_response("osascript", "-e", code=1, err="AppleEvent timed out. (-1712)")
    assert rows(denv)["ae.automation"]["status"] == "yellow"


def test_smoke_pass_fail_unchecked(denv):
    assert rows(denv)["ae.smoke"]["status"] == "green"
    denv.smoke = GateResult.fail("ae.smoke", "aerender made 12 frames of 30")
    row = rows(denv)["ae.smoke"]
    assert row["status"] == "red" and "12 frames" in row["summary"]
    denv.smoke = GateResult.unchecked("ae.smoke", "the After Effects smoke test hasn't run on this Mac yet")
    row = rows(denv)["ae.smoke"]
    assert row["status"] == "yellow" and "slate connect aftereffects" in row["fix"]


def test_real_smoke_last_never_starts_ae(slate_home):
    """slate.ae.smoke.run() without go only reports the last result: UNCHECKED on a fresh Mac."""
    from slate.doctor.env import Env

    res = Env().ae_smoke_last()
    assert res.status == "UNCHECKED"
