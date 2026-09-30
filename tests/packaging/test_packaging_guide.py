"""`slate guide`: topics come straight from the skills' own files, printed verbatim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import guide, paths
from slate.cli import run_cli
from slate.errors import UsageError


@pytest.fixture
def guides(skills_src, monkeypatch):
    """The guide command reads the fixture skills instead of the checkout's."""
    monkeypatch.setattr(paths, "SKILLS_SRC", skills_src)
    return skills_src


def _names(topics):
    return {n: t["topic"] for t in topics for n in t["names"]}


def test_topics_cover_every_skill_and_reference(guides):
    names = _names(guide.topics())
    assert names["demo-front"] == "demo-front"
    assert names["demo-front/intake"] == "demo-front/intake"
    assert names["intake"] == "demo-front/intake"  # unique across skills, so the bare name works
    assert names["eases"] == "demo-craft/eases"
    assert {t["kind"] for t in guide.topics()} == {"skill", "reference"}


def test_a_reference_name_two_skills_share_needs_its_skill(guides):
    """The file exists twice, so the answer names both (with their titles) instead of saying there's no such guide."""
    (guides / "demo-craft" / "references" / "intake.md").write_text("# Craft intake\n")
    names = _names(guide.topics())
    assert "intake" not in names
    with pytest.raises(UsageError, match="'intake' is a guide in 2 skills") as err:
        guide.resolve("intake")
    assert "demo-craft/intake (Craft intake)" in err.value.message
    assert err.value.hint == "slate guide demo-craft/intake or slate guide demo-front/intake"
    assert guide.resolve("demo-craft/intake")["path"].endswith("demo-craft/references/intake.md")
    assert guide.read("demo-craft/intake")["text"] == "# Craft intake\n"


def test_the_real_shared_reference_name_points_at_both_skills():
    """signature-moments.md is in slate-story and slate-motion (the skills builder's note to packaging)."""
    code, out, err = run_cli(["guide", "signature-moments"])
    assert code == 2 and "is a guide in 2 skills" in err
    assert "story/signature-moments" in err and "motion/signature-moments" in err


def test_the_slate_prefix_may_be_left_off(tmp_path, monkeypatch):
    folder = tmp_path / "skills" / "slate-motion"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: slate-motion\ndescription: d\n---\n# Motion\n")
    (folder / "references" / "eases.md").write_text("# Eases\n")
    monkeypatch.setattr(paths, "SKILLS_SRC", tmp_path / "skills")
    assert guide.resolve("motion")["path"].endswith("slate-motion/SKILL.md")
    assert guide.resolve("motion/eases")["topic"] == "slate-motion/eases"
    assert guide.resolve("slate-motion/references/eases.md")["topic"] == "slate-motion/eases"
    assert guide.resolve("MOTION/Eases.md")["topic"] == "slate-motion/eases"


def test_the_text_is_the_file_verbatim(guides):
    found = guide.read("intake")
    assert found["text"] == (guides / "demo-front" / "references" / "intake.md").read_text()
    assert found["title"] == "Intake: the request, word for word"
    assert guide.read("demo-front")["text"] == (guides / "demo-front" / "SKILL.md").read_text()


def test_unknown_topics_suggest_close_matches(guides):
    with pytest.raises(UsageError) as info:
        guide.resolve("intak")
    assert "Did you mean" in (info.value.hint or "") and "intake" in info.value.hint


def test_cli_lists_prints_and_refuses(guides):
    code, out, _ = run_cli(["guide"])
    assert code == 0 and "intake" in out and "Print one with `slate guide <topic>`" in out

    code, out, _ = run_cli(["guide", "eases"])
    assert code == 0 and out.rstrip("\n") == (guides / "demo-craft/references/eases.md").read_text().rstrip("\n")

    code, out, _ = run_cli(["guide", "eases", "--json"])
    data = json.loads(out)
    assert code == 0 and data["topic"] == "demo-craft/eases" and data["text"].startswith("# Eases")

    code, out, _ = run_cli(["guide", "eases", "--path"])
    assert code == 0 and Path(out.strip()) == guides / "demo-craft/references/eases.md"

    code, out, err = run_cli(["guide", "nope-nothing"])
    assert code == 2 and "No guide called" in err and "slate guide" in err

    code, out, _ = run_cli(["guide", "--json"])
    topics = json.loads(out)["topics"]
    assert code == 0 and {"demo-front", "demo-front/intake", "demo-craft/eases"} <= {t["topic"] for t in topics}


def test_no_skills_yet_is_said_plainly(tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(paths, "SKILLS_SRC", tmp_path / "empty")
    code, out, _ = run_cli(["guide"])
    assert code == 0 and "No guides yet" in out
    code, _, err = run_cli(["guide", "motion"])
    assert code == 2 and "no skills in it" in err


def test_the_real_skills_are_readable_through_the_guide():
    """Whatever skills this checkout has, every one of their files is reachable by a guide topic."""
    topics = guide.topics()
    for t in topics:
        assert Path(t["path"]).is_file()
        assert guide.read(t["topic"])["text"] == Path(t["path"]).read_text(encoding="utf-8")
