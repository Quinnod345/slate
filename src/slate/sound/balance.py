"""The balance: every word of the voice understood, and every family of sounds where the brand's palette puts it.

This is the films' balance check (their p3 mix, 2026-09-30) generalized. The owner's note behind it: the whooshes,
scratches, synths and risers were "way too loud and overpower everything… trying to understand what the guy's saying
versus having just too much audio in the way", while what he loved about proof 2 was that "the sound stuck out but
wasn't too loud". So the design stays; each family of sounds gets its place against the voice, and every mix is
measured against that place. Nothing here is judged by ear, and nothing here changes a sound.

The families (brand sound-palette data: brand.json `sound.palette.balance`, film.json `sound.palette.balance` over it):

    accents   small, dry tactile sounds and small notes (tap, land, snap, count, data, stamp, character): they stick
              out by design, short and spectrally unlike speech
    textures  noise textures (type, draw, page, whoosh): felt more than heard; their air is the consonant band
    swells    pads and risers (swell): sustained, so they mask for their whole length
    impacts   the big landings (impact): they keep their weight, never louder than the voice
    score     the composed score (the music stem)
    plate     ambience (the ambience stem)

"While the voice speaks" means inside a word's span (Whisper's word times) where the voice is actually sounding: its
50 ms K-weighted level within 30 dB of its loud level. A word shorter than 120 ms is measured over 120 ms centred on
it; a word whose span misses the voice is looked for 150 ms either side. A film with no word timings yet is measured
phrase by phrase: the voice's own spoken stretches, split at pauses of 300 ms or more.

The rules (each a gate for `slate verify`):

    sound.balance.clarity     every word: the voice at least 12 dB over everything else in 1-5 kHz, the band that
                              carries consonants (the Speech Intelligibility Index credits a band linearly from -15 to
                              +15 dB, so 12 keeps 90% of each word's most important band). Each family on its own is
                              reported too, and a family with its own floor keeps it (the score: 19 dB on every word).
                              The owner's "in between": an accent on a word may be ridden only half the guard's depth,
                              so a word under a half-ridden accent may sit under the rule. That word is the trade he
                              chose: listed, not failed; under 8 dB it waits for a person; under 6 dB it fails.
    sound.balance.never_over  while the voice speaks (every 10 ms inside every word), no family, not all the effects
                              together and not everything else together is louder than the voice: EBU momentary
                              loudness (400 ms, K-weighted, windows centred on the moment).
    sound.balance.texture_air every word: the textures' 2-6 kHz at least 20 dB under the voice's own 2-6 kHz (1% of the
                              voice's energy in the consonant band: it costs the band nothing).
    sound.balance.accents     every accent and impact clears the score by at least 3 dB in one of its own loudest
                              third-octave bands over its loudest 120 ms.
    sound.balance.levels      every family inside its range against the voice: integrated, hit by hit (loudest
                              momentary), and inside words.
    sound.balance.approved_spans
                              a span the owner approved as a clip (palette balance approved_spans: its window and the
                              approved clip) is kept as approved: the film's mix cut to the window and the clip are
                              measured the same way and compared family by family, ride by ride and by the score's
                              level over the span's core; past the meters' tolerance (1 LU, 1 dB) it fails.
    sound.planned_stops       the music's quiet stretches are all planned: each planned stop widened by the music's own
                              fade into and out of it, the composed score's own rests counted as planned beats; the film
                              ends in a silent last second always (the films' rule; a brand can set end_silent "plan")
                              and opens out of silence only when its plan says so.

Where the levels are measured. When the mix writes family stems (sound/mix/family_stems/<family>.wav, summing to
the mix), every family is measured on its own. Otherwise the delivery stems give what they can: the voice
(dialogue), the score (the music stem less the tonal effects the mix adds to it), the plate (ambience), and the
effects as one stream. The effect families' own rules are then UNCHECKED, never passed, until the effects are split.
A hit (from sound/sound_cues.json) is measured on its family's stem over its own span, so two overlapping hits of
one family read as one: an upper bound.

What the mix rode (sound/mix/rides.json, written by whatever rides hits; Slate's mix rides nothing yet):

    {"rides": [{"hit": "<id>", "t": 12.3, "end": 12.6, "family": "accents", "depth_db": -7.5,
                "full_depth_db": -15.0, "half_depth": true}],
     "pinned": [{"hit": "<id>", "t": 40.1, "end": 40.4, "why": "an approved clip's exact rides"}]}

    from slate.sound import balance
    balance.GATES                         # for slate verify (each takes verify's context or a film folder)
    balance.measure(streams, sr, words=…) # the measurements on any arrays or files
    balance.half_depth_words(now, before, rides)
    balance.widen_stops(music, sr, stops) / score_rests(score, sr) / opens_in_silence(score, sr)
"""

from __future__ import annotations

import argparse
import contextvars
import copy
import functools
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from scipy import signal

from slate import gates, jsonio, log
from slate.errors import MissingPrerequisite, UsageError
from slate.gates import GateResult
from slate.sound import audio, measure as meter

# ---------------------------------------------------------------------------------------------------------------
# The starting balance (brand sound-palette data; a brand sets its own in brand.json sound.palette.balance)

STARTING_BALANCE: dict[str, Any] = {
    "source": "the films' balanced mix (p3, approved 2026-09-30), the starting point; every brand can set its own",
    "families": {
        "accents": {"kinds": ["tap", "land", "snap", "count", "data", "stamp", "character"],
                    "what": "small, dry tactile sounds and small notes: they stick out by design"},
        "textures": {"kinds": ["type", "draw", "page", "whoosh"], "what": "noise textures: felt more than heard"},
        "swells": {"kinds": ["swell"], "what": "pads and risers"},
        "impacts": {"kinds": ["impact"], "what": "the big landings: they keep their weight"},
        "score": {"stem": "music", "what": "the composed score"},
        "plate": {"stem": "ambience", "what": "ambience"},
    },
    "effects": ["accents", "textures", "swells", "impacts"],
    "speaking": {"within_db": 30.0, "loud_percentile": 95.0, "min_word_s": 0.12, "search_s": 0.15,
                 "phrase_gap_s": 0.3},
    "clarity": {"band_hz": [1000.0, 5000.0], "min_db": 12.0, "trade_flag_db": 8.0, "trade_floor_db": 6.0,
                "alone_min_db": {"score": 19.0}},
    # The score's own clarity guard in `slate mix` (slate.sound.guard): its top dips under any word it would crowd
    # to the score-alone floor above, by just enough. `score_guard: false` in a brand's balance turns it off.
    "score_guard": {"band_hz": [1000.0, 5000.0], "target_db": 19.0, "split_hz": 600.0, "max_dip_db": -20.0,
                    "hold_s": 0.15, "ramp_s": 0.3},
    "never_over": {"min_margin_db": 0.0},
    "texture_air": {"band_hz": [2000.0, 6000.0], "min_db_under_voice": 20.0, "family": "textures"},
    "accents_over_score": {"min_db": 3.0, "families": ["accents", "impacts"], "score": "score", "window_s": 0.12},
    "levels": {
        "accents": {"hit_median_lu": [-24.0, -17.0], "hit_max_lu": -8.0},
        "textures": {"hit_max_lu": -18.0, "inside_words_max_lu": -30.0},
        "swells": {"hit_max_lu": -22.0, "inside_words_max_lu": -28.0},
        "impacts": {"hit_max_lu": -2.0},
        "score": {"integrated_lu": [-28.0, -17.0], "inside_words_max_lu": -19.0},
    },
    "stops": {"under_median_db": 20.0, "fade_cap_s": 2.0, "step_s": 0.1, "score_rests": True,
              "gap_threshold_dbfs": -60.0, "gap_window_s": 0.5,
              # the films' rule (p3): a silent last second always; nothing before 0.5 s only when the plan opens out of
              # silence ("plan" keeps the ending to the plan, for a brand that wants it)
              "opening_s": 0.5, "last_s": 1.0, "silent_dbfs": -90.0, "open_silent": "plan", "end_silent": "always"},
    "approved_spans": [],
    "hit_default_s": 0.5,
}

HOP_S = 0.01  # the meters' hop: 10 ms
BLOCK_S = 10.0  # samples are streamed in blocks this long
MARGIN_S = 0.25  # zero-phase band filters see this much either side of a block
NON_FAMILY = ("voice", "effects", "everything_else")
MIX_DIR = "sound/mix"
REPORT = "sound/mix/balance_check.json"
BEFORE = "sound/mix/balance_check.before.json"
RIDES = "sound/mix/rides.json"
CUES = "sound/sound_cues.json"
VERSION_TAG = "balance.1"


def deep_merge(base: dict, extra: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "families":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def palette_balance(palette: dict | None) -> dict:
    """The balance for a sound palette: STARTING_BALANCE with the palette's own `balance` over it, key by key. A
    palette that names its families replaces the starting families whole (a brand's families are its own)."""
    return deep_merge(STARTING_BALANCE, (palette or {}).get("balance") or {})


def family_of_kind(kind: str | None, bal: dict) -> str | None:
    for name, fam in (bal.get("families") or {}).items():
        if kind is not None and kind in (fam.get("kinds") or []):
            return name
    return None


# ---------------------------------------------------------------------------------------------------------------
# Streams and the meter

Part = tuple[Any, float]  # (a WAV path or an array (n, ch), coefficient)


def _as_stereo(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    return audio.to_stereo(x)


def _length(src: Any) -> int:
    if isinstance(src, (str, os.PathLike)):
        return audio.wav_info(src).frames
    return int(np.asarray(src).shape[0])


def _rate(src: Any) -> int | None:
    if isinstance(src, (str, os.PathLike)):
        return audio.wav_info(src).sample_rate
    return None


class _Reader:
    """Reads ranges of the streams' sources, each once per range."""

    def __init__(self):
        self.cache: dict[int, tuple[int, int, np.ndarray]] = {}

    def read(self, src: Any, a: int, b: int) -> np.ndarray:
        key = id(src)
        hit = self.cache.get(key)
        if hit is not None and hit[0] == a and hit[1] == b:
            return hit[2]
        if isinstance(src, (str, os.PathLike)):
            x, _ = audio.read_wav(src, a, b - a)
        else:
            x = np.asarray(src)[a:b]
        x = _as_stereo(x)
        if len(x) < b - a:
            x = np.concatenate([x, np.zeros((b - a - len(x), x.shape[1]))])
        self.cache[key] = (a, b, x)
        return x

    def stream(self, parts: list[Part], a: int, b: int) -> np.ndarray:
        out = np.zeros((b - a, 2))
        for src, coef in parts:
            out += coef * self.read(src, a, b)
        return out


class Curves:
    """Every stream's meters at 10 ms hops: K-weighted power (summed over channels), band power for the bands asked
    for, whether it has any sound at all, and its integrated loudness (BS.1770-4, gated)."""

    def __init__(self, sr: int, n: int, hop: int):
        self.sr, self.n, self.hop = sr, n, hop
        self.m = n // hop
        self.ksum: dict[str, np.ndarray] = {}
        self.band: dict[tuple[str, float, float], np.ndarray] = {}
        self.live: dict[str, bool] = {}
        self.peak: dict[str, float] = {}

    def kpower(self, name: str) -> np.ndarray:
        return self.ksum[name] / self.hop

    def window(self, name: str, win_s: float) -> np.ndarray:
        """Mean K-weighted power in windows of win_s centred on every hop."""
        w = max(1, int(round(win_s / HOP_S)))
        return np.convolve(self.kpower(name), np.ones(w) / w, mode="same")

    def integrated(self, name: str) -> float:
        """Integrated loudness the BS.1770-4 way (400 ms blocks every 100 ms, gated at -70 LUFS and 10 LU under)."""
        steps_per = int(round(meter.STEP_S / HOP_S))
        k = self.ksum[name]
        s = k[: len(k) // steps_per * steps_per].reshape(-1, steps_per).sum(axis=1)
        nb = int(round(meter.BLOCK_S / meter.STEP_S))
        if len(s) < nb:
            return meter.SILENT_FLOOR
        csum = np.concatenate([[0.0], np.cumsum(s)])
        z = (csum[nb:] - csum[:-nb]) / (nb * steps_per * self.hop)
        with np.errstate(divide="ignore"):
            loud = -0.691 + 10 * np.log10(np.maximum(z, 0.0))
        value, _ = meter._gated_mean_power(z, loud, meter.REL_GATE_LU)
        return value if math.isfinite(value) and value > meter.SILENT_FLOOR else meter.SILENT_FLOOR


def run_meters(streams: dict[str, list[Part]], sr: int, *, bands: dict[str, list[tuple[float, float]]] | None = None,
               n: int | None = None, block_s: float = BLOCK_S) -> Curves:
    """Meter every stream in one streamed pass (a feature-length mix never loads whole). A stream is a list of
    (source, coefficient): a WAV path or an array, so the score can be the music stem less the tonal effects."""
    bands = bands or {}
    srcs = [s for parts in streams.values() for s, _ in parts]
    for s in srcs:
        r = _rate(s)
        if r is not None and r != sr:
            raise UsageError(f"{s} is {r} Hz; the film is {sr} Hz.")
    n = n if n is not None else max((_length(s) for s in srcs), default=0)
    hop = int(round(HOP_S * sr))
    out = Curves(sr, n, hop)
    m = out.m
    ksos = meter.k_weighting_sos(sr)
    bsos = {(lo, hi): signal.butter(4, [lo, hi], "bandpass", fs=sr, output="sos")
            for lst in bands.values() for lo, hi in lst}
    zi = {name: np.zeros((ksos.shape[0], 2, 2)) for name in streams}
    for name in streams:
        out.ksum[name] = np.zeros(m)
        out.live[name] = False
        out.peak[name] = 0.0
        for lo, hi in bands.get(name, []):
            out.band[(name, lo, hi)] = np.zeros(m)
    block = max(1, int(round(block_s / HOP_S))) * hop
    margin = int(round(MARGIN_S * sr))
    reader = _Reader()
    for a in range(0, m * hop, block):
        b = min(a + block, m * hop)
        a0, b0 = max(0, a - margin), min(n, b + margin)
        h0, h1 = a // hop, b // hop
        reader.cache.clear()
        for name, parts in streams.items():
            wide = reader.stream(parts, a0, b0)
            core = wide[a - a0:a - a0 + (b - a)]
            if core.size:
                pk = float(np.max(np.abs(core)))
                if pk > 0:
                    out.live[name] = True
                    out.peak[name] = max(out.peak[name], pk)
            z, zi[name] = signal.sosfilt(ksos, core, axis=0, zi=zi[name])
            out.ksum[name][h0:h1] = (z ** 2).sum(axis=1).reshape(h1 - h0, hop).sum(axis=1)
            for lo, hi in bands.get(name, []):
                if not np.any(wide):
                    continue  # silence stays at zero
                y = signal.sosfiltfilt(bsos[(lo, hi)], wide, axis=0) if len(wide) > 64 else wide
                p = (y[a - a0:a - a0 + (b - a)] ** 2).sum(axis=1)
                out.band[(name, lo, hi)][h0:h1] = p.reshape(h1 - h0, hop).mean(axis=1)
    return out


def lufs(pw: np.ndarray) -> np.ndarray:
    return -0.691 + 10 * np.log10(np.maximum(pw, 1e-20))


# ---------------------------------------------------------------------------------------------------------------
# While the voice speaks: words, or phrases

SPOKEN_SYMBOLS = set("%&$€£+@#")  # read aloud ("25%" is "twenty-five percent"); a dash on its own is a pause


def is_spoken(token: str) -> bool:
    return any(ch.isalnum() for ch in token) or any(ch in SPOKEN_SYMBOLS for ch in token)


def speaking(curves: Curves, *, within_db: float = 30.0, percentile: float = 95.0, voice: str = "voice") -> np.ndarray:
    """Per hop: the voice is sounding (its 50 ms K-weighted level within within_db of its loud level)."""
    L50 = lufs(curves.window(voice, 0.05))
    live = L50 > -70
    loud = float(np.percentile(L50[live], percentile)) if live.any() else 0.0
    return L50 >= loud - within_db


def unit_frames(spk: np.ndarray, units: Iterable[tuple[str, float, float]], sr: int, hop: int, *, t0: float = 0.0,
                n: int | None = None, min_s: float = 0.12, search_s: float = 0.15) -> tuple[list[tuple], list[np.ndarray]]:
    """Each word's (or phrase's) hops where the voice is sounding: [(w, s, e, a, b)] and their frames."""
    n = n if n is not None else len(spk) * hop
    rows, frames = [], []
    look = int(round(search_s / HOP_S))
    for w, s, e in units:
        if not is_spoken(str(w)):
            continue
        a, b = int(round((s - t0) * sr)), int(round((e - t0) * sr))
        if b - a < int(min_s * sr):
            c = (a + b) // 2
            a, b = c - int(min_s / 2 * sr), c + int(min_s / 2 * sr)
        if a < 0 or b > n:
            continue
        fr = np.arange(a // hop, min(b // hop, len(spk)))
        fr = fr[spk[fr]]
        if len(fr) < 3:
            fr = np.arange(max(0, a // hop - look), min(b // hop + look, len(spk)))
            fr = fr[spk[fr]]
        rows.append((str(w), round(float(s), 3), round(float(e), 3), a, b))
        frames.append(fr)
    return rows, frames


def phrases(spk: np.ndarray, *, gap_s: float = 0.3, min_s: float = 0.12, t0: float = 0.0) -> list[tuple[str, float, float]]:
    """The voice's spoken stretches, split at pauses of gap_s or more: [(label, start, end)]."""
    out: list[tuple[str, float, float]] = []
    gap = max(1, int(round(gap_s / HOP_S)))
    on = np.flatnonzero(spk)
    if not len(on):
        return out
    start = prev = int(on[0])
    for i in on[1:]:
        i = int(i)
        if i - prev > gap:
            out.append((start, prev + 1))
            start = i
        prev = i
    out.append((start, prev + 1))
    return [(f"phrase {k + 1}", round(t0 + a * HOP_S, 3), round(t0 + b * HOP_S, 3))
            for k, (a, b) in enumerate(p for p in out if (p[1] - p[0]) * HOP_S >= min_s)]


# ---------------------------------------------------------------------------------------------------------------
# The measurements

THIRD_EDGES = 1000 * 2 ** (np.arange(-15, 14) / 3)


def _bands_of(x: np.ndarray, sr: int) -> np.ndarray:
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return np.array([X[(f >= lo) & (f < hi)].sum() + 1e-20 for lo, hi in zip(THIRD_EDGES[:-1], THIRD_EDGES[1:])])


def _db(p: float, q: float) -> float:
    return round(10 * math.log10((p + 1e-20) / (q + 1e-20)), 1)


def _span(hits_spans: list[tuple[float, float]], s: float, e: float) -> bool:
    return any(a < e and b > s for a, b in hits_spans)


def measure(streams: dict[str, list[Part]], sr: int, *, words: list[tuple[str, float, float]] | None = None,
            hits: list[dict] | None = None, bal: dict | None = None, rides: dict | None = None, t0: float = 0.0,
            n: int | None = None, split: bool = True) -> dict:
    """The balance of a mix: streams must include "voice"; families are the other stream names (except "effects" and
    "everything_else", which are given or built from the families). words: [(word, start, end)] at stream time
    (without them the voice's phrases are used). hits: [{id, kind, family, t, end, beat?}]. rides: the rides record.
    split: False when the effects are one stream (the effect families can't be measured apart)."""
    bal = bal or palette_balance(None)
    hits = hits or []
    streams = dict(streams)
    fams = [k for k in streams if k not in NON_FAMILY]
    effect_names = [f for f in bal.get("effects") or [] if f in streams]
    if "effects" not in streams:
        streams["effects"] = [p for f in effect_names for p in streams[f]]
    if "everything_else" not in streams:
        streams["everything_else"] = [p for f in fams for p in streams[f]]
    clar_band = tuple(float(v) for v in bal["clarity"]["band_hz"])
    air = bal.get("texture_air") or {}
    air_band = tuple(float(v) for v in air.get("band_hz", (2000.0, 6000.0)))
    tex = air.get("family", "textures")
    bands = {"voice": [clar_band, air_band], "everything_else": [clar_band]}
    for f in fams:
        bands[f] = [clar_band] + ([air_band] if f == tex else [])
    curves = run_meters(streams, sr, bands=bands, n=n)
    sp = bal.get("speaking") or {}
    spk = speaking(curves, within_db=float(sp.get("within_db", 30.0)), percentile=float(sp.get("loud_percentile", 95.0)))
    unit = "word" if words else "phrase"
    units = words if words else phrases(spk, gap_s=float(sp.get("phrase_gap_s", 0.3)), t0=t0)
    rows, wf = unit_frames(spk, units, sr, curves.hop, t0=t0, n=curves.n, min_s=float(sp.get("min_word_s", 0.12)),
                           search_s=float(sp.get("search_s", 0.15)))
    sel = np.zeros(curves.m, bool)
    for fr in wf:
        sel[fr] = True
    has_voice = curves.live.get("voice", False)
    vI = curves.integrated("voice") if has_voice else None
    res: dict[str, Any] = {"unit": unit, "units": len(rows), "families": fams, "split": split,
                           "voice_lufs": None if vI is None else round(vI, 2), "live": dict(curves.live),
                           "units_not_found_in_the_voice": [r[0] for r, fr in zip(rows, wf) if not len(fr)]}

    # levels: integrated, against the voice
    integ = {}
    for f in [*fams, "effects", "everything_else"]:
        v = curves.integrated(f) if curves.live.get(f) else None
        integ[f] = None if v is None or vI is None or v <= meter.SILENT_FLOOR else round(v - vI, 1)
    Lm = {k: lufs(curves.window(k, 0.4)) for k in ["voice", *fams, "effects", "everything_else"]}
    Ls = {k: lufs(curves.window(k, 0.1)) for k in ["voice", *fams, "effects"]}

    # per hit: on its family's stream over its own span
    hit_rows = []
    for h in hits:
        f = h.get("family")
        if f not in fams or vI is None:
            continue
        a = max(0, int(math.floor((float(h["t"]) - t0) / HOP_S)))
        b = min(curves.m, int(math.ceil((float(h["end"]) - t0) / HOP_S)))
        if b <= a:
            continue
        pm, ps = Lm[f][a:b], Ls[f][a:b]
        live = pm > -70
        if not live.any():
            continue
        mask = sel[a:b] & live
        margin = round(float(np.min(Lm["voice"][a:b][mask] - pm[mask])), 1) if mask.any() else None
        under = [r[0] for r in rows if r[1] < float(h["end"]) and r[2] > float(h["t"])][:6]
        hit_rows.append({"id": h.get("id"), "kind": h.get("kind"), "family": f, "t": round(float(h["t"]), 3),
                         "end": round(float(h["end"]), 3), "beat": h.get("beat"),
                         "momentary_lu": round(float(pm.max()) - vI, 1), "short_lu": round(float(ps.max()) - vI, 1),
                         "margin_to_voice_db": margin, "under_words": under})
    per_family = {}
    for f in fams:
        hs = [h for h in hit_rows if h["family"] == f]
        mom = [h["momentary_lu"] for h in hs]
        per_family[f] = {"integrated_lu": integ.get(f), "hits": len(hs),
                         "hit_momentary_lu": ({"median": round(float(np.median(mom)), 1), "max": round(float(np.max(mom)), 1),
                                               "min": round(float(np.min(mom)), 1)} if mom else None),
                         "loudest_hits": sorted(({"id": h["id"], "kind": h["kind"], "momentary_lu": h["momentary_lu"],
                                                  "t": h["t"]} for h in hs), key=lambda r: -r["momentary_lu"])[:4]}

    # under speech: how far each family sits under the voice inside words and between them
    duck = {}
    floor_pw = 10 ** ((-70 + 0.691) / 10)
    for f in [*fams, "effects"]:
        x = curves.window(f, 0.4)
        live = x > floor_pw
        if vI is None or not live.any():
            continue
        duck[f] = {"inside_words_lu": round(float(lufs(np.mean(x[sel & live]))) - vI, 1) if (sel & live).any() else None,
                   "between_words_lu": round(float(lufs(np.mean(x[~spk & live]))) - vI, 1) if (~spk & live).any() else None}
    res["under_speech"] = duck

    # clarity: every unit, the voice over everything else in the clarity band, and over each family on its own
    bv = curves.band[("voice", *clar_band)]
    br = curves.band[("everything_else", *clar_band)]
    bfam = {f: curves.band[(f, *clar_band)] for f in fams if curves.live.get(f)}
    clar = []
    for (w, s, e, _a, _b), fr in zip(rows, wf):
        if not len(fr):
            continue
        pv = float(bv[fr].mean())
        clar.append({"word": w, "s": s, "e": e, "voice_over_rest_db": _db(pv, float(br[fr].mean())),
                     "by_family_db": {f: _db(pv, float(x[fr].mean())) for f, x in bfam.items()}})
    cl = bal["clarity"]
    rule, flag_db, floor_db = float(cl["min_db"]), float(cl.get("trade_flag_db", 8.0)), float(cl.get("trade_floor_db", 6.0))
    rides = rides or {}
    half = [(float(r["t"]), float(r["end"]), str(r.get("hit"))) for r in rides.get("rides") or []
            if r.get("half_depth") and r.get("t") is not None and r.get("end") is not None]
    pinned = [(float(r["t"]), float(r["end"])) for r in rides.get("pinned") or []
              if r.get("t") is not None and r.get("end") is not None]
    pinned += [(float(s["from"]), float(s["to"])) for s in bal.get("approved_spans") or []]
    half_spans = [(a, b) for a, b, _ in half]
    for r in clar:
        r["under_half_depth"] = [hk for a, b, hk in half if a < r["e"] and b > r["s"]]
    under = [r for r in clar if r["voice_over_rest_db"] < rule]
    traded = [r for r in under if _span(half_spans, r["s"], r["e"])]
    approved = [r for r in under if r not in traded and _span(pinned, r["s"], r["e"])]
    failing = [r for r in under if r not in traded and r not in approved]
    alone_rules = {k: float(v) for k, v in (cl.get("alone_min_db") or {}).items()}
    alone_fail = []
    fam_min: dict[str, tuple] = {}
    for r in clar:
        for f, v in r["by_family_db"].items():
            if v < 100 and (f not in fam_min or v < fam_min[f][0]):
                fam_min[f] = (v, r["word"], r["s"])
            if f in alone_rules and v < alone_rules[f]:
                alone_fail.append({"family": f, "word": r["word"], "s": r["s"], "db": v, "rule_db": alone_rules[f]})
    worst = sorted(clar, key=lambda r: r["voice_over_rest_db"])
    res["clarity"] = {"rule_db": rule, "band_hz": list(clar_band), "flag_db": flag_db, "floor_db": floor_db,
                      "units": len(clar), "min_db": worst[0]["voice_over_rest_db"] if worst else None,
                      "median_db": round(float(np.median([r["voice_over_rest_db"] for r in clar])), 1) if clar else None,
                      "worst": worst[:8], "failing": failing, "traded_under_half_depth": traded,
                      "traded_under_floor": [r for r in traded if r["voice_over_rest_db"] < floor_db],
                      "traded_flagged": [r for r in traded if floor_db <= r["voice_over_rest_db"] < flag_db],
                      "approved_span_under_rule": approved, "alone_under_floor": alone_fail,
                      "each_family_alone_worst": {f: {"db": v[0], "word": v[1], "s": v[2]} for f, v in fam_min.items()},
                      "half_ridden_hits": len(half), "per_unit": [[r["word"], r["s"], r["e"], r["voice_over_rest_db"]]
                                                                  for r in clar]}

    # never over: every 10 ms inside every unit, each family, all the effects and everything else under the voice
    over = {}
    for f in [*fams, "effects", "everything_else"]:
        live = sel & (Lm[f] > -70)
        if not live.any():
            over[f] = None
            continue
        mrg = Lm["voice"] - Lm[f]
        i = int(np.flatnonzero(live)[np.argmin(mrg[live])])
        word = next((r[0] for r, fr in zip(rows, wf) if i in set(fr.tolist())), None)
        short = None
        if f in Ls and (sel & (Ls[f] > -70)).any():
            short = round(float(np.min((Ls["voice"] - Ls[f])[sel & (Ls[f] > -70)])), 1)
        over[f] = {"min_margin_db": round(float(mrg[i]), 1), "at": round(t0 + (i + 0.5) * HOP_S, 3), "word": word,
                   "short_min_margin_db": short}
    lim = float((bal.get("never_over") or {}).get("min_margin_db", 0.0))
    res["never_over"] = {"rule_db": lim, "by_family": over,
                         "failing": {f: v for f, v in over.items() if v and v["min_margin_db"] < lim}}

    # texture air: the textures' consonant band under the voice's, every unit
    rows_air = []
    if tex in fams and curves.live.get(tex):
        tv, tt = curves.band[("voice", *air_band)], curves.band[(tex, *air_band)]
        for (w, s, e, _a, _b), fr in zip(rows, wf):
            if not len(fr):
                continue
            pt = float(tt[fr].mean())
            if pt <= 1e-14:
                continue
            rows_air.append({"word": w, "s": s, "db_under_voice": _db(float(tv[fr].mean()), pt),
                             "texture_dbfs": round(10 * math.log10(pt / 2 + 1e-20), 1)})
    rows_air.sort(key=lambda r: r["db_under_voice"])
    L2 = float(air.get("min_db_under_voice", 20.0))
    res["texture_air"] = {"rule_db": L2, "band_hz": list(air_band), "family": tex, "units_with_texture": len(rows_air),
                          "min_db_under_voice": rows_air[0]["db_under_voice"] if rows_air else None,
                          "worst": rows_air[:8], "failing": [r for r in rows_air if r["db_under_voice"] < L2]}

    # accents over the score: each accent and impact, its loudest 120 ms, in its own loudest third-octave bands
    aos = bal.get("accents_over_score") or {}
    score_name = aos.get("score", "score")
    win = int(round(float(aos.get("window_s", 0.12)) * sr))
    rowsA = []
    if score_name in fams and curves.live.get(score_name):
        reader = _Reader()
        for h in hit_rows:
            if h["family"] not in (aos.get("families") or ["accents", "impacts"]):
                continue
            a = max(0, int(round((h["t"] - t0) * sr)))
            b = min(curves.n, int(round((h["end"] - t0) * sr)))
            if b - a < win:
                b = min(curves.n, a + win)
            y = reader.stream(streams[h["family"]], a, b).mean(axis=1)
            k50 = max(1, int(round(0.05 * sr)))
            e = np.sqrt(np.convolve(y ** 2, np.ones(k50) / k50, mode="same"))
            k = int(np.argmax(e))
            a0 = max(0, k - int(round(0.06 * sr)))
            g0 = a + a0
            if g0 + win > curves.n:
                continue
            seg = reader.stream(streams[h["family"]], g0, g0 + win).mean(axis=1)
            sc = reader.stream(streams[score_name], g0, g0 + win).mean(axis=1)
            Eb, Mb = _bands_of(seg, sr), _bands_of(sc, sr)
            dom = Eb >= Eb.max() * 0.1
            rowsA.append((round(float(np.max(10 * np.log10(Eb[dom] / Mb[dom]))), 1), h["id"], h["kind"], h["t"]))
    rowsA.sort(key=lambda r: r[0])
    lim3 = float(aos.get("min_db", 3.0))
    res["accents_over_score"] = {"rule_db": lim3, "score": score_name,
                                 "families": list(aos.get("families") or ["accents", "impacts"]),
                                 "hits": len(rowsA), "min_db": rowsA[0][0] if rowsA else None,
                                 "weakest": [list(r) for r in rowsA[:6]],
                                 "median_db": float(np.median([r[0] for r in rowsA])) if rowsA else None,
                                 "failing": [list(r) for r in rowsA if r[0] < lim3]}

    # levels: every family inside its range
    lv_bad = []
    for f, t in (bal.get("levels") or {}).items():
        pf = per_family.get(f) or {}
        il, hm = pf.get("integrated_lu"), (pf.get("hit_momentary_lu") or {})
        if "integrated_lu" in t and il is not None and not (t["integrated_lu"][0] <= il <= t["integrated_lu"][1]):
            lv_bad.append(f"{f}: integrated {il} LU, outside {t['integrated_lu'][0]:g} to {t['integrated_lu'][1]:g}")
        if "hit_max_lu" in t and hm and hm["max"] > t["hit_max_lu"]:
            lv_bad.append(f"{f}: loudest hit {hm['max']} LU, over {t['hit_max_lu']:g}")
        if "hit_median_lu" in t and hm and not (t["hit_median_lu"][0] <= hm["median"] <= t["hit_median_lu"][1]):
            lv_bad.append(f"{f}: median hit {hm['median']} LU, outside {t['hit_median_lu'][0]:g} to {t['hit_median_lu'][1]:g}")
        iw = (duck.get(f) or {}).get("inside_words_lu")
        if "inside_words_max_lu" in t and iw is not None and iw > t["inside_words_max_lu"]:
            lv_bad.append(f"{f}: {iw} LU inside words, over {t['inside_words_max_lu']:g}")
    res["levels"] = {"voice_lufs": res["voice_lufs"], "integrated_lu_vs_voice": integ, "per_family": per_family,
                     "failing": lv_bad}
    res["hits"] = hit_rows
    return res


# ---------------------------------------------------------------------------------------------------------------
# Quinn's "in between", word by word


def half_depth_words(now: dict, before: dict | None, rides: dict, *, rule_db: float | None = None,
                     flag_db: float | None = None) -> dict:
    """Every word under a half-ridden accent: its clarity now and before (the same film's mix at full depth), the
    change, and the hits over it with their full and half depths. now/before are measure() results (or their saved
    reports); rides is the rides record of the mix that's "now"."""
    c = now["clarity"]
    rule = float(rule_db if rule_db is not None else c["rule_db"])
    flag = float(flag_db if flag_db is not None else c.get("flag_db", 8.0))
    half = [r for r in rides.get("rides") or [] if r.get("half_depth") and r.get("t") is not None]
    b = {(w, round(s, 2)): db for w, s, _e, db in ((before or {}).get("clarity") or {}).get("per_unit") or []}
    rows = []
    for w, s, e, db in c.get("per_unit") or []:
        over = [r for r in half if float(r["t"]) < e and float(r["end"]) > s]
        if not over:
            continue
        was = b.get((w, round(s, 2)))
        rows.append({"word": w, "s": s, "before_db": was, "now_db": db,
                     "change_db": None if was is None else round(db - was, 1),
                     "under": [{"hit": r.get("hit"), "full_db": r.get("full_depth_db"), "now_db": r.get("depth_db")}
                               for r in over]})
    rows.sort(key=lambda r: r["now_db"])
    flagged = [r for r in rows if r["now_db"] < flag]
    return {"rule_db": rule, "flag_db": flag, "half_ridden_hits": len(half), "words_under_them": len(rows),
            "under_the_rule": len([r for r in rows if r["now_db"] < rule]), "flagged": flagged, "words": rows,
            "before": bool(before)}


def half_depth_markdown(res: dict, *, now: str = "this mix", before: str = "the mix before") -> str:
    lines = [f"# Half-depth rides, word by word: {now} against {before}", "",
             f"Accents on words are ridden half as deep in dB (the owner's \"in between\"): {res['half_ridden_hits']} "
             f"of them. {res['words_under_them']} words sit under them; {res['under_the_rule']} of those are under the "
             f"{res['rule_db']:g} dB clarity rule (the trade: listed, not failed), and {len(res['flagged'])} under the "
             f"{res['flag_db']:g} dB flag.", "", "| Word | At (s) | Before (dB) | Now (dB) | Change | Under |",
             "|---|---|---|---|---|---|"]
    for r in res["words"]:
        mark = " **(flag)**" if r["now_db"] < res["flag_db"] else ""
        under = ", ".join(f"`{u['hit']}` {u['full_db']} → {u['now_db']} dB" for u in r["under"])
        change = f"{r['change_db']:+.1f}" if r["change_db"] is not None else "–"
        before_db = r["before_db"] if r["before_db"] is not None else "–"
        lines.append(f"| {r['word']}{mark} | {r['s']} | {before_db} | **{r['now_db']}** | {change} | {under} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# Planned stops


def levels_100ms(src: Any, sr: int | None = None, step_s: float = 0.1) -> tuple[np.ndarray, float]:
    """(level per step in dB of mean channel-summed power, the median of the non-silent steps)."""
    if isinstance(src, (str, os.PathLike)):
        info = audio.wav_info(src)
        sr, step = info.sample_rate, int(round(step_s * info.sample_rate))
        sums: list[float] = []
        carry = np.zeros((0, info.channels))
        for blk in audio.iter_wav(src, step * 200):
            blk = np.concatenate([carry, blk])
            k = len(blk) // step
            if k:
                sums.extend(((blk[:k * step] ** 2).sum(axis=1)).reshape(k, step).mean(axis=1).tolist())
            carry = blk[k * step:]
        lv = 10 * np.log10(np.array(sums) + 1e-24) if sums else np.zeros(0)
    else:
        x = _as_stereo(src)
        step = int(round(step_s * int(sr)))
        k = len(x) // step
        lv = 10 * np.log10((x[:k * step] ** 2).sum(axis=1).reshape(k, step).mean(axis=1) + 1e-24) if k else np.zeros(0)
    med = float(np.median(lv[lv > -200])) if (lv > -200).any() else -240.0
    return lv, med


def widen_stops(music: Any, sr: int | None, stops: list[dict], *, under_median_db: float = 20.0, cap_s: float = 2.0,
                step_s: float = 0.1, t0: float = 0.0, levels: tuple[np.ndarray, float] | None = None
                ) -> tuple[list[dict], float]:
    """Each planned stop widened by the music's own fade into and out of it: the stretch running into the stop (and
    out of it) where the music sits more than under_median_db under its median level, at most cap_s either side. The
    music fading into a planned silence is part of that silence; a hole anywhere else is still a hole."""
    lv, med = levels if levels is not None else levels_100ms(music, sr, step_s)
    quiet = lv < med - under_median_db
    k = len(lv)
    out = []
    for st in stops:
        a, b = float(st["from"]), float(st["to"])
        i = int((a - t0) / step_s) - 1
        lo = a
        while i >= 0 and quiet[i] and a - (i * step_s + t0) <= cap_s:
            lo = i * step_s + t0
            i -= 1
        j = int(math.ceil((b - t0) / step_s))
        hi = b
        while j < k and quiet[j] and (j + 1) * step_s + t0 - b <= cap_s:
            hi = (j + 1) * step_s + t0
            j += 1
        out.append({**st, "from": round(lo, 2), "to": round(hi, 2), "planned": [a, b],
                    "fade_tails_s": [round(a - lo, 2), round(hi - b, 2)]})
    return out, med


def score_rests(score: Any, sr: int | None = None, *, under_median_db: float = 20.0, step_s: float = 0.1,
                t0: float = 0.0) -> tuple[list[dict], float]:
    """The composed score's own rests, measured on the score itself: the stretches more than under_median_db under its
    median level. The owner: the score's silences are planned beats."""
    lv, med = levels_100ms(score, sr, step_s)
    quiet = lv < med - under_median_db
    out, i, k = [], 0, len(lv)
    while i < k:
        if quiet[i]:
            j = i
            while j < k and quiet[j]:
                j += 1
            low = float(lv[i:j].min())
            depth = "silent" if low < -200 else f"{round(med - low)} dB under its median at its quietest"
            out.append({"from": round(t0 + i * step_s, 2), "to": round(t0 + j * step_s, 2),
                        "why": f"a rest in the composed score itself ({depth}): a planned beat"})
            i = j
        else:
            i += 1
    return out, med


def opens_in_silence(score: Any, sr: int | None = None, *, under_median_db: float = 20.0, step_s: float = 0.1,
                     opening_s: float = 0.5) -> bool:
    """Whether a score opens out of silence: its first opening_s more than under_median_db under its median."""
    lv, med = levels_100ms(score, sr, step_s)
    first = lv[: max(1, int(round(opening_s / step_s)))]
    return bool(len(first) and np.all(first < med - under_median_db))


def silences(mix: Any, sr: int | None = None, *, opening_s: float = 0.5, last_s: float = 1.0,
             silent_dbfs: float = -90.0) -> dict:
    """The mix's first sound, its opening's loudest peak and its last stretch's loudest peak."""
    thr = 10 ** (silent_dbfs / 20)
    if isinstance(mix, (str, os.PathLike)):
        info = audio.wav_info(mix)
        sr, n = info.sample_rate, info.frames
        first, seen = None, 0
        for blk in audio.iter_wav(mix, sr * 5):
            idx = np.flatnonzero(np.abs(blk).max(axis=1) > thr)
            if len(idx):
                first = seen + int(idx[0])
                break
            seen += len(blk)
        head, _ = audio.read_wav(mix, 0, int(opening_s * sr))
        tail, _ = audio.read_wav(mix, max(0, n - int(last_s * sr)), int(last_s * sr))
    else:
        x = _as_stereo(mix)
        n = len(x)
        idx = np.flatnonzero(np.abs(x).max(axis=1) > thr)
        first = int(idx[0]) if len(idx) else None
        head, tail = x[: int(opening_s * int(sr))], x[max(0, n - int(last_s * int(sr))):]

    def dbfs(y: np.ndarray) -> float:
        pk = float(np.max(np.abs(y))) if y.size else 0.0
        return round(20 * math.log10(pk), 1) if pk > 0 else -240.0

    return {"first_sound_s": None if first is None else round(first / int(sr), 3), "opening_peak_dbfs": dbfs(head),
            "last_peak_dbfs": dbfs(tail), "seconds": round(n / int(sr), 3)}


# ---------------------------------------------------------------------------------------------------------------
# A film's mix


def _film(x: Any):
    """A sound Film from a Film, verify's context (it has .film) or a film folder."""
    from slate.sound.film import Film, load  # noqa: PLC0415

    if isinstance(x, Film):
        return x
    film = getattr(x, "film", None)
    if isinstance(film, Film):
        return film
    return load(Path(x))


def mix_streams(film) -> tuple[dict[str, list[Part]], dict]:
    """The streams the balance is measured on, and where they came from. Family stems when the mix wrote them;
    otherwise the delivery stems, with the score as the music stem less the tonal effects the mix adds to it."""
    bal = palette_balance(film.palette)
    d = film.path(MIX_DIR)
    fam_dir = d / "family_stems"
    files = {p.stem: p for p in sorted(fam_dir.glob("*.wav")) if not p.name.startswith(".")} if fam_dir.is_dir() else {}
    if files:
        voice = next((files[k] for k in ("voice", "dialogue", "vo") if k in files), None)
        if voice is None:
            raise MissingPrerequisite(f"{film.rel(fam_dir)} has no voice stem (voice.wav).",
                                      hint="Mix again, or remove the family stems so the delivery stems are measured.")
        streams: dict[str, list[Part]] = {"voice": [(voice, 1.0)]}
        others = []
        for k, p in files.items():
            if p == voice:
                continue
            if k in (bal.get("families") or {}):
                streams[k] = [(p, 1.0)]
            else:
                others.append(p)
        streams["everything_else"] = [(p, 1.0) for k, p in files.items() if p != voice]
        return streams, {"source": "family stems", "folder": film.rel(fam_dir), "split": True,
                         "files": [film.rel(p) for p in files.values()],
                         "unknown_family_stems": [film.rel(p) for p in others]}
    stems = {k: d / "stems" / f"{k}.wav" for k in ("dialogue", "effects", "ambience", "music")}
    if not stems["dialogue"].exists():
        raise MissingPrerequisite(f"{film.id} hasn't been mixed yet (no {film.rel(stems['dialogue'])}).",
                                  hint="Run `slate mix` first.")
    eng_dir = d / "engine_stems"
    eng = {p.stem: p for p in sorted(eng_dir.glob("*.wav"))} if eng_dir.is_dir() else {}
    tonal = [eng[r] for r in ("tonal", "tverb") if r in eng]
    families = bal.get("families") or {}
    score_name = next((k for k, v in families.items() if v.get("stem") == "music"), "score")
    plate_name = next((k for k, v in families.items() if v.get("stem") == "ambience"), "plate")
    streams = {"voice": [(stems["dialogue"], 1.0)]}
    used = [stems["dialogue"]]
    if stems["music"].exists():
        streams[score_name] = [(stems["music"], 1.0)] + [(t, -1.0) for t in tonal]
        used += [stems["music"], *tonal]
    if stems["ambience"].exists():
        streams[plate_name] = [(stems["ambience"], 1.0)]
        used.append(stems["ambience"])
    eff = ([(stems["effects"], 1.0)] if stems["effects"].exists() else []) + [(t, 1.0) for t in tonal]
    streams["effects"] = eff
    if stems["effects"].exists():
        used.append(stems["effects"])
    streams["everything_else"] = [(stems[k], 1.0) for k in ("effects", "ambience", "music") if stems[k].exists()]
    return streams, {"source": "delivery stems", "split": False, "files": [film.rel(p) for p in dict.fromkeys(used)],
                     "score_less_tonal": [film.rel(t) for t in tonal]}


def cue_hits(film, bal: dict) -> list[dict]:
    """Every hit in sound/sound_cues.json with its family and span (a gesture's notes are hits of their own)."""
    p = film.path(CUES)
    if not p.exists():
        return []
    sheet = jsonio.read(p) or {}
    out = []
    default = float(bal.get("hit_default_s", 0.5))
    for cue in sheet.get("cues") or []:
        base = {k: v for k, v in cue.items() if k != "notes"}
        items = [{**base, **note, "id": f"{cue.get('id', '?')}.{i + 1}"} for i, note in enumerate(cue.get("notes") or [])] \
            if cue.get("notes") else [base]
        for h in items:
            if "t" not in h:
                continue
            fam = h.get("family") if h.get("family") in (bal.get("families") or {}) else family_of_kind(h.get("kind"), bal)
            if fam is None:
                continue
            dur = h.get("dur") or h.get("seconds")
            if dur is None and h.get("trim"):
                dur = float(h["trim"][1]) - float(h["trim"][0])
            if dur is None and h.get("file"):
                try:
                    dur = audio.duration_s(film.resolve_media(h["file"]))
                except Exception:  # noqa: BLE001 - a missing file leaves the default span
                    dur = None
            t = float(h["t"])
            out.append({"id": h.get("id") or h.get("event"), "kind": h.get("kind"), "family": fam, "t": t,
                        "end": t + float(dur if dur is not None else default), "beat": h.get("beat")})
    return out


def read_rides(film, hits: list[dict] | None = None) -> dict:
    """The mix's rides record (sound/mix/rides.json), each ride with a span (from the record, or its hit's)."""
    data = jsonio.read(film.path(RIDES), default={}) or {}
    spans = {str(h["id"]): (h["t"], h["end"]) for h in hits or []}
    out: dict[str, list] = {"rides": [], "pinned": []}
    for key in ("rides", "pinned"):
        for r in data.get(key) or []:
            r = dict(r)
            if (r.get("t") is None or r.get("end") is None) and str(r.get("hit")) in spans:
                r["t"], r["end"] = spans[str(r.get("hit"))]
            out[key].append(r)
    return out


def _brand_file(film) -> Path | None:
    b = film.brand or {}
    d = b.get("_dir")
    return Path(d) / "brand.json" if d and (Path(d) / "brand.json").exists() else None


def _stat(p: Path) -> tuple:
    try:
        st = p.stat()
        return (str(p), st.st_size, st.st_mtime_ns)
    except OSError:
        return (str(p), None, None)


_CACHE: dict[str, tuple[tuple, dict]] = {}
_WRITE: contextvars.ContextVar[bool] = contextvars.ContextVar("slate_balance_write", default=True)


def analyse(x: Any) -> dict:
    """measure() on a film's mix, with its words, hits and rides, cached for one set of inputs (so the six gates of
    one `slate verify` share one pass). Writes sound/mix/balance_check.json, keeping the previous mix's as
    balance_check.before.json for the half-depth comparison."""
    film = _film(x)
    from slate.sound.film import words_for  # noqa: PLC0415

    bal = palette_balance(film.palette)
    streams, where = mix_streams(film)
    files = sorted({Path(s) for parts in streams.values() for s, _ in parts if isinstance(s, (str, os.PathLike))})
    extra = [film.path(r) for r in ("timing/vo_words.json", "timing/vo_cues.json", CUES, RIDES, "film.json")]
    key = (tuple(_stat(p) for p in [*files, *extra]), jsonio.sha256_obj(bal))
    hit = _CACHE.get(str(film.dir))
    if hit is not None and hit[0] == key:
        res = hit[1]
        if _WRITE.get() and not res.get("_written"):  # measured during a dry run: record it now
            _write_report(film, res, read_rides(film, cue_hits(film, bal) if res["where"]["split"] else []))
            res["_written"] = True
        return res
    words = words_for(film)
    hits = cue_hits(film, bal) if where["split"] else []
    rides = read_rides(film, hits)
    res = measure(streams, film.sr, words=words or None, hits=hits, bal=bal, rides=rides, n=film.n,
                  split=where["split"])
    res["where"] = where
    res["balance"] = {k: bal.get(k) for k in ("source", "effects", "clarity", "never_over", "texture_air",
                                               "accents_over_score", "levels", "approved_spans")}
    res["inputs"] = gates.inputs_for(film.dir, [*files, *[p for p in extra if p.exists()],
                                                *([_brand_file(film)] if _brand_file(film) else [])])
    res["mix_sha"] = jsonio.sha256_obj({k: v for k, v in res["inputs"].items() if k.startswith(MIX_DIR)})
    res["rides"] = {"rides": len(rides["rides"]), "pinned": len(rides["pinned"]),
                    "half_depth": sum(1 for r in rides["rides"] if r.get("half_depth"))}
    if _WRITE.get():
        _write_report(film, res, rides)
        res["_written"] = True
    _CACHE.clear()
    _CACHE[str(film.dir)] = (key, res)
    return res


def _write_report(film, res: dict, rides: dict) -> None:
    out = film.path(REPORT)
    old = jsonio.read(out, default=None) if out.exists() else None
    if isinstance(old, dict) and old.get("mix_sha") and old.get("mix_sha") != res["mix_sha"]:
        jsonio.write_atomic(film.path(BEFORE), old)
    body = {k: v for k, v in res.items() if not k.startswith("_")}
    jsonio.write_atomic(out, jsonio.mark_generated({"kind": "balance_check", "film": film.id, **body}, "slate verify"))
    if res["rides"]["half_depth"]:
        before = jsonio.read(film.path(BEFORE), default=None)
        hd = half_depth_words(res, before if isinstance(before, dict) else None, rides)
        jsonio.write_atomic(film.path("sound/mix/half_depth_words.json"),
                            jsonio.mark_generated(hd, "slate verify"))
        jsonio.write_text_atomic(film.path("sound/mix/half_depth_words.md"),
                                 half_depth_markdown(hd, now=film.rel(film.path(MIX_DIR)),
                                                     before="the previous mix" if hd["before"] else "(no earlier mix)"))


# ---------------------------------------------------------------------------------------------------------------
# Gates


def _engine() -> dict:
    from slate import __version__  # noqa: PLC0415

    return gates.engine("slate balance", f"{__version__}+{VERSION_TAG}", meter=meter.ENGINE["name"])


def _result(make: Callable, gate: str, summary: str, res: dict, details: dict, evidence: list[str] | None = None) -> GateResult:
    r = make(gate, summary, evidence=evidence or [REPORT], engine=_engine(), inputs=res.get("inputs") or {},
             details={"stage": "sound", "unit": res.get("unit"), "source": (res.get("where") or {}).get("source"),
                      **details})
    return r


def _no_voice(gate: str, res: dict) -> GateResult | None:
    if res.get("voice_lufs") is None:
        return _result(GateResult.pass_, gate, "no voice in this film, so nothing to keep clear of it", res, {})
    if not res.get("units"):
        return _result(GateResult.unchecked, gate, "the voice has no measurable words or phrases", res, {})
    return None


def _effects_unsplit(res: dict) -> bool:
    return not res.get("split") and bool((res.get("live") or {}).get("effects"))


def _guarded(gate: str):
    """A gate that can't measure says so: a missing prerequisite goes up as itself (slate verify turns it into
    UNCHECKED with its next step); any other refusal (a stem at the wrong sample rate, a broken record) becomes an
    UNCHECKED result here, never a crash and never a pass."""
    def wrap(fn: Callable[[Any], GateResult]) -> Callable[[Any], GateResult]:
        @functools.wraps(fn)
        def inner(x: Any) -> GateResult:
            from slate.errors import SlateError  # noqa: PLC0415

            try:
                return fn(x)
            except MissingPrerequisite:
                raise
            except SlateError as exc:
                return GateResult.unchecked(gate, f"couldn't measure the balance: {exc.message.splitlines()[0]}",
                                            evidence=[f"next: {exc.hint}"] if exc.hint else [], engine=_engine(),
                                            details={"stage": "sound", "error": exc.to_dict()})
        return inner
    return wrap


def _accepted(film, gate: str, key: str, basis: dict) -> dict | None:
    try:
        from slate.qa import accept  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - without the qa module there are no acceptances to honour
        return None
    return accept.match(film.dir, gate, key, basis)


@_guarded("sound.balance.clarity")
def gate_clarity(x: Any) -> GateResult:
    """Every word: the voice at least 12 dB over everything else in 1-5 kHz (each family on its own reported, and a
    family's own floor kept); a word under a half-ridden accent is the owner's trade: listed, a person listens under
    8 dB, and under 6 dB it fails."""
    gate = "sound.balance.clarity"
    film = _film(x)
    res = analyse(film)
    early = _no_voice(gate, res)
    if early:
        return early
    c = res["clarity"]
    basis = {k: v for k, v in (res.get("inputs") or {}).items() if k.startswith(MIX_DIR)}
    unit = res["unit"]
    items, accepted = [], []
    for r in c["traded_flagged"]:
        item = {"key": f"{unit}:{r['s']:.3f}", "what": f"{r['word']!r} at {r['s']} s: {r['voice_over_rest_db']} dB "
                f"over everything else in {c['band_hz'][0]:g}-{c['band_hz'][1]:g} Hz, under the half-ridden "
                f"{', '.join(r['under_half_depth'][:4])} (the trade; under the {c['flag_db']:g} dB flag)", "basis": basis}
        a = _accepted(film, gate, item["key"], basis)
        (accepted if a else items).append({**item, **({"accepted_by": a.get("by")} if a else {})})
    details = {k: c[k] for k in ("rule_db", "band_hz", "units", "min_db", "median_db", "worst", "failing",
                                 "traded_under_half_depth", "traded_under_floor", "approved_span_under_rule",
                                 "alone_under_floor", "each_family_alone_worst")}
    details.update({"review_items": items, "accepted": accepted})
    head = (f"{c['units']} {unit}s: worst {c['min_db']} dB, median {c['median_db']} dB over everything else in "
            f"{c['band_hz'][0]:g}-{c['band_hz'][1]:g} Hz (rule ≥ {c['rule_db']:g})")
    notes = []
    if c["traded_under_half_depth"]:
        notes.append(f"{len(c['traded_under_half_depth'])} under a half-ridden accent (the trade, listed)")
    if c["approved_span_under_rule"]:
        notes.append(f"{len(c['approved_span_under_rule'])} in an approved span, as approved")
    if c["failing"]:
        bad = sorted(c["failing"], key=lambda r: r["voice_over_rest_db"])
        return _result(GateResult.fail, gate, f"{len(bad)} {unit}{'s' if len(bad) != 1 else ''} under {c['rule_db']:g} dB "
                       f"(worst {bad[0]['word']!r} at {bad[0]['s']} s, {bad[0]['voice_over_rest_db']} dB); {head}", res,
                       details, [REPORT] + [f"{r['word']!r} at {r['s']} s: {r['voice_over_rest_db']} dB "
                                            f"(by family: {r['by_family_db']})" for r in bad[:20]])
    if c["traded_under_floor"]:
        w = c["traded_under_floor"][0]
        return _result(GateResult.fail, gate, f"{len(c['traded_under_floor'])} traded {unit}(s) under the "
                       f"{c['floor_db']:g} dB floor (worst {w['word']!r} at {w['s']} s, {w['voice_over_rest_db']} dB): "
                       "the trade stops there", res, details)
    if c["alone_under_floor"]:
        a = sorted(c["alone_under_floor"], key=lambda r: r["db"])
        return _result(GateResult.fail, gate, f"the {a[0]['family']} on its own crowds {len(a)} {unit}(s) (worst "
                       f"{a[0]['word']!r} at {a[0]['s']} s: {a[0]['db']} dB, its floor {a[0]['rule_db']:g})", res, details,
                       [REPORT] + [f"{r['family']}: {r['word']!r} at {r['s']} s, {r['db']} dB" for r in a[:20]])
    if items:
        return _result(GateResult.review, gate, f"{len(items)} traded {unit}(s) under the {c['flag_db']:g} dB flag wait "
                       f"for a person to listen; {head}", res, details, [REPORT] + [i["what"] for i in items[:20]])
    return _result(GateResult.pass_, gate, head + (f" ({'; '.join(notes)})" if notes else ""), res, details)


@_guarded("sound.balance.never_over")
def gate_never_over(x: Any) -> GateResult:
    """While the voice speaks, nothing is louder than it: each family, all the effects together and everything else
    together, every 10 ms inside every word (EBU momentary, 400 ms, K-weighted)."""
    gate = "sound.balance.never_over"
    film = _film(x)
    res = analyse(film)
    early = _no_voice(gate, res)
    if early:
        return early
    o = res["never_over"]
    live = {f: v for f, v in o["by_family"].items() if v}
    details = {"rule_db": o["rule_db"], "by_family": o["by_family"]}
    if not live:
        return _result(GateResult.pass_, gate, "nothing else sounds while the voice speaks", res, details)
    worst = min(live.items(), key=lambda kv: kv[1]["min_margin_db"])
    if o["failing"]:
        bad = sorted(o["failing"].items(), key=lambda kv: kv[1]["min_margin_db"])
        return _result(GateResult.fail, gate, f"louder than the voice while it speaks: " + "; ".join(
            f"{f} {v['min_margin_db']:+.1f} dB at {v['at']} s ({v['word']!r})" for f, v in bad), res, details)
    return _result(GateResult.pass_, gate, f"the closest is {worst[0]} at {worst[1]['at']} s ({worst[1]['word']!r}), "
                   f"{worst[1]['min_margin_db']} dB under the voice (rule ≥ {o['rule_db']:g})", res, details)


@_guarded("sound.balance.texture_air")
def gate_texture_air(x: Any) -> GateResult:
    """Every word: the noise textures' 2-6 kHz at least 20 dB under the voice's own 2-6 kHz."""
    gate = "sound.balance.texture_air"
    film = _film(x)
    res = analyse(film)
    early = _no_voice(gate, res)
    if early:
        return early
    t = res["texture_air"]
    details = {k: t[k] for k in ("rule_db", "band_hz", "family", "units_with_texture", "min_db_under_voice", "worst",
                                 "failing")}
    if t["family"] not in res["families"]:
        if _effects_unsplit(res):
            return _result(GateResult.unchecked, gate, "the effects are one stem, so the textures can't be measured "
                           "apart (the effects engine writes family stems)", res, details,
                           [REPORT, "next: mix with family stems (sound/mix/family_stems/)"])
        return _result(GateResult.pass_, gate, "no textures in this film", res, details)
    if t["failing"]:
        w = t["failing"][0]
        return _result(GateResult.fail, gate, f"{len(t['failing'])} {res['unit']}(s) with the textures' air within "
                       f"{t['rule_db']:g} dB of the voice (worst {w['word']!r} at {w['s']} s, {w['db_under_voice']} dB)",
                       res, details)
    if not t["units_with_texture"]:
        return _result(GateResult.pass_, gate, "no texture sounds under any word", res, details)
    return _result(GateResult.pass_, gate, f"{t['units_with_texture']} {res['unit']}(s) with texture under them; the "
                   f"closest {t['min_db_under_voice']} dB under the voice (rule ≥ {t['rule_db']:g})", res, details)


@_guarded("sound.balance.accents")
def gate_accents(x: Any) -> GateResult:
    """Every accent and impact clears the score by at least 3 dB in one of its own loudest third-octave bands, over
    its loudest 120 ms."""
    gate = "sound.balance.accents"
    film = _film(x)
    res = analyse(film)
    a = res["accents_over_score"]
    details = dict(a)
    fams = res["families"]
    score_live = (res.get("live") or {}).get(a["score"])
    if not any(f in fams for f in a["families"]):
        if _effects_unsplit(res):
            return _result(GateResult.unchecked, gate, "the effects are one stem, so the accents can't be measured "
                           "apart (the effects engine writes family stems)", res, details,
                           [REPORT, "next: mix with family stems (sound/mix/family_stems/)"])
        return _result(GateResult.pass_, gate, "no accents or impacts in this film", res, details)
    if not score_live:
        return _result(GateResult.pass_, gate, "no score for the accents to clear", res, details)
    if not a["hits"]:
        cues_there = film.path(CUES).exists()
        if cues_there:
            return _result(GateResult.pass_, gate, "no accent or impact sounds in the mix", res, details)
        return _result(GateResult.unchecked, gate, "there's no cue sheet (sound/sound_cues.json) to find the accents by",
                       res, details)
    if a["failing"]:
        w = a["failing"][0]
        return _result(GateResult.fail, gate, f"{len(a['failing'])} hit(s) under {a['rule_db']:g} dB over the score "
                       f"(weakest {w[1]} at {w[3]} s: {w[0]} dB)", res, details)
    return _result(GateResult.pass_, gate, f"{a['hits']} hit(s) clear the score; the weakest by {a['min_db']} dB "
                   f"(rule ≥ {a['rule_db']:g})", res, details)


@_guarded("sound.balance.levels")
def gate_levels(x: Any) -> GateResult:
    """Every family inside its range against the voice: integrated, its hits' median and loudest, and inside words."""
    gate = "sound.balance.levels"
    film = _film(x)
    res = analyse(film)
    lv = res["levels"]
    details = {"integrated_lu_vs_voice": lv["integrated_lu_vs_voice"], "per_family": lv["per_family"],
               "under_speech": res["under_speech"], "failing": lv["failing"], "ranges": res["balance"]["levels"]}
    if res.get("voice_lufs") is None:
        return _result(GateResult.pass_, gate, "no voice in this film: the families have nothing to sit under", res,
                       details)
    table = ", ".join(f"{f} {v:+.1f}" for f, v in lv["integrated_lu_vs_voice"].items()
                      if v is not None and f not in ("effects", "everything_else"))
    if lv["failing"]:
        return _result(GateResult.fail, gate, "; ".join(lv["failing"][:3]) + (" …" if len(lv["failing"]) > 3 else ""),
                       res, details, [REPORT, *lv["failing"]])
    effect_fams = [f for f in (res["balance"]["levels"] or {}) if f in (res["balance"].get("effects") or [])]
    if _effects_unsplit(res) and effect_fams:
        return _result(GateResult.unchecked, gate, f"the effects are one stem, so {', '.join(effect_fams)} can't be "
                       f"measured apart (the rest are inside their ranges: {table or 'nothing else'})", res, details,
                       [REPORT, "next: mix with family stems (sound/mix/family_stems/)"])
    return _result(GateResult.pass_, gate, f"every family inside its range (LU against the voice: {table or 'none'})",
                   res, details)


def _stops_analysis(film) -> dict:
    from slate.sound.mix import planned_stops  # noqa: PLC0415

    bal = palette_balance(film.palette)
    st = bal.get("stops") or {}
    d = film.path(MIX_DIR)
    music, mix = d / "stems" / "music.wav", d / "final_mix.wav"
    score = film.path("sound/score/score.wav")
    plan = planned_stops(film)
    out: dict[str, Any] = {"plan": plan, "music": film.rel(music) if music.exists() else None,
                           "score": film.rel(score) if score.exists() else None}
    if not mix.exists():
        raise MissingPrerequisite(f"{film.id} hasn't been mixed yet (no {film.rel(mix)}).", hint="Run `slate mix` first.")
    under, step = float(st.get("under_median_db", 20.0)), float(st.get("step_s", 0.1))
    levels = levels_100ms(music, None, step) if music.exists() else None
    out["music_silent"] = levels is None or levels[1] <= -200.0
    if levels is not None and plan and not out["music_silent"]:
        stops, med = widen_stops(music, None, plan, under_median_db=under, cap_s=float(st.get("fade_cap_s", 2.0)),
                                 step_s=step, levels=levels)
    else:
        stops, med = [dict(s) for s in plan], (levels[1] if levels is not None else None)
    rests: list[dict] = []
    if score.exists() and st.get("score_rests", True):
        rests, _ = score_rests(score, None, under_median_db=under, step_s=step)
    out.update({"widened": stops, "music_median_dbfs": None if med is None else round(med, 1), "score_rests": rests})
    from slate.sound import gap  # noqa: PLC0415

    out["score_planned"] = score.exists() or film.path("sound/score/plan.json").exists()
    if music.exists() and not out["music_silent"]:
        out["gap"] = gap.check_file(music, stops + rests, threshold=float(st.get("gap_threshold_dbfs", -60.0)),
                                    window_s=float(st.get("gap_window_s", 0.5)))
    out["silences"] = silences(mix, None, opening_s=float(st.get("opening_s", 0.5)), last_s=float(st.get("last_s", 1.0)),
                               silent_dbfs=float(st.get("silent_dbfs", -90.0)))
    if score.exists():
        out["score_opens_in_silence"] = opens_in_silence(score, None, under_median_db=float(st.get("under_median_db", 20.0)),
                                                         opening_s=float(st.get("opening_s", 0.5)))
    rels = [p for p in (music, mix, score, film.path("sound/score/score.json"), film.path("sound/score/directions.json"),
                        film.path("film.json")) if p.exists()]
    out["inputs"] = gates.inputs_for(film.dir, rels)
    return out


@_guarded("sound.planned_stops")
def gate_planned_stops(x: Any) -> GateResult:
    """The music's quiet stretches are all planned (each stop widened by the music's own fade into and out of it; the
    composed score's own rests count as planned beats), and the film opens out of silence and ends in it only when its
    plan says so."""
    from slate.qa.checks import Sub, combine  # noqa: PLC0415

    gate = "sound.planned_stops"
    film = _film(x)
    st = palette_balance(film.palette).get("stops") or {}
    a = _stops_analysis(film)
    subs = []
    g = a.get("gap")
    widened = [s for s in a["widened"] if s.get("fade_tails_s") and any(s["fade_tails_s"])]
    if a["music_silent"] and a["score_planned"]:
        subs.append(Sub("gap", "FAIL", "the film has a score, but its music stem is silent"))
    elif a["music_silent"]:
        subs.append(Sub("gap", "PASS", "no score in this film (voice and effects only), so no music gaps"))
    if g is not None:
        if g["pass"]:
            extra = (f"; {len(widened)} stop(s) widened by the music's own fade" if widened else "") + \
                    (f"; {len(a['score_rests'])} rest(s) in the score itself counted as planned" if a["score_rests"] else "")
            subs.append(Sub("gap", "PASS", f"quietest music window outside the planned stops: "
                            f"{g['quietest_outside_stops_dbfs']} dBFS (floor {g['threshold_dbfs']:g}){extra}"))
        else:
            f0 = g["failures"][0]
            subs.append(Sub("gap", "FAIL", f"{len(g['failures'])} music window(s) under {g['threshold_dbfs']:g} dBFS outside "
                            f"the planned stops, first at {f0['t']} s ({f0['dbfs']} dBFS)",
                            [f"{f['t']} s: {f['dbfs']} dBFS" for f in g["failures"][:20]]))
    frame = film.frame_s
    sil = a["silences"]
    opening = any(float(s["from"]) <= frame for s in a["plan"])
    ending = any(float(s["to"]) >= film.duration_s - frame for s in a["plan"])
    want_open = st.get("open_silent", "plan") in (True, "always") or (st.get("open_silent", "plan") == "plan" and opening)
    want_end = st.get("end_silent", "plan") in (True, "always") or (st.get("end_silent", "plan") == "plan" and ending)
    if want_open:
        ok = sil["first_sound_s"] is None or sil["first_sound_s"] >= float(st.get("opening_s", 0.5)) - 1e-9
        hint = []
        if not ok and a.get("score_opens_in_silence") is False:
            hint = ["the score sounds from its first moments (measured on the score file), so either the plan's "
                    "opening stop goes or the score waits"]
        subs.append(Sub("opening", "PASS" if ok else "FAIL", f"the plan opens out of silence: first sound at "
                        f"{sil['first_sound_s']} s (want ≥ {float(st.get('opening_s', 0.5)):g} s)", hint))
    if want_end:
        ok = sil["last_peak_dbfs"] <= float(st.get("silent_dbfs", -90.0))
        subs.append(Sub("ending", "PASS" if ok else "FAIL", f"the plan ends in silence: the last "
                        f"{float(st.get('last_s', 1.0)):g} s peaks at {sil['last_peak_dbfs']} dBFS (want ≤ "
                        f"{float(st.get('silent_dbfs', -90.0)):g})"))
    return combine(gate, subs, engine=_engine(), inputs=a["inputs"],
                   details={"stage": "sound", "planned": a["plan"], "widened": a["widened"], "score_rests": a["score_rests"],
                            "music_median_dbfs": a["music_median_dbfs"], "silences": sil,
                            "score_opens_in_silence": a.get("score_opens_in_silence"),
                            "gap": {k: g[k] for k in ("quietest_outside_stops_dbfs", "quietest_at_s", "failures",
                                                      "threshold_dbfs", "window_s")} if g else None})


# ---------------------------------------------------------------------------------------------------------------
# Approved spans: a film keeps what the owner approved in a clip (L-sound-S031)

SPAN_TOLERANCE_LU = 1.0  # a family, the score or the span's loudness may move this much and still be the approved mix
SPAN_RIDE_TOLERANCE_DB = 1.0  # the films' span check: the film rides each hit the clip rode within 1 dB
SPAN_CORE_PAD_S = (0.5, 2.0)  # the clip's own fade-in and fade-out, left out of the score's level


def dir_streams(mix_dir: Path, bal: dict) -> tuple[dict[str, list[Part]], dict]:
    """The streams of a mix folder (a clip's sound/mix, or any folder with final_mix.wav and stems/): its family stems
    when it has them, else its delivery stems (voice, score, plate and the effects as one)."""
    fam_dir = mix_dir / "family_stems"
    files = {p.stem: p for p in sorted(fam_dir.glob("*.wav")) if not p.name.startswith(".")} if fam_dir.is_dir() else {}
    if files:
        voice = next((files[k] for k in ("voice", "dialogue", "vo") if k in files), None)
        if voice is None:
            raise MissingPrerequisite(f"{fam_dir} has no voice stem (voice.wav).")
        streams: dict[str, list[Part]] = {"voice": [(voice, 1.0)]}
        for k, p in files.items():
            if p != voice and k in (bal.get("families") or {}):
                streams[k] = [(p, 1.0)]
        streams["everything_else"] = [(p, 1.0) for p in files.values() if p != voice]
        return streams, {"source": "family stems", "split": True}
    stems = {k: mix_dir / "stems" / f"{k}.wav" for k in ("dialogue", "effects", "ambience", "music")}
    if not stems["dialogue"].exists():
        raise MissingPrerequisite(f"{mix_dir} has no mix to measure (no stems/dialogue.wav or family_stems/).")
    families = bal.get("families") or {}
    score_name = next((k for k, v in families.items() if v.get("stem") == "music"), "score")
    plate_name = next((k for k, v in families.items() if v.get("stem") == "ambience"), "plate")
    streams = {"voice": [(stems["dialogue"], 1.0)]}
    if stems["music"].exists():
        streams[score_name] = [(stems["music"], 1.0)]
    if stems["ambience"].exists():
        streams[plate_name] = [(stems["ambience"], 1.0)]
    streams["effects"] = [(stems["effects"], 1.0)] if stems["effects"].exists() else []
    streams["everything_else"] = [(stems[k], 1.0) for k in ("effects", "ambience", "music") if stems[k].exists()]
    return streams, {"source": "delivery stems", "split": False}


def _cut(streams: dict[str, list[Part]], a: int, n: int) -> dict[str, list[Part]]:
    """Every stream's sources cut to samples a..a+n (zero-padded past either end)."""
    reader = _Reader()
    out: dict[str, list[Part]] = {}
    for name, parts in streams.items():
        out[name] = []
        for src, coef in parts:
            x = reader.read(src, max(0, a), max(0, a) + n)
            if a < 0:
                x = np.concatenate([np.zeros((-a, x.shape[1])), x])[:n]
            out[name].append((x.copy(), coef))
            reader.cache.clear()
    return out


def _shift(items: list[dict], t0: float, t1: float, offset: float) -> list[dict]:
    return [{**h, "t": float(h["t"]) - offset, "end": float(h["end"]) - offset} for h in items
            if t0 - 1e-3 <= float(h.get("beat", h["t"]) if h.get("beat") is not None else h["t"]) < t1]


def _ride_db(r: dict) -> float:
    return float(r.get("depth_db") or 0.0)


def _core_score_lu(streams: dict[str, list[Part]], sr: int, n: int, bal: dict) -> float | None:
    families = bal.get("families") or {}
    score_name = next((k for k, v in families.items() if v.get("stem") == "music"), "score")
    if score_name not in streams:
        return None
    i0, i1 = int(SPAN_CORE_PAD_S[0] * sr), n - int(SPAN_CORE_PAD_S[1] * sr)
    if i1 - i0 < int(1.0 * sr):
        i0, i1 = 0, n
    rd = _Reader()
    v = rd.stream(streams["voice"], i0, i1)
    rd.cache.clear()
    sc = rd.stream(streams[score_name], i0, i1)
    if not np.any(v) or not np.any(sc):
        return None
    return round(meter.integrated(sc, sr) - meter.integrated(v, sr), 2)


def _span_clip(film, spec: dict) -> Path | None:
    raw = spec.get("clip") or spec.get("file")
    if not raw:
        return None
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        brand_dir = (film.brand or {}).get("_dir")
        p = next((c for c in (film.path(p), Path(brand_dir) / p if brand_dir else None) if c is not None and c.exists()),
                 film.path(p))
    if p.is_file():
        p = p.parent
    return p / "sound" / "mix" if (p / "sound" / "mix").is_dir() else p


def compare_span(film, spec: dict, bal: dict, film_streams: dict, where: dict, words: list, hits: list,
                 rides: dict) -> dict:
    """One approved span: the film's mix cut to the window against the approved clip, measured the same way."""
    t0, t1 = float(spec["from"]), float(spec["to"])
    mix_dir = _span_clip(film, spec)
    if mix_dir is None or not mix_dir.exists():
        return {"span": [t0, t1], "unchecked": f"the span {t0:g}-{t1:g} s names no approved clip that's on this "
                                               "machine (approved_spans: clip)"}
    clip_streams, cwhere = dir_streams(mix_dir, bal)
    n = _length(clip_streams["voice"][0][0])
    starts = float(spec.get("clip_starts_at", t0))  # the film second the clip's first sample sits at
    a = int(round(starts * film.sr))
    span_streams = _cut(film_streams, a, n)
    wd = [(w, s - starts, e - starts) for w, s, e in words if t0 <= s < t1]
    hits_span = _shift(hits, t0, t1, starts)
    ride_span = {k: _shift(rides.get(k) or [], t0, t1, starts) for k in ("rides", "pinned")}
    clip_rides = jsonio.read(mix_dir / "rides.json", default={}) or {}
    clip_rides = {k: [{**r, "t": float(r["t"]), "end": float(r["end"])} for r in clip_rides.get(k) or []
                      if r.get("t") is not None and r.get("end") is not None] for k in ("rides", "pinned")}
    clip_hits = hits_span if cwhere["split"] else []
    m_clip = measure(clip_streams, film.sr, words=wd or None, hits=clip_hits, bal=bal, rides=clip_rides, n=n,
                     split=cwhere["split"])
    m_span = measure(span_streams, film.sr, words=wd or None, hits=hits_span if where["split"] else [], bal=bal,
                     rides=ride_span, n=n, split=where["split"])
    problems, families = [], {}
    for f in sorted(set(m_clip["families"]) & set(m_span["families"])):
        pc, ps = (m_clip["levels"]["per_family"].get(f) or {}), (m_span["levels"]["per_family"].get(f) or {})
        rc, rs = (m_clip["under_speech"].get(f) or {}), (m_span["under_speech"].get(f) or {})
        row = {"integrated_lu": [pc.get("integrated_lu"), ps.get("integrated_lu")],
               "loudest_hit_lu": [(pc.get("hit_momentary_lu") or {}).get("max"), (ps.get("hit_momentary_lu") or {}).get("max")],
               "inside_words_lu": [rc.get("inside_words_lu"), rs.get("inside_words_lu")]}
        families[f] = row
        for k, (c, v) in row.items():
            if c is not None and v is not None and abs(v - c) > SPAN_TOLERANCE_LU + 1e-9:
                problems.append(f"{f} {k.replace('_', ' ')}: {v:+.1f} LU in the film against {c:+.1f} in the approved clip")
    ride_rows = []
    film_by_hit = {str(r.get("hit")): r for r in ride_span["rides"]}
    for r in clip_rides["rides"]:
        fr = film_by_hit.get(str(r.get("hit"))) or next(
            (x for x in ride_span["rides"] if abs(float(x["t"]) - float(r["t"])) <= 0.05), None)
        diff = round((_ride_db(fr) if fr else 0.0) - _ride_db(r), 1)
        ride_rows.append({"hit": r.get("hit"), "clip_db": _ride_db(r), "film_db": _ride_db(fr) if fr else 0.0,
                          "diff_db": diff})
        if abs(diff) >= SPAN_RIDE_TOLERANCE_DB:
            problems.append(f"the ride on {r.get('hit')}: {ride_rows[-1]['film_db']:+.1f} dB in the film against "
                            f"{_ride_db(r):+.1f} in the approved clip")
    for fr in ride_span["rides"]:
        if not any(str(fr.get("hit")) == str(r.get("hit")) for r in clip_rides["rides"]) and abs(_ride_db(fr)) >= SPAN_RIDE_TOLERANCE_DB:
            ride_rows.append({"hit": fr.get("hit"), "clip_db": 0.0, "film_db": _ride_db(fr), "diff_db": _ride_db(fr)})
            problems.append(f"the film rides {fr.get('hit')} {_ride_db(fr):+.1f} dB, which the approved clip left alone")
    core = [_core_score_lu(clip_streams, film.sr, n, bal), _core_score_lu(span_streams, film.sr, n, bal)]
    if None not in core and abs(core[1] - core[0]) > SPAN_TOLERANCE_LU + 1e-9:
        problems.append(f"the score over the span's core: {core[1]:+.1f} LU against the voice in the film, "
                        f"{core[0]:+.1f} in the approved clip")
    return {"span": [t0, t1], "clip": str(mix_dir), "why": spec.get("why"), "families": families, "rides": ride_rows,
            "score_core_lu_vs_voice": core, "clip_source": cwhere["source"], "film_source": where["source"],
            "problems": problems}


@_guarded("sound.balance.approved_spans")
def gate_approved_spans(x: Any) -> GateResult:
    """Every approved span (palette balance approved_spans) is kept exactly as approved: the film's mix over the span
    against the approved clip, family by family, ride by ride and by the score's level over the span's core."""
    gate = "sound.balance.approved_spans"
    film = _film(x)
    bal = palette_balance(film.palette)
    streams, where = mix_streams(film)  # a gate of the mix: no mix yet is its next step, as for every balance gate
    spans = [s for s in bal.get("approved_spans") or [] if isinstance(s, dict) and s.get("from") is not None]
    if not spans:
        return GateResult.pass_(gate, "no approved spans in this film's palette", engine=_engine(),
                                details={"stage": "sound"})
    from slate.sound.film import words_for  # noqa: PLC0415

    hits = cue_hits(film, bal) if where["split"] else []
    rides = read_rides(film, hits)
    rows = [compare_span(film, sp, bal, streams, where, words_for(film), hits, rides) for sp in spans]
    problems = [f"{r['span'][0]:g}-{r['span'][1]:g} s: {p}" for r in rows for p in r.get("problems") or []]
    unchecked = [r["unchecked"] for r in rows if r.get("unchecked")]
    details = {"stage": "sound", "spans": rows, "tolerance_lu": SPAN_TOLERANCE_LU,
               "ride_tolerance_db": SPAN_RIDE_TOLERANCE_DB}
    inputs = gates.inputs_for(film.dir, [p for p in film.path(MIX_DIR).glob("**/*.wav")][:40])
    if problems:
        return GateResult.fail(gate, f"{len(problems)} difference(s) from what the owner approved: {problems[0]}",
                               evidence=problems[:20], engine=_engine(), inputs=inputs, details=details)
    if unchecked:
        return GateResult.unchecked(gate, unchecked[0], engine=_engine(), inputs=inputs, details=details)
    return GateResult.pass_(gate, f"{len(rows)} approved span(s) kept as approved: every family within "
                            f"{SPAN_TOLERANCE_LU:g} LU, every ride within {SPAN_RIDE_TOLERANCE_DB:g} dB",
                            engine=_engine(), inputs=inputs, details=details)


GATES: dict[str, Callable[[Any], GateResult]] = {
    "sound.balance.clarity": gate_clarity,
    "sound.balance.never_over": gate_never_over,
    "sound.balance.texture_air": gate_texture_air,
    "sound.balance.accents": gate_accents,
    "sound.balance.levels": gate_levels,
    "sound.planned_stops": gate_planned_stops,
    "sound.balance.approved_spans": gate_approved_spans,
}


def run(film_dir: Path, names: list[str] | None = None, *, write: bool = True) -> list[GateResult]:
    """Run the balance gates on a film's mix (UNCHECKED, with the next step, when one can't run), record them in
    qa/gates.json (and the measurements in sound/mix/balance_check.json), and return them. write=False changes
    nothing on disk. slate verify runs the same gates once they're in its GATES."""
    from slate.errors import SlateError  # noqa: PLC0415

    chosen = names or list(GATES)
    unknown = [n for n in chosen if n not in GATES and f"sound.{n}" not in GATES]
    if unknown:
        raise UsageError(f"Unknown balance gate(s): {', '.join(unknown)}. Known: {', '.join(GATES)}.")
    results = []
    token = _WRITE.set(bool(write))
    try:
        for name in chosen:
            key = name if name in GATES else f"sound.{name}"
            try:
                results.append(GATES[key](film_dir))
            except SlateError as exc:
                results.append(GateResult.unchecked(key, exc.message, evidence=[f"next: {exc.hint}"] if exc.hint else [],
                                                    engine=_engine(), details={"stage": "sound"}))
    finally:
        _WRITE.reset(token)
    if write:
        gates.write_report(Path(film_dir), results)
    return results


# ---------------------------------------------------------------------------------------------------------------
# The command: `slate sound balance`

HELP = "the balance: every word clear of the score and effects, every family where the palette puts it"
DESCRIPTION = """The balance of the film's mix against the voice, measured word by word (or phrase by phrase when
the film has no word timings yet), recorded in qa/gates.json and sound/mix/balance_check.json.

    slate sound balance                  every balance gate, and a table of the families against the voice
    slate sound balance --words          also every word: how far the voice sits over everything else
    slate sound balance clarity levels   just these gates

The rules and the families' ranges are the brand's sound palette (brand.json sound.palette.balance)."""


def register(sub) -> None:
    """Add `balance` to a subparsers object (the `slate sound` group)."""
    from slate.cli import add_command  # noqa: PLC0415

    p = add_command(sub, "balance", _cmd, help=HELP, description=DESCRIPTION)
    p.add_argument("gates", nargs="*", metavar="GATE", help="balance gates (default: all), e.g. clarity levels")
    p.add_argument("--words", action="store_true", help="print every word's clarity too")


def _cmd(args: argparse.Namespace) -> int:
    from slate.cli import film as film_of  # noqa: PLC0415

    from slate.cli import dry_stop  # noqa: PLC0415

    film_dir = film_of(args)
    names = [g if g.startswith("sound.") else (f"sound.balance.{g}" if f"sound.balance.{g}" in GATES else f"sound.{g}")
             for g in (args.gates or [])]
    if dry_stop(args, f"measure the mix's balance word by word ({', '.join(names) if names else 'every balance gate'}),"
                      " here (not queued), and record it"):
        return 0  # the measurement reads the whole mix: a dry run starts none of it
    results = run(film_dir, names or None, write=True)
    rep = jsonio.read(Path(film_dir) / REPORT, default={}) or {}
    lines = [gates.format_table(results)]
    lv = (rep.get("levels") or {}).get("integrated_lu_vs_voice") or {}
    if lv:
        lines.append("against the voice (LU, integrated | inside words | between words):")
        for f, v in lv.items():
            us = (rep.get("under_speech") or {}).get(f) or {}
            lines.append(f"  {f:16s} {str(v):>6s} | {str(us.get('inside_words_lu')):>6s} | {str(us.get('between_words_lu')):>6s}")
    if args.words:
        for w, s, _e, db in (rep.get("clarity") or {}).get("per_unit") or []:
            lines.append(f"  {s:9.3f}  {db:6.1f} dB  {w}")
    log.result({"gates": [r.to_dict() for r in results], "summary": gates.summarize(results), "report": REPORT},
               "\n".join(lines))
    return gates.exit_code(results)


def ending_stop(film) -> dict | None:
    """The planned stop a film's ending needs: its last second silent, when the brand's balance says so (end_silent
    "always", the films' rule), else None. `slate score plan` writes it and the score fades into it."""
    st = palette_balance(film.palette).get("stops") or {}
    if st.get("end_silent", "always") not in (True, "always"):
        return None
    last = float(st.get("last_s", 1.0))
    end = float(film.duration_s)
    if end <= last:
        return None
    # to the millisecond, rounded down: the silence starts at or before the last second the gate measures
    return {"from": math.floor((end - last) * 1000) / 1000, "to": round(end, 3),
            "why": "the film ends in silence: its last second, always"}
