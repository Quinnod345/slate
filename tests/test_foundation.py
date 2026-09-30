"""Tests for the shared foundation: paths, clock, gates, ledger, secrets, procs, replay and the CLI dispatcher."""

import json
import os

import pytest

from slate import clock, gates, jsonio, ledger, paths, procs, replay, secrets
from slate.cli import run_cli
from slate.errors import MissingPrerequisite, SpendRefused, UsageError


def test_paths_follow_environment(slate_home):
    assert paths.DATA == slate_home["data"]
    assert paths.films() == slate_home["films"]
    assert paths.ledger_file() == slate_home["data"] / "ledger.jsonl"
    assert paths.BRAND_DIRS[0] == paths.REPO / "brands"


def test_film_dir_resolution(slate_home, monkeypatch, tmp_path):
    film = slate_home["films"] / "f1"
    film.mkdir()
    (film / "film.json").write_text("{}")
    assert paths.film_dir("f1") == film.resolve()
    assert paths.film_dir(str(film)) == film.resolve()
    monkeypatch.chdir(film)
    assert paths.film_dir() == film.resolve()
    with pytest.raises(UsageError):
        paths.film_dir("nope")


def test_clock_matches_make_shots_rounding():
    c = clock.Clock(30000, 1001)
    # S01 in film A: 6.0 s storyboard -> 180 frames, 6.006 s
    assert c.boundaries([6.0]) == [(0, 180)]
    assert round(c.secs_of(180), 6) == 6.006
    # drift-free: cumulative rounding, lengths are differences of rounded starts
    b = c.boundaries([1.001] * 1000)
    assert b[-1][1] == c.frame_of(1001.0)
    assert all(b[i][1] == b[i + 1][0] for i in range(len(b) - 1))
    assert c.timecode(c.frame_of_timecode("00:01:02:03")) == "00:01:02:03"


def test_gate_states_and_staleness(slate_home, tmp_path):
    film = tmp_path / "film"
    (film / "qa").mkdir(parents=True)
    src = film / "mix.wav"
    src.write_bytes(b"a")
    r = gates.GateResult.pass_("loudness", "-14.0 LUFS", inputs=gates.inputs_for(film, ["mix.wav"]),
                               engine=gates.engine("ffmpeg ebur128", "9.0.2"))
    gates.write_report(film, [r])
    assert gates.load_report(film)["loudness"].status == "PASS"
    src.write_bytes(b"b")
    stale = gates.load_report(film)["loudness"]
    assert stale.status == "STALE" and stale.details["was"] == "PASS"
    assert gates.overall([]) == "UNCHECKED"
    assert gates.overall([r, gates.GateResult.unchecked("ocr", "Vision unavailable")]) == "UNCHECKED"
    assert gates.exit_code([r]) == 0
    with pytest.raises(ValueError):
        gates.GateResult("x", "OK", "bad status")


def test_ledger_inflight_and_budgets(slate_home, monkeypatch):
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")  # budgets only bite on real spend
    est = ledger.estimate("elevenlabs", "tts", model="eleven_v4", chars=500)
    assert est.amount == 500 and est.unit == "credits"
    rid = ledger.begin(est, mode="supervised")
    ledger.request(rid, "req_123")
    assert [r["id"] for r in ledger.inflight()] == [rid]
    ledger.finish(rid, actual=480)
    assert ledger.inflight() == []
    assert ledger.spent("elevenlabs") == 480
    # autonomous monthly cap (default 20,000 credits)
    big = ledger.estimate("elevenlabs", "music", amount=25000)
    with pytest.raises(SpendRefused):
        ledger.check_budget(big, mode="autonomous")
    # autonomous Higgsfield needs a person's yes on every call
    hf = ledger.estimate("higgsfield", "plate", amount=2.31)
    with pytest.raises(SpendRefused):
        ledger.check_budget(hf, mode="autonomous")
    # ...and the default autonomous Higgsfield cap is $0 even with a yes, until a person raises it
    with pytest.raises(SpendRefused):
        ledger.check_budget(hf, mode="autonomous", approval_id="appr_1")
    # film budget: hard stop, 1.5x only for rework
    film = {"id": "f1", "budget": {"elevenlabs": 1000}}
    ok = ledger.estimate("elevenlabs", "tts", amount=400)
    ledger.check_budget(ok, film=film)
    with pytest.raises(SpendRefused):
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=1200), film=film)
    ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=1200), film=film, rework=True)


def test_ledger_spend_context_marks_failures(slate_home):
    est = ledger.estimate("elevenlabs", "tts", amount=10)
    with pytest.raises(RuntimeError):
        with ledger.spend(est) as row:
            row.request("r1")
            raise RuntimeError("vendor fell over")
    (only,) = ledger.rows()
    assert only["status"] == "failed" and only["request_id"] == "r1" and only["replay"] is True
    assert ledger.spent("elevenlabs") == 0  # replay rows never count


def test_ledger_reconcile(slate_home):
    ledger.snapshot("elevenlabs", 10000)
    ledger.snapshot("elevenlabs", 9000)
    rec = ledger.reconcile("elevenlabs")
    assert rec["status"] == "REVIEW" and rec["unexplained"] == 1000


def test_secrets_missing_names_connect(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "_keychain_read", lambda s: None)
    monkeypatch.setattr(secrets, "_keychain_has", lambda s: False)
    with pytest.raises(secrets.MissingSecret) as exc:
        secrets.get("elevenlabs")
    assert "slate connect elevenlabs" in exc.value.hint
    assert exc.value.exit_code == 3
    assert secrets.present("elevenlabs") is None


def test_secrets_env_and_redaction(monkeypatch):
    monkeypatch.setenv("HF_KEY", "id123:sekret456")
    assert secrets.get("higgsfield") == "id123:sekret456"
    assert secrets.present("higgsfield") == "env"
    assert "sekret456" not in secrets.redact("boom id123:sekret456 boom")


def test_store_interactive_refuses_agents(monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "codex")
    with pytest.raises(Exception) as exc:
        secrets.store_interactive("elevenlabs")
    assert "Terminal" in str(exc.value) or "Terminal" in (getattr(exc.value, "hint", "") or "")


def test_procs_browser_none_and_no_shell():
    env = procs.child_env({"BROWSER": "dia"})
    assert env["BROWSER"] == "none"
    out = procs.run(["/usr/bin/env"], check=True).stdout
    assert "BROWSER=none" in out
    with pytest.raises(TypeError):
        procs.run("echo hi")
    with pytest.raises(MissingPrerequisite):
        procs.run(["definitely-not-a-tool-xyz"])


def test_agent_context_override(monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "openclaw")
    assert procs.agent_context() == "openclaw"


def test_replay_fixture_and_faker(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_FIXTURES", str(tmp_path / "fx"))
    assert replay.fake_vendors()
    replay.record("elevenlabs", "k1", {"ok": 1})
    assert replay.respond("elevenlabs", "tts", {}, key="k1")["ok"] == 1
    replay.register_faker("elevenlabs", "tts", lambda key, req: {"audio": "tone"})
    assert replay.respond("elevenlabs", "tts", {"text": "hi"})["replay"] is True
    with pytest.raises(replay.ReplayMissing):
        replay.respond("higgsfield", "nothing", {})
    monkeypatch.setenv("SLATE_LIVE", "1")
    assert not replay.fake_vendors()


def test_jsonio_atomic_and_generated(tmp_path):
    p = tmp_path / "a" / "x.json"
    jsonio.write_atomic(p, jsonio.mark_generated({"a": 1}, "slate timing"))
    data = json.loads(p.read_text())
    assert data["generated"].startswith("do not edit by hand") and data["a"] == 1
    assert p.read_text().endswith("\n")


def test_cli_version_and_exit_codes(slate_home):
    code, out, err = run_cli(["--version"])
    assert code == 0 and "slate" in out
    code, out, err = run_cli([])
    assert code == 2


def test_child_path_prefers_arm64_homebrew_over_usr_local(monkeypatch):
    # /etc/paths puts /usr/local/bin first; stale Intel tools there must never win.
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    parts = procs.child_env()["PATH"].split(":")
    assert parts.index("/opt/homebrew/bin") < parts.index("/usr/local/bin")
    assert len(parts) == len(set(parts))
    node = procs.which("node")
    if node and os.path.exists("/opt/homebrew/bin/node"):
        assert node == "/opt/homebrew/bin/node"
