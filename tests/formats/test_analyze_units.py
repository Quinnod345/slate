"""The analyzer's parts on their own: scene scores and peaks, Higgsfield's scenes, the transcript cleanup, the
optional model steps' UNCHECKED paths, OCR through a scripted engine, and the move finder and tracker."""

from __future__ import annotations

import json
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from slate.analyze import beats, cuts, eases, library, onscreen, stems, transcript
from slate.errors import UsageError

# ---------------------------------------------------------------------------------------------------------------
# Scene scores, peaks and scenes

SCORES_TEXT = """frame:0    pts:0       pts_time:0
lavfi.scene_score=0.000000
frame:1    pts:1001    pts_time:0.0333667
lavfi.scene_score=0.002000
frame:2    pts:2002    pts_time:0.0667333
lavfi.scene_score=0.090000
frame:3    pts:3003    pts_time:0.1001
lavfi.scene_score=0.120000
frame:60   pts:60060   pts_time:2.002
lavfi.scene_score=1.000000
frame:61   pts:61061   pts_time:2.03537
lavfi.scene_score=0.010000
"""


def test_scores_parse_like_the_films_file():
    rows = cuts.parse_scores(SCORES_TEXT)
    assert len(rows) == 6 and rows[4] == {"frame": 60, "t": 2.002, "score": 1.0}


def test_peaks_merge_close_changes_and_split_cuts_from_changes():
    rows = cuts.parse_scores(SCORES_TEXT)
    found = cuts.peaks(rows, cuts.CHANGE)
    assert [(p["frame"], p["kind"]) for p in found] == [(3, "change"), (60, "cut")]  # 2 and 3 merge; 3 is stronger
    assert all(p["frame"] > 0 for p in cuts.peaks(rows, 0.0))  # the first frame is never a change


def test_scenes_span_the_film_and_grab_after_each_change():
    bounds = [{"t": 2.0, "frame": 50, "score": 0.9, "kind": "cut"}, {"t": 3.0, "frame": 75, "score": 0.1, "kind": "change"}]
    scenes = cuts.build_scenes(bounds, 4.0)
    assert [(s["start"], s["end"], s["starts_with"]) for s in scenes] == [(0.0, 2.0, "start"), (2.0, 3.0, "cut"),
                                                                          (3.0, 4.0, "change")]
    assert scenes[1]["change_t"] == 2.5 and scenes[1]["settled_t"] == 2.5
    assert scenes[0]["peak_score"] is None and scenes[2]["peak_score"] == 0.1


HF = {"result": {"id": "abc", "status": "completed", "video_s3_url": "https://example.invalid/storage/secret.mp4",
                 "scenes": [
                     {"scene_number": 1, "timestamp_start": "0:00", "timestamp_end": "0:01", "label": "Open",
                      "visual": "A card.", "shot_type": "Wide Shot", "audio": "Paraphrase of the on-screen words."},
                     {"scene_number": 2, "timestamp_start": "0:02", "timestamp_end": "0:03", "label": "Middle",
                      "visual": "Blue.", "shot_type": "Wide Shot", "audio": ""},
                     {"scene_number": 3, "timestamp_start": "0:03", "timestamp_end": "0:04", "label": "End",
                      "visual": "Light.", "shot_type": "Close", "audio": ""}]}}


def test_higgsfield_scenes_snap_to_ffmpeg_peaks_and_never_copy_storage_urls(tmp_path):
    path = tmp_path / "hf.json"
    path.write_text(json.dumps(HF))
    hf = cuts.parse_higgsfield(path)
    assert [s["start"] for s in hf["scenes"]] == [0.0, 2.0, 3.0]
    assert "storage" not in json.dumps(hf) and "s3" not in json.dumps(hf)
    scores = [{"frame": 50, "t": 2.0, "score": 0.9}, {"frame": 80, "t": 3.2, "score": 1.0},
              {"frame": 107, "t": 4.3, "score": 0.3}]
    changes = cuts.peaks(scores, cuts.CHANGE)
    bounds = cuts.snap_to_scores(hf, scores, changes)
    assert [(round(b["t"], 2), b["source"]) for b in bounds] == [(0.0, "higgsfield"), (2.0, "higgsfield"),
                                                                 (3.2, "higgsfield"), (4.3, "ffmpeg")]
    scenes = cuts.build_scenes(bounds, 4.4)
    assert scenes[0]["higgsfield"]["label"] == "Open" and scenes[2]["higgsfield"]["label"] == "End"
    assert scenes[2]["snapped"] is True and scenes[3]["starts_with"] == "change"


def test_higgsfield_timestamps_and_bad_files(tmp_path):
    assert cuts._timestamp("1:07") == 67 and cuts._timestamp("1:02:03") == 3723 and cuts._timestamp(4.5) == 4.5
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"result": {"scenes": []}}))
    with pytest.raises(UsageError):
        cuts.parse_higgsfield(bad)


# ---------------------------------------------------------------------------------------------------------------
# The transcript


def _seg(start, end, text, probs=None):
    words = text.split()
    probs = probs or [0.95] * len(words)
    step = (end - start) / max(1, len(words))
    return {"start": start, "end": end, "text": " " + text,
            "words": [{"word": " " + w, "start": start + i * step, "end": start + (i + 1) * step, "probability": p}
                      for i, (w, p) in enumerate(zip(words, probs))]}


LOOPED = {"segments": [
    _seg(0.2, 1.6, "Let me ask you something."),
    _seg(2.6, 4.0, "Find out where you stand.", [0.9, 0.9, 0.2, 0.9, 0.9]),
    _seg(4.1, 5.5, "Find out where you stand."),
    _seg(5.6, 7.0, "Find out where you stand."),
    {"start": 7.1, "end": 7.5, "text": " ", "words": []},
    _seg(8.0, 8.6, "Zzz mumble", [0.05, 0.1]),
    _seg(12.5, 13.0, "after the end"),
]}


def test_the_cleanup_drops_loops_blanks_tail_mumbles_and_anything_past_the_end():
    cleaned = transcript.clean(LOOPED, 12.0)
    assert [s["text"] for s in cleaned["segments"]] == ["Let me ask you something.", "Find out where you stand."]
    reasons = [d["reason"] for d in cleaned["dropped"]]
    assert reasons.count("repeats the segment before it (a Whisper loop)") == 2
    assert "empty" in reasons and any("hallucination" in r for r in reasons) and "starts after the audio ends" in reasons
    assert [w["w"] for w in cleaned["low_confidence"]] == ["where"]  # flagged, not changed
    assert "where" in cleaned["segments"][1]["text"]


def test_pace_stats_and_the_scene_join():
    cleaned = transcript.clean(LOOPED, 12.0)
    st = transcript.stats(cleaned["words"])
    assert st["words"] == 10 and st["first_s"] == 0.2 and st["last_s"] == 4.0
    assert st["pauses"] and st["pauses"][0]["gap"] > transcript.PAUSE_S
    scenes = [{"id": 1, "start": 0.0, "end": 2.0}, {"id": 2, "start": 2.0, "end": 12.0}]
    transcript.join_scenes(scenes, cleaned["segments"])
    assert scenes[0]["vo"] == "Let me ask you something." and scenes[0]["vo_words"] == 5
    assert scenes[1]["vo_first"] == 2.6 and scenes[1]["low_conf"][0].startswith("where@")
    assert transcript.stats([])["wpm"] is None


def test_whisper_missing_is_unchecked_not_a_crash(slate_home, monkeypatch, tmp_path):
    monkeypatch.delenv("SLATE_FAKE_VENDORS", raising=False)
    from slate import procs
    from slate.errors import MissingPrerequisite

    def no_whisper(tool, hint=None):
        raise MissingPrerequisite("whisper isn't installed or isn't on PATH.", hint="Install openai-whisper.")

    monkeypatch.setattr(procs, "require", no_whisper)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"")
    result, data = transcript.run(audio, {"sha256": "0" * 64, "duration_s": 1.0}, tmp_path, language="en", source="mix")
    assert result.status == "UNCHECKED" and data == {} and "Install" in " ".join(result.evidence)


# ---------------------------------------------------------------------------------------------------------------
# Demucs and librosa through uvx


def _tone_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    t = np.arange(int(seconds * rate)) / rate
    data = (0.25 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return path


def test_demucs_runs_offline_by_default_and_is_unchecked_without_its_pieces(tmp_path, monkeypatch):
    wav = _tone_wav(tmp_path / "audio" / "mix.wav")
    assert "--offline" in stems.command(wav, tmp_path) and "--offline" not in stems.command(wav, tmp_path, offline=False)
    assert "demucs==4.1.0" in stems.command(wav, tmp_path) and "--two-stems=vocals" in stems.command(wav, tmp_path)
    monkeypatch.setattr(stems.procs, "which", lambda tool: None)
    r, _ = stems.run(wav, tmp_path / "stems", duration_s=1.0)
    assert r.status == "UNCHECKED" and "uv" in r.summary
    monkeypatch.setattr(stems.procs, "which", lambda tool: "/usr/bin/true")
    monkeypatch.setattr(stems, "weights_cached", lambda: False)
    r, _ = stems.run(wav, tmp_path / "stems", duration_s=1.0)
    assert r.status == "UNCHECKED" and any("--allow-download" in e for e in r.evidence)


def test_demucs_output_is_found_and_measured(tmp_path, monkeypatch):
    wav = _tone_wav(tmp_path / "audio" / "mix.wav")
    monkeypatch.setattr(stems.procs, "which", lambda tool: "/usr/bin/true")
    monkeypatch.setattr(stems, "weights_cached", lambda: True)

    def fake_demucs(argv, timeout=None):
        out = Path(argv[argv.index("-o") + 1]) / stems.MODEL / wav.stem
        _tone_wav(out / "vocals.wav")
        _tone_wav(out / "no_vocals.wav")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    r, data = stems.run(wav, tmp_path / "stems", duration_s=1.0, runner=fake_demucs)
    assert r.status == "PASS" and data["vocals"] == "stems/htdemucs/mix/vocals.wav"
    assert data["levels"]["music"]["integrated_lufs"] < 0
    r, _ = stems.run(wav, tmp_path / "nothing", duration_s=1.0,
                     runner=lambda argv, timeout=None: types.SimpleNamespace(returncode=2, stdout="",
                                                                            stderr="error: offline, no cache"))
    assert r.status == "UNCHECKED" and "allow-download" in r.summary


def test_librosa_beats_parse_or_are_unchecked(tmp_path, monkeypatch):
    wav = _tone_wav(tmp_path / "m.wav")
    monkeypatch.setattr(beats.procs, "which", lambda tool: "/usr/bin/true")
    good = {"tempo_bpm": 117.5, "beats": [0.5, 1.01, 1.52, 2.03], "interval_mean_s": 0.51, "interval_std_s": 0.01,
            "librosa": "0.11.0"}
    r, data = beats.run(wav, source="music stem",
                        runner=lambda argv, timeout=None: types.SimpleNamespace(returncode=0, stdout=json.dumps(good), stderr=""))
    assert r.status == "PASS" and data["tempo_bpm"] == 117.5 and data["source"] == "music stem"
    assert data["steady"] and data["tempo_from_beats_bpm"] == round(60 / 0.51, 2) and "steady beat grid" in r.summary
    loose = dict(good, interval_std_s=0.2)
    r, data = beats.run(wav, source="mix",
                        runner=lambda argv, timeout=None: types.SimpleNamespace(returncode=0, stdout=json.dumps(loose), stderr=""))
    assert not data["steady"] and r.summary.startswith("about 117.5 BPM (librosa's estimate")
    r, _ = beats.run(wav, source="mix",
                     runner=lambda argv, timeout=None: types.SimpleNamespace(returncode=1, stdout="", stderr="offline"))
    assert r.status == "UNCHECKED"
    assert "--offline" in beats.command(wav) and "librosa==0.11.0" in beats.command(wav)


# ---------------------------------------------------------------------------------------------------------------
# On-screen text


class ScriptedOCR:
    engine = {"name": "scripted OCR (test)", "version": "1"}

    def recognize(self, image):
        return [{"text": "Hello there", "conf": 0.98, "box": [0.1, 0.1, 0.5, 0.1]},
                {"text": "noise", "conf": 0.2, "box": [0.1, 0.5, 0.2, 0.1]}]


def test_ocr_reads_each_scenes_settled_frame_through_the_one_engine(tmp_path, monkeypatch):
    from PIL import Image

    from slate.qa import ocr

    (tmp_path / "frames" / "settled").mkdir(parents=True)
    Image.new("RGB", (64, 36), "white").save(tmp_path / "frames" / "settled" / "s001.jpg")
    scenes = [{"id": 1, "settled_frame": "frames/settled/s001.jpg"}, {"id": 2}]
    with ocr.use_backend(ScriptedOCR()):
        r, data = onscreen.run(scenes, tmp_path)
    assert r.status == "PASS" and scenes[0]["ocr"] == ["Hello there"] and data["frames_read"] == 1
    monkeypatch.setattr(ocr, "available", lambda refresh=False: (False, {"name": "Apple Vision (ocrmac)",
                                                                          "reason": "ocrmac isn't importable",
                                                                          "hint": "install the ocr extra"}))
    r, _ = onscreen.run(scenes, tmp_path)
    assert r.status == "UNCHECKED" and "install the ocr extra" in r.evidence[0]


# ---------------------------------------------------------------------------------------------------------------
# Finding and following moves


def test_the_finder_needs_stillness_on_both_sides_and_never_crosses_a_cut():
    e = np.zeros(60)
    e[10:20] = 2.0  # a move with rest around it
    e[40] = 50.0  # a cut: one huge frame
    e[55:60] = 3.0  # a move running off the end of the file
    moves = eases.find_moves(e, cuts=[40])
    assert [(m["change_from"], m["change_to"]) for m in moves] == [(10, 19)]
    assert moves[0]["first"] == 10 - eases.REST_PAD and moves[0]["last"] == 19 + eases.REST_PAD
    tails = np.zeros(40)
    tails[10:20] = 2.0
    tails[8:10] = 0.05  # a slow start below the active floor still belongs to the move
    tails[20:23] = 0.05
    got = eases.find_moves(tails)
    assert got[0]["change_from"] == 8 and got[0]["change_to"] == 22


def _moving(bg, fg, xs, size=12, h=48, w=96, textured=False):
    rng = np.random.default_rng(7)
    base = np.zeros((h, w, 3), np.uint8) + np.array(bg, np.uint8)
    if textured:
        base = (rng.integers(0, 255, (h, w, 3))).astype(np.uint8)
    frames = []
    for x in xs:
        f = base.copy()
        f[18:18 + size, int(x):int(x) + size] = fg
        frames.append(f)
    return np.stack(frames)


def test_the_tracker_follows_an_element_on_flat_and_textured_backgrounds():
    xs = [10, 10, 10, 14, 24, 40, 56, 66, 70, 70, 70]
    for textured, how in ((False, "flat"), (True, "two-pose")):
        stack = _moving((30, 30, 30), (250, 250, 250), xs, textured=textured)
        masks, label = eases.element_masks(stack)
        assert how in label
        chans = eases.channels_from_masks(masks)
        assert np.allclose(chans["left"], xs)
        picked = eases.pick_channel(chans, 96, 48)
        assert picked is not None and picked[0] in ("centroid_x", "left", "right")


def test_a_wandering_or_tiny_signal_is_not_a_move():
    still = {"centroid_x": np.array([10.0, 10.2, 9.9, 10.1, 10.0])}
    assert eases.pick_channel(still, 100, 100) is None
    wander = {"centroid_x": np.array([10.0, 40.0, 5.0, 45.0, 12.0, 50.0])}
    assert eases.pick_channel(wander, 100, 100) is None


def test_named_curves_are_matched_rest_to_rest_with_their_own_timing():
    from slate.motion.eases import evaluate, parse

    fps = 25.0
    true = parse([0.05, 0.7, 0.1, 1.0])
    t = np.arange(0, 40) / fps
    a, b = 0.3, 1.3
    v = 100 + 200 * np.array([evaluate(true, (x - a) / (b - a)) for x in t])
    rows = eases._named(t, v, library.candidates())
    assert rows[0]["name"] == "m3-emphasized-decelerate" and rows[0]["rms"] < 0.002
    assert abs(rows[0]["start_s"] - a) < 0.02 and abs(rows[0]["end_s"] - b) < 0.02


def test_brand_eases_come_first_and_a_missing_pack_is_a_note(slate_home):
    brand = slate_home["brands"] / "curvy"
    brand.mkdir()
    (brand / "brand.json").write_text(json.dumps({"name": "curvy", "motion": {"eases": {"arrive": [0.2, 0.8, 0.2, 1.0]}}}))
    curves, note = library.brand_eases("curvy")
    assert note is None and set(curves) == {"arrive"}
    assert library.candidates(curves)[0]["family"] == "brand"
    none, why = library.brand_eases("no-such-brand")
    assert none == {} and "public curves only" in why


def test_moves_files_are_checked(tmp_path):
    good = tmp_path / "moves.json"
    good.write_text(json.dumps({"moves": [{"t0": 0.1, "t1": 0.9}]}))
    assert eases.load_specs(good)[0]["name"] == "move-01"
    for bad in ([], [{"t0": 1}], [{"t0": 1.0, "t1": 0.5}], [{"t0": 0, "t1": 1, "metric": "wobble"}]):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad))
        with pytest.raises(UsageError):
            eases.load_specs(p)


def test_filled_measurements_count_only_well_covered_columns_and_rows():
    m = np.zeros((1, 10, 20), dtype=bool)
    m[0, :, 4:8] = True  # four fully covered columns
    m[0, :3, 12] = True  # a column covered 30%
    got = eases.filled_channels(m, fill=0.5)
    assert got["filled_width"][0] == 4 and got["filled_left"][0] == 4 and got["filled_right"][0] == 7
    assert got["filled_centroid_x"][0] == 5.5
    assert eases.filled_channels(m, fill=0.25)["filled_width"][0] == 5
    empty = eases.filled_channels(np.zeros((1, 4, 4), dtype=bool))
    assert empty["filled_width"][0] == 0 and np.isnan(empty["filled_left"][0])


def test_described_masks_measure_from_one_pixel_and_the_finder_ignores_specks():
    m = np.zeros((1, 10, 10), dtype=bool)
    m[0, 2, 3] = True
    assert eases.channels_from_masks(m, min_pixels=1)["left"][0] == 3
    assert np.isnan(eases.channels_from_masks(m)["left"][0])


def test_fill_must_be_a_share(tmp_path):
    p = tmp_path / "moves.json"
    p.write_text(json.dumps([{"t0": 0, "t1": 1, "metric": "filled_width", "fill": 1.5}]))
    with pytest.raises(UsageError):
        eases.load_specs(p)
    p.write_text(json.dumps([{"t0": 0, "t1": 1, "metric": "filled_width", "fill": 0.9}]))
    assert eases.load_specs(p)[0]["fill"] == 0.9


def test_confidence_needs_both_a_small_error_and_enough_samples():
    assert eases.confidence(0.001, 25) == "good"
    assert eases.confidence(0.001, 5) == "fair"  # four or five samples prove little, however well they fit
    assert eases.confidence(0.001, 4) == "rough" and eases.confidence(0.08, 40) == "rough"
