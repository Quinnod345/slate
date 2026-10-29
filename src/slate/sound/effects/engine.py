"""Approved effects: picture events to measured cues, then balanced stems for the delivery mix.

The voices and family mixer are pure-array modules. These entry points own film paths, approval checks, input
freshness, resource locks and publication. A failed render keeps the previous complete stem set.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

from slate import gates, jsonio
from slate.errors import MissingPrerequisite, Refused, UsageError
from slate.jobs import locks
from slate.sound import audio, vo
from slate.sound.effects import require_proof3, status
from slate.sound.film import CUES_FILE, STEMS_DIR, Film, load, record_lock, words_for

PRODUCER = "slate sfx"


def plan(film_dir: Path, *, stage: str = "run") -> dict:
    """Read-only description, including the gate; dry runs neither decode audio nor acquire locks."""
    film = load(film_dir)
    acts = sorted({int(s["act"]) for s in film.shots.get("shots", [])})
    sheet = film.source(CUES_FILE)
    return {"film": film.id, "stage": stage, "sample_rate": film.sr, "samples": film.n,
            "seconds": film.duration_s, "approval": status(), "cue_sheet": str(film.path(CUES_FILE)),
            "cue_sheet_exists": sheet.is_file(), "stems": str(film.path(STEMS_DIR)),
            "events": [{"file": f"timing/visual_events/act{a}.json",
                        "exists": film.source(f"timing/visual_events/act{a}.json").is_file()} for a in acts]}


def _unlocked(film: Film, paths: list[str]) -> None:
    from slate.state import lock

    for rel in paths:
        if lock.is_locked(film.dir, rel):
            raise Refused(f"{rel} is locked; its approved sound will not be overwritten.",
                          hint="Make a revision or variant before changing this sound.")


def reference_loader(film: Film):
    """Load an explicitly approved reference bundle. Its folder hash binds cues, rides and audio together.

    A filename, a numeric ride or a cue's `approved` marker is not authorization. Every load rechecks the person's
    film-level sound_proof approval before and after reading, including when a caller uses the same span twice.
    """
    from slate.state import approvals

    def read(clip: str) -> dict:
        relative = Path(clip)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise UsageError("A sound reference must be a film-relative folder.")
        checked = approvals.check(film.dir, "sound_proof", clip)
        if not checked["approved"]:
            raise Refused(f"The sound reference {clip} is not approved: {checked['reason']}.",
                          hint="Approve the exact reference bundle with slate approve sound_proof.")
        folder = film.source(clip)
        required = [folder / "cue_sheet.json", folder / "balance_used.json"]
        if not folder.is_dir() or any(not p.is_file() for p in required):
            raise MissingPrerequisite(f"{clip} needs cue_sheet.json and balance_used.json in its approved folder.")
        result = {"cue_sheet": jsonio.read(required[0]), "balance_used": jsonio.read(required[1]),
                  "approval": checked["record"], "sha256": checked["current_sha256"]}
        render_log = folder / "render_log.json"
        if render_log.is_file():
            result["render_log"] = jsonio.read(render_log)
        for name in ("voice", "score"):
            path = folder / "family_stems" / f"{name}.wav"
            if path.is_file():
                result[name] = audio.load(path, film.sr)
        after = approvals.check(film.dir, "sound_proof", clip)
        if not after["approved"] or after["current_sha256"] != checked["current_sha256"]:
            raise Refused(f"The approved sound reference {clip} changed while it was being read.")
        return result
    return read


def render_cue(cue: dict, film_dir: Path | None = None, sample_rate: int = 48000, **kwargs):
    """The exact dry samples and placement offset, also used by independent cue verification."""
    require_proof3()
    from slate.sound.effects import rendering

    return rendering.render_cue(cue, film_dir=film_dir, sample_rate=sample_rate, **kwargs)


def _context_hash(film: Film) -> str:
    return jsonio.sha256_obj({"film": film.data, "shots": film.shots, "palette": film.palette,
                              "sound": (film.brand or {}).get("sound"),
                              "motion": (film.brand or {}).get("motion")})


def _snapshot(film: Film, sheet: dict | None = None, score: Path | None = None) -> dict:
    from slate.sound.effects import voices

    paths = {"film.json", "timing/shots.json", "timing/vo_cues.json", "timing/vo_words.json", CUES_FILE}
    paths.update(f"timing/visual_events/act{int(s['act'])}.json" for s in film.shots.get("shots", []))
    paths.update(str(c["file"]) for c in vo.cues(film) if c.get("file"))
    if score:
        paths.add(str(score))
    for cue in (sheet or {}).get("cues", []):
        paths.update(str(p) for p in voices.dependencies(cue, film_dir=film.dir, palette=film.palette))
    from slate.state import approvals
    references = {}
    for match in ((sheet or {}).get("music") or {}).get("match", []):
        check = approvals.check(film.dir, "sound_proof", match["clip"])
        if not check["approved"]:
            raise Refused(f"The sound reference {match['clip']} is no longer approved: {check['reason']}.")
        references[match["clip"]] = check["current_sha256"]
    return {"context": _context_hash(film), "references": references,
            "files": {rel: jsonio.sha256(film.source(rel)) if film.source(rel).is_file() else None
                      for rel in sorted(paths)}}


def _unchanged(film: Film, before: dict, *, sheet: dict | None = None, score: Path | None = None) -> None:
    require_proof3()
    if _snapshot(load(film.dir), sheet, score) != before:
        raise Refused("Sound inputs changed during this run; the previous outputs were kept.",
                      hint="Run the sound stage again against the current picture, voice and palette.")


def spot(film_dir: Path, *, seed: int = 0) -> Path:
    """One logical cue per event, with seeded variation and measured anchors on the film's clock."""
    require_proof3()
    from slate.sound import mix
    from slate.sound.effects import rendering, spotting

    film = load(film_dir)
    with locks.hold("render", owner="slate sfx spot"), mix._mix_lock(film):
        _unlocked(film, [CUES_FILE])
        before = _snapshot(film)
        from slate.sound.effects import voices
        recordings = {}
        def cue_renderer(cue):
            for path in voices.dependencies(cue, film_dir=film.dir, palette=film.palette):
                recordings.setdefault(path, jsonio.sha256(path))
            return rendering.render_cue(cue, film.dir, film.sr, palette=film.palette,
                                        motion=(film.brand or {}).get("motion") or {}, clock=(film.fps_num, film.fps_den))
        sheet = spotting.build(film, seed=seed, render_cue=cue_renderer,
                               load_reference=reference_loader(film))
        _unchanged(film, before)
        if any(not path.is_file() or jsonio.sha256(path) != digest for path, digest in recordings.items()):
            raise Refused("A sampled sound changed during spotting; the previous cue sheet was kept.")
        for match in (sheet.get("music") or {}).get("match", []):
            reference_loader(film)(match["clip"])
        path = film.path(CUES_FILE)
        jsonio.write_atomic(path, jsonio.mark_generated(sheet, PRODUCER))
        inputs = ["timing/shots.json"] + [r["file"] for r in sheet["input_report"]]
        record_lock(film, CUES_FILE, producer="slate sfx spot", inputs=inputs,
                    tracked=["film:sound"] + ([f"brand:{film.data['brand']}#sound",
                                               f"brand:{film.data['brand']}#motion"] if film.data.get("brand") else []))
        return path


def cue_sheet(film: Film) -> dict:
    path = film.source(CUES_FILE)
    if not path.is_file():
        raise MissingPrerequisite("There is no sound cue sheet yet.", hint="Run slate sfx spot first.")
    sheet = jsonio.read(path)
    jsonio.require_valid(sheet, "sound_cues", "Effect cue sheet")
    if sheet.get("sample_rate", film.sr) != film.sr or sheet.get("fps_num", film.fps_num) != film.fps_num \
            or sheet.get("fps_den", film.fps_den) != film.fps_den:
        raise Refused("The sound cue sheet uses an older film clock.", hint="Run slate sfx spot again.")
    provenance = sheet.get("provenance")
    if provenance:
        from slate.sound.effects import spotting

        _, events = spotting._events(film)
        _, _, spans = spotting._spans(film, sheet)
        expected = {"spotting_version": spotting.VERSION, "palette_sha256": jsonio.sha256_obj(film.palette),
                    "film_sound_sha256": jsonio.sha256_obj(film.data.get("sound")),
                    "brand_sound_sha256": jsonio.sha256_obj((film.brand or {}).get("sound")),
                    "brand_motion_sha256": jsonio.sha256_obj((film.brand or {}).get("motion")),
                    "shots_sha256": jsonio.sha256(film.source("timing/shots.json")),
                    "span_inputs_sha256": jsonio.sha256_obj(spans)}
        if any(provenance.get(k) != v for k, v in expected.items()) or sheet.get("input_report") != events:
            raise Refused("The picture events, clock or palette changed after effects were spotted.",
                          hint="Run slate sfx spot, then render the effects again.")
    return sheet


def balanced(film: Film, voice: np.ndarray, score: np.ndarray | None, *, sheet: dict | None = None,
             trims: dict | None = None, under_lu: float | None = None, duck_db: float | None = None) -> dict:
    """Balance effects against the actual master-conditioned voice; score normalization happens exactly once."""
    require_proof3()
    from slate.sound import balance, guard, mix
    from slate.sound.effects import balance_mix, rendering

    sheet = cue_sheet(film) if sheet is None else sheet
    levels, words = film.palette["levels"], words_for(film)
    music = levels.get("music") or {}
    config = balance_mix.settings(film.palette.get("balance"))
    prepared, score_report = mix.prepare_score(score, voice, film.sr,
        under_lu=float(under_lu if under_lu is not None else music.get("under_voice_lu", 18.5)),
        duck_db=float(duck_db if duck_db is not None else music.get("duck_db", 6)),
        attack_s=float(music.get("attack_s", .08)), release_s=float(music.get("release_s", .65)),
        words=words, score_guard=guard.settings_of(balance.palette_balance(film.palette)))
    resolver = reference_loader(film)
    matches = [m for m in (sheet.get("music") or {}).get("match", []) if m.get("core")]
    if matches:
        if score is None:
            raise MissingPrerequisite("Approved score spans require an assembled score.")
        prepared, score_report["approved_matches"] = balance_mix.score_match(
            prepared, voice, matches, resolver, sample_rate=film.sr)
    silences = sheet.get("silences") or []
    prepared *= rendering.silence_mask(silences, "score", film.n, film.sr)[:, None]

    def delivered(result, prepared_score):
        if not np.any(voice):
            return voice
        master = mix.mix_arrays(voice, result["stems"], prepared_score, film.sr,
                                targets=film.targets, score_prepared=True,
                                effects_ceiling_dbtp=levels.get("effects_ceiling_dbtp"),
                                linked_limit=bool(levels.get("linked_limit", True)),
                                limiter=str(levels.get("limiter", "slate")))
        return master["final"]["dialogue"] / master["engine"].gain

    cues = [c for c in sheet["cues"] if not (score is not None and balance_mix.suppressed_by_score(c))]
    result = balance_mix.mix(cues, film.n, voice=voice, words=words, sample_rate=film.sr,
                            fps=film.fps_num / film.fps_den, config=config,
                            score=prepared if score is not None else None, silences=silences,
                            trims=trims if trims is not None else levels.get("stem_trim_db"),
                            film_dir=film.dir, delivered_voice=delivered, sheet=sheet, load_reference=resolver)
    result.update(sheet=sheet, score_report=score_report,
                  suppressed_by_score=[str(hit.get("id")) for cue in sheet["cues"] if cue not in cues
                                       for hit in rendering.expand_cues([cue])])
    return result


def _publish(staging: Path, target: Path) -> None:
    """Swap a complete generated directory with rollback if the second rename fails."""
    backup = staging.with_name(staging.name + "-previous")
    had = target.exists()
    if had:
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except BaseException:
        if had:
            os.replace(backup, target)
        raise
    if had:
        shutil.rmtree(backup)


def render(film_dir: Path) -> dict:
    """Render a complete, balanced stem set. Final mixing recalculates guards for its current inputs."""
    require_proof3()
    from slate.sound import mix
    from slate.sound.effects import rendering

    film = load(film_dir)
    with locks.hold("render", owner="slate sfx render"), mix._mix_lock(film):
        sheet = cue_sheet(film)
        target = film.path(STEMS_DIR)
        _unlocked(film, [f"{STEMS_DIR}/stems.json"] + [f"{STEMS_DIR}/{r}.wav" for r in rendering.STEMS])
        sp = mix.score_path(film)
        before = _snapshot(film, sheet, sp)
        voice, placed = vo.render_stem(film)
        result = balanced(film, voice, audio.load(sp, film.sr, film.n) if sp else None, sheet=sheet)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".sfx-", dir=target.parent))
        try:
            files = {}
            for role, samples in result["stems"].items():
                path = audio.write_wav(staging / f"{role}.wav", samples, film.sr, 32)
                files[role] = {"file": path.name, "sha256": jsonio.sha256(path), "duck": False,
                               "trim_applied": True}
            report = {"producer": PRODUCER, "film": film.id, "made_at": gates.now_iso(),
                      "sample_rate": film.sr, "samples": film.n, "stems": files,
                      "inputs": before, "voice_cues": len(placed), "cue_sheet_sha256": jsonio.sha256(film.source(CUES_FILE)),
                      "trim_applied": result["stem_trim_applied"], "guard_report": result["guard_report"],
                      "provisional": True, "next": "slate mix recalculates guards against its current delivered voice"}
            jsonio.write_atomic(staging / "stems.json", jsonio.mark_generated(report, PRODUCER))
            jsonio.write_atomic(staging / "render_log.json", jsonio.mark_generated(
                {"cue_sheet_sha256": report["cue_sheet_sha256"], "sample_rate": film.sr,
                 "cues": result["applied"], "suppressed_by_score": result["suppressed_by_score"]}, PRODUCER))
            _unchanged(film, before, sheet=sheet, score=sp)
            _publish(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        for role in files:
            record_lock(film, f"{STEMS_DIR}/{role}.wav", producer=PRODUCER,
                        inputs=[key for key, value in before["files"].items() if value], tracked=mix.mix_tracked(film))
        return {"film": film.id, "stage": "render", "stems": str(target), "report": report}
