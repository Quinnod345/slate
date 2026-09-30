"""The report itself: the §12 interface, the verdict, crashed checks, groups, and off-macOS."""

from __future__ import annotations

import json

import pytest

from slate import doctor
from slate.doctor import core
from slate.doctor import rows as R
from slate.errors import UsageError

from doctor_helpers import FakeEnv, by_id, statuses


def test_a_fully_set_up_mac_is_all_green(denv):
    rows = core.checks(env=denv)
    bad = {r["id"]: (r["status"], r["summary"]) for r in rows if r["status"] != "green"}
    assert bad == {}
    assert R.overall(rows) == "green"


def test_checks_interface_shape(denv):
    rows = doctor.checks()  # docs/ARCHITECTURE.md §12: checks(context=None) -> list[dict]
    assert rows and all(isinstance(r, dict) for r in rows)
    for r in rows:
        assert {"id", "status", "summary", "fix"} <= set(r)
        assert r["status"] in ("green", "yellow", "red")
        assert isinstance(r["summary"], str) and r["summary"]
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "every row id is unique"


def test_report_shape_and_json(denv):
    rep = doctor.report("claude-code")
    assert {"status", "context", "checks", "counts", "detected", "host", "checked_at", "replay"} <= set(rep)
    assert rep["context"] == "claude-code"
    assert rep["status"] == "green"
    assert sum(rep["counts"].values()) == len(rep["checks"])
    json.dumps(rep)  # everything in it is JSON


def test_context_defaults_to_the_detected_agent(denv):
    denv.agent = "codex"
    assert doctor.report()["context"] == "codex"
    denv.agent = None
    assert doctor.report()["context"] == "terminal"


def test_overall_is_green_only_when_nothing_is_red_or_yellow():
    g = R.green("a", "fine", group="tools")
    y = R.yellow("b", "hmm", "fix it", group="tools")
    r = R.red("c", "broken", "fix it", group="tools")
    assert R.overall([g, g]) == "green"
    assert R.overall([g, y]) == "yellow"
    assert R.overall([g, y, r]) == "red"
    assert R.overall([]) == "yellow"  # nothing checked is never green
    assert R.exit_code("green") == 0 and R.exit_code("yellow") == 0 and R.exit_code("red") == 3
    assert R.exit_code("yellow", strict=True) == 1


def test_rows_refuse_unknown_status_or_group():
    with pytest.raises(ValueError):
        R.row("x", "blue", "?", group="tools")
    with pytest.raises(ValueError):
        R.row("x", "green", "?", group="nowhere")


def test_green_rows_never_carry_an_install_item():
    assert "install" not in R.green("x", "ok", group="tools", install={"id": "brew:x"})
    assert "install" in R.yellow("x", "hmm", group="tools", install={"id": "brew:x"})


def test_a_crashing_check_becomes_a_red_row_and_hides_nothing(denv, monkeypatch):
    real = core.registry()

    def boom(env, ctx):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(core, "registry", lambda: [("tools", "exploding", boom), *real])
    rows = core.checks(env=denv)
    row = by_id(rows)["doctor.exploding"]
    assert row["status"] == "red" and "probe exploded" in row["summary"]
    assert "tool.ffmpeg" in by_id(rows)  # the others still ran


def test_only_limits_groups_and_unknown_groups_are_refused(denv):
    rows = core.checks(only=["keys"], env=denv)
    assert rows and {r["group"] for r in rows} == {"keys"}
    with pytest.raises(UsageError):
        core.checks(only=["nope"], env=denv)


def test_parallel_and_serial_give_the_same_rows_in_the_same_order(denv):
    a = [r["id"] for r in core.checks(env=denv, parallel=True)]
    b = [r["id"] for r in core.checks(env=denv, parallel=False)]
    assert a == b


def test_off_macos_there_is_one_clear_red_row(tmp_path, slate_home):
    env = FakeEnv(tmp_path / "linux", sys="Linux", rel="6.8.0")
    rows = core.checks(env=env)
    assert statuses(rows) == {"host.macos": "red"}
    assert "macOS only" in rows[0]["summary"] and "Linux 6.8.0" in rows[0]["summary"]
    rep = core.report(env=env)
    assert rep["status"] == "red" and "macOS only" in rep["refused"]


def test_intel_mac_is_yellow(denv):
    denv.host_arch = "x86_64"
    denv.responses[("sysctl", "-n", "hw.optional.arm64")] = denv.responses[("sysctl", "-n", "hw.optional.arm64")]
    row = by_id(core.checks(only=["host"], env=denv))["host.macos"]
    assert row["status"] == "yellow" and "Apple silicon" in row["summary"]


def test_human_report_lists_fixes_under_problems(denv):
    denv.secrets.pop("elevenlabs")
    rep = core.report(env=denv)
    text = R.format_report(rep)
    assert "key.elevenlabs" in text and "fix: Run `slate connect elevenlabs`" in text
    assert text.strip().splitlines()[-2].startswith("overall: red") or "overall: red" in text


def test_every_registered_check_returns_a_list_of_rows(denv):
    ctx = core.make_ctx(denv)
    for group, name, fn in core.registry():
        out = fn(denv, ctx)
        assert isinstance(out, list), f"{name} returned {type(out).__name__}, not a list of rows"
        assert out and all(isinstance(r, dict) and r["group"] == group for r in out), name


def test_a_bare_row_is_kept_not_dropped(denv, monkeypatch):
    monkeypatch.setattr(core, "registry", lambda: [("tools", "bare", lambda env, ctx: R.green("x.bare", "one row",
                                                                                                group="tools"))])
    assert [r["id"] for r in core.checks(env=denv)] == ["x.bare"]
