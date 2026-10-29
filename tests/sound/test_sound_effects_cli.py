"""Effects stage routing, nonmutating plans, and explicit queued execution."""

import json

import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.sound import effects
from slate.sound.effects import engine


@pytest.fixture
def commands(slate_home, monkeypatch):
    folder = slate_home["films"] / "effects"
    jsonio.write_atomic(folder / "film.json", {"id": "effects"})
    calls = []
    monkeypatch.setattr(effects, "require_proof3", lambda: {})
    monkeypatch.setattr(engine, "spot", lambda film, seed=0: calls.append(("spot", film, seed)) or
                        film / "sound/sound_cues.json")
    monkeypatch.setattr(engine, "render", lambda film: calls.append(("render", film)) or {"stems": ["ui", "fx"]})
    monkeypatch.setattr(engine, "plan", lambda film, stage="run": calls.append(("plan", film, stage)) or
                        {"stage": stage, "film": str(film)}, raising=False)
    return folder, calls


@pytest.mark.parametrize("stage,expected", [("spot", ["spot"]), ("render", ["render"]),
                                           ("run", ["spot", "render"]), (None, ["spot", "render"])])
def test_sfx_stages_do_only_their_own_work_and_return_one_json(commands, stage, expected):
    folder, calls = commands
    argv = ["sfx"] + ([stage] if stage else []) + ["--film", str(folder), "--seed", "17", "--json"]
    code, out, err = run_cli(argv)
    assert code == 0, err
    result = json.loads(out)
    assert [c[0] for c in calls] == expected
    assert result["stage"] == (stage or "run")
    if "spot" in expected:
        assert calls[0][2] == result["seed"] == 17
        assert result["cue_sheet"].endswith("sound/sound_cues.json")
    if "render" in expected:
        assert result["render"] == {"stems": ["ui", "fx"]}


@pytest.mark.parametrize("stage", ["spot", "render", "run"])
def test_sfx_dry_run_never_queues_or_executes(commands, monkeypatch, stage):
    from slate.jobs import client

    folder, calls = commands
    monkeypatch.setattr(client, "detach_current", lambda *a, **k: pytest.fail("A dry run queued a job."))
    monkeypatch.setattr(effects, "require_proof3", lambda: pytest.fail("A plan entered the execution gate."))
    before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    code, out, err = run_cli(["sfx", stage, "--film", str(folder), "--dry-run", "--detach", "--json"])
    assert code == 0, err
    assert json.loads(out)["dry_run"] is True
    assert calls == [("plan", folder.resolve(), stage)]
    assert before == {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("stage", ["spot", "render", "run"])
def test_detach_preserves_stage_seed_and_render_resource(commands, monkeypatch, stage):
    from slate.jobs import client

    folder, calls = commands
    submitted = []
    monkeypatch.setattr(client, "detach_current", lambda *a, **k: submitted.append((a, k)) or "j-20261004-120000-ab12")
    code, out, err = run_cli(["sfx", stage, "--film", str(folder), "--seed", "-3", "--detach", "--json"])
    assert code == 0, err
    assert not calls
    assert json.loads(out)["queued"] is True
    args, kwargs = submitted[0]
    assert args == ("sfx", ("render",))
    assert kwargs["argv"] == ["sfx", stage, "--film", str(folder.resolve()), "--seed", "-3", "--json"]
    assert kwargs["film"] == str(folder.resolve())


def test_status_needs_no_film_and_reports_the_gate(slate_home):
    code, out, err = run_cli(["sfx", "status", "--json"])
    assert code == 0, err
    assert json.loads(out)["approved"] is False
