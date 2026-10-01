"""`slate ae …` through the real dispatcher: help, --json output and the shared exit codes (0 ok, 1 refused,
2 usage, 3 missing prerequisite, 5 busy). Commands that would start After Effects are only exercised up to their
refusals and dry runs here; their work is tested with fakes in the other files."""

from __future__ import annotations

import json

import pytest

from slate import procs
from slate.ae import audit, prefs
from slate.cli import run_cli

from ae_helpers import make_film, prefs_bytes


def _json(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


@pytest.mark.parametrize("argv", [["ae", "--help"], ["ae", "prefs", "--help"], ["ae", "prefs", "set", "--help"],
                                  ["ae", "smoke", "--help"], ["ae", "build", "--help"], ["ae", "run", "--help"],
                                  ["ae", "masters", "--help"], ["ae", "audit", "--help"], ["ae", "acts", "--help"],
                                  ["ae", "bridge", "status", "--help"], ["ae", "bridge", "install", "--help"],
                                  ["ae", "bridge", "acceptance", "--help"], ["ae", "bridge", "looks", "--help"]])
def test_help(argv):
    code, out, err = run_cli(argv)
    assert code == 0 and "usage: slate ae" in out


def test_ae_alone_shows_its_stages():
    code, _out, err = run_cli(["ae"])
    assert code == 2 and "prefs" in err and "bridge" in err


# ---- prefs ----------------------------------------------------------------------------------------------------


def test_prefs_check_json_and_human(tmp_path):
    f = tmp_path / "p.txt"
    f.write_bytes(prefs_bytes())
    code, out, _ = run_cli(["ae", "prefs", "check", "--file", str(f), "--json"])
    assert code == 0 and _json(out)["scripting"] == "on" and _json(out)["lf"] == 0
    code, out, _ = run_cli(["ae", "prefs", "check", "--file", str(f)])
    assert code == 0 and "Allow Scripts to Write Files and Access Network: on" in out and "(ok)" in out
    f.write_bytes(prefs_bytes().replace(b"\r", b"\n"))
    code, out, _ = run_cli(["ae", "prefs", "check", "--file", str(f)])
    assert code == 1 and "DAMAGED" in out


def test_prefs_get_set_and_their_refusals(tmp_path):
    f = tmp_path / "p.txt"
    f.write_bytes(prefs_bytes())
    code, out, _ = run_cli(["ae", "prefs", "get", "Disk Cache Controls", "Enabled 2", "--file", str(f)])
    assert code == 0 and out.strip() == "1"
    code, out, _ = run_cli(["ae", "prefs", "get", "Disk Cache Controls", "Nope", "--file", str(f), "--json"])
    assert code == 1 and _json(out)["error"] == "refused"
    code, out, _ = run_cli(["ae", "prefs", "set", "Disk Cache Controls", "Enabled 2", "0", "--file", str(f), "--dry-run"])
    assert code == 0 and "dry run" in out and prefs.get("Disk Cache Controls", "Enabled 2", file=f) == "1"
    code, out, _ = run_cli(["ae", "prefs", "set", "Disk Cache Controls", "Enabled 2", "0", "--file", str(f)])
    assert code == 0 and '"1" -> "0"' in out and prefs.get("Disk Cache Controls", "Enabled 2", file=f) == "0"
    code, out, _ = run_cli(["ae", "prefs", "set", "Main Pref Section v2", "Pref_SCRIPTING_FILE_NETWORK_SECURITY", "0",
                            "--file", str(f), "--json"])
    assert code == 1 and "security setting" in _json(out)["message"]


def test_prefs_set_is_busy_while_ae_runs(live_prefs, monkeypatch):
    live_prefs()
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    code, out, _ = run_cli(["ae", "prefs", "set", "Disk Cache Controls", "Enabled 2", "0", "--json"])
    body = _json(out)
    assert code == 5 and body["error"] == "busy"
    assert "save your work in After Effects and quit it, then run this again" in body["message"]


def test_prefs_without_any_prefs_file_is_a_missing_prerequisite():
    code, out, _ = run_cli(["ae", "prefs", "check", "--json"])
    assert code == 3 and _json(out)["error"] == "missing_prerequisite"


def test_prefs_restore_dry_run(tmp_path):
    cur, old = tmp_path / "cur.txt", tmp_path / "old.txt"
    cur.write_bytes(b'["S"]\r\t"a" = "1"\r')
    old.write_bytes(b'["S"]\r\t"a" = "1"\r\t"b" = "2"\r')
    code, out, _ = run_cli(["ae", "prefs", "restore", "--from", str(old), "--file", str(cur)])
    assert code == 0 and "settings to add back: 1" in out and "dry run" in out
    assert cur.read_bytes() == b'["S"]\r\t"a" = "1"\r'


# ---- commands that start After Effects: refusals and dry runs only ---------------------------------------------


@pytest.fixture
def film(slate_home):
    d, _media = make_film(slate_home["films"], slate_home["brands"])
    return d


def test_every_ae_command_needs_go(film):
    for argv in (["ae", "smoke"], ["ae", "build", "--film", str(film)], ["ae", "masters", "--film", str(film)],
                 ["ae", "acts", "render", "2", "--film", str(film)], ["ae", "bridge", "acceptance"]):
        code, out, _ = run_cli(argv + ["--json"])
        body = _json(out)
        assert code == 1 and body["error"] == "refused" and "--go" in body["hint"], argv
    jsx = film / "x.jsx"
    jsx.write_text("1;")
    code, out, _ = run_cli(["ae", "run", str(jsx), "--json"])
    assert code == 1 and "--go" in _json(out)["hint"]


def test_autonomous_films_are_refused_even_with_go(slate_home):
    auto, _ = make_film(slate_home["films"], slate_home["brands"], "auto", mode="autonomous")
    for argv in (["ae", "build", "--go"], ["ae", "masters", "--go"]):
        code, out, _ = run_cli(argv + ["--film", str(auto), "--json"])
        body = _json(out)
        assert code == 1 and body["error"] == "refused" and "utonomous" in body["message"], argv


def test_masters_is_busy_while_ae_runs(film, monkeypatch):
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    code, out, _ = run_cli(["ae", "masters", "--go", "--film", str(film), "--json"])
    assert code == 5 and _json(out)["error"] == "busy"


def test_dry_runs_change_nothing(film):
    code, out, _ = run_cli(["ae", "build", "--dry-run", "--film", str(film), "--json"])
    body = _json(out)
    assert code == 0 and body["dry_run"] and body["acts"] == [[1, "render"], [2, "ae"], [3, "render"]]
    assert body["masters_chunks"] == 8 and not (film / "ae" / "build_spec.json").exists()
    code, out, _ = run_cli(["ae", "smoke", "--dry-run", "--json", "--size", "640x360", "--fps", "25"])
    p = _json(out)["params"]
    assert code == 0 and (p["width"], p["height"], p["fps_num"], p["frames"]) == (640, 360, 25, 25)
    code, out, _ = run_cli(["ae", "run", str(film / "film.json"), "--dry-run", "--json"])
    assert code == 0 and _json(out)["argv"][0] == "/usr/bin/osascript"


def test_run_usage_errors(film):
    code, out, _ = run_cli(["ae", "run", str(film / "film.json"), "--go", "--args", "{nope", "--json"])
    assert code == 2 and _json(out)["error"] == "usage"
    code, out, _ = run_cli(["ae", "smoke", "--dry-run", "--size", "big", "--json"])
    assert code == 2


def test_audit_from_a_saved_report(film, tmp_path):
    rep = tmp_path / "v.json"
    rep.write_text(json.dumps({"checks": [{"check": c, "status": "PASS", "evidence": "ok"} for c in audit.CHECKS]}))
    code, out, _ = run_cli(["ae", "audit", str(tmp_path / "f.aep"), "--report", str(rep), "--film", str(film),
                            "--json"])
    body = _json(out)
    assert code == 0 and body["summary"]["status"] == "PASS" and len(body["gates"]) == len(audit.CHECKS)
    code, out, _ = run_cli(["ae", "audit", "--film", str(film), "--json"])
    assert code == 3 and _json(out)["hint"] == "slate ae build --go"  # nothing built yet, so nothing to audit


def test_acts_export(film):
    code, out, _ = run_cli(["ae", "acts", "export", "--film", str(film), "--json"])
    body = _json(out)
    assert code == 0 and body["exported"][0]["act"] == 2 and body["exported"][0]["beats"] == 1


# ---- the bridge -----------------------------------------------------------------------------------------------


def test_bridge_status_install_plan_and_looks(monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_AE_BRIDGE_DIR", str(tmp_path / "none"))
    from slate.ae import bridge

    monkeypatch.setattr(bridge, "GLOBAL_ROOTS", (str(tmp_path / "none"),))
    code, out, _ = run_cli(["ae", "bridge", "status", "--json"])
    st = _json(out)
    assert code == 0 and st["installed"] is False and st["route"] == "slate-es3" and st["pinned"] == "0.1.3"
    code, out, _ = run_cli(["ae", "bridge", "status"])
    assert code == 0 and "Slate's own ES3 library" in out and "never changes it" in out
    code, out, _ = run_cli(["ae", "bridge", "install", "--json"])
    assert code == 0 and _json(out)["ran"] is False
    assert _json(out)["plan"]["install"][-1] == "fnf-after-effects-mcp@0.1.3"
    code, out, _ = run_cli(["ae", "bridge", "acceptance", "--plan", "--json"])
    assert code == 0 and _json(out)["script"]["ops"][0]["tool"] == "ae_catalog"
    code, out, _ = run_cli(["ae", "bridge", "looks", "--json"])
    assert code == 0 and len(_json(out)["looks"]) == 13


# ---- --detach: long AE runs through the job queue (review addition) -------------------------------------------


def test_masters_and_act_renders_detach_into_the_queue_with_the_persons_go(film, monkeypatch):
    from slate.jobs import client

    submitted = []
    monkeypatch.setattr(client, "submit", lambda kind, argv, **kw: submitted.append((kind, argv, kw)) or f"job-{len(submitted)}")
    code, out, _ = run_cli(["ae", "masters", "--go", "--detach", "--keep-chunks", "--film", str(film), "--json"])
    assert code == 0 and _json(out)["job_id"] == "job-1" and _json(out)["ran"] == "queue"
    kind, argv, kw = submitted[0]
    assert kind == "ae_masters" and tuple(kw["resources"]) == ("render", "ae") and kw["priority"] == "supervised"
    # through the queue's --detach convention (detach_current): `slate` and the command, spelled out
    assert argv == ["slate", "ae", "masters", "--keep-chunks", "--film", str(film), "--json", "--go"]
    assert "--detach" not in argv and kw["resumable"] is True
    code, out, _ = run_cli(["ae", "acts", "render", "2", "--go", "--detach", "--film", str(film), "--json"])
    assert code == 0 and submitted[1][0] == "ae_render"
    assert submitted[1][1] == ["slate", "ae", "acts", "render", "2", "--film", str(film), "--json", "--go"]


def test_detach_goes_through_the_queues_detach_convention(film, monkeypatch):
    """jobs' --detach convention is slate.jobs.client.detach_current; AE masters use it with the command spelled out
    (never this process's argv, which is wrong under the MCP server)."""
    from slate.jobs import client

    calls = []
    monkeypatch.setattr(client, "detach_current", lambda kind, resources=(), **kw: calls.append((kind, resources, kw))
                        or "job-x")
    code, out, _ = run_cli(["ae", "masters", "--go", "--detach", "--film", str(film), "--json"])
    assert code == 0 and _json(out)["job_id"] == "job-x"
    kind, resources, kw = calls[0]
    assert kind == "ae_masters" and tuple(resources) == ("render", "ae")
    assert kw["argv"] == ["ae", "masters", "--film", str(film), "--json", "--go"] and kw["film"] == str(film)


def test_detach_refuses_now_what_the_job_would_refuse(film, slate_home, monkeypatch):
    from slate.jobs import client

    monkeypatch.setattr(client, "submit", lambda *a, **k: pytest.fail("nothing may be queued"))
    code, out, _ = run_cli(["ae", "masters", "--detach", "--film", str(film), "--json"])
    assert code == 1 and "--go" in _json(out)["hint"]
    auto, _ = make_film(slate_home["films"], slate_home["brands"], "auto-detach", mode="autonomous")
    code, out, _ = run_cli(["ae", "acts", "render", "2", "--go", "--detach", "--film", str(auto), "--json"])
    assert code == 1 and "utonomous" in _json(out)["message"]
    code, out, _ = run_cli(["ae", "masters", "--go", "--detach", "--requeue", "--film", str(film), "--json"])
    assert code == 2 and "without --detach" in _json(out)["message"]
