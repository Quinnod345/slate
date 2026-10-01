"""The DSP the mix and the score assembler share. Every function here is a port of something the films proved, with
the lesson that shaped it kept next to it.

- Equal-power joins: sine and cosine *amplitude* gains. Two different takes are uncorrelated, so their powers add,
  and sin² + cos² = 1 keeps the level flat through the join. The first score assembly used squared gains (sin², cos²),
  which add to one in amplitude and so dip 3 dB halfway through a crossfade between different takes.
- The voice follower: a 0..1 speech envelope from the dialogue (0 under -40 dBFS RMS, 1 over -28), smoothed at a 1 kHz
  control rate with separate attack and release, which ducks the music a few dB under every word.
- The limiter: a true-peak gain curve computed from the whole signal (4x oversampled peaks, a running minimum over a
  few milliseconds either side, a slow release) and applied to the signal *where it is*. There's no lookahead delay to
  compensate, so the voice can't move. The films' limiter was ffmpeg's alimiter, whose 5 ms lookahead put the voice
  240 samples late until `latency=1` was added; `limit_ffmpeg()` keeps that path (with the flag) for parity runs.
  Applying one curve to several stems (the linked limiter) keeps them summing to the mix.
- Freeze repairs: a phase-vocoder freeze holds a chord at its true pitch (each bin advances at its own measured
  frequency), so a hole between two parts is closed with the music's own material instead of moving parts around,
  which cascaded into the next seam every time it was tried.
- Measured placement: a take's entry, where its music ends, and its onsets (spectral flux), because Eleven Music fades
  every take in and out whatever the plan says.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d

from slate.sound import audio, measure


def db(gain_db: float | np.ndarray) -> float | np.ndarray:
    """dB to a linear amplitude factor."""
    return 10 ** (np.asarray(gain_db) / 20) if isinstance(gain_db, np.ndarray) else 10 ** (gain_db / 20)


def to_db(amplitude: float) -> float:
    return 20 * math.log10(max(float(amplitude), 1e-12))


def fade(n: int, attack_s: float, release_s: float, sr: int) -> np.ndarray:
    """A gain envelope with a sin² attack and cos² release (the films' sampler fades)."""
    e = np.ones(n)
    a, r = min(int(attack_s * sr), n), min(int(release_s * sr), n)
    if a:
        e[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if r:
        e[n - r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return e


# ---------------------------------------------------------------------------------------------------------------
# Joins


def equal_power(n: int, up: bool = True) -> np.ndarray:
    """Amplitude gains for an equal-power crossfade: sin rising (or cos falling). Powers of the pair sum to 1."""
    r = np.sin(np.linspace(0, np.pi / 2, max(int(n), 1)))
    return r if up else r[::-1]


def crossfade(a: np.ndarray, b: np.ndarray, n: int) -> np.ndarray:
    """a then b, overlapping by n samples with equal-power gains (a's last n under b's first n)."""
    n = int(min(n, len(a), len(b)))
    if n <= 0:
        return np.concatenate([a, b])
    shape = (slice(None), None) if a.ndim == 2 else (slice(None),)
    seam = a[-n:] * equal_power(n, False)[shape] + b[:n] * equal_power(n, True)[shape]
    return np.concatenate([a[:-n], seam, b[n:]])


def ramp(n_total: int, t0: float, a: float, b: float, up: bool, sr: int) -> np.ndarray:
    """A gain curve over n_total samples (sample 0 = film time t0): 0 -> 1 (up) or 1 -> 0 over film time a..b, equal
    power; flat 0 before (up) or after (down) the ramp."""
    g = np.ones(n_total)
    ia, ib = int(round((a - t0) * sr)), int(round((b - t0) * sr))
    ia_c, ib_c = max(0, min(n_total, ia)), max(0, min(n_total, ib))
    if ib > ia:
        shape = equal_power(ib - ia, up)
        g[ia_c:ib_c] = shape[ia_c - ia:ib_c - ia]
    if up:
        g[:ia_c] = 0.0
    else:
        g[ib_c:] = 0.0
    return g


# ---------------------------------------------------------------------------------------------------------------
# The voice follower


def follower(key: np.ndarray, sr: int, attack: float = 0.080, release: float = 0.650, lo_db: float = -40.0,
             hi_db: float = -28.0) -> np.ndarray:
    """A 0..1 speech envelope from the dialogue: 0 under lo_db RMS, 1 over hi_db, smoothed with attack/release at a
    1 kHz control rate and interpolated back to the sample rate (final_mix.py's follower, unchanged)."""
    key = np.asarray(key, dtype=np.float64)
    if key.ndim == 1:
        key = key[:, None]
    hop = sr // 1000
    m = len(key) // hop
    if m == 0:
        return np.zeros(len(key))
    p = (key[:m * hop] ** 2).mean(axis=1).reshape(m, hop).mean(axis=1)
    rms_db = 10 * np.log10(np.maximum(np.convolve(p, np.ones(10) / 10, mode="same"), 1e-18))  # 10 ms RMS
    gate = np.clip((rms_db - lo_db) / (hi_db - lo_db), 0, 1)
    ca, cr = math.exp(-1 / (attack * 1000)), math.exp(-1 / (release * 1000))
    env = np.zeros(m)
    e = 0.0
    for i in range(m):
        c = ca if gate[i] > e else cr
        e = c * e + (1 - c) * gate[i]
        env[i] = e
    return np.interp(np.arange(len(key)) / hop, np.arange(m), env)


# ---------------------------------------------------------------------------------------------------------------
# Limiting


def gain_curve(x: np.ndarray, ceiling_db: float, sr: int, look_ms: float = 5.0, release_ms: float = 80.0) -> np.ndarray:
    """A per-sample gain (<= 1) that keeps x's true peak (4x oversampled) at or under ceiling_db.

    The required gain per millisecond, a running minimum over +-look_ms (so it starts coming down before a peak and
    holds just after it), a release no faster than release_ms, interpolated to the sample rate. Because the minimum
    spans a whole millisecond either side, the interpolated curve is never above what any sample in that millisecond
    needs. It only moves where x would go over. Nothing is delayed: the curve is applied to x where it stands.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.ones(0)
    env = measure.peak_envelope(x, sr)
    need = np.minimum(1.0, db(ceiling_db) / np.maximum(env, 1e-12))
    if need.min() >= 1.0:
        return np.ones(n)
    hop = max(1, sr // 1000)
    m = -(-n // hop)
    pad = np.ones(m * hop)
    pad[:n] = need
    frames = pad.reshape(m, hop).min(axis=1)
    look = max(1, int(round(look_ms)))
    g = minimum_filter1d(frames, size=2 * look + 1, mode="nearest")
    rel = 1 - math.exp(-1 / max(release_ms, 1e-3))
    snap = 1 - 1e-5  # within 0.0001 dB of unity the release lets go completely, so untouched audio is bit-exact
    out = g.copy()
    for k in range(1, m):
        v = out[k - 1] + (1 - out[k - 1]) * rel
        if v > snap:
            v = 1.0
        out[k] = g[k] if g[k] < v else v
    return np.minimum(np.interp(np.arange(n) / hop, np.arange(m), out), 1.0)


def limit(x: np.ndarray, ceiling_db: float, sr: int, attack_ms: float = 5.0, release_ms: float = 60.0) -> np.ndarray:
    """Peak-limit a signal to ceiling_db dBTP without moving it (see gain_curve). The dialogue limiter: speech plosives
    are the only thing it touches; everything under the ceiling passes unchanged."""
    x = np.asarray(x, dtype=np.float64)
    g = gain_curve(x, ceiling_db, sr, look_ms=attack_ms, release_ms=release_ms)
    return x * (g[:, None] if x.ndim == 2 else g)


def linked_gain(mix: np.ndarray, ceiling_db: float, sr: int, look_ms: float = 5.0, release_ms: float = 80.0) -> np.ndarray:
    """final_mix.py's linked limiter: one gain curve from the sum, applied to every stem alike (stems still sum)."""
    return gain_curve(mix, ceiling_db, sr, look_ms=look_ms, release_ms=release_ms)


def ffmpeg_limiter_args(ceiling_db: float, sr: int, attack_ms: float = 5.0, release_ms: float = 60.0) -> str:
    """The films' dialogue limiter as an ffmpeg filter: alimiter at 4x the rate, its lookahead compensated.

    `latency=1` is not optional: without it the lookahead delays the output, and the voice lands 240 samples (5 ms at
    48 kHz) late against its cue. tests/sound/test_dsp.py holds that regression.
    """
    lin = 10 ** (ceiling_db / 20)
    return (f"aresample={sr * 4},alimiter=limit={lin:.6f}:attack={attack_ms:g}:release={release_ms:g}"
            f":level=disabled:asc=1:latency=1,aresample={sr}")


def limit_ffmpeg(x: np.ndarray, ceiling_db: float, sr: int, attack_ms: float = 5.0, release_ms: float = 60.0) -> np.ndarray:
    """The films' limiter, through ffmpeg (for runs that must match a delivered film exactly)."""
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from slate import procs  # noqa: PLC0415
    from slate.errors import MissingPrerequisite  # noqa: PLC0415

    if not procs.which("ffmpeg"):
        raise MissingPrerequisite("The ffmpeg limiter was asked for, but ffmpeg isn't installed.",
                                  hint="Use Slate's own limiter (the default), or run `slate doctor --install`.")
    x = audio.to_stereo(x)
    with tempfile.TemporaryDirectory(prefix="slate-limit-") as tmp:
        src = Path(tmp) / "in.wav"
        audio.write_wav(src, x, sr, bits=32)
        proc = procs.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-af",
                          ffmpeg_limiter_args(ceiling_db, sr, attack_ms, release_ms),
                          "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"], text=False, timeout=1800, check=True)
    y = np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2).astype(np.float64)
    return audio.fit(y, len(x))


# ---------------------------------------------------------------------------------------------------------------
# Measuring a take (placement by measurement)


def env_db(y: np.ndarray, sr: int, win_s: float = 0.05, smooth: int = 5) -> np.ndarray:
    """Level in dB per 50 ms window, lightly smoothed (the score assemblers' envelope)."""
    m = audio.mono(np.asarray(y, dtype=np.float64))
    w = max(1, int(round(win_s * sr)))
    k = len(m) // w
    if k == 0:
        return np.full(1, -120.0)
    p = (m[:k * w] ** 2).reshape(k, w).mean(axis=1)
    if smooth > 1:
        p = np.convolve(p, np.ones(smooth) / smooth, mode="same")
    return 10 * np.log10(np.maximum(p, 1e-12))


def entry_s(e: np.ndarray, win_s: float = 0.05, after: float = 0.0, below_db: float = 12.0) -> float:
    """Where a take's music really starts: the first window within below_db of its median level."""
    med = np.median(e)
    i0 = int(after / win_s)
    hits = np.where(e[i0:] >= med - below_db)[0]
    return (i0 + hits[0]) * win_s if len(hits) else after


def last_music_s(e: np.ndarray, win_s: float = 0.05, below_db: float = 20.0) -> float:
    """Where a take's music ends: the last window within below_db of its median level."""
    med = np.median(e)
    hits = np.where(e >= med - below_db)[0]
    return hits[-1] * win_s if len(hits) else 0.0


def flux_onsets(y: np.ndarray, sr: int, a: float, b: float, hop_s: float = 0.01, min_z: float = 8.0) -> list[tuple[float, float]]:
    """Onsets in y's time a..b: peaks of positive spectral flux (log magnitude, 150 Hz-6 kHz) with their strength in
    units of the take's own median spread. Returns [(time s, strength)]."""
    m = audio.mono(np.asarray(y, dtype=np.float64))
    n_fft, h = 2048, max(1, int(hop_s * sr))
    if len(m) < n_fft + 2 * h:
        return []
    win = np.hanning(n_fft)
    f = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (f > 150) & (f < 6000)
    starts = np.arange(0, len(m) - n_fft, h)
    frames = np.lib.stride_tricks.sliding_window_view(m, n_fft)[starts] * win
    mags = np.log(np.abs(np.fft.rfft(frames, axis=1))[:, band] + 1e-6)
    flux = np.concatenate([[0.0], np.maximum(np.diff(mags, axis=0), 0).mean(axis=1)])
    med = np.median(flux)
    mad = np.median(np.abs(flux - med)) + 1e-9
    z = (flux - med) / mad
    times = (starts + n_fft / 2) / sr
    out: list[tuple[float, float]] = []
    for k in range(1, len(z) - 1):
        if a <= times[k] <= b and z[k] > min_z and z[k] >= z[k - 1] and z[k] >= z[k + 1]:
            if not out or times[k] - out[-1][0] > 0.08:
                out.append((float(times[k]), float(z[k])))
    return out


def onset_index(y: np.ndarray, below_db: float = 12.0) -> int:
    """The first sample within below_db of the sound's own peak: where the ear hears a strike start."""
    m = np.abs(audio.mono(np.asarray(y, dtype=np.float64)))
    if not len(m):
        return 0
    pk = m.max() + 1e-12
    return int(np.argmax(m >= pk * 10 ** (-below_db / 20)))


def loudest_window_index(y: np.ndarray, sr: int, win_s: float) -> int:
    """The centre of the loudest win_s window (how air and swells are anchored: the ear follows their crest)."""
    m = audio.mono(np.asarray(y, dtype=np.float64)) ** 2
    w = max(1, int(win_s * sr))
    e = np.convolve(m, np.ones(w) / w, mode="same")
    return int(np.argmax(e))


def last_above_index(y: np.ndarray, below_db: float = 40.0, win_s: float = 0.002, sr: int = audio.DEFAULT_SR) -> int:
    """One past the last sample within below_db of the peak envelope (a lift's or a line's end)."""
    m = audio.mono(np.asarray(y, dtype=np.float64)) ** 2
    w = max(1, int(win_s * sr))
    e = np.sqrt(np.convolve(m, np.ones(w) / w, mode="same"))
    idx = np.flatnonzero(e >= e.max() * 10 ** (-below_db / 20))
    return int(idx[-1] + 1) if len(idx) else 0


# ---------------------------------------------------------------------------------------------------------------
# Holding a chord


def freeze(seg: np.ndarray, dur_s: float, sr: int, seed: int, n_fft: int = 8192, hop: int = 2048,
           mask: np.ndarray | None = None) -> np.ndarray:
    """Hold a sound: seg's average magnitude spectrum, resynthesised with each bin advancing at its own measured
    frequency (a phase-vocoder freeze), so its partials sustain at their true pitch for dur_s seconds. Stereo in,
    stereo out, at seg's RMS. An optional spectral mask (one value per bin) keeps only part of it (one note of a chord).
    """
    seg = audio.to_stereo(seg)
    rng = np.random.default_rng(seed)
    win = np.hanning(n_fft)
    n_out = int(dur_s * sr)
    if n_out <= 0:
        return np.zeros((0, 2))
    if len(seg) < n_fft + hop:
        seg = np.concatenate([seg] * int(math.ceil((n_fft + hop + 1) / max(len(seg), 1))))
    k = np.arange(n_fft // 2 + 1)
    start_phase = rng.uniform(0, 2 * np.pi, len(k))
    full = np.zeros((n_out + n_fft, 2))
    masked = np.zeros((n_out + n_fft, 2)) if mask is not None else None
    for ch in range(2):
        x = seg[:, ch]
        frames = [np.fft.rfft(x[i:i + n_fft] * win) for i in range(0, len(x) - n_fft - hop + 1, hop)]
        if len(frames) < 2:
            frames = [np.fft.rfft(x[:n_fft] * win), np.fft.rfft(x[hop:hop + n_fft] * win)]
        mag = np.mean([np.abs(fr) for fr in frames], axis=0)
        dphi = np.angle(frames[1]) - np.angle(frames[0]) - 2 * np.pi * k * hop / n_fft
        dphi = (dphi + np.pi) % (2 * np.pi) - np.pi
        omega = 2 * np.pi * k / n_fft + dphi / hop
        phase = start_phase.copy()
        for pos in range(0, n_out, hop):
            spec = mag * np.exp(1j * phase)
            full[pos:pos + n_fft, ch] += np.fft.irfft(spec, n_fft) * win
            if masked is not None:
                masked[pos:pos + n_fft, ch] += np.fft.irfft(spec * mask, n_fft) * win
            phase += omega * hop
    full = full[:n_out]
    ref = math.sqrt(float(np.mean(seg ** 2))) / (math.sqrt(float(np.mean(full ** 2))) + 1e-12)
    return (masked[:n_out] if masked is not None else full) * ref


def lowpass(x: np.ndarray, hz: float, sr: int, order: int = 2) -> np.ndarray:
    sos = signal.butter(order, hz, "lowpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x, axis=0)


def highpass(x: np.ndarray, hz: float, sr: int, order: int = 2, *, zero_phase: bool = False) -> np.ndarray:
    """A Butterworth high-pass. zero_phase runs it forwards and back (sosfiltfilt), so nothing in the signal moves in
    time: what the mix's 25 Hz cut needs, since every hit keeps its anchor."""
    sos = signal.butter(order, hz, "highpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x, axis=0) if zero_phase else signal.sosfilt(sos, x, axis=0)


def keep_silence(y: np.ndarray, x: np.ndarray, sr: int, *, min_s: float = 0.01) -> np.ndarray:
    """y (a filtered x) with x's digital silences put back: a zero-phase filter rings a little either side of a sound,
    and silence has to stay silence (a film's silent last second, the gaps between takes). Runs of exact zeros in x of
    at least min_s are zeroed in y; everything else is y as filtered. In place, and returned."""
    x2 = x if x.ndim == 2 else x[:, None]
    zero = ~np.any(x2 != 0.0, axis=1)
    if not zero.any():
        return y
    edges = np.diff(np.concatenate([[0], zero.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    k = max(1, int(round(min_s * sr)))
    for a, b in zip(starts, ends):
        if b - a >= k:
            y[a:b] = 0.0
    return y


def limiter_touch(voice: np.ndarray, gain: np.ndarray, sr: int, *, within_db: float = 30.0,
                  touch_db: float = 0.01) -> dict:
    """How much of the speech a voice-only limiter touched: the share of the voice's speaking milliseconds (its 10 ms
    level within within_db of its loud level, the 95th percentile) where the limiter's gain sat under unity by more
    than touch_db. The films' rule: a voice limiter that touches 5% of the speech or more is shaping the read, not
    catching its plosives."""
    voice = np.asarray(voice, dtype=np.float64)
    if voice.ndim == 1:
        voice = voice[:, None]
    hop = max(1, sr // 1000)
    m = min(len(voice), len(gain)) // hop
    if m == 0:
        return {"speech_ms": 0, "touched_ms": 0, "share": 0.0, "max_reduction_db": 0.0}
    p = (voice[:m * hop] ** 2).mean(axis=1).reshape(m, hop).mean(axis=1)
    lv = 10 * np.log10(np.maximum(np.convolve(p, np.ones(10) / 10, mode="same"), 1e-20))
    live = lv > -70
    if not live.any():
        return {"speech_ms": 0, "touched_ms": 0, "share": 0.0, "max_reduction_db": 0.0}
    speech = lv >= float(np.percentile(lv[live], 95)) - within_db
    g = np.asarray(gain, dtype=np.float64)[:m * hop].reshape(m, hop).min(axis=1)
    touched = speech & (g < 10 ** (-touch_db / 20))
    worst = min(1.0, float(np.min(gain))) if len(gain) else 1.0
    return {"speech_ms": int(speech.sum()), "touched_ms": int(touched.sum()),
            "share": round(float(touched.sum()) / max(1, int(speech.sum())), 4),
            "max_reduction_db": round(abs(20 * math.log10(max(worst, 1e-12))), 2)}
