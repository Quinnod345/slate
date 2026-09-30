"""Colours for brand packs: read any colour a brand's code or guidelines write, and measure text contrast.

Onboarding meets colours in many spellings: `#00AACC` in a stylesheet, `rgb(0 170 204 / 80%)`, `hsl(…)`, the
`oklch(…)` that modern design tokens use, `Color(red: 0.1, green: …)` in SwiftUI, "RGB 0, 170, 204" in a guidelines
PDF. Everything is read into one shape, (r, g, b, alpha) with r, g, b in 0…255, and written into a pack as `#RRGGBB`
(or `#RRGGBBAA` when it's see-through), because that is what every engine reads: the Remotion kit, After Effects'
ExtendScript kit and the accessibility gate.

Contrast is WCAG 2's: relative luminance, then (lighter + 0.05) / (darker + 0.05). Body text needs 4.5:1 and large
text 3:1 (docs/PLAN.md, "Quality"). A see-through background is judged over black and over white and the worse one
counts, the same rule the accessibility gate (`slate.qa.access`) applies, so onboarding and the gate agree.
"""

from __future__ import annotations

import math
import re
from typing import Any

RGBA = tuple[float, float, float, float]

#: What a text colour pair needs, by role (WCAG 2 AA).
CONTRAST = {"body": 4.5, "large": 3.0}

_NAMED = {"white": (255.0, 255.0, 255.0, 1.0), "black": (0.0, 0.0, 0.0, 1.0),
          "transparent": (0.0, 0.0, 0.0, 0.0)}
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC = re.compile(r"^(rgba?|hsla?|oklch|oklab)\(\s*([^)]*)\)$", re.IGNORECASE)


def _num(text: str) -> float | None:
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _split(args: str) -> tuple[list[str], str | None]:
    """CSS colour arguments, comma or space separated, with an optional `/ alpha`."""
    alpha = None
    if "/" in args:
        args, alpha = args.split("/", 1)
        alpha = alpha.strip()
    parts = [p for p in re.split(r"[\s,]+", args.strip()) if p]
    if alpha is None and len(parts) == 4:
        alpha = parts.pop()
    return parts, alpha


def _alpha(text: str | None) -> float | None:
    if text is None:
        return 1.0
    t = text.strip()
    if t.endswith("%"):
        v = _num(t[:-1])
        return None if v is None else max(0.0, min(1.0, v / 100))
    v = _num(t)
    return None if v is None else max(0.0, min(1.0, v))


def _channel(text: str) -> float | None:
    """An rgb() channel: 0…255, or a percentage."""
    t = text.strip()
    if t.endswith("%"):
        v = _num(t[:-1])
        return None if v is None else v * 2.55
    return _num(t)


def _hsl(h: float, s: float, light: float) -> tuple[float, float, float]:
    h = (h % 360) / 360.0
    if s == 0:
        return (light * 255, light * 255, light * 255)

    def hue(p: float, q: float, t: float) -> float:
        t %= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    q = light * (1 + s) if light < 0.5 else light + s - light * s
    p = 2 * light - q
    return tuple(255 * hue(p, q, h + d) for d in (1 / 3, 0.0, -1 / 3))  # type: ignore[return-value]


def _encode(c: float) -> float:
    """Linear light to sRGB (0…1)."""
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab(L: float, a: float, b: float) -> tuple[float, float, float]:
    """OKLab to sRGB 0…255 (Björn Ottosson's matrices), clamped into the sRGB gamut."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return (_encode(r) * 255, _encode(g) * 255, _encode(bl) * 255)


def _lightness(text: str) -> float | None:
    t = text.strip()
    if t.endswith("%"):
        v = _num(t[:-1])
        return None if v is None else v / 100
    return _num(t)


def parse(value: Any) -> RGBA | None:
    """(r, g, b, alpha) from a hex string, a CSS colour function, [r, g, b(, a)] or {"hex": …}. None when it isn't one."""
    if isinstance(value, dict):
        for key in ("hex", "value", "color", "colour"):
            if key in value:
                return parse(value[key])
        if all(k in value for k in ("r", "g", "b")):
            return parse([value["r"], value["g"], value["b"], value.get("a", value.get("alpha", 1.0))])
        return None
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        nums = [_num(str(x)) for x in value]
        if any(n is None for n in nums):
            return None
        r, g, b = (float(n) for n in nums[:3])  # type: ignore[arg-type]
        if max(r, g, b) <= 1.0 and any(x != int(x) for x in (r, g, b)):
            r, g, b = r * 255, g * 255, b * 255
        a = float(nums[3]) if len(nums) == 4 else 1.0  # type: ignore[arg-type]
        return (r, g, b, a if a <= 1 else a / 255)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.lower() in _NAMED:
        return _NAMED[text.lower()]
    m = _HEX.match(text)
    if m and (text.startswith("#") or len(text) in (6, 8)):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (float(r), float(g), float(b), a)
    f = _FUNC.match(text)
    if not f:
        return None
    kind, (parts, alpha_text) = f.group(1).lower(), _split(f.group(2))
    alpha = _alpha(alpha_text)
    if len(parts) != 3 or alpha is None:
        return None
    if kind.startswith("rgb"):
        chans = [_channel(p) for p in parts]
        if any(c is None for c in chans):
            return None
        r, g, b = (max(0.0, min(255.0, float(c))) for c in chans)  # type: ignore[arg-type]
        return (r, g, b, alpha)
    if kind.startswith("hsl"):
        h = _num(parts[0].replace("deg", ""))
        s, light = _lightness(parts[1]), _lightness(parts[2])
        if h is None or s is None or light is None:
            return None
        return (*_hsl(h, max(0.0, min(1.0, s)), max(0.0, min(1.0, light))), alpha)
    L = _lightness(parts[0])
    if L is None:
        return None
    if kind == "oklab":
        a, b = _num(parts[1]), _num(parts[2])
        if a is None or b is None:
            return None
        return (*_oklab(L, a, b), alpha)
    c, h = _num(parts[1].rstrip("%")), _num(parts[2].replace("deg", ""))
    if c is None or h is None:
        return None
    if parts[1].endswith("%"):
        c = c / 100 * 0.4  # CSS Color 4: 100% chroma is 0.4
    rad = math.radians(h)
    return (*_oklab(L, c * math.cos(rad), c * math.sin(rad)), alpha)


def is_colour(value: Any) -> bool:
    return parse(value) is not None


def _byte(v: float) -> int:
    """0…255, rounding halves up (76.5 → 77) the way colour tools do, not to even."""
    return max(0, min(255, int(math.floor(v + 0.5))))


def to_hex(rgba: RGBA) -> str:
    """`#RRGGBB`, or `#RRGGBBAA` when the colour is see-through."""
    r, g, b, a = rgba
    out = "#" + "".join(f"{_byte(c):02X}" for c in (r, g, b))
    if a < 1.0:
        out += f"{_byte(a * 255):02X}"
    return out


def normalise(value: Any) -> str | None:
    """Any colour as the hex a pack stores, or None."""
    c = parse(value)
    return to_hex(c) if c is not None else None


def _linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgba: RGBA) -> float:
    """WCAG relative luminance of an opaque colour."""
    r, g, b = (_linear(x) for x in rgba[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _composite(top: RGBA, under: RGBA) -> RGBA:
    a = top[3]
    return (top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1.0)


def ratio(fg: RGBA, bg: RGBA) -> float:
    """Contrast of a text colour over a background (1 to 21). A see-through background is judged over black and over
    white, and the worse counts; a see-through text colour is composited over the background first."""
    if bg[3] < 1.0:
        return min(ratio(fg, _composite(bg, under)) for under in (_NAMED["black"], _NAMED["white"]))
    shown = _composite(fg, bg) if fg[3] < 1.0 else fg
    la, lb = luminance(shown), luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)
