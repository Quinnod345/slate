"""The film as finishing reads it: clock, size, acts and the renders standing for them, the mix, stems, captions,
versions, and the fingerprint that says whether the content moved on.

Everything is read through slate.qa's FilmContext, so finishing sees exactly what the gates see (the same current
render per act, the same mix, the same masters). Nothing here writes into the film.

Frame rates and sizes come from film.json and timing/shots.json only. A film without them is a usage error that
names the fix; nothing is ever assumed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slate import clock as clock_mod
from slate import jsonio
from slate.errors import MissingPrerequisite, UsageError
from slate.finish import deps


def judged_mode(film_dir: Path | None, data: dict | None) -> str:
    """slate.state.autonomy.film_mode, or film.json's own mode without the state module."""
    own = str((data or {}).get("mode") or "supervised")
    try:
        from slate.state import autonomy  # noqa: PLC0415 - finish works without film state installed
    except ImportError:
        return own
    return autonomy.film_mode(film_dir, data if isinstance(data, dict) else None)

MASTER_RECORD = "masters/master.json"
NOTES_FILE = "review/notes.json"
REVIEW_DIR = "review"
DELIVER_DIR = "deliver"


def context(film_dir: Path):
    """A fresh slate.qa FilmContext for this film (fresh, so a file written a moment ago is seen)."""
    mod = deps.module("slate.qa.film")
    if mod is None:
        raise MissingPrerequisite("Finishing reads the film through slate.qa, which isn't installed.",
                                  hint="slate doctor names what's missing.")
    return mod.FilmContext(Path(film_dir))


@dataclass
class Film:
    """One film folder, read for finishing."""

    dir: Path

    def __post_init__(self) -> None:
        self.dir = Path(self.dir).resolve()
        self.ctx = context(self.dir)
        self.data = deps.film_load(self.dir)

    # ------------------------------------------------------------------ identity
    @property
    def id(self) -> str:
        return str(self.data.get("id") or self.dir.name)

    @property
    def title(self) -> str:
        return str(self.data.get("title") or self.id)

    @property
    def mode(self) -> str:
        """The mode the film is judged in: autonomous whenever film.json or Slate's own record says it was made
        autonomous (slate.state.autonomy, which only a person at their own Terminal changes), else film.json's. So an
        edited film.json can't route an autonomous film's review, notes or delivery as a supervised one's."""
        return judged_mode(self.dir, self.data)

    @property
    def brand(self) -> str | None:
        b = self.data.get("brand")
        return (b.get("name") or b.get("id")) if isinstance(b, dict) else b

    @property
    def language(self) -> str:
        return str(self.data.get("language") or "en")

    def rel(self, p: str | os.PathLike) -> str:
        return self.ctx.rel(p)

    def path(self, rel: str | os.PathLike) -> Path:
        return self.ctx.path(rel)

    # ------------------------------------------------------------------ clock and size
    @property
    def clock(self) -> clock_mod.Clock:
        c = self.ctx.clock
        if c is None:
            raise UsageError("film.json has no frame rate (fps_num and fps_den); Slate never assumes one.",
                             hint="slate film set fps_num N and fps_den D, or re-create the film from its format.")
        return c

    @property
    def size(self) -> tuple[int, int]:
        s = self.ctx.size
        if not s:
            raise UsageError("film.json has no width and height; Slate never assumes a frame size.")
        return s

    @property
    def rate(self) -> str:
        return f"{self.clock.fps_num}/{self.clock.fps_den}"

    @property
    def total_frames(self) -> int:
        n = self.ctx.total_frames
        if not n:
            raise MissingPrerequisite("This film has no timing yet (timing/shots.json).", hint="slate timing")
        return int(n)

    @property
    def seconds(self) -> float:
        return self.clock.secs_of(self.total_frames)

    def timecode(self, frame: int) -> str:
        return self.clock.timecode(int(frame))

    # ------------------------------------------------------------------ shots, acts, beats
    @property
    def shots(self) -> list[dict]:
        return self.ctx.shots

    @property
    def acts(self) -> list[dict]:
        return self.ctx.acts

    def shot_at(self, frame: int) -> dict | None:
        for s in self.shots:
            if int(s.get("frame_in", -1)) <= frame < int(s.get("frame_out", -1)):
                return s
        return None

    def shot_ranges(self) -> list[tuple[int, int, str]]:
        """(frame_in, frame_out exclusive, shot id) in film order."""
        return [(int(s["frame_in"]), int(s["frame_out"]), str(s.get("id"))) for s in self.shots
                if "frame_in" in s and "frame_out" in s]

    def neighbours(self, shot_id: str) -> tuple[str | None, str | None]:
        ids = [str(s.get("id")) for s in self.shots]
        if shot_id not in ids:
            return None, None
        i = ids.index(shot_id)
        return (ids[i - 1] if i > 0 else None), (ids[i + 1] if i + 1 < len(ids) else None)

    def beats(self) -> list[dict]:
        return self.ctx.beats()

    # ------------------------------------------------------------------ picture and sound
    def act_renders(self) -> list[dict]:
        """The render standing for each act, in film order, proven to tile the film exactly (no gap, no overlap).

        Raises MissingPrerequisite naming the acts that have no whole-act render (the next command renders them).
        """
        acts = self.acts
        if not acts:
            raise MissingPrerequisite("This film has no acts in timing/shots.json yet.", hint="slate timing")
        current = self.ctx.current_renders()
        missing = [a["index"] for a in acts if a["index"] not in current or not current[a["index"]]["path"].is_file()]
        if missing:
            listed = ", ".join(str(m) for m in missing)
            many = len(missing) > 1
            raise MissingPrerequisite(f"Act{'s' if many else ''} {listed} ha{'ve' if many else 's'} no whole-act "
                                      "render yet.",
                                      hint=f"slate render --act {missing[0]}" if len(missing) == 1 else "slate render")
        out, at = [], 0
        for a in acts:
            e = current[a["index"]]
            if int(a["frame_in"]) != at:
                raise UsageError(f"Act {a['index']} starts at frame {a['frame_in']}, but the act before ends at {at}: "
                                 "the acts don't tile the film.", hint="slate timing rebuilds the shots.")
            out.append({"act": a["index"], "path": Path(e["path"]), "file": self.rel(e["path"]),
                        "frame_in": int(a["frame_in"]), "frame_out": int(a["frame_out"]),
                        "sha256": e.get("sha256"), "engine": e.get("engine")})
            at = int(a["frame_out"])
        if at != self.total_frames:
            raise UsageError(f"The acts cover {at} frames; the film has {self.total_frames}.",
                             hint="slate timing rebuilds the shots.")
        return out

    def mix(self) -> Path | None:
        return self.ctx.mix_path()

    def mix_record(self) -> Path | None:
        """The mix's own report (sound/mix/final_mix.json beside the mix), when there is one."""
        mix = self.mix()
        if mix is not None:
            cand = mix.with_suffix(".json")
            if cand.is_file():
                return cand
        rec, _doc = self.ctx.mix_record()
        return self.path(rec) if rec else None

    def stems(self) -> dict[str, Path]:
        return {k: p for k, p in self.ctx.stem_paths().items() if Path(p).is_file()}

    def captions(self) -> list[Path]:
        """Every caption file the film has: the machine cues (captions.json), SRT and WebVTT."""
        return self.ctx.caption_paths(include_delivery=False)

    # ------------------------------------------------------------------ masters
    def master_record(self) -> dict | None:
        rec = jsonio.read(self.path(MASTER_RECORD), default=None)
        return rec if isinstance(rec, dict) else None

    def masters(self) -> list[dict]:
        """The masters finishing made, from masters/master.json (with a `path` and whether the file still matches)."""
        rec = self.master_record() or {}
        out = []
        for m in rec.get("masters") or []:
            p = self.path(m["file"])
            now = jsonio.sha256(p) if p.is_file() else None
            out.append({**m, "path": p, "intact": now is not None and now == m.get("sha256")})
        return out

    def master_inputs_now(self) -> dict[str, str | None]:
        """{film-relative path: sha256} of what a master is made from right now: the act renders and the mix."""
        mix = self.mix()
        return self.ctx.inputs([r["path"] for r in self.act_renders()] + ([mix] if mix else []))

    def master_current(self) -> tuple[bool, str]:
        """(True, "") when masters/master.json was made from exactly today's act renders and mix; else the reason.

        Captions and stems are delivered beside the masters, so changing them never makes a master stale."""
        rec = self.master_record()
        if not rec:
            return False, "there's no master yet"
        try:
            now = self.master_inputs_now()
        except (MissingPrerequisite, UsageError) as exc:
            return False, exc.message
        if (rec.get("inputs") or {}) != now:
            return False, "the masters are older than the film's current renders or mix"
        return True, ""

    def primary_master(self) -> dict | None:
        """The mezzanine master (ProRes) when there is one, else the first."""
        ms = [m for m in self.masters() if m["path"].is_file()]
        for m in ms:
            if str(m.get("codec") or "").startswith("prores") or m.get("kind") == "prores_422_hq":
                return m
        return ms[0] if ms else None

    # ------------------------------------------------------------------ content fingerprint
    def content(self) -> dict[str, str | None]:
        """{film-relative path: sha256} of what the film's picture and sound are made of now: the act renders
        standing for each act, the mix, its stems, the shots and the captions. Masters, cuts and sheets are derived
        from these (and proven against them), so they aren't part of it."""
        paths: list[Any] = ["timing/shots.json"]
        try:
            paths += [r["path"] for r in self.act_renders()]
        except (MissingPrerequisite, UsageError):
            paths += [e["path"] for e in self.ctx.current_renders().values()]
        mix = self.mix()
        if mix is not None:
            paths.append(mix)
        paths += list(self.stems().values())
        paths += self.captions()
        return self.ctx.inputs(paths)

    def fingerprint(self) -> str:
        return jsonio.sha256_obj(self.content())

    # ------------------------------------------------------------------ versions
    def version(self) -> int:
        return deps.current_version(self.dir)

    def review_dir(self, n: int) -> Path:
        return self.path(f"{REVIEW_DIR}/v{int(n)}")

    def deliver_dir(self, n: int) -> Path:
        return self.path(f"{DELIVER_DIR}/v{int(n)}")


def load(film_dir: Path) -> Film:
    return Film(Path(film_dir))
