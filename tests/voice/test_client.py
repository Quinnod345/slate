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
# music_v1's section plan, and the chunk plan music_v2 and music_v2_5 take instead (they reject v1 sections)
V1_PLAN = {"positive_global_styles": ["warm pad"], "negative_global_styles": [],
           "sections": [{"section_name": "one", "positive_local_styles": [], "negative_local_styles": [],
                         "duration_ms": 4000, "lines": []}]}
PLAN = {"chunks": [{"text": "[one]", "duration_ms": 4000, "positive_styles": ["warm pad"],
                    "negative_styles": ["vocals"]}]}


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
    assert tts["request_ids"] == [res["request_id"]]  # the vendor's id, never a made-up one nothing can poll
    assert tts["result"]["client_request_id"] == res["client_request_id"] and tts["key"].startswith("tts-")
    assert tts["estimate"]["amount"] == len("Hello there.")
    assert ledger.spent("elevenlabs") == 0  # replay never counts as spend


def test_music_plans_go_out_in_the_shape_their_model_takes(fx, monkeypatch):
    seen = []
    real = replay.respond

    def spy(provider, op, request, key=None):
        if op == "music":
            seen.append(request["body"])
        return real(provider, op, request, key)

    monkeypatch.setattr(replay, "respond", spy)
    c = Client()
    m = c.compose_music(composition_plan=PLAN, seed=7, out=fx / "v2.wav")
    assert m["seconds_planned"] == 4.0
    v1 = c.compose_music(composition_plan=V1_PLAN, model_id="music_v1", seed=7, out=fx / "v1.wav")
    assert v1["model"] == "music_v1"
    chunks, sections = seen
    assert chunks["model_id"] == "music_v2_5" and "chunks" in chunks["composition_plan"]
    assert "respect_sections_durations" not in chunks  # a music_v1 option; v2 keeps chunk durations anyway
    assert sections["model_id"] == "music_v1" and sections["respect_sections_durations"] is True
    assert elevenlabs.music_plan_schema("music_v2") == elevenlabs.music_plan_schema("music_v2_5") == "chunks"
    assert elevenlabs.music_plan_schema("music_v1") == "sections"


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
    with pytest.raises(UsageError) as exc:
        c.compose_music(composition_plan=V1_PLAN)  # a v1 section plan on music_v2_5: rejected before any spend
    assert "chunks" in exc.value.message and "slate score plan" in exc.value.hint
    with pytest.raises(UsageError):
        c.compose_music(composition_plan=PLAN, model_id="music_v1")  # and a chunk plan on music_v1
    with pytest.raises(UsageError):
        c.compose_music(composition_plan={"chunks": [{"text": "[a]", "duration_ms": 1500, "positive_styles": []}]})
    with pytest.raises(UsageError):
        c.compose_music(composition_plan={"chunks": [{"text": "[a]", "duration_ms": 130000,
                                                      "positive_styles": []}]})
    with pytest.raises(UsageError):
        c.compose_music(composition_plan={"chunks": [{"text": "", "duration_ms": 4000, "positive_styles": []}]})
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
        self.inflight_seen.append([(r.get("key"), r.get("request_ids")) for r in ledger.inflight()])
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
    # each paid attempt had an in-flight row carrying its stable request key (and no made-up request id) before the
    # vendor answered, so a crash leaves a row the queue can find and close as orphaned
    tts_seen = [seen for call, seen in zip(live.calls, live.inflight_seen) if "/v1/text-to-speech/" in call["url"]]
    assert len(tts_seen) == 2 and all(seen and seen[-1][0].startswith("tts-") and not seen[-1][1]
                                      for seen in tts_seen)
    rows = ledger.rows(op="tts")
    assert [r["status"] for r in rows] == ["failed", "done"]
    assert rows[-1]["request_id"] == "req_live_1" and rows[-1]["actual"] == 12.0 and rows[-1]["replay"] is False
    assert ledger.spent("elevenlabs") == 12.0


def _tts_calls(live):
    return [c for c in live.calls if "/v1/text-to-speech/" in c["url"]]


def test_an_answer_paid_for_before_a_crash_is_never_paid_for_again(live, fx, monkeypatch):
    """The vendor answered (and billed); the process died before the take was written. The rerun of the same request
    uses the parked answer: no second call, no second row, and the park is cleared once the file is written."""
    c = Client()
    real_write = Client.write_audio

    def crash(*a, **k):
        raise KeyboardInterrupt("the Mac went to sleep")

    monkeypatch.setattr(Client, "write_audio", staticmethod(crash))
    with pytest.raises(KeyboardInterrupt):
        c.tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "take.wav")
    assert len(_tts_calls(live)) == 1 and not (fx / "take.wav").exists()
    parked = list((fx / "data" / "cache" / "paid" / "elevenlabs").glob("*.json"))
    assert len(parked) == 1
    monkeypatch.setattr(Client, "write_audio", staticmethod(real_write))
    res = Client().tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "take.wav")
    assert len(_tts_calls(live)) == 1  # nothing was paid for twice
    assert res["reused_parked_answer"] and res["request_id"] == "req_live_1" and (fx / "take.wav").is_file()
    rows = ledger.rows(op="tts")
    assert [r["status"] for r in rows] == ["done"] and ledger.spent("elevenlabs") == 12.0
    assert not list((fx / "data" / "cache" / "paid" / "elevenlabs").glob("*"))  # unparked once written
    again = Client().tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "take2.wav")
    assert len(_tts_calls(live)) == 2 and not again.get("reused_parked_answer")  # a deliberate rerun pays


def test_a_row_abandoned_mid_call_is_orphaned_and_counted(live, fx, monkeypatch):
    """A process died waiting on ElevenLabs (no answer, no request id). The queue's paid_call finds the row by its key,
    closes it as orphaned (still counted at its estimate), and makes the call once more under a new row."""
    c = Client()
    c.tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "t.wav")
    key = ledger.rows(op="tts")[-1]["key"]
    est = ledger.estimate("elevenlabs", "tts", model="eleven_v4", chars=12)
    with monkeypatch.context() as m:
        m.setattr(ledger, "_identity", lambda: {"pid": 99999, "proc_start": "gone", "job": None})
        ledger.begin(est, key=key, note="died waiting")
    c.tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "t2.wav")
    assert [r["status"] for r in ledger.rows(key=key)] == ["done", "orphaned", "done"]
    assert ledger.spent("elevenlabs") == 12.0 * 3  # the orphan counts at its estimate until a reconcile


def test_a_crash_between_the_answer_and_the_row_closing_settles_the_row(live, fx, monkeypatch):
    c = Client()
    res = c.tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "first.wav")
    key = ledger.rows(op="tts")[-1]["key"]
    # re-create the state a crash leaves: the answer parked, its row abandoned in flight
    c._park(key, {"request-id": "req_live_1", "character-cost": "12"},  # noqa: SLF001
            {"data": (fx / "first.wav").read_bytes()[44:], "encoding": "pcm_s16le", "rate": 44100, "channels": 1,
             "words": None})
    est = ledger.estimate("elevenlabs", "tts", model="eleven_v4", chars=12)
    with monkeypatch.context() as m:
        m.setattr(ledger, "_identity", lambda: {"pid": 99999, "proc_start": "gone", "job": None})
        row = ledger.begin(est, key=key, note="died after the answer")
    calls = len(_tts_calls(live))
    again = Client().tts(voice_id="v", text="Twelve chars", output_format="pcm_44100", out=fx / "second.wav")
    assert len(_tts_calls(live)) == calls and again["reused_parked_answer"]
    (settled,) = ledger.rows(id=row)
    assert settled["status"] == "done" and settled["actual"] == 12.0
    assert res["request_id"] == again["request_id"] == "req_live_1"


def test_a_rate_limit_is_busy_and_an_empty_balance_stops_and_asks(live, fx, monkeypatch):
    def refuse(status, body):
        def fake(method, url, *, headers=None, json_body=None, data=None, timeout=60, retries=3):
            if url.endswith("/v1/user/subscription"):
                return live(method, url, headers=headers, json_body=json_body, data=data, timeout=timeout)
            raise http.HttpError(status, url, body)
        return fake

    monkeypatch.setattr(http, "request", refuse(429, '{"detail": {"status": "too_many_concurrent_requests"}}'))
    with pytest.raises(Exception) as exc:
        Client().tts(voice_id="v", text="Twelve chars", output_format="pcm_44100")
    assert exc.value.exit_code == 5 and "Nothing was billed" in exc.value.message
    monkeypatch.setattr(http, "request", refuse(401, '{"detail": {"status": "quota_exceeded"}}'))
    with pytest.raises(SpendRefused) as exc:
        Client().tts(voice_id="v", text="Twelve chars", output_format="pcm_44100")
    assert exc.value.exit_code == 4 and "never buys credits" in exc.value.message
    assert all(r["status"] == "failed" for r in ledger.rows(op="tts")) and ledger.spent("elevenlabs") == 0


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
