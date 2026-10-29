"""Nonfinite gate evidence must remain readable through strict JSON consumers.

Neutral contexts exercise the real limiter decision, report writers, command handler and installed MCP client.
The source mix report and in-memory measurements remain raw evidence; exported scalar labels are not measurements.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from slate import gates, jsonio
from slate.cli import run_cli
from slate.mcp import server, tools
from slate.sound import cli as sound_cli, verify


NONFINITE = [
    pytest.param(float("nan"), "NaN", id="nan"),
    pytest.param(float("inf"), "Infinity", id="positive-infinity"),
    pytest.param(float("-inf"), "-Infinity", id="negative-infinity"),
]


def _reject_constant(value):
    raise ValueError(f"Nonstandard JSON number: {value}")


def _strict(text):
    return json.loads(text, parse_constant=_reject_constant)


def _result(value, *, gate="sound.voice_limiter"):
    return gates.GateResult.unchecked(
        gate, "invalid measurement", engine=gates.engine("neutral", "1", rule_revision=1),
        checked_at="2026-01-01T00:00:00+00:00", inputs={"fixture.json": None},
        details={"share": value, "stage": "sound"},
    )


def _limiter_context(folder, value):
    report_path = folder / "sound/mix/final_mix.json"
    report = {"limiter": "slate", "dialogue_limited": True,
              "voice_limiter": {"share": value, "measurement_basis": "gain_curve", "limited": True,
                                "speech_ms": 20, "touched_ms": 0, "max_reduction_db": 0}}
    jsonio.write_atomic(report_path, report)
    film = SimpleNamespace(
        id="neutral-gate-export", dir=folder,
        path=lambda rel: folder / rel,
        rel=lambda path: str(Path(path).relative_to(folder)),
    )
    stem_path = folder / "sound/mix/stems/dialogue.wav"

    class Context:
        def __init__(self):
            self.film, self.report, self.report_path = film, report, report_path
            self.stem_paths = {"dialogue": stem_path}

        def stem(self, name):
            assert name == "dialogue"
            return np.full((20, 2), 0.25)

        def inputs(self, *paths, include_missing=False):
            assert include_missing is True
            return gates.inputs_for(folder, paths)

    return Context()


def _install_context(monkeypatch, context):
    monkeypatch.setattr(verify, "_engine", lambda: gates.engine("neutral", "1"))
    monkeypatch.setattr(verify, "load", lambda folder: context.film)
    monkeypatch.setattr(verify, "_Ctx", lambda film: context)
    monkeypatch.setattr(verify, "external_gates", lambda: {})
    monkeypatch.setattr(sound_cli, "film", lambda args: context.film.dir)


@pytest.mark.parametrize(("value", "label"), NONFINITE)
def test_actual_limiter_export_is_strict_without_changing_its_evidence(tmp_path, monkeypatch, value, label):
    context = _limiter_context(tmp_path, value)
    _install_context(monkeypatch, context)
    original = context.report_path.read_bytes()
    result = verify.gate_voice_limiter(context)

    assert result.status == "UNCHECKED" and not result.passed
    assert result.details["share"] is value
    exported = result.to_dict()
    assert _strict(json.dumps(exported, allow_nan=False))["details"]["share"] == label
    assert exported["inputs"] == result.inputs and exported["engine"] == result.engine
    assert result.details["share"] is value and context.report_path.read_bytes() == original


def test_nested_diagnostics_preserve_ordinary_values_and_raw_containers():
    nan, positive, negative = float("nan"), float("inf"), float("-inf")
    details = {"nested": [{"nan": nan, "positive": positive, "negative": negative}],
               "tuple": (nan, 0.25), "finite": [-0.0, 0, 1, 0.1313, False, None, "NaN"]}
    result = _result(nan)
    result.details = details
    result.engine["diagnostics"] = {"reading": positive}
    exported = result.to_dict()

    parsed = _strict(json.dumps(exported, allow_nan=False))
    assert parsed["details"]["nested"] == [{"nan": "NaN", "positive": "Infinity", "negative": "-Infinity"}]
    assert exported["details"]["tuple"] == ("NaN", 0.25)
    assert parsed["details"]["finite"] == [-0.0, 0, 1, 0.1313, False, None, "NaN"]
    assert math.copysign(1, parsed["details"]["finite"][0]) == -1
    assert parsed["engine"]["diagnostics"]["reading"] == "Infinity"
    assert result.details is details and result.details["nested"][0]["nan"] is nan
    assert result.engine["diagnostics"]["reading"] is positive
    assert result.to_dict() == exported


@pytest.mark.parametrize(("value", "label"), NONFINITE)
def test_latest_projects_legacy_unknown_fields_without_rewriting_history(tmp_path, value, label):
    older = asdict(_result(value, gate="neutral.old"))
    older["future_diagnostic"] = {"reading": value, "flag": False}
    jsonio.write_atomic(tmp_path / "qa/gates.json", {"gates": {"neutral.old": older}})
    history = tmp_path / "qa/history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    prefix = (json.dumps(older) + "\n").encode()
    history.write_bytes(prefix)
    fresh = _result(value, gate="neutral.fresh")

    gates.write_report(tmp_path, [fresh])

    latest = _strict((tmp_path / "qa/gates.json").read_text())["gates"]
    assert latest["neutral.old"]["future_diagnostic"] == {"reading": label, "flag": False}
    for key in ("status", "checked_at", "inputs", "engine", "summary", "evidence"):
        assert latest["neutral.old"][key] == older[key]
    data = history.read_bytes()
    assert data.startswith(prefix)
    appended = data[len(prefix):].decode().splitlines()
    assert len(appended) == 1 and _strict(appended[0])["details"]["share"] == label
    assert fresh.details["share"] is value and older["future_diagnostic"]["reading"] is value


@pytest.mark.parametrize(("value", "label"), NONFINITE)
def test_legacy_readback_exports_strictly_and_retains_dependency_staleness(tmp_path, value, label):
    dependency = tmp_path / "fixture.json"
    dependency.write_bytes(b"before")
    result = _result(value)
    result.inputs = gates.inputs_for(tmp_path, [dependency])
    digest = result.inputs["fixture.json"]
    jsonio.write_atomic(tmp_path / "qa/gates.json", {"gates": {result.gate: asdict(result)}})

    readback = gates.load_report(tmp_path)[result.gate]
    assert readback.status == "UNCHECKED"
    exported = _strict(json.dumps(readback.to_dict(), allow_nan=False))
    assert exported["details"]["share"] == label
    gates.write_report(tmp_path, [readback])
    projected = gates.load_report(tmp_path)[result.gate]
    assert projected.details["share"] == label and projected.status == "UNCHECKED"

    dependency.write_bytes(b"after")
    stale = gates.load_report(tmp_path)[result.gate]
    assert stale.status == "STALE" and stale.details["was"] == "UNCHECKED"
    assert stale.inputs["fixture.json"] == digest and stale.details["share"] == label
    _strict(json.dumps(stale.to_dict(), allow_nan=False))


@pytest.mark.parametrize(("value", "label"), NONFINITE)
def test_sound_writers_keep_raw_report_and_write_strict_gate_documents(tmp_path, monkeypatch, value, label):
    context = _limiter_context(tmp_path, value)
    _install_context(monkeypatch, context)
    original, digest = context.report_path.read_bytes(), jsonio.sha256(context.report_path)

    [result] = verify.run(tmp_path, ["sound.voice_limiter"])

    assert result.status == "UNCHECKED" and result.details["share"] is value
    assert result.details["stage"] == "sound" and result.engine["rule_revision"] == 1
    assert result.inputs["sound/mix/final_mix.json"] == digest
    assert context.report_path.read_bytes() == original
    latest = _strict((tmp_path / "qa/gates.json").read_text())["gates"][result.gate]
    sound = _strict((tmp_path / "sound/mix/verify.json").read_text())
    assert latest["details"]["share"] == label
    assert sound["gates"][result.gate]["details"]["share"] == label
    assert sound["summary"]["status"] == "UNCHECKED"
    lines = (tmp_path / "qa/history.jsonl").read_text().splitlines()
    assert len(lines) == 1 and _strict(lines[0])["details"]["share"] == label


@pytest.mark.parametrize(("value", "label"), NONFINITE)
def test_sound_cli_and_installed_mcp_client_keep_strict_diagnostics(tmp_path, monkeypatch, value, label):
    context = _limiter_context(tmp_path, value)
    _install_context(monkeypatch, context)
    original = context.report_path.read_bytes()
    argv = ["verify", "--film", str(tmp_path), "--no-write", "sound.voice_limiter"]
    code, stdout, stderr = run_cli([*argv, "--json"])

    assert code == 1 and len(stdout.strip().splitlines()) == 1
    parsed = _strict(stdout)
    assert parsed["summary"]["status"] == "UNCHECKED"
    assert parsed["gates"][0]["details"]["share"] == label
    payload = tools.call(argv)
    assert payload["ok"] is False and payload["exit_code"] == 1
    small, text = tools.shrink(payload)
    assert "truncated" not in small and _strict(text) == small
    json.dumps(small, allow_nan=False)
    monkeypatch.setattr(tools, "status", lambda film=None: payload)

    from mcp import Client

    async def call():
        async with Client(server.build_server(), mode="legacy") as client:
            return await client.call_tool("status", {})

    response = asyncio.run(call())
    assert response.is_error is True
    assert _strict(response.content[0].text) == small
    assert response.structured_content == small
    json.dumps(response.structured_content, allow_nan=False)
    assert context.report_path.read_bytes() == original


@pytest.mark.parametrize("status", gates.STATUSES)
def test_finite_exports_and_shared_json_hashes_keep_their_contract(status):
    result = gates.GateResult(
        "neutral.finite", status, "finite control", evidence=["fixture.json"],
        engine=gates.engine("neutral", "1"), inputs={"fixture.json": None},
        checked_at="2026-01-01T00:00:00+00:00",
        details={"values": [-0.0, 0, 1, 0.1313, False, None, "Infinity"]},
    )
    raw = asdict(result)
    assert result.to_dict() == raw
    assert jsonio.dumps(result.to_dict()) == jsonio.dumps(raw)
    assert result.passed is (status == "PASS")
    assert jsonio.sha256_obj(result.to_dict()) == jsonio.sha256_obj(raw)
    nonfinite = {"value": float("nan")}
    assert jsonio.dumps(nonfinite) == '{\n "value": NaN\n}\n'
    assert jsonio.sha256_obj(nonfinite) == hashlib.sha256(b'{"value":NaN}').hexdigest()
