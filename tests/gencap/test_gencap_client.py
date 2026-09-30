"""The Higgsfield Cloud API client: replay, estimate-before-submit, no POST retry, probe, ledger, uploads, keys."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from slate import ledger, secrets
from slate.errors import UsageError
from slate.vendors import higgsfield

PATH = "bytedance/seedance-2.5/image-to-video"
BODY = {"prompt": "a quiet shoreline", "image_url": "https://example.com/a.png", "duration": 2}


class FakeTransport:
    """Answers like the Cloud API and records every request (method, url, headers, body)."""

    def __init__(self, *, status_answers=None, estimate=None, on_status=None):
        self.calls: list[dict] = []
        self.status_answers = list(status_answers or [{"status": "completed", "video": {"url": "https://cdn/x.mp4"}}])
        self.estimate = estimate or {"usd": 1.0, "discount": {"usd": 0.5}, "pricing_description": "test"}
        self.on_status = on_status
        self.submits = 0

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
        self.submits += 1
        return 200, {}, json.dumps({"status": "queued", "request_id": f"req-{self.submits}"}).encode()


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


def test_submit_is_never_retried_but_status_reads_are(slate_home, monkeypatch):
    """A POST that times out may have created (and billed) a job, so it is sent exactly once. GETs retry."""
    monkeypatch.setenv("SLATE_LIVE", "1")
    from slate import http

    monkeypatch.setattr(http, "_sleep", lambda attempt, retry_after: None)
    counts = {"submit": 0, "status": 0}
    agents = []

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
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = higgsfield.Client(key="kid:ksecret")
    assert not client.fake
    with pytest.raises(higgsfield.HiggsfieldError) as exc:
        client.submit(PATH, BODY)
    assert counts["submit"] == 1
    assert "never retries" in (exc.value.hint or "")
    (row,) = ledger.rows(provider="higgsfield")
    assert row["status"] == "failed" and row["result"]["ambiguous"] is True
    assert client.status("req-123")["status"] == "completed"
    assert counts["status"] == 3  # reads retry with backoff
    assert all(a and not a.startswith("Python-urllib") for a in agents)  # Cloudflare bans urllib's default


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
    assert row["status"] == "done" and row["actual"] == 0.5  # the discounted price is what's billed
    assert row["estimate"]["amount"] == 1.0  # the list price is what budgets are held to
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
