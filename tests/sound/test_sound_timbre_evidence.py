import numpy as np

from slate import jsonio
from slate.sound import audio, mix, verify


def test_metallic_failure_keeps_its_high_partial_witness(sound_film):
    """Quiet high partial. Keep rejection witness behind six louder low partials."""
    sr = 48000
    t = np.arange(2 * sr) / sr
    partials = ((200, 1), (250, .9), (320, .8), (410, .7), (530, .6), (680, .5),
                (1100, .15), (1500, .12))
    hit = sum(amplitude * np.sin(2 * np.pi * hz * t) for hz, amplitude in partials) * .05
    hit *= np.minimum(t / .01, 1) * np.minimum((2 - t) / .4, 1)
    stem = np.zeros((14 * sr, 2))
    stem[4 * sr:6 * sr] = hit[:, None]
    audio.write_wav(sound_film / 'sound/stems/tonal.wav', stem, sr)
    mix.run(sound_film)
    result, = verify.run(sound_film, ['sound.timbre'])
    assert result.status == 'FAIL'
    failures = result.details['stems']['tonal']['metallic']
    assert failures
    for failure in failures:
        assert any(hz >= 1000 for hz in failure['metallic']['partials_hz'])
    assert '1100' in ' '.join(result.evidence)
    path = sound_film / 'film.json'
    film = jsonio.read(path)
    film.setdefault('sound', {}).setdefault('palette', {})['timbre'] = {
        'centroid_max_hz': None, 'no_chimes': False, 'no_metallic': False}
    jsonio.write_atomic(path, film)
    allowed, = verify.run(sound_film, ['sound.timbre'])
    assert allowed.status == 'PASS'
    assert allowed.details['stems']['tonal']['metallic'] == failures
    assert allowed.details['policy']['no_metallic'] is False


def test_band_limited_noise_is_not_a_chime_but_a_chime_still_is():
    """The air on a draw (noise band-passed and still sounding 300 ms on) stands far over a wide band's median, most of
    which is stopband, but never over the noise beside it: not a chime's tail. A struck 2.6 kHz partial still is."""
    import numpy as np
    from scipy import signal as sg

    from slate.sound import checks

    sr = 48000
    rng = np.random.default_rng(3)
    n = int(1.2 * sr)
    t = np.arange(n) / sr
    noise = sg.sosfilt(sg.butter(2, [1200, 4500], "bandpass", fs=sr, output="sos"), rng.standard_normal(n))
    noise *= np.minimum(1, t / 0.012) * np.exp(-np.maximum(0, t - 0.8) / 0.05)
    lead = np.zeros(int(0.5 * sr))
    air = np.concatenate([lead, noise, lead])
    r = checks.timbre_check(np.stack([air, air], 1), sr)
    assert r["chime_tails"] == [] and r["metallic"] == []
    chime = np.sin(2 * np.pi * 2637 * t) * np.exp(-t / 0.9) * np.minimum(1, t / 0.002)
    struck = np.concatenate([lead, chime, lead])
    r = checks.timbre_check(np.stack([struck, struck], 1), sr)
    assert r["chime_tails"], r


def test_a_swelling_pad_chord_is_not_a_ringing_strike():
    """A pad's slow attack is louder 300 ms on than in its first 60 ms: it isn't a strike ringing out, so its chord
    partials aren't read as metal or a chime's tail. A struck chime still is (test above)."""
    import numpy as np

    from slate.sound import checks

    sr = 48000
    n = int(2.0 * sr)
    t = np.arange(n) / sr
    # a low-passed string pad: the chord's harmonics falling off fast, swelling in on a smooth 0.65 s attack
    chord = sum(np.sin(2 * np.pi * f0 * k * t) / k ** 2 for f0 in (146.8, 220.0, 329.6, 370.0) for k in range(1, 8))
    attack = 0.5 - 0.5 * np.cos(np.pi * np.minimum(1, t / 0.65))
    pad = chord * attack * np.exp(-np.maximum(0, t - 1.4) / 0.2)
    lead = np.zeros(int(0.5 * sr))
    y = np.concatenate([lead, pad, lead])
    r = checks.timbre_check(np.stack([y, y], 1), sr)
    assert r["chime_tails"] == [] and r["metallic"] == []
