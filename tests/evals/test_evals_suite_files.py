"""The suite's own files: configs.json and every case validate, and together they cover what the plan asks for."""

import json
import os
import re

import pytest

from slate import paths
from slate.errors import UsageError
from slate.evals import checks, suite

EVALS = paths.REPO / "evals"

REQUIRED_CONFIGS = ("claude-code", "codex", "pursuit-slice", "openclaw")


def test_configs_json_validates_with_the_four_configurations(real_configs):
    assert list(real_configs["configurations"]) == list(REQUIRED_CONFIGS)
    assert "oneiro-strategist" in real_configs["harnesses"]
    assert suite.validate_configs(real_configs) == []


def test_every_configuration_says_how_it_was_verified(real_configs):
    for section in ("configurations", "harnesses"):
        for cid, cfg in real_configs[section].items():
            verified = cfg["verified"]
            assert verified["on"] == "2026-09-30", cid
            assert verified["how"], cid
            assert any("--help" in c for c in verified.get("help_checked", [])), f"{cid} names the help it checked"


def test_models_and_setups_match_the_plan(real_configs):
    c = real_configs["configurations"]
    assert c["claude-code"]["model"] == "claude-opus-5-5"
    assert c["codex"]["model"] == "gpt-6-astra"
    assert c["pursuit-slice"]["model"] == "gpt-6-sol"
    assert c["openclaw"]["model"] == "openai/gpt-5.6-sol"
    slice_ = c["pursuit-slice"]
    assert "--ignore-user-config" in slice_["argv"]
    assert slice_["stdin"] == "prompt" and slice_["timeout_s"] == 600 and slice_["kill_is_real"]
    servers = {m.group(1) for a in slice_["argv"] for m in [re.match(r"mcp_servers\.([\w-]+)\.", a)] if m}
    assert servers == {"aside", "apple"}, "only the aside and apple MCP servers"
    assert "no_stdin_wait" in slice_["extra_checks"]


def test_every_case_file_validates(real_configs):
    files = sorted((EVALS / "cases").glob("*.json"))
    assert len(files) >= 12
    for path in files:
        obj = json.loads(path.read_text())
        assert suite.validate_case(obj, real_configs) == [], path.name
        assert obj["id"] == path.stem


def test_cases_cover_the_plan(real_cases):
    by_group = {}
    for c in real_cases:
        by_group.setdefault(c.group, []).append(c)
    assert set(by_group) == {"triggering", "routing", "negative", "dry-run", "oneiro"}
    ids = {c.id for c in real_cases}
    for needed in ("triggering-launch-video", "routing-open-request", "routing-ui-recording",
                   "routing-fast-lane-picked", "routing-fast-lane-brand", "negative-email", "negative-css-bug",
                   "negative-publish-without-approval", "dry-run-script-stage", "oneiro-strategist-slate-move"):
        assert needed in ids
    for c in real_cases:
        if c.runner == "agent":
            assert c.configs == list(REQUIRED_CONFIGS), f"{c.id} runs in every agent"


def test_the_strategist_case_is_pending_with_a_precondition(real_cases):
    case = next(c for c in real_cases if c.id == "oneiro-strategist-slate-move")
    assert case.pending and case.runner == "strategist"
    kinds = {p["check"] for p in case.precondition["all"]}
    assert kinds == {"strategist_prompt_mentions", "file_exists"}
    assert "pursuit-work/27/pursuit.json" in json.dumps(case.precondition)
    assert "/oca/trace/64" in json.dumps(case.precondition["info"])


def test_every_case_is_graded_for_browsers_and_the_slice_for_stdin(real_cases, real_configs):
    for case in real_cases:
        for cid in case.configs:
            planned, skipped = checks.plan_checks(case, cid, real_configs)
            names = {p["check"] for p in planned} | {k.split("#")[0] for k in skipped}
            assert "no_browser" in names, (case.id, cid)
            assert "slate_children_browser_none" in names, (case.id, cid)
            if cid == "pursuit-slice":
                assert "no_stdin_wait" in names
    # OpenClaw can't carry the probe, and says why instead of passing it
    planned, skipped = checks.plan_checks(real_cases[0], "openclaw", real_configs)
    assert "slate_children_browser_none" in skipped and "Gateway" in skipped["slate_children_browser_none"]


def test_the_dry_run_case_asks_for_the_script_stage_in_replay_mode(real_cases, real_configs):
    case = next(c for c in real_cases if c.id == "dry-run-script-stage")
    checks_by = {e["check"] for e in case.expect}
    assert {"slate_ran", "file_written", "order", "slate_not_ran", "final_message_matches"} <= checks_by
    assert real_configs["eval_env"]["set"]["SLATE_FAKE_VENDORS"] == "1"
    assert case.needs_eval_env
    assert "demo" in case.prompt or "demo" in json.dumps(case.vars)


def test_no_company_specifics_in_the_suite():
    banned = re.compile(r"(?i)true\s?space|vixa|trudy|\bEOS\b|inner\s?echo|eos\s?worldwide")
    roots = [EVALS, suite.PACKAGE_DIR, EVALS.parent / "tests" / "evals"]
    offenders = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                if name == os.path.basename(__file__) or "__pycache__" in path:
                    continue
                text = open(path, encoding="utf-8", errors="replace").read()
                if banned.search(text):
                    offenders.append(path)
    assert offenders == []


def test_a_broken_case_is_a_usage_error_that_names_the_problem(tmp_path, real_configs):
    (tmp_path / "cases").mkdir()
    (tmp_path / "configs.json").write_text((EVALS / "configs.json").read_text())
    bad = {"id": "bad-case", "title": "A case with a typo in it", "group": "negative", "status": "active",
           "prompt": "hello {nobody}", "configs": ["claude-code", "codx"],
           "expect": [{"check": "skill_loadd", "skill": "slate"}, {"check": "command_ran", "pattern": "("}]}
    (tmp_path / "cases" / "bad-case.json").write_text(json.dumps(bad))
    with pytest.raises(UsageError) as err:
        suite.load_cases(tmp_path, configs=real_configs)
    msg = err.value.message
    assert "codx" in msg and "skill_loadd" in msg and "valid regex" in msg and "nobody" in msg


def test_a_pending_case_without_a_precondition_is_refused(real_configs):
    obj = {"id": "pending-x", "title": "Pending without a precondition", "group": "oneiro", "status": "pending",
           "prompt": "p", "configs": ["claude-code"], "expect": [{"check": "no_browser"}]}
    assert any("precondition" in p for p in suite.validate_case(obj, real_configs))


def test_selecting_an_unknown_case_is_a_usage_error(real_cases):
    with pytest.raises(UsageError):
        suite.select(real_cases, ids=["no-such-case"])
    pairs = suite.select(real_cases, groups=["negative"], configs=["codex"])
    assert pairs and all(c.group == "negative" and cid == "codex" for c, cid in pairs)
