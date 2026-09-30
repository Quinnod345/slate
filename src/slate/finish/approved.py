"""`slate approved <file>`: is this exact file approved for publishing?

Exit 0 only when the file's SHA-256 matches a person's final-cut approval (`slate approve final_cut <file> --by NAME`,
recorded by slate.state with the sha256 of exactly what they watched) and the same bytes are part of a final
delivery (deliver/vN/manifest.json, which only exists when every gate passed with nothing replayed). Anything else is
exit 1 with the reason. Oneiro posts only a file that passes this, so a re-encode, a trim, a newer render or a draft
never goes out by accident: the bytes must be the approved bytes.

The file can be anywhere (a copy in Oneiro's workspace works): the film is the one given with --film, the film
folder the file sits in, or else every film in the films folder is searched for those bytes.
"""

from __future__ import annotations

from pathlib import Path

from slate import jsonio, paths
from slate.errors import Refused, UsageError
from slate.finish import deps


def _kind(rec: dict) -> str:
    kind = str(rec.get("kind") or "")
    if kind:
        return kind
    base = str(rec.get("what") or "").split(":", 1)[0]
    return {"final-cut": "final_cut"}.get(base, base)


def _films(file: Path, film: str | None) -> list[Path]:
    if film:
        return [paths.film_dir(film)]
    root = paths.find_film_root(file.parent)
    if root is not None:
        return [root]
    base = paths.films()
    if not base.is_dir():
        return []
    return sorted(d for d in base.iterdir() if (d / "film.json").is_file())


def _manifests(film_dir: Path) -> list[Path]:
    root = Path(film_dir) / "deliver"
    return sorted(root.glob("v*/manifest.json")) + sorted((root / "drafts").glob("v*/manifest.json"))


def state_for_sha(film_dir: Path, sha: str) -> dict:
    """{approved, approval, deliveries} for a sha256 within one film."""
    approvals = [r for r in deps.approval_records(Path(film_dir)) if _kind(r) == "final_cut" and r.get("sha256") == sha]
    hits = []
    for mpath in _manifests(film_dir):
        m = jsonio.read(mpath, default=None) or {}
        for f in m.get("files") or []:
            if f.get("sha256") == sha:
                hits.append({"manifest": str(mpath), "folder": m.get("folder"), "version": m.get("version"),
                             "final": bool(m.get("final")), "file": f.get("file"), "what": f.get("what")})
    finals = [h for h in hits if h["final"]]
    approval = approvals[-1] if approvals else None
    return {"approved": bool(approval and finals), "approval": ({k: approval.get(k) for k in
                                                                  ("id", "what", "path", "approved_by", "at")}
                                                                 if approval else None),
            "deliveries": hits}


def check(file: str | Path, *, film: str | None = None) -> dict:
    """The approval of this exact file, or Refused (exit 1) saying why it isn't approved for publishing."""
    p = Path(file).expanduser()
    if not p.is_file():
        raise UsageError(f"There's no file at {p}.")
    sha = jsonio.sha256(p)
    candidates = _films(p.resolve(), film)
    seen_approval, seen_draft, seen_final = None, None, None
    for fd in candidates:
        st = state_for_sha(fd, sha)
        if st["approved"]:
            final = next(h for h in st["deliveries"] if h["final"])
            return {"approved": True, "file": str(p), "sha256": sha, "film": Path(fd).name, "film_dir": str(fd),
                    "version": final["version"], "delivered_as": f"{final['folder']}/{final['file']}",
                    "approval": st["approval"]}
        if st["approval"] and not seen_approval:
            seen_approval = (fd, st)
        if any(not h["final"] for h in st["deliveries"]):
            seen_draft = fd
        if any(h["final"] for h in st["deliveries"]):
            seen_final = fd
    short = sha[:12]
    details = {"file": str(p), "sha256": sha, "films_searched": [Path(f).name for f in candidates]}
    if seen_approval and seen_draft and not seen_final:
        raise Refused(f"{p.name} was approved, but these bytes only come from a draft delivery, and drafts are never "
                      "final.", details=details,
                      hint="Deliver the film as final (slate deliver), then approve that file.")
    if seen_approval:
        raise Refused(f"{p.name} (sha256 {short}…) was approved, but it isn't part of a final delivery yet.",
                      details=details, hint="slate deliver (every gate passes first), then run this again.")
    if seen_final:
        raise Refused(f"{p.name} (sha256 {short}…) was delivered but nobody has approved this exact file for "
                      "publishing.", details=details,
                      hint="The owner watches it, then: slate approve final_cut <this file> --by NAME")
    raise Refused(f"No approval matches {p.name} (sha256 {short}…): it isn't approved for publishing.",
                  details=details,
                  hint="Only a file the owner approved byte for byte may be posted (slate approve final_cut <file> "
                       "--by NAME, after they watch it).")
