"""Every `slate …` command the skills and docs/METHOD.md cite is in ARCHITECTURE §4, and parses in the real CLI.

§4 is the contract every agent relies on, and it lists every command and stage that's built (the test below holds it
to the live CLI both ways). A skill that cites a stage §4 doesn't promise would send an agent to a command that may not
exist; a step that isn't built yet is described in words instead.
"""

from __future__ import annotations

import pytest

import skills_lint as L
from slate import cli

CONTRACT = L.section4_contract()
CITATIONS = [c for p in L.all_docs() for c in L.citations(p)]


def test_the_contract_parses():
    assert {"status", "new", "voice", "render", "qa", "brand", "ledger", "approve", "film", "revise"} <= set(CONTRACT)
    assert {"tags", "tts", "polish", "analyze", "check"} <= CONTRACT["voice"]
    assert CONTRACT["ledger"] == frozenset({"list", "inflight", "reconcile", "snapshot"})
    assert CONTRACT["qa"] is None  # `slate qa [gate ...]` leaves the next word open (gate names, triage, stability)


def _stages(parser) -> set[str] | None:
    for action in parser._actions:  # noqa: SLF001 - argparse keeps subparsers here
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            return set(action.choices)
    return None


def test_section_4_names_every_stage_the_cli_has():
    """Where §4 enumerates a command's stages, they're exactly the stages the CLI registers (both ways), so §4 stays
    the whole surface: a new stage goes into §4 the day it's built, and §4 never promises one that isn't."""
    parser = cli.build_parser()
    top = next(a for a in parser._actions if getattr(a, "choices", None) and isinstance(a.choices, dict)).choices
    assert set(top) <= set(CONTRACT) | {"demo"}, f"commands missing from §4: {sorted(set(top) - set(CONTRACT))}"
    for command, promised in CONTRACT.items():
        if promised is None or command not in top:
            continue
        built = _stages(top[command]) or set()
        assert promised == built, f"§4 promises `slate {command} {'|'.join(sorted(promised))}`, the CLI has " \
                                  f"{'|'.join(sorted(built))}"


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
    bad = [L.Citation("x.md", 1, "slate voice sing", "voice", "sing"),
           L.Citation("x.md", 2, "slate connect github", "connect", "github"),
           L.Citation("x.md", 3, "slate job dance 12", "job", "dance"),
           L.Citation("x.md", 4, "slate teleport", "teleport", None)]
    assert len(L.contract_problems(bad, CONTRACT)) == 4
    good = [L.Citation("x.md", 1, "slate ledger snapshot --all", "ledger", "snapshot"),
            L.Citation("x.md", 2, "slate qa triage", "qa", "triage"),
            L.Citation("x.md", 3, "slate voice tts", "voice", "tts"),
            L.Citation("x.md", 4, "slate voice tags rules", "voice", "tags"),
            L.Citation("x.md", 5, "slate job wait 12", "job", "wait"),
            L.Citation("x.md", 6, "slate approve script", "approve", "script")]
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
