"""Before re-rendering a fix, the fault's flags come out of the act's baseline, so the new render proves the fix
instead of the baseline hiding the fault (the films' B4 re-renders: "their flags were taken out of the baseline
first")."""

from __future__ import annotations

import argparse
import json

import numpy as np
import pytest

from qa_stability_helpers import needs_ffmpeg
from slate import jsonio, log
from slate.errors import Refused, UsageError
from slate.qa import stability as S

pytestmark = needs_ffmpeg

POP_AT = 16  # local frame where act 1's card pops in


def _pop_frames(helpers, film) -> np.ndarray:
    frames = np.repeat(helpers.textured(film.size, seed=3)[None], film.n, axis=0)
    frames[POP_AT:, 60:120, 200:260] = 245.0
    return frames


def _film(stab_film, helpers):
    f = stab_film(frames=26)
    f.render(1, _pop_frames(helpers, f))
    f.render(2, np.repeat(helpers.textured(f.size, seed=4)[None], f.n, axis=0))
    return f


def _gate(film):
    with S.run_options(jobs=1):
        return S.gate(film.path)


def test_flags_taken_out_of_the_baseline_count_again(stab_film, helpers):
    f = _film(stab_film, helpers)
    assert _gate(f).status == "REVIEW"
    S.accept_baseline(f.path, 1, by="Quinn", reason="looked at the strip")
    assert _gate(f).status == "PASS"
    pop = f.act_in(1) + POP_AT
    # a range elsewhere, or another class, takes nothing out
    assert S.drop_from_baseline(f.path, 1, (pop + 3, pop + 6), by="Quinn", reason="wrong frames")["dropped"] == 0
    assert S.drop_from_baseline(f.path, 1, (pop, pop), classes=["wobble"], by="Quinn", reason="x")["dropped"] == 0
    rec = S.drop_from_baseline(f.path, 1, (pop - 1, pop + 1), by="Quinn", reason="re-rendering the card's fix")
    assert rec["dropped"] >= 1 and rec["frames"] == [pop - 1, pop + 1]
    data = jsonio.read(f.path / S.baseline_rel(1))
    last = data["dropped"][-1]
    assert last["by"] == "Quinn" and last["reason"] == "re-rendering the card's fix" and last["flags"]
    assert data["accepted"][0]["by"] == "Quinn"  # the history stays: who accepted, then who took out
    r = _gate(f)
    assert r.status == "REVIEW"  # the pop is open again, for the fix's render to clear
    assert [i["key"] for i in r.details["review_items"] if i["act"] == 1] == [f"pop:frame:{POP_AT}"]


def test_taking_flags_out_needs_a_person_a_reason_and_a_baseline(stab_film, helpers, monkeypatch):
    f = _film(stab_film, helpers)
    _gate(f)
    with pytest.raises(UsageError, match="no baseline"):
        S.drop_from_baseline(f.path, 1, (0, 99), by="Quinn", reason="fix")
    S.accept_baseline(f.path, 1, by="Quinn", reason="looked")
    with pytest.raises(UsageError, match="name and their reason"):
        S.drop_from_baseline(f.path, 1, (0, 99), by="", reason="")
    monkeypatch.setattr("slate.procs.agent_context", lambda: "an agent")
    with pytest.raises(Refused, match="autonomous"):
        S.drop_from_baseline(f.path, 1, (0, 99), by="agent", reason="fix", mode="autonomous")


def test_the_drop_option(stab_film, helpers, capsys):
    f = _film(stab_film, helpers)
    _gate(f)
    S.accept_baseline(f.path, 1, by="Quinn", reason="looked")
    parser = argparse.ArgumentParser(prog="slate qa")
    S.register(parser.add_subparsers(dest="stage"))
    pop = f.act_in(1) + POP_AT
    log.set_json(True)
    try:
        args = parser.parse_args(["stability", "baseline", "--act", "1", "--drop", f"{pop - 2}-{pop + 2}",
                                  "--drop-class", "pop", "--by", "Quinn", "--reason", "fixing the card",
                                  "--film", str(f.path), "--json"])
        assert args.handler(args) == 0
        out = json.loads(capsys.readouterr().out)["baseline"]
        assert out["dropped"] >= 1 and out["classes"] == ["pop"]
        args = parser.parse_args(["stability", "baseline", "--act", "1", "--drop", "soon", "--by", "Quinn",
                                  "--reason", "x", "--film", str(f.path), "--json"])
        with pytest.raises(UsageError, match="film frames"):
            args.handler(args)
    finally:
        log.set_json(False)
