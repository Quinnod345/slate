"""What each skill must say: the plan's points for the skills (docs/plan_points.json, owner "skills") and the brief.

These are regression guards. Each phrase is a rule the films paid for or a routing decision the owner made; if an edit
drops one, the test names the file and the missing rule. Phrases are matched case-insensitively with whitespace
collapsed, across the skill's SKILL.md and its references unless a test says otherwise.
"""

from __future__ import annotations

import json
import re

import pytest

import skills_lint as L


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def skill_text(name: str, *, refs: bool = True) -> str:
    parts = [L.read(L.skill_md(name))]
    if refs:
        parts += [L.read(p) for p in L.reference_files(name)]
    return _norm("\n".join(parts))


def missing(text: str, phrases: list[str]) -> list[str]:
    return [p for p in phrases if _norm(p) not in text]


# --------------------------------------------------------------------------------------------- the front door

def test_front_door_routing_is_in_skill_md_itself():
    """P057, P059, P261, P324, P370: routing lives in SKILL.md, where every agent reads it first."""
    text = skill_text("slate", refs=False)
    assert not missing(text, [
        "Do you want a full production or a quick generated clip?",  # the one question (P057, P261)
        "alone in its message",
        "Run nothing and write nothing until they answer",
        "Straight to production",                                   # scoped briefs (P057)
        "Don't ask the lane question",
        "higgsfield-generate", "higgsfield-video-explainer",        # the fast lane (P059, P370)
        "Never the fast lane",                                      # never for brand work (P059)
        "use video-verifier",                                       # UI recordings (P324)
        "through Aside",
        "live action", "audio description",                         # out of scope (P370)
    ])


def test_front_door_credits_and_vendor_skills():
    """P058, P214, P263: Higgsfield's skills stay global and need app credits outside Slate; the trigger adjusts."""
    text = skill_text("slate", refs=False)
    assert not missing(text, [
        "Higgsfield's skills stay installed globally",
        "app credits",
        "Inside Slate every generation runs on the Cloud API",
        "Remotion's and ElevenLabs' skills live inside Slate's engine folder, not globally",
        "Slate never deletes a vendor skill",
        "one-time permission",
    ])


def test_front_door_covers_the_brief():
    """The brief's list for the front door: intake, formats, sittings, budgets, modes, tracker, protections, helpers."""
    text = skill_text("slate", refs=False)
    assert not missing(text, [
        "`slate status` first",                          # the first command in any session
        "Capture every ask",                             # goal fidelity
        "re-derive the checklist from the verbatim request",
        "onboarding comes first",                        # a brand with no approved pack (P355)
        "`slate new",
        "Two sittings block a supervised film", "Packets don't block",
        "Budgets and spend", "never buys credits",
        "Supervised", "Express", "Draft", "Autonomous",
        "brief/GOAL.md", "brief/PRODUCTION.md",
        "Perfect, don't replace",
        "detached jobs", "Nothing prompts",
        "Voices:", "People and claims:", "AI labels:", "Company material:", "Spend:", "Browsers:",
        "ponder", "asset-creator", "canvas-design", "frontend-design", "avoid-ai-writing",
        "OpenMontage is AGPL",
        "If you can run sub-agents, build acts in parallel", "otherwise build them in order",  # P020
    ])


def test_front_door_snapshots_the_ledger_at_both_ends_of_a_session_and_reconciles():
    """The jobs builder's request: connector spend bypasses the ledger, so every session starts and ends with a
    balance snapshot under one label, then a reconcile. Checked in SKILL.md itself, where every agent reads it first,
    and against the real CLI (a stage renamed later fails here, not in an agent's session)."""
    text = skill_text("slate", refs=False)
    assert not missing(text, [
        "`slate ledger snapshot --all --session <label>`",
        "At the end, run the same `slate ledger snapshot --all --session <label>` again",
        "`slate ledger reconcile --session <label>`",
        "`slate ledger snapshot --provider higgsfield --balance <n> --session <label>`",
    ])
    budgets = _norm(L.read(L.skill_dir("slate") / "references" / "budgets.md"))
    assert not missing(budgets, ["slate ledger snapshot --all --session <label>        # at the end, with the same label",
                                 "slate ledger reconcile --session <label>", "--provider higgsfield --balance"])
    from slate import cli

    for argv in (["ledger", "snapshot", "--all", "--session", "s1", "--help"],
                 ["ledger", "snapshot", "--provider", "higgsfield", "--balance", "1", "--session", "s1", "--help"],
                 ["ledger", "reconcile", "--session", "s1", "--help"]):
        code, _out, err = cli.run_cli(argv)
        assert code == 0, (argv, err[-300:])


def test_the_why_of_onboarding_is_stated():
    """P355: the first film looked right only after its look came from the brand's own design system."""
    for name in ("slate", "slate-brand"):
        text = skill_text(name)
        assert "four" in text and "rejected" in text and "design system" in text, name
    assert not missing(skill_text("slate-brand", refs=False), [
        "motion law measured from reference films", "the product's own motion tokens"])


def test_descriptions_announce_precisely():
    """P365: AGENTS.md is at its cap and TOOLS.md isn't injected, so each description is the announcement."""
    desc = {n: L.parse_frontmatter(L.read(L.skill_md(n))).fields["description"] for n in L.SKILLS}
    front = _norm(desc["slate"])
    assert not missing(front, ["make, edit, plan or review a video", "explainers", "ads", "reels", "promos",
                               "full production or a quick generated clip", "scoped brief",
                               "Not for screen-recording an app", "video-verifier",
                               "styling or embedding a video element"])
    for name, text in desc.items():
        if name != "slate":
            assert "slate" in text.lower(), f"{name}'s description should say it's for Slate films"


# --------------------------------------------------------------------------------------------- the craft skills

REQUIRED = {
    "slate-story": ["script/script.json", "only hand-edited", "2.6 words a second", "truth pack",
                    "one signature moment per act", "visionary", "treatment", "slate script build",
                    "No invented testimonials"],
    "slate-motion": ["The Browser Company", "never their brand", "Material 3", "IBM Carbon", "HIG",
                     "Illusion of Life", "UX in Motion", "only easing carries over directly", "motion law",
                     "look test", "gesture vocabulary", "glitch catalogue", "read time", "safe areas",
                     "visionary bar", "30 px a frame", "16-frame"],
    "slate-build": ["placeholder", "first working version of every shot", "NOTES.md", "contract",
                    "slate emit-events", "slate check", "If you can run sub-agents", "slate capture ios",
                    "slate capture web", "kit first", "hand-in"],
    "slate-voice": ["whisper, soft, quiet, gentle", "full, clear voice", "strips back to the script exactly",
                    "slate voice cast", "slate voice design", "slate voice tts", "slate voice clean",
                    "slate voice pace", "slate voice align", "slate voice captions", "professional clone",
                    "never a clone", "public figure", "by ear"],
    "slate-sound": ["proof 2", "starting point", "its own palette", "one deliberate sound per kind of interaction",
                    "composed score", "Eleven Music", "slate score plan", "slate mix", "slate verify",
                    "slate sfx", "proof 3", "No chimes", "3-second pre-roll", "90 seconds", "equal-power",
                    "freeze", "gap check", "the voice first"],
    "slate-generate": ["rarely by default", "requester decides how much", "Code first", "real people",
                       "real scene", "stock-style", "at least two takes", "never ship", "best models",
                       "Ground every generation", "matte", "Cloud API", "estimate", "never buys credits"],
    "slate-finish": ["slate render", "--resume", "--from", "--to", "--go", "job queue", "slate master --ae",
                     "slate master --ffmpeg", "derived film", "cut-down", "aspect", "captions", "thumbnails",
                     "manifest last", "not approved for publishing", "slate review", "slate deliver"],
    "slate-qa": ["Only PASS", "UNCHECKED", "STALE", "REVIEW", "slate qa triage", "done means verified",
                 "separate reviewer", "before it parks", "ponder", "review packet", "contact sheet",
                 "slate qa unpark"],
    "slate-brand": ["truth", "look and motion", "voice", "sound", "fonts", "a website", "a guidelines PDF",
                    "an app", "claim authority", "moving", "stills don't count", "SHA-256", "slate brand onboard",
                    "slate analyze"],
}


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_each_craft_skill_carries_its_rules(name):
    gaps = missing(skill_text(name), REQUIRED[name])
    assert not gaps, f"skills/{name} no longer says: {gaps}"


def test_the_effects_engine_is_described_as_gated_not_built():
    text = skill_text("slate-sound")
    assert "refuses until" in text and "proof 3" in text


def test_character_motion_generalizes_the_mark_as_character_method():
    text = _norm(L.read(L.skill_dir("slate-motion") / "references" / "character-and-motif-motion.md"))
    assert not missing(text, ["one clock", "unison", "pairs", "wave", "spoke lock", "whole frames", "8 frames",
                              "at most 30 px a frame", "8 px a frame squared", "two sheets", "split point",
                              "stillness is the default", "c = s + 2", "gesture vocabulary", "acceptance tests"])


# --------------------------------------------------------------------------------------------- voice tags

TAGGED = re.compile(r"`([^`\n]*\[[^\]\n]+\][^`\n]*)`")


def test_every_tag_example_passes_the_voice_modules_own_rules():
    """No skill ever shows a whisper, soft, quiet or gentle tag as an example (slate.voice.tags decides)."""
    tags = pytest.importorskip("slate.voice.tags")
    bad = []
    for path in L.all_docs():
        for m in TAGGED.finditer(L.read(path)):
            hits = tags.banned_in(m.group(1))
            if hits:
                bad.append(f"{L.rel(path)}: {m.group(1)} ({hits})")
    assert not bad, bad


def test_the_voice_skill_shows_allowed_tag_examples():
    tags = pytest.importorskip("slate.voice.tags")
    examples = [m.group(1) for p in (L.skill_md("slate-voice"), *L.reference_files("slate-voice"))
                for m in TAGGED.finditer(L.read(p))]
    assert len(examples) >= 10
    assert all(not tags.banned_in(e) for e in examples)


# --------------------------------------------------------------------------------------------- the method

STAGES = ["Brand onboarding", "Brief", "Truth", "Treatment and script", "Voice", "Timing", "Look test",
          "Kit, builders' brief", "Generated shots", "Score, sound and mix", "Render with true motion blur", "Master",
          "Validate and deliver", "Review and correct"]


def test_method_has_every_stage_with_who_approves_and_its_gate():
    text = L.read(L.METHOD)
    header = next(line for line in text.split("\n") if line.startswith("| #"))
    assert "Who approves" in header and "Machine gate" in header
    rows = [line for line in text.split("\n") if re.match(r"^\| \d+ \|", line)]
    assert [int(r.split("|")[1]) for r in rows] == list(range(14))
    for row, stage in zip(rows, STAGES):
        assert stage in row, f"stage row {row[:40]!r} should be {stage!r}"


def test_method_covers_review_perfect_resilience_modes_protections():
    text = _norm(L.read(L.METHOD))
    assert not missing(text, ["## Review and correct", "## Perfect, don't replace", "## Resilience",
                              "## The four modes", "## Protections", "Supervised", "Express", "Draft", "Autonomous",
                              "first working version of every shot", "NOTES.md", "goals kept in the requester's own words",
                              "truth packs", "look tests", "kit-first", "frame-exact contracts", "review cuts"])


# --------------------------------------------------------------------------------------------- the knowledge ledger

def _knowledge_targets() -> list[tuple[str, str]]:
    """(lesson id, target) for every lesson the knowledge ledger sends to a skill file."""
    files = [L.REPO / "knowledge" / "lessons.json"]
    if not files[0].exists():
        files = [L.REPO / "knowledge" / "harvest" / "doc_rules.json",
                 L.REPO / "knowledge" / "harvest" / "inventory_lessons.json"]
    out = []
    for f in files:
        if not f.exists():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("lessons", [])
        for row in rows:
            dest = row.get("destination") or row.get("proposed_destination") or {}
            if isinstance(dest, dict) and dest.get("type") == "skill_rule":
                target = str(dest.get("target", ""))
                if target.startswith("skills/"):
                    out.append((str(row.get("id")), target.split("#")[0].split(" ")[0]))
    return out


def test_every_skill_rule_in_the_knowledge_ledger_has_its_file():
    targets = _knowledge_targets()
    if not targets:
        pytest.skip("no knowledge ledger or harvest in this checkout")
    gone = sorted({t for _, t in targets if not (L.REPO / t).exists()})
    assert not gone, f"the knowledge ledger sends skill rules to files that don't exist: {gone}"
