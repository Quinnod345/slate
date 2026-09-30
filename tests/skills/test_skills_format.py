"""The ten skills are in the open Agent Skills format: spec-only frontmatter, SKILL.md under 500 lines, real references.

PLAN §1's authoring rules, so the same files work in Claude Code, Codex and OpenClaw.
"""

from __future__ import annotations

import re

import pytest

import skills_lint as L


def test_the_ten_skills_are_there_and_nothing_else():
    found = sorted(p.name for p in L.SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
    assert found == sorted(L.SKILLS), f"skills/ holds {found}; the plan names exactly {sorted(L.SKILLS)}"
    for name in L.SKILLS:
        assert L.skill_md(name).is_file(), f"skills/{name}/SKILL.md is missing"


@pytest.mark.parametrize("name", L.SKILLS)
def test_frontmatter_is_spec_only(name):
    fm = L.parse_frontmatter(L.read(L.skill_md(name)))
    assert not fm.problems, f"skills/{name}/SKILL.md: " + "; ".join(fm.problems)
    extra = set(fm.fields) - L.SPEC_FIELDS
    assert not extra, f"skills/{name}: frontmatter fields the spec doesn't define: {sorted(extra)}"
    # Slate's own skills carry exactly the two required fields: the name and the announcement.
    assert set(fm.fields) == {"name", "description"}, f"skills/{name}: frontmatter is {sorted(fm.fields)}"


@pytest.mark.parametrize("name", L.SKILLS)
def test_name_matches_its_folder_and_the_spec(name):
    fm = L.parse_frontmatter(L.read(L.skill_md(name)))
    value = fm.fields.get("name", "")
    assert value == name, f"skills/{name}: name is {value!r}; the spec wants the folder's name"
    assert L.NAME_RE.match(value) and len(value) <= L.MAX_NAME


@pytest.mark.parametrize("name", L.SKILLS)
def test_description_is_a_precise_trigger(name):
    fm = L.parse_frontmatter(L.read(L.skill_md(name)))
    desc = fm.fields.get("description", "")
    assert 80 <= len(desc) <= L.MAX_DESCRIPTION, f"skills/{name}: description is {len(desc)} characters"
    # The description is the announcement (Oneiro's AGENTS.md is at its cap and TOOLS.md isn't injected), so it says
    # what the skill does and exactly when to use it.
    assert re.search(r"\bUse (?:it )?(?:when|for)\b", desc), f"skills/{name}: the description never says when to use it"
    assert "\n" not in desc


@pytest.mark.parametrize("name", L.SKILLS)
def test_skill_md_is_under_500_lines(name):
    lines = L.read(L.skill_md(name)).count("\n") + 1
    assert lines < L.MAX_SKILL_LINES, f"skills/{name}/SKILL.md has {lines} lines; the rule is under 500"


@pytest.mark.parametrize("name", L.SKILLS)
def test_every_skill_has_real_reference_files(name):
    refs = L.reference_files(name)
    assert len(refs) >= 4, f"skills/{name} has {len(refs)} reference files; depth belongs in references/"
    for ref in refs:
        text = L.read(ref)
        assert text.startswith("# "), f"{L.rel(ref)} doesn't open with a title"
        body = [ln for ln in text.split("\n") if ln.strip()]
        assert len(body) >= 12, f"{L.rel(ref)} has {len(body)} non-empty lines: too thin to be a reference"
        for stub in ("TODO", "TBD", "lorem ipsum", "coming soon", "FIXME"):
            assert stub not in text, f"{L.rel(ref)} contains {stub!r}: no stubs presented as done"


@pytest.mark.parametrize("name", L.SKILLS)
def test_every_reference_is_linked_from_its_skill(name):
    text = L.read(L.skill_md(name))
    linked = {t.split("#")[0] for _, t in L.links(text)}
    for ref in L.reference_files(name):
        target = f"references/{ref.name}"
        assert target in linked, f"skills/{name}/SKILL.md never links {target}; an agent would never find it"


@pytest.mark.parametrize("path", L.all_docs(), ids=lambda p: L.rel(p))
def test_tables_keep_their_columns(path):
    """A pipe inside inline code still splits a Markdown table cell; every row must match its header's columns."""
    problems, header = [], None
    for n, line in enumerate(L.read(path).split("\n"), 1):
        if not line.startswith("|"):
            header = None
            continue
        cells = re.sub(r"`[^`]*`", "x", line.replace("\\|", "x")).count("|")
        if header is None:
            header = cells
        elif cells != header:
            problems.append(f"line {n} has {cells - 1} columns, its header {header - 1}")
    assert not problems, f"{L.rel(path)}: " + "; ".join(problems)


def test_no_stub_markers_in_any_skill_md():
    for name in L.SKILLS:
        text = L.read(L.skill_md(name))
        for stub in ("TODO", "TBD", "FIXME", "lorem ipsum"):
            assert stub not in text, f"skills/{name}/SKILL.md contains {stub!r}"
