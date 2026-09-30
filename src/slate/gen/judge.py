"""Judging takes: a contact sheet, numbers that help a person look, and the person's pick.

Every generated shot is judged from at least two takes, and ugly or AI-looking takes never ship, however much they
cost. A machine can't tell "reads as AI", so `slate gen judge` never passes a take on its own: it builds a contact
sheet (every take side by side, sampled across its length), measures what numbers can (black and flat frames, freezes,
flicker, size, frame rate and length against the request) and records a REVIEW gate, `gen_takes:<shot>`. Only a
person's pick (`slate gen pick --take … --by …`) turns it into a PASS, records the approval with the take's SHA-256,
and locks the take. `slate plates conform` then takes only that approved file.
"""

from __future__ import annotations

from pathlib import Path

from slate import gates, jsonio, procs
from slate.capture import media
from slate.errors import Refused, UsageError
from slate.gen import deps, policy, store

GATE_PREFIX = "gen_takes:"
SHEET_BG = (27, 27, 27)
SHEET_FG = (225, 225, 225)
SHEET_DIM = (150, 150, 150)


def _metrics(path: Path, probe: dict | None, requested: float | None) -> dict:
    """Numbers about one take (never a verdict): black and flat frames, the longest freeze, the biggest flicker."""
    import numpy as np  # noqa: PLC0415

    out: dict = {"file": path.name}
    if probe:
        out.update({k: probe.get(k) for k in ("width", "height", "fps", "duration", "kind")})
    if requested is not None and probe and probe.get("duration"):
        out["requested_duration"] = requested
        out["duration_short_by"] = round(max(0.0, requested - float(probe["duration"])), 3)
    if not probe or probe.get("kind") != "video":
        return out
    raw = procs.run([media.ffmpeg(), "-v", "error", "-i", str(path), "-vf", "scale=64:36:flags=area,format=gray",
                     "-f", "rawvideo", "-"], text=False, timeout=300, check=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 36, 64).astype(np.float32)
    if len(frames) == 0:
        out["frames_decoded"] = 0
        return out
    means = frames.mean(axis=(1, 2))
    stds = frames.std(axis=(1, 2))
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2)) if len(frames) > 1 else np.zeros(0)
    longest, run = 0, 0
    for d in diffs:
        run = run + 1 if d < 0.3 else 0
        longest = max(longest, run)
    out.update({"frames_decoded": int(len(frames)), "black_frames": int((means < 16).sum()),
                "flat_frames": int((stds < 2).sum()), "longest_freeze_frames": int(longest + 1 if longest else 0),
                "max_luma_jump": round(float(np.abs(np.diff(means)).max()) if len(means) > 1 else 0.0, 2)})
    return out


def _thumbs(path: Path, probe: dict | None, count: int, width: int) -> list:
    from PIL import Image  # noqa: PLC0415

    if probe and probe.get("kind") == "video" and probe.get("duration"):
        duration = float(probe["duration"])
        times = [(j + 0.5) * duration / count for j in range(count)]
        return media.sample(path, times, width=width)
    with Image.open(path) as im:
        img = im.convert("RGB")
        img.thumbnail((width, width * 4))
        return [img]


def contact_sheet(film_dir: Path, rec: dict, takes: list[dict], out: Path, *, samples: int = 6,
                  width: int = 320) -> Path:
    """Every take in a row, sampled across its length, labelled with what it is and what it cost."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    font = ImageFont.load_default()
    label_w, pad, head = 250, 10, 34
    rows = []
    for take in takes:
        path = store.take_file(film_dir, take)
        rows.append((take, _thumbs(path, take.get("probe"), samples, width)))
    thumb_h = max((im.size[1] for _, ims in rows for im in ims), default=width // 2)
    cols = max(len(ims) for _, ims in rows)
    sheet_w = label_w + pad + cols * (width + pad)
    sheet_h = head + len(rows) * (thumb_h + pad) + pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    title = (f"{rec.get('film') or 'film'} · {rec['shot']} · {len(rows)} takes on {rec.get('model')} · "
             "pick the best; ugly or AI-looking takes never ship")
    draw.text((pad, 10), title, fill=SHEET_FG, font=font)
    y = head
    for take, ims in rows:
        probe = take.get("probe") or {}
        lines = [f"{take['id']}  {str(take.get('request_id') or '')[:18]}",
                 f"{probe.get('width', '?')}x{probe.get('height', '?')}"
                 + (f" @ {probe.get('fps')}" if probe.get("fps") else "")
                 + (f", {float(probe['duration']):.2f} s" if probe.get("duration") else ""),
                 f"${take.get('actual_usd') if take.get('actual_usd') is not None else '?'}"
                 + ("  (replay)" if take.get("replay") else ""),
                 f"verdict: {take.get('verdict', 'unjudged')}"]
        for i, line in enumerate(lines):
            draw.text((pad, y + 4 + i * 14), line, fill=SHEET_FG if i == 0 else SHEET_DIM, font=font)
        x = label_w + pad
        for im in ims:
            sheet.paste(im, (x, y + (thumb_h - im.size[1]) // 2))
            x += width + pad
        y += thumb_h + pad
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=90)
    return out


def judge(film_dir: Path, shot_id: str, *, samples: int = 6, width: int = 320) -> dict:
    """Build the contact sheet and record the REVIEW gate: a person picks, the machine only measures."""
    film_dir = Path(film_dir)
    rec = store.require_record(film_dir, shot_id)
    done = store.completed_takes(rec)
    if len(done) < policy.MIN_TAKES:
        raise Refused(f"Judge at least {policy.MIN_TAKES} takes: {shot_id} has {len(done)} finished.",
                      hint=f"slate gen run --shot {shot_id} (it makes the missing takes)")
    sheet = contact_sheet(film_dir, rec, done, store.shot_dir(film_dir, shot_id) / "judge" / "contact_sheet.jpg",
                          samples=samples, width=width)
    requested = (rec.get("body") or {}).get("duration")
    metrics = []
    for take in done:
        m = _metrics(store.take_file(film_dir, take), take.get("probe"),
                     float(requested) if isinstance(requested, (int, float)) else None)
        metrics.append({"take": take["id"], "request_id": take.get("request_id"), "actual_usd": take.get("actual_usd"),
                        **m})
    files = [store.take_file(film_dir, t) for t in done]
    gate = gates.GateResult.review(
        f"{GATE_PREFIX}{shot_id}",
        f"{len(done)} takes of {shot_id} wait for a person's pick (ugly or AI-looking takes never ship)",
        evidence=[deps.rel(film_dir, sheet)] + [deps.rel(film_dir, f) for f in files],
        engine=gates.engine("slate gen judge", "1", sheet="contact sheet", measures="ffmpeg " + media.ffmpeg_version()),
        inputs=gates.inputs_for(film_dir, files), details={"takes": metrics})
    gates.write_report(film_dir, [gate])
    rec["judge"] = {"sheet": deps.rel(film_dir, sheet), "gate": gate.gate, "at": deps.now_iso()}
    store.save_record(film_dir, shot_id, rec, "slate gen judge")
    return {"shot": shot_id, "sheet": deps.rel(film_dir, sheet), "gate": gate.to_dict(), "takes": metrics,
            "next": f"look at the sheet, then: slate gen pick --shot {shot_id} --take <id> --by <who>"}


def pick(film_dir: Path, shot_id: str, *, take_id: str | None, by: str, reject: list[str] | None = None,
         why: str | None = None, note: str | None = None) -> dict:
    """Record a person's verdict: the approved take (hashed, recorded, locked) and any rejected ones."""
    film_dir = Path(film_dir)
    if not by or not by.strip():
        raise UsageError("Say who is judging (--by): a take is approved by a person, never by Slate.")
    if not take_id and not reject:
        raise UsageError("Pick a take (--take) or reject some (--reject), or both.")
    rec = store.require_record(film_dir, shot_id)
    if not rec.get("judge") or not (film_dir / rec["judge"]["sheet"]).is_file():
        raise Refused(f"{shot_id}'s takes haven't been put side by side yet.", hint=f"slate gen judge --shot {shot_id}")
    now = deps.now_iso()
    rejected = []
    for rid in reject or []:
        take = store.find_take(rec, rid)
        if rid == take_id:
            raise UsageError(f"{rid} can't be both picked and rejected.")
        if not why:
            raise UsageError("Say why a take is rejected (--why), so the next take can avoid it.")
        take.update(verdict="rejected", verdict_by=by, verdict_at=now, verdict_why=why)
        rejected.append(rid)
    approved = None
    if take_id:
        take = store.find_take(rec, take_id)
        if take.get("status") != "completed" or not take.get("files"):
            raise Refused(f"{take_id} didn't finish ({take.get('status')}), so it can't be picked.")
        for other in rec["takes"]:
            if other is not take and other.get("verdict") == "approved":
                other.update(verdict="rejected", verdict_by=by, verdict_at=now,
                             verdict_why=f"superseded by {take_id}")
        path = store.take_file(film_dir, take)
        take.update(verdict="approved", verdict_by=by, verdict_at=now, verdict_why=note, sha256=jsonio.sha256(path))
        rel = deps.rel(film_dir, path)
        record = deps.approvals().record(film_dir, what=f"plate-take:{shot_id}", path=rel, approved_by=by, note=note)
        locked = deps.is_locked(film_dir, rel)  # the state module locks an approved plate take by its hash
        if not locked:
            locked = deps.lock_asset(film_dir, rel, reason=f"approved take of {shot_id} (picked by {by})")
        rec["picked"] = take_id
        approved = {"take": take_id, "file": rel, "sha256": take["sha256"], "approval": record, "locked": locked}
    store.save_record(film_dir, shot_id, rec, "slate gen pick")
    left = [t for t in store.completed_takes(rec) if t.get("verdict") != "rejected"]
    evidence = [rec["judge"]["sheet"]]
    engine = gates.engine("slate gen pick", "1", judged_by=by)
    gate_name = f"{GATE_PREFIX}{shot_id}"
    if approved:
        gate = gates.GateResult.pass_(gate_name, f"{take_id} picked by {by}" +
                                      (f"; {', '.join(rejected)} rejected" if rejected else ""),
                                      evidence=evidence + [approved["file"]], engine=engine,
                                      inputs=gates.inputs_for(film_dir, [approved["file"]]),
                                      details={"picked": take_id, "rejected": rejected, "why": why, "note": note})
    elif not left:
        gate = gates.GateResult.fail(gate_name, f"every take of {shot_id} was rejected: generate more, or build the "
                                                "shot in code", evidence=evidence, engine=engine,
                                     details={"rejected": rejected, "why": why})
    else:
        gate = gates.GateResult.review(gate_name, f"{', '.join(rejected)} rejected; {len(left)} take(s) still to judge",
                                       evidence=evidence, engine=engine, details={"rejected": rejected, "why": why})
    gates.write_report(film_dir, [gate])
    return {"shot": shot_id, "approved": approved, "rejected": rejected, "gate": gate.to_dict(),
            "next": f"slate plates conform --shot {shot_id}" if approved else
            (f"slate gen run --shot {shot_id} --takes {policy.MIN_TAKES}" if not left else "keep judging")}
