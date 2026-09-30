"""The skills are portable: no Claude-only tool names, no brand or company material, no browser but Aside.

ARCHITECTURE §1 rules 1, 2, 4 and 12. The same files are read by Claude Code, Codex, OpenClaw and Oneiro's research
slices, and they're meant to be shareable later.
"""

from __future__ import annotations

import re

import pytest

import skills_lint as L

DOCS = L.all_docs()


@pytest.mark.parametrize("path", DOCS, ids=lambda p: L.rel(p))
def test_no_claude_only_tool_names(path):
    text = L.read(path)
    hits = [name for name in L.CLAUDE_ONLY if name in text]
    assert not hits, f"{L.rel(path)} names agent-specific tools {hits}; say what to do, agent-neutrally"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: L.rel(p))
def test_no_brand_or_company_material(path):
    hits = L.brand_hits(L.read(path))
    assert not hits, f"{L.rel(path)} carries company or brand material: " + "; ".join(
        f"line {n}: {what!r}" for n, what in hits)


def test_the_brand_guard_catches_real_company_material(filmroot):
    """The guard works on hashes, so prove it on the film project's own docs (read-only), which name the company."""
    motion = (filmroot / "MOTION.md").read_text(encoding="utf-8")
    kit = (filmroot / "pipeline" / "r3f" / "KIT_C.md").read_text(encoding="utf-8")
    assert len({w.lower() for _, w in L.brand_hits(motion + kit)}) >= 5, "the guard should find several guarded terms"


def test_the_brand_guard_leaves_neutral_text_alone():
    assert L.brand_hits("A neutral launch film for the demo brand, with its own palette tokens.") == []
    assert L.brand_hits("colour #12AB9F") and L.brand_hits("a file under /Users/someone/film")
    assert all(len(h) == 16 for h in L.GUARDED_LOWER | L.GUARDED_EXACT)


BROWSER_OPENERS = (
    re.compile(r"\bopen\s+(?:-a\s+\S+\s+)?https?://"),       # `open https://…`
    re.compile(r"\bopen\s+-a\s+\"?(?:Safari|Google Chrome|Chrome|Dia|Arc|Firefox)"),
    re.compile(r"\bplaywright\s+(?:open|codegen|test)\b", re.I),
    re.compile(r"remotion\s+studio(?![^\n]*--no-open)", re.I),
)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: L.rel(p))
def test_nothing_tells_an_agent_to_open_a_browser(path):
    text = L.read(path)
    hits = [p.pattern for p in BROWSER_OPENERS if p.search(text)]
    assert not hits, f"{L.rel(path)} would open a browser ({hits}); web steps go through Aside only"


def test_the_front_door_states_the_browser_rule():
    text = L.read(L.skill_md("slate")) + "".join(L.read(p) for p in L.reference_files("slate"))
    assert "Aside" in text and re.search(r"never open[s]? a (?:web )?browser", text, re.I)


@pytest.mark.parametrize("name", L.SKILLS)
def test_parallel_work_is_phrased_for_any_agent(name):
    """Where a skill talks about sub-agents, it gives the fallback for an agent that has none."""
    files = [L.skill_md(name), *L.reference_files(name)]
    for path in files:
        text = L.read(path)
        for m in re.finditer(r"sub-agents?", text):
            window = text[max(0, m.start() - 300): m.end() + 300]
            assert re.search(r"\b(?:if|when|where) (?:you|the agent|your agent) can\b|otherwise", window), (
                f"{L.rel(path)} mentions sub-agents without the agent-neutral fallback near line "
                f"{text.count(chr(10), 0, m.start()) + 1}")
