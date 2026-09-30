"""DSP the mix and the assembler share: joins, the follower, the limiter (and its latency regression), freeze."""

import numpy as np
import pytest
from scipy import signal

from slate.sound import checks, dsp, measure, vo

import sound_helpers as H

SR = 48000


def _noise(seed, seconds, db=-20.0):
    x = np.random.default_rng(seed).standard_normal((int(seconds * SR), 2))
    return x * 10 ** (db / 20) / np.sqrt(np.mean(x ** 2))


def _level_track(x, win_s=0.05):
    w = int(win_s * SR)
    k = len(x) // w
    return 10 * np.log10((x[: k * w] ** 2).reshape(k, w, 2).mean(axis=(1, 2)))


def test_equal_power_gains_keep_constant_power():
    up, down = dsp.equal_power(4800, True), dsp.equal_power(4800, False)
    assert np.allclose(up ** 2 + down ** 2, 1.0)
    assert up[0] == 0.0 and abs(up[-1] - 1.0) < 1e-12


def test_equal_power_join_between_two_takes_holds_the_level():
    # two different takes (uncorrelated) at the same level, crossfaded over 1.2 s: no dip anywhere
    a, b = _noise(1, 4.0), _noise(2, 4.0)
    n = int(1.2 * SR)
    joined = dsp.crossfade(a, b, n)
    lv = _level_track(joined)
    assert np.max(np.abs(lv - (-20.0))) < 0.6


def test_squared_gains_dip_three_db_which_is_why_joins_are_equal_power():
    # the first score assembly used sin²/cos² amplitudes: they sum to one in amplitude, so two different takes lose
    # half their power halfway through the join (-3 dB). Equal power (sin/cos amplitudes) doesn't.
    a, b = _noise(3, 4.0), _noise(4, 4.0)
    n = int(1.2 * SR)
    t = np.linspace(0, np.pi / 2, n)
    squared = np.concatenate([a[:-n], a[-n:] * (np.cos(t) ** 2)[:, None] + b[:n] * (np.sin(t) ** 2)[:, None], b[n:]])
    mid = len(a) - n + n // 2
    dip = 10 * np.log10(np.mean(squared[mid - 2400:mid + 2400] ** 2)) - (-20.0)
    good = dsp.crossfade(a, b, n)
    flat = 10 * np.log10(np.mean(good[mid - 2400:mid + 2400] ** 2)) - (-20.0)
    assert -3.5 < dip < -2.5
    assert abs(flat) < 0.4


def test_follower_is_one_in_speech_and_zero_in_silence():
    y, _ = H.speech(3.0, seed=1, level=0.35)
    key = np.concatenate([np.zeros((SR, 2)), y, np.zeros((2 * SR, 2))])
    env = dsp.follower(key, SR)
    assert env[: SR // 2].max() < 1e-6
    assert env[SR + SR // 2: SR + 2 * SR].max() > 0.9
    assert env[-SR // 4:].max() < 0.05  # released by the end (650 ms release)


def _plosive_voice():
    y, _ = H.speech(4.0, seed=7, level=0.45, plosives=True)
    return np.concatenate([np.zeros((SR // 2, 2)), y, np.zeros((SR // 2, 2))])


def test_limiter_holds_the_true_peak_and_leaves_the_rest_alone():
    # a steady tone with three short spikes: only the spikes' neighbourhoods may move
    x = H.sine(-20.0, 3.0, f=440.0)
    spikes = (0.7, 1.5, 2.3)
    for t in spikes:
        i = int(t * SR)
        x[i:i + 48] += 0.8 * np.hanning(96)[:48, None]
    y = dsp.limit(x, -10.0, SR)
    assert measure.true_peak(y, SR) <= -10.0 + 0.02
    far = np.ones(len(x), bool)
    for t in spikes:
        far[int((t - 0.02) * SR):int((t + 0.7) * SR)] = False  # 5 ms before; after, the 60 ms release until it lets go
    assert np.array_equal(y[far], x[far])


def test_limiter_latency_regression_the_voice_never_moves():
    """The films' dialogue limiter was ffmpeg's alimiter, whose 5 ms lookahead put the voice 240 samples late until
    `latency=1` was added. Slate's limiter applies its gain where the signal is: the limited voice lines up with the
    unlimited one at exactly zero lag."""
    x = _plosive_voice()
    y = dsp.limit(x, measure.true_peak(x, SR) - 3.0, SR)
    lag, r = vo.find_take(y, x, 0, 2400)
    assert lag == 0 and r > 0.97


def test_ffmpeg_limiter_is_compensated_and_would_be_late_without_it(tmp_path):
    from slate import procs
    from slate.sound import audio

    if not procs.which("ffmpeg"):
        pytest.skip("ffmpeg isn't installed here")
    x = _plosive_voice()
    ceiling = measure.true_peak(x, SR) - 3.0
    args = dsp.ffmpeg_limiter_args(ceiling, SR)
    assert "latency=1" in args
    y = dsp.limit_ffmpeg(x, ceiling, SR)
    lag, r = vo.find_take(y, x, 0, 2400)
    assert lag == 0 and r > 0.97
    # the same filter without the compensation lands the voice 240 samples (5 ms at 48 kHz) late: the regression
    src = audio.write_wav(tmp_path / "in.wav", x, SR, bits=32)
    late = procs.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-af", args.replace("latency=1", "latency=0"),
                      "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"], text=False, check=True)
    z = np.frombuffer(late.stdout, dtype="<f4").reshape(-1, 2).astype(np.float64)
    lag_late, _ = vo.find_take(audio.fit(z, len(x)), x, 0, 2400)
    assert abs(lag_late - 240) <= 2


def test_linked_gain_keeps_stems_summing():
    a, b = _noise(5, 3.0, -8.0), _plosive_voice()[: 3 * SR]
    mix = a + b
    curve = dsp.linked_gain(mix, -3.0, SR)[:, None]
    assert measure.true_peak(mix * curve, SR) <= -3.0 + 0.02
    assert np.max(np.abs((a * curve + b * curve) - mix * curve)) < 1e-12


def test_freeze_holds_a_chord_at_its_pitch():
    chord = H.pad(1.0, notes=(220.0, 293.66, 369.99), level_db=-20.0)
    held = dsp.freeze(chord[int(0.2 * SR):int(0.8 * SR)], 3.0, SR, seed=1)
    assert len(held) == 3 * SR
    assert abs(10 * np.log10(np.mean(held ** 2)) - (-20.0)) < 1.5
    spec = np.abs(np.fft.rfft(held[:, 0] * np.hanning(len(held))))
    f = np.fft.rfftfreq(len(held), 1 / SR)
    for note in (220.0, 293.66, 369.99):
        band = (f > note * 0.97) & (f < note * 1.03)
        peak = f[band][np.argmax(spec[band])]
        assert abs(1200 * np.log2(peak / note)) < 10  # within 10 cents
    assert checks.glide_check(held, SR)["pass"]  # a hold never glides


def test_measured_entry_and_end_of_a_take():
    take = np.concatenate([np.zeros((SR, 2)), H.pad(6.0, fade_in=1.0), np.zeros((2 * SR, 2))])
    e = dsp.env_db(take, SR)
    entry = dsp.entry_s(e)
    assert 1.1 <= entry <= 1.5  # a linear 1 s fade-in is within 12 dB of full level a quarter of the way in
    assert 6.5 <= dsp.last_music_s(e) <= 7.15  # the 5-window smoothing reaches a window or two past the end


def test_onset_measures_a_strike_on_its_own():
    hit = H.wood_hit(3)
    y = np.concatenate([np.zeros((1000, 2)), hit])
    assert abs(dsp.onset_index(y) - 1000) <= 30


def test_flux_onsets_find_a_new_chord():
    a, b = H.pad(2.0, notes=(146.83, 220.0)), H.pad(2.0, notes=(196.0, 246.94, 392.0), level_db=-16.0)
    x = np.concatenate([a, b])
    ons = dsp.flux_onsets(x, SR, 1.5, 2.5)
    assert ons and abs(max(ons, key=lambda o: o[1])[0] - 2.0) < 0.03


def test_lowpass_and_highpass_shape_the_spectrum():
    x = H.sine(-6, 1.0, f=100.0) + H.sine(-6, 1.0, f=8000.0)
    lo, hi = dsp.lowpass(x, 1000, SR), dsp.highpass(x, 1000, SR)
    f = np.fft.rfftfreq(SR, 1 / SR)
    spec = lambda s: np.abs(np.fft.rfft(s[:SR, 0]))  # noqa: E731
    assert spec(lo)[100] > 10 * spec(lo)[8000]
    assert spec(hi)[8000] > 10 * spec(hi)[100]
    assert signal is not None and f[100] == 100.0
