"""Building each eval's command: argv, stdin, environment, timeout and how its transcript is collected.

The templates live in `evals/configs.json`; this fills them for one (case, configuration) pair. `--dry-run` prints
exactly what this builds and the live runner executes exactly what this builds, so the printed command is the command.

Placeholders: {prompt} {workdir} {pair_dir} {run_id} {case_id} {config_id} {home} {repo} {evals_dir} {probe_dir}
{binary} {model} {context} {node} {session_key} {mcp_config} {deny} {history_params} {session_id}, and
{block_args} (an argv element that expands to zero or more arguments).

Every sandbox says it's a sandbox: the notice in `evals/sandbox-notice.md` is written into the working folder under the
names its agents read (AGENTS.md for Codex, CLAUDE.md for Claude Code), and an agent that reads its instructions from a
workspace of its own (OpenClaw main) gets it at the top of its message instead.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from slate import paths
from slate.errors import UsageError
from slate.evals import preconditions, strategist, suite


@dataclass
class Invocation:
    case_id: str
    config_id: str
    runner: str  # agent | strategist
    argv: list[str]
    cwd: str
    stdin: str  # devnull | prompt | pipe
    prompt: str
    env_set: dict[str, str]
    env_unset: list[str]
    env_path: list[str] | None  # an explicit PATH (the slice's), else inherited
    timeout_s: int
    kill: str
    kill_grace_s: float
    kill_is_real: bool
    pair_dir: str
    workdir: str
    transcript_format: str
    transcript_source: str
    collect: list[dict] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # written before the run: path -> text
    budget: dict = field(default_factory=dict)
    session_key: str | None = None
    env_reaches_tools: bool = True
    skip: str | None = None  # why this pair won't run live
    precondition: dict | None = None
    notes: list[str] = field(default_factory=list)
    tools_cwd: str | None = None  # where the agent's tools run when it isn't the sandbox (OpenClaw main's workspace)
    notice: dict = field(default_factory=dict)  # where the sandbox notice went: {"files": [...], "in_message": bool}
    shared_locks: list[str] = field(default_factory=list)  # resources whose slots the sandbox shares with this Mac
    cut_off: bool = False  # the case stops its run on purpose: at its time limit, or at its first render

    def to_dict(self) -> dict:
        return asdict(self)

    def shell_line(self) -> str:
        return shlex.join(self.argv)


def _home() -> str:
    return str(Path.home())


def resolve_binary(configured: str, values: dict) -> tuple[str, str | None]:
    """The configured binary if it's there; otherwise the same name on PATH; otherwise the configured path, with a
    note saying it's missing."""
    path = Path(suite.expand(configured, values)).expanduser()
    if path.is_absolute() and os.access(path, os.X_OK):
        return str(path), None
    found = shutil.which(path.name)
    if found:
        note = None if not path.is_absolute() else f"{path} isn't there; using {found} from PATH"
        return found, note
    return str(path), f"{path.name} isn't installed on this Mac (looked for {path} and on PATH)"


def _expand_argv(template: list[str], values: dict) -> list[str]:
    out: list[str] = []
    for arg in template:
        if arg == "{block_args}":
            out += list(values.get("block_args") or [])
            continue
        out.append(suite.expand(arg, values))
    return out


def plan(case: suite.Case, config_id: str, configs: dict, *, run_id: str, run_dir: Path,
         allow_host_env: bool = False, services: bool = False) -> Invocation:
    """Everything needed to run one case on one configuration, without touching disk."""
    cfg = suite.config_entry(configs, config_id)
    pair_dir = Path(run_dir) / case.id / config_id
    workdir = pair_dir / "work"
    ev = suite.evals_dir()
    values: dict = {
        "home": _home(), "repo": str(paths.REPO), "evals_dir": str(ev), "probe_dir": str(suite.PROBE_DIR),
        "run_id": run_id, "case_id": case.id, "config_id": config_id, "pair_dir": str(pair_dir),
        "workdir": str(workdir), "model": cfg.get("model", ""), "context": cfg.get("context", cfg["agent"]),
        "session_id": "{session_id}", "block_args": [],
    }
    notes: list[str] = []
    binary, why = resolve_binary(cfg["binary"], values)
    values["binary"] = binary
    if why:
        notes.append(why)
    node = cfg.get("node")
    if node:
        values["node"], why = resolve_binary(node, values)
        if why:
            notes.append(why)

    # The prompt: the case's own, or, for the strategist harness, the engine's prompt rebuilt from its files.
    prompt_values = {**case.vars, "workdir": str(workdir), "home": _home()}
    prompt = suite.expand(case.prompt, prompt_values)
    pre = preconditions.evaluate(case.precondition, values, services=services) if case.precondition else None
    if case.runner == "strategist":
        h = {k: suite.expand(str(v), values) if isinstance(v, str) else v for k, v in case.harness.items()}
        agents_js, snapshot = Path(h.get("agents_js", "")).expanduser(), Path(h.get("snapshot", "")).expanduser()
        if pre is not None and not pre["met"]:
            # Don't read the pursuit's snapshot until the case can actually run.
            notes.append(f"the strategist prompt is built from {agents_js} and {snapshot} once the precondition holds")
        elif agents_js.is_file() and snapshot.is_file():
            try:
                prompt, caveats = strategist.compose_prompt(agents_js, snapshot, compose=cfg.get("compose"))
                notes += caveats
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                notes.append(f"the strategist prompt can't be built yet: {exc}")
        else:
            notes.append(f"the strategist prompt is built at run time from {agents_js} and {snapshot}")
    ee = configs["eval_env"]
    notice_files, notice_text = sandbox_notice(ee, workdir, ev, pair_dir=pair_dir)
    in_message = bool(cfg.get("notice_in_message") and notice_text and case.runner == "agent")
    if in_message:
        prompt = suite.expand((ee.get("notice") or {}).get("message") or "{notice}\n\n{prompt}",
                              {"notice": notice_text, "prompt": prompt})
    values["prompt"] = prompt

    turn = cfg.get("turn_timeout_s")
    if turn is not None:
        # The agent stops its own turn (OpenClaw's Gateway keeps a turn going after the local CLI is killed), so a
        # cut-off case's limit has to reach the turn itself.
        values["turn_timeout_s"] = str(min(int(turn), int(case.timeout_s)) if case.cut_off and case.timeout_s
                                       else int(turn))
    if cfg.get("session_key"):
        values["session_key"] = suite.expand(cfg["session_key"], values)
        values["history_params"] = json.dumps({"sessionKey": values["session_key"], "limit": 1000})
    deny = list(cfg.get("deny") or [])
    for block in case.block:
        spec = (cfg.get("blocks") or {}).get(block) or {}
        deny += spec.get("deny") or []
        values["block_args"] = list(values["block_args"]) + [suite.expand(a, values) for a in spec.get("args") or []]
        if spec.get("note"):
            notes.append(f"{block}: {spec['note']}")
    values["deny"] = ",".join(deny)
    files: dict[str, str] = dict(notice_files) if case.runner == "agent" else {}
    if cfg.get("mcp_template"):
        target = pair_dir / "mcp.json"
        values["mcp_config"] = str(target)
        # JSON braces are safe here: expand only touches {lowercase_name} placeholders.
        files[str(target)] = suite.expand((ev / cfg["mcp_template"]).read_text(encoding="utf-8"), values)
    argv = _expand_argv(cfg["argv"], values)

    env_set = {k: suite.expand(v, values) for k, v in ee["set"].items()}
    env_cfg = cfg.get("env") or {}
    env_set.update({k: suite.expand(v, values) for k, v in (env_cfg.get("set") or {}).items()})
    env_unset = list(dict.fromkeys(list(ee["unset"]) + list(env_cfg.get("unset") or [])))
    env_path = [suite.expand(p, values) for p in env_cfg["path"]] if env_cfg.get("path") else None
    collect = [{**step, "argv": [a if a == "{session_id}" else suite.expand(a, values) for a in step["argv"]]}
               for step in cfg.get("collect") or []]

    skip = None
    if pre is not None and not pre["met"]:
        skip = pre["summary"]
    elif case.needs_eval_env and not cfg["env_reaches_tools"] and not allow_host_env:
        skip = ("needs the eval environment (replay mode, sandbox folders), which this configuration's tools can't "
                "take; pass --allow-host-env to run it against your real Slate folders")
    if not cfg["env_reaches_tools"]:
        notes.append("this agent's tools run in its own environment: replay mode, the sandbox folders, the probe and "
                     "the BROWSER tripwire don't reach them")
    # A case can ask for more or less time, except where the limit is the real setup's own (the slice's ten minutes),
    # which only a case that stops its run on purpose (cut_off) may shorten, never stretch.
    if cfg.get("kill_is_real"):
        timeout = int(cfg["timeout_s"])
        if case.cut_off and case.timeout_s:
            timeout = min(timeout, int(case.timeout_s))
    else:
        timeout = int(case.timeout_s or cfg["timeout_s"])
    if "turn_timeout_s" in values:
        timeout = max(timeout, int(values["turn_timeout_s"]) + 60)  # the turn ends first; then its transcript is read
    return Invocation(
        case_id=case.id, config_id=config_id, runner=case.runner, argv=argv, cwd=str(workdir), stdin=cfg["stdin"],
        prompt=prompt, env_set=env_set, env_unset=env_unset, env_path=env_path, timeout_s=timeout,
        kill=cfg.get("kill", "group"), kill_grace_s=float(cfg.get("kill_grace_s", 2.0)),
        kill_is_real=bool(cfg.get("kill_is_real", False)), pair_dir=str(pair_dir), workdir=str(workdir),
        transcript_format=cfg["transcript"]["format"], transcript_source=cfg["transcript"]["source"],
        collect=collect, files=files, budget=dict(ee["budget"]), session_key=values.get("session_key"),
        env_reaches_tools=bool(cfg["env_reaches_tools"]), skip=skip, precondition=pre, notes=notes,
        tools_cwd=suite.expand(cfg["tools_cwd"], values) if cfg.get("tools_cwd") else None,
        notice={"files": [Path(f).name for f in notice_files] if case.runner == "agent" else [],
                "in_message": in_message},
        shared_locks=list((ee.get("shared_locks") or {}).get("resources") or []), cut_off=bool(case.cut_off),
    )


def sandbox_notice(eval_env: dict, workdir: Path, evals_root: Path, *, pair_dir: Path | None = None
                   ) -> tuple[dict[str, str], str]:
    """The sandbox notice for one working folder: ({path: text} for each file it's written as, the text). It names the
    pair's whole folder as where writes go, so an agent doesn't move Slate's data folder into its working folder to
    keep a narrower rule (Codex did, on 2026-10-01)."""
    spec = eval_env.get("notice") or {}
    if not spec:
        return {}, ""
    template = evals_root / spec["template"]
    try:
        text = suite.expand(template.read_text(encoding="utf-8"),
                            {"workdir": str(workdir), "pair_dir": str(pair_dir or Path(workdir).parent)})
    except FileNotFoundError as exc:
        raise UsageError(f"The sandbox notice {template} is missing.",
                         hint="It lives in evals/ beside configs.json (eval_env.notice.template).") from exc
    return {str(workdir / name): text for name in spec.get("files") or []}, text


def child_env(inv: Invocation, base: dict | None = None) -> dict[str, str]:
    """The environment the agent gets: this process's, with the eval's settings layered on and secrets removed.
    PYTHONPATH is prepended to (so the probe loads first) rather than replaced."""
    env = dict(os.environ if base is None else base)
    for key in inv.env_unset:
        env.pop(key, None)
    for key, value in inv.env_set.items():
        if key == "PYTHONPATH" and env.get("PYTHONPATH"):
            env[key] = f"{value}{os.pathsep}{env['PYTHONPATH']}"
        else:
            env[key] = value
    if inv.env_path is not None:
        env["PATH"] = os.pathsep.join(inv.env_path)
    return env


def render(inv: Invocation) -> str:
    """The human dry-run view of one invocation."""
    stdin = {"devnull": "none (/dev/null)", "prompt": "a pipe: the prompt is written, then the pipe is closed",
             "pipe": "a pipe, closed at once"}[inv.stdin]
    kill = f"after {inv.timeout_s} s: SIGTERM to the process group, SIGKILL {inv.kill_grace_s:g} s later"
    if inv.kill_is_real:
        kill += " (the real setup's own limit)"
    if inv.cut_off and inv.transcript_source == "stdout":
        kill += ", or at its first render (a cut-off case)"
    lines = [f"{inv.case_id} · {inv.config_id}" + (f"   [won't run: {inv.skip}]" if inv.skip else "")]
    lines.append(f"  cwd     {inv.cwd}")
    lines.append(f"  stdin   {stdin}")
    lines.append(f"  kill    {kill}")
    shown = {k: v for k, v in inv.env_set.items()}
    lines.append("  env     " + " ".join(f"{k}={shlex.quote(v)}" for k, v in shown.items()))
    lines.append(f"  unset   {' '.join(inv.env_unset)}")
    if inv.env_path is not None:
        lines.append(f"  PATH    {os.pathsep.join(inv.env_path)}")
    lines.append(f"  argv    {inv.shell_line()}")
    if inv.stdin == "prompt":
        preview = inv.prompt if len(inv.prompt) < 400 else inv.prompt[:400] + f"… ({len(inv.prompt)} characters)"
        lines.append(f"  prompt  {preview}")
    for step in inv.collect:
        lines.append(f"  collect {shlex.join(step['argv'])}  ({step.get('note', step['stdout'])})")
    for path in inv.files:
        lines.append(f"  creates {path} (before the run)")
    for resource in inv.shared_locks:
        lines.append(f"  shares  this Mac's {resource} slot (SLATE_DATA/locks/{resource} links to {paths.locks_dir()}/"
                     f"{resource})")
    if inv.notice.get("in_message"):
        lines.append("  notice  the sandbox notice opens the message (this agent reads its own workspace, not the "
                     "sandbox)")
    for note in inv.notes:
        lines.append(f"  note    {note}")
    return "\n".join(lines)


def check_pairs(pairs, configs: dict) -> None:
    for case, config_id in pairs:
        if config_id not in suite.all_config_ids(configs):
            raise UsageError(f"{case.id} names an unknown configuration {config_id!r}.")
