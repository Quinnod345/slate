"""A brand's motion law on paper: `motion.json` and `MOTION.md`, both written from brand.json's `motion`.

brand.json holds the law (every engine reads it there). Onboarding's look step also writes it out in two forms a
person and other tools use: `motion.json`, the law on its own, and `MOTION.md`, the law explained: each named ease
with its curve, its job, when it reaches 90% of its travel (where "lands" is measured everywhere in Slate), its After
Effects handles and its lineage; the duration scale; staggers and their caps; exits; the overshoot policy; the
signature motion; the rules and their reasons.

Both are deterministic (no dates, no machine paths), so `slate brand validate` can regenerate them in memory and fail
when the files on disk have drifted from brand.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slate import jsonio
from slate.motion import eases as law_math

MOTION_JSON = "motion.json"
MOTION_MD = "MOTION.md"


def _command(name: str) -> str:
    return f"slate brand onboard {name} --step look"


def export(raw: dict) -> dict:
    """motion.json's content: the law, stamped as generated from brand.json."""
    name = raw.get("name") or "<brand>"
    motion = raw.get("motion") if isinstance(raw.get("motion"), dict) else {}
    return {"generated": f"do not edit by hand: {_command(name)} writes this from brand.json's motion (edit it there)",
            "brand": name, **{k: v for k, v in motion.items() if k != "generated"}}


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, (int, float)) else str(v)


def _curve(raw: Any) -> str:
    spec = law_math.parse(raw)
    if spec is None:
        return "unreadable"
    if spec.kind == "bezier":
        return "cubic-bezier(" + ", ".join(_fmt(v) for v in (spec.bezier or ())) + ")"
    if spec.kind == "spring0":
        return f"spring0 (stiffness {_fmt(spec.stiffness)})"
    return spec.kind


def _ae(raw: Any) -> str:
    spec = law_math.parse(raw)
    if spec is None or spec.kind != "bezier" or not spec.bezier:
        return "—"
    h = law_math.ae_keyframe_ease(spec.bezier)
    out, inn = h["out"], h["in"]
    return (f"out {_fmt(out['influence'])}% at {_fmt(out['speed'])}·v; in {_fmt(inn['influence'])}% at "
            f"{_fmt(inn['speed'])}·v")


def _seconds(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, dict):
        s = v.get("seconds", v.get("s"))
        return float(s) if isinstance(s, (int, float)) else None
    return None


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_md(raw: dict) -> str:
    """MOTION.md for a pack (deterministic)."""
    name = raw.get("name") or "<brand>"
    title = raw.get("display_name") or name
    m = raw.get("motion") if isinstance(raw.get("motion"), dict) else {}
    roles = m.get("roles") if isinstance(m.get("roles"), dict) else {}
    role_of = {v: k for k, v in roles.items()}
    lines = [f"# MOTION.md: how {title} moves", "",
             f"*Generated from brand.json's `motion` by `{_command(name)}`. Edit the law in brand.json; this page "
             "and motion.json are rewritten from it.*", ""]
    if m.get("notes"):
        lines += [str(m["notes"]), ""]
    eases = m.get("eases") if isinstance(m.get("eases"), dict) else {}
    lines += ["## Named eases", ""]
    if not eases:
        lines += ["None yet. Onboarding's look step proposes them from the product's own motion values (CSS easing "
                  "tokens, SwiftUI springs) and from moves measured in two or three reference films.", ""]
    else:
        lines += ["| Name | Curve | Job | Lands (T90) | After Effects handles | Lineage |", "|---|---|---|---|---|---|"]
        for ease_name, raw_ease in eases.items():
            spec = law_math.parse(raw_ease)
            t90 = f"{law_math.t90(spec):.1%} of the move" if spec else "—"
            info = raw_ease if isinstance(raw_ease, dict) else {}
            job = info.get("job") or (f"{role_of[ease_name]} (role)" if ease_name in role_of else "")
            lineage = info.get("lineage") or "not recorded"
            lines.append(f"| **{_cell(ease_name)}** | {_cell(_curve(raw_ease))} | {_cell(job)} | {t90} | "
                         f"{_cell(_ae(raw_ease))} | {_cell(lineage)} |")
        lines.append("")
        peaks = [(n, e.get("peak")) for n, e in eases.items() if isinstance(e, dict) and (e.get("peak") or 1) > 1]
        if peaks:
            lines += ["Curves that overshoot: " + ", ".join(f"{n} (peaks at {_fmt(p)})" for n, p in peaks) + ".", ""]
        lines += ["AE handles: speed is a multiple of the move's average speed v (distance ÷ duration); influence is "
                  "the handle's reach as a share of the segment.", ""]
    if roles:
        lines += ["**Roles:** " + ", ".join(f"{r} → {e}" for r, e in roles.items()) + ".", ""]
    durs = m.get("durations")
    lines += ["## Duration scale", ""]
    items = list(durs.items()) if isinstance(durs, dict) else [(d.get("name"), d) for d in (durs or [])
                                                               if isinstance(d, dict)]
    if items:
        lineage = any(isinstance(v, dict) and v.get("lineage") for _, v in items)
        lines += ["| Token | Seconds | Use |" + (" Lineage |" if lineage else ""),
                  "|---|---|---|" + ("---|" if lineage else "")]
        for token, v in items:
            info = v if isinstance(v, dict) else {}
            lines.append(f"| **{_cell(token)}** | {_fmt(_seconds(v)) if _seconds(v) is not None else '—'} | "
                         f"{_cell(info.get('use', ''))} |" + (f" {_cell(info.get('lineage', ''))} |" if lineage
                                                              else ""))
        lines += ["", "Seconds are the source of truth; frames come from each film's own rate.", ""]
    else:
        lines += ["None yet.", ""]
    if m.get("exit_factor") is not None:
        lines += [f"**Exits** run {_fmt(m['exit_factor'])}× the duration of their entrance.", ""]
    stag = m.get("staggers") if isinstance(m.get("staggers"), dict) else {}
    if stag:
        lines += ["## Staggers", ""]
        for unit, v in stag.items():
            s = _seconds(v)
            if s is None and isinstance(v, dict) and v.get("frames") is not None:
                desc = f"{_fmt(v['frames'])} frames" + (f" at {_fmt(v['at_fps'])} fps" if v.get("at_fps") else "")
            else:
                desc = f"{_fmt(s)} s" if s is not None else "unreadable"
            lines.append(f"- **{unit}**: {desc}")
        cap = m.get("stagger_cap") if isinstance(m.get("stagger_cap"), dict) else {}
        if cap:
            bits = []
            if cap.get("seconds") is not None:
                bits.append(f"at most {_fmt(cap['seconds'])} s")
            if cap.get("fraction") is not None:
                bits.append(f"at most {_fmt(cap['fraction'] * 100)}% of the group's own entrance")
            if cap.get("max_items") is not None:
                bits.append(f"more than {cap['max_items']} items are grouped, never staggered one by one")
            lines.append("- **Cap:** a group's whole spread is " + "; ".join(bits) + ".")
        lines.append("")
    ov = m.get("overshoot")
    lines += ["## Overshoot", ""]
    if isinstance(ov, dict):
        allowed = ov.get("allowed")
        where = ov.get("where") or []
        if allowed:
            lines.append(f"Allowed{' only in ' + ', '.join(map(str, where)) if where else ''}, peaking at "
                         f"{_fmt(ov.get('max', 1))} at most.")
        else:
            lines.append("None: nothing bounces, and nothing passes its target.")
        if ov.get("notes"):
            lines.append(str(ov["notes"]))
    else:
        lines.append("Not stated yet (the motion gate treats an unstated policy as no overshoot).")
    lines.append("")
    sig = m.get("signature")
    if isinstance(sig, dict) and sig:
        lines += ["## Signature motion", ""]
        for key in ("name", "what", "logo", "ease", "duration", "sound", "rules", "notes"):
            if sig.get(key):
                val = sig[key]
                lines.append(f"- **{key}**: " + ("; ".join(map(str, val)) if isinstance(val, list) else str(val)))
        lines.append("")
    rules = []
    if m.get("linear_props"):
        rules.append("Linear only for things that show time: " + ", ".join(map(str, m["linear_props"])) + ".")
    loops = m.get("loops") if isinstance(m.get("loops"), dict) else {}
    if loops:
        rules.append("Loops: " + ", ".join(f"{k} {_fmt(v)}" for k, v in loops.items()) + ".")
    for b in m.get("banned") or []:
        if isinstance(b, dict):
            rules.append(f"Never {b.get('what')}: {b.get('why', '')}".rstrip(": ") +
                         (f" (exception: {b['exception']})" if b.get("exception") else "") + ".")
        else:
            rules.append(f"Never {b}.")
    if rules:
        lines += ["## Rules", ""] + [f"- {r}" for r in rules] + [""]
    return "\n".join(lines).rstrip() + "\n"


def files(folder: Path, raw: dict) -> dict[str, str]:
    """{file name: the text it should hold}."""
    return {MOTION_JSON: jsonio.dumps(export(raw)), MOTION_MD: render_md(raw)}


def write(folder: str | Path, raw: dict) -> list[Path]:
    folder = Path(folder)
    out = []
    for fname, text in files(folder, raw).items():
        out.append(jsonio.write_text_atomic(folder / fname, text))
    return out


def check(folder: str | Path, raw: dict) -> dict[str, str]:
    """{file: current | stale | missing} for motion.json and MOTION.md against brand.json's law."""
    folder = Path(folder)
    out = {}
    for fname, text in files(folder, raw).items():
        p = folder / fname
        out[fname] = "missing" if not p.is_file() else ("current" if p.read_text(encoding="utf-8") == text else "stale")
    return out
