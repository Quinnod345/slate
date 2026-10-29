"""Recorded replay provenance follows the loaded fixture without changing its bytes.

These regressions use local JSON and paired payload files. The client calls stop at their replay response paths;
no speech synthesis, media decoding, spend, credentials, network or child process is permitted.
"""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from slate import http, ledger, procs, replay, secrets
from slate.gen import run as gen_run
from slate.vendors import egress, elevenlabs, higgsfield


@pytest.fixture(autouse=True)
def isolated_replay(slate_home, monkeypatch):
    root = slate_home["tmp"] / "vendor-fixtures"
    root.mkdir()
    monkeypatch.setenv("SLATE_FIXTURES", str(root))
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "1")
    monkeypatch.setenv("SLATE_RECORD", "0")
    monkeypatch.delenv("SLATE_LIVE", raising=False)
    monkeypatch.setattr(replay, "_FAKERS", dict(replay._FAKERS))

    def forbidden(*args, **kwargs):
        pytest.fail("A local replay regression reached a live, spend, credential or child-process boundary")

    monkeypatch.setattr(http, "request", forbidden)
    monkeypatch.setattr(procs, "run", forbidden)
    monkeypatch.setattr(secrets, "get", forbidden)
    monkeypatch.setattr(ledger, "begin", forbidden)
    monkeypatch.setattr(egress, "route", forbidden)
    return root


def _write_fixture(provider, key, response, wrapped):
    if wrapped:
        return replay.record(provider, key, response)
    path = replay.fixture_path(provider, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    return path


def _snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("wrapped", [True, False], ids=["recorded-envelope", "legacy-response"])
@pytest.mark.parametrize("flag", [False, None, 0, True, "absent"], ids=["false", "null", "zero", "true", "absent"])
def test_recorded_response_is_replay_without_rewriting_provider_fields(isolated_replay, wrapped, flag):
    request = {"operation": "neutral fixture read"}
    key = replay.request_key("fixture-provider", "read", request)
    response = {"value": "captured value", "provider_fields": {"replay": False, "labels": ["keep", "these"]}}
    if flag != "absent":
        response["replay"] = flag
    original = deepcopy(response)
    _write_fixture("fixture-provider", key, response, wrapped)
    before = _snapshot(isolated_replay)

    answer = replay.respond("fixture-provider", "read", request)

    assert answer["replay"] is True
    assert answer["replay_key"] == key
    assert {k: v for k, v in answer.items() if k not in ("replay", "replay_key")} == {
        k: v for k, v in original.items() if k not in ("replay", "replay_key")
    }
    assert response == original
    assert _snapshot(isolated_replay) == before


@pytest.mark.parametrize("wrapped", [True, False], ids=["recorded-envelope", "legacy-response"])
def test_explicit_loaded_key_replaces_conflicting_reserved_key(isolated_replay, wrapped):
    response = {"replay": True, "replay_key": "different-fixture", "provider_note": "keep this note"}
    _write_fixture("fixture-provider", "selected-fixture", response, wrapped)
    before = _snapshot(isolated_replay)

    answer = replay.respond("fixture-provider", "read", {"request": "does not choose the key"}, key="selected-fixture")

    assert answer["replay"] is True
    assert answer["replay_key"] == "selected-fixture"
    assert answer["provider_note"] == response["provider_note"]
    assert _snapshot(isolated_replay) == before


def test_elevenlabs_replay_uses_selected_paired_audio_not_response_key(isolated_replay, monkeypatch):
    request = {"text": "Fixture audio."}
    key = replay.request_key("elevenlabs", "tts", request)
    selected_audio = b"\x01\x00\x02\x00\x03\x00\x04\x00"
    other_audio = b"\x09\x00\x08\x00\x07\x00\x06\x00"
    response = {"replay": False, "replay_key": "different-audio", "encoding": "pcm_s16le", "sample_rate": 32000,
                "channels": 1, "channels_from": "fixture", "headers": {"request-id": "selected-request"},
                "provider_note": "paired audio belongs to the selected fixture"}
    replay.record("elevenlabs", key, response, payloads={"audio": selected_audio})
    replay.record("elevenlabs", "different-audio", {"encoding": "pcm_s16le"}, payloads={"audio": other_audio})
    before = _snapshot(isolated_replay)
    client = elevenlabs.Client()

    def forbidden(*args, **kwargs):
        pytest.fail("The paired-payload regression reached ElevenLabs live transport")

    monkeypatch.setattr(client, "_live", forbidden)
    headers, audio, encoding, rate, channels, words = client._audio_result(
        "tts", request, "pcm_32000", path="/fixture-unused", query={}
    )

    assert audio == selected_audio
    assert audio != other_audio
    assert (encoding, rate, channels, words) == ("pcm_s16le", 32000, 1, None)
    assert headers["request-id"] == "selected-request"
    assert _snapshot(isolated_replay) == before
    answer = replay.respond("elevenlabs", "tts", request)
    assert answer["replay"] is True and answer["replay_key"] == key
    assert answer["provider_note"] == response["provider_note"]
    assert _snapshot(isolated_replay) == before


@pytest.mark.parametrize("flag", [False, None, 0], ids=["false", "null", "zero"])
def test_higgsfield_recording_keeps_generated_take_marked_as_replay(isolated_replay, flag):
    request = {"path": "fixture/image", "body": {"prompt": "Neutral fixture."}}
    key = replay.request_key("higgsfield", "submit", request)
    response = {"request_id": "fixture-request", "replay": flag, "provider_note": "keep provider metadata"}
    replay.record("higgsfield", key, response)
    before = _snapshot(isolated_replay)
    client = higgsfield.Client(fake=True)

    status, answer = client._send("submit", "POST", request["path"], body=request["body"], replay_request=request)
    take = {"id": "fixture-take"}
    gen_run._took(take, {**answer, "ledger_row": "fixture-ledger-row", "quote": {}})

    assert status == 200
    assert take["replay"] is True
    assert take["request_id"] == response["request_id"]
    assert answer["provider_note"] == response["provider_note"]
    assert answer["replay_key"] == key
    assert _snapshot(isolated_replay) == before


@pytest.mark.parametrize("flag", [False, None, 0], ids=["false", "null", "zero"])
def test_faker_reserved_fields_are_normalized_on_a_copy(isolated_replay, monkeypatch, flag):
    response = {"replay": flag, "replay_key": "different-fixture", "provider_fields": {"replay": False}}
    before = deepcopy(response)
    monkeypatch.setitem(replay._FAKERS, ("fixture-provider", "stand-in"), lambda key, request: response)
    request = {"value": "fixture"}

    answer = replay.respond("fixture-provider", "stand-in", request)

    assert answer["replay"] is True
    assert answer["replay_key"] == replay.request_key("fixture-provider", "stand-in", request)
    assert answer["provider_fields"] == before["provider_fields"]
    assert response == before
    assert _snapshot(isolated_replay) == {}


def test_missing_fixture_still_refuses_without_faker(isolated_replay):
    with pytest.raises(replay.ReplayMissing):
        replay.respond("unregistered-fixture-provider", "missing", {"value": "fixture"})
    assert _snapshot(isolated_replay) == {}
