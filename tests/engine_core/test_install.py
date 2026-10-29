"""Installing the engine into Slate's data folder: sources copied, packages only through `npm ci`, pins kept."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from slate import config, jsonio, paths
from slate.engine import install
from slate.errors import Busy, Refused


def test_ensure_copies_the_sources_and_nothing_else(slate_home):
    dest = install.ensure()
    assert dest == paths.engine_home(config.engine_version())
    for rel in ["cli.mjs", "package.json", "package-lock.json", "tsconfig.json", "remotion/entry.mjs",
                "remotion/render_mb.mjs", "remotion/lib/common.mjs", "remotion/neutral/film.json",
                ".puppeteerrc.cjs"]:
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
    npm.write_text(f"#!/bin/sh\necho \"$PWD $@\" >> {log}\necho \"skip=$PUPPETEER_SKIP_DOWNLOAD\" >> {log}.env\n"
                   "mkdir -p node_modules/remotion && echo '{}' > node_modules/remotion/package.json\n")
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    # Named exactly: slate.procs searches Homebrew first whatever PATH says, so a stand-in can't shadow the real npm.
    monkeypatch.setenv("SLATE_NPM", str(npm))
    dest = install.ensure()
    assert not log.exists()
    install.ensure(npm_ci=True)
    where, *argv = log.read_text().split()
    assert Path(where).resolve() == dest.resolve()
    assert argv == ["ci", "--no-audit", "--no-fund"]
    # puppeteer's own Chrome download is skipped: the web renderer drives the engine's headless shell by path
    assert (tmp_path / "npm.log.env").read_text().split() == ["skip=1"]
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


def _source(tmp_path):
    src = tmp_path / "checkout" / "engine"
    (src / "remotion").mkdir(parents=True)
    (src / "cli.mjs").write_text("// engine entry\n")
    (src / "remotion" / "kit.mjs").write_text("// original kit\n")
    jsonio.write_atomic(src / "package.json", {"version": "test", "dependencies": {"remotion": config.engine_version()}})
    jsonio.write_atomic(src / "package-lock.json", {"lockfileVersion": 3})
    return src


def test_source_freshness_reads_real_files_and_explicit_checkout(slate_home, tmp_path):
    src = _source(tmp_path)
    before = install.plan(source=src)
    assert not before["sources_match"] and not Path(before["destination"]).exists()
    dest = install.ensure(source=src)
    assert install.plan(source=src)["sources_match"]
    receipt = (dest / install.MARKER).read_bytes()
    (dest / "remotion" / "kit.mjs").write_text("// changed behind the receipt\n")
    assert not install.plan(source=src)["sources_match"]
    assert (dest / install.MARKER).read_bytes() == receipt  # checking doesn't repair or rewrite anything
    install.ensure(source=src)
    (dest / "remotion" / "removed.mjs").write_text("// stale module\n")
    assert not install.plan(source=src)["sources_match"]
    install.ensure(source=src)
    assert install.plan(source=src)["sources_match"]
    (src / "remotion" / "kit.mjs").write_text("// updated checkout\n")
    assert not install.plan(source=src)["sources_match"]
    install.ensure(source=src)
    assert (dest / "remotion" / "kit.mjs").read_text() == "// updated checkout\n"


def test_engine_refresh_waits_for_every_render_slot(slate_home, tmp_path, monkeypatch):
    from slate.jobs import locks

    src = _source(tmp_path)
    dest = install.ensure(source=src)
    before = install.source_sha256(dest)
    (src / "cli.mjs").write_text("// next engine\n")
    slots = locks.slots
    monkeypatch.setattr(locks, "slots", lambda resource: 2 if resource == "render" else slots(resource))
    with locks.hold("render", timeout=0, owner="active render"):
        with pytest.raises(Busy, match="all render work"):
            install.ensure(source=src)
        assert install.source_sha256(dest) == before
        assert len(locks.holders("render")) == 1  # the failed refresh released the spare slot
    install.ensure(source=src)
    assert install.plan(source=src)["sources_match"]
    assert locks.holders("render") == []


def test_failed_source_swap_restores_engine_packages_and_skills(slate_home, tmp_path, monkeypatch):
    src = _source(tmp_path)
    dest = install.ensure(source=src)
    for rel in ("node_modules/remotion/package.json", "skills/vendor/SKILL.md"):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text("preserved")
    before = install.source_sha256(dest)
    receipt = (dest / install.MARKER).read_bytes()
    (src / "cli.mjs").write_text("// next engine\n")
    replace = install.os.replace

    def fail_on_new_kit(source, target):
        if Path(source).parent.name == "new" and Path(source).name == "remotion":
            raise OSError("simulated interrupted source swap")
        return replace(source, target)

    monkeypatch.setattr(install.os, "replace", fail_on_new_kit)
    with pytest.raises(OSError, match="interrupted source swap"):
        install.ensure(source=src)
    assert install.source_sha256(dest) == before
    assert (dest / install.MARKER).read_bytes() == receipt
    assert (dest / "node_modules/remotion/package.json").read_text() == "preserved"
    assert (dest / "skills/vendor/SKILL.md").read_text() == "preserved"
    assert not list(dest.parent.glob(".slate-engine-*"))


def test_failed_npm_cannot_leave_partial_packages_selected_as_complete(slate_home, tmp_path, monkeypatch):
    from slate.engine import bridge
    from slate.errors import MissingPrerequisite

    src = _source(tmp_path)
    dest = install.ensure(source=src)
    before = install.source_sha256(dest)
    (src / "cli.mjs").write_text("// next engine\n")
    monkeypatch.setenv("SLATE_NPM", "fixture-npm")

    def interrupted(argv, *, cwd, **kwargs):
        remotion = Path(cwd) / "node_modules/remotion/package.json"
        remotion.parent.mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(remotion, {"version": config.engine_version()})
        raise Refused("npm stopped after writing only part of node_modules")

    monkeypatch.setattr(install.procs, "run", interrupted)
    with pytest.raises(Refused, match="npm stopped"):
        install.ensure(source=src, npm_ci=True)
    assert install.source_sha256(dest) == before
    assert not install.is_complete(dest)
    assert not install.plan(source=src)["packages_installed"]
    assert install.marker(dest)["packages_ready"] is False
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(dest))
    with pytest.raises(MissingPrerequisite, match="did not finish"):
        bridge.engine_dir()
    monkeypatch.delenv("SLATE_ENGINE_DIR")
    monkeypatch.setattr(paths, "ENGINE_SRC", tmp_path / "no-checkout-engine")
    with pytest.raises(MissingPrerequisite, match="isn't installed"):
        bridge.engine_dir()

    monkeypatch.setattr(install.procs, "run", lambda *args, **kwargs: None)
    install.ensure(source=src, npm_ci=True)
    assert install.is_complete(dest)
    assert install.marker(dest)["packages_ready"] is True
    assert bridge.engine_dir() == dest
