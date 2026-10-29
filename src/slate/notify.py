"""iMessage and push notifications for held films and questions: `imessage(text, attachment=None)`,
`push(title, body)`.

Held films and questions reach their owner by iMessage, with the phone cut, and as a push notification
(docs/PLAN.md §7, autonomous mode). The routes are the ones Oneiro already uses, and every piece is configurable in
slate.toml:

    [notify]
    imessage_handle = ""          # the owner's iMessage handle (phone number or Apple ID); SLATE_NOTIFY_IMESSAGE wins
    imessage_route = "auto"       # auto | openclaw | osascript
    openclaw_cli = "openclaw"     # OpenClaw's CLI (its iMessage channel keeps the owner's standing conversation)
    push_route = "auto"           # auto | command | openclaw
    push_command = []             # argv for a push, e.g. ["my-push", "--title", "{title}", "--body", "{body}"]
    push_node = ""                # OpenClaw's paired iPhone node id for its push path; SLATE_NOTIFY_PUSH_NODE wins
    title_prefix = "Slate"

- **iMessage** goes through OpenClaw's iMessage channel (`openclaw message send --channel imessage --target <handle>
  -m <text> [--media <file>]`) when its CLI is here, and falls back to Messages on this Mac through osascript (the
  text and the file passed as arguments, never pasted into the script).
- **Push** runs `push_command` when one is set (`{title}` and `{body}` are filled in; no shell), else OpenClaw's
  gateway push to the paired phone (`openclaw gateway call push.test --params {nodeId, title, body}`).

Nothing is ever sent from a test, a replay run (SLATE_FAKE_VENDORS=1 without --live), with SLATE_NO_NOTIFY=1, or with
`dry_run=True`: each call returns what it would have sent instead. Tests replace `transport` to see the sends; a test
that reaches the real one fails on purpose. Every child process goes through slate.procs.run.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from slate import config, log, procs, replay
from slate.errors import MissingPrerequisite, SlateError, UsageError

DEFAULTS: dict[str, Any] = {
    "imessage_handle": "",
    "imessage_route": "auto",
    "openclaw_cli": "openclaw",
    "push_route": "auto",
    "push_command": [],
    "push_node": "",
    "title_prefix": "Slate",
    "timeout_s": 30,
}
MAX_TITLE = 60
MAX_BODY = 220

# Sends Messages a text and, optionally, a file. The values arrive as arguments (item 1, 2, 3 of argv), so nothing
# the text says can change the script.
APPLESCRIPT = """on run argv
  set theHandle to item 1 of argv
  set theText to item 2 of argv
  tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to participant theHandle of targetService
    if theText is not "" then send theText to targetBuddy
    if (count of argv) > 2 then send (POSIX file (item 3 of argv)) to targetBuddy
  end tell
end run"""


def settings() -> dict:
    """[notify] from slate.toml over the defaults, then the environment."""
    out = copy.deepcopy(DEFAULTS)
    out.update(config.load().get("notify") or {})
    if os.environ.get("SLATE_NOTIFY_IMESSAGE"):
        out["imessage_handle"] = os.environ["SLATE_NOTIFY_IMESSAGE"]
    if os.environ.get("SLATE_NOTIFY_PUSH_NODE"):
        out["push_node"] = os.environ["SLATE_NOTIFY_PUSH_NODE"]
    return out


def mask(handle: str | None) -> str | None:
    """A handle for records and logs: enough to recognise it, not enough to copy it."""
    if not handle:
        return handle
    h = str(handle)
    if "@" in h:
        name, _, domain = h.partition("@")
        return f"{name[:2]}…@{domain}"
    digits = re.sub(r"\D", "", h)
    return f"…{digits[-4:]}" if len(digits) >= 4 else "…"


def blocked() -> str | None:
    """Why nothing may be sent right now, or None when sending is allowed."""
    if os.environ.get("SLATE_NO_NOTIFY") == "1":
        return "SLATE_NO_NOTIFY=1"
    if replay.fake_vendors():
        return "replay mode (SLATE_FAKE_VENDORS=1): nothing is sent without --live"
    return None


def _real_transport(argv: list[str], *, timeout: float) -> dict:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("a test reached slate.notify's real transport; tests must replace slate.notify.transport")
    proc = procs.run(argv, timeout=timeout)
    return {"returncode": proc.returncode, "stdout": (proc.stdout or "")[-2000:], "stderr": (proc.stderr or "")[-2000:]}


# Replaced by tests (and only by tests) to see what would be sent.
transport: Callable[..., dict] = _real_transport


def _display(argv: list[str], handle: str | None) -> list[str]:
    return [mask(a) if handle and a == handle else a for a in argv]


def _record(channel: str, route: str | None, **kw) -> dict:
    return {"channel": channel, "route": route, **kw}


# ---------------------------------------------------------------------------------------------------------------
# iMessage


def _openclaw(cfg: dict) -> str | None:
    cli = str(cfg.get("openclaw_cli") or "openclaw")
    return procs.which(cli)


def _imessage_routes(cfg: dict) -> list[str]:
    route = str(cfg.get("imessage_route") or "auto").strip().lower()
    if route == "auto":
        return (["openclaw"] if _openclaw(cfg) else []) + ["osascript"]
    if route not in ("openclaw", "osascript"):
        raise UsageError(f"[notify] imessage_route {route!r} isn't one of auto, openclaw, osascript.")
    return [route]


def _imessage_argv(route: str, cfg: dict, handle: str, text: str, attachment: str | None) -> list[str]:
    if route == "openclaw":
        cli = _openclaw(cfg) or str(cfg.get("openclaw_cli") or "openclaw")
        argv = [cli, "message", "send", "--channel", "imessage", "--target", handle, "-m", text, "--json"]
        return argv + (["--media", attachment] if attachment else [])
    argv = ["/usr/bin/osascript"]
    for line in APPLESCRIPT.splitlines():
        argv += ["-e", line]
    return argv + [handle, text] + ([attachment] if attachment else [])


def _ok(route: str, res: dict) -> bool:
    if res.get("returncode") != 0:
        return False
    if route == "openclaw":
        out = str(res.get("stdout") or "")
        if re.search(r'"ok"\s*:\s*false', out) or ('"error"' in out and not re.search(r'"ok"\s*:\s*true', out)):
            return False
    return True


def imessage(text: str, attachment: str | os.PathLike | None = None, *, dry_run: bool = False,
             handle: str | None = None) -> dict:
    """Send `text` (and a file, such as the phone cut) to the owner by iMessage. Returns what was (or would be) sent.

    Raises MissingPrerequisite (exit 3) when no handle is configured, and SlateError when every route failed.
    """
    cfg = settings()
    handle = (handle or str(cfg.get("imessage_handle") or "")).strip()
    text = str(text or "").strip()
    if not text and not attachment:
        raise UsageError("There's nothing to send: no text and no file.")
    att = str(Path(attachment).expanduser().resolve()) if attachment else None
    if att and not Path(att).is_file():
        raise UsageError(f"The file to send isn't there: {att}")
    if not handle:
        raise MissingPrerequisite("No iMessage handle is set for the owner, so Slate can't send this.",
                                  hint="Add imessage_handle to [notify] in slate.toml (or set SLATE_NOTIFY_IMESSAGE).")
    routes = _imessage_routes(cfg)
    base = {"to": mask(handle), "text": text, "attachment": att}
    why = "dry run" if dry_run else blocked()
    if why:
        argv = _imessage_argv(routes[0], cfg, handle, text, att)
        return _record("imessage", routes[0], **base, sent=False, dry_run=dry_run, why=f"not sent: {why}",
                       would_run=_display(argv, handle))
    errors = []
    timeout = float(cfg.get("timeout_s") or 30)
    for route in routes:
        argv = _imessage_argv(route, cfg, handle, text, att)
        try:
            res = transport(argv, timeout=timeout)
        except SlateError as exc:
            # Keep ordinary transport and missing-tool fallback. Refusal, usage and retry controls must reach the
            # caller unchanged rather than becoming a base send error that the two-channel helper can suppress.
            if type(exc) is not SlateError and not isinstance(exc, MissingPrerequisite):
                raise
            errors.append(f"{route}: {exc.message}")
            continue
        if _ok(route, res):
            return _record("imessage", route, **base, sent=True, dry_run=False, why="sent")
        errors.append(f"{route}: {(res.get('stderr') or res.get('stdout') or 'failed').strip()[:200]}")
        log.warn(f"iMessage through {route} didn't go through; " + ("trying the next route" if route != routes[-1]
                                                                    else "no other route left"))
    raise SlateError("The iMessage didn't go through: " + "; ".join(errors),
                     hint="Check Messages is signed in on this Mac, and [notify] in slate.toml.",
                     details={"routes": routes})


# ---------------------------------------------------------------------------------------------------------------
# Push


def _push_argv(route: str, cfg: dict, title: str, body: str) -> list[str]:
    if route == "command":
        cmd = cfg.get("push_command") or []
        if isinstance(cmd, str):
            raise UsageError("[notify] push_command is an argv list, like [\"my-push\", \"{title}\", \"{body}\"]; "
                             "Slate never runs a shell string.")
        return [str(a).replace("{title}", title).replace("{body}", body) for a in cmd]
    cli = _openclaw(cfg) or str(cfg.get("openclaw_cli") or "openclaw")
    params = json.dumps({"nodeId": str(cfg.get("push_node")), "title": title, "body": body}, ensure_ascii=False)
    return [cli, "gateway", "call", "push.test", "--json", "--timeout", "20000", "--params", params]


def _push_route(cfg: dict) -> str:
    route = str(cfg.get("push_route") or "auto").strip().lower()
    if route == "auto":
        if cfg.get("push_command"):
            return "command"
        if cfg.get("push_node"):
            return "openclaw"
        raise MissingPrerequisite("No push route is set up for the owner's phone.",
                                  hint="Set push_command, or push_node (OpenClaw's paired iPhone), in [notify] in "
                                       "slate.toml.")
    if route == "command" and not cfg.get("push_command"):
        raise MissingPrerequisite("[notify] push_route is 'command' but push_command is empty.")
    if route == "openclaw" and not cfg.get("push_node"):
        raise MissingPrerequisite("[notify] push_route is 'openclaw' but push_node (the paired iPhone) is empty.",
                                  hint="Set push_node, or SLATE_NOTIFY_PUSH_NODE.")
    if route not in ("command", "openclaw"):
        raise UsageError(f"[notify] push_route {route!r} isn't one of auto, command, openclaw.")
    return route


def push(title: str, body: str, *, dry_run: bool = False) -> dict:
    """A push notification to the owner's phone. Returns what was (or would be) sent."""
    cfg = settings()
    prefix = str(cfg.get("title_prefix") or "").strip()
    title = str(title or "").strip() or prefix or "Slate"
    if prefix and not title.lower().startswith(prefix.lower()):
        title = f"{prefix}: {title}"
    title, body = title[:MAX_TITLE], str(body or "").strip()[:MAX_BODY]
    route = _push_route(cfg)
    argv = _push_argv(route, cfg, title, body)
    base = {"title": title, "body": body}
    why = "dry run" if dry_run else blocked()
    if why:
        return _record("push", route, **base, sent=False, dry_run=dry_run, why=f"not sent: {why}", would_run=argv)
    res = transport(argv, timeout=float(cfg.get("timeout_s") or 30))
    if not _ok(route, res):
        raise SlateError("The push notification didn't go through: "
                         + (res.get("stderr") or res.get("stdout") or "failed").strip()[:300],
                         hint="Check [notify] push_command or push_node in slate.toml.")
    return _record("push", route, **base, sent=True, dry_run=False, why="sent")


def both(text: str, *, title: str, attachment: str | os.PathLike | None = None, dry_run: bool = False) -> list[dict]:
    """Try iMessage and push independently and report each ordinary delivery failure.

    Missing prerequisites and the transport's base SlateError become channel results. Usage, refusal and retry
    controls retain their exceptions; interrupts and cancellation are not caught.
    """
    out = []
    for channel, send in (("imessage", lambda: imessage(text, attachment, dry_run=dry_run)),
                          ("push", lambda: push(title, text, dry_run=dry_run))):
        try:
            out.append(send())
        except MissingPrerequisite as exc:
            out.append({"channel": channel, "sent": False, "why": exc.message, "hint": exc.hint, "missing": True})
        except SlateError as exc:
            # The current transport reports start, timeout and send failures with the base SlateError. Keep other
            # control exceptions intact instead of turning a refusal or a requested retry into a completed send.
            if type(exc) is not SlateError:
                raise
            log.warn(f"{channel}: {exc.message}")
            out.append({"channel": channel, "sent": False, "why": exc.message, **exc.to_dict()})
    return out
