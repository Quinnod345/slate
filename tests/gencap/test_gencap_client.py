"""The Higgsfield Cloud API client: replay, estimate-before-submit, idempotent submits (and no blind retries), probe,
ledger, resume after a crash, uploads, keys, and the estimate read the right way round (usd is the price)."""

from __future__ import annotations

import importlib
import io
import json
import urllib.error
import urllib.request

import pytest

from slate import ledger, secrets
from slate.errors import SlateError, UsageError
from slate.vendors import higgsfield

PATH = "bytedance/seedance-2.5/image-to-video"
BODY = {"prompt": "a quiet shoreline", "image_url": "https://example.com/a.png", "duration": 2}


class FakeTransport:
    """Answers like the Cloud API and records every request (method, url, headers, body).

    Submits honour Idempotency-Key the way the API does: the same key and body return the original request (no new
    job), the same key with another body is a 422. `lose` makes the first N submits create their job and then lose
    the answer (a timeout on the way back); `fail_status` answers the first N submits with that status instead."""

    def __init__(self, *, status_answers=None, estimate=None, on_status=None, lose=0, fail_status=None, fail=0):
        self.calls: list[dict] = []
        self.status_answers = list(status_answers or [{"status": "completed", "video": {"url": "https://cdn/x.mp4"}}])
        self.estimate = estimate or {"usd": 1.0, "discount": {"usd": 0.5}, "pricing_description": "test"}
        self.on_status = on_status
        self.submits = 0
        self.jobs: dict[str, tuple[str, str]] = {}  # idempotency key -> (body, request id)
        self.lose, self.fail_status, self.fail = lose, fail_status, fail

    def __call__(self, method, url, *, headers, json_body=None, data=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": json_body})
        path = url.split("api.higgsfield.ai/", 1)[-1]
        if path.startswith("estimate/"):
            return 200, {}, json.dumps(self.estimate).encode()
        if path.startswith("requests/") and path.endswith("/status"):
            if self.on_status:
                self.on_status(path.split("/")[1])
            answer = self.status_answers.pop(0) if len(self.status_answers) > 1 else self.status_answers[0]
            return 200, {}, json.dumps(answer).encode()
        if path.startswith("requests/") and path.endswith("/cancel"):
            return 200, {}, json.dumps({"status": "canceled"}).encode()
        if path == "files/generate-upload-url":
            return 200, {}, json.dumps({"upload_url": "https://s3.example/put?X-Amz-Signature=sig",
                                        "public_url": "https://cdn.example/u.png",
                                        "upload_headers": {"Content-Type": "image/png"}}).encode()
        if method == "PUT":
            return 200, {}, b""
        if self.fail:
            self.fail -= 1
            return self.fail_status, {}, b'{"detail": "upstream error"}'
        key, body = headers.get("Idempotency-Key"), json.dumps(json_body, sort_keys=True)
        if key and key in self.jobs:
            if self.jobs[key][0] != body:
                return 422, {}, b'{"detail": "Idempotency key reused with a different request body"}'
            request_id = self.jobs[key][1]
        else:
            self.submits += 1
            request_id = f"req-{self.submits}"
            if key:
                self.jobs[key] = (body, request_id)
        if self.lose:
            self.lose -= 1
            raise SlateError("Couldn't reach api.higgsfield.ai: timed out")  # created, but the answer was lost
        return 200, {}, json.dumps({"status": "queued", "request_id": request_id}).encode()

    def posts(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith(PATH)
                and "/estimate/" not in c["url"]]


def test_replay_runs_the_whole_path_without_the_network(slate_home, monkeypatch, tmp_path):
    def no_network(*a, **k):
        raise AssertionError("replay mode must never reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    client = higgsfield.Client()
    assert client.fake and client.lane == "cloud-api"
    quote = client.estimate(PATH, BODY)
    assert quote["replay"] is True and quote["usd"] == pytest.approx(0.924)  # registry: $0.462/s × 2 s
    first, second = client.submit(PATH, BODY), client.submit(PATH, BODY)
    assert first["request_id"] != second["request_id"]  # two submits are two jobs, in replay too
    final = client.wait(first["request_id"])
    assert final["status"] == "completed"
    (clip,) = [client.download(u, tmp_path, "take") for u in client.outputs(final)]
    from slate.capture import media

    info = media.probe(clip)
    assert info["duration"] == pytest.approx(2.0, abs=0.05) and info["width"] > info["height"]
    still = higgsfield.Client().submit("higgsfield-ai/soul/cinema", {"prompt": "a street", "aspect_ratio": "9:16"})
    done = client.wait(still["request_id"])
    image = client.download(client.outputs(done)[0], tmp_path, "still")
    from PIL import Image

    with Image.open(image) as im:
        assert im.size[1] > im.size[0]  # a flat test frame in the asked aspect
    assert client.probe()["ok"] and client.probe()["replay"]
    assert client.cancel(first["request_id"])["canceled"] is True
    rows = ledger.rows(provider="higgsfield")
    assert rows and all(r["replay"] for r in rows)
    assert ledger.spent("higgsfield") == 0  # replay never counts as spend


def test_estimate_comes_before_every_submit(slate_home):
    fake = FakeTransport()
    client = higgsfield.Client(transport=fake, key="kid:ksecret", fake=False)
    client.submit(PATH, BODY)
    other = {**BODY, "prompt": "another shot"}
    client.submit(PATH, other)
    client.estimate(PATH, BODY)
    client.submit(PATH, BODY)
    seen_estimates: list = []
    for call in fake.calls:
        if "/estimate/" in call["url"]:
            seen_estimates.append(call["body"])
        elif call["method"] == "POST" and call["url"].endswith(PATH):
            assert call["body"] in seen_estimates, "a submit went out without a quote for that exact body"
    submits = [c for c in fake.calls if c["method"] == "POST" and c["url"].endswith(PATH) and "/estimate/" not in c["url"]]
    assert len(submits) == 3 and len(seen_estimates) == 3  # the cached quote still came first for that body
    for call in fake.calls:
        assert call["headers"].get("Authorization") == "Key kid:ksecret"  # the key travels in the header only
        assert "ksecret" not in call["url"]


class _Resp(io.BytesIO):
    def __init__(self, payload: dict, status: int = 200):
        super().__init__(json.dumps(payload).encode())
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_lost_submit_is_resent_with_the_same_idempotency_key_and_reads_retry(slate_home, monkeypatch):
    """Over the real HTTP layer: a submit whose answer is lost is sent again with the same key and body (the API
    answers a repeat with the original request), a few times at most; GETs retry on their own."""
    monkeypatch.setenv("SLATE_LIVE", "1")
    from slate import http

    monkeypatch.setattr(http, "_sleep", lambda attempt, retry_after: None)
    counts = {"submit": 0, "status": 0}
    agents, keys, bodies = [], [], []

    def fake_urlopen(req, timeout=60):
        agents.append(req.get_header("User-agent"))
        url = req.full_url
        if "/estimate/" in url:
            return _Resp({"usd": 1.0})
        if url.endswith("/status"):
            counts["status"] += 1
            if counts["status"] < 3:
                raise urllib.error.URLError("connection reset")
            return _Resp({"status": "completed", "images": []})
        counts["submit"] += 1
        keys.append(req.get_header("Idempotency-key"))
        bodies.append(req.data)
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = higgsfield.Client(key="kid:ksecret", sleep=lambda s: None)
    assert not client.fake
    with pytest.raises(higgsfield.HiggsfieldError) as exc:
        client.submit(PATH, BODY, key="film f1 S02 t1")
    assert counts["submit"] == higgsfield.SUBMIT_TRIES
    assert len(set(keys)) == 1 and keys[0] == higgsfield.idempotency_key("film f1 S02 t1")
    assert len(set(bodies)) == 1  # the same body every time: a different one would be a new request
    assert "safe to run it again" in (exc.value.hint or "")
    (row,) = ledger.rows(provider="higgsfield")
    assert row["status"] == "failed" and row["result"]["ambiguous"] is True
    assert row["actual"] == 1.0  # counted at the quoted price, in case the job was created
    assert client.status("req-123")["status"] == "completed"
    assert counts["status"] == 3  # reads retry with backoff
    assert all(a and not a.startswith("Python-urllib") for a in agents)  # Cloudflare bans urllib's default


def test_a_submit_without_an_idempotency_key_is_never_retried(slate_home):
    fake = FakeTransport(lose=5)
    client = higgsfield.Client(transport=fake, key="k:s", fake=False, sleep=lambda s: None)
    with pytest.raises(higgsfield.HiggsfieldError) as exc:
        client.submit(PATH, BODY, idempotent=False)
    (post,) = fake.posts()  # exactly once: a blind retry could create, and bill, a second job
    assert "Idempotency-Key" not in post["headers"]
    assert "never retries" in (exc.value.hint or "")
    (row,) = ledger.rows(provider="higgsfield")
    assert row["result"]["ambiguous"] is True and row["actual"] == 1.0


def test_every_submit_carries_a_key_derived_from_its_ledger_row(slate_home):
    fake = FakeTransport()
    client = higgsfield.Client(transport=fake, key="k:s", fake=False)
    sub = client.submit(PATH, BODY, key="film f1 S02 t1")
    (post,) = fake.posts()
    assert post["headers"]["Idempotency-Key"] == sub["idempotency_key"] == higgsfield.idempotency_key("film f1 S02 t1")
    (row,) = ledger.rows(id=sub["ledger_row"])
    assert row["key"] == "film f1 S02 t1" and row["estimate"]["details"]["idempotency_key"] == sub["idempotency_key"]
    key = sub["idempotency_key"]
    assert 1 <= len(key) <= 255 and key.isascii() and key.isprintable() and " " not in key
    a, b = client.submit(PATH, BODY), client.submit(PATH, BODY)  # no identity given: two calls, two requests
    assert a["idempotency_key"] != b["idempotency_key"] and a["request_id"] != b["request_id"]


def test_a_lost_answer_is_resent_and_charged_once(slate_home):
    fake = FakeTransport(lose=1)
    client = higgsfield.Client(transport=fake, key="k:s", fake=False, sleep=lambda s: None)
    sub = client.submit(PATH, BODY, key="film f1 S02 t1")
    assert len(fake.posts()) == 2 and fake.submits == 1  # sent twice, one job
    assert sub["request_id"] == "req-1"
    (row,) = ledger.rows(provider="higgsfield")
    assert row["request_id"] == "req-1" and row["status"] == "inflight"


def test_a_server_error_is_resent_and_a_changed_body_under_the_same_key_is_named(slate_home):
    fake = FakeTransport(fail_status=503, fail=1)
    client = higgsfield.Client(transport=fake, key="k:s", fake=False, sleep=lambda s: None)
    sub = client.submit(PATH, BODY, key="film f1 S02 t1")
    assert len(fake.posts()) == 2 and sub["request_id"] == "req-1"
    with pytest.raises(higgsfield.HiggsfieldError) as exc:
        client.submit(PATH, {**BODY, "prompt": "something else"}, key="film f1 S02 t1")
    assert exc.value.status == 422 and "different body" in exc.value.message
    assert fake.submits == 1  # nothing new was created or billed
    failed = [r for r in ledger.rows(provider="higgsfield") if r["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["actual"] == 0.0


def _crashed_row(monkeypatch, est, *, key, request_id=None):
    """An in-flight row written by a process that's gone (as after a crash), optionally with its request id."""
    with monkeypatch.context() as m:  # only this: undo() would also undo the test's Slate home
        m.setattr(ledger, "_identity", lambda: {"pid": 99999, "proc_start": "gone", "job": None})
        row = ledger.begin(est, key=key, note="crashed")
    if request_id:
        ledger.request(row, request_id)
    return row


def test_a_rerun_takes_over_the_abandoned_row_instead_of_paying_again(slate_home, monkeypatch):
    importlib.import_module("slate.jobs.resume")
    est = higgsfield.ledger_estimate(PATH, BODY, {"usd": 1.0})
    # 1. the crash came after Higgsfield answered: wait on that request, send nothing
    row = _crashed_row(monkeypatch, est, key="film f1 S02 t1", request_id="req-77")
    fake = FakeTransport()
    client = higgsfield.Client(transport=fake, key="k:s", fake=False)
    sub = client.submit(PATH, BODY, key="film f1 S02 t1")
    assert sub["resumed"] and sub["request_id"] == "req-77" and sub["ledger_row"] == row
    assert fake.posts() == []
    # 2. the crash came before any answer: send it again with the same key, on the same row
    row2 = _crashed_row(monkeypatch, est, key="film f1 S02 t2")
    sub2 = client.submit(PATH, BODY, key="film f1 S02 t2")
    (post,) = fake.posts()
    assert post["headers"]["Idempotency-Key"] == higgsfield.idempotency_key("film f1 S02 t2")
    assert sub2["resumed"] and sub2["ledger_row"] == row2
    assert len(ledger.rows(provider="higgsfield")) == 2  # no second row: nothing is counted twice
    (again,) = ledger.rows(id=row2)
    assert again["request_id"] == sub2["request_id"] and again.get("adopted_by")


def test_the_estimate_is_read_the_right_way_round(slate_home):
    """/estimate's usd is the price (after any discount); discount.usd is the saving. A real answer: Kling Pro 5 s."""
    quote = {"usd": 0.308, "discount": {"usd": 0.252}, "credits": 4.93}
    assert higgsfield.quote_usd(quote) == 0.308
    assert higgsfield.quote_saving_usd(quote) == 0.252
    assert higgsfield.quote_list_usd(quote) == pytest.approx(0.56)
    est = higgsfield.ledger_estimate(PATH, BODY, quote)
    assert est.amount == 0.308 and est.details["list_usd"] == pytest.approx(0.56)
    assert "discounted_usd" not in est.details
    fake = FakeTransport(estimate=quote)
    client = higgsfield.Client(transport=fake, key="k:s", fake=False)
    sub = client.submit(PATH, BODY)
    client.wait(sub["request_id"])
    (row,) = ledger.rows(id=sub["ledger_row"])
    assert row["status"] == "done" and row["actual"] == 0.308  # the price is what a finished job costs


def test_the_queue_settles_an_abandoned_row_through_the_clients_hook(slate_home, monkeypatch):
    resume = importlib.import_module("slate.jobs.resume")
    assert resume.poller("higgsfield") is higgsfield.resume_poll
    est = higgsfield.ledger_estimate(PATH, BODY, {"usd": 2.31, "discount": {"usd": 0.46}})
    rid = higgsfield.Client().submit(PATH, BODY)["request_id"]  # replay: its status answers completed
    row = _crashed_row(monkeypatch, est, key="film f1 S09 t1", request_id=rid)
    (outcome,) = resume.recover(key="film f1 S09 t1")
    assert outcome["status"] == "done"
    (settled,) = ledger.rows(id=row)
    assert settled["status"] == "done" and settled["actual"] == 2.31  # the price, never the saving


def test_probe_semantics(slate_home, monkeypatch):
    def transport(method, url, *, headers, json_body=None, data=None, timeout=60):
        assert method == "GET" and url.endswith(f"/requests/{higgsfield.PROBE_REQUEST_ID}/status")
        return (404, {}, b'{"detail":"not found"}') if headers.get("Authorization") == "Key good:key" \
            else (401, {}, b'{"detail":"unauthorized"}')

    ok = higgsfield.Client(transport=transport, key="good:key").probe()
    assert ok["ok"] and ok["status"] == "ok" and ok["http"] == 404
    bad = higgsfield.Client(transport=transport, key="bad:key").probe()
    assert not bad["ok"] and bad["status"] == "rejected" and bad["http"] == 401
    for var in ("HF_KEY", "HF_API_KEY", "HF_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(secrets, "_keychain_read", lambda service: None)
    called = []
    none = higgsfield.Client(transport=lambda *a, **k: called.append(1), fake=False).probe()
    assert none["status"] == "no_key" and not called  # no key: nothing is sent
    assert ledger.rows() == []  # a probe is free: no ledger row


def test_request_id_is_on_the_ledger_row_before_anyone_waits(slate_home):
    seen = {}

    def check_row(request_id):
        rows = [r for r in ledger.inflight() if r.get("request_id") == request_id]
        seen[request_id] = len(rows)

    fake = FakeTransport(on_status=check_row)
    client = higgsfield.Client(transport=fake, key="kid:ksecret", fake=False)
    sub = client.submit(PATH, BODY, note="t1")
    final = client.wait(sub["request_id"])
    assert seen[sub["request_id"]] == 1  # the in-flight row carried the id while we were polling
    (row,) = ledger.rows(id=sub["ledger_row"])
    assert row["status"] == "done" and row["actual"] == 1.0  # usd is the price: what a finished job costs
    assert row["estimate"]["amount"] == 1.0 and row["estimate"]["details"]["saving_usd"] == 0.5
    assert final["status"] == "completed"


def test_failed_and_moderated_jobs_are_not_billed(slate_home):
    for state in ("failed", "nsfw"):
        client = higgsfield.Client(transport=FakeTransport(status_answers=[{"status": state}]), key="k:s", fake=False)
        sub = client.submit(PATH, {**BODY, "prompt": state})
        client.wait(sub["request_id"])
        (row,) = ledger.rows(id=sub["ledger_row"])
        assert row["status"] == "failed" and row["actual"] == 0.0


def test_wait_backs_off_and_a_timeout_leaves_the_job_running(slate_home):
    clock = {"t": 0.0}
    delays: list[float] = []

    def sleep(seconds):
        delays.append(seconds)
        clock["t"] += seconds

    fake = FakeTransport(status_answers=[{"status": "in_progress"}])
    client = higgsfield.Client(transport=fake, key="k:s", fake=False, sleep=sleep, monotonic=lambda: clock["t"])
    sub = client.submit(PATH, BODY)
    out = client.wait(sub["request_id"], timeout_s=40)
    assert out["status"] == "timeout"
    assert 2.0 <= delays[0] < 2.5 and 3.0 <= delays[1] < 3.5  # 2 s, growing by half each poll
    assert all(d < 10.5 for d in delays) and max(delays) >= 10.0  # capped at 10 s (plus jitter)
    assert [r["id"] for r in ledger.inflight()] == [sub["ledger_row"]]  # still running, still on the ledger


def test_cancel_closes_the_row_as_cancelled(slate_home):
    client = higgsfield.Client(transport=FakeTransport(), key="k:s", fake=False)
    sub = client.submit(PATH, BODY)
    assert client.cancel(sub["request_id"])["canceled"] is True
    (row,) = ledger.rows(id=sub["ledger_row"])
    assert row["status"] == "cancelled" and row["actual"] == 0.0


def test_upload_uses_the_presigned_url_without_the_api_key(slate_home, tmp_path):
    from PIL import Image

    png = tmp_path / "ref.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(png)
    fake = FakeTransport()
    url = higgsfield.Client(transport=fake, key="k:s", fake=False).upload(png)
    assert url == "https://cdn.example/u.png"
    put = [c for c in fake.calls if c["method"] == "PUT"][0]
    assert put["headers"] == {"Content-Type": "image/png"}  # exactly the returned headers: no Authorization
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    with pytest.raises(UsageError):
        higgsfield.Client(transport=fake, key="k:s", fake=False).upload(txt)


def test_bad_paths_and_ids_are_refused_before_sending(slate_home):
    client = higgsfield.Client(transport=FakeTransport(), key="k:s", fake=False)
    for path in ("", "requests/abc/status", "estimate/x/y", "no-slash"):
        with pytest.raises(UsageError):
            client.estimate(path, BODY)
    with pytest.raises(UsageError):
        client.wait("../../etc")


def test_the_lane_is_the_cloud_api_only():
    lane = higgsfield.describe_lane()
    assert lane["lane"] == "cloud-api" and lane["base"] == "https://api.higgsfield.ai"
    assert "not funded" in lane["app_credits"]
    assert "sync_agents" in lane["denied_connector_tools"]
