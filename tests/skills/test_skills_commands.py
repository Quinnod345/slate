"""Every `slate …` command the skills and docs/METHOD.md cite is in ARCHITECTURE §4, and parses in the real CLI.

§4 is the contract every agent relies on. A skill that cites a stage §4 doesn't promise (say `slate voice tags`) would
send an agent to a command that may be renamed; those steps are described in words and found through `slate status`
and `--help` until §4 lists them.
"""

from __future__ import annotations

import pytest

import skills_lint as L
from slate import cli

CONTRACT = L.section4_contract()
CITATIONS = [c for p in L.all_docs() for c in L.citations(p)]


def test_the_contract_parses():
    assert {"status", "new", "voice", "render", "qa", "brand", "ledger"} <= set(CONTRACT)
    assert CONTRACT["voice"] == frozenset({"cast", "design", "tts", "clean", "pace", "align", "captions"})
    assert CONTRACT["ledger"] is None


def test_the_skills_cite_commands():
    assert len(CITATIONS) >= 100, f"only {len(CITATIONS)} command citations found; the parser may be broken"
    cited = {c.command for c in CITATIONS}
    for must in ("status", "new", "script", "voice", "timing", "render", "qa", "master", "deliver", "review",
                 "brand", "gen", "plates", "score", "mix", "verify", "capture", "check", "emit-events", "ledger"):
        assert must in cited, f"no skill cites `slate {must}`"


def test_every_cited_command_is_in_section_4():
    problems = L.contract_problems(CITATIONS, CONTRACT)
    assert not problems, "\n".join(problems)


def test_the_citation_parser_catches_a_stage_section_4_does_not_promise():
    bad = [L.Citation("x.md", 1, "slate voice tags rules", "voice", "tags"),
           L.Citation("x.md", 2, "slate approve script", "approve", "script"),
           L.Citation("x.md", 3, "slate job wait 12", "job", "wait")]
    assert len(L.contract_problems(bad, CONTRACT)) == 3
    good = [L.Citation("x.md", 1, "slate ledger snapshot --all", "ledger", "snapshot"),
            L.Citation("x.md", 2, "slate qa triage", "qa", "triage"),
            L.Citation("x.md", 3, "slate voice tts", "voice", "tts")]
    assert L.contract_problems(good, CONTRACT) == []


def _registered() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:  # noqa: SLF001 - argparse keeps subparsers here
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            return set(action.choices)
    return set()


def test_cited_commands_parse_where_their_module_is_built():
    """A typo in a cited stage (`slate render still`) fails here for every module that's installed today."""
    registered = _registered()
    pairs = sorted({(c.command, c.stage) for c in CITATIONS if c.command in registered},
                   key=lambda p: (p[0], p[1] or ""))
    assert pairs, "no cited command is registered; is the CLI importable?"
    failures = []
    for cmd, stage in pairs:
        argv = [cmd] + ([stage] if stage else []) + ["--help"]
        code, _out, err = cli.run_cli(argv)
        if code != 0:
            failures.append(f"slate {' '.join(argv)} exited {code}: {err.strip()[-200:]}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("name", L.SKILLS)
def test_each_skill_points_at_real_commands(name):
    cites = [c for p in (L.skill_md(name), *L.reference_files(name)) for c in L.citations(p)]
    assert cites, f"skills/{name} never cites a slate command; skills drive the CLI"
