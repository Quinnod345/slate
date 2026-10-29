"""Changed type-size acceptance must invalidate old access verdicts without rewriting them."""

from __future__ import annotations

import pytest

from slate import gates
from slate.qa import access as A
from slate.qa import media
from qa_motion_helpers import make_film


@pytest.mark.parametrize("revision", [None, True, "1", 0, 2])
def test_access_verdict_with_missing_invalid_or_old_rule_is_stale(slate_home, revision):
    film = make_film(slate_home["films"], brand=None)
    metadata = {} if revision is None else {"rule_revision": revision}
    original = gates.GateResult("access", "PASS", "Legacy body-text verdict.", engine=metadata)
    gates.write_report(film, [original])
    report = film / "qa" / "gates.json"
    history = film / "qa" / "history.jsonl"
    saved_report, saved_history = report.read_bytes(), history.read_bytes()

    loaded = gates.load_report(film)["access"]

    assert loaded.status == "STALE"
    assert loaded.details["was"] == "PASS"
    assert report.read_bytes() == saved_report and history.read_bytes() == saved_history


def test_legacy_failed_access_verdict_preserves_prior_failure_on_readback(slate_home):
    film = make_film(slate_home["films"], brand=None)
    gates.write_report(film, [gates.GateResult("access", "FAIL", "Observed small body text.")])
    loaded = gates.load_report(film)["access"]
    assert loaded.status == "STALE" and loaded.details["was"] == "FAIL"


def test_current_access_rule_with_unchanged_inputs_stays_fresh(slate_home):
    film = make_film(slate_home["films"], brand=None)
    assert gates.rule_revision("access") == 1
    gates.write_report(film, [gates.GateResult("access", "PASS", "Measured body text.",
                                             engine={"rule_revision": gates.rule_revision("access")})])
    assert gates.load_report(film)["access"].status == "PASS"


def test_access_producer_stamps_current_rule_even_for_narrowed_results(slate_home, monkeypatch):
    film = make_film(slate_home["films"], brand=None)
    monkeypatch.setattr(A, "check_type_size", lambda *args, **kwargs: A._sub("type_size", "PASS", "Measured."))
    monkeypatch.setattr(A, "caption_files", lambda *args, **kwargs: [])
    monkeypatch.setattr(media, "version", lambda tool: "scripted")
    result = A.evaluate(film, only=["type_size"])
    assert result.status == "UNCHECKED"
    assert result.engine["rule_revision"] == gates.rule_revision("access") == 1


def test_unregistered_gate_keeps_existing_input_only_freshness(slate_home):
    film = make_film(slate_home["films"], brand=None)
    gates.write_report(film, [gates.GateResult("contrast-fixture", "PASS", "Measured contrast.")])
    assert gates.load_report(film)["contrast-fixture"].status == "PASS"
