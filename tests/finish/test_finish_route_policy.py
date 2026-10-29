"""A social variant's finish gate follows the same route as its master command."""

from types import SimpleNamespace

import pytest

from slate.finish import checks, master


@pytest.mark.parametrize("mode", ["supervised", "express"])
def test_social_variant_ffmpeg_master_passes_its_declared_finish_route(slate_home, monkeypatch, mode):
    film = SimpleNamespace(mode=mode, dir=slate_home["films"] / "social", data={
        "mode": mode,
        "derivation": {"route": {"master": "ffmpeg", "route": "social", "channel": "reels", "label": "Reel"}},
    }, ctx=SimpleNamespace(inputs=lambda names: {}))
    monkeypatch.setattr(master, "film_mode", lambda _film: mode)

    method, _ = master.choose(film)

    assert method == "ffmpeg"
    result = checks.master_finish(film, method)
    assert result.status == "PASS", result.summary
    assert result.details["route"]["channel"] == "reels"


def test_supervised_interim_ffmpeg_master_still_needs_ae(slate_home):
    film = SimpleNamespace(mode="supervised", data={}, ctx=SimpleNamespace(inputs=lambda names: {}))
    assert checks.master_finish(film, "ffmpeg").status == "FAIL"
