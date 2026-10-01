"""The access gate: flashes, text contrast, caption pace and platform safe zones.

`slate qa access` (and `slate qa`, which runs every gate) checks four things as sub-checks of one `access` gate:

- flashes     at most 3 flashes in any one second, by WCAG 2.3.1's general-flash and red-flash thresholds, measured
              on the picture (the master when there is one, else each act's render);
- contrast    every text colour pair the brand declares (slate.brand.pack.contrast_pairs) meets 4.5:1 for body text
              and 3:1 for large text, by WCAG's relative luminance;
- captions    at most 17 characters a second, and at most 2 lines of 42 characters each (tightened, never
              loosened, by the film's format at its aspect and by film.json: the same rule as slate.qa's captions
              gate, so the two never disagree about one cue);
- safe_zones  on a 9:16 film, no text sits where Instagram Reels, TikTok or YouTube Shorts draw their own buttons and
              captions (the format's zones when it has them, else built-in ones). The text boxes come from Vision OCR:
              on the picture every quarter second (the shared sweep, which holds every frame of the half-second
              contact sheet, read at full size), else on the film's contact sheets, else on the sheet given with
              --sheet. The label the engine burns into each contact-sheet cell is review material and is left out.
              UNCHECKED when Vision OCR can't run here.
- title_safe  on every film, every text box inside title-safe: the middle 90% of the frame (96 px in from the sides
              and 54 px from the top and bottom on a 1920 × 1080 frame, scaled to the frame); text outside it fails,
              and text outside action-safe (93%) says so too. Same text boxes as safe_zones.
- type_size   body copy (a line of four or more words, not set in capitals) stands at least 28 px tall at 1080 lines:
              each line's capitals are measured on the picture inside its OCR box and must reach 62% of 28 px (the
              lowest common cap height), scaled to the frame's short side. The original film's 12 to 14 px body copy
              was unreadable. Labels, eyebrows and anything a shot declares not for reading are left out.

A sub-check that doesn't apply is left out rather than passed: a 16:9 film has no 9:16 safe zones, and a film with
no voice has no captions. One that applies but can't run yet (no render, no OCR, no brand pairs) is UNCHECKED, with
the step that fixes it. A run narrowed to part of the film (--only, or a file given in place of the film's own) says
so in an UNCHECKED scope sub-check: it reports what it finds, but only a full run can pass the gate.

How flashes are measured (WCAG 2.2, "general flash and red flash thresholds"): a general flash is a pair of opposing
changes in relative luminance of at least 0.1 where the darker state is below 0.8; a red flash is a pair of opposing
transitions to or from a saturated red (R / (R + G + B) ≥ 0.8) that move at least 0.2 in the CIE 1976 u′v′
chromaticity diagram. Each pixel's changes are followed as runs between its peaks and valleys, so a change spread
over several frames still counts as one transition. A transition counts when it covers at least 25% of a 10° visual
field, taken as a third of the frame's width by a third of its height (WCAG's 341 × 256 at 1024 × 768). Frames are
compared at a small size, averaged, so a fine balanced pattern (a flickering checkerboard) cancels out, as WCAG's
exception for it intends. More than 6 transitions (3 flashes) in any one second fails.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from slate import __version__, gates, jsonio
from slate.errors import MissingPrerequisite, SlateError, UsageError
from slate.gates import GateResult
# The film's format is read by slate.qa.film, so every gate reads one film's format the same way.
from slate.qa.film import _load_with, _raw_format, film_aspect  # noqa: F401 - kept importable from here
from slate.qa.film import read_format as _read_format

GATE = "access"

# ---------------------------------------------------------------------------------------------------- thresholds
#: WCAG 2.3.1 (Three Flashes or Below Threshold), with the 2.2 definitions of general and red flashes.
FLASH: dict[str, Any] = {
    "max_flashes": 3,  # per second; a flash is two opposing transitions, so more than 6 transitions fails
    "delta": 0.10,  # a general transition changes relative luminance by at least 10% of the maximum
    "darker_below": 0.80,  # … and its darker state is below 0.80
    "red_saturation": 0.80,  # a saturated red: R / (R + G + B) ≥ 0.8
    "red_uv": 0.20,  # a red transition moves more than 0.2 in the CIE 1976 u′v′ diagram
    "area": 0.25,  # of a 10° visual field
    "field": 1 / 3,  # a 10° field is a third of the frame each way (WCAG's 341 × 256 at 1024 × 768)
    "field_step": 1 / 12,  # the field slides over the frame in twelfths
    "long_side": 96,  # analysis size: frames are averaged down to 96 px on their long side
    "dead_band": 0.004,  # luminance changes smaller than this are noise, not the start of a transition
    "red_dead_band": 0.01,
    "chroma_floor": 0.02,  # below this luminance a pixel has no reliable colour (codec noise): it counts as neutral
}
D65_UV = (0.1978, 0.4683)  # the white point's u′v′: black and greys sit here

#: WCAG 1.4.3 (Contrast, Minimum): 4.5:1 for body text, 3:1 for large text.
CONTRAST = {"body": 4.5, "large": 3.0}

#: Caption pace and shape (docs/PLAN.md "Quality"; Netflix's timed-text guide sets 17 characters a second for
#: children's programmes and 20 for adults, and two lines of 42). A format may ask for less, never more.
CAPTIONS = {"max_cps": 17.0, "max_lines": 2, "max_chars": 42}

#: Built-in 9:16 safe zones, as fractions of the frame, for when slate.formats isn't installed (whenever it is, its
#: channel library, formats/_platforms.json, answers instead). Text stays out of the top, bottom and side bands and
#: the keep-out areas ([x0, y0, x1, y1] from the top left) where each platform draws its own buttons, names and
#: captions. The numbers are the library's own, so the two can't disagree (tests hold them equal); verified 2026-09-30.
BUILTIN_ZONES: dict[str, dict] = {
    "instagram_reels": {
        "top": 0.14, "bottom": 0.35, "left": 0.06, "right": 0.06, "keep_out": [[0.79, 0.6, 1.0, 1.0]],
        "source": "Meta Ads Guide, Instagram Reels video ads: keep at least 14% of the top, 35% of the bottom and 6% "
                  "on each side free of text, logos and key elements (with the action column at the right)",
    },
    "tiktok": {
        "top": 0.125, "bottom": 0.3438, "left": 0.1111, "right": 0.1111, "keep_out": [[0.7222, 0.2917, 1.0, 1.0]],
        "source": "TikTok's in-feed safe-zone overlay, as measured by AdKit: 240 px top, 660 px bottom, 120 px each "
                  "side of 1080 x 1920, and a 300 px action column from y = 560 down",
    },
    "youtube_shorts": {
        "top": 0.15, "bottom": 0.35, "left": 0.0444, "right": 0.1778, "keep_out": [],
        "source": "Google's transparent vertical video template, as measured by AdKit: 288 px top, 672 px bottom, "
                  "48 px left and 192 px right of 1080 x 1920",
    },
}
#: Spellings of the three built-in channels, for when slate.formats (whose library has every alias) isn't installed.
#: Plain "youtube" is YouTube's own 16:9 channel, not Shorts.
PLATFORM_ALIASES = {
    "reels": "instagram_reels", "instagram": "instagram_reels", "ig": "instagram_reels", "ig_reels": "instagram_reels",
    "instagramreels": "instagram_reels", "tik_tok": "tiktok", "shorts": "youtube_shorts",
    "yt_shorts": "youtube_shorts", "ytshorts": "youtube_shorts", "youtubeshorts": "youtube_shorts",
}
OCR_MIN_CONF = 0.5
ZONE_TOLERANCE = 0.005  # a text box may graze a zone's edge by half a percent of the frame (OCR boxes are loose)


def _np():
    import numpy  # noqa: PLC0415

    return numpy


def _optional(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _sub(name: str, status: str, summary: str, evidence: list[str] | None = None, **details):
    """A sub-check result. A `hint` (the step that fixes an UNCHECKED) is kept in details and shown as evidence."""
    from slate.qa.checks import Sub  # noqa: PLC0415

    evidence = list(evidence or [])
    if details.get("hint"):
        evidence.append(f"next: {details['hint']}")
    return Sub(name, status, summary, evidence, details)


# ---------------------------------------------------------------------------------------------------- colour
def srgb_to_linear(c):
    """sRGB component(s) 0…1 to linear light (the sRGB transfer, as WCAG uses it)."""
    np = _np()
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def relative_luminance(rgb) -> float:
    """WCAG relative luminance of an sRGB colour given as 0…255 values (or 0…1 floats)."""
    np = _np()
    c = np.asarray(rgb[:3], dtype=float)
    if c.max() > 1.0:
        c = c / 255.0
    r, g, b = srgb_to_linear(c)
    return float(0.2126 * r + 0.7152 * g + 0.0722 * b)


def contrast_ratio(a, b) -> float:
    """WCAG contrast ratio (L1 + 0.05) / (L2 + 0.05), lighter over darker: 1 (none) to 21 (black on white)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


_HEX = re.compile(r"^#?([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def parse_colour(v: Any) -> tuple[float, float, float, float] | None:
    """(r, g, b, alpha) with r, g, b in 0…255 and alpha 0…1, from #rgb, #rgba, #rrggbb, #rrggbbaa, rgb(), rgba(),
    [r, g, b(, a)] or {"hex": …}/{"r": …}. None when it isn't a colour."""
    if isinstance(v, dict):
        for k in ("hex", "value", "color", "colour", "srgb"):
            if k in v:
                return parse_colour(v[k])
        if all(k in v for k in ("r", "g", "b")):
            return parse_colour([v["r"], v["g"], v["b"], v.get("a", v.get("alpha", 1.0))])
        return None
    if isinstance(v, (list, tuple)) and len(v) in (3, 4) and all(_num(x) is not None for x in v):
        r, g, b = (float(x) for x in v[:3])
        # 0…255 unless every channel is at most 1 and one of them is fractional (then it's 0…1 floats)
        if max(r, g, b) <= 1.0 and any(x != int(x) for x in (r, g, b)):
            r, g, b = r * 255, g * 255, b * 255
        a = float(v[3]) if len(v) == 4 else 1.0
        return (r, g, b, a if a <= 1 else a / 255.0)
    if not isinstance(v, str):
        return None
    s = v.strip()
    m = _HEX.match(s)
    if m and (s.startswith("#") or len(s) in (6, 8)):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (float(r), float(g), float(b), a)
    m = re.match(r"^rgba?\(\s*([^)]*)\)$", s, re.I)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        if len(parts) in (3, 4):
            try:
                rgb = [float(p[:-1]) * 2.55 if p.endswith("%") else float(p) for p in parts[:3]]
                a = 1.0
                if len(parts) == 4:
                    a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
                return (rgb[0], rgb[1], rgb[2], a)
            except ValueError:
                return None
    return None


def composite(fg: tuple, bg: tuple) -> tuple[float, float, float]:
    """fg painted over an opaque bg (sRGB values, the way screens blend)."""
    a = fg[3]
    return tuple(a * fg[i] + (1 - a) * bg[i] for i in range(3))  # type: ignore[return-value]


def pair_ratio(fg: tuple, bg: tuple) -> float:
    """The contrast of a text colour over a background. A see-through background is judged over black and over
    white (whatever is under it), and the worse of the two counts."""
    if bg[3] < 1.0:
        worst = math.inf
        for under in ((0.0, 0.0, 0.0, 1.0), (255.0, 255.0, 255.0, 1.0)):
            b = (*composite(bg, under), 1.0)
            worst = min(worst, pair_ratio(fg, b))
        return worst
    shown = composite(fg, bg) if fg[3] < 1.0 else fg[:3]
    return contrast_ratio(shown, bg[:3])


# ---------------------------------------------------------------------------------------------------- flashes
def _luma_chroma(frame):
    """Linear RGB, relative luminance, redness R / (R + G + B) and u′v′ chromaticity per pixel of an sRGB frame."""
    np = _np()
    x = np.asarray(frame, dtype=np.float64)
    if x.max() > 1.0:
        x = x / 255.0
    lin = srgb_to_linear(x[..., :3])
    R, G, B = lin[..., 0], lin[..., 1], lin[..., 2]
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    X = 0.4124 * R + 0.3576 * G + 0.1805 * B
    Z = 0.0193 * R + 0.1192 * G + 0.9505 * B
    s = R + G + B
    denom = X + 15 * Y + 3 * Z
    neutral = (Y < FLASH["chroma_floor"]) | (denom <= 1e-12)
    with np.errstate(divide="ignore", invalid="ignore"):
        red = np.where(s > 1e-12, R / s, 1 / 3)
        u = np.where(neutral, D65_UV[0], 4 * X / denom)
        v = np.where(neutral, D65_UV[1], 9 * Y / denom)
    red = np.where(neutral, 1 / 3, red)
    return Y, red, u, v


@dataclasses.dataclass
class _Windows:
    """The 10° fields the transitions are measured over: a third of the frame each way, slid in twelfths."""

    h: int
    w: int

    def __post_init__(self):
        self.wh = max(1, int(round(self.h * FLASH["field"])))
        self.ww = max(1, int(round(self.w * FLASH["field"])))
        sy = max(1, int(round(self.h * FLASH["field_step"])))
        sx = max(1, int(round(self.w * FLASH["field_step"])))
        ys = sorted(set(list(range(0, self.h - self.wh + 1, sy)) + [self.h - self.wh]))
        xs = sorted(set(list(range(0, self.w - self.ww + 1, sx)) + [self.w - self.ww]))
        np = _np()
        self.y0 = np.array([y for y in ys for _x in xs])
        self.x0 = np.array([x for _y in ys for x in xs])

    @property
    def count(self) -> int:
        return len(self.y0)

    def fractions(self, mask):
        """The share of each field that `mask` covers."""
        np = _np()
        s = np.zeros((self.h + 1, self.w + 1))
        s[1:, 1:] = np.cumsum(np.cumsum(mask.astype(np.float64), axis=0), axis=1)
        y0, x0, y1, x1 = self.y0, self.x0, self.y0 + self.wh, self.x0 + self.ww
        total = s[y1, x1] - s[y0, x1] - s[y1, x0] + s[y0, x0]
        return total / float(self.wh * self.ww)

    def box(self, i: int) -> list[float]:
        x0, y0 = int(self.x0[i]), int(self.y0[i])
        return [round(x0 / self.w, 3), round(y0 / self.h, 3), round((x0 + self.ww) / self.w, 3),
                round((y0 + self.wh) / self.h, 3)]


class FlashScan:
    """Counts general and red flashes per 10° field, frame by frame (feed() each frame in order, then result())."""

    def __init__(self, fps: Fraction | float, *, first_frame: int = 0):
        self.fps = Fraction(fps).limit_denominator(1_000_000) if not isinstance(fps, Fraction) else fps
        self.first = int(first_frame)
        self.n = 0
        self.state: dict[str, Any] = {}
        self.win: _Windows | None = None
        self.events: dict[str, list[list[tuple[int, int]]]] = {"general": [], "red": []}

    def feed(self, frame) -> None:
        np = _np()
        Y, red, u, v = _luma_chroma(frame)
        f = self.first + self.n
        self.n += 1
        if self.win is None:
            self.win = _Windows(*Y.shape)
            self.events = {"general": [[] for _ in range(self.win.count)], "red": [[] for _ in range(self.win.count)]}
            z = np.zeros(Y.shape, dtype=np.int8)
            self.state = {"L": Y, "dir": z.copy(), "start": Y.copy(), "done": np.zeros(Y.shape, bool),
                          "r": red, "u": u, "v": v, "rdir": z.copy(), "rs": red.copy(), "us": u.copy(),
                          "vs": v.copy(), "rdone": np.zeros(Y.shape, bool)}
            return
        s = self.state
        # general flashes: runs of luminance change between a pixel's peaks and valleys
        d = Y - s["L"]
        new = np.where(d > FLASH["dead_band"], 1, np.where(d < -FLASH["dead_band"], -1, 0)).astype(np.int8)
        restart = (new != 0) & (new != s["dir"])
        s["start"] = np.where(restart, s["L"], s["start"])
        s["dir"] = np.where(restart, new, s["dir"])
        s["done"] = np.where(restart, False, s["done"])
        amp = Y - s["start"]
        qual = (s["dir"] != 0) & (np.abs(amp) >= FLASH["delta"]) & \
            (np.minimum(s["start"], Y) < FLASH["darker_below"]) & ~s["done"]
        s["done"] = s["done"] | qual
        s["L"] = Y
        self._record("general", f, qual & (amp > 0), qual & (amp < 0))
        # red flashes: runs of change in redness, measured as distance in u′v′, to or from a saturated red
        dr = red - s["r"]
        rnew = np.where(dr > FLASH["red_dead_band"], 1, np.where(dr < -FLASH["red_dead_band"], -1, 0)).astype(np.int8)
        rrestart = (rnew != 0) & (rnew != s["rdir"])
        s["rs"] = np.where(rrestart, s["r"], s["rs"])
        s["us"] = np.where(rrestart, s["u"], s["us"])
        s["vs"] = np.where(rrestart, s["v"], s["vs"])
        s["rdir"] = np.where(rrestart, rnew, s["rdir"])
        s["rdone"] = np.where(rrestart, False, s["rdone"])
        dist = np.hypot(u - s["us"], v - s["vs"])
        rqual = (s["rdir"] != 0) & (dist > FLASH["red_uv"]) & \
            (np.maximum(s["rs"], red) >= FLASH["red_saturation"]) & ~s["rdone"]
        s["rdone"] = s["rdone"] | rqual
        s["r"], s["u"], s["v"] = red, u, v
        self._record("red", f, rqual & (s["rdir"] > 0), rqual & (s["rdir"] < 0))

    def _record(self, kind: str, frame: int, up, down) -> None:
        if not (up.any() or down.any()):
            return
        fu = self.win.fractions(up)
        fd = self.win.fractions(down)
        area = FLASH["area"]
        np = _np()
        for i in np.flatnonzero((fu >= area) | (fd >= area)):
            self.events[kind][i].append((frame, 1 if fu[i] >= fd[i] else -1))

    def _worst(self, events: list[tuple[int, int]]) -> tuple[int, int, list[tuple[int, int]]]:
        """(most transitions in any one second, the frame that second starts, every second with more than 6)."""
        merged: list[tuple[int, int]] = []
        for f, d in events:
            if merged and merged[-1][1] == d:
                continue  # a flash needs opposing transitions: a second change the same way is part of the first
            merged.append((f, d))
        frames = [f for f, _d in merged]
        num, den = self.fps.numerator, self.fps.denominator
        best, at, over = 0, frames[0] if frames else 0, []
        j = 0
        limit = 2 * FLASH["max_flashes"]
        for i in range(len(frames)):
            while (frames[i] - frames[j]) * den >= num:
                j += 1
            count = i - j + 1
            if count > best:
                best, at = count, frames[j]
            if count > limit:
                over.append((frames[j], frames[i]))
        return best, at, over

    def result(self) -> dict:
        out: dict[str, Any] = {"frames": self.n, "first_frame": self.first}
        failing: list[tuple[int, int]] = []
        for kind in ("general", "red"):
            best, at, window, spans = 0, None, None, []
            for i, ev in enumerate(self.events.get(kind) or []):
                if not ev:
                    continue
                b, a, over = self._worst(ev)
                spans += over
                if b > best:
                    best, at, window = b, a, i
            out[kind] = {"max_transitions_per_second": best, "max_flashes_per_second": best / 2,
                         "at_frame": at, "field": self.win.box(window) if (self.win and window is not None) else None}
            failing += spans
        merged: list[list[int]] = []
        for a, b in sorted(failing):
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        out["failing"] = merged
        out["passed"] = not merged
        return out


def flash_size(width: int, height: int) -> tuple[int, int]:
    """The analysis size: the film's aspect, FLASH['long_side'] px on the long side."""
    long_side = min(FLASH["long_side"], max(width, height))
    if width >= height:
        return long_side, max(3, int(round(long_side * height / width)))
    return max(3, int(round(long_side * width / height))), long_side


def read_rgb(source: Path, size: tuple[int, int], *, count: int | None = None) -> Iterator:
    """Frames of a video (through slate.qa.media) or of a folder of images, as uint8 RGB at `size`."""
    np = _np()
    source = Path(source)
    if source.is_dir():
        from PIL import Image  # noqa: PLC0415

        from slate.qa.motion import IMAGE_EXTS  # noqa: PLC0415

        files = sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."))
        for p in files[: count if count is not None else None]:
            with Image.open(p) as im:
                yield np.asarray(im.convert("RGB").resize(size, Image.Resampling.BOX))
        return
    from slate.qa import media  # noqa: PLC0415

    yield from media.iter_frames(source, count=count, size=size, pix_fmt="rgb24")


def scan_flashes(frames: Iterable, fps: Fraction | float, *, first_frame: int = 0) -> dict:
    """Run the flash scan over frames (RGB arrays, in order) at a frame rate; see FlashScan."""
    scan = FlashScan(fps, first_frame=first_frame)
    for frame in frames:
        scan.feed(frame)
    return scan.result()


def _picture_sources(ctx, render: str | None, render_act: int | None, render_from: int | None):
    """What the flash scan reads: an explicit file, else the master (the whole film), else each act's render.
    Returns ([(label, path, first film frame, frame count)], acts without a render)."""
    if render:
        p = Path(render).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p) if (Path.cwd() / p).exists() else ctx.path(render)
        a = next((x for x in ctx.acts if render_act is not None and x["index"] == render_act), None)
        start = render_from if render_from is not None else (int(a["frame_in"]) if a and a["frame_in"] is not None
                                                             else 0)
        return [(ctx.rel(p), p, int(start), None)], []
    masters = ctx.masters()
    if masters:
        return [(ctx.rel(masters[0]), masters[0], 0, ctx.total_frames)], []
    current = ctx.current_renders()
    out, missing = [], []
    for a in ctx.acts:
        e = current.get(a["index"])
        if e is None or not Path(e["path"]).exists():
            missing.append(a["index"])
            continue
        out.append((ctx.rel(e["path"]), Path(e["path"]), int(e["frame_in"]), int(e["frame_out"]) - int(e["frame_in"])))
    return out, missing


def check_flashes(ctx, *, render: str | None = None, render_act: int | None = None, render_from: int | None = None,
                  read: list | None = None):
    """At most 3 flashes a second on the picture. Every picture file it reads is appended to `read` (the gate's
    inputs, so a new render or master turns the result STALE)."""
    from slate.qa.motion import cached_scan, source_digest  # noqa: PLC0415

    read = read if read is not None else []
    fps = ctx.fps
    if fps is None:
        return _sub("flashes", "UNCHECKED", "film.json has no frame rate, so flashes per second can't be counted")
    size = ctx.size
    sources, missing = _picture_sources(ctx, render, render_act, render_from)
    if not sources:
        where = f" of act{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))}" if missing else ""
        return _sub("flashes", "UNCHECKED", f"there's no picture{where} to scan for flashes yet (no master, no act "
                    "render)", [], hint="`slate render` registers each act's render; then run `slate qa access`.",
                    missing=missing)
    from slate.qa import media  # noqa: PLC0415

    results = []
    evidence = []
    for label, path, first, count in sources:
        if not path.exists():
            return _sub("flashes", "UNCHECKED", f"{label} doesn't exist", [str(path)])
        read.append(path)
        if path.is_file() and not media.have("ffmpeg"):
            return _sub("flashes", "UNCHECKED", "ffmpeg isn't installed, so the picture can't be decoded", [],
                        hint="Install it with Homebrew (`brew install ffmpeg`), or run `slate doctor --install`.")
        w, h = size if size else _source_size(path)
        analysis = flash_size(w, h)
        key = {"file": label, "sha256": source_digest(ctx, path), "first_frame": first, "frames": count,
               "fps": [fps.numerator, fps.denominator], "analysis": list(analysis), "flash": FLASH,
               "version": __version__}

        def scan(path=path, analysis=analysis, count=count, first=first):
            return scan_flashes(read_rgb(path, analysis, count=count), fps, first_frame=first)

        try:
            res = dict(cached_scan(ctx, "access", path, key, scan, what="flashes", kind="flashes"))
        except MissingPrerequisite as exc:
            return _sub("flashes", "UNCHECKED", exc.message, [label], hint=exc.hint)
        except SlateError as exc:
            return _sub("flashes", "UNCHECKED", f"{label} couldn't be decoded: {exc}", [str(path)])
        res["file"] = label
        results.append(res)
        evidence.append(f"{label}: {res['frames']} frames; most in any second: general "
                        f"{res['general']['max_flashes_per_second']:g}, red {res['red']['max_flashes_per_second']:g}")
    worst_g = max(r["general"]["max_flashes_per_second"] for r in results)
    worst_r = max(r["red"]["max_flashes_per_second"] for r in results)
    failing = [(r["file"], span) for r in results for span in r["failing"]]
    details = {"scans": results, "method": "WCAG 2.3.1 general and red flash thresholds (2.2 definitions)",
               "analysis_size": list(flash_size(*(size or (0, 0)))) if size else None}
    if missing:
        details["missing"] = missing
    if failing:
        times = []
        for label, (a, b) in failing[:8]:
            times.append(f"{label}: {ctx.secs_of(a):.2f}–{ctx.secs_of(b):.2f} s (film frames {a}–{b})")
        kind = "red" if worst_r > worst_g else "general"
        top = max(worst_g, worst_r)
        return _sub("flashes", "FAIL", f"the picture flashes up to {top:g} times a second ({kind} flashes; the limit "
                    f"is 3) in {_plural(len(failing), 'place')}, first at {times[0]}", times + evidence, **details)
    if missing:
        return _sub("flashes", "UNCHECKED", f"no flashing in what's rendered, but act{'s' if len(missing) > 1 else ''} "
                    f"{', '.join(map(str, missing))} {'have' if len(missing) > 1 else 'has'} no render to scan yet",
                    evidence, **details)
    if max(worst_g, worst_r) == 0:
        return _sub("flashes", "PASS", "no flashing (the limit is 3 flashes a second)", evidence, **details)
    return _sub("flashes", "PASS", f"at most {max(worst_g, worst_r):g} flashes in any second (general "
                f"{worst_g:g}, red {worst_r:g}; the limit is 3)", evidence, **details)


def _source_size(path: Path) -> tuple[int, int]:
    if path.is_dir():
        from PIL import Image  # noqa: PLC0415

        from slate.qa.motion import IMAGE_EXTS  # noqa: PLC0415

        first = next(p for p in sorted(path.iterdir()) if p.suffix.lower() in IMAGE_EXTS)
        with Image.open(first) as im:
            return im.size
    from slate.qa import media  # noqa: PLC0415

    info = media.video_info(path, count=False)
    return int(info["width"]), int(info["height"])


# ---------------------------------------------------------------------------------------------------- contrast
def _pack_fn(name: str) -> Callable | None:
    mod = _optional("slate.brand.pack")
    fn = getattr(mod, name, None) if mod is not None else None
    return fn if callable(fn) else None


def brand_pairs(pack: dict) -> tuple[list[dict] | None, dict, str]:
    """(contrast pairs, palette, where from): slate.brand.pack when it's installed, else brand.json's keys."""
    pairs: Any = None
    source = "brand.json"
    fn = _pack_fn("contrast_pairs")
    if fn is not None:
        try:
            pairs = fn(pack)
            source = "slate.brand.pack.contrast_pairs"
        except Exception:  # noqa: BLE001 - read the keys instead
            pairs = None
    if pairs is None:
        pal = pack.get("palette") if isinstance(pack.get("palette"), dict) else {}
        pairs = pack.get("contrast_pairs") or pack.get("text_pairs") or pal.get("pairs") or pal.get("contrast_pairs")
    palette: Any = None
    fn = _pack_fn("palette")
    if fn is not None:
        try:
            palette = fn(pack)
        except Exception:  # noqa: BLE001
            palette = None
    if not isinstance(palette, dict):
        palette = flat_palette(pack.get("palette") if isinstance(pack.get("palette"), dict) else {})
    tokens = palette.get("tokens") if isinstance(palette.get("tokens"), dict) else palette
    if pairs is None:
        return None, tokens, source
    return [p for p in pairs if isinstance(p, dict)], tokens, source


_PALETTE_RESERVED = frozenset({"roles", "tones", "notes", "note", "source", "sources", "pairs", "contrast_pairs",
                               "tokens"})


def flat_palette(pal: dict) -> dict:
    """A palette as flat tokens ("gray.50": "#…"), with each role (palette.roles) added under its own name: the shape
    slate.brand.pack.palette gives, for when that module isn't installed."""
    out: dict[str, Any] = {}

    def walk(tree: Any, prefix: str = "") -> None:
        for key, value in (tree.items() if isinstance(tree, dict) else ()):
            if not prefix and key in _PALETTE_RESERVED:
                continue
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and parse_colour(value) is None:
                walk(value, name)
            else:
                out[name] = value
    walk(pal.get("tokens") if isinstance(pal.get("tokens"), dict) else pal)
    roles = pal.get("roles") if isinstance(pal.get("roles"), dict) else {}
    for role, ref in roles.items():
        out.setdefault(str(role), ref)
    return out


def resolve_colour(ref: Any, palette: dict, depth: int = 0) -> tuple[float, float, float, float] | None:
    """A colour from a literal (#hex, rgb()) or a palette token name (tokens may point at other tokens)."""
    c = parse_colour(ref)
    if c is not None or depth > 8:
        return c
    if isinstance(ref, str):
        key = ref.strip()
        for cand in (key, key.lstrip("$").lstrip("-"), key.lower()):
            if cand in palette:
                return resolve_colour(palette[cand], palette, depth + 1)
        folded = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in palette.items()}
        k2 = re.sub(r"[^a-z0-9]", "", key.lower())
        if k2 in folded:
            return resolve_colour(folded[k2], palette, depth + 1)
    if isinstance(ref, dict):
        for k in ("token", "ref", "name"):
            if isinstance(ref.get(k), str):
                return resolve_colour(ref[k], palette, depth + 1)
    return None


def check_pairs(pairs: list[dict], palette: dict) -> tuple[list[dict], list[dict]]:
    """(every pair measured, the ones that fall short). Unknown roles are held to the body-text bar."""
    rows, short = [], []
    for i, p in enumerate(pairs):
        fg_ref = p.get("fg", p.get("text", p.get("foreground")))
        bg_ref = p.get("bg", p.get("background", p.get("surface")))
        role = str(p.get("role") or "body").lower()
        need = CONTRAST.get(role, CONTRAST["body"])
        # the brand module resolves each token once (fg_hex / bg_hex), so onboarding and this gate read the same
        # colour; the ratio is measured here all the same
        fg = parse_colour(p.get("fg_hex")) or resolve_colour(fg_ref, palette)
        bg = parse_colour(p.get("bg_hex")) or resolve_colour(bg_ref, palette)
        row = {"fg": fg_ref, "bg": bg_ref, "role": role, "needs": need}
        if fg is None or bg is None:
            which = [str(r) for r, c in ((fg_ref, fg), (bg_ref, bg)) if c is None]
            row["problem"] = f"{' and '.join(which)} isn't a colour or a palette token"
            short.append(row)
            rows.append(row)
            continue
        ratio = pair_ratio(fg, bg)
        row["ratio"] = round(ratio, 2)
        if ratio + 1e-9 < need:
            row["problem"] = f"{ratio:.2f}:1, below the {need:g}:1 {role} text needs"
            short.append(row)
        rows.append(row)
    return rows, short


def check_contrast(ctx):
    from slate.qa import brandinfo  # noqa: PLC0415

    pack, source = brandinfo.load(ctx.brand)
    if pack is None:
        return _sub("contrast", "UNCHECKED", f"the brand's colour pairs can't be read: {source}", [],
                    hint="Name the film's brand in film.json and onboard it (`slate brand onboard`).")
    pairs, palette, where = brand_pairs(pack)
    if not pairs:
        return _sub("contrast", "UNCHECKED", "the brand declares no text colour pairs, so there's nothing to measure",
                    [], hint="Add contrast_pairs to the brand pack ({fg, bg, role: body|large}, by palette token) "
                             "during onboarding.", source=where)
    rows, short = check_pairs(pairs, palette)
    details = {"pairs": rows, "source": where, "needs": CONTRAST}
    if short:
        s = short[0]
        verb = "falls" if len(short) == 1 else "fall"
        return _sub("contrast", "FAIL", f"{_plural(len(short), 'text colour pair')} {verb} short: {s['fg']} on "
                    f"{s['bg']} ({s['role']}) is {s.get('problem')}",
                    [f"{r['fg']} on {r['bg']} ({r['role']}): {r.get('problem')}" for r in short], **details)
    low = min(rows, key=lambda r: r["ratio"] / r["needs"])
    return _sub("contrast", "PASS", f"every text colour pair meets its bar ({len(rows)} pairs; the closest is "
                f"{low['fg']} on {low['bg']} at {low['ratio']:.2f}:1 for {low['role']} text)", [], **details)


# ---------------------------------------------------------------------------------------------------- captions
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[,.](\d{1,3}))?")
_ARROW = re.compile(r"^\s*(\S+)\s+-->\s+(\S+)")


def _secs(text: str) -> float | None:
    m = _TIME.fullmatch(text.strip())
    if not m:
        return None
    h, mi, s, ms = m.groups()
    frac = int((ms or "0").ljust(3, "0")) / 1000.0
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + frac


def _clean(line: str) -> str:
    line = re.sub(r"<[^>]*>", "", line)
    line = re.sub(r"\{\\[^}]*\}", "", line)
    return line.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


@dataclasses.dataclass
class Cue:
    start: float
    end: float
    lines: list[str]
    index: int

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def chars(self) -> int:
        """Characters read: every character, spaces and punctuation included, with a line break read as a space
        (the same count slate.qa's captions gate uses, so the two gates can't disagree about one caption)."""
        return len(" ".join(line.strip() for line in self.lines))

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def cps(self) -> float:
        return self.chars / self.duration if self.duration > 0 else math.inf


def parse_timed_text(text: str) -> list[Cue]:
    """Cues from SRT or WebVTT text (markup stripped, cue settings ignored, NOTE/STYLE/REGION blocks skipped)."""
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip("﻿\n "))
    for block in blocks:
        lines = [ln for ln in block.split("\n")]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        k = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if k is None:
            continue
        m = _ARROW.match(lines[k])
        if not m:
            continue
        a, b = _secs(m.group(1)), _secs(m.group(2))
        if a is None or b is None:
            continue
        body = [_clean(ln) for ln in lines[k + 1:]]
        body = [ln for ln in body if ln]
        cues.append(Cue(a, b, body, len(cues) + 1))
    return cues


def parse_caption_json(doc: Any, fps: float | None = None) -> list[Cue]:
    """Cues from a captions.json: a list (or {"cues": […]}) of {start, end, text, lines?} in seconds, or with
    frame_in/frame_out and the film's frame rate."""
    items = doc.get("cues") or doc.get("captions") or [] if isinstance(doc, dict) else doc
    cues = []
    for i, c in enumerate(items or []):
        if not isinstance(c, dict):
            continue
        start, end = _num(c.get("start", c.get("start_s"))), _num(c.get("end", c.get("end_s")))
        if (start is None or end is None) and fps and _num(c.get("frame_in")) is not None:
            start, end = float(c["frame_in"]) / fps, float(c["frame_out"]) / fps
        if start is None or end is None:
            continue
        lines = c.get("lines")
        if not (isinstance(lines, list) and all(isinstance(x, str) for x in lines) and lines):
            lines = str(c.get("text") or "").split("\n")
        cues.append(Cue(start, end, [_clean(x) for x in lines if _clean(x)], i + 1))
    return cues


def load_captions(path: Path, fps: float | None = None) -> list[Cue]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return parse_caption_json(jsonio.read(path), fps)
    return parse_timed_text(path.read_text(encoding="utf-8", errors="replace"))


def caption_limits(ctx) -> tuple[dict, str]:
    """The law's limits (17 characters a second, 2 lines of 42), tightened, never loosened, by the film's format at
    its aspect and by film.json's "captions": slate.qa's one caption rule (checks.text.caption_rules), so this check
    and the captions gate can't disagree about one cue."""
    from slate.qa.checks.text import caption_rules  # noqa: PLC0415

    rules, source = caption_rules(ctx)
    return {k: rules[k] for k in CAPTIONS}, source


def check_cues(cues: list[Cue], limits: dict) -> list[dict]:
    """Every rule a cue breaks: pace, line count, line length, or an end before its start."""
    out = []
    for c in cues:
        where = f"caption {c.index} ({c.start:.2f}–{c.end:.2f} s, “{c.text[:48]}”)"
        if c.duration <= 0:
            out.append({"cue": c.index, "start": c.start, "problem": f"{where} ends before it starts"})
            continue
        if c.cps > limits["max_cps"] + 1e-9:
            out.append({"cue": c.index, "start": c.start, "cps": round(c.cps, 2),
                        "problem": f"{where} asks for {c.cps:.1f} characters a second ({c.chars} in "
                                   f"{c.duration:.2f} s); the limit is {limits['max_cps']:g}"})
        if len(c.lines) > limits["max_lines"]:
            out.append({"cue": c.index, "start": c.start, "lines": len(c.lines),
                        "problem": f"{where} has {len(c.lines)} lines; the limit is {limits['max_lines']}"})
        for ln in c.lines:
            if len(ln) > limits["max_chars"]:
                out.append({"cue": c.index, "start": c.start, "line_chars": len(ln),
                            "problem": f"{where} has a {len(ln)}-character line; the limit is {limits['max_chars']}"})
                break
    return out


def caption_files(ctx, explicit: str | None = None) -> list[Path]:
    """The captions to check: an explicit file, else the film's structured captions and every delivered .srt/.vtt."""
    if explicit:
        p = Path(explicit).expanduser()
        return [p if p.is_absolute() or p.exists() else ctx.path(explicit)]
    from slate.qa.film import CAPTION_DIRS  # noqa: PLC0415

    found: list[Path] = []
    primary = ctx.captions_path()
    if primary is not None:
        found.append(primary)
    for d in CAPTION_DIRS:
        folder = ctx.path(d)
        if folder.is_dir():
            for p in sorted(folder.iterdir()):
                if p.suffix.lower() in (".srt", ".vtt") and not p.name.startswith(".") and p not in found:
                    found.append(p)
    return found


def check_captions(ctx, *, captions: str | None = None):
    files = caption_files(ctx, captions)
    voiced = bool(ctx.vo_words or ctx.vo_cues)
    if not files:
        if not voiced:
            return None  # no voice, no captions: nothing to check
        return _sub("captions", "UNCHECKED", "the film is voiced but has no captions yet", [],
                    hint="`slate voice captions` writes them; then run `slate qa access`.")
    limits, source = caption_limits(ctx)
    fps = float(ctx.fps) if ctx.fps else None
    problems: list[dict] = []
    seen: set[tuple] = set()
    counted = 0
    per_file = []
    fastest = 0.0
    for p in files:
        if not p.is_file():
            return _sub("captions", "UNCHECKED", f"{ctx.rel(p)} doesn't exist", [str(p)])
        try:
            cues = load_captions(p, fps)
        except (SlateError, OSError, UnicodeDecodeError) as exc:
            problems.append({"file": ctx.rel(p), "problem": f"{ctx.rel(p)} can't be read: {exc}"})
            continue
        if not cues:
            problems.append({"file": ctx.rel(p), "problem": f"{ctx.rel(p)} has no captions in it"})
            continue
        counted += len(cues)
        fastest = max(fastest, max((c.cps for c in cues if c.duration > 0), default=0.0))
        per_file.append({"file": ctx.rel(p), "cues": len(cues)})
        for prob in check_cues(cues, limits):
            key = (round(prob.get("start", 0.0), 2), prob["problem"].split(";")[0][-60:])
            if key in seen:
                continue
            seen.add(key)
            problems.append({**prob, "file": ctx.rel(p)})
    details = {"files": per_file, "limits": limits, "limits_from": source,
               "fastest_cps": round(fastest, 2) if fastest != math.inf else None}
    if problems:
        details["problems"] = problems[:60]
        details["problems_total"] = len(problems)
        return _sub("captions", "FAIL", f"{_plural(len(problems), 'caption problem')}: {problems[0]['problem']}",
                    [f"{p['file']}: {p['problem']}" for p in problems[:15]], **details)
    return _sub("captions", "PASS", f"{_plural(counted, 'caption')} within {limits['max_cps']:g} characters a second "
                f"and {limits['max_lines']} lines of {limits['max_chars']} (fastest {fastest:.1f} cps)", [], **details)


# ---------------------------------------------------------------------------------------------------- safe zones
def _format(ctx) -> dict | None:
    """The film's format data (slate.qa.film.read_format, read once per run): slate.formats.load at the film's aspect
    when it's installed, else the format file film.json records (`format_source`), else formats/<name>.json. None when
    there's none."""
    fmt_of = getattr(ctx, "format", None)
    return fmt_of() if callable(fmt_of) else _read_format(ctx.film)



def platform_key(name: str) -> str:
    """A channel's key from any spelling: slate.formats.channel_key (the library's aliases) when it knows the name,
    else the built-in spellings."""
    mod = _optional("slate.formats")
    fn = getattr(mod, "channel_key", None) if mod is not None else None
    if callable(fn):
        try:
            return str(fn(name))
        except Exception:  # noqa: BLE001 - a channel the library doesn't know: keep the name as written
            pass
    key = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return PLATFORM_ALIASES.get(key, PLATFORM_ALIASES.get(key.replace("_", ""), key))


def is_vertical(width: int, height: int) -> bool:
    """A 9:16 frame (to a pixel of rounding)."""
    return width > 0 and abs(height / width - 16 / 9) < 0.01


def _frac(v: Any, full: float, unit: str | None = None) -> float | None:
    """A margin as a fraction of the frame: px when the zone says so (or when it's more than 1), percent when it
    says so, else a fraction."""
    n = _num(v)
    if n is None:
        return None
    if unit in ("px", "pixels", "pixel"):
        return n / float(full)
    if unit in ("%", "percent", "pct"):
        return n / 100.0
    return n if n <= 1.0 else n / float(full)


def normalize_zone(z: Any, width: int, height: int, source: str) -> dict | None:
    """A platform's zone as {"safe": [x0, y0, x1, y1], "keep_out": [[…]…], "source"}, in fractions of the frame.

    Accepts margins ({top, bottom, left, right}, as fractions or px of the zone's own canvas or of the film), a safe
    rectangle ([x, y, w, h] or {x, y, width, height}) and keep-out rectangles ([x0, y0, x1, y1]).
    """
    if not isinstance(z, dict):
        return None
    canvas = z.get("canvas") if isinstance(z.get("canvas"), dict) else {}
    cw = _num(z.get("width")) or _num(canvas.get("width")) or float(width)
    ch = _num(z.get("height")) or _num(canvas.get("height")) or float(height)
    unit = str(z.get("unit") or z.get("units") or "").lower() or None
    m = z.get("margins") if isinstance(z.get("margins"), dict) else z
    unit = str(m.get("unit") or m.get("units") or "").lower() or unit
    safe = None
    if all(k in m for k in ("top", "bottom", "left", "right")):
        t, b = _frac(m["top"], ch, unit), _frac(m["bottom"], ch, unit)
        left, right = _frac(m["left"], cw, unit), _frac(m["right"], cw, unit)
        if None not in (t, b, left, right):
            safe = [left, t, 1.0 - right, 1.0 - b]
    rect = z.get("safe") or z.get("safe_rect") or z.get("rect") or z.get("safe_area")
    if safe is None and rect is not None:
        if isinstance(rect, dict):
            rect = [rect.get("x"), rect.get("y"), rect.get("width", rect.get("w")), rect.get("height", rect.get("h"))]
        if isinstance(rect, (list, tuple)) and len(rect) == 4 and all(_num(v) is not None for v in rect):
            x, y, w, h = (float(v) for v in rect)
            if max(x, y, w, h) > 1.0:
                x, w, y, h = x / cw, w / cw, y / ch, h / ch
            safe = [x, y, x + w, y + h]
    keep_out = []
    for r in z.get("keep_out") or z.get("unsafe") or z.get("avoid") or []:
        if isinstance(r, dict):
            r = [r.get("x0", r.get("x")), r.get("y0", r.get("y")), r.get("x1"), r.get("y1")]
        if isinstance(r, (list, tuple)) and len(r) == 4 and all(_num(v) is not None for v in r):
            x0, y0, x1, y1 = (float(v) for v in r)
            if max(x0, y0, x1, y1) > 1.0:
                x0, x1, y0, y1 = x0 / cw, x1 / cw, y0 / ch, y1 / ch
            keep_out.append([x0, y0, x1, y1])
    if safe is None and not keep_out:
        return None
    return {"safe": safe or [0.0, 0.0, 1.0, 1.0], "keep_out": keep_out, "source": z.get("source") or source}


def platforms_for(ctx, explicit: list[str] | None = None) -> tuple[list[str], str]:
    """Which platforms' zones apply: the ones asked for; else, on a 9:16 film, the channels its format makes it for
    at that aspect, else the three short-video platforms; else a format of the film's own that names platforms and
    their zones outright. A landscape film made from a library format has no platform zones to check (the plan's
    zones are the 9:16 ones)."""
    if explicit:
        return list(dict.fromkeys(platform_key(p) for p in explicit)), "asked for"
    fmt = _format(ctx)
    w, h = ctx.size or (0, 0)
    vertical = is_vertical(w, h)
    name = ctx.film.get("format")
    if isinstance(fmt, dict):
        aspects = fmt.get("aspects") if isinstance(fmt.get("aspects"), dict) else None
        declared: Any = None
        if vertical and aspects and isinstance(aspects.get(fmt.get("aspect")), dict):
            declared = aspects[fmt["aspect"]].get("channels")
        if not declared and (vertical or aspects is None):
            declared = fmt.get("platforms") or fmt.get("channels")
            if isinstance(declared, dict):
                declared = list(declared)
            zones = fmt.get("safe_zones")
            if not declared and isinstance(zones, dict):
                declared = list(zones)
        if isinstance(declared, list) and declared:
            return (list(dict.fromkeys(platform_key(p) for p in declared if isinstance(p, str))),
                    f"the {name} format" + (f" at {fmt.get('aspect')}" if aspects and fmt.get("aspect") else ""))
    if vertical:
        return list(BUILTIN_ZONES), "the three 9:16 platforms (built in)"
    return [], "none"


def lookup_zone(ctx, platform: str) -> tuple[dict | None, str]:
    """(zone, where from) for one platform on this film, or (None, "none") when the formats library says the channel
    keeps no part of this frame, or (None, "unknown") when nothing knows its zones. The library answers first
    (slate.formats.safe_zones at the film's aspect; its answer is final), then the format's own `safe_zones`, then the
    built-in 9:16 zones."""
    w, h = ctx.size or (0, 0)
    mod = _optional("slate.formats")
    fn = getattr(mod, "safe_zones", None) if mod is not None else None
    name = ctx.film.get("format")
    fmt = _format(ctx)
    if callable(fn) and (fmt is not None or name):
        arg = fmt if fmt is not None else name
        aspect = fmt.get("aspect") if isinstance(fmt, dict) else None
        where = f"slate.formats.safe_zones({name!r}, {platform!r}" + (f", aspect={aspect!r})" if aspect else ")")
        answered, z = False, None
        for kw in (({"aspect": aspect},) if aspect else ()) + ({},):
            try:
                z = fn(arg, platform, **kw)
                answered = True
                break
            except TypeError:
                continue  # a safe_zones() that takes no aspect
            except Exception:  # noqa: BLE001 - a channel it doesn't know: fall back below
                break
        if answered:
            norm = normalize_zone(z, w, h, where)
            if norm is not None:
                norm["source"] = (z.get("source") if isinstance(z, dict) and z.get("source") else where)
                return norm, where
            if isinstance(fmt, dict) and fmt.get("aspects"):
                return None, "none"  # the library's own format: no zone for this channel at this aspect
    own = fmt.get("safe_zones") if isinstance(fmt, dict) else None
    if isinstance(own, dict):
        z = own.get(platform)
        if z is None:
            z = next((v for k, v in own.items() if platform_key(k) == platform), None)
        norm = normalize_zone(z, w, h, f"the {name} format's safe_zones.{platform}")
        if norm:
            return norm, f"the {name} format"
    if platform in BUILTIN_ZONES and is_vertical(w, h):
        return normalize_zone(BUILTIN_ZONES[platform], w, h, BUILTIN_ZONES[platform]["source"]), "built in"
    return None, "unknown"


def zone_for(ctx, platform: str) -> dict | None:
    """A platform's zone on this film (see lookup_zone), or None."""
    return lookup_zone(ctx, platform)[0]


def box_from_ocr(box: Any) -> list[float] | None:
    """An OCR box [x, y, w, h] (fractions, origin bottom-left, as Vision gives it) as [x0, y0, x1, y1] from the top
    left."""
    if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(_num(v) is not None for v in box)):
        return None
    x, y, w, h = (float(v) for v in box)
    return [x, 1.0 - (y + h), x + w, 1.0 - y]


def zone_problems(box: list[float], zone: dict) -> list[str]:
    """How a text box breaks a zone: past a safe edge, or over a keep-out area."""
    x0, y0, x1, y1 = box
    sx0, sy0, sx1, sy1 = zone["safe"]
    t = ZONE_TOLERANCE
    out = []
    if y0 < sy0 - t:
        out.append(f"into the top {sy0 * 100:.1f}%")
    if y1 > sy1 + t:
        out.append(f"into the bottom {(1 - sy1) * 100:.1f}%")
    if x0 < sx0 - t:
        out.append(f"into the left {sx0 * 100:.1f}%")
    if x1 > sx1 + t:
        out.append(f"into the right {(1 - sx1) * 100:.1f}%")
    for k in zone["keep_out"]:
        if min(x1, k[2]) - max(x0, k[0]) > t and min(y1, k[3]) - max(y0, k[1]) > t:
            out.append(f"over a covered area {[round(v, 3) for v in k]}")
    return out


# ---------------------------------------------------------------------------------------------------- contact sheets
#: Where a film's contact sheets live. The engine's stills command tiles a sheet into <Comp>_sheet.png and writes its
#: index, <Comp>_sheet.json ({comp, file, rows, columns, cells: [{cell, frame, film_frame, shot, timecode}]}), beside
#: it: review/stills/ by default, and the review packet keeps its sheets under review/ too.
SHEET_DIRS = ("review", "renders/stills")
#: The engine burns a label ("Act2 · f120 · S04 · 00:00:04:00") into each cell's bottom-left corner: a 3.2%-high
#: line with its padding and margin, so its box ends within the bottom 8% of the cell. Text there that reads as the
#: label is review material, not the film's own text, and is left out of the safe-zone check.
STAMP_BAND = 0.14
STAMP_LEFT = 0.5
STAMP_MATCH = 0.75  # an OCR line is the label when at least 75% of its letters and digits match the label's in order
#: The widest gutter the geometry search tries (the engine uses 8 px). A sheet laid out with more says so in its index.
MAX_TILE_PADDING = 64


@dataclasses.dataclass
class Cell:
    """One frame read for text: its picture, where it sits in the film (when known), and the label the engine burned
    into it, if any (review material, never the film's own text)."""

    label: str
    image: Any
    t: float | None = None
    frame: int | None = None
    stamp: str | None = None


def tile_geometry(sheet_size: tuple[int, int], columns: int, rows: int, aspect: float | None = None,
                  hint: dict | None = None) -> dict | None:
    """Where the cells of a tiled contact sheet sit: {"cell": [w, h], "padding": p, "margin": m} in sheet pixels.

    ffmpeg's tile filter (what the engine's sheets use) puts `margin` round the grid and `padding` between cells, so
    width = 2·margin + columns·w + (columns − 1)·padding. When the index says, that's used. Otherwise the padding
    (the engine uses one number for both, 8 px unless asked) is the one up to MAX_TILE_PADDING that leaves whole
    cells of the film's shape, nearest 8 on a tie. None when nothing fits within 2% of the film's shape.
    """
    W, H = (int(v) for v in sheet_size)
    columns, rows = max(1, int(columns)), max(1, int(rows))
    if isinstance(hint, dict):
        cell = hint.get("cell") if isinstance(hint.get("cell"), (list, tuple)) else \
            [hint.get("cell_width"), hint.get("cell_height")]
        cw, ch = (_num(v) for v in (list(cell) + [None, None])[:2])
        if cw and ch:
            pad = _num(hint.get("padding")) or 0.0
            margin = _num(hint.get("margin"))
            return {"cell": [int(cw), int(ch)], "padding": int(pad), "margin": int(pad if margin is None else margin)}
    best = None
    for p in range(0, MAX_TILE_PADDING + 1):
        wn, hn = W - (columns + 1) * p, H - (rows + 1) * p
        if wn <= 0 or hn <= 0:
            break
        if wn % columns or hn % rows:
            continue
        cw, ch = wn // columns, hn // rows
        err = abs((cw / ch) / aspect - 1.0) if aspect else 0.0
        cand = (round(err, 4), abs(p - 8), p, cw, ch)
        if best is None or cand < best:
            best = cand
    if best is None or (aspect and best[0] > 0.02):
        return None
    return {"cell": [best[3], best[4]], "padding": best[2], "margin": best[2]}


def stamp_for(index: dict, cell: dict) -> str | None:
    """The label the engine burned into a cell ("<comp> · f<frame> · <shot> · <timecode>"), or None when the index
    says the sheet has no labels."""
    if index.get("labels") is False or cell.get("frame") is None:
        return None
    parts = [str(index.get("comp") or "Film"), f"f{cell['frame']}"]
    if cell.get("shot"):
        parts.append(str(cell["shot"]))
    if cell.get("timecode"):
        parts.append(str(cell["timecode"]))
    return " · ".join(parts)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


_FRAME_TOKEN = re.compile(r"(?<![a-z0-9])f\s*\d+(?![0-9])", re.I)
_TIMECODE = re.compile(r"\d{1,2}\s*[:;.]\s*\d{2}\s*[:;.]\s*\d{2}(?:\s*[:;.]\s*\d{2})?")


def is_stamp(text: str, box: list[float], stamp: str | None) -> bool:
    """Whether an OCR line is a contact-sheet cell's burned-in label, which is review material, not the film's text.

    It has to sit in the cell's bottom-left corner, where the engine draws the label, and read as the label: all of
    it part of the label and either shaped like one (a frame number "f120" or a timecode) or long enough not to be a
    coincidence; or, allowing for OCR misreads, shaped like a label with 75% of its letters and digits the label's,
    in order. Film text in that corner ("Film 2026", "Act 2") stays in the check."""
    if not stamp or box is None:
        return False
    x0, y0, _x1, _y1 = box
    if y0 < 1.0 - STAMP_BAND or x0 > STAMP_LEFT:
        return False
    t, s = _alnum(text), _alnum(stamp)
    if len(t) < 3:
        return False
    shaped = bool(_FRAME_TOKEN.search(text) or _TIMECODE.search(text))
    if t in s and (shaped or len(t) >= 8):
        return True
    if not shaped:
        return False
    import difflib  # noqa: PLC0415

    matched = sum(b.size for b in difflib.SequenceMatcher(None, t, s, autojunk=False).get_matching_blocks())
    return matched >= STAMP_MATCH * len(t)


def _images_in(folder: Path) -> list[Path]:
    from slate.qa.motion import IMAGE_EXTS  # noqa: PLC0415

    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."))


def sheet_cells(path: Path, *, film_size: tuple[int, int] | None = None, fps: float | None = None) -> list[Cell]:
    """The frames of a contact sheet, each with where it sits in the film and its burned-in label.

    `path` is an engine sheet (its PNG, or its index JSON) whose index gives rows, columns and cells; a sheet with a
    tiles index ({"tiles": [{"rect": [x, y, w, h], "t" or "frame": …}]}); a folder of frames; or one frame.
    """
    from PIL import Image  # noqa: PLC0415

    path = Path(path)
    if path.is_dir():
        out = []
        for p in _images_in(path):
            with Image.open(p) as im:
                out.append(Cell(p.name, im.convert("RGB")))
        return out
    index: Any = None
    image = path
    if path.suffix.lower() == ".json":
        index = jsonio.read(path)
        name = index.get("file") if isinstance(index, dict) else None
        image = path.parent / str(name or path.with_suffix(".png").name)
        if not image.is_file():
            raise UsageError(f"{path.name} indexes a contact sheet, but its image ({image.name}) isn't beside it.")
    else:
        side = path.with_suffix(".json")
        index = jsonio.read(side) if side.is_file() else None
    with Image.open(image) as im:
        sheet = im.convert("RGB")
    if not isinstance(index, dict):
        return [Cell(image.name, sheet)]
    tiles = index.get("tiles")
    if isinstance(tiles, list) and tiles:
        out = []
        for i, t in enumerate(tiles):
            r = t.get("rect") if isinstance(t, dict) else None
            if not (isinstance(r, (list, tuple)) and len(r) == 4 and all(_num(v) is not None for v in r)):
                continue
            x, y, w, h = (int(round(float(v))) for v in r)
            frame = t.get("film_frame", t.get("frame"))
            secs = _num(t.get("t"))
            if secs is None and fps and _num(frame) is not None:
                secs = float(frame) / fps
            label = f"{image.name} tile {i + 1}" + (f" (film frame {frame})" if frame is not None else
                                                    f" ({secs:.2f} s)" if secs is not None else "")
            out.append(Cell(label, sheet.crop((x, y, x + w, y + h)), secs,
                            int(frame) if _num(frame) is not None else None, t.get("stamp")))
        return out
    cells = index.get("cells")
    if isinstance(cells, list) and cells and _num(index.get("columns")) and _num(index.get("rows")):
        columns, rows = int(index["columns"]), int(index["rows"])
        aspect = film_size[0] / film_size[1] if film_size and film_size[1] else None
        geo = tile_geometry(sheet.size, columns, rows, aspect, index.get("geometry") or index)
        if geo is None:
            raise UsageError(f"The cells of {image.name} can't be located: its index doesn't give their size, and no "
                             f"padding leaves {columns}×{rows} cells of the film's shape in a {sheet.size[0]}×"
                             f"{sheet.size[1]} sheet.",
                             hint="Make the sheet again with the engine's stills command (it tiles the film's frames "
                                  "at the film's shape), or pass a folder of frames with --sheet.")
        (cw, ch), pad, margin = geo["cell"], geo["padding"], geo["margin"]
        out = []
        for k, c in enumerate(cells):
            if not isinstance(c, dict):
                continue
            i = int(c.get("cell", k))
            col, row = i % columns, i // columns
            x, y = margin + col * (cw + pad), margin + row * (ch + pad)
            if x + cw > sheet.size[0] or y + ch > sheet.size[1]:
                continue
            ff = c.get("film_frame")
            secs = float(ff) / fps if fps and _num(ff) is not None else None
            where = [f"film frame {ff}"] if ff is not None else [f"frame {c.get('frame')}"]
            if secs is not None:
                where.append(f"{secs:.2f} s")
            out.append(Cell(f"{image.name} cell {i + 1} ({', '.join(where)})", sheet.crop((x, y, x + cw, y + ch)),
                            secs, int(ff) if _num(ff) is not None else None, stamp_for(index, c)))
        return out
    return [Cell(image.name, sheet)]


def find_sheets(ctx) -> list[Path]:
    """The film's contact sheets: the index JSONs under review/ and renders/stills/ that describe a tiled sheet (rows,
    columns and cells, as the engine writes them) with its image beside it. When several versions of one
    composition's sheet exist, only the newest counts."""
    newest: dict[str, tuple[float, Path]] = {}
    for d in SHEET_DIRS:
        folder = ctx.path(d)
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*.json")):
            try:
                doc = jsonio.read(p)
            except SlateError:
                continue
            if not (isinstance(doc, dict) and isinstance(doc.get("cells"), list) and _num(doc.get("rows"))
                    and _num(doc.get("columns")) and (p.parent / str(doc.get("file") or "")).is_file()):
                continue
            comp = str(doc.get("comp") or p.stem)
            mtime = p.stat().st_mtime
            if comp not in newest or mtime > newest[comp][0]:
                newest[comp] = (mtime, p)
    return sorted(p for _m, p in newest.values())


def _has_picture(ctx) -> bool:
    """Whether the film has a picture the OCR sweep can read (a master, or a whole-act render)."""
    if ctx.masters():
        return True
    return any(Path(e["path"]).is_file() for e in ctx.current_renders().values())


def _read_cells(ctx, cells: list[Cell], sources: list[Path]) -> tuple[list[dict], dict]:
    """OCR each cell (cached against the sheets' hashes, so a quick run reuses it and never starts it)."""
    from slate.qa import ocr  # noqa: PLC0415
    from slate.qa.motion import cached_scan, source_digest  # noqa: PLC0415

    backend = ocr.backend()
    engine = dict(backend.engine)
    key = {"sources": [{"file": ctx.rel(f), "sha256": source_digest(ctx, Path(f))} for f in _sheet_files(sources)],
           "cells": [c.label for c in cells], "engine": engine, "version": __version__}

    def scan():
        return [backend.recognize(c.image) for c in cells]

    lines = cached_scan(ctx, "access", sources[0], key, scan, what="text", kind="sheet")
    samples = [{"label": c.label, "t": c.t, "frame": c.frame, "stamp": c.stamp, "lines": ln or []}
               for c, ln in zip(cells, lines)]
    return samples, engine


def _ocr_samples(ctx, sheet: str | None) -> tuple[list[dict], dict, list[str]]:
    """OCR'd text boxes, as [{label, t, frame, stamp, lines: [{text, conf, box}]}], the engine that read them, and
    the files read: an explicit sheet; else the shared sweep of the picture; else, with no picture yet, the film's
    contact sheets. Raises MissingPrerequisite (UNCHECKED) when there's nothing to read or OCR can't run."""
    from slate.qa import ocr  # noqa: PLC0415

    fps = float(ctx.fps) if ctx.fps else None
    if sheet:
        p = Path(sheet).expanduser()
        p = p if p.is_absolute() or p.exists() else ctx.path(sheet)
        if not p.exists():
            raise UsageError(f"There's no contact sheet at {p}.")
        cells = sheet_cells(p, film_size=ctx.size, fps=fps)
        if not cells:
            raise MissingPrerequisite(f"{ctx.rel(p)} has no frames in it to read.")
        samples, engine = _read_cells(ctx, cells, [p])
        return samples, {**engine, "source": f"contact sheet {ctx.rel(p)}"}, _sheet_files([p])
    if _has_picture(ctx):
        sweep = ocr.sweep(ctx)
        samples = [{"label": f"{s['t']:.2f} s (film frame {s['frame']})", "t": s["t"], "frame": s["frame"],
                    "stamp": None, "lines": s.get("lines") or []} for s in sweep.samples]
        extra = {"every_s": sweep.every, "source": f"the picture ({sweep.source}), every {sweep.every:g} s: every "
                 "frame of the half-second contact sheet and those between, at full size", "cache": sweep.cache}
        if sweep.missing_acts:
            extra["missing_acts"] = sweep.missing_acts
        return samples, {**sweep.engine, **extra}, list(sweep.files)
    sheets = find_sheets(ctx)
    if not sheets:
        raise MissingPrerequisite("There's nothing to read for text yet: no master, no act render and no contact "
                                  "sheet.", hint="Render the acts (`slate render`) or make the review packet's "
                                                 "contact sheet, then run `slate qa access` again.")
    cells: list[Cell] = []
    for s in sheets:
        cells += sheet_cells(s, film_size=ctx.size, fps=fps)
    samples, engine = _read_cells(ctx, cells, sheets)
    return samples, {**engine, "source": "the film's contact sheets: " + ", ".join(ctx.rel(s) for s in sheets)}, \
        _sheet_files(sheets)


def _sheet_files(paths: list[Path]) -> list[str]:
    """A contact sheet's files, image first and then its index (or a folder's frames): what the check read, for the
    gate's inputs and the OCR cache's key."""
    out: list[str] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += [str(x) for x in _images_in(p)]
            continue
        if p.suffix.lower() == ".json":
            doc = jsonio.read(p, default=None) if p.is_file() else None
            name = doc.get("file") if isinstance(doc, dict) else None
            pair = [p.parent / str(name or p.with_suffix(".png").name), p]
        else:
            pair = [p, p.with_suffix(".json")]
        out += [str(q) for q in pair if q.is_file() and str(q) not in out]
    return out


def check_safe_zones(ctx, *, sheet: str | None = None, platforms: list[str] | None = None, read: list | None = None):
    """Text inside the platforms' safe zones on a 9:16 film, by OCR'd text boxes. The contact sheets it reads are
    appended to `read` (the gate's inputs)."""
    read = read if read is not None else []
    size = ctx.size
    if not size:
        return _sub("safe_zones", "UNCHECKED", "film.json has no width and height, so the frame's shape is unknown")
    names, why = platforms_for(ctx, platforms)
    if not names:
        return None  # not 9:16 and no format zones: no platform safe zones apply
    zones = {}
    unknown = []
    no_zone = []
    for n in names:
        z, where = lookup_zone(ctx, n)
        if z is not None:
            zones[n] = z
        elif where == "none":
            no_zone.append(n)  # the channel draws nothing over this frame
        else:
            unknown.append(n)
    if not zones and not unknown:
        return None  # none of the film's channels keeps text out of any part of this frame
    if not zones:
        return _sub("safe_zones", "UNCHECKED", f"no safe zones are known for {', '.join(unknown)} at this frame shape",
                    [], hint="Add the platform's zones to the film's format (formats/<name>.json).", platforms=names)
    from slate.qa import ocr  # noqa: PLC0415

    ok, engine = ocr.available()
    if not ok:
        return _sub("safe_zones", "UNCHECKED", f"text can't be located without OCR: {engine.get('reason')}", [],
                    hint=engine.get("hint") or ocr.INSTALL_HINT, engine=engine, platforms=list(zones))
    try:
        samples, engine, files = _ocr_samples(ctx, sheet)
    except SlateError as exc:  # no picture yet, OCR can't run, or a contact sheet whose cells can't be located
        return _sub("safe_zones", "UNCHECKED", f"the frames couldn't be read for text: {exc.message}", [],
                    hint=exc.hint, platforms=list(zones))
    read += list(files)
    if not samples:
        return _sub("safe_zones", "UNCHECKED", "there were no frames to read for text", [], platforms=list(zones))
    violations = []
    texts = 0
    stamps = 0
    for s in samples:
        for line in s["lines"]:
            text = str(line.get("text") or "").strip()
            conf = _num(line.get("conf", line.get("confidence")))
            box = box_from_ocr(line.get("box"))
            if not text or box is None or not re.search(r"[A-Za-z0-9]", text):
                continue
            if conf is not None and conf < OCR_MIN_CONF:
                continue
            if is_stamp(text, box, s.get("stamp")):
                stamps += 1  # the contact sheet's own label, burned into the cell for review
                continue
            texts += 1
            for name, z in zones.items():
                where = zone_problems(box, z)
                if where:
                    violations.append({"platform": name, "at": s["label"], "t": s["t"], "text": text[:60],
                                       "box": [round(v, 3) for v in box], "where": where})
    details = {"platforms": {n: {"safe": [round(v, 4) for v in z["safe"]], "keep_out": z["keep_out"],
                                 "source": z["source"]} for n, z in zones.items()},
               "platforms_from": why, "frames_read": len(samples), "text_boxes": texts, "engine": engine,
               "files": files, **({"sheet_labels_left_out": stamps} if stamps else {})}
    if unknown:
        details["no_zones_for"] = unknown
    if no_zone:
        details["no_zone_at_this_aspect"] = no_zone
    if violations:
        details["violations"] = violations[:60]
        details["violations_total"] = len(violations)
        v = violations[0]
        verb = "sits" if len(violations) == 1 else "sit"
        return _sub("safe_zones", "FAIL", f"{_plural(len(violations), 'text box', 'text boxes')} {verb} where a "
                    f"platform covers the frame: “{v['text']}” at {v['at']} reaches {v['where'][0]} "
                    f"({v['platform']})",
                    [f"{x['platform']}: “{x['text']}” at {x['at']} reaches {', '.join(x['where'])}"
                     for x in violations[:15]], **details)
    if unknown:
        return _sub("safe_zones", "UNCHECKED", f"text stays inside {', '.join(zones)}'s safe zones, but no zones are "
                    f"known for {', '.join(unknown)}", [], **details)
    missing = engine.get("missing_acts") if isinstance(engine, dict) else None
    if missing:
        return _sub("safe_zones", "UNCHECKED", f"text stays inside the safe zones where there's picture, but act"
                    f"{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} "
                    f"{'have' if len(missing) > 1 else 'has'} no render to read yet", [], **details)
    return _sub("safe_zones", "PASS", f"every text box stays inside the safe zones of {', '.join(zones)} "
                f"({texts} boxes in {len(samples)} frames)", [], **details)


# ---------------------------------------------------------------------------------------------------- title-safe
TITLE_SAFE = 0.90  # the middle 90% of the frame: 96 px from the sides and 54 px from top and bottom at 1920 × 1080
ACTION_SAFE = 0.93


def safe_area(fraction: float) -> list[float]:
    m = (1.0 - fraction) / 2.0
    return [m, m, 1.0 - m, 1.0 - m]


def outside(box: list[float], area: list[float]) -> list[str]:
    """Which edges of a safe area a text box [x0, y0, x1, y1] crosses (fractions of the frame, top-left origin)."""
    x0, y0, x1, y1 = box
    ax0, ay0, ax1, ay1 = area
    t = ZONE_TOLERANCE
    out = []
    if y0 < ay0 - t:
        out.append("top")
    if y1 > ay1 + t:
        out.append("bottom")
    if x0 < ax0 - t:
        out.append("left")
    if x1 > ax1 + t:
        out.append("right")
    return out


def _text_lines(samples: list[dict]):
    """(sample, text, box) for every OCR'd line that counts: readable, confident, and not a contact sheet's label."""
    for s in samples:
        for line in s["lines"]:
            text = str(line.get("text") or "").strip()
            conf = _num(line.get("conf", line.get("confidence")))
            box = box_from_ocr(line.get("box"))
            if not text or box is None or not re.search(r"[A-Za-z0-9]", text):
                continue
            if conf is not None and conf < OCR_MIN_CONF:
                continue
            if is_stamp(text, box, s.get("stamp")):
                continue
            yield s, text, box


def _read_text_boxes(ctx, sheet: str | None, name: str, read: list):
    """(samples, engine, files) for a text check, or an UNCHECKED sub-check saying why there are none."""
    from slate.qa import ocr  # noqa: PLC0415

    ok, engine = ocr.available()
    if not ok:
        return None, _sub(name, "UNCHECKED", f"text can't be located without OCR: {engine.get('reason')}", [],
                          hint=engine.get("hint") or ocr.INSTALL_HINT, engine=engine)
    try:
        samples, engine, files = _ocr_samples(ctx, sheet)
    except SlateError as exc:
        return None, _sub(name, "UNCHECKED", f"the frames couldn't be read for text: {exc.message}", [],
                          hint=exc.hint)
    read += list(files)
    if not samples:
        return None, _sub(name, "UNCHECKED", "there were no frames to read for text", [])
    return (samples, engine, files), None


def check_title_safe(ctx, *, sheet: str | None = None, read: list | None = None):
    """Every OCR'd text box inside title-safe (the middle 90% of the frame); outside action-safe (93%) said too."""
    read = read if read is not None else []
    if not ctx.size:
        return _sub("title_safe", "UNCHECKED", "film.json has no width and height, so the frame's shape is unknown")
    got, unchecked = _read_text_boxes(ctx, sheet, "title_safe", read)
    if unchecked is not None:
        return unchecked
    samples, engine, files = got
    title, action = safe_area(TITLE_SAFE), safe_area(ACTION_SAFE)
    w, h = ctx.size
    violations, texts = [], 0
    for s, text, box in _text_lines(samples):
        texts += 1
        where = outside(box, title)
        if where:
            past = outside(box, action)
            violations.append({"at": s["label"], "t": s["t"], "text": text[:60], "box": [round(v, 4) for v in box],
                               "where": where, "outside_action_safe": past})
    details = {"title_safe_px": [round(title[0] * w), round(title[1] * h)], "frames_read": len(samples),
               "text_boxes": texts, "engine": engine, "files": files}
    if violations:
        v = violations[0]
        lines = [f"“{x['text']}” at {x['at']} crosses title-safe at the {', '.join(x['where'])}"
                 + (f" (and action-safe at the {', '.join(x['outside_action_safe'])})" if x["outside_action_safe"]
                    else "") for x in violations[:15]]
        return _sub("title_safe", "FAIL", f"{_plural(len(violations), 'text box', 'text boxes')} outside title-safe "
                    f"(the middle {TITLE_SAFE:.0%}: {details['title_safe_px'][0]} px from the sides, "
                    f"{details['title_safe_px'][1]} px from top and bottom): “{v['text']}” at {v['at']}", lines,
                    violations=violations[:60], violations_total=len(violations), **details)
    return _sub("title_safe", "PASS", f"every text box sits inside title-safe ({texts} boxes in {len(samples)} "
                "frames)", [], **details)


# ---------------------------------------------------------------------------------------------------- type size
BODY_PX = 28.0  # body copy at 1080 lines…
BODY_SHARE = 7 / 270  # …which is this share of the frame's short side (scaled to any film)
REFERENCE_LINES = BODY_PX / BODY_SHARE  # the line count the 28 px is stated at
CAP_EM = 0.62  # the lowest common cap height, as a share of the type size (sans faces run about 0.62–0.73)
BODY_WORDS = 4  # a line of this many words or more, not set in capitals, is body copy
_MEASURABLE = re.compile(r"[A-Z0-9bdfhklt]")  # a capital, a figure or an ascender reaches the cap line


def cap_height(gray) -> float | None:
    """How tall a line's capitals (or ascenders) stand, in px, from a crop of it: from the cap line to the baseline.
    Column by column: the baseline is where most columns' ink ends (descenders are a minority of columns, so g, p, q
    and y don't make the line taller), and the cap line is the highest ink a few columns agree on (one stray pixel
    doesn't count). None when the crop has no ink."""
    np = _np()
    g = np.asarray(gray, dtype=float)
    if g.ndim != 2 or min(g.shape) < 3:
        return None
    border = np.concatenate([g[0], g[-1], g[:, 0], g[:, -1]])
    diff = np.abs(g - float(np.median(border)))
    peak = float(diff.max())
    if peak < 12:
        return None
    ink = diff > max(12.0, 0.4 * peak)
    cols = np.flatnonzero(ink.any(axis=0))
    if len(cols) < 3:
        return None
    h = ink.shape[0]
    bottoms = h - 1 - np.argmax(ink[::-1, cols], axis=0)
    tops = np.sort(np.argmax(ink[:, cols], axis=0))
    agree = max(2, int(round(0.01 * len(cols))))
    return float(np.median(bottoms) - tops[min(len(tops) - 1, agree - 1)] + 1)


def _not_for_reading(ctx) -> set[str]:
    try:
        from slate.qa.checks.text import reading_declarations  # noqa: PLC0415

        decls, _problems = reading_declarations(ctx)
    except Exception:  # noqa: BLE001 - a broken declaration is the read-time gate's to report
        return set()
    return {sid for sid, d in decls.items() if d.get("not_for_reading")}


def check_type_size(ctx, *, sheet: str | None = None, read: list | None = None):
    """Body copy at least 28 px at 1080 lines, measured on the picture inside each line's OCR box."""
    read = read if read is not None else []
    if sheet:
        return None  # a contact sheet's thumbnails are scaled: type is measured on the picture itself
    if not ctx.size:
        return _sub("type_size", "UNCHECKED", "film.json has no width and height, so type can't be measured")
    got, unchecked = _read_text_boxes(ctx, None, "type_size", read)
    if unchecked is not None:
        return unchecked
    samples, engine, files = got
    from slate.qa import media, ocr  # noqa: PLC0415

    try:
        _source, parts, _missing = ocr._sources(ctx)  # noqa: SLF001 - the same picture the sweep read
    except SlateError as exc:
        return _sub("type_size", "UNCHECKED", f"the picture couldn't be found: {exc.message}", [], hint=exc.hint)
    skip = _not_for_reading(ctx)
    w, h = ctx.size
    floor = BODY_SHARE * CAP_EM * min(w, h)
    wanted: dict[tuple, tuple] = {}
    for s, text, box in _text_lines(samples):
        words = text.split()
        if len(words) < BODY_WORDS or text.upper() == text or not _MEASURABLE.search(text):
            continue
        shot = ctx.shot_of_frame(int(s["frame"])) if s.get("frame") is not None else None
        if shot in skip:
            continue
        key = (re.sub(r"\W+", "", text.lower())[:40], tuple(round(v, 2) for v in box))
        wanted.setdefault(key, (s, text, box, shot))
    measured, small = [], []
    by_part: dict[int, list] = {}
    for item in list(wanted.values())[:400]:
        frame = int(item[0]["frame"])
        k = next((i for i, (_p, f0, n) in enumerate(parts) if f0 <= frame < f0 + n), None)
        if k is not None:
            by_part.setdefault(k, []).append(item)
    for k, items in by_part.items():
        path, f0, _n = parts[k]
        frames = media.read_frames(path, [int(it[0]["frame"]) - f0 for it in items], pix_fmt="gray")
        for s, text, box, shot in items:
            arr = frames.get(int(s["frame"]) - f0)
            if arr is None:
                continue
            fh, fw = arr.shape[:2]
            x0, y0, x1, y1 = box
            pad = 0.15 * (y1 - y0)
            crop = arr[max(0, int((y0 - pad) * fh)):min(fh, int(math.ceil((y1 + pad) * fh))),
                       max(0, int(x0 * fw)):min(fw, int(math.ceil(x1 * fw)))]
            cap = cap_height(crop) if crop.size else None
            if cap is None:
                continue
            cap *= h / fh  # in the film's own pixels
            row = {"text": text[:60], "at": s["label"], "shot": shot, "cap_px": round(cap, 1),
                   "type_px_at_1080": round(cap / CAP_EM * REFERENCE_LINES / min(w, h), 1)}
            measured.append(row)
            if cap < floor:
                small.append(row)
    details = {"floor_cap_px": round(floor, 1), "measured": len(measured), "engine": engine, "files": files,
               "rule": f"body copy (≥ {BODY_WORDS} words, not in capitals) ≥ {BODY_PX:g} px at 1080 lines"}
    if small:
        smallest = min(small, key=lambda r: r["cap_px"])
        return _sub("type_size", "FAIL", f"{_plural(len(small), 'line')} of body copy under {BODY_PX:g} px at 1080 "
                    f"lines: “{smallest['text']}” at {smallest['at']} stands about {smallest['type_px_at_1080']:g} px",
                    [f"“{r['text']}” at {r['at']}: capitals {r['cap_px']:g} px (about {r['type_px_at_1080']:g} px "
                     f"type at 1080 lines)" for r in small[:15]], small=small[:40], **details)
    if not measured:
        return _sub("type_size", "PASS", "no body copy to measure (labels and titles aren't body copy)", [], **details)
    lowest = min(measured, key=lambda r: r["cap_px"])
    return _sub("type_size", "PASS", f"all {_plural(len(measured), 'line')} of body copy stand at {BODY_PX:g} px or "
                f"more at 1080 lines (smallest about {lowest['type_px_at_1080']:g} px)", [], **details)


# ---------------------------------------------------------------------------------------------------- the gate
SUBCHECKS = ("flashes", "contrast", "captions", "safe_zones", "title_safe", "type_size")


def evaluate(film_dir: Path, *, render: str | None = None, render_act: int | None = None,
             render_from: int | None = None, captions: str | None = None, sheet: str | None = None,
             platforms: list[str] | None = None, only: Iterable[str] | None = None) -> GateResult:
    """Run the access sub-checks on a film (every one, or `only` these) and combine them into the one `access` gate
    result. A narrowed run (some sub-checks, or a file given in place of the film's own) carries an UNCHECKED scope
    sub-check, so it can't pass the gate."""
    from slate.qa.checks import combine  # noqa: PLC0415
    from slate.qa.film import context  # noqa: PLC0415
    from slate.qa.motion import lifted_review_items, scope_sub  # noqa: PLC0415

    ctx = context(film_dir)
    _ = ctx.film
    wanted = set(only) if only else None
    if wanted is not None and not wanted <= set(SUBCHECKS):
        bad = ", ".join(sorted(wanted - set(SUBCHECKS)))
        raise UsageError(f"The access gate has no {bad} check.", hint="Its checks: " + ", ".join(SUBCHECKS) + ".")
    read: list[Any] = []
    runs: list[tuple[str, Callable]] = [
        ("flashes", lambda: check_flashes(ctx, render=render, render_act=render_act, render_from=render_from,
                                          read=read)),
        ("contrast", lambda: check_contrast(ctx)),
        ("captions", lambda: check_captions(ctx, captions=captions)),
        ("safe_zones", lambda: check_safe_zones(ctx, sheet=sheet, platforms=platforms, read=read)),
        ("title_safe", lambda: check_title_safe(ctx, sheet=sheet, read=read)),
        ("type_size", lambda: check_type_size(ctx, sheet=sheet, read=read)),
    ]
    subs = []
    for name, run in runs:
        if wanted is not None and name not in wanted:
            continue
        s = run()
        if s is not None:
            subs.append(s)
    narrowed = []
    runs_it = {n: wanted is None or n in wanted for n in SUBCHECKS}
    if wanted is not None and wanted != set(SUBCHECKS):
        narrowed.append(f"only {', '.join(s for s in SUBCHECKS if s in wanted)} (--only)")
    if render and runs_it["flashes"]:
        narrowed.append(f"the flashes of {render} instead of the film's picture (--render)")
    if captions and runs_it["captions"]:
        narrowed.append(f"the captions in {captions} instead of the film's own (--captions)")
    if sheet and (runs_it["safe_zones"] or runs_it["title_safe"]):
        narrowed.append(f"the text of {sheet} instead of the film's picture (--sheet)")
    if platforms and runs_it["safe_zones"]:
        default, _why = platforms_for(ctx, None)
        if {platform_key(x) for x in platforms} != set(default):
            narrowed.append(f"the zones of {', '.join(platform_key(x) for x in platforms)} (--platform)")
    if narrowed:
        subs.append(scope_sub(GATE, narrowed))
    paths: list[Any] = ["film.json", "timing/shots.json", "renders/registry.json"]
    paths += [str(p) for p in ctx.masters()]
    paths += [str(p) for p in caption_files(ctx, captions)]
    paths += [str(p) for p in read]
    for extra in (render, sheet):
        if extra and not read:
            paths.append(extra)
    from slate.qa import brandinfo  # noqa: PLC0415

    folder = brandinfo.find(ctx.brand) if ctx.brand else None
    if folder is not None:
        paths.append(str(folder / "brand.json"))
    from slate.qa import media  # noqa: PLC0415

    engine = gates.engine("slate qa access", __version__,
                          flashes="WCAG 2.3.1 general and red flash thresholds (2.2 definitions)",
                          contrast="WCAG relative luminance",
                          decoder=f"ffmpeg {media.version('ffmpeg') or 'not installed'}")
    result = combine(GATE, subs, engine=engine, inputs=ctx.inputs(dict.fromkeys(str(p) for p in paths)),
                     details={"only": sorted(wanted) if wanted else None,
                              **({"narrowed": narrowed} if narrowed else {}), **lifted_review_items(subs)},
                     empty_summary="nothing to check yet")
    if result.status == "PASS":
        result.summary = "accessible: " + "; ".join(c["summary"] for c in result.details.get("checks", []))
    return result


def gate(film_dir: Path) -> GateResult:
    """Accessibility: at most 3 flashes a second (WCAG 2.3.1 general and red flashes), the brand's text colour pairs
    at 4.5:1 (body) and 3:1 (large), captions at 17 characters a second and 2 lines of 42, and on 9:16 films text
    inside the platforms' safe zones (by OCR; UNCHECKED without it).

    The film folder in, one GateResult out (slate.qa runs it with every other gate; `slate qa access` alone). Inside a
    `slate.qa.motion.run_options(...)` block it takes that run's options (--only, --render, --captions, --sheet,
    --platform)."""
    from slate.qa.motion import current_options  # noqa: PLC0415

    opts = current_options()
    only = [o for o in opts.get("only") or [] if o in SUBCHECKS] or None
    acts = opts.get("acts")
    return evaluate(Path(film_dir), render=opts.get("render"), render_act=acts[0] if acts else None,
                    render_from=opts.get("render_from"), captions=opts.get("captions"), sheet=opts.get("sheet"),
                    platforms=opts.get("platforms"), only=only)


GATES: dict[str, Callable[[Path], GateResult]] = {GATE: gate}
#: Gates here that read the whole picture (the flash scan decodes it; the safe zones read it through OCR). Under
#: `slate qa --quick` they reuse their cached scans and never start one.
SLOW = (GATE,)


# ---------------------------------------------------------------------------------------------------- CLI
HELP = "flashes, text contrast, caption pace and 9:16 safe zones"
DESCRIPTION = """Check the film's accessibility: at most 3 flashes a second (WCAG 2.3.1's general and red flash
thresholds, on the master or each act's render), the brand's text colour pairs (4.5:1 body, 3:1 large), captions
(at most 17 characters a second and 2 lines of 42), and on 9:16 films the platform safe zones (Instagram Reels,
TikTok, YouTube Shorts, or the format's own), located by Vision OCR on the picture or the film's contact sheet.
Without OCR the safe zones are UNCHECKED. A run narrowed with --only, --render, --captions, --sheet or --platform
reports what it finds but can't pass the gate."""


def _cmd(args: argparse.Namespace) -> int:
    from slate.qa.motion import run_command  # noqa: PLC0415

    return run_command(GATE, args)


def register(sub) -> None:
    """Add `access` to a subparsers object (a stage-style `qa` group: `slate qa access`). qa-core's `slate qa` runs
    the same gate by name and takes these flags through slate.qa.motion.add_qa_options and qa_options."""
    from slate.cli import add_command  # noqa: PLC0415
    from slate.qa.motion import add_qa_options  # noqa: PLC0415

    p = add_command(sub, "access", _cmd, help=HELP, description=DESCRIPTION)
    add_qa_options(p, gates=(GATE,))
