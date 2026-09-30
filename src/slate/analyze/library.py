"""The named eases a measured move is compared against: public design-system curves, Slate's kit curves, and the
brand's own named eases when a brand is given.

These are neutral, published values, so a measured move can be described in words ("closest to Material 3's
emphasized decelerate, 1.1% off") before brand onboarding decides what the brand's own curve is. Nothing here is a
brand's curve; a brand's eases come from its pack.
"""

from __future__ import annotations

from typing import Any

from slate.motion.eases import EaseSpec, parse

FAMILIES = {
    "css": "W3C CSS Easing Functions Level 1: the cubic-bezier keywords",
    "material3": "Material 3 motion easing tokens (material-web, tokens/_md-sys-motion.scss)",
    "carbon": "IBM Carbon motion easing tokens (carbon-design-system/carbon, packages/motion)",
    "slate": "Slate's kit curves (slate.motion.eases): a half cosine and a critically damped spring",
}

NAMED: list[dict[str, Any]] = [
    {"name": "linear", "family": "css", "ease": "linear"},
    {"name": "ease", "family": "css", "ease": [0.25, 0.1, 0.25, 1.0]},
    {"name": "ease-in", "family": "css", "ease": [0.42, 0.0, 1.0, 1.0]},
    {"name": "ease-out", "family": "css", "ease": [0.0, 0.0, 0.58, 1.0]},
    {"name": "ease-in-out", "family": "css", "ease": [0.42, 0.0, 0.58, 1.0]},
    {"name": "m3-standard", "family": "material3", "ease": [0.2, 0.0, 0.0, 1.0]},
    {"name": "m3-standard-decelerate", "family": "material3", "ease": [0.0, 0.0, 0.0, 1.0]},
    {"name": "m3-standard-accelerate", "family": "material3", "ease": [0.3, 0.0, 1.0, 1.0]},
    {"name": "m3-emphasized-decelerate", "family": "material3", "ease": [0.05, 0.7, 0.1, 1.0]},
    {"name": "m3-emphasized-accelerate", "family": "material3", "ease": [0.3, 0.0, 0.8, 0.15]},
    {"name": "m3-legacy", "family": "material3", "ease": [0.4, 0.0, 0.2, 1.0]},
    {"name": "carbon-productive-standard", "family": "carbon", "ease": [0.2, 0.0, 0.38, 0.9]},
    {"name": "carbon-productive-entrance", "family": "carbon", "ease": [0.0, 0.0, 0.38, 0.9]},
    {"name": "carbon-productive-exit", "family": "carbon", "ease": [0.2, 0.0, 1.0, 0.9]},
    {"name": "carbon-expressive-standard", "family": "carbon", "ease": [0.4, 0.14, 0.3, 1.0]},
    {"name": "carbon-expressive-entrance", "family": "carbon", "ease": [0.0, 0.0, 0.3, 1.0]},
    {"name": "carbon-expressive-exit", "family": "carbon", "ease": [0.4, 0.14, 1.0, 1.0]},
    {"name": "sine", "family": "slate", "ease": "sine"},
    {"name": "spring0", "family": "slate", "ease": "spring0"},
]


def candidates(brand_eases: dict[str, EaseSpec] | None = None) -> list[dict]:
    """[{name, family, spec: EaseSpec, curve}] to compare a move against: the brand's own eases first."""
    out: list[dict] = []
    for name, spec in (brand_eases or {}).items():
        out.append({"name": name, "family": "brand", "spec": spec, "curve": spec.to_dict()})
    for row in NAMED:
        spec = parse(row["ease"])
        if spec is None:  # pragma: no cover - the table above is fixed
            continue
        out.append({"name": row["name"], "family": row["family"], "spec": spec, "curve": spec.to_dict()})
    return out


def brand_eases(brand: str | None) -> tuple[dict[str, EaseSpec], str | None]:
    """The brand pack's named eases, and a note when there are none (no brand, no pack, or no motion law yet)."""
    if not brand:
        return {}, None
    try:
        from slate.state import deps  # noqa: PLC0415 - state reads packs through slate.brand.pack when it's built

        pack = deps.brand_load(brand)
    except Exception as exc:  # noqa: BLE001 - a missing pack only means no brand curves to compare against
        return {}, f"brand {brand!r} couldn't be read ({getattr(exc, 'message', exc)}); compared with public curves only"
    from slate.motion.eases import law_from  # noqa: PLC0415

    law = law_from(pack.get("motion"))
    if law.neutral:
        return {}, f"brand {brand!r} has no named eases yet (motion.eases); compared with public curves only"
    return dict(law.eases), None
