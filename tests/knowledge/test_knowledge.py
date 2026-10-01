"""The knowledge ledger's check (slate.knowledge), on small fixture checkouts and then on the real ledger.

Each fixture is a tiny repo in a temp folder: a harvest, one module, one test file, one skill and a ledger built per
test. The real run at the end checks `slate knowledge check` on this checkout without --run-tests: it may report gaps
(and lessons still pending), so it asserts the report is well-formed, not that it passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import cli, gates, knowledge
from slate.knowledge import Repo

MODULE = '''"""A module the fixture ledger points at."""

MAX_WORDS = 12


def pace_limit() -> float:
    """VO at or under 2.6 words per second."""
    return 2.6


def demo_gate(film_dir):
    """Returns the demo.words gate."""
    return {"gate": "demo.words", "status": "PASS"}
'''

TESTS = '''def test_demo_gate_passes():
    assert 1 + 1 == 2


def test_demo_gate_fails():
    assert 1 + 1 == 3


class TestGrouped:
    def test_inside_a_class(self):
        assert True
'''

SKILL = """# Demo skill

- At most 12 on-screen words per shot (headline and labels together).
- Hero text never enters on opacity alone.
"""


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "checkout"
    _write(root, "knowledge/harvest/inventory_lessons.json",
           json.dumps([{"id": f"L-demo-00{i}", "text": f"lesson {i}"} for i in range(1, 5)]))
    _write(root, "knowledge/harvest/doc_rules.json", json.dumps([{"id": "L-demo-D001", "text": "a doc rule"}]))
    _write(root, "src/slate/demo.py", MODULE)
    _write(root, "tests/test_demo.py", TESTS)
    _write(root, "skills/demo/SKILL.md", SKILL)
    return root


def _lesson(lid: str, dest: dict, **kw) -> dict:
    return {"id": lid, "text": f"text of {lid}", "sources": ["FILMROOT/MOTION.md:1"], "kind": "rule", "area": "demo",
            "gate_type": dest.get("type") == "gate", "destination": dest, **kw}


def good_ledger() -> dict:
    return {"meta": {"harvests": [{"date": "2026-09-30", "sources": ["FILMROOT/MOTION.md"], "items": 5}],
                     "next_harvest": "once films B and C and the p3 sound are approved"},
            "lessons": [
                _lesson("L-demo-001", {"type": "code_default", "file": "src/slate/demo.py", "symbol": "pace_limit",
                                       "line": 6, "terms": ["2.6"]}),
                _lesson("L-demo-002", {"type": "gate", "gate": "demo.words", "file": "src/slate/demo.py",
                                       "symbol": "demo_gate", "line": 11,
                                       "tests": ["tests/test_demo.py::test_demo_gate_passes"]}),
                _lesson("L-demo-003", {"type": "skill_rule", "target": "skills/demo/SKILL.md",
                                       "terms": ["At most 12 on-screen words per shot"]},
                        absorbed=["L-demo-D001"], sources=["FILMROOT/MOTION.md:191", "FILMROOT/QA.md:12"]),
                _lesson("L-demo-004", {"type": "dropped", "reason": "one film's own content"}),
            ]}


def by_name(results) -> dict[str, gates.GateResult]:
    return {r.gate: r for r in results}


def run(repo: Path, ledger: dict, **kw) -> dict[str, gates.GateResult]:
    return by_name(knowledge.check(repo=repo, ledger=ledger, scan=False, **kw))


# ------------------------------------------------------------------------------------------------ the passing case
def test_a_fully_mapped_ledger_passes(repo):
    results = run(repo, good_ledger())
    assert {r.status for r in results.values()} == {"PASS"}, {k: r.summary for k, r in results.items()}
    assert gates.overall(results.values()) == "PASS"
    assert "all 5 harvested items are mapped" in results["knowledge.mapped"].summary
    assert results["knowledge.mapped"].details["merged"] == 1
    assert "1 gates" in results["knowledge.destinations"].summary


def test_every_result_fits_the_gate_result_shape(repo):
    for r in knowledge.check(repo=repo, ledger=good_ledger(), scan=True):
        d = r.to_dict()
        assert d["status"] in gates.STATUSES and d["gate"].startswith("knowledge.")
        assert d["engine"]["name"] and d["summary"]
        json.dumps(d)  # plain JSON all the way down


# ------------------------------------------------------------------------------------------------ what fails
def test_an_unmapped_harvest_item_fails(repo):
    ledger = good_ledger()
    ledger["lessons"] = [x for x in ledger["lessons"] if x["id"] != "L-demo-004"]
    r = run(repo, ledger)["knowledge.mapped"]
    assert r.status == "FAIL" and "L-demo-004" in r.summary
    assert r.details["missing"] == ["L-demo-004"]


def test_a_lesson_without_a_known_destination_is_unmapped(repo):
    ledger = good_ledger()
    ledger["lessons"][3]["destination"] = {"type": "later"}
    r = run(repo, ledger)["knowledge.mapped"]
    assert r.status == "FAIL" and r.details["unmapped"] == ["L-demo-004"]


def test_an_id_listed_twice_fails(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["absorbed"] = ["L-demo-004"]
    r = run(repo, ledger)["knowledge.mapped"]
    assert r.status == "FAIL" and "L-demo-004" in r.details["listed_twice"]


def test_a_gap_fails_until_it_is_closed(repo):
    ledger = good_ledger()
    ledger["lessons"][3]["destination"] = {"type": "gap", "module": "sound",
                                           "change": "add the check to src/slate/sound/verify.py"}
    results = run(repo, ledger)
    assert results["knowledge.gaps"].status == "FAIL"
    assert results["knowledge.gaps"].details["by_module"] == {"sound": 1}
    assert results["knowledge.destinations"].status == "PASS"
    assert gates.overall(results.values()) == "FAIL"


def test_a_pending_destination_fails_until_it_is_re_mapped_and_says_when_it_has_landed(repo):
    ledger = good_ledger()
    ledger["lessons"][3]["destination"] = {"type": "pending", "file": "src/slate/later.py", "symbol": "build",
                                           "module": "sound", "what": "the mix's guards, built in this run"}
    results = run(repo, ledger)
    r = results["knowledge.gaps"]
    assert r.status == "FAIL" and "1 still pending" in r.summary and r.details["pending_landed"] == []
    assert results["knowledge.destinations"].status == "PASS"
    _write(repo, "src/slate/later.py", "def build():\n    return 1\n")
    r = run(repo, ledger)["knowledge.gaps"]
    assert r.status == "FAIL" and r.details["pending_landed"] == ["L-demo-004"] and "re-map them" in r.summary


def test_a_pending_destination_must_name_its_plan(repo):
    ledger = good_ledger()
    ledger["lessons"][3]["destination"] = {"type": "pending", "file": "src/slate/later.py", "module": "sound"}
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "planned file" in r.summary


def test_a_gate_type_lesson_may_wait_on_a_pending_gate(repo):
    ledger = good_ledger()
    ledger["lessons"][1]["destination"] = {"type": "pending", "file": "src/slate/later.py", "symbol": "gate",
                                           "module": "qa", "what": "the gate, built in this run"}
    results = run(repo, ledger)
    assert results["knowledge.destinations"].status == "PASS"
    assert results["knowledge.gaps"].status == "FAIL"


def test_a_gap_must_say_what_closes_it(repo):
    ledger = good_ledger()
    ledger["lessons"][3]["destination"] = {"type": "gap", "module": "sound", "change": ""}
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "change that would close it" in r.summary


def test_a_missing_file_fails(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["destination"]["file"] = "src/slate/gone.py"
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "src/slate/gone.py doesn't exist" in r.summary


def test_a_missing_symbol_fails(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["destination"]["symbol"] = "renamed_away"
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "has no renamed_away" in r.summary


def test_a_code_default_must_hold_its_value(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["destination"]["terms"] = ["3.0"]
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "doesn't hold 3.0" in r.summary


def test_a_skill_rule_the_file_does_not_state_fails(repo):
    ledger = good_ledger()
    ledger["lessons"][2]["destination"]["terms"] = ["At most 9 on-screen words per shot"]
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "doesn't state it" in r.summary


def test_a_skill_rule_must_point_into_skills(repo):
    ledger = good_ledger()
    ledger["lessons"][2]["destination"]["target"] = "docs/NOTES.md"
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "skills/<name>" in r.summary


def test_a_gate_needs_real_tests(repo):
    ledger = good_ledger()
    ledger["lessons"][1]["destination"]["tests"] = ["tests/test_demo.py::test_nothing_like_this"]
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "no test tests/test_demo.py::test_nothing_like_this" in r.summary
    ledger["lessons"][1]["destination"]["tests"] = []
    assert run(repo, ledger)["knowledge.destinations"].status == "FAIL"


def test_a_gate_type_lesson_must_land_on_a_gate(repo):
    ledger = good_ledger()
    ledger["lessons"][2]["gate_type"] = True
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "gate-type lesson" in r.summary


def test_a_gate_enforced_by_construction_needs_its_tests_but_no_gate_name(repo):
    ledger = good_ledger()
    ledger["lessons"][1]["destination"] = {"type": "gate", "gate": "demo.never_named", "file": "src/slate/demo.py",
                                           "symbol": "pace_limit", "enforced": "construction",
                                           "tests": ["tests/test_demo.py::test_demo_gate_passes"]}
    assert run(repo, ledger)["knowledge.destinations"].status == "PASS"
    del ledger["lessons"][1]["destination"]["enforced"]
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "never names the demo.never_named gate" in r.summary
    ledger["lessons"][1]["destination"].update(enforced="construction", tests=[])
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "a gate without tests" in r.summary
    ledger["lessons"][1]["destination"].update(enforced="by hand", tests=["tests/test_demo.py::test_demo_gate_passes"])
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "unknown enforcement" in r.summary


def test_an_also_destination_is_checked_too(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["also"] = [{"type": "code_default", "file": "src/slate/demo.py", "symbol": "nowhere"}]
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "FAIL" and "(also #1)" in r.summary


def test_a_moved_symbol_still_resolves_and_is_reported(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["destination"]["line"] = 99
    r = run(repo, ledger)["knowledge.destinations"]
    assert r.status == "PASS" and r.details["moved"]["L-demo-001"] == {
        "file": "src/slate/demo.py", "symbol": "pace_limit", "was": 99, "now": 6}


def test_every_harvest_file_counts(repo):
    _write(repo, "knowledge/harvest/second_harvest.json",
           json.dumps([{"id": "L-demo-S001", "text": "a lesson from the second harvest"}]))
    r = run(repo, good_ledger())["knowledge.mapped"]
    assert r.status == "FAIL" and r.details["missing"] == ["L-demo-S001"]
    ledger = good_ledger()
    ledger["lessons"][3]["absorbed"] = ["L-demo-S001"]
    r = run(repo, ledger)["knowledge.mapped"]
    assert r.status == "PASS" and r.details["harvested"] == 6 and "knowledge/harvest/second_harvest.json" in r.evidence


def test_without_the_harvest_the_ledger_cannot_be_proved_complete(repo):
    for f in (repo / "knowledge" / "harvest").iterdir():
        f.unlink()
    assert run(repo, good_ledger())["knowledge.mapped"].status == "UNCHECKED"


# ------------------------------------------------------------------------------------------------ the pieces
def test_symbols_resolve_in_every_kind_of_file(repo):
    _write(repo, "engine/kit/eases.ts", "export const glide = 1;\nexport function settle() {}\n")
    _write(repo, "formats/reel.json", json.dumps({"captions": {"max_cps": 17}}))
    _write(repo, "install.sh", "#!/bin/sh\nlink_cli() {\n  :\n}\n")
    r = Repo(repo)
    assert r.symbol("src/slate/demo.py", "pace_limit") == (6, 8)
    assert r.symbol("engine/kit/eases.ts", "settle") == (2, None)
    assert r.symbol("formats/reel.json", "captions.max_cps")[0] == 1
    assert r.symbol("formats/reel.json", "captions.max_lines") is None
    assert r.symbol("install.sh", "link_cli") == (2, None)
    assert r.has_test("tests/test_demo.py::test_demo_gate_passes[a-b]")
    assert r.has_test("tests/test_demo.py::TestGrouped::test_inside_a_class")
    assert not r.has_test("tests/test_demo.py::TestGrouped")


def test_junit_results_map_back_to_node_ids():
    xml = """<testsuites><testsuite>
      <testcase classname="tests.test_demo" file="tests/test_demo.py" line="1" name="test_a"/>
      <testcase classname="tests.test_demo" file="tests/test_demo.py" line="4" name="test_b[x]"><failure/></testcase>
      <testcase classname="tests.test_demo" file="tests/test_demo.py" line="4" name="test_b[y]"/>
      <testcase classname="tests.test_demo.TestGrouped" file="tests/test_demo.py" line="9" name="test_c"><skipped/></testcase>
    </testsuite></testsuites>"""
    got = knowledge.parse_junit(xml)
    assert got == {"tests/test_demo.py::test_a": "passed", "tests/test_demo.py::test_b[x]": "failed",
                   "tests/test_demo.py::test_b[y]": "passed", "tests/test_demo.py::TestGrouped::test_c": "skipped"}
    assert knowledge._outcome_for("tests/test_demo.py::test_b", got) == "failed"
    assert knowledge._outcome_for("tests/test_demo.py::test_a", got) == "passed"
    assert knowledge._outcome_for("tests/test_demo.py::test_z", got) == "missing"


def test_run_tests_runs_every_gate_test_in_one_batch(repo, monkeypatch):
    calls = []
    real = knowledge.run_pytest

    def spy(ids, root, **kw):
        calls.append(list(ids))
        return real(ids, root, **kw)

    monkeypatch.setattr(knowledge, "run_pytest", spy)
    ledger = good_ledger()
    ledger["lessons"].append(_lesson("L-demo-D001", {"type": "gate", "gate": "demo.words",
                                                     "file": "src/slate/demo.py", "symbol": "demo_gate",
                                                     "tests": ["tests/test_demo.py::test_demo_gate_fails"]}))
    ledger["lessons"][2]["absorbed"] = []
    results = run(repo, ledger, run_tests=True)
    assert len(calls) == 1 and sorted(calls[0]) == ["tests/test_demo.py::test_demo_gate_fails",
                                                     "tests/test_demo.py::test_demo_gate_passes"]
    r = results["knowledge.tests"]
    assert r.status == "FAIL" and list(r.details["failing"]) == ["L-demo-D001"]
    ledger["lessons"].pop()
    ledger["lessons"][2]["absorbed"] = ["L-demo-D001"]
    assert run(repo, ledger, run_tests=True)["knowledge.tests"].status == "PASS"


def test_without_run_tests_the_tests_are_named_but_not_run(repo, monkeypatch):
    monkeypatch.setattr(knowledge, "run_pytest", lambda *a, **k: pytest.fail("ran pytest without --run-tests"))
    assert "knowledge.tests" not in run(repo, good_ledger())


def test_components_must_land_on_real_modules(repo):
    doc = {"outside_the_core": {"FILMROOT (left in place)": "stays"}, "statuses": {"ported": "", "dropped": ""},
           "categories": {"film-only": ""}, "counts": {"total": 2},
           "components": [{"id": "ve-000", "category": "film-only",
                           "destination": {"module": "src/slate", "status": "ported", "reason": "the code"}},
                          {"id": "ve-001", "category": "film-only",
                           "destination": {"module": "FILMROOT (left in place)", "status": "dropped",
                                           "reason": "one film's scenes"}}]}
    _write(repo, "knowledge/components.json", json.dumps(doc))
    assert knowledge.gate_components(Repo(repo)).status == "PASS"
    doc["components"][0]["destination"]["module"] = "src/slate/not_there"
    _write(repo, "knowledge/components.json", json.dumps(doc))
    r = knowledge.gate_components(Repo(repo))
    assert r.status == "FAIL" and "src/slate/not_there isn't in the repo" in r.summary


# ------------------------------------------------------------------------------------------------ left behind
NAME = "tru" + "dy"  # guarded names, built from pieces so this file doesn't carry them
OTHER = "vi" + "xa"


def test_nothing_left_behind_passes(repo):
    r = knowledge.gate_left_behind(Repo(repo), film_root=None)
    assert r.status == "PASS", r.details["hits"]


def test_a_left_behind_file_is_found_by_name(repo):
    _write(repo, "scripts/work.sh", "#!/bin/sh\necho render elsewhere\n")
    r = knowledge.gate_left_behind(Repo(repo), film_root=None)
    assert r.status == "FAIL" and "scripts/work.sh" in r.summary


def test_a_guarded_name_in_shipped_text_fails_but_provenance_and_guards_pass(repo):
    _write(repo, "docs/PLAN.md", f"Left behind: all {NAME} content.\n")
    _write(repo, "tests/test_guard.py", f'BANNED = re.compile(r"{NAME}|{OTHER}", re.I)\n')
    assert knowledge.gate_left_behind(Repo(repo), film_root=None).status == "PASS"
    _write(repo, "skills/demo/references/x.md", f"The {NAME.capitalize()} rig waves hello.\n")
    r = knowledge.gate_left_behind(Repo(repo), film_root=None)
    assert r.status == "FAIL" and "skills/demo/references/x.md:1" in r.summary
    assert NAME not in json.dumps(r.to_dict()).lower()  # the report never spells the name out


def test_the_ledger_may_cite_film_paths_but_its_own_words_are_scanned(repo):
    ledger = good_ledger()
    ledger["lessons"][0]["sources"] = [f"FILMROOT/sound/{NAME}_voice/V4_TAGS.md:250-266"]
    _write(repo, "knowledge/lessons.json", json.dumps(ledger))
    r = knowledge.gate_left_behind(Repo(repo), film_root=None)
    assert r.status == "PASS", r.details["hits"]
    ledger["lessons"][1]["destination"]["note"] = f"Tuned on {NAME.capitalize()}'s voice."
    _write(repo, "knowledge/lessons.json", json.dumps(ledger))
    r = knowledge.gate_left_behind(Repo(repo), film_root=None)
    assert r.status == "FAIL" and "knowledge/lessons.json: L-demo-002's note: a guarded name" in r.summary
    assert NAME not in json.dumps(r.to_dict()).lower()


def test_a_copy_of_a_film_project_file_is_found_under_any_name(repo, tmp_path):
    film = tmp_path / "film"
    body = "export function Scene() { return null; }\n" * 10
    _write(film, "pipeline/r3f/src/acts/act1/Scene.tsx", body)
    _write(repo, "engine/remotion/kit/borrowed.tsx", body)
    r = knowledge.gate_left_behind(Repo(repo), film_root=film)
    assert r.status == "FAIL" and "borrowed.tsx: a copy of the film project's pipeline/r3f/src/acts/act1/Scene.tsx" in \
        r.summary
    assert r.details["film_project_files_compared"] == 1


# ------------------------------------------------------------------------------------------------ the CLI
def test_show_finds_a_lesson_by_an_absorbed_id(repo, monkeypatch):
    monkeypatch.setattr(knowledge.paths, "REPO", repo)
    _write(repo, "knowledge/lessons.json", json.dumps(good_ledger()))
    code, out, err = cli.run_cli(["knowledge", "show", "L-demo-D001", "--json"])
    assert code == 0, err
    doc = json.loads(out)
    assert doc["lesson"]["id"] == "L-demo-003" and doc["checked"][0]["resolves"] is True
    code, out, _ = cli.run_cli(["knowledge", "show", "L-nope-001"])
    assert code == 2


def test_stats_counts_destinations_and_merges(repo, monkeypatch):
    monkeypatch.setattr(knowledge.paths, "REPO", repo)
    _write(repo, "knowledge/lessons.json", json.dumps(good_ledger()))
    code, out, _ = cli.run_cli(["knowledge", "stats", "--json"])
    doc = json.loads(out)
    assert code == 0 and doc["lessons"] == 4 and doc["harvested"] == 5 and doc["merged"] == 1
    assert doc["by_type"] == {"code_default": 1, "gate": 1, "skill_rule": 1, "dropped": 1, "gap": 0, "pending": 0}
    assert doc["next_harvest"] == "once films B and C and the p3 sound are approved"


def test_check_without_a_ledger_is_a_missing_prerequisite(repo, monkeypatch):
    monkeypatch.setattr(knowledge.paths, "REPO", repo)
    code, _, err = cli.run_cli(["knowledge", "check"])
    assert code == 3 and "no knowledge ledger" in err


# ------------------------------------------------------------------------------------------------ the real ledger
def test_the_real_check_gives_a_well_formed_report():
    """`slate knowledge check` on this checkout, without --run-tests. It may report gaps (the integrator closes them
    from knowledge/GAPS.md), so this checks the report's shape and that nothing crashed, not that it passes."""
    code, out, err = cli.run_cli(["knowledge", "check", "--json"])
    assert code in (0, 1), err
    doc = json.loads(out)
    assert doc["status"] in gates.STATUSES and doc["tests_run"] is False
    names = [g["gate"] for g in doc["gates"]]
    assert names == ["knowledge.mapped", "knowledge.gaps", "knowledge.destinations", "knowledge.components",
                     "knowledge.left_behind"]
    for g in doc["gates"]:
        assert g["status"] in gates.STATUSES and g["summary"] and g["engine"]["name"] == "slate knowledge"
        assert "Traceback" not in json.dumps(g)
    assert doc["status"] == gates.overall(gates.GateResult.from_dict(g) for g in doc["gates"])
    counts = doc["counts"]
    harvested = knowledge.harvested_ids(knowledge.paths.REPO)
    assert harvested and len(harvested) == len(set(harvested))
    assert counts["harvested"] == len(harvested) and counts["lessons"] + counts["merged"] == counts["harvested"]
    assert sum(counts["by_type"].values()) == counts["lessons"]
    assert [h["date"] for h in counts["harvests"]] == ["2026-09-30", "2026-10-01"]
    assert counts["next_harvest"] == "once films B and C and the p3 sound are approved"
    assert (code == 0) == (doc["status"] == "PASS")
    gaps = next(g for g in doc["gates"] if g["gate"] == "knowledge.gaps")
    assert (gaps["status"] == "PASS") == (counts["by_type"]["gap"] + counts["by_type"]["pending"] == 0)
