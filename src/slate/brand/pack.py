"""Brand packs: find one, load it, and answer what the rest of Slate asks of it (docs/ARCHITECTURE.md §7 and §12).

A pack is a folder with a `brand.json` and its assets (logos, references, onboarding records, the brand's own agent
skills). Two neutral packs ship in the repo, `brands/_template` and `brands/demo`; every real brand lives in the
private store (`paths.brands_store()`, `~/code/slate-brands` by default), which stays on this Mac. Packs are found in
that order: the repo's neutral packs, then the store.

**brand.json is the one source of truth.** Every engine reads it as it is: the Remotion kit (logo SVGs inlined by the
engine), the After Effects kit, the motion and accessibility gates, the voice, sound and story stages. Files next to it
that repeat part of it (`motion.json`, `MOTION.md`, `onboarding/truth/TRUTH.md`) are written from it by onboarding and
checked by `slate brand validate`, so they can't quietly drift.

**The four references.** Onboarding ends with four approved references: truth, look and motion, voice and sound. Each
approval (`slate brand approve`) records who approved it, when, the SHA-256 of the approved file, and a fingerprint of
the part of brand.json it covers (the claims for truth, the palette, type, logos and motion law for the look, the
voice's id, model, settings and seed, the sound palette). A reference counts as approved only while both still match:
change the file, or change what it covered, and it needs a person again. That is what "later films are checked against
these references" rests on, and why autonomous films refuse a brand until all four hold.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Iterable

from slate import jsonio, paths
from slate.brand import colour
from slate.errors import Refused, UsageError

PACK_FILE = "brand.json"
REFERENCES = ("truth", "look_motion", "voice", "sound")
NEUTRAL = ("_template", "demo")
#: Which onboarding step produces each reference.
STEP_OF = {"truth": "truth", "look_motion": "look", "voice": "voice", "sound": "sound"}
RUNTIME_KEYS = ("_dir", "_source", "_problems")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

#: The part of brand.json each reference's approval covers (dotted keys).
COVERS: dict[str, tuple[str, ...]] = {
    "truth": ("truth",),
    "look_motion": ("palette", "contrast_pairs", "type.families", "type.scale", "logos", "motif", "look.id", "motion"),
    "voice": ("voice.provider", "voice.source", "voice.voice_id", "voice.model", "voice.settings", "voice.seed"),
    "sound": ("sound",),
}
#: Keys that document rather than decide: editing them never un-approves a reference.
DOC_KEYS = frozenset({"note", "notes", "lineage", "why", "comment", "comments", "about", "description", "licence",
                      "license", "source_note", "evidence"})


# ------------------------------------------------------------------------------------------------------- locating
def check_name(name: str) -> str:
    """A brand's name is its folder's name: lowercase letters, digits, dots, dashes and underscores."""
    text = str(name or "").strip()
    if text in NEUTRAL:
        return text
    if not NAME_RE.match(text):
        raise UsageError(f"{name!r} can't be a brand name: use lowercase letters, digits, dots, dashes and "
                         "underscores, starting with a letter or digit (at most 64).")
    return text


def _looks_like_path(ref: str) -> bool:
    return os.sep in ref or ref.startswith((".", "~")) or Path(ref).is_absolute()


def find(name: str | os.PathLike) -> Path:
    """The folder of a brand pack, by name (repo's neutral packs first, then the private store) or by path."""
    ref = str(name)
    p = Path(ref).expanduser()
    if p.name == PACK_FILE and p.is_file():
        return p.parent.resolve()
    if _looks_like_path(ref) and (p / PACK_FILE).is_file():
        return p.resolve()
    if not _looks_like_path(ref):
        for base in paths.brand_dirs():
            candidate = Path(base) / ref
            if (candidate / PACK_FILE).is_file():
                return candidate.resolve()
    searched = ", ".join(str(d) for d in paths.brand_dirs())
    raise UsageError(f"No brand pack called {ref!r} (looked in {searched}).",
                     hint=f"Make one with `slate brand init {ref}`; the neutral pack is 'demo'.")


def in_repo(folder: str | os.PathLike) -> bool:
    """Is this folder inside Slate's own repo (where only the neutral packs may live)?"""
    try:
        Path(folder).resolve().relative_to(paths.REPO.resolve())
        return True
    except ValueError:
        return False


def is_neutral(folder: str | os.PathLike) -> bool:
    """One of the two packs Slate ships (brands/_template, brands/demo)."""
    f = Path(folder).resolve()
    return f.parent == paths.BRANDS_REPO.resolve() and f.name in NEUTRAL


def where(folder: str | os.PathLike) -> str:
    f = Path(folder).resolve()
    if in_repo(f):
        return "repo"
    try:
        f.relative_to(paths.brands_store().resolve())
        return "store"
    except ValueError:
        return "path"


def pack_dir(pack: dict) -> Path | None:
    d = pack.get("_dir") if isinstance(pack, dict) else None
    return Path(d) if d else None


# -------------------------------------------------------------------------------------------------------- reading
def read_raw(folder: str | os.PathLike) -> dict:
    """brand.json exactly as written (for commands that change a pack and save it back)."""
    path = Path(folder) / PACK_FILE
    data = jsonio.read(path)
    if not isinstance(data, dict):
        raise UsageError(f"{path} isn't a brand pack (expected a JSON object).")
    return data


def load(name_or_path: str | os.PathLike | dict) -> dict:
    """A brand pack as the rest of Slate reads it: brand.json, plus `_dir` (its folder) and `_source` (repo, store or
    path). The generator-uploads decision is mirrored into `grounding.uploads_allowed` (what the generate stage
    reads). A pack that's already loaded is returned as it is. Never raises on content: `slate brand validate` judges
    content; this only refuses a pack that isn't there or isn't JSON."""
    if isinstance(name_or_path, dict):
        return name_or_path
    folder = find(name_or_path)
    pack = copy.deepcopy(read_raw(folder))
    pack.setdefault("name", folder.name)
    pack["_dir"] = str(folder)
    pack["_source"] = where(folder)
    problems: list[str] = []
    mot = pack.get("motion")
    if isinstance(mot, str):  # a path to a law file: readable here, but the engines need it inline (validate says so)
        law = jsonio.read(folder / mot, default=None)
        pack["motion"] = law if isinstance(law, dict) else {}
        problems.append(f"motion points at {mot!r}; engines read brand.json as it is, so the law must be inline")
    grounding = pack.get("grounding") if isinstance(pack.get("grounding"), dict) else {}
    pack["grounding"] = {**grounding, "uploads_allowed": uploads_allowed(pack)}
    if problems:
        pack["_problems"] = problems
    return pack


def _as_pack(pack: Any) -> dict:
    return load(pack) if isinstance(pack, (str, os.PathLike)) else (pack if isinstance(pack, dict) else {})


def strip_runtime(pack: dict) -> dict:
    return {k: v for k, v in pack.items() if k not in RUNTIME_KEYS}


def save(folder: str | os.PathLike, data: dict, *, allow_repo: bool = False) -> Path:
    """Write brand.json atomically. The repo's packs are neutral and read-only to commands."""
    folder = Path(folder)
    if in_repo(folder) and not allow_repo:
        raise Refused(f"{folder} is in Slate's repo, which holds only the neutral packs; commands don't change it.",
                      hint="Make your own copy in the private store: slate brand init <name> --from-pack demo")
    return jsonio.write_atomic(folder / PACK_FILE, strip_runtime(data))


def list_packs() -> list[dict]:
    """Every pack Slate can see, repo first, with its onboarding state."""
    out: list[dict] = []
    seen: set[str] = set()
    for base in paths.brand_dirs():
        base = Path(base)
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if (p / PACK_FILE).is_file()):
            row = {"name": folder.name, "dir": str(folder), "where": where(folder), "neutral": is_neutral(folder),
                   "shadowed": folder.name in seen}
            seen.add(folder.name)
            try:
                st = status(load(folder))
                row.update(status=st["status"], missing=st["missing"])
            except Exception as exc:  # noqa: BLE001 - one broken pack must not hide the others
                row.update(status="unreadable", error=str(getattr(exc, "message", exc)))
            out.append(row)
    return out


# --------------------------------------------------------------------------------------------------- the truth
def _truth(pack: dict) -> dict:
    t = pack.get("truth")
    return t if isinstance(t, dict) else {}


def banned_words(pack: dict) -> list[str] | None:
    """The brand's one banned-word list (script, captions, OCR of the picture and transcripts all read this).

    None when the pack doesn't state a list at all: that's unknown, and a gate that can't know the words is UNCHECKED,
    never a pass. An empty list is the pack saying there are none."""
    t = _truth(_as_pack(pack))
    if not isinstance(t.get("banned_words"), list):
        return None
    out: list[str] = []
    for w in t["banned_words"]:
        text = w.get("word") or w.get("text") if isinstance(w, dict) else w
        text = str(text or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def declares_banned_words(pack: dict) -> bool:
    """Does the pack state its list at all (an empty list is a statement; a missing key isn't)?"""
    return isinstance(_truth(_as_pack(pack)).get("banned_words"), list)


def never_claim(pack: dict) -> list[str]:
    out = []
    for x in _truth(_as_pack(pack)).get("never_claim") or []:
        text = str((x.get("text") or x.get("claim")) if isinstance(x, dict) else x or "").strip()
        if text:
            out.append(text)
    return out


def approved_claims(pack: dict) -> list[dict]:
    """The approved-claims list as [{id, text, …}] (a bare string becomes {id: claim-N, text})."""
    out = []
    for i, c in enumerate(_truth(_as_pack(pack)).get("approved_claims") or []):
        if isinstance(c, str) and c.strip():
            out.append({"id": f"claim-{i + 1}", "text": c.strip()})
        elif isinstance(c, dict):
            text = str(c.get("text") or c.get("claim") or "").strip()
            if text:
                out.append({**{k: v for k, v in c.items() if k not in ("id", "text", "claim")},
                            "id": str(c.get("id") or f"claim-{i + 1}"), "text": text})
    return out


def claim_authority(pack: dict) -> dict | None:
    """Who rules on claims for this brand: {name, role, organisation, …}, or None when nobody is named."""
    a = _truth(_as_pack(pack)).get("claim_authority")
    if isinstance(a, str) and a.strip():
        return {"name": a.strip()}
    if isinstance(a, dict) and str(a.get("name") or "").strip():
        return dict(a)
    return None


def ownership(pack: dict) -> dict:
    o = _as_pack(pack).get("ownership")
    return o if isinstance(o, dict) else {}


def is_company_material(pack: dict) -> bool:
    return bool(ownership(pack).get("company_material"))


# ------------------------------------------------------------------------------------------------ the look
def motion(pack: dict) -> dict:
    """The brand's motion law (named eases with lineage, durations, staggers, overshoot, signature motion)."""
    m = _as_pack(pack).get("motion")
    if isinstance(m, str):
        folder = pack_dir(_as_pack(pack))
        law = jsonio.read(folder / m, default=None) if folder else None
        m = law if isinstance(law, dict) else {}
    return copy.deepcopy(m) if isinstance(m, dict) else {}


_PALETTE_RESERVED = frozenset({"roles", "tones", "notes", "note", "source", "sources", "pairs", "contrast_pairs"})


def _flatten(tree: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(tree, dict):
        return out
    for key, value in tree.items():
        if not prefix and key in _PALETTE_RESERVED:
            continue
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, name))
            continue
        hexed = colour.normalise(value)
        if hexed:
            out[name] = hexed
    return out


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def resolve_colour(ref: Any, tokens: dict[str, str], depth: int = 0) -> str | None:
    """A literal colour, a palette token ("gray.50"), or a role; None when it's none of them."""
    hexed = colour.normalise(ref)
    if hexed or depth > 8 or not isinstance(ref, str):
        return hexed
    key = ref.strip().lstrip("$").removeprefix("--")
    if key in tokens:
        return tokens[key]
    folded = {_squash(k): v for k, v in tokens.items()}
    return folded.get(_squash(key))


def palette_tokens(pack: dict) -> dict[str, str]:
    """Every colour token the palette defines, flattened ("gray.50": "#…"), without the role names."""
    pal = _as_pack(pack).get("palette")
    return _flatten(pal if isinstance(pal, dict) else {})


def palette_roles(pack: dict) -> dict[str, str]:
    """{role: "#hex"} for each role the palette names (palette.roles: page, surface, ink, accent, inverse, …)."""
    pal = _as_pack(pack).get("palette")
    roles = pal.get("roles") if isinstance(pal, dict) and isinstance(pal.get("roles"), dict) else {}
    tokens = palette_tokens(pack)
    out = {}
    for role, ref in roles.items():
        hexed = resolve_colour(ref, tokens)
        if hexed:
            out[str(role)] = hexed
    return out


def palette(pack: dict) -> dict:
    """The palette as flat tokens, {token: "#RRGGBB"}, with each role's name added where no token already has it, so a
    contrast pair or a scene can name a colour either way ("ink" or "graphite.900")."""
    tokens = palette_tokens(pack)
    for role, hexed in palette_roles(pack).items():
        tokens.setdefault(role, hexed)
    return tokens


def contrast_pairs(pack: dict) -> list[dict]:
    """Every text colour pair the brand declares, measured: [{fg, bg, role (body|large), fg_hex, bg_hex, ratio, needs,
    ok, problem?}]. fg and bg stay as the pack wrote them (a token, a role or a colour)."""
    p = _as_pack(pack)
    pal = p.get("palette") if isinstance(p.get("palette"), dict) else {}
    raw = p.get("contrast_pairs") or pal.get("contrast_pairs") or pal.get("pairs") or []
    tokens = palette(p)
    out = []
    for pair in raw if isinstance(raw, list) else []:
        if not isinstance(pair, dict):
            continue
        fg_ref = pair.get("fg", pair.get("text", pair.get("foreground")))
        bg_ref = pair.get("bg", pair.get("background", pair.get("surface")))
        role = str(pair.get("role") or "body").lower()
        needs = colour.CONTRAST.get(role, colour.CONTRAST["body"])
        fg_hex, bg_hex = resolve_colour(fg_ref, tokens), resolve_colour(bg_ref, tokens)
        row: dict[str, Any] = {"fg": fg_ref, "bg": bg_ref, "role": role, "fg_hex": fg_hex, "bg_hex": bg_hex,
                               "needs": needs, "ratio": None, "ok": False}
        if pair.get("where"):
            row["where"] = pair["where"]
        if fg_hex is None or bg_hex is None:
            missing = [str(r) for r, h in ((fg_ref, fg_hex), (bg_ref, bg_hex)) if h is None]
            row["problem"] = f"{' and '.join(missing)} isn't a colour or a palette token"
        else:
            ratio = colour.ratio(colour.parse(fg_hex), colour.parse(bg_hex))  # type: ignore[arg-type]
            row["ratio"] = round(ratio, 2)
            row["ok"] = ratio + 1e-9 >= needs
            if not row["ok"]:
                row["problem"] = f"{ratio:.2f}:1, below the {needs:g}:1 {role} text needs"
        out.append(row)
    return out


def logos(pack: dict) -> list[dict]:
    """[{name, file, path, exists, build}] for each logo (files relative to the pack)."""
    p = _as_pack(pack)
    raw = p.get("logos") or {}
    entries = list(raw.items()) if isinstance(raw, dict) else [
        (str(x.get("name") or x.get("id") or f"logo{i + 1}"), x) for i, x in enumerate(raw) if isinstance(x, dict)]
    folder = pack_dir(p)
    out = []
    for name, spec in entries:
        rel = spec if isinstance(spec, str) else (spec.get("file") or spec.get("path")) if isinstance(spec, dict) \
            else None
        path = (folder / rel) if (rel and folder and not Path(rel).is_absolute()) else (Path(rel) if rel else None)
        out.append({"name": name, "file": rel, "path": str(path) if path else None,
                    "exists": bool(path and path.is_file()),
                    "inline": isinstance(spec, dict) and isinstance(spec.get("svg"), str) and "<svg" in spec["svg"],
                    "build": spec.get("build") if isinstance(spec, dict) else None})
    return out


def uploads_allowed(pack: dict) -> bool:
    """May brand material go to generators? Only when the brand owner said yes (off by default). If the two places a
    pack can say it disagree, the answer is no."""
    p = pack if isinstance(pack, dict) else {}
    top = p.get("allow_generator_uploads")
    g = p.get("grounding")
    nested = g.get("uploads_allowed") if isinstance(g, dict) else None
    said = [v for v in (top, nested) if isinstance(v, bool)]
    return bool(said) and all(said)


def skills(pack: dict) -> list[dict]:
    """The brand's own agent skills (skills/<name>/SKILL.md inside the pack): where brand taste lives."""
    folder = pack_dir(_as_pack(pack))
    out = []
    if not folder or not (folder / "skills").is_dir():
        return out
    for sk in sorted(p for p in (folder / "skills").iterdir() if p.is_dir()):
        doc = sk / "SKILL.md"
        meta = frontmatter(doc.read_text(encoding="utf-8")) if doc.is_file() else {}
        out.append({"name": meta.get("name") or sk.name, "folder": str(sk), "has_skill_md": doc.is_file(),
                    "description": meta.get("description")})
    return out


def frontmatter(text: str) -> dict:
    """The simple `key: value` lines between a SKILL.md's --- fences."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, re.DOTALL)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip("'\"")
    return out


# ---------------------------------------------------------------------------------------------- the references
def _dig(data: Any, dotted: str) -> Any:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in DOC_KEYS and not str(k).startswith("_")}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _asset_hashes(pack: dict) -> dict[str, str | None]:
    """The artwork the look covers (logo and motif SVGs), by content."""
    folder = pack_dir(pack)
    out: dict[str, str | None] = {}
    for lg in logos(pack):
        if lg["path"]:
            out[f"logo:{lg['name']}"] = jsonio.sha256(lg["path"]) if lg["exists"] else None
    motif = pack.get("motif")
    rel = (motif.get("file") or motif.get("path")) if isinstance(motif, dict) else None
    if rel and folder:
        p = folder / rel
        out["motif"] = jsonio.sha256(p) if p.is_file() else None
    return out


def fingerprint(pack: dict, key: str) -> str:
    """SHA-256 of the part of brand.json a reference's approval covers (documentation keys left out)."""
    if key not in COVERS:
        raise UsageError(f"{key!r} isn't a reference; the four are {', '.join(REFERENCES)}.")
    p = _as_pack(pack)
    covered: dict[str, Any] = {k: _clean(_dig(p, k)) for k in COVERS[key]}
    if key == "look_motion":
        covered["artwork"] = _asset_hashes(p)
    return jsonio.sha256_obj(covered)


def reference_file(pack: dict, record: dict) -> Path | None:
    rel = record.get("file") if isinstance(record, dict) else None
    if not rel:
        return None
    p = Path(str(rel)).expanduser()
    if p.is_absolute():
        return p
    folder = pack_dir(pack)
    return folder / p if folder else p


def reference_status(pack: dict, key: str) -> dict:
    """Where one reference stands: {reference, state, approved, reason, file, file_present, record}.

    States: approved · not_approved (never approved, or a placeholder) · incomplete (the record can't say what was
    approved) · changed (the file isn't the one approved) · data_changed (what the approval covered in brand.json
    changed). An approved reference whose file isn't here any more stays approved (what was approved is known by its
    hash) and says so, because films can't be compared with a reference that's gone.
    """
    if key not in REFERENCES:
        raise UsageError(f"{key!r} isn't a reference; the four are {', '.join(REFERENCES)}.")
    p = _as_pack(pack)
    rec = (p.get("references") or {}).get(key) if isinstance(p.get("references"), dict) else None
    out: dict[str, Any] = {"reference": key, "step": STEP_OF[key], "record": rec, "file": None, "file_present": False}

    def done(state: str, reason: str) -> dict:
        out.update(state=state, approved=state == "approved", reason=reason)
        return out

    if not isinstance(rec, dict):
        return done("not_approved", "not approved yet")
    path = reference_file(p, rec)
    out["file"] = str(path) if path else None
    out["file_present"] = bool(path and path.is_file())
    who = str(rec.get("approved_by") or "").strip()
    if not who:
        return done("not_approved", "a placeholder, not approved" if rec.get("placeholder") else "not approved yet")
    sha = str(rec.get("sha256") or "").strip().lower()
    if not SHA_RE.match(sha):
        return done("incomplete", f"{who}'s approval has no SHA-256, so nothing says what was approved")
    when = rec.get("approved_at") or "an unrecorded date"
    if out["file_present"]:
        now = jsonio.sha256(path)  # type: ignore[arg-type]
        if now != sha:
            return done("changed", f"{rec.get('file')} has changed since {who} approved it ({when})")
    covers = str(rec.get("covers_sha256") or "").strip().lower()
    if covers and covers != fingerprint(p, key):
        keys = ", ".join(COVERS[key])
        return done("data_changed", f"brand.json's {keys} changed since {who} approved it ({when})")
    reason = f"approved by {who} ({when})"
    if not out["file_present"]:
        reason += "; its file isn't here to compare films with"
    return done("approved", reason)


def references(pack: dict) -> dict[str, dict]:
    p = _as_pack(pack)
    return {k: reference_status(p, k) for k in REFERENCES}


def missing_references(pack: dict) -> list[str]:
    """The references that aren't approved (or no longer are), in onboarding order."""
    p = _as_pack(pack)
    return [k for k in REFERENCES if not reference_status(p, k)["approved"]]


def references_approved(pack: dict) -> bool:
    """All four references approved and unchanged: the bar for autonomous films."""
    return not missing_references(pack)


def require_onboarded(pack: dict, *, who: str = "Autonomous films") -> None:
    """Refuse (exit 1) unless all four references are approved, naming each one that isn't and why."""
    p = _as_pack(pack)
    refs = references(p)
    missing = [k for k, st in refs.items() if not st["approved"]]
    if missing:
        name = p.get("name") or "this brand"
        why = "; ".join(f"{k}: {refs[k]['reason']}" for k in missing)
        raise Refused(f"{who} only run for brands whose four references are approved, and {name} isn't there yet "
                      f"({why}).", hint=f"slate brand status {name}",
                      details={"missing_references": missing})


def status(pack: dict) -> dict:
    """Onboarding status: "onboarded" once all four references are approved, else "needs onboarding" and the next
    step (the front door routes a brand to onboarding on this)."""
    p = _as_pack(pack)
    refs = references(p)
    missing = [k for k, st in refs.items() if not st["approved"]]
    name = p.get("name")
    nxt = None
    folder = pack_dir(p)
    if missing and folder is not None and is_neutral(folder):
        nxt = {"reference": missing[0], "command": f"slate brand init <name> --from-pack {name}",
               "why": "a neutral pack's references stay placeholders; onboard your own copy"}
    elif missing:
        first = missing[0]
        nxt = {"reference": first, "command": f"slate brand onboard {name} --step {STEP_OF[first]}",
               "why": refs[first]["reason"]}
    return {"brand": name, "status": "onboarded" if not missing else "needs onboarding", "onboarded": not missing,
            "missing": missing, "references": {k: {x: v for x, v in st.items() if x != "record"}
                                                for k, st in refs.items()}, "next": nxt}


def summary(pack: dict) -> dict:
    """A short description of a pack for `slate brand show` and `list`."""
    p = _as_pack(pack)
    fams = (p.get("type") or {}).get("families") if isinstance(p.get("type"), dict) else None
    families = [f for f in (fams if isinstance(fams, list) else (fams or {}).values()) if isinstance(f, dict)] \
        if fams else []
    law = motion(p)
    voice = p.get("voice") if isinstance(p.get("voice"), dict) else {}
    sound = p.get("sound") if isinstance(p.get("sound"), dict) else {}
    return {
        "name": p.get("name"), "display_name": p.get("display_name"), "dir": p.get("_dir"),
        "where": p.get("_source"), "ownership": ownership(p),
        "palette": {"tokens": len(palette_tokens(p)), "roles": palette_roles(p)},
        "contrast_pairs": len(contrast_pairs(p)),
        "type": [f"{f.get('family')} ({f.get('role', '?')}, {f.get('source', '?')})" for f in families],
        "logos": [lg["name"] for lg in logos(p)],
        "motif": bool(p.get("motif")),
        "look": (p.get("look") or {}).get("id") if isinstance(p.get("look"), dict) else p.get("look"),
        "motion": {"eases": sorted((law.get("eases") or {}).keys()), "durations": law.get("durations") or {}},
        "voice": {k: voice.get(k) for k in ("provider", "source", "voice_id", "voice_name", "model", "seed")},
        "sound": {k: sound.get(k) for k in ("key", "tempo", "materials", "banned_timbres")},
        "truth": {"sources": len(_truth(p).get("sources") or []), "claim_authority": claim_authority(p),
                  "approved_claims": len(approved_claims(p)), "banned_words": len(banned_words(p) or []),
                  "never_claim": len(never_claim(p))},
        "grounding": {k: v for k, v in (p.get("grounding") or {}).items() if k != "uploads_allowed"},
        "uploads_allowed": uploads_allowed(p),
        "skills": [s["name"] for s in skills(p)],
        "status": status(p),
    }


def iter_packs(names: Iterable[str] | None = None) -> list[dict]:
    """Loaded packs by name (all packs when names is empty)."""
    if names:
        return [load(n) for n in names]
    return [load(row["dir"]) for row in list_packs() if row.get("status") != "unreadable"]
