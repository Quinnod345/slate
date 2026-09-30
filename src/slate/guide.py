"""`slate guide [topic]`: print Slate's reference files, straight from the skills.

Every skill keeps its depth in `skills/<skill>/references/*.md`. This command prints those same files (and each
SKILL.md), so an agent that can run a shell but can't load skills (a pursuit slice, a cron job, a person in Terminal)
reads exactly what the skills say. It only reads: the skills never depend on the command line to be readable, and the
command line never keeps its own copy.

Topics, loosest first:

    slate guide                       every topic, grouped by skill
    slate guide motion                the slate-motion skill's SKILL.md ("slate-" may be left off)
    slate guide motion/eases          one reference file of one skill
    slate guide eases                 a reference file, when only one skill has that name
"""

from __future__ import annotations

import argparse
import difflib
import os
from pathlib import Path

from slate import cli, log
from slate.errors import UsageError

PREFIX = "slate-"


def _title(text: str, fallback: str) -> str:
    in_front = text.startswith("---")
    for n, line in enumerate(text.splitlines()):
        if in_front:
            if n > 0 and line.strip() in ("---", "..."):
                in_front = False
            continue
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _short(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) and len(name) > len(PREFIX) else name


def topics(src: str | os.PathLike | None = None) -> list[dict]:
    """Every readable topic: one per SKILL.md and one per reference file, with the names it answers to."""
    from slate import skills_build  # noqa: PLC0415 (keeps `slate --help` fast)

    root = skills_build.skills_src(src)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = folder.name
        text = skill_md.read_text(encoding="utf-8")
        out.append({"topic": name, "skill": name, "kind": "skill", "path": str(skill_md),
                    "title": _title(text, name), "names": sorted({name, _short(name)})})
        ref_dir = folder / "references"
        if ref_dir.is_dir():
            for ref in sorted(p for p in ref_dir.rglob("*.md") if p.is_file() and not p.name.startswith(".")):
                rel = ref.relative_to(ref_dir).with_suffix("").as_posix()
                names = {f"{name}/{rel}", f"{_short(name)}/{rel}"}
                out.append({"topic": f"{name}/{rel}", "skill": name, "kind": "reference", "path": str(ref),
                            "title": _title(ref.read_text(encoding="utf-8"), rel), "names": sorted(names),
                            "short": rel})
    # A bare reference name works when only one skill has it.
    counts: dict[str, int] = {}
    for t in out:
        if t["kind"] == "reference":
            counts[t["short"]] = counts.get(t["short"], 0) + 1
    skill_names = {n for t in out if t["kind"] == "skill" for n in t["names"]}
    for t in out:
        if t["kind"] == "reference" and counts[t["short"]] == 1 and t["short"] not in skill_names:
            t["names"] = sorted(set(t["names"]) | {t["short"]})
    return out


def _normal(topic: str) -> str:
    text = topic.strip().strip("/").lower()
    text = text.replace("/references/", "/")
    return text[:-3] if text.endswith(".md") else text


def resolve(topic: str, src: str | os.PathLike | None = None) -> dict:
    """The one topic a name refers to. Unknown or ambiguous names are a UsageError that suggests close matches."""
    wanted = _normal(topic)
    every = topics(src)
    if not every:
        raise UsageError("There are no guides yet: Slate's skills folder has no skills in it.",
                         hint="The skills live in skills/<name>/ (SKILL.md and references/).")
    hits = [t for t in every if wanted in (n.lower() for n in t["names"])]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise UsageError(f"{topic!r} matches more than one guide: {', '.join(t['topic'] for t in hits)}.",
                         hint="Name the skill too, e.g. slate guide " + hits[0]["topic"])
    names = sorted({n for t in every for n in t["names"]})
    close = difflib.get_close_matches(wanted, names, n=4, cutoff=0.5)
    raise UsageError(f"No guide called {topic!r}.",
                     hint=("Did you mean: " + ", ".join(close) + "?  " if close else "")
                     + "`slate guide` lists every topic.")


def read(topic: str, src: str | os.PathLike | None = None) -> dict:
    """The topic's file, verbatim: {topic, skill, kind, title, path, text}."""
    found = resolve(topic, src)
    text = Path(found["path"]).read_text(encoding="utf-8")
    return {k: found[k] for k in ("topic", "skill", "kind", "title", "path")} | {"text": text}


def listing(src: str | os.PathLike | None = None) -> str:
    every = topics(src)
    if not every:
        return "No guides yet: Slate's skills folder has no skills in it."
    lines = ["Slate's guides, read straight from its skills. Print one with `slate guide <topic>`.", ""]
    width = max(len(min(t["names"], key=len)) for t in every)
    for t in every:
        label = min(t["names"], key=len)
        if t["kind"] == "skill":
            if len(lines) > 2:
                lines.append("")
            lines.append(f"{label:<{width}}  {t['title']}  (the {t['skill']} skill)")
        else:
            lines.append(f"{label:<{width}}  {t['title']}")
    return "\n".join(lines)


def cmd_guide(args: argparse.Namespace) -> int:
    if not args.topic:
        every = topics(args.src)
        log.result({"topics": [{k: t[k] for k in ("topic", "skill", "kind", "title", "names", "path")}
                               for t in every]}, listing(args.src))
        return 0
    found = read(args.topic, args.src)
    if args.path:
        log.result({k: found[k] for k in ("topic", "path")}, found["path"])
        return 0
    log.result(found, found["text"].rstrip("\n"))
    return 0


def register(sub) -> None:
    p = cli.add_command(sub, "guide", cmd_guide, "print Slate's reference guides (the same files the skills use)",
                        description=__doc__,
                        epilog="Exit codes: 0 printed, 2 no such topic (the message suggests close matches).")
    p.add_argument("topic", nargs="?", help="a skill (motion), a reference (motion/eases) or a unique reference name")
    p.add_argument("--path", action="store_true", help="print the file's path instead of its text")
    p.add_argument("--src", metavar="DIR", help=argparse.SUPPRESS)


__all__ = ["register", "topics", "resolve", "read", "listing"]
