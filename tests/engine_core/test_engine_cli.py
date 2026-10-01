"""The engine's Slate commands, run the way agents run them: `slate engine …`, `slate emit-events`, `slate check`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from slate import config, gates, paths
from slate.cli import run_cli

FIXTURE = paths.ENGINE_SRC / "tests" / "core" / "fixtures" / "film"


@pytest.fixture
def film(slate_home, monkeypatch):
    """A copy of the engine's neutral fixture film (one act, 30 frames), with a brand pack in the brand store."""
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    dest = slate_home["films"] / "fixture"
    shutil.copytree(FIXTURE, dest)
    brand = slate_home["brands"] / "engine-fixture-brand"
    brand.mkdir()
    (brand / "brand.json").write_text(json.dumps({"name": "engine-fixture-brand", "truth": {"banned_words": ["gratis"]}}))
    return dest


def run(*argv):
    code, out, err = run_cli(list(argv))
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


def test_engine_path_and_status(slate_home, monkeypatch):
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    code, out, _ = run("engine", "path", "--json")
    assert code == 0 and out == {"engine_dir": str(paths.ENGINE_SRC), "source": "repo"}
    code, out, err = run("engine", "status", "--json")
    assert code in (0, 1), err  # 1 only when something is missing (e.g. the browser on a fresh machine)
    assert out["pinned_remotion"] == config.engine_version()
    assert out["remotion"] == config.engine_version() and out["matches_pin"] is True
    assert out["bundles"] == {"folder": str(paths.cache_dir() / "bundles"), "count": 0, "megabytes": 0.0}


def test_engine_install_dry_run_changes_nothing(slate_home):
    code, out, _ = run("engine", "install", "--dry-run", "--json")
    assert code == 0 and out["dry_run"] is True
    assert not Path(out["destination"]).exists()


def test_emit_events_then_check_passes_every_gate(film):
    code, out, err = run("emit-events", "--film", str(film), "--json")
    assert code == 0, err
    assert out["acts"][0]["events"] == 2
    assert (film / "timing" / "visual_events" / "act1.json").is_file()
    code, out, err = run("check", "1", "--film", str(film), "--json")
    assert code == 0, err
    assert out["summary"]["status"] == "PASS"
    assert {g["gate"] for g in out["gates"]} == {f"handin.act1.{n}" for n in
                                                  ("typecheck", "module", "word_misses", "banned_words", "never_claim",
                                                   "literals", "events", "margins", "contracts")}
    report = gates.load_report(film)
    assert report["handin.act1.events"].status == "PASS"
    # the brand pack reached the check: its one banned word was looked for
    assert "the brand's 1 banned words" in report["handin.act1.banned_words"].summary


def test_check_fails_with_exit_1_on_a_banned_word(film):
    (film.parent.parent / "brands" / "engine-fixture-brand" / "brand.json").write_text(
        json.dumps({"name": "engine-fixture-brand", "truth": {"banned_words": ["steady"]}}))
    run("emit-events", "--film", str(film))
    code, out, _ = run("check", "1", "--film", str(film), "--json")
    assert code == 1
    failed = [g for g in out["gates"] if g["status"] != "PASS"]
    assert [g["gate"] for g in failed] == ["handin.act1.banned_words"]
    code, text, _ = run("check", "1", "--film", str(film))
    assert code == 1 and "FAIL      handin.act1.banned_words" in text


def test_check_is_not_a_pass_when_the_brand_pack_is_missing(film):
    shutil.rmtree(film.parent.parent / "brands" / "engine-fixture-brand")
    run("emit-events", "--film", str(film))
    code, out, _ = run("check", "1", "--film", str(film), "--json")
    assert code == 1
    assert {g["gate"]: g["status"] for g in out["gates"]}["handin.act1.banned_words"] == "UNCHECKED"


def test_check_all_and_usage_errors(film):
    run("emit-events", "--film", str(film))
    code, out, _ = run("check", "all", "--film", str(film), "--json")
    assert code == 0 and out["acts"] == [1]
    code, out, _ = run("check", "one", "--film", str(film), "--json")
    assert code == 2 and out["error"] == "usage"
    code, out, _ = run("check", "7", "--film", str(film), "--json")
    assert code == 2 and "no act 7" in out["message"]


def test_emit_events_refuses_an_act_with_no_beats_and_no_silence(film):
    scene = film / "scenes" / "act1" / "index.tsx"
    text = scene.read_text()
    start = text.index("export const events = [")
    end = text.index("];", start) + 2
    scene.write_text(text[:start] + "export const events = [];" + text[end:])
    code, out, _ = run("emit-events", "--film", str(film), "--json")
    assert code == 1 and out["error"] == "refused"
    assert "doesn't say it's silent" in out["message"]


def test_emit_events_dry_run_writes_nothing_and_exits_1_when_it_would_be_refused(film):
    code, out, err = run("emit-events", "--film", str(film), "--dry-run", "--json")
    assert code == 0, err
    assert out["dry_run"] is True
    assert out["acts"][0]["written"] is False and out["acts"][0]["problems"] == []
    assert not (film / "timing" / "visual_events").exists()
    scene = film / "scenes" / "act1" / "index.tsx"
    text = scene.read_text()
    start = text.index("export const events = [")
    scene.write_text(text[:start] + "export const events = [];" + text[text.index("];", start) + 2:])
    code, text, _ = run("emit-events", "--film", str(film), "--dry-run")
    assert code == 1
    assert "act 1: 0 beats → timing/visual_events/act1.json" in text and "(1 problem; dry run, nothing written)" in text
    assert "no beats and doesn't say it's silent" in text
    assert not (film / "timing" / "visual_events").exists()


def test_emit_events_records_what_made_the_file(film):
    code, _, err = run("emit-events", "--film", str(film))
    assert code == 0, err
    lock = json.loads((film / "film.lock.json").read_text())
    entry = lock["files"]["timing/visual_events/act1.json"]
    assert entry["producer"] == "slate emit-events"
    assert set(entry["inputs"]) == {"scenes/act1/index.tsx", "timing/shots.json"}


def _two_acts(film: Path) -> None:
    shots = json.loads((film / "timing" / "shots.json").read_text())
    shots["shots"][1]["act"] = 2
    shots["acts"] = [{"index": 1, "name": "One", "shots": ["S01"]}, {"index": 2, "name": "Two", "shots": ["S02"]}]
    (film / "timing" / "shots.json").write_text(json.dumps(shots))
    scene = film / "scenes" / "act1" / "index.tsx"
    text = scene.read_text()
    start = text.index("export const events = [")
    scene.write_text(text[:start] + "export const silent = 'the first of two acts';" + text[text.index("];", start) + 2:])


def test_check_all_hands_in_web_acts_too_through_the_web_renderer(film, monkeypatch):
    """A web scene is checked like any act (through the open web renderer, in the engine), not skipped."""
    from slate.engine import bridge

    _two_acts(film)
    (film / "scenes" / "act2" / "web").mkdir(parents=True)
    (film / "scenes" / "act2" / "web" / "index.html").write_text("<!doctype html>\n")
    asked = []
    real = bridge.call

    def watching(command, args=None, *, timeout=None):
        if command == "check":
            asked.append(args)
            return {"ok": True, "gates": [], "acts": []}
        return real(command, args, timeout=timeout)

    monkeypatch.setattr(bridge, "call", watching)
    code, out, _ = run("check", "all", "--film", str(film), "--no-contracts", "--json")
    assert asked and asked[0]["acts"] == [1, 2]
    assert out["acts"] == [1, 2] and out["skipped"] == []


def test_banned_words_unknown_is_never_none(film, slate_home):
    """A pack that states no banned-word list is unknown (None: the gate is UNCHECKED), never an empty list (a PASS);
    a pack that says [] means it bans none."""
    from slate.engine import cli as engine_cli

    pack = slate_home["brands"] / "engine-fixture-brand" / "brand.json"
    pack.write_text(json.dumps({"name": "engine-fixture-brand", "truth": {}}))
    assert engine_cli.banned_words(film) is None
    pack.write_text(json.dumps({"name": "engine-fixture-brand"}))
    assert engine_cli.banned_words(film) is None
    pack.write_text(json.dumps({"name": "engine-fixture-brand", "truth": {"banned_words": []}}))
    assert engine_cli.banned_words(film) == []
    pack.write_text(json.dumps({"name": "engine-fixture-brand", "truth": {"banned_words": ["gratis", {"word": "free"}]}}))
    assert engine_cli.banned_words(film) == ["gratis", "free"]


def test_check_with_a_pack_that_states_no_list_is_unchecked_not_passed(film, slate_home):
    pack = slate_home["brands"] / "engine-fixture-brand" / "brand.json"
    pack.write_text(json.dumps({"name": "engine-fixture-brand", "truth": {"sources": []}}))
    run("emit-events", "--film", str(film))
    code, out, _ = run("check", "1", "--film", str(film), "--json")
    assert code == 1
    banned = {g["gate"]: g for g in out["gates"]}["handin.act1.banned_words"]
    assert banned["status"] == "UNCHECKED"


def test_engine_scaffold_starts_a_web_act_from_the_template(film, slate_home):
    _two_acts(film)
    code, out, err = run("engine", "scaffold", "--act", "2", "--engine", "web", "--film", str(film), "--json")
    assert code == 0, err
    assert out["scaffolded"] is True and out["act"] == 2
    web = film / "scenes" / "act2" / "web"
    assert (web / "index.html").is_file() and (web / "scene.ts").is_file()
    # nothing is replaced without --force
    code, out, _ = run("engine", "scaffold", "--act", "2", "--engine", "web", "--film", str(film), "--json")
    assert code == 1 and "already has a web scene" in out["message"]
    # the brand's look picks the engine when --engine isn't given; a Remotion act starts as the kit's placeholder
    code, out, _ = run("engine", "scaffold", "--act", "1", "--film", str(film), "--json")
    assert code == 2 and "placeholder" in out["message"]
    pack = slate_home["brands"] / "engine-fixture-brand" / "brand.json"
    pack.write_text(json.dumps({"name": "engine-fixture-brand", "truth": {"banned_words": []},
                                "look": {"id": "flat", "engine": "web"}}))
    code, out, err = run("engine", "scaffold", "--act", "1", "--film", str(film), "--json")
    assert code == 0, err
    assert (film / "scenes" / "act1" / "web" / "index.html").is_file()
