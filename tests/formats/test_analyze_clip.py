"""`slate analyze` on a short synthetic clip: two hard cuts, two moves drawn on known curves, and a tone.

The clip is lossless, so the cuts land on their exact frames and the measured moves can be checked against the curves
that drew them. Nothing here runs a model: Whisper replays a recorded answer, and Demucs and librosa are left
UNCHECKED (or faked) on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import formats_clips as clips
from slate import cli

FPS = clips.FPS


@pytest.fixture(scope="module")
def clip_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("analyze-clip")
    clips.reference_clip(folder)
    clips.reference_clip(folder, tone_hz=440.0)
    return folder


@pytest.fixture
def no_ocr(monkeypatch):
    from slate.qa import ocr

    monkeypatch.setattr(ocr, "available", lambda refresh=False: (False, {"name": "Apple Vision (ocrmac)",
                                                                          "reason": "ocrmac isn't importable here",
                                                                          "hint": "install the ocr extra"}))


def test_the_default_steps_find_the_cuts_and_fit_the_moves(clip_dir, slate_home, no_ocr):
    from slate import analyze

    out = slate_home["tmp"] / "out"
    a = analyze.run(clip_dir / "reference.mkv", out=out)
    assert a["requested"] == ["cuts", "ocr", "eases"]
    assert [c["frame"] for c in a["cuts"]["hard_cuts"]] == [clips.CUT_1, clips.CUT_2]
    assert [(s["start"], s["end"]) for s in a["scenes"]] == [(0.0, clips.CUT_1 / FPS), (clips.CUT_1 / FPS, clips.CUT_2 / FPS),
                                                              (clips.CUT_2 / FPS, clips.TOTAL / FPS)]
    assert a["steps"]["cuts"]["status"] == "PASS" and a["steps"]["eases"]["status"] == "PASS"
    assert a["steps"]["ocr"]["status"] == "UNCHECKED"
    moves = a["eases"]["moves"]
    assert len(moves) == 2
    for move, truth, channel in ((moves[0], clips.MOVE_1, "centroid_x"), (moves[1], clips.MOVE_2, "centroid_y")):
        assert move["channel"] == channel and move["confidence"] == "good"
        best = move["best_named"]
        assert best["name"] == truth["name"] and best["rms"] < 0.005
        first, last = truth["frames"]
        assert abs(best["start_s"] - first / FPS) < 0.02 and abs(best["end_s"] - last / FPS) < 0.02
        assert move["lineage"]["sha256"] == a["video"]["sha256"] and move["lineage"]["tool"] == "slate analyze --eases"
    assert abs(moves[0]["travel"] - (clips.MOVE_1["x"][1] - clips.MOVE_1["x"][0])) < 0.5
    # the frames and contact sheets are on disk, and the report reads like the films' ANALYSIS.md
    for sc in a["scenes"]:
        assert (out / sc["change_frame"]).is_file() and (out / sc["settled_frame"]).is_file()
    assert (out / a["cuts"]["sheets"]["settled"]).is_file()
    report = (out / "analysis.md").read_text()
    for heading in ("## The short version", "## Method", "## Scenes", "## Measured eases"):
        assert heading in report
    assert "Not checked: on-screen text" in report and "install the ocr extra" in report
    saved = json.loads((out / "analysis.json").read_text())
    assert saved["generated"].startswith("do not edit by hand") and saved["steps"]["probe"]["status"] == "PASS"


def test_described_moves_follow_a_colour_inside_a_region(clip_dir, slate_home):
    from slate import analyze

    spec = slate_home["tmp"] / "moves.json"
    spec.write_text(json.dumps([{"name": "push", "t0": 0.2, "t1": 1.5, "roi": [0, 60, 320, 60], "metric": "left",
                                 "mask": {"color": "#ffffff", "tolerance": 40}, "note": "the square's left edge"},
                                {"name": "empty", "t0": 2.2, "t1": 3.0, "metric": "left",
                                 "mask": {"rgb_min": [0, 250, 0], "rgb_max": [5, 255, 5]}}]))
    a = analyze.run(clip_dir / "reference.mkv", out=slate_home["tmp"] / "described", steps=["eases"], eases_spec=spec)
    moves = {m["name"]: m for m in a["eases"]["moves"]}
    assert moves["push"]["channel"] == "left" and moves["push"]["best_named"]["name"] == clips.MOVE_1["name"]
    assert moves["push"]["note"] == "the square's left edge" and moves["push"]["how"].startswith("described")
    assert [s["name"] for s in a["eases"]["skipped"]] == ["empty"]  # nothing green there: named, not faked
    assert "cuts" not in a["steps"]  # eases alone doesn't need the scene scores


def test_higgsfield_scenes_join_and_the_voice_never_comes_from_them(clip_dir, slate_home, no_ocr):
    from slate import analyze

    hf = slate_home["tmp"] / "hf.json"
    hf.write_text(json.dumps({"result": {"id": "job-1", "status": "completed", "scenes": [
        {"scene_number": 1, "timestamp_start": "0:00", "timestamp_end": "0:02", "label": "Open", "visual": "A square."},
        {"scene_number": 2, "timestamp_start": "0:02", "timestamp_end": "0:03", "label": "Bar", "visual": "A bar."},
        {"scene_number": 3, "timestamp_start": "0:03", "timestamp_end": "0:04", "label": "Drop", "visual": "A drop.",
         "audio": "a paraphrase of the words on screen"}]}}))
    a = analyze.run(clip_dir / "reference.mkv", out=slate_home["tmp"] / "hf", steps=["eases"], higgsfield=hf)
    assert a["steps"]["cuts"]["status"] == "PASS"  # the join needs the scene scores, so they ran
    assert [s["higgsfield"]["label"] for s in a["scenes"]] == ["Open", "Bar", "Drop"]
    assert a["scenes"][2]["start"] == clips.CUT_2 / FPS and a["scenes"][2]["snapped"] is True
    assert a["higgsfield"]["scenes"] == 3 and any("Whisper" in n for n in a["notes"])
    assert "vo" not in a["scenes"][2]  # no transcript asked for: the paraphrase is never used as the voice


def test_the_transcript_replays_and_is_cleaned_and_the_mix_is_measured(clip_dir, slate_home, monkeypatch):
    from slate import analyze, jsonio
    from slate.timing import align

    fixtures = slate_home["tmp"] / "fixtures"
    monkeypatch.setenv("SLATE_FIXTURES", str(fixtures))
    video = clip_dir / "reference_tone.mkv"
    sha = jsonio.sha256(video)
    words = [{"word": " Hello", "start": 0.5, "end": 0.9, "probability": 0.99},
             {"word": " there.", "start": 0.95, "end": 1.4, "probability": 0.4}]
    loop = {"text": " Hello there.", "start": 1.6, "end": 2.4,
            "words": [{"word": " Hello", "start": 1.6, "end": 2.0, "probability": 0.9},
                      {"word": " there.", "start": 2.0, "end": 2.4, "probability": 0.9}]}
    response = {"text": " Hello there.", "language": "en",
                "segments": [{"text": " Hello there.", "start": 0.5, "end": 1.4, "words": words}, loop]}
    key = f"analyze-{sha[:16]}-{align.whisper_model(None)}"
    (fixtures / "whisper").mkdir(parents=True)
    (fixtures / "whisper" / f"{key}.json").write_text(json.dumps({"replay_fixture": True, "response": response}))
    a = analyze.run(video, out=slate_home["tmp"] / "voice", steps=["transcript", "cuts"])
    tr = a["transcript"]
    assert a["steps"]["transcript"]["status"] == "PASS" and tr["engine"]["replay"] is True
    assert [s["text"] for s in tr["segments"]] == ["Hello there."] and tr["dropped"][0]["reason"].startswith("repeats")
    assert tr["stats"]["words"] == 2 and [w["w"] for w in tr["low_confidence"]] == ["there."]
    assert a["scenes"][0]["vo"] == "Hello there." and tr["source"] == "mix"
    mix = a["audio"]["mix"]
    assert a["steps"]["audio"]["status"] == "PASS" and -40 < mix["integrated_lufs"] < 0
    assert (slate_home["tmp"] / "voice" / "transcript" / "words.tsv").read_text().count("\n") == 3


def test_a_film_without_sound_leaves_the_sound_steps_unchecked(clip_dir, slate_home, monkeypatch):
    from slate import analyze
    from slate.analyze import stems

    monkeypatch.setattr(stems, "weights_cached", lambda: False)
    a = analyze.run(clip_dir / "reference.mkv", out=slate_home["tmp"] / "silent",
                    steps=["transcript", "stems", "beats"])
    for step in ("transcript", "stems", "beats"):
        assert a["steps"][step]["status"] == "UNCHECKED" and "no audio" in a["steps"][step]["summary"]
    assert "audio" not in a


def test_the_default_output_is_the_films_folder_or_slates_data_never_beside_the_video(clip_dir, slate_home):
    from slate import analyze, paths

    video = clip_dir / "reference.mkv"
    assert analyze.default_out(video, "ab" * 32).parent == paths.data() / "analysis"
    film = slate_home["films"] / "f"
    assert analyze.default_out(video, "ab" * 32, film) == film / "analysis" / "reference"
    with pytest.raises(Exception):
        analyze.run(video, steps=["wobble"])


# ---------------------------------------------------------------------------------------------------------------
# The command


def test_the_command_writes_json_and_its_exit_codes_follow_the_contract(clip_dir, slate_home, no_ocr, monkeypatch):
    from slate.analyze import stems

    out = slate_home["tmp"] / "cli"
    code, stdout, err = cli.run_cli(["analyze", str(clip_dir / "reference.mkv"), "--cuts", "--eases", "--json",
                                     "--out", str(out)])
    assert code == 0, err
    payload = json.loads(stdout)
    assert payload["steps"]["cuts"]["status"] == "PASS" and payload["moves"] == 2 and payload["scenes"] == 3
    assert Path(payload["report"]).is_file()
    # a step named on the command line that can't run here: the rest still runs, and the exit code says so (3)
    code, stdout, _ = cli.run_cli(["analyze", str(clip_dir / "reference.mkv"), "--ocr", "--json", "--out", str(out)])
    assert code == 3 and json.loads(stdout)["unchecked_requested"] == ["ocr"]
    # the quick defaults don't fail the command when OCR isn't installed
    code, _, _ = cli.run_cli(["analyze", str(clip_dir / "reference.mkv"), "--json", "--out", str(out)])
    assert code == 0
    # a named Demucs split without its weights is exit 3, with the fix in the report
    monkeypatch.setattr(stems, "weights_cached", lambda: False)
    code, stdout, _ = cli.run_cli(["analyze", str(clip_dir / "reference_tone.mkv"), "--stems", "--json", "--out", str(out)])
    assert code == 3 and "stems" in json.loads(stdout)["unchecked_requested"]
    # no such video: usage (2); dry run changes nothing
    code, stdout, _ = cli.run_cli(["analyze", str(clip_dir / "missing.mkv"), "--json"])
    assert code == 2 and json.loads(stdout)["error"] == "usage"
    code, stdout, _ = cli.run_cli(["analyze", str(clip_dir / "reference.mkv"), "--dry-run", "--json",
                                   "--out", str(slate_home["tmp"] / "dry")])
    assert code == 0 and json.loads(stdout)["dry_run"] and not (slate_home["tmp"] / "dry").exists()


def test_detach_queues_a_job_with_the_whisper_slot(clip_dir, slate_home, monkeypatch):
    from slate.jobs import client

    seen = {}

    def fake_detach(kind, resources=(), *, film=None, **kw):
        seen.update(kind=kind, resources=tuple(resources), film=film)
        return "job-123"

    monkeypatch.setattr(client, "detach_current", fake_detach)
    code, stdout, _ = cli.run_cli(["analyze", str(clip_dir / "reference_tone.mkv"), "--transcript", "--detach", "--json"])
    assert code == 0 and json.loads(stdout)["job"] == "job-123"
    assert seen == {"kind": "analyze", "resources": ("whisper",), "film": None}



def test_two_reference_films_in_one_command_each_get_their_own_folder(clip_dir, slate_home, no_ocr):
    out = slate_home["tmp"] / "study"
    code, stdout, err = cli.run_cli(["analyze", str(clip_dir / "reference.mkv"), str(clip_dir / "reference_tone.mkv"),
                                     "--eases", "--json", "--out", str(out)])
    assert code == 0, err
    data = json.loads(stdout)
    assert [Path(a["out"]).name for a in data["analyses"]] == ["reference", "reference_tone"]
    assert all(a["moves"] == 2 for a in data["analyses"]) and data["unchecked_requested"] == []
    assert (out / "reference" / "analysis.json").is_file() and (out / "reference_tone" / "analysis.md").is_file()


def test_the_mix_is_measured_but_not_kept_when_no_sound_step_needs_it(clip_dir, slate_home, no_ocr):
    from slate import analyze

    out = slate_home["tmp"] / "quiet"
    a = analyze.run(clip_dir / "reference_tone.mkv", out=out, steps=["cuts"])
    assert a["steps"]["audio"]["status"] == "PASS" and a["audio"]["mix"]["file"] is None
    assert not (out / "audio" / "mix.wav").exists()
