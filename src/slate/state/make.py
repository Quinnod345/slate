"""`slate make <target>`: rebuild only what's stale, in order, by calling each stage's own command.

The targets form a small graph with declared inputs and outputs:

    storyboard → voice → timing → events ─┬→ render ─┬→ master → qa → deliver
                                           └→ mix ────┘

For each node on the way to the target, make asks the film's records whether its outputs are fresh (film.lock.json
hashes, the renders registry, qa/gates.json) and runs the owning command in-process (`slate.cli.run_cli`) only when
they aren't. A command that isn't built in this Slate yet is reported (exit 3), never faked, and nothing downstream of
it runs. Locked outputs (approved work) are never remade here. Paid stages (voicing) run only with --spend, or in
replay mode, because make is a convenience and spending must stay a decision.

A node can also track inputs its command doesn't record for itself (`Node.tracked`), including named ones
(`brand:{brand}#sound`, the film's brand's sound palette). The mix tracks the palette and the effect cues: when the
palette changes, every effect has to be re-spotted in it and the mix made again (the films' rule). `slate mix` records
both in its own lock entry now; for a mix made before it did, make adds them once it has seen the output fresh against
them, so from then on a change shows by hash, and before that a palette or cue sheet newer than the output counts as a
change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from slate import gates, jsonio, log
from slate.errors import UsageError
from slate.replay import fake_vendors
from slate.state import _util, film, lock, status


@dataclass(frozen=True)
class Node:
    name: str
    cmd: tuple[str, ...]
    after: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    dirs: tuple[str, ...] = ()
    producers: tuple[str, ...] = ()
    owner: str = ""
    paid: bool = False
    # Inputs the node's own command doesn't record in its lock entries, which make tracks for it: hashed onto the
    # output's entry once it's seen fresh (adopt_inputs), and judged by modification time until then.
    tracked: tuple[str, ...] = ()


NODES: dict[str, Node] = {n.name: n for n in (
    Node("storyboard", ("script", "build"), (), ("script/script.json",),
         ("script/storyboard.json", "script/SCRIPT.md"), (), ("slate script",), "story"),
    Node("voice", ("voice", "tts"), ("storyboard",), ("script/storyboard.json",), (), ("voice",),
         ("slate voice tts",), "voice", paid=True),
    Node("timing", ("timing",), ("storyboard", "voice"), ("script/storyboard.json",),
         ("timing/shots.json", "timing/vo_cues.json", "timing/vo_words.json", "timing/timeline.json"), (),
         ("slate timing",), "timing"),
    Node("events", ("emit-events",), ("timing",), ("timing/shots.json", "timing/timeline.json"), (),
         ("timing/visual_events",), ("slate emit-events",), "engine"),
    Node("render", ("render",), ("timing", "events"), ("timing/shots.json", "timing/timeline.json"),
         ("renders/registry.json",), (), ("slate render",), "render"),
    # The palette and the effect cues are the mix's inputs too: a palette change re-spots the effects and remakes it.
    Node("mix", ("mix",), ("timing", "events"), ("timing/vo_cues.json",), (), (), ("slate mix",), "sound",
         tracked=("sound/sound_cues.json", "brand:{brand}#sound")),
    Node("master", ("master",), ("render", "mix"), ("renders/registry.json",), (), ("masters",),
         ("slate master",), "finish"),
    Node("qa", ("qa",), ("master",), (), ("qa/gates.json",), (), ("slate qa",), "qa"),
    Node("deliver", ("deliver",), ("qa",), ("qa/gates.json",), (), ("deliver",), ("slate deliver",), "finish"),
)}
ORDER = tuple(NODES)
ALIASES = {"all": "deliver", "script": "storyboard", "emit-events": "events", "sound": "mix", "renders": "render",
           "masters": "master", "delivery": "deliver"}

Runner = Callable[[list[str]], tuple[int, str, str]]


def targets() -> list[str]:
    return list(ORDER)


def resolve_target(name: str) -> str:
    key = ALIASES.get(name, name)
    if key not in NODES:
        raise UsageError(f"Unknown make target {name!r}.", hint="Targets: " + ", ".join(ORDER) + " (or all).")
    return key


def plan(target: str) -> list[str]:
    """The target and everything upstream of it, in build order."""
    want: set[str] = set()

    def visit(name: str) -> None:
        if name in want:
            return
        want.add(name)
        for up in NODES[name].after:
            visit(up)

    visit(resolve_target(target))
    return [n for n in ORDER if n in want]


# ---------------------------------------------------------------------------------------------------------------
# Is a node fresh?


def _outputs(film_dir: Path, node: Node, files: dict[str, dict]) -> list[str]:
    rels = set(node.outputs)
    for rel, e in files.items():
        if node.producers and any(str(e.get("producer", "")).startswith(p) for p in node.producers):
            rels.add(rel)
    return sorted(rels)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def declared_inputs(film_dir: Path, node: Node) -> list[str]:
    """The inputs make tracks for this node, for this film: `{brand}` becomes the film's brand (a named input with no
    brand is dropped)."""
    out = []
    brand = None
    for key in node.tracked:
        if "{brand}" in key:
            if brand is None:
                try:
                    brand = str(film.load(film_dir).get("brand") or "")
                except UsageError:
                    brand = ""
            if not brand:
                continue
            key = key.replace("{brand}", brand)
        out.append(key)
    return out


def _named(key: str) -> bool:
    return bool(lock.SCHEME_RE.match(key))


def _stamp(text: str | None) -> float | None:
    import datetime as _dt  # noqa: PLC0415

    try:
        return _dt.datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def _changed_after(film_dir: Path, key: str, made_at: str | None) -> bool:
    """Before make has a hash for a declared input, a file (or brand pack) newer than the output counts as a change."""
    made = _stamp(made_at)
    if made is None:
        return False
    if _named(key):
        scheme, _sep, rest = key.partition(":")
        if scheme != "brand":
            return False
        from slate.errors import SlateError  # noqa: PLC0415
        from slate.state import deps  # noqa: PLC0415

        try:
            path = Path(deps.brand_find(rest.partition("#")[0])) / "brand.json"
        except SlateError:
            return False
    else:
        path = lock.resolve(film_dir, key)
    m = _mtime(path)
    return m is not None and m > made + 1.0  # stamps are to the second


def adopt_inputs(film_dir: Path, node: Node) -> list[str]:
    """Record the node's declared inputs on its outputs' lock entries (hashed now), where they're missing. Called once
    the outputs are known fresh against them, so later changes show by hash. Returns the outputs it touched."""
    keys = declared_inputs(film_dir, node)
    if not keys:
        return []
    files = lock.load(film_dir)["files"]
    touched = []
    for rel in _outputs(film_dir, node, files):
        e = files.get(rel)
        if not e or e.get("locked") or e.get("ref"):
            continue
        missing = [k for k in keys if k not in (e.get("inputs") or {})]
        if missing and lock.add_inputs(film_dir, rel, missing):
            touched.append(rel)
    return touched


def node_state(film_dir: Path, node: Node, *, hasher: _util.Hasher | None = None) -> dict:
    """{node, state: fresh|stale|missing|locked, reasons, outputs} from the film's own records."""
    film_dir = Path(film_dir)
    files = lock.load(film_dir)["files"]
    own = hasher is None
    hasher = hasher or _util.Hasher(film_dir)
    memo: dict = {}
    reasons: list[str] = []
    try:
        outs = _outputs(film_dir, node, files)
        dir_files = [p for d in node.dirs for p in _util.walk_files(film_dir / d, limit=1)]
        if node.name == "qa":
            try:
                report = gates.load_report(film_dir)
            except UsageError:
                return {"node": node.name, "state": "stale", "reasons": ["qa/gates.json doesn't parse"],
                        "outputs": outs}
            if not report:
                return {"node": node.name, "state": "missing", "reasons": ["no gates have run"], "outputs": outs}
            stale_g = [g for g in report.values() if g.status == "STALE"]
            state = "stale" if stale_g else "fresh"
            return {"node": node.name, "state": state, "outputs": outs,
                    "reasons": [f"gate {g.gate} is STALE" for g in stale_g[:5]]}
        missing = [r for r in node.outputs if not lock.resolve(film_dir, r).exists()]
        if missing:
            return {"node": node.name, "state": "missing", "reasons": [f"{r} isn't there" for r in missing],
                    "outputs": outs}
        if not outs and not dir_files:
            return {"node": node.name, "state": "missing", "reasons": ["nothing made yet"], "outputs": outs}
        locked_n = 0
        declared = declared_inputs(film_dir, node)
        newest_input = max((m for r in (*node.inputs, *(k for k in declared if not _named(k)))
                            if (m := _mtime(lock.resolve(film_dir, r))) is not None), default=None)
        for rel in outs:
            if rel in files:
                x = lock.explain(film_dir, rel, hasher=hasher, _memo=memo)
                if x["state"] == "locked":
                    locked_n += 1
                elif x["state"] != "fresh":
                    reasons.append(f"{rel}: {x['reasons'][0] if x['reasons'] else x['state']}")
                else:
                    e = files[rel]
                    for key in declared:
                        if key not in (e.get("inputs") or {}) and _changed_after(film_dir, key, e.get("made_at")):
                            what = f"the brand pack's {key.partition('#')[2] or 'brand.json'}" if _named(key) else key
                            reasons.append(f"{rel}: {what} changed after it was made")
                            break
            else:
                # No record of what made it: fall back to modification times against the declared inputs.
                m = _mtime(lock.resolve(film_dir, rel))
                if m is not None and newest_input is not None and newest_input > m:
                    reasons.append(f"{rel} is older than its inputs (no lock record)")
        if node.name == "render":
            try:
                reg = jsonio.read(film_dir / lock.RENDER_REGISTRY, default={}) or {}
            except UsageError:
                reg = {}
                reasons.append("renders/registry.json doesn't parse")
            for e in (reg.get("entries") or []) if isinstance(reg, dict) else []:
                f = e.get("file")
                if not f:
                    continue
                p = Path(f) if Path(f).is_absolute() else film_dir / f
                if not p.exists():
                    reasons.append(f"{f} is registered but missing")
                elif e.get("sha256") and hasher.sha(p) != e["sha256"]:
                    reasons.append(f"{f} changed since it was registered")
        if reasons:
            state = "stale"
        elif outs and locked_n == len(outs):
            state = "locked"
        else:
            state = "fresh"
        return {"node": node.name, "state": state, "reasons": reasons, "outputs": outs}
    finally:
        if own:
            hasher.save()


# ---------------------------------------------------------------------------------------------------------------
# Running


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    from slate import cli  # noqa: PLC0415

    was_json = log.json_mode()
    try:
        return cli.run_cli(argv)
    finally:
        log.set_json(was_json)  # run_cli resets output modes; the outer command keeps its own


def run(film_dir: Path, target: str, *, force: bool = False, dry_run: bool = False, spend: bool = False,
        runner: Runner | None = None) -> dict:
    """Bring `target` up to date. Returns {target, film, plan, results, exit_code}; runs nothing that's fresh."""
    film_dir = Path(film_dir)
    target = resolve_target(target)
    order = plan(target)
    runner = runner or _default_runner
    results: list[dict] = []
    blocked: dict[str, str] = {}
    code = 0
    status.reset_command_cache()
    with _util.Hasher(film_dir) as hasher:
        for name in order:
            node = NODES[name]
            st = node_state(film_dir, node, hasher=hasher)
            forced = force and name == target
            needs_work = forced or st["state"] in ("stale", "missing")
            row = {"node": name, "command": "slate " + " ".join(node.cmd), "owner": node.owner, "before": st["state"],
                   "reasons": st["reasons"]}
            upstream = [u for u in node.after if u in blocked]
            if not needs_work:
                row["action"] = "skipped"
                row["why"] = "locked (approved work is never remade here)" if st["state"] == "locked" else "fresh"
                if st["state"] == "fresh" and not dry_run:
                    adopt_inputs(film_dir, node)  # fresh against its declared inputs: hash them from now on
                results.append(row)
                continue
            if upstream:
                row.update(action="blocked", why=f"waiting on {', '.join(upstream)} ({blocked[upstream[0]]})")
                blocked[name] = "upstream blocked"
                results.append(row)
                continue
            if forced and st["state"] == "locked":
                row.update(action="refused", why="its outputs are locked; remake approved work through the stage's own "
                                                 "command with --force --approval <id>")
                blocked[name] = "locked"
                code = 1
                results.append(row)
                continue
            if not status.command_available(node.cmd):
                row.update(action="not built", why=f"`{row['command']}` isn't built in this Slate yet ({node.owner})")
                blocked[name] = "not built yet"
                code = code or 3
                results.append(row)
                continue
            if node.paid and not spend and not fake_vendors():
                row.update(action="needs --spend", why=f"{name} spends vendor credits; run `{row['command']}` "
                                                        "yourself, or `slate make … --spend`")
                blocked[name] = "spend not allowed"
                code = code or 4
                results.append(row)
                continue
            if dry_run:
                row.update(action="would run", why="dry run")
                results.append(row)
                continue
            argv = [*node.cmd, "--film", str(film_dir), "--json"]
            rc, out, err = runner(argv)
            row["exit_code"] = rc
            if rc != 0:
                row.update(action="failed", why=_error_text(out, err, rc))
                blocked[name] = "failed"
                code = 1
                results.append(row)
                continue
            adopt_inputs(film_dir, node)  # it was just made from its declared inputs as they are now
            after = node_state(film_dir, node, hasher=_util.Hasher(film_dir))
            row["after"] = after["state"]
            if after["state"] in ("stale", "missing"):
                row.update(action="unverified", why="it ran, but its outputs still aren't recorded as fresh: "
                                                    + "; ".join(after["reasons"][:2]))
                blocked[name] = "outputs not recorded"
                code = 1
            else:
                row.update(action="ran", why="rebuilt")
            results.append(row)
    return {"target": target, "film": film.film_id(film_dir), "plan": order, "results": results, "exit_code": code,
            "dry_run": dry_run}


def _error_text(out: str, err: str, rc: int) -> str:
    """Why a stage command failed, in one line: its error message, or the gates it failed (gates.summarize's shape)."""
    for line in reversed((out or "").strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("message"):
            return f"exit {rc}: {obj['message']}"
        summary = obj.get("summary") if isinstance(obj.get("summary"), dict) else obj
        failed = summary.get("not_passed") if isinstance(summary, dict) else None
        if isinstance(failed, list) and failed:
            listed = "; ".join(f"{g.get('gate')} {g.get('status')}: {g.get('summary')}" for g in failed[:4])
            more = f" (+{len(failed) - 4} more)" if len(failed) > 4 else ""
            return f"exit {rc}: gates didn't pass: {listed}{more}"
        if obj.get("status"):
            return f"exit {rc}: {obj['status']}"
    tail = (err or out or "").strip().splitlines()[-3:]
    return f"exit {rc}: " + (" / ".join(tail) if tail else "no output")


def format_human(res: dict) -> str:
    lines = [f"make {res['target']} for {res['film']}" + (" (dry run)" if res.get("dry_run") else "")]
    for r in res["results"]:
        lines.append(f"  {r['node']:<11} {r['action']:<14} {r['why']}")
    ran = [r["node"] for r in res["results"] if r["action"] == "ran"]
    lines.append(f"ran {len(ran)} of {len(res['plan'])}: {', '.join(ran) or 'nothing (all fresh or blocked)'}")
    return "\n".join(lines)
