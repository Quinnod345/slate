"""Per-shot engines: a shot may carry its own engine, and by default it's drawn by its act's (P074).

The engine is chosen per brand (brand.json `look.engine`) and, where one shot needs something its act's engine can't
give, per shot. What's built, and the rules that keep it safe:

- **Declaring it.** A shot's `engine` (remotion, web or ae) in script/script.json, where a person writes it, carried
  through script/storyboard.json to timing/shots.json by `slate script build` and `slate timing`. Render and check
  read shots.json first, then the script by shot id (a storyboard built before it carried engines). A shot that names
  no engine is drawn by its act's.
- **Where its scene lives.** A web shot inside a Remotion act is a page of its own, `scenes/shots/<SHOT>/web/
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

Not built yet, so refused by name rather than guessed at (engine/README.md, docs/ARCHITECTURE.md §8):

- an After Effects shot inside a code-engine act, or a code-engine shot inside an AE-built act: aerender renders whole
  comps, and the AE project builds whole acts, so the AE lane works per act (`scenes/ae/actN.json`);
- a Remotion shot inside a web act: it needs a composition of its own in the generated entry (`Shot-<ID>`, drawn at
  act time), which the entry doesn't make yet;
- a web shot's tweens in the act's motion trace: the renderer writes a web scene's trace for its whole act, so a
  shot's render passes `trace: false`, and `slate qa motion` doesn't see that shot's motion yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from slate import jsonio
from slate.errors import MissingPrerequisite, Refused, UsageError

ENGINES = ("remotion", "web", "ae")
SHOT_SCENES = Path("scenes") / "shots"


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


def scene_engines(film_dir: Path) -> dict[str, str]:
    """{shot id: engine} for the shot scenes on disk (scenes/shots/<SHOT>/web/index.html)."""
    root = Path(film_dir) / SHOT_SCENES
    out: dict[str, str] = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if (d / "web" / "index.html").is_file():
                out[d.name] = "web"
    return out


def act_engine(film_dir: Path, act: int) -> str | None:
    """The engine an act is built in: an AE scene, a Remotion module, or a web page; None while it isn't built."""
    film_dir = Path(film_dir)
    if (film_dir / "scenes" / "ae" / f"act{int(act)}.json").is_file():
        return "ae"
    folder = film_dir / "scenes" / f"act{int(act)}"
    if any((folder / f"index{ext}").is_file() for ext in (".tsx", ".ts", ".jsx", ".js")):
        return "remotion"
    if (folder / "web" / "index.html").is_file():
        return "web"
    return None


def problems(film_dir: Path, shots_doc: dict) -> list[str]:
    """Everything wrong with the film's per-shot engines: an engine Slate doesn't know, a declaration with no scene, a
    scene with no declaration, and the combinations that aren't built yet. Empty when every shot is drawn safely."""
    film_dir = Path(film_dir)
    decl = declared(film_dir, shots_doc)
    scenes = scene_engines(film_dir)
    by_id = {str(s.get("id")): s for s in shots_doc.get("shots") or []}
    out: list[str] = []
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
            continue  # the act's own engine: nothing to do per shot
        if eng == "ae" or base == "ae":
            out.append(f"shot {sid} is in {eng} but act {act} is {base or 'not built yet'}: After Effects builds and "
                       "renders whole acts, so a shot can't move into or out of it (build the act in one engine)")
        elif eng == "remotion" and base == "web":
            out.append(f"shot {sid} is in remotion inside a web act: a Remotion shot needs a composition of its own, "
                       "which the engine's entry doesn't make yet (build the act in one engine, or make the act "
                       "Remotion and the other shots web)")
        elif eng == "web" and scenes.get(sid) != "web":
            out.append(f"shot {sid} says web, but there's no {SHOT_SCENES.as_posix()}/{sid}/web/index.html (copy the "
                       "web template there and make it draw that shot)")
    for sid, eng in sorted(scenes.items()):
        if sid not in by_id:
            out.append(f"{SHOT_SCENES.as_posix()}/{sid}/ draws a shot the film doesn't have")
        elif decl.get(sid) != eng:
            out.append(f"{SHOT_SCENES.as_posix()}/{sid}/{eng}/ is a {eng} scene, but shot {sid} doesn't say "
                       f"engine: {eng} (in script/script.json, carried to timing/shots.json)")
        elif act_engine(film_dir, int(by_id[sid]["act"])) == eng:
            out.append(f"{SHOT_SCENES.as_posix()}/{sid}/{eng}/ is never drawn: act {by_id[sid]['act']} is itself a "
                       f"{eng} act, so its own page draws shot {sid}")
    return out


def segments(film_dir: Path, shots_doc: dict, act_index: int, act_frame_in: int, base: str) -> list[Segment]:
    """The shots of one act drawn by another engine than `base` (the engine the act renders in), in act frames.
    Refuses when the film's per-shot engines have a problem that touches this act."""
    film_dir = Path(film_dir)
    decl = declared(film_dir, shots_doc)
    found = problems(film_dir, shots_doc)
    mine = [s for s in shots_doc.get("shots") or [] if int(s.get("act", -1)) == int(act_index)]
    ids = {str(s["id"]) for s in mine}
    relevant = [p for p in found if any(f"shot {sid} " in p or f"/{sid}/" in p for sid in ids)]
    if relevant:
        raise Refused(f"Act {act_index}'s per-shot engines can't be drawn: {relevant[0]}.",
                      hint="slate check shows every problem with the film's shot engines.",
                      details={"problems": relevant})
    out: list[Segment] = []
    for s in sorted(mine, key=lambda x: int(x["frame_in"])):
        eng = decl.get(str(s["id"]))
        if not eng or eng == base:
            continue
        if base != "remotion" or eng != "web":
            raise UsageError(f"Act {act_index} renders in {base}, and shot {s['id']} in {eng}: only web shots inside "
                             "a Remotion act are built (see slate.render.shot_engines).")
        scene = shot_scene(film_dir, str(s["id"]), eng)
        if not (scene / "index.html").is_file():
            raise MissingPrerequisite(f"Shot {s['id']} is in web, but {scene / 'index.html'} isn't there.")
        out.append(Segment(str(s["id"]), eng, int(s["frame_in"]) - int(act_frame_in),
                           int(s["frame_out"]) - 1 - int(act_frame_in), scene))
    return out


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
