"""`slate status`: where a film stands, from its folder alone, and the one next command.

Everything a film knows is in its folder, so any agent in any session can run `slate status` and pick up the work.
For each stage (brief, truth, script, voice, timing, look, scenes, captures, generated shots, sound, render, master,
qa, deliver, review) status reports one state:

- **fresh**: made, recorded, and nothing it was made from has changed;
- **stale**: something it was made from changed (film.lock.json hashes, a STALE gate, an approval that no longer
  matches, or a derived film's re-staging and sound re-plan still to do);
- **missing**: not made yet;
- **locked**: approved and unchanged, or held on purpose (the timeline once act renders exist);
- **skipped**: not needed for this film, with the reason (no captures planned, generation off, the brand's look
  already approved).

It reads film.lock.json (what made each file), qa/gates.json (gates turn STALE when their inputs change), approvals and
the files present. Gates can name their stage in `details.stage`; otherwise the gate's name places it. A parked film
shows its question first, because nothing moves until a person answers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slate import gates, jsonio, paths
from slate.errors import UsageError
from slate.state import _util, approvals, film, lock, production


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    dirs: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    producers: tuple[str, ...] = ()
    gate_words: tuple[str, ...] = ()
    approval: str | None = None
    command: tuple[str, ...] = ()
    stale_command: tuple[str, ...] | None = None
    hint: str = ""
    extra: dict = field(default_factory=dict)


STAGES: tuple[Stage, ...] = (
    Stage("brief", "brief", sources=("brief/GOAL.md", "brief/brief.json"), approval="checklist",
          command=("film", "ask"), hint="record the request exactly as it was given"),
    Stage("truth", "truth", sources=("brief/truth.json",), producers=("slate truth", "slate claims"),
          gate_words=("claim", "truth", "source", "number", "quote"),
          approval="claims", command=("script", "build"),
          hint="every claim on the brand's approved list or sourced (the script gates check it)"),
    Stage("script", "script", sources=("script/script.json",), files=("script/storyboard.json", "script/SCRIPT.md"),
          producers=("slate script",),
          gate_words=("script", "storyboard", "pace", "read_time", "readtime", "runtime", "banned"),
          approval="script", command=("script", "build"),
          hint="write the acts in script/script.json, then build the storyboard"),
    Stage("voice", "voice", dirs=("voice",), producers=("slate voice",),
          gate_words=("voice", "whisper", "take", "tag"), approval="voice_shortlist", command=("voice", "cast"),
          stale_command=("voice", "tts"), hint="cast or design the voice, then voice every line"),
    Stage("timing", "timing", files=("timing/shots.json", "timing/vo_cues.json", "timing/vo_words.json",
                                     "timing/timeline.json"),
          producers=("slate timing",), gate_words=("timing", "shots", "contiguous"), command=("timing",),
          hint="shots from the real reads, then words and events on one clock"),
    Stage("look", "look", gate_words=("look",), approval="look_test", command=("approve",),
          hint="a moving 10–15 second look test with sound, approved by a person (stills don't count)"),
    Stage("scenes", "scenes", dirs=("scenes", "timing/visual_events", "timing/motion"),
          producers=("slate emit-events", "slate check", "slate engine", "slate scenes"),
          gate_words=("scene", "type_check", "typecheck", "hand_in", "handin", "event", "word_miss", "kit", "font"),
          command=("check",), hint="build each act with the kit, then the hand-in check (slate check <act>)"),
    Stage("captures", "captures", dirs=("captures",), producers=("slate capture",), gate_words=("capture",),
          command=("capture",), hint="scripted captures of the app or site, conformed to the film's clock"),
    Stage("generated", "generated shots", producers=("slate gen", "slate plates"), gate_words=("plate", "generat"),
          command=("plates",), hint="generate only where code can't do the job, estimate first"),
    Stage("sound", "sound", dirs=("sound",), producers=("slate score", "slate mix", "slate verify", "slate sfx",
                                                         "slate sound"),
          gate_words=("loudness", "mix", "sync", "stem", "sound", "music", "gap", "verify", "cue", "score"),
          command=("mix",), hint="the score, the effects and the mix"),
    Stage("render", "render", files=("renders/registry.json",), dirs=("renders",), producers=("slate render",),
          gate_words=("render", "glitch", "boundary", "join", "motion", "frame_count", "frames"), command=("render",),
          hint="render every act with true motion blur"),
    Stage("master", "master", dirs=("masters",), producers=("slate master",), gate_words=("master", "psnr", "aep"),
          command=("master",), hint="the masters (After Effects for supervised films, ffmpeg for draft and "
                                    "autonomous films)"),
    Stage("qa", "qa", files=("qa/gates.json",), command=("qa",), hint="every film gate"),
    Stage("deliver", "deliver", dirs=("deliver",), producers=("slate deliver",), gate_words=("deliver", "manifest"),
          command=("deliver",), hint="the delivery set, with the manifest written last"),
    Stage("review", "review", dirs=("review",), producers=("slate review",), gate_words=("review",),
          command=("review",), hint="a review packet for this version"),
)
BY_KEY = {s.key: s for s in STAGES}
# The order the work happens in, for the next command (the display keeps the order above).
PIPELINE = ("brief", "script", "truth", "voice", "look", "@preproduction", "timing", "scenes", "captures",
            "generated", "sound", "render", "master", "qa", "deliver", "review", "@final_cut")
# Where a gate goes when it doesn't say (details.stage): first match wins, in this order.
GATE_ORDER = ("truth", "script", "voice", "look", "captures", "generated", "render", "master", "sound", "timing",
              "scenes", "deliver", "review")
BAD_GATES = ("FAIL", "REVIEW", "UNCHECKED")


# ---------------------------------------------------------------------------------------------------------------
# Command availability (a command that isn't built yet is reported, never faked)


_AVAILABLE: dict[tuple[str, ...], bool] = {}


def command_available(argv: tuple[str, ...]) -> bool:
    """Is `slate <argv…>` a registered command in this Slate?"""
    if not argv:
        return False
    if argv in _AVAILABLE:
        return _AVAILABLE[argv]
    from slate import cli  # noqa: PLC0415

    import argparse  # noqa: PLC0415

    parser: Any = cli.build_parser()
    ok = True
    for word in argv:
        action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        if action is None or word not in action.choices:
            ok = False
            break
        parser = action.choices[word]
    _AVAILABLE[argv] = ok
    return ok


def reset_command_cache() -> None:
    _AVAILABLE.clear()


# ---------------------------------------------------------------------------------------------------------------
# Reading the film


def _gate_stage(result: gates.GateResult) -> str:
    declared = (result.details or {}).get("stage") if isinstance(result.details, dict) else None
    if declared in BY_KEY:
        return declared
    name = result.gate.lower()
    for key in GATE_ORDER:
        if any(word in name for word in BY_KEY[key].gate_words):
            return key
    return "qa"


def _read(film_dir: Path, rel: str) -> Any:
    """A JSON file that may be mid-edit: None when it's missing or doesn't parse (status must never crash)."""
    try:
        return jsonio.read(lock.resolve(film_dir, rel), default=None)
    except UsageError:
        return None


def _script_acts(film_dir: Path) -> tuple[int, int]:
    """(acts, shots) in script.json (the film's own, or its parent's when it's reused by reference)."""
    data = _read(film_dir, "script/script.json")
    if not isinstance(data, dict):
        return 0, 0
    acts = data.get("acts") or []
    shots = [s for a in acts if isinstance(a, dict) for s in (a.get("shots") or a.get("lines") or [])]
    return len(acts), len(shots)


def _script_wants(film_dir: Path, words: tuple[str, ...]) -> bool:
    """Does any shot in the script or storyboard carry one of these keys (e.g. 'capture', 'plate', 'generate')?"""
    for rel in ("script/storyboard.json", "script/script.json"):
        data = _read(film_dir, rel)
        if not isinstance(data, dict):
            continue
        for act in data.get("acts") or []:
            for shot in (act.get("shots") or act.get("lines") or []) if isinstance(act, dict) else []:
                if isinstance(shot, dict) and any(shot.get(w) for w in words):
                    return True
    return False


def _count_files(film_dir: Path, folders: tuple[str, ...]) -> tuple[int, list[str]]:
    total, sample = 0, []
    for folder in folders:
        for p in _util.walk_files(film_dir / folder, limit=5000):
            total += 1
            if len(sample) < 5:
                sample.append(p.relative_to(film_dir).as_posix())
    return total, sample


def _stage_entries(stage: Stage, files: dict[str, dict]) -> list[str]:
    out = []
    for rel, e in files.items():
        if rel in stage.files or rel in stage.sources:
            out.append(rel)
            continue
        if any(rel == d or rel.startswith(d + "/") for d in stage.dirs):
            out.append(rel)
            continue
        if stage.producers and any(str(e.get("producer", "")).startswith(p) for p in stage.producers):
            out.append(rel)
    return sorted(set(out))


class _Ctx:
    def __init__(self, film_dir: Path):
        self.film_dir = Path(film_dir)
        self.fj = film.load(self.film_dir)
        self.lockdata = lock.load(self.film_dir)
        self.files = self.lockdata["files"]
        self.hasher = _util.Hasher(self.film_dir)
        self.memo: dict = {}
        self.problems: list[str] = []
        try:
            self.gates = gates.load_report(self.film_dir)
        except UsageError as exc:
            self.gates = {}
            self.problems.append(exc.message)
        self.gates_by_stage: dict[str, list[gates.GateResult]] = {}
        for r in self.gates.values():
            self.gates_by_stage.setdefault(_gate_stage(r), []).append(r)
        self.derivation = self.fj.get("derivation") or None
        self.created = str(self.fj.get("created_at") or "")

    def explain(self, rel: str) -> dict:
        return lock.explain(self.film_dir, rel, hasher=self.hasher, _memo=self.memo)

    def exists(self, rel: str) -> bool:
        return lock.resolve(self.film_dir, rel).exists()


def _own_since_derivation(ctx: _Ctx, rels: list[str]) -> bool:
    """A derived film re-did this stage: it recorded its own files (not references) after it was derived."""
    for rel in rels:
        e = ctx.files.get(rel) or {}
        if not e.get("ref") and str(e.get("made_at", "")) >= ctx.created:
            return True
    return False


def _stage(ctx: _Ctx, stage: Stage, sit: dict) -> dict:
    film_dir, fj = ctx.film_dir, ctx.fj
    out: dict[str, Any] = {"stage": stage.key, "label": stage.label, "state": "fresh", "ok": True, "summary": "",
                           "reasons": [], "problems": [], "evidence": [], "gates": {}, "approval": None,
                           "command": None}
    rels = _stage_entries(stage, ctx.files)
    explained = {rel: ctx.explain(rel) for rel in rels}
    stale = [r for r, x in explained.items() if x["state"] in ("stale", "missing")]
    tampered = [r for r, x in explained.items() if x["state"] == "tampered"]
    locked = [r for r, x in explained.items() if x["state"] == "locked"]
    moved = [r for r in locked if explained[r]["reasons"]]
    n_dir_files, sample = _count_files(film_dir, stage.dirs)
    present_files = [r for r in stage.files + stage.sources if ctx.exists(r)]
    unrecorded = [r for r in stage.files if ctx.exists(r) and r not in ctx.files]
    stage_gates = ctx.gates_by_stage.get(stage.key, []) if stage.key != "qa" else list(ctx.gates.values())
    out["gates"] = {g.gate: g.status for g in stage_gates}
    gate_stale = [g for g in stage_gates if g.status == "STALE"]
    gate_bad = [g for g in stage_gates if g.status in BAD_GATES]
    appr = approvals.kind_state(film_dir, stage.approval, ctx.hasher) if stage.approval else None
    out["approval"] = appr
    present = bool(rels or n_dir_files or present_files or stage_gates or (appr and (appr["paths"] or appr["stale"])))
    out["evidence"] = (rels[:5] or present_files[:5] or sample)
    reasons: list[str] = []
    problems: list[str] = [f"{g.gate} {g.status}: {g.summary}" for g in gate_bad]
    if tampered:
        problems.append(f"locked file changed on disk: {', '.join(tampered[:3])} (put the approved version back)")
    if moved:
        problems.append(f"{len(moved)} locked file(s) have inputs that moved since they were approved "
                        f"({', '.join(moved[:3])}); remaking them needs force and a person's spend approval")
    if stale:
        reasons.append(f"{len(stale)} file(s) out of date: " + "; ".join(
            f"{r} ({explained[r]['reasons'][0] if explained[r]['reasons'] else explained[r]['state']})"
            for r in stale[:3]))
    if gate_stale:
        reasons.append("gates to re-run: " + ", ".join(g.gate for g in gate_stale[:5]))
    if appr and appr["stale"] and not appr["approved"]:
        reasons.append(f"changed since its {stage.approval} approval: {', '.join(appr['stale'][:3])}")
    if unrecorded:
        out["unrecorded"] = unrecorded
    skipped: str | None = None
    summary = ""
    command = stage.command

    # Stage-specific rules.
    if stage.key == "brief":
        asks = production.asks_summary(film_dir)
        present = asks["request"]
        c = asks["counts"]
        summary = (f"request captured; {asks['total']} asks ({c['done']} done)" if present else
                   "no request recorded yet")
        if present and not asks["total"]:
            problems.append("the checklist has no asks")
    elif stage.key == "truth":
        covered = sit.get("covered", {}).get("claims")
        if covered:
            skipped = covered
        summary = ("claims checked" if stage_gates else "no claims check yet")
        if appr and appr["approved"]:
            summary = "claims approved"
    elif stage.key == "script":
        n_acts, n_shots = _script_acts(film_dir)
        frozen = lock.frozen_shot_ids(film_dir)
        if ctx.exists("script/script.json") and _read(film_dir, "script/script.json") is None:
            problems.append("script/script.json doesn't parse as JSON")
        if not n_shots:
            present = False
            summary = "script/script.json has no shots yet" if ctx.exists("script/script.json") else \
                "script/script.json is missing"
        elif not ctx.exists("script/storyboard.json"):
            present = False
            summary = f"{n_acts} acts, {n_shots} shots written; the storyboard isn't built"
        else:
            summary = f"{n_acts} acts, {n_shots} shots" + (f"; {len(frozen)} shot ids frozen" if frozen else "")
            if "script/storyboard.json" not in ctx.files:
                reasons.append("nothing records the storyboard being built from script.json")
    elif stage.key == "voice":
        takes_locked = len(locked)
        summary = (f"{n_dir_files} files" + (f", {takes_locked} locked" if takes_locked else "")) if present else \
            "no voice yet"
        if not present:
            command = stage.command
        elif stale:
            command = stage.stale_command or stage.command
    elif stage.key == "timing":
        summary = f"{len(present_files)} of {len(stage.files)} timing files" if present else "no timing yet"
        if lock.timeline_locked(film_dir):
            out["timeline_locked"] = True
            if stale or gate_stale:
                problems.append("inputs changed after the timeline locked: re-timing needs a person's go "
                                "(--force --approved-by NAME --reason) and then re-renders")
            reasons, stale, gate_stale = [], [], []
            summary += "; timeline locked (act renders registered)"
    elif stage.key == "look":
        covered = sit.get("covered", {}).get("look_test")
        if covered:
            skipped = covered
        summary = "look test approved" if appr and appr["approved"] else "no approved look test"
    elif stage.key == "captures":
        if not present and not _script_wants(film_dir, ("capture", "captures")):
            skipped = "no captures planned in the script"
        summary = f"{n_dir_files} files" if present else "none yet"
    elif stage.key == "generated":
        mode = fj.get("mode")
        if fj.get("generation") == "none":
            skipped = "generation is off for this film"
        elif mode == "draft":
            skipped = "draft films have no plates"
        elif not present and not _script_wants(film_dir, ("plate", "generate", "generated")):
            skipped = "no generated shots planned (generation is rare by default)"
        summary = f"{len(rels)} generated files" if present else "none yet"
    elif stage.key == "render":
        entries = lock.registered_act_renders(film_dir)
        reg = _read(film_dir, lock.RENDER_REGISTRY)
        if reg is None and (film_dir / lock.RENDER_REGISTRY).exists():
            problems.append("renders/registry.json doesn't parse")
        all_entries = (reg or {}).get("entries") or [] if isinstance(reg, dict) else []
        bad = []
        for e in all_entries:
            f = e.get("file")
            if not f:
                continue
            p = Path(f) if Path(f).is_absolute() else film_dir / f
            if not p.exists():
                bad.append(f"{f} is missing")
            elif e.get("sha256") and ctx.hasher.sha(p) != e.get("sha256"):
                bad.append(f"{f} changed since it was registered")
        if bad:
            reasons.append("; ".join(bad[:3]))
        summary = f"{len(entries)} act renders registered" if present else "nothing rendered yet"
    elif stage.key == "qa":
        present = bool(stage_gates) or bool(ctx.problems)
        if present:
            counts = gates.summarize(stage_gates)["counts"]
            summary = f"{len(stage_gates)} gates: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v)
            problems = [f"{g.gate} {g.status}: {g.summary}" for g in gate_bad]
        else:
            summary = "no gates have run"
        problems += ctx.problems
    elif stage.key == "deliver":
        final = approvals.kind_state(film_dir, "final_cut", ctx.hasher)
        summary = f"{n_dir_files} files" if present else "nothing delivered yet"
        if final["approved"]:
            summary += f"; final cut approved ({', '.join(final['paths'][:2])})"
    else:
        summary = f"{n_dir_files} files" if present else "nothing yet"

    # Derived films: re-staging and sound re-planning are owed until the film records its own work.
    if ctx.derivation:
        if stage.key == "scenes" and (ctx.derivation.get("restage") or {}).get("needed"):
            if not _own_since_derivation(ctx, rels):
                reasons.append(f"re-stage for this film: {(ctx.derivation['restage'].get('why') or 'its own layout')}")
                present = True
        if stage.key == "sound" and (ctx.derivation.get("sound") or {}).get("replan"):
            if not _own_since_derivation(ctx, rels):
                reasons.append("re-plan the sound for this derived film")
                present = True

    if skipped:
        state = "skipped"
        summary = skipped
    elif not present:
        state = "missing"
    elif reasons:
        state = "stale"
        if stage.stale_command:
            command = stage.stale_command
    elif (appr and appr["approved"]) or (locked and len(locked) == len(rels)) or out.get("timeline_locked"):
        state = "locked"
    else:
        state = "fresh"
    out.update(state=state, summary=summary, reasons=reasons, problems=problems,
               ok=state in ("fresh", "locked", "skipped") and not problems, command=list(command) or None)
    return out


# ---------------------------------------------------------------------------------------------------------------
# The one next command


def _cmd(argv: list[str] | tuple[str, ...] | None) -> str | None:
    return ("slate " + " ".join(argv)) if argv else None


def _next(ctx: _Ctx, stages: dict[str, dict], sit: dict, parked: list[dict], revision: dict | None,
          tampered: list[str]) -> dict:
    fid = ctx.fj.get("id")
    if parked:
        q = parked[0]
        return {"command": f"slate film answer {q['id']} --by NAME --text \"…\"", "who": "person", "available": True,
                "why": f"parked: {q['text']}", "stage": None}
    if tampered:
        return {"command": None, "who": "agent", "available": True, "stage": None,
                "why": f"a locked (approved) file changed on disk: {', '.join(tampered[:3])}. Put the approved "
                       "version back (git, the archive or the parent film); Slate never keeps an overwrite."}
    if revision:
        return {"command": f"slate revise check {revision['id']}", "who": "agent", "available": True, "stage": None,
                "why": f"revision {revision['id']} of {revision['artifact']} is open: make the checklist's changes "
                       "to that exact artifact, check them, then `slate revise done`."}
    for step in PIPELINE:
        if step.startswith("@"):
            name = step[1:]
            s = sit.get(name) or {}
            if s.get("blocking") and s.get("pending"):
                what = s["pending"][0]
                label = "pre-production sitting" if name == "preproduction" else "final-cut sitting"
                if name == "final_cut" and ctx.fj.get("mode") == "autonomous":
                    label = "held for a person (final-cut sitting)"
                return {"command": f"slate approve {what} <path> --by NAME", "who": "person", "available": True,
                        "stage": None, "sitting": name,
                        "why": f"{label}: a person approves {', '.join(s['pending'])}" + (
                            f" ({s['note']})" if s.get("note") else "")}
            continue
        st = stages[step]
        if st["ok"]:
            continue
        argv = tuple(st["command"] or ())
        available = command_available(argv) if argv else False
        why = st["summary"]
        if st["reasons"]:
            why = st["reasons"][0]
        if st["problems"]:
            why = st["problems"][0]
        hint = BY_KEY[step].hint
        text = f"{st['label']} is {st['state']}: {why}. Next: {hint}."
        if argv and not available:
            text += f" (`{_cmd(argv)}` isn't built in this Slate yet.)"
        if step == "look" and not (st["approval"] or {}).get("approved"):
            return {"command": "slate approve look_test <file> --by NAME", "who": "person", "available": True,
                    "stage": step, "why": text}
        if step == "brief" and st["state"] == "missing":
            return {"command": "slate film ask --request \"<the request, verbatim>\" --source NAME", "who": "agent",
                    "available": True, "stage": step, "why": text}
        return {"command": _cmd(argv), "who": "agent", "available": available, "stage": step, "why": text}
    mode = ctx.fj.get("mode")
    if mode == "draft":
        why = "every stage is done for a draft (drafts are never delivered as final)."
    else:
        final = sit.get("final_cut") or {}
        why = ("done: every stage is current and the final cut is approved. Only that exact file may be published."
               if final.get("done") else "every stage is current.")
    return {"command": None, "who": None, "available": True, "stage": None, "why": f"{fid}: {why}"}


def compute(film_dir: Path) -> dict:
    """Where the film stands: per-stage states, sittings, locks, questions, the open revision and the next command."""
    film_dir = Path(film_dir)
    from slate.state import park  # noqa: PLC0415

    park.sync_qa(film_dir)
    ctx = _Ctx(film_dir)
    try:
        sit = approvals.sittings(film_dir, ctx.hasher)
        stages = {s.key: _stage(ctx, s, sit) for s in STAGES}
        parked = approvals.open_questions(film_dir, parking=True)
        other_q = approvals.open_questions(film_dir, parking=False)
        revs = [r for r in approvals.load(film_dir)["revisions"] if r.get("status") == "open"]
        tampered = [r for r, e in ctx.files.items() if e.get("locked") and ctx.explain(r)["state"] == "tampered"]
        frozen_ids = lock.frozen_shot_ids(film_dir)
        fj = ctx.fj
        der = fj.get("derivation") or None
        result = {
            "film": fj.get("id"),
            "title": fj.get("title"),
            "dir": str(film_dir.resolve()),
            "mode": fj.get("mode"),
            "format": fj.get("format"),
            "brand": fj.get("brand"),
            "language": fj.get("language"),
            "size": f"{fj.get('width')}x{fj.get('height')}",
            "aspect": fj.get("aspect"),
            "fps": f"{fj.get('fps_num')}/{fj.get('fps_den')}",
            "generation": fj.get("generation"),
            "version": film.current_version(film_dir),
            "parent": fj.get("parent"),
            "derivation": ({"kind": der.get("kind"), "kinds": der.get("kinds"), "reused": (der.get("reuse") or {}).get(
                "count"), "redo": der.get("redo")} if der else None),
            "parked": parked,
            "questions": other_q,
            "asks": production.asks_summary(film_dir),
            "stages": [stages[s.key] for s in STAGES],
            "sittings": {k: v for k, v in sit.items() if k in ("preproduction", "final_cut", "covered", "packets")},
            "locks": {"timeline": lock.timeline_locked(film_dir), "shot_ids_frozen": len(frozen_ids),
                      "locked_assets": len(lock.locked_assets(film_dir)), "tampered": tampered},
            "revision": ({"id": revs[-1]["id"], "artifact": revs[-1]["artifact"],
                          "items": len(revs[-1]["items"]), "questions": revs[-1]["questions"]} if revs else None),
            "computed_at": _util.now(),
        }
        result["next"] = _next(ctx, stages, sit, parked, result["revision"], tampered)
        result["ok"] = all(s["ok"] for s in result["stages"]) and not parked
        return result
    finally:
        ctx.hasher.save()


# ---------------------------------------------------------------------------------------------------------------
# For people


def format_human(st: dict) -> str:
    home = str(Path.home())
    where = st["dir"].replace(home, "~", 1) if st["dir"].startswith(home) else st["dir"]
    head = [f"Film     {st['film']}: {st['title']}",
            f"         {st['mode']} · {st['format']} · {st['size'].replace('x', '×')} ({st['aspect']}) · "
            f"{st['fps']} fps · brand {st['brand']} · {st['language']} · version {st['version']}",
            f"         {where}"]
    if st.get("parent"):
        d = st.get("derivation") or {}
        head.append(f"         derived from {st['parent']['id']} ({'+'.join(d.get('kinds') or [])}); "
                    f"{d.get('reused') or 0} parent files reused by reference")
    lines = head + [""]
    for q in st["parked"]:
        lines.append(f"PARKED   {q['id']}: {q['text']}")
        if q.get("evidence"):
            lines.append(f"         evidence: {'; '.join(q['evidence'][:4])}")
        lines.append(f"         a person answers: slate film answer {q['id']} --by NAME --text \"…\"")
    if st["parked"]:
        lines.append("")
    width = max(len(s["label"]) for s in st["stages"])
    lines.append(f"{'stage':<{width}}  {'state':<8}  notes")
    for s in st["stages"]:
        mark = "" if s["ok"] else "  ⚠ " + (s["problems"][0] if s["problems"] else (s["reasons"][0] if s["reasons"]
                                                                                       else ""))
        note = s["summary"]
        if s["reasons"] and s["state"] == "stale":
            note = s["reasons"][0]
        lines.append(f"{s['label']:<{width}}  {s['state']:<8}  {note}{mark if s['problems'] else ''}")
    lines.append("")
    sit = st["sittings"]
    for name, label in (("preproduction", "pre-production"), ("final_cut", "final cut")):
        s = sit.get(name) or {}
        if not s.get("items"):
            lines.append(f"{label:<15} {s.get('note') or 'not needed'}")
            continue
        blocking = "blocking" if s.get("blocking") else "not blocking"
        pend = f"; waiting on {', '.join(s['pending'])}" if s.get("pending") else ""
        lines.append(f"{label:<15} {len(s['approved'])} of {len(s['items'])} approved ({blocking}){pend}")
    locks = st["locks"]
    lines.append(f"{'locks':<15} timeline {'locked' if locks['timeline'] else 'open'} · "
                 f"{locks['shot_ids_frozen']} shot ids frozen · {locks['locked_assets']} locked assets"
                 + (f" · CHANGED ON DISK: {', '.join(locks['tampered'][:3])}" if locks["tampered"] else ""))
    asks = st["asks"]
    lines.append(f"{'asks':<15} {asks['total']} in the checklist, {asks['counts']['done']} done")
    if st.get("questions"):
        lines.append(f"{'questions':<15} " + "; ".join(f"{q['id']}: {q['text']}" for q in st["questions"][:3]))
    if st.get("revision"):
        r = st["revision"]
        lines.append(f"{'revision':<15} {r['id']} of {r['artifact']} is open ({r['items']} changes)")
    nxt = st["next"]
    lines.append("")
    if nxt.get("command"):
        who = " (a person)" if nxt.get("who") == "person" else ""
        lines.append(f"next{who}: {nxt['command']}")
        lines.append(f"      {nxt['why']}")
    else:
        lines.append(f"next: {nxt['why']}")
    return "\n".join(lines)


def list_films(root: Path | None = None) -> list[dict]:
    """Every film under the films folder, with its mode and next step (for `slate status` outside a film)."""
    root = Path(root) if root else paths.films()
    out = []
    if not root.is_dir():
        return out
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        if not entry.is_dir() or not (Path(entry.path) / film.FILM_JSON).is_file():
            continue
        try:
            st = compute(Path(entry.path))
            out.append({"film": st["film"], "dir": st["dir"], "mode": st["mode"], "parked": bool(st["parked"]),
                        "next": st["next"].get("command"), "why": st["next"].get("why")})
        except UsageError as exc:
            out.append({"film": entry.name, "dir": entry.path, "error": exc.message})
    return out
