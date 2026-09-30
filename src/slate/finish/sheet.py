"""Contact sheets: a frame every half second, tiled, each labelled with its frame, shot and timecode
(contact_sheet.py and labeled_sheet.py, generalized).

Frames are picked by the film's own clock (the frame nearest each 0.5 s step, `Clock.frame_of`) and read exactly by
frame number from the master or the act renders, so a tile's label is the true film position of what it shows. Tile
size, grid and spacing come from [finish.review] (tile_long_side, sheet_cols, sheet_rows, sheet_step_s); the tile
keeps the film's aspect.

The sheets follow the engine's own contact sheets (engine/remotion/stills.mjs), so every reader treats them alike:

- each cell's label sits in its bottom-left corner and reads `f<frame> · <shot> · <timecode>`, the same timecode the
  phone cut shows under its picture (a note can quote either);
- beside each sheet image is its index, `{comp, file, rows, columns, cells: [{cell, frame, film_frame, shot,
  timecode}], geometry}`, which `slate qa access` reads to check safe zones on the sheet before a film has a render
  (the label is review material, and that check leaves it out);
- `<prefix>_index.json` lists every tile across all the sheets, for the packet.
"""

from __future__ import annotations

import math
from pathlib import Path

from slate import jsonio
from slate.errors import MissingPrerequisite, SlateError
from slate.finish import deps, media, settings
from slate.finish.cuts import Source, source_for
from slate.finish.film import Film

GAP = 4  # between cells and round the grid (the engine's tile filter calls these padding and margin)
COMP = "Film"


def tile_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    k = long_side / max(width, height)
    return media.even(width * k), media.even(height * k)


def sample_frames(film: Film, step_s: float) -> list[tuple[int, float]]:
    """(film frame, film seconds) every `step_s` seconds across the film, on the film's clock."""
    if step_s <= 0:
        raise SlateError("The contact-sheet spacing must be more than zero seconds.")
    total = film.total_frames
    out, k = [], 0
    while True:
        t = k * step_s
        f = film.clock.frame_of(t)
        if f >= total:
            break
        out.append((f, t))
        k += 1
    return out


def cell_label(frame: int, shot: str | None, timecode: str) -> str:
    """The label burned into a cell: the engine's, without the composition name (which the index carries)."""
    return " · ".join([f"f{frame}", *([shot] if shot else []), timecode])


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
    except TypeError:
        return ImageFont.load_default()


def _fitted_font(text_len_probe: str, width: int, height: int):
    """The largest font (7.5% of the cell's height, at least 8 px) whose label fits the cell's width."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    size = max(8, int(height * 0.075))
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    while size > 8:
        font = _font(size)
        if probe.textlength(text_len_probe, font=font) <= width - 10:
            return font, size
        size -= 1
    return _font(size), size


def _draw_label(im, text: str, font, size: int) -> None:
    from PIL import ImageDraw  # noqa: PLC0415

    d = ImageDraw.Draw(im)
    w = int(d.textlength(text, font=font))
    box_h = size + 6
    top = im.height - box_h
    d.rectangle([0, top, w + 8, im.height], fill=(0, 0, 0))
    d.text((4, top + 2), text, font=font, fill=(255, 255, 0))


def build(film: Film, out_dir: Path, *, source: Source | None = None, prefix: str = "sheet") -> dict:
    """Write the sheets (`<prefix>_NN.jpg`, each with `<prefix>_NN.json` beside it) and `<prefix>_index.json`.
    Returns the combined index."""
    from PIL import Image  # noqa: PLC0415

    qa_media = deps.module("slate.qa.media")
    if qa_media is None:
        raise MissingPrerequisite("Contact sheets read frames through slate.qa, which isn't installed.")
    cfg = settings.load()["review"]
    step = float(cfg.get("sheet_step_s") or 0.5)
    cols, rows = int(cfg.get("sheet_cols") or 12), int(cfg.get("sheet_rows") or 10)
    tw, th = tile_size(*film.size, int(cfg.get("tile_long_side") or 240))
    source = source or source_for(film)
    picks = sample_frames(film, step)
    tiles: dict[int, object] = {}
    by_file: dict[Path, list[tuple[int, int]]] = {}
    for f, _t in picks:
        path, local = source.locate(f)
        by_file.setdefault(path, []).append((f, local))
    for path, pairs in by_file.items():
        got = qa_media.read_frames(path, [local for _f, local in pairs], size=(tw, th), pix_fmt="rgb24")
        for f, local in pairs:
            if local not in got:
                raise SlateError(f"Couldn't read frame {f} from {film.rel(path)} for the contact sheet.")
            tiles[f] = got[local]
    out_dir.mkdir(parents=True, exist_ok=True)
    per = cols * rows
    longest = cell_label(max((f for f, _t in picks), default=0), max((str(s.get("id")) for s in film.shots),
                                                                     key=len, default=""), film.timecode(0))
    font, size = _fitted_font(longest, tw, th)
    sheets, index = [], []
    for s in range(0, len(picks), per):
        chunk = picks[s:s + per]
        k = len(sheets)
        used_cols = min(cols, len(chunk))
        used_rows = math.ceil(len(chunk) / cols)
        sheet = Image.new("RGB", (used_cols * (tw + GAP) + GAP, used_rows * (th + GAP) + GAP), (27, 27, 27))
        cells = []
        for i, (f, t) in enumerate(chunk):
            im = Image.fromarray(tiles[f])
            shot = film.shot_at(f)
            sid = str(shot.get("id")) if shot else None
            tc = film.timecode(f)
            _draw_label(im, cell_label(f, sid, tc), font, size)
            sheet.paste(im, (GAP + (i % used_cols) * (tw + GAP), GAP + (i // used_cols) * (th + GAP)))
            cells.append({"cell": i, "frame": f, "film_frame": f, "shot": sid, "timecode": tc})
            index.append({"frame": f, "t": round(t, 3), "timecode": tc, "shot": sid, "sheet": k, "tile": i})
        dest = out_dir / f"{prefix}_{k:02d}.jpg"
        sheet.save(dest, quality=85)
        jsonio.write_atomic(dest.with_suffix(".json"), {
            "generated": "do not edit by hand: slate review (contact sheet)",
            "comp": f"{COMP} sheet {k + 1:02d}", "file": dest.name, "rows": used_rows, "columns": used_cols,
            "geometry": {"cell": [tw, th], "padding": GAP, "margin": GAP}, "labels": True,
            "step_s": step, "cells": cells})
        sheets.append(dest)
    record = {"generated": "do not edit by hand: slate review", "step_s": step, "tile": [tw, th],
              "grid": [cols, rows], "source": source.describe(film),
              "sheets": [p.name for p in sheets], "tiles": index}
    jsonio.write_atomic(out_dir / f"{prefix}_index.json", record)
    return {**record, "paths": sheets, "index_file": out_dir / f"{prefix}_index.json"}
