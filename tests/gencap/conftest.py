"""Fixtures for generation and capture: a neutral film on the film clock, in replay mode, in temporary folders.

Films are made through slate.state.film.create (the documented interface) when the state module is installed, with a
tiny neutral format (25 fps, 320×180, so conforms stay fast) and a neutral brand pack whose four references are
approved (so autonomous films can exist). Without the state module, a minimal film.json stands in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gencap_helpers import FPS, SIZE, write_brand, write_timing


@pytest.fixture
def make_film(slate_home):
    """make_film(mode=..., generation=..., uploads=..., shots=...) → a film folder in replay mode."""
    from PIL import Image

    def make(film_id: str = "f1", *, mode: str = "supervised", generation: str = "rarely", uploads: bool = True,
             shots: list[dict] | None = None) -> Path:
        write_brand(slate_home["brands"], uploads=uploads)
        fmt = slate_home["tmp"] / "fmt-test.json"
        fmt.write_text(json.dumps({"name": "test", "width": SIZE[0], "height": SIZE[1], "fps_num": FPS[0],
                                   "fps_den": FPS[1], "runtime_s": 12, "budget": {"higgsfield": 50,
                                                                                  "elevenlabs": 1000}}))
        try:
            from slate.state import film as state_film
        except ImportError:
            state_film = None
        if state_film is not None:
            film = state_film.create(film_id, brand="neutral", format=str(fmt), mode=mode, generation=generation,
                                     git=False)
        else:
            film = slate_home["films"] / film_id
            film.mkdir(parents=True)
            (film / "film.json").write_text(json.dumps({
                "id": film_id, "fps_num": FPS[0], "fps_den": FPS[1], "width": SIZE[0], "height": SIZE[1],
                "brand": "neutral", "mode": mode, "generation": generation, "budget": {}, "pins": {}}))
        (film / "refs").mkdir(exist_ok=True)
        Image.new("RGB", SIZE, (120, 130, 140)).save(film / "refs" / "start.png")
        write_timing(film, shots)
        return film

    return make


@pytest.fixture
def film(make_film):
    return make_film()
