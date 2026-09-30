"""Vendor skills: pinned, fetched into the installed engine only, and overridden where they'd break Slate's rules."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from slate import config, paths
from slate.cli import run_cli
from slate.engine import install, skills
from slate.errors import MissingPrerequisite

SKILL = "---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n\n{body}\n"


def test_the_pins_are_exact_commits_with_their_dates():
    for name, pin in skills.pins().items():
        assert len(pin["commit"]) == 40 and all(c in "0123456789abcdef" for c in pin["commit"]), name
        assert pin["verified_on"] == "2026-09-30"
    assert skills.pins()["remotion"]["repo"] == "remotion-dev/skills"
    assert skills.pins()["elevenlabs"]["repo"] == "elevenlabs/skills"
    assert "mcp.json" not in skills.pins()["elevenlabs"]["take"]


def test_dry_run_shows_the_plan_and_fetches_nothing(slate_home):
    code, out, err = run_cli(["engine", "skills", "--dry-run", "--json"])
    assert code == 0, err
    plan = json.loads(out)
    assert plan["dry_run"] is True
    assert plan["destination"] == str(paths.engine_home(config.engine_version()) / "skills")
    assert [r["repo"] for r in plan["repos"]] == ["remotion-dev/skills", "elevenlabs/skills"]
    assert plan["repos"][0]["commit"] == skills.PINS["remotion"]["commit"]
    assert plan["repos"][0]["commands"][1][-1] == skills.PINS["remotion"]["commit"]
    assert any("--no-open" in o for o in plan["overrides"])
    assert any("meme" in o for o in plan["overrides"])
    assert any(config.model("tts") in o for o in plan["overrides"])
    assert not Path(plan["destination"]).exists()
    code, out, _ = run_cli(["engine", "skills", "--dry-run"])
    assert code == 0 and "remotion-dev/skills @ a1b09e904a99" in out


def _vendor_tree(root: Path) -> Path:
    """A small tree with the vendor text Slate overrides, laid out as fetched."""
    (root / "remotion-studio").mkdir(parents=True)
    (root / "remotion-studio" / "SKILL.md").write_text(SKILL.format(
        name="remotion-studio", body="Execute:\n\n```bash\nnpx remotion studio --no-open\n```\n\nOpen the URL in the browser."))
    (root / "remotion-best-practices" / "remotion-markup").mkdir(parents=True)
    (root / "remotion-best-practices" / "SKILL.md").write_text(SKILL.format(
        name="remotion-best-practices",
        body="This will open the Studio in the browser or re-focus it.\n\n```bash\nnpx remotion studio\n```"))
    (root / "remotion-best-practices" / "remotion-markup" / "sfx.md").write_text(
        "To include a sound effect:\n\n- `https://remotion.media/whoosh.wav`\n- `https://remotion.media/vine-boom.wav`\n\n"
        "For more sound effects, search the internet. A good resource is somewhere.\n")
    (root / "remotion-best-practices" / "remotion-markup" / "voiceover.md").write_text(
        'body: JSON.stringify({\n  text: "Welcome.",\n  model_id: "eleven_multilingual_v2",\n})\n')
    (root / "setup-api-key").mkdir()
    (root / "setup-api-key" / "SKILL.md").write_text(SKILL.format(name="setup-api-key", body="Put the key in .env."))
    return root


def test_overrides_rewrite_voice_sound_studio_and_keys(tmp_path):
    root = _vendor_tree(tmp_path / "skills")
    changed = skills.apply_overrides(root, tts_model="eleven_v4")
    assert sorted(changed) == sorted([
        "remotion-studio/SKILL.md", "remotion-best-practices/SKILL.md", "remotion-best-practices/remotion-markup/sfx.md",
        "remotion-best-practices/remotion-markup/voiceover.md", "setup-api-key/SKILL.md"])
    router = (root / "remotion-best-practices" / "SKILL.md").read_text()
    assert "npx remotion studio --no-open" in router and "open the Studio in the browser" not in router
    assert router.startswith("---\nname: remotion-best-practices")
    assert "Slate overrides apply" in router and "(../SLATE_OVERRIDES.md)" in router
    studio = (root / "remotion-studio" / "SKILL.md").read_text()
    assert studio.count("npx remotion studio --no-open") == 1 and "--no-open --no-open" not in studio
    assert "Open the URL in the browser." not in studio
    sfx = (root / "remotion-best-practices" / "remotion-markup" / "sfx.md").read_text()
    assert "https://remotion.media/" not in sfx and "search the internet" not in sfx and "Slate override" in sfx
    voice = (root / "remotion-best-practices" / "remotion-markup" / "voiceover.md").read_text()
    assert 'model_id: "eleven_v4"' in voice and "multilingual" not in voice
    assert "slate connect elevenlabs" in (root / "setup-api-key" / "SKILL.md").read_text()
    assert "eleven_v4" in (root / "SLATE_OVERRIDES.md").read_text()
    # idempotent: a second pass changes nothing
    assert skills.apply_overrides(root, tts_model="eleven_v4") == []


def test_fetch_refuses_until_the_engine_is_installed(slate_home):
    with pytest.raises(MissingPrerequisite, match="skills go into it, never into the repo"):
        skills.fetch()
    code, out, err = run_cli(["engine", "skills", "--json"])
    assert code == 3
    assert json.loads(out)["error"] == "missing_prerequisite"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
                        "HOME": str(cwd)})


def _local_repo(root: Path, files: dict[str, str]) -> tuple[Path, str]:
    root.mkdir(parents=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git("init", "-q", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "fixture", cwd=root)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    return root, sha


def test_fetch_checks_out_the_pinned_commits_into_the_installed_engine(slate_home, tmp_path, monkeypatch):
    remotion_repo, r_sha = _local_repo(tmp_path / "remotion", {
        "skills/remotion-studio/SKILL.md": SKILL.format(name="remotion-studio", body="npx remotion studio"),
        "skills/remotion-markup/SKILL.md": SKILL.format(name="remotion-markup", body="markup"),
        "skills/remotion-markup/sfx.md": "- `https://remotion.media/bruh.wav`\n",
        "scripts/sync.ts": "// not a skill\n",
    })
    eleven_repo, e_sha = _local_repo(tmp_path / "eleven", {
        **{f"{n}/SKILL.md": SKILL.format(name=n, body='model_id="eleven_multilingual_v2"') for n in skills.PINS["elevenlabs"]["take"]
           if n not in ("LICENSE", "README.md")},
        "LICENSE": "MIT\n", "README.md": "readme\n", "mcp.json": "{}\n", "evals/run.py": "\n",
        ".agents/skills/x/SKILL.md": SKILL.format(name="x", body="maintainers only"),
    })
    monkeypatch.setitem(skills.PINS, "remotion", {**skills.PINS["remotion"], "commit": r_sha, "expect_skills": 2})
    monkeypatch.setitem(skills.PINS, "elevenlabs", {**skills.PINS["elevenlabs"], "commit": e_sha})
    install.ensure()
    lock = skills.fetch(sources={"remotion": str(remotion_repo), "elevenlabs": str(eleven_repo)})
    dest = skills.destination()
    assert dest == paths.engine_home(config.engine_version()) / "skills"
    assert lock["repos"]["remotion"]["commit"] == r_sha
    assert lock["repos"]["remotion"]["skills"] == ["remotion-markup", "remotion-studio"]
    assert lock["repos"]["elevenlabs"]["count"] == 10
    assert not (dest / "elevenlabs" / "mcp.json").exists()
    assert not (dest / "elevenlabs" / "evals").exists() and not (dest / "elevenlabs" / ".agents").exists()
    assert not (dest / "remotion" / "scripts").exists()
    assert "--no-open" in (dest / "remotion" / "remotion-studio" / "SKILL.md").read_text()
    assert "https://remotion.media/" not in (dest / "remotion" / "remotion-markup" / "sfx.md").read_text()
    assert f'model_id="{config.model("tts")}"' in (dest / "elevenlabs" / "text-to-speech" / "SKILL.md").read_text()
    assert json.loads((dest / skills.LOCK).read_text())["overrides_version"] == skills.OVERRIDES_VERSION
    assert skills.plan()["up_to_date"] is True  # the lock matches the pins in force (this test's commits)
    # the repo checkout never receives vendor skills
    assert not (paths.ENGINE_SRC / "skills").exists()


# --- the overrides against the vendors' real text ---------------------------------------------------------------------
# The vendor text can't be copied into this repo (Remotion's skills carry no licence: depend, never vendor), so this runs
# against local mirrors of the two repos when SLATE_TEST_SKILLS_MIRROR names a folder holding `remotion` and `elevenlabs`
# clones (any clone that has the pinned commits). The install wave runs it before the real fetch.
MIRROR = os.environ.get("SLATE_TEST_SKILLS_MIRROR")


@pytest.mark.skipif(not MIRROR, reason="set SLATE_TEST_SKILLS_MIRROR to clones of remotion-dev/skills and elevenlabs/skills")
def test_overrides_hold_on_the_pinned_vendor_text(slate_home):
    mirror = Path(MIRROR)
    install.ensure()
    lock = skills.fetch(sources={"remotion": str(mirror / "remotion"), "elevenlabs": str(mirror / "elevenlabs")})
    assert lock["repos"]["remotion"]["commit"] == skills.PINS["remotion"]["commit"]
    assert lock["repos"]["remotion"]["count"] == lock["repos"]["remotion"]["expected"] == 12
    assert lock["repos"]["elevenlabs"]["count"] == lock["repos"]["elevenlabs"]["expected"] == 10
    dest = skills.destination()
    problems = []
    for md in sorted(dest.rglob("*.md")):
        if md.name == "SLATE_OVERRIDES.md":
            continue
        rel = md.relative_to(dest)
        text = md.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if "npx remotion studio" in line and "--no-open" not in line:
                problems.append(f"{rel}:{n}: Studio without --no-open")
            low = line.lower()
            for phrase in ("open the url in the browser", "open the studio in the browser", "open the remotion studio",
                           "open the [remotion studio", "localhost", "open its exact local url"):
                if phrase in low:
                    problems.append(f"{rel}:{n}: {phrase!r}")
        if md.name == "sfx.md" and ("remotion.media/" in text or "search the internet" in text):
            problems.append(f"{rel}: stock sounds left")
        if skills._OLD_MODEL.search(text):
            problems.append(f"{rel}: an example still voices on the old model")
        if md.name == "SKILL.md" and "Slate overrides apply" not in text:
            problems.append(f"{rel}: no pointer to SLATE_OVERRIDES.md")
    assert problems == []
    key = (dest / "elevenlabs" / "setup-api-key" / "SKILL.md").read_text(encoding="utf-8")
    assert key.index("slate connect elevenlabs") < key.index(".env")
    # a second pass changes nothing
    assert skills.apply_overrides(dest) == []
