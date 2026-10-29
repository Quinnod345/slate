"""A read-only view of one film folder, shared by every gate in a `slate qa` run.

Every gate takes the film folder and nothing else (the films' tools each had their own idea of where things were:
`build/`, `build/B`, `FILM=C`, hard-coded paths, the frame size typed into a reshape). This module is the one place
that knows the film folder's layout (docs/ARCHITECTURE.md §5), reads the clock and the frame size from film.json,
and hashes each input once per run, so a ten-gigabyte master isn't read ten times.

Nothing here writes into the film folder. Where a file's location isn't fixed by the architecture yet (the mix, the
stems, the captions, the masters), each locator tries a short, documented list of places in order and says which
one it used.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Iterator

from slate import clock as _clock
from slate import gates, jsonio
from slate.errors import SlateError

# Visual-event kinds (docs/ARCHITECTURE.md §5). The sound engine maps each kind to a sound, so an unknown kind would
# land silently: it is a problem in the events file, not a new kind.
EVENT_KINDS = ("tap", "type", "land", "snap", "count", "draw", "data", "page", "stamp", "whoosh", "swell",
               "character", "impact")

# The sound module writes the mix to sound/mix/final_mix.wav with its four delivery stems beside it in
# sound/mix/stems/. (sound/stems/ holds the effects engine's own role stems, which are not delivery stems.)
MIX_CANDIDATES = ("sound/mix/final_mix.wav", "sound/final_mix.wav", "sound/mix.wav", "sound/mix/mix.wav")
# `slate mix` records the mix in sound/mix/final_mix.json (its stems under outputs, and `empty_stems`: the delivery stems
# a film has nothing for, which are written silent on purpose).
MIX_RECORDS = ("sound/mix.json", "sound/mix/mix.json", "sound/mix/final_mix.json")
VERIFY_RECORDS = ("sound/verify.json", "sound/mix/verify.json")
CAPTION_CANDIDATES = ("timing/captions.json", "voice/captions/captions.json", "voice/captions.json",
                      "captions/captions.json", "deliver/captions/captions.json")
CAPTION_DIRS = ("voice/captions", "captions", "deliver/captions", "deliver")
MASTER_DIR = "masters"
MASTER_RECORDS = ("masters/masters.json", "masters/master.json")
VIDEO_EXT = (".mov", ".mp4", ".mxf", ".mkv", ".m4v")
DIALOGUE_STEMS = ("dialogue", "voice", "vo")


@dataclass
class ActEvents:
    """One act's visual-events file, parsed and checked against the contract."""

    act: int
    path: Path
    exists: bool = False
    silent: bool = False
    reason: str | None = None
    events: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exists and not self.problems


class FilmContext:
    """Everything a gate reads from one film folder, loaded lazily and cached for the run."""

    def __init__(self, film_dir: str | os.PathLike, *, quick: bool = False):
        self.dir = Path(film_dir).expanduser().resolve()
        self.quick = quick  # --quick: reuse cached whole-picture work (the OCR sweep), never start it
        self._json: dict[str, Any] = {}
        self._sha: dict[tuple[str, int, int], str] = {}
        self._memo: dict[str, Any] = {}

    # ------------------------------------------------------------------ files and hashes
    def path(self, rel: str | os.PathLike) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.dir / p

    def rel(self, p: str | os.PathLike) -> str:
        full = Path(p)
        full = full if full.is_absolute() else self.dir / full
        try:
            return str(full.resolve().relative_to(self.dir))
        except ValueError:
            return str(full)

    def source_path(self, rel: str | os.PathLike) -> Path:
        """Resolve a missing relative input by reference. path() remains the child's output destination."""
        own = self.path(rel)
        if own.exists() or Path(rel).is_absolute():
            return own
        try:
            from slate.state import lock  # noqa: PLC0415 - film state is optional for read-only QA
        except ImportError:
            return own
        return Path(lock.resolve(self.dir, str(rel)))

    def _reference_names(self, directory: str | Path, suffix: str) -> set[str]:
        """Names explicitly referenced in one logical input folder; never discover its parent's neighbours."""
        try:
            from slate.state import lock  # noqa: PLC0415 - state is optional for read-only QA
        except ImportError:
            return set()
        refs = lock.load(self.dir).get("files") or {}
        return {Path(rel).name for rel, entry in refs.items()
                if entry.get("ref") and Path(rel).parent == Path(directory) and Path(rel).suffix == suffix
                and not Path(rel).name.startswith(".")}

    def exists(self, rel: str | os.PathLike) -> bool:
        return self.source_path(rel).is_file()

    def read_json(self, rel: str | os.PathLike, default: Any = None) -> Any:
        """A JSON file in the film, cached. Missing gives `default`; broken JSON raises UsageError (gates report it)."""
        key = self.rel(rel)
        if key not in self._json:
            p = self.source_path(rel)
            self._json[key] = jsonio.read(p) if p.is_file() else None
        value = self._json[key]
        return default if value is None else value

    def sha(self, p: str | os.PathLike) -> str | None:
        full = self.source_path(p)
        try:
            st = full.stat()
        except OSError:
            return None
        if not full.is_file():
            return None
        key = (str(full.resolve()), st.st_size, st.st_mtime_ns)
        if key not in self._sha:
            self._sha[key] = jsonio.sha256(full)
        return self._sha[key]

    def inputs(self, paths: Iterable[str | os.PathLike | None]) -> dict[str, str | None]:
        """{film-relative path: sha256 or None} for what a gate read, including files it expected but didn't find
        (None), so the result turns STALE when they appear or change.

        film.json is recorded as `film:*` (all of it except its bookkeeping), and named inputs (`film:…`, `brand:…`,
        `format:…`) are hashed the way slate.state.lock defines them, so a pin, a new version or a budget raise
        written to film.json doesn't turn the gate STALE."""
        out: dict[str, str | None] = {}
        named: list[str] = []
        for p in gates.expand_film_json(self.dir, [p for p in paths if p is not None]):
            if gates.is_named(p, self.dir):
                named.append(str(p))
                out[str(p)] = None
                continue
            out[self.rel(p)] = self.sha(p)
        if named:
            out.update(gates.inputs_for(self.dir, named))
        return out

    # ------------------------------------------------------------------ film.json and the clock
    @property
    def film(self) -> dict:
        data = self.read_json("film.json")
        if data is None:
            raise SlateError(f"{self.dir} has no film.json, so it isn't a film folder.")
        return data

    @property
    def film_id(self) -> str:
        return str(self.film.get("id") or self.dir.name)

    @property
    def mode(self) -> str:
        """The mode the film is judged in: autonomous whenever film.json or Slate's own record says it was made
        autonomous (slate.state.autonomy), else film.json's. The claims check is strict for it either way."""
        own = str(self.film.get("mode") or "supervised")
        try:
            from slate.state import autonomy  # noqa: PLC0415 - qa runs without film state installed
        except ImportError:
            return own
        return autonomy.film_mode(self.dir, self.film)

    @property
    def targets(self) -> dict:
        return dict(self.film.get("targets") or {})

    @property
    def brand(self) -> str | None:
        b = self.film.get("brand")
        if isinstance(b, dict):
            return b.get("name") or b.get("id")
        return b

    def _rational(self) -> Fraction | None:
        for src in (self.film, self.shots_doc or {}):
            num, den = src.get("fps_num"), src.get("fps_den")
            if num and den:
                return Fraction(int(num), int(den))
        return None

    @property
    def fps(self) -> Fraction | None:
        """The film's frame rate as an exact fraction (film.json first, then shots.json), or None."""
        return self._rational()

    @property
    def clock(self) -> _clock.Clock | None:
        r = self.fps
        return _clock.Clock(r.numerator, r.denominator) if r else None

    @property
    def frame_seconds(self) -> float | None:
        r = self.fps
        return float(1 / r) if r else None

    @property
    def size(self) -> tuple[int, int] | None:
        for src in (self.film, self.shots_doc or {}):
            w, h = src.get("width"), src.get("height")
            if w and h:
                return int(w), int(h)
        return None

    def frame_of(self, seconds: float) -> int:
        c = self.clock
        if c is None:
            raise SlateError("film.json has no frame rate (fps_num / fps_den).")
        return c.frame_of(seconds)

    def secs_of(self, frame: int) -> float:
        c = self.clock
        if c is None:
            raise SlateError("film.json has no frame rate (fps_num / fps_den).")
        return c.secs_of(frame)

    # ------------------------------------------------------------------ timing
    SHOTS = "timing/shots.json"

    @property
    def shots_doc(self) -> dict | None:
        return self.read_json(self.SHOTS)

    @property
    def shots(self) -> list[dict]:
        doc = self.shots_doc or {}
        return sorted((s for s in doc.get("shots") or [] if isinstance(s, dict)), key=lambda s: s.get("frame_in", 0))

    @property
    def shot_by_id(self) -> dict[str, dict]:
        return {str(s.get("id")): s for s in self.shots}

    @property
    def total_frames(self) -> int | None:
        doc = self.shots_doc or {}
        if doc.get("total_frames") is not None:
            return int(doc["total_frames"])
        shots = self.shots
        return int(shots[-1]["frame_out"]) if shots and "frame_out" in shots[-1] else None

    @property
    def acts(self) -> list[dict]:
        """Acts as {index, name, shots, frame_in, frame_out} in film order, from shots.json's `acts` (or, failing
        that, from the `act` number on each shot)."""
        if "acts" in self._memo:
            return self._memo["acts"]
        doc = self.shots_doc or {}
        by_act: dict[int, list[dict]] = {}
        for s in self.shots:
            if s.get("act") is not None:
                by_act.setdefault(int(s["act"]), []).append(s)
        acts = []
        declared = [a for a in doc.get("acts") or [] if isinstance(a, dict)]
        if declared:
            for i, a in enumerate(declared, start=1):
                index = int(a.get("index", i))
                ids = [str(x) for x in a.get("shots") or []]
                members = [s for s in self.shots if str(s.get("id")) in ids] or by_act.get(index, [])
                acts.append(self._act(index, a.get("name"), members))
        else:
            for index in sorted(by_act):
                acts.append(self._act(index, None, by_act[index]))
        self._memo["acts"] = acts
        return acts

    @staticmethod
    def _act(index: int, name: str | None, members: list[dict]) -> dict:
        fin = min((int(s["frame_in"]) for s in members if "frame_in" in s), default=None)
        fout = max((int(s["frame_out"]) for s in members if "frame_out" in s), default=None)
        return {"index": index, "name": name or f"Act {index}", "shots": [str(s.get("id")) for s in members],
                "frame_in": fin, "frame_out": fout}

    def act_of_frame(self, frame: int) -> int | None:
        for a in self.acts:
            if a["frame_in"] is not None and a["frame_in"] <= frame < a["frame_out"]:
                return a["index"]
        return None

    def shot_of_frame(self, frame: int) -> str | None:
        for s in self.shots:
            if int(s.get("frame_in", -1)) <= frame < int(s.get("frame_out", -1)):
                return str(s.get("id"))
        return None

    def events_rel(self, act: int) -> str:
        return f"timing/visual_events/act{act}.json"

    def act_events(self, act: dict | int) -> ActEvents:
        """Parse and check one act's visual events: a list of beats, or {"silent": true, "reason": "…"}."""
        a = act if isinstance(act, dict) else next((x for x in self.acts if x["index"] == int(act)), None)
        index = int(a["index"]) if a else int(act)  # type: ignore[arg-type]
        key = f"events:{index}"
        if key in self._memo:
            return self._memo[key]
        rel = self.events_rel(index)
        out = ActEvents(index, self.source_path(rel))
        if not out.path.is_file():
            self._memo[key] = out
            return out
        out.exists = True
        try:
            data = jsonio.read(out.path)
        except SlateError as exc:
            out.problems.append(str(exc))
            self._memo[key] = out
            return out
        if isinstance(data, dict):
            if data.get("silent") is True:
                out.silent = True
                out.reason = str(data.get("reason") or "").strip() or None
                if not out.reason:
                    out.problems.append("says silent but gives no reason")
                if data.get("events") or data.get("beats"):
                    out.problems.append("says silent but also lists beats")
                self._memo[key] = out
                return out
            data = data.get("events", data.get("beats"))
            if data is None:
                out.problems.append('is an object without "silent": true or an "events" list')
                self._memo[key] = out
                return out
        if not isinstance(data, list):
            out.problems.append(f"should be a list of beats, found {type(data).__name__}")
            self._memo[key] = out
            return out
        if not data:
            out.problems.append('is an empty list: an act with no beats says {"silent": true, "reason": "…"}')
        members = set(a["shots"]) if a else set()
        lo = self.secs_of(a["frame_in"]) if (a and a["frame_in"] is not None and self.clock) else None
        hi = self.secs_of(a["frame_out"]) if (a and a["frame_out"] is not None and self.clock) else None
        slack = (self.frame_seconds or 0.0) * 1.0001
        seen: set[tuple[str, str]] = set()
        for i, e in enumerate(data):
            where = f"beat {i + 1}"
            if not isinstance(e, dict):
                out.problems.append(f"{where} isn't an object")
                continue
            missing = [k for k in ("shot", "id", "t", "kind") if k not in e]
            if missing:
                out.problems.append(f"{where} ({e.get('id', '?')}) is missing {', '.join(missing)}")
                continue
            where = f"{e['shot']}/{e['id']}"
            if not isinstance(e["t"], (int, float)) or isinstance(e["t"], bool):
                out.problems.append(f"{where}: t should be film seconds, found {e['t']!r}")
                continue
            if e["kind"] not in EVENT_KINDS:
                out.problems.append(f"{where}: kind {e['kind']!r} isn't one of {', '.join(EVENT_KINDS)}")
            if members and str(e["shot"]) not in members:
                out.problems.append(f"{where}: shot {e['shot']} isn't in act {index}")
            if lo is not None and hi is not None and not (lo - slack <= float(e["t"]) <= hi + slack):
                out.problems.append(f"{where}: t = {float(e['t']):.3f} s is outside act {index} "
                                    f"({lo:.3f}–{hi:.3f} s)")
            if (str(e["shot"]), str(e["id"])) in seen:
                out.problems.append(f"{where}: the id is used twice")
            seen.add((str(e["shot"]), str(e["id"])))
            out.events.append(e)
        # t is always the beat: a draw or count ends on it (start + dur), a whoosh or swell peaks inside its span.
        # The arithmetic the schema can't hold, checked the one way every engine and reader shares.
        try:
            from slate.timing import beats as _beats  # noqa: PLC0415

            out.problems += _beats.problems(out.events, fps=float(self.fps) if self.fps else None)
        except ImportError:  # pragma: no cover - the timing module is part of every install
            pass
        self._memo[key] = out
        return out

    def beats(self) -> list[dict]:
        """Every planned beat as {frame, shot, id, kind, source}: visual events (film seconds) and the resolved
        timeline's events (film frames). The glitch triage skips flags near these."""
        if "beats" in self._memo:
            return self._memo["beats"]
        out: list[dict] = []
        if self.clock is not None:
            for a in self.acts:
                ev = self.act_events(a)
                for e in ev.events:
                    out.append({"frame": self.frame_of(float(e["t"])), "shot": str(e["shot"]), "id": str(e["id"]),
                                "kind": e.get("kind"), "source": self.rel(ev.path)})
        tl = self.read_json("timing/timeline.json") or {}
        shots = self.shot_by_id
        per_shot = tl.get("shots") if isinstance(tl, dict) else None
        if isinstance(per_shot, dict):
            for sid, lst in per_shot.items():
                for e in lst or []:
                    if not isinstance(e, dict):
                        continue
                    frame = e.get("film_frame")
                    if frame is None and e.get("frame") is not None and sid in shots:
                        frame = int(shots[sid].get("frame_in", 0)) + int(e["frame"])
                    if frame is not None:
                        out.append({"frame": int(frame), "shot": str(sid), "id": str(e.get("id")),
                                    "kind": e.get("kind"), "source": "timing/timeline.json"})
                    start = e.get("start_frame")
                    if start is not None and sid in shots:
                        # a move's first frame is planned too (a count-up starts there)
                        out.append({"frame": int(shots[sid].get("frame_in", 0)) + int(start), "shot": str(sid),
                                    "id": f"{e.get('id')}:start", "kind": e.get("kind"),
                                    "source": "timing/timeline.json"})
        out.sort(key=lambda b: b["frame"])
        self._memo["beats"] = out
        return out

    # ------------------------------------------------------------------ renders and masters
    REGISTRY = "renders/registry.json"

    def render_entries(self) -> list[dict]:
        """renders/registry.json entries with a resolved `path` and film frames `frame_in`/`frame_out` (out exclusive,
        like shots.json)."""
        doc = self.read_json(self.REGISTRY) or {}
        entries = doc.get("entries") if isinstance(doc, dict) else doc
        out = []
        for i, e in enumerate(entries or []):
            if not isinstance(e, dict) or not e.get("file"):
                continue
            fr = e.get("frames")
            fin = e.get("frame_in", fr[0] if isinstance(fr, (list, tuple)) and len(fr) == 2 else None)
            fout = e.get("frame_out", fr[1] if isinstance(fr, (list, tuple)) and len(fr) == 2 else None)
            out.append({**e, "order": i, "path": self.path(e["file"]),
                        "frame_in": None if fin is None else int(fin), "frame_out": None if fout is None else int(fout)})
        return out

    def current_renders(self) -> dict[int, dict]:
        """The render standing for each act: the last registered entry that spans the whole act (partial renders are
        patches, spliced into a whole-act entry before they count). Superseded entries never stand."""
        acts = {a["index"]: a for a in self.acts}
        best: dict[int, dict] = {}
        for e in self.render_entries():
            if e.get("superseded") or e.get("current") is False:
                continue
            act = e.get("act")
            if act is None:
                continue
            try:
                act = int(act)
            except (TypeError, ValueError):
                continue
            a = acts.get(act)
            if a is None or e["frame_in"] != a["frame_in"] or e["frame_out"] != a["frame_out"]:
                continue
            best[act] = e
        return dict(sorted(best.items()))

    def masters(self) -> list[Path]:
        """Master files: those a masters record lists, else the videos in masters/ (not hidden, not partial)."""
        for rec in MASTER_RECORDS:
            doc = self.read_json(rec)
            if doc:
                items = doc.get("masters") or doc.get("entries") or doc.get("files") or [] if isinstance(doc, dict) else doc
                files = [self.path(x["file"] if isinstance(x, dict) else x) for x in items
                         if (isinstance(x, dict) and x.get("file")) or isinstance(x, str)]
                if files:
                    return files
        d = self.path(MASTER_DIR)
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT
                      and not p.name.startswith(".") and ".part" not in p.name)

    def master_records(self) -> list[str]:
        return list(MASTER_RECORDS)

    # ------------------------------------------------------------------ sound
    def mix_record(self) -> tuple[str | None, dict | None]:
        for rec in MIX_RECORDS:
            doc = self.read_json(rec)
            if isinstance(doc, dict):
                return rec, doc
        return None, None

    def mix_path(self) -> Path | None:
        """The final mix: named by a mix record (sound/mix.json: "mix" or "file"), else the first of MIX_CANDIDATES."""
        _rec, doc = self.mix_record()
        if doc:
            for key in ("mix", "file", "final_mix", "path"):
                if isinstance(doc.get(key), str) and self.source_path(doc[key]).is_file():
                    return self.source_path(doc[key])
        for cand in MIX_CANDIDATES:
            if self.exists(cand):
                return self.source_path(cand)
        return None

    def stem_paths(self) -> dict[str, Path]:
        """Delivery stems by name: from a mix record's "stems", else the WAVs in the stems/ folder beside the mix."""
        _rec, doc = self.mix_record()
        stems = (doc or {}).get("stems")
        if isinstance(stems, dict) and stems:
            return {str(k): self.source_path(v if isinstance(v, str) else v.get("file")) for k, v in stems.items()
                    if isinstance(v, str) or (isinstance(v, dict) and v.get("file"))}
        if isinstance(stems, list) and stems:
            out = {}
            for v in stems:
                f = v if isinstance(v, str) else (v or {}).get("file")
                if f:
                    out[Path(f).stem] = self.source_path(f)
            return out
        mix = self.mix_path()
        if mix is None:
            return {}
        logical_mix = mix
        candidates = [doc[key] for key in ("mix", "file", "final_mix", "path")
                      if isinstance((doc or {}).get(key), str)] + list(MIX_CANDIDATES)
        for cand in candidates:
            if self.source_path(cand) == mix:
                logical_mix = self.path(cand)
                break
        try:
            directory = (logical_mix.parent / "stems").relative_to(self.dir)
        except ValueError:
            return {}  # an external mix never grants ownership of its adjacent stems
        folder = self.path(directory)
        names = self._reference_names(directory, ".wav")
        if folder.is_dir():
            names.update(p.name for p in folder.glob("*.wav") if not p.name.startswith("."))
        files = [self.source_path(directory / name) for name in sorted(names)]
        return {p.stem: p for p in files if p.is_file()}

    def empty_stems(self) -> dict[str, str]:
        """{stem: why} for the delivery stems the mix record says this film has nothing for (silent on purpose)."""
        _rec, doc = self.mix_record()
        empty = (doc or {}).get("empty_stems")
        return {str(k): str(v) for k, v in empty.items()} if isinstance(empty, dict) else {}

    def dialogue_stem(self) -> Path | None:
        stems = self.stem_paths()
        for name in DIALOGUE_STEMS:
            for k, p in stems.items():
                if k.lower() == name:
                    return p
        return None

    def verify_record(self) -> tuple[str | None, dict | None]:
        """The sound engine's own verification (effects anchors against beats, VO placement), when it wrote one."""
        for rec in VERIFY_RECORDS:
            doc = self.read_json(rec)
            if isinstance(doc, dict):
                return rec, doc
        return None, None

    # ------------------------------------------------------------------ voice, captions, script
    @property
    def vo_cues(self) -> list[dict]:
        doc = self.read_json("timing/vo_cues.json") or {}
        return [c for c in (doc.get("cues") or []) if isinstance(c, dict)]

    @property
    def vo_words(self) -> dict[str, dict]:
        doc = self.read_json("timing/vo_words.json") or {}
        return {str(k): v for k, v in (doc.get("shots") or {}).items() if isinstance(v, dict)}

    def format(self) -> dict | None:
        """The film's format (read_format), read once per run."""
        if "format" not in self._memo:
            self._memo["format"] = read_format(self.film)
        return self._memo["format"]

    def caption_paths(self, *, include_delivery: bool = True) -> list[Path]:
        """Caption inputs, including explicit sidecar file references; never an output destination."""
        found: list[Path] = []
        for cand in CAPTION_CANDIDATES:
            if not include_delivery and Path(cand).parts[0] == "deliver":
                continue
            if self.exists(cand):
                found.append(self.source_path(cand))
        for ext in (".srt", ".vtt"):
            for d in CAPTION_DIRS:
                if not include_delivery and Path(d).parts[0] == "deliver":
                    continue
                folder = self.path(d)
                names = set()
                if folder.is_dir():
                    names.update(p.name for p in folder.glob(f"*{ext}") if not p.name.startswith("."))
                names.update(self._reference_names(d, ext))
                for name in sorted(names):
                    source = self.source_path(Path(d) / name)
                    if source.is_file():
                        found.append(source)
        seen, unique = set(), []
        for source in found:
            key = source.resolve()
            if key not in seen:
                seen.add(key)
                unique.append(source)
        return unique

    def captions_path(self) -> Path | None:
        """Structured cues first, then an SRT or VTT, from owned inputs or their explicit references."""
        return next(iter(self.caption_paths()), None)

    @property
    def script(self) -> dict | None:
        return self.read_json("script/script.json")

    @property
    def storyboard(self) -> dict | None:
        return self.read_json("script/storyboard.json")

    def spoken_lines(self) -> list[tuple[str, str]]:
        """(shot, line) for every voiced line in film order: vo_words.json's booth lines, else the storyboard's."""
        words = self.vo_words
        order = {sid: i for i, sid in enumerate(s.get("id") for s in self.shots)}
        if words:
            ordered = sorted(words.items(), key=lambda kv: order.get(kv[0], 1e9))
            return [(sid, str(w.get("line") or "")) for sid, w in ordered if str(w.get("line") or "").strip()]
        out = []
        for shot in iter_storyboard_shots(self.storyboard or {}):
            vo = shot.get("vo") or shot.get("line")
            if isinstance(vo, str) and vo.strip():
                out.append((str(shot.get("id")), vo))
        return out


# ---------------------------------------------------------------------- the film's format
def film_aspect(film: dict) -> str | None:
    """The film's aspect as film.json says it ("9:16"), else its frame size ("1080x1920")."""
    if film.get("aspect"):
        return str(film["aspect"])
    w, h = film.get("width"), film.get("height")
    return f"{w}x{h}" if w and h else None


def _load_with(mod, ref: str, aspect: str | None) -> dict | None:
    """slate.formats.load(ref) at the film's aspect (or its default when it doesn't come in that one)."""
    for kw in (({"aspect": aspect},) if aspect else ()) + ({},):
        try:
            fmt = mod.load(ref, **kw)
        except TypeError:
            continue  # a load() that takes no aspect
        except Exception:  # noqa: BLE001 - not this aspect, or not this format
            continue
        if isinstance(fmt, dict):
            return fmt
    return None


def _raw_format(path: Path) -> dict | None:
    try:
        fmt = jsonio.read(path) if path.is_file() else None
    except SlateError:
        return None
    return fmt if isinstance(fmt, dict) else None


def read_format(film: dict) -> dict | None:
    """The film's format data: slate.formats.load at the film's aspect when it's installed, else the raw file. When
    film.json records the file it was made from (`format_source`), that file is the film's format, never another one
    with the same name; else the format by name (formats/<name>.json). None when there's none."""
    import importlib  # noqa: PLC0415

    from slate import paths  # noqa: PLC0415

    name = film.get("format")
    try:
        mod = importlib.import_module("slate.formats")
    except ImportError:
        mod = None
    loader = mod if mod is not None and hasattr(mod, "load") else None
    aspect = film_aspect(film)
    source = film.get("format_source")
    source = Path(source).expanduser() if isinstance(source, str) and source.endswith(".json") else None
    if source is not None and source.is_file():
        fmt = _load_with(loader, str(source), aspect) if loader else None
        return fmt if fmt is not None else _raw_format(source)
    if name and loader is not None:
        fmt = _load_with(loader, str(name), aspect)
        if fmt is not None:
            return fmt
    return _raw_format(paths.FORMATS_DIR / f"{name}.json") if name else None


def iter_storyboard_shots(doc: dict) -> Iterator[dict]:
    """Shots from a storyboard (acts[].shots[]) or a flat shots list."""
    acts = doc.get("acts") if isinstance(doc, dict) else None
    if isinstance(acts, list):
        for a in acts:
            for s in (a or {}).get("shots") or []:
                if isinstance(s, dict):
                    yield s
    elif isinstance(doc, dict) and isinstance(doc.get("shots"), list):
        for s in doc["shots"]:
            if isinstance(s, dict):
                yield s


# ---------------------------------------------------------------------- one context per run
_ACTIVE: dict[str, FilmContext] = {}


def context(film_dir: str | os.PathLike) -> FilmContext:
    """The shared context for this film during a run (see `session`), or a fresh one."""
    key = str(Path(film_dir).expanduser().resolve())
    return _ACTIVE.get(key) or FilmContext(key)


@contextlib.contextmanager
def session(film_dir: str | os.PathLike) -> Iterator[FilmContext]:
    """Share one FilmContext (and its hash cache) across every gate in a run."""
    key = str(Path(film_dir).expanduser().resolve())
    outer = _ACTIVE.get(key)
    ctx = outer or FilmContext(key)
    _ACTIVE[key] = ctx
    try:
        yield ctx
    finally:
        if outer is None:
            _ACTIVE.pop(key, None)
