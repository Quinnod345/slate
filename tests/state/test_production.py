"""The brief (asks verbatim, numbered directions), language as data, the deps fallback, and film set."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.state import _util, deps, film, production

STATE_SRC = Path(__file__).resolve().parents[2] / "src" / "slate" / "state"


def test_directions_are_numbered_and_verbatim(new_film):
    fd = new_film(request="Make the film.", request_source="Test Person")
    text = "The tonal layer is the problem.\nEvery other sound is great."
    assert production.add_direction(fd, text, source="Test Person", supersedes="the synth pads") == 1
    assert production.add_direction(fd, "Keep the wood clicks.", source="Test Person") == 2
    brief = production.load_brief(fd)
    assert [d["n"] for d in brief["directions"]] == [1, 2] and brief["directions"][0]["text"] == text
    prod = (fd / "brief" / "PRODUCTION.md").read_text()
    assert "1. **Test Person" in prod and "supersedes the synth pads" in prod and "2. **Test Person" in prod
    with pytest.raises(UsageError):
        production.add_direction(fd, "  ", source="Test Person")
    with pytest.raises(UsageError):
        production.add_direction(fd, "x", source="")


def test_asks_messages_and_marks(new_film):
    fd = new_film()
    numbers = production.set_request(fd, "1. A 30-second reel\n2. Calm voice\n3. End on the logo",
                                     source="Test Person")
    assert numbers == [1, 2, 3]
    assert [a["text"] for a in production.load_brief(fd)["asks"]] == ["A 30-second reel", "Calm voice",
                                                                        "End on the logo"]
    with pytest.raises(UsageError):
        production.set_request(fd, "another request", source="Test Person")  # the original request is kept once
    later = production.add_ask(fd, "Also: captions in the brand font.", source="Test Person")
    assert later == [4]
    brief = production.load_brief(fd)
    assert brief["messages"][0]["text"] == "Also: captions in the brand font." and brief["messages"][0]["asks"] == [4]
    with pytest.raises(UsageError):
        production.mark(fd, 1, status="done")  # done means verified: evidence required
    production.mark(fd, 1, status="done", evidence="timing/shots.json: 30.0 s")
    assert production.asks_summary(fd)["counts"]["done"] == 1
    with pytest.raises(UsageError):
        production.mark(fd, 99, status="open")
    goal = (fd / "brief" / "GOAL.md").read_text()
    assert "| 1 | A 30-second reel _(split automatically: check the wording)_ | done | timing/shots.json: 30.0 s |" \
        in goal
    assert "**1.** Test Person" in goal


def test_managed_blocks_keep_hand_written_text(new_film):
    fd = new_film(request="Make the film.", request_source="Test Person")
    prod = fd / "brief" / "PRODUCTION.md"
    prod.write_text(prod.read_text() + "\n## Notes from the edit bay\n\nKeep this paragraph.\n")
    production.add_direction(fd, "Shorter end card.", source="Test Person")
    text = prod.read_text()
    assert "Keep this paragraph." in text and "Shorter end card." in text
    goal = fd / "brief" / "GOAL.md"
    goal.write_text("# My own goal page\n\nWritten by hand.\n")  # markers gone entirely
    production.render(fd)
    text = goal.read_text()
    assert text.startswith("# My own goal page") and "<!-- slate:begin checklist -->" in text


def test_film_cli_brief_commands(new_film):
    fd = new_film()
    code, out, err = run_cli(["film", "ask", "--request", "Make a reel. Keep it short.", "--source", "Test Person",
                              "--film", str(fd), "--json"])
    assert code == 0, err
    assert json.loads(out)["asks"] == [1, 2]
    code, out, _ = run_cli(["film", "mark", "1", "--status", "done", "--film", str(fd), "--json"])
    assert code == 2
    code, out, _ = run_cli(["film", "mark", "1", "--status", "done", "--evidence", "cut is 20 s", "--film", str(fd),
                            "--json"])
    assert code == 0 and json.loads(out)["status"] == "done"
    code, out, _ = run_cli(["film", "direction", "Use the second take.", "--source", "Test Person", "--film", str(fd),
                            "--json"])
    assert code == 0 and json.loads(out)["direction"] == 1


def test_splitting_and_screening_are_language_data(monkeypatch, new_film):
    assert _util.split_items("One thing. Two things! Three?") == ["One thing.", "Two things!", "Three?"]
    assert _util.split_items("- a\n- b\n  continued") == ["a", "b continued"]
    assert _util.needs_new_approach("Maybe redesign the whole reveal") == "redesign"
    assert _util.needs_new_approach("Bring the click down 2 dB") is None
    tables = {"en": _util._languages()["en"],
              "xx": {"name": "test", "list_item": r"^\s*[>]\s+", "sentence_end": r"[|](\s+)",
                     "new_approach": ["zorp"]}}
    monkeypatch.setattr(_util, "_languages", lambda: tables)
    assert _util.split_items("uno| dos| tres", "xx") == ["uno|", "dos|", "tres"]
    assert _util.needs_new_approach("please zorp it", "xx") == "zorp"
    assert _util.needs_new_approach("please redesign it", "xx") is None  # nothing English is baked into the logic
    assert _util.supported_language("xx") and not _util.supported_language("yy")


def test_deps_fallback_reads_data_files_directly(state_env, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "slate.formats", None)  # as if the formats module weren't built
    monkeypatch.setitem(sys.modules, "slate.brand.pack", None)
    fmt_file = tmp_path / "fallback-reel.json"
    fmt_file.write_text(json.dumps({"name": "fallback-reel", "size": "540x960", "fps": "24/1", "runtime_s": 20}))
    fmt, src = deps.format_load(str(fmt_file))
    assert src.endswith("fallback-reel.json")
    d = film.format_defaults(fmt)
    assert (d["width"], d["height"], d["aspect"], d["fps_num"], d["fps_den"]) == (540, 960, "9:16", 24, 1)
    assert deps.brand_find("open-brand") == state_env["brands"] / "open-brand"
    pack = deps.brand_load("onboarded-brand")
    assert deps.brand_references_approved(pack) and deps.brand_missing_references(deps.brand_load("open-brand"))
    fd = film.create("fallback", brand="open-brand", format=str(fmt_file), git=False)
    assert film.load(fd)["width"] == 540
    with pytest.raises(UsageError):
        deps.format_load("not-installed-anywhere")
    with pytest.raises(UsageError):
        film.format_defaults({"name": "no-size", "fps": 24})
    with pytest.raises(UsageError):
        film.format_defaults({"name": "no-clock", "width": 640, "height": 360})


def test_film_set_rules(new_film):
    fd = new_film()
    out = film.set_value(fd, "targets.runtime_s", 75)
    assert out == {"key": "targets.runtime_s", "old": 90, "new": 75}
    film.set_value(fd, "title", "A better title")
    film.set_value(fd, "budget.elevenlabs", 4000)
    assert film.load(fd)["budget"]["elevenlabs"] == 4000
    for key in ("id", "pins", "parent", "derivation", "language", "versions"):
        with pytest.raises(Refused):
            film.set_value(fd, key, "x")
    with pytest.raises(UsageError):
        film.set_value(fd, "made_up_key", 1)
    with pytest.raises(UsageError):
        film.set_value(fd, "mode", "turbo")
    film.set_value(fd, "format", "fixture-reel")  # nothing made yet: the format's defaults come with it
    assert film.load(fd)["aspect"] == "9:16"
    with pytest.raises(UsageError):
        film.set_value(fd, "generation", "loads")  # schema-checked
    code, out, _ = run_cli(["film", "set", "targets.lufs", "-16", "--film", str(fd), "--json"])
    assert code == 0 and json.loads(out)["new"] == -16
    code, out, _ = run_cli(["film", "show", "--film", str(fd), "--json"])
    assert code == 0 and json.loads(out)["film"]["targets"]["lufs"] == -16
    assert film.new_version(fd, "after the first review") == 2 and film.current_version(fd) == 2


def test_no_size_or_rate_literals_and_no_company_material():
    literal = re.compile(r"(?<![\d.])(1920|1080)(?![\d])|30000\s*[/,]\s*1001")
    company = re.compile(r"TrueSpace|Vixa|Trudy|\bEOS\b|Inner Echo", re.I)
    offenders = []
    for path in sorted(STATE_SRC.rglob("*")):
        if path.suffix not in (".py", ".json"):
            continue
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if company.search(line):
                offenders.append(f"{path.name}:{n}: company material: {line.strip()[:80]}")
            if path.suffix == ".py" and literal.search(line):
                offenders.append(f"{path.name}:{n}: size/rate literal: {line.strip()[:80]}")
    for schema in ("film", "film_lock", "approvals"):
        text = (STATE_SRC.parent / "schemas" / f"{schema}.schema.json").read_text()
        assert not company.search(text), schema
    assert offenders == []
    assert jsonio.validate({}, "film")  # the schemas load and reject an empty film


def test_dry_run_changes_nothing(new_film):
    fd = new_film(request="Make the film.", request_source="Test Person")
    before = {p: p.read_bytes() for p in fd.rglob("*") if p.is_file() and ".git" not in p.parts and ".slate" not in p.parts}
    for argv in (["film", "pin", "tts", "m1"], ["film", "repin", "tts", "m2", "--approved-by", "X", "--reason", "y"],
                 ["film", "ask", "More please.", "--source", "X"], ["film", "mark", "1", "--status", "blocked"],
                 ["film", "direction", "Shorter.", "--source", "X"], ["film", "park", "A or B?", "--source", "agent"],
                 ["film", "answer", "q1", "--by", "X", "--text", "A"], ["film", "set", "title", "New"],
                 ["approve", "checklist", "brief/GOAL.md", "--by", "X"], ["revise", "start", "brief/GOAL.md", "--notes",
                                                                          "x", "--by", "X"],
                 ["revise", "check"], ["revise", "done"], ["status", "--write"]):
        code, out, err = run_cli([*argv, "--film", str(fd), "--dry-run", "--json"])
        assert code == 0, (argv, out, err)
        assert json.loads(out).get("dry_run") is True or argv[0] == "status", argv
    after = {p: p.read_bytes() for p in fd.rglob("*") if p.is_file() and ".git" not in p.parts and ".slate" not in p.parts}
    assert before == after
