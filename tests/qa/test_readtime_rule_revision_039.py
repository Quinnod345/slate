"""The expanded read-time contract invalidates earlier results without rewriting their evidence."""

import pytest

from slate import gates


@pytest.mark.parametrize("saved_status", ["PASS", "FAIL", "UNCHECKED"])
def test_revision_one_readtime_results_are_stale_under_the_new_contract(tmp_path, saved_status):
    prior = gates.GateResult(
        "readtime", saved_status, "Retained earlier read-time result.",
        engine={"name": "historical fixture", "version": "1", "rule_revision": 1},
        inputs={}, details={"retained_measurement": 1.75},
    )
    report = gates.write_report(tmp_path, [prior])
    history = tmp_path / "qa/history.jsonl"
    report_bytes, history_bytes = report.read_bytes(), history.read_bytes()

    loaded = gates.load_report(tmp_path)["readtime"]

    assert loaded.status == "STALE"
    assert loaded.details["was"] == saved_status
    assert loaded.details["retained_measurement"] == 1.75
    assert report.read_bytes() == report_bytes
    assert history.read_bytes() == history_bytes
