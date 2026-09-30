"""After Effects: installed, rendering, allowed, bridged, proven.

Doctor never launches After Effects or aerender, and never changes an AE setting. It reads:

- the newest AE install and its version, from the app's own Info.plist;
- where aerender is (it isn't run: running it starts AE's render engine);
- AE's "Allow Scripts to Write Files and Access Network" setting, through slate.ae.prefs.check (the byte-safe reader).
  Off is yellow, not red: finishing works without it, only building scenes through the AE bridge needs it, and only
  the person turns it on;
- Higgsfield's AE bridge: installed, at the pinned version, accepted on this version, and where it's registered;
- macOS's Automation permission for the app running Slate to control AE. Testing it means sending AE an Apple Event,
  which would launch AE if it weren't open, so doctor only asks when AE is already open. Otherwise the last smoke test
  is the proof; with neither, the row is yellow (unchecked is never green) and the fix is the smoke test;
- the last 1-second smoke test's result (slate.ae.smoke without a go only reports; `slate connect aftereffects` runs
  it).
"""

from __future__ import annotations

from pathlib import Path

from slate.doctor import clients
from slate.doctor import rows as R
from slate.doctor.common import Ctx, call_item, first_line, major, tilde
from slate.doctor.env import Env

G = "aftereffects"
AE_BUNDLE_ID = "com.adobe.AfterEffects.application"
BRIDGE_SERVER = "higgsfield-use-after-effects"
BRIDGE_PACKAGE = "fnf-after-effects-mcp"
PREF = "\"Allow Scripts to Write Files and Access Network\""
PREF_PATH = "After Effects > Settings > Scripting & Expressions"
# Asks AE its version only if it's already running (the `is running` test itself never launches it), and gives up
# after 10 seconds: AE may be busy, or macOS may be waiting on the person to answer a permission prompt.
AUTOMATION_SCRIPT = f'''if application id "{AE_BUNDLE_ID}" is running then
  with timeout of 10 seconds
    tell application id "{AE_BUNDLE_ID}" to get version
  end timeout
else
  return "not running"
end if'''


def check_ae(env: Env, ctx: Ctx) -> list[dict]:
    installs = env.ae_installs()
    if not installs:
        return [R.red("ae.app", "After Effects isn't installed. Supervised films finish in AE, and it's the second way "
                                "to build scenes.",
                      "Install After Effects from Creative Cloud (paid, and your call: Slate never buys or starts a "
                      "licence).", group=G),
                R.red("ae.aerender", "aerender (After Effects' command-line renderer) comes with After Effects, so "
                                     "it's missing too.", "Install After Effects from Creative Cloud.", group=G)]
    rows = [_app(env, installs), _aerender(env), _pref(env), _bridge(env, ctx)]
    smoke = _smoke(env)
    rows.append(_automation(env, ctx, smoke))
    rows.append(smoke)
    return rows


def _app(env: Env, installs: list[dict]) -> dict:
    newest = installs[0]
    info = env.read_plist(Path(newest["app"]) / "Contents" / "Info.plist") or {}
    version = info.get("CFBundleShortVersionString") or "(version unreadable)"
    build = info.get("CFBundleVersion")
    verified = str(env.registry_tool("after-effects").get("version") or "")
    others = [i["name"] for i in installs[1:]]
    details = {"app": newest["app"], "version": version, "build": build, "verified": verified,
               "bundle_id": info.get("CFBundleIdentifier"), "other_installs": others}
    text = f"After Effects {version}" + (f" (build {build})" if build else "") + f" at {newest['app']}"
    if others:
        text += f"; also installed: {', '.join(others)} (Slate uses the newest)"
    if verified and major(version) is not None and major(version) != major(verified):
        return R.yellow("ae.app", text + f". Slate was verified with {verified}; the smoke test proves a new version.",
                        "Run `slate connect aftereffects` (you start AE; it renders a 1-second test).", group=G,
                        details=details)
    return R.green("ae.app", text, group=G, details=details)


def _aerender(env: Env) -> dict:
    try:
        path = env.aerender_path()
    except Exception as exc:  # noqa: BLE001
        return R.red("ae.aerender", f"aerender, After Effects' command-line renderer, isn't where it should be: "
                                    f"{getattr(exc, 'message', exc)}",
                     "Reinstall After Effects from Creative Cloud; aerender comes with it.", group=G)
    if not env.is_executable(path):
        return R.red("ae.aerender", f"aerender at {path} isn't executable.",
                     "Reinstall After Effects from Creative Cloud.", group=G, details={"path": path})
    return R.green("ae.aerender", f"aerender at {path} (masters and the smoke test render with it; doctor doesn't run "
                                  "it)", group=G, details={"path": path, "archs": env.archs(path)})


def _pref(env: Env) -> dict:
    rid = "ae.scripting-pref"
    try:
        info = env.ae_prefs()
    except Exception as exc:  # noqa: BLE001 - no prefs file yet, or one Slate refuses to read
        return R.yellow(rid, f"After Effects' preferences couldn't be read, so {PREF} is unknown "
                             f"({getattr(exc, 'message', exc)}).",
                        "Open After Effects once and quit it (it writes its preferences then), and run `slate doctor` "
                        "again.", group=G)
    details = {"file": info.get("file"), "scripting": info.get("scripting"), "damaged": info.get("damaged"),
               "disk_cache_on": (info.get("disk_cache") or {}).get("on"), "running": info.get("running")}
    if info.get("damaged"):
        return R.red(rid, f"AE's preferences file has LF line endings ({info.get('lf')} of them), so AE will throw it "
                          "away and reset every preference the next time it opens.",
                     "Don't open After Effects yet. Run `slate ae prefs check`, then restore your settings from a "
                     "backup with `slate ae prefs restore <older copy>` (it's a dry run until you add --apply).",
                     group=G, details=details)
    if info.get("scripting_on"):
        return R.green(rid, f"{PREF} is on: building scenes through the AE bridge works. Slate never changes this "
                            "setting.", group=G, details=details)
    return R.yellow(rid, f"{PREF} is {info.get('scripting') or 'off'}. Finishing works without it; building scenes in "
                         "AE through the bridge needs it.",
                    f"Only you turn it on, in {PREF_PATH} ({PREF}). Slate never changes it.", group=G,
                    details=details)


def bridge_registrations(env: Env) -> dict:
    """Where the AE bridge is registered, and the package folder each registration launches (read-only)."""
    out: dict = {}
    for scope, name, spec in clients.claude_all_servers(env):
        if name == BRIDGE_SERVER or clients.mentions(spec, BRIDGE_PACKAGE):
            folder = clients.bridge_dir_from(spec, BRIDGE_PACKAGE)
            out["claude-code"] = {"name": name, "scope": scope, "package_dir": str(folder) if folder else None,
                                  "launcher": Path(str(spec.get("command") or "")).name or None}
            break
    for label, servers in (("codex", clients.codex_servers(env)), ("openclaw", clients.openclaw_servers(env))):
        for name, spec in (servers or {}).items():
            if name == BRIDGE_SERVER or clients.mentions(spec, BRIDGE_PACKAGE):
                out[label] = {"name": name}
                break
    return out


def _bridge(env: Env, ctx: Ctx) -> dict:
    rid = "ae.bridge"
    try:
        st = env.ae_bridge_status()
    except Exception as exc:  # noqa: BLE001
        return R.red(rid, f"Couldn't read the AE bridge's status: {getattr(exc, 'message', exc)}",
                     "Run `slate ae bridge status` to see the error in full.", group=G)
    pin = st.get("pinned") or "?"
    regs = bridge_registrations(env)
    item = call_item("ae-bridge", f"Higgsfield's AE bridge ({BRIDGE_PACKAGE} {pin}, pinned exactly; npm, global)",
                     f"npm install --global {BRIDGE_PACKAGE}@{pin}", size="small")
    details = {"installed": st.get("installed"), "version": st.get("version"), "pinned": pin, "where": st.get("where"),
               "acceptance_current": st.get("acceptance_current"), "route": st.get("route"), "registered": regs,
               "scripting_pref": st.get("scripting_pref")}
    names = {"claude-code": "Claude Code", "codex": "Codex", "openclaw": "OpenClaw"}
    where_regs = ", ".join(names.get(k, k) for k in sorted(regs)) or "no agent"
    if not st.get("installed"):
        return R.yellow(rid, "Higgsfield's AE bridge isn't installed. Finishing and Slate's own ExtendScript builds work "
                             "without it; building scenes through the bridge needs it.",
                        f"`slate connect aftereffects` installs it at {pin} and registers it (you run it).", group=G,
                        details=details, install=item)
    if not st.get("pin_ok"):
        return R.yellow(rid, f"The AE bridge is {st.get('version')}, but Slate pins {pin}, so scenes build with Slate's "
                             "own ExtendScript library until the pin is back.",
                        f"slate ae bridge install --yes (reinstalls {pin}), or run the acceptance on this version first: "
                        "slate ae bridge acceptance --go", group=G, details=details, install=item)
    if "claude-code" not in regs:
        return R.yellow(rid, f"The AE bridge {pin} is installed but not registered with Claude Code, so no session can "
                             "use it.", "`slate connect aftereffects` registers it (you run it).", group=G,
                        details=details)
    if not st.get("acceptance_current"):
        return R.yellow(rid, f"The AE bridge {pin} is installed (registered with {where_regs}), but it hasn't passed "
                             "Slate's acceptance test on this version, so scenes build with Slate's own ExtendScript "
                             "library for now.",
                        f"With After Effects open and {PREF} on: slate ae bridge acceptance --go (you start it).",
                        group=G, details=details)
    return R.green(rid, f"Higgsfield's AE bridge {pin} (pinned) is installed, accepted, and registered with "
                        f"{where_regs}; scenes can build through it.", group=G, details=details)


def _smoke(env: Env) -> dict:
    rid = "ae.smoke"
    try:
        res = env.ae_smoke_last()
    except Exception as exc:  # noqa: BLE001
        return R.yellow(rid, f"Couldn't read the last smoke test: {getattr(exc, 'message', exc)}",
                        "Run `slate connect aftereffects` (you start AE; it renders a 1-second test).", group=G)
    status = getattr(res, "status", None) or (res.get("status") if isinstance(res, dict) else None)
    summary = getattr(res, "summary", None) or (res.get("summary") if isinstance(res, dict) else "")
    when = getattr(res, "checked_at", None) or (res.get("checked_at") if isinstance(res, dict) else None)
    details = {"status": status, "checked_at": when}
    if status == "PASS":
        return R.green(rid, f"The last 1-second smoke test passed ({when or 'date unknown'}): {summary}", group=G,
                       details=details)
    if status == "FAIL":
        return R.red(rid, f"The last smoke test failed ({when or 'date unknown'}): {summary}",
                     "Run `slate connect aftereffects` (you start AE): it runs the test again and says what broke.",
                     group=G, details=details)
    return R.yellow(rid, "The After Effects smoke test hasn't run on this Mac yet, so the AE lane isn't proven here.",
                    "Run `slate connect aftereffects` in your own Terminal (it opens AE and renders one second).",
                    group=G, details=details)


def _deferred(host: str, smoke: dict, details: dict, *, closed_while_looking: bool = False) -> dict:
    """AE isn't open, so doctor can't ask it (asking would launch it). Green only when the last smoke test proved the
    permission; otherwise it's unchecked, and unchecked is never green."""
    rid = "ae.automation"
    details["deferred"] = True
    lead = ("After Effects closed while doctor looked, so it couldn't be asked" if closed_while_looking else
            "After Effects isn't open (doctor never launches it to test this)")
    if (smoke.get("details") or {}).get("status") == "PASS":
        details["proven_by"] = "the last smoke test"
        return R.green(rid, f"macOS's Automation permission to control After Effects was proven by the last smoke test, "
                            f"for the app that ran it. {lead}; if a different app ({host}) runs an AE step, macOS asks "
                            "you once at that step.", group=G, details=details)
    return R.yellow(rid, f"macOS's Automation permission ({host} controlling After Effects) isn't checked yet. {lead}, "
                         "and no smoke test has proven it.",
                    "Run `slate connect aftereffects` in your own Terminal: its 1-second smoke test proves the "
                    "permission (macOS asks you the first time). Or run `slate doctor` while After Effects is open.",
                    group=G, details=details)


def _automation(env: Env, ctx: Ctx, smoke: dict) -> dict:
    rid = "ae.automation"
    host = ctx.host_app
    details: dict = {"host": host, "bundle_id": AE_BUNDLE_ID}
    if not env.ae_running():
        return _deferred(host, smoke, details)
    ran = env.run(["/usr/bin/osascript", "-e", AUTOMATION_SCRIPT], timeout=20, cache=False)
    details["deferred"] = False
    text = ran.text
    fix = (f"Only you can allow it: System Settings > Privacy & Security > Automation, then turn on After Effects "
           f"under {host}.")
    if ran.ok and "not running" in ran.stdout:
        return _deferred(host, smoke, details, closed_while_looking=True)
    if ran.ok:
        return R.green(rid, f"{host} may control After Effects (macOS's Automation permission; AE was already open, and "
                            f"answered version {first_line(ran.stdout)}).", group=G, details=details)
    if "-1743" in text or "not authorized" in text.lower() or "not allowed" in text.lower():
        return R.red(rid, f"macOS doesn't let {host} control After Effects (the Automation permission is off).", fix,
                     group=G, details=details)
    if "-1712" in text or "timed out" in text.lower():
        return R.yellow(rid, "After Effects didn't answer within 10 seconds: it may be busy, or macOS may be showing you "
                             f"a prompt asking whether {host} may control it.",
                        "If macOS asks, choose Allow; then run `slate doctor` again.", group=G, details=details)
    return R.yellow(rid, f"Couldn't check the Automation permission ({first_line(text) or f'exit {ran.returncode}'}).",
                    "It's checked again at the first supervised AE step.", group=G, details=details)
