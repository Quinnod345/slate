"""The delivery mix: targets met with fixed gains, stems that sum, the voice where its cues put it, levels by palette."""

import fcntl
import json

import numpy as np
import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.errors import Busy, MissingPrerequisite
from slate.sound import audio, measure, mix, vo
from slate.sound.film import load

import sound_helpers as H

SR = 48000


def _stems_sum_error(fd):
    m = H.read(fd / "sound/mix/final_mix.wav")
    total = sum(H.read(fd / f"sound/mix/stems/{k}.wav") for k in mix.DELIVERY)
    return float(np.max(np.abs(total - m)))


def test_score_and_voice_alone_mix_to_target(sound_film):
    """The first reel's case: only the score and the voice, through the same plumbing."""
    path = mix.run(sound_film)
    rep = jsonio.read(path.with_name("final_mix.json"))
    assert rep["pass"] and rep["kind"] == "mix"
    got = measure.loudness(path)
    assert abs(got["integrated_lufs"] + 14.0) <= 0.5
    assert got["true_peak_dbtp"] <= -1.0 + 1e-6
    info = audio.wav_info(path)
    assert (info.sample_rate, info.bits, info.frames) == (SR, 24, 14 * SR)  # exactly the film's length at 25 fps
    assert _stems_sum_error(sound_film) <= 1e-6
    assert rep["stems_sum_max_abs"] <= 1e-6
    assert rep["music_vs_voice_lu"] == -15.0 and rep["duck_db"] == 4.0
    assert rep["music_check"]["pass"]
    assert not rep["outputs"]["engine_stems"]  # no effects in this film
    assert jsonio.validate(rep, "mix") == []


def test_every_take_lands_on_its_cue(sound_film):
    mix.run(sound_film)
    film = load(sound_film)
    d = audio.mono(H.read(sound_film / "sound/mix/stems/dialogue.wav"))
    for cue in vo.cues(film):
        lag, r = vo.find_take(d, vo.render_take(film, cue), int(round(vo.cue_time(film, cue) * SR)), 2400)
        assert lag == 0 and r > 0.99


def test_music_sits_under_the_voice_and_ducks(sound_film):
    mix.run(sound_film)
    film = load(sound_film)
    dlg, music = H.read(sound_film / "sound/mix/stems/dialogue.wav"), H.read(sound_film / "sound/mix/stems/music.wav")
    rel = measure.integrated(music, SR) - measure.integrated(dlg, SR)
    assert -21.0 < rel < -15.0  # 15 LU under before the 4 dB duck under speech takes a little more off
    score = H.read(sound_film / "sound/score/score.wav")
    ratio = lambda a, b: 20 * np.log10(np.sqrt(np.mean(music[a:b] ** 2)) / np.sqrt(np.mean(score[a:b] ** 2)))  # noqa: E731
    first = vo.cue_time(film, vo.cues(film)[0])
    before = ratio(0, int((first - 0.1) * SR))
    during = ratio(int((first + 0.8) * SR), int((first + 2.0) * SR))
    assert 2.0 < before - during <= 4.05  # the score's own gain is fixed; the only change is the duck (up to 4 dB)


def test_voice_only_without_a_score(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "vonly", score=False)
    rep = jsonio.read(mix.run(fd).with_name("final_mix.json"))
    assert rep["pass"] and rep["music_check"] is None and rep["score_lufs_pre"] is None
    assert not np.any(H.read(fd / "sound/mix/stems/music.wav"))


def test_peaks_in_the_voice_are_limited_in_place(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "plosive", plosives=True)
    rep = jsonio.read(mix.run(fd).with_name("final_mix.json"))
    assert rep["dialogue_limited"] and rep["pass"]
    assert measure.loudness(fd / "sound/mix/final_mix.wav")["true_peak_dbtp"] <= -1.0 + 1e-6
    film = load(fd)
    d = audio.mono(H.read(fd / "sound/mix/stems/dialogue.wav"))
    for cue in vo.cues(film):
        lag, r = vo.find_take(d, vo.render_take(film, cue), int(round(vo.cue_time(film, cue) * SR)), 2400)
        assert lag == 0 and r > 0.95  # the limiter reshapes plosives; it never moves the voice


def _effect_stems(fd, ui_db=-30.0, fx_db=-30.0):
    n = 14 * SR
    ui = np.zeros((n, 2))
    for k, t in enumerate(np.arange(1.0, 13.0, 0.9)):
        h = H.wood_hit(k, pitch=900 + 40 * k)
        i = int(t * SR)
        ui[i:i + len(h)] += h
    ui *= 10 ** (ui_db / 20) / np.max(np.abs(ui))
    rng = np.random.default_rng(9)
    fx = np.zeros((n, 2))
    for t in (2.0, 6.0, 10.5):
        k = int(0.6 * SR)
        i = int(t * SR)
        fx[i:i + k] += rng.standard_normal((k, 2)) * np.hanning(k)[:, None]
    fx *= 10 ** (fx_db / 20) / np.max(np.abs(fx))
    audio.write_wav(fd / "sound/stems/ui.wav", ui, SR)
    audio.write_wav(fd / "sound/stems/fx.wav", fx, SR)


def test_effect_stems_are_calibrated_to_the_palette_levels(sound_film):
    _effect_stems(sound_film)
    rep = jsonio.read(mix.run(sound_film, calibrate=True).with_name("final_mix.json"))
    lv = rep["levels_vs_voice_lu"]
    assert abs(lv["ui"] - (-17.0)) <= 0.3 and abs(lv["fx"] - (-11.2)) <= 0.3  # proof 2's balance
    assert set(rep["outputs"]["engine_stems"]) == {"ui", "fx"}
    assert rep["calibration"]["stem_trim_db"].keys() == {"ui", "fx"}
    assert _stems_sum_error(sound_film) <= 1e-6


def test_brand_palette_levels_win_over_the_starting_point(slate_home):
    H.brand(slate_home["brands"], palette={"levels": {"lu_vs_voice": {"ui": -20.0, "fx": -14.0}}})
    fd = H.film(slate_home["films"], "branded")
    _effect_stems(fd)
    rep = jsonio.read(mix.run(fd, calibrate=True).with_name("final_mix.json"))
    assert abs(rep["levels_vs_voice_lu"]["ui"] + 20.0) <= 0.3 and abs(rep["levels_vs_voice_lu"]["fx"] + 14.0) <= 0.3
    assert rep["palette"]["from_brand"] is True


def test_nothing_to_mix_is_a_missing_prerequisite(slate_home):
    H.brand(slate_home["brands"])
    fd = H.film(slate_home["films"], "empty", voice=False, score=False)
    with pytest.raises(MissingPrerequisite):
        mix.run(fd)
    code, out, _ = run_cli(["mix", "--film", str(fd), "--json"])
    assert code == 3 and json.loads(out)["error"] == "missing_prerequisite"


def test_no_timing_yet_says_run_timing(slate_home):
    fd = slate_home["films"] / "notiming"
    fd.mkdir()
    (fd / "film.json").write_text(json.dumps({"id": "notiming", "fps_num": 25, "fps_den": 1}))
    code, out, _ = run_cli(["mix", "--film", str(fd), "--json"])
    assert code == 3 and "slate timing" in json.loads(out)["hint"]


def test_cli_json_and_dry_run(sound_film):
    code, out, _ = run_cli(["mix", "--film", str(sound_film), "--dry-run", "--json"])
    assert code == 0 and json.loads(out)["dry_run"] and not (sound_film / "sound/mix/final_mix.wav").exists()
    code, out, _ = run_cli(["mix", "--film", str(sound_film), "--json"])
    res = json.loads(out)
    assert code == 0 and res["report"]["pass"] and res["mix"].endswith("final_mix.wav")


def test_a_second_mix_at_once_is_busy(sound_film):
    lock = sound_film / "sound/mix/.mix.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        with pytest.raises(Busy):
            mix.run(sound_film)
        code, _, _ = run_cli(["mix", "--film", str(sound_film)])
        assert code == 5


def test_mix_arrays_refuses_what_it_cannot_meet():
    from slate.errors import Refused

    n = SR * 3
    loud_music = H.pad(3.0, level_db=-3.0)
    with pytest.raises(Refused) as exc:
        # an impossible pair of targets: -3 LUFS with a -12 dBTP ceiling and no voice to limit
        mix.mix_arrays(np.zeros((n, 2)), {}, loud_music, SR, targets={"lufs": -3.0, "tol": 0.5, "true_peak": -12.0},
                       linked_limit=False)
    assert "can't meet" in exc.value.message


def test_the_fixture_film_is_a_valid_film(sound_film):
    assert jsonio.validate(json.loads((sound_film / "film.json").read_text()), "film") == []


def test_the_mix_is_recorded_in_the_films_lock_file(sound_film):
    pytest.importorskip("slate.state.lock")
    mix.run(sound_film)
    from slate.state import lock

    entry = lock.load(sound_film)["files"].get("sound/mix/final_mix.wav")
    assert entry and entry["producer"] == "slate mix" and "timing/vo_cues.json" in entry["inputs"]
