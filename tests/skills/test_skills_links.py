"""Every relative link in the skills and docs/METHOD.md resolves, and a skill's links stay inside its own folder.

Skills are symlinked one by one into each agent's skills folder and zipped for claude.ai, so a link that climbs out of
its skill (`../../docs/…`) resolves in the repo and breaks everywhere else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import skills_lint as L


def _targets(path: Path):
    for line, target in L.links(L.read(path)):
        if L.is_external(target):
            continue
        yield line, target


@pytest.mark.parametrize("path", L.all_docs(), ids=lambda p: L.rel(p))
def test_relative_links_resolve(path):
    problems = []
    for line, target in _targets(path):
        file_part, _, frag = target.partition("#")
        if target.startswith("/") or target.startswith("~"):
            problems.append(f"line {line}: {target} is absolute; links are relative")
            continue
        dest = path if not file_part else (path.parent / file_part)
        if not dest.exists():
            problems.append(f"line {line}: {target} doesn't exist")
            continue
        if frag and dest.suffix == ".md" and frag not in L.anchors(L.read(dest)):
            problems.append(f"line {line}: {target}: no heading #{frag} in {dest.name}")
    assert not problems, f"{L.rel(path)}: " + "; ".join(problems)


@pytest.mark.parametrize("name", L.SKILLS)
def test_skill_links_stay_inside_the_skill(name):
    root = L.skill_dir(name).resolve()
    files = [L.skill_md(name), *L.reference_files(name)]
    problems = []
    for path in files:
        for line, target in _targets(path):
            file_part = target.partition("#")[0]
            if not file_part:
                continue
            dest = (path.parent / file_part).resolve()
            if root not in dest.parents and dest != root:
                problems.append(f"{L.rel(path)}:{line}: {target} leaves skills/{name}/")
    assert not problems, "; ".join(problems)


def test_method_links_into_the_skills_resolve():
    text = L.read(L.METHOD)
    skill_links = [t for _, t in L.links(text) if "skills/" in t]
    assert skill_links, "docs/METHOD.md should point each stage at its skill"
    for target in skill_links:
        assert (L.METHOD.parent / target.partition("#")[0]).exists(), target
