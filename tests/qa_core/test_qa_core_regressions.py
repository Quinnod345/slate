"""Regression tests for the four validator bugs found in the films' QA tools (docs/PLAN.md, "Quality", test 5).

1. 24-bit audio was reported but never gated (validate_film.py computed `ok` with an operator-precedence slip and
   then didn't use it), so a 16-bit mix passed.
2. The silent-stem test was `integrated < −70 LUFS`, which can't fire: ffmpeg's meter reads digital silence as exactly
   −70.0, its floor, so film B's silent ambience stem passed.
3. The banned-word list was duplicated (validate_film.py kept its own copy instead of importing the storyboard's), so
   the picture check and the script check could drift apart.
4. The act-join check (boundary_check.py) had no film switch: it only ever read film A's render list, and it assumed
   the frame size in a reshape.
"""

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from slate import paths
from slate.qa import ocr
from slate.qa.checks import audio as audio_gates
from slate.qa.checks import picture, text

from qa_core_helpers import (ScriptedOCR, moving_square, needs_ffmpeg, paint_codes, tone, write_brand,
                             write_json)


def _sub(result, name):
    return next(c for c in result.details["checks"] if c["check"] == name)


def _calibrated_mix(fb, *, bits=24, sr=48000, target=-14.0):
    """Stems whose sum measures `target` LUFS on ffmpeg's meter (a tone, scaled from one measurement)."""
    from slate.qa import audio, wav

    probe = wav.write(fb.path / "probe.wav", tone(4.0, sr=sr, amp=0.1), sr, 24)
    measured = audio.ebur128(probe)["integrated_lufs"]
    probe.unlink()
    amp = 0.1 * 10 ** ((target - measured) / 20)
    voice = tone(4.0, sr=sr, amp=amp * 0.75)
    music = tone(4.0, sr=sr, amp=amp * 0.25)
    return fb.mix({"dialogue": voice, "music": music}, sr=sr, bits=bits)


# ---------------------------------------------------------------------------------------------- 1. 24-bit
@needs_ffmpeg
def test_regression_16_bit_mix_fails_bit_depth_even_when_loudness_is_perfect(film_builder):
    fb = film_builder()
    _calibrated_mix(fb, bits=16)
    loud = audio_gates.loudness(fb.path)
    assert loud.status == "PASS", loud.summary  # what the old validator saw: right loudness, so PASS overall
    depth = audio_gates.bit_depth(fb.path)
    assert depth.status == "FAIL"
    assert "16-bit pcm, delivery is 24-bit PCM" in depth.summary


def test_regression_bit_depth_gates_float_rate_and_every_stem(film_builder):
    fb = film_builder()
    fb.mix({"dialogue": tone(1.0), "music": tone(1.0, amp=0.05)})
    assert audio_gates.bit_depth(fb.path).status == "PASS"
    fb.wav(fb.MIX, tone(1.0), bits=32, float_format=True)
    assert audio_gates.bit_depth(fb.path).status == "FAIL"
    fb.wav(fb.MIX, tone(1.0, sr=44100), sr=44100)
    r = audio_gates.bit_depth(fb.path)
    assert r.status == "FAIL" and "44100 Hz" in r.summary
    fb.wav(fb.MIX, tone(1.0))
    fb.wav(fb.stem("music"), tone(1.0, amp=0.05), bits=16)
    r = audio_gates.bit_depth(fb.path)
    assert r.status == "FAIL" and "music" in r.summary


def test_regression_bit_depth_follows_film_targets(film_builder):
    fb = film_builder(targets={"lufs": -14.0, "true_peak": -1.0, "bit_depth": 16, "sample_rate": 44100})
    fb.mix({"dialogue": tone(1.0, sr=44100)}, sr=44100, bits=16)
    assert audio_gates.bit_depth(fb.path).status == "PASS"


# ---------------------------------------------------------------------------------------------- 2. silent stems
@needs_ffmpeg
def test_regression_a_digitally_silent_stem_fails(film_builder):
    fb = film_builder()
    fb.mix({"dialogue": tone(3.0), "ambience": np.zeros((48000 * 3, 2))})
    r = audio_gates.silent_stems(fb.path)
    assert r.status == "FAIL"
    assert r.details["silent"] == ["ambience"]
    # the old threshold on the same reading: −70.0 is not < −70, so it would have passed
    ambience_row = next(e for e in r.evidence if e.startswith("ambience"))
    assert "-70.0 LUFS" in ambience_row and "digital silence" in ambience_row


@needs_ffmpeg
def test_a_stem_the_mix_says_is_empty_on_purpose_passes(film_builder):
    """A score-and-voice film has no effects or ambience: `slate mix` still writes those stems (every delivery has
    four) and lists them in final_mix.json's empty_stems. Those are silent on purpose; an unlisted silent stem still
    fails."""
    import json

    fb = film_builder()
    n = 48000 * 3
    fb.mix({"dialogue": tone(3.0), "music": tone(3.0, amp=0.05), "effects": np.zeros((n, 2)),
            "ambience": np.zeros((n, 2))})
    record = fb.path / "sound" / "mix" / "final_mix.json"
    record.write_text(json.dumps({"kind": "mix", "empty_stems": {"effects": "no effect stems in this film",
                                                                 "ambience": "no ambience stems in this film"}}))
    r = audio_gates.silent_stems(fb.path)
    assert r.status == "PASS", r.summary
    assert "ambience and effects are empty on purpose" in r.summary
    assert r.details["empty_on_purpose"] == {"ambience": "no ambience stems in this film",
                                             "effects": "no effect stems in this film"}
    record.write_text(json.dumps({"kind": "mix", "empty_stems": {"effects": "no effect stems in this film"}}))
    r = audio_gates.silent_stems(fb.path)
    assert r.status == "FAIL" and r.details["silent"] == ["ambience"]


def test_regression_silence_rule():
    assert audio_gates.is_silent(-math.inf, -70.0)
    assert audio_gates.is_silent(-40.0, -70.0)  # the meter's floor: no block above the gate
    assert audio_gates.is_silent(-95.0, None)  # no meter, but under the noise floor
    assert not audio_gates.is_silent(-20.0, -31.0)
    assert not audio_gates.is_silent(-20.0, None)


@needs_ffmpeg
def test_regression_a_sparse_stem_is_not_silent(film_builder):
    fb = film_builder()
    one_hit = tone(0.3, amp=0.3, start=2.0, total=4.0)  # a single short sound in four seconds of silence
    fb.mix({"dialogue": tone(4.0), "effects": one_hit, "near_silent": np.full((48000 * 4, 2), 10 ** (-100 / 20))})
    r = audio_gates.silent_stems(fb.path)
    assert r.status == "FAIL" and r.details["silent"] == ["near_silent"]


# ---------------------------------------------------------------------------------------------- 3. one banned list
def _banned_film(film_builder, brand_store, words):
    fb = film_builder(shots=[(1, 30)])
    write_brand(brand_store, banned=words)
    fb.script({"title": "A fixture", "acts": [{"name": "One", "shots": [
        {"id": "S01", "vo": "Nothing forbidden here, surely.", "on_screen": "A clean title",
         "picture": "a forbidden sign on the wall", "notes": "forbidden in a note is only a note"}]}]})
    fb.captions([{"text": "Nothing forbidden here, surely.", "lines": ["Nothing forbidden here, surely."],
                  "start": 0.1, "end": 0.9}])
    # what Whisper heard in the take (the banned-words gate checks the voice too)
    write_json(fb.path / "timing" / "vo_words.json", {"shots": {"S01": {
        "take": "voice/S01.wav", "line": "Nothing forbidden here, surely.", "heard": "Nothing here, surely.",
        "words": [{"w": "Nothing", "start": 0.1, "end": 0.3}]}}})
    frames = paint_codes(moving_square(30, fb.size), [(0, 30, 1)])
    fb.render(1, frames)
    return fb


@needs_ffmpeg
def test_regression_one_banned_list_from_the_brand_pack_for_all_three_surfaces(film_builder, brand_store):
    fb = _banned_film(film_builder, brand_store, ["forbidden"])
    with ocr.use_backend(ScriptedOCR({1: ["FORBIDDEN ZONE"]})):
        r = text.banned_words(fb.path)
    assert r.status == "FAIL"
    surfaces = {h.split(" ")[0] for h in r.details["hits"]}
    assert surfaces == {"script", "captions", "picture"}, r.details["hits"]
    assert "qa-fixture-brand brand pack truth.banned_words" in r.details["list"]
    # the picture direction counts (as it does in the script gate); a production note doesn't
    assert any(".picture" in h for h in r.details["hits"])
    assert not any(".notes" in h for h in r.details["hits"])


@needs_ffmpeg
def test_regression_change_the_pack_and_every_surface_follows(film_builder, brand_store):
    fb = _banned_film(film_builder, brand_store, [])
    with ocr.use_backend(ScriptedOCR({1: ["FORBIDDEN ZONE"]})):
        clean = text.banned_words(fb.path)
        assert clean.status == "PASS", clean.summary  # no built-in list anywhere: an empty pack list finds nothing
        write_brand(brand_store, banned=["surely", "zone"])
        r = text.banned_words(fb.path)
    hits = " ".join(r.details["hits"])
    assert r.status == "FAIL" and "'surely'" in hits and "'ZONE'" in hits


def test_regression_no_banned_list_lives_in_the_qa_code():
    """No module in slate.qa keeps its own list of banned words (a list, tuple or set literal bound to a name with
    "banned" in it): the brand pack is the only list."""
    offenders = []
    for py in sorted((paths.PACKAGE / "qa").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if any("banned" in n.lower() for n in names) and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                    if any(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.value.elts):
                        offenders.append(f"{py.name}:{node.lineno} {names}")
    assert offenders == []


def test_banned_words_unchecked_when_the_pack_does_not_say(film_builder, brand_store):
    fb = film_builder()
    write_brand(brand_store, omit_banned=True)
    r = text.banned_words(fb.path)
    assert r.status == "UNCHECKED" and "truth.banned_words" in r.summary


# ---------------------------------------------------------------------------------------------- 4. every film's joins
def _joined_film(film_builder, *, film_id, fps, size, shots, jump_at_join=False):
    fb = film_builder(film_id=film_id, fps=fps, size=size, shots=shots)
    picture_ = moving_square(fb.total, size, speed=0.5)
    if jump_at_join:
        a1_end = fb.act_range(1)[1]
        picture_[a1_end:] = 255 - picture_[a1_end:]  # everything after the first join changes at once
    for a in sorted({s[0] for s in shots}):
        fin, fout = fb.act_range(a)
        fb.render(a, picture_[fin:fout])
    return fb


@needs_ffmpeg
def test_regression_boundary_runs_for_every_film(film_builder):
    """The old check only ever read film A's renders. Two films with their own sizes, rates and act counts each get
    their own joins checked: a smooth film passes, and the other film's bad join is flagged, not missed."""
    smooth = _joined_film(film_builder, film_id="first", fps=(24, 1), size=(96, 54), shots=[(1, 18), (2, 18), (3, 18)])
    rough = _joined_film(film_builder, film_id="second", fps=(30, 1), size=(54, 96), shots=[(1, 15), (2, 15)],
                         jump_at_join=True)
    r1 = picture.boundary(smooth.path)
    assert r1.status == "PASS", r1.summary
    assert [j["join"] for j in r1.details["joins"]] == ["act 1 → act 2", "act 2 → act 3"]
    r2 = picture.boundary(rough.path)
    assert r2.status == "REVIEW"
    assert r2.details["review_items"][0]["key"] == "join:1-2"
    assert (rough.path / "qa" / "boundaries" / "act1_act2.jpg").is_file()


@needs_ffmpeg
def test_boundary_gap_between_acts_fails(film_builder):
    import json

    fb = film_builder(shots=[(1, 12), (2, 12)])
    film = moving_square(26, fb.size)
    doc = json.loads((fb.path / "timing" / "shots.json").read_text())
    doc["shots"][1].update(frame_in=14, frame_out=26)  # act 2 starts two frames after act 1 ends
    doc["total_frames"] = 26
    write_json(fb.path / "timing" / "shots.json", doc)
    fb.render(1, film[:12], frames_range=(0, 12))
    fb.render(2, film[14:26], frames_range=(14, 26))
    r = picture.boundary(fb.path)
    assert r.status == "FAIL" and "gap" in r.summary


@needs_ffmpeg
def test_boundary_waits_for_a_render_that_spans_its_act(film_builder):
    import json

    fb = film_builder(shots=[(1, 12), (2, 12)])
    fb.render_all()
    reg = json.loads((fb.path / "renders" / "registry.json").read_text())
    reg["entries"][1]["frames"] = [12, 20]  # a partial render (a patch) isn't the act's render
    write_json(fb.path / "renders" / "registry.json", reg)
    r = picture.boundary(fb.path)
    assert r.status == "UNCHECKED" and "act 2's render not there yet" in r.summary


def test_changing_the_brands_banned_list_makes_an_old_verdict_stale(film_builder, brand_store):
    from slate import gates

    fb = film_builder()
    write_brand(brand_store, banned=["forbidden"])
    r = text.banned_words(fb.path)
    assert any(k.endswith("brand.json") for k in r.inputs)
    gates.write_report(fb.path, [r])
    assert gates.load_report(fb.path)["banned_words"].status == r.status
    write_brand(brand_store, banned=["forbidden", "another"])
    assert gates.load_report(fb.path)["banned_words"].status == "STALE"
