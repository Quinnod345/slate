"""The Mac itself and Slate's own moving parts: macOS, disk, the default browser, the job agent, OCR, Apple's sampled
instruments, modules that didn't import, and the Remotion licence reminder."""

from __future__ import annotations

import sys
from pathlib import Path

from slate.doctor import rows as R
from slate.doctor.common import Ctx, first_line, tilde
from slate.doctor.env import Env

DISK_COMFORT_GB = 30.0  # a 6-minute master alone is about 10 GB, and AE's disk cache once took 60 GB
GARAGEBAND = ("Open GarageBand and choose GarageBand > Sound Library > Download All Available Sounds (free). Slate "
              "never downloads or copies Apple's sounds itself.")


def not_macos_row(env: Env) -> dict:
    return R.red("host.macos", f"Slate runs on macOS only for now, and this is {env.system()} {env.release()}. It relies "
                               "on the Keychain, osascript, Apple's Vision OCR and Apple's sound libraries.",
                 "Run Slate on a Mac. Other hosts come later.", group="host",
                 details={"system": env.system(), "release": env.release()})


def check_macos(env: Env, ctx: Ctx) -> list[dict]:
    version, arch = env.mac_version(), env.arch()
    details = {"macos": version, "arch": arch}
    if arch != "arm64":
        return [R.yellow("host.macos", f"macOS {version} on {arch}. Slate is built and tested on Apple silicon; an Intel "
                                       "Mac is untested.", "Nothing to do if it works; report what breaks.",
                         group="host", details=details)]
    return [R.green("host.macos", f"macOS {version} on Apple silicon ({arch})", group="host", details=details)]


# ---------------------------------------------------------------------------------------------------------------
# Disk


def check_disk(env: Env, ctx: Ctx) -> list[dict]:
    reserve = float(((env.settings().get("jobs") or {}).get("disk_reserve_gb")) or 5.0)
    places = [("films", env.films_dir()), ("Slate's data", env.data_dir())]
    seen: dict = {}
    for what, path in places:
        dev = env.device(path)
        if dev in seen:
            seen[dev]["what"].append(what)
            continue
        seen[dev] = {"what": [what], "path": str(path), "free_gb": round(env.disk_free(path) / 1e9, 1)}
    disks = list(seen.values())
    worst = min(disks, key=lambda d: d["free_gb"])
    details = {"disks": disks, "reserve_gb": reserve, "comfort_gb": DISK_COMFORT_GB}
    where = f"{' and '.join(worst['what'])} ({tilde(env, worst['path'])})"
    if worst["free_gb"] < reserve:
        return [R.red("system.disk", f"Only {worst['free_gb']:g} GB free on the disk that holds {where}. Slate won't start "
                                     f"a job below {reserve:g} GB.",
                      "Free up space (old renders and After Effects' disk cache are the usual culprits), then run "
                      "`slate doctor` again.", group="system", details=details)]
    if worst["free_gb"] < DISK_COMFORT_GB:
        return [R.yellow("system.disk", f"{worst['free_gb']:g} GB free for {where}: enough for short films, tight for a "
                                        "brand film (a 6-minute master alone is about 10 GB).",
                         "Free up some space before a long film.", group="system", details=details)]
    text = "; ".join(f"{d['free_gb']:g} GB free for {' and '.join(d['what'])}" for d in disks)
    return [R.green("system.disk", text, group="system", details=details)]


# ---------------------------------------------------------------------------------------------------------------
# The default browser, and every step that would open it


def browser_steps(env: Env, ctx: Ctx) -> list[dict]:
    """Each thing in Slate's world that could open a web page, what stops it, and who runs it."""
    from slate import procs  # noqa: PLC0415

    name = ctx.browser_name
    child_ok = procs.child_env().get("BROWSER") == "none"
    config_ok = None
    try:
        folder, _source = env.engine_dir()
        text = env.read_text(Path(folder) / "remotion" / "remotion.config.ts") or ""
        config_ok = "setShouldOpenBrowser(false)" in text.replace(" ", "")
    except Exception:  # noqa: BLE001 - no engine yet: the engine rows say so
        config_ok = None
    aside = env.which("aside")
    return [
        {"step": "Every command Slate runs", "opens": False, "guard": "BROWSER=none on every child process "
         "(slate.procs)", "verified": child_ok},
        {"step": "Remotion Studio", "opens": f"would open {name}", "guard": "always --no-open, and "
         "setShouldOpenBrowser(false) in the engine's remotion.config.ts", "verified": config_ok},
        {"step": "higgsfield auth login", "opens": f"opens {name} to sign in", "guard": "Slate never runs it: `slate "
         "connect higgsfield` prints it for you to run in your own Terminal", "who": "you", "verified": True},
        {"step": "Signing an agent into an MCP server (claude /mcp, codex mcp login, openclaw mcp login)",
         "opens": f"opens {name}", "guard": "Slate never runs these", "who": "you", "verified": True},
        {"step": "Web captures and web research", "opens": False, "guard": "Aside only (~/.local/bin/aside)",
         "verified": bool(aside)},
        {"step": "The open web renderer (puppeteer)", "opens": False, "guard": "headless, and it loads only Slate's own "
         "local pages", "verified": True},
    ]


def check_browser(env: Env, ctx: Ctx) -> list[dict]:
    rid = "system.browser"
    info = ctx.browser or {}
    steps = browser_steps(env, ctx)
    details = {"default": info, "steps": steps}
    unverified = [s["step"] for s in steps if s.get("verified") is False]
    name = f"{info.get('name')} ({info.get('bundle_id')})" if info.get("name") != info.get("bundle_id") else info.get(
        "bundle_id")
    base = (f"Your default browser is {name}. Nothing Slate runs opens it. Two steps would, and you run both yourself: "
            "`higgsfield auth login`, and signing an agent into an MCP server.")
    if unverified:
        return [R.yellow(rid, f"Your default browser is {name}, and this guard couldn't be confirmed: "
                              f"{'; '.join(unverified)}.",
                         "If Aside is the missing piece, install it; otherwise run `slate engine install` so the engine's "
                         "Remotion config (Studio never opens a browser) is in place.", group="system", details=details)]
    return [R.green(rid, base, group="system", details=details)]


# ---------------------------------------------------------------------------------------------------------------
# The job agent, OCR, modules


def _queue_words(q: dict | None) -> tuple[str, int]:
    """'the queue is empty' or '1 running, 2 queued', and how many jobs are waiting."""
    counts = (q or {}).get("counts") or {}
    queued, running = int(counts.get("queued") or 0), int(counts.get("running") or 0)
    if q is None:
        return "the queue itself couldn't be read", 0
    if not queued and not running:
        return "the queue is empty", 0
    return f"{running} running, {queued} queued", queued


def check_jobs_agent(env: Env, ctx: Ctx) -> list[dict]:
    """The launchd agent that keeps the queue going, and the runner and queue right now (`slate jobs status`).

    The agent runs `slate jobs serve` with KeepAlive, so a loaded agent with no runner serving means the runner keeps
    exiting (launchd restarts it every 10 seconds): that's a fault, not an idle queue.
    """
    rid = "system.jobs-agent"
    try:
        st = env.jobs_agent()
    except Exception as exc:  # noqa: BLE001
        return [R.yellow(rid, f"Couldn't read the job queue's launchd agent: {getattr(exc, 'message', exc)}",
                         "Run `slate jobs status` to see why.", group="system")]
    try:
        q = env.jobs_summary()
    except Exception:  # noqa: BLE001 - the agent's state still says most of it
        q = None
    label = st.get("label") or "the job agent"
    serving = bool(q.get("runner")) if isinstance(q, dict) else bool(st.get("runner_serving"))
    queue, waiting = _queue_words(q if isinstance(q, dict) else None)
    details = {k: st.get(k) for k in ("label", "installed", "loaded", "pid", "state", "last_exit", "plist", "log")}
    details.update({"runner_serving": serving, "queue": (q or {}).get("counts"), "next": (q or {}).get("next"),
                    "running": (q or {}).get("running")})
    item = {"id": "jobs-agent", "what": "the job queue's launchd agent (a login item that keeps long jobs going after "
                                        "the agent that started them has gone)", "call": "jobs-agent",
            "shows": "slate jobs agent install --yes", "kind": "slate"}
    runner = "a runner is serving it" if serving else "no runner is serving it"
    stuck = ""
    if waiting and not serving:
        many = waiting != 1
        stuck = (f" {waiting} job{'s are' if many else ' is'} waiting with nothing running {'them' if many else 'it'}: "
                 "the next `slate job` command starts a runner, or run `slate jobs serve --once`.")
    if st.get("loaded"):
        if serving:
            return [R.green(rid, f"The job queue's launchd agent ({label}) is loaded and its runner is serving the queue "
                                 f"({queue}).", group="system", details=details)]
        exit_note = f" (its last exit: {st['last_exit']})" if st.get("last_exit") else ""
        return [R.yellow(rid, f"The job queue's launchd agent ({label}) is loaded, but no runner is serving the queue"
                              f"{exit_note}. launchd restarts it every 10 seconds, so it's most likely failing as it "
                              f"starts ({queue}).{stuck}",
                         f"Read its log ({st.get('log') or 'the jobs log in the data folder'}); `slate jobs serve "
                         "--once` in your own Terminal shows the same error directly.", group="system",
                         details=details)]
    if st.get("installed"):
        return [R.yellow(rid, f"The job queue's launchd agent ({label}) is installed but not loaded, so nothing restarts "
                              f"the queue after a reboot. Right now {runner} ({queue}).{stuck}",
                         "Run `slate jobs agent install` in your own Terminal to load it again.", group="system",
                         details=details, install=item)]
    return [R.yellow(rid, "The job queue's launchd agent isn't installed. Jobs still run (each `slate job` command starts a "
                          f"one-shot runner), but nothing keeps the queue going after a reboot. Right now {runner} "
                          f"({queue}).{stuck}",
                     "Run `slate jobs agent install` in your own Terminal (it adds a login item, so it's your call), or "
                     "say yes to it in `slate doctor --install`.", group="system", details=details, install=item)]


def check_ocr(env: Env, ctx: Ctx) -> list[dict]:
    rid = "system.ocr"
    try:
        ok, info = env.ocr_available()
    except Exception as exc:  # noqa: BLE001
        ok, info = False, {"reason": getattr(exc, "message", str(exc))}
    if ok:
        return [R.green(rid, f"OCR runs: {info.get('name')} {info.get('version') or ''} at the "
                             f"{info.get('recognition_level')} level (the one engine every OCR gate uses)".replace(
                                 "  ", " "), group="system", details=info)]
    uv = env.which("uv")
    reqs = env.ocr_requirements() or ["ocrmac>=1.0"]
    item = {"id": "ocr", "what": "Slate's OCR extra (ocrmac, Apple Vision) in Slate's own Python",
            "argv": [uv or "uv", "pip", "install", "--python", sys.executable, *reqs], "kind": "uv"}
    if not uv:
        item["needs"] = "uv (brew install uv) comes first."
    return [R.red(rid, f"OCR can't run ({info.get('reason') or 'unknown reason'}), so every OCR gate (read time, on-screen "
                       "banned words) stays UNCHECKED and no film can pass QA.",
                  "`slate doctor --install` adds Slate's ocr extra (ocrmac) to its Python, asking first.", group="system",
                  details=info, install=item)]


def check_render_stability(env: Env, ctx: Ctx) -> list[dict]:
    """The render-stability conformance procedure's last result on this Mac (`slate qa stability conformance`: the
    engine kit's stability bench rendered here and scanned for pops, snaps and wobbles). It stays UNCHECKED until the
    kit's bench exists, which isn't something to fix on this Mac, so that row is green and says so."""
    rid, group = "engine.render-stability", "engine"
    run = ("Render the engine kit's stability bench (one file per variant, named after it), then run "
           "`slate qa stability conformance RENDERS_FOLDER`.")
    try:
        st = env.stability_conformance()
    except Exception as exc:  # noqa: BLE001
        return [R.yellow(rid, f"Couldn't read the render-stability conformance record: {getattr(exc, 'message', exc)}",
                         run, group=group)]
    rec = st.get("record")
    if not rec:
        if not st.get("bench"):
            return [R.green(rid, "Render-stability conformance is UNCHECKED: the engine kit's stability bench isn't "
                                 "built yet, so there's nothing to run on this Mac. Every act render is still scanned "
                                 "by `slate qa stability`.", group=group, details={"status": "UNCHECKED", "bench": False})]
        return [R.yellow(rid, "The engine kit's stability bench is here, but its conformance hasn't run on this Mac, so "
                              "nothing yet shows this Mac renders motion without pops, snaps or wobbles.", run,
                         group=group, details={"status": "UNCHECKED", "bench": True})]
    res = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    status, when = res.get("status") or "UNCHECKED", res.get("checked_at") or "an unknown time"
    said = first_line(res.get("summary") or "", 200)
    details = {"status": status, "checked_at": res.get("checked_at"), "bench": bool(st.get("bench"))}
    if st.get("spec_sha256") and rec.get("spec_sha256") and st["spec_sha256"] != rec["spec_sha256"]:
        return [R.yellow(rid, f"The stability bench changed after its conformance last ran ({when}), so that result "
                              f"({status}) no longer describes it.", run, group=group, details=details)]
    if status == "PASS":
        return [R.green(rid, f"Render-stability conformance passed on this Mac ({when}): {said}", group=group,
                        details=details)]
    if status == "FAIL":
        return [R.red(rid, f"Render-stability conformance failed on this Mac ({when}): {said}",
                      "Fix what it names (each step is listed by `slate qa stability conformance`), then run it again. "
                      "Act renders made meanwhile may carry the same fault.", group=group, details=details)]
    return [R.yellow(rid, f"Render-stability conformance is {status} ({when}): {said}", run, group=group,
                     details=details)]


def check_modules(env: Env, ctx: Ctx) -> list[dict]:
    rid = "system.modules"
    broken, total = env.broken_modules()
    not_built = {m: r for m, r in broken.items() if r == "not built yet"}
    errors = {m: r for m, r in broken.items() if r != "not built yet"}
    details = {"total": total, "broken": errors, "not_built": sorted(not_built)}
    if errors:
        listed = "; ".join(f"{m} ({first_line(r, 140)})" for m, r in sorted(errors.items()))
        first = sorted(errors)[0]
        return [R.red(rid, f"{len(errors)} of Slate's command modules fail to import, so their commands are missing: "
                           f"{listed}", f"See the full error with `{sys.executable} -c 'import {first}'`; updating Slate "
                                        "(`slate update`, or git pull) usually fixes it.", group="system",
                      details=details)]
    if not_built:
        return [R.yellow(rid, f"{len(not_built)} of Slate's {total} command modules aren't in this checkout yet: "
                              f"{', '.join(sorted(not_built))}. Their commands are missing until they land.",
                         "Update Slate (git pull) once they're built.", group="system", details=details)]
    return [R.green(rid, f"All {total} of Slate's command modules load.", group="system", details=details)]


def check_licence(env: Env, ctx: Ctx) -> list[dict]:
    pin = env.engine_pin()
    return [R.green("system.remotion-licence",
                    "Reminder: Remotion is free for individuals, for evaluation and for teams of up to 3. A company of 4 "
                    "or more needs its own Remotion licence before it publishes a Remotion-rendered film, or can render "
                    "with the open web renderer or After Effects instead. Slate never buys a licence.",
                    group="system", info=True, details={"remotion": pin})]


# ---------------------------------------------------------------------------------------------------------------
# Apple's sampled instruments (slate.sound's own rows)


def check_instruments(env: Env, ctx: Ctx) -> list[dict]:
    try:
        raw = env.instrument_rows()
    except Exception as exc:  # noqa: BLE001
        return [R.red("sound.instruments", f"Couldn't check Apple's sampled instruments: "
                                           f"{getattr(exc, 'message', exc)}", GARAGEBAND, group="sound")]
    if not raw:
        return [R.yellow("sound.instruments", "The starting sound palette names no tonal roles, so there were no "
                                              "instruments to check.", "Nothing to do until a palette names some.",
                         group="sound")]
    try:
        download = env.instruments_download()
    except Exception:  # noqa: BLE001
        download = GARAGEBAND
    out = []
    for r in raw:
        status = r.get("status") if r.get("status") in R.STATUSES else "red"
        fix = r.get("fix") or (download if status != "green" else None)
        summary = r.get("summary") or r.get("id")
        if status == "red":
            summary = (f"{summary}. Tonal roles play on Apple's own sampled instruments, and Slate never synthesizes a "
                       "stand-in.")
        out.append(R.row(str(r.get("id")), status, summary, fix, group="sound"))
    return out
