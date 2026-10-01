"""The autonomy lock (GAPS-FINAL A1): a film made autonomous stays autonomous until a person at their own Terminal says
otherwise, whatever its film.json says.

- `slate new` records each film's mode in Slate's own data folder, outside the film folder;
- an agent's `slate film set mode supervised` is refused, and the attempt is recorded;
- a hand-edited film.json changes nothing: the ledger (caps, Oneiro's key, raises), the AE guard, master, the job queue
  and the film's own mode rules all still judge the film autonomous, even from a copy of its folder;
- a person at their own Terminal can change it, with a reason, and the change is recorded with who and why;
- a film derived from an autonomous one is autonomous too, unless that same person says otherwise.

The owner's own After Effects finish of such a film is tested with the finish fixtures
(tests/finish/test_finish_master_autonomy.py).
"""

from __future__ import annotations

import json
import os
import shutil
import stat

import pytest

from slate import ledger, procs
from slate.ae import guard
from slate.cli import run_cli
from slate.errors import Refused, SpendRefused, UsageError
from slate.finish import master
from slate.finish.film import Film
from slate.jobs import client, store
from slate.state import autonomy, derive, film, modes, production, status
from slate.vendors import elevenlabs


@pytest.fixture
def agent(monkeypatch):
    """An agent at the keyboard: never a person at a Terminal (what this suite's own runner is, too)."""
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (False, "this is running under test-agent"))
    monkeypatch.setattr(procs, "agent_context", lambda: "test-agent")


@pytest.fixture
def person(monkeypatch):
    """A person typing in their own Terminal."""
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (True, ""))
    monkeypatch.setattr(procs, "agent_context", lambda: None)


def _new_autonomous(state_env, film_id: str = "auto") -> tuple:
    code, out, err = run_cli(["new", film_id, "--brand", "onboarded-brand", "--format", "fixture-reel", "--mode",
                              "autonomous", "--no-git", "--json"])
    assert code == 0, err
    fd = (state_env["films"] / film_id).resolve()
    return fd, json.loads(out)


def _events() -> list[dict]:
    return autonomy.events()


def _hand_edit(fd, mode: str = "supervised") -> None:
    data = json.loads((fd / "film.json").read_text())
    data["mode"] = mode
    (fd / "film.json").write_text(json.dumps(data, indent=1))  # an agent editing the file directly


# ---------------------------------------------------------------------------------------------------------------
# The record


def test_slate_new_records_the_mode_in_slates_own_data_folder(state_env):
    fd, out = _new_autonomous(state_env)
    record = autonomy.path()
    assert record.is_file() and record.parent == state_env["data"]
    assert fd not in record.parents  # outside the film folder, where the film's own caller can't reach it by accident
    assert stat.S_IMODE(os.stat(record).st_mode) == 0o600
    created = [e for e in _events() if e["event"] == "created" and e["film"] == "auto"]
    assert len(created) == 1 and created[0]["mode"] == "autonomous" and created[0]["dir"] == str(fd)
    assert created[0]["created_at"] == film.load(fd)["created_at"]
    sup = (state_env["films"] / "sup").resolve()
    film.create("sup", brand="open-brand", format="fixture-explainer", git=False)
    assert autonomy.state(sup)["mode"] == "supervised" and not autonomy.is_locked(sup)
    assert autonomy.describe(fd)["locked"] and not autonomy.describe(fd)["mismatch"]


# ---------------------------------------------------------------------------------------------------------------
# An agent's flip


def test_an_agents_flip_is_refused_and_recorded(state_env, agent):
    fd, _ = _new_autonomous(state_env)
    code, out, err = run_cli(["film", "set", "mode", "supervised", "--film", str(fd), "--reason", "faster", "--json"])
    assert code == 1
    refusal = json.loads(out)
    assert refusal["error"] == "refused" and "only a person at their own Terminal" in refusal["message"]
    assert "slate film set mode supervised" in refusal["hint"]
    assert film.load(fd)["mode"] == "autonomous"
    with pytest.raises(Refused):  # the critic's probe called film.set_value directly: refused the same way
        film.set_value(fd, "mode", "supervised")
    with pytest.raises(Refused):  # every mode away from autonomous, not just supervised
        film.set_value(fd, "mode", "draft", reason="cheaper")
    refused = [e for e in _events() if e["event"] == "refused"]
    assert len(refused) == 3 and {e["to"] for e in refused} == {"supervised", "draft"}
    assert all(e["context"] == "test-agent" and e["dir"] == str(fd) for e in refused)
    assert refused[0]["why"] == "faster"  # who tried, and the reason they gave
    st = status.compute(fd)
    assert st["mode"] == "autonomous" and st["mode_lock"]["refused_changes"] == 3
    assert "attempt(s) to change its mode were refused" in status.format_human(st)
    code, out, _ = run_cli(["film", "set", "mode", "supervised", "--film", str(fd), "--dry-run", "--json"])
    assert code == 0 and json.loads(out)["would"] == "refuse"
    assert len([e for e in _events() if e["event"] == "refused"]) == 3  # a dry run records nothing
    # Tightening is anyone's: an agent may make a film autonomous (with an onboarded brand), and it locks.
    sup = film.create("sup", brand="onboarded-brand", format="fixture-reel", git=False)
    film.set_value(sup, "mode", "autonomous")
    assert autonomy.is_locked(sup)
    with pytest.raises(Refused):
        film.set_value(sup, "mode", "supervised", reason="changed my mind")


# ---------------------------------------------------------------------------------------------------------------
# A hand-edited film.json


def test_a_hand_edited_film_json_is_still_capped_and_refused_after_effects(state_env, agent, monkeypatch):
    fd, _ = _new_autonomous(state_env)
    _hand_edit(fd, "supervised")
    fj = film.load(fd)
    assert fj["mode"] == "supervised"
    # every judge reads Slate's record, not the file
    assert film.mode_of(fd) == "autonomous" and autonomy.film_mode(fd) == "autonomous"
    assert ledger.effective_mode(None, fj, film_dir=fd) == "autonomous"
    assert ledger.effective_mode("supervised", fj) == "autonomous"  # a film.json dict alone is found too
    assert ledger.film_mode(fd, "supervised") == "autonomous"
    assert guard.film_mode(fd) == "autonomous"
    assert master.film_mode(Film(fd)) == "autonomous"

    # the caps: the monthly autonomous cap and a yes for every generation (real spend only; replay spends nothing)
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    with pytest.raises(SpendRefused, match="autonomous cap for elevenlabs"):
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=25000), film=fd, mode="supervised")
    with pytest.raises(SpendRefused, match="wait for a person's yes"):
        ledger.check_budget(ledger.estimate("higgsfield", "generate", amount=1.0), film=fd, mode="supervised")
    with pytest.raises(SpendRefused, match="film.json says supervised"):
        ledger.raise_film_budget(fd, "elevenlabs", 5000, approved_by="Test Person")
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "1")
    # Oneiro's capped key, whatever mode the caller read from film.json
    voice = elevenlabs.Client(mode="supervised", film=fd)
    assert voice.mode == "autonomous" and voice.key_name == "elevenlabs_oneiro"

    # After Effects: the guard, master, the film's own rules and the queue all refuse
    with pytest.raises(Refused, match="After Effects"):
        guard.person_start(True, "Mastering", film_dir=fd, action="ae_master")
    with pytest.raises(Refused, match="never use After Effects"):
        master.choose(Film(fd), ae=True)
    assert master.choose(Film(fd))[0] == "ffmpeg"
    with pytest.raises(Refused, match="Slate's own record holds it autonomous"):
        modes.check(fd, "ae_build")
    with pytest.raises(SpendRefused):
        modes.check(fd, "generate")  # every generation still waits for a person's yes
    with pytest.raises(Refused, match="Adobe"):
        client.submit("master", ["slate", "master", "--ae", "--go"], film=fd, resources=("ae",), start_runner=False)
    job = store.load(client.submit("render", ["true"], film=fd, priority="supervised", start_runner=False))
    assert job["priority"] == "autonomous" and job["film_mode"] == "autonomous"
    code, out, err = run_cli(["master", "--ae", "--go", "--film", str(fd)])
    assert code == 1 and "never use After Effects" in err

    # status says so, with the way back
    st = status.compute(fd)
    assert st["mode"] == "autonomous" and st["mode_lock"]["mismatch"]
    assert "Put it back: slate film set mode autonomous" in status.format_human(st)
    film.set_value(fd, "mode", "autonomous")  # putting it back is anyone's
    assert film.load(fd)["mode"] == "autonomous" and not autonomy.describe(fd)["mismatch"]


def test_a_moved_copy_of_an_autonomous_film_is_still_autonomous(state_env, agent):
    fd, _ = _new_autonomous(state_env)
    elsewhere = state_env["tmp"] / "elsewhere" / "auto-copy"
    shutil.copytree(fd, elsewhere)
    _hand_edit(elsewhere, "supervised")
    assert autonomy.film_mode(elsewhere) == "autonomous"  # found by its id and creation stamp
    with pytest.raises(Refused):
        guard.person_start(True, "Building", film_dir=elsewhere, action="ae_build")
    with pytest.raises(Refused):
        film.set_value(elsewhere, "mode", "supervised", reason="new folder, new rules")
    # a new film that merely reuses the id is its own film: no false match
    other = film.create("auto", brand="open-brand", format="fixture-explainer", git=False,
                        films_dir=state_env["tmp"] / "other-films")
    assert autonomy.film_mode(other) == "supervised"


# ---------------------------------------------------------------------------------------------------------------
# The person's path


def test_a_person_at_their_terminal_changes_the_mode_with_who_and_why(state_env, person):
    fd, _ = _new_autonomous(state_env)
    code, out, err = run_cli(["film", "set", "mode", "supervised", "--film", str(fd), "--json"])
    assert code == 2 and "--reason" in json.loads(out)["message"]  # who and why are recorded, so why is required
    assert autonomy.is_locked(fd)
    code, out, err = run_cli(["film", "set", "mode", "supervised", "--film", str(fd), "--by", "Test Person",
                              "--reason", "I'm taking this one over", "--json"])
    assert code == 0, err
    res = json.loads(out)
    assert res["new"] == "supervised" and res["recorded"]["by"] == "Test Person"
    assert res["recorded"]["why"] == "I'm taking this one over"
    changed = [e for e in _events() if e["event"] == "changed"]
    assert len(changed) == 1 and changed[0]["from"] == "autonomous" and changed[0]["to"] == "supervised"
    assert changed[0]["context"] == "terminal" and changed[0]["by"] == "Test Person"
    assert not autonomy.is_locked(fd) and film.mode_of(fd) == "supervised"
    assert ledger.effective_mode(None, film.load(fd), film_dir=fd) == "supervised"
    directions = production.load_brief(fd)["directions"]
    assert directions[-1]["kind"] == "mode change" and directions[-1]["source"] == "Test Person"
    assert "from autonomous to supervised: I'm taking this one over" in directions[-1]["text"]
    assert "Mode changed from autonomous to supervised" in (fd / "brief" / "PRODUCTION.md").read_text()
    modes.check(fd, "ae_build")  # a supervised film's AE steps start on a person's go, as always


def test_a_film_derived_from_an_autonomous_one_is_autonomous_too(state_env, monkeypatch):
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (False, "this is running under test-agent"))
    parent, _ = _new_autonomous(state_env, "auto-parent")
    _hand_edit(parent, "supervised")  # the parent's file says supervised; its record doesn't
    child = derive.create(parent, "auto-cut", cut=10)
    assert film.load(child)["mode"] == "autonomous" and autonomy.is_locked(child)
    created = [e for e in _events() if e["event"] == "created" and e["film"] == "auto-cut"]
    assert created[0]["parent"]["id"] == "auto-parent" and created[0]["parent"]["mode"] == "autonomous"
    with pytest.raises(Refused, match="only a person at their own Terminal"):
        derive.create(parent, "sup-cut", cut=12, mode="supervised", reason="faster")
    code, out, err = run_cli(["variant", str(parent), "sup-cut2", "--cut", "12", "--mode", "draft", "--json"])
    assert code == 1
    monkeypatch.setattr(ledger, "person_at_terminal", lambda: (True, ""))
    with pytest.raises(UsageError, match="--reason"):
        derive.create(parent, "sup-cut3", cut=12, mode="supervised")
    ok = derive.create(parent, "sup-cut4", cut=12, mode="supervised", by="Test Person", reason="a supervised cut")
    rec = autonomy.state(ok)
    assert rec["mode"] == "supervised" and rec["why"] == "a supervised cut" and rec["by"] == "Test Person"


def test_every_module_that_routes_by_mode_judges_a_hand_edited_film_by_the_record(state_env, agent):
    """The modules that still read film.json's mode for their own routing (G1's open item) ask the record now: the
    script's claims stay approved-only, an AE render is refused, the voice and sound stages and QA's parking see the
    film as autonomous, and a queued render keeps the autonomous priority."""
    from slate.qa import film as qa_film
    from slate.render import engines
    from slate.sound import film as sound_film
    from slate.story import gates as story_gates
    from slate.voice.film import VoiceFilm

    fd, _ = _new_autonomous(state_env, "routed")
    _hand_edit(fd, "supervised")
    fj = film.load(fd)
    assert fj["mode"] == "supervised"
    assert story_gates.judged_mode(fj) == "autonomous"  # found by its id and creation stamp, no folder needed
    assert engines._judged_mode(fd, fj) == "autonomous"  # noqa: SLF001
    with pytest.raises(Refused, match="never touch After Effects"):
        engines.AeEngine(fd, fj, go=True).rules()
    assert VoiceFilm(fd).mode == "autonomous"
    sf = sound_film.Film(dir=fd, data=fj, shots={}, fps_num=30, fps_den=1, sr=48000, duration_s=1.0, targets={},
                         brand=None, palette={}, fmt=None)
    assert sf.mode == "autonomous"
    assert qa_film.context(fd).mode == "autonomous"
    # a film the record doesn't know keeps its own mode everywhere
    other = dict(fj, id="unknown-film", created_at="2020-01-01T00:00:00+00:00", mode="supervised")
    assert story_gates.judged_mode(other) == "supervised"
