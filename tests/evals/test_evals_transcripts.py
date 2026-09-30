"""Reading what agents did: shell commands, and each agent's transcript format."""

import json

from slate.evals import shell, transcript


def test_shell_unwraps_codex_style_commands():
    cmds = shell.parse("/bin/zsh -lc \"cd /tmp/w && SLATE_FAKE_VENDORS=1 slate --json new reel-1 --brand demo\"")
    assert [s.program for s in cmds] == ["cd", "slate"]
    call = shell.slate_call(cmds[1])
    assert call.sub_is("new") and not call.live and cmds[1].assignments == {"SLATE_FAKE_VENDORS": "1"}


def test_shell_drops_heredoc_bodies_and_finds_writes():
    cmds = shell.parse("cat > films/x/script/script.json <<'EOF'\n{\"open https://x\": 1}\nEOF\nslate script build")
    assert [s.program for s in cmds] == ["cat", "slate"]
    assert shell.writes(cmds[0]) == ["films/x/script/script.json"]
    assert shell.slate_call(cmds[1]).sub_is("script build")


def test_shell_peels_wrappers_and_sees_env_unset():
    (s,) = shell.parse("env -u SLATE_FAKE_VENDORS caffeinate -i timeout 30 slate voice tts --live")
    assert s.program == "slate" and s.unset == ["SLATE_FAKE_VENDORS"]
    assert shell.slate_call(s).live


def test_shell_reads_every_spelling_of_slate():
    for line in ("python3 -m slate status", "uv run --project ~/code/slate slate status", "~/.local/bin/slate status"):
        (s,) = shell.parse(line)
        assert shell.slate_call(s).sub_is("status"), line


def test_skill_reads_count_but_ls_does_not():
    cmds = shell.parse("ls ~/.agents/skills/slate/SKILL.md && sed -n '1,9p' ~/.claude/skills/video-verifier/SKILL.md")
    assert shell.skills_read_by(cmds[0]) == []
    assert shell.skills_read_by(cmds[1]) == ["video-verifier"]


def test_shell_never_crashes_on_broken_quoting():
    assert shell.parse("sed -n '1,5p' 'unbalanced")[0].program == "sed"
    assert shell.parse("") == []


def test_claude_stream_json_is_normalised():
    lines = [
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "skills": ["slate"]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Skill", "input": {"skill": "slate"}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "slate new x --brand demo"}},
            {"type": "tool_use", "id": "c", "name": "Write", "input": {"file_path": "films/x/script/script.json"}},
            {"type": "tool_use", "id": "d", "name": "mcp__aside__exec", "input": {"task": "read the page"}},
            {"type": "tool_use", "id": "e", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "Which lane?", "options": [{"label": "Full production"}]}]}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b", "content": "Exit code 3",
                                                  "is_error": True}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "done"},
    ]
    t = transcript.parse("\n".join(json.dumps(x) for x in lines), "claude-stream-json")
    assert [e.kind for e in t.events] == ["skill", "command", "write", "tool", "question"]
    assert t.events[1].ok is False and t.events[1].exit_code == 3
    assert t.skills_loaded()[0][0] == "slate" and t.final == "done" and t.ended == "ok"
    assert t.questions()[0].args["_count"] == 1


def test_codex_jsonl_keeps_the_latest_state_of_each_item():
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.started", "item": {"id": "1", "type": "command_execution", "command": "bash -lc 'slate status'",
                                          "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": "bash -lc 'slate status'",
                                            "aggregated_output": "ok", "exit_code": 0, "status": "completed"}},
        {"type": "item.started", "item": {"id": "2", "type": "command_execution", "command": "read x",
                                          "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "3", "type": "mcp_tool_call", "server": "aside", "tool": "aside_read",
                                            "arguments": {"url": "https://example.com"}, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "4", "type": "dynamic_tool_call", "tool": "browser_use",
                                            "arguments": {"url": "https://example.com"}, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "5", "type": "agent_message", "text": "done"}},
    ]
    t = transcript.parse("\n".join(json.dumps(x) for x in lines), "codex-jsonl")
    kinds = [(e.kind, e.name, e.status) for e in t.events]
    assert kinds[0] == ("command", "shell", "completed") and t.events[0].exit_code == 0
    assert kinds[1] == ("command", "shell", "in_progress")
    assert kinds[2][1] == "mcp__aside__aside_read" and kinds[3][1] == "browser_use"
    assert t.final == "done"


def test_openclaw_reads_session_lines_history_json_and_the_envelope():
    session = [
        {"type": "session", "id": "s1", "cwd": "/w"},
        {"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "c1", "name": "read", "arguments": {"path": "/u/.agents/skills/slate/SKILL.md"}},
            {"type": "toolCall", "id": "c2", "name": "aside__aside_click", "arguments": {"ref": "e1"}},
            {"type": "toolCall", "id": "c3", "name": "exec", "arguments": {"command": "slate status"}}]}},
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c3", "toolName": "exec",
                                        "content": [{"type": "text", "text": "ok"}], "isError": False}},
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ]
    t = transcript.parse("\n".join(json.dumps(x) for x in session), "openclaw")
    assert [e.kind for e in t.events] == ["read", "tool", "command", "text"]
    assert t.events[1].name == "mcp__aside__aside_click" and t.events[2].exit_code == 0
    assert t.skills_loaded()[0][0] == "slate" and t.final == "Done."

    history = {"messages": [{"role": "assistant", "content": [{"type": "toolCall", "name": "browser",
                                                               "arguments": {"action": "open"}}]}]}
    envelope = {"status": "ok", "meta": {"agentMeta": {"sessionId": "abc"}}, "payloads": [{"text": "hi"}]}
    t2 = transcript.parse(json.dumps(history), "openclaw", envelope=envelope)
    assert t2.events[0].name == "browser" and t2.info["session_id"] == "abc" and t2.final == "hi"

    only_envelope = {"status": "error", "error": {"message": "timed out"}, "toolSummary": {"tools": ["browser"]}}
    t3 = transcript.parse(json.dumps(only_envelope), "openclaw")
    assert t3.run_error.startswith("error") and t3.events[0].name == "browser"


def test_an_empty_transcript_ends_empty(tmp_path):
    t = transcript.load(tmp_path, "codex-jsonl")
    assert t.ended == "empty" and t.probe is None and t.tripwire is None
