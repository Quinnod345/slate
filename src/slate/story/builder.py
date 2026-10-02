"""`slate script build`: script/script.json in, gated storyboard.json and SCRIPT.md out, or nothing at all.

Film C's generator was the model (build/C/tools/c_storyboard.py): the content lives in one data file, the build computes
every length and timecode, renumbers ids and remaps mentions, and exits non-zero writing nothing when any check fails.
Here the checks are the script gates (`slate.story.gates`) plus the shot-id freeze (`slate.story.freeze`).

Lengths come from planning (words at the planning rate) until the film is voiced. `--from-takes` retimes every voiced
shot from its approved take and switches the film to take timing, which later builds keep (they use each line's take
when it has one, and say which lines are still planned).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

from slate import __version__, gates, jsonio, log
from slate.errors import MissingPrerequisite, Refused
from slate.gates import GateResult
from slate.story import derived, freeze, inputs, scriptmd
from slate.story import gates as script_gates
from slate.story.model import SCRIPT, Plan, plan
from slate.timing.takes import find_takes

STORYBOARD = Path("script") / "storyboard.json"
SCRIPT_MD = Path("script") / "SCRIPT.md"
TRUTH = Path("brief") / "truth.json"
COMMAND = "slate script build"
# The film.json values the storyboard is made from. The lock records these keys, not the whole file, so a pin written
# at first use (a model id, a tool version) doesn't make the storyboard, and everything after it, stale.
FILM_KEYS = ("brand", "format", "mode", "targets", "language", "title")


@dataclass
class ScriptBuild:
    results: list[GateResult] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    plan: Plan | None = None
    storyboard: dict | None = None
    refused: bool = False
    retimed: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[GateResult]:
        return [r for r in self.results if r.status == "FAIL"]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _shot_doc(s) -> dict:
    d = s.data
    sources = d.get("sources") or []
    doc = {"id": s.id, "dur": s.dur, "dur_source": s.dur_source, "enter_token": d.get("enter_token"),
           "vo": s.vo, "on_screen": d.get("on_screen") or "", "picture": d.get("picture") or "",
           "transition": d.get("transition") or "", "build": d.get("build") or "", "sound": d.get("sound") or "",
           "source": "; ".join(x if isinstance(x, str) else str(x.get("ref") or x.get("id") or x) for x in sources),
           "sources": sources, "claims": d.get("claims") or [], "quotes": d.get("quotes") or [],
           "notes": d.get("notes") or "", "lead": s.lead, "hold": s.hold, "act_end": s.act_end, "words": s.words}
    if s.source_id != s.id:
        doc["source_id"] = s.source_id
    if d.get("events"):
        doc["events"] = d["events"]
    if d.get("generate"):
        doc["generate"] = d["generate"]  # the shot's generation request, carried to timing/shots.json for `slate gen`
    if d.get("engine"):
        doc["engine"] = d["engine"]  # the engine that draws this shot when it isn't its act's (P074), for timing
    if d.get("reading") is not None:
        doc["reading"] = d["reading"]  # how its text is read (voice_matched, not_for_reading): `slate qa readtime`
    if d.get("hero_lines"):
        doc["hero_lines"] = d["hero_lines"]  # its on-screen text set in the hero role (script.hero_lines checked them)
    if s.take:
        doc["take"] = s.take
    return doc


def storyboard_doc(p: Plan, *, film: dict, results: list[GateResult], previous: dict | None, target: dict,
                   made_from: dict) -> dict:
    acts = []
    for i, act in enumerate(p.acts, 1):
        acts.append({**act, "shots": [_shot_doc(s) for s in p.shots if s.act == i]})
    before = (previous or {}).get("shot_ids") or {}
    timing = dict(p.timing)
    timing["source"] = p.mode
    if p.mode == "takes":
        timing["takes"] = {s.id: s.take for s in p.shots if s.take}
        timing["planned_lines"] = list(p.missing_takes)
    doc = {
        "title": p.script.get("title") or film.get("title") or film.get("id"),
        "version": p.script.get("version"),
        "film": film.get("id"),
        "language": p.lang,
        "made_from": made_from,
        "timing": timing,
        "runtime_s": p.runtime,
        "target": target,
        "shot_ids": {"mode": p.ids_mode, "frozen_at": before.get("frozen_at") or _now(),
                     "ids": [{"id": s.id, "source_id": s.source_id} for s in p.shots]},
        "acts": acts,
        "gates": gates.summarize(results),
    }
    return jsonio.mark_generated(doc, COMMAND)


def _locked(film_dir: Path) -> str | None:
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        return None
    for rel in (STORYBOARD, SCRIPT_MD):
        try:
            if state_lock.is_locked(film_dir, str(rel)):
                return f"{rel} is locked in film.lock.json"
        except Exception:  # noqa: BLE001
            return None
    return None


def _record_lock(film_dir: Path, rel: Path, inputs_: dict) -> None:
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        return
    try:
        state_lock.record(film_dir, str(rel), producer=COMMAND, inputs=inputs_, tool_version=__version__)
    except Exception as exc:  # noqa: BLE001 - the files are written; say so rather than fail after the fact
        log.warn(f"couldn't record {rel} in film.lock.json: {exc}")


def lock_inputs(film_dir: Path, film: dict, files: list) -> dict:
    """{input: sha256} for the lock and the storyboard's made_from: the files read, the film.json keys used, the
    blocks of the brand pack the gates read (inputs.SCRIPT_BRAND_BLOCKS: its truth and duration scale, never the whole
    pack, so an approval or a look edit leaves the storyboard fresh) and the format template (state's named inputs, so
    each is checked on its own)."""
    keys = [str(f) for f in files] + [f"film:{k}" for k in FILM_KEYS if k in film]
    keys += inputs.brand_block_inputs(film.get("brand"), inputs.SCRIPT_BRAND_BLOCKS)
    if film.get("format"):
        keys.append(f"format:{film['format']}")
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415

        hashed = state_lock.hash_inputs(film_dir, keys)
    except Exception:  # noqa: BLE001 - without film state, hash the files ourselves and leave the named inputs out
        hashed = gates.inputs_for(film_dir, files)
    return dict(hashed)  # a file that isn't there yet stays in, as None, so its arrival makes the storyboard stale


def run(film_dir: Path, *, check_only: bool = False, from_takes: bool = False) -> ScriptBuild:
    """Plan, gate and (when nothing fails and this isn't a check) write the storyboard and SCRIPT.md."""
    film_dir = Path(film_dir)
    film = inputs.load_film(film_dir)
    script, parent = derived.load(film_dir, film)  # a cut-down's script comes from its parent's shots
    lang = film.get("language") or script.get("language") or "en"
    brand = inputs.load_brand(film.get("brand"))
    fmt, fmt_problem = inputs.load_format(film.get("format"))
    truth = inputs.load_truth(film_dir)
    timing = inputs.timing_settings(script, fmt)
    previous = jsonio.read(film_dir / STORYBOARD, default=None)
    sticky = isinstance(previous, dict) and (previous.get("timing") or {}).get("source") == "takes"
    use_takes = from_takes or sticky
    schema = script_gates.check_schema(script)
    if schema is not None:
        schema.inputs = gates.inputs_for(film_dir, [SCRIPT])
        return ScriptBuild(results=[schema], refused=True)
    if parent.problems:  # a cut that keeps nothing, or keeps shots its parent doesn't have
        return ScriptBuild(results=[derived.gate(parent, [])], refused=True)
    takes = find_takes(film_dir) if use_takes else {}
    default_token = (script.get("defaults") or {}).get("enter_token")
    p = plan(script, timing=timing, takes=takes, use_takes=use_takes, lang=lang, default_enter_token=default_token)
    if from_takes and p.missing_takes:
        raise MissingPrerequisite(f"{len(p.missing_takes)} voiced line(s) have no take yet: "
                                  f"{', '.join(p.missing_takes[:12])}.",
                                  hint="Voice them with `slate voice tts`, then run `slate script build --from-takes`.")
    out = ScriptBuild(plan=p)
    out.results = script_gates.check_plan(p, brand=brand, fmt=fmt, truth=truth, film=film, format_problem=fmt_problem)
    if not p.problems:
        out.results.append(freeze.check(film_dir, p))
        lineage = derived.gate(parent, [s.id for s in p.shots])
        if lineage is not None:
            out.results.append(lineage)
    # The gates record the files they read. film.json isn't one of them: the keys they use (mode, targets, format,
    # brand) are the storyboard's own lock inputs, so a change there makes the storyboard stale and these gates run
    # again, while a pin or a new version written into film.json doesn't turn every script gate STALE.
    input_paths = [SCRIPT, TRUTH]
    if parent.parent_script:
        input_paths.append(parent.parent_script)
    # the brand blocks the gates read, as the storyboard's lock records them (a pack named by a path keeps its file)
    brand_keys = inputs.brand_block_inputs(film.get("brand"), inputs.SCRIPT_BRAND_BLOCKS)
    if brand_keys and gates.is_named(brand_keys[0], film_dir):
        input_paths += brand_keys
    elif brand.get("_dir"):
        input_paths.append(Path(brand["_dir"]) / "brand.json")
    input_paths += [t.path for t in takes.values()]
    stamped = gates.inputs_for(film_dir, input_paths)
    for r in out.results:
        r.inputs = dict(stamped)
    if previous and use_takes:
        old = {s["id"]: s.get("dur") for a in previous.get("acts") or [] for s in a.get("shots") or []}
        out.retimed = [f"{s.id} {old[s.id]:.2f}→{s.dur:.2f} s" for s in p.shots
                       if s.id in old and old[s.id] is not None and abs(float(old[s.id]) - s.dur) > 1e-9]
    if check_only or out.failed:
        out.refused = bool(out.failed)
        return out
    lock = _locked(film_dir)
    if lock:
        raise Refused(f"The storyboard is locked: {lock}.",
                      hint="An approved script changes only through its approval; unlock it with the state tools first.")
    target = inputs.runtime_target(film, fmt)
    made_from = lock_inputs(film_dir, film, [SCRIPT, TRUTH, *[t.rel for t in takes.values()],
                                             *([parent.parent_script] if parent.parent_script else [])])
    out.storyboard = storyboard_doc(p, film=film, results=out.results, previous=previous, target=target,
                                    made_from=made_from)
    title = str(out.storyboard.get("title") or film.get("id") or "Film")
    out.written.append(jsonio.write_atomic(film_dir / STORYBOARD, out.storyboard))
    out.written.append(jsonio.write_text_atomic(film_dir / SCRIPT_MD, scriptmd.render(
        p, title=title, results=out.results, target=target, film_id=film.get("id"))))
    for rel in (STORYBOARD, SCRIPT_MD):
        _record_lock(film_dir, rel, made_from)
    freeze.record_freeze(film_dir, [s.id for s in p.shots])
    gates.write_report(film_dir, out.results)
    return out


def build(film_dir: Path, **kw) -> list[GateResult]:
    """The §12 interface: the gate results (written only when none fails)."""
    return run(Path(film_dir), **kw).results
