"""Timecode and shot id burned into review cuts, so a note can say exactly where.

The label sits in a bar added below the picture (nothing in the frame is covered) and reads, for every frame:

    00:00:12:05  S04                                       v2

The timecode is the film's own clock (slate.clock.Clock.timecode on film.json's fps_num/fps_den: non-drop, counted
on the nominal rate) starting at the film's first frame, and the shot id comes from timing/shots.json. Two ways to
draw it, same result:

- **drawtext**, when this ffmpeg has it: one `drawtext` with ffmpeg's own timecode at the film's rate, and one per
  shot enabled over that shot's frames;
- **Pillow**, when it doesn't (Homebrew's ffmpeg bottle is built without drawtext): one small grey label image per
  frame, overlaid on the bar. Films' labels were drawn with Pillow for the same reason.

`[finish.review] burnin` picks one ("auto", "drawtext" or "pillow").
"""

from __future__ import annotations

import os
from pathlib import Path

from slate.errors import MissingPrerequisite
from slate.finish import media, settings
from slate.finish.film import Film

LABEL_GAP = "  "


def method(choice: str | None = None) -> str:
    """'drawtext' or 'pillow' for this ffmpeg and the configured choice."""
    choice = (choice or str(settings.load()["review"].get("burnin") or "auto")).strip().lower()
    if choice == "pillow":
        return "pillow"
    if media.has_filter("drawtext"):
        return "drawtext"
    if choice == "drawtext":
        raise MissingPrerequisite("This ffmpeg has no drawtext filter (it was built without libfreetype).",
                                  hint="Set [finish.review] burnin = \"auto\" to draw the labels with Pillow instead.")
    return "pillow"


def bar_height(picture_height: int, picture_width: int) -> int:
    """The label bar: about 7.5% of the picture's short side, at least 16 px, even."""
    return media.even(max(16.0, min(picture_height, picture_width) * 0.075))


def font_size(bar: int) -> int:
    return max(9, int(bar * 0.62))


def label(film: Film, frame: int, shot: str | None) -> str:
    return f"{film.timecode(frame)}{LABEL_GAP}{shot or '—'}"


def _font(size: int):
    from PIL import ImageFont  # noqa: PLC0415

    path = settings.font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def draw_strip(text: str, width: int, bar: int, *, right: str | None = None, font=None):
    """One label bar as a greyscale image: white text on black."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    font = font or _font(font_size(bar))
    img = Image.new("L", (width, bar), 0)
    d = ImageDraw.Draw(img)
    pad = max(4, bar // 3)
    top = max(0, (bar - font_size(bar)) // 2 - 1)
    d.text((pad, top), text, font=font, fill=255)
    if right:
        w = int(d.textlength(right, font=font))
        d.text((max(pad, width - pad - w), top), right, font=font, fill=255)
    return img


def pillow_strips(film: Film, folder: Path, width: int, bar: int, *, right: str | None = None) -> str:
    """Write one label image per film frame into `folder`; returns the ffmpeg input pattern."""
    folder.mkdir(parents=True, exist_ok=True)
    font = _font(font_size(bar))
    ranges = film.shot_ranges()
    i = 0
    for f in range(film.total_frames):
        while i + 1 < len(ranges) and f >= ranges[i][1]:
            i += 1
        shot = ranges[i][2] if ranges and ranges[i][0] <= f < ranges[i][1] else None
        img = draw_strip(label(film, f, shot), width, bar, right=right, font=font)
        img.save(folder / f"{f:06d}.png", compress_level=1)
    return str(folder / "%06d.png")


def _esc(text: str) -> str:
    """Escape a value for a drawtext option inside a filtergraph."""
    return (str(text).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
            .replace(",", "\\,"))


def drawtext_chain(film: Film, *, width: int, y0: int, bar: int, right: str | None = None) -> str:
    """The drawtext filters for the bar starting at y0 (timecode at the film's rate, then each shot's id)."""
    fs = font_size(bar)
    pad = max(4, bar // 3)
    font = settings.font_path()
    fontopt = f"fontfile='{_esc(font)}':" if font else ""
    ytext = f"{y0}+({bar}-text_h)/2"
    rate = f"{film.clock.fps_num}/{film.clock.fps_den}"
    parts = [f"drawtext={fontopt}timecode='00\\:00\\:00\\:00':rate={rate}:fontsize={fs}:fontcolor=white:"
             f"x={pad}:y={ytext}"]
    x_shot = pad + int(fs * 7.4)
    for fin, fout, sid in film.shot_ranges():
        parts.append(f"drawtext={fontopt}text='{_esc(sid)}':fontsize={fs}:fontcolor=white:x={x_shot}:y={ytext}:"
                     f"enable='between(n\\,{fin}\\,{fout - 1})'")
    if right:
        parts.append(f"drawtext={fontopt}text='{_esc(right)}':fontsize={fs}:fontcolor=white:x=w-tw-{pad}:y={ytext}")
    return ",".join(parts)


def describe(film: Film, how: str) -> dict:
    return {"method": how, "timecode": f"HH:MM:SS:FF on the film's clock ({film.clock.fps_num}/{film.clock.fps_den}, "
                                       f"non-drop, {round(film.clock.fps)} frames a second counted)",
            "shot_ids": True, "position": "a bar below the picture (nothing in the frame is covered)",
            "font": os.path.basename(settings.font_path() or "") or "Pillow's built-in font"}
