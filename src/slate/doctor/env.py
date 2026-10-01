"""What doctor reads from the machine, in one place.

Every fact `slate doctor` needs comes through an `Env`: a binary's path and version, a config file, a plist, whether a
key is there, another Slate module's own status. Each check is then a small function of what the Env reports, and a
test can swap any fact for a fake by subclassing Env (tests/doctor does exactly that).

Nothing here writes, prompts or opens anything:

- processes run through slate.procs (no shell, BROWSER=none, stdin closed, a timeout), and a failure is an answer
  (an exit code and its text), never an exception;
- files are only read, and a missing or unreadable file is None;
- other Slate modules are imported only when the check that needs them runs, so one broken module can't take doctor
  down with it (its own row turns red instead).
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shutil
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Sequence

from slate import config, paths, procs
from slate.errors import MissingPrerequisite, SlateError

# Mach-O CPU types, for telling an arm64 build from an Intel one without running it.
_CPU = {0x01000007: "x86_64", 0x0100000C: "arm64", 7: "i386", 12: "arm"}

# The installed font families as macOS itself lists them (CoreText, through AppKit's font manager). osascript's
# JavaScript bridge reads it in about a quarter of a second and opens no app.
JXA_FONT_FAMILIES = ('ObjC.import("AppKit"); '
                     'JSON.stringify(ObjC.deepUnwrap($.NSFontManager.sharedFontManager.availableFontFamilies))')

# Adobe Fonts activated through Creative Cloud. After Effects sees these even when macOS's own list doesn't, so the
# font check reads Creative Cloud's manifest too (family names only).
ADOBE_FONTS_MANIFESTS = (
    "Library/Application Support/Adobe/CoreSync/plugins/livetype/.c/entitlements.xml",
    "Library/Application Support/Adobe/CoreSync/plugins/livetype/c/entitlements.xml",
)

LAUNCHSERVICES = "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"


@dataclass
class Ran:
    """What a command did: its exit code and output. 127 means it wasn't found, 124 that it timed out."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return f"{self.stdout or ''}\n{self.stderr or ''}".strip()


def macho_archs(data: bytes) -> list[str]:
    """The architectures in a Mach-O header (thin or universal); empty for anything else, such as a script."""
    if len(data) < 8:
        return []
    be = int.from_bytes(data[:4], "big")
    if be in (0xCAFEBABE, 0xCAFEBABF):
        count = int.from_bytes(data[4:8], "big")
        if count > 30:  # 0xCAFEBABE is also a Java class file's magic; there these bytes are its version
            return []
        step = 20 if be == 0xCAFEBABE else 32
        out = []
        for i in range(count):
            off = 8 + i * step
            if off + 4 > len(data):
                break
            cpu = int.from_bytes(data[off:off + 4], "big")
            out.append(_CPU.get(cpu, hex(cpu)))
        return out
    le = int.from_bytes(data[:4], "little")
    if le in (0xFEEDFACF, 0xFEEDFACE):
        return [_CPU.get(int.from_bytes(data[4:8], "little"), "unknown")]
    if le in (0xCFFAEDFE, 0xCEFAEDFE):
        return [_CPU.get(int.from_bytes(data[4:8], "big"), "unknown")]
    return []


class Env:
    """The real machine. Tests subclass it and override whichever facts they need."""

    def __init__(self, home: str | os.PathLike | None = None):
        self.home = Path(home).expanduser() if home else Path.home()
        self._runs: dict[tuple, Ran] = {}
        self._running: dict[tuple, threading.Lock] = {}
        self._lock = threading.Lock()
        self._no_open_dir: str | None = None
        self._agent: tuple[str | None] | None = None

    # -- the host --------------------------------------------------------------------------------------------------

    def system(self) -> str:
        return platform.system()

    def release(self) -> str:
        return platform.release()

    def mac_version(self) -> str:
        return platform.mac_ver()[0] or "unknown"

    def arch(self) -> str:
        """This Mac's real architecture: arm64 on Apple silicon, even when Python itself runs under Rosetta."""
        ran = self.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], timeout=5)
        if ran.ok and ran.stdout.strip() == "1":
            return "arm64"
        return platform.machine() or "unknown"

    def getenv(self, name: str) -> str | None:
        return os.environ.get(name) or None

    def agent_context(self) -> str | None:
        """Which agent runs us (asked once per run: it can walk the process tree)."""
        if self._agent is None:
            self._agent = (procs.agent_context(),)
        return self._agent[0]

    def person_at_terminal(self) -> tuple[bool, str]:
        """(True, "") only for a person typing in their own Terminal (no agent, stdin a real TTY)."""
        from slate import ledger  # noqa: PLC0415

        return ledger.person_at_terminal()

    # -- processes -------------------------------------------------------------------------------------------------

    def which(self, tool: str) -> str | None:
        return procs.which(tool)

    def run(self, argv: Sequence[str | os.PathLike], *, timeout: float = 30.0, cache: bool = True,
            no_open: bool = False) -> Ran:
        """Run a command the Slate way and return what happened. Never raises.

        `no_open` puts a stand-in `open` first on the child's PATH, so a tool that tries to open a web page (a
        sign-in, say) opens nothing: defence in depth on top of BROWSER=none, for vendor CLIs doctor calls.
        """
        key = tuple(str(a) for a in argv) + (("no-open",) if no_open else ())
        if not cache:
            return self._run(argv, timeout, no_open)
        with self._lock:
            if key in self._runs:
                return self._runs[key]
            gate = self._running.setdefault(key, threading.Lock())
        with gate:  # checks asking for the same listing at once share one process
            with self._lock:
                if key in self._runs:
                    return self._runs[key]
            out = self._run(argv, timeout, no_open)
            with self._lock:
                self._runs[key] = out
        return out

    def _run(self, argv: Sequence[str | os.PathLike], timeout: float, no_open: bool) -> Ran:
        extra = {"PATH": f"{self._no_open_path()}{os.pathsep}{procs.child_path()}"} if no_open else None
        try:
            proc = procs.run([os.fspath(a) for a in argv], timeout=timeout, env=extra)
            return Ran(proc.returncode, proc.stdout or "", proc.stderr or "")
        except MissingPrerequisite as exc:
            return Ran(127, "", exc.message)
        except SlateError as exc:
            return Ran(124 if "longer than" in exc.message else 1, "", exc.message)
        except OSError as exc:
            return Ran(126, "", f"{type(exc).__name__}: {exc}")

    def _no_open_path(self) -> str:
        with self._lock:
            if self._no_open_dir is None or not os.path.isdir(self._no_open_dir):
                folder = tempfile.mkdtemp(prefix="slate-doctor-noopen-")
                for name in ("open", "xdg-open"):
                    shim = Path(folder) / name
                    shim.write_text("#!/bin/sh\n# slate doctor: nothing opens a browser from here.\nexit 1\n",
                                    encoding="utf-8")
                    shim.chmod(0o755)
                self._no_open_dir = folder
            return self._no_open_dir

    def cleanup(self) -> None:
        """Remove the stand-in `open` folder, if one was made."""
        if self._no_open_dir and os.path.isdir(self._no_open_dir):
            shutil.rmtree(self._no_open_dir, ignore_errors=True)
        self._no_open_dir = None

    # -- files -----------------------------------------------------------------------------------------------------

    def exists(self, path: str | os.PathLike) -> bool:
        return os.path.lexists(path)

    def is_file(self, path: str | os.PathLike) -> bool:
        return os.path.isfile(path)

    def is_dir(self, path: str | os.PathLike) -> bool:
        return os.path.isdir(path)

    def is_symlink(self, path: str | os.PathLike) -> bool:
        return os.path.islink(path)

    def is_executable(self, path: str | os.PathLike) -> bool:
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def realpath(self, path: str | os.PathLike) -> str:
        return os.path.realpath(path)

    def listdir(self, path: str | os.PathLike) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def size(self, path: str | os.PathLike) -> int | None:
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    def read_bytes(self, path: str | os.PathLike, limit: int | None = None) -> bytes | None:
        try:
            with open(path, "rb") as fh:
                return fh.read(limit) if limit else fh.read()
        except OSError:
            return None

    def read_text(self, path: str | os.PathLike, limit: int | None = None) -> str | None:
        data = self.read_bytes(path, limit)
        return None if data is None else data.decode("utf-8", "replace")

    def read_json(self, path: str | os.PathLike) -> Any:
        text = self.read_text(path)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def read_toml(self, path: str | os.PathLike) -> dict | None:
        text = self.read_text(path)
        if text is None:
            return None
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return None

    def read_plist(self, path: str | os.PathLike) -> dict | None:
        data = self.read_bytes(path)
        if data is None:
            return None
        try:
            value = plistlib.loads(data)
        except Exception:  # noqa: BLE001 - plistlib raises several types for a bad file
            return None
        return value if isinstance(value, dict) else None

    def archs(self, path: str | os.PathLike) -> list[str]:
        data = self.read_bytes(os.path.realpath(path), 4096)
        return macho_archs(data) if data else []

    def disk_free(self, path: str | os.PathLike) -> int:
        return procs.disk_free_bytes(path)

    def device(self, path: str | os.PathLike) -> int | None:
        p = Path(path)
        while not p.exists() and p != p.parent:
            p = p.parent
        try:
            return os.stat(p).st_dev
        except OSError:
            return None

    # -- processes and windows (read-only) ------------------------------------------------------------------------

    def processes(self) -> list[dict] | None:
        """Every process as {pid, ppid, command} (`ps`, read-only), or None when the list can't be read. Command lines
        can carry secrets, so callers only ever compare them, never report them."""
        ran = self.run(["/bin/ps", "-axo", "pid=,ppid=,command="], timeout=20, cache=False)
        if not ran.ok:
            return None
        out = []
        for line in ran.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                out.append({"pid": int(parts[0]), "ppid": int(parts[1]), "command": parts[2] if len(parts) > 2 else ""})
        return out

    def ae_windows(self) -> dict:
        """After Effects' open windows by title, through System Events (it never launches AE: it asks only when AE is
        already running). {running, windows: [titles] | None, error}. Reading another app's windows needs the
        Accessibility permission for the app running doctor; without it, windows is None and error says why."""
        script = ('tell application "System Events"\n'
                  '  set ps to (every process whose bundle identifier is "com.adobe.AfterEffects.application")\n'
                  '  if ps is {} then return "not running"\n'
                  '  set out to ""\n'
                  '  repeat with w in (every window of (item 1 of ps))\n'
                  '    set out to out & (name of w as text) & linefeed\n'
                  '  end repeat\n'
                  '  return "windows:" & linefeed & out\n'
                  'end tell')
        ran = self.run(["/usr/bin/osascript", "-e", script], timeout=15, cache=False)
        if ran.ok and ran.stdout.strip() == "not running":
            return {"running": False, "windows": [], "error": None}
        if ran.ok and ran.stdout.startswith("windows:"):
            titles = [t.strip() for t in ran.stdout.splitlines()[1:] if t.strip()]
            return {"running": True, "windows": titles, "error": None}
        return {"running": True, "windows": None, "error": (ran.text or f"exit {ran.returncode}")[:200]}

    def claude_desktop_versions(self) -> list[str]:
        """The Claude Code builds the desktop app keeps for itself (~/Library/Application Support/Claude/claude-code/
        <version>/claude.app), newest last."""
        root = self.home / "Library" / "Application Support" / "Claude" / "claude-code"
        found = [n for n in self.listdir(root) if re.fullmatch(r"\d+(\.\d+)+", n)
                 and self.exists(root / n / "claude.app")]
        return sorted(found, key=lambda v: tuple(int(x) for x in v.split(".")))

    # -- Slate itself ----------------------------------------------------------------------------------------------

    def repo(self) -> Path:
        return paths.REPO

    def data_dir(self) -> Path:
        return paths.data()

    def films_dir(self) -> Path:
        return paths.films()

    def brand_dirs(self) -> list[Path]:
        return paths.brand_dirs()

    def settings(self) -> dict:
        return config.load()

    def registry_tool(self, name: str) -> dict:
        try:
            return dict(config.registry().get("tools", {}).get(name) or {})
        except SlateError:
            return {}

    def engine_pin(self) -> str:
        return config.engine_version()

    def replay(self) -> bool:
        from slate import replay  # noqa: PLC0415

        return replay.fake_vendors()

    def secret_where(self, name: str) -> str | None:
        """'env', 'keychain' or None. Never reads the value (slate.secrets.present)."""
        from slate import secrets  # noqa: PLC0415

        return secrets.present(name)

    def store_key(self, name: str) -> int:
        """Ask for a key in macOS's own prompt and store it in the Keychain. Refuses outside a person's Terminal."""
        from slate import secrets  # noqa: PLC0415

        return secrets.store_interactive(name)

    def oneiro_cap(self) -> float | None:
        from slate import ledger  # noqa: PLC0415

        try:
            return ledger.budget_config()["monthly"]["autonomous"]["elevenlabs"]
        except (KeyError, TypeError, SlateError):
            return None

    def eleven_subscription(self) -> dict:
        """ElevenLabs' free plan read (GET /v1/user/subscription), replay-aware. Raises on failure."""
        from slate.vendors import elevenlabs  # noqa: PLC0415

        return elevenlabs.Client().subscription()

    def eleven_tier_rank(self, tier: str | None) -> tuple[int | None, tuple[str, ...]]:
        from slate.vendors import elevenlabs  # noqa: PLC0415

        return elevenlabs.tier_rank(tier), tuple(elevenlabs.TIERS)

    def higgsfield_probe(self) -> dict:
        """Higgsfield's zero-cost key probe (404 with a key, 401 without), replay-aware."""
        from slate.vendors import higgsfield  # noqa: PLC0415

        return higgsfield.Client().probe()

    def tls_info(self) -> dict:
        """Can this Python verify TLS certificates? Counts the CA certificates the default context loads."""
        import ssl  # noqa: PLC0415

        source = "Python's default context"
        try:
            from slate import http  # noqa: PLC0415

            maker = getattr(http, "ssl_context", None)
            if callable(maker):
                ctx = maker()
                source = "slate.http"
            else:
                ctx = ssl.create_default_context()
        except Exception:  # noqa: BLE001 - fall back to what urllib would use
            ctx = ssl.create_default_context()
        stats = ctx.cert_store_stats() if hasattr(ctx, "cert_store_stats") else {}
        dv = ssl.get_default_verify_paths()
        capath = dv.openssl_capath
        python_default = int(ssl.create_default_context().cert_store_stats().get("x509_ca", 0))
        loaded_from = None
        if source == "slate.http" and not python_default:
            # slate.http fills an empty default store from SSL_CERT_FILE or the system bundle, in that order; name the
            # file it used, not Python's own (missing) one.
            from slate import http  # noqa: PLC0415

            for path in (os.environ.get("SSL_CERT_FILE"), *getattr(http, "SYSTEM_CA_FILES", ())):
                if path and os.path.exists(path):
                    loaded_from = path
                    break
        return {
            "ca_certs": int(stats.get("x509_ca", 0)),
            "source": source,
            "loaded_from": loaded_from,
            "python_default_ca_certs": python_default,
            "cafile": dv.openssl_cafile,
            "cafile_exists": bool(dv.openssl_cafile and os.path.isfile(dv.openssl_cafile)),
            "capath": capath,
            "capath_has_certs": bool(capath and os.path.isdir(capath) and os.listdir(capath)),
            "env_cert_file": os.environ.get("SSL_CERT_FILE"),
            "system_bundle": "/etc/ssl/cert.pem" if os.path.isfile("/etc/ssl/cert.pem") else None,
        }

    def rubberband_info(self) -> dict:
        from slate.voice import pace  # noqa: PLC0415

        return pace.rubberband_info()

    def engine_dir(self) -> tuple[Path, str]:
        """The Node engine Slate would run, and where it came from ('installed', 'repo' or 'env'). Raises when none."""
        from slate.engine import bridge  # noqa: PLC0415

        folder = bridge.engine_dir()
        return folder, bridge.engine_source()

    def ae_installs(self) -> list[dict]:
        from slate.ae import driver  # noqa: PLC0415

        return driver.installs()

    def aerender_path(self) -> str:
        from slate.ae import driver  # noqa: PLC0415

        return driver.aerender_path()

    def ae_prefs(self) -> dict:
        """AE's prefs read byte-safely through slate.ae.prefs.check (read-only). Raises when there's no prefs file."""
        from slate.ae import prefs  # noqa: PLC0415

        return prefs.check()

    def ae_bridge_status(self) -> dict:
        from slate.ae import bridge  # noqa: PLC0415

        return bridge.status()

    def ae_bridge_pin(self) -> str:
        from slate.ae import bridge  # noqa: PLC0415

        return bridge.pinned()

    def ae_smoke_last(self) -> Any:
        """The last smoke test's GateResult (never starts AE: without go, smoke.run only reports)."""
        from slate.ae import smoke  # noqa: PLC0415

        return smoke.run()

    def stability_conformance(self) -> dict:
        """{record, bench, spec_sha256}: the render-stability conformance record in Slate's data folder (None when the
        procedure hasn't run on this Mac), and whether the engine kit's stability bench spec is in this checkout."""
        from slate import jsonio  # noqa: PLC0415
        from slate.qa import stability  # noqa: PLC0415

        rec = self.read_json(self.data_dir() / stability.CONFORMANCE_RECORD)
        spec = stability.bench_spec_path()
        bench = spec.is_file()
        return {"record": rec if isinstance(rec, dict) else None, "bench": bench,
                "spec_sha256": jsonio.sha256(spec) if bench else None}

    def ae_running(self) -> bool:
        return procs.ae_running()

    # The three AE actions `slate connect aftereffects` takes, only ever on a person's go.

    def ae_bridge_install(self) -> dict:
        from slate.ae import bridge  # noqa: PLC0415

        return bridge.install(yes=True)

    def ae_smoke_run(self, out_dir: str | os.PathLike | None = None) -> Any:
        from slate.ae import smoke  # noqa: PLC0415

        return smoke.run(out_dir, go=True)

    def ae_font_check(self, families: list[str]) -> dict:
        from slate.ae import driver  # noqa: PLC0415

        return driver.font_check(families)

    def jobs_agent(self) -> dict:
        from slate.jobs import agent  # noqa: PLC0415

        return agent.status()

    def jobs_summary(self) -> dict:
        """The queue as `slate jobs status --json` reports it: whether a runner is serving, counts per state, next up."""
        from slate.jobs import client  # noqa: PLC0415

        return client.summary()

    def ocr_available(self) -> tuple[bool, dict]:
        from slate.qa import ocr  # noqa: PLC0415

        return ocr.available()

    def instrument_rows(self) -> list[dict]:
        from slate.sound import instruments  # noqa: PLC0415

        return instruments.doctor_check()

    def instruments_download(self) -> str:
        from slate.sound import instruments  # noqa: PLC0415

        return instruments.DOWNLOAD

    def broken_modules(self) -> tuple[dict[str, str], int]:
        """Slate's command modules that don't import (slate.cli.BROKEN), and how many modules there are."""
        from slate import cli  # noqa: PLC0415

        cli.build_parser()
        return dict(cli.BROKEN), len(cli.MODULES)

    def mcp_stdio_check(self) -> dict:
        """Start `slate mcp` as a client would, list its tools and call its read-only status (slate.mcp.cli)."""
        from slate.mcp import cli as mcp_cli  # noqa: PLC0415

        return mcp_cli.stdio_check(timeout=60)

    def ocr_requirements(self) -> list[str]:
        data = self.read_toml(self.repo() / "pyproject.toml") or {}
        return list(((data.get("project") or {}).get("optional-dependencies") or {}).get("ocr") or [])

    # -- fonts and browsers ----------------------------------------------------------------------------------------

    def font_families(self) -> set[str] | None:
        """Font families macOS lists (CoreText), or None when it couldn't be asked."""
        ran = self.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", JXA_FONT_FAMILIES], timeout=30)
        if not ran.ok:
            return None
        try:
            return {str(x) for x in json.loads(ran.stdout)}
        except ValueError:
            return None

    def adobe_font_families(self) -> set[str]:
        """Families activated through Adobe Fonts (Creative Cloud's manifest), which After Effects can use."""
        for rel in ADOBE_FONTS_MANIFESTS:
            text = self.read_text(self.home / rel)
            if text:
                return {unescape(m).strip() for m in re.findall(r"<familyName>([^<]+)</familyName>", text)}
        return set()

    def launchservices(self) -> dict | None:
        return self.read_plist(self.home / LAUNCHSERVICES)
