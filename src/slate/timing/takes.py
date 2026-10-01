"""Where a film's voice takes are, and how long each read is.

The voice stages (`slate voice tts|clean|pace|polish`) own the takes and record them in `voice/takes.json`:

    {"shots": {"S02": {"selected": "t01", "approved": {...} | null,
                       "takes": {"t01": {"raw": "voice/takes/S02/t01.wav", "clean": "…clean.wav", "paced": "…paced.wav",
                                         "polished": "…polished.wav"}}}}}

A shot's take is its selected take's final stage: polished, else paced, else clean, else raw (the voice module's own
rule, slate.voice.film.VoiceFilm.STAGES). The studio polish keeps every sample where it was, so a polished take reads
exactly as long as the stage it was polished from and its words land at the same times; picking it here is what
carries the de-esser, the EQ and the room into the cue sheets and the mix. Timing
also accepts a flat list ({"takes": [{"shot", "file", "seconds", "dur"}]}) and, with no manifest at all, files named
`voice/takes/<shot>.<wav|flac|aif|aiff|mp3|m4a>`. `dur` (or `trim`) is a play length shorter than the file, for a
take whose leaked tail was trimmed (film A trimmed S10 and S20 this way). A take's read length is that trim when it
has one, else the file's length.

A derived film (a cut-down or a new aspect) reuses its parent's takes by reference: its film.lock.json points into the
parent and nothing is copied. Every path here goes through `film_file`, which follows those references, while a take's
`rel` stays film-relative, the way cue sheets write it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slate import jsonio
from slate.timing import audio

MANIFEST = Path("voice") / "takes.json"
TAKES_DIR = Path("voice") / "takes"
EXTENSIONS = (".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a")
STAGES = ("polished", "paced", "clean", "raw")  # the final stage wins


@dataclass
class Take:
    shot: str
    path: Path  # absolute
    rel: str  # as written in cue sheets: film-relative when inside the film
    seconds: float | None = None  # file length (measured lazily)
    trim: float | None = None
    note: str | None = None
    take_id: str | None = None
    stage: str | None = None
    approved: bool = False
    meta: dict = field(default_factory=dict)

    def length(self) -> float:
        if self.seconds is None:
            self.seconds = round(audio.duration(self.path), 6)
        return float(self.seconds)

    @property
    def read_seconds(self) -> float:
        """How long the read plays: the trim when a leaked tail was cut, else the whole file."""
        return float(self.trim) if self.trim else self.length()

    def sha256(self) -> str:
        return jsonio.sha256(self.path)


def _rel(film_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(film_dir.resolve()))
    except ValueError:
        return str(path)


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def film_file(film_dir: Path, rel: str | Path) -> Path:
    """The real file behind a film-relative path: the film's own, else its parent's when a derived film reuses it by
    reference (film state's `lock.resolve`). Absolute paths are taken as they are."""
    rel = Path(rel)
    if rel.is_absolute():
        return rel
    own = Path(film_dir) / rel
    if own.exists():
        return own
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415
    except ImportError:
        return own
    try:
        return Path(state_lock.resolve(Path(film_dir), rel.as_posix()))
    except Exception:  # noqa: BLE001 - an unreadable lock is the same as no reference
        return own


def _path(film_dir: Path, file: str) -> Path:
    return film_file(film_dir, file)


def _rel_of(film_dir: Path, file: str, path: Path) -> str:
    """How a cue sheet names the take: as the manifest wrote it when that's film-relative, else relative to the film."""
    if not Path(file).is_absolute():
        return Path(file).as_posix()
    return _rel(film_dir, path)


def _flat(film_dir: Path, shot: str, entry: dict) -> Take | None:
    file = entry.get("file") or entry.get("path")
    if not file:
        return None
    path = _path(film_dir, str(file))
    return Take(shot=shot, path=path, rel=_rel_of(film_dir, str(file), path),
                seconds=_num(entry.get("seconds", entry.get("duration"))),
                trim=_num(entry.get("dur", entry.get("trim"))), note=entry.get("note"),
                meta={k: v for k, v in entry.items() if k not in ("shot", "file", "path", "dur", "trim", "seconds",
                                                                   "duration", "note")})


def _selected(film_dir: Path, shot: str, entry: dict) -> Take | None:
    """The voice module's shape: the selected take's final stage."""
    take_id = entry.get("selected")
    info = (entry.get("takes") or {}).get(take_id) if take_id else None
    if not isinstance(info, dict):
        return None
    for stage in STAGES:
        file = info.get(stage)
        if file and _path(film_dir, str(file)).is_file():
            path = _path(film_dir, str(file))
            seconds = _num(info.get(f"{stage}_seconds")) or (_num(info.get("seconds")) if stage == "raw" else None)
            return Take(shot=shot, path=path, rel=_rel_of(film_dir, str(file), path), seconds=seconds,
                        trim=_num(info.get("dur", info.get("trim"))), note=info.get("note"), take_id=str(take_id),
                        stage=stage, approved=bool(entry.get("approved")))
    return None


def find_takes(film_dir: Path, shot_ids: list[str] | None = None) -> dict[str, Take]:
    """{shot id: Take} for the film's takes that exist on disk."""
    film_dir = Path(film_dir)
    out: dict[str, Take] = {}
    manifest = jsonio.read(film_file(film_dir, MANIFEST), default=None)
    if isinstance(manifest, dict) and (manifest.get("takes") or manifest.get("shots")):
        if isinstance(manifest.get("takes"), list):
            for e in manifest["takes"]:
                if isinstance(e, dict) and e.get("shot"):
                    take = _flat(film_dir, str(e["shot"]), e)
                    if take and take.path.is_file():
                        out[take.shot] = take
        if isinstance(manifest.get("shots"), dict):
            for shot, entry in manifest["shots"].items():
                if not isinstance(entry, dict):
                    continue
                take = _selected(film_dir, str(shot), entry) if "selected" in entry or "takes" in entry else \
                    _flat(film_dir, str(shot), entry)
                if take and take.path.is_file():
                    out[str(shot)] = take
    else:
        folder = film_dir / TAKES_DIR
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() in EXTENSIONS and path.is_file() and not path.name.startswith("."):
                    out.setdefault(path.stem, Take(shot=path.stem, path=path, rel=_rel(film_dir, path)))
        for rel in _referenced(film_dir, TAKES_DIR):  # a derived film's takes live in its parent
            name = Path(rel)
            if name.parent == TAKES_DIR and name.suffix.lower() in EXTENSIONS and not name.name.startswith("."):
                real = film_file(film_dir, rel)
                if real.is_file():
                    out.setdefault(name.stem, Take(shot=name.stem, path=real, rel=name.as_posix()))
    if shot_ids is not None:
        wanted = set(shot_ids)
        out = {k: v for k, v in out.items() if k in wanted}
    return out


def _referenced(film_dir: Path, folder: Path) -> list[str]:
    """Film-relative paths under `folder` that film.lock.json reuses from a parent film (none for an ordinary film)."""
    try:
        from slate.state import lock as state_lock  # noqa: PLC0415

        entries = state_lock.entries(Path(film_dir), prefix=folder.as_posix() + "/")
    except Exception:  # noqa: BLE001 - no film state, no references
        return []
    return sorted(rel for rel, e in entries.items() if isinstance(e, dict) and e.get("ref"))
