"""The ElevenLabs client: every endpoint in replay, ledger rows with request ids, formats by plan, and the live path
(against a fake HTTP layer: nothing here reaches ElevenLabs or spends)."""

import json

import numpy as np
import pytest

from slate import http, jsonio, ledger, replay
from slate.errors import MissingPrerequisite, SpendRefused, UsageError
from slate.vendors import elevenlabs
from slate.vendors.elevenlabs import Client
from slate.voice import audio

TEST_KEY = "sk_test_not_a_real_key_0123456789"
PLAN = {"positive_global_styles": ["warm pad"], "negative_global_styles": [],
        "sections": [{"section_name": "one", "positive_local_styles": [], "negative_local_styles": [],
                      "duration_ms": 4000, "lines": []}]}


@pytest.fixture
def fx(slate_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fixtures"))
    return tmp_path


def test_every_endpoint_in_replay(fx):
    c = Client()
    sub = c.subscription()
    assert sub["replay"] and sub["tier"] == "pro" and sub["lossless"] and sub["best_voice_format"] == "pcm_48000"
    info = c.voice_info("v1")
    assert info["category"] == "premade"
    res = c.tts(voice_id="v1", text="[warmly] One line, then another.", seed=7, previous_text="Before.",
                next_text="After.", out=fx / "t.wav")
    x, sr = audio.read_wav(fx / "t.wav")
    assert res["replay"] and sr == 48000 and len(x) > sr and res["format"] == "pcm_48000"
    assert [w["word"] for w in res["words"]] == ["One", "line,", "then", "another."]  # the tag isn't spoken
    assert res["request_id"].startswith("replay_") and res["client_request_id"].startswith("slate_")
    d = c.design(voice_description="A clear, bright narrator, mid thirties, steady pace.", text="x " * 60, seed=11,
                 guidance_scale=5, out_dir=fx / "prev")
    assert len(d["previews"]) == 3 and d["model_id"] == "eleven_ttv_v3"
    assert all((fx / "prev" / f"preview_{i}.wav").is_file() for i in (1, 2, 3))
    saved = c.save_voice(generated_voice_id=d["previews"][0]["generated_voice_id"], voice_name="n",
                         voice_description="a clear bright narrator")
    assert saved["voice_id"].startswith("replay_voice_")
    rm = c.remix(voice_id="v1", voice_description="Keep the voice; a cleaner recording.", text="y " * 60,
                 prompt_strength=0.25, seed=11, out_dir=fx / "remix")
    assert len(rm["previews"]) == 3 and rm["model_id"] == "eleven_ttv_v3"
    iso = c.isolate(audio=fx / "t.wav", out=fx / "iso.wav")
    assert iso["replay"] and (fx / "iso.wav").is_file()
    al = c.align(audio=fx / "t.wav", text="One line, then another.")
    assert [w["text"] for w in al["words"]] == ["One", "line,", "then", "another."]
    assert al["model"] == "elevenlabs_forced_alignment"
    # the fake aligner finds the fake voice's own word bursts
    assert abs(al["words"][1]["start"] - res["words"][1]["start"]) < 0.02
    m = c.compose_music(composition_plan=PLAN, seed=7, out=fx / "m.wav")
    y, msr = audio.read_wav(fx / "m.wav")
    assert m["model"] == "music_v2_5" and abs(len(y) / msr - 4.0) < 0.01 and y.ndim == 2


def test_ledger_rows_carry_request_ids_and_free_calls_write_none(fx):
    c = Client(film=None)
    c.subscription()
    c.voice_info("v1")
    assert ledger.rows() == []
    res = c.tts(voice_id="v1", text="Hello there.", out=fx / "a.wav")
    c.compose_music(prompt="a soft pad", music_length_ms=3000, out=fx / "m.wav")
    rows = {r["op"]: r for r in ledger.rows()}
    assert set(rows) == {"tts", "music"}
    tts = rows["tts"]
    assert tts["status"] == "done" and tts["replay"] is True and tts["provider"] == "elevenlabs"
    assert tts["request_ids"][0] == res["client_request_id"] and tts["request_id"] == res["request_id"]
    assert tts["estimate"]["amount"] == len("Hello there.")
    assert ledger.spent("elevenlabs") == 0  # replay never counts as spend


def test_model_id_is_always_sent_for_design_and_music(fx, monkeypatch):
    seen = {}
    real = replay.respond

    def spy(provider, op, request, key=None):
        seen[op] = request
        return real(provider, op, request, key)

    monkeypatch.setattr(replay, "respond", spy)
    c = Client()
    c.design(voice_description="A clear, bright narrator, mid thirties, steady pace.", text="z " * 60)
    c.compose_music(prompt="a soft pad", music_length_ms=3000)
    assert seen["design"]["body"]["model_id"] == "eleven_ttv_v3"
    assert seen["music"]["body"]["model_id"] == "music_v2_5"
    c.tts(voice_id="v", text="Hi.")
    assert seen["tts"]["body"]["model_id"] == "eleven_v4"


def test_validation_before_any_call(fx):
    c = Client()
    with pytest.raises(UsageError):
        c.design(voice_description="too short", text="x" * 150)
    with pytest.raises(UsageError):
        c.design(voice_description="A clear, bright narrator, mid thirties.", text="short text")
    with pytest.raises(UsageError):
        c.compose_music(composition_plan={"sections": [{"section_name": "a", "duration_ms": 1500}]})
    with pytest.raises(UsageError):
        c.compose_music(prompt="x", composition_plan=PLAN)
    with pytest.raises(UsageError):
        c.tts(voice_id="v", text="   ")
    assert ledger.rows() == []


@pytest.mark.parametrize("tier, fmt, lossless", [("pro", "pcm_48000", True), ("scale", "pcm_48000", True),
                                                 ("enterprise", "pcm_48000", True),
                                                 ("creator", "mp3_44100_192", False),
                                                 ("starter", "mp3_44100_128", False),
                                                 ("free", "mp3_44100_128", False)])
def test_best_format_by_plan(tier, fmt, lossless):
    chosen, why = elevenlabs.best_format(tier)
    assert chosen == fmt and elevenlabs.is_lossless(chosen) is lossless
    assert ("Pro or higher" in why) == (not lossless)


def test_plan_tier_from_the_subscription_decides_the_format(fx):
    key = replay.request_key("elevenlabs", "subscription", {"key": "elevenlabs"})
    replay.record("elevenlabs", key, {"json": {"tier": "creator", "character_count": 10, "character_limit": 100}})
    c = Client()
    sub = c.subscription()
    assert sub["tier"] == "creator" and not sub["lossless"] and sub["best_voice_format"] == "mp3_44100_192"
    choice = c.choose_format()
    assert choice["format"] == "mp3_44100_192" and "lossless needs Pro" in choice["reason"]


def test_autonomous_films_use_oneiros_key_and_pool(fx):
    c = Client(mode="autonomous")
    assert c.key_name == "elevenlabs_oneiro" and c.provider == "elevenlabs_oneiro"
    c.tts(voice_id="v", text="Hi there.")
    (row,) = ledger.rows()
    assert row["provider"] == "elevenlabs_oneiro" and row["mode"] == "autonomous"
    with pytest.raises(UsageError):
        Client(mode="autonomous", key="elevenlabs")


# ---------------------------------------------------------------------------------------------------------------
# The live path, against a fake HTTP layer


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.inflight_seen = []

    def __call__(self, method, url, *, headers=None, json_body=None, data=None, timeout=60, retries=3):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "json": json_body})
        self.inflight_seen.append([r.get("request_ids") for r in ledger.inflight()])
        if url.endswith("/v1/user/subscription"):
            return 200, {}, json.dumps({"tier": "pro", "character_count": 5, "character_limit": 100}).encode()
        if "/v1/voices/" in url:
            return 200, {}, json.dumps({"voice_id": "v", "category": "premade"}).encode()
        if "/v1/text-to-speech/" in url:
            if "output_format=pcm_48000" in url:
                raise http.HttpError(403, url, "output format not allowed on this plan")
            pcm = (np.sin(np.arange(4410) / 10) * 8000).astype("<i2").tobytes()
            return 200, {"Request-Id": "req_live_1", "Character-Cost": "12"}, pcm
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def live(fx, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setenv("ELEVENLABS_API_KEY", TEST_KEY)
    fake = FakeHttp()
    monkeypatch.setattr(http, "request", fake)
    return fake


def test_live_tts_falls_back_records_ids_and_keeps_the_key_out_of_urls(live, fx):
    c = Client()
    res = c.tts(voice_id="v", text="Twelve chars", seed=3, output_format="pcm_48000", out=fx / "live.wav")
    assert res["format"] == "pcm_44100" and res["format_fallbacks"] == [{"format": "pcm_48000", "http": 403}]
    assert res["request_id"] == "req_live_1" and res["character_cost"] == 12.0 and not res["replay"]
    x, sr = audio.read_wav(fx / "live.wav")
    assert sr == 44100 and len(x) == 4410
    for call in live.calls:
        assert TEST_KEY not in call["url"]
        assert call["headers"]["xi-api-key"] == TEST_KEY
    # each attempt had an in-flight row carrying our own request id before the vendor answered
    assert all(seen and seen[-1] and seen[-1][0].startswith("slate_") for seen in live.inflight_seen)
    rows = ledger.rows(op="tts")
    assert [r["status"] for r in rows] == ["failed", "done"]
    assert rows[-1]["request_id"] == "req_live_1" and rows[-1]["actual"] == 12.0 and rows[-1]["replay"] is False
    assert ledger.spent("elevenlabs") == 12.0


def test_live_spend_refused_before_any_request(live):
    c = Client(film={"id": "f1", "budget": {"elevenlabs": 5}})
    with pytest.raises(SpendRefused) as exc:
        c.tts(voice_id="v", text="This line costs more than five credits.", output_format="pcm_44100")
    assert exc.value.exit_code == 4
    assert live.calls == [] and ledger.rows() == []


def test_live_without_a_key_names_the_connect_command(fx, monkeypatch):
    from slate import secrets

    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "_keychain_read", lambda s: None)
    with pytest.raises(MissingPrerequisite) as exc:
        Client().subscription()
    assert "slate connect elevenlabs" in exc.value.hint and exc.value.exit_code == 3


def test_recorded_responses_replay_later(live, fx, monkeypatch):
    monkeypatch.setenv("SLATE_RECORD", "1")
    c = Client()
    c.tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "rec.wav")
    monkeypatch.delenv("SLATE_LIVE")
    monkeypatch.delenv("SLATE_RECORD")
    before = len(live.calls)
    again = Client().tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "again.wav")
    assert len(live.calls) == before and again["replay"]
    assert jsonio.sha256(fx / "again.wav") == jsonio.sha256(fx / "rec.wav")


def test_balance_hooks_for_ledger_snapshots(fx):
    assert elevenlabs.remaining_credits() == 1_000_000.0  # the replayed plan: nothing used of a million
    assert elevenlabs.remaining_credits("elevenlabs_oneiro") == 1_000_000.0
    try:
        from slate.jobs import resume
    except ImportError:
        return
    reader = resume.balance_reader("elevenlabs")
    assert reader is not None and reader() == 1_000_000.0
