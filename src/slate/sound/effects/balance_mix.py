"""Approved effect-family processing and adaptive mix guards.

The renderer owns voices, placement, sends and planned silences. This module owns
per-hit treatments, return ducking and guard rides. It never marks a gate passed:
its reports include caps and remaining short words for the independent checker.
"""
from __future__ import annotations
import copy
import math
from fractions import Fraction
import numpy as np
from scipy import signal
from slate import jsonio
from slate.errors import Refused, UsageError
from slate.sound import measure as meter

HOP = 0.01
SYNC_TOL_MS = 10.0
NOSYNC_ANCHORS = (None, "bed", "vo", "plate", "echo", "clicks")
SPOKEN_SYMBOLS = set("%&$€£+@#")
FAMILIES = ("voice", "accents", "textures", "swells", "impacts", "score", "plate")
EFFECT_FAMILIES = ("accents", "textures", "swells", "impacts")
RETURNS = ("verb", "tverb")
DUCK_P3 = {"tonal": -6.0, "amb": -6.0, "fx": -3.0, "verb": -4.0, "tverb": -4.0}

def db(value):
    return 10 ** (np.asarray(value) / 20)

DEFAULT = {'families': {'accents': {'mix': 'proof2', 'gain_db': 0.0},
              'textures': {'mix': 'keyed',
                           'key': 'texture',
                           'gain_db': -9.0,
                           'duck_db': -5.0,
                           'speech_hf': {'split_hz': 1000, 'extra_db': -12.0}},
              'swells': {'mix': 'keyed',
                         'key': 'swell',
                         'gain_db': -5.0,
                         'duck_db': -8.0,
                         'continuous_over_s': 4.0,
                         'speech_hf': {'split_hz': 1000, 'extra_db': -6.0, 'types': ['swell']}},
              'impacts': {'mix': 'proof2',
                          'gain_db': 0.0,
                          'under_speech': {'key': 'texture',
                                           'duck_db': -3.0,
                                           'speech_hf': {'split_hz': 500,
                                                         'extra_db': -12.0,
                                                         'keep_attack_ms': 80}}},
              'score': {'mix': 'proof2', 'gain_db': 0.0},
              'plate': {'mix': 'keyed',
                        'key': 'texture',
                        'gain_db': 0.0,
                        'duck_db': -5.0,
                        'speech_hf': {'split_hz': 1000, 'extra_db': -12.0},
                        'continuous_over_s': 0.01}},
 'keys': {'texture': {'lookahead_ms': 40,
                      'attack_ms': 8,
                      'hold_ms': 150,
                      'release_ms': 250,
                      'off_db': 40,
                      'on_db': 25},
          'swell': {'lookahead_ms': 80,
                    'attack_ms': 40,
                    'hold_ms': 300,
                    'release_ms': 600,
                    'off_db': 40,
                    'on_db': 25}},
 'checks': {'clarity': {'band_hz': [1000, 5000], 'min_db': 12.0, 'trade_flag_db': 8.0, 'trade_floor_db': 6.0},
            'never_over': {'min_margin_db': 0.0},
            'texture_air': {'band_hz': [2000, 6000], 'min_db_under_voice': 20.0},
            'accents_over_score': {'min_db': 3.0},
            'levels': {'accents': {'hit_median_lu': [-24, -17], 'hit_max_lu': -8},
                       'textures': {'hit_max_lu': -18, 'inside_words_max_lu': -30},
                       'swells': {'hit_max_lu': -22, 'inside_words_max_lu': -28},
                       'impacts': {'hit_max_lu': -2},
                       'score': {'integrated_lu': [-28, -17], 'inside_words_max_lu': -19}}},
 'clarity_guard': {'families': ['accents'],
                   'band_hz': [1000, 5000],
                   'target_db': 13.0,
                   'split_hz': 600,
                   'max_ride_db': -15.0,
                   'depth_fraction': 0.5},
 'air_guard': {'families': ['textures'],
               'band_hz': [2000, 6000],
               'compare': 'own',
               'target_db': 20.3,
               'split_hz': None,
               'max_ride_db': -12.0},
 'score_bus': {'under_voice_lu': 18.5, 'duck_db': -6.0, 'opens_after_s': 0.5},
 'score_guard': {'band_hz': [1000, 5000],
                 'target_db': 19.0,
                 'split_hz': 600,
                 'max_dip_db': -20.0,
                 'hold_s': 0.15,
                 'ramp_s': 0.3,
                 'accent_margin_db': 0.5},
 'never_over_guard': {'margin_db': 0.5, 'rounds': 3, 'cap_db': -12.0}}

class FamilyMixer:
    """One render context; rates, keys and callbacks never live in module globals."""

    def proof2_env(self, vo):
        """The approved ducking envelope (p3engine.render): the voice's power smoothed at 6 Hz, over its 60th percentile."""
        env = np.sqrt(signal.sosfilt(signal.butter(1, 6, 'lowpass', fs=self.sr, output='sos'), (vo ** 2).mean(axis=1)))
        return np.clip(env / (np.percentile(env[env > 0.0001], 60) if (env > 0.0001).any() else 1.0), 0, 1)

    def _window_max(self, x, back, ahead):
        """max(x[i - back .. i + ahead]) for every i (edges padded with the edge value)."""
        p = np.pad(x, (back, ahead), mode='edge')
        return np.lib.stride_tricks.sliding_window_view(p, back + ahead + 1).max(axis=1)

    def speech_key(self, vo, k):
        """Build the approved 1 kHz speech key with lookahead, hold, attack and release."""
        x = vo.mean(axis=1) if vo.ndim == 2 else vo
        hop = self.sr / 1000
        m = int(len(x) // hop)
        if m == 0:
            return np.zeros(len(x))
        if self.sr % 1000 == 0:
            size = int(hop)
            p = (x[:m * size] ** 2).reshape(m, size).mean(axis=1)
        else:
            edges = np.floor(np.arange(m+1)*hop).astype(int)
            sums = np.concatenate(([0.0], np.cumsum(x*x)))
            p = np.diff(sums[edges])/np.diff(edges)
        rms_db = 10 * np.log10(np.maximum(signal.convolve(p, np.ones(20) / 20, mode='same'), 1e-20))
        loud = np.percentile(rms_db[rms_db > -100], 99) if (rms_db > -100).any() else 0.0
        off, on = (loud - k.get('off_db', 40.0), loud - k.get('on_db', 25.0))
        gate = np.clip((rms_db - off) / (on - off), 0, 1)
        held = self._window_max(gate, int(k.get('hold_ms', 150)), int(k.get('lookahead_ms', 40)))
        ca, cr = (np.exp(-1 / max(k.get('attack_ms', 8.0), 0.001)), np.exp(-1 / max(k.get('release_ms', 250.0), 0.001)))
        env = np.zeros(m)
        e = 0.0
        for i in range(m):
            c = ca if held[i] > e else cr
            e = c * e + (1 - c) * held[i]
            env[i] = e
        out = np.interp(np.arange(len(x)) / hop, np.arange(m), env)
        return np.clip(out, 0.0, 1.0)

    def keys_needed(self, cfg, mode):
        """The speech keys the families use (by key name)."""
        if mode != 'balanced':
            return set()
        out = set()
        for fc in cfg['families'].values():
            if fc.get('mix') == 'keyed':
                out.add(fc['key'])
            if fc.get('under_speech'):
                out.add(fc['under_speech']['key'])
        return out

    def power_curve(self, x, win_s, hop_s=HOP, weight=True):
        """Mean power (summed over channels) in windows of win_s centred every hop_s, K-weighted by default."""
        z = signal.sosfilt(meter.k_weighting_sos(self.sr), x, axis=0) if weight else x
        p = (z ** 2).sum(axis=1)
        h = int(hop_s * self.sr)
        m = len(p) // h
        if m == 0:
            return np.zeros(0)
        ph = p[:m * h].reshape(m, h).mean(axis=1)
        w = max(1, int(round(win_s / hop_s)))
        return signal.convolve(ph, np.ones(w) / w, mode='same')

    def lufs_of_power(self, pw):
        return -0.691 + 10 * np.log10(np.maximum(pw, 1e-20))

    def band_power(self, x, lo, hi):
        """Per-sample power (summed over channels) in a band: 4th-order Butterworth band-pass, zero-phase."""
        sos = signal.butter(4, [lo, min(hi, self.sr * .49)], 'bandpass', fs=self.sr, output='sos')
        return (signal.sosfiltfilt(sos, x, axis=0, padlen=min(15, len(x)-1)) ** 2).sum(axis=1)

    def frames_of(self, pw, m):
        """Per-sample power -> mean per 10 ms frame (m frames)."""
        h = int(HOP * self.sr)
        pw = np.pad(pw, (0, max(0, m * h - len(pw))))[:m * h]
        return pw.reshape(m, h).mean(axis=1)

    def is_spoken(self, w):
        """Distinguish spoken words and symbols from pauses and punctuation."""
        return any((ch.isalnum() for ch in w)) or any((ch in SPOKEN_SYMBOLS for ch in w))

    def word_frames(self, voice, words, t0, within_db=30.0, min_word_s=0.12):
        """Measure only active voice frames inside each word, with the approved short-word search."""
        n = len(voice)
        hop = int(HOP * self.sr)
        L50 = self.lufs_of_power(self.power_curve(voice, 0.05))
        loud = float(np.percentile(L50[L50 > -70], 95)) if (L50 > -70).any() else 0.0
        spk = L50 >= loud - within_db
        ws, wf = ([], [])
        for w, s, e in words:
            if not self.is_spoken(w):
                continue
            a, b = (int(round((s - t0) * self.sr)), int(round((e - t0) * self.sr)))
            if b - a < int(min_word_s * self.sr):
                c = (a + b) // 2
                a, b = (c - int(min_word_s / 2 * self.sr), c + int(min_word_s / 2 * self.sr))
            if a < 0 or b > n:
                continue
            fr = np.arange(a // hop, min(b // hop, len(spk)))
            fr = fr[spk[fr]]
            if len(fr) < 3:
                fr = np.arange(max(0, a // hop - 15), min(b // hop + 15, len(spk)))
                fr = fr[spk[fr]]
            ws.append((w, round(s, 3), round(e, 3), a, b))
            wf.append(fr)
        return (ws, wf, spk)

    def duck_gain(self, key, depth_db):
        return 1 - key * (1 - db(depth_db))

    def depth_for(self, d, stem):
        """A duck depth: one number for every stem, or {stem: dB} (a stem not named is not ducked)."""
        return d.get(stem) if isinstance(d, dict) else d

    def split_band(self, x, hz, pad=0):
        """Split at a zero-phase low-pass; low plus high equals the original samples exactly."""
        sos = signal.butter(2, min(hz, self.sr*.49), 'lowpass', fs=self.sr, output='sos')
        if pad:
            lo = signal.sosfiltfilt(sos, np.pad(x, ((pad, pad), (0, 0))), axis=0)[pad:-pad]
        else:
            lo = signal.sosfiltfilt(sos, x, axis=0, padlen=min(15, len(x)-1))
        return (lo, x - lo)

    def audible_key(self, seg, key_seg, within_db=10.0):
        """Take the maximum speech key over the audible part of a hit."""
        m = (seg ** 2).sum(axis=1)
        e = signal.convolve(m, np.ones(max(1, round(.01 * self.sr))) / max(1, round(.01 * self.sr)), mode='same')
        live = e >= e.max() * 10 ** (-within_db / 10)
        return float(key_seg[live].max()) if live.any() else 0.0

    def sync_tol_ms(self, c, seg, seg_start_t):
        """Limit spectral anchor movement to ten milliseconds and the available frame tolerance."""
        i0 = self.anchor_index(c.get('anchor'), seg)
        if i0 is None or 'beat' not in c:
            return None
        off_ms = abs(seg_start_t + i0 / self.sr - c['beat']) * 1000
        return max(0.0, min(SYNC_TOL_MS, self.frame_ms - 1.0 - off_ms))

    def movavg(self, x, w):
        """signal.convolve(x, ones(w)/w, mode='same'), in O(n) (a running sum)."""
        n = len(x)
        c = np.concatenate([[0.0], np.cumsum(x)])
        i = np.arange(n)
        lo = np.clip(i - w // 2, 0, n)
        hi = np.clip(i + (w - 1) // 2 + 1, 0, n)
        return (c[hi] - c[lo]) / w

    def anchor_index(self, kind, y):
        """verify.anchor_times()'s rules on a rendered hit y (from its first sample): the anchor's sample index, or None."""
        if kind in NOSYNC_ANCHORS:
            return None
        m = np.abs(y.mean(axis=1) if y.ndim == 2 else y)
        if not len(m) or m.max() <= 0:
            return None
        if kind == 'onset':
            return int(np.argmax(m >= m.max() * 10 ** (-12 / 20)))
        e2 = np.sqrt(np.maximum(self.movavg(m ** 2, max(1, round(.002 * self.sr))), 0))
        if kind in ('onset24', 'start'):
            return int(np.argmax(e2 >= e2.max() * 10 ** (-40 / 20)))
        if kind in ('peak20', 'peak50'):
            return int(np.argmax(self.movavg(m ** 2, max(1, round(.05 * self.sr)))))
        if kind in ('end', 'end30', 'end20'):
            thr = e2.max() * 10 ** ({'end': -40, 'end30': -30, 'end20': -20}[kind] / 20)
            return int(np.flatnonzero(e2 >= thr)[-1] + 1)
        return None

    def _treat(self, seg, k, stem, t, pad, ctype=None, anchor=None, tol_ms=None):
        """One per-hit treatment t ({duck_db, speech_hf: {split_hz, extra_db[, keep_attack_ms][, types]}}) at key value k."""
        info = {}
        hf = t.get('speech_hf')
        if hf and hf.get('types') and (ctype not in hf['types']):
            hf = None
        if hf and k > 0:
            lo, hi = self.split_band(seg, hf['split_hz'], pad=pad)

            def shaped(e):
                g = self.duck_gain(k, e)
                if hf.get('keep_attack_ms'):
                    a0, r0 = (int(hf['keep_attack_ms'] / 1000 * self.sr), int(0.03 * self.sr))
                    ramp = np.ones(len(seg))
                    ramp[a0:a0 + r0] = np.linspace(1, g, len(ramp[a0:a0 + r0]))
                    ramp[a0 + r0:] = g
                    return lo + hi * ramp[:, None]
                return lo + hi * g
            e_req = hf['extra_db']
            out = shaped(e_req)
            i0 = self.anchor_index(anchor, seg)
            if i0 is not None:
                tol = int((SYNC_TOL_MS if tol_ms is None else tol_ms) / 1000 * self.sr)
                i1 = self.anchor_index(anchor, out)
                if i1 is None or abs(i1 - i0) > tol:
                    safe, unsafe = (0.0, e_req)
                    for _ in range(12):
                        mid = (safe + unsafe) / 2
                        im = self.anchor_index(anchor, shaped(mid))
                        safe, unsafe = (mid, unsafe) if im is not None and abs(im - i0) <= tol else (safe, mid)
                    comp = self.duck_gain(k, e_req) / self.duck_gain(k, safe)
                    out = shaped(safe) * comp
                    info['sync_safe'] = {'top_split_db': round(20 * np.log10(self.duck_gain(k, safe)), 2), 'whole_hit_db': round(20 * np.log10(comp), 2)}
            seg = out
            info['top_db'] = round(20 * np.log10(self.duck_gain(k, e_req)), 2)
        d = self.depth_for(t.get('duck_db'), stem)
        if d is not None:
            seg = seg * self.duck_gain(k, d)
            info['duck_db'] = round(20 * np.log10(self.duck_gain(k, d)), 2)
        return (seg, info)

    def cue_mix(self, fam, stem, seg, a, spec, keys, ctype=None, anchor=None, tol_ms=None):
        """Apply family gains once per hit, except deliberately continuous pads and plates."""
        applied = {}
        pad = int(0.05 * self.sr)
        span = spec.get('audible_within_db', 10.0)
        if spec.get('mix') == 'keyed':
            if spec.get('continuous_over_s') and len(seg) > spec['continuous_over_s'] * self.sr:
                kk = keys[spec['key']][a:a + len(seg)]
                seg = seg * db(spec.get('gain_db', 0.0))
                hf = spec.get('speech_hf')
                if hf and (not (hf.get('types') and ctype not in hf['types'])):
                    lo, hi = self.split_band(seg, hf['split_hz'], pad=pad)
                    seg = lo + hi * self.duck_gain(kk, hf['extra_db'])[:, None]
                d = self.depth_for(spec['duck_db'], stem)
                if d is not None:
                    seg = seg * self.duck_gain(kk, d)[:, None]
                applied = {'key': 'continuous', 'gain_db': spec.get('gain_db', 0.0), 'duck_db': d, 'mean_key': round(float(kk.mean()), 3)}
            else:
                k = self.audible_key(seg, keys[spec['key']][a:a + len(seg)], span)
                seg = seg * db(spec.get('gain_db', 0.0))
                seg, info = self._treat(seg, k, stem, spec, pad, ctype, anchor, tol_ms)
                applied = {'key': round(k, 3), 'gain_db': spec.get('gain_db', 0.0), **info}
        us = spec.get('under_speech')
        if us:
            k = self.audible_key(seg, keys[us['key']][a:a + len(seg)], span)
            seg, info = self._treat(seg, k, stem, us, pad, ctype, anchor, tol_ms)
            applied.update({'speech_key': round(k, 3), **{'under_speech_' + kk: v for kk, v in info.items()}})
        return (seg, applied)

    def bus_mix(self, fam, stem, x, spec, env_p2, keys):
        """The continuous part of a family's mix, on a whole bus (or a slice of one):"""
        if spec.get('mix', 'proof2') == 'proof2':
            y = x * db(spec.get('gain_db', 0.0))
            d = DUCK_P3.get(stem)
            return y * self.duck_gain(env_p2, d)[:, None] if d is not None else y
        if stem not in RETURNS:
            return x
        key = keys[spec['key']]
        y = x
        hf = spec.get('speech_hf')
        if hf and (not hf.get('types')):
            lo, hi = self.split_band(y, hf['split_hz'])
            y = lo + hi * self.duck_gain(key, hf['extra_db'])[:, None]
        d = self.depth_for(spec['duck_db'], stem)
        return y * self.duck_gain(key, d)[:, None] if d is not None else y

    def ride(self, seg, split_hz, ride_db):
        """A clarity ride: the hit's top (above split_hz) down ride_db, its body left alone; one gain for the whole hit."""
        lo, hi = self.split_band(seg, split_hz, pad=int(0.05 * self.sr))
        return lo + hi * db(ride_db)

    def apply_ride(self, seg, r, default_split=1000.0):
        """Apply a top, whole-hit or body ride; a body ride retains the initial impact attack."""
        if isinstance(r, (int, float)):
            return self.ride(seg, default_split, float(r))
        out = seg
        if r.get('top_db'):
            out = self.ride(seg, r['split'], r['top_db']) if r.get('split') else seg * db(r['top_db'])
        if r.get('whole_db'):
            out = out * db(r['whole_db'])
        if r.get('body_db'):
            k = min(len(out), int(float(r.get('keep_attack_ms', 80.0)) / 1000 * self.sr))
            k2 = min(len(out), k + int(0.02 * self.sr))
            env = np.full(len(out), db(r['body_db']))
            env[:k] = 1.0
            env[k:k2] = np.linspace(1.0, db(r['body_db']), k2 - k)
            out = out * env[:, None]
        return out

    def scale_ride(self, r, frac):
        """Scale top, whole, and body ride depths in dB."""
        if isinstance(r, (int, float)):
            return round(float(r) * frac, 2)
        out = dict(r)
        for k in ('top_db', 'whole_db', 'body_db'):
            if out.get(k):
                out[k] = round(float(out[k]) * frac, 2)
        return out

    def ride_db(self, r):
        """A ride's total reduction in dB (for sorting and reports): a number, or a dict's top plus whole plus body."""
        return float(r) if isinstance(r, (int, float)) else float(r.get('top_db', 0.0)) + float(r.get('whole_db', 0.0)) + float(r.get('body_db', 0.0))

    def combine_rides(self, a, b, clarity_split=1000.0):
        """Two rounds of rides on the same hits, as one (dB add; a number is a top ride at the clarity split)."""
        out = {k: dict(v) if isinstance(v, dict) else v for k, v in a.items()}
        for k, v in b.items():
            if k not in out:
                out[k] = v
                continue
            x = out[k] if isinstance(out[k], dict) else {'top_db': out[k], 'split': clarity_split, 'whole_db': 0.0}
            y = v if isinstance(v, dict) else {'top_db': v, 'split': clarity_split, 'whole_db': 0.0}
            if y.get('split') == x.get('split') or not y.get('top_db'):
                x = dict(x, top_db=round(x.get('top_db', 0.0) + y.get('top_db', 0.0), 2), whole_db=round(x.get('whole_db', 0.0) + y.get('whole_db', 0.0), 2))
            else:
                x = dict(x, whole_db=round(x.get('whole_db', 0.0) + y.get('top_db', 0.0) + y.get('whole_db', 0.0), 2))
            if y.get('body_db') or x.get('body_db'):
                x = dict(x, body_db=round(x.get('body_db', 0.0) + y.get('body_db', 0.0), 2), keep_attack_ms=y.get('keep_attack_ms', x.get('keep_attack_ms', 80.0)))
            out[k] = x
        return out

    def silence_mask(self, silences, stem, t0, n):
        """Use the renderer's exact clipped mask, including incoming ramps and overlapping spans."""
        if not any(stem in sil.get('stems', ('ui','fx','tonal','verb','tverb','amb')) for sil in silences):
            return None
        from slate.sound.effects.rendering import silence_mask
        return silence_mask(silences,stem,n,self.sr,t0)

    def span_dips(self, spans, n, hold_s, ramp_s):
        """Build a held, smoothly ramped dip curve; the deepest overlapping dip wins."""
        h = int(HOP * self.sr)
        m = n // h + 1
        c = np.zeros(m)
        k = int(round(hold_s / HOP))
        for a, b, d in spans:
            i0, i1 = (max(a // h - k, 0), min(b // h + 1 + k, m))
            c[i0:i1] = np.minimum(c[i0:i1], d)
        w = max(3, int(round(ramp_s / HOP)) | 1)
        win = np.hanning(w + 2)[1:-1]
        c = signal.convolve(np.pad(c, (w // 2, w // 2), mode='edge'), win / win.sum(), mode='valid')[:m]
        return (10 ** (np.interp(np.arange(n) / h, np.arange(m), c) / 20), c)

    def guard_rides(self, result, words, cfg, gname, extra=(), boost=None, whole=False, voice=None, frozen=()):
        """Jointly minimize per-hit rides while counting every hit and its own return in each word."""
        g = cfg.get(gname)
        if not g or not words:
            return ({}, {'rides': 0})
        boost = boost or {}
        fams, n, t0 = (result['families'], result['n'], result['t0'])
        voice = fams['voice'] if voice is None else voice
        guarded = set(g['families'])
        split = None if whole else g.get('split_hz')
        if g.get('compare', 'all') == 'all':
            other = sum((fams[f] for f in FAMILIES if f not in guarded and f != 'voice'), np.zeros((n, 2))) + sum(extra, np.zeros((n, 2)))
        else:
            other = np.zeros((n, 2))
        ws, wf, spk = self.word_frames(voice, words, t0)
        m, hop = (len(spk), int(HOP * self.sr))
        lo_hz, hi_hz = g['band_hz']
        V = self.frames_of(self.band_power(voice, lo_hz, hi_hz), m)
        O = self.frames_of(self.band_power(other, lo_hz, hi_hz), m) if np.max(np.abs(other)) > 0 else np.zeros(m)
        budget = [float(V[fr].mean()) / 10 ** ((g['target_db'] + boost.get(wi, 0.0)) / 10) - float(O[fr].mean()) if len(fr) else None for wi, fr in enumerate(wf)]
        sos = signal.butter(4, [lo_hz, hi_hz], 'bandpass', fs=self.sr, output='sos')
        pad = int(0.1 * self.sr)
        hits = {}
        fixed = np.zeros(m)
        frozen = set(frozen)
        ir = self.ir
        for hk, fam, stem, a, seg, rv in result['kept']:
            if fam not in guarded:
                continue
            if split:
                lo0, hi0 = self.split_band(seg, split, pad=int(0.05 * self.sr))
            else:
                lo0, hi0 = (np.zeros_like(seg), seg)
            halves = [self._solo_part(result, fam, stem, a, part, rv) for part in (lo0, hi0)]
            lo, hi = halves
            bl = signal.sosfiltfilt(sos, np.pad(lo, ((pad, pad), (0, 0))), axis=0)
            bh = signal.sosfiltfilt(sos, np.pad(hi, ((pad, pad), (0, 0))), axis=0)
            f0 = (a - pad) // hop
            off = a - pad - f0 * hop
            per = lambda p: self.frames_of(np.pad(p, (off, 0)), -(-(len(p) + off) // hop))
            el, eh, ec = (per((bl ** 2).sum(axis=1)), per((bh ** 2).sum(axis=1)), per((bl * bh).sum(axis=1)))
            if hk in frozen:
                tot_e = el + 2 * ec + eh
                i0, i1 = (max(0, f0), min(m, f0 + len(tot_e)))
                if i1 > i0:
                    fixed[i0:i1] += tot_e[i0 - f0:i1 - f0]
                continue
            hits[hk] = (f0, el, eh, ec)
        if fixed.any():
            budget = [None if b_ is None else b_ - float(fixed[fr].mean()) for b_, fr in zip(budget, wf)]
        keys_h = sorted(hits)

        def terms(hk, fr):
            """The hit's (body, cross, top) band energy over the word's frames, per frame of the word."""
            f0, el, eh, ec = hits[hk]
            i = fr - f0
            i = i[(i >= 0) & (i < len(el))]
            if not len(i):
                return None
            return (float(el[i].sum()) / len(fr), float(ec[i].sum()) / len(fr), float(eh[i].sum()) / len(fr))
        floor = db(g['max_ride_db'])
        cons = []
        for wi, fr in enumerate(wf):
            if not len(fr) or budget[wi] is None:
                continue
            tt = {hk: t for hk in keys_h if (t := terms(hk, fr)) is not None and t[0] + 2 * t[1] + t[2] > 0}
            if not tt:
                continue
            tot = sum((t[0] + 2 * t[1] + t[2] for t in tt.values()))
            if tot <= budget[wi]:
                continue
            best = sum((t[0] + 2 * floor * t[1] + floor ** 2 * t[2] for t in tt.values()))
            bw = budget[wi] if best <= budget[wi] else best * 1.0001
            cons.append((wi, tt, bw / tot))
        gains = {hk: 1.0 for hk in hits}
        report = []
        if cons:
            from scipy.optimize import minimize
            live = sorted({hk for _, tt, _ in cons for hk in tt})
            j = {hk: k for k, hk in enumerate(live)}
            lo_db = g['max_ride_db']

            def cfun(u):
                gg = 10 ** (np.asarray(u) / 20)
                out = []
                for wi, tt, bn in cons:
                    tot = sum((t[0] + 2 * t[1] + t[2] for t in tt.values()))
                    e = sum((t[0] + 2 * gg[j[hk]] * t[1] + gg[j[hk]] ** 2 * t[2] for hk, t in tt.items()))
                    out.append(bn - e / tot)
                return np.array(out)
            sol = minimize(lambda u: -np.sum(u), np.zeros(len(live)), jac=lambda u: -np.ones(len(live)), method='SLSQP', bounds=[(lo_db, 0.0)] * len(live), constraints=[{'type': 'ineq', 'fun': cfun}], options={'maxiter': 500, 'ftol': 1e-09})
            u = np.clip(sol.x, lo_db, 0.0)
            for _ in range(50):
                short = [k for k, v in enumerate(cfun(u)) if v < -1e-09]
                if not short:
                    break
                for k in short:
                    for hk in cons[k][1]:
                        u[j[hk]] = max(lo_db, u[j[hk]] - 0.25)
            for hk in live:
                gains[hk] = 10 ** (u[j[hk]] / 20)
        rides = {hk: {'top_db': round(20 * np.log10(gv), 2), 'split': split, 'whole_db': 0.0} for hk, gv in gains.items() if gv < 0.9989}
        for wi, tt, bn in cons:
            rr = {hk: rides[hk]['top_db'] for hk in tt if hk in rides}
            if rr:
                report.append({'word': ws[wi][0], 's': ws[wi][1], 'hits': rr})
        return (rides, {'rides': len(rides), 'target_db': g['target_db'], 'max_ride_db': g['max_ride_db'], 'split_hz': split, 'capped': [hk for hk, v in rides.items() if v['top_db'] <= g['max_ride_db'] + 0.01], 'words': report})

    def measure_words(self, result, words, cfg, extra=(), voice=None):
        """Measure voice clarity over all other families and texture air, word by word."""
        fams, n, t0 = (result['families'], result['n'], result['t0'])
        voice = fams['voice'] if voice is None else voice
        ws, wf, spk = self.word_frames(voice, words, t0)
        m = len(spk)
        rest = sum((fams[f] for f in FAMILIES if f != 'voice'), np.zeros((n, 2))) + sum(extra, np.zeros((n, 2)))
        ch = cfg['checks']
        lo, hi = ch['clarity']['band_hz']
        V, R = (self.frames_of(self.band_power(voice, lo, hi), m), self.frames_of(self.band_power(rest, lo, hi), m))
        lo2, hi2 = ch['texture_air']['band_hz']
        V2, T = (self.frames_of(self.band_power(voice, lo2, hi2), m), self.frames_of(self.band_power(fams['textures'], lo2, hi2), m))
        clar = {wi: float(10 * np.log10((V[fr].mean() + 1e-20) / (R[fr].mean() + 1e-20))) for wi, fr in enumerate(wf) if len(fr)}
        air = {wi: float(10 * np.log10((V2[fr].mean() + 1e-20) / (T[fr].mean() + 1e-20))) for wi, fr in enumerate(wf) if len(fr) and T[fr].mean() > 1e-14}
        return (clar, air, ws)

    def mix_balanced(self, cues, n, t0, silences, cfg, words, extra_fn=None, want_stems=('tonal',), log=None, loops=2, margin_db=0.3, scratch=None, pinned=None, frozen=()):
        """Solve clarity and air rides, then verify against the delivered voice for at most two more passes."""
        guards = [gn for gn in ('air_guard', 'clarity_guard') if cfg.get(gn)]
        keep = tuple(sorted({f for gn in guards for f in cfg[gn]['families']}))
        pinned = dict(pinned or {})
        r = self.render_families(cues, n, t0, 'balanced', silences, cfg=cfg, want_stems=want_stems, keep=keep, log=log, rides=pinned)
        self.baseline_kept = list(r['kept'])
        extra = extra_fn(r) if extra_fn else []
        split_c = (cfg.get('clarity_guard') or {}).get('split_hz', 1000.0)
        rides, report, boost = (dict(pinned), {'rounds': []}, {gn: {} for gn in guards})
        ch = cfg['checks']
        scratch = None
        delivered = self.voice_as_delivered(r, extra, scratch) if guards and self.delivered_callback else None
        for rnd in range(1 + loops):
            new = {}
            for gn in guards:
                if rnd > 0 and (not boost[gn]):
                    continue
                rr, rep = self.guard_rides(r, words, cfg, gn, extra, boost[gn], whole=rnd > 0, voice=delivered, frozen=frozen)
                report.setdefault(gn, []).append(rep)
                new = self.combine_rides(new, rr, split_c)
            if not new:
                break
            rides = self.combine_rides(rides, new, split_c)
            del r
            if log is not None:
                log.clear()
            r = self.render_families(cues, n, t0, 'balanced', silences, cfg=cfg, want_stems=want_stems, keep=keep, log=log, rides=rides)
            delivered = self.voice_as_delivered(r, extra, scratch)
            clar, air, ws = self.measure_words(r, words, cfg, extra, voice=delivered)
            need_c, need_a = (ch['clarity']['min_db'] + margin_db, ch['texture_air']['min_db_under_voice'] + margin_db)
            short_c = {wi: need_c - v for wi, v in clar.items() if v < need_c}
            short_a = {wi: need_a - v for wi, v in air.items() if v < need_a}
            report['rounds'].append({'round': rnd, 'hits_ridden': len(rides), 'words_short_clarity': [ws[wi][0] for wi in short_c], 'words_short_air': [ws[wi][0] for wi in short_a]})
            if not short_c and (not short_a):
                break
            for wi, d in short_c.items():
                if 'clarity_guard' in boost:
                    boost['clarity_guard'][wi] = boost['clarity_guard'].get(wi, 0.0) + d + margin_db
            for wi, d in short_a.items():
                if 'air_guard' in boost:
                    boost['air_guard'][wi] = boost['air_guard'].get(wi, 0.0) + d + margin_db
        return (r, rides, report, extra)

    def trade_floor(self, r, rides, rides_full, half, cues, cfg, silences, words, t0, n, log, keep, scratch, extra, floor, frac, step=0.125, margin_db=0.3):
        """Deepen only half-ridden accents covering words below the clarity trade floor."""
        by_hit = {self.hit_key(c): c for c in cues}
        span = {hk: (float(c['t']) - 0.02, float(c['t']) + min(float(c.get('dur') or 0.4), 1.0)) for hk, c in by_hit.items()}
        deep, rounds = ({}, [])
        for _ in range(int(round((1.0 - frac) / step)) + 1):
            delivered = self.voice_as_delivered(r, extra, scratch)
            clar, _, ws = self.measure_words(r, words, cfg, extra, voice=delivered)
            low = {wi: v for wi, v in clar.items() if v < floor + margin_db}
            grow = sorted({hk for wi in low for hk in half if hk in span and deep.get(hk, frac) < 1.0 and (span[hk][0] < ws[wi][2]) and (span[hk][1] > ws[wi][1])})
            rounds.append({'words_under': [{'word': ws[wi][0], 's': ws[wi][1], 'db': round(v, 1)} for wi, v in sorted(low.items())], 'deepened': grow})
            if not grow:
                break
            for hk in grow:
                deep[hk] = round(min(1.0, deep.get(hk, frac) + step), 3)
            rides = dict(rides)
            for hk, f in deep.items():
                rides[hk] = self.scale_ride(rides_full[hk], f)
            del r
            log.clear()
            r = self.render_families(cues, n, t0, 'balanced', silences, cfg=cfg, want_stems=True, keep=keep, log=log, rides=rides)
        return (r, rides, {'floor_db': floor, 'step': step, 'depth_fraction_now': deep, 'rounds': rounds, 'why': "the trade's floor: a word under a half-ridden accent is never left under floor_db; the accents over it are ridden deeper, toward full depth, until it clears"})

    def score_accent_dips(self, r, score, cues, cfg, rides, silences, t0, n):
        """Dip the score only in the bands where it masks an accent or impact."""
        g = cfg['score_guard']
        need = float(cfg['checks']['accents_over_score']['min_db']) + float(g.get('accent_margin_db', 0.5))
        cap = float(g['max_dip_db'])
        hold, ramp = (float(g.get('hold_s', 0.15)), float(g.get('ramp_s', 0.3)))
        split = float(g['split_hz'])
        M = {'n': n, 't0': t0, 'keys': self.keys_1k(r), 'sheet': {'silences': silences}, 'master': {'gain_db': 0.0}}
        cfg_r = dict(cfg, mode='balanced', rides=rides)
        trims = self.stem_trims()
        L = int(0.12 * self.sr)
        edges = 1000 * 2 ** (np.arange(-15, 14) / 3)
        fbin = np.fft.rfftfreq(L, 1 / self.sr)
        han = np.hanning(L)

        def bands_of(x):
            X = np.abs(np.fft.rfft(x * han)) ** 2
            return np.array([X[(fbin >= lo) & (fbin < hi)].sum() + 1e-20 for lo, hi in zip(edges[:-1], edges[1:])])
        hits = []
        for c in cues:
            if self.family_of(c, cfg) not in ('accents', 'impacts'):
                continue
            o = self.solo_mixed(c, M, cfg_r, trims)
            if o is None:
                continue
            a, y, _ = o
            mo = y.mean(axis=1)
            e = np.sqrt(np.maximum(signal.convolve(mo ** 2, np.ones(max(1, round(.05 * self.sr))) / max(1, round(.05 * self.sr)), mode='same'), 0.0))  # FFT rounding dips just under zero
            k = int(np.argmax(e))
            a0 = max(0, k - int(0.06 * self.sr))
            # A short dry hit still has a 120 ms measurement window; its missing tail is silence.
            seg = np.pad(mo[a0:a0 + L], (0, max(0, L - len(mo[a0:a0 + L]))))
            g0 = a + a0
            if g0 + L > n:
                continue
            Eb = bands_of(seg)
            dom = Eb >= Eb.max() * 0.1
            hits.append({'hk': self.hit_key(c), 'event': c.get('event'), 't': round(float(c.get('beat', c['t'])), 3), 'g0': g0, 'Eb': Eb, 'dom': dom, 'band': (float(edges[:-1][dom].min()), float(edges[1:][dom].max()))})

        def margin_of(h, x):
            Mb = bands_of(x[h['g0']:h['g0'] + L].mean(axis=1))
            return float(np.max(10 * np.log10(h['Eb'][h['dom']] / Mb[h['dom']])))
        before = {h['hk']: margin_of(h, score) for h in hits}
        out = score
        report = []
        top_hits = [h for h in hits if before[h['hk']] < need - 0.05 and h['band'][0] >= split]
        if top_hits:
            low, high = self.split_band(score, split)
            dips, gain = ({}, np.ones(n))
            for _ in range(6):
                x = low + high * gain[:, None] if dips else score
                short = {h['hk']: need - margin_of(h, x) for h in top_hits}
                short = {hk: sh for hk, sh in short.items() if sh > 0.05 and dips.get(hk, 0.0) > cap}
                if not short:
                    break
                for hk, sh in short.items():
                    dips[hk] = max(cap, dips.get(hk, 0.0) - sh - 0.1)
                byk = {h['hk']: h for h in top_hits}
                gain, _ = self.span_dips([(byk[hk]['g0'], byk[hk]['g0'] + L, d) for hk, d in dips.items()], n, hold, ramp)
            out = low + high * gain[:, None]
            del low, high
            report += [{'hit': hk, 'how': f'its top, above {split:g} Hz', 'dip_db': round(d, 1)} for hk, d in dips.items()]
        for h in [h for h in hits if before[h['hk']] < need - 0.05 and h['band'][0] < split]:
            f_lo, f_hi = (max(30.0, h['band'][0] / 2 ** (1 / 3)), min(18000.0, h['band'][1] * 2 ** (1 / 3)))
            s0, s1 = (max(0, h['g0'] - int(1.2 * self.sr)), min(n, h['g0'] + L + int(1.2 * self.sr)))
            x = out[s0:s1]
            l1, h1 = self.split_band(x, f_lo)
            b, top = self.split_band(h1, f_hi)
            d, gl = (0.0, np.ones(s1 - s0))
            for _ in range(6):
                y = l1 + b * gl[:, None] + top
                sh = need - margin_of(dict(h, g0=h['g0'] - s0), y)
                if sh <= 0.05 or d <= cap:
                    break
                d = max(cap, d - sh - 0.1)
                gl, _ = self.span_dips([(h['g0'] - s0, h['g0'] - s0 + L, d)], s1 - s0, hold, ramp)
            if d < 0:
                out = out.copy() if out is score else out
                out[s0:s1] = l1 + b * gl[:, None] + top
                report.append({'hit': h['hk'], 'how': f'its band, {f_lo:.0f}–{f_hi:.0f} Hz', 'dip_db': round(d, 1)})
        after = {h['hk']: margin_of(h, out) for h in hits} if report else before
        rep = {'rule_db': need, 'hits': len(hits), 'hits_dipped': len(report), 'weakest_before_db': round(min(before.values()), 1) if before else None, 'weakest_after_db': round(min(after.values()), 1) if after else None, 'dips': [dict(x, margin_before_db=round(before[x['hit']], 1), margin_after_db=round(after[x['hit']], 1)) for x in sorted(report, key=lambda x: x['hit'])]}
        return (out, rep)

    def never_over_guard(self, r, rides, score, cues, cfg, silences, words, t0, n, log, scratch, margin_db=0.5, rounds=3, cap_db=-12.0, frozen=()):
        """Enforce family hit maxima and the voice margin after the other guards."""
        g = cfg.get('score_guard') or {}
        hold, ramp = (float(g.get('hold_s', 0.15)), float(g.get('ramp_s', 0.3)))
        split_c = (cfg.get('clarity_guard') or {}).get('split_hz', 1000.0)
        keep = tuple(sorted({f for gn in ('air_guard', 'clarity_guard') if cfg.get(gn) for f in cfg[gn]['families']}))
        hitfam = ('accents', 'textures', 'swells', 'impacts')
        limits = {f: v['hit_max_lu'] for f, v in cfg['checks']['levels'].items() if f in hitfam and 'hit_max_lu' in v}
        hop = int(HOP * self.sr)
        trims = self.stem_trims()
        lin = lambda d: 10 ** (d / 20)
        report = {'rounds': [], 'rides': {}, 'score_dips': []}
        sdips = []

        def score_now():
            if not sdips:
                return score
            gain, _ = self.span_dips(sdips, n, hold, ramp)
            return score * gain[:, None]
        for rnd in range(rounds):
            sc = score_now() if score is not None else np.zeros((n, 2))
            delivered = self.voice_as_delivered(r, [sc], scratch)
            vI = self.integrated(delivered, scratch)
            Lv = self.lufs_of_power(self.power_curve(delivered, 0.4))
            ws, wf, spk = self.word_frames(delivered, words, t0)
            sel = np.zeros(len(Lv), bool)
            for fr in wf:
                sel[fr[fr < len(sel)]] = True
            fams = r['families']
            effects = sum((fams[f] for f in hitfam))
            groups = {f: fams[f] for f in hitfam}
            groups.update({'score': sc, 'plate': fams['plate'], 'effects': effects, 'everything_else': effects + sc + fams['plate']})
            Lg = {k: self.lufs_of_power(self.power_curve(v, 0.4)) for k, v in groups.items()}
            del effects
            viol = {}
            for k, L in Lg.items():
                m = Lv[:len(L)] - L
                bad = np.flatnonzero(sel[:len(L)] & (L > -70) & (m < margin_db))
                if len(bad):
                    viol[k] = (bad, m)
            M = {'n': n, 't0': t0, 'keys': self.keys_1k(r), 'sheet': {'silences': silences}, 'master': {'gain_db': 0.0}}
            cfg_r = dict(cfg, mode='balanced', rides=rides)
            bus_max = {f: float(Lg[f].max()) - vI for f in hitfam}
            want = set()
            for k in viol:
                want |= set(hitfam) if k in ('effects', 'everything_else') else {k} if k in hitfam else set()
            want |= {f for f, lim in limits.items() if bus_max[f] > lim - 0.5}
            curves, starts = ({}, {})
            for c in cues:
                f = self.family_of(c, cfg)
                if f not in want:
                    continue
                o = self.solo_mixed(c, M, cfg_r, trims)
                if o is None:
                    continue
                a, y, _ = o
                g0 = max(0, (a - int(0.3 * self.sr)) // hop * hop)
                g1 = min(n, -(-(a + len(y) + int(0.3 * self.sr)) // hop) * hop)
                buf = np.zeros((g1 - g0, 2))
                buf[a - g0:a - g0 + len(y)] = y[:max(0, min(len(y), g1 - a))]
                curves[self.hit_key(c)] = (f, g0 // hop, self.power_curve(buf, 0.4))
                starts[self.hit_key(c)] = a
            keep_ms = float(((cfg['families'].get('impacts') or {}).get('under_speech') or {}).get('speech_hf', {}).get('keep_attack_ms', 80.0))
            new, new_body = ({}, {})

            def ride(hk, d, i=None):
                """d (negative dB) at frame i. An impact whose 400 ms window at i misses its attack (its first keep_ms) is
                over only in its body: that much comes off the body alone, and the attack keeps its weight. A frozen hit (an
                approved span's) is never ridden."""
                if hk in frozen:
                    return
                if curves[hk][0] == 'impacts' and i is not None and (i * hop - int(0.2 * self.sr) > starts[hk] + int(keep_ms / 1000 * self.sr)):
                    new_body[hk] = min(new_body.get(hk, 0.0), d)
                else:
                    new[hk] = min(new.get(hk, 0.0), d)
            for hk, (f, off, pw) in curves.items():
                if f in limits:
                    top = float(self.lufs_of_power(pw).max()) - vI
                    if top > limits[f] - 0.3:
                        ride(hk, -(top - limits[f] + 0.3))
            dip_now = self.span_dips(sdips, n, hold, ramp)[1] if sdips else np.zeros(len(Lv))
            pw_of = lambda L_, i_: 10 ** ((float(L_[i_]) + 0.691) / 10)
            for k, (bad, m) in viol.items():
                fams_k = set(hitfam) if k in ('effects', 'everything_else') else {k} if k in hitfam else set()
                for i in bad:
                    need = margin_db - float(m[i]) + 0.2
                    contrib = [(float(pw[i - off]), hk) for hk, (f, off, pw) in curves.items() if f in fams_k and off <= i < off + len(pw)]
                    sp = pw_of(Lg['score'], i) if k in ('score', 'everything_else') else 0.0
                    total = sum((p for p, _ in contrib)) + sp
                    for p_, hk in contrib:
                        if total > 0 and p_ >= 0.1 * total:
                            ride(hk, -need, i)
                    if total > 0 and sp >= 0.1 * total:
                        sdips.append((i * hop, (i + 1) * hop, float(dip_now[min(i, len(dip_now) - 1)]) - need))
            report['rounds'].append({'round': rnd, 'over': {k: {'frames': len(b), 'worst_db': round(float(m[b].min()), 1), 'at_s': round(t0 + (int(b[np.argmin(m[b])]) + 0.5) * HOP, 3)} for k, (b, m) in viol.items()}, 'hits_ridden': len(set(new) | set(new_body)), 'score_dips': len(sdips)})
            if not new and (not new_body) and (not viol):
                break
            if not new and (not new_body) and all((k == 'score' for k in viol)):
                continue
            capped = {}
            for hk in set(new) | set(new_body):
                had = report['rides'].get(hk) or 0.0
                w = max(cap_db - had, new.get(hk, 0.0))
                b = max(cap_db - had - w, min(0.0, new_body.get(hk, 0.0) - w))
                if w < -0.05 or b < -0.05:
                    capped[hk] = {'top_db': 0.0, 'split': None, 'whole_db': round(w, 2)}
                    if b < -0.05:
                        capped[hk].update(body_db=round(b, 2), keep_attack_ms=keep_ms)
                    report['rides'][hk] = round(had + w + b, 2)
            if not capped and (not sdips):
                break
            if capped:
                rides = self.combine_rides(rides, capped, split_c)
                del r
                if log is not None:
                    log.clear()
                r = self.render_families(cues, n, t0, 'balanced', silences, cfg=cfg, want_stems=True, keep=keep, log=log, rides=rides)
        score_out = score_now() if score is not None else None
        if sdips:
            _, c = self.span_dips(sdips, n, hold, ramp)
            report['score_dips'] = {'spans': len(sdips), 'deepest_db': round(float(c.min()), 1), 'time_dipped_s': round(float((c < -0.5).sum() * HOP), 2)}
        return (r, rides, score_out, report)

    def __init__(self, voice, *, sample_rate=48000, fps=30.0, config=None, renderer=None,
                 impulse_response=None, film_dir=None, trims=None, delivered_voice=None, motion=None):
        if renderer is None or impulse_response is None:
            from slate.sound.effects import rendering
        self.sr = int(sample_rate)
        self.fps = Fraction(str(float(fps))).limit_denominator(1_000_000)
        self.frame_ms = 1000.0 / float(fps)
        if self.sr < 16000 or not math.isfinite(self.frame_ms) or self.frame_ms <= 0:
            raise ValueError('A positive frame rate and audio sample rate of at least 16000 Hz are required.')
        self.voice = np.asarray(voice, dtype=np.float64)
        if self.voice.ndim != 2 or self.voice.shape[1] != 2 or not np.isfinite(self.voice).all():
            raise ValueError('Voice must be a finite stereo array.')
        self.cfg = settings(config)
        self.renderer = renderer or rendering.render_parts
        self.ir = impulse_response if impulse_response is not None else rendering.make_ir(self.sr)
        self.film_dir, self.trims = film_dir, dict(trims or {})
        self.motion = copy.deepcopy(motion)
        self.delivered_callback = delivered_voice
        self.current = None

    @staticmethod
    def hit_key(cue):
        return str(cue.get('id') or f"{cue.get('event')}@{float(cue['t']):.5f}")

    @staticmethod
    def family_of(cue, cfg=None):
        family = cue.get('family')
        if family not in FAMILIES:
            raise Refused(f'Cue {cue.get("id", cue.get("event"))!r} has no recognized sound family.')
        return family

    def stem_trims(self):
        return self.trims

    def keys_1k(self, result):
        return {k: v[::max(1, self.sr // 1000)] for k, v in result['keys'].items()}

    def integrated(self, samples, scratch=None):
        return meter.integrated(samples, self.sr)

    def voice_as_delivered(self, result, extra=(), scratch=None):
        if self.delivered_callback is None:
            # Provisional effects-only rendering; final mix must supply its real master feedback.
            return result['families']['voice']
        score = sum(extra, np.zeros_like(self.voice))
        voice = np.asarray(self.delivered_callback(result, score), dtype=np.float64)
        if voice.shape != self.voice.shape or not np.isfinite(voice).all():
            raise ValueError('Delivered-voice callback must return finite stereo samples at pre-master scale.')
        return voice

    def render_families(self, cues, n, t0, mode='balanced', silences=(), trims=None, log=None,
                        cfg=None, want_stems=True, rides=None, keep=()):
        cfg = cfg or self.cfg
        rides = rides or {}
        env = self.proof2_env(self.voice)
        keys = {name: self.speech_key(self.voice, cfg['keys'][name])
                for name in sorted(self.keys_needed(cfg, 'balanced'))}
        specs = cfg['families']
        split = (cfg.get('clarity_guard') or {}).get('split_hz', 600.0)
        def cue_hook(c, seg, a):
            family = self.family_of(c)
            if family == 'voice':
                return seg, {}
            stem = c.get('stem', 'fx')
            tol = self.sync_tol_ms(c, seg, t0 + a / self.sr)
            from slate.sound.effects.voices import parameters
            out, info = self.cue_mix(family, stem, seg, a, specs[family], keys,
                                     parameters(c).get('type'), c.get('anchor'), tol)
            hit = self.hit_key(c)
            if hit in rides:
                out = self.apply_ride(out, rides[hit], split)
                info = {**info, 'ride': copy.deepcopy(rides[hit])}
            return out, info
        def bus_hook(family, stem, samples):
            return self.bus_mix(family, stem, samples, specs[family], env, keys)
        result = self.renderer(cues, n, voice=self.voice, film_dir=self.film_dir, sample_rate=self.sr,
                               trims=self.trims if trims is None else trims, silences=silences, t0=t0,
                               process_cue=cue_hook, process_bus=bus_hook)
        result['families'].setdefault('voice', self.voice)
        for family in FAMILIES:
            result['families'].setdefault(family, np.zeros_like(self.voice))
        result.update(keys=keys, env_p2=env, specs=specs, silences=list(silences), t0=t0, n=n)
        if log is not None:
            log.extend(result.get('applied', []))
        self.current = result
        return result

    def solo_mixed(self, cue, metadata=None, cfg=None, trims=None):
        """An already treated hit with its own return, masks and bus treatment, at current mix scale."""
        result = self.current
        hit = self.hit_key(cue)
        match = [v for v in result['kept'] if v[0] == hit]
        if not match:
            return None
        if len(match) != 1:
            raise Refused(f'Duplicate sound hit identity {hit!r}.')
        _, family, stem, a, seg, reverb = match[0]
        return a, self._solo_part(result, family, stem, a, seg, reverb), family

    def _solo_part(self, result, family, stem, a, segment, reverb):
        n, t0 = result['n'], result['t0']
        sl = slice(a, a + len(segment))
        mask = self.silence_mask(result['silences'], stem, t0, n)
        dry_input = segment if mask is None else segment * mask[sl, None]
        dry = self.bus_mix(family, stem, dry_input, result['specs'][family], result['env_p2'][sl],
                          {k: v[sl] for k, v in result['keys'].items()})
        if mask is not None:
            dry = dry.copy()
            dry[mask[sl] == 0] = 0
        if reverb is None:
            return dry
        ret = 'tverb' if stem == 'tonal' else 'verb'
        wet = np.stack([signal.fftconvolve(segment[:, ch] * db(reverb), self.ir[:, ch])
                        for ch in range(2)], axis=1)[:n-a]
        sw = slice(a, a + len(wet))
        mask = self.silence_mask(result['silences'], ret, t0, n)
        if mask is not None:
            wet = wet * mask[sw, None]
        wet = self.bus_mix(family, ret, wet, result['specs'][family], result['env_p2'][sw],
                          {k: v[sw] for k, v in result['keys'].items()})
        if mask is not None:
            wet[mask[sw] == 0] = 0
        out = np.pad(dry, ((0, max(0, len(wet)-len(dry))), (0, 0)))
        out[:len(wet)] += wet
        return out

    def run(self, cues, *, words=(), t0=0.0, silences=(), score=None, pinned=None,
            frozen=(), full_depth=()):
        """Render the full approved order; pinned/frozen inputs must come from resolve_approved."""
        from slate.sound.effects.rendering import expand_cues
        cues = expand_cues(cues)
        cfg, n = self.cfg, len(self.voice)
        voice_available = bool(np.any(self.voice))
        if not voice_available:
            cfg = copy.deepcopy(cfg)
            cfg.update(clarity_guard=False,air_guard=False,never_over_guard=False)
        if n == 0:
            raise ValueError('An effect mix requires at least one sample.')
        identities = [self.hit_key(c) for c in cues]
        if len(set(identities)) != len(identities):
            raise Refused('The cue sheet contains duplicate hit identities.')
        frozen, full_depth = set(frozen), set(full_depth)
        if set(pinned or {}) - frozen or (frozen | full_depth) - set(identities):
            raise Refused('Approved ride identities do not match the cue sheet.')
        score = None if score is None else np.asarray(score, dtype=np.float64)
        if score is not None and (score.shape != self.voice.shape or not np.isfinite(score).all()):
            raise ValueError('Score must be finite stereo audio with the same length as the voice.')
        words = list(words or ())
        unit = 'word'
        if not words and voice_available:
            from slate.sound.balance import phrases
            words = phrases(self.word_frames(self.voice, (), t0)[2], t0=t0)
            unit = 'phrase'
        log = []
        extra = [] if score is None else [score]
        r, rides, report, _ = self.mix_balanced(cues, n, t0, silences, cfg, list(words),
                extra_fn=lambda result: extra, want_stems=True, log=log, pinned=pinned, frozen=frozen)
        rides_full = copy.deepcopy(rides)
        fraction = float((cfg.get('clarity_guard') or {}).get('depth_fraction', 1.0))
        if not 0 <= fraction <= 1:
            raise ValueError('Clarity depth_fraction must be between zero and one.')
        half = sorted(self.hit_key(c) for c in cues if c['family'] == 'accents'
                      and self.hit_key(c) in rides and self.hit_key(c) not in frozen | full_depth)
        if fraction < 1 and half:
            rides = {hit: self.scale_ride(ride, fraction) if hit in half else ride for hit, ride in rides.items()}
            log.clear()
            r = self.render_families(cues,n,t0,silences=silences,cfg=cfg,rides=rides,log=log)
            floor = cfg['checks']['clarity'].get('trade_floor_db')
            if floor is not None:
                r, rides, report['trade_floor'] = self.trade_floor(r,rides,rides_full,half,cues,cfg,silences,
                      list(words),t0,n,log,(),None,extra,float(floor),fraction)
        else:
            half = []
        if cfg.get('never_over_guard'):
            r, rides, score, report['never_over_guard'] = self.never_over_guard(r,rides,score,cues,cfg,silences,
                    list(words),t0,n,log,None,frozen=frozen,**cfg['never_over_guard'])
        if score is not None and (cfg.get('score_guard') or {}).get('accent_margin_db') is not None:
            score, report['score_accents'] = self.score_accent_dips(r,score,cues,cfg,rides,silences,t0,n)
        extra = [] if score is None else [score]
        delivered = self.voice_as_delivered(r, extra)
        clarity, air, rows = self.measure_words(r,list(words),cfg,extra,voice=delivered)
        report.update(delivered_voice_feedback=self.delivered_callback is not None,voice_available=voice_available,unit=unit,
                      final_clarity_db={str(i): round(v,3) for i,v in clarity.items()},
                      final_air_db={str(i): round(v,3) for i,v in air.items()},
                      words=[{'word': row[0], 's':row[1], 'e':row[2]} for row in rows])
        by_hit = {self.hit_key(c): c for c in cues}
        placements = {row[0]: (row[3], len(row[4])) for row in r['kept']}
        rows = []
        for hit in sorted(set(rides) | frozen):
            cue = by_hit[hit]
            a, size = placements.get(hit, (round((cue['t']-t0)*self.sr), 0))
            ride = copy.deepcopy(rides.get(hit, {}))
            full = copy.deepcopy(rides_full.get(hit, {}))
            rows.append({'hit': hit, 't': t0+a/self.sr, 'end': t0+(a+size)/self.sr,
                         'family': cue['family'], 'depth_db': self.ride_db(ride),
                         'full_depth_db': self.ride_db(full), 'half_depth': hit in half,
                         'pinned': hit in frozen, 'ride': ride, 'full_ride': full,
                         'cue': copy.deepcopy(cue)})
        final_result = r
        baseline = list(self.baseline_kept)
        def solo_hit(hit, baseline=False, include_return=True):
            kept = final_result['baseline_kept'] if baseline else final_result['kept']
            row = next((row for row in kept if row[0] == hit), None)
            if row is None:
                raise KeyError(hit)
            _, family, stem, a, segment, reverb = row
            return a, self._solo_part(final_result,family,stem,a,segment,reverb if include_return else None)
        r.update(baseline_kept=baseline,solo_hit=solo_hit,ride_rows=rows)
        sync_rows = [{'id': self.hit_key(c), 'cue': copy.deepcopy(c),
                      'start_sample': placements.get(self.hit_key(c), (None,0))[0]} for c in cues]
        def measure_sync(transform=None, sync_frames=1.0):
            from slate.sound.verify import anchor_offset
            threshold_ms = self.frame_ms * float(sync_frames)
            if not math.isfinite(threshold_ms) or threshold_ms <= 0:
                raise ValueError('A sound sync threshold must be positive and finite.')
            measured, over, unmeasured = [], [], []
            motion = self.motion
            for row in sync_rows:
                cue = row['cue']
                rule = cue.get('anchor')
                if rule == 'clicks':
                    # Legacy counters carry an explicit click schedule, not one waveform peak.
                    from slate.sound.effects.voices import count_ticks, parameters
                    try:
                        params = parameters(cue)
                        params.setdefault('fps_num', self.fps.numerator)
                        params.setdefault('fps_den', self.fps.denominator)
                        if motion is None and self.film_dir is not None:
                            from slate.sound.film import load
                            motion = (load(self.film_dir).brand or {}).get('motion') or {}
                        ticks = count_ticks(params, self.sr, motion)
                        a, before = solo_hit(row['id'], baseline=True, include_return=False)
                        b, after = solo_hit(row['id'], include_return=False)
                        if transform is not None:
                            a, before = transform(a,before,cue)
                            b, after = transform(b,after,cue)
                        if not ticks or not np.any(before) or not np.any(after):
                            raise ValueError('the mixed counter has no audible clicks')
                        placed = [item for item in final_result.get('applied', []) if item.get('hit') == row['id']]
                        if len(placed) != 1 or not isinstance(placed[0].get('start_sample'), (int, float)):
                            raise ValueError('the counter has no unique rendered placement record')
                        start = t0 + placed[0]['start_sample'] / self.sr
                        first = start + ticks[0]['at']
                        last = start + ticks[-1]['at']
                        beat = float(cue.get('beat', first))
                        beat_end = float(cue.get('beat_end', float(cue['t']) + float(params['dur'])))
                        delta, end_delta = (first-beat)*1000, (last-beat_end)*1000
                        entry = {'id':row['id'], 'kind':cue.get('kind'), 'rule':rule,
                                 'signal':'scheduled_clicks', 'beat':beat, 'beat_end':beat_end,
                                 'before_anchor_s':round(first,8), 'after_anchor_s':round(first,8),
                                 'end_anchor_s':round(last,8), 'shift_ms':0.0,
                                 'delta_ms':round(delta,5), 'end_delta_ms':round(end_delta,5),
                                 'within_frame':max(abs(delta),abs(end_delta))<=threshold_ms+1e-9,
                                 'clicks':len(ticks), 'audible_before':True, 'audible_after':True}
                        measured.append(entry)
                        if not entry['within_frame']:
                            over.append(entry)
                    except (ValueError, KeyError, UsageError) as error:
                        unmeasured.append({'id':row['id'],'why':str(error)})
                    continue
                if rule in (*NOSYNC_ANCHORS, 'none') or 'beat' not in cue:
                    continue
                try:
                    # The dry gesture carries sync. Its hall tail must not redefine an end anchor.
                    a, before = solo_hit(row['id'],baseline=True,include_return=False)
                    b, after = solo_hit(row['id'],include_return=False)
                    if transform is not None:
                        a, before = transform(a,before,cue)
                        b, after = transform(b,after,cue)
                    i0 = anchor_offset(before,rule,self.sr)
                    i1 = anchor_offset(after,rule,self.sr)
                    if i0 is None or i1 is None:
                        raise ValueError('the mixed hit has no audible anchor')
                    start = t0 + (a+i0)/self.sr
                    end = t0 + (b+i1)/self.sr
                    delta = (end-float(cue['beat']))*1000
                    entry = {'id':row['id'],'kind':cue.get('kind'),'beat':float(cue['beat']),'rule':rule,
                             'before_anchor_s':round(start,8),'after_anchor_s':round(end,8),
                             'shift_ms':round((end-start)*1000,5),'delta_ms':round(delta,5),
                             'within_frame':abs(delta)<=threshold_ms+1e-9}
                    measured.append(entry)
                    if not entry['within_frame']:
                        over.append(entry)
                except (ValueError, KeyError) as error:
                    unmeasured.append({'id':row['id'],'why':str(error)})
            return {'rows':measured,'over':over,'unmeasured':unmeasured,'sample_rate':self.sr,
                    'threshold_ms':threshold_ms,'scope':'master' if transform is not None else 'pre_master',
                    'signal':'dry_hit'}
        r.update(sync_rows=sync_rows,measure_sync=measure_sync)

        r.update(score=score, rides=rides, rides_full=rides_full, half_depth=half,
                 pinned=copy.deepcopy(pinned or {}), frozen=sorted(frozen), full_depth=sorted(full_depth),
                 guard_report=report, config=cfg)
        return r


def settings(overrides=None):
    """Merge portable processing defaults with the existing public balance schema."""
    def merge(base, patch):
        out = copy.deepcopy(base)
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
        return out
    patch = copy.deepcopy(overrides or {})
    checks = patch.setdefault('checks', {})
    for key in ('clarity', 'never_over', 'texture_air', 'accents_over_score', 'levels'):
        if key in patch:
            checks[key] = merge(checks.get(key, {}), patch.pop(key))
    return merge(DEFAULT, patch)


def approved_family(cue, balance_used):
    """The source's processing family, never a classification from the current palette."""
    family = cue.get('family')
    if family is not None:
        if family not in FAMILIES:
            raise Refused('An approved cue names an unrecognized sound family.')
        return family
    kind = cue.get('p3kind')
    if balance_used is not None and not isinstance(balance_used, dict):
        raise Refused('An approved balance record has no usable sound-family mapping.')
    families = (balance_used or {}).get('families') or {}
    if not isinstance(families, dict):
        raise Refused('An approved balance record has no usable sound-family mapping.')
    candidates = [name for name, spec in families.items()
                  if isinstance(spec, dict) and isinstance(spec.get('kinds'), list)
                  and kind is not None and kind in spec['kinds']]
    if len(candidates) != 1 or candidates[0] not in FAMILIES:
        raise Refused('An approved cue has no unique approval-bound sound family.',
                      hint="Keep its source family, or supply its approved balance family's p3kind mapping.")
    return candidates[0]


def suppressed_by_score(cue):
    """An authored bed or bloom is replaced by a composed score."""
    return (cue.get('role') in ('bed', 'bloom') or cue.get('type') in ('bed', 'bloom')
            or cue.get('p3kind') in ('bed', 'landing'))


def _source_voice(cue):
    return cue.get('family') == 'voice' or cue.get('type') == 'vo' or cue.get('stem') in ('vo', 'voice')


def _needs_reference_wave(cue):
    """Legacy cues may reuse literal synthesis, never unbound recordings or brand curves."""
    from slate.motion import eases
    from slate.sound.effects.voices import parameters
    params = parameters(cue)
    typ = params.get('type')
    if typ in ('sample', 'snote', 'spad'):
        return True
    if typ in ('whoosh', 'air', 'swell', 'countup3'):
        # voices.ease parses built-ins before consulting brand motion. A brand
        # cannot replace linear/sine; an unknown name depends on its current pack.
        if eases.parse(params.get('ease', 'linear')) is None:
            return True
    return typ == 'layered' and any(_needs_reference_wave(c) for c in params.get('layers') or [])


def resolve_approved(sheet, cues, load_reference=None, *, composed_score=False,
                     render_cue=None, sample_rate=None):
    """Resolve explicit, authorized reference spans to exact rides and frozen identities.

    The caller must verify each reference's current approval before returning its
    contents. Cue markers or numeric rides by themselves are never approvals.
    """
    from slate.sound.effects.rendering import expand_cues
    from slate.sound.effects.voices import parameters
    cues = expand_cues(cues)
    # The engine removes score-replaced beds before rendering. Check the complete
    # sheet too, so an unapproved cue cannot hide by declaring itself a bed.
    complete = expand_cues(sheet.get('cues', cues))
    def reference_hits(source):
        normalized = []
        for raw in source:
            cue = copy.deepcopy(raw)
            if cue.get('notes') and not isinstance(cue['notes'][0], dict):
                cue['params'] = {**(cue.get('params') or {}), 'notes': cue.pop('notes')}
            normalized.append(cue)
        return expand_cues(normalized)

    def event_key(c):
        if c.get('gesture') is not None:
            return str(c.get('event')) + ':' + str(c['id']).rsplit('.', 1)[-1]
        return c.get('event')
    matches = (sheet.get('music') or {}).get('match') or []
    pinned, frozen, full_depth = {}, set(), set()
    used_spans = []
    for match in matches:
        if not match.get('pin_cues') and not match.get('pin_rides'):
            continue
        if load_reference is None:
            raise Refused('Approved sound spans require an approval-checked reference resolver.')
        clip = match['clip']
        reference = load_reference(clip)
        source = reference['cue_sheet']
        start, end = map(float, source['window'])
        start = max(start, float(match.get('from', start)))
        end = min(end, float(match.get('to', end)))
        if match.get('offset_s') or not math.isfinite(start + end) or not start < end:
            raise Refused('An approved sound span must retain a valid window on its approved clock.')
        if any(start < b and end > a for a, b in used_spans):
            raise Refused('Approved sound spans overlap; their frozen cues would be ambiguous.')
        used_spans.append((start, end))
        def in_span(c):
            return start - .001 <= float(c.get('beat', c['t'])) < end
        all_source = reference_hits(source['cues'])
        original = [c for c in all_source if in_span(c)]
        balance_used = reference.get('balance_used') or {}
        for cue in original:
            if not _source_voice(cue):
                cue['family'] = approved_family(cue, balance_used)
        source_events = [event_key(c) for c in original if event_key(c) is not None]
        if any(event_key(c) is None for c in original if not _source_voice(c)) or len(set(source_events)) != len(source_events):
            raise Refused('An approved cue span must give every cue a unique event identity.')
        source_by_event = {event_key(c): c for c in original if event_key(c) is not None}
        def excluded(old):
            return _source_voice(old) or (composed_score and suppressed_by_score(old))
        expected = {event_key(c) for c in original if not excluded(c)}
        def same_sound(old, cue):
            if abs(float(cue.get('beat', cue['t'])) - float(old.get('beat', old['t']))) > .002:
                raise Refused('An approved cue does not match its reference event at the same beat.')
            routing_before = (old['family'], old.get('stem', 'fx'), old.get('anchor'), old.get('beat_end'))
            routing_after = (cue.get('family'), cue.get('stem', 'fx'), cue.get('anchor'), cue.get('beat_end'))
            if routing_before != routing_after:
                raise Refused('An approved cue changed its family, stem or anchor; a new approval is required.')
            if old['t'] != cue['t']:
                raise Refused('An approved cue changed its sound parameters; a new approval is required.')
            fields = ('attack','breath','breath_phase','bw','cents','decay','dur','ease','f0','f1',
                      'fade_in','fade_out','file','floor','fps_den','fps_num','from','hold_frames','jitter_db',
                      'layers','lp_from','lp_to','notes','pan','pan_from','pan_to','peak','pitch','rate',
                      'release','reverse','ring','role','scale','section','seed','skip','step','take','to',
                      'trim','type','vel','gain','reverb','hp','lp','lp_hint','pan_hint','clip_gain','align','t','p3kind')
            original_params, current_params = parameters(old), parameters(cue)
            if original_params.get('type') in ('wood','countup3'):
                fields = (*fields,'kind')
            before = {k: original_params[k] for k in fields if k in original_params}
            after = {k: current_params[k] for k in fields if k in current_params}
            if jsonio.sha256_obj(before) != jsonio.sha256_obj(after):
                raise Refused('An approved cue changed its sound parameters; a new approval is required.')
        def current_events(current):
            selected = {}
            seen = set()
            for cue in current:
                if not in_span(cue):
                    continue
                event = event_key(cue)
                old = source_by_event.get(event)
                if old is None:
                    raise Refused('An approved span is missing or adding an event relative to its reference.')
                if event in seen:
                    raise Refused('An approved sound span has duplicate current event identities.')
                seen.add(event)
                if _source_voice(old) and _source_voice(cue):
                    continue
                if cue.get('approved') != clip:
                    raise Refused('An approved span contains an extra unapproved cue.')
                same_sound(old, cue)
                if not excluded(old):
                    selected[event] = cue
            if set(selected) != expected:
                raise Refused('An approved span is missing or adding an event relative to its reference.')
            return selected
        current_events(complete)
        by_event = current_events(cues)
        selected = list(by_event.values())
        if 'render_log' in reference:
            from slate.sound import checks
            render_log = reference['render_log']
            if not isinstance(render_log, dict) or not isinstance(render_log.get('cues'), list):
                raise Refused('The approved reference render log has no usable waveform records.')
            current_rate = sample_rate if sample_rate is not None else sheet.get('sample_rate')
            if current_rate is None or render_log.get('sample_rate') != current_rate:
                raise Refused('Approved reference waveforms require the same audio sample rate.')
            for event, cue in by_event.items():
                old = source_by_event[event]
                keys = {FamilyMixer.hit_key(old), f"{old.get('event')}@{float(old['t']):.5f}",
                        f"{old.get('event')}@{float(old['t']):.6f}"}
                records = [r for r in render_log['cues'] if isinstance(r, dict) and r.get('hit') in keys]
                if len(records) != 1 or not isinstance(records[0].get('waveform_identity'), str):
                    raise Refused('An approved cue has no unique source waveform record.')
                if render_cue is None:
                    raise Refused('Approved reference waveforms require a current dry-cue renderer.')
                wave, _ = render_cue(cue)
                if checks.hit_hash(wave) != records[0]['waveform_identity']:
                    raise Refused('An approved cue no longer renders its approved waveform.',
                                  hint='Restore its approved instrument and motion inputs, or approve the changed sound.')
        elif any(_needs_reference_wave(source_by_event[event]) for event in by_event):
            raise Refused('A legacy approved cue has unbound sample or motion inputs.',
                          hint='Restore its approved render log; unresolved instruments and named curves cannot be guessed.')
        hits = {FamilyMixer.hit_key(c) for c in selected}
        if len(hits) != len(selected):
            raise Refused('An approved sound span has duplicate current hit identities.')
        if match.get('pin_rides'):
            frozen.update(hits)
            source_keys = {FamilyMixer.hit_key(c): c for c in all_source}
            for key, value in balance_used.get('rides', {}).items():
                old = source_keys.get(key)
                if old is None:
                    # Historical sheets used event@time even when the portable cue has an id.
                    old = next((c for c in all_source if f"{c.get('event')}@{float(c['t']):.5f}" == key), None)
                if old is not None and (not in_span(old) or excluded(old)):
                    continue
                cue = by_event.get(event_key(old)) if old else None
                if cue is None:
                    raise Refused('An approved ride has no matching current cue; refusing to guess.')
                pinned[FamilyMixer.hit_key(cue)] = copy.deepcopy(value)
        else:
            full_depth.update(hits)
    return pinned, frozen, full_depth


def score_match(score, voice, matches, load_reference, *, sample_rate=48000, t0=0.0,
                ramp_in_s=1.0, ramp_out_s=.05):
    """Keep an approved score-to-voice level over each reference's core interval."""
    if matches and load_reference is None:
        raise Refused('Score span matching requires an approval-checked reference resolver.')
    out, reports = np.array(score, dtype=np.float64, copy=True), []
    voice = np.asarray(voice)
    n = len(out)
    if out.shape != voice.shape:
        raise ValueError('Score and voice lengths differ.')
    for match in matches:
        ref = load_reference(match['clip'])
        start = float(ref['cue_sheet']['window'][0])
        ca, cb = map(float, match['core'])
        a, b = round((ca-start)*sample_rate), round((cb-start)*sample_rate)
        j0,j1 = round((ca-t0)*sample_rate),round((cb-t0)*sample_rate)
        rv, rs = np.asarray(ref['voice']), np.asarray(ref['score'])
        if not (0 <= a < b <= min(len(rv),len(rs)) and 0 <= j0 < j1 <= n):
            raise Refused('An approved score core falls outside its reference or the current mix.')
        ref_score_lu = meter.integrated(rs[a:b],sample_rate)
        ref_voice_lu = meter.integrated(rv[a:b],sample_rate)
        vl = meter.integrated(voice[j0:j1],sample_rate)
        current_score_lu = meter.integrated(out[j0:j1],sample_rate)
        if min(ref_score_lu,ref_voice_lu,vl,current_score_lu) <= meter.SILENT_FLOOR:
            raise Refused('An approved score core must contain measurable voice and music.')
        target = ref_score_lu-ref_voice_lu
        before = current_score_lu-vl
        if not all(math.isfinite(v) for v in (target,vl,before)):
            raise Refused('An approved score core must contain measurable voice and music.')
        s0,s1 = round((float(match['from'])-t0)*sample_rate),round((float(match['to'])-t0)*sample_rate)
        if not 0 <= s0 < s1 <= n:
            raise Refused('An approved score span falls outside the current mix.')
        kin,kout = min(s1-s0,round(ramp_in_s*sample_rate)),min(n-s1,round(ramp_out_s*sample_rate))
        base = out.copy()
        trim = 0.0
        for _ in range(3):
            now = meter.integrated(out[j0:j1],sample_rate)-vl
            if abs(target-now) < .05:
                break
            trim += target-now
            env = np.ones(n)
            env[s0:s1] = db(trim)
            env[s0:s0+kin] = np.linspace(1.0,db(trim),kin)
            env[s1:s1+kout] = np.linspace(db(trim),1.0,kout)
            out = base*env[:,None]
        reports.append({'span_s':[match['from'],match['to']], 'core_s':[ca,cb], 'clip':match['clip'],
                        'reference_score_lu_vs_voice':round(target,2), 'before_lu':round(before,2),
                        'after_lu':round(meter.integrated(out[j0:j1],sample_rate)-vl,2), 'trim_db':round(trim,2)})
    return out,reports


def mix(cues, n, *, voice, words=(), sample_rate=48000, fps=30.0, t0=0.0, config=None,
        score=None, silences=(), trims=None, film_dir=None, delivered_voice=None,
        sheet=None, load_reference=None, renderer=None, impulse_response=None):
    """Mix families around an already prepared score; never levels or ducks the score twice."""
    if len(voice) != n:
        raise ValueError('The voice length does not match the requested mix length.')
    from slate.sound.effects.rendering import expand_cues, render_cue
    cues = expand_cues(cues)
    pinned,frozen,full_depth = resolve_approved(sheet or {},cues,load_reference,composed_score=score is not None,
        render_cue=lambda cue: render_cue(cue,film_dir,sample_rate),sample_rate=sample_rate)
    mixer = FamilyMixer(voice,sample_rate=sample_rate,fps=fps,config=config,renderer=renderer,
                        impulse_response=impulse_response,film_dir=film_dir,trims=trims,
                        delivered_voice=delivered_voice)
    return mixer.run(cues,words=words,t0=t0,silences=silences,score=score,
                     pinned=pinned,frozen=frozen,full_depth=full_depth)
