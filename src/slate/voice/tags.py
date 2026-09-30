"""Direction with eleven_v4 audio tags, and the rules that keep it safe.

An audio tag is a short instruction in square brackets, placed where the delivery should change:
`[warmly, with a smile in her voice] Hi there.` Tags direct; they never change the words. So:

- **A tagged line must strip back to the script exactly.** `strip(tagged)` removes the tags, and `check()` refuses
  any tagged line whose words differ from the script line (ElevenLabs' own tag writer is told the same).
- **Never ask a voice for less effort or more breath.** Whisper, soft, quiet and gentle tags (and their kin: hushed,
  murmuring, breathy, intimate, calm, a low voice) made the films' narrator sound whispery and a little scary
  (Quinn, 2026-09-30). They are refused before any request is sent, in every film and for every brand. A brand can
  ban more words (`tag_policy.banned_extra`); nothing can un-ban these.
- v4 doesn't support SSML: pauses come from punctuation or a `[pause]` tag.
- Neighbouring-line context (`previous_text` / `next_text`) always carries plain words; a tag belongs to its line.

Emotion comes through a full, clear voice: warm, with a smile, bright, confident, reassuring, sincere,
matter-of-fact. Every tagged take is still measured against the voice's own untagged takes (`slate voice tags ab`).
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable

from slate.errors import Refused
from slate.voice import lang

TAG = re.compile(r"\[[^\[\]]*\]")
PAUSE_TAG = re.compile(r"^\[\s*(?:(?:short|long|brief)\s+)?pause\s*\]$", re.IGNORECASE)
SSML = re.compile(r"<\s*/?\s*(?:break|prosody|emphasis|speak|phoneme|say-as|sub)\b[^>]*>", re.IGNORECASE)

# (label, pattern). Matched case-insensitively inside each tag, so variants are caught too:
# "[whispering]", "[softly]", "[Softening, reflective]", "[quietly hopeful]", "[gently, with care]", "[under her breath]".
BANNED: tuple[tuple[str, str], ...] = (
    ("whisper", r"whisper"),
    ("soft", r"soft"),
    ("quiet", r"quiet"),
    ("gentle", r"gentl"),
    ("hushed", r"hush"),
    ("murmur", r"murmur"),
    ("breath", r"breath"),
    ("intimate", r"intima"),
    ("calm", r"calm"),
    ("low voice", r"\blow(?:er)?\b[^\]]*\bvoice\b"),
)

WHY_BANNED = ("tags that lower vocal effort or add breath make the read sound whispery and scary; give the feeling "
              "through a full, clear voice instead (warm, with a smile, bright, confident, reassuring, sincere)")


def tags_in(text: str) -> list[str]:
    """The audio tags in a line, in order, brackets included."""
    return TAG.findall(text or "")


def strip(text: str) -> str:
    """The words without their tags, with spacing tidied: `"Hello [laughs], friend"` -> `"Hello, friend"`."""
    marked = TAG.sub("\x00", text or "")
    marked = re.sub(r"\s*\x00\s*(?=[,.;:!?…)\]])", "", marked)
    return " ".join(marked.replace("\x00", " ").split())


def banned_patterns(extra: Iterable[str] = ()) -> list[tuple[str, re.Pattern]]:
    out = [(label, re.compile(pat, re.IGNORECASE)) for label, pat in BANNED]
    for word in extra or ():
        word = str(word).strip()
        if word:
            out.append((word, re.compile(re.escape(word), re.IGNORECASE)))
    return out


def banned_in(tagged: str, extra: Iterable[str] = ()) -> list[tuple[str, str]]:
    """[(tag, banned word)] for every tag that asks for less effort or more breath (or a brand's extra bans)."""
    pats = banned_patterns(extra)
    hits = []
    for tag in tags_in(tagged):
        for label, pat in pats:
            if pat.search(tag):
                hits.append((tag, label))
                break
    return hits


def refuse_banned(tagged: str, extra: Iterable[str] = (), *, where: str = "") -> None:
    """Raise Refused when a line carries a banned tag. Called before any request is sent."""
    hits = banned_in(tagged, extra)
    if hits:
        listed = ", ".join(f"{t} ({w})" for t, w in hits)
        raise Refused(f"{where + ': ' if where else ''}refusing banned audio tags {listed}: {WHY_BANNED}.",
                      hint="Swap the tag for a full-voice one (the words stay as written), then run it again.",
                      details={"banned": [t for t, _ in hits]})


def _canon(line: str) -> str:
    return " ".join((line or "").split())


def _word_diff(expected: str, got: str) -> str:
    a, b = expected.split(), got.split()
    parts = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        want, have = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if op == "replace":
            parts.append(f"'{want}' became '{have}'")
        elif op == "delete":
            parts.append(f"'{want}' is missing")
        else:
            parts.append(f"'{have}' was added")
    return "; ".join(parts) or "spacing or punctuation differs"


def check(tagged: str, line: str, *, policy: dict | None = None) -> list[str]:
    """Problems with a tagged line against its script line (empty when it's fine to send).

    Checks: the script line itself is plain; brackets balance; no empty tags; no SSML; no banned tag; the brand's
    tag policy (extra bans, an allowed vocabulary, a per-line cap); and the tagged line strips back to the script
    exactly.
    """
    policy = policy or {}
    problems: list[str] = []
    tagged = tagged or ""
    if tags_in(line):
        problems.append(f"the script line itself carries audio tags {tags_in(line)}; tags belong in the direction, "
                        "not the script")
    bare = TAG.sub("", tagged)
    if "[" in bare or "]" in bare:
        problems.append("unbalanced square brackets: every tag needs an opening [ and a closing ]")
    for tag in tags_in(tagged):
        if not tag[1:-1].strip():
            problems.append("an empty tag []")
    if SSML.search(tagged):
        problems.append("SSML markup: eleven_v4 doesn't support SSML; use punctuation, a line break or a [pause] tag")
    for tag, word in banned_in(tagged, policy.get("banned_extra") or ()):
        problems.append(f"banned tag {tag} ({word}): {WHY_BANNED}")
    allowed = policy.get("allowed")
    if allowed:
        vocab = {a.casefold().strip("[] ") for a in allowed}
        for tag in tags_in(tagged):
            if tag[1:-1].casefold().strip() not in vocab and not PAUSE_TAG.match(tag):
                problems.append(f"tag {tag} isn't in this brand's tag vocabulary")
    cap = policy.get("max_tags_per_line")
    if cap is not None and len(tags_in(tagged)) > int(cap):
        problems.append(f"{len(tags_in(tagged))} tags on one line; this brand allows {cap}")
    stripped, want = strip(tagged), _canon(line)
    if stripped != want:
        problems.append(f"the tagged line doesn't strip back to the script: {_word_diff(want, stripped)}")
    return problems


def require(tagged: str, line: str, *, policy: dict | None = None, where: str = "") -> None:
    """Raise Refused listing every problem `check` finds."""
    problems = check(tagged, line, policy=policy)
    if problems:
        head = f"{where}: " if where else ""
        raise Refused(head + "the direction can't be sent as written:\n  - " + "\n  - ".join(problems),
                      hint="Fix the tagged line (tags change delivery, never words) and run it again.",
                      details={"problems": problems})


def scope_word(tagged: str) -> int | None:
    """Index, in script words, of the first word an emotion tag governs. Pause tags don't count; None if untagged.

    The scope matters when measuring: a tag on the last word can change that word while the line average barely
    moves, so tagged takes are measured from here to the end of the line.
    """
    pos, count = None, 0
    for part in re.split(r"(\[[^\[\]]*\])", tagged or ""):
        if not part.strip():
            continue
        if TAG.fullmatch(part.strip()):
            if pos is None and not PAUSE_TAG.match(part.strip()):
                pos = count
            continue
        count += len(lang.norm_words(part))
    return pos


def spoken_tag_words(tagged: str, script: str, heard_words: Iterable[str]) -> list[str]:
    """Words from a tag that were heard but aren't in the script (a tag read aloud)."""
    script_words = set(lang.norm_words(script))
    tag_words = {w for t in tags_in(tagged) for w in lang.norm_words(t[1:-1])} - script_words
    heard = set(heard_words)
    return sorted(tag_words & heard)
