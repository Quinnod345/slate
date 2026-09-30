"""The delivery set (docs/PLAN.md §7 stage 12; delivery_manifest.py generalized).

`slate deliver` puts one version's deliverables in deliver/vN/:

    masters/      the ProRes 422 HQ and H.264 masters (with the final mix)
    sound/        the final mix, its record and the delivery stems
    captions/     SRT, WebVTT and the machine cues
    cuts/         a half-size review cut and a phone cut (clean: no burn-in), made from the master
    thumbnails/   stills from the master
    platform/     the platform's own files when the film's format names them (encode.files: the App Store
                  preview's H.264, the hero loop's MP4 and WebM), encoded from the master and checked against the
                  platform's frame-rate and size limits
    ae/           the editable After Effects project, when After Effects finished the film
    validation/   every gate's latest result
    script/       the script
    manifest.json written last: every file's size, sha256 and what ffprobe reads; anything expected but missing;
                  the gates; and, internal only, which voices, shots and music are synthetic

Rules:

- **Every film gate passes first** (slate.finish.checks.film_gates): every gate slate.qa knows, the master gates, the
  AE audit for an AE finish, and any other gate recorded for the film. UNCHECKED and STALE are not passes.
- **Drafts are never final**, and **any replayed stand-in blocks a final delivery** (slate.finish.synthetic.replayed).
  `slate deliver --draft` makes a non-final set in deliver/drafts/vN.K/ instead, marked as such.
- **Every file is "not approved for publishing"** until the owner approves that exact sha256
  (`slate approve final_cut <file> --by NAME`); `slate approved <file>` is the check Oneiro runs before posting.
- **No AI labels** unless a law requires one; the manifest records what's synthetic, internal only, and each
  platform's labelling rule as the owner's call.
- **A delivered version is never overwritten.** The set is built in a staging folder and moved into place only when
  complete; a folder that exists is refused, and delivered files are made read-only. When the film's content has
  moved on since vN was delivered or reviewed, the delivery starts the next version.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from slate import gates, jsonio, log, procs
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.finish import checks, cuts, deps, media, synthetic, versions
from slate.finish.film import Film

COMMAND = "slate deliver"
PUBLISHING = "not approved for publishing"
DRAFTS = "deliver/drafts"
RULE = ("Every file here is not approved for publishing until the owner approves that exact file (its sha256). After "
        "their yes, only that exact file is posted, never another version.")
DRAFT_APPROVE = ("Nothing in a draft is ever approved for publishing: deliver the film as final (slate deliver), then "
                 "the owner approves a file from deliver/vN/.")
MASTER_LABELS = {"prores_422_hq": "master, ProRes 422 HQ", "h264": "master, H.264"}


# ---------------------------------------------------------------------------------------------------------------
# What stands in the way


def _masters(film: Film) -> tuple[dict, list[dict]]:
    rec = film.master_record()
    if not rec or not rec.get("masters"):
        raise MissingPrerequisite("There's no master to deliver yet.", hint="slate master")
    ms = film.masters()
    changed = [m["file"] for m in ms if not m["intact"]]
    if changed:
        them = "it" if len(changed) == 1 else "them"
        raise Refused(f"{', '.join(changed)} changed since `slate master` made {them}.",
                      hint="slate master makes them again, with their gates")
    current, why = film.master_current()
    if not current:
        raise Refused(why[:1].upper() + why[1:] + ".", hint="slate master, then slate qa, then deliver")
    return rec, ms


def platform_specs(film: Film) -> tuple[dict, list[dict]]:
    """(the format's encode block, its platform files) at the film's aspect; ({}, []) when the format names none."""
    fmt = deps.film_format(film.data) or {}
    enc = fmt.get("encode") if isinstance(fmt.get("encode"), dict) else {}
    return enc, [f for f in enc.get("files") or [] if isinstance(f, dict) and f.get("id") and f.get("container")]


def platform_problems(film: Film, enc: dict, specs: list[dict]) -> list[str]:
    """What stops this film's platform files meeting their spec before anything is encoded (its clock, for now)."""
    max_fps = enc.get("max_fps")
    if specs and max_fps and film.clock.fps_num / film.clock.fps_den > float(max_fps) + 1e-9:
        names = ", ".join(str(s.get("label") or s["id"]) for s in specs)
        return [f"the platform takes at most {float(max_fps):g} fps and the film runs at {film.rate} ({names})"]
    return []


def blockers(film: Film) -> dict:
    """Everything that stops a final delivery right now (empty lists when nothing does)."""
    why: list[str] = []
    if film.mode == "draft":
        why.append("draft films are never delivered as final")
    enc, specs = platform_specs(film)
    platform = platform_problems(film, enc, specs)
    why += platform
    replayed = synthetic.replayed(film)
    if replayed:
        listed = ", ".join(r["what"] for r in replayed[:4]) + (f" and {len(replayed) - 4} more" if len(replayed) > 4
                                                               else "")
        why.append(f"{len(replayed)} replayed stand-in{'s are' if len(replayed) != 1 else ' is'} in use ({listed}): "
                   "make them for real with --live first")
    for r in synthetic.unapproved_plates(film):
        why.append(f"{r['what']} ({r['record']})")
    fg = checks.film_gates(film)
    if not fg["passed"]:
        names = ", ".join(f"{b['gate']} {b['status']}" for b in fg["blocking"][:8])
        more = f" and {len(fg['blocking']) - 8} more" if len(fg["blocking"]) > 8 else ""
        why.append(f"not every film gate passes: {names}{more}")
    parked = [q for q in deps.open_questions(film.dir) if q.get("parks")]
    if parked:
        why.append(f"the film is parked, waiting on a person: {parked[0].get('text')}")
    return {"why_not_final": why, "gates": fg, "replayed": replayed, "platform": platform}


def _hint(film: Film, b: dict) -> str:
    if film.mode == "draft":
        return "slate deliver --draft makes a non-final set; a final delivery needs a supervised or express film"
    if b["replayed"]:
        return "re-make the replayed assets with --live (they spend), or deliver a draft with --draft"
    if b.get("platform"):
        return ("the platform needs a slower clock than this film's, and a derived film keeps its parent's: make the "
                "film from the platform's own format (slate new … --format <format>), or deliver a draft with --draft")
    if b["gates"]["blocking"]:
        return checks.next_for(b["gates"]["blocking"])
    return "slate film questions shows what the film is waiting on"


# ---------------------------------------------------------------------------------------------------------------
# The set


def _copy_items(film: Film, rec: dict, masters: list[dict]) -> tuple[list[dict], list[dict]]:
    """(items to copy, expected-but-missing) as {what, kind, src, dest}."""
    items, missing = [], []
    for m in masters:
        label = MASTER_LABELS.get(m["kind"], f"master, {m['kind']}")
        label += " + final mix" if m.get("audio") else " (no sound)"
        items.append({"what": label, "kind": f"master:{m['kind']}", "src": m["path"],
                      "dest": f"masters/{Path(m['file']).name}"})
    mix = film.mix()
    if mix is not None:
        items.append({"what": "final mix, 48 kHz / 24-bit", "kind": "mix", "src": mix, "dest": f"sound/{mix.name}"})
        rec_path = film.mix_record()
        if rec_path is not None:
            items.append({"what": "mix record (loudness, gains)", "kind": "mix_record", "src": rec_path,
                          "dest": f"sound/{rec_path.name}"})
        stems = film.stems()
        for name, p in sorted(stems.items()):
            items.append({"what": f"stem: {name}", "kind": "stem", "src": p, "dest": f"sound/stems/{p.name}"})
        if not stems:
            missing.append({"what": "delivery stems", "expected": "sound/mix/stems/*.wav (slate mix writes them)"})
    else:
        missing.append({"what": "final mix", "expected": "sound/mix/final_mix.wav (slate mix)"})
    caps = film.captions()
    labels = {".srt": "captions, SRT", ".vtt": "captions, WebVTT", ".json": "captions, cues (JSON)"}
    for p in caps:
        items.append({"what": labels.get(p.suffix.lower(), "captions"), "kind": "captions", "src": p,
                      "dest": f"captions/{p.name}"})
    for suffix, what in ((".srt", "captions, SRT"), (".vtt", "captions, WebVTT")):
        if not any(p.suffix.lower() == suffix for p in caps):
            missing.append({"what": what, "expected": f"voice/captions/*{suffix} (slate voice captions)"})
    if rec.get("method") == "ae":
        aep = film.path(rec["aep"]["file"]) if rec.get("aep") else None
        if aep is not None and aep.is_file():
            items.append({"what": "After Effects project (editable)", "kind": "ae_project", "src": aep,
                          "dest": f"ae/{aep.name}"})
        else:
            missing.append({"what": "After Effects project", "expected": "ae/<film>.aep (slate master --ae --go)"})
    report = film.path("qa/gates.json")
    if report.is_file():
        items.append({"what": "validation: every gate's latest result", "kind": "validation", "src": report,
                      "dest": "validation/gates.json"})
    script = film.path("script/SCRIPT.md")
    if script.is_file():
        items.append({"what": "the script", "kind": "script", "src": script, "dest": "script/SCRIPT.md"})
    seen: set[str] = set()
    for it in items:  # two sources with one name (timing/captions.json, voice/captions/captions.json) both arrive
        if it["dest"] in seen:
            dest = Path(it["dest"])
            it["dest"] = str(dest.with_name(f"{Path(it['src']).parent.name}-{dest.name}"))
        seen.add(it["dest"])
    return items, missing


def _platform_files(film: Film, staging: Path, n: int, src, master_rel: str, *, final: bool, missing: list[dict],
                    skip: bool) -> list[dict]:
    """Encode the format's platform files from the master into staging/platform/. A file over the platform's size
    limit refuses a final delivery (and is left out of a draft, named in `missing`); so does a clock the platform
    doesn't take (checked before anything is encoded)."""
    enc, specs = platform_specs(film)
    if not specs:
        return []
    fmt = film.data.get("format")
    if skip:  # only a draft gets here with a clock the platform doesn't take (blockers refuse a final delivery)
        for spec in specs:
            missing.append({"what": str(spec.get("label") or spec["id"]),
                            "expected": f"platform/{spec['id']}: " + "; ".join(platform_problems(film, enc, specs))})
        return []
    out = []
    limit = enc.get("max_bytes")
    for spec in specs:
        log.info(f"deliver v{n}: {spec.get('label') or spec['id']}")
        got = cuts.platform_file(film, spec, staging / "platform" / f"{film.id}_v{n}_{spec['id']}.{spec['container']}",
                                 source=src)
        path, size = Path(got["file"]), int(got["bytes"])
        if limit and size > int(limit):
            path.unlink(missing_ok=True)
            problem = (f"{spec.get('label') or spec['id']} came out at {size:,} bytes, over the platform's limit of "
                       f"{int(limit):,}")
            if final:
                raise Refused(f"Not a final delivery yet: {problem}.",
                              hint=f"Shorten the film, or lower the bitrate in the {fmt} format's encode spec.")
            missing.append({"what": str(spec.get("label") or spec["id"]), "expected": problem})
            continue
        out.append(_entry(staging, path, what=str(spec.get("label") or spec["id"]), kind=f"platform:{spec['id']}",
                          source=f"made from {master_rel} to the {fmt} format's encode spec"))
    return out


def _entry(folder: Path, path: Path, *, what: str, kind: str, source: str | None, sha: str | None = None) -> dict:
    return {"what": what, "kind": kind, "file": path.relative_to(folder).as_posix(), "source": source,
            "bytes": path.stat().st_size, "sha256": sha or jsonio.sha256(path), "media": media.probe_summary(path),
            "publishing": PUBLISHING}


def _next_draft(film: Film, n: int) -> int:
    root = film.path(DRAFTS)
    nums = []
    if root.is_dir():
        for d in root.iterdir():
            name = d.name
            if name.startswith(f"v{n}.") and name[len(f"v{n}."):].isdigit():
                nums.append(int(name[len(f"v{n}."):]))
    return max(nums, default=0) + 1


def _read_only(folder: Path) -> None:
    for p in folder.rglob("*"):
        if p.is_file():
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def plan(film_dir: Path, *, final: bool = True) -> dict:
    """What `slate deliver` would do (--dry-run), changing nothing."""
    film = Film(Path(film_dir))
    out: dict = {"film": film.id, "final": final, "dry_run": True}
    try:
        rec, ms = _masters(film)
    except SlateError as exc:
        return {**out, "would": "refuse", "why": [exc.message], "hint": exc.hint}
    b = blockers(film)
    ver = versions.resolve(film, start=False)
    n = ver["version"]
    items, missing = _copy_items(film, rec, ms)
    out.update(version=n, would_start_version=bool(ver.get("would_start")), method=rec.get("method"),
               copies=[i["dest"] for i in items],
               makes=["cuts/ (review and phone cuts)", "thumbnails/"] + [
                   f"platform/{film.id}_v{n}_{s['id']}.{s['container']} ({s.get('label') or s['id']})"
                   for s in platform_specs(film)[1]],
               missing=missing, why_not_final=b["why_not_final"], blocking=b["gates"]["blocking"])
    if final and versions.delivered(film, n) and not ver.get("would_start"):
        out["would"] = "nothing: this version is delivered and nothing has changed"
    elif final and b["why_not_final"]:
        out["would"] = "refuse"
        out["hint"] = _hint(film, b)
    else:
        out["would"] = f"deliver to {'deliver/v' + str(n) if final else DRAFTS + '/v' + str(n) + '.N'}"
    return out


def deliver(film_dir: Path, *, final: bool = True) -> dict:
    """Make the delivery set and its manifest. Returns what was delivered (see the module docstring)."""
    film = Film(Path(film_dir))
    media.require()
    rec, ms = _masters(film)
    if final:
        peek = versions.resolve(film, start=False)
        if not peek.get("would_start") and versions.delivered(film, peek["version"]):
            n = peek["version"]
            m = versions.manifest_record(film, n) or {}
            return {"status": "already_delivered", "film": film.id, "version": n, "final": bool(m.get("final", True)),
                    "folder": film.rel(film.deliver_dir(n)),
                    "manifest": film.rel(film.deliver_dir(n) / "manifest.json"),
                    "delivered_at": m.get("delivered_at"),
                    "message": f"v{n} was delivered on {str(m.get('delivered_at'))[:16]} and nothing has changed "
                               "since; a delivered version is never overwritten."}
    b = blockers(film)
    if final:
        try:
            deps.modes_check(film.dir, "deliver_final")
        except Refused as exc:
            if not b["why_not_final"]:  # the film's mode rules found something the checks above didn't
                b["why_not_final"].append(exc.message)
        if b["why_not_final"]:
            raise Refused("Not a final delivery yet: " + "; ".join(b["why_not_final"]) + ".", hint=_hint(film, b),
                          details={"why_not_final": b["why_not_final"], "blocking": b["gates"]["blocking"],
                                   "replayed": b["replayed"]})
        fp = film.fingerprint()
        ver = versions.resolve(film, fingerprint=fp)
        n = ver["version"]
        target = film.deliver_dir(n)
        if versions.delivered(film, n):
            m = versions.manifest_record(film, n) or {}
            return {"status": "already_delivered", "film": film.id, "version": n, "final": True,
                    "folder": film.rel(target), "manifest": film.rel(target / "manifest.json"),
                    "delivered_at": m.get("delivered_at"),
                    "message": f"v{n} was delivered on {str(m.get('delivered_at'))[:16]} and nothing has changed "
                               "since; a delivered version is never overwritten."}
        if target.exists():
            raise Refused(f"{film.rel(target)} already exists without a manifest; Slate never writes over it.",
                          hint="Look inside it, and move it aside yourself if it's a stray.")
        draft_no = None
    else:
        n = film.version()
        draft_no = _next_draft(film, n)
        target = film.path(f"{DRAFTS}/v{n}.{draft_no}")
        fp = film.fingerprint()
        ver = {"version": n, "started": False}
    items, missing = _copy_items(film, rec, ms)
    staging = target.parent / f".staging-{target.name}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    entries: list[dict] = []
    try:
        for it in items:
            dest = staging / it["dest"]
            got = media.copy_exact(it["src"], dest)
            entries.append(_entry(staging, dest, what=it["what"], kind=it["kind"], source=film.rel(it["src"]),
                                  sha=got["sha256"]))
        primary = film.primary_master()
        src = cuts.Source("master", [primary["path"]], [0], None, bool(primary.get("audio")))
        master_rel = film.rel(primary["path"])
        log.info(f"deliver v{n}: review and phone cuts from {master_rel}")
        half = cuts.review_half(film, staging / "cuts" / f"{film.id}_v{n}_review_half.mp4", source=src)
        entries.append(_entry(staging, Path(half["file"]), what="whole-film review cut, half size", kind="cut:review",
                              source=f"made from {master_rel}"))
        phone = cuts.phone_cut(film, staging / "cuts" / f"{film.id}_v{n}_phone.mp4", source=src, burn=False)
        entries.append(_entry(staging, Path(phone["file"]), what="phone cut", kind="cut:phone",
                              source=f"made from {master_rel}"))
        log.info(f"deliver v{n}: thumbnails")
        for t in cuts.thumbnails(film, staging / "thumbnails", source=src):
            entries.append(_entry(staging, Path(t["file"]),
                                  what=f"thumbnail, film frame {t['frame']} ({t['timecode']}"
                                       + (f", {t['shot']})" if t["shot"] else ")"),
                                  kind="thumbnail", source=f"made from {master_rel}"))
        entries += _platform_files(film, staging, n, src, master_rel, final=final, missing=missing,
                                   skip=bool(b.get("platform")))
        folder_rel = film.rel(target)
        post = next((e["file"] for e in entries if e["kind"] == "master:h264"), entries[0]["file"])
        manifest = {
            "generated": f"do not edit by hand: {COMMAND}",
            "schema": 1,
            "film": {"id": film.id, "title": film.title, "brand": film.brand, "format": film.data.get("format"),
                     "mode": film.mode, "language": film.language, "fps": film.rate, "width": film.size[0],
                     "height": film.size[1], "frames": film.total_frames, "seconds": round(film.seconds, 6)},
            "version": n,
            "draft_number": draft_no,
            "final": bool(final),
            "why_not_final": [] if final else (b["why_not_final"] or ["delivered as a draft on request (--draft)"]),
            "delivered_at": gates.now_iso(),
            "delivered_by": procs.agent_context() or "terminal",
            "folder": folder_rel,
            "method": rec.get("method") or "ffmpeg",
            "content_fingerprint": fp,
            "publishing": {"approved": False, "status": PUBLISHING, "rule": RULE,
                           "approve": (f"slate approve final_cut {folder_rel}/{post} --by NAME" if final
                                       else DRAFT_APPROVE),
                           "check": "slate approved <file>   (exit 0 only when that exact file was approved)"},
            "gates": {"passed": b["gates"]["passed"], "required": b["gates"]["required"],
                      "results": [{"gate": r["gate"], "status": r["status"], "summary": r.get("summary"),
                                   "checked_at": r.get("checked_at"), "engine": r.get("engine")}
                                  for r in b["gates"]["results"]],
                      "blocking": b["gates"]["blocking"]},
            "files": entries,
            "missing": missing,
            "replayed": b["replayed"],
            "internal": {"note": synthetic.NOTE, "synthetic_media": synthetic.record(film),
                         "labels": synthetic.labels(film)},
        }
        problems = jsonio.validate(manifest, "manifest")
        if problems:
            raise UsageError("The manifest doesn't match its schema:\n  " + "\n  ".join(problems[:10]))
        jsonio.write_atomic(staging / "manifest.json", manifest)  # written last, after every file above
        if target.exists():
            raise Refused(f"{folder_rel} appeared while this delivery was being made; Slate never writes over it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    _read_only(target)
    # film.json isn't touched here: nearly every gate hashes it, so a write now would turn them all STALE. The
    # delivery's own record is its manifest (slate.finish.versions reads delivered versions from disk).
    deps.lock_record(film.dir, f"{folder_rel}/manifest.json", producer=COMMAND,
                     inputs=[m["file"] for m in rec.get("masters") or []])
    return {"status": "delivered", "film": film.id, "version": n, "final": bool(final),
            "started_version": bool(ver.get("started")), "folder": folder_rel,
            "manifest": f"{folder_rel}/manifest.json", "files": len(entries),
            "bytes": sum(e["bytes"] for e in entries), "missing": missing,
            "why_not_final": manifest["why_not_final"], "publishing": PUBLISHING,
            "approve": manifest["publishing"]["approve"]}


def run(film_dir: Path, *, final: bool = True) -> Path:
    """Deliver the film (docs/ARCHITECTURE.md §12). Returns the manifest's path (written last)."""
    out = deliver(film_dir, final=final)
    return Film(Path(film_dir)).path(out["manifest"])


def listing(film_dir: Path) -> list[dict]:
    """Every delivered set, final and draft, with each master's live approval state."""
    from slate.finish import approved as approved_mod  # noqa: PLC0415

    film = Film(Path(film_dir))
    rows = []
    manifests = (sorted(film.path("deliver").glob("v*/manifest.json"))
                 + sorted(film.path(DRAFTS).glob("v*/manifest.json")))
    for mpath in manifests:
        m = jsonio.read(mpath, default=None) or {}
        masters = []
        for f in m.get("files") or []:
            if str(f.get("kind", "")).startswith("master"):
                state = approved_mod.state_for_sha(film.dir, f["sha256"])
                masters.append({"file": f"{m.get('folder')}/{f['file']}", "sha256": f["sha256"],
                                "publishing": "approved for publishing" if state["approved"] else PUBLISHING,
                                "approval": state.get("approval")})
        rows.append({"folder": m.get("folder"), "version": m.get("version"), "final": m.get("final"),
                     "delivered_at": m.get("delivered_at"), "files": len(m.get("files") or []),
                     "missing": len(m.get("missing") or []), "masters": masters})
    return rows


def describe(out: dict) -> str:
    if out.get("status") == "already_delivered":
        return out["message"]
    lines = [f"Delivered {out['film']} v{out['version']}" + ("" if out["final"] else " as a draft (never final)")
             + f" to {out['folder']}: {out['files']} files, {out['bytes'] / 1e6:.1f} MB, manifest written last."]
    if out["final"]:
        lines += [f"Every file is {PUBLISHING} until the owner approves that exact file:", f"  {out['approve']}"]
    else:
        lines.append(out["approve"])
    if out.get("missing"):
        lines.append("Expected but missing: " + ", ".join(m["what"] for m in out["missing"]) + ".")
    if not out["final"]:
        lines.append("Why it isn't final: " + "; ".join(out["why_not_final"]) + ".")
    return "\n".join(lines)
