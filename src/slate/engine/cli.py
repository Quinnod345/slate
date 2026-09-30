"""Commands for the Node engine: `slate engine install|status|path|skills`, `slate emit-events`, `slate check`."""

from __future__ import annotations

import argparse
from pathlib import Path

from slate import cli as slate_cli
from slate import config, gates, jsonio, log, paths
from slate.engine import bridge, install, skills
from slate.errors import Refused, UsageError

EMIT_TIMEOUT = 900.0
CHECK_TIMEOUT = 1800.0


# --- slate engine … -------------------------------------------------------------------------------------------------

def _install(args: argparse.Namespace) -> int:
    version = args.version or config.engine_version()
    if slate_cli.flag(args, "dry_run"):
        plan = install.plan(version)
        log.result({"dry_run": True, **plan},
                   f"Would copy the engine ({', '.join(plan['files'] + plan['folders'])}) to {plan['destination']}"
                   + (" and run npm ci there." if args.npm_ci else "."))
        return 0
    dest = install.ensure(version, npm_ci=args.npm_ci)
    complete = install.is_complete(dest)
    log.result({"engine_dir": str(dest), "version": version, "complete": complete, "marker": install.marker(dest)},
               f"Engine {version} is at {dest}" + ("." if complete else
                                                  ": its packages aren't installed yet; run `slate engine install --npm-ci`."))
    return 0


def _bundles() -> dict:
    root = paths.cache_dir() / "bundles"
    count, size = 0, 0
    if root.is_dir():
        for folder in root.iterdir():
            if folder.name.startswith("."):
                continue
            count += 1
            size += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and not f.is_symlink())
    return {"folder": str(root), "count": count, "megabytes": round(size / 1e6, 1)}


def _status(args: argparse.Namespace) -> int:
    pinned = config.engine_version()
    out: dict = {"pinned_remotion": pinned, "installed_versions": install.installed_versions(), "bundles": _bundles()}
    try:
        engine = bridge.engine_dir()
        version = bridge.call("version", {}, timeout=60)
        out.update({
            "engine_dir": str(engine),
            "source": bridge.engine_source(),
            "engine_version": version.get("engine_version"),
            "remotion": version.get("installed", {}).get("remotion"),
            "pins_ok": version.get("pins_ok"),
            "matches_pin": version.get("installed", {}).get("remotion") == pinned,
            "browser": version.get("browser"),
            "kit": version.get("kit"),
            "web": version.get("web"),
            "node": version.get("node"),
        })
        out["skills"] = skills.plan(pinned)["current"]
        problems = []
        if not out["matches_pin"]:
            problems.append(f"the engine has Remotion {out['remotion']}, but Slate is pinned to {pinned}")
        if not (out["browser"] or {}).get("matches"):
            problems.append("chrome-headless-shell 149 isn't downloaded (node cli.mjs browser-ensure)")
        out["problems"] = problems
    except Exception as exc:  # noqa: BLE001 - status reports what's wrong rather than failing
        out["error"] = getattr(exc, "message", str(exc))
        out["problems"] = [out["error"]]
    lines = [
        f"engine    {out.get('engine_dir', 'not found')} ({out.get('source', '?')})",
        f"remotion  {out.get('remotion', '?')} (pinned {pinned})",
        f"browser   {(out.get('browser') or {}).get('version') or 'not downloaded'}",
        f"kit       {'present' if (out.get('kit') or {}).get('present') else 'missing'}"
        f" · web renderer {'present' if (out.get('web') or {}).get('present') else 'missing'}",
        f"bundles   {out['bundles']['count']} on disk ({out['bundles']['megabytes']} MB)",
    ]
    lines += [f"problem   {p}" for p in out["problems"]]
    log.result(out, "\n".join(lines))
    return 0 if not out["problems"] else 1


def _path(args: argparse.Namespace) -> int:
    folder = bridge.engine_dir()
    log.result({"engine_dir": str(folder), "source": bridge.engine_source()}, str(folder))
    return 0


def _skills(args: argparse.Namespace) -> int:
    plan = skills.plan()
    if slate_cli.flag(args, "dry_run"):
        human = [f"Would fetch into {plan['destination']}:"]
        for r in plan["repos"]:
            human.append(f"  {r['repo']} @ {r['commit'][:12]} ({r['licence']}{', optional' if r['optional'] else ''})")
        human.append("and apply Slate's overrides:")
        human += [f"  - {o}" for o in plan["overrides"]]
        log.result({"dry_run": True, **plan}, "\n".join(human))
        return 0
    if paths.engine_home(config.engine_version()).resolve() == paths.ENGINE_SRC.resolve():
        raise Refused("Skills go into the installed engine, never into the repo.", hint="Run `slate engine install` first.")
    lock = skills.fetch()
    counts = ", ".join(f"{r['repo']} {r['count']}/{r['expected']}" for r in lock["repos"].values())
    log.result(lock, f"Skills fetched into {skills.destination()} ({counts}); overrides applied to {len(lock['changes'])} files.")
    return 0


# --- slate emit-events ----------------------------------------------------------------------------------------------

def _record_lock(film_dir: Path, rel: str, sources: list[str]) -> None:
    """Record what made the file in film.lock.json, when the state module is there (it turns the file stale)."""
    try:
        from slate.state import lock  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - the state module is optional here
        return
    try:
        lock.record(film_dir, rel, producer="slate emit-events", inputs=sorted(set(sources + ["timing/shots.json"])))
    except Exception as exc:  # noqa: BLE001 - a lock-file problem must not undo the events just written
        log.warn(f"couldn't record {rel} in film.lock.json: {exc}")


def _emit(args: argparse.Namespace) -> int:
    film_dir = slate_cli.film(args)
    res = bridge.call("emit-events", {"film": str(film_dir), "act": args.act}, timeout=EMIT_TIMEOUT)
    lines = []
    for a in res["acts"]:
        _record_lock(film_dir, a["file"], a.get("sources", []))
        if a.get("motion", {}).get("written"):
            _record_lock(film_dir, a["motion"]["file"], a.get("sources", []))
        what = f"silent ({a['reason']})" if a["silent"] else f"{a['events']} beats"
        motion = a.get("motion", {})
        trace = f"; motion trace {motion['entries']} moves" if motion.get("written") else f"; no motion trace: {motion.get('reason')}"
        lines.append(f"act {a['act']}: {what} → {a['file']}{trace}")
    lines += [f"act {s['act']}: skipped ({s['reason']})" for s in res.get("skipped", [])]
    log.result({k: v for k, v in res.items() if k not in ("ok", "command")}, "\n".join(lines) or "No acts to emit.")
    return 0


# --- slate check ----------------------------------------------------------------------------------------------------

def banned_words(film_dir: Path) -> list[str] | None:
    """The brand pack's banned words, or None when the pack can't be read (the gate is then UNCHECKED)."""
    name = (jsonio.read(film_dir / "film.json", default={}) or {}).get("brand")
    if not name:
        return None
    try:
        from slate.brand import pack  # noqa: PLC0415

        return [str(w) for w in pack.banned_words(pack.load(name))]
    except Exception:  # noqa: BLE001 - fall back to reading the pack's documented shape (ARCHITECTURE §7)
        folder = bridge.brand_path(film_dir)
        if not folder:
            return None
        data = jsonio.read(folder / "brand.json", default=None)
        if not isinstance(data, dict):
            return None
        words = (data.get("truth") or {}).get("banned_words")
        return [str(w) for w in words] if isinstance(words, list) else []


def _check(args: argparse.Namespace) -> int:
    film_dir = slate_cli.film(args)
    shots = jsonio.read(film_dir / "timing" / "shots.json", default=None)
    if args.act == "all":
        if not shots:
            raise UsageError("This film has no timing/shots.json yet.", hint="Run `slate timing` first.")
        acts = sorted({int(s["act"]) for s in shots.get("shots", [])})
        acts = [a for a in acts if (film_dir / "scenes" / f"act{a}").is_dir()]
    else:
        try:
            acts = [int(args.act)]
        except ValueError as exc:
            raise UsageError(f"`slate check` takes an act number or 'all', not {args.act!r}.") from exc
    words = banned_words(film_dir)
    results: list[gates.GateResult] = []
    for act in acts:
        res = bridge.call("check", {"film": str(film_dir), "act": act, "banned_words": words,
                                    "contracts": not args.no_contracts, "hold": args.hold}, timeout=CHECK_TIMEOUT)
        results += [gates.GateResult.from_dict(g) for g in res["gates"]]
    if results:
        gates.write_report(film_dir, results)
    summary = gates.summarize(results)
    log.result({"film": str(film_dir), "acts": acts, "gates": [r.to_dict() for r in results], "summary": summary},
               gates.format_table(results))
    return gates.exit_code(results)


def register(sub) -> None:
    grp = slate_cli.add_group(sub, "engine", help="the Node engine: install it, see its status, find it, fetch its vendor skills")
    p = slate_cli.add_command(grp, "install", _install,
                              help="copy the engine into Slate's data folder for the pinned Remotion (npm ci with --npm-ci)")
    p.add_argument("--npm-ci", dest="npm_ci", action="store_true",
                   help="install the lockfile's exact packages there (npm ci; never npm install, never global)")
    p.add_argument("--version", default=None, help="the Remotion version to install for (default: the pinned one)")
    slate_cli.add_command(grp, "status", _status, help="which engine runs, its versions, its browser, kit and bundles")
    slate_cli.add_command(grp, "path", _path, help="print the engine folder Slate runs")
    slate_cli.add_command(grp, "skills", _skills,
                          help="fetch Remotion's and ElevenLabs' skills (pinned) into the engine, with Slate's overrides")
    p = slate_cli.add_command(sub, "emit-events", _emit,
                              help="write each act's visual events (or its silence) and motion trace from its scenes")
    p.add_argument("--act", type=int, default=None, help="one act (default: every act that's built)")
    p = slate_cli.add_command(sub, "check", _check,
                              help="an act's hand-in check: types, word misses, banned words, events, boundary contracts")
    p.add_argument("act", help="the act number, or 'all' for every built act")
    p.add_argument("--no-contracts", dest="no_contracts", action="store_true",
                   help="skip the boundary renders (the contracts gate reports UNCHECKED)")
    p.add_argument("--hold", type=int, default=6, help="frames a rest state must hold at a boundary (default 6)")
