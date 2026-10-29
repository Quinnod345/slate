"""The runner's live machinery, driven against a stand-in agent (fake_agent.py): no model is ever called.

Covers the environment every run gets, the slice's stdin pipe and its kill, the BROWSER tripwire and the child-process
probe end to end, OpenClaw's collect step, recording with scrubbing, and replay of recorded runs.
"""

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from slate.errors import UsageError
from slate.evals import commands, runner, suite, transcript


def _one(root, case_id):
    configs = suite.load_configs(root)
    cases = {c.id: c for c in suite.load_cases(root, configs=configs)}
    return cases[case_id], configs


def _checks(result):
    return {c["gate"]: c["status"] for c in result["checks"]}


def test_every_run_gets_the_eval_environment(fake_suite, slate_home, monkeypatch):
    root = fake_suite({"claude-code": "env", "pursuit-slice": "env"}, cases=["negative-email"])
    monkeypatch.setenv("HF_KEY", "not-a-real-key")
    monkeypatch.setenv("SLATE_LIVE", "1")
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    summary = runner.live([(case, "claude-code"), (case, "pursuit-slice")], configs, out=runs, run_id="t1")
    assert summary["overall"] == "PASS", summary["headline"]
    for cid in ("claude-code", "pursuit-slice"):
        pair = runs / "t1" / "negative-email" / cid
        env = json.loads(transcript.load(pair, "claude-stream-json").final)
        assert env["BROWSER"] == str(root / "tripwire" / "browser")
        assert env["SLATE_FAKE_VENDORS"] == "1" and env["SLATE_LIVE"] is None and env["HF_KEY"] is None
        assert env["SLATE_FILMS"] == str(pair / "work" / "films") and env["SLATE_DATA"] == str(pair / "slate-data")
        assert env["HIGGSFIELD_CREDENTIALS_PATH"] == str(pair / "higgsfield" / "credentials.json")  # signed out
        assert Path(env["cwd"]).resolve() == (pair / "work").resolve()
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(suite.PROBE_DIR)
        assert env["prompt"].startswith("Write a short email")
        budget = json.loads((pair / "slate-data" / "budget.json").read_text())
        assert all(v == {"elevenlabs": 0, "higgsfield": 0} for v in budget["monthly"].values())
        meta = json.loads((pair / "meta.json").read_text())
        assert meta["exit_code"] == 0 and not meta["killed"] and meta["synthetic"] is False
        assert (pair / "grade.json").exists() and (pair / "tripwire.log").read_text() == ""
    slice_env = json.loads(transcript.load(runs / "t1" / "negative-email" / "pursuit-slice", "claude-stream-json").final)
    assert slice_env["TERM"] == "dumb" and slice_env["PATH"].split(os.pathsep)[0] == f"{Path.home()}/.local/bin"
    assert (runs / "t1" / "report.json").exists()


def test_the_slice_is_killed_at_its_limit_and_fails_for_it(fake_suite, slate_home):
    root = fake_suite({"pursuit-slice": "sleep"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "pursuit-slice", configs, run_id="t2", run_dir=slate_home["tmp"] / "runs" / "t2")
    inv.timeout_s, inv.kill_grace_s = 2, 1
    started = time.monotonic()
    result = runner.run_pair(inv, case, configs)
    assert time.monotonic() - started < 20
    meta = json.loads((Path(inv.pair_dir) / "meta.json").read_text())
    assert meta["killed"] is True
    assert _checks(result)["no_stdin_wait"] == "FAIL" and result["status"] == "FAIL"
    no_browser = next(c for c in result["checks"] if c["gate"] == "no_browser")
    assert no_browser["status"] == "PASS" and "real setup kills it" in no_browser["summary"], \
        "the slice's own kill is the end of its run: what it didn't do in ten minutes it never does"


def test_the_tripwire_and_the_probe_work_end_to_end(fake_suite, slate_home):
    root = fake_suite({"claude-code": "tripwire", "codex": "slate"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    summary = runner.live([(case, "claude-code"), (case, "codex")], configs, out=runs, run_id="t3")
    by = {r["config"]: r for r in summary["results"]}
    trip = (runs / "t3" / "negative-email" / "claude-code" / "tripwire.log").read_text()
    assert "BROWSER" in trip and "https://example.com/never-opened" in trip
    assert _checks(by["claude-code"])["no_browser"] == "FAIL"
    probe = [json.loads(line) for line in (runs / "t3" / "negative-email" / "codex" / "probe.jsonl").read_text().splitlines()]
    children = [p for p in probe if p["kind"] == "child"]
    assert children and all(p["browser"] == "none" and p["stdin"] == "devnull" for p in children)
    assert any(p["kind"] == "slate_process" for p in probe)
    assert _checks(by["codex"])["slate_children_browser_none"] == "PASS"
    assert _checks(by["codex"])["slate_not_ran"] == "FAIL", "the stand-in ran slate on an email request"


def test_openclaws_transcript_is_collected_after_the_turn(fake_suite, slate_home):
    root = fake_suite({"openclaw": "envelope"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    summary = runner.live([(case, "openclaw")], configs, out=runs, run_id="t4")
    pair = runs / "t4" / "negative-email" / "openclaw"
    meta = json.loads((pair / "meta.json").read_text())
    assert "collect" in meta["collected_by"] and json.loads((pair / "run.json").read_text())["sessionId"] == "sess-123"
    t = transcript.load(pair, "openclaw")
    assert t.final == "collected sess-123"
    result = summary["results"][0]
    assert "slate_children_browser_none" in result["not_applicable"]


@pytest.mark.parametrize("mode", ["collect-path", "collect-jsonl"])
def test_the_transcript_step_takes_a_path_or_the_jsonl_itself(fake_suite, slate_home, mode):
    root = fake_suite({"openclaw": "envelope"}, cases=["negative-email"])
    configs = json.loads((root / "configs.json").read_text())
    configs["configurations"]["openclaw"]["collect"] = [
        {"argv": ["{binary}", str(Path(__file__).with_name("fake_agent.py")), mode, "{session_id}"],
         "stdout": "path", "needs": ["session_id"]}]
    (root / "configs.json").write_text(json.dumps(configs))
    case, configs = _one(root, "negative-email")
    runner.live([(case, "openclaw")], configs, out=slate_home["tmp"] / "runs", run_id=f"t-{mode}")
    pair = slate_home["tmp"] / "runs" / f"t-{mode}" / "negative-email" / "openclaw"
    assert transcript.load(pair, "openclaw").final == "transcript of sess-123"


def test_openclaw_skips_film_cases_without_allow_host_env(fake_suite, slate_home):
    root = fake_suite({"openclaw": "envelope"}, cases=["dry-run-script-stage"])
    case, configs = _one(root, "dry-run-script-stage")
    summary = runner.live([(case, "openclaw")], configs, out=slate_home["tmp"] / "runs", run_id="t5")
    assert summary["results"][0]["status"] == "UNCHECKED" and "allow-host-env" in summary["results"][0]["summary"]


def test_record_saves_a_scrubbed_fixture_that_replays(fake_suite, slate_home, monkeypatch):
    root = fake_suite({"claude-code": "env"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runner.live([(case, "claude-code")], configs, out=slate_home["tmp"] / "runs", run_id="t6", record=True)
    saved = root / "fixtures" / "recorded" / "negative-email" / "claude-code"
    text = (saved / "transcript.jsonl").read_text()
    assert str(Path.home()) not in text and "~" in text
    meta = json.loads((saved / "meta.json").read_text())
    assert "argv" not in meta and meta["case"] == "negative-email"
    summary = runner.replay([(case, "claude-code")], configs, out=slate_home["tmp"] / "runs", run_id="t6r")
    assert summary["results"][0]["source"] == str(saved) and summary["results"][0]["status"] == "PASS"


CODEX_CONFIG = ('model = "gpt-6-astra"\n\n[projects."/Users/someone/code"]\ntrust_level = "trusted"\n\n'
                '[mcp_servers.slate]\ncommand = "slate"\n')


def test_failed_model_start_does_not_replace_a_useful_recording(fake_suite, slate_home):
    root = fake_suite({"claude-code": "env"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    runner.live([(case, "claude-code")], configs, out=runs, run_id="first", record=True)
    saved = root / "fixtures/recorded/negative-email/claude-code"
    before = {p.name: p.read_bytes() for p in saved.iterdir() if p.is_file()}
    configs["configurations"]["claude-code"]["argv"][2] = "error"

    result = runner.live([(case, "claude-code")], configs, out=runs, run_id="unavailable", record=True)

    assert result["results"][0]["status"] == "UNCHECKED"
    assert result["results"][0]["recording"]["written"] is False
    assert {p.name: p.read_bytes() for p in saved.iterdir() if p.is_file()} == before
    assert (runs / "unavailable/negative-email/claude-code/transcript.jsonl").is_file()


def test_a_codex_run_leaves_codexs_config_as_it_found_it(fake_suite, slate_home, _scratch_codex_home):
    """`codex exec` adds a trusted-project table for its folder to config.toml; the runner takes exactly that out."""
    config = _scratch_codex_home / "config.toml"
    config.write_text(CODEX_CONFIG)
    config.chmod(0o600)
    root = fake_suite({"codex": "trust", "pursuit-slice": "trust"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    runner.live([(case, "codex"), (case, "pursuit-slice")], configs, out=runs, run_id="t7")
    assert config.read_text() == CODEX_CONFIG and (config.stat().st_mode & 0o777) == 0o600
    for cid in ("codex", "pursuit-slice"):
        meta = json.loads((runs / "t7" / "negative-email" / cid / "meta.json").read_text())
        assert "taken out again" in meta["codex_trust_pruned"]


def test_pruning_codexs_trust_list_takes_only_the_sandbox_table(tmp_path):
    sandbox = tmp_path / "run" / "work"
    sandbox.mkdir(parents=True)
    ours = f'[projects."{sandbox}"]\ntrust_level = "trusted"\n\n'
    config = tmp_path / "config.toml"
    config.write_text(CODEX_CONFIG.replace("[mcp_servers", ours + "[mcp_servers"))
    assert runner.codex_trusts(sandbox, config) and runner.prune_codex_trust(sandbox, config)
    assert config.read_text() == CODEX_CONFIG and not runner.codex_trusts(sandbox, config)
    assert not runner.prune_codex_trust(sandbox, config)  # nothing left to take
    config.write_text(CODEX_CONFIG + "\n" + ours.rstrip("\n") + "\n")  # last in the file
    assert runner.prune_codex_trust(sandbox, config) and config.read_text() == CODEX_CONFIG + "\n"
    richer = CODEX_CONFIG + f'\n[projects."{sandbox}"]\ntrust_level = "trusted"\nnote = "kept"\n'
    config.write_text(richer)
    assert not runner.prune_codex_trust(sandbox, config) and config.read_text() == richer  # more than ours: kept


def test_a_trust_table_that_was_there_before_the_run_stays(fake_suite, slate_home, _scratch_codex_home):
    root = fake_suite({"codex": "trust"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    inv = commands.plan(case, "codex", configs, run_id="t8", run_dir=runs / "t8")
    before = CODEX_CONFIG + f'\n[projects."{inv.workdir}"]\ntrust_level = "trusted"\n'
    (_scratch_codex_home / "config.toml").write_text(before)
    runner.run_pair(inv, case, configs)
    assert (_scratch_codex_home / "config.toml").read_text().startswith(before)


def test_a_stopped_codex_run_stops_its_agent_and_still_puts_the_trust_list_back(fake_suite, slate_home,
                                                                                 _scratch_codex_home):
    """The runner can be stopped mid-run: slate.cli turns SIGTERM and SIGHUP into SystemExit. The agent runs in a
    session of its own, so the runner stops it too, and the trust table Codex added still comes out."""
    config = _scratch_codex_home / "config.toml"
    config.write_text(CODEX_CONFIG)
    root = fake_suite({"codex": "trust-sleep"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "codex", configs, run_id="t9", run_dir=slate_home["tmp"] / "runs" / "t9")
    pid_file = Path(inv.cwd) / "agent.pid"

    def stop_once_trusted():  # the agent writes its pid only after its trust table
        deadline = time.monotonic() + 60
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGUSR1)

    previous = signal.signal(signal.SIGUSR1, lambda *_: sys.exit(128 + signal.SIGTERM))
    try:
        threading.Thread(target=stop_once_trusted, daemon=True).start()
        with pytest.raises(SystemExit):
            runner.run_pair(inv, case, configs)
    finally:
        signal.signal(signal.SIGUSR1, previous)
    assert config.read_text() == CODEX_CONFIG
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)  # the agent was stopped with the runner


def test_scrub_never_eats_the_backslash_of_an_escape():
    line = json.dumps({"code": 'token = ev.get("enter_token")\nsecret=abcdefghijk\\"x'})
    json.loads(runner.scrub(line))


def test_scrub_takes_out_key_shapes_and_keeps_json_valid():
    line = json.dumps({"out": "Authorization: Bearer abcdefghijklmnop123456 and sk-proj-ABCDEFGHIJKLMNOPQRST "
                              "HF_KEY=id:secretvalue api_key=0123456789abcdef", "home": str(Path.home())})
    clean = runner.scrub(line)
    assert "abcdefghijklmnop123456" not in clean and "sk-proj" not in clean and "secretvalue" not in clean
    assert "0123456789abcdef" not in clean and str(Path.home()) not in clean
    json.loads(clean)


def test_scrub_takes_the_account_name_out_of_folder_names_built_from_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/averyperson")
    line = json.dumps({"ls": "drwxr-xr-x@  3 averyperson  staff  96 Oct  1 .",
                       "spill": "/Users/averyperson/.claude/projects/-Users-averyperson-Library-x/s/tool-results",
                       "tmp": "/private/tmp/claude-501/-Users-averyperson-Documents/x",
                       "other": "averypersons and averyperson2 aren't the name"})
    clean = runner.scrub(line)
    assert "averyperson " not in clean and "-averyperson-" not in clean and "/Users/averyperson" not in clean
    assert "[user]  staff" in clean and "~/.claude/projects/-Users-[user]-Library-x" in clean
    assert "averypersons and averyperson2" in clean
    json.loads(clean)


def test_scrub_takes_out_email_addresses_but_not_their_look_alikes():
    line = json.dumps({"status": "Auth: oauth (openai:personal (avery.person+x@example-mail.dev))",
                       "log": "first line\nsecond@example.org: done",
                       "files": "AppIcon60x60@2x.png, node_modules/@scope/cli, react@18.2.0, user@localhost"})
    clean = runner.scrub(line)
    assert "avery.person" not in clean and "example-mail.dev" not in clean and "example.org" not in clean
    data = json.loads(clean)
    assert data["status"] == "Auth: oauth (openai:personal ([email]))"
    assert data["log"] == "first line\n[email]: done"
    assert data["files"] == "AppIcon60x60@2x.png, node_modules/@scope/cli, react@18.2.0, user@localhost"


def test_replay_of_recordings_reports_whats_missing_and_whats_pending(slate_home, monkeypatch):
    monkeypatch.setenv("HOME", str(slate_home["tmp"]))  # no Oneiro here, so the strategist case stays pending
    monkeypatch.delenv("SLATE_EVALS_DIR", raising=False)
    configs = suite.load_configs()
    pairs = suite.select(suite.load_cases(configs=configs))
    summary = runner.replay(pairs, configs, out=slate_home["tmp"] / "runs", run_id="r1")
    results = {(r["case"], r["config"]): r for r in summary["results"]}
    assert results[("oneiro-strategist-slate-move", "oneiro-strategist")]["status"] == "PENDING"
    for (case_id, config_id), r in results.items():
        if case_id == "oneiro-strategist-slate-move":
            continue
        if (suite.recorded_fixture(case_id, config_id) / "transcript.jsonl").exists():
            assert r["ended"] != "not run" and r["checks"], (case_id, config_id)  # a recording is graded
        else:
            assert r["status"] == "UNCHECKED" and "no recording yet" in r["summary"], (case_id, config_id)
    assert summary["overall"] != "PASS" or all(r["status"] in ("PASS", "PENDING") for r in results.values())
    assert summary["pending"] == ["oneiro-strategist-slate-move"]
    assert (slate_home["tmp"] / "runs" / "r1" / "report.json").exists()


def test_modes_are_exclusive():
    with pytest.raises(UsageError):
        runner.require_mode(True, True, False, False)
    with pytest.raises(UsageError):
        runner.require_mode(False, False, True, True)
    assert runner.require_mode(False, False, False, False) == "replay"
    assert runner.require_mode(True, False, False, False) == "dry-run"


# ---- what a recording keeps ----------------------------------------------------------------------------------------------
def _same_events(a, b):
    return [(e.kind, e.name, e.text, e.ok, e.exit_code) for e in a.events] == \
        [(e.kind, e.name, e.text, e.ok, e.exit_code) for e in b.events] and a.final == b.final


def test_a_claude_recording_leaves_out_memory_hooks_and_thinking():
    lines = [
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "skills": ["acme-frontend-taste"],
         "cwd": "/x/work", "memory_paths": {"auto": "/x/.claude/projects/p/memory"}},
        {"type": "system", "subtype": "hook_response", "hook_name": "SessionStart:startup", "output": "personal context"},
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": "50"},
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "curl -s localhost:3333/pulse"}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "slate status"}},
            {"type": "tool_use", "id": "c", "name": "Read",
             "input": {"file_path": "/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault/Status.md"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "Quinn's memories"},
            {"type": "tool_result", "tool_use_id": "b", "content": "no films yet"},
            {"type": "tool_result", "tool_use_id": "c", "content": "the owner's vault page"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Done."},
    ]
    text = "".join(json.dumps(x) + "\n" for x in lines)
    clean = runner.sanitize_recording(text, "claude-stream-json")
    assert "Quinn's memories" not in clean and "personal context" not in clean and "private reasoning" not in clean
    assert "the owner's vault page" not in clean
    assert "thinking_tokens" not in clean and "no films yet" in clean
    assert "acme-frontend-taste" not in clean and "memory_paths" not in clean and '"cwd": "/x/work"' in clean
    assert _same_events(transcript.parse(text, "claude-stream-json"), transcript.parse(clean, "claude-stream-json"))


def test_a_codex_recording_leaves_out_memory_and_reasoning():
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"id": "1", "type": "reasoning", "text": "private reasoning"}},
        {"type": "item.completed", "item": {"id": "2", "type": "mcp_tool_call", "server": "oneiro-memory",
                                            "tool": "whats_going_on", "arguments": {}, "status": "completed",
                                            "result": {"content": [{"type": "text", "text": "Quinn's week"}]}}},
        {"type": "item.completed", "item": {"id": "3", "type": "command_execution", "command": "slate status",
                                            "aggregated_output": "no films yet", "exit_code": 0,
                                            "status": "completed"}},
        {"type": "item.completed", "item": {"id": "4", "type": "agent_message", "text": "Done."}},
    ]
    text = "".join(json.dumps(x) + "\n" for x in lines)
    clean = runner.sanitize_recording(text, "codex-jsonl")
    assert "Quinn's week" not in clean and "private reasoning" not in clean and "no films yet" in clean
    assert _same_events(transcript.parse(text, "codex-jsonl"), transcript.parse(clean, "codex-jsonl"))


def test_an_openclaw_recording_leaves_out_oneiros_memory():
    history = {"sessionKey": "agent:main:x", "messages": [
        {"role": "user", "content": "make a video"},
        {"role": "assistant", "content": [{"type": "toolCall", "id": "e1", "name": "exec",
                                           "arguments": {"command": "curl -s http://localhost:3333/pulse"}}]},
        {"role": "toolResult", "toolCallId": "e1", "content": [{"type": "toolResult", "text": "undercurrents"}]},
        {"role": "assistant", "content": [{"type": "toolCall", "id": "e2", "name": "exec",
                                           "arguments": {"command": "sed -n 1,9p ~/code/slate/skills/slate/SKILL.md"}}]},
        {"role": "toolResult", "toolCallId": "e2", "content": [{"type": "toolResult", "text": "name: slate"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Do you want a full production or a quick "
                                                                    "generated clip?"}]},
    ]}
    text = json.dumps(history)
    clean = runner.sanitize_recording(text, "openclaw")
    assert "undercurrents" not in clean and "name: slate" in clean
    assert _same_events(transcript.parse(text, "openclaw"), transcript.parse(clean, "openclaw"))


def test_an_openclaw_recording_leaves_out_what_its_own_tools_return():
    # Main's memory search, its scheduled jobs, its session status (the sign-in) and its session's history (which
    # repeats them) are the agent's own state; the calls stay, so the graders see them.
    calls = [("m1", "memory_search", {"query": "active task"}, "a private memory snippet"),
             ("a1", "automations", {"action": "list"}, "heartbeat job list"),
             ("s1", "session_status", {"sessionKey": "current"}, "Auth: oauth (personal sign-in)"),
             ("h1", "sessions_history", {"sessionKey": "current"}, "a private memory snippet again"),
             ("e1", "exec", {"command": "slate status --film demo-launch"}, "Film demo-launch")]
    messages = [{"role": "user", "content": "post the film"}]
    for cid, name, args, result in calls:
        messages += [{"role": "assistant", "content": [{"type": "toolCall", "id": cid, "name": name, "arguments": args}]},
                     {"role": "toolResult", "toolCallId": cid, "content": [{"type": "toolResult", "text": result}]}]
    text = json.dumps({"sessionKey": "agent:main:x", "messages": messages})
    clean = runner.sanitize_recording(text, "openclaw")
    assert "private memory snippet" not in clean and "heartbeat job list" not in clean and "sign-in" not in clean
    assert "Film demo-launch" in clean and '"memory_search"' in clean and '"session_status"' in clean
    assert _same_events(transcript.parse(text, "openclaw"), transcript.parse(clean, "openclaw"))


def test_recordings_name_no_company():
    text = "The Tr" + "ueSpace film for E" + "OS, and in" + "ner echo's reel; videos and eosin stay."
    assert runner.neutral(text) == "The [company] film for [company], and [company]'s reel; videos and eosin stay."


def test_an_empty_claude_project_folder_for_the_sandbox_is_removed(tmp_path):
    sandbox = tmp_path / "runs" / "r" / "case" / "claude-code" / "work"
    folder = runner.claude_project_folder(sandbox)
    assert str(tmp_path / "claude-home") in str(folder) and "/" not in folder.name
    (folder / "memory").mkdir(parents=True)
    assert runner.tidy_claude_project(sandbox) and not folder.exists()
    (folder / "memory").mkdir(parents=True)
    (folder / "session-1" / "tool-results").mkdir(parents=True)
    (folder / "session-1" / "tool-results" / "b1.txt").write_text("a long tool output Claude Code spilled to disk")
    assert runner.tidy_claude_project(sandbox) and not folder.exists()
    (folder / "memory").mkdir(parents=True)
    (folder / "memory" / "MEMORY.md").write_text("a note an agent left")
    assert not runner.tidy_claude_project(sandbox) and (folder / "memory" / "MEMORY.md").exists()


def test_a_recording_keeps_the_ends_of_long_tool_output():
    long = "head\n" + "x" * 20000 + "\nOk to proceed? (y)"
    lines = [{"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": "npx create-thing",
                                                 "aggregated_output": long, "exit_code": 1, "status": "completed"}}]
    text = "".join(json.dumps(x) + "\n" for x in lines)
    clean = runner.sanitize_recording(text, "codex-jsonl")
    assert len(clean) < 7000 and "head" in clean and "Ok to proceed? (y)" in clean and "left out of the recording" in clean
    t = transcript.parse(clean, "codex-jsonl")
    assert t.commands()[0].exit_code == 1



# ---- containment --------------------------------------------------------------------------------------------------------
def test_every_sandbox_holds_the_notice_and_its_meta_says_where_it_was(fake_suite, slate_home):
    root = fake_suite({"codex": "env"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    runner.live([(case, "codex")], configs, out=runs, run_id="n1")
    pair = runs / "n1" / "negative-email" / "codex"
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (pair / "work" / name).read_text()
        assert "This is an eval" in text and str(pair / "work") in text
    meta = json.loads((pair / "meta.json").read_text())
    assert meta["pair_dir"] == str(pair) and meta["workdir"] == str(pair / "work") and meta["tools_cwd"] is None
    assert meta["notice"] == {"files": ["AGENTS.md", "CLAUDE.md"], "in_message": False}
    assert _checks(json.loads((pair / "grade.json").read_text()))["stays_in_sandbox"] == "PASS"


def test_an_eval_sandbox_shares_this_macs_render_slot(slate_home, monkeypatch):
    """A render an eval agent starts waits behind the machine's queue instead of running beside it."""
    from slate.jobs import locks

    sandbox = slate_home["tmp"] / "runs" / "r" / "case" / "codex" / "slate-data"
    shared = runner.share_locks(sandbox, ["render"])
    assert shared == {"render": str(slate_home["data"] / "locks" / "render")}
    assert (sandbox / "locks" / "render").is_symlink() and "slots = 1" in (sandbox / "slate.toml").read_text()
    machine = locks.try_acquire("render", owner="the machine's queue")
    try:
        monkeypatch.setenv("SLATE_DATA", str(sandbox))
        assert locks.slots("render") == 1
        assert locks.try_acquire("render", owner="an eval agent") is None, "the slot is the machine's, and it's taken"
        assert locks.holders("render")[0]["owner"] == "the machine's queue"
    finally:
        machine.release()
    held = locks.try_acquire("render", owner="an eval agent")
    assert held is not None, "free again for the eval once the machine lets go"
    held.release()
    assert runner.share_locks(sandbox, ["render"]) == {}, "from inside the sandbox there's no machine's slot to link"
    monkeypatch.setenv("SLATE_DATA", str(slate_home["data"]))
    assert runner.share_locks(sandbox, ["render"]) == shared, "preparing a pair twice keeps one link"
    assert (sandbox / "locks" / "render").resolve() == (slate_home["data"] / "locks" / "render").resolve()


def test_a_render_an_agent_queues_waits_behind_the_mac_and_is_cancelled_when_the_pair_ends(fake_suite, slate_home):
    from slate.jobs import locks

    root = fake_suite({"codex": "queue"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "codex", configs, run_id="q1", run_dir=slate_home["tmp"] / "runs" / "q1")
    machine = locks.try_acquire("render", owner="the machine's own render")
    try:
        result = runner.run_pair(inv, case, configs)
    finally:
        machine.release()
    meta = json.loads((Path(inv.pair_dir) / "meta.json").read_text())
    queued = meta["sandbox_queue"]["cancelled"]
    assert len(queued) == 1 and "runner" not in meta["sandbox_queue"], meta["sandbox_queue"]
    job = json.loads((Path(inv.pair_dir) / "slate-data" / "jobs" / f"{queued[0]}.json").read_text())
    assert job["state"] == "cancelled" and not job.get("started_at"), "it never ran beside the machine's render"
    assert not runner._lock_held(Path(inv.pair_dir) / "slate-data" / "locks" / "runner.lock")  # noqa: SLF001
    assert result["case"] == "negative-email"


def test_a_runner_left_in_the_sandbox_is_stopped(tmp_path):
    import subprocess

    data = tmp_path / "pair" / "slate-data"
    (data / "jobs").mkdir(parents=True)
    (data / "locks").mkdir()
    holder = ("import fcntl, sys, time; f = open(sys.argv[1], 'a+'); fcntl.flock(f, fcntl.LOCK_EX); "
              "print('locked', flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", holder, str(data / "locks" / "runner.lock"), "jobs", "serve"],
                            stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert proc.stdout.readline().strip() == "locked"
        (data / "jobs" / "runner.json").write_text(json.dumps({"pid": proc.pid, "mode": "daemon"}))
        inv = commands.Invocation(case_id="c", config_id="codex", runner="agent", argv=["x"], cwd=str(tmp_path),
                                  stdin="devnull", prompt="", env_set={"SLATE_DATA": str(data)}, env_unset=[],
                                  env_path=None, timeout_s=10, kill="group", kill_grace_s=1, kill_is_real=False,
                                  pair_dir=str(tmp_path / "pair"), workdir=str(tmp_path / "pair" / "work"),
                                  transcript_format="codex-jsonl", transcript_source="stdout")
        done = runner.stop_sandbox_queue(inv, {**os.environ, "SLATE_DATA": str(data)}, wait_s=1)
        assert done["runner"] == f"stopped (pid {proc.pid})"
        assert proc.wait(timeout=10) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_the_watchdog_stops_an_agent_at_its_first_escape_and_the_run_stops_there(fake_suite, slate_home, monkeypatch):
    """A write outside the sandbox that the disk confirms stops the agent at once, and the run there."""
    from slate.evals import containment

    monkeypatch.setattr(runner, "WATCH_EVERY_S", 0.2)
    monkeypatch.setattr(containment, "_TEMP_RE", __import__("re").compile(r"^/nowhere/"))  # the test's tmp isn't scratch
    monkeypatch.setenv("FAKE_AGENT_OUTSIDE", str(slate_home["tmp"] / "outside" / "Demo — Project Status.md"))
    root = fake_suite({"claude-code": "escape", "codex": "env"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    runs = slate_home["tmp"] / "runs"
    started = time.monotonic()
    summary = runner.live([(case, "claude-code"), (case, "codex")], configs, out=runs, run_id="w1")
    assert time.monotonic() - started < 30, "stopped at the escape, not at the agent's own end a minute later"
    first, second = summary["results"]
    meta = json.loads((runs / "w1" / "negative-email" / "claude-code" / "meta.json").read_text())
    assert meta["killed"] and "outside the sandbox" in meta["stopped_for_escape"][0]
    assert _checks(first)["stays_in_sandbox"] == "FAIL" and runner.escaped(first) == ["stays_in_sandbox"]
    assert second["status"] == "UNCHECKED" and "the run stopped after negative-email on claude-code" in second["summary"]
    assert "stopped" in summary and not (runs / "w1" / "negative-email" / "codex").exists()
    kept = runner.live([(case, "claude-code"), (case, "codex")], configs, out=runs, run_id="w2", keep_going=True)
    assert [r["status"] for r in kept["results"]][1] == "PASS" and "stopped" not in kept



def test_a_cut_off_case_stops_at_its_first_render_and_the_run_goes_on(fake_suite, slate_home, monkeypatch):
    """A production case asks about a production's first moves, so it's cut at its first render: an eval never renders
    on this Mac, through Slate or the engine directly (which takes no lock)."""
    monkeypatch.setattr(runner, "WATCH_EVERY_S", 0.2)
    root = fake_suite({"codex": "render", "claude-code": "env"}, cases=["triggering-launch-video", "negative-email"])
    configs = suite.load_configs(root)
    cases = {c.id: c for c in suite.load_cases(root, configs=configs)}
    runs = slate_home["tmp"] / "runs"
    started = time.monotonic()
    summary = runner.live([(cases["triggering-launch-video"], "codex"), (cases["negative-email"], "claude-code")],
                          configs, out=runs, run_id="c1")
    assert time.monotonic() - started < 40
    meta = json.loads((runs / "c1" / "triggering-launch-video" / "codex" / "meta.json").read_text())
    assert meta["killed"] and "the engine renders directly (cli.mjs web-render)" in meta["cut_at_render"][0]
    assert "stopped_for_escape" not in meta and "stopped" not in summary, "a cut isn't an escape: the run goes on"
    assert summary["results"][1]["status"] == "PASS"


def test_a_case_that_isnt_cut_off_isnt_cut_at_a_render(fake_suite, slate_home, monkeypatch):
    monkeypatch.setattr(runner, "WATCH_EVERY_S", 0.2)
    root = fake_suite({"codex": "render"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "codex", configs, run_id="c2", run_dir=slate_home["tmp"] / "runs" / "c2")
    inv.timeout_s, inv.kill_grace_s = 3, 1
    runner.run_pair(inv, case, configs)
    meta = json.loads((Path(inv.pair_dir) / "meta.json").read_text())
    assert meta["killed"] and "cut_at_render" not in meta and meta["duration_s"] >= 2.5


def test_stopping_an_agent_stops_what_it_started_in_a_session_of_its_own(fake_suite, slate_home):
    root = fake_suite({"codex": "stray"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "codex", configs, run_id="s1", run_dir=slate_home["tmp"] / "runs" / "s1")
    inv.timeout_s, inv.kill_grace_s = 3, 1
    runner.run_pair(inv, case, configs)
    stray = int((Path(inv.workdir) / "stray.pid").read_text())
    for _ in range(50):
        try:
            os.kill(stray, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(stray, signal.SIGKILL)
        raise AssertionError("the agent's stray child outlived it")


@pytest.mark.parametrize("command,renders", [
    ("slate render --film films/x --act 1", True),
    ("slate job submit --kind render --resource render -- slate render --act 2", True),
    ("slate demo", True),
    ("node /Users/you/code/slate/engine/cli.mjs web-render --json '{}'", True),
    ("npx remotion render src/index.ts Main out.mp4", True),
    ("slate render --help", False),
    ("slate render --dry-run --film x", False),
    ("slate script build", False),
    ("node /Users/you/code/slate/engine/cli.mjs check --json '{}'", False),
])
def test_what_counts_as_starting_a_render(command, renders):
    from slate.evals import containment
    from slate.evals.transcript import Event, Transcript

    t = Transcript("codex-jsonl", events=[Event(0, "command", name="shell", text=command)])
    assert bool(containment.render_starts(t)) is renders, command



def test_a_write_the_disk_never_shows_doesnt_stop_the_agent_but_still_fails_it(fake_suite, slate_home, monkeypatch):
    """The watchdog stops a run only for a write it can see on disk, so a command it misreads never stops a contained
    run; the grader still judges the transcript."""
    monkeypatch.setattr(runner, "WATCH_EVERY_S", 0.2)
    root = fake_suite({"claude-code": "escape"}, cases=["negative-email"])
    case, configs = _one(root, "negative-email")
    inv = commands.plan(case, "claude-code", configs, run_id="w3", run_dir=slate_home["tmp"] / "runs" / "w3")
    inv.timeout_s, inv.kill_grace_s = 4, 1
    result = runner.run_pair(inv, case, configs)
    meta = json.loads((Path(inv.pair_dir) / "meta.json").read_text())
    assert meta["killed"] and meta["duration_s"] >= 3.5 and "stopped_for_escape" not in meta
    assert _checks(result)["stays_in_sandbox"] == "FAIL", "the vault page it says it wrote still fails the pair"


def test_on_disk_reads_the_file_or_its_folder():
    import tempfile

    from slate.evals import containment
    from slate.evals.transcript import Event

    with tempfile.TemporaryDirectory() as d:
        before = time.time()
        path = Path(d) / "page.md"
        path.write_text("x")
        hit = containment.Finding(Event(0, "write"), "wrote it", "outside the sandbox", str(path))
        assert containment.on_disk(hit, before)
        assert not containment.on_disk(hit, before + 3600), "older than the run: not this run's write"
        gone = containment.Finding(Event(0, "command"), "deleted it", "outside the sandbox", str(Path(d) / "gone.md"))
        assert containment.on_disk(gone, before), "deleted: the folder it was in changed"
        missing = containment.Finding(Event(0, "write"), "wrote it", "x", "/nowhere/at/all.md")
        assert not containment.on_disk(missing, before)
        endpoint = containment.Finding(Event(0, "command"), "POST /feel", "Oneiro's memory")
        assert containment.on_disk(endpoint, before), "not a file: taken as it is"



def test_a_cut_off_case_stops_when_its_agent_starts_a_renderer_its_transcript_doesnt_name(fake_suite, slate_home,
                                                                                          monkeypatch):
    """The slice wrote its own renderer on 2026-10-01 (frames through rsvg-convert into ffmpeg): the processes under the
    agent show a render its transcript never names."""
    monkeypatch.setattr(runner, "WATCH_EVERY_S", 0.2)
    root = fake_suite({"claude-code": "own-renderer"}, cases=["triggering-launch-video"])
    case, configs = _one(root, "triggering-launch-video")
    inv = commands.plan(case, "claude-code", configs, run_id="r1", run_dir=slate_home["tmp"] / "runs" / "r1")
    started = time.monotonic()
    runner.run_pair(inv, case, configs)
    assert time.monotonic() - started < 30
    meta = json.loads((Path(inv.pair_dir) / "meta.json").read_text())
    assert meta["killed"] and "the agent started a renderer" in meta["cut_at_render"][0] and "ffmpeg" in \
        meta["cut_at_render"][0]
    renderer = int((Path(inv.workdir) / "renderer.pid").read_text())
    for _ in range(50):
        try:
            os.kill(renderer, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(renderer, signal.SIGKILL)
        raise AssertionError("the renderer outlived the cut")
