"""Running the checks and assembling the report.

`checks(context)` returns the rows; `report(context)` wraps them as `slate doctor --json` prints them:

    {"status": "green|yellow|red", "context": "claude-code", "detected": "claude-code", "host": {...},
     "checked_at": "...", "replay": false, "counts": {...}, "checks": [rows]}

The format is the same from every context (a Terminal, Claude Code's shell, a Codex research slice, the OpenClaw
gateway), which is what lets the phase-1 spike compare them. A check that crashes becomes a red row naming the
crash, so one broken probe never hides the others. Checks that wait on a process run a few at a time.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import re
from pathlib import Path
from typing import Callable, Iterable

from slate import jsonio, paths
from slate.doctor import rows as R
from slate.doctor.common import Ctx, default_browser, host_app
from slate.doctor.env import Env
from slate.errors import SlateError, UsageError

Check = Callable[[Env, Ctx], list]
CONTEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WORKERS = 6


def make_env() -> Env:
    """The Env doctor uses (tests replace this with a fake one)."""
    return Env()


def registry() -> list[tuple[str, str, Check]]:
    """Every check, in report order: (group, name, function)."""
    from slate.doctor import aftereffects, agents, fonts, keys, system, tools  # noqa: PLC0415

    return [
        ("host", "macos", system.check_macos),
        ("tools", "ffmpeg", tools.check_ffmpeg),
        ("tools", "rubberband", tools.check_rubberband),
        ("tools", "uv", tools.check_uv),
        ("tools", "node", tools.check_node),
        ("tools", "git", tools.check_git),
        ("tools", "whisper", tools.check_whisper),
        ("tools", "macos-tools", tools.check_macos_tools),
        ("tools", "simctl", tools.check_simctl),
        ("tools", "optional", tools.check_optional_tools),
        ("tools", "higgsfield", tools.check_higgsfield_cli),
        ("tools", "aside", tools.check_aside),
        ("engine", "engine", tools.check_engine),
        ("keys", "keys", keys.check_keys),
        ("aftereffects", "aftereffects", aftereffects.check_ae),
        ("sound", "instruments", system.check_instruments),
        ("agents", "skills", agents.check_skills),
        ("agents", "mcp", agents.check_mcp),
        ("agents", "cli-path", agents.check_cli_path),
        ("agents", "codex-dia", agents.check_codex_dia),
        ("system", "disk", system.check_disk),
        ("system", "browser", system.check_browser),
        ("system", "jobs-agent", system.check_jobs_agent),
        ("system", "ocr", system.check_ocr),
        ("system", "modules", system.check_modules),
        ("system", "licence", system.check_licence),
        ("fonts", "fonts", fonts.check_fonts),
    ]


def make_ctx(env: Env, context: str | None = None, *, brand: str | None = None) -> Ctx:
    agent = env.agent_context()
    label = context or agent or "terminal"
    try:
        replay = env.replay()
    except Exception:  # noqa: BLE001
        replay = False
    try:
        browser = default_browser(env)
    except Exception:  # noqa: BLE001
        browser = {}
    return Ctx(context=label, agent=agent, brand=brand, replay=replay, host_app=host_app(env, agent), browser=browser)


def _crashed(group: str, name: str, exc: BaseException) -> dict:
    from slate.secrets import redact  # noqa: PLC0415

    message = redact(getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}")
    return R.red(f"doctor.{name}", f"Doctor couldn't run its {name} check: {message[:300]}",
                 "This is a bug in the check, not necessarily a problem on this Mac. Run `slate doctor --json` and "
                 "pass the row on.", group=group)


def _run_one(env: Env, ctx: Ctx, group: str, name: str, fn: Check) -> list[dict]:
    try:
        out = fn(env, ctx)
    except UsageError:
        raise  # a bad --brand is the caller's mistake: exit 2, not a red row
    except Exception as exc:  # noqa: BLE001 - one crashed check must never hide the rest
        return [_crashed(group, name, exc)]
    if isinstance(out, dict):  # one row returned bare: keep it, never drop it
        out = [out]
    return [r for r in (out or []) if isinstance(r, dict)]


def checks(context: str | None = None, *, brand: str | None = None, only: Iterable[str] | None = None,
           env: Env | None = None, parallel: bool = True) -> list[dict]:
    """Every doctor row for this Mac: [{id, status: green|yellow|red, summary, fix, …}] (docs/ARCHITECTURE.md §12).

    `context` labels the run; `brand` adds that brand's fonts; `only` limits it to some groups. Off macOS there's a
    single red row, because every other check assumes a Mac.
    """
    env = env or make_env()
    try:
        if env.system() != "Darwin":
            from slate.doctor import system  # noqa: PLC0415

            return [system.not_macos_row(env)]
        ctx = make_ctx(env, context, brand=brand)
        wanted = set(only or R.GROUPS)
        unknown = wanted - set(R.GROUPS)
        if unknown:
            raise UsageError(f"Unknown doctor group(s): {', '.join(sorted(unknown))}. Known: {', '.join(R.GROUPS)}.")
        chosen = [(g, n, f) for g, n, f in registry() if g in wanted]
        # The modules check imports every Slate module; doing it first keeps later lazy imports off the thread pool.
        first = [c for c in chosen if c[1] == "modules"]
        rest = [c for c in chosen if c[1] != "modules"]
        results: dict[int, list[dict]] = {}
        for g, n, f in first:
            results[chosen.index((g, n, f))] = _run_one(env, ctx, g, n, f)
        if parallel and len(rest) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="doctor") as pool:
                futures = {pool.submit(_run_one, env, ctx, g, n, f): chosen.index((g, n, f)) for g, n, f in rest}
                for fut in concurrent.futures.as_completed(futures):
                    results[futures[fut]] = fut.result()
        else:
            for g, n, f in rest:
                results[chosen.index((g, n, f))] = _run_one(env, ctx, g, n, f)
        return [r for i in sorted(results) for r in results[i]]
    finally:
        env.cleanup()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def report(context: str | None = None, *, brand: str | None = None, only: Iterable[str] | None = None,
           env: Env | None = None, parallel: bool = True) -> dict:
    """The whole report, as `slate doctor --json` prints it and `--context` saves it."""
    env = env or make_env()
    rows = checks(context, brand=brand, only=only, env=env, parallel=parallel)
    agent = env.agent_context()
    try:
        replay = env.replay()
    except Exception:  # noqa: BLE001
        replay = False
    host = {"system": env.system()}
    if env.system() == "Darwin":
        host.update({"macos": env.mac_version(), "arch": env.arch()})
    out = {
        "status": R.overall(rows),
        "context": context or agent or "terminal",
        "detected": agent or "terminal",
        "host": host,
        "checked_at": now_iso(),
        "replay": replay,
        "counts": R.counts(rows),
        "checks": rows,
    }
    if only:
        out["only"] = sorted(set(only))
    if brand:
        out["brand"] = brand
    if host["system"] != "Darwin":
        out["refused"] = rows[0]["summary"] if rows else "Slate runs on macOS only for now."
    return out


def check_context_name(name: str) -> str:
    if not CONTEXT_RE.match(name or ""):
        raise UsageError(f"{name!r} can't be a context name: use letters, digits, dots, dashes or underscores "
                         "(for example claude-code, pursuit-slice, openclaw-gateway).")
    return name


def save(rep: dict, name: str) -> Path:
    """Keep a labelled run at DATA/doctor/<name>.json (the spike compares these across contexts)."""
    check_context_name(name)
    path = paths.data() / "doctor" / f"{name}.json"
    jsonio.write_atomic(path, rep)
    return path


def load_saved(name: str) -> dict:
    check_context_name(name)
    path = paths.data() / "doctor" / f"{name}.json"
    data = jsonio.read(path, default=None)
    if data is None:
        raise SlateError(f"No saved doctor run called {name!r} ({path}).")
    return data
