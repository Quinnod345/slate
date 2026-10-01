"""Derived films: a cut-down, a new aspect or a new language, made from a parent film without copying its work.

A derived film is its own folder with `parent` and `derivation` in film.json. It:

- reuses the parent's approved takes, timings, beats and assets **by reference**: its film.lock.json entries point into
  the parent (film, folder, path and hash), nothing is copied, and `lock.resolve` finds the real file;
- keeps the parent's clock (fps), so every reused timing stays frame-exact;
- re-stages its layout with its own type scale and safe areas (recorded in `derivation.restage`; status shows the
  scenes as owed until the film records its own);
- re-plans its sound (`derivation.sound.replan`);
- runs every gate (no gate result carries over) and needs its own final-cut approval. Pre-production approvals of
  things it reuses unchanged come with it by reference, and stop counting the moment the parent's file changes.

What each kind reuses:

| reused by reference            | aspect | cut-down | language |
|--------------------------------|--------|----------|----------|
| script and storyboard          | yes    | no (a cut) | no (a translation) |
| voice takes                    | yes    | yes      | no       |
| shots and timeline             | no (rebuilt at the new size; the frames don't move) | no (re-derived for the cut) | no |
| cues, words, events, beats     | yes    | yes      | no       |
| motion traces                  | no (re-staged) | yes | no  |
| captures                       | yes    | yes      | no       |
| generated plates, score takes  | yes    | yes      | yes      |

A film that is two kinds at once (a 15-second 9:16 cut) reuses only what both kinds reuse.

timing/shots.json carries the frame size with the frames, and every engine and QA check holds it to film.json's
("one clock, one size"), so a new aspect makes its own with `slate timing`: the same frames from the parent's
storyboard and takes (read by reference), at the child's size.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slate import __version__, config, jsonio, log, paths
from slate.errors import UsageError
from slate.state import _util, approvals, autonomy, deps, film, lock, modes, production, scaffold

KINDS = ("cutdown", "aspect", "language")

# Film-relative paths (a trailing slash means a folder) and the kinds that reuse them by reference.
PATH_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("script/script.json", frozenset({"aspect"})),
    ("script/storyboard.json", frozenset({"aspect"})),
    ("script/SCRIPT.md", frozenset({"aspect"})),
    ("voice/", frozenset({"aspect", "cutdown"})),
    ("timing/shots.json", frozenset()),  # it names the frame size: each film makes its own (same frames)
    ("timing/timeline.json", frozenset()),
    ("timing/vo_cues.json", frozenset({"aspect", "cutdown"})),
    ("timing/vo_words.json", frozenset({"aspect", "cutdown"})),
    ("timing/events.json", frozenset({"aspect", "cutdown"})),
    ("timing/visual_events/", frozenset({"aspect", "cutdown"})),
    ("timing/motion/", frozenset({"cutdown"})),
    ("captures/", frozenset({"aspect", "cutdown"})),
)
# Lock entries reused wherever they live, by the command that made them.
PRODUCER_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("slate gen", frozenset(KINDS)),
    ("slate plates", frozenset(KINDS)),
    ("slate score", frozenset(KINDS)),
)
# Pre-production approvals a derived film carries by reference, per kind.
INHERIT = {
    "aspect": frozenset({"claims", "script", "voice_shortlist", "look_test"}),
    "cutdown": frozenset({"claims", "voice_shortlist", "look_test"}),
    "language": frozenset({"look_test"}),
}
PACKET_INHERIT = frozenset({"take", "plate", "score_take"})  # approvals that travel with a reused file


def _reuse_kinds(rel: str, producer: str | None) -> frozenset[str]:
    for prefix, kinds in PATH_RULES:
        if rel == prefix or (prefix.endswith("/") and rel.startswith(prefix)):
            return kinds
    for prefix, kinds in PRODUCER_RULES:
        if producer and producer.startswith(prefix):
            return kinds
    return frozenset()


def reusable(rel: str, producer: str | None, kinds: list[str]) -> bool:
    allowed = _reuse_kinds(rel, producer)
    return bool(kinds) and all(k in allowed for k in kinds)


def _candidates(parent_dir: Path) -> dict[str, str | None]:
    """{parent-relative path: producer} for everything a child could reuse (files present, and lock entries)."""
    out: dict[str, str | None] = {}
    files = lock.load(parent_dir)["files"]
    for folder in ("script", "voice", "timing", "captures"):
        for p in _util.walk_files(parent_dir / folder):
            rel = p.relative_to(parent_dir).as_posix()
            out[rel] = (files.get(rel) or {}).get("producer")
    for rel, e in files.items():
        out.setdefault(rel, e.get("producer"))
    return out


def _label(kinds: list[str], aspect: str | None, cut: float | None, language: str | None) -> str:
    parts = []
    if "cutdown" in kinds and cut:
        parts.append(f"{cut:g}-second cut")
    if "aspect" in kinds and aspect:
        parts.append(aspect)
    if "language" in kinds and language:
        parts.append(language)
    return ", ".join(parts)


def create(parent_dir: Path, child_id: str, *, format: str | None = None, aspect: str | None = None,
           language: str | None = None, cut: float | None = None, title: str | None = None, mode: str | None = None,
           films_dir: str | Path | None = None, git: bool | None = None, channel: str | None = None,
           by: str | None = None, reason: str | None = None) -> Path:
    """Make a derived film from `parent_dir` and return its folder (under the films folder by default).

    `channel` is the channel the variant serves (tiktok, youtube, …): its route from slate.formats.route_for is
    recorded in `derivation.route` (hero: the full pipeline at master quality; social: draft samples and an ffmpeg
    master), and render and master follow it.

    The child starts in its parent's mode as Slate judges it (slate.state.autonomy), so a film made autonomous can't
    shed its limits by having a child: another mode for that child needs a person at their own Terminal and a reason
    (`by` and `reason` are recorded with it)."""
    parent_dir = Path(parent_dir).resolve()
    pj = film.load(parent_dir)
    child_id = film.check_id(child_id)
    if child_id == pj["id"]:
        raise UsageError("A derived film needs its own id.")
    kinds: list[str] = []
    fmt: dict | None = None
    fmt_source = None
    if format:
        fmt, fmt_source = deps.format_load(format)
    if cut is not None:
        cut = float(cut)
        runtime = (pj.get("targets") or {}).get("runtime_s")
        if cut <= 0:
            raise UsageError("--cut is the cut-down's length in seconds, above zero.")
        if runtime and cut >= float(runtime):
            raise UsageError(f"A {cut:g}-second cut isn't shorter than the parent ({float(runtime):g} s).")
        kinds.append("cutdown")
    if aspect:
        aw, ah = film.parse_aspect(aspect)
        aspect = f"{aw}:{ah}"
    fmt_defaults = film.format_defaults(fmt, aspect=aspect if fmt and fmt.get("aspects") else None) if fmt else None
    if not aspect and fmt_defaults and fmt_defaults["aspect"] != pj.get("aspect"):
        aspect = fmt_defaults["aspect"]
    if aspect:
        if film.aspect_of(*film.parse_aspect(aspect)) == film.aspect_of(pj["width"], pj["height"]):
            if "cutdown" not in kinds and not language:
                raise UsageError(f"{pj['id']} is already {pj.get('aspect')}; name another aspect.")
        else:
            kinds.append("aspect")
    if language:
        language = language.strip()
        if language == pj.get("language"):
            raise UsageError(f"{pj['id']} is already in {language}.")
        kinds.append("language")
    if not kinds:
        raise UsageError("Say what the derived film changes: --cut SECONDS, --aspect W:H, --language xx, or a "
                         "--format with another aspect.")
    route = None
    if channel:
        r = deps.route_for(channel)
        route = {"channel": r["channel"], "route": r["route"], "label": r.get("route_label") or r["route"],
                 "quality": r.get("quality"), "pipeline": r.get("pipeline"), "render_samples": r.get("render_samples"),
                 "master": r.get("master")}
    parent_mode = autonomy.film_mode(parent_dir, pj)
    mode = mode or parent_mode or "supervised"
    film.check_mode(mode)
    autonomy.check_child(parent_dir, pj, mode, why=reason)
    pack = deps.brand_load(pj["brand"])
    modes.check_new(mode, pj["brand"], pack)

    # Size: the format's (for its aspect) or the parent's short side kept at the new aspect. Clock: the parent's.
    width, height = pj["width"], pj["height"]
    if "aspect" in kinds:
        if fmt_defaults and fmt_defaults["aspect"] == film.aspect_of(*film.parse_aspect(aspect)):
            width, height = fmt_defaults["width"], fmt_defaults["height"]
        else:
            width, height = film.size_for_aspect(aspect, pj["width"], pj["height"])
    elif fmt_defaults:
        width, height = fmt_defaults["width"], fmt_defaults["height"]
    if fmt_defaults and (fmt_defaults["fps_num"], fmt_defaults["fps_den"]) != (pj["fps_num"], pj["fps_den"]):
        log.warn(f"{child_id} keeps its parent's clock ({pj['fps_num']}/{pj['fps_den']}), not the format's "
                 f"({fmt_defaults['fps_num']}/{fmt_defaults['fps_den']}), so every reused timing stays frame-exact.")
    label = _label(kinds, aspect, cut, language)
    lang_ok = _util.supported_language(language) if language else True
    if language and not lang_ok:
        log.warn(f"v1 speaks English only: the voice, captions and word timings for {language!r} need language "
                 "tables that don't exist yet. The film is created; those stages will say so.")

    targets = dict(pj.get("targets") or {})
    if cut:
        targets["runtime_s"] = cut
        targets["runtime_range_s"] = None
    if fmt_defaults:
        targets["lufs"] = fmt_defaults["lufs"] if fmt_defaults["lufs"] is not None else targets.get("lufs")
        targets["true_peak"] = (fmt_defaults["true_peak"] if fmt_defaults["true_peak"] is not None
                                else targets.get("true_peak"))
    budget = dict(pj.get("budget") or {})
    if fmt_defaults:
        budget.update({k: v for k, v in fmt_defaults["budget"].items() if v is not None})
    if mode in ("draft", "autonomous") or pj.get("generation") == "none":
        budget["higgsfield"] = 0.0

    restage_why = []
    if "aspect" in kinds:
        restage_why.append(f"a new aspect ({aspect}): its own layout, type scale and safe areas")
    if "language" in kinds:
        restage_why.append(f"text in {language} runs to different lengths")
    if "cutdown" in kinds:
        restage_why.append("the acts are re-cut to the kept shots, and every join is checked again")
    # Every channel's zone at the child's own aspect, from the formats module when it knows the format there (any
    # format, not only a 9:16-only one); else the named format's own safe_zones.
    source_fmt = fmt or {}
    zones = deps.zones_for(fmt or pj.get("format"), aspect if "aspect" in kinds else None) or {}
    if not zones:
        for platform in deps.format_platforms(source_fmt):
            z = deps.safe_zones(source_fmt, platform)
            if z is not None:
                zones[platform] = z
    type_block = source_fmt.get("type") if isinstance(source_fmt.get("type"), dict) else {}
    type_scale = source_fmt.get("type_scale") or type_block.get("scale")
    redo = ["scenes: re-stage", "sound: re-plan", "render", "master", "qa: every gate", "deliver", "review",
            "final cut: its own approval"]
    if "cutdown" in kinds:
        redo[0:0] = ["script: choose the shots for the cut (script/script.json cut.keep)", "timing: re-derived"]
    elif "aspect" in kinds:
        redo[0:0] = ["timing: rebuilt at the new frame size (slate timing; the frames don't move)"]
    if "language" in kinds:
        redo[0:0] = [f"script: translate into {language}", "claims: approve in the new language",
                     f"voice: every line in {language}", "timing: re-derived from the new takes"]
    now = _util.now()
    derivation: dict[str, Any] = {
        "kind": kinds[0], "kinds": kinds, "aspect": aspect if "aspect" in kinds else None,
        "cut_s": cut, "language": language if "language" in kinds else None,
        "format": (fmt or {}).get("name") if fmt else None, "parent_version": film.current_version(parent_dir),
        "created_at": now,
        "restage": {"needed": True, "why": "; ".join(restage_why), "width": width, "height": height,
                    "aspect": film.aspect_of(width, height), "type_scale": type_scale, "safe_zones": zones or None,
                    "from": str(parent_dir / "scenes")},
        "sound": {"replan": True, "why": "a derived film re-plans its sound: the cut, the aspect or the language moves "
                                         "its beats and levels; approved score takes are reused by reference"},
        "gates": "every gate runs on this film; no result carries over from the parent",
        "approval": "this film needs its own final-cut approval",
        "redo": redo,
        "language_supported": lang_ok,
        "channel": route["channel"] if route else None,
        "route": route,
    }
    child_fj = {
        **{k: v for k, v in pj.items() if k not in ("pins", "pin_log", "versions", "derivation", "parent")},
        "id": child_id,
        "title": title or f"{pj.get('title')} ({label})",
        "format": (fmt or {}).get("name") or pj.get("format"),
        "format_source": fmt_source or pj.get("format_source"),
        "language": language or pj.get("language"),
        "width": width, "height": height, "aspect": film.aspect_of(width, height),
        "fps_num": pj["fps_num"], "fps_den": pj["fps_den"],
        "targets": targets, "budget": budget, "mode": mode,
        "parent": {"id": pj["id"], "dir": str(parent_dir)},
        "derivation": derivation,
        "pins": dict(pj.get("pins") or {}),
        "pin_log": [{"key": k, "event": "inherited", "at": now, "value": v, "previous": None, "approved_by": None,
                     "reason": None, "evidence": None, "source": f"parent {pj['id']}"}
                    for k, v in (pj.get("pins") or {}).items()],
        "versions": [{"n": 1, "started_at": now, "note": f"derived from {pj['id']} v{derivation['parent_version']}",
                      "delivered_at": None}],
        "created_at": now,
        "slate_version": __version__,
    }
    if "language" in kinds and isinstance(child_fj.get("voice"), dict):
        child_fj["voice"] = {**child_fj["voice"], "note": f"re-voice every line in {language}"}
    target = film._target_dir(child_id, films_dir)  # noqa: SLF001 - same package
    left = parent_mode == autonomy.AUTONOMOUS and mode != autonomy.AUTONOMOUS
    autonomy.record_created(target, child_fj, parent={"id": pj["id"], "dir": str(parent_dir), "mode": parent_mode},
                            by=(by or None) if left else None, why=reason if left else None)

    # The child's own script: none for an aspect change (it reads the parent's), a cut or a translation otherwise.
    parent_script_sha = _util.Hasher(parent_dir).sha(lock.resolve(parent_dir, "script/script.json"))
    script: dict | bool = False
    if "cutdown" in kinds or "language" in kinds:
        extra: dict[str, Any] = {"derived_from": {"film": pj["id"], "path": "script/script.json",
                                                  "sha256": parent_script_sha}}
        if "cutdown" in kinds:
            extra["cut"] = {"target_s": cut, "keep": []}
        if "language" in kinds:
            extra["translate"] = {"from": pj.get("language"), "to": language}
        script = scaffold.script_skeleton(child_fj, **extra)
    request = (f"Make a {label} version of {pj.get('title')} ({pj['id']}), as a derived film: reuse the parent's "
               "approved takes, timings, beats and assets by reference, re-stage the layout, re-plan the sound, run "
               "every gate and get its own approval.")
    asks = [("Reuse the parent's approved takes, timings, beats and assets by reference (nothing copied)", "slate variant"),
            (f"Re-stage the layout for this film: {derivation['restage']['why']}", "slate variant"),
            ("Re-plan the sound for this film", "slate variant"),
            ("Run every gate on this film", "slate variant"),
            ("Get this film's own final-cut approval", "slate variant")]
    if "cutdown" in kinds:
        asks.insert(1, (f"Cut it to {cut:g} seconds: choose the shots in script/script.json (cut.keep)", "slate variant"))
    if "language" in kinds:
        asks.insert(1, (f"Translate the script into {language} and voice every line in it", "slate variant"))
    scaffold.write(target, child_fj, request=request, request_source="slate variant", request_asks=False, asks=asks,
                   script=script, git=False)

    # References into the parent (one write), frozen ids, and approvals that travel with them.
    parent_hasher = _util.Hasher(parent_dir)
    parent_files = lock.load(parent_dir)["files"]
    refs = []
    for rel, producer in sorted(_candidates(parent_dir).items()):
        if not reusable(rel, producer, kinds):
            continue
        real = lock.resolve(parent_dir, rel)
        digest, kind = parent_hasher.any(real)
        if digest is None:
            continue
        pe = parent_files.get(rel) or {}
        refs.append({"path": rel, "parent_path": rel, "sha256": digest, "kind": kind, "locked": bool(pe.get("locked")),
                     "lock_reason": (pe.get("lock") or {}).get("reason")})
    inherited: list[dict] = []
    carry = frozenset.intersection(*(INHERIT[k] for k in kinds))
    ref_paths = {r["path"] for r in refs}
    for rec in approvals.load(parent_dir)["approvals"]:
        kind = rec.get("kind") or approvals.kind_of(rec["what"])
        wanted = kind in carry or (kind in PACKET_INHERIT and rec["path"] in ref_paths)
        if not wanted or kind == "checklist" or rec.get("basis") == "asks":
            continue
        if not approvals.check(parent_dir, rec["what"], rec["path"])["approved"]:
            continue
        if (target / rec["path"]).exists():
            continue  # the child has its own file there (a cut, a translation): that one needs its own approval
        if rec["path"] not in ref_paths:
            real = lock.resolve(parent_dir, rec["path"])
            digest, kind = parent_hasher.any(real)
            if digest is None:
                continue
            refs.append({"path": rec["path"], "parent_path": rec["path"], "sha256": digest, "kind": kind,
                         "locked": False, "note": f"approved {rec['what']} in {pj['id']}"})
            ref_paths.add(rec["path"])
        inherited.append(rec)
    parent_hasher.save()
    lock.add_refs(target, refs, parent_id=pj["id"], parent_dir=parent_dir)
    lock.inherit_frozen_ids(target, lock.frozen_shot_ids(parent_dir), parent_id=pj["id"])
    for rec in inherited:
        approvals.inherit(target, rec, parent_id=pj["id"], parent_dir=parent_dir)

    def note_reuse(data: dict) -> None:
        by_folder: dict[str, int] = {}
        for r in refs:
            top = r["path"].split("/")[0]
            by_folder[top] = by_folder.get(top, 0) + 1
        data["derivation"]["reuse"] = {"count": len(refs), "by_folder": by_folder,
                                       "paths": [r["path"] for r in refs][:200],
                                       "approvals": [f"{r['what']} {r['path']}" for r in inherited]}

    film.update(target, note_reuse)
    production.render(target)
    use_git = git if git is not None else config.load().get("films", {}).get("git_init", True)
    if use_git:
        scaffold.git_init(target, f"Start derived film {child_id} from {pj['id']} ({label})")
    return target


def children(parent_dir: Path, films_root: Path | None = None) -> list[dict]:
    """Derived films of a parent found under the films folder (for status and deliver)."""
    parent_dir = Path(parent_dir).resolve()
    root = Path(films_root) if films_root else paths.films()
    out = []
    if not root.is_dir():
        return out
    for fj_path in sorted(root.glob("*/film.json")):
        data = jsonio.read(fj_path, default={}) or {}
        parent = data.get("parent") or {}
        if parent and Path(parent.get("dir", "")).resolve() == parent_dir:
            out.append({"id": data.get("id"), "dir": str(fj_path.parent), "kinds": (data.get("derivation") or {}).get(
                "kinds")})
    return out
