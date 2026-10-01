"""Every gate reads only the film it validates (L-qa-008).

One film's first validation once read another film's captions and old cue sheets, and passed the wrong film. Here
two fixture films sit side by side in one films folder, with the same layout, and every slate.qa gate runs on each.
Two things are checked: every input a result records lies inside its own film (or is shared data: the brand pack, a
format, a named input), and nothing the gates open or hand to a child process lies inside the other film.
"""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path

import pytest

from slate import paths, procs, qa

from qa_core_helpers import moving_square, needs_ffmpeg, tone, write_brand, write_json


def _film(film_builder, film_id: str, words: str):
    fb = film_builder(film_id=film_id, shots=[(1, 30), (2, 30)])
    fb.all_events()
    fb.render_all()
    fb.master(moving_square(fb.total, fb.size))
    fb.script({"title": film_id, "acts": [{"name": "One", "shots": [{"id": "S01", "vo": words, "on_screen": words}]},
                                          {"name": "Two", "shots": [{"id": "S03", "vo": "", "on_screen": ""}]}]})
    fb.storyboard({"acts": [{"shots": [{"id": "S01", "on_screen": words}]}]})
    fb.captions([{"text": words, "lines": [words], "start": 0.1, "end": 0.9}])
    write_json(fb.path / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "at": 0.1, "file": "voice/S01.wav"}]})
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {"take": "voice/S01.wav", "line": words,
                                                                        "heard": words, "words": []}}})
    fb.mix({"dialogue": tone(2.0, amp=0.1), "music": tone(2.0, amp=0.05, freq=440.0)})
    return fb


def _under(path: str, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


@needs_ffmpeg
def test_every_gate_reads_only_its_own_film(film_builder, brand_store, monkeypatch):
    write_brand(brand_store, banned=["forbidden"])
    a = _film(film_builder, "film-a", "Every number has a source.")
    b = _film(film_builder, "film-b", "Another film says another thing.")
    opened: list[str] = []
    real_open, real_io_open, real_run = builtins.open, io.open, procs.run

    def spy_open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            opened.append(os.fspath(file))
        return real_open(file, *args, **kwargs)

    def spy_io_open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            opened.append(os.fspath(file))
        return real_io_open(file, *args, **kwargs)

    def spy_run(argv, *args, **kwargs):
        opened.extend(os.fspath(x) for x in argv if isinstance(x, (str, os.PathLike)) and os.sep in os.fspath(x))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_io_open)
    monkeypatch.setattr(procs, "run", spy_run)
    names = qa.resolve(None)
    assert {"captions", "sync", "banned_words", "glitch", "boundary", "cuts", "psnr", "end_hold"} <= set(names)
    for mine, other in ((a, b), (b, a)):
        opened.clear()
        results = qa.run(mine.path)
        assert {r.gate for r in results} >= set(names) - {"stability"}
        strays = sorted({p for p in opened if _under(p, other.path)})
        assert strays == [], f"running {mine.path.name}'s gates opened {other.path.name}'s files: {strays[:5]}"
        for r in results:
            for key in r.inputs or {}:
                if ":" in key and not key.startswith("/"):
                    continue  # a named input (film:*, brand:<name>, format:<name>)
                if not key.startswith("/"):
                    assert ".." not in Path(key).parts, (r.gate, key)  # relative: inside its own film
                    continue
                assert not _under(key, other.path), (r.gate, key)
                # absolute: its own film, or shared data (the brand pack, Slate's own schemas and formats)
                assert _under(key, mine.path) or _under(key, brand_store) or _under(key, paths.REPO), (r.gate, key)


def test_the_check_would_catch_a_gate_that_strays(film_builder, monkeypatch):
    """The spy itself works: a gate that reads the other film's captions is caught."""
    a = film_builder(film_id="film-a")
    b = film_builder(film_id="film-b")
    b.captions([{"text": "Theirs", "lines": ["Theirs"], "start": 0.0, "end": 1.0}])
    opened: list[str] = []
    real_io_open = io.open

    def spy(file, *args, **kwargs):
        opened.append(os.fspath(file))
        return real_io_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    (b.path / "voice" / "captions" / "captions.json").read_text()  # what the films' first validator did
    assert any(_under(p, b.path) for p in opened)
    assert not any(_under(p, a.path) for p in opened)
