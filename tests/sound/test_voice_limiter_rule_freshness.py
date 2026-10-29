"""Cached voice-limiter acceptance requires current rules and the dependencies that justified the result.

Only scratch JSON/text files, neutral arrays and fake contexts are used. The real shared report reader, writer and
input hashing are exercised; the reference resolver and sound-engine lookup are confined to neutral test doubles.
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from slate import gates, jsonio
from slate.sound import verify


_NAME = "sound.voice_limiter"
_SUMMARY = "the voice limiter touched 1.0% of the speech"
_ABSENT = object()


@pytest.fixture(autouse=True)
def _neutral_boundaries(monkeypatch):
    monkeypatch.setattr(verify, "_engine", lambda: {"name": "neutral limiter control", "version": "1"})
    monkeypatch.setattr(gates, "_input_file",
                        lambda film_dir, path: Path(path) if Path(path).is_absolute() else Path(film_dir) / path)


def _cached(tmp_path, *, status="PASS", revision=_ABSENT, name=_NAME, metadata=_ABSENT):
    dependency = tmp_path / "neutral-input.txt"
    dependency.write_text("unchanged neutral dependency\n", encoding="utf-8")
    engine = {"name": "neutral limiter control", "version": "1"}
    if revision is not _ABSENT:
        engine["rule_revision"] = revision
    if metadata is not _ABSENT:
        engine = metadata
    result = gates.GateResult(name, status, _SUMMARY, engine=engine,
                              inputs={str(dependency): jsonio.sha256(dependency)},
                              details={"share": 0.01, "measurement_basis": "waveform_ratio", "note": "retained"})
    report = gates.write_report(tmp_path, [result])
    history = tmp_path / "qa" / "history.jsonl"
    return result, dependency, report, history


def _unchanged_readback(tmp_path, result, report, history):
    report_before, history_before = report.read_bytes(), history.read_bytes()
    loaded = gates.load_report(tmp_path)[result.gate]
    assert report.read_bytes() == report_before and history.read_bytes() == history_before
    return loaded


def test_cached_estimated_pass_requires_current_rule_revision(tmp_path):
    result, dependency, report, history = _cached(tmp_path)
    original = copy.deepcopy(result.to_dict())
    assert jsonio.read(report)["gates"][_NAME]["status"] == "PASS"

    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and not loaded.passed
    assert loaded.details == {**original["details"], "was": "PASS"}
    assert loaded.summary.startswith("check rule revision") and _SUMMARY in loaded.summary
    assert "inputs changed" not in loaded.summary
    assert loaded.engine == original["engine"] and loaded.inputs == original["inputs"]
    assert loaded.evidence == original["evidence"] and loaded.checked_at == original["checked_at"]
    assert result.to_dict() == original
    assert jsonio.sha256(dependency) == original["inputs"][str(dependency)]


@pytest.mark.parametrize("status", ["FAIL", "REVIEW", "UNCHECKED"], ids=["fail", "review", "unchecked"])
def test_unversioned_nonpass_results_do_not_claim_current_acceptance(tmp_path, status):
    result, _, report, history = _cached(tmp_path, status=status)
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == status
    assert loaded.summary.startswith("check rule revision")
    assert jsonio.read(report)["gates"][_NAME]["status"] == status


@pytest.mark.parametrize("status", ["PASS", "FAIL", "REVIEW", "UNCHECKED"], ids=["pass", "fail", "review", "unchecked"])
def test_matching_integer_revision_keeps_the_recorded_outcome(tmp_path, status):
    result, _, report, history = _cached(tmp_path, status=status, revision=1)
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.to_dict() == result.to_dict()
    assert gates._is_stale(tmp_path, result) is False


@pytest.mark.parametrize(
    "revision",
    [
        pytest.param(None, id="null"),
        pytest.param(0, id="old"),
        pytest.param(2, id="different"),
        pytest.param(True, id="boolean"),
        pytest.param(1.0, id="float"),
        pytest.param("1", id="string"),
        pytest.param([], id="list"),
    ],
)
def test_corrupt_or_mismatched_revision_cannot_keep_a_cached_pass(tmp_path, revision):
    result, _, report, history = _cached(tmp_path, revision=revision)
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.engine == result.engine
    assert loaded.summary.startswith("check rule revision") and "inputs changed" not in loaded.summary


@pytest.mark.parametrize("metadata", [None, "invalid engine metadata", []], ids=["null", "string", "list"])
def test_malformed_engine_metadata_cannot_keep_a_cached_pass(tmp_path, metadata):
    result, _, report, history = _cached(tmp_path, metadata=metadata)
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.engine == metadata


@pytest.mark.parametrize("name", ["sound.loudness", "generic.unrelated"], ids=["other-sound", "other-generic"])
def test_unrelated_gates_do_not_gain_a_revision_requirement(tmp_path, name):
    result, _, report, history = _cached(tmp_path, name=name, revision=True)
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.to_dict() == result.to_dict()
    assert gates._is_stale(tmp_path, result) is False


@pytest.mark.parametrize("name", [_NAME, "sound.loudness"], ids=["voice-limiter", "other-sound"])
def test_input_changes_keep_the_existing_stale_reason(tmp_path, name):
    result, dependency, report, history = _cached(tmp_path, name=name, revision=1)
    dependency.write_text("changed neutral dependency\n", encoding="utf-8")
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.summary == f"inputs changed since this was checked ({_SUMMARY})"
    assert gates._is_stale(tmp_path, result) is True


def test_shared_revision_map_is_bounded_to_voice_limiter():
    assert gates.rule_revision(_NAME) == 1
    assert type(gates.rule_revision(_NAME)) is int
    # Other producers register their own revisions; these sound/generic controls remain unrelated.
    for name in ("sound.loudness", "loudness", "generic.unrelated", "sound.final_limiter", "unknown"):
        assert gates.rule_revision(name) is None


def test_direct_staleness_check_rejects_an_unversioned_voice_pass(tmp_path):
    result, _, _, _ = _cached(tmp_path)
    assert gates._is_stale(tmp_path, result) is True


class _Context:
    inputs = verify._Ctx.inputs

    def __init__(self, tmp_path, report, dialogue, *, dependency=None):
        self.report = report
        self.dialogue = dialogue
        self.report_path = tmp_path / "neutral-report.json"
        self.stem_paths = {"dialogue": tmp_path / "neutral-dialogue.txt"}
        self.film = SimpleNamespace(dir=tmp_path, rel=lambda path: str(Path(path).relative_to(tmp_path)),
                                    path=lambda path: Path(path) if Path(path).is_absolute() else tmp_path / path)
        jsonio.write_atomic(self.report_path, report)
        if dependency is not None:
            self.stem_paths["dialogue"].write_text(dependency, encoding="utf-8")

    def stem(self, name):
        assert name == "dialogue"
        return self.dialogue


def _mix_report(share, *, basis="gain_curve", limiter="slate", limited=True):
    touched = round(share * 10000)
    measured = {"share": share, "speech_ms": 10000, "touched_ms": touched,
                "max_reduction_db": 1.33 if touched else 0.0, "limited": limited}
    if basis is not _ABSENT:
        measured["measurement_basis"] = basis
    return {"limiter": limiter, "dialogue_limited": limited, "voice_limiter": measured}


def _fresh_context(tmp_path, case):
    dialogue = np.full((20, 2), 0.25)
    report = _mix_report(0.01)
    dependency = "neutral dialogue dependency\n"
    if case == "missing-dialogue":
        dialogue, dependency = None, None
    elif case == "silent-dialogue":
        dialogue = np.zeros((20, 2))
    elif case == "missing-report":
        report = {}
    elif case == "exact-fail":
        report = _mix_report(0.1313)
    elif case == "estimate":
        report = _mix_report(0.01, basis="waveform_ratio", limiter="ffmpeg")
    elif case == "legacy-gain":
        report = _mix_report(0.01, basis=_ABSENT)
    elif case == "legacy-estimate":
        report = _mix_report(0.01, basis=_ABSENT, limiter="ffmpeg")
    elif case == "ffmpeg-bypass":
        report = _mix_report(0.0, basis="bypassed", limiter="ffmpeg", limited=False)
    return _Context(tmp_path, report, dialogue, dependency=dependency)


@pytest.mark.parametrize(
    ("case", "status"),
    [
        pytest.param("missing-dialogue", "PASS", id="missing-dialogue"),
        pytest.param("silent-dialogue", "PASS", id="silent-dialogue"),
        pytest.param("missing-report", "UNCHECKED", id="missing-report"),
        pytest.param("exact-pass", "PASS", id="exact-pass"),
        pytest.param("exact-fail", "FAIL", id="exact-fail"),
        pytest.param("estimate", "UNCHECKED", id="estimate"),
        pytest.param("legacy-gain", "PASS", id="legacy-gain"),
        pytest.param("legacy-estimate", "UNCHECKED", id="legacy-estimate"),
        pytest.param("ffmpeg-bypass", "PASS", id="ffmpeg-bypass"),
    ],
)
def test_every_fresh_voice_limiter_outcome_survives_report_readback(tmp_path, case, status):
    context = _fresh_context(tmp_path, case)
    report_before = copy.deepcopy(context.report)

    result = verify.gate_voice_limiter(context)

    assert result.status == status
    assert result.engine["rule_revision"] == 1 and type(result.engine["rule_revision"]) is int
    assert context.report == report_before
    assert result.inputs == gates.inputs_for(tmp_path, [context.report_path, context.stem_paths["dialogue"]])
    report = gates.write_report(tmp_path, [result])
    history = tmp_path / "qa" / "history.jsonl"
    loaded = _unchanged_readback(tmp_path, result, report, history)
    assert loaded.to_dict() == result.to_dict()
    assert gates._is_stale(tmp_path, result) is False


def test_already_stale_record_retains_its_original_status_history(tmp_path):
    result, _, report, history = _cached(tmp_path, status="STALE")
    result.details["was"] = "FAIL"
    result.summary = "previously stale result"
    gates.write_report(tmp_path, [result])
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.to_dict() == result.to_dict()


def test_changed_rule_is_distinguished_when_an_input_also_changed(tmp_path):
    result, dependency, report, history = _cached(tmp_path, revision=0)
    dependency.write_text("also changed\n", encoding="utf-8")
    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.summary.startswith("check rule revision") and "inputs changed" not in loaded.summary


def test_context_tracks_missing_dependencies_only_when_requested(tmp_path):
    context = _fresh_context(tmp_path, "missing-dialogue")
    report_key = context.report_path.relative_to(tmp_path).as_posix()
    dialogue_key = context.stem_paths["dialogue"].relative_to(tmp_path).as_posix()
    ordinary = context.inputs(context.report_path, context.stem_paths["dialogue"])
    assert ordinary == {report_key: jsonio.sha256(context.report_path)}

    tracked = context.inputs(context.report_path, context.stem_paths["dialogue"], include_missing=True)

    assert tracked == {report_key: jsonio.sha256(context.report_path), dialogue_key: None}


def _no_voice_cache(tmp_path, case):
    context = _fresh_context(tmp_path, case)
    result = verify.gate_voice_limiter(context)
    assert result.status == "PASS" and result.summary == "no voice in this film"
    # Isolate dependency freshness even on the old emitter, which has no revision metadata yet.
    result.engine["rule_revision"] = 1
    report = gates.write_report(tmp_path, [result])
    history = tmp_path / "qa" / "history.jsonl"
    unchanged = _unchanged_readback(tmp_path, result, report, history)
    assert unchanged.to_dict() == result.to_dict()
    return context, result, report, history


def test_no_dialogue_cache_becomes_stale_when_the_dependency_is_created(tmp_path):
    context, result, report, history = _no_voice_cache(tmp_path, "missing-dialogue")
    context.stem_paths["dialogue"].write_text("new neutral dialogue dependency\n", encoding="utf-8")

    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.summary == "inputs changed since this was checked (no voice in this film)"
    assert result.inputs[context.stem_paths["dialogue"].relative_to(tmp_path).as_posix()] is None


def test_silent_dialogue_cache_becomes_stale_when_the_dependency_changes(tmp_path):
    context, result, report, history = _no_voice_cache(tmp_path, "silent-dialogue")
    context.stem_paths["dialogue"].write_text("changed neutral dialogue dependency\n", encoding="utf-8")

    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.summary == "inputs changed since this was checked (no voice in this film)"


def test_no_voice_cache_also_tracks_its_mix_report(tmp_path):
    context, result, report, history = _no_voice_cache(tmp_path, "missing-dialogue")
    jsonio.write_atomic(context.report_path, {"neutral": "changed mix report dependency"})

    loaded = _unchanged_readback(tmp_path, result, report, history)

    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert loaded.summary == "inputs changed since this was checked (no voice in this film)"
