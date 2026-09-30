"""Checks for Slate's ten Agent Skills and docs/METHOD.md, shared by the tests in this folder.

The skills are the one piece of Slate every agent reads before it runs anything, so they're held to the authoring
rules in docs/PLAN.md §1: spec-only frontmatter, SKILL.md under 500 lines, real reference files, relative links that
resolve, agent-neutral wording, no brand material, and only the commands docs/ARCHITECTURE.md §4 promises.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO / "skills"
METHOD = REPO / "docs" / "METHOD.md"
ARCHITECTURE = REPO / "docs" / "ARCHITECTURE.md"

SKILLS = ("slate", "slate-story", "slate-motion", "slate-build", "slate-voice", "slate-sound", "slate-generate",
          "slate-finish", "slate-qa", "slate-brand")

# The fields the open Agent Skills specification defines for SKILL.md frontmatter (agentskills.io).
SPEC_FIELDS = frozenset({"name", "description", "license", "compatibility", "metadata", "allowed-tools"})
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_SKILL_LINES = 500

# Tool names only one agent has. Skills are read by Claude Code, Codex and OpenClaw alike, so they say what to do
# ("if you can run sub-agents…"), never which proprietary tool does it.
CLAUDE_ONLY = ("Task tool", "TodoWrite", "TaskCreate", "TaskUpdate", "AskUserQuestion", "Workflow tool",
               "subagent_type", "mcp__", "Skill tool", "SlashCommand", "ExitPlanMode", "NotebookEdit", "WebFetch",
               "WebSearch", "run_in_background", "Bash tool", "Read tool", "Edit tool", "Write tool", "Grep tool",
               "Glob tool", "CLAUDE.md", "ultrathink")

# Company and brand material that must never enter the core (docs/ARCHITECTURE.md §1 rules 1-2): the company, product,
# character, partner and brand names, sample people and companies, the licensed font, a font-kit id, brand colours,
# a meeting name, and a personal account name. The guard can't list them in plain text without putting them in the
# repo itself, so it keeps SHA-256 prefixes (the first 16 hex digits) and hashes every word and word pair it reads:
# lower-cased for names and colours, exactly as written for vendor voice and brand-kit ids.
GUARDED_LOWER = frozenset({
    "048db8c6cc8acd66", "129eaf4b1219beda", "16149626b0365e04", "1ed8c4f3b421977d", "2ad60e2919a3ae8f",
    "30c07346a4e77ab1", "327d1a439db7fba7", "332ae32df0414eff", "48e1a02417ba183f", "55dba21b14c7d258",
    "5e7963f61d92d557", "61acc4cab1bfb5d3", "711ba890de7ac444", "961ce64fded2c98d", "a30a9e529370f662",
    "ae095d277769201d", "bdd6bed80a39febc", "db4ea2e2c2875a50", "e252b79c39aa92dd", "f8d73da6e93bc010",
    "fd73d009d4b851d4", "ff9b5c834ecab632",
})
GUARDED_EXACT = frozenset({"3ca5597926129d4a", "4e5e297cd9dba5cb", "6fb6c18285512ed7", "a75905cb33c2a6b5",
                           "e4a4d373c5637b77"})
# Things no skill needs at all, guarded by shape rather than by name.
SHAPE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("a hex colour (brand colours are pack data)", re.compile(r"#[0-9A-Fa-f]{6}\b")),
    ("a personal path", re.compile(r"/Users/")),
)


def _prefix(term: str) -> str:
    return hashlib.sha256(term.encode("utf-8")).hexdigest()[:16]


def brand_hits(text: str) -> list[tuple[int, str]]:
    """(line number, the words as written) for every guarded term or shape in the text."""
    hits: list[tuple[int, str]] = []
    for n, line in enumerate(text.split("\n"), 1):
        tokens = re.findall(r"[A-Za-z0-9]+", line)
        lower = [t.lower() for t in tokens]
        for i, tok in enumerate(tokens):
            if _prefix(lower[i]) in GUARDED_LOWER or _prefix(tok) in GUARDED_EXACT:
                hits.append((n, tok))
            if i + 1 < len(tokens) and _prefix(f"{lower[i]} {lower[i + 1]}") in GUARDED_LOWER:
                hits.append((n, f"{tokens[i]} {tokens[i + 1]}"))
        for label, pattern in SHAPE_PATTERNS:
            for m in pattern.finditer(line):
                hits.append((n, f"{m.group(0)} ({label})"))
    return hits


# ------------------------------------------------------------------------------------------------ files and text

def skill_dir(name: str) -> Path:
    return SKILLS_DIR / name


def skill_md(name: str) -> Path:
    return skill_dir(name) / "SKILL.md"


def reference_files(name: str) -> list[Path]:
    folder = skill_dir(name) / "references"
    return sorted(folder.glob("*.md")) if folder.is_dir() else []


def all_docs() -> list[Path]:
    """Every Markdown file the skills ship, plus docs/METHOD.md."""
    out: list[Path] = []
    for name in SKILLS:
        if skill_md(name).exists():
            out.append(skill_md(name))
        out.extend(reference_files(name))
    if METHOD.exists():
        out.append(METHOD)
    return out


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def strip_code(text: str) -> str:
    """The text with fenced code blocks and inline code removed (for link and prose checks)."""
    text = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


# ------------------------------------------------------------------------------------------------ frontmatter

@dataclass
class Frontmatter:
    fields: dict[str, str]
    problems: list[str]
    body_start: int  # the line after the closing ---


def _plain_scalar_problem(value: str) -> str | None:
    """Why a value isn't a safe YAML plain scalar on one line (None when it is)."""
    if not value:
        return "is empty"
    if value[0] in "-?:,[]{}#&*!|>'\"%@`":
        return f"starts with {value[0]!r}, which YAML reads as syntax"
    if ": " in value or value.endswith(":"):
        return "contains ': ', which YAML reads as a mapping"
    if " #" in value:
        return "contains ' #', which YAML reads as a comment"
    return None


def parse_frontmatter(text: str) -> Frontmatter:
    """Strictly parse a SKILL.md's frontmatter: `---`, one `key: value` per line, `---`.

    Values are single-line plain scalars or double-quoted strings, so every YAML parser in every agent reads the same
    thing. Anything fancier (block scalars, nested maps) is reported as a problem rather than guessed at.
    """
    lines = text.split("\n")
    problems: list[str] = []
    if not lines or lines[0].strip() != "---":
        return Frontmatter({}, ["the file doesn't start with a --- frontmatter block"], 0)
    fields: dict[str, str] = {}
    end = None
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            end = i
            break
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$", line)
        if not m:
            problems.append(f"frontmatter line {i + 1} isn't `key: value`: {line!r}")
            continue
        key, value = m.group(1), (m.group(2) or "").strip()
        if key in fields:
            problems.append(f"frontmatter repeats {key!r}")
        if value.startswith('"'):
            if not (len(value) >= 2 and value.endswith('"')):
                problems.append(f"{key}: an unterminated double-quoted value")
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        else:
            why = _plain_scalar_problem(value)
            if why:
                problems.append(f"{key}: the value {why}")
        fields[key] = value
    if end is None:
        problems.append("the frontmatter block never closes with ---")
        end = len(lines) - 1
    return Frontmatter(fields, problems, end + 1)


# ------------------------------------------------------------------------------------------------ links

LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def links(text: str) -> list[tuple[int, str]]:
    """(line number, target) for every inline Markdown link outside code."""
    out = []
    for n, line in enumerate(strip_code(text).split("\n"), 1):
        for m in LINK_RE.finditer(line):
            out.append((n, m.group(2)))
    return out


def anchor(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to dashes."""
    h = heading.strip().lower()
    h = re.sub(r"[`*_~]", "", h)
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(text: str) -> set[str]:
    out: set[str] = set()
    seen: dict[str, int] = {}
    for line in strip_code(text).split("\n"):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        a = anchor(m.group(1))
        if a in seen:
            seen[a] += 1
            a = f"{a}-{seen[a]}"
        else:
            seen[a] = 0
        out.add(a)
    return out


def is_external(target: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*:", target))


# ------------------------------------------------------------------------------------------------ the CLI contract

def section4_rows() -> list[str]:
    """The Command cells of docs/ARCHITECTURE.md §4's table, with escaped pipes restored."""
    text = read(ARCHITECTURE)
    m = re.search(r"^## 4\..*?$(.*?)^## 5\.", text, flags=re.S | re.M)
    if not m:
        raise AssertionError("docs/ARCHITECTURE.md has no §4 (the CLI surface)")
    rows = []
    for line in m.group(1).split("\n"):
        if not line.startswith("| `slate"):
            continue
        cells = re.split(r"(?<!\\)\|", line)
        rows.append(cells[1].replace("\\|", "|").strip())
    return rows


BARE = re.compile(r"^[a-z][a-z0-9-]*$")
ENUM = re.compile(r"^[a-z][a-z0-9-]*(?:\|[a-z][a-z0-9-]*)*$")


def section4_contract() -> dict[str, frozenset[str] | None]:
    """{command: the stages §4 names for it, or None when §4 leaves the next word open}.

    `slate voice cast|design|…` names its stages, so only those are promised; `slate qa [gate ...]`,
    `slate gen ...` or a bare `slate ledger` leave the next word open.
    """
    contract: dict[str, frozenset[str] | None] = {}
    for cell in section4_rows():
        for form in re.findall(r"`([^`]+)`", cell):
            tokens = form.split()
            if len(tokens) < 2 or tokens[0] != "slate":
                continue
            cmd = tokens[1]
            nxt = tokens[2] if len(tokens) > 2 else None
            allowed: frozenset[str] | None
            allowed = frozenset(nxt.split("|")) if nxt and ENUM.match(nxt) else None
            if cmd in contract and contract[cmd] is None:
                continue
            if allowed is None:
                contract[cmd] = None
            else:
                contract[cmd] = (contract.get(cmd) or frozenset()) | allowed
    return contract


@dataclass(frozen=True)
class Citation:
    path: str
    line: int
    text: str
    command: str
    stage: str | None


GLOBAL_VALUE_FLAGS = {"--film"}


def _citation(path: str, n: int, text: str) -> Citation | None:
    tokens = text.strip().split()
    if len(tokens) < 2 or tokens[0] != "slate":
        return None
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in GLOBAL_VALUE_FLAGS else 1
    if i >= len(tokens):
        return None  # `slate --help`, `slate --version`
    cmd = tokens[i]
    if not BARE.match(cmd):
        return None  # a placeholder such as `slate <command> --help`
    stage = tokens[i + 1] if i + 1 < len(tokens) and BARE.match(tokens[i + 1]) else None
    return Citation(path, n, text.strip(), cmd, stage)


def citations(path: Path) -> list[Citation]:
    """Every `slate …` command a file cites: inline code, and command lines in fenced blocks."""
    text = read(path)
    out: list[Citation] = []
    in_fence = False
    for n, line in enumerate(text.split("\n"), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            body = re.sub(r"^\s*\$\s+", "", line).split("#")[0]
            for part in re.split(r"&&|;|\|\|", body):
                c = _citation(rel(path), n, part)
                if c:
                    out.append(c)
            continue
        for m in re.finditer(r"`([^`\n]+)`", line):
            c = _citation(rel(path), n, m.group(1))
            if c:
                out.append(c)
    return out


def contract_problems(cites: list[Citation], contract: dict[str, frozenset[str] | None]) -> list[str]:
    problems = []
    for c in cites:
        if c.command not in contract:
            problems.append(f"{c.path}:{c.line}: `{c.text}` cites `slate {c.command}`, which isn't in §4")
            continue
        allowed = contract[c.command]
        if allowed is not None and c.stage is not None and c.stage not in allowed:
            problems.append(f"{c.path}:{c.line}: `{c.text}`: §4 promises `slate {c.command} "
                            f"{'|'.join(sorted(allowed))}`, not `{c.stage}`")
    return problems
