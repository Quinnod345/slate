"""Splicing a re-rendered range into an act render, by stream copy, with proof.

Generalized from the films' splice tool, which patched act renders dozens of times without a bad join:

    splice(film_dir, "renders/act4.mov", "renders/patches/act4_00630-02711.mov", 630, 2711)

`frame_in` and `frame_out` are the base render's own frame numbers, inclusive: the same numbers the range was rendered
with (`slate render --act N --from F --to T`), so the patch holds frame_out − frame_in + 1 frames. ProRes is
intra-only, so the base's frames before and after the range are copied untouched and the result is exact. Before the
base is replaced, the result has to prove itself:

- the frame count is unchanged;
- the frames either side of the patch (frame_in − 1 and frame_out + 1) hash the same as in the original, and the
  patch's first and last frames hash the same as frame_in and frame_out, so nothing shifted at either join;
- the patch's edge frames are compared with the original's frames around frame_in and frame_out (PSNR over ±2
  frames). A patch should start and end inside unchanged frames, so each edge must match best at its own frame
  number; a better match a frame or two away means the patch was rendered for other frame numbers, and it's refused.

The original is kept beside the result as `<name>.pre-splice.<ext>` until the registry records the new hash, then
removed. Frame times come from film.json; nothing assumes a frame rate.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from slate import gates, jsonio
from slate.clock import Clock
from slate.errors import Refused, UsageError
from slate.render import deps, registry
from slate.render.joins import BETTER_BY_DB, scan_edge
from slate.render.media import backend, frame_hash

# Below this, an edge frame differs from the original enough that the change probably reaches the patch's edge: worth
# a note, not a refusal (the films' tool said the same).
EDGE_NOTE_DB = 35.0


def _resolve(film_dir: Path, p: str | os.PathLike) -> Path:
    path = Path(p).expanduser()
    return (path if path.is_absolute() else Path(film_dir) / path).resolve()


def pre_splice_path(base: Path) -> Path:
    return base.with_name(f"{base.stem}.pre-splice{base.suffix}")


def _clock(film_dir: Path) -> Clock:
    film = deps.load_film(film_dir)
    if not film.get("fps_num") or not film.get("fps_den"):
        raise UsageError("film.json has no frame rate (fps_num, fps_den); a splice needs it to cut on frame edges.")
    return Clock(int(film["fps_num"]), int(film["fps_den"]))


def splice(film_dir: Path, base: str | os.PathLike, patch: str | os.PathLike, frame_in: int, frame_out: int) -> dict:
    """Replace frames [frame_in, frame_out] of `base` with `patch`, proven. Returns the proof (status PASS).

    Raises Refused (exit 1) and leaves `base` untouched when any check fails.
    """
    film_dir = Path(film_dir).resolve()
    base_p, patch_p = _resolve(film_dir, base), _resolve(film_dir, patch)
    a, b = int(frame_in), int(frame_out)
    for label, p in (("render", base_p), ("patch", patch_p)):
        if not p.is_file():
            raise Refused(f"There's no {label} at {p}.")
    media = backend()
    clock = _clock(film_dir)
    for label, p in (("render", base_p), ("patch", patch_p)):
        if not media.is_intra(p):
            raise Refused(f"The {label} {p.name} isn't intra-only (ProRes), so it can't be spliced by stream copy "
                          "without changing frames around the cut.")
    total, n_patch = media.count_frames(base_p), media.count_frames(patch_p)
    if not (0 <= a <= b < total):
        raise UsageError(f"Frames {a}–{b} aren't inside {base_p.name}'s 0–{total - 1}.")
    if n_patch != b - a + 1:
        raise Refused(f"The patch has {n_patch} frames; frames {a}–{b} need {b - a + 1}. Nothing was replaced.",
                      hint="Render the patch with the same --from/--to you're splicing.")
    entry = registry.find_file(film_dir, base_p)
    sha_before = jsonio.sha256(base_p)
    if entry is not None and entry.get("sha256") != sha_before:
        raise Refused(f"{entry['file']} changed on disk since it was registered, so a splice can't prove itself "
                      "against it. Nothing was replaced.", hint=f"slate render --act {entry['act']} re-renders it.")

    work = base_p.parent / f".splice-{base_p.stem}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        parts = []
        if a > 0:
            head = work / f"head{base_p.suffix}"
            media.cut_copy(base_p, 0, a, head, clock)
            parts.append(head)
        parts.append(patch_p)
        if b < total - 1:
            tail = work / f"tail{base_p.suffix}"
            media.cut_copy(base_p, b + 1, None, tail, clock)
            counted = media.count_frames(tail)
            if counted != total - b - 1:
                raise Refused(f"The tail came out {counted} frames, not {total - b - 1}; nothing was replaced.")
            parts.append(tail)
        out = work / f"spliced{base_p.suffix}"
        media.join_copy(parts, out)
        proof = prove(base_p, patch_p, out, a, b, total, n_patch, media)
        proof.update(base=registry_path(film_dir, base_p), patch=registry_path(film_dir, patch_p),
                     sha256_before=sha_before, patch_sha256=jsonio.sha256(patch_p))
        if proof["problems"]:
            raise Refused("Splice refused, nothing was replaced: " + "; ".join(proof["problems"]) + ".",
                          details={"proof": proof})
        keep = pre_splice_path(base_p)
        os.replace(base_p, keep)
        os.replace(out, base_p)
        proof["sha256_after"] = jsonio.sha256(base_p)
        if entry is not None:
            try:
                registry.update(film_dir, entry["act"], sha256=proof["sha256_after"],
                                history={"what": "splice", "frames": [a, b], "patch_sha256": proof["patch_sha256"],
                                         "sha256_before": sha_before, "sha256": proof["sha256_after"],
                                         "made_at": gates.now_iso(), "proof": _summary(proof)})
            except BaseException:
                os.replace(keep, base_p)  # the registry still describes the original, so the original goes back
                raise
            proof["registered"] = True
        keep.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    proof["gate"] = gate(proof).to_dict()
    return proof


def registry_path(film_dir: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(film_dir).as_posix()
    except ValueError:
        return str(p)


def prove(base: Path, patch: Path, out: Path, a: int, b: int, total: int, n_patch: int, media) -> dict:
    """The splice's checks, on decoded pixels. `problems` is empty when the splice holds."""
    problems: list[str] = []
    notes: list[str] = []
    counted = media.count_frames(out)
    if counted != total:
        problems.append(f"the spliced file has {counted} frames, not {total}")
        return {"frames": [a, b], "total": total, "counted": counted, "joins": [], "edges": [], "notes": notes,
                "problems": problems, "status": "FAIL"}
    base_idx = set(range(max(0, a - 2), min(total, a + 3))) | set(range(max(0, b - 2), min(total, b + 3)))
    if a > 0:
        base_idx.add(a - 1)
    if b < total - 1:
        base_idx.add(b + 1)
    base_frames = media.read_frames(base, base_idx)
    patch_frames = media.read_frames(patch, {0, n_patch - 1})
    out_idx = {a, b} | ({a - 1} if a > 0 else set()) | ({b + 1} if b < total - 1 else set())
    out_frames = media.read_frames(out, out_idx)

    joins = []
    checks = [(a, "patch", patch_frames[0], "the patch's first frame"),
              (b, "patch", patch_frames[n_patch - 1], "the patch's last frame")]
    if a > 0:
        checks.append((a - 1, "render", base_frames[a - 1], f"the original's frame {a - 1}"))
    if b < total - 1:
        checks.append((b + 1, "render", base_frames[b + 1], f"the original's frame {b + 1}"))
    for n, source, ref, label in checks:
        ok = frame_hash(out_frames[n]) == frame_hash(ref)
        joins.append({"frame": n, "source": source, "hash": frame_hash(out_frames[n]), "match": ok})
        if not ok:
            problems.append(f"frame {n} doesn't match {label}")

    edges = []
    for patch_n, n in ((0, a), (n_patch - 1, b)):
        scan = scan_edge(patch_frames[patch_n], base_frames, n)
        edges.append({"frame": n, **scan})
        if scan["shifted"]:
            best = scan["best_offset"]
            problems.append(f"the patch's frame for {n} matches the original's frame {n + best} best "
                            f"({scan['scan'][str(best)]} dB against {scan['psnr']} dB at {n}, more than "
                            f"{BETTER_BY_DB:.0f} dB better): the patch looks {best:+d} frame(s) off")
        elif scan["psnr"] != "identical" and scan["psnr"] is not None and scan["psnr"] < EDGE_NOTE_DB:
            notes.append(f"the patch's frame for {n} differs from the original's ({scan['psnr']} dB), as expected if "
                         "the change reaches the patch's edge")
    return {"frames": [a, b], "total": total, "counted": counted, "joins": joins, "edges": edges, "notes": notes,
            "problems": problems, "status": "PASS" if not problems else "FAIL"}


def _summary(proof: dict) -> dict:
    return {"status": proof["status"], "total": proof["total"],
            "joins": {str(j["frame"]): j["match"] for j in proof["joins"]},
            "edges": {str(e["frame"]): e["psnr"] for e in proof["edges"]}, "notes": proof["notes"]}


def gate(proof: dict) -> gates.GateResult:
    """The splice as a gate result, for the render's report."""
    media = backend()
    try:
        version = media.version()
    except Exception:  # noqa: BLE001 - a missing version never hides the verdict
        version = "unknown"
    a, b = proof["frames"]
    joined = ", ".join(str(j["frame"]) for j in proof["joins"])
    edges = ", ".join(f"{e['frame']}: {e['psnr']}" + ("" if e["psnr"] == "identical" else " dB")
                      for e in proof["edges"])
    summary = (f"frames {a}–{b} replaced; {proof['total']} frames; joins verified at {joined}; edges against the "
               f"original: {edges}") if proof["status"] == "PASS" else "; ".join(proof["problems"])
    return gates.GateResult("render.splice", proof["status"], summary, evidence=[proof.get("base", "")],
                            engine=gates.engine(getattr(media, "name", "media"), version), details=_summary(proof))
