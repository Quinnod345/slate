"""The report channel (read_ae_report.py port) and the person-start guard."""

from __future__ import annotations

import json

import pytest

from slate import procs
from slate.ae import guard, report
from slate.errors import Busy, Refused

from ae_helpers import prefs_bytes, settings_entry


def _with_report(entries: dict[str, str]) -> bytes:
    return prefs_bytes(report=[settings_entry(k, v) for k, v in entries.items()])


# ---- decoding ---------------------------------------------------------------------------------------------------


def test_decode_joins_quoted_runs_and_hex_bytes_once():
    assert report.decode_value('"a"C2B7"b"') == "a·b"  # not "aÂ·b"
    assert report.decode_value('"say "22"hi"22""') == 'say "hi"'
    assert report.decode_value("0A1B") == "\n\x1b"


def test_section_values_joins_wrapped_lines_and_reads_the_last_section():
    data = prefs_bytes(report=[b'\t"run.long" = "aaaa"\\', b'\t\t"bbbb"', b'\t"run.x" = "1"'])
    vals = report.section_values(data, "Slate", "run.")
    assert vals == {"run.long": "aaaabbbb", "run.x": "1"}
    assert report.section_values(prefs_bytes(), "Slate") == {}


def test_read_uses_the_newest_prefs_folder_by_number(live_prefs):
    live_prefs("26.5", _with_report({"run.id": "old"}))
    live_prefs("26.10", _with_report({"run.id": "new"}))
    assert report.read("Slate", "run.")["run.id"] == "new"


# ---- runs -------------------------------------------------------------------------------------------------------


def test_read_run_round_trips_a_chunked_json_report(live_prefs):
    payload = {"comps": [{"name": f"S{i:02d} · café", "frames": i * 10} for i in range(80)], "ok": True}
    entries = report.encode_for_settings(payload, run_id="r42", status="OK", chunk=200)
    assert int(entries["run.json.n"]) > 3
    live_prefs("26.5", _with_report(entries))
    got = report.read_run("r42")
    assert got["status"] == "OK" and got["data"] == payload and got["channel"] == "settings"


def test_read_run_ignores_a_stale_report(live_prefs):
    live_prefs("26.5", _with_report(report.encode_for_settings({"a": 1}, run_id="older", status="OK")))
    got = report.read_run("mine")
    assert got["status"] == "MISSING" and got["seen_id"] == "older"


def test_read_run_prefers_the_file_channel(tmp_path, live_prefs):
    live_prefs("26.5", _with_report(report.encode_for_settings({"via": "settings"}, run_id="r1", status="OK")))
    f = tmp_path / "report.json"
    f.write_text(json.dumps({"run_id": "r1", "status": "OK", "data": {"via": "file"}}))
    got = report.read_run("r1", report_file=f)
    assert got["data"] == {"via": "file"} and got["channel"] == "file"


def test_read_run_without_any_prefs_file_is_missing():
    assert report.read_run("r1")["status"] == "MISSING"


# ---- the guard ----------------------------------------------------------------------------------------------------


def _film(tmp_path, mode="supervised"):
    d = tmp_path / "film"
    d.mkdir(parents=True)
    (d / "film.json").write_text(json.dumps({"id": "f", "mode": mode}))
    return d


def test_go_is_required(tmp_path):
    with pytest.raises(Refused) as exc:
        guard.person_start(False, "Building the AE project", film_dir=_film(tmp_path))
    assert exc.value.exit_code == 1 and "--go" in exc.value.hint
    guard.person_start(True, "Building the AE project", film_dir=_film(tmp_path / "b"))


def test_autonomous_films_never_touch_ae_even_with_go(tmp_path):
    with pytest.raises(Refused) as exc:
        guard.person_start(True, "Mastering", film_dir=_film(tmp_path, "autonomous"), action="ae_master")
    assert exc.value.exit_code == 1
    assert "utonomous" in exc.value.message and "After Effects" in exc.value.message


def test_autonomous_refusal_without_the_state_module(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "slate.state", None)  # the state module isn't installed
    with pytest.raises(Refused) as exc:
        guard.person_start(True, "Mastering", film_dir=_film(tmp_path, "autonomous"))
    assert "PSLT" in exc.value.hint and "autonomous" in exc.value.message
    guard.person_start(True, "Mastering", film_dir=_film(tmp_path / "ok", "supervised"))


def test_autonomous_environment_refuses_filmless_commands(monkeypatch):
    monkeypatch.setenv("SLATE_MODE", "autonomous")
    with pytest.raises(Refused):
        guard.person_start(True, "The smoke test")


def test_state_modes_check_is_consulted_when_present(tmp_path, monkeypatch):
    import sys
    import types

    fake = types.ModuleType("slate.state.modes")
    seen = {}

    def check(film_dir, action):
        seen["args"] = (film_dir, action)
        return (False, "held for the person")

    fake.check = check
    pkg = types.ModuleType("slate.state")
    pkg.modes = fake
    monkeypatch.setitem(sys.modules, "slate.state", pkg)
    monkeypatch.setitem(sys.modules, "slate.state.modes", fake)
    film = _film(tmp_path)
    with pytest.raises(Refused, match="held for the person"):
        guard.person_start(True, "Mastering", film_dir=film)
    assert seen["args"] == (film, "ae")


def test_require_ae_closed_is_busy_with_the_exact_ask(monkeypatch):
    guard.require_ae_closed("mastering")
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    with pytest.raises(Busy) as exc:
        guard.require_ae_closed("mastering")
    assert exc.value.exit_code == 5
    assert "save your work in After Effects and quit it, then run this again" in exc.value.message
    monkeypatch.setattr(procs, "ae_running", lambda: False)
    monkeypatch.setattr(procs, "aerender_running", lambda: True)
    with pytest.raises(Busy):
        guard.require_ae_closed("mastering")
    guard.require_ae_closed("mastering", allow_aerender=True)


# ---- AE's launch-time dialogs (L-ae-048) -------------------------------------------------------------------------


def _dialogs(monkeypatch, titles, *, running=True, readable=True):
    """AE running (or not) with these window titles, read through doctor's launch_dialogs as a supervised step does."""
    from slate.doctor import aftereffects
    from slate.doctor.env import Env

    class FakeEnv(Env):
        def ae_running(self):
            return running

        def ae_windows(self):
            return {"running": running, "windows": list(titles) if readable else None,
                    "error": None if readable else "no Accessibility permission"}

        def ae_bridge_status(self):
            return {}

    monkeypatch.setattr(procs, "ae_running", lambda: running)
    asked = []
    monkeypatch.setattr(guard, "launch_dialogs", lambda: asked.append(1) or aftereffects.launch_dialogs(FakeEnv()))
    return asked


def test_a_supervised_step_stops_on_a_launch_dialog(tmp_path, monkeypatch):
    """Every supervised AE step runs doctor's launch_dialogs() once the person said go, and stops (exit 5) on a dialog
    a script can't dismiss, naming it and saying what the person does."""
    asked = _dialogs(monkeypatch, ["Adobe After Effects 2026 - Untitled Project.aep", "Missing Fonts"])
    with pytest.raises(Busy) as exc:
        guard.person_start(True, "Building the AE project", film_dir=_film(tmp_path), action="ae_build")
    assert asked == [1] and exc.value.exit_code == 5
    assert "a missing-fonts dialog" in exc.value.message and "Building the AE project would hang" in exc.value.message
    assert "Install or replace the fonts" in exc.value.hint
    assert exc.value.details["dialogs"] == [{"kind": "missing_fonts", "title": "Missing Fonts"}]


def test_a_dialog_is_only_looked_for_once_the_person_said_go(tmp_path, monkeypatch):
    asked = _dialogs(monkeypatch, ["Restore project?"])
    with pytest.raises(Refused):
        guard.person_start(False, "Building the AE project", film_dir=_film(tmp_path))
    assert asked == []  # a refused step never reaches into System Events


def test_no_dialog_open_or_ae_closed_lets_the_step_start(tmp_path, monkeypatch):
    _dialogs(monkeypatch, ["Adobe After Effects 2026 - Untitled Project.aep", "Composition: Act 1"])
    guard.person_start(True, "Building the AE project", film_dir=_film(tmp_path))
    asked = _dialogs(monkeypatch, [], running=False)
    guard.person_start(True, "Rendering the masters with aerender", film_dir=_film(tmp_path / "b"), action="ae_master")
    assert asked == []  # AE closed: nothing is asked of System Events at all


def test_unreadable_windows_warn_and_let_the_person_go_on(tmp_path, monkeypatch, capsys):
    _dialogs(monkeypatch, [], readable=False)
    guard.person_start(True, "Building the AE project", film_dir=_film(tmp_path))
    assert "Couldn't read After Effects' windows" in capsys.readouterr().err
