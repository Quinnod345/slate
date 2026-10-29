"""Effects are an optional prerequisite: explicit productions rebuild them; score-only films do not acquire them."""
from slate import jsonio
from slate.state import make


def test_effects_join_only_explicit_or_previously_generated_productions(new_film):
    folder = new_film()
    assert "sfx" not in make.plan("deliver", folder)
    data = jsonio.read(folder / "film.json")
    data["sound"] = {"effects": True}
    jsonio.write_atomic(folder / "film.json", data)
    order = make.plan("deliver", folder)
    assert order.index("events") < order.index("sfx") < order.index("mix")
    data["sound"] = {}
    jsonio.write_atomic(folder / "film.json", data)
    jsonio.write_atomic(folder / "sound/stems/stems.json", {"producer": "slate sfx", "stems": {}})
    assert "sfx" in make.plan("mix", folder)


def test_manual_stems_do_not_trigger_effect_generation(new_film):
    folder = new_film()
    jsonio.write_atomic(folder / "sound/stems/stems.json", {"producer": "handmade", "stems": {}})
    assert "sfx" not in make.plan("mix", folder)
    assert make.plan("sfx", folder)[-1] == "sfx"


def test_optional_effect_failure_blocks_the_mix(new_film, monkeypatch):
    folder = new_film()
    data = jsonio.read(folder / "film.json")
    data["sound"] = {"effects": True}
    jsonio.write_atomic(folder / "film.json", data)
    def state(folder, node, **kwargs):
        return {"node": node.name, "state": "missing" if node.name in ("sfx", "mix") else "fresh",
                "outputs": [], "reasons": []}
    monkeypatch.setattr(make, "node_state", state)
    monkeypatch.setattr(make, "adopt_inputs", lambda *args: [])
    monkeypatch.setattr(make.status, "command_available", lambda *args: True)
    calls = []
    def runner(argv):
        calls.append(argv[:2])
        return 1, '{"message":"missing installed sample"}', ""
    result = make.run(folder, "mix", runner=runner)
    rows = {row["node"]: row for row in result["results"]}
    assert calls == [["sfx", "run"]]
    assert rows["sfx"]["action"] == "failed" and rows["mix"]["action"] == "blocked"
