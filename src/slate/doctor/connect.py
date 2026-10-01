"""`slate connect`: the connect steps of Slate's promise, each safe to run from an agent.

- **claude**: Slate's skills, command-line tool and MCP server go into Claude Code, and into Codex and OpenClaw when
  they're on this Mac. The installer (slate.install) does the linking; this changes each agent's own settings, so it
  runs for a person at their Terminal, or for an agent with `--yes` (the person's yes, passed on).
- **higgsfield**: the higgsfield CLI's sign-in opens a browser, so Slate never runs it, from an agent or anywhere
  else: it prints `higgsfield auth login` for the person to run in their own Terminal. `--api-key` stores the Cloud
  API key (the one lane Slate generates through) in the Keychain, typed by the person into macOS's own prompt.
- **elevenlabs**: stores the API key the same way. `--oneiro` stores Oneiro's separate, capped key for autonomous
  films, with instructions for creating it (a credit quota; text to speech, music and alignment only).
- **aftereffects**: finds AE and aerender, reports the permissions and the brand's fonts, installs Higgsfield's AE
  bridge at its pin and registers it, then renders the 1-second smoke test. A person running this is the go; an agent
  needs `--go`, passed only on the person's say-so.

Every step returns (exit code, machine result, human text), so `--json` prints one object and a Terminal reads well.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any, Callable

from slate import log
from slate.doctor import agents as agents_mod
from slate.doctor import aftereffects as ae_mod
from slate.doctor import fonts as fonts_mod
from slate.doctor import keys as keys_mod
from slate.doctor.common import default_browser, tilde
from slate.doctor.core import make_ctx
from slate.doctor.env import Env
from slate.errors import MissingPrerequisite, SlateError

Result = tuple[int, dict, str]


def _say_err(text: str) -> None:
    """Words for the person at the prompt: stderr, so stdout stays one JSON object under --json."""
    print(text, file=sys.stderr, flush=True)


def _q(path: Any) -> str:
    return shlex.quote(str(path))


# ---------------------------------------------------------------------------------------------------------------
# claude


def detected_agents(env: Env) -> list[str]:
    found = ["claude"]
    if env.which("codex") or env.is_dir(env.home / ".codex"):
        found.append("codex")
    if env.which("openclaw") or env.is_dir(env.home / ".openclaw"):
        found.append("openclaw")
    return found


def installer() -> Callable[..., dict] | None:
    """The installer's `wire(agents, *, dry_run)` when this checkout has it (slate.install.core.wire)."""
    try:
        from slate.install import core as icore  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - not built, or broken mid-update: fall back to printing the steps
        return None
    fn = getattr(icore, "wire", None)
    return fn if callable(fn) else None


def plan_steps(env: Env, agents: list[str]) -> list[dict]:
    """The steps the installer takes, as commands a person could run themselves."""
    repo = env.repo()
    names, built = agents_mod.slate_skills(env)
    shim = env.home / ".local" / "bin" / "slate"
    steps: list[dict] = []
    if not built:
        steps.append({"what": "This checkout has no skills yet (skills/ is empty); their links wait for them.",
                      "command": None})
    if built and "claude" in agents:
        steps += [{"what": f"link the {n} skill for Claude Code",
                   "command": f"ln -s {_q(repo / 'skills' / n)} ~/.claude/skills/{n}"} for n in names]
    if built and ({"codex", "openclaw"} & set(agents)):
        steps += [{"what": f"link the {n} skill for Codex and OpenClaw",
                   "command": f"ln -s {_q(repo / 'skills' / n)} ~/.agents/skills/{n}"} for n in names]
    steps.append({"what": "put the slate command on PATH",
                  "command": f"ln -s {_q(repo / '.venv' / 'bin' / 'slate')} ~/.local/bin/slate"})
    if "openclaw" in agents:
        steps.append({"what": "put it on the OpenClaw gateway's PATH too",
                      "command": f"ln -s {_q(repo / '.venv' / 'bin' / 'slate')} /opt/homebrew/bin/slate"})
    steps.append({"what": "register Slate's MCP server with Claude Code",
                  "command": f"claude mcp add --scope user slate -- {_q(shim)} mcp"})
    if "codex" in agents:
        steps.append({"what": "register it with Codex", "command": f"codex mcp add slate -- {_q(shim)} mcp"})
    if "openclaw" in agents:
        # OpenClaw 2026.9.4's form; it probes the server before saving, and `slate mcp` needs more than its default
        # 5 seconds to answer on a busy Mac (slate.install.mcp_register explains).
        steps.append({"what": "register it with OpenClaw",
                      "command": f"openclaw mcp add slate --command {_q(shim)} --arg mcp --connect-timeout 30"})
    return steps


def _summarize_install(out: dict) -> dict:
    links = out.get("links") or {}
    return {
        "ok": bool(out.get("ok", True)),
        "dry_run": bool(out.get("dry_run")),
        "failures": out.get("failures") or [],
        "next_steps": out.get("next_steps") or [],
        "links": {"changed": links.get("changed"), "kept": links.get("kept"),
                  "moved_aside": len(links.get("backups") or []), "errors": links.get("errors") or []},
        "mcp": [{k: e.get(k) for k in ("client", "state", "did", "next") if e.get(k) is not None}
                for e in (out.get("mcp") or []) if isinstance(e, dict)],
    }


def _install_lines(out: dict) -> list[str]:
    try:
        from slate.install import core as icore  # noqa: PLC0415

        lines = icore.summary_lines(out)
        if lines:
            return [str(x) for x in lines]
    except Exception:  # noqa: BLE001
        pass
    s = _summarize_install(out)
    lines = [f"links and shims: {s['links']['changed']} changed, {s['links']['kept']} already right"]
    lines += [f"MCP {e.get('client')}: {e.get('did') or e.get('state')}" for e in s["mcp"]]
    lines += [f"next: {n}" for n in s["next_steps"]]
    return lines


def claude(env: Env, *, yes: bool = False, dry_run: bool = False) -> Result:
    if not env.which("claude"):
        raise MissingPrerequisite("Claude Code isn't installed on this Mac.",
                                  hint="Install Claude Code, then run `slate connect claude` again.")
    agents = detected_agents(env)
    steps = plan_steps(env, agents)
    wire = installer()
    at_terminal, why = env.person_at_terminal()
    result: dict = {"service": "claude", "agents": agents, "steps": steps, "done": False}
    listing = "\n".join(f"  {s['what']}" + (f"\n      {s['command']}" if s.get("command") else "") for s in steps)
    who = " and ".join({"claude": "Claude Code", "codex": "Codex", "openclaw": "OpenClaw"}[a] for a in agents)
    if dry_run:
        result["dry_run"] = True
        if wire:
            try:
                out = wire(agents, dry_run=True)
            except SlateError as exc:
                result["installer_error"] = exc.message
            else:
                result["installer"] = _summarize_install(out)
                lines = "\n".join(f"  {line}" for line in _install_lines(out))
                return 0, result, f"Dry run: nothing changed. Slate's installer would do this for {who}:\n{lines}"
        return 0, result, f"Connecting Slate to {who} would take these steps (dry run: nothing changed):\n{listing}"
    if wire is None:
        result["why"] = "Slate's installer (slate.install) isn't in this checkout, so these are its steps to run yourself."
        result["next"] = "Run the steps above in your own Terminal, or update Slate and run `slate connect claude` again."
        return 3, result, (f"Slate's installer isn't in this checkout yet, so here's what it would do for {who}. Run "
                           f"these in your own Terminal, or update Slate and run `slate connect claude` again:\n{listing}")
    if not at_terminal and not yes:
        result["why"] = f"Linking Slate in changes each agent's own settings, so it waits for a person's yes ({why})."
        result["next"] = ("Ask the person. With their yes, run `slate connect claude --yes`; or they run `slate "
                          "connect claude` in their own Terminal.")
        return 3, result, (f"This links Slate into {who}, which changes their own settings, so it waits for your yes "
                           f"({why}). With it, run `slate connect claude --yes` (an agent passes --yes only on your "
                           f"say-so), or run `slate connect claude` in your own Terminal. The steps:\n{listing}")
    out = wire(agents, dry_run=False)
    summary = _summarize_install(out)
    result.update(done=summary["ok"], installer=summary)
    human = [f"Connected Slate to {who}." if summary["ok"] else f"Connecting Slate to {who} hit a problem:"]
    human += [f"  {line}" for line in _install_lines(out)]
    human.append("Run `slate doctor` to see every agent's skills, MCP server and CLI checked.")
    return (0 if summary["ok"] else 1), result, "\n".join(human)


# ---------------------------------------------------------------------------------------------------------------
# higgsfield

HF_KEY_GUIDE = ("Your Higgsfield Cloud API key is a key id and a secret joined by a colon (id:secret), from Higgsfield's "
                "Cloud dashboard. It's the one lane Slate generates through, billed to your Cloud API balance.")


def _hf_key_status(env: Env) -> dict:
    row = keys_mod._higgsfield(env, make_ctx(env))
    return {"status": row["status"], "summary": row["summary"], "fix": row.get("fix"),
            "where": env.secret_where("higgsfield")}


def higgsfield(env: Env, *, api_key: bool = False, check: bool = False) -> Result:
    browser = default_browser(env)
    pin = str(env.registry_tool("higgsfield-cli").get("version") or "1.1.26")
    cli = env.which("higgsfield")
    creds = env.home / ".config" / "higgsfield" / "credentials.json"
    signed_in = bool(cli) and env.is_file(creds)
    login = "higgsfield auth login"
    result: dict = {"service": "higgsfield", "cli": {"path": cli, "pinned": pin}, "signed_in": signed_in,
                    "sign_in_command": login, "ran_sign_in": False}
    lines: list[str] = []
    code = 0
    if api_key and not check:
        # Refuses (exit 1) outside a person's own Terminal: keys are only ever typed by a person.
        at_terminal, _why = env.person_at_terminal()
        if at_terminal:
            _say_err(HF_KEY_GUIDE)
        rc = env.store_key("higgsfield")
        if rc != 0:
            raise SlateError(f"The key wasn't stored (macOS's `security` exited with {rc}).",
                             hint="Run `slate connect higgsfield --api-key` again in your own Terminal.")
        key = _hf_key_status(env)
        result["key"] = key
        lines.append(f"Cloud API key stored in your login Keychain. {key['summary']}")
        code = 1 if key["status"] == "red" else 0  # stored; only a rejection means it didn't work
    else:
        key = _hf_key_status(env)
        result["key"] = key
        lines.append(key["summary"] if key["status"] == "green" else
                     f"{key['summary']} ({key.get('fix') or 'slate connect higgsfield --api-key'})")
    if not cli:
        lines.append(f"The higgsfield CLI isn't installed. Install it with `npm install --global @higgsfield/cli@{pin}` "
                     "(or `slate doctor --install`), then sign in.")
        result["next"] = f"npm install --global @higgsfield/cli@{pin}"
        if not api_key:
            code = 3
    elif signed_in:
        lines.append("The higgsfield CLI is signed in (a sign-in is on file). `slate doctor` asks it for your plan on a "
                     "live run.")
    else:
        lines.append(f"To sign the higgsfield CLI in, run this in your own Terminal:\n\n    {login}\n\nIt opens "
                     f"{browser.get('name') or 'your default browser'} so you can sign in. Slate never runs it for you, "
                     "and never opens a browser.")
        result["next"] = login
        if not api_key:
            code = 3
    return code, result, "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# elevenlabs

KEY_GUIDE = ("Create an API key in your ElevenLabs account (its API keys settings) and paste it at the prompt below. "
             "It goes straight into your login Keychain; it never appears on screen or in chat.")


def oneiro_guide(cap: float | None) -> str:
    cap_text = f"{cap:,.0f} credits a month" if isinstance(cap, (int, float)) and cap else "its monthly cap"
    return ("Oneiro's key is a second ElevenLabs key, made only for autonomous films, so ElevenLabs itself backs "
            "Slate's cap:\n"
            "  1. In ElevenLabs, create a new API key and name it for Oneiro.\n"
            f"  2. Give it a credit quota. Slate caps autonomous films at {cap_text}, so set the quota at or under that.\n"
            "  3. Allow only text to speech, music and alignment (forced alignment). Leave everything else off, so it "
            "can't design, clone or delete voices, or touch your account.\n"
            "  4. Paste it at the prompt. It goes straight into your login Keychain; it never appears on screen or in "
            "chat.")


def elevenlabs(env: Env, *, oneiro: bool = False, check: bool = False) -> Result:
    name = "elevenlabs_oneiro" if oneiro else "elevenlabs"
    label = "Oneiro's capped ElevenLabs key" if oneiro else "Your ElevenLabs API key"
    command = "slate connect elevenlabs" + (" --oneiro" if oneiro else "")
    guide = oneiro_guide(env.oneiro_cap()) if oneiro else KEY_GUIDE
    at_terminal, why = env.person_at_terminal()
    where = env.secret_where(name)
    result: dict = {"service": "elevenlabs", "key": name, "where": where}
    if check or not at_terminal:
        if not where:
            if oneiro:
                log.say(guide)  # what the person does in ElevenLabs first (JSON carries it in details)
            raise MissingPrerequisite(
                f"{label} isn't set up yet, and keys are only ever typed by a person, in their own Terminal"
                + ("" if check else f" ({why})") + ".",
                hint=f"Open Terminal and run: {command}", details={"how": guide})
        return _eleven_verify(env, oneiro, where, result, stored=False)
    _say_err(guide)
    rc = env.store_key(name)
    if rc != 0:
        raise SlateError(f"The key wasn't stored (macOS's `security` exited with {rc}).",
                         hint=f"Run `{command}` again in your own Terminal.")
    where = env.secret_where(name) or "keychain"
    result["where"] = where
    return _eleven_verify(env, oneiro, where, result, stored=True)


def _eleven_verify(env: Env, oneiro: bool, where: str, result: dict, *, stored: bool) -> Result:
    place = keys_mod._where(where)
    lead = f"Stored in {place}." if stored else f"Found in {place} (checked by presence; the value is never shown)."
    if oneiro:
        result["verified"] = "presence"
        return 0, result, (f"Oneiro's capped ElevenLabs key: {lead} Its narrow permissions don't include reading the plan, "
                           "so Slate checks it on the first autonomous voice line.")
    row = keys_mod._eleven_plan(env, make_ctx(env))
    result["plan"] = {"status": row["status"], "summary": row["summary"], "fix": row.get("fix")}
    code = 1 if row["status"] == "red" else 0
    fix = f" {row['fix']}" if row["status"] != "green" and row.get("fix") else ""
    return code, result, f"ElevenLabs API key: {lead} {row['summary']}{fix}"


# ---------------------------------------------------------------------------------------------------------------
# aftereffects


def _step(steps: list[dict], step: str, status: str, summary: str, **extra: Any) -> dict:
    entry = {"step": step, "status": status, "summary": summary, **{k: v for k, v in extra.items() if v is not None}}
    steps.append(entry)
    return entry


def aftereffects(env: Env, *, go: bool = False, brand: str | None = None, dry_run: bool = False) -> Result:
    at_terminal, why = env.person_at_terminal()
    person_go = go or at_terminal  # a person running connect is the go
    ctx = make_ctx(env, brand=brand)
    installs = env.ae_installs()
    if not installs:
        raise MissingPrerequisite("After Effects isn't installed on this Mac.",
                                  hint="Install it from Creative Cloud (paid, and your call: Slate never buys or starts "
                                       "a licence), then run `slate connect aftereffects` again.")
    steps: list[dict] = []
    app = ae_mod._app(env, installs)
    _step(steps, "find After Effects", "ok", app["summary"])
    aer = ae_mod._aerender(env)
    if aer["status"] == "red":
        raise MissingPrerequisite(aer["summary"], hint=aer.get("fix"))
    _step(steps, "find aerender", "ok", aer["summary"])
    pref = ae_mod._pref(env)
    _step(steps, "scripting permission", "ok" if pref["status"] == "green" else "note", pref["summary"],
          fix=pref.get("fix") if pref["status"] != "green" else None)
    _step(steps, "Automation permission", "note", f"The smoke test proves that {ctx.host_app} may control After Effects "
                                                  "(macOS asks you the first time).")
    font_rows: list[dict] = []
    families: list[str] = []
    if brand:
        font_rows = fonts_mod.check_fonts(env, ctx)
        pack, _where = fonts_mod.load_brand(env, brand)
        families = [f["family"] for f in fonts_mod.families(pack)]
        bad = [r for r in font_rows if r["status"] != "green"]
        _step(steps, "brand fonts on this Mac", "ok" if not bad else "note",
              f"{len(font_rows) - len(bad)} of {len(font_rows)} families available"
              + (f"; not available: {', '.join((r.get('details') or {}).get('family') or r['id'] for r in bad)}"
                 if bad else ""),
              fix=bad[0].get("fix") if bad else None)
    # The bridge: how it's installed here, from its MCP registration (read-only), then install and register at the pin.
    regs = ae_mod.bridge_registrations(env)
    st = env.ae_bridge_status()
    pin = st.get("pinned") or env.ae_bridge_pin()
    reg = regs.get("claude-code") or {}
    if reg.get("package_dir"):
        where_text = (f"registered with Claude Code as {reg.get('name')!r}, launched by {reg.get('launcher') or 'node'} "
                      f"from {tilde(env, reg['package_dir'])}")
        if st.get("where") and Path(st["where"]) != Path(reg["package_dir"]):
            where_text += f"; the installed copy Slate found is at {tilde(env, st['where'])}"
    else:
        where_text = "not registered with Claude Code yet"
    _step(steps, "the AE bridge: how it's installed", "ok", where_text, registered=sorted(regs) or None)
    need_install = not st.get("installed") or not st.get("pin_ok")
    need_register = "claude-code" not in regs and bool(env.which("claude"))
    result: dict = {"service": "aftereffects", "go": person_go, "steps": steps, "fonts": font_rows or None}
    # the bridge's one policy, as every registration has it (L-ae-025, L-packaging-D004)
    from slate.ae import bridge as bridge_mod  # noqa: PLC0415

    policy = [x for k, v in bridge_mod.POLICY_ENV.items() for x in ("-e", f"{k}={v}")]
    if dry_run:
        if need_install:
            _step(steps, "install the AE bridge", "would",
                  f"npm install --global --ignore-scripts fnf-after-effects-mcp@{pin}")
        if need_register:
            _step(steps, "register the AE bridge", "would",
                  f"claude mcp add --scope user {ae_mod.BRIDGE_SERVER} {' '.join(policy)} -- node …/dist/index.js "
                  "(arbitrary eval off, its operations limited to the categories Slate's scenes use)")
        _step(steps, "1-second smoke test", "would", "build, render and measure one second in After Effects")
        result["dry_run"] = True
        return 0, result, _ae_text(steps, "Dry run: nothing was installed, and After Effects wasn't started.")
    if need_install:
        if person_go:
            try:
                res = env.ae_bridge_install()
                st = env.ae_bridge_status()
                _step(steps, "install the AE bridge", "done", f"fnf-after-effects-mcp {res.get('version') or pin} "
                                                             "installed (npm, global, pinned exactly)")
            except SlateError as exc:
                _step(steps, "install the AE bridge", "failed", exc.message, fix=exc.hint)
        else:
            _step(steps, "install the AE bridge", "waiting", f"fnf-after-effects-mcp {pin} installs when you run this "
                                                             "yourself (or an agent passes --go on your say-so).")
    else:
        _step(steps, "the AE bridge", "ok", f"fnf-after-effects-mcp {st.get('version')} is installed at the pin")
    if need_register:
        pkg = Path(st.get("where") or "/opt/homebrew/lib/node_modules/fnf-after-effects-mcp")
        node = env.which("node") or "node"
        argv = [env.which("claude") or "claude", "mcp", "add", "--scope", "user", ae_mod.BRIDGE_SERVER, *policy, "--",
                node, str(pkg / "dist" / "index.js")]
        if person_go and st.get("installed"):
            ran = env.run(argv, timeout=60, cache=False)
            if ran.ok:
                _step(steps, "register the AE bridge", "done", "registered with Claude Code (user scope)")
            else:
                _step(steps, "register the AE bridge", "failed", f"`claude mcp add` exited with {ran.returncode}",
                      fix=" ".join(shlex.quote(a) for a in argv))
        else:
            _step(steps, "register the AE bridge", "waiting", "registers with Claude Code once it's installed, when you "
                                                              "run this yourself")
    if not person_go:
        _step(steps, "1-second smoke test", "waiting", "It starts After Effects, so it waits for a person's go.")
        result["why"] = why
        result["next"] = ("Ask the person. With their go, run `slate connect aftereffects --go`; or they run `slate "
                          "connect aftereffects` in their own Terminal.")
        return 1, result, _ae_text(steps, "After Effects wasn't started: the smoke test waits for a person's go. With "
                                          "it, run `slate connect aftereffects --go` (an agent passes --go only on your "
                                          "say-so), or run `slate connect aftereffects` in your own Terminal.")
    from slate.ae import guard  # noqa: PLC0415

    try:
        guard.person_start(True, "The After Effects smoke test")
        res = env.ae_smoke_run(None)
    except SlateError as exc:
        _step(steps, "1-second smoke test", "failed", exc.message, fix=exc.hint)
        result["smoke"] = {"status": "error", "message": exc.message}
        return exc.exit_code, result, _ae_text(steps, "The smoke test didn't run to the end.")
    status = getattr(res, "status", "UNCHECKED")
    summary = getattr(res, "summary", "")
    result["smoke"] = res.to_dict() if hasattr(res, "to_dict") else {"status": status, "summary": summary}
    if status != "PASS":
        _step(steps, "1-second smoke test", "failed", summary)
        return 1, result, _ae_text(steps, "The After Effects lane isn't proven yet: the smoke test didn't pass.")
    _step(steps, "1-second smoke test", "done", summary)
    if families:
        try:
            rep = env.ae_font_check(families)
            missing = list(rep.get("missing") or [])
            _step(steps, "brand fonts in After Effects", "ok" if not missing else "failed",
                  "After Effects sees every brand family" if not missing else
                  f"After Effects doesn't see: {', '.join(missing)}",
                  fix="Install or activate those families, then restart After Effects." if missing else None)
        except SlateError as exc:
            _step(steps, "brand fonts in After Effects", "failed", exc.message, fix=exc.hint)
    failed = [s for s in steps if s["status"] == "failed"]
    return (1 if failed else 0), result, _ae_text(
        steps, "After Effects is connected." if not failed else "After Effects is connected, with the problems above.")


def _ae_text(steps: list[dict], closing: str) -> str:
    lines = []
    for s in steps:
        lines.append(f"  {s['status']:<8} {s['step']}: {s['summary']}")
        if s.get("fix") and s["status"] not in ("ok", "done"):
            lines.append(f"           fix: {s['fix']}")
    lines.append(closing)
    return "\n".join(lines)
