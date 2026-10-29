"""Voice-limiter acceptance needs exact gain or a proven bypass, not a waveform-only estimate.

All controls use in-memory arrays and a fake gate context. No film, audio file, limiter backend or external service
is invoked. Legacy acceptance is a bounded interpretation of recorded producer fields, not an archived gain curve.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from slate.sound import mix, verify


_ABSENT = object()


@pytest.fixture(autouse=True)
def _neutral_engine(monkeypatch):
    monkeypatch.setattr(verify, "_engine", lambda: {"name": "neutral limiter control", "version": "1"})


class _Context:
    def __init__(self, report, dialogue):
        self.report = report
        self.dialogue = dialogue
        self.report_path = Path("sound/mix/final_mix.json")
        self.stem_paths = {"dialogue": Path("sound/mix/stems/dialogue.wav")}
        self.film = SimpleNamespace(rel=str)

    def stem(self, name):
        assert name == "dialogue"
        return self.dialogue

    def inputs(self, *paths, include_missing=False):
        assert paths == (self.report_path, self.stem_paths["dialogue"])
        return {"fixture:report": "0" * 64, "fixture:dialogue": "1" * 64}


def _report(share, *, basis="gain_curve", limiter="slate", limited=True):
    touched = 0
    if isinstance(share, (int, float)) and not isinstance(share, bool) and 0 <= share <= 1 and np.isfinite(share):
        touched = round(share * 10000)
    rep = {"share": share, "speech_ms": 10000, "touched_ms": touched,
           "max_reduction_db": 1.33 if touched else 0.0, "limited": limited, "rule": "under 5% of the speech"}
    if basis is not _ABSENT:
        rep["measurement_basis"] = basis
    return {"limiter": limiter, "dialogue_limited": limited, "voice_limiter": rep}


def _gate(report):
    return verify.gate_voice_limiter(_Context(report, np.full((20, 2), 0.25)))


def _assert_retained(result, report):
    rep = report["voice_limiter"]
    for key, value in rep.items():
        assert result.details[key] is value or result.details[key] == value
    assert "effective_measurement_basis" not in rep and "measurement_provenance" not in rep


def test_gain_curve_retains_the_exact_thirteen_percent_measurement():
    raw = np.full((10000, 2), 0.25)
    gain = np.ones(10000)
    gain[:1313] = 10 ** (-1.33 / 20)
    limited = raw * gain[:, None]
    raw_before, limited_before, gain_before = raw.copy(), limited.copy(), gain.copy()

    rep = mix._voice_limiter(raw, limited, gain, 1000)

    assert rep["measurement_basis"] == "gain_curve"
    assert rep["limited"] is True
    assert rep["share"] == 0.1313
    assert rep["speech_ms"] == 10000 and rep["touched_ms"] == 1313
    assert rep["max_reduction_db"] == 1.33
    assert rep["rule"] == "under 5% of the speech"
    np.testing.assert_array_equal(raw, raw_before)
    np.testing.assert_array_equal(limited, limited_before)
    np.testing.assert_array_equal(gain, gain_before)


def test_bypass_is_proven_without_using_an_unused_gain_curve():
    raw = np.full((100, 2), 0.25)
    rep = mix._voice_limiter(raw, None, np.full(100, 0.5), 1000)

    assert rep["measurement_basis"] == "bypassed" and rep["limited"] is False
    assert rep["share"] == 0.0 and rep["touched_ms"] == 0 and rep["max_reduction_db"] == 0.0
    assert rep["speech_ms"] == 100


def test_waveform_ratio_keeps_its_numeric_estimate_even_when_it_is_zero():
    raw = np.full((100, 2), 0.25)
    rep = mix._voice_limiter(raw, raw * 2, None, 1000)

    assert rep["measurement_basis"] == "waveform_ratio" and rep["limited"] is True
    assert rep["share"] == 0.0 and rep["touched_ms"] == 0 and rep["max_reduction_db"] == 0.0
    report = {"limiter": "ffmpeg", "dialogue_limited": True, "voice_limiter": rep}
    result = _gate(report)
    assert result.status == "UNCHECKED" and not result.passed
    assert result.details["effective_measurement_basis"] == "waveform_ratio"
    _assert_retained(result, report)


@pytest.mark.parametrize(
    ("basis", "limiter", "share"),
    [
        pytest.param("waveform_ratio", "ffmpeg", 0.0, id="estimated-zero"),
        pytest.param("waveform_ratio", "ffmpeg", 0.2485, id="estimated-high"),
        pytest.param(_ABSENT, "ffmpeg", 0.0, id="legacy-ffmpeg-zero"),
        pytest.param(_ABSENT, "ffmpeg", 0.2485, id="legacy-ffmpeg-high"),
        pytest.param("future-basis", "slate", 0.01, id="unknown-basis"),
        pytest.param(None, "slate", 0.0, id="explicit-null-basis"),
        pytest.param(["gain_curve"], "slate", 0.0, id="malformed-basis"),
        pytest.param(_ABSENT, None, 0.0, id="legacy-missing-backend"),
        pytest.param(_ABSENT, "unknown", 0.0, id="legacy-unknown-backend"),
    ],
)
def test_inferred_or_unknown_measurement_stays_unchecked(basis, limiter, share):
    report = _report(share, basis=basis, limiter=limiter)
    result = _gate(report)

    assert result.status == "UNCHECKED" and not result.passed
    _assert_retained(result, report)
    assert result.inputs == {"fixture:report": "0" * 64, "fixture:dialogue": "1" * 64}
    assert any("exact gain or a proven bypass" in step for step in result.evidence)
    if limiter == "ffmpeg" and (basis is _ABSENT or basis == "waveform_ratio"):
        assert "estimate" in result.summary
        assert result.details["effective_measurement_basis"] == "waveform_ratio"


@pytest.mark.parametrize(
    ("share", "status"),
    [
        pytest.param(0.0, "PASS", id="zero"),
        pytest.param(0.0499, "PASS", id="under-five-percent"),
        pytest.param(0.05, "FAIL", id="at-five-percent"),
        pytest.param(0.0501, "FAIL", id="above-five-percent"),
        pytest.param(0.1313, "FAIL", id="exact-default-thirteen-percent"),
        pytest.param(1.0, "FAIL", id="one"),
    ],
)
def test_exact_slate_gain_keeps_the_existing_threshold(share, status):
    report = _report(share)
    result = _gate(report)

    assert result.status == status
    assert result.passed is (status == "PASS")
    _assert_retained(result, report)
    assert "of the speech" in result.summary
    if share == 0.1313:
        assert "13.1%" in result.summary


@pytest.mark.parametrize("limiter", ["slate", "ffmpeg"], ids=["slate", "ffmpeg"])
def test_proven_bypass_can_pass_for_either_backend(limiter):
    report = _report(0.0, basis="bypassed", limiter=limiter, limited=False)
    result = _gate(report)

    assert result.status == "PASS" and result.passed
    assert result.details["measurement_basis"] == "bypassed"
    _assert_retained(result, report)


@pytest.mark.parametrize(
    ("share", "status"),
    [
        pytest.param(0.0499, "PASS", id="under-five-percent"),
        pytest.param(0.05, "FAIL", id="at-five-percent"),
        pytest.param(0.1313, "FAIL", id="legacy-default-thirteen-percent"),
    ],
)
def test_corroborated_legacy_slate_gain_keeps_the_boundary_and_exposes_its_contract(share, status):
    report = _report(share, basis=_ABSENT)
    result = _gate(report)

    assert result.status == status
    assert result.details["measurement_provenance"] == "legacy_report_contract"
    assert result.details["effective_measurement_basis"] == "gain_curve"
    assert "measurement_basis" not in report["voice_limiter"]
    _assert_retained(result, report)


@pytest.mark.parametrize("limiter", ["slate", "ffmpeg"], ids=["slate", "ffmpeg"])
def test_corroborated_legacy_bypass_is_accepted_for_both_backends(limiter):
    report = _report(0.0, basis=_ABSENT, limiter=limiter, limited=False)
    result = _gate(report)

    assert result.status == "PASS" and result.passed
    assert result.details["measurement_provenance"] == "legacy_report_contract"
    assert result.details["effective_measurement_basis"] == "bypassed"
    _assert_retained(result, report)


@pytest.mark.parametrize(
    ("basis", "updates"),
    [
        pytest.param("gain_curve", {"limiter": "ffmpeg"}, id="gain-with-waveform-backend"),
        pytest.param("gain_curve", {"limited": False}, id="gain-with-false-limited"),
        pytest.param("gain_curve", {"dialogue_limited": False}, id="gain-with-false-dialogue-limited"),
        pytest.param("gain_curve", {"limited": 1}, id="gain-with-numeric-limited"),
        pytest.param("bypassed", {"limited": True}, id="bypass-with-true-limited"),
        pytest.param("bypassed", {"dialogue_limited": True}, id="bypass-with-true-dialogue-limited"),
        pytest.param("bypassed", {"dialogue_limited": 0}, id="bypass-with-numeric-dialogue-limited"),
        pytest.param("bypassed", {"share": 0.001}, id="bypass-with-touch-share"),
        pytest.param("bypassed", {"touched_ms": 1}, id="bypass-with-touched-ms"),
        pytest.param("bypassed", {"max_reduction_db": 0.01}, id="bypass-with-reduction"),
        pytest.param("bypassed", {"speech_ms": float("nan")}, id="bypass-with-nan-speaking-time"),
        pytest.param("bypassed", {"speech_ms": -1}, id="bypass-with-negative-speaking-time"),
        pytest.param("bypassed", {"speech_ms": _ABSENT}, id="bypass-with-missing-speaking-time"),
        pytest.param("bypassed", {"limited": _ABSENT}, id="bypass-with-missing-limited"),
    ],
)
def test_conflicting_provenance_stays_unchecked(basis, updates):
    report = _report(0.0, basis=basis, limited=basis == "gain_curve")
    for name, value in updates.items():
        target = report if name in ("limiter", "dialogue_limited") else report["voice_limiter"]
        if value is _ABSENT:
            target.pop(name)
        else:
            target[name] = value
    result = _gate(report)

    assert result.status == "UNCHECKED" and not result.passed
    _assert_retained(result, report)


@pytest.mark.parametrize(
    "updates",
    [
        pytest.param({"limited": _ABSENT}, id="missing-nested-limited"),
        pytest.param({"dialogue_limited": _ABSENT}, id="missing-dialogue-limited"),
        pytest.param({"limited": 1}, id="numeric-nested-limited"),
    ],
)
def test_legacy_gain_requires_both_strict_application_flags(updates):
    report = _report(0.0, basis=_ABSENT)
    for name, value in updates.items():
        target = report if name == "dialogue_limited" else report["voice_limiter"]
        if value is _ABSENT:
            target.pop(name)
        else:
            target[name] = value
    result = _gate(report)

    assert result.status == "UNCHECKED" and not result.passed
    assert result.details["measurement_provenance"] == "unresolved_legacy_report"
    _assert_retained(result, report)


@pytest.mark.parametrize(
    "share",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(-0.01, id="negative"),
        pytest.param(1.01, id="over-one"),
        pytest.param(False, id="boolean"),
        pytest.param("not-a-number", id="malformed-string"),
        pytest.param("0.01", id="numeric-string"),
    ],
)
def test_malformed_share_is_unchecked_instead_of_pass_or_crash(share):
    report = _report(share)
    result = _gate(report)

    assert result.status == "UNCHECKED" and not result.passed
    assert result.details["share"] is share
    _assert_retained(result, report)


@pytest.mark.parametrize("silent", [False, True], ids=["missing-dialogue", "silent-dialogue"])
def test_no_voice_passes_before_report_validation(silent):
    context = _Context({"voice_limiter": {"share": float("nan")}}, np.zeros((20, 2)) if silent else None)
    result = verify.gate_voice_limiter(context)

    assert result.status == "PASS" and result.passed
    assert result.summary == "no voice in this film"
    assert result.inputs == {"fixture:report": "0" * 64, "fixture:dialogue": "1" * 64}


@pytest.mark.parametrize(
    "missing", ["report", "share", "null-share"], ids=["missing-report", "missing-share", "null-share"]
)
def test_missing_measurement_keeps_the_existing_unchecked_control(missing):
    report = _report(0.0)
    if missing == "report":
        report.pop("voice_limiter")
    elif missing == "share":
        report["voice_limiter"].pop("share")
    else:
        report["voice_limiter"]["share"] = None
    result = _gate(report)

    assert result.status == "UNCHECKED" and not result.passed
    assert "mix again" in result.summary
