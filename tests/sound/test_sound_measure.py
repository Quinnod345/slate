"""The loudness meter against signals with known answers (ITU-R BS.1770-4, EBU Tech 3341 and 3342)."""

import numpy as np
import pytest

from slate.sound import audio, measure

import sound_helpers as H

SR = 48000


def test_k_weighting_matches_the_bs1770_table_at_48k():
    sos = measure.k_weighting_sos(48000)
    # BS.1770-4 Table 1 (stage 1) and Table 2 (stage 2), as printed
    assert np.allclose(sos[0, :3], [1.53512485958697, -2.69169618940638, 1.19839281085285], atol=1e-9)
    assert np.allclose(sos[0, 4:], [-1.69065929318241, 0.73248077421585], atol=1e-9)
    assert np.allclose(sos[1, :3], [1.0, -2.0, 1.0])
    assert np.allclose(sos[1, 4:], [-1.99004745483398, 0.99007225036621], atol=1e-9)


@pytest.mark.parametrize("level", [-20.0, -23.0, -33.0])
def test_stereo_1khz_sine_reads_its_level(level):
    # EBU Tech 3341 cases 1 and 2: a stereo 1 kHz sine at L dBFS reads L LUFS (±0.1)
    r = measure.loudness(H.sine(level, 20.0), SR)
    assert abs(r["integrated_lufs"] - level) <= 0.1


def test_mono_sine_is_3db_quieter_than_the_same_on_two_channels():
    r = measure.loudness(H.sine(-20.0, 20.0, ch=1), SR)
    assert abs(r["integrated_lufs"] - (-23.01)) <= 0.1


def test_relative_gate_ignores_the_quiet_parts():
    # EBU Tech 3341 case 3: -36 / -23 / -36 dBFS reads -23 LUFS
    x = np.concatenate([H.sine(-36, 10), H.sine(-23, 60), H.sine(-36, 10)])
    assert abs(measure.loudness(x, SR)["integrated_lufs"] + 23.0) <= 0.1


def test_absolute_gate_ignores_near_silence():
    # EBU Tech 3341 case 4: -72 / -36 / -23 / -36 / -72 dBFS reads -23 LUFS
    x = np.concatenate([H.sine(-72, 10), H.sine(-36, 10), H.sine(-23, 60), H.sine(-36, 10), H.sine(-72, 10)])
    assert abs(measure.loudness(x, SR)["integrated_lufs"] + 23.0) <= 0.1


def test_level_steps_average_by_power():
    # EBU Tech 3341 case 5: -26 / -20 / -26 dBFS (20 s, 20.1 s, 20 s) reads -23 LUFS
    x = np.concatenate([H.sine(-26, 20), H.sine(-20, 20.1), H.sine(-26, 20)])
    assert abs(measure.loudness(x, SR)["integrated_lufs"] + 23.0) <= 0.1


@pytest.mark.parametrize("a,b,lra", [(-20, -30, 10.0), (-20, -15, 5.0), (-40, -20, 20.0)])
def test_loudness_range_on_level_steps(a, b, lra):
    # EBU Tech 3342 cases 1-3
    x = np.concatenate([H.sine(a, 20), H.sine(b, 20)])
    assert abs(measure.loudness(x, SR)["lra_lu"] - lra) <= 1.0


def test_true_peak_finds_the_peak_between_samples():
    # a quarter-rate sine at 45° never has a sample on its crest: sample peak -9.03 dBFS, true peak -6.02 dBTP
    x = H.sine(-6.0206, 3.0, f=SR / 4, phase=np.pi / 4, fade_s=0.05)
    r = measure.loudness(x, SR)
    assert abs(r["sample_peak_dbfs"] + 9.03) <= 0.05
    assert -6.02 - 0.4 <= r["true_peak_dbtp"] <= -6.02 + 0.2  # EBU Tech 3341's true-peak tolerance


def test_true_peak_of_a_low_sine_equals_its_sample_peak():
    r = measure.loudness(H.sine(-3.0, 2.0, f=997.0, fade_s=0.05), SR)
    assert abs(r["true_peak_dbtp"] - r["sample_peak_dbfs"]) <= 0.01


def test_streaming_chunks_match_one_block():
    # the meter streams films in 20 s chunks; the answer must not depend on where the chunks fall
    rng = np.random.default_rng(4)
    x = 0.2 * rng.standard_normal((SR * 47, 2)) * np.sin(np.linspace(0, 20, SR * 47))[:, None]
    whole = measure.loudness(x, SR)
    env = measure.peak_envelope(x, SR)
    assert abs(whole["true_peak_dbtp"] - 20 * np.log10(env.max())) <= 1e-9
    assert abs(whole["integrated_lufs"] - measure.integrated(x, SR)) <= 1e-9


def test_silence_reports_the_floor():
    r = measure.loudness(np.zeros((SR * 5, 2)), SR)
    assert r["silent"] and r["integrated_lufs"] == measure.SILENT_FLOOR
    assert measure.lu_vs(-20.0, measure.SILENT_FLOOR) is None


def test_files_measure_like_arrays(tmp_path):
    x = H.sine(-18.0, 6.0, f=440.0, fade_s=0.1)
    p = audio.write_wav(tmp_path / "t.wav", x, SR, 24)
    a, b = measure.loudness(p), measure.loudness(x, SR)
    assert abs(a["integrated_lufs"] - b["integrated_lufs"]) < 1e-4
    assert abs(a["true_peak_dbtp"] - b["true_peak_dbtp"]) < 1e-3
    assert a["engine"]["name"] == "slate bs1770-4"


def test_other_sample_rates_have_their_own_k_weighting():
    # the 44.1 kHz filters differ from the 48 kHz table but read the same sine the same
    x = H.sine(-20.0, 10.0, sr=44100)
    assert abs(measure.loudness(x, 44100)["integrated_lufs"] + 20.0) <= 0.1
    assert not np.allclose(measure.k_weighting_sos(44100), measure.k_weighting_sos(48000))


def test_cross_check_agrees_with_ffmpeg(tmp_path):
    if measure.ffmpeg_version() is None:
        pytest.skip("ffmpeg isn't installed here")
    rng = np.random.default_rng(3)
    prog = np.concatenate([H.sine(-18, 8, fade_s=0.2), H.pad(6.0, level_db=-22.0, fade_in=0.5, fade_out=0.5),
                           0.05 * rng.standard_normal((SR * 4, 2)) * np.hanning(SR * 4)[:, None]])
    p = audio.write_wav(tmp_path / "prog.wav", prog, SR, 24)
    cc = measure.cross_check(p)
    assert cc["available"] and cc["agree"], cc
    assert abs(cc["delta_integrated_lu"]) <= 0.1
    assert abs(cc["delta_true_peak_db"]) <= 0.2
    assert abs(cc["slate"]["lra_lu"] - cc["ffmpeg"]["lra_lu"]) <= 1.0


def test_window_rms_matches_the_films_gap_windows():
    x = np.concatenate([H.sine(-10, 1.0), np.zeros((SR // 2, 2)), H.sine(-10, 1.0)])
    wins = measure.window_rms_dbfs(x, SR, 0.5)
    assert len(wins) == 5 and wins[2][1] < -200 + 100  # the silent window is at the floor
    assert abs(wins[0][1] - (-13.01)) < 0.05  # a sine's RMS is 3 dB under its peak
