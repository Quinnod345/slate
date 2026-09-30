"""The safe zones on the contact sheet: the engine's tiled sheets read cell by cell (their geometry inferred the way
ffmpeg's tile filter lays them out), the label the engine burns into each cell left out, the film's own sheets used
when there's no picture yet, and the OCR of a sheet cached so a quick run reuses it and never starts it."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from qa_motion_helpers import make_film
from slate.qa import access as A
from slate.qa import ocr
from slate.qa.film import FilmContext

FPS = 30


def timecode(frame: int, fps: int = FPS) -> str:
    """The engine's timecode (engine/remotion/lib/common.mjs): HH:MM:SS:FF."""
    total, ff = divmod(frame, fps)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}:{ff:02d}"


def colour(i: int) -> tuple[int, int, int]:
    return (20 + 37 * i % 200, 90 + 11 * i % 120, 140)


def engine_sheet(folder: Path, *, film=(540, 960), scale=0.25, columns=3, frames=(0, 15, 30, 45, 60), pad=8,
                 comp="Film", shot="S1", labels=True) -> Path:
    """A contact sheet laid out as the engine's stills command makes one: cells of the film's shape at `scale`,
    tiled by ffmpeg's tile filter (margin = padding = `pad`), blank filler cells, and the index JSON beside it."""
    folder.mkdir(parents=True, exist_ok=True)
    cw, ch = round(film[0] * scale), round(film[1] * scale)
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGB", (columns * cw + (columns + 1) * pad, rows * ch + (rows + 1) * pad), (27, 27, 27))
    for i, _f in enumerate(frames):
        col, row = i % columns, i // columns
        sheet.paste(Image.new("RGB", (cw, ch), colour(i)), (pad + col * (cw + pad), pad + row * (ch + pad)))
    png = folder / f"{comp}_sheet.png"
    sheet.save(png)
    index = {"generated": "do not edit by hand: slate stills (contact sheet)", "comp": comp, "file": png.name,
             "rows": rows, "columns": columns,
             "cells": [{"cell": i, "frame": f, "film_frame": f, "shot": shot, "timecode": timecode(f)}
                       for i, f in enumerate(frames)]}
    if not labels:
        index["labels"] = False
    (folder / f"{comp}_sheet.json").write_text(json.dumps(index, indent=1))
    return png


class CellOCR:
    """Stands in for Vision on a contact sheet: knows each cell by its colour and reads that cell's burned-in label
    (bottom-left) plus whatever film text the test puts in it."""

    engine = {"name": "scripted OCR", "version": "test", "recognition_level": "accurate"}

    def __init__(self, frames, *, comp="Film", shot="S1", film_text=None, label=True, misread=False):
        self.frames, self.comp, self.shot = list(frames), comp, shot
        self.film_text = film_text or {}
        self.label, self.misread = label, misread
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        rgb = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        i = next(k for k in range(len(self.frames)) if colour(k) == rgb)
        f = self.frames[i]
        lines = []
        if self.label:
            text = f"{self.comp} · f{f} · {self.shot} · {timecode(f)}"
            if self.misread:
                text = text.replace("·", "•").replace("1", "l", 1)  # how OCR tends to misread a small mono label
            lines.append({"text": text, "conf": 0.93, "box": [0.03, 0.02, 0.9, 0.04]})  # the bottom-left corner
        lines += [dict(x) for x in self.film_text.get(i, [])]
        return lines


def vertical(home, film_id="reel", **kw):
    return make_film(home["films"], film_id=film_id, size=(540, 960), fmt="social-reel", acts={1: (0, 75)}, **kw)


# ---------------------------------------------------------------------------------------------------- geometry
@pytest.mark.parametrize("pad", [0, 4, 8, 12])
def test_tile_geometry_finds_the_cells_of_the_films_shape(pad):
    cw, ch, cols, rows = 135, 240, 3, 2
    size = (cols * cw + (cols + 1) * pad, rows * ch + (rows + 1) * pad)
    geo = A.tile_geometry(size, cols, rows, aspect=540 / 960)
    assert geo == {"cell": [cw, ch], "padding": pad, "margin": pad}


def test_tile_geometry_uses_the_index_when_it_says_and_refuses_what_doesnt_fit():
    assert A.tile_geometry((500, 300), 2, 1, hint={"cell_width": 240, "cell_height": 135, "padding": 4,
                                                   "margin": 6}) == {"cell": [240, 135], "padding": 4, "margin": 6}
    assert A.tile_geometry((1000, 1000), 3, 2, aspect=540 / 960) is None  # no padding makes 9:16 cells there


def test_the_engines_sheet_is_read_cell_by_cell(slate_home):
    png = engine_sheet(slate_home["tmp"] / "review" / "stills")
    cells = A.sheet_cells(png.with_suffix(".json"), film_size=(540, 960), fps=FPS)
    assert len(cells) == 5  # the sixth tile is the engine's blank filler
    for i, c in enumerate(cells):
        assert c.image.size == (135, 240) and c.image.getpixel((5, 5)) == colour(i)
        assert c.image.getpixel((134, 239)) == colour(i)  # no gutter inside the crop
    assert cells[2].frame == 30 and cells[2].t == 1.0 and "film frame 30, 1.00 s" in cells[2].label
    assert cells[1].stamp == "Film · f15 · S1 · 00:00:00:15"
    same = A.sheet_cells(png, film_size=(540, 960), fps=FPS)  # the PNG finds its index beside it
    assert [c.label for c in same] == [c.label for c in cells]


def test_a_sheet_whose_cells_cant_be_located_is_a_usage_error(slate_home):
    from slate.errors import UsageError

    png = engine_sheet(slate_home["tmp"] / "odd")
    with pytest.raises(UsageError, match="can't be located"):
        A.sheet_cells(png, film_size=(1000, 1000), fps=FPS)  # a square film can't have made these cells


# ---------------------------------------------------------------------------------------------------- the label
def test_the_label_is_recognised_only_in_its_corner_and_only_when_it_reads_as_the_label():
    stamp = "Act2 · f120 · S04 · 00:00:04:00"
    corner = [0.03, 0.93, 0.9, 0.97]
    assert A.is_stamp("Act2 · f120 · S04 · 00:00:04:00", corner, stamp)
    assert A.is_stamp("Act2 • f12O • S04 • 00:00:04:00", corner, stamp)  # a misread O for 0
    assert A.is_stamp("00:00:04:00", corner, stamp)  # a split-off piece
    assert not A.is_stamp("Act2 · f120 · S04 · 00:00:04:00", [0.03, 0.40, 0.9, 0.44], stamp)  # not in the corner
    assert not A.is_stamp("Film 2026", corner, "Film · f15 · S1 · 00:00:00:15")  # film text in the corner
    assert not A.is_stamp("Act 2", corner, stamp)
    assert not A.is_stamp("Book a demo", corner, stamp)
    assert not A.is_stamp("Act2 · f120", corner, None)  # no label drawn: nothing is left out


def test_the_burned_in_label_is_left_out_and_film_text_is_still_checked(slate_home):
    film = vertical(slate_home)
    png = engine_sheet(film / "review" / "stills")
    low = {"text": "Swipe up", "conf": 0.97, "box": [0.3, 0.08, 0.4, 0.05]}  # y 0.87–0.92: every platform covers it
    frames = (0, 15, 30, 45, 60)
    with ocr.use_backend(CellOCR(frames, film_text={3: [low]}, misread=True)):
        s = A.check_safe_zones(FilmContext(film), sheet=str(png))
    assert s.status == "FAIL"
    assert s.details["sheet_labels_left_out"] == 5 and s.details["text_boxes"] == 1
    assert {v["text"] for v in s.details["violations"]} == {"Swipe up"}
    assert all("cell 4 (film frame 45, 1.50 s)" in v["at"] for v in s.details["violations"])


def test_labels_alone_pass(slate_home):
    film = vertical(slate_home)
    png = engine_sheet(film / "review" / "stills")
    mid = {"text": "Plan your week", "conf": 0.98, "box": [0.25, 0.5, 0.4, 0.05]}  # clear of TikTok's button column
    with ocr.use_backend(CellOCR((0, 15, 30, 45, 60), film_text={0: [mid]})):
        s = A.check_safe_zones(FilmContext(film), sheet=str(png))
    assert s.status == "PASS" and s.details["sheet_labels_left_out"] == 5 and s.details["text_boxes"] == 1


# ---------------------------------------------------------------------------------------------------- the film's sheets
def test_with_no_picture_yet_the_films_own_contact_sheet_is_read(slate_home):
    film = vertical(slate_home)
    engine_sheet(film / "review" / "stills")
    low = {"text": "Link in bio", "conf": 0.97, "box": [0.2, 0.1, 0.6, 0.05]}
    with ocr.use_backend(CellOCR((0, 15, 30, 45, 60), film_text={0: [low]})):
        s = A.check_safe_zones(FilmContext(film))
    assert s.status == "FAIL" and "contact sheets" in s.details["engine"]["source"]
    stills = film / "review" / "stills"
    assert s.details["files"] == [str(stills / "Film_sheet.png"), str(stills / "Film_sheet.json")]


def test_only_the_newest_sheet_of_each_composition_counts(slate_home):
    film = vertical(slate_home)
    old = engine_sheet(film / "review" / "v1")
    new = engine_sheet(film / "review" / "v2")
    act = engine_sheet(film / "review" / "v2", comp="Act1")
    past = time.time() - 3600
    os.utime(old.with_suffix(".json"), (past, past))
    found = A.find_sheets(FilmContext(film))
    assert found == sorted([new.with_suffix(".json"), act.with_suffix(".json")])


def test_nothing_to_read_is_unchecked_with_the_next_step(slate_home):
    with ocr.use_backend(CellOCR((0,))):
        s = A.check_safe_zones(FilmContext(vertical(slate_home)))
    assert s.status == "UNCHECKED" and "no contact sheet" in s.summary and "slate render" in " ".join(s.evidence)


def test_reading_a_sheet_is_cached_and_a_quick_run_never_starts_it(slate_home):
    film = vertical(slate_home)
    png = engine_sheet(film / "review" / "stills")
    frames = (0, 15, 30, 45, 60)
    quick = FilmContext(film, quick=True)
    with ocr.use_backend(CellOCR(frames)) as backend:
        s = A.check_safe_zones(quick)
    assert s.status == "UNCHECKED" and "--quick" in s.summary and backend.calls == 0
    with ocr.use_backend(CellOCR(frames)) as backend:
        first = A.check_safe_zones(FilmContext(film))
        assert backend.calls == 5
        again = A.check_safe_zones(FilmContext(film, quick=True))
        assert backend.calls == 5  # the quick run reused what the full run read
    assert first.status == again.status == "PASS"
    assert list((film / "qa" / "access").glob("sheet_*.json"))
    Image.new("RGB", Image.open(png).size, "white").save(png)  # a new sheet: the cache no longer fits
    with ocr.use_backend(CellOCR(frames)):
        s = A.check_safe_zones(FilmContext(film, quick=True))
    assert s.status == "UNCHECKED"


def test_a_sheet_given_on_the_command_line_narrows_the_run(slate_home):
    film = vertical(slate_home)
    png = engine_sheet(slate_home["tmp"] / "elsewhere")
    with ocr.use_backend(CellOCR((0, 15, 30, 45, 60))):
        r = A.evaluate(film, sheet=str(png), only=["safe_zones"])
    checks = {c["check"]: c["status"] for c in r.details["checks"]}
    assert checks == {"safe_zones": "PASS", "scope": "UNCHECKED"} and r.status == "UNCHECKED"
    assert any("--sheet" in n for n in r.details["narrowed"])
    assert str(png) in r.inputs or any(k.endswith("Film_sheet.png") for k in r.inputs)
