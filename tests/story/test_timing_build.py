"""`slate timing` end to end: the five files, Whisper (replayed, cached, reused), the render lock (P024, P062, P116)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from storyfix import write_json

from slate import jsonio, replay
from slate.cli import run_cli
from slate.timing import align as aligner

FILES = ["events.json", "shots.json", "timeline.json", "vo_cues.json", "vo_words.json", "word_misses.json"]
REAL_RUN_WHISPER = aligner.run_whisper  # captured before the fixture below swaps it for a refusal


def timing(*argv):
    code, out, err = run_cli(["timing", *argv, "--json"])
    try:
        return code, json.loads(out), err
    except json.JSONDecodeError:
        return code, out, err


@pytest.fixture
def voiced(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    return film


@pytest.fixture(autouse=True)
def never_run_whisper(monkeypatch):
    """Tests never start the real Whisper CLI (1.6 GB model, minutes of CPU)."""
    def refuse(*_a, **_kw):
        raise AssertionError("a test tried to run the whisper CLI")

    monkeypatch.setattr(aligner, "run_whisper", refuse)


def test_timing_writes_every_file_stamped_and_valid(voiced):
    code, out, _ = timing("--film", str(voiced))
    assert code == 0 and out["ok"] and out["status"] == "PASS"
    assert sorted(p.name for p in (voiced / "timing").iterdir() if p.is_file()) == FILES
    assert out["written"] == [f"timing/{n}" for n in ("shots.json", "vo_cues.json", "vo_words.json",
                                                         "timeline.json", "events.json")]
    for name, schema in (("shots", "shots"), ("vo_cues", "vo_cues"), ("vo_words", "vo_words"),
                         ("timeline", "timeline"), ("events", "events")):
        doc = jsonio.read(voiced / "timing" / f"{name}.json")
        assert doc["generated"] == "do not edit by hand: slate timing"
        assert jsonio.validate(doc, schema) == [], name
    assert {g["gate"]: g["status"] for g in out["gates"]} == {
        "timing.frames": "PASS", "timing.reads": "PASS", "timing.words": "PASS", "timing.events": "PASS"}
    assert out["aligner"] == {"engine": "whisper", "model": "turbo", "version": "replay", "replay": True}


def test_cues_place_each_take_at_its_lead(voiced):
    timing("--film", str(voiced))
    cues = jsonio.read(voiced / "timing" / "vo_cues.json")
    assert cues["voice"] == "voice-demo-1" and cues["lead"] == 0.3
    assert [(c["shot"], c["at"], c["file"]) for c in cues["cues"]] == [
        ("S02", 0.3, "voice/takes/S02.wav"), ("S03", 0.3, "voice/takes/S03.wav"), ("S04", 0.3, "voice/takes/S04.wav")]
    assert all(c["gain"] == -20.0 and c["fade_in"] == 0.01 and c["fade_out"] == 0.05 for c in cues["cues"])


def test_the_studio_polish_reaches_the_cue_sheet(voiced):
    """A polished take is what the film plays: the cue sheet (and so the mix) carries the polished file, ahead of the
    paced one it was made from. The polish keeps every sample where it was, so the reads and words don't move."""
    import shutil

    folder = voiced / "voice" / "takes" / "S02"
    folder.mkdir(parents=True)
    raw = voiced / "voice" / "takes" / "S02.wav"
    for name in ("t01.wav", "t01.paced.wav", "t01.polished.wav"):
        shutil.copyfile(raw, folder / name)
    write_json(voiced / "voice" / "takes.json", {"shots": {"S02": {"selected": "t01", "approved": None, "takes": {
        "t01": {"raw": "voice/takes/S02/t01.wav", "paced": "voice/takes/S02/t01.paced.wav",
                "polished": "voice/takes/S02/t01.polished.wav"}}},
        "S03": {"file": "voice/takes/S03.wav"}, "S04": {"file": "voice/takes/S04.wav"}}})
    code, out, _ = timing("--film", str(voiced))
    assert code == 0 and out["status"] == "PASS"
    cues = {c["shot"]: c["file"] for c in jsonio.read(voiced / "timing" / "vo_cues.json")["cues"]}
    assert cues == {"S02": "voice/takes/S02/t01.polished.wav", "S03": "voice/takes/S03.wav",
                    "S04": "voice/takes/S04.wav"}
    assert jsonio.read(voiced / "timing" / "vo_words.json")["shots"]["S02"]["take"] == "voice/takes/S02/t01.polished.wav"


def test_timeline_lands_moves_on_words_with_the_brands_eases(voiced):
    timing("--film", str(voiced))
    tl = jsonio.read(voiced / "timing" / "timeline.json")
    steam = tl["shots"]["S01"][0]
    assert (steam["frame"], steam["ease"], steam["dur"]) == (10, "settle", 0.5)  # 0.4 s at 25 fps
    door = tl["shots"]["S02"][0]
    shots = {s["id"]: s for s in jsonio.read(voiced / "timing" / "shots.json")["shots"]}
    words = jsonio.read(voiced / "timing" / "vo_words.json")["shots"]["S02"]["words"]
    lead = (shots["S02"]["vo_frame"] - shots["S02"]["frame_in"]) / 25
    ovens = next(w for w in words if w["w"] == "ovens")
    assert door["anchored_word_sec"] == round(lead + ovens["start"], 4)
    assert door["film_frame"] == shots["S02"]["frame_in"] + door["frame"]
    assert door["start_frame"] < door["frame"] and door["ease"] == "swift"
    events = jsonio.read(voiced / "timing" / "events.json")
    assert list(events["shots"]) == ["S01", "S02"]


def test_an_unknown_ease_fails_the_events_gate(make_film):
    film = make_film(takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    script = jsonio.read(film / "script" / "script.json")
    script["acts"][0]["shots"][0]["events"][0]["land"] = "bouncy:0.5"
    write_json(film / "script" / "script.json", script)
    run_cli(["script", "build", "--from-takes", "--film", str(film)])
    code, out, _ = timing("--film", str(film))
    gate = next(g for g in out["gates"] if g["gate"] == "timing.events")
    assert code == 1 and gate["status"] == "FAIL" and "no ease called 'bouncy'" in gate["summary"]


def test_a_read_that_does_not_fit_fails_the_reads_gate(voiced):
    (voiced / "voice" / "takes" / "S04.wav").unlink()
    from storyfix import write_wav

    write_wav(voiced / "voice" / "takes" / "S04.wav", 4.0, bursts=[(0.1, 3.8)])
    code, out, _ = timing("--film", str(voiced))
    gate = next(g for g in out["gates"] if g["gate"] == "timing.reads")
    assert code == 1 and gate["status"] == "FAIL" and gate["evidence"][0].startswith("S04: the 4.00 s read")
    assert "slate script build --from-takes" in gate["evidence"][-1]


# --- Whisper: invocation, replay fixtures, cache, the voice stage's alignments -----------------------------------------------

def test_whisper_runs_with_the_films_settings(monkeypatch, tmp_path):
    """timing's Whisper is the voice module's one runner (slate.voice.align.transcribe), run the way the films ran it."""
    from slate.voice import align as voice_align

    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = [str(a) for a in argv]
        seen["input"] = Path(seen["argv"][1]).resolve()  # the runner links each file in under a unique name
        out_dir = Path(seen["argv"][seen["argv"].index("--output_dir") + 1])
        (out_dir / f"{Path(seen['argv'][1]).stem}.json").write_text(json.dumps({"text": " Hi.", "segments": [
            {"words": [{"word": " Hi.", "start": 0.1, "end": 0.4}]}], "language": "en"}))
        return None

    take = tmp_path / "S02.wav"
    take.write_bytes(b"RIFF")
    monkeypatch.setattr(voice_align, "whisper_info", lambda: {"path": "/opt/fake/bin/whisper", "version": "1"})
    monkeypatch.setattr(voice_align.procs, "run", fake_run)
    got = REAL_RUN_WHISPER(take, model="turbo", language="en")
    argv = seen["argv"]
    assert argv[0] == "/opt/fake/bin/whisper" and seen["input"] == take.resolve()
    for flag, value in (("--model", "turbo"), ("--language", "en"), ("--word_timestamps", "True"),
                        ("--output_format", "json"), ("--fp16", "False")):
        assert argv[argv.index(flag) + 1] == value
    assert got["segments"][0]["words"][0]["word"] == " Hi."


def test_slate_whisper_names_the_cli_timing_hears_with(monkeypatch, tmp_path):
    """NEEDS (after the voice polish port): SLATE_WHISPER names one Whisper for every stage. timing runs it through
    slate.voice.align.transcribe, so a stand-in named there is the one timing hears with, whatever Homebrew has."""
    import sys

    fake = tmp_path / "bin" / "whisper-stand-in"
    fake.parent.mkdir()
    fake.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("--output_dir") + 1])
words = [{{"word": " Come", "start": 0.12, "end": 0.35}}, {{"word": " early.", "start": 0.41, "end": 0.9}}]
(out / (Path(args[0]).stem + ".json")).write_text(json.dumps(
    {{"text": " Come early.", "language": "en", "segments": [{{"id": 0, "words": words}}]}}))
""")
    fake.chmod(0o755)
    monkeypatch.setenv("SLATE_WHISPER", str(fake))
    take = tmp_path / "S04.wav"
    take.write_bytes(b"RIFF")
    assert aligner.whisper_exe() == str(fake)
    got = REAL_RUN_WHISPER(take, model="turbo", language="en")
    assert [w["word"] for w in got["segments"][0]["words"]] == [" Come", " early."]
    monkeypatch.setenv("SLATE_WHISPER", str(tmp_path / "missing-whisper"))
    assert aligner.whisper_exe(required=False) is None
    with pytest.raises(Exception, match="SLATE_WHISPER"):
        aligner.whisper_exe()


def test_whisper_version_comes_from_the_homebrew_cellar_path(monkeypatch):
    from slate.voice import align as voice_align

    monkeypatch.setattr(voice_align.procs, "which", lambda tool: "/opt/homebrew/bin/whisper")
    monkeypatch.setattr(voice_align.Path, "resolve",
                        lambda self, strict=False: Path("/opt/homebrew/Cellar/openai-whisper/20250625_6/libexec/bin/whisper"))
    monkeypatch.delenv("SLATE_WHISPER", raising=False)
    assert aligner.whisper_version() == "20250625_6"
    monkeypatch.setattr(voice_align.procs, "which", lambda tool: None)
    assert aligner.whisper_version() == "not installed"


def test_a_recorded_whisper_fixture_answers_in_replay(voiced, tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    monkeypatch.setenv("SLATE_FIXTURES", str(fixtures))
    sha = jsonio.sha256(voiced / "voice" / "takes" / "S04.wav")
    replay.record("whisper", f"transcribe-{sha[:16]}-turbo", {"text": " Come early.", "language": "en", "segments": [
        {"words": [{"word": " Come", "start": 0.12, "end": 0.35}, {"word": " early.", "start": 0.41, "end": 0.9}]}]})
    code, out, _ = timing("--film", str(voiced))
    rec = jsonio.read(voiced / "timing" / "vo_words.json")["shots"]["S04"]
    assert [(w["w"], w["whisper_start"], w["end"]) for w in rec["words"]] == [("Come", 0.12, 0.35), ("early.", 0.41, 0.9)]
    assert rec["heard"] == "Come early." and rec["aligner"]["replay"] is True


def test_alignments_are_cached_per_take(voiced, monkeypatch):
    calls = []
    real = aligner.replay.respond

    def counting(*a, **kw):
        calls.append(a[0])
        return real(*a, **kw)

    monkeypatch.setattr(aligner.replay, "respond", counting)
    timing("--film", str(voiced))
    assert calls.count("whisper") == 3
    timing("--film", str(voiced))
    assert calls.count("whisper") == 3  # nothing re-transcribed
    code, out, _ = run_cli(["timing", "words", "--realign", "--film", str(voiced), "--json"])
    assert code == 0 and calls.count("whisper") == 6
    cached = jsonio.read(voiced / "timing" / "align" / "whisper" / "S02.json")
    assert cached["take_sha256"] == jsonio.sha256(voiced / "voice" / "takes" / "S02.wav")
    assert (cached["engine"], cached["model"]) == ("whisper", "turbo")


def test_the_voice_stages_alignment_is_reused_when_it_matches_the_take(voiced):
    take = voiced / "voice" / "takes" / "S04.wav"
    write_json(voiced / "voice" / "align" / "S04.json", {
        "generated": "do not edit by hand: slate voice align",
        "engine": {"name": "openai-whisper", "version": "20250625_6", "model": "turbo"},
        "language": "en", "audio": str(take), "sha256": jsonio.sha256(take), "text": "Come early.",
        "segments": [{"words": [{"word": " Come", "start": 0.2, "end": 0.4}, {"word": " early.", "start": 0.45,
                                                                               "end": 0.8}]}]})
    code, out, _ = timing("--film", str(voiced))
    rec = jsonio.read(voiced / "timing" / "vo_words.json")["shots"]["S04"]
    assert rec["aligner"] == {"engine": "whisper", "model": "turbo", "version": "20250625_6", "replay": False}
    assert [w["whisper_start"] for w in rec["words"]] == [0.2, 0.45]
    assert not (voiced / "timing" / "align" / "whisper" / "S04.json").exists()  # nothing transcribed again


def test_a_stale_voice_alignment_is_ignored(voiced):
    write_json(voiced / "voice" / "align" / "S04.json", {
        "engine": {"name": "openai-whisper", "model": "turbo"}, "sha256": "0" * 64, "segments": [
            {"words": [{"word": " Stale", "start": 0.0, "end": 0.1}]}]})
    timing("--film", str(voiced))
    rec = jsonio.read(voiced / "timing" / "vo_words.json")["shots"]["S04"]
    assert [w["w"] for w in rec["words"]] == ["Come", "early."]


def test_forced_alignment_goes_through_the_voice_client_in_replay(voiced):
    code, out, _ = run_cli(["timing", "words", "--engine", "elevenlabs", "--film", str(voiced), "--json"])
    body = json.loads(out)
    if body.get("error") == "missing_prerequisite":  # the voice module's client isn't installed on this checkout
        assert code == 3 and "--engine whisper" in body["hint"]
        return
    assert code == 0 and body["aligner"]["engine"] == "elevenlabs" and body["aligner"]["replay"] is True
    assert set(body["takes"]) == {"S02", "S03", "S04"}


def test_forced_alignment_without_the_client_exits_three(voiced, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_vendor(name, *a, **kw):
        if name == "slate.vendors.elevenlabs":
            raise ImportError("not built yet")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_vendor)
    code, out, _ = run_cli(["timing", "words", "--engine", "elevenlabs", "--film", str(voiced), "--json"])
    assert code == 3 and "--engine whisper" in json.loads(out)["hint"]


# --- locks ---------------------------------------------------------------------------------------------------------------------

def register_render(film: Path) -> None:
    write_json(film / "renders" / "registry.json", {"entries": [{
        "act": 1, "engine": "remotion", "file": "renders/act1.mov", "frames": [0, 170], "samples": 16,
        "sha256": "a" * 64, "made_at": "2026-09-30T12:00:00+00:00", "bundle_hash": None}]})


def test_a_registered_render_locks_the_timing(voiced):
    assert timing("--film", str(voiced))[0] == 0
    register_render(voiced)
    before = (voiced / "timing" / "shots.json").read_bytes()
    code, out, _ = timing("--film", str(voiced))
    assert code == 1 and out["error"] == "refused" and "locked" in out["message"]
    code, out, _ = timing("--film", str(voiced), "--force")
    assert code == 1 and "--approved-by" in out["message"] + out.get("hint", "")
    assert (voiced / "timing" / "shots.json").read_bytes() == before
    code, out, _ = timing("--film", str(voiced), "--force", "--approved-by", "Q", "--reason", "re-cut act 2")
    assert code == 0 and out["ok"]


def test_dry_run_changes_nothing(voiced):
    code, out, _ = timing("--film", str(voiced), "--dry-run")
    assert code == 0 and out["written"] == [] and not (voiced / "timing").exists()


def test_show_prints_one_shot_on_the_clock(voiced):
    timing("--film", str(voiced))
    code, out, _ = timing("show", "S02", "--film", str(voiced))
    assert code == 0 and out["shot"]["id"] == "S02" and out["timecode"] == ["00:00:02:00", "00:00:06:20"]
    assert [w["w"] for w in out["words"]][:2] == ["Every", "morning"]
    assert out["events"][0]["id"] == "door"
    code, text, _ = run_cli(["timing", "show", "S02", "--film", str(voiced)])
    assert code == 0 and "event door: word 'ovens'" in text
    code, out, _ = timing("show", "S99", "--film", str(voiced))
    assert code == 2 and out["error"] == "usage"


def test_timing_needs_a_storyboard(make_film):
    film = make_film()
    code, out, _ = timing("--film", str(film))
    assert code == 3 and "slate script build" in out["hint"]


def test_the_python_interface(voiced):
    from slate import timing as timing_mod

    paths = timing_mod.build(voiced)
    assert [p.name for p in paths] == ["shots.json", "vo_cues.json", "vo_words.json", "timeline.json", "events.json"]
    shots = timing_mod.load_shots(voiced)
    assert shots["total_frames"] == sum(s["frames"] for s in shots["shots"])


def test_load_shots_before_timing_says_what_to_run(make_film):
    from slate import timing as timing_mod
    from slate.errors import MissingPrerequisite

    with pytest.raises(MissingPrerequisite, match="timing/shots.json"):
        timing_mod.load_shots(make_film())


def test_lock_records_go_through_the_state_module_when_installed(voiced):
    timing("--film", str(voiced))
    lock = jsonio.read(voiced / "film.lock.json", default=None)
    if lock is None:
        pytest.skip("the state module isn't installed on this checkout")
    for name in ("shots.json", "vo_cues.json", "vo_words.json", "timeline.json", "events.json"):
        entry = lock["files"][f"timing/{name}"]
        assert entry["producer"] == "slate timing" and "script/storyboard.json" in entry["inputs"]


def test_a_shots_generate_block_rides_through_to_shots_json(make_film):
    """gen-capture's ask: `slate gen` reads each shot's generation request from timing/shots.json, carried from
    script.json through storyboard.json."""
    import copy

    from storyfix import SCRIPT

    script = copy.deepcopy(SCRIPT)
    request = {"model": "plate_video", "prompt": "Steam rolls over a dark oven door.", "takes": 2}
    script["acts"][0]["shots"][0]["generate"] = request
    film = make_film(script=script, takes={"S02": 3.1, "S03": 3.4, "S04": 1.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    sb = jsonio.read(film / "script" / "storyboard.json")
    assert sb["acts"][0]["shots"][0]["generate"] == request
    assert timing("--film", str(film))[0] == 0
    shots = {s["id"]: s for s in jsonio.read(film / "timing" / "shots.json")["shots"]}
    assert shots["S01"]["generate"] == request and "generate" not in shots["S02"]
    assert jsonio.validate(jsonio.read(film / "timing" / "shots.json"), "shots") == []


def test_vo_words_says_how_each_number_and_symbol_in_the_vo_is_said(make_film):
    """engine-kit's ask: the engine's word() matches "1987" against what was heard without number tables of its own,
    so vo_words.json carries every number and symbol in the VO lines with its spoken forms (the engine puts this in
    @film/data as `spoken`)."""
    import copy

    from storyfix import SCRIPT

    from slate.timing import spoken

    script = copy.deepcopy(SCRIPT)
    script["acts"][1]["shots"][1]["vo"] = "Come early & say hello."
    film = make_film(script=script, takes={"S02": 3.1, "S03": 3.4, "S04": 2.0})
    assert run_cli(["script", "build", "--from-takes", "--film", str(film)])[0] == 0
    assert timing("--film", str(film))[0] in (0, 1)
    table = jsonio.read(film / "timing" / "vo_words.json")["spoken"]
    assert set(table) == {"1987", "&"}  # the token without its full stop; plain words aren't listed
    assert table["1987"] == spoken.forms("1987") and ["nineteen", "eighty-seven"] in table["1987"]
    assert table["&"] == [["and"]]
