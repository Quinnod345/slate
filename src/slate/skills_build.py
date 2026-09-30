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

`slate skills build --check` rebuilds everything in memory and compares it with what's on disk; CI runs it, so a
skill edited without a rebuild fails the build instead of shipping a stale copy.

The same pass enforces the authoring rules every client depends on: spec-only frontmatter (claude.ai rejects any other
key), a name that matches its folder, a description under 1,024 characters, SKILL.md under 500 lines, working links to
reference files, and no wording that only one agent understands (Claude-only tool names, client path placeholders).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
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

# The folders the build owns inside its output folder. Anything in them that the build didn't make is stale.
MANAGED = ("skills", "claude-ai", "openclaw")

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
    return problems


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


def openclaw_entries(skills: list[Skill]) -> dict:
    return {
        "generated": GENERATED,
        "about": ("Slate's entries for OpenClaw. Merge `openclaw_json` into ~/.openclaw/openclaw.json (JSON5), then "
                  "restart the gateway (`openclaw gateway restart`). OpenClaw reads Slate's skills from "
                  "~/.agents/skills, linked to plugins/slate/skills, and only when the gateway uses its default "
                  "state folder. Its PATH lacks ~/.local/bin, so the `slate` command must also be linked into "
                  "/opt/homebrew/bin. If OpenClaw refuses the linked skills, add this checkout's plugins/slate/skills "
                  "to skills.load.allowSymlinkTargets."),
        "skills": [s.name for s in skills],
        "denied_connector_tools": {
            "tools": _denied_connector_tools(),
            "why": "Higgsfield's hosted connector can upload a person's skills and a personality profile, and publish "
                   "to TikTok or a website. Slate never calls these. OpenClaw has no Higgsfield connector today; if "
                   "one is ever added for an agent that runs autonomous films, deny these tools there.",
        },
        "openclaw_json": {
            "skills": {"entries": {s.name: {"enabled": True} for s in skills}},
            "mcp": {"servers": {"slate": {"command": "slate", "args": ["mcp"]}}},
        },
    }


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
    return Build(files=files, skills=skills, problems=problems, notes=notes)


def default_out() -> Path:
    return paths.REPO / "plugins" / "slate"


def _guard_out(out: Path, src: Path) -> None:
    out, src = out.resolve(), src.resolve()
    if out == paths.REPO.resolve() or out == src or src.is_relative_to(out) or out.is_relative_to(src):
        raise UsageError(f"The build folder {out} would overlap the skills themselves ({src}); pick another --out.")


def _on_disk(out: Path) -> dict[str, tuple[bytes, bool]]:
    found: dict[str, tuple[bytes, bool]] = {}
    for sub in MANAGED:
        base = out / sub
        if not base.is_dir():
            continue
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if name == ".DS_Store":
                    continue
                full = Path(root) / name
                found[full.relative_to(out).as_posix()] = (full.read_bytes(), os.access(full, os.X_OK))
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
        for sub in MANAGED:
            _prune_empty(target / sub)
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


def check(src: str | os.PathLike | None = None, out: str | os.PathLike | None = None) -> dict:
    """Rebuild in memory and compare with the build folder. `clean` is True only when nothing drifted."""
    return build(src, out, write=False)


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
        lines.append("These skills break the authoring rules, so nothing was built:")
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
    report = build(args.src, args.out, write=not (checking or dry))
    if dry and not checking:
        report["dry_run"] = True
        report["written"] = report.get("missing", []) + report.get("changed", [])
        report["removed"] = report.get("extra", [])
    log.result(report, _human_build(report, checking=checking))
    if report["problems"]:
        return 1
    if checking and not report["clean"]:
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


EPILOG = """Exit codes: 0 ok, 1 a skill breaks an authoring rule or (with --check) a generated copy drifted,
2 usage, 3 no skills folder."""


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
                   help="write nothing; exit 1 if a generated copy has drifted from the skills (CI runs this)")
    p.add_argument("--src", metavar="DIR", help="the skills folder (default: skills/ in this checkout)")
    p.add_argument("--out", metavar="DIR", help="the build folder (default: plugins/slate in this checkout)")
    p = cli.add_command(group, "list", cmd_list, "the skills, whether each follows the rules, and where agents see them",
                        epilog=EPILOG)
    p.add_argument("--src", metavar="DIR", help="the skills folder (default: skills/ in this checkout)")
    p.add_argument("--installed", action="store_true",
                   help="also show, per agent, whether each skill is installed exactly once")


__all__ = ["register", "discover", "load_skill", "parse_frontmatter", "check_skill", "render", "build", "check",
           "compare", "installed", "knowledge_edition", "codex_sidecar", "openclaw_entries", "Skill",
           "FrontmatterError", "ALLOWED_KEYS"]
