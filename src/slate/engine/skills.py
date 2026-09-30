"""Remotion's and ElevenLabs' agent skills, fetched into the installed engine (never globally) with Slate's overrides.

Plan, appendix A: remotion-dev/skills (12 skills, no licence file) and elevenlabs/skills (10, MIT) are dependencies:
pinned to a commit, fetched into `<engine>/skills/`, never copied into this repo and never installed globally. Slate
overrides what would pull a film away from its own rules:

- **Voice.** Their examples voice with `eleven_multilingual_v2`; Slate voices every line through `slate voice` on its
  pinned model (registry role `tts`, a film's pin first), so code examples are rewritten to that model.
- **Sound.** Remotion's sound-effects page lists stock and meme sounds from remotion.media and says to search the
  internet for more. None of them is used in a Slate film: sound is designed per brand and locked to the picture's
  events. The list is removed.
- **Studio never auto-opens.** Every `npx remotion studio` gets `--no-open`, and the instructions to open its URL in a
  browser are replaced: previews are stills and renders, or a page opened through Aside.
- **Keys.** ElevenLabs' key setup writes the key into a .env file; in Slate it goes into the Keychain through
  `slate connect elevenlabs`, typed in Terminal.

Every SKILL.md gets a short note pointing at SLATE_OVERRIDES.md, so an agent reading a vendor skill meets Slate's rules
first. The real fetch runs in the install wave (`slate engine skills`); `--dry-run` shows the plan.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import tempfile
from pathlib import Path

from slate import config, jsonio, paths, procs
from slate.errors import MissingPrerequisite, Refused

OVERRIDES_VERSION = 1
LOCK = "skills.lock.json"

# Pinned 2026-09-30 from each repo's main branch (both last committed 2026-09-29). registry.json's tools table can
# override a pin ("remotion-dev/skills": {"commit": …}); re-pinning is a deliberate step, like any other pin.
PINS: dict[str, dict] = {
    "remotion": {
        "repo": "remotion-dev/skills",
        "url": "https://github.com/remotion-dev/skills.git",
        "commit": "a1b09e904a995f8bbf6d3b865fc612eedf7a4c43",
        "committed": "2026-09-29",
        "verified_on": "2026-09-30",
        "licence": None,
        "take": ["skills"],  # the twelve skill folders
        "expect_skills": 12,
        "optional": False,
    },
    "elevenlabs": {
        "repo": "elevenlabs/skills",
        "url": "https://github.com/elevenlabs/skills.git",
        "commit": "279173d974ea5cf0c4c4f91d4e7e65202b406dab",
        "committed": "2026-09-29",
        "verified_on": "2026-09-30",
        "licence": "MIT",
        # the ten skills; not its evals, its maintainers' own skill (.agents/) or mcp.json (the hosted MCP is the chat
        # lane's, configured by the person, never by an engine install)
        "take": ["agents", "dubbing", "music", "setup-api-key", "sound-effects", "speech-engine", "speech-to-text",
                 "text-to-speech", "voice-changer", "voice-isolator", "LICENSE", "README.md"],
        "expect_skills": 10,
        "optional": True,
    },
}

STUDIO_NOTE = ("Slate override: nothing opens a browser. Studio, if it's ever needed, runs with `--no-open` and prints its "
               "URL; previews are stills and renders (`slate render stills`, `slate render`), or a page opened through "
               "Aside.")
PREVIEW_NOTE = ("> **Slate override.** A Slate film isn't previewed in Remotion Studio, and nothing opens a browser. "
                "Previews are stills, contact sheets and renders (`slate render stills`, `slate render`), or a page "
                "opened through Aside. If Studio is ever needed, it runs only with `--no-open`.")
SOUND_NOTE = ("> **Slate override.** This page listed stock and meme sound effects to load from the internet. None of "
              "them is used in a Slate film, and no sound is taken from the internet: a film's sound is designed for "
              "its brand and locked to the picture's events (`slate sfx`, `slate score`, `slate mix`).")
KEY_NOTE = ("> **Slate override.** In Slate the ElevenLabs key goes into the macOS Keychain through "
            "`slate connect elevenlabs`, typed in your own Terminal: never into a chat and never into a .env file in "
            "a film or a repo. Skip the .env steps below.")


def pins() -> dict[str, dict]:
    """The pins, with any override from registry.json's tools table."""
    tools = config.registry().get("tools", {})
    out = {}
    for name, pin in PINS.items():
        override = tools.get(pin["repo"]) or {}
        out[name] = {**pin, **{k: override[k] for k in ("commit", "verified_on") if k in override}}
    return out


def overrides_text(tts_model: str) -> str:
    return "\n".join([
        "# Slate's overrides for these vendor skills",
        "",
        "These skills come from Remotion (remotion-dev/skills) and ElevenLabs (elevenlabs/skills), pinned to a commit",
        "and fetched into Slate's engine folder. Where they and Slate disagree, Slate's rules win:",
        "",
        f"1. **Voice.** Voice every line through `slate voice` on Slate's pinned model (`{tts_model}` today: the film's",
        "   pin first, then registry.json). Their examples have been rewritten to it; never call a text-to-speech API",
        "   directly for a film.",
        "2. **Sound.** No stock or meme sound effects, and no sounds from the internet. Sound is designed per brand and",
        "   locked to the picture's events (`slate sfx`, `slate score`, `slate mix`).",
        "3. **Studio never auto-opens.** Run it only with `--no-open`, and never open its URL in a browser. Previews are",
        "   stills and renders, or a page opened through Aside.",
        "4. **Rendering** goes through `slate render` (true motion blur, ProRes, the renders registry), not",
        "   `npx remotion render`.",
        "5. **Films aren't Remotion projects.** A film's scenes live in its folder and compile through Slate's generated",
        "   entry (`@slate/kit`, `@film/data`); don't scaffold a project, add a remotion.config or install packages.",
        "6. **Keys** go into the Keychain through `slate connect elevenlabs`, typed in Terminal: never into chat or a",
        "   .env file.",
        "",
    ])


_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_STUDIO = re.compile(r"npx remotion studio(?![^\n`]*--no-open)")
_OPEN_URL = re.compile(r"(This will open the Studio in the browser[^\n]*|Open the URL in the browser\.)")
# Lines that send an agent to the Studio or its local URL (checked against the pinned text on 2026-09-30).
_OPEN_STUDIO_LINE = re.compile(r"^[^\n]*\bopen the \[?Remotion Studio\b[^\n]*$", re.M | re.I)
_LOCAL_URL_LINE = re.compile(r"^[^\n]*(?:navigating to[^\n]*localhost|open its exact local URL)[^\n]*$", re.M | re.I)
# Remotion's "make a video" preview section (start Studio, open it in a browser): one Slate note instead.
_PREVIEW_SECTION = re.compile(r"^## Open the preview\b.*?(?=^## |\Z)", re.S | re.M)
_OLD_MODEL = re.compile(r"""((?:model_id|modelId|model-id)["']?\s*[:=]?\s*["']?)eleven_multilingual_v2""")
_STOCK_SOUNDS = re.compile(r"https://remotion\.media/[^\s\"'`)]*\.(?:wav|mp3)|search the internet", re.I)


def _note_for(skill_md: Path, root: Path) -> str:
    rel = Path(*[".."] * (len(skill_md.parent.relative_to(root).parts))) / "SLATE_OVERRIDES.md"
    return (f"> **Slate overrides apply** ([{rel.as_posix()}]({rel.as_posix()})): voice through `slate voice`, no stock "
            "or meme sounds, Studio only with `--no-open`, never a browser.\n\n")


def _after_frontmatter(text: str, insert: str) -> str:
    fm = _FRONTMATTER.match(text)
    cut = fm.end() if fm else 0
    return text[:cut] + "\n" + insert + text[cut:].lstrip("\n")


def apply_overrides(root: Path, *, tts_model: str | None = None) -> list[str]:
    """Apply Slate's overrides to fetched skills under `root` (idempotent). Returns what changed, file by file.

    - sfx.md (a page of stock and meme sounds to load from the internet) keeps only its frontmatter and Slate's note;
    - the preview section that starts Studio and opens it in a browser becomes Slate's note, and any other line that
      opens Studio or its local URL becomes a one-line note; every `npx remotion studio` gets `--no-open`;
    - code examples on the old voice model use Slate's pinned TTS model (a comparison table's rows stay as they are);
    - the key setup starts with Slate's Keychain note, and every SKILL.md with a pointer to SLATE_OVERRIDES.md.
    """
    root = Path(root)
    tts_model = tts_model or config.model("tts")
    changes: list[str] = []
    (root / "SLATE_OVERRIDES.md").write_text(overrides_text(tts_model), encoding="utf-8")
    for md in sorted(root.rglob("*.md")):
        if md.name == "SLATE_OVERRIDES.md":
            continue
        text = md.read_text(encoding="utf-8")
        new = text
        if md.name == "sfx.md" and _STOCK_SOUNDS.search(new):
            fm = _FRONTMATTER.match(new)
            new = (fm.group(0) if fm else "") + "\n" + SOUND_NOTE + "\n"
        new = _PREVIEW_SECTION.sub(lambda _m: "## Open the preview\n\n" + PREVIEW_NOTE + "\n\n", new)
        new = _STUDIO.sub("npx remotion studio --no-open", new)
        new = _OPEN_URL.sub(STUDIO_NOTE, new)
        new = _OPEN_STUDIO_LINE.sub(STUDIO_NOTE, new)
        new = _LOCAL_URL_LINE.sub(STUDIO_NOTE, new)
        new = _OLD_MODEL.sub(lambda m: f"{m.group(1)}{tts_model}", new)
        if md.parent.name == "setup-api-key" and md.name == "SKILL.md" and KEY_NOTE not in new:
            new = _after_frontmatter(new, KEY_NOTE + "\n\n")
        if md.name == "SKILL.md" and "Slate overrides apply" not in new:
            new = _after_frontmatter(new, _note_for(md, root))
        if new != text:
            md.write_text(new, encoding="utf-8")
            changes.append(str(md.relative_to(root)))
    return changes


def destination(version: str | None = None) -> Path:
    """Where skills go: the installed engine for the pinned version (never the repo checkout)."""
    return paths.engine_home(version or config.engine_version()) / "skills"


def plan(version: str | None = None) -> dict:
    dest = destination(version)
    tts = config.model("tts")
    repos = []
    for name, pin in pins().items():
        repos.append({
            "name": name,
            "repo": pin["repo"],
            "commit": pin["commit"],
            "committed": pin["committed"],
            "verified_on": pin["verified_on"],
            "licence": pin["licence"] or "none (depend, never vendor)",
            "optional": pin["optional"],
            "takes": pin["take"],
            "into": str(dest / name),
            "commands": [
                ["git", "clone", "--filter=blob:none", "--no-checkout", pin["url"], "<temp>"],
                ["git", "-C", "<temp>", "checkout", "--detach", pin["commit"]],
            ],
        })
    lock = jsonio.read(dest / LOCK, default=None)
    return {
        "destination": str(dest),
        "engine_installed": (dest.parent / "cli.mjs").is_file(),
        "tts_model": tts,
        "overrides_version": OVERRIDES_VERSION,
        "overrides": [
            f"voice: code examples using eleven_multilingual_v2 are rewritten to {tts} (Slate's pinned TTS model)",
            "sound: Remotion's sfx.md (stock and meme sounds from the internet) keeps only its frontmatter and "
            "Slate's note; sound is designed per brand",
            "studio: every `npx remotion studio` gets --no-open; the 'open the preview' section and every line that "
            "opens Studio or its local URL become Slate's note (stills, renders, or Aside; never a browser)",
            "keys: ElevenLabs' key setup starts with Slate's note (`slate connect elevenlabs`, the Keychain, typed in "
            "Terminal) before any .env step",
            "every SKILL.md starts with a note pointing at SLATE_OVERRIDES.md",
        ],
        "repos": repos,
        "current": lock,
        "up_to_date": bool(lock) and lock.get("overrides_version") == OVERRIDES_VERSION
        and all(lock.get("repos", {}).get(r["name"], {}).get("commit") == r["commit"] for r in repos),
    }


def _copy_take(src_root: Path, take: list[str], dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for item in take:
        src = src_root / item
        if item == "skills" and src.is_dir():
            for child in src.iterdir():
                if child.is_dir():
                    shutil.copytree(child, dest / child.name)
        elif src.is_dir():
            shutil.copytree(src, dest / item)
        elif src.is_file():
            shutil.copy2(src, dest / item)
        else:
            raise Refused(f"The pinned commit has no {item}; the pin and the take list disagree.",
                          hint="Re-pin the skills (update PINS or registry.json) after reading what changed.")


def fetch(version: str | None = None, *, sources: dict[str, str] | None = None) -> dict:
    """Fetch every pinned skills repo into the installed engine and apply the overrides. Returns the lock written.

    `sources` swaps a repo's URL (a local mirror, a test repo); the commit is still the pin.
    """
    dest = destination(version)
    if not (dest.parent / "cli.mjs").is_file():
        raise MissingPrerequisite(
            f"The engine for this version isn't installed ({dest.parent}); skills go into it, never into the repo.",
            hint="Run `slate engine install` first.",
        )
    procs.require("git", hint="Install git (`slate doctor --install` offers it).")
    lock = {"generated": "do not edit by hand: slate engine skills", "overrides_version": OVERRIDES_VERSION,
            "fetched_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"), "repos": {}}
    for name, pin in pins().items():
        url = (sources or {}).get(name, pin["url"])
        with tempfile.TemporaryDirectory(prefix=f"slate-skills-{name}-") as tmp:
            work = Path(tmp) / "repo"
            procs.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(work)], timeout=300, check=True)
            procs.run(["git", "-C", str(work), "checkout", "--quiet", "--detach", pin["commit"]], timeout=300, check=True)
            _copy_take(work, pin["take"], dest / name)
        skills = sorted(p.parent.name for p in (dest / name).rglob("SKILL.md") if p.parent.parent == dest / name)
        lock["repos"][name] = {"repo": pin["repo"], "commit": pin["commit"], "url": url, "skills": skills,
                               "count": len(skills), "expected": pin["expect_skills"]}
    lock["changes"] = apply_overrides(dest)
    jsonio.write_atomic(dest / LOCK, lock)
    return lock
