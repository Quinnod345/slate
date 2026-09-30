"""A brand's fonts: is every type family it names available on this Mac?

Brand packs name fonts by reference, never as files (`type.families`: `{role, family, weights, source, licence}`,
where `source` is `installed`, `google`, a kit URL or a font file URL). Doctor looks in two places:

- macOS's own font list (CoreText, read through osascript's JavaScript bridge; no app opens);
- Adobe Fonts activated through Creative Cloud, which After Effects sees even when macOS's list doesn't.

Whether a missing family is red or yellow depends on where the brand gets it. A Google font is yellow: Remotion loads
it from Google Fonts at render time, and only After Effects needs it installed. Anything else is red: the font gate
fails on a substitution, so a missing family means the film can't pass.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slate.doctor import rows as R
from slate.doctor.common import Ctx
from slate.doctor.env import Env
from slate.errors import UsageError

G = "fonts"


def load_brand(env: Env, name: str) -> tuple[dict, str]:
    """The brand pack (through slate.brand.pack when it's there) and where it lives. UsageError when there's none."""
    try:
        from slate.brand import pack as bpack  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - brand module missing or broken: read brand.json directly
        bpack = None
    if bpack is not None:
        folder = bpack.find(name)
        return bpack.load(folder), str(folder)
    candidates = []
    p = Path(name).expanduser()
    candidates += [p / "brand.json", p] if p.suffix != ".json" else [p]
    candidates += [Path(d) / name / "brand.json" for d in env.brand_dirs()]
    for c in candidates:
        if env.is_file(c):
            data = env.read_json(c)
            if isinstance(data, dict):
                return data, str(c.parent)
    raise UsageError(f"No brand pack called {name!r} (looked in {', '.join(str(d) for d in env.brand_dirs())}).",
                     hint=f"Make one with `slate brand init {name}`; the neutral pack is 'demo'.")


def families(pack: dict) -> list[dict]:
    """The brand's type families as [{family, role, source, licence}], whatever shape brand.json uses."""
    t = pack.get("type") or {}
    raw: Any = t.get("families") if isinstance(t, dict) else t
    out: list[dict] = []
    if isinstance(raw, dict):
        raw = [{**(v if isinstance(v, dict) else {"family": v}), "role": v.get("role", k) if isinstance(v, dict) else k}
               for k, v in raw.items()]
    for entry in raw or []:
        if isinstance(entry, str):
            out.append({"family": entry, "role": None, "source": "installed", "licence": None})
        elif isinstance(entry, dict) and entry.get("family"):
            out.append({"family": str(entry["family"]), "role": entry.get("role"),
                        "source": str(entry.get("source") or "installed"), "licence": entry.get("licence")})
    seen, unique = set(), []
    for f in out:
        key = f["family"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _kind(source: str) -> str:
    s = source.lower()
    if s == "google" or "fonts.googleapis" in s or "fonts.google" in s:
        return "google"
    if "typekit" in s or "use.typekit" in s or "adobe" in s:
        return "adobe"
    if s.startswith("http"):
        return "url"
    return "installed"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "family"


def check_fonts(env: Env, ctx: Ctx) -> list[dict]:
    if not ctx.brand:
        return [R.green("fonts.brand", "No brand named, so no brand fonts were checked: add --brand <name> to check "
                                       "one brand's families.", group=G, info=True)]
    pack, where = load_brand(env, ctx.brand)
    brand = str(pack.get("name") or ctx.brand)
    fams = families(pack)
    if not fams:
        return [R.yellow(f"fonts.{_slug(brand)}", f"The {brand} pack names no type families, so the font gate has "
                                                  "nothing to hold its films to.",
                         "Add type.families to its brand.json (family, source and licence for each).", group=G,
                         details={"pack": where})]
    system = env.font_families()
    adobe = env.adobe_font_families()
    sys_lower = {f.lower() for f in system or ()}
    adobe_lower = {f.lower() for f in adobe}
    out = []
    for f in fams:
        fam, kind = f["family"], _kind(f["source"])
        rid = f"fonts.{_slug(brand)}.{_slug(fam)}"
        in_sys, in_adobe = fam.lower() in sys_lower, fam.lower() in adobe_lower
        details = {"brand": brand, "family": fam, "role": f["role"], "source": f["source"], "licence": f["licence"],
                   "macos": in_sys, "adobe_fonts": in_adobe, "macos_list_read": system is not None}
        role = f" ({f['role']})" if f["role"] else ""
        licence = f" Its licence: {f['licence']}." if f["licence"] else ""
        if in_sys or in_adobe:
            found = "macOS's font list" if in_sys else "Adobe Fonts (Creative Cloud), which After Effects uses"
            out.append(R.green(rid, f"{fam}{role} is available: {found}.", group=G, details=details))
        elif kind == "google":
            out.append(R.yellow(rid, f"{fam}{role} isn't installed. Remotion loads it from Google Fonts when it renders; "
                                     "After Effects needs it installed.",
                                f"Download {fam} from Google Fonts (an open licence) and install it, if this brand's "
                                "films finish in After Effects.", group=G, details=details))
        elif kind == "adobe":
            out.append(R.red(rid, f"{fam}{role} isn't active: it comes from Adobe Fonts, and neither macOS nor Creative "
                                  "Cloud lists it.",
                             f"Activate {fam} in Creative Cloud (Adobe Fonts) under the brand's licence.{licence}",
                             group=G, details=details))
        elif system is None and not adobe:
            out.append(R.yellow(rid, f"{fam}{role} couldn't be checked: macOS's font list didn't answer.",
                                "Run `slate doctor --brand` again; `slate connect aftereffects --brand` also asks After "
                                "Effects itself.", group=G, details=details))
        else:
            out.append(R.red(rid, f"{fam}{role} isn't installed, and the font gate fails on a substitution.",
                             f"Install {fam} on this Mac from the brand's own source; Slate never ships font files."
                             f"{licence}", group=G, details=details))
    return out
