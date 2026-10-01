"""What a render will do, decided before it does anything: which acts, which frames, which chunks, how many motion
blur samples, how much disk, and whether it runs here or in the queue.

Frame numbers, so nobody has to guess:

- An act's range is in *film* frames, `frame_in` inclusive and `frame_out` exclusive, exactly as `timing/shots.json`
  and the films' renders manifests had it.
- `first`/`last` (and `--from`/`--to` with `--act`) are the act render's *own* frame numbers, 0-based and inclusive,
  the same numbers the engine's `render` command and the splice take. Without `--act`, `--from`/`--to` are film frames,
  inclusive, and Slate works out which acts they fall in.

Nothing here knows a frame size or a frame rate: both come from film.json.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from slate import config
from slate.clock import Clock
from slate.errors import MissingPrerequisite, UsageError

ENGINES = ("remotion", "web", "ae")
# What each engine's chunks are, and so how they're joined: the code engines write lossless intermediates that are
# encoded to ProRes once. After Effects renders whole acts to ProRes (slate.ae.acts), so its one piece is the act.
CHUNK_CODEC = {"remotion": "lossless", "web": "lossless", "ae": "prores"}
# aerender renders a comp whole (slate.ae.acts), so an AE render is one piece: a whole act, never a range.
WHOLE_ACTS_ONLY = {"ae"}
CHUNK_EXT = {"lossless": ".mkv", "prores": ".mov"}

# ProRes 422 HQ targets about 220 Mb/s for HD at about thirty frames a second, which is about 3.5 bits per pixel per
# frame (Apple's ProRes white paper), and the budget per frame is the same at other rates. Rounded up: it reserves.
PRORES_HQ_BITS_PER_PIXEL = 3.6
# Lossless chunks relative to the ProRes they become. Flat UI frames come out 8 to 13 times smaller, grainy plates can
# come out bigger, so the reservation counts them as the same size.
LOSSLESS_VS_PRORES = 1.0
# A render bigger than this many sub-frames (frames x samples) always goes through the job queue. About a minute or
# two of rendering; anything longer shouldn't be tied to one agent's shell. Overridable in slate.toml [render].
DEFAULT_FOREGROUND_MAX_SUBFRAMES = 2400
# aerender has no sub-frame count; its cost is weighed as if it rendered this many samples.
AE_COST_SAMPLES = 16


@dataclass(frozen=True)
class Act:
    """One act as timing/shots.json has it: film frames [frame_in, frame_out)."""

    index: int
    frame_in: int
    frame_out: int
    first_shot: str
    last_shot: str

    @property
    def frames(self) -> int:
        return self.frame_out - self.frame_in

    @property
    def shots(self) -> str:
        return self.first_shot if self.first_shot == self.last_shot else f"{self.first_shot}-{self.last_shot}"


@dataclass
class Target:
    """One file a render produces.

    kind: "act" (a whole act, registered), "patch" (a range re-rendered and spliced into the registered act render),
    or "range" (a range written to a file of your choosing; not registered).
    """

    kind: str
    act: Act
    first: int
    last: int
    out: str  # film-relative when inside the film, otherwise absolute

    @property
    def frames(self) -> int:
        return self.last - self.first + 1

    @property
    def film_frames(self) -> list[int]:
        """[in, out) in film frames."""
        return [self.act.frame_in + self.first, self.act.frame_in + self.last + 1]

    def to_dict(self) -> dict:
        out = asdict(self)
        out["act"] = self.act.index
        out["frames"] = self.frames
        out["film_frames"] = self.film_frames
        out["shots"] = self.act.shots
        return out


@dataclass
class Plan:
    film: str
    engine: str
    samples: int | None
    draft: bool
    width: int
    height: int
    fps_num: int
    fps_den: int
    chunk_frames: int
    targets: list[Target] = field(default_factory=list)
    chunks: dict[str, list[list[int]]] = field(default_factory=dict)
    subframes: int = 0
    disk_gb: float = 0.0
    runs: str = "here"  # "here" or "queue"
    why: str = ""

    @property
    def clock(self) -> Clock:
        return Clock(self.fps_num, self.fps_den)

    def to_dict(self) -> dict:
        return {
            "film": self.film, "engine": self.engine, "samples": self.samples, "draft": self.draft,
            "width": self.width, "height": self.height, "fps_num": self.fps_num, "fps_den": self.fps_den,
            "chunk_frames": self.chunk_frames, "targets": [t.to_dict() for t in self.targets],
            "chunks": self.chunks, "subframes": self.subframes, "disk_gb": round(self.disk_gb, 3),
            "disk_bytes": int(round(self.disk_gb * 1e9)), "reserve_gb": ceil_gb(self.disk_gb),
            "runs": self.runs, "why": self.why,
        }


def render_settings() -> dict:
    return config.load().get("render", {})


def film_format(film: dict) -> tuple[int, int, int, int]:
    """(width, height, fps_num, fps_den) from film.json. Every one is required: Slate never guesses a format."""
    missing = [k for k in ("width", "height", "fps_num", "fps_den") if not film.get(k)]
    if missing:
        raise UsageError(f"film.json is missing {', '.join(missing)}; a render needs the film's size and frame rate.",
                         hint="Set them in film.json (slate new writes them from the format).")
    return int(film["width"]), int(film["height"]), int(film["fps_num"]), int(film["fps_den"])


def acts_from_shots(shots: dict) -> list[Act]:
    """Each act's film-frame range from shots.json, checked to run contiguously from frame 0 to total_frames."""
    by_id = {s.get("id"): s for s in shots.get("shots") or []}
    out: list[Act] = []
    for a in shots.get("acts") or []:
        ids = list(a.get("shots") or [])
        if not ids:
            continue
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise UsageError(f"timing/shots.json lists shots that aren't in it: {', '.join(missing)}.",
                             hint="Rebuild the timing with `slate timing`.")
        first, last = by_id[ids[0]], by_id[ids[-1]]
        out.append(Act(int(a["index"]), int(first["frame_in"]), int(last["frame_out"]), str(ids[0]), str(ids[-1])))
    if not out:
        raise MissingPrerequisite("timing/shots.json has no acts with shots yet, so there's nothing to render.",
                                  hint="slate timing")
    out.sort(key=lambda a: a.frame_in)
    problems = []
    if out[0].frame_in != 0:
        problems.append(f"the first act starts at frame {out[0].frame_in}, not 0")
    for prev, nxt in zip(out, out[1:]):
        if prev.frame_out != nxt.frame_in:
            problems.append(f"act {prev.index} ends at {prev.frame_out} but act {nxt.index} starts at {nxt.frame_in}")
    for a in out:
        if a.frame_out <= a.frame_in:
            problems.append(f"act {a.index} has no frames ({a.frame_in}–{a.frame_out})")
    total = shots.get("total_frames")
    if total is not None and out[-1].frame_out != int(total):
        problems.append(f"the last act ends at {out[-1].frame_out}, not total_frames {total}")
    if problems:
        raise UsageError("The acts in timing/shots.json aren't contiguous: " + "; ".join(problems) + ".",
                         hint="Rebuild the timing with `slate timing`; renders need acts that meet frame for frame.")
    return out


def find_act(acts: list[Act], index: int) -> Act:
    for a in acts:
        if a.index == index:
            return a
    known = ", ".join(str(a.index) for a in acts)
    raise UsageError(f"There's no act {index} in timing/shots.json (acts: {known}).")


def chunk_ranges(first: int, last: int, size: int) -> list[tuple[int, int]]:
    """Split [first, last] (inclusive) into consecutive chunks of at most `size` frames."""
    if size < 1:
        raise UsageError(f"render.chunk_frames must be at least 1 (it's {size}).")
    if first < 0 or last < first:
        raise UsageError(f"Can't chunk frames {first}–{last}: the range is empty or negative.")
    return [(start, min(start + size - 1, last)) for start in range(first, last + 1, size)]


def segments(film_first: int, film_last: int, acts: list[Act]) -> list[tuple[Act, int, int]]:
    """Film frames [film_first, film_last] (inclusive) as (act, first, last) in each act's own frame numbers."""
    if film_first < 0 or film_last < film_first:
        raise UsageError(f"--from {film_first} --to {film_last} isn't a range of frames.")
    end = acts[-1].frame_out - 1
    if film_last > end:
        raise UsageError(f"The film's last frame is {end}; --to {film_last} is past it.")
    out = []
    for a in acts:
        lo, hi = max(film_first, a.frame_in), min(film_last, a.frame_out - 1)
        if lo <= hi:
            out.append((a, lo - a.frame_in, hi - a.frame_in))
    return out


def route_of(film: dict) -> str | None:
    """The route a derived film was made for (film.json derivation.route: "hero" or "social", or the
    slate.formats.route_for dict carrying it), or None for a film that doesn't say."""
    derivation = film.get("derivation")
    route = derivation.get("route") if isinstance(derivation, dict) else None
    if isinstance(route, dict):
        route = route.get("route")
    return route if isinstance(route, str) and route else None


def resolve_samples(film: dict, *, engine: str, samples: int | None = None, draft: bool = False,
                    settings: dict | None = None) -> tuple[int | None, bool]:
    """(motion-blur samples, draft?) for a render.

    An explicit count wins. Draft mode (the film's mode, or `--draft`) renders with the draft count, 4 by default:
    animatics and quick variants, never delivered as final. A social cut-down (a derived film on formats' "social"
    route) renders with the draft count too, so it takes minutes, but it isn't a draft: it's delivered as itself.
    Otherwise the delivery bar, 16 (the "hero" route's render.motion_blur_samples). After Effects keeps its own motion
    blur, so an AE render has no sample count.
    """
    settings = settings if settings is not None else render_settings()
    is_draft = bool(draft) or film.get("mode") == "draft"
    if engine == "ae":
        if samples is not None:
            raise UsageError("--samples is for the remotion and web engines; After Effects renders with the comp's own "
                             "motion blur settings.")
        return None, is_draft
    if samples is not None:
        if int(samples) < 1:
            raise UsageError(f"--samples must be at least 1 (got {samples}).")
        return int(samples), is_draft
    fast = is_draft or route_of(film) == "social"
    key = "draft_samples" if fast else "motion_blur_samples"
    return int(settings.get(key, 4 if fast else 16)), is_draft


def bytes_per_frame(width: int, height: int) -> float:
    """Roughly how big one ProRes 422 HQ frame of this size is, for disk reservations."""
    return width * height * PRORES_HQ_BITS_PER_PIXEL / 8.0


def estimate_gb(targets: list[Target], width: int, height: int, *, registered_frames: dict[int, int] | None = None,
                chunk_codec: str = "lossless") -> float:
    """Disk a render needs at its peak: its chunks, the file they become and, for a splice, a new copy of the act.

    A splice writes the act's head and tail beside the patch, then the spliced act, before the old one goes: about
    two acts' worth on top of the patch itself.
    """
    per = bytes_per_frame(width, height)
    chunk_factor = LOSSLESS_VS_PRORES if chunk_codec == "lossless" else 1.0
    total = 0.0
    for t in targets:
        total += t.frames * per * (1.0 + chunk_factor)
        if t.kind == "patch":
            act_frames = (registered_frames or {}).get(t.act.index, t.act.frames)
            total += 2.0 * act_frames * per
    return total / 1e9


def cost_subframes(targets: list[Target], samples: int | None) -> int:
    return sum(t.frames * (samples or AE_COST_SAMPLES) for t in targets)


def where(plan: Plan, *, detach: bool, settings: dict | None = None) -> tuple[str, str]:
    """("here" | "queue", why). Long renders always go through the queue; --detach sends any render there."""
    if detach:
        return "queue", "--detach"
    settings = settings if settings is not None else render_settings()
    limit = int(settings.get("foreground_max_subframes", DEFAULT_FOREGROUND_MAX_SUBFRAMES))
    if plan.subframes > limit:
        return "queue", (f"{plan.subframes} sub-frames is past the {limit} a render may run in the foreground; "
                         "long renders always go through the queue")
    return "here", f"short ({plan.subframes} sub-frames)"


def default_out(film_dir: Path, act: Act, kind: str, first: int = 0, last: int = 0) -> str:
    if kind == "act":
        return f"renders/act{act.index}.mov"
    return f"renders/patches/act{act.index}_{first:05d}-{last:05d}.mov"


def rel_or_abs(film_dir: Path, path: str | Path) -> str:
    """A film-relative POSIX path when the file is inside the film, otherwise the absolute path."""
    p = Path(path).expanduser()
    full = p if p.is_absolute() else (Path(film_dir) / p)
    try:
        return full.resolve().relative_to(Path(film_dir).resolve()).as_posix()
    except ValueError:
        return str(full.resolve())


def build(film_dir: Path, film: dict, shots: dict, *, act: int | None = None, frames: tuple[int, int] | None = None,
          engine: str = "remotion", samples: int | None = None, draft: bool = False, out: str | Path | None = None,
          detach: bool = False, registered: dict[int, dict] | None = None, settings: dict | None = None) -> Plan:
    """Work out a render without touching anything. `registered` maps act -> registry entry (for patches)."""
    if engine not in ENGINES:
        raise UsageError(f"Unknown engine {engine!r}: use one of {', '.join(ENGINES)}.")
    settings = settings if settings is not None else render_settings()
    width, height, fps_num, fps_den = film_format(film)
    for key, want in (("fps_num", fps_num), ("fps_den", fps_den)):
        have = shots.get(key)
        if have is not None and int(have) != want:
            raise UsageError(f"timing/shots.json was timed at {shots.get('fps_num')}/{shots.get('fps_den')} fps but "
                             f"film.json says {fps_num}/{fps_den}.", hint="Rebuild the timing with `slate timing`.")
    acts = acts_from_shots(shots)
    registered = registered or {}
    chosen, is_draft = resolve_samples(film, engine=engine, samples=samples, draft=draft, settings=settings)
    targets: list[Target] = []
    if frames is None:
        if out is not None and act is None:
            raise UsageError("--out writes one file, so it needs --act (or --from/--to inside one act).")
        for a in ([find_act(acts, act)] if act is not None else acts):
            dest = rel_or_abs(film_dir, out) if out is not None else default_out(film_dir, a, "act")
            targets.append(Target("range" if out is not None else "act", a, 0, a.frames - 1, dest))
    else:
        first, last = int(frames[0]), int(frames[1])
        if act is not None:
            a = find_act(acts, act)
            if first < 0 or last < first or last >= a.frames:
                raise UsageError(f"Act {act} has frames 0–{a.frames - 1}; --from {first} --to {last} isn't inside "
                                 "it.",
                                 hint="With --act, --from/--to are the act render's own frame numbers (inclusive).")
            parts = [(a, first, last)]
        else:
            parts = segments(first, last, acts)
        if out is not None and len(parts) != 1:
            raise UsageError("--out writes one file, but that range crosses acts; render each act's part on its own.")
        for a, lo, hi in parts:
            if out is not None:
                targets.append(Target("range", a, lo, hi, rel_or_abs(film_dir, out)))
            elif lo == 0 and hi == a.frames - 1:
                # the whole act: a fresh act render, not a splice of every frame
                targets.append(Target("act", a, lo, hi, default_out(film_dir, a, "act")))
            else:
                targets.append(Target("patch", a, lo, hi, default_out(film_dir, a, "patch", lo, hi)))
    if engine in WHOLE_ACTS_ONLY:
        partial = [t for t in targets if not (t.first == 0 and t.last == t.act.frames - 1)]
        if partial:
            t = partial[0]
            raise UsageError(f"After Effects renders whole acts, so act {t.act.index} frames {t.first}–{t.last} "
                             "can't be rendered or patched on their own.",
                             hint=f"Render the whole act again: slate render --engine ae --act {t.act.index} --go")
    size = int(settings.get("chunk_frames", 600))
    if engine in WHOLE_ACTS_ONLY:
        size = max(t.frames for t in targets)  # one pass per act
    plan = Plan(str(film_dir), engine, chosen, is_draft, width, height, fps_num, fps_den, size, targets)
    for t in targets:
        plan.chunks[f"act{t.act.index}:{t.first}-{t.last}"] = [list(c) for c in chunk_ranges(t.first, t.last, size)]
    plan.subframes = cost_subframes(targets, chosen)
    reg_frames = {k: int(v["frames"][1]) - int(v["frames"][0]) for k, v in registered.items() if v.get("frames")}
    plan.disk_gb = estimate_gb(targets, width, height, registered_frames=reg_frames, chunk_codec=CHUNK_CODEC[engine])
    plan.runs, plan.why = where(plan, detach=detach, settings=settings)
    return plan


def ceil_gb(gb: float) -> float:
    """Reservations in tenths of a gigabyte, rounded up (a reservation never rounds down)."""
    return math.ceil(gb * 10.0) / 10.0
