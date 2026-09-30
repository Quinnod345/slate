"""Small pieces every doctor check shares: the run context, version parsing, install items, the default browser."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from slate.doctor.env import Env

ARM_BREW = "/opt/homebrew/bin/brew"

# Bundle ids of the browsers a default could be, for a readable name. Anything else is shown by its bundle id.
BROWSER_NAMES = {
    "company.thebrowser.dia": "Dia",
    "company.thebrowser.browser": "Arc",
    "com.apple.safari": "Safari",
    "com.google.chrome": "Google Chrome",
    "com.google.chrome.canary": "Google Chrome Canary",
    "org.mozilla.firefox": "Firefox",
    "com.microsoft.edgemac": "Microsoft Edge",
    "com.brave.browser": "Brave",
    "com.operasoftware.opera": "Opera",
    "com.vivaldi.vivaldi": "Vivaldi",
}


@dataclass
class Ctx:
    """One doctor run: who's asking, from where, and in which mode."""

    context: str  # the label this run reports under (--context NAME, else the detected agent, else "terminal")
    agent: str | None = None  # the agent running us (slate.procs.agent_context), None for a person at a Terminal
    brand: str | None = None
    replay: bool = False
    host_app: str = "this Terminal"
    browser: dict = field(default_factory=dict)  # {"bundle_id", "name"}

    @property
    def browser_name(self) -> str:
        return self.browser.get("name") or "your default browser"


def tilde(env: Env, path: str | Path | None) -> str:
    """A path with the home folder shown as ~ (shorter, and the same on every Mac)."""
    if path is None:
        return "?"
    text = str(path)
    home = str(env.home)
    return "~" + text[len(home):] if text == home or text.startswith(home + "/") else text


def version_in(text: str | None, pattern: str = r"(\d+(?:\.\d+)+)") -> str | None:
    """The first version-looking number in a tool's output (9.0.2, 26.8.1, 0.9.28)."""
    if not text:
        return None
    m = re.search(pattern, text)
    return m.group(1) if m else None


def major(version: str | None) -> int | None:
    if not version:
        return None
    m = re.match(r"v?(\d+)", version.strip())
    return int(m.group(1)) if m else None


def first_line(text: str | None, limit: int = 160) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def brew(env: Env) -> str | None:
    """Homebrew for Apple silicon (/opt/homebrew) first: Slate's tools should be arm64 builds."""
    return ARM_BREW if env.is_executable(ARM_BREW) else env.which("brew")


def brew_item(env: Env, formula: str, what: str, *, upgrade: bool = False, size: str | None = None) -> dict:
    """An install item for `slate doctor --install`: one Homebrew formula."""
    exe = brew(env)
    item = {"id": f"brew:{formula}", "what": what, "argv": [exe or "brew", "upgrade" if upgrade else "install", formula],
            "kind": "brew"}
    if size:
        item["size"] = size
    if not exe:
        item["needs"] = "Homebrew isn't installed. Install it from https://brew.sh first (you run its installer)."
    elif exe != ARM_BREW:
        item["note"] = f"{exe} isn't the Apple-silicon Homebrew in /opt/homebrew, so this may install an Intel build."
    return item


def npm_global_item(env: Env, package: str, version: str, what: str, *, size: str | None = None) -> dict:
    npm = env.which("npm")
    item = {"id": f"npm:{package}", "what": what, "argv": [npm or "npm", "install", "--global", f"{package}@{version}"],
            "kind": "npm"}
    if size:
        item["size"] = size
    if not npm:
        item["needs"] = "npm isn't installed (it comes with Node 24 or newer)."
    return item


def call_item(item_id: str, what: str, shows: str, *, size: str | None = None) -> dict:
    """An install item that Slate carries out itself (an engine install, a model download)."""
    item = {"id": item_id, "what": what, "call": item_id, "shows": shows, "kind": "slate"}
    if size:
        item["size"] = size
    return item


def command_text(item: dict) -> str:
    """How an install item reads as one command line."""
    if item.get("argv"):
        return shlex.join(str(a) for a in item["argv"])
    return str(item.get("shows") or item.get("id"))


def default_browser(env: Env) -> dict:
    """The browser macOS opens web links in, from LaunchServices (read-only). Safari when nothing is set."""
    data = env.launchservices() or {}
    bundle = None
    for handler in data.get("LSHandlers") or []:
        if not isinstance(handler, dict):
            continue
        if handler.get("LSHandlerURLScheme") in ("https", "http") and handler.get("LSHandlerRoleAll"):
            bundle = str(handler["LSHandlerRoleAll"])
            if handler.get("LSHandlerURLScheme") == "https":
                break
    source = "LaunchServices" if bundle else "macOS default (no handler set)"
    if not bundle:
        bundle = "com.apple.safari"
    name = BROWSER_NAMES.get(bundle.lower(), bundle)
    return {"bundle_id": bundle, "name": name, "source": source}


def host_app(env: Env, agent: str | None) -> str:
    """The app macOS asks about when this process sends Apple Events: what a person sees in Automation settings."""
    bundle = env.getenv("__CFBundleIdentifier") or ""
    term = env.getenv("TERM_PROGRAM") or ""
    apps = {"com.apple.terminal": "Terminal", "com.googlecode.iterm2": "iTerm", "com.anthropic.claudefordesktop":
            "Claude (the desktop app)", "com.openai.chat": "ChatGPT", "com.openai.codex": "Codex",
            "com.microsoft.vscode": "VS Code", "dev.warp.warp-stable": "Warp", "com.mitchellh.ghostty": "Ghostty"}
    terms = {"apple_terminal": "Terminal", "iterm.app": "iTerm", "vscode": "VS Code", "warpterminal": "Warp",
             "ghostty": "Ghostty"}
    app = apps.get(bundle.lower()) or terms.get(term.lower())
    agents = {"claude-code": "Claude Code", "codex": "Codex", "openclaw": "the OpenClaw gateway"}
    who = agents.get(agent or "", agent)
    if who and app:
        return f"{who} in {app}"
    if who:
        return who
    return app or "this Terminal"
