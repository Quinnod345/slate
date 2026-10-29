"""A knowledge test gate preserves named-case evidence and requires a successful batch exit.

The tests supply parsed neutral JUnit outcomes at run_pytest's result boundary. They never invoke pytest as a child
process, inspect a real ledger or run a provider, renderer, model or media tool.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from slate import knowledge, procs


FIRST = "tests/test_fixture.py::test_first"
SECOND = "tests/test_fixture.py::test_second"
PASSING_JUNIT = """<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">
<testcase file="tests/test_fixture.py" classname="tests.test_fixture" name="test_first"/>
<testcase file="tests/test_fixture.py" classname="tests.test_fixture" name="test_second"/>
</testsuite></testsuites>"""


@pytest.fixture(autouse=True)
def no_child_process(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A knowledge result regression launched a child process")

    monkeypatch.setattr(procs, "run", forbidden)


def _ledger():
    return {"meta": {"source": "neutral result-boundary fixture"}, "lessons": [
        {"id": "L-fixture-001", "gate_type": True,
         "destination": {"type": "gate", "tests": [SECOND, FIRST]}},
        {"id": "L-fixture-002", "gate_type": True,
         "destination": {"type": "gate", "tests": [FIRST]}},
    ]}


def _run_gate(tmp_path, monkeypatch, info, results=None):
    data = _ledger()
    original_data = deepcopy(data)
    outcomes = knowledge.parse_junit(PASSING_JUNIT) if results is None else results
    original_outcomes, original_info = deepcopy(outcomes), deepcopy(info)
    calls = []

    def completed_result(node_ids, repo, *, timeout):
        calls.append((list(node_ids), repo, timeout))
        return outcomes, info

    monkeypatch.setattr(knowledge, "run_pytest", completed_result)
    result = knowledge.gate_tests(data, knowledge.Repo(tmp_path), timeout=12.0)

    assert calls == [([FIRST, SECOND], tmp_path, 12.0)]
    assert data == original_data
    assert outcomes == original_outcomes
    assert info == original_info
    assert result.details["lessons"] == 2
    assert result.details["tests"] == 2
    assert result.details["pytest"] == original_info
    return result


def test_passing_cases_and_confirmed_zero_exit_pass(tmp_path, monkeypatch):
    result = _run_gate(tmp_path, monkeypatch, {"exit_code": 0, "requested": 2, "tail": "2 passed"})

    assert result.status == "PASS"
    assert result.details["outcomes"] == {"passed": 2}
    assert result.details["failing"] == {}
    assert result.details["not_run"] == {}


@pytest.mark.parametrize("exit_code", [1, 2, -9], ids=["session-failed", "interrupted", "signal"])
def test_passing_junit_cases_do_not_hide_nonzero_batch_exit(tmp_path, monkeypatch, exit_code):
    info = {"exit_code": exit_code, "requested": 2, "tail": "Named cases passed; session did not finish successfully"}
    result = _run_gate(tmp_path, monkeypatch, info)

    assert result.status == "FAIL"
    assert f"exit code {exit_code}" in result.summary
    assert result.details["outcomes"] == {"passed": 2}
    assert result.details["failing"] == {}
    assert result.details["not_run"] == {}


@pytest.mark.parametrize("info", [{}, {"exit_code": None}, {"exit_code": "0"}, {"exit_code": False},
                                  {"exit_code": 0.0}], ids=["absent", "null", "text-zero", "boolean", "float"])
def test_passing_cases_without_confirmed_terminal_exit_are_unchecked(tmp_path, monkeypatch, info):
    result = _run_gate(tmp_path, monkeypatch, info)

    assert result.status == "UNCHECKED"
    assert "confirmed terminal exit code" in result.summary
    assert result.details["outcomes"] == {"passed": 2}
    assert result.details["failing"] == {}
    assert result.details["not_run"] == {}


@pytest.mark.parametrize("outcome", ["failed", "error"])
@pytest.mark.parametrize("exit_code", [0, None], ids=["zero", "unknown"])
def test_individual_case_failure_remains_failure(tmp_path, monkeypatch, outcome, exit_code):
    result = _run_gate(tmp_path, monkeypatch, {"exit_code": exit_code}, {FIRST: outcome, SECOND: "passed"})

    assert result.status == "FAIL"
    assert "have failing tests" in result.summary
    assert result.details["outcomes"] == {outcome: 1, "passed": 1}
    assert result.details["failing"] == {"L-fixture-001": [FIRST], "L-fixture-002": [FIRST]}
    assert result.details["not_run"] == {}


@pytest.mark.parametrize("outcome", ["skipped", "missing"])
@pytest.mark.parametrize("exit_code", [0, None], ids=["zero", "unknown"])
def test_incomplete_named_case_evidence_remains_unchecked(tmp_path, monkeypatch, outcome, exit_code):
    outcomes = {SECOND: "passed"}
    if outcome == "skipped":
        outcomes[FIRST] = "skipped"
    result = _run_gate(tmp_path, monkeypatch, {"exit_code": exit_code}, outcomes)

    assert result.status == "UNCHECKED"
    assert result.details["outcomes"] == {outcome: 1, "passed": 1}
    assert result.details["failing"] == {}
    assert result.details["not_run"] == {"L-fixture-001": [FIRST], "L-fixture-002": [FIRST]}


@pytest.mark.parametrize("outcome", ["skipped", "missing"])
def test_nonzero_batch_keeps_incomplete_case_priority_and_runner_evidence(tmp_path, monkeypatch, outcome):
    outcomes = {SECOND: "passed"}
    if outcome == "skipped":
        outcomes[FIRST] = "skipped"
    result = _run_gate(tmp_path, monkeypatch, {"exit_code": 2}, outcomes)

    assert result.status == "UNCHECKED"
    assert result.details["pytest"]["exit_code"] == 2
    assert result.details["outcomes"] == {outcome: 1, "passed": 1}
    assert result.details["failing"] == {}
    assert result.details["not_run"] == {"L-fixture-001": [FIRST], "L-fixture-002": [FIRST]}


def test_parameterized_case_failure_is_not_relabelled_as_runner_failure(tmp_path, monkeypatch):
    outcomes = {FIRST + "[one]": "passed", FIRST + "[two]": "failed", SECOND: "passed"}
    result = _run_gate(tmp_path, monkeypatch, {"exit_code": 1}, outcomes)

    assert result.status == "FAIL"
    assert "have failing tests" in result.summary
    assert result.details["outcomes"] == {"failed": 1, "passed": 1}
    assert result.details["failing"] == {"L-fixture-001": [FIRST], "L-fixture-002": [FIRST]}


def test_no_named_gate_tests_remains_unchecked_without_running_batch(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An empty knowledge test selection launched a batch")

    monkeypatch.setattr(knowledge, "run_pytest", forbidden)
    result = knowledge.gate_tests({"lessons": []}, knowledge.Repo(tmp_path))

    assert result.status == "UNCHECKED"
    assert result.summary == "no gate-type lesson names a test"
