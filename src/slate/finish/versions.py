"""Which version a packet or a delivery belongs to.

Versions are numbered (film.json "versions", kept by slate.state). A version's review packet and its delivery must
show the same film, so a version is "taken" once a packet or a final delivery records its content fingerprint (the
act renders, mix, stems, shots and captions: see slate.finish.film.Film.content). When the film's content moves on
after that (notes were fixed, a shot re-rendered, the mix redone), the next packet or delivery starts the next
version. A delivered version is never overwritten; asking to deliver unchanged content again says so and changes
nothing.
"""

from __future__ import annotations

from slate import jsonio
from slate.errors import MissingPrerequisite
from slate.finish import deps
from slate.finish.film import Film


def packet_record(film: Film, n: int) -> dict | None:
    rec = jsonio.read(film.review_dir(n) / "packet.json", default=None)
    return rec if isinstance(rec, dict) else None


def manifest_record(film: Film, n: int) -> dict | None:
    rec = jsonio.read(film.deliver_dir(n) / "manifest.json", default=None)
    return rec if isinstance(rec, dict) else None


def delivered(film: Film, n: int) -> bool:
    """True when deliver/vN holds a final delivery (its manifest is written last, so a manifest means complete)."""
    return (film.deliver_dir(n) / "manifest.json").is_file()


def moved_on(film: Film, n: int, fingerprint: str) -> list[str]:
    """Why version n can't take this content any more (empty when it still can)."""
    why = []
    pk = packet_record(film, n)
    if pk and pk.get("content_fingerprint") and pk["content_fingerprint"] != fingerprint:
        why.append(f"v{n}'s review packet shows different renders or sound")
    mf = manifest_record(film, n)
    if mf and mf.get("content_fingerprint") and mf["content_fingerprint"] != fingerprint:
        why.append(f"v{n} was delivered with different renders or sound")
    return why


def next_version(film: Film, note: str) -> int:
    """Start the next version through slate.state (never reuses a number)."""
    before = film.version()
    n = deps.new_version(film.dir, note)
    guard = 0
    while n <= before and guard < 3:  # a hand-made film.json without a versions list starts at 1
        n = deps.new_version(film.dir, note)
        guard += 1
    if n <= before:
        raise MissingPrerequisite(f"Couldn't start version {before + 1} in film.json.",
                                  hint="slate status shows the film's versions.")
    film.data = deps.film_load(film.dir)
    return n


def resolve(film: Film, *, fingerprint: str | None = None, start: bool = True) -> dict:
    """{version, started: bool, why: [...], fingerprint} for new work on this film's content.

    With start=False nothing is written: `version` is what the next version would be (for --dry-run).
    """
    fp = fingerprint or film.fingerprint()
    n = film.version()
    why = moved_on(film, n, fp)
    if not why:
        return {"version": n, "started": False, "why": [], "fingerprint": fp}
    if not start:
        return {"version": n + 1, "started": False, "would_start": True, "why": why, "fingerprint": fp}
    new = next_version(film, f"after v{n}: " + "; ".join(why))
    return {"version": new, "started": True, "why": why, "fingerprint": fp}
