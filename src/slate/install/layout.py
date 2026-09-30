"""Where an install goes, and whether it's the real thing.

Every agent reads skills from its own folder, so one checkout reaches all of them through symlinks:

- Claude Code reads `~/.claude/skills` (it doesn't read `~/.agents/skills`);
- Codex (both binaries) and the OpenClaw gateway read `~/.agents/skills`;
- `~/.codex/skills` is never used: both Codex binaries read it as well, so a skill there and in `~/.agents/skills` is
  listed twice.

The CLI goes into `~/.local/bin` and `/opt/homebrew/bin`, because the OpenClaw gateway's PATH lacks `~/.local/bin`.

**Sandbox homes.** A home that isn't this account's real home (a temp folder in a test, `--home`, or `HOME=` set by
the fresh-home test) is a sandbox. A sandbox install writes only inside that folder: the Homebrew link goes to
`<home>/opt/homebrew/bin`, and anything that would reach this Mac's real session (registering MCP servers with the
agents' own tools, loading the launchd agent, installing Homebrew tools) is shown, never done. `launchctl` and the
agents' CLIs act on the logged-in account whatever `HOME` says, which is why this is decided here, by construction,
rather than left to flags.
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from slate import paths
from slate.errors import UsageError

AGENTS = ("claude", "codex", "openclaw")
# The folder (under the home) each agent reads skills from. Codex and OpenClaw share one.
SKILL_ROOTS = {"claude": ".claude/skills", "codex": ".agents/skills", "openclaw": ".agents/skills"}
# Where each agent keeps its own settings; its presence means the agent has been used here.
CONFIG_DIRS = {"claude": ".claude", "codex": ".codex", "openclaw": ".openclaw"}
NEVER_SKILL_ROOT = ".codex/skills"
HOMEBREW_BIN = "opt/homebrew/bin"
LOCAL_BIN = ".local/bin"
CLI_NAME = "slate"


def account_home() -> Path:
    """This account's real home, from the user database. `HOME` can point anywhere; this can't."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


@dataclass(frozen=True)
class Layout:
    """One install's paths. Build it with `resolve()`."""

    home: Path
    sandbox: bool
    root: Path  # "/" for the real home; the sandbox home otherwise (system folders are mirrored inside it)
    repo: Path  # the Slate checkout every link points into
    data: Path  # Slate's data folder for this home (manifest, backups)

    @property
    def skills_src(self) -> Path:
        return self.repo / "skills"

    def skill_root(self, agent: str) -> Path:
        if agent not in SKILL_ROOTS:
            raise UsageError(f"Slate doesn't know an agent called {agent!r}. Known: {', '.join(AGENTS)}.")
        return self.home / SKILL_ROOTS[agent]

    @property
    def claude_skills(self) -> Path:
        return self.home / SKILL_ROOTS["claude"]

    @property
    def agents_skills(self) -> Path:
        return self.home / SKILL_ROOTS["codex"]

    @property
    def codex_skills(self) -> Path:
        """Read by both Codex binaries. Slate never links here; it only looks, to spot skills listed twice."""
        return self.home / NEVER_SKILL_ROOT

    @property
    def local_bin(self) -> Path:
        return self.home / LOCAL_BIN

    @property
    def homebrew_bin(self) -> Path:
        return self.root / HOMEBREW_BIN

    @property
    def shim(self) -> Path:
        """The one CLI path every MCP registration uses."""
        return self.local_bin / CLI_NAME

    @property
    def venv_python(self) -> Path:
        return self.repo / ".venv" / "bin" / "python"

    @property
    def install_dir(self) -> Path:
        return self.data / "install"

    @property
    def manifest_file(self) -> Path:
        return self.install_dir / "manifest.json"

    @property
    def backups_dir(self) -> Path:
        # Outside every skill root on purpose: OpenClaw finds a SKILL.md up to six levels down, so a backup kept inside
        # a skill folder would come back as a second copy of the skill.
        return self.data / "backups"

    @property
    def launch_agents(self) -> Path:
        return self.home / "Library" / "LaunchAgents"

    def config_dir(self, agent: str) -> Path:
        return self.home / CONFIG_DIRS[agent]

    def label(self, folder: Path) -> str:
        """A short name for a folder in messages: `~/.claude/skills` rather than the full path."""
        try:
            return "~/" + str(Path(folder).relative_to(self.home))
        except ValueError:
            return str(folder)

    def to_dict(self) -> dict:
        return {"home": str(self.home), "sandbox": self.sandbox, "root": str(self.root), "checkout": str(self.repo),
                "data": str(self.data), "shim": str(self.shim)}


def resolve(home: str | os.PathLike | None = None, *, repo: str | os.PathLike | None = None,
            root: str | os.PathLike | None = None) -> Layout:
    """The layout for installing into `home` (default: the current HOME) from `repo` (default: this checkout).

    A home other than this account's real one is a sandbox, whatever `HOME` says (see the module docstring).
    """
    home_path = Path(home).expanduser() if home else Path.home()
    home_path = Path(os.path.abspath(home_path))
    sandbox = not _same(home_path, account_home())
    if root is None:
        root_path = home_path if sandbox else Path("/")
    else:
        root_path = Path(os.path.abspath(Path(root).expanduser()))
        if sandbox and _same(root_path, Path("/")):
            raise UsageError(
                f"{home_path} isn't this account's home, so the install stays inside it: it never writes to this "
                "Mac's /opt/homebrew/bin.",
                hint="Leave out the root, or pass one inside the sandbox home.",
            )
    repo_path = Path(os.path.abspath(Path(repo).expanduser())) if repo else paths.REPO
    # The real home keeps Slate's usual data folder (SLATE_DATA still wins); a sandbox keeps everything inside itself.
    data = paths.data() if not sandbox else home_path / "Library" / "Application Support" / "Slate"
    return Layout(home=home_path, sandbox=sandbox, root=root_path, repo=repo_path, data=data)


def parse_agents(value: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    """`claude,codex` -> ['claude', 'codex']. None stays None (meaning: detect them)."""
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else list(value)
    out: list[str] = []
    for raw in items:
        name = raw.strip().lower()
        if not name:
            continue
        aliases = {"claude-code": "claude", "claude_code": "claude", "oneiro": "openclaw"}
        name = aliases.get(name, name)
        if name not in AGENTS:
            raise UsageError(f"Slate doesn't know an agent called {raw.strip()!r}. Pick from: {', '.join(AGENTS)}.",
                             hint="slate install --agents claude,codex,openclaw")
        if name not in out:
            out.append(name)
    if not out:
        raise UsageError("--agents was empty. Name at least one of: " + ", ".join(AGENTS) + ".")
    return out
