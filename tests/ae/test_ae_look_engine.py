"""The AE lane reads the brand's look.engine when it starts acts (P074): `slate engine scaffold --act N` on a brand whose
look builds in After Effects starts the act as an AE scene written as data (a starter that resolves against the film
and the brand before it's kept), and the AE build names the acts that aren't started yet."""

from __future__ import annotations

import json

from slate import jsonio
from slate.ae import acts, spec
from slate.cli import run_cli

from ae_helpers import make_film


def run(*argv):
    code, out, err = run_cli(list(argv))
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


def _look_ae(brands):
    pack = brands / "neutral-ae" / "brand.json"
    data = json.loads(pack.read_text())
    data["look"] = {"id": "flat", "engine": "ae"}
    pack.write_text(json.dumps(data))


def test_scaffold_starts_an_act_in_after_effects_when_the_look_builds_there(slate_home):
    d, _media = make_film(slate_home["films"], slate_home["brands"], scene=None)
    _look_ae(slate_home["brands"])
    # the build names the act that isn't started yet, with the next step
    warnings = spec.build_spec(d, queue=False)["warnings"]
    assert any("act 2 isn't started in After Effects yet" in w and "slate engine scaffold --act 2" in w for w in warnings)
    code, out, err = run("engine", "scaffold", "--act", "2", "--film", str(d), "--json")
    assert code == 0, err
    assert out["file"] == "scenes/ae/act2.json" and out["engine"] == "ae"
    scene = jsonio.read(d / "scenes" / "ae" / "act2.json")
    assert scene["bg"] == "paper" and scene["layers"][0]["font"] == "display" and scene["layers"][0]["text"] == "Hello"
    assert scene["anims"][0]["keys"][0]["ease"] == "enter"  # the brand's own entrance ease
    # the act is AE-built now: the build carries its resolved scene, and its trace and events export without AE
    built = spec.build_spec(d, queue=False)
    assert next(a for a in built["acts"] if a["index"] == 2)["engine"] == "ae"
    assert not any("act 2 isn't started" in w for w in built["warnings"])
    res = acts.export(d, 2)
    assert res["silent"] is True and res["entries"] == 1
    # never replaced without --force
    code, out, _ = run("engine", "scaffold", "--act", "2", "--film", str(d), "--json")
    assert code == 1 and "already started in After Effects" in out["message"]
    code, out, err = run("engine", "scaffold", "--act", "2", "--film", str(d), "--force", "--json")
    assert code == 0, err


def test_a_look_that_builds_elsewhere_names_nothing(slate_home):
    d, _media = make_film(slate_home["films"], slate_home["brands"], scene=None)
    assert not any("isn't started in After Effects" in w for w in spec.build_spec(d, queue=False)["warnings"])
    assert acts.look_engine({"look": {"engine": "ae"}}) == "ae" and acts.look_engine({"look": {}}) is None
