"""Persist hand-in freshness separately from the engine's atomic raw source capture."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate import gates, jsonio
from slate.engine import cli as engine_cli
from slate.errors import Refused


@pytest.fixture
def captured_handin(slate_home, monkeypatch):
    film = slate_home["films"] / "neutral-contract"
    (film / "timing").mkdir(parents=True)
    (film / "scenes" / "act1").mkdir(parents=True)
    jsonio.write_atomic(film / "film.json", {
        "id": "neutral-contract", "title": "Neutral contract", "width": 640, "height": 360,
        "fps_num": 30000, "fps_den": 1001, "targets": {"runtime_s": 4.0},
        "pins": {}, "pin_log": [], "budget": {"limit_usd": 1.0}, "versions": [],
    })
    jsonio.write_atomic(film / "timing" / "shots.json", {
        "shots": [{"id": "N01", "act": 1, "frame_in": 0, "frame_out": 120}],
    })
    (film / "scenes" / "act1" / "index.tsx").write_text(
        "export default function Neutral(){return null;}\n", encoding="utf-8")
    raw = {
        "gate": "handin.act1.contracts", "status": "PASS",
        "summary": "the film has one act: no boundaries", "evidence": [],
        "engine": {"name": "remotion renderStill + ffmpeg pixel hash", "version": "swangle"},
        "inputs": {
            str((film / "film.json").resolve()): jsonio.sha256(film / "film.json"),
            "scenes/act1/index.tsx": jsonio.sha256(film / "scenes/act1/index.tsx"),
            "timing/shots.json": jsonio.sha256(film / "timing/shots.json"),
        },
        "checked_at": "2026-01-01T00:00:00Z",
        "details": {"stage": "scenes", "boundaries": 0, "checked": 0},
    }
    emitted = []
    monkeypatch.setattr(engine_cli.slate_cli, "film", lambda args: film)
    monkeypatch.setattr(engine_cli, "banned_words", lambda _: [])
    monkeypatch.setattr(engine_cli, "never_claim", lambda _: [])
    monkeypatch.setattr(engine_cli, "_shot_engine_gates", lambda *args: [])
    monkeypatch.setattr(engine_cli, "_motion_gates", lambda *args: ([], []))
    monkeypatch.setattr(engine_cli.log, "result", lambda doc, human: emitted.append(doc))

    def check(*, mutate_before_return=None):
        def call(command, args, **kwargs):
            assert command == "check" and args["film"] == str(film)
            if mutate_before_return:
                mutate_before_return()
            return {"gates": [deepcopy(raw)]}
        monkeypatch.setattr(engine_cli.bridge, "call", call)
        code = engine_cli._check(SimpleNamespace(
            act="1", against=None, markup=False, no_contracts=False, hold=6,
            determinism=False, dry_run=False))
        return code, emitted[-1]

    return film, raw, check


@pytest.mark.parametrize("alias", ["absolute", "relative"])
@pytest.mark.parametrize("change", ["score_pin", "version", "budget"])
def test_handin_bookkeeping_does_not_stale_contract(captured_handin, alias, change):
    film, raw, check = captured_handin
    if alias == "relative":
        raw["inputs"]["film.json"] = raw["inputs"].pop(str((film / "film.json").resolve()))
    code, result = check()
    assert code == 0 and result["gates"][0]["status"] == "PASS"
    data = jsonio.read(film / "film.json")
    if change == "score_pin":
        data["pins"]["music"] = "neutral-score-model"
        data["pin_log"].append({"key": "music", "event": "pin", "value": "neutral-score-model"})
    elif change == "version":
        data["versions"].append({"n": 2, "note": "review revision"})
    else:
        data["budget"]["limit_usd"] = 2.0
    jsonio.write_atomic(film / "film.json", data)
    fresh = gates.load_report(film)["handin.act1.contracts"]
    assert fresh.status == "PASS", fresh.to_dict()
    assert fresh.inputs["film:*"] == gates.inputs_for(film, ["film.json"])["film:*"]
    assert "film.json" not in fresh.inputs
    assert str((film / "film.json").resolve()) not in fresh.inputs


@pytest.mark.parametrize("change", ["film_setting", "scene", "timing"])
def test_handin_meaningful_inputs_still_stale_contract(captured_handin, change):
    film, raw, check = captured_handin
    assert check()[0] == 0
    if change == "film_setting":
        data = jsonio.read(film / "film.json")
        data["width"] = 720
        jsonio.write_atomic(film / "film.json", data)
    elif change == "scene":
        with (film / "scenes/act1/index.tsx").open("a", encoding="utf-8") as fh:
            fh.write("export const changed = true;\n")
    else:
        data = jsonio.read(film / "timing/shots.json")
        data["shots"][0]["frame_out"] = 121
        jsonio.write_atomic(film / "timing/shots.json", data)
    fresh = gates.load_report(film)["handin.act1.contracts"]
    assert fresh.status == "STALE" and fresh.details["was"] == "PASS"


@pytest.mark.parametrize("change", ["score_pin", "film_setting"])
def test_handin_raw_capture_change_is_refused_before_persistence(captured_handin, change):
    film, raw, check = captured_handin

    def tamper():
        data = jsonio.read(film / "film.json")
        if change == "score_pin":
            data["pins"]["music"] = "neutral-score-model"
            data["pin_log"].append({"key": "music", "event": "pin"})
        else:
            data["width"] = 720
        jsonio.write_atomic(film / "film.json", data)

    with pytest.raises(Refused, match="changed"):
        check(mutate_before_return=tamper)
    assert not (film / "qa/gates.json").exists()
    assert not (film / "qa/history.jsonl").exists()


def test_handin_conflicting_raw_alias_is_refused(captured_handin):
    film, raw, check = captured_handin
    raw["inputs"]["film.json"] = "0" * 64
    with pytest.raises(Refused, match="changed"):
        check()
    assert not (film / "qa/gates.json").exists()
