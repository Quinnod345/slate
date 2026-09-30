"""Installing the engine into Slate's data folder: sources copied, packages only through `npm ci`, pins kept."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from slate import config, paths
from slate.engine import install
from slate.errors import Refused


def test_ensure_copies_the_sources_and_nothing_else(slate_home):
    dest = install.ensure()
    assert dest == paths.engine_home(config.engine_version())
    for rel in ["cli.mjs", "package.json", "package-lock.json", "tsconfig.json", "remotion/entry.mjs",
                "remotion/render_mb.mjs", "remotion/lib/common.mjs", "remotion/neutral/film.json"]:
        assert (dest / rel).is_file(), rel
    assert not (dest / "node_modules").exists()
    assert not (dest / "tests").exists()
    marker = install.marker(dest)
    assert marker["version"] == config.engine_version()
    assert marker["lock_sha256"] and "npm_ci_lock_sha256" not in marker
    assert install.installed_versions() == [config.engine_version()]
    assert install.is_complete(dest) is False  # no packages until npm ci


def test_reinstalling_replaces_sources_but_keeps_packages_and_skills(slate_home):
    dest = install.ensure()
    (dest / "remotion" / "stale.mjs").write_text("// removed from the repo since\n")
    (dest / "node_modules" / "remotion").mkdir(parents=True)
    (dest / "node_modules" / "remotion" / "package.json").write_text("{}")
    (dest / "skills").mkdir()
    (dest / "skills" / "skills.lock.json").write_text("{}")
    install.ensure()
    assert not (dest / "remotion" / "stale.mjs").exists()
    assert (dest / "node_modules" / "remotion" / "package.json").is_file()
    assert (dest / "skills" / "skills.lock.json").is_file()
    assert install.is_complete(dest)


def test_ensure_refuses_a_version_the_engine_does_not_pin(slate_home):
    with pytest.raises(Refused, match="pins Remotion 4.0.521, but the engine asked for is 4.0.999"):
        install.ensure("4.0.999")


def test_npm_ci_runs_only_when_asked_and_is_recorded(slate_home, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "npm.log"
    npm = bin_dir / "npm"
    npm.write_text(f"#!/bin/sh\necho \"$PWD $@\" >> {log}\nmkdir -p node_modules/remotion && echo '{{}}' > node_modules/remotion/package.json\n")
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    # Named exactly: slate.procs searches Homebrew first whatever PATH says, so a stand-in can't shadow the real npm.
    monkeypatch.setenv("SLATE_NPM", str(npm))
    dest = install.ensure()
    assert not log.exists()
    install.ensure(npm_ci=True)
    where, *argv = log.read_text().split()
    assert Path(where).resolve() == dest.resolve()
    assert argv == ["ci", "--no-audit", "--no-fund"]
    marker = install.marker(dest)
    assert marker["npm_ci_lock_sha256"] == marker["lock_sha256"]
    assert install.is_complete(dest)
    assert install.plan()["packages_match_lockfile"] is True


def test_plan_describes_the_install_without_doing_it(slate_home):
    p = install.plan()
    assert p["destination"] == str(paths.engine_home(config.engine_version()))
    assert "cli.mjs" in p["files"] and "remotion" in p["folders"]
    assert p["installed"] is False
    assert not Path(p["destination"]).exists()
