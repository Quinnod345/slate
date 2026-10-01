"""The OpenClaw gateway's egress proxy, and the way round it that Slate owns: the job queue.

An agent working inside the OpenClaw gateway reaches the internet only through the gateway's egress proxy, which lets
through just the hosts in `secrets.egressProxy.allowedHosts` in ~/.openclaw/openclaw.json. ElevenLabs and Higgsfield
aren't on that list, so a vendor call from there dies at the proxy's tunnel ("Tunnel connection failed: 403
Forbidden") before it reaches the vendor. The list is the owner's security setting: Slate reads it to know where it
stands and never edits it.

So, before a live vendor call (`route`):

- outside the gateway (a person's Terminal, Claude Code, a Codex slice, a queued job) nothing changes;
- inside it, when the allowlist lets the vendor's host through, nothing changes either;
- otherwise the call isn't tried. When the process is the `slate` command itself and Slate's launchd job runner is
  loaded (it runs outside the gateway), the command submits itself to the job queue and stops with the job's id
  (`QueuedOutsideGateway`, exit 3: it didn't run here, it runs there). Otherwise it refuses with the exact next step:
  the queue command to run, loading the runner, or the allowlist (`EgressBlocked`, exit 3).

And a proxy's 403 at the tunnel, wherever it happens (`from_error`), becomes the same `EgressBlocked`: it says what
happened, that nothing reached the vendor and nothing was billed, and names the allowlist and the queue route.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from slate import procs
from slate.errors import MissingPrerequisite

CONFIG_ENV = "SLATE_OPENCLAW_CONFIG"  # where the gateway's config is read from (tests point it at a scratch file)
DEFAULT_CONFIG = "~/.openclaw/openclaw.json"
ALLOWLIST_KEY = "secrets.egressProxy.allowedHosts"
ALLOWLIST = f"{ALLOWLIST_KEY} in {DEFAULT_CONFIG}"
HOSTS = {"elevenlabs": "api.elevenlabs.io", "higgsfield": "api.higgsfield.ai"}
NAMES = {"elevenlabs": "ElevenLabs", "higgsfield": "Higgsfield"}
# The job-queue slot a vendor's work takes (slate.jobs.model.RESOURCES).
RESOURCE = {"elevenlabs": "elevenlabs_tts", "elevenlabs_music": "elevenlabs_music", "higgsfield": "higgsfield"}
MUSIC_OPS = ("music", "compose", "compose_music", "plan")
# Commands that never queue themselves: the server, the checks of this very context, the queue itself, setup, and
# anything that only reads. They refuse with the next step instead.
NEVER_QUEUE = frozenset({"mcp", "doctor", "connect", "job", "jobs", "budget", "ledger", "install", "uninstall",
                         "update", "guide", "status", "evals", "knowledge", "approved"})
TUNNEL_RE = re.compile(r"tunnel connection failed:\s*(\d{3})", re.IGNORECASE)
RUNNER_LABEL = "Slate's launchd job runner"


class EgressBlocked(MissingPrerequisite):
    """A vendor host the egress proxy doesn't let through. Exit 3: what's missing is a route out, and the hint names
    both (the job queue, or the owner's allowlist). Its details never carry an HTTP `status`, so nobody reads the
    proxy's 403 as the vendor turning the key down."""


class QueuedOutsideGateway(EgressBlocked):
    """The command didn't run here: it submitted itself to the job queue, whose launchd runner works outside the
    gateway. `details["job"]` is the job's id; `slate job wait <id>` follows it. Exit 3, so nothing chained after the
    command (`&&`) runs as if the work were done."""


# ---------------------------------------------------------------------------------------------------------------
# Where this process is


def family(provider: str) -> str:
    return "elevenlabs" if str(provider).startswith("elevenlabs") else str(provider)


_CONTEXT: dict[tuple, str | None] = {}


def _agent_context() -> str | None:
    """procs.agent_context(), remembered per environment (it may walk the parent processes)."""
    key = tuple(sorted((k, v) for k, v in os.environ.items()
                       if k in ("SLATE_CONTEXT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
                       or k.startswith(("OPENCLAW", "CODEX_"))))
    if key not in _CONTEXT:
        _CONTEXT[key] = procs.agent_context()
    return _CONTEXT[key]


def gateway_context() -> str | None:
    """The agent context when this process is inside the OpenClaw gateway and isn't a queued job, else None. (A job
    started from the gateway carries its context along, but runs under the launchd runner, outside the gateway.)"""
    if os.environ.get("SLATE_JOB_ID"):
        return None
    ctx = _agent_context()
    return ctx if ctx and str(ctx).lower().startswith("openclaw") else None


def _openclaw_markers() -> bool:
    """Any sign the proxy in the way is the OpenClaw gateway's (for the wording of a refusal seen after the fact)."""
    ctx = _agent_context()
    if ctx and str(ctx).lower().startswith("openclaw"):
        return True
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "SSL_CERT_FILE"):
        if "openclaw" in str(os.environ.get(name) or "").lower():
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------
# The owner's allowlist (read, never written)


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG).expanduser()


def allowlist() -> dict:
    """{file, readable, enabled, hosts}: the gateway's egress allowlist as the owner set it. Only that one list is
    read out of the file; nothing else in it is kept, shown or logged."""
    path = config_path()
    out: dict[str, Any] = {"file": str(path), "readable": False, "enabled": None, "hosts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    proxy = ((data.get("secrets") or {}).get("egressProxy") or {}) if isinstance(data, dict) else {}
    if not isinstance(proxy, dict):
        return out
    hosts = proxy.get("allowedHosts")
    out.update(readable=True, enabled=proxy.get("enabled") if isinstance(proxy.get("enabled"), bool) else None,
               hosts=[str(h).strip().lower() for h in hosts if isinstance(h, str)] if isinstance(hosts, list) else [])
    return out


def host_allowed(host: str) -> bool | None:
    """Does the gateway's egress proxy let `host` through? None when the owner's config can't be read."""
    info = allowlist()
    if not info["readable"]:
        return None
    if info["enabled"] is False:
        return True  # the proxy is off: nothing stands in the way
    host = str(host or "").strip().lower()
    for entry in info["hosts"]:
        if entry == host or (entry.startswith("*.") and host.endswith(entry[1:])) or (
                entry.startswith(".") and host.endswith(entry)):
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------
# The queue route


def command_words() -> list[str] | None:
    """The `slate …` command this process is running, when the process is the slate command itself (the shim, the
    console script or `python -m slate`) and the command is one that may queue itself. None otherwise (the MCP server
    running commands in-process, a library caller, a test)."""
    argv = [str(a) for a in sys.argv]
    if not argv:
        return None
    first = Path(argv[0])
    if not (first.name == "slate" or (first.name == "__main__.py" and first.parent.name == "slate")):
        return None
    words = [w for w in argv[1:] if w != "--detach"]
    positional = [w for w in words if not w.startswith("-")]
    if not positional or positional[0] in NEVER_QUEUE:
        return None
    return words


def _resource(provider: str, op: str | None) -> str:
    if family(provider) == "elevenlabs" and op and any(m in str(op).lower() for m in MUSIC_OPS):
        return RESOURCE["elevenlabs_music"]
    return RESOURCE.get(family(provider), RESOURCE["elevenlabs"])


def _kind(words: list[str] | None, provider: str) -> str:
    named = [w for w in (words or []) if not w.startswith("-")][:2]
    kind = re.sub(r"[^a-z0-9_.-]+", "-", "-".join(named).lower()).strip("-.")[:40]
    return kind if kind and kind[0].isalpha() else family(provider)


def queue_command(words: list[str] | None, provider: str, op: str | None = None) -> str:
    """The exact command that runs this through the job queue."""
    head = f"slate job submit --kind {_kind(words, provider)} --resource {_resource(provider, op)} -- slate"
    if words:
        return f"{head} {shlex.join(words)}"
    return f"{head} <the same command> (or the stage's own --detach)"


def queue_route() -> tuple[bool, str]:
    """(True, "") when Slate's launchd job runner is loaded, so a queued job runs outside the gateway."""
    try:
        from slate.jobs import agent  # noqa: PLC0415

        info = agent.status()
    except Exception as exc:  # noqa: BLE001 - can't tell: don't count on it
        return False, f"{RUNNER_LABEL}'s state couldn't be read ({type(exc).__name__})"
    if info.get("loaded"):
        return True, ""
    if info.get("installed"):
        return False, f"{RUNNER_LABEL} ({info.get('label')}) is installed but not loaded"
    return False, f"{RUNNER_LABEL} ({info.get('label')}) isn't installed"


def submit(words: list[str], provider: str, op: str | None = None, *, host: str) -> tuple[str, bool]:
    """Submit this command to the job queue as it was typed (the same Python, folder and film), or find the same
    command already waiting there. Returns (job id, reused). Never starts a runner from inside the gateway."""
    from slate.jobs import client, model, store  # noqa: PLC0415

    argv = [sys.executable, "-m", "slate", *words]
    here = str(Path.cwd().resolve())
    for job in store.by_state(*model.ACTIVE):
        if job.get("argv") == argv and job.get("cwd") == here:
            return str(job["id"]), True
    film = os.environ.get("SLATE_FILM") or None
    job_id = client.submit(_kind(words, provider), argv, film=film, cwd=here,
                           resources=(_resource(provider, op),),
                           note=f"queued from the OpenClaw gateway: its egress proxy doesn't let {host} through",
                           start_runner=False)
    return job_id, False


# ---------------------------------------------------------------------------------------------------------------
# Before a live call, and after a refusal


def _fixes(provider: str, host: str, words: list[str] | None, op: str | None, *, loaded: bool, why_not: str) -> str:
    name = NAMES.get(family(provider), provider)
    queue = queue_command(words, provider, op)
    runner = "" if loaded else (f" ({why_not}: the owner loads it once, in their own Terminal, with `slate jobs agent "
                                "install`)")
    return (f"Run it through Slate's job queue, whose launchd runner works outside the gateway{runner}: {queue}. Or "
            f"the owner adds {host} to {ALLOWLIST} (their setting; Slate never edits it), and {name} works from inside "
            "the gateway too.")


def route(provider: str, *, host: str | None = None, op: str | None = None, queue: bool = True) -> None:
    """Call before a live request to `provider`. Returns quietly outside the OpenClaw gateway, or when its allowlist lets
    the host through. Otherwise the command queues itself (QueuedOutsideGateway) or refuses with the next step
    (EgressBlocked); either way nothing is sent from here. `queue=False` (a probe, which reports on this very
    context) always refuses instead."""
    ctx = gateway_context()
    if ctx is None:
        return
    host = host or HOSTS.get(family(provider), provider)
    allowed = host_allowed(host)
    if allowed is True:
        return
    name = NAMES.get(family(provider), provider)
    words = command_words()
    loaded, why_not = queue_route()
    details = {"provider": family(provider), "host": host, "egress_proxy": True, "context": ctx,
               "allowlist": ALLOWLIST, "allowlist_file": str(config_path()), "queue": queue_command(words, provider, op)}
    if words and loaded and queue:
        job_id, reused = submit(words, provider, op, host=host)
        raise QueuedOutsideGateway(
            f"{'Already queued' if reused else 'Queued'} as job {job_id}: this command didn't run here. The OpenClaw "
            f"gateway's egress proxy doesn't let {host} through, so Slate's job queue runs it outside the gateway "
            f"instead, where {name} can be reached.",
            hint=f"Follow it with `slate job wait {job_id}` (or `slate job status {job_id}`). To reach {name} from inside "
                 f"the gateway itself, the owner can add {host} to {ALLOWLIST} (their setting; Slate never edits it).",
            details={**details, "job": job_id, "queued": True, "reused": reused})
    why = (f"{ALLOWLIST_KEY} doesn't list it" if allowed is False
           else f"its allowlist in {config_path()} couldn't be read, so Slate counts it as closed")
    raise EgressBlocked(
        f"The OpenClaw gateway's egress proxy doesn't let {host} through ({why}), so {name} can't be reached from "
        "here; nothing was sent.",
        hint=_fixes(provider, host, words, op, loaded=loaded, why_not=why_not), details=details)


def tunnel_refusal(exc: BaseException | str) -> int | None:
    """The HTTP status a proxy answered the tunnel with ("Tunnel connection failed: 403 Forbidden"), if that's what
    this error is."""
    seen: list[str] = []
    cur: Any = exc
    for _ in range(4):  # the error, and what it wraps
        if cur is None:
            break
        seen.append(str(getattr(cur, "message", "") or ""))
        seen.append(str(cur))
        seen.append(str(getattr(cur, "reason", "") or ""))
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    m = TUNNEL_RE.search(" ".join(seen))
    return int(m.group(1)) if m else None


def blocked_error(provider: str, host: str, status: int = 403, *, op: str | None = None) -> EgressBlocked:
    """The error for a proxy that refused the tunnel to `host`: what happened, that nothing was billed, and the fixes."""
    name = NAMES.get(family(provider), provider)
    words = command_words()
    details = {"provider": family(provider), "host": host, "egress_proxy": True, "tunnel_status": status,
               "allowlist": ALLOWLIST, "queue": queue_command(words, provider, op)}
    if _openclaw_markers():
        loaded, why_not = queue_route()
        return EgressBlocked(
            f"The OpenClaw gateway's egress proxy refused the tunnel to {host} (HTTP {status}): {host} isn't on its "
            f"allowlist, so the request never reached {name} and nothing was billed.",
            hint=_fixes(provider, host, words, op, loaded=loaded, why_not=why_not),
            details={**details, "context": _agent_context()})
    return EgressBlocked(
        f"An HTTPS proxy refused the tunnel to {host} (HTTP {status}), so the request never reached {name} and nothing "
        "was billed.",
        hint=(f"Allow {host} in that proxy's settings, or run it where no proxy stands in the way: a shell without "
              f"HTTPS_PROXY, or Slate's job queue ({queue_command(words, provider, op)}). Inside the OpenClaw gateway "
              f"the list is {ALLOWLIST} (the owner's setting; Slate never edits it)."),
        details=details)


def from_error(provider: str, host: str, exc: BaseException, *, op: str | None = None) -> EgressBlocked | None:
    """`blocked_error` when `exc` is a proxy's refusal at the tunnel, else None (the caller re-raises `exc`)."""
    if isinstance(exc, EgressBlocked):
        return exc
    status = tunnel_refusal(exc)
    if status is None:
        return None
    return blocked_error(provider, host, status, op=op)
