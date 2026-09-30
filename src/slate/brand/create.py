"""`slate brand init`: a new brand pack, in the private store, never in Slate's repo.

Real brands live in the private store (`paths.brands_store()`: `~/code/slate-brands`, or `SLATE_BRANDS`), which stays
on this Mac; the repo holds only the neutral `brands/_template` and `brands/demo` (decision 4, docs/PLAN.md §4). So
`init` refuses any destination inside the repo, whatever the name, and it refuses the neutral packs' own names.

A new pack starts from the template (every key, empty values), or from another pack's design with `--from-pack`
(its references are never carried over: a new brand is approved on its own). With `--from-app`, `--from-website` or
`--from-pdf` it goes straight into onboarding's look step, which reads that material in place and fills the palette
and type it finds. For code on the WORK Mac (`--from-app work:<path>`) it prints the read-only procedure instead:
company code is read where it lives, never copied.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Iterable

from slate import jsonio, paths
from slate.brand import onboard, pack
from slate.errors import Refused, UsageError

TEMPLATE = "_template"
FOLDERS = ("logos", "references", "onboarding", "skills")
COPY_FROM_PACK = ("logos", "skills")


def _template() -> dict:
    return jsonio.read(paths.BRANDS_REPO / TEMPLATE / pack.PACK_FILE)


def _readme(raw: dict) -> str:
    name = raw["name"]
    title = raw.get("display_name") or name
    return (f"# {title}\n\nA Slate brand pack. brand.json is the brand as data; the template's README "
            f"(brands/_template/README.md in Slate's repo) documents every key.\n\n"
            f"- Where onboarding stands: `slate brand status {name}` (or onboarding/CHECKLIST.md)\n"
            f"- Next step: `slate brand onboard {name}`\n- Check the pack: `slate brand validate {name}`\n")


def init(name: str, *, store: str | Path | None = None, from_app: Iterable[str] = (), from_website: str | None = None,
         from_pdf: str | None = None, from_pack: str | None = None, display_name: str | None = None,
         kind: str | None = None, organisation: str | None = None, company_material: bool = False,
         dry_run: bool = False) -> dict:
    """Create a pack and return {brand, dir, created, look, next}."""
    name = pack.check_name(name)
    if name in pack.NEUTRAL:
        raise Refused(f"{name!r} is one of Slate's own neutral packs; a brand of your own needs its own name.")
    base = Path(store).expanduser() if store else paths.brands_store()
    target = (base / name).resolve() if base.exists() else (base / name)
    if pack.in_repo(target):
        raise Refused(f"{target} is inside Slate's repo, which holds only the neutral packs (brands/_template and "
                      "brands/demo). A real brand lives in the private store, which stays on this Mac.",
                      hint=f"slate brand init {name} puts it in the private store; if SLATE_BRANDS points into "
                           "the repo, point it somewhere outside it")
    if target.exists() and any(target.iterdir()):
        raise Refused(f"{target} already exists; Slate never writes over a brand pack.",
                      hint=f"slate brand status {name}  ·  slate brand onboard {name}")
    if kind is not None and kind not in ("own", "client"):
        raise UsageError("--kind is own (the owner's own brand) or client (a brand the owner doesn't own).")
    if from_pack:
        src_folder = pack.find(from_pack)
        raw = copy.deepcopy(pack.read_raw(src_folder))
        seeded = {"from_pack": src_folder.name}
    else:
        src_folder = None
        raw = copy.deepcopy(_template())
        seeded = {}
    kind = kind or ("client" if organisation or company_material else "own")
    raw["name"] = name
    raw["display_name"] = display_name
    raw["ownership"] = {"kind": kind, "organisation": organisation, "company_material": bool(company_material),
                        "notes": None}
    raw["references"] = {k: None for k in pack.REFERENCES}
    if from_pack:
        raw["about"] = None
        truth = raw.get("truth") if isinstance(raw.get("truth"), dict) else {}
        raw["truth"] = {**copy.deepcopy(_template()["truth"]), "banned_words": list(truth.get("banned_words") or [])}
        look = raw.get("look") if isinstance(raw.get("look"), dict) else {}
        raw["look"] = {**look, "references": [], "moodboards": []}
        s = raw.get("sound") if isinstance(raw.get("sound"), dict) else {}
        raw["sound"] = {**s, "reference_levels": None, "reference_measured_from": None}
    created = [str(target / pack.PACK_FILE)] + [str(target / f) for f in FOLDERS]
    if dry_run:
        return {"brand": name, "dir": str(target), "dry_run": True, "would_create": created, "brand_json": raw}
    target.mkdir(parents=True, exist_ok=True)
    for f in FOLDERS:
        (target / f).mkdir(exist_ok=True)
    if src_folder is not None:
        for sub in COPY_FROM_PACK:
            if (src_folder / sub).is_dir():
                shutil.copytree(src_folder / sub, target / sub, dirs_exist_ok=True)
    skills_readme = paths.BRANDS_REPO / TEMPLATE / "skills" / "README.md"
    if skills_readme.is_file() and not (target / "skills" / "README.md").exists():
        shutil.copy2(skills_readme, target / "skills" / "README.md")
    pack.save(target, raw)
    jsonio.write_text_atomic(target / "README.md", _readme(raw))
    look = None
    apps = list(from_app)
    if apps or from_website or from_pdf:
        look = onboard.look(str(target), from_app=apps, from_website=from_website, from_pdf=from_pdf)
    else:
        onboard.write_overview(target, raw)
        if from_pack:
            from slate.brand import motiondoc  # noqa: PLC0415

            motiondoc.write(target, raw)
    ov = onboard.overview(str(target))
    return {"brand": name, "dir": str(target), "created": created, "seeded": seeded, "ownership": raw["ownership"],
            "look": look, "status": ov["status"], "next": ov["steps"]["truth"]["next"],
            "in_store": pack.where(target) == "store"}
