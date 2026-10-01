"""Voice polish: a studio vocal chain on every take, so a generated voice sounds recorded, not rendered.

From the films' `vo_polish.py` and `polish_report.py` (2026-09-30). The owner heard a generated narrator as "a little too
harsh … you can tell it was generated" and asked for a de-esser, a lot of EQ and other local vocal effects, at a
strength between "barely noticeable" and "clearly warmer": a marked change that's still the same voice. Measuring first
found the harshness where it really lived (for that voice, the very top and the S sounds, not 2-5 kHz), and the chain
was tuned on those numbers. This is that chain, generalized:

    1. high-pass         rumble and low thump out (12 dB an octave)
    2. low-mid cleanup   a gentle cut where the voice measures boxy or honky
    3. compressor        moderate, on the take brought to a working loudness first, so the threshold means the same
                         thing on every line
    4. the edge          a dynamic cut on 2-5 kHz, only while a consonant (T, SH, CH) dominates the band
    5. de-esser          the same on the sibilance band (5-9 kHz by default), faster, for S and SH
    6. warmth            presence, a low shelf for body and a high shelf easing a glassy top
    7. room              a small synthetic room, far under the dry voice
    8. loudness          back to the take's own integrated loudness (BS.1770), so the mix balance holds; a take whose
                         true peak would pass the ceiling sits a hair lower instead

Every stage keeps the take's length sample for sample, so no word, cue or shot moves (`slate.voice.dsp`). Numpy and
scipy only: the films ran the EQ and compressor in a GPL-licensed plug-in host, which Slate doesn't depend on; the same
formulas here give the same output to float precision.

**Tunings are data.** A voice's tuning lives in its VoiceProfile (`polish` in brand.json "voice", or a film's own
override), with a neutral default. A tuning names its strengths: `points` are complete chains ("A", the subtler side of
a marked change; "B", the warmer side), and `strengths` places each on a 0-1 scale. A number between them blends
neighbouring points (0 is the untouched take). **The film pins what it used** (`voice_polish` in film.json pins: the
strength and the whole resolved chain), so a re-voice in week three gets exactly week one's polish; moving it is an
explicit re-pin with who approved it and why.

**Perfect, don't replace.** The polish writes `<take>.polished.wav` beside the take and never touches the raw, clean or
paced files. Voice measurements (breath, tags) keep reading the unpolished stages, because a polished baseline would
make the next round's new takes look harsher than the voice's own reads.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal

from slate import jsonio, log
from slate.errors import Refused, UsageError
from slate.voice import dsp

PIN_KEY = "voice_polish"
GENERATED = "do not edit by hand: slate voice polish"
WORK_LUFS = -20.0
PEAK_CEILING_DBTP = -1.0
SILENT_LUFS = -70.0

# ---------------------------------------------------------------------------------------------------------------
# Tunings

# A neutral voice's chain at its two named strengths. A brand's voice brings its own tuning measured on its own takes
# (where its harshness, boxiness and sibilance actually sit); these are sound starting points, not a voice's identity.
NEUTRAL_A: dict[str, Any] = {
    "highpass_hz": 80.0,
    "low_mid": {"hz": 300.0, "gain_db": -1.5, "q": 1.0},
    "compressor": {"threshold_db": -20.0, "ratio": 2.0, "attack_ms": 15.0, "release_ms": 150.0},
    "edge": {"band_hz": [2000.0, 5000.0], "threshold_db": -36.0, "relative_db": -12.0, "ratio": 2.5,
             "max_cut_db": 3.0, "attack_ms": 1.5, "release_ms": 50.0},
    "deess": {"band_hz": [5000.0, 9000.0], "threshold_db": -36.0, "relative_db": -10.0, "ratio": 3.0,
              "max_cut_db": 6.0, "attack_ms": 1.0, "release_ms": 60.0},
    "presence": {"hz": 3400.0, "gain_db": 0.0, "q": 0.9},
    "low_shelf": {"hz": 250.0, "gain_db": 1.5},
    "high_shelf": {"hz": 8000.0, "gain_db": -2.5},
    "room": {"wet_db": -22.0, "rt60_s": 0.28},
    "work_lufs": WORK_LUFS,
    "peak_ceiling_dbtp": PEAK_CEILING_DBTP,
}
NEUTRAL_B: dict[str, Any] = {
    **copy.deepcopy(NEUTRAL_A),
    "low_mid": {"hz": 300.0, "gain_db": -2.0, "q": 1.0},
    "compressor": {"threshold_db": -21.0, "ratio": 2.5, "attack_ms": 15.0, "release_ms": 150.0},
    "edge": {"band_hz": [2000.0, 5000.0], "threshold_db": -38.0, "relative_db": -12.0, "ratio": 3.0,
             "max_cut_db": 4.5, "attack_ms": 1.5, "release_ms": 50.0},
    "deess": {"band_hz": [5000.0, 9000.0], "threshold_db": -38.0, "relative_db": -10.0, "ratio": 4.0,
              "max_cut_db": 8.0, "attack_ms": 1.0, "release_ms": 60.0},
    "low_shelf": {"hz": 250.0, "gain_db": 3.0},
    "high_shelf": {"hz": 8000.0, "gain_db": -4.0},
    "room": {"wet_db": -19.0, "rt60_s": 0.30},
}

# What "verified by measurement" means for a polished take (a tuning's own "verify" block overrides any of these).
VERIFY: dict[str, float] = {
    "loudness_lu": 0.5,               # back to the take's own loudness, within this
    "max_spectral_distance_db": 6.0,  # RMS third-octave change: the films' strengths measured 2.5 to 3.7 dB
    "max_clarity_loss_db": 2.5,       # 1-5 kHz (where words stay clear in a mix): the films lost 0.7 to 1.4 dB
    "min_sibilance_cut_db": 0.3,      # when the de-esser acted, the S peaks come down at least this much
    "edge_rise_allowed_db": 1.0,      # presence may lift the 2-5 kHz peaks a little; more reads as harsher
    "headroom_margin_db": 3.0,        # a cut beyond the chain's own reach (plus this) is over-processing
    "true_peak_margin_db": 0.1,
}

NEUTRAL: dict[str, Any] = {
    "source": "neutral default",
    "required": False,
    "default_strength": "A",
    "strengths": {"A": 0.6, "B": 1.0},
    "points": {"A": NEUTRAL_A, "B": NEUTRAL_B},
    "verify": dict(VERIFY),
}

# Amounts and their untouched value: the 0 end of the strength scale (thresholds, times and frequencies don't move).
IDENTITY_AMOUNTS = {("low_mid", "gain_db"): 0.0, ("compressor", "ratio"): 1.0, ("edge", "ratio"): 1.0,
                    ("edge", "max_cut_db"): 0.0, ("deess", "ratio"): 1.0, ("deess", "max_cut_db"): 0.0,
                    ("presence", "gain_db"): 0.0, ("low_shelf", "gain_db"): 0.0, ("high_shelf", "gain_db"): 0.0,
                    ("room", "wet_db"): -120.0}
STAGES = ("highpass_hz", "low_mid", "compressor", "edge", "deess", "presence", "low_shelf", "high_shelf", "room")


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def complete(chain: dict, base: dict | None = None) -> dict:
    """A chain with every stage filled in (a tuning may give only what differs from the neutral chain)."""
    return _merge(base or NEUTRAL_A, chain or {})


def tuning_for(profile: dict | None) -> dict:
    """The voice's tuning: the profile's `polish` over the neutral default. Points are completed stage by stage."""
    own = (profile or {}).get("polish") or {}
    tuning = _merge({k: v for k, v in NEUTRAL.items() if k != "points"}, {k: v for k, v in own.items()
                                                                           if k != "points"})
    if own.get("points"):
        # A voice's own points come with their own scale: its strengths replace the neutral A and B outright.
        tuning["points"] = {name: complete(chain) for name, chain in own["points"].items()}
        tuning["strengths"] = dict(own.get("strengths") or {name: round((i + 1) / len(own["points"]), 3)
                                                            for i, name in enumerate(own["points"])})
        tuning["source"] = own.get("source") or "voice profile"
    else:
        tuning["points"] = copy.deepcopy(NEUTRAL["points"])
    missing = [n for n in tuning["strengths"] if n not in tuning["points"]]
    if missing:
        raise UsageError(f"The polish tuning places strengths it has no chain for: {', '.join(missing)}.",
                         hint="Every name in polish.strengths needs a chain in polish.points.")
    if tuning.get("default_strength") not in tuning["points"]:
        tuning["default_strength"] = sorted(tuning["strengths"], key=tuning["strengths"].get)[0]
    tuning["verify"] = {**VERIFY, **(own.get("verify") or {})}
    return tuning


def identity(chain: dict) -> dict:
    """The untouched end of the strength scale for this chain's shape: every amount at its no-change value."""
    out = copy.deepcopy(chain)
    for (stage, key), value in IDENTITY_AMOUNTS.items():
        if isinstance(out.get(stage), dict):
            out[stage][key] = value
    return out


def _mix(a: Any, b: Any, t: float, path: tuple) -> Any:
    if isinstance(a, dict):
        return {k: _mix(a[k], b.get(k, a[k]) if isinstance(b, dict) else a[k], t, path + (k,)) for k in a}
    if isinstance(a, list):
        return [_mix(x, y, t, path) for x, y in zip(a, b)]
    if not isinstance(a, (int, float)) or isinstance(a, bool) or not isinstance(b, (int, float)):
        return a if t < 0.5 else b
    key = path[-1] if path else ""
    if key in ("hz", "highpass_hz") or "band_hz" in path:
        if a > 0 and b > 0:
            return round(math.exp((1 - t) * math.log(a) + t * math.log(b)), 3)
    if key == "wet_db":
        ga, gb = dsp.db_to_gain(a), dsp.db_to_gain(b)
        return round(20 * math.log10(max((1 - t) * ga + t * gb, 1e-6)), 3)
    return round((1 - t) * a + t * b, 4)


def resolve(tuning: dict, strength: str | float | None = None) -> dict:
    """The chain for a strength: a named point exactly, or a number from 0 (untouched) to 1 blending neighbours.

    Returns {label, position, chain (None means untouched), between, t, clamped}."""
    points, places = tuning["points"], tuning["strengths"]
    label = tuning.get("default_strength") if strength is None or strength == "" else strength
    if isinstance(label, str) and label in points:
        return {"label": label, "position": places.get(label), "chain": copy.deepcopy(points[label]),
                "between": [label], "t": None, "clamped": False}
    try:
        s = float(label)
    except (TypeError, ValueError):
        raise UsageError(f"Polish strength {label!r}: one of {', '.join(points)}, or a number from 0 to 1.") from None
    if not 0.0 <= s <= 1.0 or math.isnan(s):
        raise UsageError(f"Polish strength {s:g} is outside 0 to 1 (0 leaves the take untouched).")
    if s == 0.0:
        return {"label": "0", "position": 0.0, "chain": None, "between": [], "t": None, "clamped": False}
    ordered = sorted((float(places[n]), n) for n in places if n in points)
    anchors = [(0.0, "untouched", identity(points[ordered[0][1]]))] + [(p, n, points[n]) for p, n in ordered]
    for p, n, chain in anchors[1:]:
        if abs(p - s) < 1e-9:
            return {"label": f"{s:g}", "position": s, "chain": copy.deepcopy(chain), "between": [n], "t": None,
                    "clamped": False}
    if s >= anchors[-1][0]:
        p, n, chain = anchors[-1]
        return {"label": f"{s:g}", "position": s, "chain": copy.deepcopy(chain), "between": [n], "t": None,
                "clamped": True}
    for (p0, n0, c0), (p1, n1, c1) in zip(anchors, anchors[1:]):
        if p0 <= s <= p1:
            t = (s - p0) / (p1 - p0)
            return {"label": f"{s:g}", "position": s, "chain": _mix(c0, c1, t, ()), "between": [n0, n1],
                    "t": round(t, 4), "clamped": False}
    raise AssertionError("unreachable: a strength between 0 and the last point always has two neighbours")


def chain_sha(chain: dict | None) -> str:
    return hashlib.sha256(json.dumps(chain, sort_keys=True).encode()).hexdigest()


def from_film_chain(film: dict) -> dict:
    """A chain written in the films' tuple form (`vo_polish.py` CHAINS, or a polished set's takes.json "chain") as a
    Slate chain, so a tuning measured on a film carries over unchanged."""
    def band(p: dict) -> dict:
        return {"band_hz": [float(p["band"][0]), float(p["band"][1])], "threshold_db": float(p["thresh_db"]),
                "relative_db": None if p.get("rel_db") is None else float(p["rel_db"]), "ratio": float(p["ratio"]),
                "max_cut_db": float(p["max_cut_db"]), "attack_ms": float(p["attack_ms"]),
                "release_ms": float(p["release_ms"])}

    f, g, q = film["eq_cut"]
    thr, ratio, att, rel = film["comp"]
    fp, gp, qp = film["presence"]
    fl, gl = film["low_shelf"]
    fh, gh = film["high_shelf"]
    return {"highpass_hz": float(film["hpf_hz"]), "low_mid": {"hz": float(f), "gain_db": float(g), "q": float(q)},
            "compressor": {"threshold_db": float(thr), "ratio": float(ratio), "attack_ms": float(att),
                           "release_ms": float(rel)},
            "edge": band(film["edge"]), "deess": band(film["deess"]),
            "presence": {"hz": float(fp), "gain_db": float(gp), "q": float(qp)},
            "low_shelf": {"hz": float(fl), "gain_db": float(gl)}, "high_shelf": {"hz": float(fh), "gain_db": float(gh)},
            "room": {"wet_db": float(film["room"]["wet_db"]), "rt60_s": float(film["room"]["rt60_s"])},
            "work_lufs": WORK_LUFS, "peak_ceiling_dbtp": PEAK_CEILING_DBTP}


# ---------------------------------------------------------------------------------------------------------------
# The chain


def _lufs(x: np.ndarray, sr: int) -> float:
    from slate.sound import measure as sound_measure  # noqa: PLC0415 - the one BS.1770 meter Slate's gates use

    return float(sound_measure.integrated(x, sr))


def _true_peak(x: np.ndarray, sr: int) -> float:
    from slate.sound import measure as sound_measure  # noqa: PLC0415

    return float(sound_measure.true_peak(x, sr))


def _channel(x: np.ndarray, sr: int, chain: dict, rep: dict) -> np.ndarray:
    """Stages 1-7 on one channel already at the working level."""
    y = dsp.highpass(x, sr, float(chain.get("highpass_hz") or 0.0))
    lm = chain["low_mid"]
    y = dsp.peak(y, sr, lm["hz"], lm["gain_db"], lm["q"])
    c = chain["compressor"]
    pre = y.copy()
    y = dsp.compressor(y, sr, c["threshold_db"], c["ratio"], c["attack_ms"], c["release_ms"])
    e_pre, e_post = dsp.env_db(pre, sr, 50.0), dsp.env_db(y, sr, 50.0)
    loud = e_pre > e_pre.max() - 20
    gr = (e_pre - e_post)[loud]
    rep["compressor_gr_db"] = {"median_loud": round(float(np.median(gr)), 2) if len(gr) else 0.0,
                               "p95": round(float(np.percentile(gr, 95)), 2) if len(gr) else 0.0}
    rep["edge"], rep["deess"] = {}, {}
    y = dsp.dynamic_band(y, sr, chain["edge"], rep["edge"])
    y = dsp.dynamic_band(y, sr, chain["deess"], rep["deess"])
    pr, ls, hs = chain["presence"], chain["low_shelf"], chain["high_shelf"]
    y = dsp.peak(y, sr, pr["hz"], pr["gain_db"], pr.get("q", 0.9))
    y = dsp.shelf(y, sr, ls["hz"], ls["gain_db"], high=False, q=ls.get("q", 0.707))
    y = dsp.shelf(y, sr, hs["hz"], hs["gain_db"], high=True, q=hs.get("q", 0.707))
    rm = chain["room"]
    return dsp.room(y, sr, rm["wet_db"], rm["rt60_s"])


def apply(x: np.ndarray, sr: int, chain: dict | None) -> tuple[np.ndarray, dict]:
    """The whole chain on a take ((n,) mono or (n, channels)). Returns the polished take, the same length and at the
    input's own integrated loudness, and what each stage did."""
    x = np.asarray(x, dtype=np.float64)
    rep: dict[str, Any] = {"samples": int(len(x)), "sample_rate": int(sr)}
    l_in = _lufs(x, sr)
    tp_in = _true_peak(x, sr)
    rep.update({"lufs_in": round(l_in, 2), "true_peak_in_dbtp": round(tp_in, 2)})
    if chain is None or l_in <= SILENT_LUFS:
        rep.update({"lufs_out": round(l_in, 2), "true_peak_out_dbtp": round(tp_in, 2), "peak_trim_db": 0.0,
                    "note": "left as it is: " + ("strength 0" if chain is None else "a silent take")})
        return x.copy(), rep
    work = float(chain.get("work_lufs", WORK_LUFS))
    g = dsp.db_to_gain(work - l_in)
    if x.ndim == 1:
        y = _channel(x * g, sr, chain, rep)
    else:
        cols = []
        for c in range(x.shape[1]):
            per: dict = {}
            cols.append(_channel(x[:, c] * g, sr, chain, per))
            if c == 0:
                rep.update(per)
        y = np.stack(cols, axis=1)
    l_mid = _lufs(y, sr)
    y = y * dsp.db_to_gain(l_in - l_mid)
    tp = _true_peak(y, sr)
    cap = max(float(chain.get("peak_ceiling_dbtp", PEAK_CEILING_DBTP)), tp_in)
    trim = min(0.0, cap - tp)
    y = y * dsp.db_to_gain(trim)
    rep.update({"lufs_out": round(_lufs(y, sr), 2), "true_peak_out_dbtp": round(_true_peak(y, sr), 2),
                "peak_trim_db": round(trim, 2)})
    if len(y) != len(x):  # pragma: no cover - every stage is length-preserving; this guards the promise
        raise AssertionError("the polish changed the take's length")
    return y, rep


# ---------------------------------------------------------------------------------------------------------------
# Measuring what it did (the films' polish_report, generalized to any rate)

AUDIBLE_DB = 60.0
THIRDS = (100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300,
          8000, 10000, 12500, 16000)
FIXED_BANDS = {"lows_80_300": (80, 300), "body_300_2k": (300, 2000), "clarity_1k_5k": (1000, 5000),
               "edge_2k_5k": (2000, 5000), "sib_5k_9k": (5000, 9000), "top_8k_12k5": (8000, 12500)}


def _stft_power(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, int]:
    nfft = int(2 ** round(math.log2(2048 * sr / 48000)))
    hop = max(1, sr // 100)
    nfft = max(nfft, hop + 1)
    f, _t, z = signal.stft(x, sr, window="hann", nperseg=nfft, noverlap=nfft - hop, boundary=None, padded=False)
    return f, np.abs(z) ** 2, hop


def _speech_frames(p: np.ndarray) -> np.ndarray:
    lv = 10 * np.log10(p.sum(axis=0) + 1e-20)
    return lv > np.percentile(lv, 95) - 35 if lv.size else np.zeros(0, bool)


def _voiced(x: np.ndarray, sr: int, n: int) -> np.ndarray:
    from slate.voice import audio as audio_mod  # noqa: PLC0415
    from slate.voice.measure import yin_f0  # noqa: PLC0415

    f0 = yin_f0(audio_mod.resample(x, sr, 16000), 16000, n=n)
    v = np.isfinite(f0)
    return np.pad(v, (0, max(0, n - len(v))))[:n]


def _band_db(power: np.ndarray, f: np.ndarray, frames: int, lo: float, hi: float) -> float:
    sel = (f >= lo) & (f < hi)
    if not sel.any() or frames <= 0:
        return -200.0
    return float(10 * np.log10(power[sel].sum() / frames + 1e-20))


def _peaks_p99(x: np.ndarray, sr: int, lo: float, hi: float, speech: np.ndarray, hop: int) -> float | None:
    b, _ = dsp.band_split(x, sr, lo, hi)
    e = dsp.env_db(b, sr, 5.0)[::hop][: len(speech)]
    sel = speech[: len(e)]
    return float(np.percentile(e[sel], 99)) if sel.any() else None


def _bands_for(chain: dict | None) -> dict[str, tuple[float, float]]:
    """The bands a report reads: the fixed ones, plus this chain's own edge, sibilance and low-mid bands."""
    c = chain or NEUTRAL_A
    lm = float(c["low_mid"]["hz"])
    return {**FIXED_BANDS, "edge": tuple(c["edge"]["band_hz"]), "sibilance": tuple(c["deess"]["band_hz"]),
            "low_mid": (lm / math.sqrt(2), lm * math.sqrt(2)), "tilt_low_50_1k": (50, 1000),
            "tilt_high_1k_5k": (1000, 5000), "warm_80_500": (80, 500)}


def pair(x: np.ndarray, y: np.ndarray, sr: int, chain: dict | None = None) -> dict:
    """What a polish did to one take, both sides brought to the working loudness by the same gain (y is at x's own
    loudness, as delivered): power per band over x's speech frames, vowels (voiced frames) and consonants apart, and
    the loudest moments (the 99th percentile of 5 ms levels) of the edge and sibilance bands."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.ndim > 1:
        x, y = x.mean(axis=1), y.mean(axis=1)
    l_in = _lufs(x, sr)
    g = dsp.db_to_gain(WORK_LUFS - l_in) if l_in > SILENT_LUFS else 1.0
    x, y = x * g, y * g
    f, px, hop = _stft_power(x, sr)
    _, py, _ = _stft_power(y, sr)
    n = px.shape[1]
    sp = _speech_frames(px)
    v = _voiced(x, sr, n)
    out: dict[str, Any] = {"freqs": f, "frames": int(sp.sum()), "voiced_frames": int((sp & v).sum()),
                           "unvoiced_frames": int((sp & ~v).sum()), "sample_rate": sr}
    for name, p in (("x", px), ("y", py)):
        out[name] = {"all": p[:, sp].sum(axis=1), "voiced": p[:, sp & v].sum(axis=1),
                     "unvoiced": p[:, sp & ~v].sum(axis=1)}
    c = chain or NEUTRAL_A
    for name, z in (("x", x), ("y", y)):
        out[f"{name}_sib_p99"] = _peaks_p99(z, sr, *c["deess"]["band_hz"], sp, hop)
        out[f"{name}_edge_p99"] = _peaks_p99(z, sr, *c["edge"]["band_hz"], sp, hop)
    out["chain"] = chain
    return out


def summarize_pairs(pairs: list[dict]) -> dict:
    """Pool takes' pair() measures into the numbers a report quotes: before, after and change (dB; negative means the
    polish removed energy there)."""
    if not pairs:
        return {}
    f = pairs[0]["freqs"]
    chain = pairs[0].get("chain")
    nfr = {k: sum(p[{"all": "frames", "voiced": "voiced_frames", "unvoiced": "unvoiced_frames"}[k]] for p in pairs)
           for k in ("all", "voiced", "unvoiced")}
    tot = {nm: {k: sum(p[nm][k] for p in pairs) for k in ("all", "voiced", "unvoiced")} for nm in ("x", "y")}

    def lvl(nm: str, kind: str, lo: float, hi: float) -> float:
        return _band_db(tot[nm][kind], f, nfr[kind], lo, hi)

    bands = _bands_for(chain)
    # A band the take barely uses (more than AUDIBLE_DB under its loudest third-octave) can't be heard against the
    # rest, and a few stray dB of room or noise there would read as a huge "change". Such bands are left out of every
    # comparison and listed as empty (a narrowband recording, or a test voice with nothing up top).
    third_before = {fc: lvl("x", "all", fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)) for fc in THIRDS[:-1]
                    if fc * 2 ** (1 / 6) <= f[-1]}
    floor = (max(third_before.values()) - AUDIBLE_DB) if third_before else -200.0
    before = {k: round(lvl("x", "all", lo, hi), 2) for k, (lo, hi) in bands.items()}
    after = {k: round(lvl("y", "all", lo, hi), 2) for k, (lo, hi) in bands.items()}
    empty = sorted(k for k, v in before.items() if v < floor)
    for side, src in ((before, "x"), (after, "y")):
        side["tilt_db"] = round(side["tilt_low_50_1k"] - side["tilt_high_1k_5k"], 2)
        side["warmth_db"] = round(side["warm_80_500"] - side["clarity_1k_5k"], 2)
        sib = [p[f"{src}_sib_p99"] for p in pairs if p.get(f"{src}_sib_p99") is not None]
        edge = [p[f"{src}_edge_p99"] for p in pairs if p.get(f"{src}_edge_p99") is not None]
        side["sibilance_peaks_db"] = round(float(np.mean(sib)), 2) if sib and "sibilance" not in empty else None
        side["edge_peaks_db"] = round(float(np.mean(edge)), 2) if edge and "edge" not in empty else None
    if "tilt_high_1k_5k" in empty or "tilt_low_50_1k" in empty:
        before["tilt_db"] = after["tilt_db"] = None
    if "clarity_1k_5k" in empty or "warm_80_500" in empty:
        before["warmth_db"] = after["warmth_db"] = None
    change = {k: (round(after[k] - before[k], 2) if after.get(k) is not None and before.get(k) is not None
                  and k not in empty else None) for k in before}
    sib_lo, sib_hi = bands["sibilance"]
    thirds = {}
    for fc, b_db in third_before.items():
        if b_db >= floor:
            thirds[str(fc)] = round(lvl("y", "all", fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)) - b_db, 2)
    in_range = [v for fc, v in thirds.items() if 100 <= int(fc) <= 12500]
    return {"before": before, "after": after, "change": change, "empty_bands": empty,
            "vowels_top_8k_12k5_change_db": round(lvl("y", "voiced", 8000, 12500) - lvl("x", "voiced", 8000, 12500), 2),
            "consonants_sibilance_change_db": round(lvl("y", "unvoiced", sib_lo, sib_hi)
                                                    - lvl("x", "unvoiced", sib_lo, sib_hi), 2),
            "thirds_change_db": thirds,
            "spectral_distance_db": round(float(np.sqrt(np.mean(np.square(in_range)))), 2) if in_range else 0.0,
            "clarity_1k_5k_removed_db": None if change.get("clarity_1k_5k") is None
            else round(-change["clarity_1k_5k"], 2),
            "frames": nfr["all"]}


def compare_outputs(x: np.ndarray, a: np.ndarray, b: np.ndarray, sr: int, chain: dict | None = None) -> dict:
    """How close two polishes of the same take are (port parity): b's band levels against a's, both brought to the
    working loudness by x's gain, over x's speech frames; the third-octave differences and their RMS (the spectral
    distance); and the sample-domain difference against a's level."""
    pa, pb = pair(x, a, sr, chain), pair(x, b, sr, chain)
    f = pa["freqs"]
    n = pa["frames"]
    bands = _bands_for(chain)
    diff = {k: round(_band_db(pb["y"]["all"], f, n, lo, hi) - _band_db(pa["y"]["all"], f, n, lo, hi), 3)
            for k, (lo, hi) in bands.items()}
    thirds = {}
    for fc in THIRDS[:-1]:
        lo, hi = fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)
        if hi <= f[-1]:
            thirds[str(fc)] = round(_band_db(pb["y"]["all"], f, n, lo, hi) - _band_db(pa["y"]["all"], f, n, lo, hi), 3)
    in_range = [v for fc, v in thirds.items() if 100 <= int(fc) <= 12500]
    a64, b64 = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    m = min(len(a64), len(b64))
    resid = b64[:m] - a64[:m]
    ref = float(np.sqrt(np.mean(a64[:m] ** 2))) or 1e-12
    corr = float(np.corrcoef(a64[:m].ravel(), b64[:m].ravel())[0, 1]) if m > 1 else 1.0
    return {"bands_diff_db": diff, "thirds_diff_db": thirds,
            "spectral_distance_db": round(float(np.sqrt(np.mean(np.square(in_range)))), 3) if in_range else 0.0,
            "peaks_diff_db": {"sibilance": None if pa["y_sib_p99"] is None else round(pb["y_sib_p99"] - pa["y_sib_p99"], 3),
                              "edge": None if pa["y_edge_p99"] is None else round(pb["y_edge_p99"] - pa["y_edge_p99"], 3)},
            "residual_db": round(20 * math.log10(float(np.sqrt(np.mean(resid ** 2))) / ref + 1e-12), 2),
            "correlation": round(corr, 6), "samples": [len(a64), len(b64)]}


# ---------------------------------------------------------------------------------------------------------------
# Verification (the polish gate's rules, per take)


def verify(rep: dict, effect: dict, chain: dict | None, rules: dict | None = None) -> dict:
    """Check one polished take by measurement. Returns {ok, problems, checks}: length identical, loudness back within
    0.5 LU, true peak under the ceiling, sibilance and edge reduced within the chain's reach, not over-processed."""
    r = {**VERIFY, **(rules or {})}
    problems: list[str] = []
    checks: dict[str, Any] = {}
    checks["length"] = rep.get("samples_out") == rep.get("samples")
    if not checks["length"]:
        problems.append(f"the length changed ({rep.get('samples')} samples in, {rep.get('samples_out')} out)")
    d_lufs = None
    if rep.get("lufs_in") is not None and rep.get("lufs_out") is not None:
        d_lufs = round(float(rep["lufs_out"]) - float(rep["lufs_in"]), 2)
    checks["loudness_change_lu"] = d_lufs
    if d_lufs is None or abs(d_lufs) > r["loudness_lu"] + 1e-9:
        problems.append(f"loudness moved {d_lufs:+.2f} LU (within {r['loudness_lu']:g} LU of the take is the rule)"
                        if d_lufs is not None else "loudness couldn't be measured")
    cap = max(float((chain or {}).get("peak_ceiling_dbtp", PEAK_CEILING_DBTP)), float(rep.get("true_peak_in_dbtp", -120)))
    tp = float(rep.get("true_peak_out_dbtp", -120))
    checks["true_peak_ok"] = tp <= cap + r["true_peak_margin_db"]
    if not checks["true_peak_ok"]:
        problems.append(f"true peak {tp:+.2f} dBTP is over the ceiling ({cap:+.2f})")
    if chain is None:
        checks["note"] = "strength 0: the take is untouched, so only length and loudness are checked"
        return {"ok": not problems, "problems": problems, "checks": checks}
    change = effect.get("change") or {}
    d_sib = change.get("sibilance_peaks_db")
    d_edge = change.get("edge_peaks_db")
    shelf_cut = max(0.0, -float(chain["high_shelf"]["gain_db"]))
    sib_reach = float(chain["deess"]["max_cut_db"]) + shelf_cut + r["headroom_margin_db"]
    edge_reach = float(chain["edge"]["max_cut_db"]) + max(0.0, -float(chain["presence"]["gain_db"])) \
        + r["headroom_margin_db"]
    deessed = float((rep.get("deess") or {}).get("time_cutting_pct") or 0.0) > 0.0
    checks.update({"sibilance_peaks_change_db": d_sib, "edge_peaks_change_db": d_edge, "deesser_acted": deessed,
                   "sibilance_reach_db": round(sib_reach, 2), "edge_reach_db": round(edge_reach, 2)})
    if d_sib is not None:
        if deessed and d_sib > -r["min_sibilance_cut_db"]:
            problems.append(f"the de-esser acted, but the S peaks moved only {d_sib:+.2f} dB")
        if d_sib < -sib_reach:
            problems.append(f"the S peaks came down {-d_sib:.1f} dB, more than this chain can do "
                            f"({sib_reach:.1f}): over-processed")
        if not deessed and d_sib > r["edge_rise_allowed_db"]:
            problems.append(f"the S peaks rose {d_sib:+.2f} dB")
    if d_edge is not None:
        if d_edge > r["edge_rise_allowed_db"]:
            problems.append(f"the 2-5 kHz peaks rose {d_edge:+.2f} dB: harsher, not smoother")
        if d_edge < -edge_reach:
            problems.append(f"the 2-5 kHz peaks came down {-d_edge:.1f} dB, more than this chain can do "
                            f"({edge_reach:.1f}): over-processed")
    dist = effect.get("spectral_distance_db")
    clarity = effect.get("clarity_1k_5k_removed_db")
    checks.update({"spectral_distance_db": dist, "clarity_1k_5k_removed_db": clarity})
    if dist is not None and dist > r["max_spectral_distance_db"]:
        problems.append(f"the overall spectral change is {dist:.1f} dB (at most {r['max_spectral_distance_db']:g}): "
                        "so different it may not be the same voice")
    if clarity is not None and clarity > r["max_clarity_loss_db"]:
        problems.append(f"{clarity:.1f} dB of 1-5 kHz removed (at most {r['max_clarity_loss_db']:g}): words would "
                        "lose clarity in the mix")
    return {"ok": not problems, "problems": problems, "checks": checks}


# ---------------------------------------------------------------------------------------------------------------
# Files


def polish_file(src: str | Path, dst: str | Path, chain: dict | None, *, rules: dict | None = None) -> dict:
    """Polish one WAV into another at its own rate (24-bit), and measure it. Returns the take's report."""
    from slate.voice import audio as audio_mod  # noqa: PLC0415

    x, sr = audio_mod.read_wav(src)
    y, rep = apply(x, sr, chain)
    if np.max(np.abs(y)) >= 1.0:
        raise Refused(f"{Path(src).name} would clip after the polish (peak {np.max(np.abs(y)):.3f}).",
                      hint="Lower the take's level or the chain's working loudness, then run it again.")
    audio_mod.write_wav(dst, y, sr, 24)
    rep["samples_out"] = int(len(y))
    effect = summarize_pairs([pair(x, y, sr, chain)])
    return {"source": str(src), "file": str(dst), "chain": chain, "stages": rep, "effect": effect,
            "verified": verify(rep, effect, chain, rules)}


def _now() -> str:
    from slate.voice.film import now  # noqa: PLC0415

    return now()


def _label_dir(label: str) -> str:
    return label if label.isidentifier() else "s" + label.replace(".", "p")


def pin_value(res: dict, tuning: dict) -> dict:
    return {"strength": res["label"], "position": res["position"], "between": res.get("between"),
            "chain": res["chain"], "chain_sha256": chain_sha(res["chain"]), "tuning": tuning.get("source")}


def settings(vf, strength: str | float | None, *, repin: bool = False, approved_by: str | None = None,
             reason: str | None = None, pin_it: bool = True) -> tuple[dict, str]:
    """The polish this film uses: its pin when it has one, else the requested strength (pinned now, first use wins).
    Returns (pin value, what happened)."""
    from slate.voice.film import get_pin, pin  # noqa: PLC0415

    tuning = tuning_for(vf.profile)
    pinned = get_pin(vf.dir, PIN_KEY)
    if pinned and strength in (None, "") and not repin:
        return pinned, "kept the film's pinned polish"
    want = pin_value(resolve(tuning, strength), tuning)
    if not pinned:
        if not pin_it:
            return want, "not pinned (a trial)"
        return pin(vf.dir, PIN_KEY, want, source="slate voice polish (first use)"), f"pinned strength {want['strength']}"
    if pinned.get("chain_sha256") == want["chain_sha256"]:
        return pinned, "kept the film's pinned polish"
    if repin:
        if not ((approved_by or "").strip() and (reason or "").strip()):
            raise Refused("Re-pinning the film's polish changes how every take sounds, so it needs the person's yes "
                          "and the reason.", hint="Try the strengths first (slate voice polish --try A,B), then pass "
                                                  "--repin --approved-by <name> --reason \"<why>\".")
        return (pin(vf.dir, PIN_KEY, want, repin=True, approved_by=approved_by, reason=reason,
                    source="slate voice polish --repin"), f"re-pinned strength {want['strength']}")
    log.warn(f"this film's polish is pinned at strength {pinned.get('strength')}; keeping it so every take matches "
             f"(--repin --approved-by <name> --reason <why> switches to {want['strength']})")
    return pinned, "kept the film's pinned polish"


def run(film_dir: str | Path, *, only: str | list[str] | None = None, strength: str | float | None = None,
        repin: bool = False, approved_by: str | None = None, reason: str | None = None, force: bool = False,
        approval_id: str | None = None, dry_run: bool = False) -> dict:
    """Polish every line's take (or `only` some) with the film's pinned chain into <take>.polished.wav."""
    from slate.voice.film import VoiceFilm, guard_regenerate, is_locked, record  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    lines = vf.select(only)
    tuning = tuning_for(vf.profile)
    if dry_run:
        from slate.voice.film import get_pin  # noqa: PLC0415

        use = get_pin(vf.dir, PIN_KEY) if strength in (None, "") else None
        use = use or pin_value(resolve(tuning, strength), tuning)
        todo = [ln.shot for ln in lines if vf.selected(ln.shot)]
        return {"dry_run": True, "film": vf.id, "would_polish": todo, "strength": use["strength"],
                "chain": use["chain"], "pinned": get_pin(vf.dir, PIN_KEY) is not None}
    use, note = settings(vf, strength, repin=repin, approved_by=approved_by, reason=reason)
    chain = use["chain"]
    results, skipped, pairs = [], [], []
    from slate.voice import audio as audio_mod  # noqa: PLC0415

    for ln in lines:
        sel = vf.selected(ln.shot)
        if not sel:
            skipped.append({"shot": ln.shot, "why": "not voiced yet"})
            continue
        take, info = sel
        src = vf.unpolished_audio(ln.shot)
        if src is None:
            skipped.append({"shot": ln.shot, "why": "its take's audio is missing"})
            continue
        out = src.with_name(f"{take}.polished.wav")
        rel_out = vf.rel(out)
        if out.exists() and is_locked(vf.dir, rel_out):
            guard_regenerate(vf.dir, rel_out, force=force, approval_id=approval_id)
        tj = vf.take_json(ln.shot, take)
        rep = polish_file(src, out, chain, rules=tuning.get("verify"))
        x, sr = audio_mod.read_wav(src)
        y, _ = audio_mod.read_wav(out)
        pairs.append(pair(x, y, sr, chain))
        report = {"generated": GENERATED, "film": vf.id, "shot": ln.shot, "take": take,
                  "source": vf.rel(src), "source_stage": vf.final_stage(ln.shot, polished=False),
                  "source_sha256": jsonio.sha256(src), "file": rel_out, "sha256": jsonio.sha256(out),
                  "strength": use["strength"], "position": use.get("position"), "chain": chain,
                  "chain_sha256": use["chain_sha256"], "replay": bool(tj.get("replay")), "stages": rep["stages"],
                  "effect": rep["effect"], "verified": rep["verified"], "at": _now()}
        problems = jsonio.validate(report, "voice_polish_report")
        if problems:  # pragma: no cover - a bug in this module, not in the film
            raise UsageError("A polish report doesn't match its schema: " + "; ".join(problems[:5]))
        report_path = jsonio.write_atomic(vf.take_dir(ln.shot) / f"{take}.polish.json", report)
        tj.setdefault("stages", {})["polish"] = {
            "file": rel_out, "source": vf.rel(src), "source_stage": report["source_stage"],
            "source_sha256": report["source_sha256"], "sha256": report["sha256"], "strength": use["strength"],
            "chain_sha256": use["chain_sha256"], "report": vf.rel(report_path), "verified": rep["verified"]["ok"],
            "at": report["at"]}
        vf.save_take_json(ln.shot, take, tj)
        vf.update_take(ln.shot, take, polished=rel_out, align=None)
        raw_rel = info.get("raw")
        inputs = {vf.rel(src): report["source_sha256"], f"film:pins.{PIN_KEY}": None}
        if raw_rel and raw_rel != vf.rel(src) and (vf.dir / raw_rel).is_file():
            inputs[raw_rel] = jsonio.sha256(vf.dir / raw_rel)
        record(vf.dir, rel_out, producer="slate voice polish", inputs=inputs,
               note=f"polished from {raw_rel or vf.rel(src)} (strength {use['strength']}); the raw take is unchanged")
        record(vf.dir, vf.rel(report_path), producer="slate voice polish", inputs={rel_out: report["sha256"]})
        st = rep["stages"]
        results.append({"shot": ln.shot, "take": take, "file": rel_out, "source": vf.rel(src),
                        "lufs_in": st["lufs_in"], "lufs_out": st["lufs_out"], "true_peak_out_dbtp": st["true_peak_out_dbtp"],
                        "peak_trim_db": st["peak_trim_db"], "compressor_gr_db": st.get("compressor_gr_db"),
                        "edge_max_cut_db": (st.get("edge") or {}).get("max_cut_db"),
                        "deess_max_cut_db": (st.get("deess") or {}).get("max_cut_db"),
                        "effect": {k: rep["effect"].get(k) for k in ("change", "spectral_distance_db",
                                                                       "clarity_1k_5k_removed_db")},
                        "verified": rep["verified"], "replay": report["replay"]})
    summary = summarize_pairs(pairs) if pairs else {}
    if pairs:
        jsonio.write_atomic(vf.dir / "voice" / "polish" / "summary.json",
                            {"generated": GENERATED, "film": vf.id, "strength": use["strength"], "chain": chain,
                             "lines": [r["shot"] for r in results], "effect": summary, "at": _now()})
    return {"film": vf.id, "strength": use["strength"], "pin": note, "chain": chain, "polished": results,
            "skipped": skipped, "effect": summary,
            "next": "slate voice align (Whisper hears the polished takes), then slate voice check"}


def trial(film_dir: str | Path, strengths: list[str], *, only: str | list[str] | None = None,
          sheet: bool = False) -> dict:
    """Polish every line at several strengths for listening, without pinning or registering anything: the unpolished
    take and each strength level-matched in a reel, every number in trial.json, and A/B sheets on request."""
    from slate.voice import reel  # noqa: PLC0415
    from slate.voice import audio as audio_mod  # noqa: PLC0415
    from slate.voice.film import VoiceFilm, record  # noqa: PLC0415

    vf = VoiceFilm(film_dir)
    tuning = tuning_for(vf.profile)
    if not strengths:
        raise UsageError("Name the strengths to try, e.g. --try A,B or --try A,0.8.")
    resolved = [resolve(tuning, s) for s in strengths]
    folder = vf.dir / "voice" / "polish" / "try"
    per_strength: dict[str, dict] = {}
    lines_out: dict[str, dict] = {}
    reel_items: list[dict] = []
    sheets: list[str] = []
    for ln in vf.select(only):
        src = vf.unpolished_audio(ln.shot)
        if src is None:
            continue
        x, sr = audio_mod.read_wav(src)
        row = {"source": vf.rel(src), "strengths": {}}
        reel_items.append({"file": src, "title": f"{ln.shot} unpolished"})
        outs = []
        for res in resolved:
            out = folder / _label_dir(res["label"]) / f"{ln.shot}.wav"
            rep = polish_file(src, out, res["chain"], rules=tuning.get("verify"))
            y, _ = audio_mod.read_wav(out)
            per_strength.setdefault(res["label"], {"pairs": [], "chain": res["chain"], "position": res["position"],
                                                    "between": res.get("between")})["pairs"].append(
                pair(x, y, sr, res["chain"]))
            row["strengths"][res["label"]] = {"file": vf.rel(out), "stages": rep["stages"], "effect": rep["effect"],
                                              "verified": rep["verified"]}
            record(vf.dir, vf.rel(out), producer="slate voice polish --try", inputs={vf.rel(src): jsonio.sha256(src)},
                   note=f"a trial at strength {res['label']}; nothing pinned")
            reel_items.append({"file": out, "title": f"{ln.shot} {res['label']}"})
            outs.append((res["label"], out))
        lines_out[ln.shot] = row
        if sheet:
            from slate.voice import sheet as sheet_mod  # noqa: PLC0415

            png = sheet_mod.compare_files([("unpolished", src)] + outs, folder / "sheets" / f"{ln.shot}.png",
                                          title=f"{ln.shot}: the polish at {', '.join(r['label'] for r in resolved)}",
                                          text=ln.text, breath=False)
            sheets.append(vf.rel(png))
    if not lines_out:
        raise UsageError("No voiced takes to try the polish on.", hint="slate voice tts, clean and pace first.")
    reel_info = reel.build(reel_items, folder / "reel.wav", gap_s=0.8)
    pooled = {label: {"position": v["position"], "between": v["between"], "chain": v["chain"],
                      "effect": summarize_pairs(v["pairs"])} for label, v in per_strength.items()}
    out = {"generated": "do not edit by hand: slate voice polish --try", "film": vf.id, "tuning": tuning.get("source"),
           "strengths": pooled, "lines": lines_out, "reel": vf.rel(Path(reel_info["reel"])), "sheets": sheets,
           "at": _now(), "note": "Trials only: nothing is pinned or registered. Listen to reel.wav (level-matched), "
                                 "then polish with the strength you pick (slate voice polish --strength <it>)."}
    jsonio.write_atomic(folder / "trial.json", out)
    return out


def describe(res: dict) -> str:
    """One readable block for people: what each polished take measured."""
    rows = []
    for r in res.get("polished") or []:
        ch = (r.get("effect") or {}).get("change") or {}
        v = r.get("verified") or {}
        rows.append(f"{r['shot']} {r['take']}: {r['lufs_in']} -> {r['lufs_out']} LUFS, true peak "
                    f"{r['true_peak_out_dbtp']} dBTP; S peaks {_fmt(ch.get('sibilance_peaks_db'))}, edge peaks "
                    f"{_fmt(ch.get('edge_peaks_db'))}, tilt {_fmt(ch.get('tilt_db'))}, low-mid {_fmt(ch.get('low_mid'))}; "
                    + ("verified" if v.get("ok") else "CHECK: " + "; ".join(v.get("problems") or [])))
    return "\n".join(rows)


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f} dB"
