"""Higgsfield's After Effects bridge (fnf-after-effects-mcp), wrapped: the creative engine's second route into AE.

The bridge is a local MCP server that runs its own ExtendScript dispatcher inside AE through a file mailbox. It
offers about 200 native operations and 13 motion-design skills, which Slate uses as AE looks. Slate depends on it
and pins it (registry.json: fnf-after-effects-mcp, exactly 0.1.3); it never vendors it. Its runtime is MIT, but its
skill corpus carries no redistribution grant, so the looks are referenced by name here and fetched from the bridge
at the moment they're used, never copied into Slate.

What this module does:

- `status()`: which version is installed and whether it matches the pin, whether AE's "Allow Scripts to Write Files
  and Access Network" is on (read through slate.ae.prefs.check, never changed; the bridge needs it for its mailbox,
  and only the person turns it on), the bridge's own offline doctor, the last acceptance run, and which route
  builds scenes today.
- `install()`: the pinned install command (npm, global, its package scripts never run) and how each agent registers
  the server (arbitrary eval off, operations limited to the categories Slate's scenes use); it runs only with the
  person's yes, and never changes anyone's MCP configuration.
- `acceptance()`: a fixed script of operations (data/bridge_acceptance.json) run against a scratch project whenever
  the installed version changes, with AE open on the person's go. A version that hasn't passed isn't used for scenes.
- `call_with_retry()`: the bridge's batches aren't transactional, so a failed or timed-out mutation is never simply
  retried: the project's state is read first, and the step is retried only if it really didn't happen.
- `choose_route()`: the bridge builds scenes when it's installed at the pin, the pref is on and this version passed
  acceptance; otherwise Slate's own ES3 library does, which needs no pref.
- `fetch_look(name)`: a look's text from the bridge, at use time.

All bridge calls hold Slate's `ae` resource lock (the job queue's), and Slate's own scripts take the bridge's busy
lock (slate.ae.bridgelock), so the two routes never send AE two scripts at once. Starting the bridge's server doesn't
start AE: reading its catalog and its skills works offline; anything that touches a project needs AE open.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from slate import config, jsonio, log, paths, procs
from slate.ae import eases, guard, prefs
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, UsageError

PACKAGE = "fnf-after-effects-mcp"
SERVER_NAME = "higgsfield-use-after-effects"
ENTRY_LOOK = "ae-clean-rig"
# The bridge's 13 After Effects skills, used as AE looks. Names only: the text is fetched from the bridge when used.
LOOKS = ("ae-animation-principles", "ae-build-orchestration", "ae-clean-rig", "ae-cleanup", "ae-depth-space",
         "ae-design-first", "ae-figma-transfer", "ae-liquid-glass", "ae-matte-painting", "ae-mcp-realities",
         "ae-transition-kit", "ae-ui-mastery", "use-after-effects")
GLOBAL_ROOTS = ("/opt/homebrew/lib/node_modules", "/usr/local/lib/node_modules")
ACCEPTANCE_FILE = Path(__file__).resolve().parent / "data" / "bridge_acceptance.json"
TEST_EASE = [0.05, 0.7, 0.1, 1.0]  # the smoke test's ease: Material 3 "emphasized decelerate"

# The bridge's own time limits per call (its config: 60 s by default, 120 s to render a frame, 10 minutes to import),
# and how much longer Slate waits for an answer, so a slow import is never cut off on Slate's side and left in the
# unknown state call_with_retry would have to read back (L-ae-023).
BRIDGE_TIMEOUT_S = 60.0
BRIDGE_TOOL_TIMEOUT_S = {"ae_render_frame": 120.0, "ae_project_import_json": 600.0}
IMPORT_TIMEOUT_S = 600.0
READ_MARGIN_S = 15.0

# What the bridge may do (its policy switches, confirmed against the pinned 0.1.3's dist/policy.js): arbitrary
# ExtendScript stays off (`eval.run` needs AE_MCP_ENABLE_EVAL=1, and Slate always passes 0, whatever the environment
# says), and operations are limited to the categories Slate's scenes and the acceptance run use. Left out: eval,
# command (arbitrary menu commands), pref (AE's preferences, which only slate.ae.prefs edits, byte by byte), egp,
# expression, effect, mask and viewer (L-ae-025).
SCENE_CATEGORIES = ("batch", "comp", "font", "footage", "item", "keyframe", "layer", "marker", "project", "property",
                    "render", "shape", "text", "timeline", "transform")
POLICY_ENV = {"AE_MCP_ENABLE_EVAL": "0", "AE_MCP_ALLOW_CATEGORIES": ",".join(SCENE_CATEGORIES)}


def read_timeout(tool: str) -> float:
    """How long Slate waits for one bridge call: a little over the bridge's own limit for that tool."""
    own = BRIDGE_TOOL_TIMEOUT_S.get(tool)
    if own is None:
        own = IMPORT_TIMEOUT_S if "import" in tool else BRIDGE_TIMEOUT_S
    return own + READ_MARGIN_S

Runner = Callable[..., Any]


# ---------------------------------------------------------------------------------------------------------------
# Finding it


def pinned() -> str:
    """The pinned version from registry.json (exact)."""
    return str(config.tool(PACKAGE)["version"])


def package_dir() -> Path | None:
    """Where the bridge's npm package is installed: SLATE_AE_BRIDGE_DIR, else the global node_modules folders."""
    env = os.environ.get("SLATE_AE_BRIDGE_DIR")
    candidates = [Path(env).expanduser()] if env else []
    candidates += [Path(r) / PACKAGE for r in GLOBAL_ROOTS]
    candidates += [Path.home() / d / "lib" / "node_modules" / PACKAGE for d in (".npm-global", ".local")]
    for c in candidates:
        if (c / "package.json").is_file():
            return c
    return None


def read_package(pkg: Path | None) -> dict | None:
    if pkg is None:
        return None
    try:
        return json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def installed_looks(pkg: Path | None) -> list[str]:
    """Skill folders the installed package ships (names only)."""
    d = pkg / "skills" / "after-effects" if pkg else None
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d and d.is_dir() else []


def acceptance_path() -> Path:
    return paths.data() / "ae" / "bridge" / "acceptance.json"


def last_acceptance() -> dict | None:
    return jsonio.read(acceptance_path(), default=None)


# ---------------------------------------------------------------------------------------------------------------
# Status


def parse_doctor(text: str | None) -> dict | None:
    """The bridge's `fnf-after-effects doctor` output (JSON) -> {ok, checks: {name: ok}, live_tested}."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        doc = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    checks = {str(c.get("name")): bool(c.get("ok")) for c in doc.get("checks") or [] if isinstance(c, dict)}
    return {"ok": bool(doc.get("ok")), "checks": checks, "live_tested": bool(doc.get("liveConnectionTested")),
            "next": doc.get("nextStep")}


def parse_status(pkg: dict | None, *, pin: str, prefs_info: dict | None, doctor: dict | None = None,
                 acceptance: dict | None = None, looks_found: Iterable[str] = (), where: str | None = None) -> dict:
    """Everything `slate ae bridge status` reports, from facts already gathered (pure, so it's testable)."""
    version = str(pkg.get("version")) if pkg else None
    installed = pkg is not None and pkg.get("name") == PACKAGE
    pin_ok = installed and version == pin
    pref = None if prefs_info is None else bool(prefs_info.get("scripting_on"))
    pref_word = "unknown (AE's prefs file wasn't found)" if pref is None else ("on" if pref else "off")
    acc_ok = bool(acceptance and acceptance.get("passed") and acceptance.get("version") == version)
    can_build = bool(installed and pin_ok and pref)
    route = "bridge" if can_build and acc_ok else "slate-es3"
    missing_looks = [n for n in LOOKS if n not in set(looks_found)] if installed else list(LOOKS)
    nxt: list[str] = []
    if not installed:
        nxt.append(f"slate ae bridge install --yes installs {PACKAGE}@{pin} (the person's yes)")
    elif not pin_ok:
        nxt.append(f"the installed bridge is {version}, Slate pins {pin}: reinstall the pin (slate ae bridge install "
                   "--yes), or run the acceptance on this version first (slate ae bridge acceptance --go)")
    if pref is False:
        nxt.append("building scenes through the bridge needs AE's \"Allow Scripts to Write Files and Access Network\" "
                   "(Settings > Scripting & Expressions); only you turn it on. Finishing works without it.")
    if installed and pin_ok and not acc_ok:
        nxt.append("run the bridge's acceptance on this version with AE open: slate ae bridge acceptance --go")
    return {
        "package": PACKAGE, "server": SERVER_NAME, "installed": installed, "version": version, "pinned": pin,
        "pin_ok": pin_ok, "where": where, "license": (pkg or {}).get("license"),
        "scripting_pref": pref_word, "scripting_pref_on": pref,
        "finishing_ok": True,  # Slate's own route (osascript + aerender) needs no special pref
        "can_build_scenes": can_build, "acceptance": acceptance, "acceptance_current": acc_ok, "route": route,
        "looks": list(LOOKS), "looks_missing": missing_looks, "doctor": doctor, "next": nxt,
        "dialogs": "AE's launch dialogs (sign-in, crash recovery, missing fonts) sit outside script control; a person "
                   "dismisses them, or mac-cua (optional, its app-control tools only, never its browser tools) can.",
    }


def _ae_windows() -> dict:
    """AE's window titles through System Events (doctor's reader): {running, windows | None, error}."""
    from slate.doctor.env import Env  # noqa: PLC0415

    return Env().ae_windows()


def open_dialogs(windows: Callable[[], dict] | None = None) -> dict:
    """AE's launch-time dialogs as the bridge reports them: {open_dialogs: [{kind, title}], dialogs_readable}.

    The bridge can't see a dialog (its mailbox only runs when AE's main window does), so this reads AE's window
    titles through System Events (doctor's reader: it never launches AE, and asks only when AE is already open) and
    sorts them with doctor's classifier. With AE closed nothing is asked and nothing is open."""
    if not procs.ae_running():
        return {"open_dialogs": [], "dialogs_readable": True}
    from slate.doctor import aftereffects as doctor_ae  # noqa: PLC0415 - doctor knows AE's dialogs

    got = (windows or _ae_windows)()
    titles = got.get("windows")
    if titles is None:
        return {"open_dialogs": [], "dialogs_readable": False}
    found = [{"kind": k, "title": str(t)[:120]} for t in titles if (k := doctor_ae.classify_dialog(t))]
    return {"open_dialogs": found, "dialogs_readable": True}


def status(*, run_doctor: bool = False, runner: Runner | None = None, dialogs: bool = True) -> dict:
    """The bridge's status (see parse_status). Reads files only, plus AE's window titles when AE is open (its launch
    dialogs, `open_dialogs`); `run_doctor` also runs the bridge's offline doctor. Doctor passes dialogs=False: it reads
    AE's windows itself in the same pass."""
    pkg_dir = package_dir()
    pkg = read_package(pkg_dir)
    try:
        info = prefs.check()
    except SlateError:
        info = None
    doctor = None
    if run_doctor and pkg_dir is not None:
        node = procs.which("node")
        if node:
            proc = (runner or procs.run)([node, str(pkg_dir / "dist" / "cli.js"), "doctor"], timeout=60)
            doctor = parse_doctor(getattr(proc, "stdout", ""))
    out = parse_status(pkg, pin=pinned(), prefs_info=info, doctor=doctor, acceptance=last_acceptance(),
                       looks_found=installed_looks(pkg_dir), where=str(pkg_dir) if pkg_dir else None)
    if dialogs:
        out.update(open_dialogs())
        if out["open_dialogs"]:
            first = out["open_dialogs"][0]
            out["next"].insert(0, f"After Effects is showing a launch dialog (\u201c{first['title'][:60]}\u201d): dismiss it "
                                  "yourself before any AE step (scripts can't)")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Install


def install_plan() -> dict:
    """How the bridge is installed and pinned, and how each agent registers it (nothing runs here).

    In order: the pinned package with npm, its own install scripts never run (`--ignore-scripts`: L-packaging-D004);
    its doctor; its config; then each agent's registration, with arbitrary eval off and operations limited to the
    categories Slate's scenes use (POLICY_ENV).
    """
    pin = pinned()
    root = Path(GLOBAL_ROOTS[0]) / PACKAGE
    node = procs.which("node") or "node"
    index = str(root / "dist" / "index.js")
    env_pairs = [f"{k}={v}" for k, v in POLICY_ENV.items()]
    return {
        "install": ["npm", "install", "--global", "--ignore-scripts", f"{PACKAGE}@{pin}"],
        "pinned": pin,
        "verify": ["fnf-after-effects", "doctor"],
        "config": ["fnf-after-effects", "config"],
        "policy": dict(POLICY_ENV),
        "register": {
            "claude-code": ["claude", "mcp", "add", "--scope", "user", SERVER_NAME,
                            *[x for pair in env_pairs for x in ("-e", pair)], "--", node, index],
            # `fnf-after-effects install-codex` registers it with no environment, so Slate registers it the same way
            # it does for the others: eval off and the categories limited
            "codex": ["codex", "mcp", "add", SERVER_NAME, *[x for pair in env_pairs for x in ("--env", pair)], "--",
                      node, index],
            # OpenClaw 2026.9.4 registers a server with `mcp add`, probes it before saving (5 s by default, so 30 here)
            # and refuses a name it already has; `fnf-after-effects config` prints the same entry for a look.
            "openclaw": ["openclaw", "mcp", "add", SERVER_NAME, "--command", node, "--arg", index,
                         *[x for pair in env_pairs for x in ("--env", pair)], "--connect-timeout", "30"],
        },
        "notes": ["Exact version only: a new version is used for scenes after `slate ae bridge acceptance --go` "
                  "passes on it.",
                  "npm runs with --ignore-scripts, so the package's own install scripts never run; then its doctor, "
                  "its config, and each agent's registration, in that order.",
                  "Every registration keeps the bridge's arbitrary eval off (AE_MCP_ENABLE_EVAL=0) and limits its "
                  "operations to the categories Slate's scenes use (AE_MCP_ALLOW_CATEGORIES).",
                  "The bridge's mailbox is a trust boundary: anything that can write there runs code in After "
                  "Effects, so Slate creates it 0700 and nothing else writes to it.",
                  "Slate never vendors the package or copies its skills (no redistribution grant); it depends on "
                  "the installed copy.",
                  "Registration changes each agent's own MCP settings, so Slate prints the commands and the person "
                  "(or install.sh, with their yes) runs them."],
    }


def install(*, yes: bool = False, runner: Runner | None = None) -> dict:
    """Install the pinned bridge with npm (global). Without `yes` it only returns the plan."""
    plan = install_plan()
    if not yes:
        return {"ran": False, "plan": plan}
    run = runner or procs.run
    proc = run(plan["install"], timeout=900)
    if getattr(proc, "returncode", 1) != 0:
        raise SlateError(f"npm couldn't install {PACKAGE}@{plan['pinned']}: {(proc.stderr or '')[-400:]}")
    pkg = read_package(package_dir())
    if not pkg or str(pkg.get("version")) != plan["pinned"]:
        raise Refused(f"After the install, the bridge reads as version {pkg and pkg.get('version')}, not the pin "
                      f"{plan['pinned']}.", hint="Check `npm ls --global` and install the pinned version.")
    return {"ran": True, "plan": plan, "version": pkg["version"]}


# ---------------------------------------------------------------------------------------------------------------
# Talking to the bridge (MCP over stdio)


def _payload(result: Any) -> tuple[bool, dict]:
    """(ok, payload) from an MCP CallToolResult (or a plain dict from a test's fake session)."""
    if isinstance(result, dict):
        return bool(result.get("ok", True)) and not result.get("isError"), result
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    payload: dict = {}
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        payload = structured
    else:
        for c in getattr(result, "content", None) or []:
            text = getattr(c, "text", None)
            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {"text": text}
                break
    return (not is_error) and payload.get("ok", True) is not False, payload


class BridgeSession:
    """A synchronous MCP client for the bridge's stdio server (started from the installed package).

        with BridgeSession() as s:
            ok, payload = s.call("ae_catalog", {})

    The server runs with a minimal environment (PATH, HOME, the temp dir, BROWSER=none): it needs no keys, and
    none are passed. Arbitrary eval stays off and operations are limited to the categories Slate's scenes use
    (POLICY_ENV), whatever the environment says. Each call waits a little longer than the bridge's own limit for that
    tool (read_timeout: about 75 s, 135 s to render a frame, 615 s to import); `timeout` overrides it for every call.
    Starting it doesn't start After Effects; only calls that touch a project do.
    """

    def __init__(self, pkg: Path | None = None, *, timeout: float | None = None):
        self.pkg = pkg or package_dir()
        if self.pkg is None:
            raise MissingPrerequisite(f"{PACKAGE} isn't installed.", hint="slate ae bridge install --yes")
        self.timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queue: asyncio.Queue | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def timeout_for(self, tool: str) -> float:
        """How long this session waits for one call to `tool`."""
        return float(self.timeout) if self.timeout is not None else read_timeout(tool)

    def _env(self) -> dict[str, str]:
        base = procs.child_env()
        keep = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR", "BROWSER")
        env = {k: base[k] for k in keep if k in base}
        for k, v in base.items():
            if (k.startswith("AE_MCP_") or k == "AE_EXE") and k not in POLICY_ENV:
                env[k] = v
        env.update(POLICY_ENV)  # eval off and the categories limited, never loosened from the environment
        return env

    async def _serve(self) -> None:
        from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
        from mcp.client.stdio import stdio_client  # noqa: PLC0415

        node = procs.which("node")
        if not node:
            raise MissingPrerequisite("node isn't installed; the AE bridge runs on Node.", hint="slate doctor --install")
        params = StdioServerParameters(command=node, args=[str(self.pkg / "dist" / "index.js")], env=self._env())
        self._queue = asyncio.Queue()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._ready.set()
                while True:
                    item = await self._queue.get()
                    if item is None:
                        break
                    tool, args, fut = item
                    try:
                        res = await session.call_tool(tool, args, read_timeout_seconds=self.timeout_for(tool))
                        fut.set_result(res)
                    except BaseException as exc:  # noqa: BLE001 - handed back to the caller's thread
                        fut.set_exception(exc)

    def __enter__(self) -> "BridgeSession":
        def main() -> None:
            self._loop = asyncio.new_event_loop()
            try:
                self._loop.run_until_complete(self._serve())
            except BaseException as exc:  # noqa: BLE001
                self._error = exc
                self._ready.set()
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=main, name="slate-ae-bridge", daemon=True)
        self._thread.start()
        if not self._ready.wait(60) or self._error is not None:
            raise SlateError(f"The AE bridge's server didn't start: {self._error}")
        return self

    def call(self, tool: str, args: dict | None = None) -> tuple[bool, dict]:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        assert self._loop is not None and self._queue is not None
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (tool, args or {}, fut))
        return _payload(fut.result(timeout=self.timeout_for(tool) + 30))

    def __exit__(self, *exc) -> None:
        if self._loop is not None and self._queue is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        if self._thread is not None:
            self._thread.join(timeout=15)


# ---------------------------------------------------------------------------------------------------------------
# Retrying safely, looks, routes


def call_with_retry(session: Any, tool: str, args: dict, *, verify: dict | None = None,
                    applied: Callable[[bool, dict], bool] | None = None, attempts: int = 2) -> dict:
    """One bridge call that may mutate the project, retried only when the project shows it didn't happen.

    The bridge's batches aren't transactional and a timeout leaves the outcome unknown, so after a failure the
    `verify` read runs first; if it shows the change is there (`applied`, default: the read succeeded), the step
    counts as done and is not sent again.
    """
    history = []
    for attempt in range(1, attempts + 1):
        try:
            ok, payload = session.call(tool, args)
        except Exception as exc:  # noqa: BLE001 - a timeout's outcome is unknown: read before retrying
            ok, payload = False, {"error": str(exc)}
        history.append({"attempt": attempt, "ok": ok, "error": payload.get("error") if not ok else None})
        if ok:
            return {"ok": True, "payload": payload, "attempts": attempt, "history": history}
        if verify is not None:
            vok, vpayload = session.call(verify["tool"], verify.get("args") or {})
            done = (applied or (lambda o, _p: o))(vok, vpayload)
            history[-1]["verified_applied"] = bool(done)
            if done:
                return {"ok": True, "payload": vpayload, "attempts": attempt, "history": history,
                        "recovered": "the project already showed the change, so it wasn't sent again"}
        elif attempt < attempts:
            break  # without a read that proves the state, a mutation is never blindly re-sent
    return {"ok": False, "payload": payload, "attempts": len(history), "history": history}


def looks() -> list[str]:
    return list(LOOKS)


def fetch_look(name: str, *, reference: str | None = None, session_factory: Callable[[], Any] | None = None) -> dict:
    """A look's text from the bridge, at the moment it's used (never stored in Slate)."""
    if name not in LOOKS:
        raise UsageError(f"{name!r} isn't one of the bridge's looks: {', '.join(LOOKS)}.")
    factory = session_factory or BridgeSession
    with factory() as s:
        ok, payload = s.call("ae_get_skill", {"name": name, **({"reference": reference} if reference else {})})
    if not ok:
        raise SlateError(f"The bridge couldn't read look {name}: {payload.get('error') or payload}")
    return payload


def choose_route(st: dict | None = None) -> dict:
    """Which engine builds AE scenes today: the bridge when it's ready and accepted, else Slate's ES3 library."""
    st = st or status()
    if st["route"] == "bridge":
        return {"route": "bridge", "reason": f"the bridge {st['version']} is installed at the pin, the file-write "
                                             "pref is on, and this version passed acceptance"}
    why = "; ".join(st["next"]) or "the bridge isn't ready"
    return {"route": "slate-es3", "reason": f"Slate's own ES3 library builds scenes (it needs no pref): {why}"}


# ---------------------------------------------------------------------------------------------------------------
# Acceptance


def acceptance_script() -> dict:
    return json.loads(ACCEPTANCE_FILE.read_text(encoding="utf-8"))


def _fill(obj: Any, values: dict) -> Any:
    if isinstance(obj, str):
        m = re.fullmatch(r"\$\{(\w+)\}", obj)
        if m:
            return values[m.group(1)]
        return re.sub(r"\$\{(\w+)\}", lambda mm: str(values[mm.group(1)]), obj)
    if isinstance(obj, list):
        return [_fill(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    return obj


def acceptance_values(scratch: Path) -> dict:
    """Run-time values: the scratch folder and Slate's own speed/influence for the test ease (480 px in 1 s)."""
    seg = eases.seg_ease(TEST_EASE, 480.0)
    return {"scratch": str(scratch), "out_speed": round(abs(seg["out"][0]), 6), "out_influence": round(seg["out"][1], 6),
            "in_speed": round(abs(seg["inn"][0]), 6), "in_influence": round(seg["inn"][1], 6)}


def _expect(expect: dict | None, payload: dict) -> str | None:
    """None when a payload meets its expectation, else what's wrong."""
    if not expect:
        return None
    body = payload.get("result", payload) if isinstance(payload.get("result"), dict) else payload
    if "min_operations" in expect:
        total = payload.get("totalOperations") or sum(c.get("operationCount", 0) for c in payload.get("categories") or [])
        if total < expect["min_operations"]:
            return f"only {total} operations"
        names = {op for c in payload.get("categories") or [] for op in c.get("operations") or []}
        missing = [o for o in expect.get("operations") or [] if o not in names]
        if missing:
            return f"missing operations: {', '.join(missing)}"
    if expect.get("looks"):
        names = {s.get("name") for s in payload.get("skills") or [] if isinstance(s, dict)}
        missing = [n for n in LOOKS if n not in names]
        if missing:
            return f"missing looks: {', '.join(missing)}"
    if "num_items" in expect and body.get("numItems") != expect["num_items"]:
        return f"the project has {body.get('numItems')} items, not {expect['num_items']}"
    if "keyframes_at_least" in expect:
        text = json.dumps(payload)
        if text.count('"time"') < expect["keyframes_at_least"]:
            return "the keyframes aren't there"
    if "file_endswith" in expect and not str(body.get("file") or "").endswith(expect["file_endswith"]):
        return f"the project isn't saved as {expect['file_endswith']}"
    return None


def acceptance(*, go: bool = False, session_factory: Callable[[], Any] | None = None,
               scratch_dir: str | os.PathLike | None = None) -> dict:
    """Run the fixed acceptance script against a scratch project (AE open, the person's go). Records the result.

    Refuses (never runs) when: nobody gave the go; this is an autonomous run; the bridge isn't installed; AE's
    file-write pref is off (the bridge can't work without it, and only the person turns it on); or the open project
    has unsaved changes (exit 5: save your work in After Effects first; nothing is ever discarded).
    """
    guard.person_start(go, "The AE bridge's acceptance run")
    pkg = read_package(package_dir())
    if not pkg:
        raise MissingPrerequisite(f"{PACKAGE} isn't installed.", hint="slate ae bridge install --yes")
    info = prefs.check()
    if not info["scripting_on"]:
        raise Refused("The AE bridge needs \"Allow Scripts to Write Files and Access Network\", and it's off.",
                      hint="Only you turn it on, in After Effects: Settings > Scripting & Expressions. Slate never "
                           "changes it. Finishing (build, audit, masters) works without it.")
    version = str(pkg.get("version"))
    scratch = paths.ensure_dir(Path(scratch_dir) if scratch_dir else paths.data() / "ae" / "bridge" / f"scratch-{version}")
    script = _fill(acceptance_script(), acceptance_values(scratch))
    from slate.ae import driver  # noqa: PLC0415 - Slate's own `ae` lock (the bridge takes its busy lock per call)

    results: list[dict] = []
    factory = session_factory or BridgeSession
    with driver.ae_lock(bridge=False, owner="slate-ae-bridge-acceptance"), factory() as s:
        pre = script["preflight"]
        ok, payload = s.call(pre["tool"], pre.get("args") or {})
        body = payload.get("result", payload) if isinstance(payload.get("result"), dict) else payload
        if not ok:
            raise SlateError(f"The bridge couldn't read After Effects' open project: {payload.get('error') or payload}",
                             hint="Is AE open with no dialog showing? Is the file-write pref on?")
        if pre.get("require_clean") and body.get("dirty"):
            raise Busy("After Effects has unsaved changes, and the acceptance run starts a scratch project.",
                       hint="save your work in After Effects (or close that project), then run this again")
        for op in script["ops"]:
            if op.get("mutates"):
                verify = op.get("verify")
                applied = (lambda o, p, v=verify: o and _expect((v or {}).get("expect"), p) is None)
                out = call_with_retry(s, op["tool"], op.get("args") or {}, verify=verify, applied=applied)
                ok, payload = out["ok"], out["payload"]
                problem = None if ok else (payload.get("error") or "failed")
                if ok and verify and "recovered" not in out:
                    vok, vp = s.call(verify["tool"], verify.get("args") or {})
                    problem = None if vok else f"its check read failed: {vp.get('error')}"
                    problem = problem or _expect(verify.get("expect"), vp)
            else:
                ok, payload = s.call(op["tool"], op.get("args") or {})
                problem = None if ok else (payload.get("error") or "failed")
                problem = problem or _expect(op.get("expect"), payload)
            results.append({"id": op["id"], "tool": op["tool"], "ok": problem is None, "problem": problem})
            if problem and op.get("mutates"):
                break  # later steps build on this one
    passed = all(r["ok"] for r in results) and len(results) == len(script["ops"])
    record = {"version": version, "pinned": pinned(), "passed": passed, "at": _now(), "results": results,
              "scratch": str(scratch), "script_version": script.get("version")}
    jsonio.write_atomic(acceptance_path(), record)
    if not passed:
        log.warn("the bridge failed its acceptance; Slate's own ES3 library keeps building scenes")
    return record


def _now() -> str:
    import datetime as _dt  # noqa: PLC0415

    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
