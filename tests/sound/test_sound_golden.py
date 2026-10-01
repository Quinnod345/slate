"""Golden data from the three films (read-only; skipped where the film project isn't on the machine).

Two rules shape this file:
- The mix plumbing (meter, stems-sum check, gap check, voice placement, the mix itself) is checked by rerunning it on
  films A, B and C's own inputs and matching the numbers they delivered.
- The *sound* is checked against the p3 pass (proof 3, and proof 2 it perfects), never against the retired v2 sound
  those deliveries carry. When A's p3 mix is delivered (build/audio_p3), its test switches on by itself.

The whole-film mix rerun is heavy (gigabytes in memory, minutes of CPU), so it runs only with SLATE_GOLDEN_HEAVY=1.
"""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from slate import jsonio, procs
from slate.sound import audio, checks, gap, measure, mix, verify, vo
from slate.sound.effects.palette import STARTING_PALETTE
from slate.sound.film import load

FILMS = {"A": "build/audio", "B": "build/B/audio", "C": "build/C/audio"}
TIMING = {"A": "build", "B": "build/B", "C": "build/C"}
P3 = "sound/p3/proof3"
PROOF2 = "sound/proofs"
SR = 48000


def _rec(filmroot, film, name):
    return json.loads((filmroot / FILMS[film] / name).read_text())


# ---------------------------------------------------------------------------------------------------------------
# The plumbing, on the delivered films


@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_the_meter_reads_what_each_film_delivered(filmroot, film):
    rec = _rec(filmroot, film, "final_mix.json")
    ours = measure.loudness(filmroot / FILMS[film] / "final_mix.wav", lra=False)
    assert abs(ours["integrated_lufs"] - rec["final_lufs"]) <= 0.1
    assert abs(ours["true_peak_dbtp"] - rec["final_true_peak_dbtp"]) <= 0.2
    assert audio.wav_info(filmroot / FILMS[film] / "final_mix.wav").frames == rec["samples"]


@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_each_films_stems_sum_to_its_mix(filmroot, film):
    d = filmroot / FILMS[film]
    info = audio.wav_info(d / "final_mix.wav")
    worst, block = 0.0, 480000
    for start in range(0, info.frames, block):
        m, _ = audio.read_wav(d / "final_mix.wav", start, block)
        total = sum(audio.read_wav(d / "stems" / f"{k}.wav", start, block)[0] for k in mix.DELIVERY)
        worst = max(worst, float(np.max(np.abs(total - m))))
    delivered = json.loads((d / "verify.json").read_text())["loudness"]["stems_sum_vs_mix_max_abs"]
    assert worst <= 1e-6 and abs(worst - delivered) <= 1.2e-7  # same answer, to a 24-bit step


@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_the_gap_check_reproduces_each_films_music_check(filmroot, film):
    mc = _rec(filmroot, film, "music_check.json")
    ours = gap.check_file(filmroot / FILMS[film] / "stems" / "music.wav", mc["planned_stops"])
    assert ours["pass"] == mc["pass"] and ours["failures"] == mc["failures"]
    assert abs(ours["quietest_outside_stops_dbfs"] - mc["quietest_outside_stops_dbfs"]) <= 0.1


def _film_from(filmroot, film, tmp_path):
    """A Slate film folder standing in for one of the films: its clock and voice cues, takes by absolute path."""
    src = filmroot / TIMING[film]
    fd = tmp_path / f"golden_{film}"
    (fd / "timing").mkdir(parents=True)
    shots = json.loads((src / "shots.json").read_text())
    (fd / "timing" / "shots.json").write_text(json.dumps(shots))
    cues = json.loads((src / "vo_cues.json").read_text())
    for c in cues["cues"]:
        c["file"] = str(filmroot / c["file"])
    (fd / "timing" / "vo_cues.json").write_text(json.dumps(cues))
    (fd / "film.json").write_text(json.dumps({"id": f"golden-{film.lower()}", "fps_num": shots["fps_num"],
                                              "fps_den": shots["fps_den"]}))
    return fd


@pytest.mark.parametrize("film", ["B", "C"])
def test_every_delivered_take_is_found_exactly_where_its_cue_says(filmroot, film, tmp_path, slate_home):
    fd = _film_from(filmroot, film, tmp_path)
    f = load(fd)
    dlg = audio.mono(audio.read_wav(filmroot / FILMS[film] / "stems" / "dialogue.wav")[0])
    delivered = json.loads((filmroot / FILMS[film] / "verify.json").read_text())["voice"]
    worst, lowest = 0.0, 1.0
    for cue in vo.cues(f):
        lag, r = vo.find_take(dlg, vo.render_take(f, cue), int(round(vo.cue_time(f, cue) * SR)), 2400)
        worst, lowest = max(worst, abs(lag) / SR * 1000), min(lowest, r)
    assert len(vo.cues(f)) == delivered["takes"]
    assert worst < 0.5  # every take within half a millisecond (all exact, as delivered)
    assert lowest >= delivered["min_corr"] - 0.002


# ---------------------------------------------------------------------------------------------------------------
# The sound, against the p3 pass


def _lufs(path):
    x, sr = audio.read_wav(path)
    return measure.integrated(audio.to_stereo(x), sr)


def test_the_starting_palette_is_proof_2s_measured_balance(filmroot):
    stems = filmroot / PROOF2 / "S19-S20_reveal_stems"
    vo_l = _lufs(stems / "vo.wav")
    targets = STARTING_PALETTE["levels"]["lu_vs_voice"]
    for role in ("ui", "fx"):
        assert abs((_lufs(stems / f"{role}.wav") - vo_l) - targets[role]) <= 0.1


def test_proof_3_measures_as_it_recorded(filmroot):
    d = filmroot / P3
    master = json.loads((d / "S19-S20_p3_master.json").read_text())
    rec = json.loads((d / "S19-S20_p3_verify.json").read_text())
    m = measure.loudness(d / "S19-S20_p3_mix.wav", lra=False)
    assert abs(m["integrated_lufs"] - master["final_lufs"]) <= 0.1
    assert abs(m["true_peak_dbtp"] - master["final_true_peak_dbtp"]) <= 0.2
    stems = {s: d / "S19-S20_p3_stems" / f"{s}.wav" for s in ("vo", "ui", "fx", "tonal", "verb")}
    mix_x, _ = audio.read_wav(d / "S19-S20_p3_mix.wav")
    total = sum(audio.read_wav(p)[0] for p in stems.values())
    assert float(np.max(np.abs(total - mix_x))) <= 1e-6
    vo_l = _lufs(stems["vo"])
    for role, rel in rec["balance"]["lu_vs_voice"].items():
        assert abs((_lufs(stems[role]) - vo_l) - rel) <= 0.15
    tonal, _ = audio.read_wav(stems["tonal"])
    g = checks.glide_check(tonal, SR)
    assert g["pass"] == rec["glide"]["pass"] is True
    assert abs(g["tracks"] - rec["glide"]["tracks"]) <= 2 and abs(g["max_abs_drift_cents"] - rec["glide"]["max_abs_drift_cents"]) <= 1.0


def test_proof_2s_hum_is_the_glide_checks_positive_control(filmroot):
    tonal, _ = audio.read_wav(filmroot / PROOF2 / "S19-S20_reveal_stems" / "tonal.wav")
    g = checks.glide_check(tonal, SR)
    assert not g["pass"] and g["slide_ups"]  # the hum that "sounds like an air raid siren"


def test_the_sound_reference_is_proof_3_never_the_v2_sound(filmroot, slate_home):
    """A brand whose approved sound reference is proof 3: verify measures its levels from proof 3's own stems."""
    ref = filmroot / P3 / "S19-S20_p3_mix.wav"
    assert "build/audio" not in str(ref) and "/v2/" not in str(ref)
    brand = {"name": "golden", "sound": {}, "references": {"sound": {"file": str(ref), "sha256": jsonio.sha256(ref),
                                                                     "approved_by": "golden test (record only)"}}}
    (slate_home["brands"] / "golden").mkdir()
    (slate_home["brands"] / "golden" / "brand.json").write_text(json.dumps(brand))
    fd = slate_home["films"] / "g"
    (fd / "timing").mkdir(parents=True)
    (fd / "timing" / "shots.json").write_text(json.dumps({"fps_num": 24, "fps_den": 1, "total_frames": 24, "shots": []}))
    (fd / "film.json").write_text(json.dumps({"id": "g", "brand": "golden", "fps_num": 24, "fps_den": 1}))
    levels, why, _ = verify.reference_levels(load(fd))
    rec = json.loads((filmroot / P3 / "S19-S20_p3_verify.json").read_text())["balance"]["lu_vs_voice"]
    assert "S19-S20_p3_stems" in why
    for role, rel in rec.items():
        assert abs(levels[role] - rel) <= 0.15


def test_as_p3_film_mix_when_it_is_delivered(filmroot):
    """A's p3 mix (being built as this was written, not yet approved): Slate's tools must measure it exactly as the p3
    pass did, whatever its verdict. Skips while it's absent or mid-build."""
    d = filmroot / "build" / "audio_p3"
    rec_path = next((d / n for n in ("final_mix.json", "master.json") if (d / n).exists()), None)
    mix_path = d / "final_mix.wav"
    if not mix_path.exists() or rec_path is None:
        pytest.skip("A's p3 mix isn't delivered yet (build/audio_p3); this test switches on when it is")
    watched = [mix_path, rec_path, d / "music_check.json", d / "stems" / "music.wav"]
    stamp = [p.stat().st_mtime if p.exists() else None for p in watched]
    rec = json.loads(rec_path.read_text())
    if rec.get("samples") != audio.wav_info(mix_path).frames:
        pytest.skip("A's p3 mix is mid-build (its record and its WAV disagree on length)")
    m = measure.loudness(mix_path, lra=False)
    mc = json.loads((d / "music_check.json").read_text()) if (d / "music_check.json").exists() else None
    ours_gap = gap.check_file(d / "stems" / "music.wav", mc["planned_stops"]) if mc else None
    if [p.stat().st_mtime if p.exists() else None for p in watched] != stamp:
        pytest.skip("A's p3 mix was rebuilt while it was being measured")
    assert abs(m["integrated_lufs"] - rec["final_lufs"]) <= 0.1
    assert abs(m["true_peak_dbtp"] - rec["final_true_peak_dbtp"]) <= 0.2
    if mc:
        assert ours_gap["pass"] == mc["pass"]
        assert [f["t"] for f in ours_gap["failures"]] == [f["t"] for f in mc["failures"]]
        assert abs(ours_gap["quietest_outside_stops_dbfs"] - mc["quietest_outside_stops_dbfs"]) <= 0.1


# ---------------------------------------------------------------------------------------------------------------
# The whole mix, rerun from a film's inputs (heavy)

# Each film's delivery mix and the inputs it was made from (sound/tools/final_mix.py in the films): the voice and effect
# stems the effects engine rendered, the composed score, and the record it wrote. B and C are the v2 sound (an effects
# ceiling at -7 dBTP and the linked limiter). A is checked twice: from build/audio/film_sound_stems, its v1 layout (no
# effects ceiling, no linked limiter; that mix is archived with its record, since build/audio now carries A's v2 sound),
# and as delivered, from build/audio_v2/film_v2_stems with the repaired score (build/audio/final_mix.json is a copy of
# build/audio_v2's). B and C land within tolerance on Slate's own voice limiter. A's voice has more for a limiter to
# do, and Slate's gain-curve limiter takes about 0.15 dB more gain on it than the films' limiter (ffmpeg's alimiter at
# 4x), right on the gain's tolerance; with the films' own limiter (mix_arrays keeps it for exactly this) A lands within
# 0.07 LU on every stem.
HEAVY = {
    "C": {"stems": "build/C/audio/film_v2_stems", "score": "sound/music/C/score_C.wav", "record": "build/C/audio",
          "limiter": "slate"},
    "B": {"stems": "build/B/audio/film_v2_stems", "score": "sound/music/B/score_B.wav", "record": "build/B/audio",
          "limiter": "slate"},
    "A": {"stems": "build/audio/film_sound_stems", "score": "sound/music/score_film.wav",
          "record": "build/renders.nosync/archive_A_draft-2.4/audio_v1", "limiter": "ffmpeg"},
    "A-delivered": {"stems": "build/audio_v2/film_v2_stems", "score": "sound/music/score_film_v2.wav",
                    "record": "build/audio", "limiter": "ffmpeg"},
}


def _mixes_again(filmroot, film):
    """A film's whole mix, rerun from its own inputs with the films' settings (15 LU under the voice, ducked 4 dB; the
    record's effects ceiling and, when it has one, the linked limiter; no high-pass, as final_mix.py ran), must land
    on the record it delivered within these tolerances: the voice 0.1 LU, the gain 0.15 dB, the loudness 0.1 LU, the
    true peak 0.25 dB, each delivery stem 0.15 LU, and the stems summing to the mix to a 24-bit step."""
    spec = HEAVY[film]
    d = filmroot / spec["record"]
    if not (d / "final_mix.json").exists() or not (filmroot / spec["stems"]).is_dir():
        pytest.skip(f"{film}'s mix or its inputs aren't on this machine")
    if spec["limiter"] == "ffmpeg" and not procs.which("ffmpeg"):
        pytest.skip("the films' limiter runs through ffmpeg, which isn't installed")
    rec = json.loads((d / "final_mix.json").read_text())
    n = rec["samples"]
    ins = filmroot / spec["stems"]
    vo_stem = audio.load(ins / "vo.wav", SR, n)
    stems = {r: audio.load(ins / f"{r}.wav", SR, n) for r in ("ui", "fx", "verb", "amb") if (ins / f"{r}.wav").exists()}
    score = audio.load(filmroot / spec["score"], SR, n)
    res = mix.mix_arrays(vo_stem, stems, score, SR, targets=rec["target"], under_lu=-float(rec["music_vs_voice_lu"]),
                         duck_db=float(rec["duck_db"]), effects_ceiling_dbtp=rec.get("effects_ceiling_dbtp"),
                         linked_limit="linked_limiter" in rec, highpass_hz=None, limiter=spec["limiter"])
    del vo_stem, stems, score
    ours = res["report"]
    assert abs(ours["voice_lufs_pre"] - rec["voice_lufs_pre"]) <= 0.1
    assert abs(ours["gain_db"] - rec["gain_db"]) <= 0.15
    assert abs(ours["final_lufs"] - rec["final_lufs"]) <= 0.1
    assert abs(ours["final_true_peak_dbtp"] - rec["final_true_peak_dbtp"]) <= 0.25
    assert ours["dialogue_limited"] == rec["dialogue_limited"]
    if "effects_limited" in rec:
        assert ours["effects_limited"] == rec["effects_limited"]
    for k in mix.DELIVERY:
        theirs = audio.read_wav(d / "stems" / f"{k}.wav")[0]
        if not np.any(theirs):
            assert not np.any(res["final"][k])
            continue
        a, b = measure.integrated(res["final"][k], SR), measure.integrated(theirs, SR)
        assert abs(a - b) <= 0.15, (k, a, b)
        del theirs
    assert mix.quantized_sum_error(res["final"], res["mix"]) <= 1e-6
    return ours


@pytest.mark.skipif(not os.environ.get("SLATE_GOLDEN_HEAVY"), reason="a whole-film mix: set SLATE_GOLDEN_HEAVY=1")
def test_c_mixes_again_from_its_own_inputs(filmroot):
    _mixes_again(filmroot, "C")


@pytest.mark.skipif(not os.environ.get("SLATE_GOLDEN_HEAVY"), reason="a whole-film mix: set SLATE_GOLDEN_HEAVY=1")
def test_b_mixes_again_from_its_own_inputs(filmroot):
    """B is the longest (442 s): its linked limiter took up to 1.03 dB off, and the rerun does the same."""
    ours = _mixes_again(filmroot, "B")
    assert ours["linked_limiter"] is not None


@pytest.mark.skipif(not os.environ.get("SLATE_GOLDEN_HEAVY"), reason="a whole-film mix: set SLATE_GOLDEN_HEAVY=1")
@pytest.mark.parametrize("film", ["A", "A-delivered"])
def test_a_mixes_again_from_its_own_inputs(filmroot, film):
    _mixes_again(filmroot, film)
