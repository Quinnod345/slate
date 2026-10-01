"""Bundle cleanup everywhere: leaked bundles, webpack caches, film leftovers, and the pre-splice recovery."""

import os
import time

import pytest

from slate import jsonio, paths
from slate.render import chunks, clean, registry


def _age(path, minutes):
    t = time.time() - minutes * 60
    os.utime(path, (t, t))


def _bundle(root, name, minutes):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "bundle.js").write_bytes(b"x" * 1000)
    _age(folder, minutes)
    return folder


def test_leaked_bundles_older_than_three_hours_go(fakes, tmp_path):
    tmp = tmp_path / "tmpdir"
    old = _bundle(tmp, "remotion-webpack-bundle-old", 200)
    young = _bundle(tmp, "remotion-webpack-bundle-young", 30)
    other = _bundle(tmp, "someone-elses-folder", 900)
    removed = clean.sweep_bundles()
    assert [r["path"] for r in removed] == [str(old)] and removed[0]["bytes"] == 1000
    assert not old.exists() and young.exists() and other.exists()


def test_slates_own_bundle_cache_is_swept_too(fakes):
    cache = paths.cache_dir() / "bundles"
    stale = _bundle(cache, "film-abc", 400)
    clean.sweep_bundles()
    assert not stale.exists()


def test_dry_run_removes_nothing(fakes, tmp_path):
    old = _bundle(tmp_path / "tmpdir", "remotion-webpack-bundle-old", 200)
    items = clean.sweep_bundles(dry_run=True)
    assert items[0]["action"] == "would be removed" and old.exists()


def test_remove_bundle_refuses_anything_that_isnt_one(fakes, film, tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    assert clean.remove_bundle(notes) is None and notes.exists()
    assert clean.remove_bundle(film, film_dir=film) is None and film.exists()
    assert clean.remove_bundle(tmp_path / "gone") == 0
    b = _bundle(tmp_path / "tmpdir", "remotion-webpack-bundle-x", 0)
    assert clean.remove_bundle(b) == 1000 and not b.exists()


def test_webpack_caches_nobody_will_read_go(fakes):
    cache = fakes.engine_src / "node_modules" / ".cache" / "webpack"
    stale = _bundle(cache / "default-production", "abc", 30)
    fresh = _bundle(cache / "default-production", "def", 2)
    removed = clean.sweep_caches()
    assert [r["path"] for r in removed] == [str(stale)] and fresh.exists()


def test_sweep_waits_while_a_render_holds_the_slot(fakes, tmp_path):
    old = _bundle(tmp_path / "tmpdir", "remotion-webpack-bundle-old", 500)
    fakes.jobs.busy = True
    report = clean.sweep(None)
    assert report["skipped"] and old.exists()
    report = clean.sweep(None, force=True)
    assert not report["skipped"] and not old.exists()


def test_film_leftovers(fakes, film):
    renders = film / "renders"
    (renders / "chunks" / "act1_00000-00029_done").mkdir(parents=True)
    jsonio.write_atomic(renders / "chunks" / "act1_00000-00029_done" / "plan.json", {"status": "done"})
    unfinished = renders / "chunks" / "act2_00000-00023_open"
    unfinished.mkdir(parents=True)
    jsonio.write_atomic(unfinished / "plan.json", {"status": "running", "options": {"act": 2}})
    (unfinished / "c000_00000-00009.mkv").write_bytes(b"good chunk")
    (unfinished / "c001_00010-00019.part.mkv").write_bytes(b"half")
    (renders / "act1.new.mov").write_bytes(b"unproven")
    (renders / ".splice-act1-1234").mkdir()
    (renders / "patches").mkdir()
    (renders / "patches" / "act1_00003-00005.mov").write_bytes(b"patch")
    items = clean.sweep_film(film)
    gone = {os.path.basename(i["path"]) for i in items}
    assert gone == {"act1_00000-00029_done", "c001_00010-00019.part.mkv", "act1.new.mov", ".splice-act1-1234"}
    assert (unfinished / "c000_00000-00009.mkv").exists()  # resume state stays
    assert (renders / "patches" / "act1_00003-00005.mov").exists()
    assert chunks.unfinished(film)
    everything = clean.sweep_film(film, everything=True)
    assert {os.path.basename(i["path"]) for i in everything} == {"act2_00000-00023_open", "act1_00003-00005.mov"}


def _registered(film, content: bytes):
    f = film / "renders" / "act1.mov"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    registry.register(film, {"act": 1, "engine": "remotion", "file": "renders/act1.mov", "frames": [0, 30],
                             "samples": 16, "sha256": jsonio.sha256(f), "made_at": "2026-09-30T12:00:00+00:00",
                             "bundle_hash": None})
    return f


def test_pre_splice_copy_is_restored_when_the_render_is_gone(fakes, film):
    f = _registered(film, b"original")
    os.replace(f, film / "renders" / "act1.pre-splice.mov")
    [item] = clean.sweep_film(film)
    assert item["action"] == "restored" and f.read_bytes() == b"original"


def test_pre_splice_copy_is_restored_when_the_registry_still_describes_it(fakes, film):
    f = _registered(film, b"original")
    os.replace(f, film / "renders" / "act1.pre-splice.mov")
    f.write_bytes(b"spliced but never registered")
    [item] = clean.sweep_film(film)
    assert item["action"] == "restored" and f.read_bytes() == b"original"


def test_pre_splice_copy_of_a_finished_splice_is_removed(fakes, film):
    f = _registered(film, b"original")
    (film / "renders" / "act1.pre-splice.mov").write_bytes(b"older")
    [item] = clean.sweep_film(film)
    assert item["action"] == "removed" and f.read_bytes() == b"original"
    assert not (film / "renders" / "act1.pre-splice.mov").exists()


@pytest.mark.parametrize("everything", [False, True])
def test_sweep_reports_freed_bytes(fakes, film, tmp_path, everything):
    _bundle(tmp_path / "tmpdir", "remotion-webpack-bundle-a", 400)
    report = clean.sweep(film, everything=everything)
    assert report["freed_bytes"] == 1000 and not report["skipped"]


def test_a_render_stopped_mid_move_gets_the_registered_file_back(fakes, film):
    f = _registered(film, b"registered render")
    os.replace(f, film / "renders" / "act1.previous.mov")
    f.write_bytes(b"new render, never registered")
    [item] = clean.sweep_film(film)
    assert item["action"] == "restored" and f.read_bytes() == b"registered render"
    assert not (film / "renders" / "act1.previous.mov").exists()


def _aged_tree(folder, minutes):
    """A folder with a file inside, everything last touched `minutes` ago (a stopped engine's leftover)."""
    (folder / "Default").mkdir(parents=True)
    (folder / "Default" / "Preferences").write_text("{}")
    for p in (folder / "Default" / "Preferences", folder / "Default", folder):
        _age(p, minutes)
    return folder


def test_the_engines_own_temp_folders_lose_only_what_no_live_chrome_uses(fakes, monkeypatch):
    """Chrome profiles live under Slate's data folder (cache/tmp for Remotion's, cache/chrome for the web renderer's),
    where only Slate sweeps: what a stopped process left a day ago goes; a recent one, or one a running Chrome names
    as its --user-data-dir, stays."""
    tmp, chrome = clean.engine_tmp_roots()
    stale = _aged_tree(tmp / "puppeteer_dev_chrome_profile-stale", 26 * 60)
    fresh = _aged_tree(tmp / "puppeteer_dev_chrome_profile-fresh", 30)
    live = _aged_tree(chrome / "web-live", 30 * 60)
    # a long render's profile: its folder looks old, but Chrome wrote inside it a minute ago
    busy = _aged_tree(chrome / "web-busy", 30 * 60)
    (busy / "Default" / "Preferences").write_text('{"x": 1}')
    monkeypatch.setattr(clean, "_profiles_in_use", lambda: {str(live)})
    removed = {item["path"] for item in clean.sweep_engine_tmp()}
    assert removed == {str(stale)}
    assert not stale.exists() and fresh.exists() and live.exists() and busy.exists()
    # unsure what's running: nothing is removed
    _aged_tree(tmp / "puppeteer_dev_chrome_profile-old2", 26 * 60)
    monkeypatch.setattr(clean, "_profiles_in_use", lambda: {"*"})
    assert clean.sweep_engine_tmp() == []


def test_profiles_in_use_reads_every_running_chromes_user_data_dir(fakes, monkeypatch):
    import subprocess

    out = ("/x/chrome-headless-shell about:blank --no-first-run --remote-debugging-port=0 "
           "--user-data-dir=/Users/me/Library/Application Support/Slate/cache/tmp/puppeteer_dev_chrome_profile-AbC\n"
           "/x/chrome-headless-shell --user-data-dir=/data/cache/chrome/web-1 --headless=old\n"
           "/usr/bin/python3 -m slate render\n")
    monkeypatch.setattr(clean.procs, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, out, ""))
    assert clean._profiles_in_use() == {
        "/Users/me/Library/Application Support/Slate/cache/tmp/puppeteer_dev_chrome_profile-AbC",
        "/data/cache/chrome/web-1",
    }
