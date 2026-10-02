"""A fake machine for doctor's tests.

`FakeEnv` answers every fact doctor asks about from plain Python values. Its defaults describe a Mac where everything
Slate needs is present, so a fresh FakeEnv must come out all green; each test then flips the one fact it's about.
Files under the test's temp folder are real (skills, links, ~/.claude.json, AGENTS.md); everything outside it (system
tools, Homebrew, After Effects) is described by the fake. Every command doctor tries to run is recorded in `calls`, and
none of them reaches a real process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from slate import secrets as _secrets
from slate.doctor.env import Env, Ran
from slate.gates import GateResult

# Captured at import, before the autouse fixture swaps it for a guard: the tests that prove the real prompt refuses
# in an agent context put this back on purpose.
REAL_STORE_INTERACTIVE = _secrets.store_interactive

SHIM = "#!/bin/sh\n# slate-shim v1: Slate's command-line tool\nSLATE_CHECKOUT={repo}\nexec \"$SLATE_CHECKOUT/.venv/bin/python\" -m slate \"$@\"\n"
CLAUDE_GET_OK = ("slate:\n  Scope: User config (available in all your projects)\n  Status: ✓ Connected\n  Type: stdio\n"
                 "  Command: {shim}\n  Args: mcp\n")
AE_APP = "/Applications/Adobe After Effects 2099/Adobe After Effects 2099.app"
AERENDER = "/Applications/Adobe After Effects 2099/aerender"
BRIDGE_DIR = "/opt/homebrew/lib/node_modules/fnf-after-effects-mcp"
HF_PKG = "/opt/homebrew/lib/node_modules/@higgsfield/cli"
ASIDE_APP = "/Users/test/.aside/cli/Aside CLI.app"


def codex_prompt_input(skills: dict[str, str]) -> str:
    """What `codex debug prompt-input` prints, with these skills in its list (each description as Codex shows it)."""
    listing = "\n".join(f"- {name}: {desc} (file: r1/{name}/SKILL.md)" for name, desc in skills.items())
    return json.dumps([{"type": "message", "role": "developer", "content": [{"type": "input_text", "text":
                       f"<skills_instructions>\n## Skills\n### Available skills\n{listing}\n</skills_instructions>"}]},
                       {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}])


class FakeEnv(Env):
    def __init__(self, tmp: Path, **over):
        tmp = Path(tmp).resolve()
        super().__init__(home=tmp / "home")
        self.tmp = tmp
        self.home.mkdir(parents=True, exist_ok=True)
        self.repo_dir = tmp / "repo"
        # render-stability conformance: never run, on a checkout whose engine kit has no bench (a fixture default)
        self.stability: dict | Exception = {"record": None, "bench": False, "spec_sha256": None}
        self.calls: list[list[str]] = []
        self.lookups: list[str] = []
        self.sys, self.rel, self.mac, self.host_arch = "Darwin", "24.0.0", "27.0", "arm64"
        self.envvars: dict[str, str] = {"PATH": "/usr/bin:/bin"}
        self.agent: str | None = None
        self.terminal: tuple[bool, str] = (False, "this is running under a test agent")
        self.is_replay = True
        self.present: set[str] = {"/usr/bin/osascript", "/usr/bin/caffeinate", "/usr/bin/security",
                                  "/opt/homebrew/bin/brew", AERENDER, "/opt/homebrew/bin/slate"}
        self.present_dirs: set[str] = set()
        self.real: dict[str, str] = {
            "/opt/homebrew/bin/ffmpeg": "/opt/homebrew/Cellar/ffmpeg/9.0.2/bin/ffmpeg",
            "/opt/homebrew/bin/ffprobe": "/opt/homebrew/Cellar/ffmpeg/9.0.2/bin/ffprobe",
            "/opt/homebrew/bin/whisper": "/opt/homebrew/Cellar/openai-whisper/20250625_6/libexec/bin/whisper",
            "/opt/homebrew/bin/higgsfield": f"{HF_PKG}/bin/higgsfield.js",
        }
        self.arch_of: dict[str, list[str]] = {}
        self.sizes: dict[str, int] = {}
        self.texts: dict[str, str] = {f"{HF_PKG}/package.json": json.dumps({"name": "@higgsfield/cli",
                                                                           "version": "1.1.26"}),
                                      "/opt/homebrew/bin/slate": SHIM.format(repo=self.repo_dir)}
        self.plists: dict[str, dict] = {
            f"{AE_APP}/Contents/Info.plist": {"CFBundleShortVersionString": "26.5.0", "CFBundleVersion": "26.5.0.89",
                                              "CFBundleIdentifier": "com.adobe.AfterEffects.application"},
            f"{ASIDE_APP}/Contents/Info.plist": {"CFBundleShortVersionString": "1.26.916"},
        }
        local_bin = self.home / ".local" / "bin"
        self.tools: dict[str, str | None] = {
            "ffmpeg": "/opt/homebrew/bin/ffmpeg", "ffprobe": "/opt/homebrew/bin/ffprobe", "uv": "/opt/homebrew/bin/uv",
            "node": "/opt/homebrew/bin/node", "npm": "/opt/homebrew/bin/npm", "git": "/opt/homebrew/bin/git",
            "gh": "/opt/homebrew/bin/gh", "whisper": "/opt/homebrew/bin/whisper",
            "rsvg-convert": "/opt/homebrew/bin/rsvg-convert", "potrace": "/opt/homebrew/bin/potrace",
            "higgsfield": "/opt/homebrew/bin/higgsfield", "aside": str(local_bin / "aside"),
            "claude": str(local_bin / "claude"), "codex": "/opt/homebrew/bin/codex",
            "openclaw": "/opt/homebrew/bin/openclaw", "brew": "/opt/homebrew/bin/brew",
        }
        self.real[str(local_bin / "aside")] = f"{ASIDE_APP}/Contents/MacOS/aside"
        shim = local_bin / "slate"
        self.responses: dict[tuple, Ran] = {
            ("sysctl", "-n", "hw.optional.arm64"): Ran(0, "1\n"),
            ("ffmpeg", "-hide_banner", "-version"): Ran(0, "ffmpeg version 9.0.2 Copyright (c) 2000-2026\n"),
            ("ffmpeg", "-hide_banner", "-filters"): Ran(0, " ... loudnorm  A->A\n ... ebur128 A->N\n"),
            ("ffprobe", "-hide_banner", "-version"): Ran(0, "ffprobe version 9.0.2 Copyright (c) 2007-2026\n"),
            ("uv", "--version"): Ran(0, "uv 0.9.28 (Homebrew 2026-09-01)\n"),
            ("node", "--version"): Ran(0, "v26.8.1\n"),
            ("npm", "--version"): Ran(0, "11.19.0\n"),
            ("git", "--version"): Ran(0, "git version 2.54.0\n"),
            ("gh", "--version"): Ran(0, "gh version 2.86.0 (2026-09-01)\n"),
            ("xcode-select", "-p"): Ran(0, "/Applications/Xcode.app/Contents/Developer\n"),
            ("xcrun", "--find", "simctl"): Ran(0, "/Applications/Xcode.app/Contents/Developer/usr/bin/simctl\n"),
            ("rsvg-convert", "--version"): Ran(0, "rsvg-convert version 2.63.2\n"),
            ("potrace", "--version"): Ran(0, "potrace 1.16. Copyright (C) 2001-2019 Peter Selinger.\n"),
            ("claude", "mcp", "get", "slate"): Ran(0, CLAUDE_GET_OK.format(shim=shim)),
            ("codex", "mcp", "list", "--json"): Ran(0, json.dumps([
                {"name": "slate", "enabled": True, "transport": {"type": "stdio", "command": str(shim),
                                                                 "args": ["mcp"]}},
                {"name": "aside", "enabled": True, "transport": {"type": "stdio", "command": "aside", "args": ["mcp"]}},
            ])),
            ("codex", "debug", "prompt-input"): Ran(0, codex_prompt_input({"slate": "test skill",
                                                                         "slate-story": "test skill"})),
            ("openclaw", "mcp", "list", "--json"): Ran(0, "OpenClaw 2026.9.4 — banner\n" + json.dumps(
                {"slate": {"command": str(shim), "args": ["mcp"]}, "aside": {"command": "node", "args": ["a.js"]}})),
            ("higgsfield", "account", "status", "--json"): Ran(0, json.dumps(
                {"credits": 50.0, "email": "person@example.test", "subscription_plan_type": "free"})),
            ("osascript", "-e"): Ran(0, "26.5.0\n"),
            ("claude", "--version"): Ran(0, "2.1.282 (Claude Code)\n"),
        }
        self.secrets = {"elevenlabs": "keychain", "higgsfield": "env", "elevenlabs_oneiro": "keychain"}
        self.stored: list[str] = []
        self.store_rc = 0
        self.store_exc: Exception | None = None
        self.eleven: dict | Exception = {"tier": "pro", "replay": True, "lossless": True,
                                         "best_voice_format": "pcm_48000"}
        self.eleven_calls = 0
        self.hf_probe: dict | Exception = {"ok": True, "status": "ok", "http": 404, "replay": True}
        self.tls: dict = {"ca_certs": 128, "source": "Python's default context", "cafile": "/etc/ssl/cert.pem",
                          "cafile_exists": True, "capath": None, "capath_has_certs": False,
                          "env_cert_file": "/etc/ssl/cert.pem"}
        self.rubberband: dict | Exception = {"path": "/opt/homebrew/bin/rubberband", "version": "4.0.0",
                                             "arch": ["arm64"], "machine": "arm64", "native": True, "status": "ok"}
        self.engine_folder = tmp / "engine"
        self.engine: tuple[Path, str] | Exception = (self.engine_folder, "installed")
        self.ae_list: list[dict] = [{"name": "Adobe After Effects 2099", "app": AE_APP,
                                     "folder": "/Applications/Adobe After Effects 2099", "aerender": AERENDER}]
        self.aerender: str | Exception = AERENDER
        self.prefs: dict | Exception = {"file": "/prefs/Adobe After Effects 2099 Prefs.txt", "scripting_on": True,
                                        "scripting": "on", "damaged": False, "lf": 0, "cr": 900,
                                        "disk_cache": {"on": False}, "running": []}
        self.bridge: dict | Exception = {"installed": True, "version": "0.1.3", "pinned": "0.1.3", "pin_ok": True,
                                         "acceptance_current": True, "where": BRIDGE_DIR, "route": "bridge",
                                         "scripting_pref": "on"}
        self.smoke: object = GateResult.pass_("ae.smoke", "30 frames rendered; the dot follows the ease to 0.40 px",
                                              checked_at="2026-09-30T12:00:00+00:00")
        self.ae_is_running = False
        self.bridge_installs = 0
        self.smoke_runs: list = []
        self.smoke_result: object = GateResult.pass_("ae.smoke", "30 frames rendered; fresh run")
        self.font_checks: list[list[str]] = []
        self.font_check_result: dict = {"missing": []}
        self.jobs: dict | Exception = {"label": "dev.slate.jobs", "installed": True, "loaded": True,
                                       "runner_serving": True, "plist": "/x.plist", "log": "/logs/runner.launchd.log"}
        self.queue: dict | Exception = {"runner": True, "counts": {"queued": 0, "running": 0, "done": 3},
                                        "next": None, "running": []}
        self.ocr: tuple[bool, dict] = (True, {"name": "Apple Vision (ocrmac)", "version": "1.0.0",
                                              "recognition_level": "accurate"})
        self.instruments: list[dict] | Exception = [
            {"id": "sound.instrument.felt_note", "status": "green", "summary": "Test Marimba: 12 samples in place",
             "fix": None}]
        self.broken: tuple[dict, int] = ({}, 23)
        self.mcp_check: dict | Exception = {"ok": True, "protocol": "2025-06-18", "tools": ["status", "doctor"],
                                            "missing": [], "status_answered": True, "exit_code": 0, "stderr": ""}
        self.fonts: set[str] | None = {"Helvetica Neue", "Test Serif"}
        self.adobe_fonts: set[str] = {"Test Adobe Sans"}
        self.ls: dict | None = {"LSHandlers": [{"LSHandlerURLScheme": "https",
                                               "LSHandlerRoleAll": "company.thebrowser.dia"},
                                              {"LSHandlerURLScheme": "http",
                                               "LSHandlerRoleAll": "company.thebrowser.dia"}]}
        self.free_bytes = 500 * 10**9
        # running processes ({pid, ppid, command}) and After Effects' window titles, for the duplicate-server and
        # launch-dialog rows (one Slate server per client, and AE closed, by default)
        self.process_list: list[dict] | None = [
            {"pid": 501, "ppid": 500, "command": f"{local_bin / 'slate'} mcp"},
            {"pid": 601, "ppid": 600, "command": f"{local_bin / 'slate'} mcp"}]
        self.ae_window_titles: list[str] | None = []
        self.ae_window_error: str | None = None
        self.sizes[str(self.home / ".cache" / "whisper" / "large-v3-turbo.pt")] = 1_617_941_637
        self._setup_files()
        for k, v in over.items():
            setattr(self, k, v)

    # -- building the fake machine ---------------------------------------------------------------------------------

    def _setup_files(self) -> None:
        for name in ("slate", "slate-story"):
            skill = self.repo_dir / "skills" / name
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test skill\n---\n", encoding="utf-8")
        for root in (self.home / ".claude" / "skills", self.home / ".agents" / "skills"):
            root.mkdir(parents=True, exist_ok=True)
            for name in ("slate", "slate-story"):
                link = root / name
                if not link.exists():
                    link.symlink_to(self.repo_dir / "skills" / name)
        (self.home / ".codex" / "skills").mkdir(parents=True, exist_ok=True)
        (self.home / ".openclaw").mkdir(parents=True, exist_ok=True)
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        shim = local_bin / "slate"
        shim.write_text(SHIM.format(repo=self.repo_dir), encoding="utf-8")
        shim.chmod(0o755)
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {
            "slate": {"type": "stdio", "command": str(shim), "args": ["mcp"], "env": {}},
            "higgsfield-use-after-effects": {"type": "stdio", "command": "/opt/homebrew/bin/node",
                                             "args": [f"{BRIDGE_DIR}/dist/index.js"], "env": {}},
        }}), encoding="utf-8")
        desktop = self.home / "Library" / "Application Support" / "Claude" / "claude-code" / "2.1.284" / "claude.app"
        desktop.mkdir(parents=True, exist_ok=True)
        (self.home / ".config" / "higgsfield").mkdir(parents=True, exist_ok=True)
        (self.home / ".config" / "higgsfield" / "credentials.json").write_text("{}", encoding="utf-8")
        eng = self.engine_folder
        (eng / "node_modules" / "remotion").mkdir(parents=True, exist_ok=True)
        (eng / "node_modules" / "remotion" / "package.json").write_text(json.dumps({"version": "4.0.521"}),
                                                                         encoding="utf-8")
        chrome = eng / "node_modules" / ".remotion" / "chrome-headless-shell"
        exe = chrome / "mac-arm64" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("", encoding="utf-8")
        (chrome / "VERSION").write_text("149.0.7790.0\n", encoding="utf-8")
        (eng / "remotion").mkdir(parents=True, exist_ok=True)
        (eng / "remotion" / "remotion.config.ts").write_text("Config.setShouldOpenBrowser(false);\n",
                                                            encoding="utf-8")

    def set_response(self, *key: str, code: int = 0, out: str = "", err: str = "") -> None:
        self.responses[tuple(key)] = Ran(code, out, err)

    # -- the host --------------------------------------------------------------------------------------------------

    def system(self):
        return self.sys

    def release(self):
        return self.rel

    def mac_version(self):
        return self.mac

    def arch(self):
        return self.host_arch

    def getenv(self, name):
        return self.envvars.get(name)

    def agent_context(self):
        return self.agent

    def person_at_terminal(self):
        return self.terminal

    # -- processes -------------------------------------------------------------------------------------------------

    def which(self, tool):
        self.lookups.append(tool)
        return self.tools.get(tool)

    def run(self, argv, *, timeout=30.0, cache=True, no_open=False):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        full = (argv[0], *argv[1:])  # a response keyed by the full path wins (two copies of one tool)
        if full in self.responses:
            return self.responses[full]
        key = (os.path.basename(argv[0]), *argv[1:])
        if key in self.responses:
            return self.responses[key]
        if key[:2] in self.responses:  # e.g. ("osascript", "-e") for any script
            return self.responses[key[:2]]
        return Ran(127, "", f"{argv[0]}: not faked")

    def cleanup(self):
        pass

    # -- files -----------------------------------------------------------------------------------------------------

    def _under(self, p) -> bool:
        return str(p).startswith(str(self.tmp))

    def exists(self, p):
        return str(p) in self.present or str(p) in self.present_dirs or (self._under(p) and super().exists(p))

    def is_file(self, p):
        return str(p) in self.present or (self._under(p) and super().is_file(p))

    def is_dir(self, p):
        return str(p) in self.present_dirs or (self._under(p) and super().is_dir(p))

    def is_symlink(self, p):
        return self._under(p) and super().is_symlink(p)

    def is_executable(self, p):
        return str(p) in self.present or (self._under(p) and super().is_executable(p))

    def realpath(self, p):
        if str(p) in self.real:
            return self.real[str(p)]
        return super().realpath(p) if self._under(p) else str(p)

    def listdir(self, p):
        return super().listdir(p) if self._under(p) else []

    def size(self, p):
        if str(p) in self.sizes:
            return self.sizes[str(p)]
        return super().size(p) if self._under(p) else None

    def read_bytes(self, p, limit=None):
        if str(p) in self.texts:
            data = self.texts[str(p)].encode("utf-8")
            return data[:limit] if limit else data
        return super().read_bytes(p, limit) if self._under(p) else None

    def read_plist(self, p):
        if str(p) in self.plists:
            return self.plists[str(p)]
        return super().read_plist(p) if self._under(p) else None

    def archs(self, p):
        return self.arch_of.get(str(p), ["arm64"])

    def disk_free(self, p):
        return self.free_bytes

    def device(self, p):
        return 1

    # -- Slate and vendors -----------------------------------------------------------------------------------------

    def repo(self):
        return self.repo_dir

    def settings(self):
        return {"jobs": {"disk_reserve_gb": 5.0}}

    def replay(self):
        return self.is_replay

    def secret_where(self, name):
        return self.secrets.get(name)

    def store_key(self, name):
        self.stored.append(name)
        if self.store_exc:
            raise self.store_exc
        return self.store_rc

    def oneiro_cap(self):
        return 20000

    def eleven_subscription(self):
        self.eleven_calls += 1
        if isinstance(self.eleven, Exception):
            raise self.eleven
        return dict(self.eleven)

    def higgsfield_probe(self):
        if isinstance(self.hf_probe, Exception):
            raise self.hf_probe
        return dict(self.hf_probe)

    def tls_info(self):
        return dict(self.tls)

    def rubberband_info(self):
        if isinstance(self.rubberband, Exception):
            raise self.rubberband
        return dict(self.rubberband)

    def engine_dir(self):
        if isinstance(self.engine, Exception):
            raise self.engine
        return self.engine

    def ae_installs(self):
        return list(self.ae_list)

    def aerender_path(self):
        if isinstance(self.aerender, Exception):
            raise self.aerender
        return self.aerender

    def ae_prefs(self):
        if isinstance(self.prefs, Exception):
            raise self.prefs
        return dict(self.prefs)

    def ae_bridge_status(self):
        if isinstance(self.bridge, Exception):
            raise self.bridge
        return dict(self.bridge)

    def ae_bridge_pin(self):
        return "0.1.3"

    def stability_conformance(self):
        if isinstance(self.stability, Exception):
            raise self.stability
        return dict(self.stability)

    def ae_smoke_last(self):
        if isinstance(self.smoke, Exception):
            raise self.smoke
        return self.smoke

    def ae_running(self):
        return self.ae_is_running

    def ae_bridge_install(self):
        self.bridge_installs += 1
        self.bridge = {**self.bridge, "installed": True, "version": "0.1.3", "pin_ok": True}
        return {"ran": True, "version": "0.1.3"}

    def ae_smoke_run(self, out_dir=None):
        self.smoke_runs.append(out_dir)
        if isinstance(self.smoke_result, Exception):
            raise self.smoke_result
        return self.smoke_result

    def ae_font_check(self, families):
        self.font_checks.append(list(families))
        return dict(self.font_check_result)

    def jobs_agent(self):
        if isinstance(self.jobs, Exception):
            raise self.jobs
        return dict(self.jobs)

    def jobs_summary(self):
        if isinstance(self.queue, Exception):
            raise self.queue
        return dict(self.queue)

    def ocr_available(self):
        return self.ocr

    def instrument_rows(self):
        if isinstance(self.instruments, Exception):
            raise self.instruments
        return [dict(r) for r in self.instruments]

    def instruments_download(self):
        return "Open GarageBand > Sound Library > Download All Available Sounds."

    def broken_modules(self):
        return self.broken

    def ocr_requirements(self):
        return ["ocrmac>=1.0"]

    def processes(self):
        return None if self.process_list is None else [dict(p) for p in self.process_list]

    def ae_windows(self):
        if not self.ae_is_running:
            return {"running": False, "windows": [], "error": None}
        if self.ae_window_titles is None:
            return {"running": True, "windows": None, "error": self.ae_window_error or "not allowed assistive access"}
        return {"running": True, "windows": list(self.ae_window_titles), "error": None}

    def mcp_stdio_check(self):
        if isinstance(self.mcp_check, Exception):
            raise self.mcp_check
        return dict(self.mcp_check)

    def font_families(self):
        return None if self.fonts is None else set(self.fonts)

    def adobe_font_families(self):
        return set(self.adobe_fonts)

    def launchservices(self):
        return self.ls


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows}


def statuses(rows: list[dict]) -> dict[str, str]:
    return {r["id"]: r["status"] for r in rows}


def write_brand(store: Path, name: str, families: list[dict]) -> Path:
    """A neutral test brand pack in the private store (never a real brand)."""
    folder = store / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "brand.json").write_text(json.dumps({"name": name, "type": {"families": families}}), encoding="utf-8")
    return folder
