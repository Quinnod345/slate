"""`slate doctor` on the command line: help, --json, exit codes, --context, --install."""

from __future__ import annotations

import json

import pytest

from slate.cli import run_cli
from slate.doctor import prereqs

from doctor_helpers import FakeEnv, by_id


@pytest.mark.parametrize("argv", [["doctor", "--help"], ["connect", "--help"], ["connect", "claude", "--help"],
                                  ["connect", "higgsfield", "--help"], ["connect", "elevenlabs", "--help"],
                                  ["connect", "aftereffects", "--help"]])
def test_help(argv):
    code, out, err = run_cli(argv)
    assert code == 0 and "usage: slate" in out


def test_json_is_one_object_with_the_report_shape(denv):
    code, out, err = run_cli(["doctor", "--json"])
    assert code == 0
    rep = json.loads(out)  # exactly one JSON object on stdout
    assert rep["status"] == "green" and isinstance(rep["checks"], list) and rep["context"] == "terminal"
    assert out.count("\n") == 1


def test_red_exits_3_yellow_exits_0_or_1_with_strict(denv):
    denv.tools["gh"] = None  # yellow
    code, out, _ = run_cli(["doctor", "--json"])
    assert code == 0 and json.loads(out)["status"] == "yellow"
    code, _, _ = run_cli(["doctor", "--json", "--strict"])
    assert code == 1
    denv.secrets.pop("elevenlabs")  # red
    code, out, _ = run_cli(["doctor", "--json"])
    assert code == 3 and json.loads(out)["status"] == "red"


def test_human_output(denv):
    denv.tools["gh"] = None
    code, out, err = run_cli(["doctor"])
    assert code == 0 and "overall: yellow" in out and "tool.gh" in out and "fix: brew install gh" in out


def test_context_saves_the_run(denv, slate_home):
    code, out, _ = run_cli(["doctor", "--json", "--context", "pursuit-slice"])
    rep = json.loads(out)
    saved = slate_home["data"] / "doctor" / "pursuit-slice.json"
    assert rep["context"] == "pursuit-slice" and rep["saved"] == str(saved)
    on_disk = json.loads(saved.read_text())
    assert on_disk["context"] == "pursuit-slice" and on_disk["checks"] == rep["checks"]


def test_the_format_is_the_same_from_every_context(denv):
    shapes = []
    for name, agent in (("claude-code", "claude-code"), ("pursuit-slice", "codex"), ("openclaw-gateway", "openclaw")):
        denv.agent = agent
        _, out, _ = run_cli(["doctor", "--json", "--context", name])
        rep = json.loads(out)
        shapes.append((sorted(rep), [r["id"] for r in rep["checks"]]))
        assert rep["detected"] == agent
    assert shapes[0] == shapes[1] == shapes[2]


def test_a_bad_context_name_is_a_usage_error(denv):
    code, out, err = run_cli(["doctor", "--context", "../escape"])
    assert code == 2 and "context name" in err


def test_unknown_brand_is_a_usage_error(denv):
    code, out, err = run_cli(["doctor", "--brand", "no-such-brand"])
    assert code == 2


def test_off_macos_is_refused_with_a_clear_message(tmp_path, slate_home, monkeypatch):
    from slate.doctor import core

    env = FakeEnv(tmp_path / "linux", sys="Linux")
    monkeypatch.setattr(core, "make_env", lambda: env)
    code, out, err = run_cli(["doctor", "--json"])
    rep = json.loads(out)
    assert code == 1 and "macOS only" in rep["refused"] and rep["status"] == "red"


# ---------------------------------------------------------------------------------------------------------------
# --install


def missing_things(denv):
    denv.tools["potrace"] = None                      # yellow, brew item
    denv.tools["gh"] = None                           # yellow, brew item
    denv.sizes.clear()                                # red, the Whisper model


def test_install_from_an_agent_prints_the_commands_and_exits_3(denv, monkeypatch):
    missing_things(denv)
    denv.agent = "claude-code"
    ran = []
    monkeypatch.setattr(prereqs, "carry_out", lambda item, env: ran.append(item) or {"ok": True})
    code, out, err = run_cli(["doctor", "--install", "--json"])
    rep = json.loads(out)
    assert code == 3 and ran == []
    items = {i["id"]: i for i in rep["install"]["items"]}
    assert {"brew:potrace", "brew:gh", "whisper-model"} <= set(items)
    assert items["brew:gh"]["command"] == "/opt/homebrew/bin/brew install gh"
    assert rep["install"]["ran"] is False and "slate doctor --install" in rep["install"]["next"]


def test_install_human_text_from_an_agent(denv, monkeypatch):
    missing_things(denv)
    monkeypatch.setattr(prereqs, "carry_out", lambda item, env: pytest.fail("nothing may run for an agent"))
    code, out, err = run_cli(["doctor", "--install"])
    assert code == 3 and "Nothing was installed" in out and "/opt/homebrew/bin/brew install gh" in out


def test_install_dry_run_runs_nothing(denv, monkeypatch):
    missing_things(denv)
    denv.terminal = (True, "")
    monkeypatch.setattr(prereqs, "carry_out", lambda item, env: pytest.fail("a dry run installs nothing"))
    code, out, err = run_cli(["doctor", "--install", "--dry-run", "--json"])
    assert code == 0 and json.loads(out)["install"]["why"] == "dry run"


def test_install_in_a_terminal_asks_each_and_runs_only_the_yeses(denv, monkeypatch):
    missing_things(denv)
    denv.terminal = (True, "")
    asked, ran = [], []

    def ask(prompt):
        asked.append(prompt)
        return "y" if "gh" in prompt else "n"

    monkeypatch.setattr(prereqs, "_ask_tty", ask)

    def fake_carry_out(item, env):
        ran.append(item["id"])
        denv.tools["gh"] = "/opt/homebrew/bin/gh"  # "installed"
        return {"id": item["id"], "ok": True}

    monkeypatch.setattr(prereqs, "carry_out", fake_carry_out)
    code, out, err = run_cli(["doctor", "--install", "--json"])
    rep = json.loads(out)
    assert ran == ["brew:gh"] and len(asked) == 3
    assert all("[y/N]" in p for p in asked)
    assert by_id(rep["checks"])["tool.gh"]["status"] == "green"  # the report after is a fresh check
    assert rep["install"]["ran"] is True
    results = {r["id"]: r for r in rep["install"]["results"]}
    assert results["brew:potrace"]["skipped"] and results["whisper-model"]["skipped"]


def test_install_with_nothing_to_do(denv):
    denv.terminal = (True, "")
    code, out, err = run_cli(["doctor", "--install", "--json"])
    assert code == 0 and json.loads(out)["install"]["items"] == []


def test_install_plan_orders_and_merges_items():
    rows = [
        {"id": "a", "status": "red", "install": {"id": "chrome", "what": "Chrome", "call": "chrome"}},
        {"id": "b", "status": "red", "install": {"id": "engine", "what": "Engine", "call": "engine"}},
        {"id": "c", "status": "red", "install": {"id": "brew:ffmpeg", "what": "ffmpeg", "argv": ["brew"]}},
        {"id": "d", "status": "red", "install": {"id": "brew:ffmpeg", "what": "ffmpeg", "argv": ["brew"]}},
        {"id": "e", "status": "green", "install": {"id": "brew:gh", "what": "gh", "argv": ["brew"]}},
    ]
    plan = prereqs.plan(rows)
    assert [i["id"] for i in plan] == ["brew:ffmpeg", "engine", "chrome"]
    assert plan[0]["for"] == ["c", "d"]


def test_carry_out_refuses_items_that_cant_run_yet(denv):
    res = prereqs.carry_out({"id": "brew:x", "what": "x", "argv": ["brew", "install", "x"],
                             "needs": "Homebrew isn't installed."}, denv)
    assert res == {"id": "brew:x", "ok": False, "error": "Homebrew isn't installed."}


def test_carry_out_runs_argv_items_through_procs(denv, monkeypatch):
    from slate import procs

    seen = []

    class P:
        returncode = 0

    monkeypatch.setattr(procs, "run", lambda argv, **kw: seen.append((argv, kw)) or P())
    res = prereqs.carry_out({"id": "brew:gh", "what": "gh", "argv": ["/opt/homebrew/bin/brew", "install", "gh"]}, denv)
    assert res["ok"] is True and seen[0][0] == ["/opt/homebrew/bin/brew", "install", "gh"]
    assert seen[0][1]["capture"] is False  # the person watches brew's own output


def test_whisper_model_install_runs_whisper_on_a_silent_clip(denv, monkeypatch):
    from slate import procs

    seen = []
    monkeypatch.setattr(procs, "run", lambda argv, **kw: seen.append(argv))
    res = prereqs.carry_out({"id": "whisper-model", "what": "model", "call": "whisper-model"}, denv)
    assert res["ok"] is True
    argv = seen[0]
    assert argv[0] == "/opt/homebrew/bin/whisper" and argv[argv.index("--model") + 1] == "turbo"
    assert argv[1].endswith("silence.wav")


def test_the_engine_install_also_fetches_its_chrome(denv, monkeypatch):
    import slate.engine.bridge as ebridge
    import slate.engine.install as einstall

    done = []
    monkeypatch.setattr(einstall, "ensure", lambda **kw: done.append(("ensure", kw)) or denv.engine_folder)
    monkeypatch.setattr(ebridge, "call", lambda cmd, args, **kw: done.append((cmd, args)) or {"browser": {"major": 149}})
    res = prereqs.carry_out({"id": "engine", "what": "engine", "call": "engine"}, denv)
    assert res["ok"] is True and done == [("ensure", {"npm_ci": True}), ("browser-ensure", {})]
