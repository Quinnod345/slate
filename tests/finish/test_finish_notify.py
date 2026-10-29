"""slate.notify: iMessage and push, dry runs, replay and SLATE_NO_NOTIFY, and mocked sends (nothing is ever sent from a
test: the transport is a fake, and the real one refuses to run under pytest)."""

from __future__ import annotations

import json

import pytest

from slate import notify
from slate.errors import MissingPrerequisite, SlateError, UsageError

HANDLE = "+15550100200"


@pytest.fixture
def cfg(fin, settings_toml):
    def write(extra: str = "") -> None:
        settings_toml(f'[notify]\nimessage_handle = "{HANDLE}"\n' + extra)

    write()
    return write


def test_dry_run_says_what_would_be_sent(cfg, sends, tmp_path):
    cut = tmp_path / "phone.mp4"
    cut.write_bytes(b"x")
    rec = notify.imessage("Your film is ready.", cut, dry_run=True)
    assert rec["sent"] is False and rec["dry_run"] is True and "dry run" in rec["why"]
    assert rec["attachment"] == str(cut.resolve()) and rec["to"] == "…0200"
    assert HANDLE not in json.dumps(rec)  # records never hold the whole handle
    assert sends == []


def test_replay_mode_sends_nothing(cfg, sends):
    rec = notify.imessage("hello")
    assert rec["sent"] is False and "replay mode" in rec["why"] and sends == []


def test_no_notify_env_sends_nothing(cfg, sends, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setenv("SLATE_NO_NOTIFY", "1")
    assert notify.imessage("hello")["sent"] is False and sends == []


def test_missing_handle_is_a_missing_prerequisite(fin):
    with pytest.raises(MissingPrerequisite, match="No iMessage handle"):
        notify.imessage("hello")


def test_openclaw_route_with_the_file(cfg, sends, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda c: "/opt/fixture/openclaw")
    cut = tmp_path / "phone.mp4"
    cut.write_bytes(b"x")
    rec = notify.imessage("Ready: v2", cut)
    assert rec["sent"] is True and rec["route"] == "openclaw"
    argv = sends[-1]
    assert argv[:5] == ["/opt/fixture/openclaw", "message", "send", "--channel", "imessage"]
    assert argv[argv.index("--target") + 1] == HANDLE and argv[argv.index("-m") + 1] == "Ready: v2"
    assert argv[argv.index("--media") + 1] == str(cut.resolve())


def test_messages_fallback_when_openclaw_fails(cfg, sends, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda c: "/opt/fixture/openclaw")
    seen = []

    def transport(argv, *, timeout):
        seen.append(argv)
        if "openclaw" in argv[0]:
            return {"returncode": 0, "stdout": '{"ok": false, "error": "channel down"}', "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(notify, "transport", transport)
    rec = notify.imessage("text with \"quotes\" and a newline\nhere")
    assert rec["sent"] is True and rec["route"] == "osascript"
    osa = seen[-1]
    assert osa[0] == "/usr/bin/osascript"
    # the text and handle arrive as arguments, never pasted into the script
    assert osa[-2:] == [HANDLE, "text with \"quotes\" and a newline\nhere"]
    script = "\n".join(osa[i + 1] for i, a in enumerate(osa) if a == "-e")
    assert HANDLE not in script and "quotes" not in script and "item 2 of argv" in script


def test_every_route_failing_is_an_error(cfg, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda c: None)
    monkeypatch.setattr(notify, "transport", lambda argv, *, timeout: {"returncode": 1, "stdout": "", "stderr": "no"})
    with pytest.raises(SlateError, match="didn't go through"):
        notify.imessage("hello")


def test_push_by_command_without_a_shell(cfg, sends, monkeypatch):
    cfg('push_command = ["fixture-push", "--title", "{title}", "--body", "{body}"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    rec = notify.push("Reel v1 held for you", "Watch the phone cut; $(rm -rf ~) stays text")
    assert rec["sent"] is True and rec["route"] == "command"
    assert sends[-1] == ["fixture-push", "--title", "Slate: Reel v1 held for you", "--body",
                         "Watch the phone cut; $(rm -rf ~) stays text"]


def test_push_through_openclaw_node(cfg, sends, monkeypatch):
    cfg('push_node = "node-fixture"\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda c: "/opt/fixture/openclaw")
    rec = notify.push("Held", "x" * 400)
    argv = sends[-1]
    assert argv[:4] == ["/opt/fixture/openclaw", "gateway", "call", "push.test"]
    params = json.loads(argv[argv.index("--params") + 1])
    assert params["nodeId"] == "node-fixture" and len(params["body"]) == notify.MAX_BODY
    assert rec["sent"] is True


def test_push_needs_a_route(cfg):
    with pytest.raises(MissingPrerequisite, match="No push route"):
        notify.push("t", "b")


def test_push_command_must_be_a_list(cfg, monkeypatch):
    cfg('push_command = "fixture-push {title}"\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    with pytest.raises(UsageError, match="argv list"):
        notify.push("t", "b")


def test_both_reports_a_missing_route_without_stopping_the_other(cfg, sends, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda c: None)
    out = notify.both("hello", title="t")
    assert out[0]["sent"] is True and out[1]["missing"] is True


def test_the_real_transport_refuses_under_pytest():
    with pytest.raises(RuntimeError, match="real transport"):
        notify._real_transport(["/usr/bin/true"], timeout=1)


def test_mask():
    assert notify.mask("+1 555 010 0200") == "…0200"
    assert notify.mask("someone@example.com") == "so…@example.com"


def test_gen_calls_match_the_interface(cfg):
    """slate.gen calls notify.imessage(message) and notify.push(title, message): both must accept that."""
    assert notify.imessage("a message", dry_run=True)["channel"] == "imessage"
    cfg('push_command = ["fixture-push", "{title}"]\n')
    assert notify.push("title", "a message", dry_run=True)["channel"] == "push"


@pytest.mark.parametrize("failed_channels", [("imessage",), ("push",), ("imessage", "push")])
@pytest.mark.parametrize("failure_mode", ["exit", "exception"])
def test_both_channel_isolation_with_configured_transports(cfg, monkeypatch, failed_channels, failure_mode):
    """Real notification handlers use a recorded transport; no process or message leaves the test."""
    cfg('imessage_route = "osascript"\npush_command = ["fixture-push", "{title}", "{body}"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    seen, warnings = [], []
    monkeypatch.setattr(notify.log, "warn", warnings.append)

    def transport(argv, *, timeout):
        channel = "imessage" if argv[0] == "/usr/bin/osascript" else "push"
        seen.append(channel)
        if channel in failed_channels:
            if failure_mode == "exception":
                raise SlateError(f"{channel} fixture transport failed")
            return {"returncode": 1, "stdout": "", "stderr": f"{channel} fixture transport failed"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(notify, "transport", transport)
    out = notify.both("The neutral phone cut is held.", title="Held")
    assert seen == ["imessage", "push"]
    assert [r["channel"] for r in out] == ["imessage", "push"]
    assert [r["sent"] for r in out] == [c not in failed_channels for c in ("imessage", "push")]
    for result in out:
        if not result["sent"]:
            assert result["error"] == "error" and result["exit_code"] == 1
            assert "fixture transport failed" in result["why"]
            assert any(w.startswith(result["channel"] + ": ") for w in warnings)


@pytest.mark.parametrize("control", ["interrupt", "terminated", "async_cancel", "future_cancel", "unexpected"])
def test_both_channel_isolation_does_not_swallow_cancellation_or_unexpected_errors(cfg, monkeypatch, control):
    import asyncio
    from concurrent.futures import CancelledError

    errors = {"interrupt": KeyboardInterrupt(), "terminated": SystemExit(143),
              "async_cancel": asyncio.CancelledError(), "future_cancel": CancelledError(),
              "unexpected": ValueError("unexpected fixture bug")}
    failure = errors[control]
    cfg('imessage_route = "osascript"\npush_command = ["fixture-push"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    seen = []

    def transport(argv, *, timeout):
        seen.append(argv[0])
        raise failure

    monkeypatch.setattr(notify, "transport", transport)
    with pytest.raises(type(failure)) as got:
        notify.both("Held", title="Held")
    assert got.value is failure and seen == ["/usr/bin/osascript"]


def test_both_channel_isolation_keeps_usage_errors(cfg, sends, monkeypatch):
    cfg('imessage_route = "invalid"\npush_command = ["fixture-push"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    with pytest.raises(UsageError, match="imessage_route"):
        notify.both("Held", title="Held")
    assert sends == []


def test_both_channel_isolation_keeps_retry_controls(monkeypatch):
    from slate.errors import TryLater

    seen = []
    retry = TryLater("fixture notification backoff", retry_after_s=12)

    def delayed(*args, **kwargs):
        raise retry

    monkeypatch.setattr(notify, "imessage", delayed)
    monkeypatch.setattr(notify, "push", lambda *args, **kwargs: seen.append("push"))
    with pytest.raises(TryLater) as got:
        notify.both("Held", title="Held")
    assert got.value is retry and got.value.retry_after_s == 12 and seen == []


@pytest.mark.parametrize("control", ["retry", "refused", "busy", "usage"])
@pytest.mark.parametrize("entrypoint", ["imessage", "both"])
def test_imessage_channel_isolation_preserves_configured_transport_controls(cfg, monkeypatch, control, entrypoint):
    from slate.errors import Busy, Refused, TryLater

    controls = {"retry": TryLater("fixture backoff", retry_after_s=12), "refused": Refused("fixture refusal"),
                "busy": Busy("fixture resource busy"), "usage": UsageError("fixture route usage")}
    failure = controls[control]
    cfg('imessage_route = "auto"\npush_command = ["fixture-push"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda _cfg: "/fixture/openclaw")
    seen = []

    def transport(argv, *, timeout):
        seen.append(list(argv))
        raise failure

    monkeypatch.setattr(notify, "transport", transport)
    with pytest.raises(type(failure)) as got:
        if entrypoint == "both":
            notify.both("Held", title="Held")
        else:
            notify.imessage("Held")
    assert got.value is failure
    assert len(seen) == 1 and seen[0][0] == "/fixture/openclaw"
    if control == "retry":
        assert got.value.retry_after_s == 12 and got.value.exit_code == 75


@pytest.mark.parametrize("failure_type", ["ordinary", "missing"])
def test_imessage_channel_isolation_preserves_ordinary_and_missing_tool_fallback(cfg, monkeypatch, failure_type):
    cfg('imessage_route = "auto"\npush_command = ["fixture-push"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    monkeypatch.setattr(notify, "_openclaw", lambda _cfg: "/fixture/openclaw")
    seen = []

    def transport(argv, *, timeout):
        seen.append(argv[0])
        if argv[0] == "/fixture/openclaw":
            error_type = SlateError if failure_type == "ordinary" else MissingPrerequisite
            raise error_type("fixture first route unavailable")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(notify, "transport", transport)
    out = notify.both("Held", title="Held")
    assert seen == ["/fixture/openclaw", "/usr/bin/osascript", "fixture-push"]
    assert [r["sent"] for r in out] == [True, True]
    assert out[0]["channel"] == "imessage" and out[0]["route"] == "osascript"
    assert out[1]["channel"] == "push" and out[1]["route"] == "command"
