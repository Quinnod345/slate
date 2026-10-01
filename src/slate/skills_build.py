"""`slate skills build`: one skills folder, every client's copy generated from it.

Slate's skills live once, in `skills/<name>/` (SKILL.md plus references/), in the open Agent Skills format that Claude
Code, Codex and OpenClaw all read. Each client wants a little more than the shared files, and those extras are
generated here, never written by hand:

- `plugins/slate/skills/<name>/`: the plugin's own copy of every skill. A Claude Code plugin can't point outside its
  folder (a symlink that leaves it is refused), so the plugin carries real copies. Each copy gains a Codex sidecar,
  `agents/openai.yaml` (display name, short description, implicit invocation), unless the skill ships its own.
- `plugins/slate/claude-ai/<name>.zip`: the knowledge-only edition for regular Claude chats, uploaded to claude.ai.
  Its SKILL.md has every reference file inlined, because a chat has no Slate command line to open them with.
- `plugins/slate/openclaw/entries.json`: the `skills.entries` and `mcp.servers` block to merge into OpenClaw's config.
- The plugin's own files: its two manifests (`.claude-plugin/plugin.json` for Claude Code, `plugin.json` for Agent
  Plugins), its two MCP configs (`.mcp.json`, `mcp.json`: Slate's own server plus the vendors' hosted connectors,
  signed in with OAuth, never a key), its README and its LICENSE. So nothing in plugins/slate is ever edited by hand.

`slate skills build --check` rebuilds everything in memory and compares it with what's on disk; CI runs it, so a
skill edited without a rebuild fails the build instead of shipping a stale copy. The check then runs the clients' own
validators on the built plugin where they're installed (`claude plugin validate --strict`, `skills-ref validate` on
every skill), each skipped with a notice where it isn't, so CI runs them wherever it has them.

The same pass enforces the authoring rules every client depends on: spec-only frontmatter (claude.ai rejects any other
key), a name that matches its folder, a description under 1,024 characters (and with any when_to_use, under Claude
Code's 1,536-character listing cut), the whole skill list inside Codex's budget (2% of its context, 8,000 characters),
SKILL.md under 500 lines, working links to reference files, no media in a skill (fonts, sound banks, LUTs, audio,
video), the plugin under claude.ai's caps (200 MB, 5,000 files), and no wording that only one agent understands
(Claude-only tool names, client path placeholders).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import textwrap
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from slate import cli, jsonio, log, paths
from slate.errors import MissingPrerequisite, Refused, UsageError

GENERATED = "do not edit by hand: slate skills build"
FRONT_DOOR = "slate"  # the skill that claims "make a video"; it gets Codex's default prompt

# The Agent Skills spec's frontmatter keys. claude.ai's upload refuses any other key with a hard error, so a skill
# that uses one fails the build rather than the upload.
ALLOWED_KEYS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_SKILL_LINES = 500
MAX_SHORT_DESCRIPTION = 120
# Claude Code shows each skill's description plus its when_to_use, cut at this many characters.
MAX_LISTING = 1536
# Codex puts every skill's name, description and path in its context, within 2% of the context and at least this many
# characters; a list past it loses skills. The build fails past it and notes when the list is within a tenth of it.
CODEX_LIST_BUDGET = 8000
CODEX_LIST_NOTE = 0.9
# claude.ai's caps for one plugin (and its largest upload). Files outside the plugin folder are never copied, and Git
# LFS files arrive as pointers, so media is fetched at run time instead of shipped.
PLUGIN_MAX_BYTES = 200 * 1024 * 1024
PLUGIN_MAX_FILES = 5000
# Media never ships in a skill: brand fonts are licensed per brand and referenced by name, sound banks and sampled
# instruments come from each machine's own install, and LUTs, audio and footage live with the films.
MEDIA = {**dict.fromkeys((".ttf", ".otf", ".ttc", ".woff", ".woff2", ".eot", ".dfont"), "a font"),
         **dict.fromkeys((".exs", ".sf2", ".sfz", ".nki", ".nkx", ".nkc"), "a sound bank"),
         **dict.fromkeys((".cube", ".3dl", ".csp", ".lut", ".look", ".icc"), "a LUT or colour profile"),
         **dict.fromkeys((".wav", ".aif", ".aiff", ".caf", ".mp3", ".m4a", ".flac", ".ogg", ".opus"), "audio"),
         **dict.fromkeys((".mov", ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mxf", ".prores"), "video")}

# The folders the build owns inside its output folder. Anything in them that the build didn't make is stale.
MANAGED = ("skills", "claude-ai", "openclaw")
# The plugin's own files, generated too. Anything else at the plugin's top level (a stray file, a `bin/` that claude.ai
# would refuse) is stale as well.
ROOT_FILES = ("README.md", "LICENSE", ".mcp.json", "mcp.json", "plugin.json", ".claude-plugin/plugin.json")

# Wording that only one client understands. Skills are read by Claude Code, Codex and OpenClaw alike (plan §1:
# "agent-neutral wording, with no Claude-only tool names"), so these fail the build.
NOT_PORTABLE: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\$\{CLAUDE_[A-Z_]+\}"), "a Claude Code path variable"),
    (re.compile(r"\{baseDir\}"), "an OpenClaw path placeholder"),
    (re.compile(r"\$\{PLUGIN_(?:ROOT|DATA)\}"), "an Agent Plugins path placeholder"),
    (re.compile(r"\b(?:TodoWrite|AskUserQuestion|ExitPlanMode|NotebookEdit|SlashCommand|WebFetch|WebSearch)\b"),
     "a Claude-only tool name"),
    (re.compile(r"\b(?:Task|Skill|Bash) tool\b"), "a Claude-only tool name"),
    (re.compile(r"\bmcp__[A-Za-z0-9_-]+"), "one client's MCP tool name"),
)

_LINK_RE = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")
_HEADING_RE = re.compile(r"^(#{1,6})(\s+.*)$")
_SKIP_NAMES = {".DS_Store", "__pycache__", "Thumbs.db"}
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class FrontmatterError(ValueError):
    """SKILL.md's frontmatter isn't something every client can read."""


# ---------------------------------------------------------------------------------------------------------------
# Frontmatter: a strict YAML subset (key: value, block scalars, quoted scalars, one-level maps)


def _unquote_double(text: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FrontmatterError(f"can't read the double-quoted value {text[:60]!r}: use JSON-style escapes") from exc
    if not isinstance(value, str):
        raise FrontmatterError(f"{text[:60]!r} isn't a string")
    return value


def _scalar(raw: str, key: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        return _unquote_double(raw)
    if raw.startswith("'"):
        if not raw.endswith("'") or len(raw) < 2:
            raise FrontmatterError(f"{key}: the single-quoted value isn't closed")
        return raw[1:-1].replace("''", "'")
    if raw.startswith(("{", "[", "&", "*", "!", "|", ">", "@", "`")):
        raise FrontmatterError(f"{key}: flow collections, anchors and tags aren't portable frontmatter; "
                               "write a plain or quoted string")
    # A plain scalar ends at " #" (a comment).
    cut = raw.find(" #")
    return (raw[:cut] if cut >= 0 else raw).strip()


def _quote_closed(raw: str) -> bool:
    raw = raw.strip()
    if raw.startswith('"'):
        escaped = False
        for ch in raw[1:]:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                return True
        return False
    if raw.startswith("'"):
        body = raw[1:].replace("''", "")
        return "'" in body
    return True


def _block(lines: list[str], start: int, indicator: str, key: str) -> tuple[str, int]:
    """Read a `|` or `>` block scalar that starts after line `start`. Returns (text, next line index)."""
    style, chomp = indicator[0], indicator[1:2]
    if indicator[1:] not in ("", "-", "+"):
        raise FrontmatterError(f"{key}: block scalar indicator {indicator!r} isn't supported (use |, |-, > or >-)")
    body: list[str] = []
    i = start + 1
    indent = None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            body.append("")
            i += 1
            continue
        lead = len(line) - len(line.lstrip(" "))
        if lead == 0:
            break
        if indent is None:
            indent = lead
        if lead < indent:
            break
        body.append(line[indent:])
        i += 1
    while body and body[-1] == "" and chomp != "+":
        body.pop()
    if style == "|":
        text = "\n".join(body)
    else:  # folded: single newlines become spaces, blank lines stay as newlines
        parts: list[str] = []
        for line in body:
            if line == "":
                parts.append("\n")
            elif parts and not parts[-1].endswith("\n"):
                parts.append(" " + line)
            else:
                parts.append(line)
        text = "".join(parts)
    if chomp != "-":
        text += "\n"
    return text, i


def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """Split SKILL.md into (frontmatter as a dict, the raw frontmatter text, the Markdown body).

    Only the portable subset of YAML is accepted: `key: value` lines, quoted and plain scalars (continued on indented
    lines), `|` and `>` block scalars, and one level of `key: value` under a key (for `metadata`). Anything else is a
    FrontmatterError, because some client would read it differently or not at all.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        raise FrontmatterError("SKILL.md must start with a --- frontmatter block")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() in ("---", "...")), None)
    if end is None:
        raise FrontmatterError("the frontmatter block never closes (no second --- line)")
    front_lines = lines[1:end]
    body = "\n".join(lines[end + 1:])
    meta: dict = {}
    i = 0
    while i < len(front_lines):
        line = front_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            raise FrontmatterError(f"unexpected indented line in frontmatter: {stripped[:60]!r}")
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s+(.*)|\s*)$", line.rstrip())
        if not match:
            raise FrontmatterError(f"can't read frontmatter line {stripped[:60]!r} (expected `key: value`)")
        key, rest = match.group(1), (match.group(2) or "").strip()
        if key in meta:
            raise FrontmatterError(f"{key} appears twice in the frontmatter")
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            meta[key], i = _block(front_lines, i, rest, key)
            continue
        if rest == "" or rest.startswith("#"):
            # A nested map (one level), or an empty value.
            nested: dict[str, str] = {}
            j = i + 1
            while j < len(front_lines):
                sub = front_lines[j]
                if not sub.strip() or sub.strip().startswith("#"):
                    j += 1
                    continue
                if sub[0] not in " \t":
                    break
                sub_match = re.match(r"^\s+([A-Za-z0-9_.-]+):(?:\s+(.*)|\s*)$", sub.rstrip())
                if not sub_match:
                    raise FrontmatterError(f"{key}: can't read {sub.strip()[:60]!r} (expected `name: value`, "
                                           "one level deep)")
                sub_key, sub_raw = sub_match.group(1), (sub_match.group(2) or "")
                if sub_raw.strip() == "":
                    raise FrontmatterError(f"{key}.{sub_key}: nested maps go one level deep, with string values")
                if sub_key in nested:
                    raise FrontmatterError(f"{key}.{sub_key} appears twice")
                nested[sub_key] = _scalar(sub_raw, f"{key}.{sub_key}")
                j += 1
            meta[key] = nested if nested else ""
            i = j
            continue
        # A scalar, possibly continued on indented lines (plain) or until its closing quote (quoted).
        raw = rest
        j = i + 1
        while j < len(front_lines):
            nxt = front_lines[j]
            if not nxt.strip():
                if raw.lstrip().startswith(('"', "'")) and not _quote_closed(raw):
                    # A blank line inside a quoted scalar is a line break (escaped for the JSON-style reader).
                    raw += "\\n" if raw.lstrip().startswith('"') else "\n"
                    j += 1
                    continue
                break
            if nxt[0] not in " \t":
                break
            raw += " " + nxt.strip()
            j += 1
        if raw.lstrip().startswith(('"', "'")) and not _quote_closed(raw):
            raise FrontmatterError(f"{key}: the quoted value never closes")
        meta[key] = _scalar(raw, key)
        i = j
    return meta, "\n".join(front_lines), body


# ---------------------------------------------------------------------------------------------------------------
# Skills on disk


@dataclass
class Skill:
    name: str
    folder: Path
    text: str
    meta: dict = field(default_factory=dict)
    front: str = ""
    body: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        value = self.meta.get("description")
        return value.strip() if isinstance(value, str) else ""

    def references(self) -> list[Path]:
        """Reference files (Markdown), sorted, relative to the skill folder."""
        ref_dir = self.folder / "references"
        if not ref_dir.is_dir():
            return []
        return sorted(p.relative_to(self.folder) for p in ref_dir.rglob("*.md") if p.is_file() and _keep(p))

    def files(self) -> list[Path]:
        """Every file the skill ships, relative to its folder, sorted (dotfiles and caches left out)."""
        out = []
        for root, dirs, names in os.walk(self.folder):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_NAMES and not d.startswith("."))
            for name in sorted(names):
                full = Path(root) / name
                if _keep(full):
                    out.append(full.relative_to(self.folder))
        return sorted(out)


def _keep(path: Path) -> bool:
    return not (path.name in _SKIP_NAMES or path.name.startswith(".") or path.suffix == ".pyc")


def skills_src(src: str | os.PathLike | None = None) -> Path:
    """The skills folder: `src` if given, else skills/ in this checkout (read at call time, so tests can move it)."""
    return Path(src).expanduser().resolve() if src else Path(paths.SKILLS_SRC)


def discover(src: str | os.PathLike | None = None) -> list[Skill]:
    """Every skill in the folder (a subfolder with a SKILL.md), sorted by name, each checked against the rules."""
    root = skills_src(src)
    if not root.is_dir():
        raise MissingPrerequisite(f"There's no skills folder at {root}.",
                                  hint="Slate's skills live in skills/ in the checkout; reinstall Slate if it's gone.")
    found = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name.startswith((".", "_")) or not (folder / "SKILL.md").is_file():
            continue
        found.append(load_skill(folder))
    return found


def load_skill(folder: Path) -> Skill:
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    skill = Skill(name=folder.name, folder=folder, text=text)
    try:
        skill.meta, skill.front, skill.body = parse_frontmatter(text)
    except FrontmatterError as exc:
        skill.problems.append(f"frontmatter: {exc}")
        return skill
    skill.problems.extend(check_skill(skill))
    return skill


def check_skill(skill: Skill) -> list[str]:
    """The authoring rules every client depends on. Each problem is one plain sentence."""
    problems: list[str] = []
    meta = skill.meta
    extra = [k for k in meta if k not in ALLOWED_KEYS]
    if extra:
        problems.append(f"frontmatter has {', '.join(sorted(extra))}: only {', '.join(ALLOWED_KEYS)} are allowed "
                        "(claude.ai rejects any other key)")
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        problems.append("frontmatter has no name")
    else:
        if name != skill.folder.name:
            problems.append(f"name {name!r} doesn't match its folder {skill.folder.name!r}")
        if len(name) > MAX_NAME or not NAME_RE.match(name):
            problems.append(f"name {name!r} must be 1-{MAX_NAME} lowercase letters, digits and single hyphens")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append("frontmatter has no description")
    elif len(description.strip()) > MAX_DESCRIPTION:
        problems.append(f"description is {len(description.strip())} characters; the limit is {MAX_DESCRIPTION}")
    listing = listing_chars(meta)
    if listing > MAX_LISTING:
        problems.append(f"description and when_to_use come to {listing} characters; Claude Code cuts a skill's listing "
                        f"at {MAX_LISTING}, so the rest would never be read")
    compat = meta.get("compatibility")
    if compat is not None and (not isinstance(compat, str) or len(compat) > MAX_COMPATIBILITY):
        problems.append(f"compatibility must be a string of at most {MAX_COMPATIBILITY} characters")
    metadata = meta.get("metadata")
    if metadata not in (None, "") and not (isinstance(metadata, dict)
                                           and all(isinstance(v, str) for v in metadata.values())):
        problems.append("metadata must map names to strings (the Agent Skills spec)")
    for key in ("license", "allowed-tools"):
        if key in meta and not isinstance(meta[key], str):
            problems.append(f"{key} must be a string")
    line_count = skill.text.count("\n") + (0 if skill.text.endswith("\n") else 1)
    if line_count > MAX_SKILL_LINES:
        problems.append(f"SKILL.md is {line_count} lines; keep it under {MAX_SKILL_LINES} and move depth into "
                        "references/")
    texts = [("SKILL.md", skill.body)] + [(str(r), (skill.folder / r).read_text(encoding="utf-8"))
                                           for r in skill.references()]
    for rel, text in texts:
        for pattern, what in NOT_PORTABLE:
            hit = pattern.search(text)
            if hit:
                problems.append(f"{rel} names {what} ({hit.group(0)!r}); every agent reads this file, so say it in "
                                "agent-neutral words")
        for target in dict.fromkeys(_local_links(text)):  # each broken link once, however often it's used
            base = skill.folder / Path(rel).parent
            if not (base / target).resolve().exists():
                problems.append(f"{rel} links to {target}, which isn't in the skill")
    for ref in skill.references():
        if len(ref.parts) > 2:
            skill.notes.append(f"{ref} is nested; the spec keeps references one level deep")
    for rel in skill.files():
        kind = MEDIA.get(rel.suffix.lower())
        if kind:
            problems.append(f"{rel} is {kind}: media stays out of skills and the plugin (claude.ai caps a plugin at "
                            "200 MB and 5,000 files, and fonts are licensed per brand); reference it by name and "
                            "fetch it at run time")
    return problems


def listing_chars(meta: dict) -> int:
    """What Claude Code shows of a skill in its listing: the description plus any when_to_use."""
    return sum(len(meta[k].strip()) for k in ("description", "when_to_use") if isinstance(meta.get(k), str))


def codex_entry(skill: "Skill") -> str:
    """One skill as Codex lists it in its context: name, description and where its file is."""
    return f"- {skill.name}: {skill.description} (file: ~/.agents/skills/{skill.name}/SKILL.md)\n"


def codex_list_chars(skills: Iterable["Skill"]) -> int:
    return sum(len(codex_entry(s)) for s in skills)


def _local_links(text: str) -> list[str]:
    out = []
    for match in _LINK_RE.finditer(text):
        target = match.group(3).split("#", 1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "/", "#")):
            continue
        out.append(target)
    return out


# ---------------------------------------------------------------------------------------------------------------
# What gets generated


def _title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    return fallback


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9 -]", "", text.lower()).strip().replace(" ", "-")
    return re.sub(r"-{2,}", "-", slug) or "section"


def display_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def short_description(description: str) -> str:
    """The first sentence, cut at a word under the limit (Codex shows it in its skill picker)."""
    text = " ".join(description.split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    first = match.group(1) if match else text
    if len(first) <= MAX_SHORT_DESCRIPTION:
        return first
    cut = first[:MAX_SHORT_DESCRIPTION - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def _yaml_str(value: str) -> str:
    # A JSON string literal is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=False)


def codex_sidecar(skill: Skill) -> str:
    """agents/openai.yaml: how Codex shows the skill, and that it may pick it up on its own."""
    lines = [
        f"# {GENERATED} (from skills/{skill.name}/SKILL.md). Edit the skill, not this file.",
        "interface:",
        f"  display_name: {_yaml_str(display_name(skill.name))}",
        f"  short_description: {_yaml_str(short_description(skill.description))}",
    ]
    if skill.name == FRONT_DOOR:
        lines.append(f"  default_prompt: {_yaml_str('Use $' + skill.name + ' to make a 30-second launch video for our product.')}")
    lines += ["policy:", "  allow_implicit_invocation: true", ""]
    return "\n".join(lines)


def knowledge_edition(skill: Skill) -> str:
    """SKILL.md for a regular Claude chat: the same frontmatter, a note on what a chat can do, every reference inlined.

    Headings inside a reference move down one level so the whole file keeps one outline, and links to a reference
    file point at its inlined section instead.
    """
    refs = [(r, (skill.folder / r).read_text(encoding="utf-8")) for r in skill.references()]
    anchors: dict[str, str] = {}
    titled = []
    for rel, text in refs:
        title = _title_of(text, rel.stem.replace("-", " ").capitalize())
        heading = f"Reference: {title}"
        anchors[str(rel)] = "#" + _slug(heading)
        titled.append((rel, text, heading))

    def relink(text: str, here: Path) -> str:
        def swap(match: re.Match) -> str:
            bang, label, target, title = match.group(1), match.group(2), match.group(3), match.group(4) or ""
            path_part = target.split("#", 1)[0]
            if bang or not path_part or "://" in path_part:
                return match.group(0)
            resolved = os.path.normpath(str(here / path_part))
            if resolved in anchors:
                return f"[{label}]({anchors[resolved]}{title})"
            return match.group(0)
        return _LINK_RE.sub(swap, text)

    def demote(text: str) -> str:
        out, fence = [], False
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
            match = None if fence else _HEADING_RE.match(line)
            if match and len(match.group(1)) < 6:
                out.append("#" + line)
            else:
                out.append(line)
        return "\n".join(out)

    front: list[str] = []
    for key, value in skill.meta.items():
        if key not in ALLOWED_KEYS or value in (None, ""):
            continue
        if isinstance(value, dict):
            front.append(f"{key}:")
            front += [f"  {sub}: {_yaml_str(str(v))}" for sub, v in value.items()]
        else:
            front.append(f"{key}: {_front_value(value)}")
    note =(f"> Knowledge-only edition of Slate's `{skill.name}` skill, for Claude chats ({GENERATED}). A chat can't "
            "run the Slate command line: use this to plan, write and review, and hand any `slate` command to the "
            "person to run, or to Slate's desktop extension if it's installed.")
    parts = ["---", *front, "---", "", note, "", relink(skill.body.strip("\n"), Path("."))]
    for rel, text, heading in titled:
        body = text
        first = body.lstrip("\n").splitlines()[:1]
        if first and _HEADING_RE.match(first[0]) and len(_HEADING_RE.match(first[0]).group(1)) == 1:
            body = body.lstrip("\n").split("\n", 1)[1] if "\n" in body.lstrip("\n") else ""
        parts += ["", "---", "", f"## {heading}", "", demote(relink(body.strip("\n"), rel.parent))]
    return "\n".join(parts).rstrip("\n") + "\n"


def _front_value(value) -> str:
    text = str(value)
    if "\n" in text.strip("\n"):
        return "|-\n" + "\n".join("  " + line for line in text.strip("\n").split("\n"))
    return _yaml_str(text.strip())


def _zip(members: dict[str, bytes]) -> bytes:
    """A deterministic zip: stored (not compressed), fixed timestamps, sorted names, fixed permissions."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, members[name])
    return buf.getvalue()


def _zip_members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in sorted(zf.namelist())}


def _denied_connector_tools() -> list[str]:
    try:
        from slate.vendors.higgsfield import DENIED_CONNECTOR_TOOLS  # noqa: PLC0415
    except ImportError:
        return []
    return list(DENIED_CONNECTOR_TOOLS)


OPENCLAW_CONNECT_TIMEOUT_MS = 30_000  # the same wait `slate install` registers (slate.install.mcp_register)


def openclaw_entries(skills: list[Skill]) -> dict:
    return {
        "generated": GENERATED,
        "about": ("Slate's entries for OpenClaw. `slate install` does all of this for you on a Mac with Slate "
                  "installed. By hand: register the server with `openclaw mcp add slate --command slate --arg mcp "
                  "--connect-timeout 30` (OpenClaw 2026.9.4 probes it before saving, and `slate mcp` can take longer "
                  "than the default 5 seconds to answer on a busy Mac), or merge `openclaw_json` into "
                  "~/.openclaw/openclaw.json (JSON5) and restart the gateway. OpenClaw reads Slate's skills from "
                  "~/.agents/skills, where the installer links each one to the checkout's skills/ folder, and only "
                  "when the gateway uses its default state folder. Its PATH lacks ~/.local/bin, so the `slate` "
                  "command is also linked into /opt/homebrew/bin. If OpenClaw refuses the linked skills, add the "
                  "checkout's skills/ folder to skills.load.allowSymlinkTargets."),
        "skills": [s.name for s in skills],
        "denied_connector_tools": {
            "tools": _denied_connector_tools(),
            "why": "Higgsfield's hosted connector can upload a person's skills and a personality profile, and publish "
                   "to TikTok or a website. Slate never calls these. OpenClaw has no Higgsfield connector today; if "
                   "one is ever added for an agent that runs autonomous films, deny these tools there.",
        },
        "openclaw_json": {
            "skills": {"entries": {s.name: {"enabled": True} for s in skills}},
            "mcp": {"servers": {"slate": {"command": "slate", "args": ["mcp"],
                                          "connectionTimeoutMs": OPENCLAW_CONNECT_TIMEOUT_MS}}},
        },
    }


# ---------------------------------------------------------------------------------------------------------------
# The plugin's own files: manifests, MCP configs, README and LICENSE, generated like the rest

PLUGIN_NAME = "slate"
PLUGIN_DESCRIPTION = ("A film studio any agent can run: a brief becomes a finished, gated film. Ten skills for intake, "
                      "story, motion, building, voice, sound, generation, finishing, QA and brand onboarding, plus "
                      "Slate's MCP server over its command line and job queue. Needs Slate installed on the Mac first.")
PLUGIN_AUTHOR = {"name": "Quinn O'Donnell", "url": "https://github.com/Quinnod345"}
PLUGIN_REPO = "https://github.com/Quinnod345/slate"
PLUGIN_KEYWORDS = ("video", "film", "motion-design", "voice", "sound-design", "remotion", "after-effects")
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0"

# Slate's own server: the command line over stdio, never opening a browser.
SLATE_SERVER = {"command": "slate", "args": ["mcp"], "env": {"BROWSER": "none"}}
# The vendors' hosted connectors (URLs checked 2026-10-01). Each signs in with OAuth in the person's own browser the
# first time it's connected, so no key is ever written here. Generations made through them bypass Slate's ledger;
# balance snapshots at a session's start and end catch them (slate ledger snapshot, then slate ledger reconcile).
HOSTED_CONNECTORS: tuple[tuple[str, str, str, str], ...] = (
    ("elevenlabs", "https://api.elevenlabs.io/v1/mcp", "ElevenLabs", "voices, music and its creative models"),
    ("higgsfield", "https://mcp.higgsfield.ai/mcp", "Higgsfield", "image and video generation"),
)

# The commands an unattended run needs without a prompt, and what each is for. aerender is left out on purpose: After
# Effects work starts only on a person's go, and Slate runs aerender itself when they give it.
PERMISSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("slate",), "every stage (Slate starts its own renders, mixes and After Effects steps)"),
    (("ffmpeg",), "checking and cutting media by hand"),
    (("ffprobe",), "reading a file's streams and length"),
    (("whisper",), "word timings checked by hand"),
    ((".venv/bin/python",), "Slate's own Python, from the checkout (its tests and tools)"),
    (("node", "engine/cli.mjs"), "the engine's renders and checks run directly, from the checkout"),
)


def mcp_servers(dialect: str) -> dict:
    """Slate's server and the hosted connectors in one client's dialect: "claude" (.mcp.json, remote servers typed
    http) or "agent-plugins" (mcp.json, where the local server says it's stdio and a remote one is streamable-http)."""
    local = json.loads(json.dumps(SLATE_SERVER))
    if dialect == "claude":
        servers = {PLUGIN_NAME: local}
        servers.update({name: {"type": "http", "url": url} for name, url, _, _ in HOSTED_CONNECTORS})
        return {"mcpServers": servers}
    if dialect == "agent-plugins":
        servers = {PLUGIN_NAME: {"type": "stdio", **local}}
        servers.update({name: {"type": "streamable-http", "url": url} for name, url, _, _ in HOSTED_CONNECTORS})
        return {"$schema": f"{AGENT_PLUGINS_SCHEMA}/mcp.schema.json", "mcpServers": servers}
    raise ValueError(f"unknown MCP dialect {dialect!r}")


def plugin_manifest(dialect: str) -> dict:
    """The plugin's manifest: Claude Code's (.claude-plugin/plugin.json) or Agent Plugins' (plugin.json)."""
    from slate import __version__  # noqa: PLC0415

    body = {"version": __version__, "description": PLUGIN_DESCRIPTION, "author": dict(PLUGIN_AUTHOR),
            "homepage": PLUGIN_REPO, "repository": PLUGIN_REPO, "license": "Proprietary",
            "keywords": list(PLUGIN_KEYWORDS)}
    if dialect == "claude":
        return {"name": PLUGIN_NAME, "displayName": "Slate", **body}
    if dialect == "agent-plugins":
        return {"$schema": f"{AGENT_PLUGINS_SCHEMA}/plugin.schema.json", "name": PLUGIN_NAME, **body}
    raise ValueError(f"unknown manifest dialect {dialect!r}")


def permission_rules() -> dict:
    """The recommended rules for an unattended run, per agent: what to allow, and the connector tools to deny."""
    allow = [f"Bash({' '.join(words)} *)" for words, _ in PERMISSIONS]
    allow.append(f"mcp__plugin_{PLUGIN_NAME}_{PLUGIN_NAME}")  # every tool of the plugin's own Slate server
    deny = [f"mcp__plugin_{PLUGIN_NAME}_higgsfield__{tool}" for tool in _denied_connector_tools()]
    codex = [f'prefix_rule(pattern={json.dumps(list(words))}, decision="allow")' for words, _ in PERMISSIONS]
    return {"claude_code": {"permissions": {"allow": allow, "deny": deny}}, "codex": codex}


def plugin_readme() -> str:
    """The plugin's README, written from the same data as its manifests and MCP configs."""
    rules = permission_rules()
    settings = jsonio.dumps(rules["claude_code"]).rstrip("\n")
    codex = "\n".join(rules["codex"])
    from slate.doctor.agents import MIN_CLAUDE_CODE  # noqa: PLC0415 - stated once, in doctor's version row

    commands = "\n".join(f"| `{' '.join(words)}` | {what} |" for words, what in PERMISSIONS)
    connectors = "\n".join(f"- **{label}** (`{url}`): {what}." for _name, url, label, what in HOSTED_CONNECTORS)
    denied = ", ".join(f"`{tool}`" for tool in _denied_connector_tools()) or "none"
    never = textwrap.fill(f"- **Slate never calls some of Higgsfield's connector tools**: {denied}. They upload a "
                          "person's skills and a profile, or publish to TikTok or a website. Deny them wherever an "
                          "agent runs films on its own (the rules below do).", width=118, subsequent_indent="  ",
                          break_on_hyphens=False, break_long_words=False)
    return f"""# Slate plugin

Slate is a film studio any agent can run: a brief becomes a finished, gated film, with story, voice, motion, sound, QA
and delivery each checked by a machine before it counts as done. This plugin brings Slate's skills, its MCP server and
the voice and generation vendors' hosted connectors to Claude Code (and to Codex, OpenClaw and other clients that read
Agent Plugins). It's for people other than Slate's author, who links the skills from his checkout instead.

Everything in this folder is written by `slate skills build` from Slate's repository
({GENERATED}).

## Before you install it

The plugin is the front door, not the studio. Slate itself (its command line, render engine and job queue) runs on
your Mac, so install it first, with one command in Terminal:

```sh
git clone {PLUGIN_REPO}.git ~/code/slate && ~/code/slate/install.sh
```

Then run `slate doctor` and fix anything it names. The plugin's MCP server runs `slate mcp`, so `slate` has to be on
your PATH (the installer links it into `~/.local/bin`).

It needs Claude Code {MIN_CLAUDE_CODE} or later, both the terminal build and the one the desktop app bundles (they
update separately); `slate doctor` checks both.

## Install

In Claude Code:

```
/plugin marketplace add Quinnod345/slate
/plugin install slate@slate
```

**Turn on auto-update for Slate's marketplace** (`/plugin`, Marketplaces): it's off by default for third-party
marketplaces, and without it you won't get fixes.

The repository is private for now, so your git credentials need access to it (`gh auth setup-git` once, if git asks).

Codex and OpenClaw don't need the plugin: the installer above links Slate's skills where both read them and registers
Slate's MCP server with each. Reading this repository as their plugin marketplace isn't confirmed yet (whether Codex
takes a Claude-style marketplace's sources, and whether OpenClaw expands Claude's path placeholders in a bundle; this
plugin uses none). Slate's repository records every client behaviour that's still unconfirmed, with the hands-on check
that settles it, in `tests/packaging/client_behaviours.json`.

## What's inside

- `skills/`: Slate's skills, copied from the repository's `skills/` folder. Each copy carries a Codex sidecar
  (`agents/openai.yaml`).
- `.mcp.json` (Claude Code) and `mcp.json` (Agent Plugins): Slate's own server, which runs `slate mcp`, a thin server
  over Slate's command line and job queue (long work such as renders, voicing, QA and delivery always runs as a
  background job), and the vendors' hosted connectors (below).
- `.claude-plugin/plugin.json` (Claude Code) and `plugin.json` (Agent Plugins): the two manifests over the same folder.
- `claude-ai/`: knowledge-only editions of each skill, zipped for uploading to a regular Claude chat.
- `openclaw/entries.json`: the skill and MCP entries for OpenClaw's config.

There's no `bin/` folder on purpose: Claude's chat and Cowork refuse to install a plugin that has one. The `slate`
command comes from the install above. There's no media either (fonts, sound banks, LUTs, audio, video): claude.ai caps
a plugin at 200 MB and 5,000 files, and `slate skills build` refuses media in a skill.

## The hosted connectors

{connectors}

Each signs in with OAuth in your own browser the first time you connect it (in Claude Code, `/mcp`; on claude.ai, the
plugin's Connectors tab). No API key goes in any file here. They're for people working in a chat; Slate's own stages
call the vendors' APIs with the keys you gave `slate connect`, through its ledger.

- **Generations made through a connector bypass Slate's ledger.** A working session starts and ends with
  `slate ledger snapshot --all --session <label>` (for Higgsfield, also
  `slate ledger snapshot --provider higgsfield --balance <n> --session <label>` with the balance its connector shows),
  then `slate ledger reconcile --session <label>`, so that spend is caught.
{never}

## Permissions for unattended runs

An agent that stops to ask before each command stalls an unattended run. These are the commands Slate's agents run
themselves:

| Command | What it's for |
|---|---|
{commands}

aerender isn't on the list on purpose: After Effects work starts only on a person's go, and Slate runs aerender itself
when they give it.

Claude Code, in `~/.claude/settings.json` (or a project's `.claude/settings.json`):

```json
{settings}
```

Codex, in a rules file such as `~/.codex/rules/slate.rules`:

```
{codex}
```

## Licence

Slate is proprietary for now (see `LICENSE`). Using it needs Quinn O'Donnell's permission; access to the repository
comes with that permission.
"""


def plugin_files() -> dict[str, tuple[bytes, bool]]:
    """The plugin's own files, {path under the build folder: (bytes, executable)}."""
    out = {
        ".claude-plugin/plugin.json": (jsonio.dumps(plugin_manifest("claude")).encode(), False),
        "plugin.json": (jsonio.dumps(plugin_manifest("agent-plugins")).encode(), False),
        ".mcp.json": (jsonio.dumps(mcp_servers("claude")).encode(), False),
        "mcp.json": (jsonio.dumps(mcp_servers("agent-plugins")).encode(), False),
        "README.md": (plugin_readme().encode(), False),
    }
    licence = paths.REPO / "LICENSE"
    if licence.is_file():
        out["LICENSE"] = (licence.read_bytes(), False)
    return out


SKILLS_README = f"""# Generated skills: don't edit here

Everything in this folder is written by `slate skills build` ({GENERATED}) from the skills in `skills/` at the root
of the Slate checkout. Edit those, then run `slate skills build`. CI runs `slate skills build --check` and fails when
this copy has drifted from the source.

Each skill here is the source skill plus a Codex sidecar (`agents/openai.yaml`). The plugin ships these copies because
a Claude Code plugin can't reach files outside its own folder.
"""

CLAUDE_AI_README = f"""# Knowledge-only skills for Claude chats

Written by `slate skills build` ({GENERATED}). Each zip is one of Slate's skills with its reference files inlined into
SKILL.md, for a regular Claude chat, where the Slate command line isn't available.

To add one: claude.ai, Customize, Skills, upload the zip. Upload each skill you want once.
"""


@dataclass
class Build:
    """The whole generated tree: {path under the build folder: (bytes, executable)}."""

    files: dict[str, tuple[bytes, bool]]
    skills: list[Skill]
    problems: list[str]
    notes: list[str]


def render(src: str | os.PathLike | None = None) -> Build:
    """Generate every client's copy in memory. Nothing is written; problems are collected, not raised."""
    skills = discover(src)
    problems = [f"{s.name}: {p}" for s in skills for p in s.problems]
    notes = [f"{s.name}: {n}" for s in skills for n in s.notes]
    files: dict[str, tuple[bytes, bool]] = {
        "skills/README.md": (SKILLS_README.encode(), False),
        "claude-ai/README.md": (CLAUDE_AI_README.encode(), False),
    }
    good = [s for s in skills if not s.problems]
    for skill in good:
        own_sidecar = False
        for rel in skill.files():
            full = skill.folder / rel
            if rel.as_posix() == "agents/openai.yaml":
                own_sidecar = True
            files[f"skills/{skill.name}/{rel.as_posix()}"] = (full.read_bytes(), os.access(full, os.X_OK))
        if not own_sidecar:
            files[f"skills/{skill.name}/agents/openai.yaml"] = (codex_sidecar(skill).encode(), False)
        edition = knowledge_edition(skill).encode()
        files[f"claude-ai/{skill.name}.zip"] = (_zip({f"{skill.name}/SKILL.md": edition}), False)
    files["openclaw/entries.json"] = (jsonio.dumps(openclaw_entries(good)).encode(), False)
    files.update(plugin_files())
    problems += plugin_problems(skills, files, notes)
    return Build(files=files, skills=skills, problems=problems, notes=notes)


def plugin_problems(skills: list[Skill], files: dict[str, tuple[bytes, bool]], notes: list[str]) -> list[str]:
    """What the clients' limits say about the whole build: Codex's skill-list budget and claude.ai's plugin caps.
    Problems name `plugin`, not a skill; a list near Codex's budget is a note."""
    problems = []
    listed = codex_list_chars(s for s in skills if s.description)
    if listed > CODEX_LIST_BUDGET:
        problems.append(f"plugin: the skill list comes to {listed:,} characters as Codex lists it (name, description "
                        f"and path); Codex keeps it within {CODEX_LIST_BUDGET:,}, so skills past it would go missing. "
                        "Tighten the descriptions")
    elif listed >= CODEX_LIST_NOTE * CODEX_LIST_BUDGET:
        notes.append(f"plugin: the skill list comes to {listed:,} of Codex's {CODEX_LIST_BUDGET:,} characters "
                     f"({listed / CODEX_LIST_BUDGET:.0%}); keep new descriptions tight")
    count, size = len(files), sum(len(data) for data, _ in files.values())
    if count > PLUGIN_MAX_FILES:
        problems.append(f"plugin: {count:,} files; claude.ai takes at most {PLUGIN_MAX_FILES:,} in a plugin")
    if size > PLUGIN_MAX_BYTES:
        problems.append(f"plugin: {size / 1024 / 1024:,.1f} MB; claude.ai takes at most "
                        f"{PLUGIN_MAX_BYTES // 1024 // 1024} MB in a plugin")
    return problems


def default_out() -> Path:
    return paths.REPO / "plugins" / "slate"


def _guard_out(out: Path, src: Path) -> None:
    out, src = out.resolve(), src.resolve()
    if out == paths.REPO.resolve() or out == src or src.is_relative_to(out) or out.is_relative_to(src):
        raise UsageError(f"The build folder {out} would overlap the skills themselves ({src}); pick another --out.")


def _stray_dirs(out: Path) -> list[Path]:
    """Top-level folders in the build folder that the build doesn't make (hidden ones other than the manifest's are
    left alone)."""
    if not out.is_dir():
        return []
    return sorted(p for p in out.iterdir() if p.is_dir() and p.name not in MANAGED and p.name != ".claude-plugin"
                  and not p.name.startswith(".") and p.name not in _SKIP_NAMES)


def _on_disk(out: Path) -> dict[str, tuple[bytes, bool]]:
    """Every file the build owns in the build folder: its managed folders, the plugin's top-level files and anything
    else at the top level (which is stale by definition)."""
    found: dict[str, tuple[bytes, bool]] = {}

    def add(full: Path) -> None:
        found[full.relative_to(out).as_posix()] = (full.read_bytes(), os.access(full, os.X_OK))

    for base in [out / sub for sub in MANAGED] + _stray_dirs(out):
        if not base.is_dir():
            continue
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if name == ".DS_Store":
                    continue
                add(Path(root) / name)
    for folder in (out, out / ".claude-plugin"):
        if folder.is_dir():
            for full in folder.iterdir():
                if full.is_file() and full.name != ".DS_Store":
                    add(full)
    return found


def _same(path: str, a: tuple[bytes, bool], b: tuple[bytes, bool]) -> bool:
    if path.endswith(".zip"):
        try:
            return _zip_members(a[0]) == _zip_members(b[0])
        except zipfile.BadZipFile:
            return False
    return a == b


def compare(built: Build, out: Path) -> dict:
    """What differs between the generated tree and the build folder: missing, changed and extra files."""
    disk = _on_disk(out)
    missing = sorted(p for p in built.files if p not in disk)
    changed = sorted(p for p in built.files if p in disk and not _same(p, built.files[p], disk[p]))
    extra = sorted(p for p in disk if p not in built.files)
    return {"missing": missing, "changed": changed, "extra": extra, "clean": not (missing or changed or extra)}


def build(src: str | os.PathLike | None = None, out: str | os.PathLike | None = None, *,
          write: bool = True) -> dict:
    """Generate and write every client's copy. Refuses (writing nothing) when any skill breaks an authoring rule."""
    source = skills_src(src)
    target = Path(out).expanduser().resolve() if out else default_out()
    _guard_out(target, source)
    built = render(source)
    report = {"src": str(source), "out": str(target), "skills": [s.name for s in built.skills],
              "problems": built.problems, "notes": built.notes}
    if built.problems:
        report.update(written=[], removed=[], clean=False)
        return report
    diff = compare(built, target)
    report.update(diff)
    written, removed = [], []
    if write:
        for rel in diff["missing"] + diff["changed"]:
            data, executable = built.files[rel]
            path = target / rel
            jsonio.write_text_atomic(path, data)
            os.chmod(path, 0o755 if executable else 0o644)
            written.append(rel)
        for rel in diff["extra"]:
            (target / rel).unlink()
            removed.append(rel)
        for folder in [target / sub for sub in MANAGED] + _stray_dirs(target):
            _prune_empty(folder)
            if folder.name not in MANAGED and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    report.update(written=written, removed=removed)
    return report


def _prune_empty(folder: Path) -> None:
    """Remove folders a removed skill left empty (children first, so parents empty out too)."""
    if not folder.is_dir():
        return
    for root, _dirs, _files in os.walk(folder, topdown=False):
        here = Path(root)
        if here != folder and not any(here.iterdir()):
            here.rmdir()


def check(src: str | os.PathLike | None = None, out: str | os.PathLike | None = None, *,
          validators: bool = False) -> dict:
    """Rebuild in memory and compare with the build folder. `clean` is True only when nothing drifted.

    With validators=True (what `slate skills build --check` does), a clean build folder is also run through each
    client's own validator where it's installed; `valid` is False when one of them fails it."""
    report = build(src, out, write=False)
    if validators and not report["problems"]:
        rows = validate(Path(report["out"]), report["skills"], ready=report["clean"])
        report["validators"] = rows
        report["valid"] = all(r["ok"] is not False for r in rows)
        report["notes"] = report["notes"] + [f"{r['validator']} was skipped: {r['why']}" for r in rows
                                             if r["status"] == "skipped"]
    return report


# ---------------------------------------------------------------------------------------------------------------
# The clients' own validators, run on the built plugin by `--check` wherever they're installed

@dataclass(frozen=True)
class Validator:
    tool: str  # the command it needs
    label: str  # how reports name it
    per_skill: bool  # run once per skill folder, or once on the plugin

    def argv(self, exe: str, target: Path) -> list[str]:
        if self.per_skill:
            return [exe, "validate", str(target)]
        return [exe, "plugin", "validate", "--strict", str(target)]


VALIDATORS = (Validator("claude", "claude plugin validate --strict", per_skill=False),
              Validator("skills-ref", "skills-ref validate", per_skill=True))
VALIDATOR_TIMEOUT_S = 120


def find_tool(name: str) -> str | None:
    """Where a validator's command is installed, or None (the tests replace this)."""
    from slate import procs  # noqa: PLC0415

    return procs.which(name)


def validate(out: Path, skills: Iterable[str], *, ready: bool = True) -> list[dict]:
    """Each client's validator on the built plugin: one row per run, {validator, target, status (ran or skipped), ok,
    output or why}. A validator that isn't installed is skipped with the reason, and so is every one when the build
    folder has drifted (`ready` False): it would judge a stale copy."""
    from slate import procs  # noqa: PLC0415

    rows: list[dict] = []
    for v in VALIDATORS:
        exe = find_tool(v.tool)
        if not exe:
            rows.append({"validator": v.label, "target": str(out), "status": "skipped", "ok": None,
                         "why": f"`{v.tool}` isn't installed here"})
            continue
        if not ready:
            rows.append({"validator": v.label, "target": str(out), "status": "skipped", "ok": None,
                         "why": "the build folder has drifted from the skills; run `slate skills build` first"})
            continue
        for target in ([out / "skills" / name for name in skills] if v.per_skill else [out]):
            try:
                proc = procs.run(v.argv(exe, target), timeout=VALIDATOR_TIMEOUT_S)
                ok, output = proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()
            except Exception as exc:  # noqa: BLE001 - a validator that can't run fails the check, with why
                ok, output = False, f"couldn't run: {exc}"
            rows.append({"validator": v.label, "target": str(target), "status": "ran", "ok": ok,
                         "output": output[-2000:]})
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Where agents see the skills (doctor's "each skill listed once" check)

AGENT_ROOTS = {
    # Codex reads both of its roots, so a skill in both is listed twice (plan §6).
    "codex": ("~/.agents/skills", "~/.codex/skills"),
    # OpenClaw reads ~/.agents/skills (default state folder only) and its managed root.
    "openclaw": ("~/.agents/skills", "~/.openclaw/skills"),
    "claude-code": ("~/.claude/skills",),
}


def installed(names: Iterable[str], roots: dict[str, Iterable[str | os.PathLike]] | None = None) -> list[dict]:
    """For each skill and agent: how many of that agent's skill roots hold it (1 is right; 0 missing; 2+ doubled)."""
    table = roots if roots is not None else AGENT_ROOTS
    out = []
    for name in names:
        for agent, agent_roots in table.items():
            where = [str(Path(r).expanduser() / name) for r in agent_roots
                     if (Path(r).expanduser() / name / "SKILL.md").is_file()]
            status = "once" if len(where) == 1 else ("missing" if not where else "duplicated")
            out.append({"skill": name, "agent": agent, "status": status, "where": where})
    return out


# ---------------------------------------------------------------------------------------------------------------
# CLI


def _human_build(report: dict, *, checking: bool) -> str:
    lines = []
    if report["problems"]:
        lines.append("These break the authoring rules or a client's limits, so nothing was built:")
        lines += [f"  - {p}" for p in report["problems"]]
        return "\n".join(lines)
    count = len(report["skills"])
    if count == 0:
        lines.append(f"No skills in {report['src']} yet (each is a folder with a SKILL.md).")
    if checking:
        if report["clean"]:
            lines.append(f"The generated copies in {report['out']} match the {count} skills. Nothing drifted.")
        else:
            lines.append(f"The generated copies in {report['out']} have drifted from the skills:")
            lines += [f"  missing  {p}" for p in report["missing"]]
            lines += [f"  changed  {p}" for p in report["changed"]]
            lines += [f"  stale    {p}" for p in report["extra"]]
            lines.append("next: slate skills build")
        for row in report.get("validators", []):
            if row["status"] == "ran":
                where = Path(row["target"]).name
                lines.append(f"  {'passed' if row['ok'] else 'FAILED'} {row['validator']} ({where})")
                if not row["ok"]:
                    lines += [f"      {ln}" for ln in row["output"].splitlines()[-8:]]
    else:
        verb = "Would write" if report.get("dry_run") else "Wrote"
        lines.append(f"Built {count} skills into {report['out']}.")
        if report["written"] or report["removed"]:
            lines.append(f"  {verb} {len(report['written'])} files, removed {len(report['removed'])} stale ones.")
        else:
            lines.append("  Everything was already up to date.")
    lines += [f"  note: {n}" for n in report["notes"]]
    return "\n".join(lines)


def cmd_build(args: argparse.Namespace) -> int:
    dry = bool(cli.flag(args, "dry_run", False))
    checking = bool(args.check)
    if checking:
        report = check(args.src, args.out, validators=not args.no_validate)
    else:
        report = build(args.src, args.out, write=not dry)
    if dry and not checking:
        report["dry_run"] = True
        report["written"] = report.get("missing", []) + report.get("changed", [])
        report["removed"] = report.get("extra", [])
    log.result(report, _human_build(report, checking=checking))
    if report["problems"]:
        return 1
    if checking and not (report["clean"] and report.get("valid", True)):
        return 1
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    skills = discover(args.src)
    rows = []
    for s in skills:
        rows.append({"name": s.name, "valid": not s.problems, "problems": s.problems, "notes": s.notes,
                     "references": [str(r) for r in s.references()], "description": s.description,
                     "path": str(s.folder)})
    out: dict = {"src": str(skills_src(args.src)), "skills": rows}
    lines = [f"Skills in {out['src']}:" if rows else f"No skills in {out['src']} yet."]
    for row in rows:
        mark = "ok     " if row["valid"] else "BROKEN "
        lines.append(f"  {mark}{row['name']:<18} {len(row['references'])} references  "
                     f"{short_description(row['description'])}")
        lines += [f"         - {p}" for p in row["problems"]]
    if args.installed:
        where = installed([r["name"] for r in rows])
        out["installed"] = where
        lines.append("Where each agent sees them (each should be listed exactly once):")
        for row in where:
            lines.append(f"  {row['status']:<10} {row['agent']:<12} {row['skill']}"
                         + (f"  ({', '.join(row['where'])})" if row["status"] == "duplicated" else ""))
    log.result(out, "\n".join(lines))
    bad = any(not r["valid"] for r in rows) or (args.installed and any(r["status"] != "once"
                                                                        for r in out.get("installed", [])))
    return 1 if bad else 0


EPILOG = """Exit codes: 0 ok, 1 a skill breaks an authoring rule or a client's limit, or (with --check) a generated
copy drifted or a client's validator failed it, 2 usage, 3 no skills folder."""


def register(sub) -> None:
    group = cli.add_group(
        sub, "skills", "Slate's skills: build every client's copy, list them, check nothing drifted",
        description="The skills live once, in skills/. `slate skills build` generates what each client needs on top: "
                    "the plugin's copies with Codex sidecars, OpenClaw's entries and the knowledge-only zips for "
                    "Claude chats. Read a skill's references with `slate guide`.")
    p = cli.add_command(group, "build", cmd_build,
                        "generate the plugin copies, Codex sidecars, OpenClaw entries and claude.ai zips",
                        description=__doc__, epilog=EPILOG)
    p.add_argument("--check", action="store_true",
                   help="write nothing; exit 1 if a generated copy has drifted from the skills, or if a client's own "
                        "validator (claude plugin validate --strict, skills-ref validate) fails it where installed "
                        "(CI runs this)")
    p.add_argument("--no-validate", action="store_true",
                   help="with --check: skip the clients' validators (the drift check still runs)")
    p.add_argument("--src", metavar="DIR", help="the skills folder (default: skills/ in this checkout)")
    p.add_argument("--out", metavar="DIR", help="the build folder (default: plugins/slate in this checkout)")
    p = cli.add_command(group, "list", cmd_list, "the skills, whether each follows the rules, and where agents see them",
                        epilog=EPILOG)
    p.add_argument("--src", metavar="DIR", help="the skills folder (default: skills/ in this checkout)")
    p.add_argument("--installed", action="store_true",
                   help="also show, per agent, whether each skill is installed exactly once")


__all__ = ["register", "discover", "load_skill", "parse_frontmatter", "check_skill", "render", "build", "check",
           "compare", "installed", "knowledge_edition", "codex_sidecar", "openclaw_entries", "Skill",
           "FrontmatterError", "ALLOWED_KEYS", "plugin_files", "plugin_manifest", "mcp_servers", "plugin_readme",
           "permission_rules", "plugin_problems", "validate", "VALIDATORS", "find_tool", "codex_list_chars",
           "listing_chars", "HOSTED_CONNECTORS", "PERMISSIONS", "ROOT_FILES", "MEDIA"]
