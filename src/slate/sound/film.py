"""Everything the sound stages need to know about a film, read once from its folder.

A film's clock (fps_num / fps_den), size and targets come from film.json and timing/shots.json; nothing in the sound
code assumes a frame rate or a length. The film's exact length is total_frames × fps_den / fps_num, and every sound
file Slate writes for it is exactly that long at the film's sample rate (48 kHz unless film.json says otherwise).

Sibling modules are used when they're installed (slate.state for film.json and lock records, slate.timing for
shots, slate.brand.pack for brand packs, slate.formats for formats), with a plain read of the same files otherwise,
so the sound stages run while the rest of Slate is still landing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slate import jsonio, log, paths
from slate.errors import MissingPrerequisite, UsageError
from slate.sound import audio
from slate.sound.effects import palette as palettes

SOUND = "sound"
SCORE_DIR = "sound/score"
MIX_DIR = "sound/mix"
STEMS_DIR = "sound/stems"
CUES_FILE = "sound/sound_cues.json"
DEFAULT_TARGETS = {"lufs": -14.0, "tol": 0.5, "true_peak": -1.0}


@dataclass
class Film:
    dir: Path
    data: dict
    shots: dict
    fps_num: int
    fps_den: int
    sr: int
    duration_s: float
    targets: dict
    brand: dict | None
    palette: dict
    fmt: dict | None
    notes: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        """The film's length in samples."""
        return int(round(self.duration_s * self.sr))

    @property
    def frame_s(self) -> float:
        return self.fps_den / self.fps_num

    @property
    def id(self) -> str:
        return str(self.data.get("id") or self.dir.name)

    @property
    def mode(self) -> str:
        """The mode Slate judges the film in: its own record holds a film made autonomous autonomous, whatever
        film.json says now (slate.state.autonomy.film_mode)."""
        own = str(self.data.get("mode") or "supervised")
        try:
            from slate.state import autonomy  # noqa: PLC0415 - sound works without film state installed
        except ImportError:
            return own
        try:
            return autonomy.film_mode(self.dir, self.data)
        except Exception:  # noqa: BLE001 - an unreadable record never makes a film less strict than its film.json
            return own

    def path(self, rel: str | Path) -> Path:
        """Where this film's own file is (what the sound stages write). Reads of reused files go through source()."""
        p = Path(rel)
        return p if p.is_absolute() else self.dir / p

    def source(self, rel: str | Path) -> Path:
        """A file to read: the film's own, else, in a derived film (a cut-down or a new aspect), the parent's file it
        reuses by reference (slate.state.lock.resolve). Never used for writing: a derived film writes its own."""
        if Path(rel).is_absolute():
            return Path(rel)
        return _resolved(self.dir, str(rel))

    def rel(self, p: str | Path) -> str:
        p = Path(p)
        try:
            return str(p.resolve().relative_to(self.dir.resolve()))
        except ValueError:
            return str(p)

    def shots_by_id(self) -> dict[str, dict]:
        return {s["id"]: s for s in self.shots.get("shots", [])}

    def shot_start(self, shot_id: str) -> float:
        s = self.shots_by_id().get(shot_id)
        if s is None:
            raise UsageError(f"Shot {shot_id!r} isn't in timing/shots.json.")
        if "sec_in" in s:
            return float(s["sec_in"])
        return int(s["frame_in"]) * self.fps_den / self.fps_num

    def resolve_media(self, ref: str) -> Path:
        """A media path from a cue sheet: absolute, or relative to the film folder (a derived film's reused take
        resolves to its parent's file)."""
        p = Path(str(ref)).expanduser()
        return p if p.is_absolute() else self.source(p)

    def broadcast(self) -> bool:
        """True when the film's use is broadcast (film, TV, radio): Eleven Music then needs its Enterprise licence."""
        if "broadcast" in self.data:
            return bool(self.data["broadcast"])
        return bool((self.fmt or {}).get("broadcast"))

    def music_licence(self) -> str | None:
        return ((self.data.get("licences") or {}).get("eleven_music") or None)


def _read_film_json(film_dir: Path) -> dict:
    try:
        from slate.state import film as state_film  # noqa: PLC0415

        return state_film.load(film_dir)
    except (ImportError, AttributeError):
        pass
    path = film_dir / "film.json"
    if not path.exists():
        raise UsageError(f"{film_dir} has no film.json.", hint="slate new <id> --brand <brand> --format <format>")
    return jsonio.read(path)


def _resolved(film_dir: Path, rel: str) -> Path:
    """A derived film's reused file (slate.state.lock.resolve), or the film's own path."""
    own = Path(film_dir) / rel
    if own.exists():
        return own
    try:
        from slate.state import lock  # noqa: PLC0415

        return Path(lock.resolve(film_dir, rel))
    except Exception:  # noqa: BLE001 - no state module, or a broken reference: read as missing
        return own


def load_shots(film_dir: Path) -> dict:
    try:
        from slate import timing  # noqa: PLC0415

        loader = getattr(timing, "load_shots", None)
        if loader is not None:
            try:
                return loader(film_dir)
            except MissingPrerequisite:
                if not _resolved(film_dir, "timing/shots.json").is_file():
                    raise  # a derived film reusing its parent's shots reads them below
    except ImportError:
        pass
    path = _resolved(film_dir, "timing/shots.json")
    if not path.exists():
        raise MissingPrerequisite(f"{film_dir.name} has no timing/shots.json yet, so there's no clock to place sound on.",
                                  hint="Run `slate timing` first.")
    return jsonio.read(path)


def load_brand(name: str | None) -> dict | None:
    """The brand pack (brand.json as a dict), or None when it can't be found."""
    if not name:
        return None
    try:
        from slate.brand import pack  # noqa: PLC0415

        loaded = pack.load(name)
        if isinstance(loaded, dict):
            return loaded
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - a broken pack shouldn't stop a mix; the checks say so
        log.warn(f"brand pack {name!r} didn't load through slate.brand ({exc}); reading brand.json directly")
    candidate = Path(str(name)).expanduser()
    for path in ([candidate / "brand.json", candidate] if candidate.exists() else []) + \
            [d / str(name) / "brand.json" for d in paths.brand_dirs()]:
        if path.is_file() and path.name == "brand.json":
            data = jsonio.read(path)
            data.setdefault("_dir", str(path.parent))
            return data
    return None


def load_format(name: str | None) -> dict | None:
    if not name:
        return None
    try:
        from slate import formats  # noqa: PLC0415

        loader = getattr(formats, "load", None)
        if loader is not None:
            return loader(name)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass
    path = paths.FORMATS_DIR / f"{name}.json"
    return jsonio.read(path) if path.exists() else None


def _duration(shots: dict, fps_num: int, fps_den: int) -> float:
    frames = shots.get("total_frames")
    if frames is None and shots.get("shots"):
        frames = max(int(s.get("frame_out", 0)) for s in shots["shots"])
    if frames:
        return int(frames) * fps_den / fps_num
    if shots.get("total_seconds"):
        return float(shots["total_seconds"])
    raise UsageError("timing/shots.json has no total_frames, shots or total_seconds, so the film has no length.")


def load(film_dir: str | Path) -> Film:
    """Read a film's sound context. Raises MissingPrerequisite when timing hasn't run yet."""
    film_dir = Path(film_dir).resolve()
    data = _read_film_json(film_dir)
    shots = load_shots(film_dir)
    fps_num = int(shots.get("fps_num") or data.get("fps_num") or 0)
    fps_den = int(shots.get("fps_den") or data.get("fps_den") or 0)
    if not fps_num or not fps_den:
        raise UsageError("Neither timing/shots.json nor film.json gives the frame rate (fps_num / fps_den).")
    tg = dict(DEFAULT_TARGETS)
    raw_t = data.get("targets") or {}
    if raw_t.get("lufs") is not None:
        tg["lufs"] = float(raw_t["lufs"])
    if raw_t.get("true_peak") is not None:
        tg["true_peak"] = float(raw_t["true_peak"])
    if raw_t.get("lufs_tolerance") is not None:
        tg["tol"] = float(raw_t["lufs_tolerance"])
    sr = int(raw_t.get("sample_rate") or data.get("sample_rate") or audio.DEFAULT_SR)
    brand = load_brand(data.get("brand"))
    notes = []
    if data.get("brand") and brand is None:
        notes.append(f"brand pack {data.get('brand')!r} wasn't found; using the starting palette")
    pal = palettes.from_brand(brand)
    film_pal = (data.get("sound") or {}).get("palette")
    if film_pal:
        pal = palettes.deep_merge(pal, film_pal)
    fmt = load_format(data.get("format"))
    duration = _duration(shots, fps_num, fps_den)
    if not math.isfinite(duration) or duration <= 0:
        raise UsageError("The film's length from timing/shots.json isn't positive.")
    return Film(film_dir, data, shots, fps_num, fps_den, sr, duration, tg, brand, pal, fmt, notes)


def record_lock(film: Film, rel_path: str, *, producer: str, inputs: list[str | Path],
                tracked: list[str] = ()) -> None:
    """Record a derived file in film.lock.json through slate.state when it's installed (quietly skipped otherwise).

    `inputs` are files that exist now, hashed here. `tracked` are inputs slate.state.lock hashes itself: named ones
    (`brand:<name>#sound`, one block of the brand pack) and files that may not exist yet (an effect cue sheet), so
    their arrival or change later makes this file stale."""
    try:
        from slate.state import lock  # noqa: PLC0415
    except ImportError:
        return
    try:
        hashes = jsonio.hash_inputs([film.path(p) for p in inputs])
        rel_hashes: dict[str, str | None] = {film.rel(k): v for k, v in hashes.items()}
        rel_hashes.update({str(k): None for k in tracked if str(k) not in rel_hashes})
        lock.record(film.dir, rel_path, producer=producer, inputs=rel_hashes)
    except Exception as exc:  # noqa: BLE001 - a lock record must never lose a finished mix
        log.warn(f"couldn't record {rel_path} in film.lock.json: {exc}")


def is_locked(film: Film, rel_path: str) -> bool:
    try:
        from slate.state import lock  # noqa: PLC0415

        return bool(lock.is_locked(film.dir, rel_path))
    except (ImportError, AttributeError):
        return False
    except Exception:  # noqa: BLE001
        return False


def words_for(film: Film) -> list[tuple[str, float, float]]:
    """Every spoken word at film time: [(word, start s, end s)], from timing/vo_words.json placed by vo_cues.json."""
    words_path = film.source("timing/vo_words.json")
    cues_path = film.source("timing/vo_cues.json")
    if not words_path.exists() or not cues_path.exists():
        return []
    words = jsonio.read(words_path).get("shots") or {}
    out: list[tuple[str, float, float]] = []
    for cue in jsonio.read(cues_path).get("cues") or []:
        shot = cue.get("shot")
        entry = words.get(shot) or {}
        if not entry.get("words"):
            continue
        t0 = float(cue["t"]) if "t" in cue else film.shot_start(shot) + float(cue.get("at", 0.0))
        trim0 = float(cue["trim"][0]) if cue.get("trim") else 0.0
        for w in entry["words"]:
            out.append((str(w.get("w", "")), t0 + float(w["start"]) - trim0, t0 + float(w["end"]) - trim0))
    return sorted(out, key=lambda r: r[1])


def any_value(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default
