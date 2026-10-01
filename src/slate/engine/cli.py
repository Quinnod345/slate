"""Commands for the Node engine: `slate engine install|status|path|skills|scaffold`, `slate emit-events`, `slate check`."""

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


def look_engine(film_dir: Path) -> str | None:
    """The engine the film's brand builds scenes in (brand.json look.engine: remotion, web or ae), or None."""
    folder = bridge.brand_path(film_dir)
    data = jsonio.read(folder / "brand.json", default=None) if folder else None
    look = data.get("look") if isinstance(data, dict) else None
    engine = look.get("engine") if isinstance(look, dict) else None
    return engine if engine in ("remotion", "web", "ae") else None


def _scaffold(args: argparse.Namespace) -> int:
    film_dir = slate_cli.film(args)
    engine = args.engine or look_engine(film_dir) or "remotion"
    if engine == "ae":
        raise UsageError("An After Effects act is a scene written as data (scenes/ae/actN.json), not a template.",
                         hint="`slate ae acts --help` shows its shape; `slate ae acts export N` writes its trace and "
                              "events.")
    if engine != "web":
        raise UsageError(f"Act {args.act} would be a Remotion act, which starts as the kit's placeholder by itself "
                         "(it renders its shot ids, on-screen text and VO until scenes/actN/index.tsx exists).",
                         hint="Write scenes/actN/index.tsx with the kit (engine/remotion/kit/README.md), or pass "
                              "--engine web for a web scene.")
    res = bridge.call("web-render", {"film": str(film_dir), "act": args.act, "scaffold": True,
                                     "force": True if args.force else None}, timeout=120)
    folder = Path(res.get("dir", film_dir / "scenes" / f"act{args.act}" / "web"))
    try:
        where = folder.resolve().relative_to(film_dir.resolve()).as_posix()
    except ValueError:
        where = str(folder)
    log.result({k: v for k, v in res.items() if k not in ("ok", "command")},
               f"act {res.get('act', args.act)}: a web scene started from the template in {where} "
               f"({len(res.get('files') or [])} files)\n"
               f"next: make it this act's picture (scene.ts), then `slate emit-events --act {args.act}` and "
               f"`slate check {args.act}`")
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
    dry = bool(slate_cli.flag(args, "dry_run", False))
    res = bridge.call("emit-events", {"film": str(film_dir), "act": args.act, "dry_run": dry or None},
                      timeout=EMIT_TIMEOUT)
    lines = []
    problems = 0
    for a in res["acts"]:
        motion = a.get("motion", {})
        if not dry:
            sources = a.get("sources", []) + list((a.get("captures") or {}).get("files", []))
            _record_lock(film_dir, a["file"], sources)
            if motion.get("written"):
                _record_lock(film_dir, motion["file"], sources)
        what = f"silent ({a['reason']})" if a["silent"] else f"{a['events']} beats"
        if a.get("capture_beats"):
            what += f", {a['capture_beats']} from captures"
        if motion.get("written") or motion.get("would_write"):
            trace = f"; motion trace {motion['entries']} moves"
        else:
            trace = f"; no motion trace: {motion.get('reason')}"
        if dry:
            found = a.get("problems") or []
            problems += len(found)
            verdict = f"{len(found)} problem{'s' if len(found) != 1 else ''}" if found else "would pass"
            lines.append(f"act {a['act']}: {what} → {a['file']}{trace} ({verdict}; dry run, nothing written)")
            lines += [f"  - {p}" for p in found]
        else:
            lines.append(f"act {a['act']}: {what} → {a['file']}{trace}")
        lines += [f"  note: {n}" for n in a.get("notes", [])]
    for s in res.get("skipped", []):
        waiting = f", {s['capture_beats']} capture beats waiting for it" if s.get("capture_beats") else ""
        lines.append(f"act {s['act']}: skipped ({s['reason']}{waiting})")
    log.result({k: v for k, v in res.items() if k not in ("ok", "command")}, "\n".join(lines) or "No acts to emit.")
    # a dry run is the check without the write: it exits 1 when the real run would be refused
    return 1 if problems else 0


# --- slate check ----------------------------------------------------------------------------------------------------

_UNREAD = object()


def _words_from_pack(name: str) -> object:
    """slate.brand.pack's answer (a list, or None when the pack states no list), or _UNREAD when it can't be asked."""
    try:
        from slate.brand import pack  # noqa: PLC0415 (the brand module is optional here)

        words = pack.banned_words(pack.load(name))
    except Exception:  # noqa: BLE001 - no brand module, or a pack it can't load: read the file instead
        return _UNREAD
    return None if words is None else [str(w) for w in words]


def banned_words(film_dir: Path) -> list[str] | None:
    """The brand pack's banned words. None means unknown, and the gate reads unknown as UNCHECKED, never as "none":
    the film names no brand, the pack can't be read, or the pack states no list at all (no truth.banned_words). An
    empty list is the pack saying there are none, and the gate passes on it."""
    name = (jsonio.read(film_dir / "film.json", default={}) or {}).get("brand")
    if not name:
        return None
    words = _words_from_pack(str(name))
    if words is not _UNREAD:
        return words  # type: ignore[return-value]
    # the pack's documented shape (ARCHITECTURE §7), read directly
    folder = bridge.brand_path(film_dir)
    if not folder:
        return None
    data = jsonio.read(folder / "brand.json", default=None)
    if not isinstance(data, dict):
        return None
    listed = (data.get("truth") or {}).get("banned_words") if isinstance(data.get("truth"), dict) else None
    if not isinstance(listed, list):
        return None
    out: list[str] = []
    for w in listed:
        raw = (w.get("word") or w.get("text")) if isinstance(w, dict) else w
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _built(film_dir: Path, act: int) -> bool:
    """A Remotion act the hand-in check reads: scenes/actN/index.* (a web scene has scenes/actN/web/index.html)."""
    return any((film_dir / "scenes" / f"act{act}" / f"index{ext}").is_file() for ext in (".tsx", ".ts", ".jsx", ".js"))


def _web(film_dir: Path, act: int) -> bool:
    return not _built(film_dir, act) and (film_dir / "scenes" / f"act{act}" / "web" / "index.html").is_file()


def _check(args: argparse.Namespace) -> int:
    film_dir = slate_cli.film(args)
    shots = jsonio.read(bridge.film_file(film_dir, "timing/shots.json"), default=None)
    skipped: list[dict] = []
    if args.act == "all":
        if not shots:
            raise UsageError("This film has no timing/shots.json yet.", hint="Run `slate timing` first.")
        every = sorted({int(s["act"]) for s in shots.get("shots", [])})
        # Remotion acts and web acts alike (a web act is checked through the open web renderer)
        acts = [a for a in every if _built(film_dir, a) or _web(film_dir, a)]
        skipped = [{"act": a, "reason": "not built yet"} for a in every if a not in acts]
        if not acts:
            raise UsageError("None of this film's acts is built yet (no scenes/actN/index.tsx, and no web scene in "
                             "scenes/actN/web/): there's nothing to hand in.",
                             hint="Build an act first; `slate status` shows where the film stands.")
    else:
        try:
            acts = [int(args.act)]
        except ValueError as exc:
            raise UsageError(f"`slate check` takes an act number or 'all', not {args.act!r}.") from exc
    words = banned_words(film_dir)
    # one engine run for every act: at most one bundle, shared by every act's boundary contracts
    res = bridge.call("check", {"film": str(film_dir), "acts": acts, "banned_words": words,
                                "contracts": not args.no_contracts, "hold": args.hold},
                      timeout=CHECK_TIMEOUT * len(acts))
    results = [gates.GateResult.from_dict(g) for g in res["gates"]]
    if results:
        gates.write_report(film_dir, results)
    summary = gates.summarize(results)
    human = gates.format_table(results)
    human += "".join(f"\nact {s['act']}: not checked ({s['reason']})" for s in skipped)
    log.result({"film": str(film_dir), "acts": acts, "skipped": skipped, "gates": [r.to_dict() for r in results],
                "summary": summary}, human)
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
    p = slate_cli.add_command(grp, "scaffold", _scaffold,
                              help="start an act's scene from the engine's template (web acts: scenes/actN/web/)",
                              description=(
                                  "Start an act's scene from the template of the engine the brand's look picks "
                                  "(brand.json look.engine), or the one you name. A web act gets the open web "
                                  "renderer's neutral template in scenes/actN/web/ (index.html and scene.ts), moved by "
                                  "the brand's motion law and drawn in its tokens. Nothing in the act is replaced "
                                  "unless you pass --force."))
    p.add_argument("--act", type=int, required=True, help="the act to start")
    p.add_argument("--engine", choices=("web", "remotion", "ae"), default=None,
                   help="the engine to start it for (default: the brand's look.engine, else remotion)")
    p.add_argument("--force", action="store_true", help="replace a scene that's already there")
    p = slate_cli.add_command(sub, "emit-events", _emit,
                              help="write each act's visual events (or its silence) and motion trace from its scenes",
                              description=(
                                  "Write timing/visual_events/actN.json (each act's beats, from the act's own modules, "
                                  "the kit's defineEvent() registrations and placed captures' taps) and "
                                  "timing/motion/actN.json (the kit's motion trace). Nothing is written while a beat "
                                  "breaks a rule or a word lookup fell back to an estimate. With --dry-run it checks "
                                  "everything, writes nothing, and exits 1 if the real run would be refused."))
    p.add_argument("--act", type=int, default=None, help="one act (default: every act that's built)")
    p = slate_cli.add_command(sub, "check", _check,
                              help="an act's hand-in check: types, word misses, banned words, events, boundary contracts")
    p.add_argument("act", help="the act number, or 'all' for every built act")
    p.add_argument("--no-contracts", dest="no_contracts", action="store_true",
                   help="skip the boundary renders (the contracts gate reports UNCHECKED)")
    p.add_argument("--hold", type=int, default=6, help="frames a rest state must hold at a boundary (default 6)")
