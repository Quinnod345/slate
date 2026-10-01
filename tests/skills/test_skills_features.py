"""What landed in integration is in the skills, where an agent doing that work will read it.

Each feature below arrived after the skills were first written (the voice polish and analysis port, the stability and
balance port, the engines' integration, the formats and onboarding review, the jobs builder's ledger request and the
phase-1 doctor spike). Each entry names the skill and the phrases that carry it; phrases are matched case-insensitively
with whitespace collapsed, across the skill's SKILL.md and its references unless the entry says SKILL.md alone. The
commands among them are also held to the live CLI by test_skills_commands.py.
"""

from __future__ import annotations

import re

import pytest

import skills_lint as L


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _text(skill: str, *, refs: bool = True) -> str:
    parts = [L.read(L.skill_md(skill))] + ([L.read(p) for p in L.reference_files(skill)] if refs else [])
    return _norm("\n".join(parts))


# feature -> [(skill, SKILL.md only?, [phrases])]
FEATURES: dict[str, list[tuple[str, bool, list[str]]]] = {
    "voice polish, analysis and the tag loop": [
        ("slate-voice", True, ["slate voice polish --try A,B --sheet", "slate voice polish --strength B",
                               "slate voice analyze", "slate voice tags optimize --estimate",
                               "slate voice tags pick", "slate voice tags abc", "slate voice tags adopt",
                               "`slate voice tags rules`", "`slate voice tags ab"]),
    ],
    "render stability: triage order, baselines, references, strips": [
        ("slate-qa", True, ["slate qa stability"]),
        ("slate-qa", False, ["**Planned beats.**", "**The reference render.**", "**The act's baseline.**",
                             "**Limits.**", "slate qa stability baseline --act 3 --by <name>",
                             "slate qa stability reference --act 3", "slate qa stability strip --act 3 --frame",
                             "qa/stability/strips/", "A whole-pixel reversal always fails"]),
        ("slate-motion", True, ["`slate qa stability` watches every act's render"]),
    ],
    "the balance gates, the score level and the score's guard": [
        ("slate-sound", True, ["slate sound balance", "seven balance gates", "18.5 LU under before a 6 dB duck",
                               "the score's own clarity guard"]),
        ("slate-sound", False, ["sound.balance.clarity", "sound.balance.never_over", "sound.balance.texture_air",
                                "sound.balance.accents", "sound.balance.levels", "sound.balance.approved_spans",
                                "sound.planned_stops",
                                "18.5 LU under before a 6 dB duck", "The score's own clarity guard",
                                "`score_guard: false`"]),
    ],
    "spans on events (t is the beat)": [
        ("slate-build", True, ["`t` is always the beat", "(`start` and `dur`, together)"]),
        ("slate-build", False, ["`t` is always **the beat**", "they **end on their beat**: `t = start + dur`"]),
    ],
    "web act hand-ins and the engine scaffold": [
        ("slate-build", True, ["`slate engine scaffold --act <N>`",
                               "A web act hands in through the open web renderer"]),
        ("slate-build", False, ["Handing in a web act", "`handin.act<N>.page`", "`handin.act<N>.network`",
                                "`handin.act<N>.fonts`"]),
    ],
    "motion-trace class, exempt, reason and the even curve": [
        ("slate-build", True, ["`class` (rig, ui, carry or whip)", "`exempt` (`reading_sweep` or `steady`) with its "
                               "`reason`", "the kit's `even` curve"]),
        ("slate-build", False, ["ease: 'even'", "class: 'carry'"]),
        ("slate-motion", True, ["names its `class` (rig, ui, carry or whip)", "the kit's `even` curve"]),
        ("slate-motion", False, ["`exempt`: `reading_sweep`", "the kit knows `even`"]),
    ],
    "formats show, deliver's platform files and onboarding's spend line": [
        ("slate", False, ["`slate formats show <name> [--aspect 9:16]`", "the platform files `slate deliver` makes"]),
        ("slate-finish", False, ["`deliver/vN/platform/`", "A platform that takes a slower clock than the film's"]),
        ("slate-brand", True, ["`slate brand onboard`'s spend line"]),
    ],
    "the ledger snapshot and reconcile at a session's start and end": [
        ("slate", True, ["`slate ledger snapshot --all --session <label>`",
                         "`slate ledger reconcile --session <label>`"]),
    ],
    "the gateway route: paid stages from the OpenClaw gateway go through the job queue": [
        ("slate", True, ["Inside the OpenClaw gateway, every paid stage is a job"]),
        ("slate", False, ["Paid stages from the OpenClaw gateway go through the job queue",
                          "Submit every paid stage as a job, never inline", "`slate job submit`",
                          "`slate doctor --context openclaw-gateway`", "which Slate never edits"]),
    ],
}


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_what_landed_in_integration_is_in_the_skills(feature):
    missing = []
    for skill, alone, phrases in FEATURES[feature]:
        text = _text(skill, refs=not alone)
        where = f"skills/{skill}/SKILL.md" if alone else f"skills/{skill}"
        missing += [f"{where}: {p!r}" for p in phrases if _norm(p) not in text]
    assert not missing, f"{feature}: " + "; ".join(missing)


def test_the_gateway_route_names_real_commands():
    """The stages the gateway route sends to the queue really can go there."""
    from slate import cli

    for argv in (["voice", "tts", "--detach", "--help"], ["gen", "run", "--detach", "--help"],
                 ["job", "submit", "--kind", "tts", "--help"], ["doctor", "--context", "openclaw-gateway", "--help"]):
        code, _out, err = cli.run_cli(argv)
        assert code == 0, (argv, err[-300:])
