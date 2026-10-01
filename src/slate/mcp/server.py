"""The MCP wiring: Slate's tools, their schemas and descriptions, served over stdio.

What each tool does lives in `slate.mcp.tools` (plain functions); what may run lives in `slate.mcp.policy`. This file
only describes the tools to clients and turns each envelope into an MCP result: the envelope as JSON text (and as
structured content), with `isError` set whenever the command didn't succeed.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass
from typing import Annotated, Any, Callable

from pydantic import Field

from slate import __version__
from slate.mcp import policy, tools
from slate.secrets import redact

# Mirrors slate.state.film (a test keeps them equal), so the server works even when film state fails to import.
MODES = ("supervised", "express", "draft", "autonomous")
GENERATION = ("none", "rarely", "where-it-helps")
ROW_STATUSES = ("inflight", "done", "failed", "cancelled", "orphaned")

INSTRUCTIONS = """\
Slate makes finished, gated films from a brief: story, voice, motion, sound, QA and delivery. Every tool here is a \
thin wrapper over the `slate` command line, and each result names the exact command, so anything done here can also \
be done in a shell.

Start with `status`: without a film it lists the films; with one it shows every stage and the one next command. \
Start a film with `new_film`, keeping the person's request word for word. Read and write the one hand-edited script \
with `script_get` and `script_put`. Quick stages (script build, timing, triage, estimates, the tag loop's picks) run \
with `run`.

Long work never blocks a chat. Renders, voicing, the voice polish and analysis, the score, the mix, QA sweeps and \
render stability, review packets and delivery go to the job queue (`job_submit`, or `run` sends them there on its \
own) and return a job id at once. Check back with `job_status`; don't wait in a loop. The queue's runner also works \
where a gateway's network proxy blocks the vendors, so paid stages always go through it.

Stages that spend vendor credits need spend=true. Show the person the estimate first (`run` with 'voice estimate', \
'voice tags estimate' or 'score estimate') and ask. After Effects steps, approvals, budget changes and connecting \
accounts are a person's to do in their own terminal, and this server refuses them. `guide` prints Slate's reference \
guides.

Generations made through a hosted connector (Higgsfield's or ElevenLabs' in a chat) bypass Slate's ledger. When a \
session uses one, record its balance with `balance_snapshot` at the start and at the end, with one session label, \
then check `ledger` with view=reconcile."""

Film = Annotated[str | None, Field(description="The film: its id under the films folder, or a path to its folder. "
                                               "Leave it out when the server runs inside a film folder.")]


def _stage_list(table: dict[str, policy.Stage]) -> str:
    return "; ".join(f"'{s.name}' ({s.summary})" for s in table.values())


def _result(payload: dict, limit: int = tools.MAX_TEXT):
    from mcp_types import CallToolResult, TextContent  # noqa: PLC0415

    small, text = tools.shrink(payload, limit)
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=small,
                          is_error=not bool(small.get("ok")))


def _safely(label: str, fn: Callable[[], dict], limit: int = tools.MAX_TEXT):
    """Run a tool body; an unexpected crash still comes back as a readable envelope (with the trace on stderr)."""
    try:
        payload = fn()
    except Exception as exc:  # noqa: BLE001 - a crash must reach the agent as a result, not a silent hang
        traceback.print_exc(file=sys.stderr)
        payload = {"ok": False, "exit_code": 1, "command": label,
                   "error": {"error": "internal", "message": redact(f"{type(exc).__name__}: {exc}"),
                             "hint": "This is a bug in Slate; the server's stderr has the details."}}
    return _result(payload, limit)


# ---------------------------------------------------------------------------------------------------------------
# Tool functions (their signatures are the tools' input schemas)


def status(film: Film = None):
    return _safely("slate status", lambda: tools.status(film))


def new_film(
    film_id: Annotated[str, Field(description="The film's id and folder name: lowercase letters, digits, dots, "
                                              "dashes (e.g. launch-45s).")],
    brand: Annotated[str, Field(description="The brand pack's name. The neutral one is 'demo'.")],
    format: Annotated[str, Field(description="A format name (a file in formats/), or a path to a format JSON "
                                             "file.")],
    request: Annotated[str | None, Field(description="The person's request, word for word. It becomes the brief "
                                                     "and its checklist.")] = None,
    title: Annotated[str | None, Field(description="A working title.")] = None,
    mode: Annotated[str, Field(description="supervised (default), express, draft or autonomous.",
                               json_schema_extra={"enum": list(MODES)})] = "supervised",
    generation: Annotated[str, Field(description="How much generated footage the requester wants: none, rarely "
                                                 "(default) or where-it-helps.",
                                     json_schema_extra={"enum": list(GENERATION)})] = "rarely",
    runtime_s: Annotated[float | None, Field(description="Target runtime in seconds (default: the "
                                                         "format's).")] = None,
    aspect: Annotated[str | None, Field(description="For formats that come in several aspects, e.g. 9:16.")] = None,
    source: Annotated[str, Field(description="Who made the request (a name).")] = "requester",
    git: Annotated[bool, Field(description="Start a git history for the film's small files (default true).")] = True,
):
    if mode not in MODES or generation not in GENERATION:
        from slate.errors import UsageError  # noqa: PLC0415

        return _result(tools.failure(UsageError(f"mode must be one of {', '.join(MODES)} and generation one of "
                                                f"{', '.join(GENERATION)}."), "slate new"))
    return _safely("slate new", lambda: tools.new_film(
        film_id, brand, format, request=request, title=title, mode=mode, generation=generation, runtime_s=runtime_s,
        aspect=aspect, source=source, git=git))


def run(
    command: Annotated[str, Field(description="The stage to run.", json_schema_extra={"enum": list(policy.RUN)})],
    film: Film = None,
    args: Annotated[list[str] | None, Field(description="Extra arguments the stage accepts, long form (e.g. "
                                                        "['--check'] for script build, gate names for qa).")] = None,
):
    return _safely("slate run", lambda: tools.run(command, film, args))


def job_submit(
    stage: Annotated[str, Field(description="The stage to queue.", json_schema_extra={"enum": list(policy.JOBS)})],
    film: Film = None,
    args: Annotated[list[str] | None, Field(description="Extra arguments the stage accepts, long form (e.g. "
                                                        "['--act', '2'] for render).")] = None,
    spend: Annotated[bool, Field(description="Set true only after the person has seen what this stage costs and "
                                             "said yes. Needed for stages that spend vendor credits.")] = False,
):
    return _safely("slate job submit", lambda: tools.job_submit(stage, film, args, spend))


def job_status(
    job_id: Annotated[str | None, Field(description="A job id (j-YYYYMMDD-HHMMSS-xxxx). Leave it out for the queue "
                                                    "and recent jobs.")] = None,
    limit: Annotated[int, Field(description="How many recent jobs to list without a job id (1-50).")] = 10,
):
    return _safely("slate job status", lambda: tools.job_status(job_id, limit))


def job_log(
    job_id: Annotated[str, Field(description="The job's id.")],
    lines: Annotated[int, Field(description="How many lines from the end (1-200).")] = 40,
):
    return _safely("slate job log", lambda: tools.job_log(job_id, lines))


def job_cancel(job_id: Annotated[str, Field(description="The job's id.")]):
    return _safely("slate job cancel", lambda: tools.job_cancel(job_id))


def script_get(film: Film = None):
    return _safely("read script/script.json", lambda: tools.script_get(film))


def script_put(
    script: Annotated[dict[str, Any], Field(description="The whole script: {title, version, language, acts: [{name, "
                                                        "job, shots: [{id, vo, on_screen, picture, source}]}]}.")],
    film: Film = None,
):
    return _safely("write script/script.json", lambda: tools.script_put(script, film))


def budget(
    film: Film = None,
    month: Annotated[str | None, Field(description="YYYY-MM (UTC); default this month.")] = None,
):
    return _safely("slate budget show", lambda: tools.budget(film, month))


def ledger(
    film: Film = None,
    provider: Annotated[str | None, Field(description="elevenlabs, elevenlabs_oneiro or higgsfield.",
                                          json_schema_extra={"enum": list(tools.PROVIDERS)})] = None,
    row_status: Annotated[str | None, Field(description="rows view: only rows in this state.",
                                            json_schema_extra={"enum": list(ROW_STATUSES)})] = None,
    month: Annotated[str | None, Field(description="rows view: YYYY-MM (UTC).")] = None,
    limit: Annotated[int, Field(description="rows view: how many rows, newest first (1-200).")] = 20,
    view: Annotated[str, Field(description="rows (paid calls, newest first), inflight (calls still running) or "
                                           "reconcile (the ledger against the balance snapshots).",
                               json_schema_extra={"enum": list(tools.LEDGER_VIEWS)})] = "rows",
    session: Annotated[str | None, Field(description="reconcile view: the session label the snapshots were "
                                                     "taken with.")] = None,
):
    return _safely("slate ledger", lambda: tools.ledger(film, provider, row_status, month, limit, view, session))


def balance_snapshot(
    provider: Annotated[str | None, Field(description="Leave it out to record every balance Slate can read itself.",
                                          json_schema_extra={"enum": list(tools.PROVIDERS)})] = None,
    balance: Annotated[float | None, Field(description="A balance read somewhere else (a connector's balance tool or "
                                                       "the vendor's dashboard), for the one provider named.")] = None,
    session: Annotated[str | None, Field(description="One label for this chat or session, used again at its "
                                                     "end.")] = None,
    note: Annotated[str | None, Field(description="Where the balance came from.")] = None,
):
    return _safely("slate ledger snapshot", lambda: tools.balance_snapshot(provider, balance, session, note))


def guide(topic: Annotated[str | None, Field(description="A topic (e.g. 'motion' or 'motion/eases'). Leave it out "
                                                         "to list them.")] = None):
    return _safely("slate guide", lambda: tools.guide(topic), limit=tools.MAX_GUIDE)


# ---------------------------------------------------------------------------------------------------------------
# The tool table


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable
    title: str
    description: str
    read_only: bool
    destructive: bool = False
    open_world: bool = False
    meta: dict | None = None


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("status", status, "Film status",
             "Where a film stands: every stage fresh, stale, missing or locked, and the one next command to run. "
             "Without a film, lists the films. CLI: slate status [--film F].", read_only=True),
    ToolSpec("new_film", new_film, "Start a film",
             "Start a film folder from a request: the brief (the request word for word, with its checklist), a "
             "script skeleton and the stage folders, in the films folder. CLI: slate new <id> --brand B --format F "
             "--request TEXT.", read_only=False),
    ToolSpec("run", run, "Run a quick stage",
             "Run one stage and return its result. Stages that read or write media (qa, render stability, review, "
             "deliver) go to the job queue on their own and return a job id. Stages: " + _stage_list(policy.RUN)
             + ". CLI: the command each result names.", read_only=False),
    ToolSpec("job_submit", job_submit, "Queue a long stage",
             "Queue a long stage as a background job and get its id at once; it keeps running whoever started it. "
             "Stages that spend (voice tts, the tag loop's rounds, score compose, ElevenLabs alignment) need "
             "spend=true, set only after the person has seen the estimate and agreed. Stages: "
             + _stage_list(policy.JOBS) + ". CLI: the stage's own command with --detach, or slate job submit.",
             read_only=False, open_world=True),
    ToolSpec("job_status", job_status, "Job status",
             "One job's state, attempt, reason and log path; without a job id, the queue at a glance and the recent "
             "jobs. CLI: slate job status <id>, slate jobs status.", read_only=True),
    ToolSpec("job_log", job_log, "Job log", "The end of a job's log. CLI: slate job log <id>.", read_only=True),
    ToolSpec("job_cancel", job_cancel, "Cancel a job",
             "Cancel a queued or running job. Slate's long stages resume where they stopped if the job is sent back "
             "later (slate job retry <id>). CLI: slate job cancel <id>.", read_only=False, destructive=True),
    ToolSpec("script_get", script_get, "Read the script",
             "The film's script/script.json: the one hand-edited script file (acts of shots with vo, on_screen, "
             "picture and source). CLI: read the file.", read_only=True),
    ToolSpec("script_put", script_put, "Write the script",
             "Write the film's whole script/script.json, checked against its schema, then lint it and return the "
             "gates. Refused when the script is approved or locked: approved work changes only through the "
             "person's notes. Then run 'script build'. CLI: edit the file, then slate script lint.",
             read_only=False, destructive=True),
    ToolSpec("budget", budget, "Budgets",
             "This month's spend against the caps for each mode, and each film's budget. Read-only: a person changes "
             "budgets in their own terminal. CLI: slate budget show.", read_only=True),
    ToolSpec("ledger", ledger, "Spend ledger",
             "Every paid call Slate made, newest first; the calls still in flight; or how the ledger squares with "
             "the vendors' balance snapshots (reconcile). CLI: slate ledger list, slate ledger inflight, slate "
             "ledger reconcile.", read_only=True),
    ToolSpec("balance_snapshot", balance_snapshot, "Record a balance",
             "Record a vendor's balance in Slate's ledger: one Slate reads itself, or one read from a connector or "
             "dashboard. It records and never spends. Generations made through a hosted connector bypass the ledger, "
             "so take one at a session's start and end with the same session label, then reconcile. CLI: slate "
             "ledger snapshot.", read_only=False),
    ToolSpec("guide", guide, "Guides",
             "Slate's reference guides, read straight from its skills: without a topic, the list of topics; with "
             "one, the file itself. CLI: slate guide [topic].", read_only=True,
             meta={"anthropic/maxResultSizeChars": 200_000}),
)

TOOL_NAMES = tuple(t.name for t in TOOLS)


def build_server():
    """The MCP server with every Slate tool registered (not yet serving)."""
    from mcp.server.mcpserver import MCPServer  # noqa: PLC0415
    from mcp_types import ToolAnnotations  # noqa: PLC0415

    server = MCPServer(
        name="slate", title="Slate", version=__version__, instructions=INSTRUCTIONS,
        description="A film studio any agent can run: story, voice, motion, sound, QA and delivery.",
        log_level="WARNING",
    )
    for spec in TOOLS:
        annotations = ToolAnnotations(title=spec.title, read_only_hint=spec.read_only,
                                      destructive_hint=None if spec.read_only else spec.destructive,
                                      open_world_hint=spec.open_world)
        server.add_tool(spec.fn, name=spec.name, title=spec.title, description=spec.description,
                        annotations=annotations, meta=spec.meta, structured_output=False)
    return server


def describe() -> list[dict]:
    """Each tool as a client sees it: name, title, description, input schema and hints."""
    server = build_server()
    listed = asyncio.run(server.list_tools())
    out = []
    for tool in listed:
        ann = tool.annotations
        out.append({"name": tool.name, "title": tool.title, "description": tool.description,
                    "input_schema": tool.input_schema,
                    "read_only": bool(ann and ann.read_only_hint), "destructive": bool(ann and ann.destructive_hint),
                    "open_world": bool(ann and ann.open_world_hint)})
    return out


DRAIN_SECONDS = 120.0  # how long requests already read may run after the client hangs up


def _is_request(message: Any) -> bool:
    return getattr(message, "method", None) is not None and getattr(message, "id", None) is not None


def _is_reply(message: Any) -> bool:
    return getattr(message, "method", None) is None and getattr(message, "id", None) is not None


async def _serve_stdio(mcp_server) -> None:
    """Serve over stdio, answering every request already read before the server hangs up.

    The SDK's dispatcher cancels in-flight requests the moment stdin closes. A client that keeps stdin open never
    notices, but one that writes its requests and closes stdin at once (a shell pipe, `slate mcp --check`, an agent
    without MCP support) would lose its answers. So stdin's end is passed on only once every request read so far
    has its reply, or after DRAIN_SECONDS.
    """
    import anyio  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    # The SDK's own in-memory transport reaches the low-level server the same way; the mcp major version is pinned.
    low = mcp_server._lowlevel_server  # noqa: SLF001
    pending: set = set()
    async with stdio_server() as (wire_in, wire_out):
        to_server, server_in = anyio.create_memory_object_stream(0)
        server_out, from_server = anyio.create_memory_object_stream(0)

        async def inbound() -> None:
            async with to_server:
                async for item in wire_in:
                    message = getattr(item, "message", None)
                    if _is_request(message):
                        pending.add(message.id)
                    await to_server.send(item)
                with anyio.move_on_after(DRAIN_SECONDS):
                    while pending:
                        await anyio.sleep(0.02)

        async def outbound() -> None:
            async with from_server:
                async for item in from_server:
                    message = getattr(item, "message", None)
                    if _is_reply(message):
                        pending.discard(message.id)
                    await wire_out.send(item)
            await wire_out.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(inbound)
            tg.start_soon(outbound)
            async with server_out:
                await low.run(server_in, server_out, low.create_initialization_options())


def serve() -> None:
    """Serve over stdin and stdout until the client hangs up. Stdout is the protocol; everything else is stderr."""
    import anyio  # noqa: PLC0415

    if sys.stdin.isatty():
        print("Slate's MCP server speaks JSON-RPC on stdin and stdout, so agents start it themselves "
              "(for example: claude mcp add --scope user slate -- slate mcp). Ctrl-C stops it.", file=sys.stderr)
    anyio.run(_serve_stdio, build_server())


__all__ = ["build_server", "serve", "describe", "TOOLS", "TOOL_NAMES", "INSTRUCTIONS", "MODES", "GENERATION"]
