"""Joining chunks into one render, and proving the join.

Two paths, chosen by what the chunks are:

- Lossless chunks (the code engines) are decoded in order and encoded to ProRes 422 HQ exactly once. The encode is the
  only lossy step, so each join is proven the way the films proved splices: the frames either side of the join are
  compared with the chunks' own edge frames over ±2 frames, and must match best at their own frame number (a better
  match one or two frames away means a frame was dropped or doubled there) and at least `PSNR_FLOOR` dB.
- ProRes chunks (from an engine that writes ProRes itself) are joined by stream copy, which is exact, so each join is
  proven by frame hashes: the decoded frames either side of every join must hash the same as the chunks' edge frames.

Either way the joined file must have exactly the frames the chunks had. Nothing is registered unless this passes.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from slate.render.chunks import Chunk
from slate.render.media import Media, db, frame_hash, psnr

# A ProRes 422 HQ frame against the 10-bit lossless frame it was encoded from measures well above this; a neighbouring
# frame of anything that moves measures far below it. The same bar the plan sets for master-vs-render.
PSNR_FLOOR = 40.0
# A neighbour has to beat the frame's own position by more than this to count as a better match (flat content
# matches everywhere, and that's fine).
BETTER_BY_DB = 1.0
SCAN = (0, -1, 1, -2, 2)  # own position first, so ties go to it


def scan_edge(ref: np.ndarray, frames: dict[int, np.ndarray], at: int) -> dict:
    """Compare `ref` with frames[at + d] for d in ±2. Returns the scan and whether `at` is the best match."""
    scan = {d: psnr(ref, frames[at + d]) for d in SCAN if (at + d) in frames}
    own = scan.get(0, -math.inf)
    best = max(scan, key=lambda d: scan[d]) if scan else 0
    shifted = best != 0 and scan[best] > own + BETTER_BY_DB
    return {"at": at, "psnr": db(own) if scan else None, "best_offset": 0 if not shifted else best,
            "scan": {str(d): db(v) for d, v in sorted(scan.items())}, "shifted": shifted}


def _window(at: int, total: int) -> range:
    return range(max(0, at - 2), min(total, at + 3))


def assemble(chunks: list[Chunk], out: Path, *, codec: str, media: Media) -> dict:
    """Join the chunks into `out` and prove it. Returns the proof; `status` is PASS only when every check held."""
    parts = [c.path for c in chunks]
    expected = sum(c.frames for c in chunks)
    method = "psnr" if codec == "lossless" else "hash"
    if method == "psnr":
        media.join_encode(parts, out, frames=expected)
    else:
        media.join_copy(parts, out)
    counted = media.count_frames(out)
    proof: dict = {"method": method, "expected": expected, "frames": counted, "chunks": len(chunks), "joins": [],
                   "problems": [], "status": "FAIL"}
    if counted != expected:
        proof["problems"].append(f"the joined file has {counted} frames; the chunks had {expected}")
        return proof

    # Where each chunk starts and ends in the joined file.
    starts, pos = [], 0
    for c in chunks:
        starts.append(pos)
        pos += c.frames
    edges = []  # (position in the joined file, chunk, frame in that chunk, label)
    for k, c in enumerate(chunks):
        edges.append((starts[k], c, 0, f"chunk {k} first"))
        edges.append((starts[k] + c.frames - 1, c, c.frames - 1, f"chunk {k} last"))
    wanted: set[int] = set()
    for at, *_ in edges:
        wanted.update(_window(at, counted) if method == "psnr" else (at,))
    joined = media.read_frames(out, wanted)
    for k, c in enumerate(chunks):
        own = media.read_frames(c.path, {0, c.frames - 1})
        for at, chunk, n, label in [e for e in edges if e[1] is c]:
            ref = own[n]
            if method == "hash":
                ok = frame_hash(joined[at]) == frame_hash(ref)
                check = {"at": at, "edge": label, "hash": frame_hash(joined[at]), "match": ok}
                if not ok:
                    proof["problems"].append(f"frame {at} of the joined file isn't {label}")
            else:
                check = {"edge": label, **scan_edge(ref, joined, at), "hash": frame_hash(joined[at])}
                own_db = check["psnr"]
                low = own_db != "identical" and (own_db is None or own_db < PSNR_FLOOR)
                check["match"] = not check["shifted"] and not low
                if check["shifted"]:
                    proof["problems"].append(f"{label} matches frame {at + check['best_offset']} of the joined file "
                                             f"better than frame {at}: a frame was dropped or doubled at that join")
                elif low:
                    proof["problems"].append(f"{label} measures {own_db} dB against frame {at}, under the "
                                             f"{PSNR_FLOOR:.0f} dB floor")
            proof["joins"].append(check)
    proof["status"] = "PASS" if not proof["problems"] else "FAIL"
    return proof
