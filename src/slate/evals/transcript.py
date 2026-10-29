"""Agent transcripts, read the same way whichever agent wrote them.

Each agent records a run in its own format:

- Claude Code's print mode with `--output-format stream-json`: `system` init, `assistant` messages with `tool_use`
  parts, `user` messages carrying `tool_result`s, and a final `result`. A confirmed skill load is a `Skill` call with
  a successful matched result.
- `codex exec --json`: JSONL `thread.started`, `item.started|updated|completed` (agent_message, command_execution,
  file_change, mcp_tool_call, web_search, dynamic_tool_call …) and `turn.completed|failed`. A confirmed skill load is
  a completed command that reads its SKILL.md and exits successfully.
- OpenClaw: the session transcript (`session` and `message` lines, assistant `toolCall` parts, `toolResult`
  messages), or the Gateway's `chat.history` JSON, plus the `openclaw agent --json` envelope. A loaded skill is a
  `read` (or an exec) of its SKILL.md with a successful matched result.

All three become one list of `Event`s, so every grader works on every agent. Tool names are normalised to
`mcp__<server>__<tool>` for MCP tools, whatever the agent called them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from slate.evals import shell

FORMATS = ("claude-stream-json", "codex-jsonl", "openclaw")

QUESTION_TOOLS = {"AskUserQuestion", "ask_user", "ask_user_question", "request_user_input", "askuser", "ask"}
SHELL_TOOLS = {"Bash", "exec", "bash", "shell", "exec_command", "gateway_exec", "local_shell", "run_command",
               "run_shell_command"}
READ_TOOLS = {"Read", "NotebookRead", "read", "read_file", "view", "cat"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "write", "edit", "apply_patch", "write_file",
               "create_file", "str_replace", "multi_edit"}
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.M)


@dataclass
class Event:
    """One thing the agent did or said, in order."""

    index: int
    kind: str  # text | command | tool | skill | read | write | question | error
    name: str = ""  # tool name, skill name, or file path
    text: str = ""  # message text, command line, or question
    args: dict = field(default_factory=dict)
    output: str = ""
    ok: bool | None = None  # None: we don't know how it ended
    exit_code: int | None = None
    status: str = "completed"  # completed | in_progress | failed | denied
    ref: str = ""  # where it came from ("line 12")
    started_at: int | None = None  # source record where this action was first observed
    completed_at: int | None = None  # source record carrying its terminal result, when present

    def brief(self, width: int = 110) -> str:
        what = self.text or self.name or json.dumps(self.args, ensure_ascii=False)[:200]
        what = re.sub(r"\s+", " ", what).strip()
        if len(what) > width:
            what = what[: width - 1] + "…"
        label = {"command": "ran", "tool": f"called {self.name}", "skill": "loaded skill", "read": "read",
                 "write": "wrote", "question": "asked", "text": "said", "error": "error"}.get(self.kind, self.kind)
        if self.kind in ("skill", "read", "write"):
            what = self.name
        return f"event {self.index} ({self.ref or '?'}): {label} {what}".rstrip()


@dataclass
class Transcript:
    format: str
    events: list[Event] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    final_text: str = ""
    run_error: str | None = None
    probe: list[dict] | None = None  # the child-process probe log; None when there was none
    tripwire: list[str] | None = None  # the BROWSER tripwire log; None when there was none
    meta: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # lines we couldn't read

    # ---- how the run ended ---------------------------------------------------------------------------------------
    @property
    def ended(self) -> str:
        """ok | empty | killed | error."""
        if not self.events and not self.final_text.strip():
            return "empty"
        if self.meta.get("killed"):
            return "killed"
        if self.run_error or self.meta.get("run_error"):
            return "error"
        code = self.meta.get("exit_code")
        if code not in (None, 0):
            return "error"
        return "ok"

    def ended_reason(self) -> str:
        e = self.ended
        if e == "empty":
            return "left no transcript we can read"
        if e == "killed":
            secs = self.meta.get("duration_s")
            return f"was killed after {secs:.0f} s" if isinstance(secs, (int, float)) else "was killed"
        if e == "error":
            why = self.run_error or self.meta.get("run_error") or f"exit code {self.meta.get('exit_code')}"
            return f"ended with an error ({why})"
        return "finished"

    # ---- views ----------------------------------------------------------------------------------------------------
    def of_kind(self, *kinds: str) -> list[Event]:
        return [e for e in self.events if e.kind in kinds]

    def commands(self) -> list[Event]:
        return self.of_kind("command")

    def simple_commands(self) -> list[tuple[Event, shell.Simple]]:
        return [(e, s) for e in self.commands() for s in shell.parse(e.text)]

    def slate_calls(self) -> list[tuple[Event, shell.SlateCall]]:
        out = []
        for e, s in self.simple_commands():
            call = shell.slate_call(s)
            if call is not None:
                out.append((e, call))
        return out

    def skills_loaded(self, *, confirmed: bool = False) -> list[tuple[str, Event]]:
        """Recognized skill-loading actions. Negative routing checks retain attempts; positive checks request only
        terminal successes. A tool invocation without a result is an attempt, not proof that its skill loaded."""
        out: list[tuple[str, Event]] = []
        for e in self.events:
            if confirmed and not (e.status == "completed" and e.ok is True and e.exit_code in (None, 0)
                                  and (e.started_at is None or e.completed_at is not None)):
                continue
            if e.kind == "skill" and e.name:
                out.append((normalize_skill(e.name), e))
            elif e.kind == "read":
                out += [(n, e) for n in shell.skill_names(e.name)]
            elif e.kind == "command":
                for s in shell.parse(e.text):
                    out += [(n, e) for n in shell.skills_read_by(s)]
            elif e.kind == "tool" and re.search(r"(?i)read|view|get_file|cat", e.name):
                out += [(n, e) for n in shell.skill_names(json.dumps(e.args, ensure_ascii=False))]
        return out

    def writes(self) -> list[tuple[str, Event]]:
        """(path, event) for every file the agent wrote or edited, through a file tool or a shell command."""
        out: list[tuple[str, Event]] = []
        for e in self.events:
            if e.kind == "write" and e.name:
                out.append((e.name, e))
            elif e.kind == "command":
                for s in shell.parse(e.text):
                    out += [(p, e) for p in shell.writes(s)]
                out += [(p, e) for p in shell.heredoc_writes(e.text)]
            elif e.kind == "tool" and re.search(r"(?i)write|edit|patch|create_file|save_file", e.name):
                path = e.args.get("path") or e.args.get("file_path")
                if isinstance(path, str):
                    out.append((path, e))
        return out

    def questions(self) -> list[Event]:
        return self.of_kind("question")

    def final_event(self) -> Event | None:
        texts = self.of_kind("text")
        return texts[-1] if texts else None

    @property
    def final(self) -> str:
        """The agent's last words: the run's result text, else its last message."""
        if self.final_text.strip():
            return self.final_text
        last = self.final_event()
        return last.text if last else ""


def normalize_skill(name: str) -> str:
    """`/slate`, `$slate` and `plugin:slate` are all the skill `slate`."""
    name = name.strip().lstrip("/$")
    return name.rsplit(":", 1)[-1]


def normalize_tool(name: str) -> str:
    """`aside__aside_click` (OpenClaw) and `mcp__aside__exec` (Claude Code) both become `mcp__server__tool`."""
    if name.startswith("mcp__") or "__" not in name:
        return name
    return f"mcp__{name}"


# ---- parsing helpers -------------------------------------------------------------------------------------------------

def _json_lines(text: str) -> Iterable[tuple[int, Any]]:
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            yield n, json.loads(line)
        except json.JSONDecodeError:
            yield n, None


def _text_of(content: Any) -> str:
    """Text from a tool result or message content, whatever its shape."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                if isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif p.get("content") is not None:
                    parts.append(_text_of(p.get("content")))
        return "\n".join(x for x in parts if x)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return _text_of(content["content"])
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _question_text(args: dict) -> str:
    qs = args.get("questions")
    if isinstance(qs, list) and qs:
        chunks = []
        for q in qs:
            if not isinstance(q, dict):
                chunks.append(str(q))
                continue
            opts = " / ".join(
                f"{o.get('label', '')} {o.get('description', '')}".strip() if isinstance(o, dict) else str(o)
                for o in (q.get("options") or [])
            )
            chunks.append(f"{q.get('question', '')} {opts}".strip())
        return "\n".join(chunks)
    return str(args.get("question") or args.get("prompt") or args.get("text") or "")


def _question_count(args: dict) -> int:
    qs = args.get("questions")
    return len(qs) if isinstance(qs, list) and qs else 1


def _patch_paths(args: dict) -> str:
    patch = args.get("patch") or args.get("input") or args.get("raw") or ""
    found = _PATCH_FILE_RE.findall(patch if isinstance(patch, str) else "")
    return found[0] if found else ""


def _tool_event(index: int, name: str, args: Any, ref: str) -> Event:
    """Classify one tool call from any agent."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    if not isinstance(args, dict):
        args = {"value": args}
    if name in SHELL_TOOLS:
        cmd = args.get("command") or args.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return Event(index, "command", name=name, text=str(cmd), args=args, ref=ref)
    if name == "Skill" or name.lower() in ("skill", "load_skill", "use_skill"):
        skill = args.get("skill") or args.get("command") or args.get("name") or ""
        return Event(index, "skill", name=str(skill), args=args, ref=ref)
    if name in READ_TOOLS:
        path = args.get("file_path") or args.get("path") or args.get("notebook_path") or ""
        return Event(index, "read", name=str(path), args=args, ref=ref)
    if name in WRITE_TOOLS:
        path = args.get("file_path") or args.get("path") or args.get("notebook_path") or _patch_paths(args)
        return Event(index, "write", name=str(path), args=args, ref=ref)
    if name in QUESTION_TOOLS:
        ev = Event(index, "question", name=name, text=_question_text(args), args=args, ref=ref)
        ev.args = {**args, "_count": _question_count(args)}
        return ev
    return Event(index, "tool", name=normalize_tool(name), args=args, ref=ref)


# ---- Claude Code: --output-format stream-json ----------------------------------------------------------------------

def parse_claude(text: str) -> Transcript:
    t = Transcript("claude-stream-json")
    by_id: dict[str, Event] = {}
    for n, obj in _json_lines(text):
        if not isinstance(obj, dict):
            t.problems.append(f"line {n} isn't JSON")
            continue
        kind = obj.get("type")
        if kind == "system":
            if obj.get("subtype") == "init":
                for key in ("model", "cwd", "tools", "mcp_servers", "skills", "slash_commands", "permissionMode",
                            "claude_code_version", "session_id"):
                    if key in obj:
                        t.info[key] = obj[key]
            continue
        if kind == "assistant":
            for part in (obj.get("message") or {}).get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and str(part.get("text", "")).strip():
                    t.events.append(Event(len(t.events), "text", text=part["text"], ref=f"line {n}"))
                elif part.get("type") == "tool_use":
                    ev = _tool_event(len(t.events), str(part.get("name", "")), part.get("input") or {}, f"line {n}")
                    ev.started_at = n
                    t.events.append(ev)
                    if part.get("id"):
                        by_id[part["id"]] = ev
            continue
        if kind == "user":
            content = (obj.get("message") or {}).get("content")
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    ev = by_id.get(part.get("tool_use_id"))
                    if ev is None:
                        continue
                    ev.output = _text_of(part.get("content"))
                    ev.completed_at = n
                    failed = bool(part.get("is_error"))
                    ev.ok = not failed
                    ev.status = "failed" if failed else "completed"
                    if failed:
                        m = re.search(r"[Ee]xit code (\d+)", ev.output)
                        ev.exit_code = int(m.group(1)) if m else None
                    elif ev.kind == "command":
                        ev.exit_code = 0
            continue
        if kind == "result":
            if isinstance(obj.get("result"), str):
                t.final_text = obj["result"]
            subtype = obj.get("subtype")
            if obj.get("is_error") or (subtype and subtype != "success"):
                t.run_error = str(subtype or "error")
            for denial in obj.get("permission_denials") or []:
                ev = by_id.get((denial or {}).get("tool_use_id"))
                if ev is not None:
                    ev.status, ev.ok = "denied", False
                    ev.completed_at = n
            t.info["result"] = {k: obj.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms")}
    return t


# ---- Codex: codex exec --json ----------------------------------------------------------------------------------------

def parse_codex(text: str) -> Transcript:
    t = Transcript("codex-jsonl")
    items: dict[str, tuple[int, int, dict, str]] = {}
    order: list[str] = []
    failures: list[str] = []
    for n, obj in _json_lines(text):
        if not isinstance(obj, dict):
            t.problems.append(f"line {n} isn't JSON")
            continue
        kind = str(obj.get("type", ""))
        if kind == "thread.started":
            t.info["thread_id"] = obj.get("thread_id")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = obj.get("item") or {}
            iid = str(item.get("id") or f"line{n}")
            if iid not in items:
                order.append(iid)
            started_at = items[iid][0] if iid in items else n
            if iid in items and item.get("type") == "command_execution":
                previous = items[iid][2]
                # Updates can omit native request metadata. Retain only cwd fields that were explicitly recorded;
                # no run default or caller environment may fill a missing command cwd.
                item = {**{k: previous[k] for k in ("cwd", "workdir", "working_directory") if k in previous}, **item}
            items[iid] = (started_at, n, item, kind)
        elif kind == "turn.completed":
            t.info["usage"] = obj.get("usage")
        elif kind == "turn.failed":
            err = obj.get("error") or {}
            failures.append(str(err.get("message") if isinstance(err, dict) else err))
        elif kind == "error":
            t.info.setdefault("errors", []).append(obj.get("message"))
    for iid in order:
        started_at, n, item, last = items[iid]
        for ev in _codex_item(item, last, len(t.events), f"line {n}"):
            ev.index = len(t.events)
            ev.started_at = started_at
            if last == "item.completed":
                ev.completed_at = n
            t.events.append(ev)
    messages = t.of_kind("text")
    if messages:
        t.final_text = messages[-1].text
    if failures:
        t.run_error = failures[-1]
    return t


def _codex_item(item: dict, last: str, index: int, ref: str) -> list[Event]:
    kind = str(item.get("type", ""))
    status = str(item.get("status") or ("completed" if last == "item.completed" else "in_progress"))
    if last != "item.completed" and status == "completed":
        status = "in_progress"  # started, never finished
    if kind == "agent_message":
        text = str(item.get("text") or "")
        return [Event(index, "text", text=text, ref=ref)] if text.strip() else []
    if kind in ("reasoning", "todo_list"):
        return []  # private reasoning and plans aren't actions
    if kind == "command_execution":
        code = item.get("exit_code")
        ok = None if code is None else code == 0
        args = {k: item[k] for k in ("cwd", "workdir", "working_directory") if k in item}
        return [Event(index, "command", name="shell", text=str(item.get("command") or ""), args=args, output=str(
            item.get("aggregated_output") or ""), ok=ok, exit_code=code, status=status, ref=ref)]
    if kind == "file_change":
        return [Event(index, "write", name=str(ch.get("path", "")), args={"kind": ch.get("kind")}, status=status,
                      ref=ref) for ch in item.get("changes") or [] if isinstance(ch, dict)]
    if kind == "mcp_tool_call":
        name = f"mcp__{item.get('server', '')}__{item.get('tool', '')}"
        result = item.get("result")
        failed = bool(item.get("error")) or status == "failed" or (
            isinstance(result, dict) and result.get("isError") is True)
        ok = False if failed else True if status == "completed" and result is not None else None
        return [Event(index, "tool", name=name, args=item.get("arguments") or {}, output=_text_of(result),
                      ok=ok, status=status, ref=ref)]
    if kind == "web_search":
        return [Event(index, "tool", name="web_search", args={"query": item.get("query")}, status=status, ref=ref)]
    if kind == "error":
        return [Event(index, "error", text=str(item.get("message") or ""), ref=ref)]
    if "skill" in kind and (item.get("name") or item.get("skill")):
        return [Event(index, "skill", name=str(item.get("name") or item.get("skill")), args=item, status=status,
                      ref=ref)]
    name = str(item.get("tool") or item.get("name") or kind)
    args = item.get("arguments") or item.get("input") or {k: v for k, v in item.items()
                                                          if k not in ("id", "type", "status")}
    ev = _tool_event(index, name, args, ref)
    ev.status = status
    return [ev]


# ---- OpenClaw: session transcript, chat.history, and the agent --json envelope ----------------------------------------

def parse_openclaw(text: str, envelope: dict | None = None) -> Transcript:
    t = Transcript("openclaw")
    messages: list[tuple[str, dict]] = []
    stripped = text.strip()
    whole: Any = None
    if stripped[:1] in ("{", "["):
        try:
            whole = json.loads(stripped)
        except json.JSONDecodeError:
            whole = None
    if isinstance(whole, dict) and isinstance(whole.get("messages"), list):
        messages = [("history", m) for m in whole["messages"] if isinstance(m, dict)]
        t.info["session_key"] = whole.get("sessionKey")
    elif isinstance(whole, list):
        messages = [("history", m) for m in whole if isinstance(m, dict)]
    elif isinstance(whole, dict) and _looks_like_envelope(whole):
        envelope = envelope or whole
    else:
        for n, obj in _json_lines(text):
            if not isinstance(obj, dict):
                t.problems.append(f"line {n} isn't JSON")
                continue
            if obj.get("type") == "session":
                t.info["session_id"] = obj.get("id")
                t.info["cwd"] = obj.get("cwd")
            elif obj.get("type") == "message" and isinstance(obj.get("message"), dict):
                messages.append((f"line {n}", obj["message"]))
            elif "role" in obj and "content" in obj:
                messages.append((f"line {n}", obj))
    by_id: dict[str, Event] = {}
    for source_position, (ref, m) in enumerate(messages, 1):
        role = m.get("role")
        content = m.get("content")
        if role == "assistant":
            parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for part in parts:
                if isinstance(part, str):
                    part = {"type": "text", "text": part}
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text" and str(part.get("text", "")).strip():
                    t.events.append(Event(len(t.events), "text", text=part["text"], ref=ref))
                elif ptype in ("toolCall", "tool_use", "toolUse", "functionCall", "function_call"):
                    name = str(part.get("name") or part.get("toolName") or "")
                    args = part.get("arguments") if part.get("arguments") is not None else part.get("input")
                    ev = _tool_event(len(t.events), name, args or {}, ref)
                    ev.started_at = source_position
                    t.events.append(ev)
                    call_id = part.get("id") or part.get("toolCallId")
                    if call_id:
                        by_id[str(call_id)] = ev
        elif role in ("toolResult", "tool"):
            ev = by_id.get(str(m.get("toolCallId") or m.get("tool_call_id") or ""))
            if ev is not None:
                ev.output = _text_of(content)
                ev.completed_at = source_position
                failed = bool(m.get("isError"))
                if ev.kind == "command":
                    # A successful tool response does not prove that its child command exited successfully. Native
                    # exec records may carry an explicit exit or the standard terminal footer; neither is inferred
                    # from isError false. A running response or an omitted command exit remains ambiguous.
                    details = m.get("details") if isinstance(m.get("details"), dict) else {}
                    codes = [code for code in (details.get("exitCode"), m.get("exitCode"), m.get("exit_code"))
                             if type(code) is int]
                    footer = re.search(r"(?:^|\n)\(Command exited with code (-?\d+)\)\s*\Z", ev.output)
                    if footer:
                        codes.append(int(footer.group(1)))
                    code = next((value for value in codes if value != 0), codes[0] if codes else None)
                    states = [status for status in (m.get("status"), details.get("status")) if status is not None]
                    state_failed = any(status in ("failed", "error", "denied") for status in states)
                    pending = any(status in ("running", "in_progress", "queued") for status in states)
                    unknown_state = any(status not in ("completed", "exited", "failed", "error", "denied",
                                                       "running", "in_progress", "queued") for status in states)
                    ev.ok = (False if failed or state_failed or code not in (None, 0) else
                             None if pending or unknown_state or code is None else True)
                    ev.exit_code = code if ev.ok is not None else None
                    ev.status = "failed" if ev.ok is False else "in_progress" if pending else "completed"
                    if (pending or unknown_state) and ev.ok is not False:
                        ev.completed_at = None
                else:
                    ev.ok = not failed
                    ev.status = "failed" if failed else "completed"
    if envelope:
        _apply_envelope(t, envelope)
    last = t.final_event()
    if last is not None:
        t.final_text = last.text
    return t


def _looks_like_envelope(obj: dict) -> bool:
    return any(k in obj for k in ("payloads", "toolSummary", "final")) or isinstance(obj.get("meta"), dict)


def _find(obj: Any, key: str) -> Any:
    """The first value under `key` anywhere in a nested JSON object."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find(value, key)
            if found is not None:
                return found
    return None


def _apply_envelope(t: Transcript, env: dict) -> None:
    """What the `openclaw agent --json` envelope adds: the session id, the final text, the tool names used, and how the
    run ended. Tool names fill in only when the full transcript couldn't be read."""
    t.info["envelope"] = {k: env.get(k) for k in ("status", "ok", "model", "provider", "sessionId") if k in env}
    session_id = _find(env, "sessionId")
    if session_id:
        t.info.setdefault("session_id", session_id)
    status = _find(env, "status")
    if status in ("error", "timeout"):
        err = _find(env, "error")
        t.run_error = f"{status}: {err.get('message') if isinstance(err, dict) else err}" if err else str(status)
    final = _find(env, "final")
    payloads = _find(env, "payloads")
    if not t.of_kind("text"):
        text = final if isinstance(final, str) else _text_of(payloads) if payloads else ""
        if text.strip():
            t.events.append(Event(len(t.events), "text", text=text, ref="envelope"))
    summary = _find(env, "toolSummary")
    if isinstance(summary, dict) and not t.of_kind("command", "tool", "read", "write", "skill", "question"):
        for name in summary.get("tools") or []:
            ev = _tool_event(len(t.events), str(name), {}, "envelope toolSummary")
            t.events.append(ev)


PARSERS = {"claude-stream-json": parse_claude, "codex-jsonl": parse_codex, "openclaw": parse_openclaw}


def parse(text: str, fmt: str, *, envelope: dict | None = None) -> Transcript:
    if fmt not in PARSERS:
        raise ValueError(f"Unknown transcript format {fmt!r}; one of {', '.join(FORMATS)}.")
    if fmt == "openclaw":
        return parse_openclaw(text, envelope)
    return PARSERS[fmt](text)


def _read_jsonl(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    out = []
    for _n, obj in _json_lines(path.read_text(encoding="utf-8", errors="replace")):
        if isinstance(obj, dict):
            out.append(obj)
    return out


def load(folder: Path, fmt: str) -> Transcript:
    """A run folder or fixture: transcript.jsonl, plus meta.json, probe.jsonl, tripwire.log and run.json (OpenClaw's
    envelope) when they exist."""
    folder = Path(folder)
    path = folder / "transcript.jsonl"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    envelope = None
    run_json = folder / "run.json"
    if run_json.exists():
        try:
            envelope = json.loads(run_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            envelope = None
    t = parse(text, fmt, envelope=envelope if isinstance(envelope, dict) else None)
    meta_path = folder / "meta.json"
    if meta_path.exists():
        try:
            t.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            t.problems.append("meta.json isn't JSON")
    t.probe = _read_jsonl(folder / "probe.jsonl")
    trip = folder / "tripwire.log"
    if trip.exists():
        t.tripwire = [line for line in trip.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return t
