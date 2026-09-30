"""Turn a brand's SVG logos into After Effects shape data, so logos land in AE as native, editable shape groups.

Generalised from the film pipeline's svg2ae.py (which handled M/L/H/V/C/Z plus translate/scale, enough for one
wordmark and potrace output). A brand's own SVG can hold more, so this reads:

- path commands M L H V C S Q T A Z, absolute and relative (quadratics become cubics; arcs become cubic segments);
- rect (with rounded corners), circle, ellipse, line, polyline and polygon elements;
- transforms on the element and every group above it: matrix, translate, scale, rotate, skewX, skewY;
- fills from the `fill` attribute or a `style` declaration, inherited from groups; "none" means no fill.

Output (the same shape the ExtendScript kit's SL.logo reads):

    {"viewBox": [x, y, w, h], "items": [{"id", "fill": "#RRGGBB" | None, "paths": [{"v", "i", "o", "c"}],
     "bbox": [x0, y0, x1, y1]}], "warnings": [...]}

Each path is AE-style: vertices, and in/out tangents relative to their vertex. Nothing in <defs> or <clipPath> is
drawn. Things it can't read (gradients, masks, text) are named in `warnings` rather than silently dropped.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from slate.errors import UsageError

SVG_NS = "{http://www.w3.org/2000/svg}"
TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
NAMED = {"black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "green": "#008000", "blue": "#0000FF",
         "gray": "#808080", "grey": "#808080"}

Matrix = tuple[float, float, float, float, float, float]  # a b c d e f: x' = a x + c y + e, y' = b x + d y + f
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------------------------------------------
# Transforms


def mul(m: Matrix, n: Matrix) -> Matrix:
    """m then n applied to a point is n ∘ m; this returns the matrix for applying n first, then m (m · n)."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    if not text:
        return m
    for kind, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", text):
        a = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v]
        if kind == "matrix" and len(a) == 6:
            t: Matrix = (a[0], a[1], a[2], a[3], a[4], a[5])
        elif kind == "translate":
            t = (1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0.0)
        elif kind == "scale":
            t = (a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)
        elif kind == "rotate":
            r = math.radians(a[0])
            rot: Matrix = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)
            if len(a) == 3:
                t = mul(mul((1, 0, 0, 1, a[1], a[2]), rot), (1, 0, 0, 1, -a[1], -a[2]))
            else:
                t = rot
        elif kind == "skewX":
            t = (1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        elif kind == "skewY":
            t = (1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        else:
            continue
        m = mul(m, t)
    return m


def apply(m: Matrix, p: Iterable[float]) -> list[float]:
    x, y = p
    a, b, c, d, e, f = m
    return [a * x + c * y + e, b * x + d * y + f]


# ---------------------------------------------------------------------------------------------------------------
# Path data -> subpaths of cubic segments (absolute coordinates)


def _arc_to_cubics(x1, y1, rx, ry, phi, large, sweep, x2, y2) -> list[tuple]:
    """SVG elliptical arc (endpoint form) -> cubic bezier segments [(c1, c2, end), ...]."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [((x1, y1), (x2, y2), (x2, y2))]
    rx, ry = abs(rx), abs(ry)
    cphi, sphi = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cphi * dx + sphi * dy, -sphi * dx + cphi * dy
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cphi * cxp - sphi * cyp + (x1 + x2) / 2
    cy = sphi * cxp + cphi * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2) - 1e-9)))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []
    t = t1
    for _ in range(n):
        c0, s0 = math.cos(t), math.sin(t)
        c1, s1 = math.cos(t + step), math.sin(t + step)
        p0 = (c0 - k * s0, s0 + k * c0)
        p1 = (c1 + k * s1, s1 - k * c1)
        p2 = (c1, s1)

        def to_xy(p):
            ex, ey = rx * p[0], ry * p[1]
            return (cphi * ex - sphi * ey + cx, sphi * ex + cphi * ey + cy)

        out.append((to_xy(p0), to_xy(p1), to_xy(p2)))
        t += step
    return out


def parse_d(d: str) -> list[dict]:
    """Path data -> [{"pts": [...], "cin": [...], "cout": [...], "closed": bool}] with absolute control points."""
    toks = TOKEN.findall(d or "")
    i = 0
    cmd = None
    x = y = sx = sy = 0.0
    last_c2: tuple | None = None
    last_q: tuple | None = None
    subpaths: list[dict] = []
    cur: dict | None = None

    def num() -> float:
        nonlocal i
        if i >= len(toks) or re.fullmatch(r"[A-Za-z]", toks[i]):
            raise UsageError(f"SVG path data ends early near token {i}: {d[:60]}…")
        v = float(toks[i])
        i += 1
        return v

    def cubic(c1, c2, end):
        nonlocal x, y
        cur["cout"][-1] = list(c1)
        cur["pts"].append(list(end))
        cur["cin"].append(list(c2))
        cur["cout"].append(None)
        x, y = end

    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    cur["closed"] = True
                    subpaths.append(cur)
                    cur = None
                x, y = sx, sy
                last_c2 = last_q = None
                continue
        if cmd is None:
            raise UsageError("SVG path data must start with a command.")
        rel = cmd.islower()
        c = cmd.upper()
        if c != "M" and cur is None:
            cur = {"pts": [[x, y]], "cin": [None], "cout": [None], "closed": False}
        if c == "M":
            nx, ny = num(), num()
            if rel:
                nx, ny = x + nx, y + ny
            if cur:
                subpaths.append(cur)
            cur = {"pts": [[nx, ny]], "cin": [None], "cout": [None], "closed": False}
            x, y, sx, sy = nx, ny, nx, ny
            cmd = "l" if rel else "L"
            last_c2 = last_q = None
        elif c in "LHV":
            if c == "L":
                nx, ny = num(), num()
                if rel:
                    nx, ny = x + nx, y + ny
            elif c == "H":
                nx = num()
                nx, ny = (x + nx if rel else nx), y
            else:
                ny = num()
                nx, ny = x, (y + ny if rel else ny)
            cur["pts"].append([nx, ny])
            cur["cin"].append(None)
            cur["cout"].append(None)
            x, y = nx, ny
            last_c2 = last_q = None
        elif c in "CS":
            if c == "C":
                c1 = (num(), num())
            else:
                c1 = (2 * x - last_c2[0], 2 * y - last_c2[1]) if last_c2 else (x, y)
            c2 = (num(), num())
            end = (num(), num())
            if rel:
                if c == "C":
                    c1 = (x + c1[0], y + c1[1])
                c2 = (x + c2[0], y + c2[1])
                end = (x + end[0], y + end[1])
            cubic(c1, c2, end)
            last_c2, last_q = c2, None
        elif c in "QT":
            if c == "Q":
                q = (num(), num())
                if rel:
                    q = (x + q[0], y + q[1])
            else:
                q = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
            end = (num(), num())
            if rel:
                end = (x + end[0], y + end[1])
            c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y))
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
            cubic(c1, c2, end)
            last_q, last_c2 = q, None
        elif c == "A":
            rx, ry, phi = num(), num(), num()
            large, sweep = int(num()), int(num())
            ex, ey = num(), num()
            if rel:
                ex, ey = x + ex, y + ey
            for c1, c2, end in _arc_to_cubics(x, y, rx, ry, phi, large, sweep, ex, ey):
                cubic(c1, c2, end)
            last_c2 = last_q = None
        else:
            raise UsageError(f"Unsupported SVG path command {cmd!r}.")
    if cur:
        subpaths.append(cur)
    out = []
    for sp in subpaths:
        pts, cin, cout = sp["pts"], sp["cin"], sp["cout"]
        # A closing Z after a segment that returns to the start: merge the duplicate end vertex.
        if sp["closed"] and len(pts) > 1 and abs(pts[-1][0] - pts[0][0]) < 1e-6 and abs(pts[-1][1] - pts[0][1]) < 1e-6:
            cin[0] = cin[-1]
            pts, cin, cout = pts[:-1], cin[:-1], cout[:-1]
        out.append({"pts": pts, "cin": cin, "cout": cout, "closed": sp["closed"]})
    return out


def shape_d(el: ET.Element) -> str | None:
    """Path data for the basic shapes (rect, circle, ellipse, line, polyline, polygon)."""
    tag = el.tag.replace(SVG_NS, "")
    f = lambda k, dflt=0.0: float(el.get(k, dflt) or dflt)  # noqa: E731
    k = 0.5522847498307936  # a circle as four cubics
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        rx = f("rx", el.get("ry", 0) or 0)
        ry = f("ry", el.get("rx", 0) or 0)
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx <= 0 or ry <= 0:
            return f"M{x},{y} H{x + w} V{y + h} H{x} Z"
        return (f"M{x + rx},{y} H{x + w - rx} A{rx},{ry} 0 0 1 {x + w},{y + ry} V{y + h - ry} "
                f"A{rx},{ry} 0 0 1 {x + w - rx},{y + h} H{x + rx} A{rx},{ry} 0 0 1 {x},{y + h - ry} "
                f"V{y + ry} A{rx},{ry} 0 0 1 {x + rx},{y} Z")
    if tag in ("circle", "ellipse"):
        cx, cy = f("cx"), f("cy")
        rx = f("r") if tag == "circle" else f("rx")
        ry = f("r") if tag == "circle" else f("ry")
        return (f"M{cx + rx},{cy} C{cx + rx},{cy + k * ry} {cx + k * rx},{cy + ry} {cx},{cy + ry} "
                f"C{cx - k * rx},{cy + ry} {cx - rx},{cy + k * ry} {cx - rx},{cy} "
                f"C{cx - rx},{cy - k * ry} {cx - k * rx},{cy - ry} {cx},{cy - ry} "
                f"C{cx + k * rx},{cy - ry} {cx + rx},{cy - k * ry} {cx + rx},{cy} Z")
    if tag == "line":
        return f"M{f('x1')},{f('y1')} L{f('x2')},{f('y2')}"
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", el.get("points", ""))]
        pts = list(zip(nums[0::2], nums[1::2]))
        if not pts:
            return None
        d = "M" + " L".join(f"{a},{b}" for a, b in pts)
        return d + (" Z" if tag == "polygon" else "")
    return None


def to_ae(subpaths: list[dict], m: Matrix) -> list[dict]:
    """Absolute subpaths through a transform -> AE paths: vertices plus tangents relative to their vertex."""
    res = []
    for sp in subpaths:
        V = [apply(m, p) for p in sp["pts"]]
        IN, OUT = [], []
        for k, v in enumerate(V):
            ci, co = sp["cin"][k], sp["cout"][k]
            IN.append([apply(m, ci)[0] - v[0], apply(m, ci)[1] - v[1]] if ci else [0.0, 0.0])
            OUT.append([apply(m, co)[0] - v[0], apply(m, co)[1] - v[1]] if co else [0.0, 0.0])
        r4 = lambda a: round(a, 4)  # noqa: E731
        res.append({"v": [[r4(a), r4(b)] for a, b in V], "i": [[r4(a), r4(b)] for a, b in IN],
                    "o": [[r4(a), r4(b)] for a, b in OUT], "c": sp["closed"]})
    return res


# ---------------------------------------------------------------------------------------------------------------
# Fills


def _style(el: ET.Element) -> dict[str, str]:
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def norm_color(value: str | None) -> tuple[str | None, str | None]:
    """(#RRGGBB or None for "none", warning or None)."""
    if value is None:
        return None, None
    v = value.strip()
    if v.lower() in ("none", "transparent"):
        return None, None
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
        return v.upper(), None
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", v):
        return ("#" + "".join(ch * 2 for ch in v[1:])).upper(), None
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", v)
    if m:
        return "#" + "".join(f"{int(x):02X}" for x in m.groups()), None
    if v.lower() in NAMED:
        return NAMED[v.lower()], None
    return None, f"fill {v!r} isn't a flat colour Slate can read (gradients and patterns need rebuilding in AE)"


def _fill_of(chain: list[ET.Element]) -> str | None:
    for el in reversed(chain):
        st = _style(el)
        if "fill" in st:
            return st["fill"]
        if el.get("fill") is not None:
            return el.get("fill")
    return "#000000"  # SVG's default fill


# ---------------------------------------------------------------------------------------------------------------
# Whole files


DRAWN = ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon")


def convert_text(text: str) -> dict:
    """SVG text -> AE shape data (see the module docstring)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise UsageError(f"That SVG doesn't parse: {exc}") from exc
    vb_raw = root.get("viewBox") or root.get("viewbox")
    if vb_raw:
        vb = [float(v) for v in re.split(r"[\s,]+", vb_raw.strip())]
    else:
        num = lambda k: float(re.sub(r"[a-z%]+$", "", root.get(k, "0") or "0") or 0)  # noqa: E731
        vb = [0.0, 0.0, num("width"), num("height")]
    parents = {c: p for p in root.iter() for c in p}
    items: list[dict] = []
    warnings: list[str] = []
    for el in root.iter():
        tag = el.tag.replace(SVG_NS, "")
        if tag in ("linearGradient", "radialGradient", "mask", "pattern", "text", "image", "use"):
            if not any(p.tag.replace(SVG_NS, "") == "defs" for p in _chain(el, parents)[:-1]):
                warnings.append(f"<{tag}> isn't converted; rebuild it in AE if the logo needs it")
            continue
        if tag not in DRAWN:
            continue
        chain = _chain(el, parents)
        if any(x.tag.replace(SVG_NS, "") in ("defs", "clipPath", "mask", "symbol") for x in chain[:-1]):
            continue
        d = el.get("d") if tag == "path" else shape_d(el)
        if not d:
            continue
        m = IDENTITY
        for x in chain:
            m = mul(m, parse_transform(x.get("transform")))
        subs = to_ae(parse_d(d), m)
        if not subs:
            continue
        fill, warn = norm_color(_fill_of(chain))
        if warn:
            warnings.append(warn)
        xs = [v[0] for s in subs for v in s["v"]]
        ys = [v[1] for s in subs for v in s["v"]]
        items.append({"id": el.get("id") or f"p{len(items) + 1}", "fill": fill, "paths": subs,
                      "bbox": [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]})
    seen: dict[str, int] = {}
    for it in items:  # AE addresses shape groups by name, so names must be unique
        n = seen.get(it["id"], 0)
        seen[it["id"]] = n + 1
        if n:
            it["id"] = f"{it['id']}-{n + 1}"
    return {"viewBox": vb, "items": items, "warnings": sorted(set(warnings))}


def _chain(el: ET.Element, parents: dict) -> list[ET.Element]:
    chain: list[ET.Element] = []
    p: ET.Element | None = el
    while p is not None:
        chain.insert(0, p)
        p = parents.get(p)
    return chain


def convert(path: str | Path) -> dict:
    """An SVG file -> AE shape data."""
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"No SVG at {p}.")
    return convert_text(p.read_text(encoding="utf-8"))


def brand_logos(pack: dict, pack_dir: Path | None) -> dict[str, dict]:
    """Every logo a brand pack lists, converted. Accepts {name: "file.svg"}, {name: {"svg": ...}} or a list of
    {name, svg|file|path}. Paths are relative to the pack's folder."""
    raw: Any = pack.get("logos") or {}
    entries: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, list):
        entries = [(str(x.get("name") or x.get("id") or f"logo{i + 1}"), x) for i, x in enumerate(raw) if isinstance(x, dict)]
    out: dict[str, dict] = {}
    for name, spec in entries:
        rel = spec if isinstance(spec, str) else (spec.get("svg") or spec.get("file") or spec.get("path")
                                                   if isinstance(spec, dict) else None)
        if not rel or not str(rel).lower().endswith(".svg"):
            continue
        p = Path(rel)
        if not p.is_absolute() and pack_dir:
            p = pack_dir / p
        out[str(name)] = convert(p)
    return out
