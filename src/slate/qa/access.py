"""The access gate: flashes, text contrast, caption pace and platform safe zones.

`slate qa access` (and `slate qa`, which runs every gate) checks four things as sub-checks of one `access` gate:

- flashes     at most 3 flashes in any one second, by WCAG 2.3.1's general-flash and red-flash thresholds, measured
              on the picture (the master when there is one, else each act's render);
- contrast    every text colour pair the brand declares (slate.brand.pack.contrast_pairs) meets 4.5:1 for body text
              and 3:1 for large text, by WCAG's relative luminance;
- captions    at most 17 characters a second, and at most 2 lines of 42 characters each;
- safe_zones  on a 9:16 film, no text sits where Instagram Reels, TikTok or YouTube Shorts draw their own buttons and
              captions (the format's zones when it has them, else built-in ones), found by OCR on the picture at the
              contact sheet's frames and those between. UNCHECKED when Vision OCR can't run here.

A sub-check that doesn't apply is left out rather than passed: a 16:9 film has no 9:16 safe zones, and a film with
no voice has no captions. One that applies but can't run yet (no render, no OCR, no brand pairs) is UNCHECKED, with
the step that fixes it.

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

#: Built-in 9:16 safe zones, as fractions of the frame, for when the film's format doesn't carry its own
#: (slate.formats.safe_zones wins whenever it answers). Keep text inside: out of the top, bottom and side bands the
#: platforms cover with their own buttons, names and captions. Checked 2026-09-30.
BUILTIN_ZONES: dict[str, dict] = {
    "instagram_reels": {
        "top": 0.14, "bottom": 0.35, "left": 0.06, "right": 0.06,
        "source": "Meta's Reels guidance: keep the top 14%, the bottom 35% and 6% at each side free of text and "
                  "logos (adnova.ai/blogs/meta-ad-safe-zones-guide; hopperhq.com/blog/instagram-reel-size), "
                  "verified 2026-09-30",
    },
    "tiktok": {
        # 160 px at the top, 480 at the bottom and 120 at each side of a 9:16 frame 1,920 tall and 1,080 wide
        "top": 0.0833, "bottom": 0.25, "left": 0.1111, "right": 0.1111,
        "source": "TikTok's in-feed overlays as the common guides measure them: profile bar on top, caption and CTA "
                  "at the bottom, the button column at the right (houseofmarketers.com guide to safe zones; "
                  "poster.ly TikTok safe zone checker), verified 2026-09-30",
    },
    "youtube_shorts": {
        "top": 0.125, "bottom": 0.198, "left": 0.056, "right": 0.186,
        "source": "YouTube Shorts overlays as the common guides measure them: title and channel at the bottom, the "
                  "button column at the right (reap.video short-form safe zones; poster.ly Shorts safe zone "
                  "checker), verified 2026-09-30",
    },
}
PLATFORM_ALIASES = {
    "reels": "instagram_reels", "instagram": "instagram_reels", "ig": "instagram_reels", "ig_reels": "instagram_reels",
    "instagramreels": "instagram_reels", "tik_tok": "tiktok", "shorts": "youtube_shorts", "youtube": "youtube_shorts",
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


def check_flashes(ctx, *, render: str | None = None, render_act: int | None = None, render_from: int | None = None):
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
        if path.is_file() and not media.have("ffmpeg"):
            return _sub("flashes", "UNCHECKED", "ffmpeg isn't installed, so the picture can't be decoded", [],
                        hint="Install it with Homebrew (`brew install ffmpeg`), or run `slate doctor --install`.")
        w, h = size if size else _source_size(path)
        try:
            res = scan_flashes(read_rgb(path, flash_size(w, h), count=count), fps, first_frame=first)
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
        palette = pack.get("palette") if isinstance(pack.get("palette"), dict) else {}
    tokens = palette.get("tokens") if isinstance(palette.get("tokens"), dict) else palette
    if pairs is None:
        return None, tokens, source
    return [p for p in pairs if isinstance(p, dict)], tokens, source


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
        fg, bg = resolve_colour(fg_ref, palette), resolve_colour(bg_ref, palette)
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
    """The law's limits, tightened (never loosened) by the film's format when it sets caption defaults."""
    limits = dict(CAPTIONS)
    source = "the law (17 characters a second, 2 lines of 42)"
    fmt = _format(ctx)
    cap = fmt.get("captions") if isinstance(fmt, dict) else None
    if isinstance(cap, dict):
        pairs = {"max_cps": ("max_cps", "cps", "max_chars_per_second"), "max_lines": ("max_lines", "lines"),
                 "max_chars": ("max_chars", "max_chars_per_line", "max_line_chars", "line_length", "chars_per_line")}
        tightened = []
        for key, names in pairs.items():
            for n in names:
                v = _num(cap.get(n))
                if v is not None and v < limits[key]:
                    limits[key] = int(v) if key != "max_cps" else v
                    tightened.append(f"{key} {v:g}")
                    break
        if tightened:
            source = f"the law, tightened by the {ctx.film.get('format')} format ({', '.join(tightened)})"
    return limits, source


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
    name = ctx.film.get("format")
    mod = _optional("slate.formats")
    if not name or mod is None or not hasattr(mod, "load"):
        return None
    try:
        fmt = mod.load(name)
    except Exception:  # noqa: BLE001 - an unknown format just means no format data
        return None
    return fmt if isinstance(fmt, dict) else None


def platform_key(name: str) -> str:
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
    """Which platforms' zones apply: the ones asked for, else the format's, else (for 9:16) the three short-video
    platforms."""
    if explicit:
        return [platform_key(p) for p in explicit], "asked for"
    fmt = _format(ctx)
    if isinstance(fmt, dict):
        declared = fmt.get("platforms") or fmt.get("channels")
        if isinstance(declared, dict):
            declared = list(declared)
        zones = fmt.get("safe_zones")
        if not declared and isinstance(zones, dict):
            declared = list(zones)
        if isinstance(declared, list) and declared:
            return [platform_key(p) for p in declared if isinstance(p, str)], f"the {ctx.film.get('format')} format"
    w, h = ctx.size or (0, 0)
    if is_vertical(w, h):
        return list(BUILTIN_ZONES), "the three 9:16 platforms (built in)"
    return [], "none"


def zone_for(ctx, platform: str) -> dict | None:
    """A platform's zone: slate.formats.safe_zones when it answers, else the built-in one, else None."""
    w, h = ctx.size or (0, 0)
    mod = _optional("slate.formats")
    fn = getattr(mod, "safe_zones", None) if mod is not None else None
    fmt_name = ctx.film.get("format")
    if callable(fn) and fmt_name:
        for arg in (fmt_name, _format(ctx)):
            if arg is None:
                continue
            try:
                z = fn(arg, platform)
            except Exception:  # noqa: BLE001 - this format/platform has no zones; try the next form, then built-in
                continue
            norm = normalize_zone(z, w, h, f"slate.formats.safe_zones({fmt_name!r}, {platform!r})")
            if norm:
                return norm
    if platform in BUILTIN_ZONES and is_vertical(w, h):
        return normalize_zone(BUILTIN_ZONES[platform], w, h, BUILTIN_ZONES[platform]["source"])
    return None


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


def sheet_images(path: Path) -> list[tuple[str, Any]]:
    """(label, PIL image) for an explicit contact sheet: a folder of frames, a single frame, or a sheet image with a
    sidecar JSON listing its tiles ({"tiles": [{"rect": [x, y, w, h], "t" or "frame": …}]})."""
    from PIL import Image  # noqa: PLC0415

    from slate.qa.motion import IMAGE_EXTS  # noqa: PLC0415

    path = Path(path)
    if path.is_dir():
        out = []
        for p in sorted(path.iterdir()):
            if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."):
                with Image.open(p) as im:
                    out.append((p.name, im.convert("RGB")))
        return out
    with Image.open(path) as im:
        sheet = im.convert("RGB")
    manifest = path.with_suffix(".json")
    if manifest.is_file():
        doc = jsonio.read(manifest)
        tiles = doc.get("tiles") if isinstance(doc, dict) else None
        if isinstance(tiles, list) and tiles:
            out = []
            for i, t in enumerate(tiles):
                r = t.get("rect") if isinstance(t, dict) else None
                if not (isinstance(r, (list, tuple)) and len(r) == 4):
                    continue
                x, y, w, h = (int(round(float(v))) for v in r)
                label = f"{path.name} tile {i + 1}" + (f" ({t['t']} s)" if "t" in t else
                                                       f" (frame {t['frame']})" if "frame" in t else "")
                out.append((label, sheet.crop((x, y, x + w, y + h))))
            return out
    return [(path.name, sheet)]


def _ocr_samples(ctx, sheet: str | None) -> tuple[list[dict], dict, list[str]]:
    """OCR'd text boxes, as [{label, t, boxes: [{text, conf, box}]}], the engine that read them, and the files."""
    from slate.qa import ocr  # noqa: PLC0415

    if sheet:
        p = Path(sheet).expanduser()
        p = p if p.is_absolute() or p.exists() else ctx.path(sheet)
        if not p.exists():
            raise UsageError(f"There's no contact sheet at {p}.")
        backend = ocr.backend()
        samples = []
        for label, img in sheet_images(p):
            samples.append({"label": label, "t": None, "lines": backend.recognize(img)})
        return samples, dict(backend.engine), [str(p)]
    sweep = ocr.sweep(ctx)
    samples = [{"label": f"{s['t']:.2f} s (film frame {s['frame']})", "t": s["t"], "lines": s.get("lines") or []}
               for s in sweep.samples]
    extra = {"every_s": sweep.every, "source": sweep.source, "cache": sweep.cache}
    if sweep.missing_acts:
        extra["missing_acts"] = sweep.missing_acts
    return samples, {**sweep.engine, **extra}, list(sweep.files)


def check_safe_zones(ctx, *, sheet: str | None = None, platforms: list[str] | None = None):
    size = ctx.size
    if not size:
        return _sub("safe_zones", "UNCHECKED", "film.json has no width and height, so the frame's shape is unknown")
    names, why = platforms_for(ctx, platforms)
    if not names:
        return None  # not 9:16 and no format zones: no platform safe zones apply
    zones = {}
    unknown = []
    for n in names:
        z = zone_for(ctx, n)
        if z is None:
            unknown.append(n)
        else:
            zones[n] = z
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
    except MissingPrerequisite as exc:
        return _sub("safe_zones", "UNCHECKED", f"the frames couldn't be read for text: {exc.message}", [],
                    hint=exc.hint, platforms=list(zones))
    if not samples:
        return _sub("safe_zones", "UNCHECKED", "there were no frames to read for text", [], platforms=list(zones))
    violations = []
    texts = 0
    for s in samples:
        for line in s["lines"]:
            text = str(line.get("text") or "").strip()
            conf = _num(line.get("conf", line.get("confidence")))
            box = box_from_ocr(line.get("box"))
            if not text or box is None or not re.search(r"[A-Za-z0-9]", text):
                continue
            if conf is not None and conf < OCR_MIN_CONF:
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
               "files": files}
    if unknown:
        details["no_zones_for"] = unknown
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


# ---------------------------------------------------------------------------------------------------- the gate
def evaluate(film_dir: Path, *, render: str | None = None, render_act: int | None = None,
             render_from: int | None = None, captions: str | None = None, sheet: str | None = None,
             platforms: list[str] | None = None, only: Iterable[str] | None = None) -> GateResult:
    """Run the access sub-checks on a film and combine them into the one `access` gate result."""
    from slate.qa.checks import combine  # noqa: PLC0415
    from slate.qa.film import context  # noqa: PLC0415

    ctx = context(film_dir)
    _ = ctx.film
    wanted = set(only) if only else None
    runs: list[tuple[str, Callable]] = [
        ("flashes", lambda: check_flashes(ctx, render=render, render_act=render_act, render_from=render_from)),
        ("contrast", lambda: check_contrast(ctx)),
        ("captions", lambda: check_captions(ctx, captions=captions)),
        ("safe_zones", lambda: check_safe_zones(ctx, sheet=sheet, platforms=platforms)),
    ]
    subs = []
    for name, run in runs:
        if wanted is not None and name not in wanted:
            continue
        s = run()
        if s is not None:
            subs.append(s)
    paths: list[Any] = ["film.json", "timing/shots.json", "renders/registry.json"]
    paths += [str(p) for p in ctx.masters()]
    paths += [str(p) for p in caption_files(ctx, captions)]
    for extra in (render, sheet):
        if extra:
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
    from slate.qa.motion import lifted_review_items  # noqa: PLC0415

    result = combine(GATE, subs, engine=engine, inputs=ctx.inputs(paths),
                     details={"only": sorted(wanted) if wanted else None, **lifted_review_items(subs)},
                     empty_summary="nothing to check yet")
    if result.status == "PASS":
        result.summary = "accessible: " + "; ".join(c["summary"] for c in result.details.get("checks", []))
    return result


def gate(film_dir: Path) -> GateResult:
    """Accessibility: at most 3 flashes a second (WCAG 2.3.1 general and red flashes), the brand's text colour pairs
    at 4.5:1 (body) and 3:1 (large), captions at 17 characters a second and 2 lines of 42, and on 9:16 films text
    inside the platforms' safe zones (by OCR; UNCHECKED without it).

    The film folder in, one GateResult out (slate.qa runs it with every other gate; `slate qa access` alone)."""
    return evaluate(Path(film_dir))


GATES: dict[str, Callable[[Path], GateResult]] = {GATE: gate}
#: Gates here that read the whole picture (the flash scan decodes it; the safe zones read it through OCR).
SLOW = (GATE,)


# ---------------------------------------------------------------------------------------------------- CLI
SUBCHECKS = ("flashes", "contrast", "captions", "safe_zones")
HELP = "flashes, text contrast, caption pace and 9:16 safe zones"
DESCRIPTION = """Check the film's accessibility: at most 3 flashes a second (WCAG 2.3.1's general and red flash
thresholds, on the master or each act's render), the brand's text colour pairs (4.5:1 body, 3:1 large), captions
(at most 17 characters a second and 2 lines of 42), and on 9:16 films the platform safe zones (Instagram Reels,
TikTok, YouTube Shorts, or the format's own), located by Vision OCR. Without OCR the safe zones are UNCHECKED."""


def _cmd(args: argparse.Namespace) -> int:
    from slate import cli  # noqa: PLC0415
    from slate.qa.motion import report  # noqa: PLC0415

    film_dir = cli.film(args)
    result = evaluate(film_dir, render=args.render, render_act=args.act, render_from=args.render_from,
                      captions=args.captions, sheet=args.sheet, platforms=args.platform, only=args.only)
    return report(film_dir, result, args)


def register(sub) -> None:
    """Add `access` to a subparsers object (the `qa` group's stages: `slate qa access`)."""
    from slate.cli import add_command  # noqa: PLC0415

    p = add_command(sub, "access", _cmd, help=HELP, description=DESCRIPTION)
    p.add_argument("--only", action="append", choices=SUBCHECKS, metavar="CHECK",
                   help=f"run only this sub-check (repeat for more): {', '.join(SUBCHECKS)}")
    p.add_argument("--render", metavar="PATH",
                   help="scan this picture for flashes (a video, or a folder of frames) instead of the film's own")
    p.add_argument("--act", type=int, metavar="N", help="with --render: the act it shows (sets its first film frame)")
    p.add_argument("--render-from", dest="render_from", type=int, metavar="FRAME",
                   help="with --render: the film frame of its first frame")
    p.add_argument("--captions", metavar="PATH", help="check this caption file (.srt, .vtt or captions.json)")
    p.add_argument("--sheet", metavar="PATH",
                   help="read text from this contact sheet (a folder of frames, a frame, or a sheet with a tiles JSON)")
    p.add_argument("--platform", action="append", metavar="NAME",
                   help="check this platform's safe zones (repeat for more): instagram_reels, tiktok, youtube_shorts")
