"""The films' lessons that live as skill rules are where an agent will meet them, in the words that carry them.

Each lesson below came from knowledge/GAPS.md's skills list: a rule the films paid for that no skill stated yet. A
rule every film needs sits in the skill's SKILL.md (often in its reference too); the rest sit in the reference an agent
opens for that kind of work. Each entry names the file and the key phrases a reader finds there (matched
case-insensitively with whitespace collapsed, the way `slate knowledge check` matches a skill rule's terms), so an edit
that drops one fails here with the lesson's id. The knowledge ledger maps each id to the same file and phrases.
"""

from __future__ import annotations

import re

import pytest

import skills_lint as L

MOTION = "skills/slate-motion/references"
BUILD = "skills/slate-build/references"
CHARACTER = f"{MOTION}/character-and-motif-motion.md"

# lesson id -> [(file, [key phrases]), ...]
LESSONS: dict[str, list[tuple[str, list[str]]]] = {
    # Sending parts away (character-and-motif-motion.md section 16)
    "L-kit-D039": [(CHARACTER, ["the part whose spoke points closest to the target at lift-off", "ties go clockwise",
                                "the ring keeps the gap"])],
    "L-kit-D040": [(CHARACTER, ["an arc that bulges away from the centre",
                                "a quadratic curve whose control point is the chord's midpoint",
                                "Size and colour change by flight progress, never by distance, and the part never "
                                "spins"])],
    "L-kit-D043": [(CHARACTER, ["sort the targets by angle around the centre",
                                "least total angle error, so no two paths cross",
                                "The longest chord sets the flight length"])],
    "L-kit-D044": [(CHARACTER, ["A ring opening around a group of targets", "grows to an ellipse",
                                "holds perfectly still until it goes home"])],
    "L-kit-D080": [(CHARACTER, ["a flight never lifts off before the previous one has seated"])],
    "L-kit-D177": [(CHARACTER, ["Size a flight from its on-screen chord", "the camera's scale included",
                                "Keep a floor so a signature flight stays unhurried"])],
    # The character's drawing, loops, contracts and staging
    "L-kit-D033": [(CHARACTER, ["A fade finishes while the parts are still clear of their overlap"])],
    "L-kit-D036": [(CHARACTER, ["A loop that turns", "turns exactly one symmetry step per cycle",
                                "Never turn it linearly and round to rest afterwards"])],
    "L-kit-D079": [(CHARACTER, ["Every point the character uses",
                                "is in the coordinate space of the layer it's drawn in"])],
    "L-kit-D178": [(CHARACTER, ["Carry it between surfaces on its own curve in screen space",
                                "ends no earlier than any camera move it overlaps",
                                "lands on a fixed target, never a moving point"])],
    "L-kit-D183": [(CHARACTER, ["At a contract frame the mark rests at exactly its rest turn",
                                "a bit-identical contract fails"])],
    # Motion craft
    "L-kit-D005": [(f"{MOTION}/techniques.md", ["The mask morph", "a matte that expands on the entrance ease",
                                                "a track matte with a wider shutter"])],
    "L-kit-D163": [(f"{MOTION}/techniques.md", ["Paper techniques", "Ink behaves like ink",
                                                "broken by the paper's tooth",
                                                "presses on the snap ease and lands its ink in the grain"])],
    "L-kit-D165": [(f"{MOTION}/techniques.md", ["hinges each leaf at the spine and bends it as strips with graded "
                                                "shading", "instead of jumping across the spine"])],
    "L-kit-D074": [(f"{MOTION}/look-development.md", ["In a paper look, a flat colour field that fills the frame",
                                                      "gets the look's paper grain and a soft paper edge"])],
    "L-kit-D099": [(f"{MOTION}/camera.md", ["Overlays on a plate take the plate's own transform",
                                            "A linear push starts and stops off screen"])],
    "L-timing-D040": [(f"{MOTION}/camera.md", ["A move bounded by a later beat shrinks to fit",
                                               "keyed drift points stay at least 0.8 s apart"])],
    "L-kit-D130": [(f"{MOTION}/anti-glitch.md", ["When a layer splits in two along a line",
                                                 "overlap the halves by about 2 px"])],
    "L-kit-D142": [(f"{MOTION}/anti-glitch.md", ["A sheet's edge shadow fades out as the edge reaches the frame's "
                                                 "border"])],
    "L-kit-D175": [(f"{MOTION}/morphs-and-shared-elements.md", ["A container that grows open",
                                                                "clipped to the growing paper",
                                                                "re-rasterises at a new sub-pixel phase every "
                                                                "frame"])],
    "L-qa-D044": [(f"{MOTION}/read-time.md", ["Text smaller than body size is only for labels of three words or "
                                              "fewer"]),
                  ("skills/slate-motion/SKILL.md", ["Smaller text is only for labels of three words or fewer"])],
    "L-qa-D055": [(f"{MOTION}/read-time.md", ["Judge type size by its measured cap height at full resolution",
                                              "42 px names measured 26 px capitals"]),
                  ("skills/slate-motion/SKILL.md", ["judged by its measured cap height at full resolution"])],
    # Building
    "L-kit-D083": [(f"{BUILD}/kit.md", ["Shared product surfaces are timeline-free faces drawn in card pixels",
                                        "0-to-1 progress props from the act's beats"])],
    "L-kit-D084": [(f"{BUILD}/kit.md", ["A surface a character sits in exports that slot",
                                        "never as a second copy"])],
    "L-kit-D085": [(f"{BUILD}/kit.md", ["Every shared surface has a cell in the film's kit review sheet"])],
    "L-kit-D176": [(f"{BUILD}/kit.md", ["A helper that places something against text", "never an assumed offset"])],
    "L-kit-D065": [(f"{BUILD}/contracts.md", ["A moving contract", "start point, start time and constant speed",
                                              "since nothing holds still"])],
    "L-kit-D181": [(f"{BUILD}/contracts.md", ["Gate the rest state on the master frame",
                                              "every motion-blur sub-sample of a contract frame draws the rest "
                                              "picture"])],
    "L-agents-D008": [(f"{BUILD}/builder-briefs.md", ["When an act is handed in, the builder cleans its scratch "
                                                      "folder", "keeps only the final preview and its contact "
                                                      "sheet"])],
    "L-kit-D096": [(f"{BUILD}/headless-chrome.md", ["Centre a lockup", "visible extents and the type's cap height",
                                                    "never on its layout box"])],
    "L-kit-D166": [(f"{BUILD}/headless-chrome.md", ["text-rendering: geometricPrecision",
                                                    "the motion-blur samples, which come from different tabs, double "
                                                    "it"])],
    "L-kit-D168": [(f"{BUILD}/remotion-gotchas.md", ["Mount every component before its first animated frame and "
                                                     "hide it until then"])],
    "L-kit-D170": [(f"{BUILD}/remotion-gotchas.md", ["3D planes that meet edge to edge crack along the seam",
                                                     "about 3 px past its shared edges"])],
    "L-kit-S015": [(f"{BUILD}/remotion-gotchas.md", ["Every rebuild key", "is named and unique among its siblings"])],
    "L-render-D020": [(f"{BUILD}/remotion-gotchas.md", ["To find which frames a change can affect without a browser",
                                                        "render the act's scene components to static markup for "
                                                        "every frame", "pixel-compare only the frames whose markup "
                                                        "changed"])],
    "L-kit-D122": [(f"{BUILD}/app-ui.md", ["Redraw the product's icons from their source geometry",
                                           "never approximations"])],
    "L-story-D047": [(f"{BUILD}/app-ui.md", ["Figures in the voice match the data state on screen",
                                             "One constant in the act picks which reading the film shows"])],
    "L-sound-D121": [(f"{BUILD}/events.md", ["A character's flight that lands with no gesture on it",
                                             "not a `character` row"])],
    # Story, generation and brand
    "L-kit-D073": [("skills/slate-story/references/truth-packs.md", ["The label sits in one corner for the whole film",
                                                                     "move the label once, film-wide"])],
    "L-kit-D167": [("skills/slate-story/references/truth-packs.md", ["lives at the act's top level, fading in with "
                                                                     "the first sample data", "inside a sub-layer it "
                                                                     "goes missing for whole shots"])],
    "L-generate-D055": [("skills/slate-generate/references/plates.md", ["bring it to exactly full frame at 1:1 "
                                                                        "scale"])],
    "L-generate-D056": [("skills/slate-generate/references/plates.md", ["Place type over footage where the plate is "
                                                                        "darkest and least textured",
                                                                        "luma 45 to 85"])],
    "L-kit-D098": [("skills/slate-brand/references/logo-and-motif.md", ["changes colourway exactly at each "
                                                                        "section's moving edge",
                                                                        "each clipped to its own ground"])],
    # QA, sound and voice
    "L-qa-S011": [("skills/slate-qa/references/triage.md", ["any crisp edge moving faster than about 4 px a frame",
                                                            "any zoom faster than about 0.5% a frame",
                                                            "every glitch flag sits inside a scheduled move"])],
    "L-sound-100": [("skills/slate-sound/references/score.md", ["Never name an artist, songwriter, song or label",
                                                                "never ask the music to sound like one, and no "
                                                                "lyrics", "some industries are excluded by the "
                                                                "vendor's terms"]),
                    ("skills/slate-sound/SKILL.md", ["Never name an artist, songwriter, song or label"])],
    "L-sound-D004": [("skills/slate-sound/references/spotting.md", ["A repeating state sounds once",
                                                                    "sounds on its first cycle only; later cycles "
                                                                    "are silent"])],
    "L-sound-S027": [("skills/slate-sound/references/spotting.md", ["Carrying an approved proof onto the real "
                                                                    "picture", "move every cue to the beat the "
                                                                    "picture really has", "trim a file that opens on "
                                                                    "silence", "tie every change to one of the "
                                                                    "owner's own words"])],
    "L-voice-S033": [("skills/slate-sound/references/mix.md", ["A voice change moves the balance",
                                                               "re-solve the mix's rides",
                                                               "a span pinned to rides approved before the polish "
                                                               "can't keep them"]),
                     ("skills/slate-voice/SKILL.md", ["A polish moves the mix too"])],
    "L-voice-S010": [("skills/slate-voice/references/v4-tags.md", ["Passing the next line as context can make a short "
                                                                   "final word read as a continuation",
                                                                   "Dropping the next-line context"])],
    "L-voice-S016": [("skills/slate-voice/references/tag-loop.md", ["Pooling more untagged reads",
                                                                    "after a recalibration, re-run every approved and "
                                                                    "proposed take through the gates"])],
    "L-voice-S036": [("skills/slate-voice/references/polish.md", ["Ask the owner for the target in their own terms "
                                                                  "before tuning", "bracket their middle with two "
                                                                  "strengths"]),
                     ("skills/slate-voice/SKILL.md", ["ask the owner for the target in their own terms"])],
    # The front door
    "L-agents-014": [("skills/slate/references/agents-and-connectors.md", ["Slate announces itself to OpenClaw agents "
                                                                           "through its skill's own name and "
                                                                           "description", "20,000-character injection "
                                                                           "cap"])],
    "L-generate-D014": [("skills/slate/references/budgets.md", ["Cloud API credit expires a year after it's bought",
                                                                "app-plan credits expire every billing cycle"])],
    "L-jobs-004": [("skills/slate/references/budgets.md", ["An optional extra that costs real credits",
                                                           "made only on the owner's yes, even when the budget would "
                                                           "cover it"]),
                   ("skills/slate/SKILL.md", ["An optional extra that costs real credits"])],
    "L-jobs-S001": [("skills/slate/references/resilience.md", ["a self-renewing watchdog in the agent host resumes the "
                                                               "stopped agents", "deletes itself when nothing is in "
                                                               "flight", "Agents from an earlier session can't be "
                                                               "resumed"])],
    "L-voice-S034": [("skills/slate/references/review-and-correct.md", ["Testing a voice change",
                                                                        "on the clip the owner knows best",
                                                                        "rebuilding the approved mix sample for "
                                                                        "sample"])],
}


# The post-02:28 addendum's skill rules (knowledge/harvest/addendum_2026-10-01.json): what the film session learned
# after the second harvest, held the same way.
ADDENDUM: dict[str, list[tuple[str, list[str]]]] = {
    "L-kit-A002": [(f"{BUILD}/remotion-gotchas.md", ["A box that grows or settles slowly is sized by a transform too",
                                                     "counter-scaling its content"])],
    "L-qa-A001": [("skills/slate-qa/references/triage.md", ["Before calling a flag a fault, check it against a "
                                                           "synthetic perfect render", "the flag is the design"])],
    "L-qa-A002": [("skills/slate-qa/references/triage.md", ["Take the fault's flags out of the baseline before you "
                                                           "re-render the fix"])],
    "L-qa-A003": [("skills/slate-finish/references/versions-and-review.md",
                   ["Every installed act passes its regression and stability checks with no reversal and no wobble",
                    "You've read the 0.5-second contact sheets end to end"])],
    "L-finish-A001": [("skills/slate-finish/references/versions-and-review.md", ["slate review --audio <mix>"])],
    "L-generate-A001": [("skills/slate-generate/references/plates.md", ["slate plates repair <shot>",
                                                                        "puts the frames on a true-time axis"])],
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


@pytest.mark.parametrize("lesson", sorted(LESSONS))
def test_the_films_lessons_are_where_agents_meet_them(lesson):
    missing = []
    for rel, phrases in LESSONS[lesson]:
        text = _norm((L.REPO / rel).read_text(encoding="utf-8"))
        missing += [f"{rel}: {p!r}" for p in phrases if _norm(p) not in text]
    assert not missing, f"{lesson} is no longer stated: " + "; ".join(missing)


def test_every_skills_gap_lesson_has_a_home():
    """The 56 skill-rule lessons GAPS.md listed for the skills, each with a file that exists in a skill."""
    assert len(LESSONS) == 56
    for lesson, homes in LESSONS.items():
        assert homes and all(rel.startswith("skills/") and (L.REPO / rel).is_file() for rel, _ in homes), lesson


def test_rules_every_film_needs_sit_in_skill_md_itself():
    """A rule every film needs is in the SKILL.md an agent reads first, not only in a reference it may never open."""
    for lesson in ("L-jobs-004", "L-sound-100", "L-voice-S033", "L-voice-S036", "L-qa-D044", "L-qa-D055"):
        assert any(rel.endswith("/SKILL.md") for rel, _ in LESSONS[lesson]), lesson


@pytest.mark.parametrize("lesson", sorted(ADDENDUM))
def test_the_addendums_lessons_are_where_agents_meet_them(lesson):
    missing = []
    for rel, phrases in ADDENDUM[lesson]:
        text = _norm((L.REPO / rel).read_text(encoding="utf-8"))
        missing += [f"{rel}: {p!r}" for p in phrases if _norm(p) not in text]
    assert not missing, f"{lesson} is no longer stated: " + "; ".join(missing)
