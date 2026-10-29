"""Per-shot engines: a shot may carry its own engine, and by default it's drawn by its act's (P074).

The engine is chosen per brand (brand.json `look.engine`) and, where one shot needs something its act's engine can't
give, per shot. What's built, and the rules that keep it safe:

- **Declaring it.** A shot's `engine` (remotion, web or ae) in script/script.json, where a person writes it, carried
  through script/storyboard.json to timing/shots.json by `slate script build` and `slate timing`. Render and check
  read shots.json first, then the script by shot id (a storyboard built before it carried engines). A shot that names
  no engine is drawn by its act's.
- **Where its scene lives.** Source discovery also recognises Remotion shot modules in
  `scenes/shots/<SHOT>/remotion/index.*`; descriptors preserve their identity and film bounds without enabling the
  unsupported render path. A web shot inside a Remotion act is a page of its own, `scenes/shots/<SHOT>/web/
  index.html`, built with `@slate/web` like a web act's: it draws the act's time (defineScene's duration is the
  act's), and is only ever asked for its shot's frames. Its own folder keeps it out of the act's type check and module.
  A declaration and the scenes on disk must agree: a shot that says web needs that page, and a page there needs the
  shot to say web.
- **Rendering.** The act renders whole in its own engine, then each shot in another engine renders over exactly its
  shot's frames, at the act's sample count, and is spliced into the registered act render with the splice's proof
  (frame count, the frames at both joins, the edge scan). The registry entry lists the shots and their engines. A
  patch of the act may not cross a seam between engines (the seam is always a cut, never inside a shot), and a patch
  inside such a shot renders in its engine.
- **Handing it in.** `slate check <act>` hands in each of the act's web shots through the open web renderer (its type
  check, its page, its network wall and fonts, its words, banned words, never-claims and literal frames), and the
  act's events include the beats its page lands, inside its shot.
- **Motion provenance.** The act emitter and render refresh read each declared web shot's runtime registry without
  writing a shot trace over the act. The combined active trace excludes base motion owned by replaced shots and
  includes the web rows on the film clock, with hashes captured from the HTML and imported module bodies. Complete
  base authoring rows remain in `timing/motion/sources/`. Ambiguous curves that cross a splice are refused rather
  than clipped or relabelled; this is a composition limitation, not a creative restriction. A failed read preserves
  previous trace bytes as failure evidence and removes the current trace, so missing coverage stays UNCHECKED.

Unavailable combinations remain refused by name rather than guessed at:

- an After Effects shot inside a code-engine act, or a code-engine shot inside an AE-built act: aerender renders whole
  comps, and the AE project builds whole acts, so the AE lane works per act (`scenes/ae/actN.json`);
- a Remotion shot inside a web act when the installed engine does not advertise the implemented
  `reverse_web_remotion` schema 1 API. Its `Shot-<ID>` composition draws at whole-act time; the marker reports API
  availability, never picture acceptance or an approval receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from os.path import abspath
from pathlib import Path

from slate import jsonio
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError

ENGINES = ("remotion", "web", "ae")
SHOT_SCENES = Path("scenes") / "shots"
SCENE_EXTENSIONS = (".tsx", ".ts", ".jsx", ".js")


@dataclass(frozen=True)
class Segment:
    """One shot of an act drawn by another engine: its act frames [first, last], inclusive."""

    shot: str
    engine: str
    first: int
    last: int
    scene: Path

    def to_dict(self, film_dir: Path) -> dict:
        try:
            scene = self.scene.relative_to(film_dir).as_posix()
        except ValueError:
            scene = str(self.scene)
        return {"shot": self.shot, "engine": self.engine, "frames": [self.first, self.last], "scene": scene}


@dataclass(frozen=True)
class ShotScene:
    """One declared replacement's source and film-clock owner, without opening a renderer."""

    shot: str
    act: int
    engine: str
    base_engine: str
    scene: Path
    index: Path
    record: dict

    def descriptor(self, film_dir: Path, act_frame_in: int) -> dict:
        """Add the owning act's exact origin to the source descriptor; no shot-local clock is introduced."""
        if not isinstance(act_frame_in, int) or isinstance(act_frame_in, bool) or act_frame_in < 0:
            raise Refused("A shot descriptor needs the owning act's whole film-frame origin.")
        first = self.record["frame_in"] - act_frame_in
        last = self.record["frame_out"] - 1 - act_frame_in
        if first < 0:
            raise Refused(f"Shot {self.shot} starts before its owning act's frame origin.")

        def relative(file: Path) -> str:
            try:
                return file.relative_to(film_dir).as_posix()
            except ValueError:
                return str(file)

        return {"shot": self.shot, "act": self.act, "engine": self.engine, "base_engine": self.base_engine,
                "frames": [first, last], "scene": relative(self.scene), "index": relative(self.index),
                "comp": f"Shot-{self.shot}" if self.engine == "remotion" else None,
                "shot_record": dict(self.record)}


def script_engines(film_dir: Path) -> dict[str, str]:
    """{shot id: engine} from script/script.json, where a person writes a shot's engine."""
    script = jsonio.read(Path(film_dir) / "script" / "script.json", default=None)
    out: dict[str, str] = {}
    if not isinstance(script, dict):
        return out
    for act in script.get("acts") or []:
        for shot in (act.get("shots") or act.get("lines") or []) if isinstance(act, dict) else []:
            if isinstance(shot, dict) and shot.get("id") and shot.get("engine"):
                out[str(shot["id"])] = str(shot["engine"])
    return out


def declared(film_dir: Path, shots_doc: dict) -> dict[str, str]:
    """{shot id: engine} for every shot that names one: timing/shots.json first, then script/script.json by id."""
    from_script = script_engines(film_dir)
    out: dict[str, str] = {}
    for s in shots_doc.get("shots") or []:
        sid = str(s.get("id"))
        eng = s.get("engine") or from_script.get(sid)
        if eng:
            out[sid] = str(eng)
    return out


def shot_scene(film_dir: Path, shot: str, engine: str) -> Path:
    return Path(film_dir) / SHOT_SCENES / shot / engine


def scene_candidates(film_dir: Path) -> dict[str, dict[str, Path]]:
    """Keep every recognised source engine for each shot; competing folders must not be guessed between."""
    root = Path(film_dir) / SHOT_SCENES
    out: dict[str, dict[str, Path]] = {}
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            found: dict[str, Path] = {}
            web = folder / "web" / "index.html"
            if web.is_file():
                found["web"] = web
            for extension in SCENE_EXTENSIONS:
                remotion = folder / "remotion" / f"index{extension}"
                if remotion.is_file():
                    found["remotion"] = remotion
                    break
            if found:
                out[folder.name] = found
    return out


def scene_engines(film_dir: Path) -> dict[str, str]:
    """{shot id: engine} for unambiguous Web pages and Remotion modules; problems() reports competing sources."""
    return {shot: next(iter(found)) for shot, found in scene_candidates(film_dir).items() if len(found) == 1}


def act_engine(film_dir: Path, act: int) -> str | None:
    """The engine an act is built in: an AE scene, a Remotion module, or a web page; None while it isn't built."""
    film_dir = Path(film_dir)
    if (film_dir / "scenes" / "ae" / f"act{int(act)}.json").is_file():
        return "ae"
    folder = film_dir / "scenes" / f"act{int(act)}"
    if any((folder / f"index{ext}").is_file() for ext in SCENE_EXTENSIONS):
        return "remotion"
    if (folder / "web" / "index.html").is_file():
        return "web"
    return None


def reverse_available() -> bool:
    """Existing engine-version compatibility, never a picture approval or creative gate."""
    from slate.render import deps  # noqa: PLC0415 - source discovery stays independent of the Node bridge

    try:
        result = deps.bridge_call("version", {}, timeout=30)
    except SlateError:
        return False
    capabilities = result.get("capabilities") if isinstance(result, dict) else None
    marker = capabilities.get("reverse_web_remotion") if isinstance(capabilities, dict) else None
    return (isinstance(marker, dict) and type(marker.get("schema")) is int and marker["schema"] == 1 and
            marker.get("enabled") is True)


def require_reverse() -> None:
    """Keep reverse rendering unavailable until the installed engine exposes its implemented API."""
    if not reverse_available():
        raise Refused("A Remotion shot inside a web act needs the owning composition API; this engine does not "
                      "advertise reverse_web_remotion schema 1.",
                      hint="Use an engine with the complete reverse composition consumers wired.",
                      details={"capability": "reverse_web_remotion", "schema": 1})


def _resolved_rows(shots_doc: dict) -> list[dict]:
    if not isinstance(shots_doc, dict) or not isinstance(shots_doc.get("shots"), list):
        raise Refused("Shot ownership needs the resolved timing shots list.")
    raw = shots_doc["shots"]
    if any(not isinstance(shot, dict) for shot in raw):
        raise Refused("Shot ownership cannot be resolved from a non-object timing row.")
    for shot in raw:
        owner = shot.get("act")
        if not isinstance(owner, int) or isinstance(owner, bool) or owner <= 0:
            raise Refused("Shot ownership needs a positive whole owning act on every timing row.")
    ids = [shot.get("id") for shot in raw]
    if (any(not isinstance(shot, str) or not shot.strip() for shot in ids) or len(set(ids)) != len(ids)):
        raise Refused("Shot ownership needs nonempty, unique shot ids in the resolved timing list.")
    return raw


def owner_frames(shots_doc: dict, act_index: int) -> tuple[int, int]:
    """The owning act's film [in, out), following the primary Node actsOf resolved-shot order."""
    mine = [shot for shot in _resolved_rows(shots_doc) if shot["act"] == int(act_index)]
    if not mine:
        raise Refused(f"Act {act_index} has no resolved shots to own a composition.")
    for shot in mine:
        first, end = shot.get("frame_in"), shot.get("frame_out")
        if (not isinstance(first, int) or isinstance(first, bool) or
                not isinstance(end, int) or isinstance(end, bool) or first < 0 or end <= first):
            raise Refused(f"Shot {shot['id']}'s ownership needs its resolved forward film-frame interval.")
    mine.sort(key=lambda shot: shot["frame_in"])
    return mine[0]["frame_in"], mine[-1]["frame_out"]


def canonical_source(file: Path) -> Path:
    """Match Node captured-source identity, including missing candidates behind existing path aliases.

    A dangling candidate remains absent at its authored name. Following its missing symlink target would change the
    producer's deliberate-absence key and falsely reject a valid lower-precedence entry.
    """
    cursor = Path(abspath(file))
    tail = []
    try:
        while not cursor.exists():
            parent = cursor.parent
            if parent == cursor:
                break
            tail.append(cursor.name)
            cursor = parent
        return cursor.resolve(strict=True).joinpath(*reversed(tail))
    except (OSError, RuntimeError) as exc:
        raise Refused(f"Captured source identity for {file} could not be resolved: {exc}.") from exc


def routing_paths(film_dir: Path, shots_doc: dict, act_index: int) -> list[Path]:
    """Files whose presence can redirect this act's engine or replacement source; missing candidates are inputs too."""
    film_dir = Path(film_dir).resolve()
    paths = {film_dir / "script" / "script.json", film_dir / ".slate" / "refs.json",
             film_dir / "scenes" / "ae" / f"act{int(act_index)}.json",
             film_dir / "scenes" / f"act{int(act_index)}" / "web" / "index.html"}
    paths.update(film_dir / "scenes" / f"act{int(act_index)}" / f"index{ext}" for ext in SCENE_EXTENSIONS)
    for shot in _resolved_rows(shots_doc):
        if shot["act"] != int(act_index):
            continue
        paths.add(shot_scene(film_dir, shot["id"], "web") / "index.html")
        paths.update(shot_scene(film_dir, shot["id"], "remotion") / f"index{ext}" for ext in SCENE_EXTENSIONS)
    # The Node capture map names actual sources and missing candidates through their real parent.
    return sorted({canonical_source(file) for file in paths})


def remotion_hints(film_dir: Path) -> bool:
    """Conservative missing-timing detection: an unowned module or declaration cannot be assumed pure Web."""
    return (any("remotion" in found for found in scene_candidates(film_dir).values()) or
            any(engine == "remotion" for engine in script_engines(film_dir).values()))


def scene_for(film_dir: Path, shots_doc: dict, scene: Path, engine: str, act_index: int | None = None) -> ShotScene:
    """Resolve a supplied source folder to exactly one declared replacement before any engine operation."""
    film_dir, scene = Path(film_dir), Path(scene).resolve()
    rows = _resolved_rows(shots_doc)
    owners = {shot["act"] for shot in rows if shot_scene(film_dir, shot["id"], engine).resolve() == scene}
    if act_index is not None:
        owners &= {int(act_index)}
    found = [item for owner in sorted(owners) for item in mixed_scenes(film_dir, shots_doc, owner)
             if item.engine == engine and item.scene.resolve() == scene]
    if len(found) != 1:
        raise Refused(f"The {engine} scene {scene} is not one declared replacement in its owning act.")
    origin, end = owner_frames(shots_doc, found[0].act)
    first, last = found[0].record["frame_in"], found[0].record["frame_out"]
    if first < origin or last > end:
        raise Refused(f"Shot {found[0].shot} falls outside its owning act's film frames.")
    return found[0]


def problems(film_dir: Path, shots_doc: dict, *, include_unbuilt: bool = True) -> list[str]:
    """Source agreement and unavailable engine combinations; source-only mode never calls a child."""
    film_dir = Path(film_dir)
    decl = declared(film_dir, shots_doc)
    candidates = scene_candidates(film_dir)
    by_id = {str(s.get("id")): s for s in shots_doc.get("shots") or []}
    out: list[str] = []
    reverse: list[tuple[str, str]] = []
    for sid, eng in sorted(decl.items()):
        if eng not in ENGINES:
            out.append(f"shot {sid} names engine {eng!r}; a shot's engine is one of {', '.join(ENGINES)}")
            continue
        shot = by_id.get(sid)
        if shot is None:
            continue
        act = int(shot["act"])
        base = act_engine(film_dir, act)
        if eng == (base or "remotion"):
            continue
        if eng == "ae" or base == "ae":
            out.append(f"shot {sid} is in {eng} but act {act} is {base or 'not built yet'}: After Effects builds and "
                       "renders whole acts, so a shot can't move into or out of it (build the act in one engine)")
        elif eng == "remotion" and base == "web":
            reverse.append((sid, f"shot {sid} is in remotion inside a web act: a Remotion shot needs a composition "
                            "of its own, which this engine does not advertise as available "
                            "(reverse_web_remotion schema 1)"))
            if "remotion" not in candidates.get(sid, {}):
                out.append(f"shot {sid} says remotion, but there's no {SHOT_SCENES.as_posix()}/{sid}/remotion/index "
                           "module (.tsx, .ts, .jsx or .js)")
            try:
                origin, end = owner_frames(shots_doc, act)
                if shot["frame_in"] < origin or shot["frame_out"] > end:
                    out.append(f"shot {sid} falls outside its owning act's film frames")
            except SlateError as exc:
                out.append(f"shot {sid} has unresolved composition ownership: {exc.message}")
        elif eng == "web" and "web" not in candidates.get(sid, {}):
            out.append(f"shot {sid} says web, but there's no {SHOT_SCENES.as_posix()}/{sid}/web/index.html (copy the "
                       "web template there and make it draw that shot)")
    for sid, found in sorted(candidates.items()):
        if len(found) > 1:
            out.append(f"{SHOT_SCENES.as_posix()}/{sid}/ has competing {' and '.join(sorted(found))} scenes; "
                       "its declared engine and one scene must agree")
        if sid not in by_id:
            out.append(f"{SHOT_SCENES.as_posix()}/{sid}/ draws a shot the film doesn't have")
            continue
        for eng in sorted(found):
            if decl.get(sid) != eng:
                out.append(f"{SHOT_SCENES.as_posix()}/{sid}/{eng}/ is a {eng} scene, but shot {sid} doesn't say "
                           f"engine: {eng} (in script/script.json, carried to timing/shots.json)")
            elif act_engine(film_dir, int(by_id[sid]["act"])) == eng:
                own = "page" if eng == "web" else "module"
                out.append(f"{SHOT_SCENES.as_posix()}/{sid}/{eng}/ is never drawn: act {by_id[sid]['act']} is itself a "
                           f"{eng} act, so its own {own} draws shot {sid}")
    if include_unbuilt and reverse:
        invalid = {sid for sid, _ in reverse
                   if any(f"shot {sid} " in item or f"/{sid}/" in item for item in out)}
        # One version read for all valid reverse candidates; unrelated source faults do not mask availability.
        available = reverse_available() if any(sid not in invalid for sid, _ in reverse) else False
        out.extend(message for sid, message in reverse if sid in invalid or not available)
    return out


def mixed_scenes(film_dir: Path, shots_doc: dict, act_index: int, *, fallback_base: str | None = None) -> list[ShotScene]:
    """Resolve both code-engine directions for preparation and composition reads, without enabling rendering.

    Source-only resolution omits compatibility admission while retaining declaration/source agreement, AE and
    unknown-engine errors. It never reads engine version or starts a renderer.
    """
    film_dir = Path(film_dir)
    act_index = int(act_index)
    base = act_engine(film_dir, act_index) or fallback_base
    if base is None:
        raise MissingPrerequisite(f"Act {act_index} has no built scene, so its shot ownership cannot be resolved.")
    raw = _resolved_rows(shots_doc)
    mine = [shot for shot in raw if shot["act"] == act_index]
    mine_ids = {shot["id"] for shot in mine}
    found = problems(film_dir, shots_doc, include_unbuilt=False)
    relevant = [problem for problem in found
                if any(f"shot {sid} " in problem or f"/{sid}/" in problem for sid in mine_ids)]
    if relevant:
        raise Refused(f"Act {act_index}'s per-shot sources can't be resolved: {relevant[0]}.",
                      hint="slate check shows every problem with the film's shot engines.",
                      details={"problems": relevant})
    decl = declared(film_dir, shots_doc)
    candidates = scene_candidates(film_dir)
    selected = []
    for shot in mine:
        sid = str(shot["id"])
        engine = decl.get(sid)
        if not engine or engine == base:
            continue
        first, end = shot.get("frame_in"), shot.get("frame_out")
        if (not isinstance(first, int) or isinstance(first, bool) or
                not isinstance(end, int) or isinstance(end, bool) or first < 0 or end <= first):
            raise Refused(f"Shot {sid}'s ownership needs its resolved forward film-frame interval.")
        # AE/unknown combinations and source agreement were checked above; no unsupported source is guessed at.
        index = candidates[sid][engine]
        selected.append(ShotScene(sid, act_index, engine, base, index.parent, index, dict(shot)))
    return sorted(selected, key=lambda scene: (scene.record["frame_in"], scene.shot))


def descriptors(film_dir: Path, shots_doc: dict, act_index: int, act_frame_in: int) -> list[dict]:
    """Symmetric source descriptors at an explicitly supplied owning-act origin; still no renderer admission."""
    return [scene.descriptor(Path(film_dir), act_frame_in)
            for scene in mixed_scenes(film_dir, shots_doc, act_index)]


def segments(film_dir: Path, shots_doc: dict, act_index: int, act_frame_in: int, base: str) -> list[Segment]:
    """Replacement ranges at whole-act time; reverse admission needs the implemented Node capability."""
    film_dir = Path(film_dir)
    rows = _resolved_rows(shots_doc)
    mine = [shot for shot in rows if shot["act"] == int(act_index)]
    decl = declared(film_dir, shots_doc)
    found = problems(film_dir, shots_doc, include_unbuilt=False)
    ids = {shot["id"] for shot in mine}
    relevant = [item for item in found if any(f"shot {sid} " in item or f"/{sid}/" in item for sid in ids)]
    if relevant:
        raise Refused(f"Act {act_index}'s per-shot engines can't be drawn: {relevant[0]}.",
                      details={"problems": relevant})
    selected_ids = {shot["id"] for shot in mine if decl.get(shot["id"]) and decl[shot["id"]] != base}
    reverse = act_engine(film_dir, act_index) == "web" and any(decl.get(shot["id"]) == "remotion" for shot in mine)
    if not selected_ids and not reverse:
        return []  # Pure previews retain their existing route without needing a built scene or a new clock check.
    scenes = mixed_scenes(film_dir, shots_doc, act_index, fallback_base=base)
    origin, end = owner_frames(shots_doc, act_index)
    if type(act_frame_in) is not int or act_frame_in < 0 or (reverse and act_frame_in != origin):
        raise Refused(f"Act {act_index}'s reverse routing needs its resolved film-frame origin {origin}.")
    selected = [scene for scene in scenes if scene.shot in selected_ids]
    if selected and (base not in ("remotion", "web") or
                     any(scene.engine not in ("remotion", "web") for scene in selected)):
        raise UsageError(f"Act {act_index} cannot splice shots between {base} and this replacement engine.")
    for scene in scenes:
        if scene.record["frame_in"] < origin or scene.record["frame_out"] > end:
            raise Refused(f"Shot {scene.shot} falls outside its owning act's film frames.")
    if reverse:
        require_reverse()
    if any(scene.record["frame_in"] < act_frame_in for scene in selected):
        raise Refused("A replacement starts before the supplied owning-act origin.")
    return [Segment(scene.shot, scene.engine, scene.record["frame_in"] - act_frame_in,
                    scene.record["frame_out"] - 1 - act_frame_in, scene.scene) for scene in selected]


def segment_for(segs: list[Segment], first: int, last: int) -> Segment | None:
    """The segment a range of act frames lies inside, or None. Raises when it crosses a seam between engines."""
    for sg in segs:
        inside = sg.first <= first and last <= sg.last
        overlaps = first <= sg.last and sg.first <= last
        if inside:
            return sg
        if overlaps:
            raise Refused(f"Frames {first}–{last} cross the seam between engines at shot {sg.shot} (frames "
                          f"{sg.first}–{sg.last}, in {sg.engine}): a seam between engines is a cut, so a patch stays "
                          "on one side of it.",
                          hint=f"Patch frames {sg.first}–{sg.last} with --engine {sg.engine}, and the rest on their own.")
    return None
