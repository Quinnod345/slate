"""A missing gate module cannot make a final delivery's requirements disappear."""

from types import SimpleNamespace

import pytest

from slate import gates
from slate.finish import checks, deps


@pytest.mark.parametrize("missing", ["slate.qa.motion", "slate.qa.access", "slate.qa.stability", "slate.sound.balance"])
def test_final_delivery_blocks_when_required_gate_module_did_not_load(tmp_path, monkeypatch, missing):
    qa = SimpleNamespace(GATES={"psnr": None}, EXTERNAL=("slate.qa.motion", "slate.qa.access", "slate.qa.stability"))
    sound = SimpleNamespace(GATE_NAMES=("sound.loudness",), EXTERNAL=("slate.sound.balance",))
    for module in (qa, sound):
        module.EXTERNAL_STATUS = {name: "import failed: missing dependency" if name == missing else "merged: gate"
                                  for name in module.EXTERNAL}
    monkeypatch.setattr(deps, "module", lambda name: {"slate.qa": qa, "slate.sound.verify": sound}.get(name))
    names = ("psnr", "sound.loudness", "master.frames", "master.finish")
    monkeypatch.setattr(gates, "load_report", lambda folder: {name: gates.GateResult.pass_(name, "checked")
                                                            for name in names})
    film = SimpleNamespace(dir=tmp_path, master_record=lambda: {"method": "ffmpeg", "masters": [{"audio": True}]})

    result = checks.film_gates(film)

    assert not result["passed"]
    assert result["blocking"] == [{"gate": missing.removeprefix("slate.") + ".available", "status": "UNCHECKED",
                                   "summary": f"required gate module {missing} is unavailable: import failed: missing dependency"}]
