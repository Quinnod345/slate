"""Slate's MCP server: its tools, a fixture film answered in-process, the allowlist, spend, and long work as jobs.

Most tests drive the server through the MCP SDK's own client, connected in-process, so the schemas, results and
error flags are what a real client sees. Jobs are queued and inspected but never run: the `studio` fixture stops the
queue from starting a runner. One test starts `slate mcp` as a real child process and talks to it over stdio.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.cli import run_cli
from slate.errors import MissingPrerequisite
from slate.jobs import store
from slate.mcp import cli as mcp_cli
from slate.mcp import policy, server, tools

REQUEST = "Make a 20-second reel about tide pools: calm, curious, and ending on the question of what the sea leaves."
SCRIPT = {"title": "Tide pools", "version": "draft-1", "language": "en", "acts": [
    {"name": "Act 1", "job": "hook", "shots": [
        {"id": "S01", "vo": "Every tide leaves a small world behind.", "on_screen": "Low tide",
         "picture": "a rock pool at dawn", "source": "requester"},
        {"id": "S02", "vo": "Look closer, and it starts to move.", "on_screen": "Look closer",
         "picture": "a crab stepping out", "source": "requester"}]}]}


def mcp(calls: list[tuple[str, dict]], *, mode: str = "auto") -> list[tuple[object, dict]]:
    """Call tools through the SDK's client, in-process. Returns (result, parsed envelope) per call."""
    from mcp import Client

    async def go():
        out = []
        async with Client(server.build_server(), mode=mode) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                out.append((result, json.loads(result.content[0].text)))
        return out

    return asyncio.run(go())


def make_film(studio, film_id: str = "tide-pools") -> Path:
    out = tools.new_film(film_id, "plain-brand", studio["format"], request=REQUEST, git=False)
    assert out["ok"], out
    return (paths.films() / film_id).resolve()


# ---------------------------------------------------------------------------------------------------------------
# The tools a client sees


def test_the_server_lists_its_tools_over_the_real_handshake(studio):
    from mcp import Client

    async def go():
        async with Client(server.build_server(), mode="legacy") as client:
            return client.server_info, client.instructions, (await client.list_tools()).tools

    info, instructions, listed = asyncio.run(go())
    assert info.name == "slate"
    assert "Long work never blocks a chat" in instructions
    by_name = {t.name: t for t in listed}
    assert tuple(by_name) == server.TOOL_NAMES
    assert set(server.TOOL_NAMES) >= {"status", "new_film", "run", "job_submit", "job_status", "budget", "ledger",
                                      "guide"}
    assert by_name["run"].input_schema["properties"]["command"]["enum"] == list(policy.RUN)
    assert by_name["job_submit"].input_schema["properties"]["stage"]["enum"] == list(policy.JOBS)
    assert by_name["job_submit"].input_schema["properties"]["spend"]["default"] is False
    assert by_name["status"].annotations.read_only_hint is True
    assert by_name["script_put"].annotations.destructive_hint is True
    assert by_name["job_submit"].annotations.open_world_hint is True
    assert by_name["guide"].meta == {"anthropic/maxResultSizeChars": 200_000}
    for tool in listed:
        assert "CLI:" in tool.description, tool.name  # every tool names its command-line equivalent


def test_the_run_allowlist_is_the_small_one_the_plan_names():
    assert {"script build", "timing", "qa", "review", "deliver"} <= set(policy.RUN)
    assert "render" not in policy.RUN  # renders only ever go to the queue
    assert {"render", "voice tts", "mix", "qa"} <= set(policy.JOBS)
    for table in (policy.RUN, policy.JOBS):
        for stage in table.values():
            assert stage.how in ("inline", "detach", "queue")
            if stage.how == "detach":
                assert "--detach" in stage.extra
            assert stage.words[0] not in ("ae", "approve", "budget", "connect", "master")
    # Every stage that reads or writes media runs as a job.
    assert all(policy.RUN[name].how != "inline" for name in ("qa", "review", "deliver"))


def test_the_server_can_never_reach_a_denied_connector_tool():
    from slate.vendors.higgsfield import DENIED_CONNECTOR_TOOLS

    names = set(server.TOOL_NAMES) | set(policy.RUN) | set(policy.JOBS)
    words = {w for s in (*policy.RUN.values(), *policy.JOBS.values()) for w in s.words}
    assert not (names | words) & set(DENIED_CONNECTOR_TOOLS)
    for denied in DENIED_CONNECTOR_TOOLS:
        assert tools.job_submit(denied)["error"]["error"] == "refused"


def test_modes_mirror_film_state():
    from slate.state import film

    assert server.MODES == film.MODES and server.GENERATION == film.GENERATION


def test_agent_facing_text_names_no_one_agents_tools():
    from slate.skills_build import NOT_PORTABLE

    texts = [server.INSTRUCTIONS] + [t.description for t in server.TOOLS]
    texts += [s.summary for s in (*policy.RUN.values(), *policy.JOBS.values())] + list(policy.REFUSED_OPTIONS.values())
    for text in texts:
        for pattern, what in NOT_PORTABLE:
            assert not pattern.search(text), (what, text)


# ---------------------------------------------------------------------------------------------------------------
# A fixture film, in-process


def test_status_outside_any_film_is_an_answer(studio):
    [(result, env)] = mcp([("status", {})])
    assert result.is_error is False and env["ok"]
    assert env["result"]["films"] == [] and "new_film" in env["result"]["next"]


def test_new_film_then_status_on_it(studio):
    [(r1, made), (r2, st)] = mcp([
        ("new_film", {"film_id": "tide-pools", "brand": "plain-brand", "format": studio["format"],
                      "request": REQUEST, "git": False}),
        ("status", {"film": "tide-pools"}),
    ])
    assert r1.is_error is False and made["ok"], made
    assert made["command"].startswith("slate new tide-pools --brand=plain-brand")
    film_dir = paths.films() / "tide-pools"
    assert (film_dir / "film.json").is_file()
    assert REQUEST in (film_dir / "brief" / "GOAL.md").read_text()  # the request, word for word
    assert r2.is_error is False and st["ok"], st
    assert st["command"] == f"slate status --film {film_dir.resolve()}"
    assert st["result"]["film"] == "tide-pools" and st["result"]["next"]["command"]
    assert r2.structured_content["result"]["film"] == "tide-pools"


def test_status_on_a_film_that_isnt_there(studio):
    [(result, env)] = mcp([("status", {"film": "no-such-film"})])
    assert result.is_error and env["exit_code"] == 2 and env["error"]["error"] == "usage"


def test_quick_stages_run_inline_and_say_their_command(studio):
    film_dir = make_film(studio)
    [(put_r, put), (build_r, built), (timing_r, timing)] = mcp([
        ("script_put", {"film": "tide-pools", "script": SCRIPT}),
        ("run", {"command": "script build", "film": "tide-pools", "args": ["--check"]}),
        ("run", {"command": "timing", "film": "tide-pools"}),
    ])
    assert jsonio.read(film_dir / "script" / "script.json") == SCRIPT
    assert put["written"].endswith("script/script.json") and put["exit_code"] in (0, 1)
    assert "slate script lint --film" in put["command"]
    assert built["command"] == f"slate script build --film {film_dir.resolve()} --check"
    assert built["exit_code"] in (0, 1) and "job_id" not in built  # it ran now; nothing was queued
    # Timing without a storyboard is a missing prerequisite, reported with the CLI's own exit code.
    assert timing_r.is_error and timing["exit_code"] == 3, timing


def test_script_put_checks_the_schema_and_leaves_approved_work_alone(studio):
    film_dir = make_film(studio)
    bad = tools.script_put({"title": "no acts"}, film="tide-pools")
    assert bad["exit_code"] == 2 and "acts" in bad["error"]["message"]

    assert tools.script_put(SCRIPT, film="tide-pools")["written"]
    from slate.state import approvals

    approvals.record(film_dir, what="script", path="script/script.json", approved_by="Test Person")
    changed = {**SCRIPT, "version": "draft-2"}
    refused = tools.script_put(changed, film="tide-pools")
    assert refused["exit_code"] == 1 and refused["error"]["error"] == "refused"
    assert "slate revise start" in refused["error"]["hint"]
    assert jsonio.read(film_dir / "script" / "script.json") == SCRIPT
    got = tools.script_get(film="tide-pools")
    assert got["ok"] and got["result"]["script"] == SCRIPT


# ---------------------------------------------------------------------------------------------------------------
# Long work becomes a job


def test_qa_through_run_becomes_a_job_and_never_blocks(studio):
    film_dir = make_film(studio)
    [(result, env)] = mcp([("run", {"command": "qa", "film": "tide-pools", "args": ["--quick"]})])
    assert result.is_error is False and env["ok"], env
    job = store.load(env["job_id"])
    assert job["state"] == "queued" and job["kind"] == "qa"
    assert job["argv"][-4:] == ["qa", "--film", str(film_dir.resolve()), "--quick"]
    assert "job_status" in env["next"]


def test_job_submit_queues_the_stage_and_status_list_cancel_follow_it(studio):
    film_dir = make_film(studio)
    [(r1, sub)] = mcp([("job_submit", {"stage": "mix", "film": "tide-pools", "args": ["--no-score"]})])
    assert r1.is_error is False and sub["ok"], sub
    job = store.load(sub["job_id"])
    assert job["kind"] == "mix" and job["state"] == "queued"
    assert job["argv"][-4:] == ["mix", "--film", str(film_dir.resolve()), "--no-score"]
    assert "(from the MCP server)" in job["note"]
    assert sub["command"] == f"slate job submit --kind mix -- slate mix --film {film_dir} --no-score"

    job_id = sub["job_id"]
    [(_, st), (_, listing), (_, cancelled), (_, after)] = mcp([
        ("job_status", {"job_id": job_id}), ("job_status", {}), ("job_cancel", {"job_id": job_id}),
        ("job_status", {"job_id": job_id})])
    assert st["ok"] and st["result"]["state"] == "queued" and st["command"] == f"slate job status {job_id}"
    assert listing["ok"] and job_id in [j["id"] for j in listing["result"]["jobs"]]
    assert cancelled["ok"], cancelled
    assert after["result"]["state"] == "cancelled"


def test_a_stage_without_its_own_detach_is_checked_before_it_is_queued(studio):
    make_film(studio)
    bad = tools.job_submit("mix", film="tide-pools", args=["--under", "loud"])
    assert bad["exit_code"] == 2 and "--under" in bad["error"]["message"]
    assert store.all_jobs() == []


# ---------------------------------------------------------------------------------------------------------------
# Refusals


@pytest.mark.parametrize("tool, args, code, words", [
    ("run", {"command": "render"}, 1, "use job_submit"),
    ("run", {"command": "ae masters"}, 1, "person's go"),
    ("run", {"command": "budget set"}, 1, "their own terminal"),
    ("job_submit", {"stage": "connect elevenlabs"}, 1, "own Terminal"),
    ("job_submit", {"stage": "render", "args": ["--go"]}, 1, "person's go"),
    ("job_submit", {"stage": "render", "args": ["--engine", "ae"]}, 2, "isn't a value"),
    ("job_submit", {"stage": "render", "args": ["--live"]}, 1, "spend=true"),
    ("run", {"command": "script build", "args": ["--out", "/tmp/elsewhere"]}, 1, "outside the film"),
    ("run", {"command": "script build", "args": ["--frobnicate"]}, 1, "Allowed here: --check, --dry-run, --from-takes"),
    ("run", {"command": "timing show", "args": ["-x"]}, 2, "long form"),
    ("run", {"command": "timing show"}, 2, "needs 1 name"),
    ("run", {"command": "qa", "args": ["triage"]}, 2, "isn't a name"),
    ("run", {"command": "qa triage", "args": ["--accept", "glitch:1"]}, 1, "person's call"),
    ("job_status", {"job_id": "../../etc"}, 2, "isn't a job id"),
    ("ledger", {"month": "2026-13"}, 2, "YYYY-MM"),
    ("ledger", {"view": "everything"}, 2, "view must be one of"),
    ("balance_snapshot", {"balance": 5}, 2, "goes with one provider"),
    ("balance_snapshot", {"provider": "acme-pay", "balance": 5}, 2, "isn't a provider"),
    ("balance_snapshot", {"provider": "higgsfield", "session": "no spaces allowed"}, 2, "isn't a session label"),
])
def test_refusals_come_back_as_errors_with_the_right_exit_code(studio, tool, args, code, words):
    make_film(studio)
    if tool in ("run", "job_submit"):
        args = {"film": "tide-pools", **args}
    [(result, env)] = mcp([(tool, args)])
    assert result.is_error is True
    assert env["exit_code"] == code, env
    text = json.dumps(env["error"])
    assert words in text, text
    assert store.all_jobs() == []  # a refusal never queues anything


def test_an_out_of_list_stage_is_refused_by_slate_not_just_the_schema(studio):
    [(result, env)] = mcp([("job_submit", {"stage": "shell", "args": ["rm", "-rf", "/"]})])
    assert result.is_error and env["exit_code"] == 1
    assert "isn't something the MCP server runs" in env["error"]["message"]


def test_spend_needs_the_persons_yes(studio):
    make_film(studio)
    tts = tools.job_submit("voice tts", film="tide-pools")
    assert tts["exit_code"] == 4 and tts["error"]["error"] == "spend_refused"
    assert "voice estimate" in tts["error"]["hint"]
    aligned = tools.job_submit("timing words", film="tide-pools", args=["--engine", "elevenlabs"])
    assert aligned["exit_code"] == 4
    assert store.all_jobs() == []

    free = tools.job_submit("timing words", film="tide-pools", args=["--engine", "whisper"])
    assert free["ok"], free
    assert store.load(free["job_id"])["resources"] == ["whisper"]
    paid = tools.job_submit("score compose", film="tide-pools", spend=True)
    assert paid["ok"], paid
    job = store.load(paid["job_id"])
    assert job["kind"] == "music" and job["resources"] == ["elevenlabs_music"]


def test_a_stage_whose_module_isnt_built_says_so():
    ghost = policy.Stage("ghost", ("definitely-not-a-command",), "queue", "a stage from the future", "slate.ghost.cli")
    with pytest.raises(MissingPrerequisite, match="isn't part of this Slate install yet"):
        tools.require_built(ghost)


def test_review_and_deliver_run_as_jobs_or_say_theyre_not_built(studio):
    make_film(studio)
    for stage in ("review", "deliver"):
        env = tools.run(stage, film="tide-pools")
        if tools.registered((stage,)):
            assert env["ok"] and store.load(env["job_id"])["kind"] == stage
        else:
            assert env["exit_code"] == 3 and "slate.finish.cli" in env["error"]["message"]


# ---------------------------------------------------------------------------------------------------------------
# Spend and guides are read-only views


def test_budget_and_ledger_views(studio):
    make_film(studio)
    [(b_r, budget), (l_r, ledger), (i_r, inflight)] = mcp([
        ("budget", {"film": "tide-pools"}), ("ledger", {"limit": 5}), ("ledger", {"view": "inflight"})])
    assert budget["ok"] and budget["command"].startswith("slate budget show --film")
    assert {row["mode"] for row in budget["result"]["monthly"]} >= {"supervised", "autonomous"}
    assert ledger["ok"] and ledger["result"]["rows"] == [] and ledger["command"] == "slate ledger list --limit 5"
    assert inflight["ok"] and inflight["command"] == "slate ledger inflight"


def test_a_chat_session_records_connector_balances_and_reconciles(studio):
    """A chat's connector generations bypass the ledger; snapshots at its start and end are how they're caught."""
    [(r1, start), (r2, end), (r3, squared)] = mcp([
        ("balance_snapshot", {"provider": "higgsfield", "balance": 120.5, "session": "chat-1",
                              "note": "the connector's balance tool"}),
        ("balance_snapshot", {"provider": "higgsfield", "balance": 120.5, "session": "chat-1"}),
        ("ledger", {"view": "reconcile", "provider": "higgsfield", "session": "chat-1"}),
    ])
    assert start["ok"] and start["command"].startswith("slate ledger snapshot --provider=higgsfield --balance=120.5")
    [snap] = start["result"]["snapshots"]
    assert snap["provider"] == "higgsfield" and snap["balance"] == 120.5 and snap["session"] == "chat-1"
    assert end["ok"]
    assert squared["command"] == "slate ledger reconcile --provider=higgsfield --session=chat-1"
    assert squared["result"]["results"][0]["provider"] == "higgsfield"


def test_guide_through_the_server(studio, skills_src, monkeypatch):
    monkeypatch.setattr(paths, "SKILLS_SRC", skills_src)
    [(r1, listing), (r2, topic), (r3, missing)] = mcp([("guide", {}), ("guide", {"topic": "intake"}),
                                                        ("guide", {"topic": "zzz"})])
    assert listing["ok"] and "demo-front/intake" in [t["topic"] for t in listing["result"]["topics"]]
    assert topic["result"]["text"] == (skills_src / "demo-front/references/intake.md").read_text()
    assert r3.is_error and missing["exit_code"] == 2


# ---------------------------------------------------------------------------------------------------------------
# Envelopes, size and safety


def test_envelopes_carry_the_command_and_the_clis_own_json():
    ok = tools.envelope(["status", "--film", "/films/a b", "--json"], 0, '{"film": "a"}')
    assert ok == {"ok": True, "exit_code": 0, "command": "slate status --film '/films/a b'", "result": {"film": "a"}}
    err = tools.envelope(["qa", "--json"], 3, '{"error": "missing_prerequisite", "message": "m", "exit_code": 3}',
                         "12:00:00 progress\n")
    assert err["error"]["error"] == "missing_prerequisite" and "progress" in err["log"] and "result" not in err
    job = tools.envelope(["qa"], 0, '{"job": "j-20260930-120000-abcd"}')
    assert job["job_id"] == "j-20260930-120000-abcd" and "job_status" in job["next"]


def test_oversized_results_are_summarized_not_dumped():
    big = {"ok": True, "exit_code": 0, "command": "slate status", "result": {"stages": ["x" * 100] * 1000}}
    small, text = tools.shrink(big)
    assert len(text) < tools.MAX_TEXT and small["truncated"]["chars"] > tools.MAX_TEXT
    assert small["result_keys"] == ["stages"] and small["command"] == "slate status"


def test_tool_calls_never_overlap(studio):
    """Two films asked about at once each get their own answer (the CLI sets SLATE_FILM while it runs)."""
    make_film(studio, "film-a")
    make_film(studio, "film-b")
    answers: dict[str, str] = {}

    def ask(name):
        for _ in range(3):
            env = tools.status(name)
            answers.setdefault(name, env["result"]["film"])
            assert env["result"]["film"] == name

    threads = [threading.Thread(target=ask, args=(n,)) for n in ("film-a", "film-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert answers == {"film-a": "film-a", "film-b": "film-b"}


def test_list_tools_command(studio):
    code, out, _ = run_cli(["mcp", "--list-tools", "--json"])
    assert code == 0 and [t["name"] for t in json.loads(out)["tools"]] == list(server.TOOL_NAMES)


def test_slate_mcp_answers_a_client_over_stdio(studio):
    """The real thing: `slate mcp` as a child process (through slate.procs). Every request goes in at once and stdin
    closes straight after, so the server must answer what it read before hanging up (the SDK alone would cancel)."""
    report = mcp_cli.stdio_check(timeout=120)
    assert report["ok"], report
    assert report["tools"] == list(server.TOOL_NAMES)
    assert report["status_answered"] and not report["status_is_error"]
    assert report["server"]["name"] == "slate" and report["exit_code"] == 0
