"""Binaries, versions and the Node engine: every tool Slate runs, checked the way Slate will run it.

Doctor resolves each tool exactly as slate.procs does (Homebrew's arm64 folder and ~/.local/bin first) and then runs it
or reads its version, because being on PATH isn't enough:

- `/usr/local/bin` can hold stale Intel builds (an ffmpeg 7.1.1 and a Node 21.6.2 were found there), so the version
  and the architecture of what Slate will actually run are what count, the ignored copies are named, and
  `tool.stale-copies` lists each stale one with its version, why it's stale and how to remove it (anything started
  with macOS's default PATH finds those first);
- a Homebrew upgrade once left ffmpeg unable to load its own libraries (PIPELINE.md, "the ffmpeg fix"), so a tool that
  exists but won't run is red.

Some tools are never run here: aerender (it starts After Effects' engine), Aside (it's a browser) and Whisper (a slow
start). Their versions come from their bundles and install folders instead.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from slate.doctor import rows as R
from slate.doctor.common import (ARM_BREW, Ctx, brew_item, call_item, first_line, major, npm_global_item, tilde,
                                 version_in)
from slate.doctor.env import Env

G = "tools"
FULL_FFMPEG_KEGS = ("/opt/homebrew/opt/ffmpeg-full",)  # the keg-only full build, if one is ever needed
MACOS_TOOLS = (
    ("osascript", "/usr/bin/osascript", "the After Effects lane talks to AE through it"),
    ("caffeinate", "/usr/bin/caffeinate", "long jobs keep the Mac awake with it"),
    ("security", "/usr/bin/security", "keys are read from your login Keychain with it"),
)
OPTIONAL_TOOLS = (
    ("rsvg-convert", "librsvg", "drawing a brand's SVG logos during onboarding"),
    ("potrace", "potrace", "tracing a logo from a picture during onboarding"),
)
# /usr/local/bin is where an Intel-era Homebrew (and Node's own installer) put their tools, and macOS's default PATH
# (/etc/paths) lists it before /opt/homebrew/bin. Slate never runs what's there (slate.procs looks in /opt/homebrew/bin
# first), but anything started with that default PATH, or a command typed where it comes first, does. On this kind of
# Mac an ffmpeg 7.1.1 and ffprobe built for x86_64 and a Node 21.6.2 were found there on 2026-09-30.
# (tool, the registry entry holding its version floor or None, how to ask its version, where the number is)
STALE_DIR = "/usr/local/bin"
STALE_TOOLS = (
    ("ffmpeg", "ffmpeg", ("-hide_banner", "-version"), r"version\s+n?(\d+(?:\.\d+)*)"),
    ("ffprobe", "ffmpeg", ("-hide_banner", "-version"), r"version\s+n?(\d+(?:\.\d+)*)"),
    ("node", "node", ("--version",), r"v?(\d+(?:\.\d+)+)"),
    ("rubberband", None, ("--version",), r"(\d+(?:\.\d+)+)"),
    ("uv", None, ("--version",), r"(\d+(?:\.\d+)+)"),
    ("whisper", None, None, None),
)
STALE_FLOORS = {"ffmpeg": 9, "node": 24}  # used when registry.json doesn't say


def _floor(env: Env, tool: str, default: int) -> int:
    spec = str(env.registry_tool(tool).get("version") or "")
    m = re.search(r"(\d+)", spec)
    return int(m.group(1)) if m else default


def keg_of(resolved: str) -> str | None:
    """The Homebrew keg a binary lives in (…/Cellar/<formula>/<version>), when it's a Homebrew build."""
    m = re.match(r"^(.*/Cellar/[^/]+/[^/]+)/", resolved)
    return m.group(1) if m else None


def shadowed(env: Env, tool: str, used: str) -> list[str]:
    """Other copies of a tool on the caller's PATH or in /usr/local/bin. Slate ignores them; they're named so nobody
    is surprised when a plain Terminal finds a different one."""
    seen: list[str] = []
    used_real = env.realpath(used)
    dirs = (env.getenv("PATH") or "").split(os.pathsep) + ["/usr/local/bin", "/opt/homebrew/bin"]
    for d in dirs:
        if not d:
            continue
        cand = os.path.join(d, tool)
        if cand not in seen and env.is_executable(cand) and env.realpath(cand) != used_real:
            seen.append(cand)
    return seen


def ignoring(paths: list[str], env: Env, limit: int = 2) -> str:
    """'ignores A, B and 3 more' for a summary line; the full list stays in details."""
    if not paths:
        return ""
    shown = [tilde(env, p) for p in paths[:limit]]
    more = len(paths) - limit
    return f"ignores {', '.join(shown)}" + (f" and {more} more" if more > 0 else "")


def _npm_version(env: Env, binary: str, package: str) -> str | None:
    """The version of the npm package a CLI comes from, read from its package.json (nothing is run)."""
    here = Path(env.realpath(binary))
    for parent in list(here.parents)[:6]:
        data = env.read_json(parent / "package.json")
        if isinstance(data, dict) and data.get("name") == package:
            return str(data.get("version") or "") or None
    return None


# ---------------------------------------------------------------------------------------------------------------
# ffmpeg and ffprobe


def check_ffmpeg(env: Env, ctx: Ctx) -> list[dict]:
    return [_av_tool(env, "ffmpeg"), _av_tool(env, "ffprobe")]


def _av_tool(env: Env, tool: str) -> dict:
    rid = f"tool.{tool}"
    floor = _floor(env, "ffmpeg", 9)
    what = f"ffmpeg and ffprobe {floor} (Homebrew, Apple silicon)"
    path = env.which(tool)
    if not path:
        return R.red(rid, f"{tool} isn't installed. Slate encodes, measures loudness and checks frames with it.",
                     "brew install ffmpeg (or `slate doctor --install`, which asks first)", group=G,
                     install=brew_item(env, "ffmpeg", what, size="about 150 MB with its libraries"))
    resolved = env.realpath(path)
    details: dict = {"path": path, "resolved": resolved, "archs": env.archs(path), "keg": keg_of(resolved),
                     "shadowed": shadowed(env, tool, path)}
    ran = env.run([path, "-hide_banner", "-version"], timeout=20)
    if not ran.ok:
        why = first_line(ran.stderr or ran.stdout) or f"exit {ran.returncode}"
        return R.red(rid, f"{tool} at {path} is installed but doesn't run: {why}",
                     "brew upgrade ffmpeg. A Homebrew upgrade can leave ffmpeg linked to a library that has moved; "
                     "upgrading it is how that was fixed on 2026-09-28.", group=G, details=details,
                     install=brew_item(env, "ffmpeg", what, upgrade=True))
    version = version_in(ran.stdout, r"version\s+n?(\d+(?:\.\d+)*)")
    details["version"] = version
    if tool == "ffmpeg":
        filters = env.run([path, "-hide_banner", "-filters"], timeout=20)
        details["drawtext"] = bool(re.search(r"\bdrawtext\b", filters.stdout)) if filters.ok else None
        full = [k for k in FULL_FFMPEG_KEGS if env.is_dir(k)]
        if full:
            details["full_build_keg"] = env.realpath(full[0])
    host = env.arch()
    archs = details["archs"]
    if archs and host not in archs:
        return R.red(rid, f"{tool} {version or ''} at {path} is built for {', '.join(archs)}, not this {host} Mac.",
                     f"brew install ffmpeg with the Apple-silicon Homebrew ({ARM_BREW}); Slate looks in "
                     "/opt/homebrew/bin first, so the other copy is then ignored.", group=G, details=details,
                     install=brew_item(env, "ffmpeg", what))
    if (major(version) or 0) < floor:
        return R.red(rid, f"{tool} {version or '(version unreadable)'} at {path} is older than {floor}.",
                     "brew upgrade ffmpeg", group=G, details=details,
                     install=brew_item(env, "ffmpeg", what, upgrade=bool(details["keg"])))
    notes = []
    if details["keg"]:
        notes.append(f"keg {details['keg']}")
    if details.get("full_build_keg"):
        notes.append(f"full build keg {details['full_build_keg']}")
    if details["shadowed"]:
        notes.append(ignoring(details["shadowed"], env))
    return R.green(rid, f"{tool} {version} ({host}) at {path}" + (f"; {'; '.join(notes)}" if notes else ""),
                   group=G, details=details)


# ---------------------------------------------------------------------------------------------------------------
# Rubber Band, uv, Node, git, gh


def check_rubberband(env: Env, ctx: Ctx) -> list[dict]:
    rid = "tool.rubberband"
    item = brew_item(env, "rubberband", "Rubber Band, the arm64 build (voice pacing)")
    info = env.rubberband_info()
    status = info.get("status")
    path = info.get("path")
    version = info.get("version") or "(version unreadable)"
    details = {k: info.get(k) for k in ("path", "resolved", "version", "arch", "machine", "status")}
    if status == "missing" or not path:
        return [R.red(rid, "Rubber Band isn't installed. Voice pacing (the gentle tempo step after each read) needs it.",
                      "brew install rubberband", group=G, details=details, install=item)]
    if status == "rosetta":
        return [R.yellow(rid, f"Rubber Band {version} at {path} is the Intel (x86_64) build, running under Rosetta. "
                              "It works, but pacing should run on the arm64 build.",
                         "brew install rubberband (with the Apple-silicon Homebrew in /opt/homebrew). Slate looks "
                         "there first, so the Intel copy is then simply ignored.",
                         group=G, details=details, install=item)]
    if status == "foreign":
        return [R.red(rid, f"Rubber Band at {path} is built for {', '.join(info.get('arch') or [])}, which this "
                           f"{info.get('machine')} Mac can't run.", "brew install rubberband", group=G,
                      details=details, install=item)]
    how = f"native {info.get('machine')}" if info.get("native") else "not a Mach-O binary; assumed native"
    return [R.green(rid, f"Rubber Band {version} ({how}) at {path}", group=G, details=details)]


def check_uv(env: Env, ctx: Ctx) -> list[dict]:
    rid = "tool.uv"
    item = brew_item(env, "uv", "uv (manages Slate's Python)")
    path = env.which("uv")
    if not path:
        return [R.red(rid, "uv isn't installed. It manages Slate's Python environment and its updates.",
                      "brew install uv", group=G, install=item)]
    ran = env.run([path, "--version"], timeout=15)
    if not ran.ok:
        return [R.red(rid, f"uv at {path} doesn't run: {first_line(ran.text)}", "brew reinstall uv", group=G,
                      install=item)]
    version = version_in(ran.stdout)
    return [R.green(rid, f"uv {version} at {path}", group=G, details={"path": path, "version": version})]


def check_node(env: Env, ctx: Ctx) -> list[dict]:
    rid = "tool.node"
    floor = _floor(env, "node", 24)
    item = brew_item(env, "node", f"Node {floor} or newer (Homebrew, Apple silicon)")
    path = env.which("node")
    if not path:
        return [R.red(rid, f"Node isn't installed. Slate's Remotion engine and the AE bridge run on Node {floor} or newer.",
                      "brew install node", group=G, install=item)]
    ran = env.run([path, "--version"], timeout=15)
    if not ran.ok:
        return [R.red(rid, f"Node at {path} doesn't run: {first_line(ran.text)}", "brew reinstall node", group=G,
                      install=item)]
    version = version_in(ran.stdout, r"v?(\d+(?:\.\d+)+)")
    archs = env.archs(path)
    host = env.arch()
    details: dict = {"path": path, "version": version, "archs": archs, "shadowed": shadowed(env, "node", path)}
    if (major(version) or 0) < floor:
        return [R.red(rid, f"Node {version} at {path} is older than {floor}: Remotion and the AE bridge need {floor} "
                           "or newer.", "brew install node (Slate looks in /opt/homebrew/bin first)", group=G,
                      details=details, install=item)]
    if archs and host not in archs:
        return [R.red(rid, f"Node {version} at {path} is built for {', '.join(archs)}, not this {host} Mac.",
                      "brew install node with the Apple-silicon Homebrew", group=G, details=details, install=item)]
    npm = env.which("npm")
    npm_ran = env.run([npm, "--version"], timeout=20) if npm else None
    if not npm or not npm_ran or not npm_ran.ok:
        return [R.red(rid, f"Node {version} is here, but npm {'is missing' if not npm else 'does not run'}: installing "
                           "the engine and the AE bridge needs it.", "brew reinstall node (npm comes with it)",
                      group=G, details=details, install=item)]
    details["npm"] = {"path": npm, "version": version_in(npm_ran.stdout)}
    note = f"; {ignoring(details['shadowed'], env)}" if details["shadowed"] else ""
    return [R.green(rid, f"Node {version} ({host}) and npm {details['npm']['version']} at {path}{note}", group=G,
                    details=details)]


def check_git(env: Env, ctx: Ctx) -> list[dict]:
    rows = []
    item = brew_item(env, "git", "git")
    path = env.which("git")
    if not path:
        rows.append(R.red("tool.git", "git isn't installed. Film folders are git repositories, and Slate's updates come "
                                      "through git.", "brew install git", group=G, install=item))
    elif path == "/usr/bin/git" and not env.run(["/usr/bin/xcode-select", "-p"], timeout=10).ok:
        # Apple's /usr/bin/git is a stub that pops up an installer when the command-line tools are missing, so it
        # isn't run here.
        rows.append(R.red("tool.git", "git here is Apple's stand-in, which needs the Xcode command-line tools, and "
                                      "they aren't installed.",
                          "Run `xcode-select --install` yourself (it opens Apple's installer), or brew install git.",
                          group=G, install=item))
    else:
        ran = env.run([path, "--version"], timeout=15)
        if ran.ok:
            rows.append(R.green("tool.git", f"git {version_in(ran.stdout)} at {path}", group=G,
                                details={"path": path, "version": version_in(ran.stdout)}))
        else:
            rows.append(R.red("tool.git", f"git at {path} doesn't run: {first_line(ran.text)}", "brew install git",
                              group=G, install=item))
    gh = env.which("gh")
    gh_item = brew_item(env, "gh", "gh (GitHub's command-line tool)")
    if not gh:
        rows.append(R.yellow("tool.gh", "gh (GitHub's command-line tool) isn't installed. Slate's updates come from a "
                                        "private GitHub repo, and gh keeps that sign-in.", "brew install gh",
                             group=G, install=gh_item))
    else:
        ran = env.run([gh, "--version"], timeout=15)
        if ran.ok:
            rows.append(R.green("tool.gh", f"gh {version_in(ran.stdout)} at {gh}", group=G,
                                details={"path": gh, "version": version_in(ran.stdout)}))
        else:
            rows.append(R.yellow("tool.gh", f"gh at {gh} doesn't run: {first_line(ran.text)}", "brew reinstall gh",
                                 group=G, install=gh_item))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Whisper and its model


def whisper_model_path(env: Env) -> Path:
    cache = Path(env.getenv("XDG_CACHE_HOME") or env.home / ".cache")
    return cache / "whisper" / "large-v3-turbo.pt"


def check_whisper(env: Env, ctx: Ctx) -> list[dict]:
    rows = []
    path = env.which("whisper")
    item = brew_item(env, "openai-whisper", "Whisper (openai-whisper, Slate's aligner)", size="about 1 GB with PyTorch")
    if not path:
        rows.append(R.red("tool.whisper", "Whisper isn't installed. It's Slate's aligner: every word's time on the "
                                          "film clock comes from it.", "brew install openai-whisper", group=G,
                          install=item))
    else:
        resolved = env.realpath(path)
        m = re.search(r"/Cellar/openai-whisper/([^/]+)/", resolved)
        version = m.group(1) if m else None
        rows.append(R.green("tool.whisper", f"openai-whisper {version or '(version not in its path)'} at {path}",
                            group=G, details={"path": path, "resolved": resolved, "version": version}))
    size_gb = float(env.registry_tool("whisper-model").get("size_gb") or 1.6)
    model = whisper_model_path(env)
    size = env.size(model)
    model_item = call_item("whisper-model", f"Whisper's turbo model (large-v3-turbo, {size_gb:g} GB)",
                           f"{path or 'whisper'} <a 1-second silent clip> --model turbo  (downloads the model into "
                           f"{tilde(env, model.parent)})", size=f"{size_gb:g} GB")
    details = {"path": str(model), "bytes": size, "expected_gb": size_gb}
    if size is None:
        rows.append(R.red("tool.whisper-model", f"Whisper's turbo model isn't downloaded ({tilde(env, model)}, "
                                                f"{size_gb:g} GB). Alignment can't run without it.",
                          "slate doctor --install downloads it (it asks first)", group=G, details=details,
                          install=model_item))
    elif size < 0.9 * size_gb * 1e9:
        rows.append(R.red("tool.whisper-model", f"Whisper's turbo model at {tilde(env, model)} is incomplete "
                                                f"({size / 1e9:.2f} of {size_gb:g} GB): a download stopped part way.",
                          "slate doctor --install downloads it again (Whisper checks the file's hash and replaces a "
                          "partial one)", group=G, details=details, install=model_item))
    else:
        rows.append(R.green("tool.whisper-model", f"Whisper's turbo model (large-v3-turbo, {size / 1e9:.1f} GB) at "
                                                  f"{tilde(env, model)}", group=G, details=details))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# The Node engine and its headless Chrome


def check_engine(env: Env, ctx: Ctx) -> list[dict]:
    g = "engine"
    pin = env.engine_pin()
    expected = str(env.registry_tool("chrome-headless-shell").get("version") or "149")
    engine_item = call_item("engine", f"Slate's Node engine (Remotion {pin}, pinned exactly: `npm ci` installs the "
                                      "lockfile's packages into Slate's data folder) and its headless Chrome",
                            "slate engine install --npm-ci, then node <engine>/cli.mjs browser-ensure",
                            size="about 750 MB with Chrome")
    chrome_item = call_item("chrome", f"Remotion's headless Chrome (chrome-headless-shell {expected}; it only renders "
                                      "Slate's own pages)", "node <engine>/cli.mjs browser-ensure",
                            size="about 190 MB")
    try:
        folder, source = env.engine_dir()
    except Exception as exc:  # noqa: BLE001 - a missing engine is a finding, not a crash
        why = getattr(exc, "message", str(exc))
        return [R.red("engine.remotion", f"Slate's Node engine isn't installed ({why})",
                      "slate engine install --npm-ci (or `slate doctor --install`, which asks first)", group=g,
                      install=engine_item),
                R.red("engine.chrome", "Remotion's headless Chrome can't be checked until the engine is installed.",
                      "slate doctor --install (the engine first, then Chrome)", group=g, install=chrome_item)]
    folder = Path(folder)
    pkg = env.read_json(folder / "node_modules" / "remotion" / "package.json") or {}
    version = pkg.get("version") if isinstance(pkg, dict) else None
    details = {"engine_dir": str(folder), "source": source, "remotion": version, "pinned": pin}
    out = []
    if version != pin:
        out.append(R.red("engine.remotion", f"The engine in {tilde(env, folder)} has Remotion {version or '(missing)'}, "
                                            f"but Slate is pinned to {pin} exactly.",
                         "slate engine install --npm-ci (it installs exactly the lockfile's packages)", group=g,
                         details=details, install=engine_item))
    elif source == "repo":
        out.append(R.yellow("engine.remotion", f"Remotion {pin} runs from this checkout's engine/ folder, the "
                                               "development copy. Films should render with the installed copy in "
                                               "Slate's data folder, so updating the checkout can't change them.",
                            "slate engine install --npm-ci (or `slate doctor --install`)", group=g, details=details,
                            install=engine_item))
    else:
        where = "from SLATE_ENGINE_DIR" if source == "env" else "installed"
        out.append(R.green("engine.remotion", f"Remotion {pin} (pinned exactly) in {tilde(env, folder)} ({where})",
                           group=g, details=details))
    root = folder / "node_modules" / ".remotion" / "chrome-headless-shell"
    ver = (env.read_text(root / "VERSION") or "").strip() or None
    exe = None
    for plat in env.listdir(root):
        cand = root / plat / f"chrome-headless-shell-{plat}" / "chrome-headless-shell"
        if env.is_file(cand):
            exe = cand
    cdetails = {"path": str(exe) if exe else None, "version": ver, "expected_major": expected}
    if not exe or not ver:
        out.append(R.red("engine.chrome", "Remotion's headless Chrome isn't downloaded for this engine, so nothing can "
                                          "render.", "slate doctor --install (it asks first)", group=g,
                         details=cdetails, install=chrome_item))
    elif major(ver) != major(expected):
        out.append(R.red("engine.chrome", f"The engine's headless Chrome is {ver}; Remotion {pin} was tested with "
                                          f"{expected}.", "slate doctor --install fetches the right one", group=g,
                         details=cdetails, install=chrome_item))
    else:
        out.append(R.green("engine.chrome", f"chrome-headless-shell {ver} at {tilde(env, exe)} (Remotion's own, "
                                            "headless; never your browser)", group=g, details=cdetails))
    return out


# ---------------------------------------------------------------------------------------------------------------
# macOS's own tools, Xcode's simctl, the optional ones


def check_macos_tools(env: Env, ctx: Ctx) -> list[dict]:
    out = []
    for name, path, why in MACOS_TOOLS:
        if env.is_executable(path):
            out.append(R.green(f"tool.{name}", f"{name} at {path} ({why})", group=G))
        else:
            out.append(R.red(f"tool.{name}", f"{name} isn't at {path}, and {why}.",
                             "It's part of macOS itself: check this Mac's macOS install.", group=G))
    return out


def check_simctl(env: Env, ctx: Ctx) -> list[dict]:
    rid = "tool.simctl"
    sel = env.run(["/usr/bin/xcode-select", "-p"], timeout=10)
    if not sel.ok:
        return [R.yellow(rid, "Xcode isn't installed. Only iOS app captures need it (the Simulator); nothing else does.",
                         "Install Xcode from the App Store (free; you do this), then open it once.", group=G)]
    found = env.run(["/usr/bin/xcrun", "--find", "simctl"], timeout=15)
    if not found.ok:
        return [R.yellow(rid, f"Apple's developer tools are at {sel.stdout.strip()}, but they don't include the "
                              "Simulator's simctl (the command-line tools alone don't). Only iOS captures need it.",
                         "Install Xcode from the App Store, then select it: sudo xcode-select -s "
                         "/Applications/Xcode.app (you run it).", group=G)]
    return [R.green(rid, f"xcrun simctl at {found.stdout.strip()} (for iOS captures)", group=G,
                    details={"developer_dir": sel.stdout.strip(), "path": found.stdout.strip()})]


def check_optional_tools(env: Env, ctx: Ctx) -> list[dict]:
    out = []
    for tool, formula, why in OPTIONAL_TOOLS:
        path = env.which(tool)
        rid = f"tool.{tool}"
        if not path:
            out.append(R.yellow(rid, f"{tool} isn't installed. It's optional: only {why} uses it.",
                                f"brew install {formula}", group=G,
                                install=brew_item(env, formula, f"{tool} (optional, for onboarding)")))
            continue
        ran = env.run([path, "--version"], timeout=15)
        version = version_in(ran.text, r"(\d+(?:\.\d+)+)") if ran.ok else None
        out.append(R.green(rid, f"{tool} {version or ''} at {path} (optional)".replace("  ", " "), group=G,
                           details={"path": path, "version": version}))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Stale copies in /usr/local/bin


def _stale_copy(env: Env, tool: str, floor_from: str | None, version_args, pattern) -> dict | None:
    """What's wrong with /usr/local/bin/<tool>, or None when it's fine, missing, or the very copy Slate runs (then the
    tool's own row speaks for it)."""
    path = f"{STALE_DIR}/{tool}"
    if not env.is_executable(path):
        return None
    used = env.which(tool)
    if used and env.realpath(used) == env.realpath(path):
        return None
    resolved = env.realpath(path)
    cellar = re.search(r"/Cellar/([^/]+)/([^/]+)/", resolved)
    version = cellar.group(2) if cellar else None
    if version is None and version_args:
        # Not a Homebrew keg, so ask the binary itself (an Intel build runs under Rosetta; a failure leaves it unknown).
        ran = env.run([path, *version_args], timeout=15)
        version = version_in(ran.text, pattern) if ran.ok else None
    archs = env.archs(path)
    host = env.arch()
    reasons = []
    if archs and host not in archs:
        reasons.append("an Intel build" if archs == ["x86_64"] else f"built for {', '.join(archs)}")
    floor = _floor(env, floor_from, STALE_FLOORS[floor_from]) if floor_from else None
    if floor and (major(version) or 0) < floor and version:
        reasons.append(f"older than {floor}")
    if not reasons:
        return None
    return {"tool": tool, "path": path, "resolved": resolved, "version": version, "archs": archs, "reasons": reasons,
            "homebrew_formula": cellar.group(1) if cellar else None}


def check_stale_copies(env: Env, ctx: Ctx) -> list[dict]:
    """Old or Intel-only copies of Slate's tools in /usr/local/bin: named, with where they came from and how to go."""
    rid = "tool.stale-copies"
    found = [s for s in (_stale_copy(env, *spec) for spec in STALE_TOOLS) if s]
    dirs = [d for d in (env.getenv("PATH") or "").split(os.pathsep) if d]
    first_here = STALE_DIR in dirs and ("/opt/homebrew/bin" not in dirs
                                        or dirs.index(STALE_DIR) < dirs.index("/opt/homebrew/bin"))
    details = {"dir": STALE_DIR, "stale": found, "path_puts_usr_local_first": first_here}
    names = ", ".join(t for t, *_ in STALE_TOOLS)
    if not found:
        return [R.green(rid, f"Nothing stale in {STALE_DIR}: no old or Intel-only copy of {names} there to be found "
                             "first on a plain PATH.", group=G, details=details)]
    listed = "; ".join(f"{s['tool']} {s['version'] or 'of unknown version'} ({', '.join(s['reasons'])})"
                       for s in found)
    summary = (f"Stale copies in {STALE_DIR}: {listed}. Slate never runs them (it looks in /opt/homebrew/bin first), "
               "but anything started with macOS's default PATH, or a command typed where /usr/local/bin comes first, "
               "finds them instead of the current ones.")
    if first_here:
        summary += f" This PATH does put {STALE_DIR} first, so a plain `{found[0]['tool']}` here is the stale one."
    formulas = sorted({s["homebrew_formula"] for s in found if s["homebrew_formula"]})
    others = [s["path"] for s in found if not s["homebrew_formula"]]
    steps = []
    if formulas:
        old_brew = "/usr/local/bin/brew"
        brew_cmd = f"arch -x86_64 {old_brew}" if env.is_executable(old_brew) else "the Homebrew in /usr/local"
        steps.append(f"`{brew_cmd} uninstall {' '.join(formulas)}` removes the Intel Homebrew's copies")
    if others:
        pronoun = "it" if len(others) == 1 else "them"
        steps.append(f"{', '.join(others)} came from an installer, not Homebrew: delete {pronoun} "
                     "(for Node, also npm, npx and /usr/local/lib/node_modules)")
    fix = ("; ".join(steps) + ". Or keep them and put /opt/homebrew/bin first on PATH (`eval \"$(/opt/homebrew/bin/brew "
           "shellenv)\"` in ~/.zprofile). It's your call: Slate never removes them.")
    return [R.yellow(rid, summary, fix, group=G, details=details)]


# ---------------------------------------------------------------------------------------------------------------
# The higgsfield CLI and Aside


def check_higgsfield_cli(env: Env, ctx: Ctx) -> list[dict]:
    pin = str(env.registry_tool("higgsfield-cli").get("version") or "1.1.26")
    item = npm_global_item(env, "@higgsfield/cli", pin, f"the higgsfield CLI {pin} (sign-in and account status)")
    path = env.which("higgsfield")
    if not path:
        return [R.red("tool.higgsfield", "The higgsfield CLI isn't installed. Connecting Higgsfield signs in through it "
                                         "(you run the sign-in yourself).",
                      f"npm install --global @higgsfield/cli@{pin} (or `slate doctor --install`)", group=G,
                      install=item)]
    version = _npm_version(env, path, "@higgsfield/cli")
    details = {"path": path, "version": version, "pinned": pin}
    if version and version != pin:
        first = R.yellow("tool.higgsfield", f"higgsfield CLI {version} at {path}; Slate is verified with {pin}.",
                         f"npm install --global @higgsfield/cli@{pin}", group=G, details=details, install=item)
    else:
        first = R.green("tool.higgsfield", f"higgsfield CLI {version or '(version unreadable)'} at {path}", group=G,
                        details=details)
    return [first, _higgsfield_account(env, ctx, path)]


def _higgsfield_account(env: Env, ctx: Ctx, path: str) -> dict:
    """`higgsfield account status`: only when a sign-in is on file, never in replay mode, and with nothing able to
    open a browser (BROWSER=none plus a stand-in `open`). The account's email is never read into the report."""
    from slate.secrets import redact  # noqa: PLC0415

    rid = "tool.higgsfield-account"
    login = (f"Run `higgsfield auth login` in your own Terminal: it opens {ctx.browser_name} so you can sign in. "
             "Slate never runs it for you.")
    why = ("Slate generates through the Cloud API key, so this sign-in only matters for Higgsfield's own skills and "
           "account checks.")
    creds = env.home / ".config" / "higgsfield" / "credentials.json"
    if not env.is_file(creds):
        return R.yellow(rid, f"The higgsfield CLI isn't signed in. {why}", login, group=G)
    if ctx.replay:
        return R.green(rid, "The higgsfield CLI has a sign-in on file. Replay mode doesn't ask Higgsfield about it; a "
                            "live run does (`higgsfield account status`).", group=G, details={"replay": True})
    ran = env.run([path, "account", "status", "--json"], timeout=30, no_open=True)
    if not ran.ok:
        text = ran.text.lower()
        if any(w in text for w in ("login", "log in", "sign in", "unauthor", "expired", "401", "token")):
            return R.yellow(rid, f"The higgsfield CLI's sign-in has expired or was refused. {why}", login, group=G)
        return R.yellow(rid, f"`higgsfield account status` didn't answer: {first_line(redact(ran.text))}",
                        "Check the network, then run `slate doctor` again.", group=G)
    data: dict = {}
    start = ran.stdout.find("{")
    if start >= 0:
        try:
            parsed = json.loads(ran.stdout[start:])
            data = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            data = {}
    plan = data.get("subscription_plan_type") or data.get("plan")
    credits = data.get("credits")
    bits = []
    if plan:
        bits.append(f"{plan} plan")
    if isinstance(credits, (int, float)):
        bits.append(f"{credits:g} app credits")
    detail = f" ({', '.join(bits)})" if bits else ""
    return R.green(rid, f"The higgsfield CLI is signed in{detail}. App credits are a separate pool: Slate's generations "
                        "spend the Cloud API balance instead.", group=G,
                   details={"plan": plan, "app_credits": credits if isinstance(credits, (int, float)) else None})


def check_aside(env: Env, ctx: Ctx) -> list[dict]:
    rid = "tool.aside"
    path = env.which("aside")
    if not path:
        return [R.red(rid, "Aside isn't installed. It's the only browser Slate uses: web captures and web research go "
                           "through it, and nothing else is ever opened.",
                      "Install Aside and its command-line tool (~/.local/bin/aside). Slate never falls back to another "
                      "browser.", group=G)]
    resolved = Path(env.realpath(path))
    version = None
    for parent in resolved.parents:
        if parent.name.endswith(".app"):
            info = env.read_plist(parent / "Contents" / "Info.plist") or {}
            version = info.get("CFBundleShortVersionString")
            break
    return [R.green(rid, f"Aside {version or ''} at {tilde(env, path)}: the only browser Slate drives (doctor doesn't "
                         "start it)".replace("Aside  at", "Aside at"), group=G,
                    details={"path": path, "resolved": str(resolved), "version": version})]
