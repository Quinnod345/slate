"""The graders on recorded transcripts: every synthetic fixture grades exactly as its meta.json says it must."""

import pytest

from slate import paths
from slate.evals import checks, runner, suite

EVALS = paths.REPO / "evals"
FIXTURES = suite.synthetic_fixtures(EVALS)


def _case(case_id):
    configs = suite.load_configs(EVALS)
    return {c.id: c for c in suite.load_cases(EVALS, configs=configs)}[case_id], configs


@pytest.mark.parametrize("folder,meta", FIXTURES, ids=[f.name for f, _ in FIXTURES])
def test_fixture_grades_as_expected(folder, meta):
    case, configs = _case(meta["case"])
    result = runner.grade_folder(folder, case, meta["config"], configs)
    failed = sorted(c["gate"] for c in result["checks"] if c["status"] == "FAIL")
    unchecked = sorted(c["gate"] for c in result["checks"] if c["status"] == "UNCHECKED")
    assert result["status"] == meta["expect"]["status"], result["summary"]
    assert failed == sorted(meta["expect"]["failed"])
    assert unchecked == sorted(meta["expect"].get("unchecked") or [])
    for check in result["checks"]:
        assert check["evidence"] is not None and check["engine"]["name"] == "slate evals grader"


def test_fixtures_cover_every_format_and_both_verdicts():
    configs = suite.load_configs(EVALS)
    formats = {suite.config_entry(configs, m["config"])["transcript"]["format"] for _, m in FIXTURES}
    assert formats == {"claude-stream-json", "codex-jsonl", "openclaw"}
    verdicts = {m["expect"]["status"] for _, m in FIXTURES}
    assert verdicts == {"PASS", "FAIL", "UNCHECKED"}
    assert {m["config"] for _, m in FIXTURES} >= {"claude-code", "codex", "pursuit-slice", "openclaw",
                                                   "oneiro-strategist"}


def test_every_grader_is_seen_passing_and_failing_somewhere():
    """Across the fixtures, each check the cases use comes out PASS at least once and FAIL at least once."""
    seen: dict[str, set] = {}
    for folder, meta in FIXTURES:
        case, configs = _case(meta["case"])
        planned, _skipped = checks.plan_checks(case, meta["config"], configs)
        check_of = {p["_id"]: p["check"] for p in planned}
        result = runner.grade_folder(folder, case, meta["config"], configs)
        for c in result["checks"]:
            seen.setdefault(check_of[c["gate"]], set()).add(c["status"])
    for name in ("skill_loaded", "skill_not_loaded", "asks_one_question", "lane_question_not_asked",
                 "fast_lane_not_used", "no_publish_action", "no_vendor_spend", "slate_not_ran", "no_browser",
                 "slate_children_browser_none", "no_stdin_wait", "final_message_matches", "strategist_proposes",
                 "artifact_exists", "file_written", "order", "slate_ran", "stays_in_sandbox", "no_foreign_access"):
        assert {"PASS", "FAIL"} <= seen.get(name, set()), f"{name}: {seen.get(name)}"


def test_synthetic_replay_through_the_runner_agrees_with_every_fixture(slate_home):
    configs = suite.load_configs(EVALS)
    pairs = suite.select(suite.load_cases(EVALS, configs=configs))
    summary = runner.replay(pairs, configs, fixtures="synthetic", out=slate_home["tmp"] / "runs")
    assert summary["overall"] == "PASS", summary["headline"]
    assert len(summary["results"]) == len(FIXTURES)
    assert all(r["synthetic"] and r["agrees"] for r in summary["results"])
