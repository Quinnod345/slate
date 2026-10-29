"""Where a film's voice takes are, and how long each read is.

The voice stages (`slate voice tts|clean|pace|polish`) own the takes and record them in `voice/takes.json`:

    {"shots": {"S02": {"selected": "t01", "approved": {...} | null,
                       "takes": {"t01": {"raw": "voice/takes/S02/t01.wav", "clean": "…clean.wav", "paced": "…paced.wav",
                                         "polished": "…polished.wav"}}}}}

A shot's take is its selected take's final stage: polished, else paced, else clean, else raw (the voice module's own
rule, slate.voice.film.VoiceFilm.STAGES). Picking it here carries the de-esser, EQ and room into cue sheets and the
mix. Picture timing measures the pre-polish source: equal sample counts preserve the clock, but room processing
can still shift a recogniser's word timestamps. `for_alignment` verifies the source and its sample clock. Timing
also accepts a flat list ({"takes": [{"shot", "file", "seconds", "dur"}]}) and, with no manifest at all, files named
`voice/takes/<shot>.<wav|flac|aif|aiff|mp3|m4a>`. `dur` (or `trim`) is a play length shorter than the file, for a
take whose leaked tail was trimmed (film A trimmed S10 and S20 this way). A take's read length is that trim when it
has one, else the file's length.

A derived film (a cut-down or a new aspect) reuses its parent's takes by reference: its film.lock.json points into the
parent and nothing is copied. Every path here goes through `film_file`, which follows those references, while a take's
`rel` stays film-relative, the way cue sheets write it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from slate import jsonio
from slate.errors import MissingPrerequisite, SlateError
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


POLISH_FIELDS = ("file", "source", "source_stage", "source_sha256", "sha256")


def _selected_info(film_dir: Path, shot: str) -> tuple[str | None, dict]:
    manifest = jsonio.read(film_file(film_dir, MANIFEST), default=None)
    entry = ((manifest or {}).get("shots") or {}).get(shot) if isinstance(manifest, dict) else None
    if not isinstance(entry, dict):
        return None, {}
    take_id = entry.get("selected")
    info = (entry.get("takes") or {}).get(take_id) if take_id else None
    return str(take_id) if take_id else None, info if isinstance(info, dict) else {}


def _polish_info(film_dir: Path, shot: str, take_id: str) -> tuple[bool, object]:
    doc = jsonio.read(film_file(film_dir, TAKES_DIR / shot / f"{take_id}.json"), default=None)
    stages = doc.get("stages") if isinstance(doc, dict) else None
    return (True, stages["polish"]) if isinstance(stages, dict) and "polish" in stages else (False, None)


def timing_selector(film_dir: Path, shot: str) -> dict | None:
    """Only the selected audio paths, read length and polish provenance; approvals and QA bookkeeping are excluded.

    State hashes this value as `voice:<shot>#timing`. Audio bytes are separate inputs. Inherited films resolve both
    the manifest and sidecar through their references, just as they resolve the selected audio.
    """
    take_id, info = _selected_info(film_dir, shot)
    if not info:
        return None
    fields = (*STAGES, *(f"{stage}_seconds" for stage in STAGES), "seconds", "dur", "trim")
    value = {"selected": take_id, "take": {key: info[key] for key in fields if key in info},
             "available_stages": [stage for stage in STAGES if info.get(stage) and
                                  film_file(film_dir, str(info[stage])).is_file()]}
    present, polish = _polish_info(film_dir, shot, take_id)
    if present:
        value["polish"] = ({key: polish.get(key) for key in POLISH_FIELDS} if isinstance(polish, dict) else polish)
    return value


def for_alignment(film_dir: Path, take: Take) -> Take:
    """The verified pre-polish take for word alignment and onset snapping; playback retains the selected final take.

    Explicit polish provenance must match both files and the selected source stage. Legacy manifests without that
    provenance use their nearest available pre-polish stage, with an exact sample-rate and frame-count check.
    """
    if take.stage != "polished":
        return take
    hint = f"Rebuild the selected polish with `slate voice polish --only {take.shot}` before rebuilding timing."

    def refuse(message: str):
        raise MissingPrerequisite(f"{take.shot} polish timing source: {message}", hint=hint)

    take_id, info = _selected_info(film_dir, take.shot)
    if take_id != take.take_id or not info:
        refuse("the selected take changed; reload its manifest.")
    present, polish = _polish_info(film_dir, take.shot, take_id)
    if present:
        if not isinstance(polish, dict):
            refuse("the recorded polish provenance is malformed.")
        stage = polish.get("source_stage")
        if stage not in STAGES[1:] or not info.get(stage) or not polish.get("source"):
            refuse("the recorded source stage is missing or invalid.")
        file = str(info[stage])
        source = film_file(film_dir, file)
        if source.resolve() != film_file(film_dir, str(polish["source"])).resolve():
            refuse("the recorded source does not match the selected take's source stage.")
        if not polish.get("file") or film_file(film_dir, str(polish["file"])).resolve() != take.path.resolve():
            refuse("the recorded output does not match the selected polish.")
        if not source.is_file() or polish.get("source_sha256") != jsonio.sha256(source):
            refuse("the recorded source hash no longer matches its audio.")
        if polish.get("sha256") != take.sha256():
            refuse("the recorded output hash no longer matches its audio.")
    else:
        stage = next((s for s in STAGES[1:] if info.get(s) and film_file(film_dir, str(info[s])).is_file()), None)
        if stage is None:
            refuse("no pre-polish source is available.")
        file = str(info[stage])
        source = film_file(film_dir, file)
    from slate.voice.audio import read_wav  # noqa: PLC0415 - native WAV clock, no transcription or external decoder

    try:
        source_samples, source_rate = read_wav(source)
        output_samples, output_rate = read_wav(take.path)
    except (OSError, ValueError, SlateError) as exc:
        refuse(f"cannot verify the sample clock ({exc}).")
    if source_rate <= 0 or len(source_samples) == 0 or source_rate != output_rate or len(source_samples) != len(output_samples):
        refuse("the source and output have different sample rates or frame counts.")
    return replace(take, path=source, rel=_rel_of(film_dir, file, source), stage=stage,
                   seconds=len(source_samples) / source_rate)
