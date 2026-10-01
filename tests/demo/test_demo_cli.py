"""`slate demo` without a render: the stages and their order, --dry-run, the live guard, what the demo expects to stay
unchecked, how a stage fails, the scratch folder's fate and the manifest check. The whole run, render and all, is
tests/integration/test_demo.py (opt-in: minutes)."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from slate import cli, demo, jsonio
from slate.errors import MissingPrerequisite, SpendRefused


def _cli(*argv: str) -> tuple[int, dict, str]:
    code, out, err = cli.run_cli(["--json", *argv])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return code, (json.loads(lines[-1]) if lines else {}), err


def _run(tmp_path: Path, **kw) -> demo.Run:
    base = dict(film_id="slate-demo-1", film=tmp_path / "films" / "slate-demo-1", root=tmp_path, keep=False,
                live=False, env={}, vision=True)
    return demo.Run(**{**base, **kw})


class _Terminal:
    def isatty(self) -> bool:
        return True


# ---------------------------------------------------------------------------------------------------- the stages
def test_the_stages_cover_the_pipeline_in_the_order_a_film_is_made():
    keys = [s.key for s in demo.STAGES]
    assert len(keys) == len(set(keys))
    for needed in ("new", "script build", "voice tts", "voice polish", "voice align", "voice analyze", "timing",
                   "voice captions", "scenes", "emit-events", "check", "render", "score plan", "score compose",
                   "score assemble", "mix", "verify", "voice check", "master", "qa", "review", "deliver",
                   "deliverables"):
        assert needed in keys, needed
    order = [keys.index(k) for k in ("new", "script build", "voice tts", "voice polish", "timing", "scenes",
                                     "emit-events", "check", "render", "mix", "master", "qa", "review", "deliver",
                                     "deliverables")]
    assert order == sorted(order)
    # the polish comes before the clock, so the cue sheet (and the mix) play the polished takes; the masters come
    # before qa, so every film gate runs, the masters' psnr included
    assert keys.index("voice polish") < keys.index("timing") < keys.index("mix")
    assert keys.index("master") < keys.index("qa")


def test_dry_run_lists_every_stage_and_makes_nothing(slate_home):
    code, out, _ = _cli("demo", "--dry-run")
    assert code == 0 and out["dry_run"] is True and out["live"] is False
    assert [s["stage"] for s in out["stages"]] == [s.key for s in demo.STAGES]
    assert not any(slate_home["films"].iterdir())


def test_the_demo_is_one_registered_command_and_takes_no_film(slate_home):
    cli.build_parser()
    assert cli.REGISTERED["demo"] == "slate.demo" and not cli.BROKEN
    code, out, _ = _cli("--film", str(slate_home["tmp"]), "demo")
    assert code == 2 and "makes its own film" in out["message"]


def test_the_demo_film_is_neutral_and_complete():
    script = jsonio.read(demo.ASSETS / "script.json")
    assert jsonio.validate(script, "script") == []
    shots = [s for a in script["acts"] for s in a["shots"]]
    assert [s["id"] for s in shots] == ["D01", "D02", "D03", "D04"] and len(script["acts"]) == 2
    assert demo.AB_SHOT in {s["id"] for s in shots}
    for name in ("act1/index.tsx", "act1/beats.ts", "act2/index.tsx", "act2/beats.ts"):
        text = (demo.ASSETS / "scenes" / name).read_text()
        imports = set(re.findall(r"from '([^']+)'", text))
        assert imports <= {"react", "@slate/kit", "@slate/kit/core", "@film/data", "./beats"}, (name, imports)
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b|font-family|fontFamily", text), name  # tokens only, no literals
    assert "PlaceholderAct" in (demo.ASSETS / "scenes" / "act1" / "index.tsx").read_text()
    assert "export const silent" in (demo.ASSETS / "scenes" / "act1" / "beats.ts").read_text()


# ---------------------------------------------------------------------------------------------------- spending
def test_live_refuses_under_an_agent_without_the_persons_yes(slate_home, monkeypatch):
    monkeypatch.setattr(demo.secrets, "present", lambda name: "env")
    monkeypatch.setattr(demo.procs, "agent_context", lambda: "claude-code")
    monkeypatch.delenv("SLATE_LIVE", raising=False)
    code, out, _ = _cli("demo", "--live")
    assert code == 4 and "Nothing was spent" in out["message"] and "SLATE_LIVE=1" in out["hint"]
    assert not any(slate_home["films"].iterdir())


def test_the_live_flag_itself_never_counts_as_the_persons_yes(slate_home, monkeypatch):
    """--live sets SLATE_LIVE=1 for the command's own run; the demo reads what the caller set."""
    seen = {}

    def handler(args):
        seen.update(caller=cli.caller_env("SLATE_LIVE"), env=os.environ.get("SLATE_LIVE"))
        return 0

    monkeypatch.setattr(demo, "cmd_demo", handler)
    monkeypatch.delenv("SLATE_LIVE", raising=False)
    assert cli.run_cli(["demo", "--live"])[0] == 0
    assert seen == {"caller": None, "env": "1"}
    monkeypatch.setenv("SLATE_LIVE", "1")
    assert cli.run_cli(["demo", "--live"])[0] == 0 and seen["caller"] == "1"


def test_live_goes_ahead_under_an_agent_only_with_SLATE_LIVE_from_the_person(monkeypatch):
    monkeypatch.setattr(demo.secrets, "present", lambda name: "keychain")
    monkeypatch.setattr(demo.procs, "agent_context", lambda: "codex")
    monkeypatch.setattr(demo.cli, "caller_env", lambda name: "1")
    assert "SLATE_LIVE=1" in demo.consent_to_spend(ask=lambda q: pytest.fail("an agent is never asked"))


def test_live_asks_a_person_in_a_terminal_and_spends_nothing_without_a_yes(monkeypatch):
    monkeypatch.setattr(demo.secrets, "present", lambda name: "keychain")
    monkeypatch.setattr(demo.procs, "agent_context", lambda: None)
    monkeypatch.setattr(demo.sys, "stdin", _Terminal())
    asked = []
    assert demo.consent_to_spend(ask=lambda q: asked.append(q) or "yes") == "yes, typed in this Terminal"
    assert "credits" in asked[0] and "Type yes" in asked[0]
    with pytest.raises(SpendRefused, match="Nothing was spent"):
        demo.consent_to_spend(ask=lambda q: "sure")


def test_live_needs_a_connected_elevenlabs_key(monkeypatch):
    monkeypatch.setattr(demo.secrets, "present", lambda name: None)
    with pytest.raises(MissingPrerequisite, match="ElevenLabs"):
        demo.consent_to_spend(ask=lambda q: "yes")


def test_replayed_stages_never_spend_even_when_the_caller_exported_SLATE_LIVE(slate_home, monkeypatch):
    monkeypatch.setenv("SLATE_LIVE", "1")
    run = demo.prepare(live=False, keep=False)
    try:
        assert run.env["SLATE_LIVE"] == "0" and run.env["SLATE_FAKE_VENDORS"] == "1"
        assert run.env["SLATE_DATA"] == str(run.root / "data") and run.env["SLATE_FILMS"] == str(run.root / "films")
        assert run.env["SLATE_DOWNLOADS"] == str(run.root / "review")
        assert run.film == run.root / "films" / "slate-demo-1"
    finally:
        shutil.rmtree(run.root)


def test_keep_puts_the_next_demo_film_in_your_films_folder(slate_home):
    (slate_home["films"] / "slate-demo-1").mkdir()
    (slate_home["films"] / "slate-demo-4").mkdir()
    run = demo.prepare(live=False, keep=True)
    try:
        assert run.film == slate_home["films"] / "slate-demo-5"
        assert run.env["SLATE_DATA"] == str(slate_home["data"]) and run.env["SLATE_FILMS"] == str(slate_home["films"])
    finally:
        shutil.rmtree(run.root)


# ---------------------------------------------------------------------------------------------------- judging
def test_expected_names_only_what_replay_the_demo_brand_or_a_missing_vision_leave_unchecked(tmp_path):
    run = _run(tmp_path)
    voice = {"gate": "voice_polish", "status": "UNCHECKED",
             "details": {"unchecked": ["D01: a replayed stand-in: length and loudness hold"]}}
    assert "replayed" in demo.expected(run, voice)
    assert demo.expected(run, {**voice, "status": "FAIL"}) is None
    assert demo.expected(dataclasses.replace(run, live=True), voice) is None  # live takes are really heard
    assert demo.expected(run, {"gate": "voice_words", "status": "UNCHECKED",
                               "details": {"unchecked": ["D01: not aligned yet"]}}) is None
    ref = {"gate": "sound.reference", "status": "UNCHECKED", "details": {"reference": {"placeholder": True}}}
    assert "placeholder" in demo.expected(run, ref)
    assert demo.expected(run, {**ref, "details": {"reference": {"approved_by": None}}}) is None
    ocr = {"gate": "ocr", "status": "UNCHECKED", "details": {}}
    assert demo.expected(run, ocr) is None and "Vision" in demo.expected(dataclasses.replace(run, vision=False), ocr)
    assert demo.expected(run, {"gate": "stability", "status": "REVIEW"}) is None


def test_a_stage_stops_on_a_gate_it_doesnt_expect_or_a_command_that_refuses(tmp_path):
    run = _run(tmp_path)
    with pytest.raises(demo.StageFailed, match="stability is REVIEW"):
        demo.judge(run, "qa", 1, {"results": [{"gate": "stability", "status": "REVIEW", "summary": "7 frames"}]}, "",
                   gate_stage=True)
    with pytest.raises(demo.StageFailed, match="exited 3: no engine"):
        demo.judge(run, "render", 3, {"message": "no engine"}, "", gate_stage=True)
    with pytest.raises(demo.StageFailed, match="exited 1"):  # exit 1 is only a gate verdict for a gate stage
        demo.judge(run, "mix", 1, {"error": "refused", "message": "nothing to mix"}, "")
    ref = {"gate": "sound.reference", "status": "UNCHECKED", "summary": "not approved",
           "details": {"reference": {"placeholder": True}}}
    opened = demo.judge(run, "verify", 1, {"gates": [{"gate": "sound.loudness", "status": "PASS"}, ref]}, "",
                        gate_stage=True)
    assert [o["gate"] for o in opened] == ["sound.reference"] and opened[0]["why"]
    line = demo._gate_line({"gates": [{"gate": "sound.loudness", "status": "PASS"}, ref]}, opened, "sound gates")
    assert line.startswith("1 sound gates PASS; 1 unchecked as expected: sound.reference (")


def test_the_manifest_check_holds_a_draft_to_its_word(tmp_path):
    run = _run(tmp_path)
    folder = run.film / "deliver" / "drafts" / "v1.1"
    (folder / "masters").mkdir(parents=True)
    (folder / "masters" / "m.mp4").write_bytes(b"picture")
    (folder / "sound").mkdir()
    (folder / "sound" / "mix.wav").write_bytes(b"sound")
    manifest = {"final": False, "folder": "deliver/drafts/v1.1",
                "why_not_final": ["draft films are never delivered as final", "4 replayed stand-ins are in use"],
                "replayed": [{"what": "voice take for D01"}],
                "files": [{"kind": "master:h264", "file": "masters/m.mp4",
                           "sha256": jsonio.sha256(folder / "masters" / "m.mp4")},
                          {"kind": "mix", "file": "sound/mix.wav", "sha256": jsonio.sha256(folder / "sound" / "mix.wav")}]}
    jsonio.write_atomic(folder / "manifest.json", manifest)
    run.docs["deliver"] = {"manifest": "deliver/drafts/v1.1/manifest.json"}
    line, doc = demo.st_deliverables(run)
    assert "2 files there with their sha256; final: no" in line and doc["final"] is False
    for broken, why in ((dict(manifest, final=True), "calls the delivery final"),
                        (dict(manifest, replayed=[]), "replayed stand-ins"),
                        (dict(manifest, files=[dict(manifest["files"][0], sha256="0" * 64), manifest["files"][1]]),
                         "doesn't match its sha256"),
                        (dict(manifest, files=manifest["files"][:1]), "no mix")):
        jsonio.write_atomic(folder / "manifest.json", broken)
        with pytest.raises(demo.StageFailed, match=why):
            demo.st_deliverables(run)


# ---------------------------------------------------------------------------------------------------- the run
def test_a_passing_scratch_run_removes_its_folder_and_a_failed_one_keeps_it(slate_home, monkeypatch):
    runs = []

    def fine(run):
        runs.append(run)
        run.film.mkdir(parents=True, exist_ok=True)
        return "done", {"gates": [{"gate": "x", "status": "PASS"}]}

    def broken(run):
        raise demo.StageFailed("render stopped: the engine is missing\nmore detail")

    monkeypatch.setattr(demo, "prerequisites", lambda: None)
    monkeypatch.setattr(demo, "STAGES", [demo.Stage("new", fine, "a"), demo.Stage("render", fine, "b")])
    lines: list[str] = []
    rec = demo.demo(say=lines.append)
    assert rec["ok"] and rec["removed"] and not runs[0].root.exists() and rec["not_passed"] == []
    assert any(re.search(r"\b1\s+new\s+ok\b", ln) for ln in lines) and "Every stage passed: 2 of 2" in lines[-2]

    monkeypatch.setattr(demo, "STAGES", [demo.Stage("new", fine, "a"), demo.Stage("render", broken, "b"),
                                         demo.Stage("qa", fine, "c")])
    lines.clear()
    rec = demo.demo(say=lines.append)
    try:
        assert not rec["ok"] and not rec["removed"] and Path(rec["scratch"]).is_dir()
        assert [s["stage"] for s in rec["stages"]] == ["new", "render"]  # it stops at the stage that failed
        assert rec["stages"][1]["error"].startswith("render stopped") and "      more detail" in lines
        assert any("Stopped at stage 2 of 3 (render)" in ln for ln in lines)
    finally:
        shutil.rmtree(rec["scratch"], ignore_errors=True)


def test_json_mode_prints_one_object_and_the_stage_lines_go_to_stderr(slate_home, monkeypatch):
    monkeypatch.setattr(demo, "prerequisites", lambda: None)
    monkeypatch.setattr(demo, "STAGES", [demo.Stage("new", lambda run: ("made it", {}), "a")])
    code, out, err = _cli("demo")
    assert code == 0 and out["ok"] is True and [s["stage"] for s in out["stages"]] == ["new"]
    assert "made it" in err and out["removed"] is True
