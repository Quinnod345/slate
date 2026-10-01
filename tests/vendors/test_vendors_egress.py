"""The OpenClaw gateway route (the phase-1 doctor spike's finding).

Inside the OpenClaw gateway, its egress proxy turns api.elevenlabs.io and api.higgsfield.ai away at the tunnel (403), so:

- a live vendor call there is never tried: the `slate` command queues itself for the launchd job runner (which works
  outside the gateway), or refuses with the exact next step;
- a host the owner's allowlist (~/.openclaw/openclaw.json secrets.egressProxy.allowedHosts, never edited by Slate) lets
  through goes ahead as normal, and so does anything outside the gateway, or inside a queued job;
- a proxy's 403 at the tunnel, wherever it happens, is a MissingPrerequisite that names the allowlist and the queue
  route, says nothing was billed, and never reads as the vendor rejecting the key.

The proxy errors here are faked; nothing reaches the network.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

from slate import http, ledger
from slate.errors import MissingPrerequisite, SlateError, TryLater
from slate.jobs import model, store
from slate.vendors import egress, elevenlabs, higgsfield

TUNNEL = "Couldn't reach {host}: <urlopen error Tunnel connection failed: 403 Forbidden>"


@pytest.fixture
def gateway(slate_home, monkeypatch):
    """This process inside the OpenClaw gateway, live, with the owner's allowlist in a scratch file."""
    config = slate_home["tmp"] / "openclaw.json"
    config.write_text(json.dumps({"agents": {"x": 1}, "secrets": {"egressProxy": {
        "enabled": True, "allowedHosts": ["pypi.org", "huggingface.co"]}}}))
    monkeypatch.setenv(egress.CONFIG_ENV, str(config))
    monkeypatch.setenv("SLATE_CONTEXT", "openclaw")
    monkeypatch.delenv("SLATE_JOB_ID", raising=False)
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(egress, "queue_route", lambda: (False, "Slate's launchd job runner (dev.slate.jobs) isn't "
                                                               "installed"))
    calls: list[str] = []

    def no_network(method, url, **kw):
        calls.append(url)
        raise AssertionError(f"a request left the gateway: {method} {url}")

    monkeypatch.setattr(http, "request", no_network)
    return {**slate_home, "config": config, "calls": calls}


def _allow(config: Path, *hosts: str, enabled: bool = True) -> None:
    config.write_text(json.dumps({"secrets": {"egressProxy": {"enabled": enabled, "allowedHosts": list(hosts)}}}))


def _no_status(exc: SlateError) -> None:
    """Doctor reads a `status` of 401/403 as the vendor rejecting the key: a proxy refusal must never carry one."""
    assert "status" not in exc.details and getattr(exc, "status", None) is None


# ---------------------------------------------------------------------------------------------------------------
# Inside the gateway: never tried


def test_inside_the_gateway_nothing_is_sent_and_the_next_step_is_exact(gateway):
    with pytest.raises(egress.EgressBlocked) as exc:
        elevenlabs.Client().subscription()
    err = exc.value
    assert isinstance(err, MissingPrerequisite) and err.exit_code == 3
    assert "egress proxy doesn't let api.elevenlabs.io through" in err.message and "nothing was sent" in err.message
    assert "secrets.egressProxy.allowedHosts in ~/.openclaw/openclaw.json" in err.hint
    assert "Slate never edits it" in err.hint
    assert "slate job submit --kind elevenlabs --resource elevenlabs_tts -- slate" in err.hint  # the queue route
    assert "slate jobs agent install" in err.hint  # and what the queue needs first, when it isn't there
    assert err.details["egress_proxy"] and err.details["host"] == "api.elevenlabs.io"
    _no_status(err)
    assert gateway["calls"] == [] and store.all_jobs() == []
    with pytest.raises(egress.EgressBlocked, match="api.higgsfield.ai"):
        higgsfield.Client(key="hf-test", fake=False).estimate("bytedance/seedream/v4/text-to-image", {"prompt": "x"})
    assert gateway["calls"] == []
    assert json.loads(gateway["config"].read_text())["agents"] == {"x": 1}  # the owner's file, untouched


def test_a_probe_reports_the_proxy_and_never_queues(gateway, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(Path(egress.__file__).parents[1] / "__main__.py"), "gen", "probe"])
    monkeypatch.setattr(egress, "queue_route", lambda: (True, ""))
    probe = higgsfield.Client(key="hf-test", fake=False).probe()
    assert probe["status"] == "unreachable" and not probe["ok"]
    assert "egress proxy doesn't let api.higgsfield.ai through" in probe["summary"]
    assert "allowedHosts" in probe["hint"] and probe["egress"]["egress_proxy"]
    assert store.all_jobs() == [] and gateway["calls"] == []


def test_a_slate_command_in_the_gateway_queues_itself_once(gateway, monkeypatch):
    film = gateway["tmp"] / "films" / "reel"
    argv = ["voice", "tts", "--film", str(film), "--live"]
    monkeypatch.setattr(sys, "argv", [str(Path(egress.__file__).parents[1] / "__main__.py"), *argv])
    monkeypatch.setattr(egress, "queue_route", lambda: (True, ""))
    with pytest.raises(egress.QueuedOutsideGateway) as exc:
        elevenlabs.Client().subscription()
    err = exc.value
    assert err.exit_code == 3 and "didn't run here" in err.message  # never mistaken for the work being done
    job_id = err.details["job"]
    assert f"slate job wait {job_id}" in err.hint and "allowedHosts" in err.hint
    job = store.load(job_id)
    assert job["argv"] == [sys.executable, "-m", "slate", *argv] and job["state"] == "queued"
    assert job["resources"] == ["elevenlabs_tts"] and job["context"] == "openclaw" and job["kind"] == "voice-tts"
    assert "egress proxy" in job["note"]
    with pytest.raises(egress.QueuedOutsideGateway) as again:  # asked again: the same job, not a second one
        elevenlabs.Client().subscription()
    assert again.value.details["job"] == job_id and again.value.details["reused"]
    assert len(store.all_jobs()) == 1 and gateway["calls"] == []
    # A command that reports on this very context (doctor) never queues itself: it says how things stand.
    monkeypatch.setattr(sys, "argv", ["/x/slate", "doctor", "--json"])
    with pytest.raises(egress.EgressBlocked) as doctor:
        elevenlabs.Client().subscription()
    assert not isinstance(doctor.value, egress.QueuedOutsideGateway) and len(store.all_jobs()) == 1


def test_a_paid_call_in_the_gateway_writes_no_ledger_row(gateway, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/x/slate", "voice", "tts", "--live"])
    monkeypatch.setattr(egress, "queue_route", lambda: (True, ""))
    client = elevenlabs.Client()
    monkeypatch.setattr(client, "choose_format", lambda *a, **k: {"format": "pcm_48000", "wanted": "pcm_48000",
                                                                    "tier": "pro", "lossless": True, "reason": "test"})
    with pytest.raises(egress.QueuedOutsideGateway):
        client.tts(voice_id="v", text="Twelve chars", output_format="pcm_48000")
    assert ledger.rows() == []  # stopped before the in-flight row: nothing was begun that couldn't go


def test_the_owners_allowlist_lets_a_listed_host_through(gateway, monkeypatch):
    sent = []

    def ok(method, url, **kw):
        sent.append(url)
        return 200, {}, json.dumps({"tier": "pro", "character_count": 1, "character_limit": 10}).encode()

    monkeypatch.setattr(http, "request", ok)
    _allow(gateway["config"], "pypi.org", "api.elevenlabs.io")
    assert elevenlabs.Client().subscription()["tier"] == "pro" and len(sent) == 1
    _allow(gateway["config"], "*.elevenlabs.io")
    assert egress.host_allowed("api.elevenlabs.io") is True and egress.host_allowed("api.higgsfield.ai") is False
    _allow(gateway["config"], enabled=False)  # the proxy is off: nothing stands in the way
    assert egress.host_allowed("api.higgsfield.ai") is True
    gateway["config"].write_text("{not json")
    assert egress.host_allowed("api.elevenlabs.io") is None  # unreadable: the gateway is treated as blocking
    with pytest.raises(egress.EgressBlocked):
        egress.route("elevenlabs")


def test_a_queued_job_and_a_terminal_go_straight_through(gateway, monkeypatch):
    sent = []

    def ok(method, url, **kw):
        sent.append(url)
        return 200, {}, json.dumps({"tier": "creator"}).encode()

    monkeypatch.setattr(http, "request", ok)
    monkeypatch.setenv("SLATE_JOB_ID", "j-20261001-000000-abcd")  # the launchd runner's job carries the context along
    assert egress.gateway_context() is None
    assert elevenlabs.Client().subscription()["tier"] == "creator"
    monkeypatch.delenv("SLATE_JOB_ID")
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    elevenlabs.Client().subscription()
    assert len(sent) == 2


# ---------------------------------------------------------------------------------------------------------------
# A proxy's 403 at the tunnel, wherever it happens


def test_a_tunnel_403_is_a_missing_prerequisite_that_names_the_allowlist(gateway, monkeypatch):
    monkeypatch.setenv("SLATE_JOB_ID", "j-20261001-000000-abcd")  # e.g. a job a runner inside the gateway started

    def tunnel(method, url, **kw):
        raise SlateError(TUNNEL.format(host="api.elevenlabs.io"), hint="Check the network.")

    monkeypatch.setattr(http, "request", tunnel)
    with pytest.raises(egress.EgressBlocked) as exc:
        elevenlabs.Client().subscription()
    err = exc.value
    assert err.exit_code == 3 and not isinstance(err, TryLater)
    assert "refused the tunnel to api.elevenlabs.io (HTTP 403)" in err.message and "nothing was billed" in err.message
    assert "OpenClaw gateway" in err.message  # the context says whose proxy it is
    assert "secrets.egressProxy.allowedHosts in ~/.openclaw/openclaw.json" in err.hint
    assert "slate job submit" in err.hint and "Slate never edits it" in err.hint
    assert err.details["tunnel_status"] == 403
    _no_status(err)


def test_a_tunnel_403_outside_the_gateway_names_the_proxy_plainly(slate_home, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test-not-a-real-key")
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "SSL_CERT_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http, "request", lambda *a, **k: (_ for _ in ()).throw(
        SlateError(TUNNEL.format(host="api.elevenlabs.io"))))
    with pytest.raises(egress.EgressBlocked) as exc:
        elevenlabs.Client().subscription()
    assert exc.value.message.startswith("An HTTPS proxy refused the tunnel to api.elevenlabs.io (HTTP 403)")
    assert "HTTPS_PROXY" in exc.value.hint and "allowedHosts" in exc.value.hint
    # The gateway's own trust bundle in the environment says whose proxy it is, even without the context.
    monkeypatch.setenv("SSL_CERT_FILE", "/Users/x/.openclaw/secret-egress-proxy/gateway-abc/trust-bundle.pem")
    with pytest.raises(egress.EgressBlocked, match="OpenClaw gateway's egress proxy refused"):
        elevenlabs.Client().subscription()
    # Anything else is still the error it was.
    monkeypatch.setattr(http, "request", lambda *a, **k: (_ for _ in ()).throw(
        SlateError("Couldn't reach api.elevenlabs.io: <urlopen error [Errno 8] nodename nor servname provided>")))
    with pytest.raises(SlateError) as other:
        elevenlabs.Client().subscription()
    assert not isinstance(other.value, egress.EgressBlocked)


def test_a_higgsfield_submit_stopped_at_the_tunnel_is_never_counted(slate_home, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    monkeypatch.setenv("SLATE_CONTEXT", "openclaw")
    monkeypatch.setenv("SLATE_JOB_ID", "j-20261001-000000-abcd")
    sent = []

    def transport(method, url, *, headers, json_body=None, data=None, timeout=60):
        sent.append(url)
        raise SlateError(TUNNEL.format(host="api.higgsfield.ai"))

    path = "bytedance/seedream/v4/text-to-image"
    client = higgsfield.Client(transport=transport, key="hf-test", fake=False, sleep=lambda s: None)
    client._quotes[higgsfield._body_key(path, {"prompt": "x"})] = {"usd": 0.03}  # quoted earlier
    with pytest.raises(egress.EgressBlocked) as exc:
        client.submit(path, {"prompt": "x"}, key="take-S01-1")
    assert exc.value.exit_code == 3 and not isinstance(exc.value, TryLater)  # not "maybe billed, try again"
    assert len(sent) == 1  # never resent: it can't have reached Higgsfield
    rows = ledger.rows(provider="higgsfield")
    assert len(rows) == 1 and rows[0]["status"] == "failed" and rows[0]["actual"] == 0.0
    assert rows[0]["result"]["egress_proxy"] and ledger.spent("higgsfield") == 0.0
    with pytest.raises(egress.EgressBlocked, match="api.higgsfield.ai"):
        client.estimate(path, {"prompt": "y"})


def test_a_taken_over_row_stays_open_when_the_resend_is_stopped_at_the_tunnel(slate_home, monkeypatch):
    """An earlier run may have reached Higgsfield before it died: its row keeps counting, whatever this resend met."""
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    monkeypatch.setenv("SLATE_CONTEXT", "claude-code")
    path, body = "bytedance/seedream/v4/text-to-image", {"prompt": "x"}
    est = higgsfield.ledger_estimate(path, body, {"usd": 0.5})
    with monkeypatch.context() as m:  # a row whose writer is gone, as after a crash before any answer
        m.setattr(ledger, "_identity", lambda: {"pid": 99999, "proc_start": "gone", "job": None})
        row = ledger.begin(est, key="take-S02-1", note="crashed")

    def transport(method, url, *, headers, json_body=None, data=None, timeout=60):
        raise SlateError(TUNNEL.format(host="api.higgsfield.ai"))

    client = higgsfield.Client(transport=transport, key="hf-test", fake=False, sleep=lambda s: None)
    client._quotes[higgsfield._body_key(path, body)] = {"usd": 0.5}
    with pytest.raises(egress.EgressBlocked):
        client.submit(path, body, key="take-S02-1")
    (again,) = ledger.rows(id=row)
    assert again["status"] == "inflight" and ledger.spent("higgsfield") == 0.5  # still counted at its estimate


def test_a_download_stopped_at_the_tunnel_names_the_media_host(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_CONTEXT", "openclaw")
    monkeypatch.setenv("SLATE_JOB_ID", "j-20261001-000000-abcd")

    def refused(url, dest, **kw):
        raise urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))

    monkeypatch.setattr(http, "download", refused)
    client = higgsfield.Client(key="hf-test", fake=False)
    with pytest.raises(egress.EgressBlocked) as exc:
        client.download("https://cdn.example.net/out/abc.mp4", tmp_path, "S01_t1")
    assert "cdn.example.net" in exc.value.message and "the request never reached Higgsfield" in exc.value.message


def test_only_the_slate_command_itself_queues_itself(monkeypatch):
    module = str(Path(egress.__file__).parents[1] / "__main__.py")
    monkeypatch.setattr(sys, "argv", [module, "voice", "tts", "--detach", "--live"])
    assert egress.command_words() == ["voice", "tts", "--live"]  # `python -m slate …` (the shim)
    monkeypatch.setattr(sys, "argv", ["/Users/x/code/slate/.venv/bin/slate", "gen", "run", "--shot", "S01"])
    assert egress.command_words() == ["gen", "run", "--shot", "S01"]  # the console script
    for argv in ([module, "mcp"], [module, "--json", "doctor"], [module, "connect", "elevenlabs"],
                 ["/usr/bin/pytest", "tests"], [module]):
        monkeypatch.setattr(sys, "argv", argv)
        assert egress.command_words() is None, argv  # the MCP server, this context's own checks, a library caller


def test_queue_commands_are_exact():
    words = ["voice", "tts", "--film", "/films/my reel", "--live"]
    assert egress.queue_command(words, "elevenlabs") == (
        "slate job submit --kind voice-tts --resource elevenlabs_tts -- slate voice tts --film '/films/my reel' --live")
    assert egress.queue_command(["score", "compose"], "elevenlabs", "/v1/music").split(" -- ")[0].endswith(
        "--resource elevenlabs_music")
    assert "--resource higgsfield" in egress.queue_command(["gen", "run", "--shot", "S01"], "higgsfield")
    assert set(egress.RESOURCE.values()) <= set(model.RESOURCES)
