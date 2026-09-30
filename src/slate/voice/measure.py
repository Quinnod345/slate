"""Measured proxies for a read: pace, pauses, loudness, pitch, brightness, noise floor, and what Whisper heard.

Ported from the films' `measure_previews.py` (the casting and Voice Design measurements) with the brand words
generalized into a lexicon and every language rule read from the language table.

These are proxies. Nothing here can hear warmth, sincerity or a smile; it can only tell whether a read is rushed
or dragging, monotone or sing-song, clean or noisy, and whether Whisper heard the words that were written. The
owner still picks by ear. Everything is numpy and scipy; the audio is read with `slate.voice.audio`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import numpy as np

from slate.voice import audio as audio_mod
from slate.voice import lang

HOP = 0.010


# ---------------------------------------------------------------------------------------------------------------
# Frames and activity


def frame_rms_db(x: np.ndarray, sr: int, win: float = 0.025, hop: float = HOP) -> tuple[np.ndarray, int]:
    w, h = int(sr * win), int(sr * hop)
    if len(x) < w:
        x = np.concatenate([x, np.zeros(w - len(x))])
    n = 1 + max(0, (len(x) - w) // h)
    idx = np.arange(w)[None, :] + h * np.arange(n)[:, None]
    rms = np.sqrt(np.mean(x[idx] ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-12), h


def runs(mask: np.ndarray, value: bool = True) -> list[tuple[int, int]]:
    out, i, n = [], 0, len(mask)
    while i < n:
        if bool(mask[i]) == value:
            j = i
            while j < n and bool(mask[j]) == value:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def speech_activity(db: np.ndarray, hop_s: float, rel: float = 38.0, floor: float = -55.0) -> dict | None:
    """Energy VAD: speech is within `rel` dB of the loud frames and above `floor` dBFS; tiny gaps (< 80 ms) inside
    words are closed and tiny blips (< 50 ms) dropped. None when there's no speech at all."""
    if not len(db):
        return None
    thr = max(floor, float(np.percentile(db, 95)) - rel)
    act = db > thr
    for a, b in runs(act, False):
        if (b - a) * hop_s < 0.08 and a > 0 and b < len(act):
            act[a:b] = True
    for a, b in runs(act, True):
        if (b - a) * hop_s < 0.05:
            act[a:b] = False
    on = np.flatnonzero(act)
    if len(on) == 0:
        return None
    start, end = int(on[0]), int(on[-1]) + 1
    pauses = [((a + start) * hop_s, (b + start) * hop_s) for a, b in runs(act[start:end], False)]
    return {"thr_db": thr, "start_s": start * hop_s, "end_s": end * hop_s, "pauses": pauses, "active": act}


# ---------------------------------------------------------------------------------------------------------------
# Pitch


def yin_f0(x: np.ndarray, sr: int = 16000, fmin: float = 70.0, fmax: float = 500.0, win: float = 0.040,
           hop: float = HOP, thresh: float = 0.15, n: int | None = None) -> np.ndarray:
    """YIN pitch (de Cheveigné and Kawahara 2002), one estimate per hop; NaN where no periodic voice is found."""
    W, H = int(sr * win), int(sr * hop)
    tmin, tmax = int(sr / fmax), int(sr / fmin)
    count = 1 + max(0, (len(x) - W - tmax) // H)
    total = n if n is not None else count
    count = min(count, total)
    f0 = np.full(total, np.nan)
    if len(x) < W + tmax:
        return f0
    nfft = 1 << int(math.ceil(math.log2(2 * W + tmax)))
    taus = np.arange(tmax + 1)
    for i in range(count):
        seg = x[i * H: i * H + W + tmax]
        a = seg[:W]
        r = np.fft.irfft(np.conj(np.fft.rfft(a, nfft)) * np.fft.rfft(seg, nfft), nfft)[: tmax + 1]
        sq = np.concatenate([[0.0], np.cumsum(seg ** 2)])
        d = sq[W] + (sq[taus + W] - sq[taus]) - 2 * r
        d[0] = 0.0
        cm = np.cumsum(d[1:])
        dn = np.ones_like(d)
        dn[1:] = d[1:] * np.arange(1, tmax + 1) / np.maximum(cm, 1e-12)
        cand = np.flatnonzero(dn[tmin:] < thresh)
        if len(cand) == 0:
            continue
        t = int(cand[0]) + tmin
        while t + 1 <= tmax and dn[t + 1] < dn[t]:
            t += 1
        tt = float(t)
        if 1 <= t < tmax:
            y0, y1, y2 = dn[t - 1], dn[t], dn[t + 1]
            den = y0 - 2 * y1 + y2
            if abs(den) > 1e-12:
                tt = t + max(-1.0, min(1.0, 0.5 * (y0 - y2) / den))
        f0[i] = sr / tt
    return f0


def clean_f0(f0: np.ndarray, st: float = 7.0) -> np.ndarray:
    """Drop octave jumps: frames more than `st` semitones off a 9-frame running median of voiced frames."""
    out = f0.copy()
    for k in np.flatnonzero(~np.isnan(f0)):
        w = f0[max(0, k - 4): k + 5]
        w = w[~np.isnan(w)]
        if len(w) >= 3 and 12 * abs(math.log2(f0[k] / np.median(w))) > st:
            out[k] = np.nan
    return out


def spectral_stats(x: np.ndarray, sr: int, active: np.ndarray, hop_s: float) -> tuple[float | None, float | None]:
    """Median spectral centroid and 4-10 kHz energy share over speech frames (brightness and crispness proxies)."""
    W, H = int(0.025 * sr), int(hop_s * sr)
    n = min(len(active), 1 + max(0, (len(x) - W) // H))
    win = np.hanning(W)
    freqs = np.fft.rfftfreq(W, 1 / sr)
    band = (freqs >= 4000) & (freqs <= 10000)
    cents, hi = [], []
    for i in range(n):
        if not active[i]:
            continue
        s = np.abs(np.fft.rfft(x[i * H: i * H + W] * win)) ** 2
        tot = s.sum()
        if tot <= 1e-10:
            continue
        cents.append(float((freqs * s).sum() / tot))
        hi.append(float(s[band].sum() / tot))
    if not cents:
        return None, None
    return float(np.median(cents)), float(np.median(hi))


# ---------------------------------------------------------------------------------------------------------------
# Words


def whisper_words(data: dict | None) -> list[dict]:
    """Flatten Whisper-shaped JSON (segments -> words) into [{word, start, end, probability?}]."""
    if not data:
        return []
    out = []
    for seg in data.get("segments") or []:
        for w in seg.get("words") or []:
            out.append({"word": str(w.get("word", "")).strip(), "start": float(w.get("start", 0.0)),
                        "end": float(w.get("end", 0.0)), **({"probability": w["probability"]}
                                                          if "probability" in w else {})})
    if not out and data.get("words"):
        out = [{"word": str(w.get("word") or w.get("text") or "").strip(), "start": float(w["start"]),
                "end": float(w["end"])} for w in data["words"]]
    return out


def align_words(ref: list[str], hyp: list[str]) -> tuple[int, list[tuple[str, str | None, str | None]],
                                                          list[tuple[int, int]]]:
    """Levenshtein alignment: (edits, ops [(op, ref word, heard word)], pairs [(ref index, hyp index)])."""
    n, m = len(ref), len(hyp)
    D = np.zeros((n + 1, m + 1), dtype=int)
    D[:, 0], D[0, :] = np.arange(n + 1), np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = min(D[i - 1, j] + 1, D[i, j - 1] + 1, D[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, ops, pairs = n, m, [], []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i, j] == D[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] != hyp[j - 1]:
                ops.append(("sub", ref[i - 1], hyp[j - 1]))
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and D[i, j] == D[i - 1, j] + 1:
            ops.append(("del", ref[i - 1], None))
            i -= 1
        else:
            ops.append(("ins", None, hyp[j - 1]))
            j -= 1
    return int(D[n, m]), ops[::-1], pairs[::-1]


def repeats(ref: list[str], hyp: list[str]) -> list[str]:
    """Word runs heard more often than the script has them (v4's occasional hallucinated repeat)."""
    out: set[str] = set()
    for n in (3, 2):
        def grams(ws: list[str]) -> dict:
            g: dict = {}
            for k in range(len(ws) - n + 1):
                g[tuple(ws[k:k + n])] = g.get(tuple(ws[k:k + n]), 0) + 1
            return g
        gr, gh = grams(ref), grams(hyp)
        out |= {" ".join(g) for g, c in gh.items() if c > gr.get(g, 0)}
    return sorted(out)


def lexicon_heard(script: str, heard_text: str, lexicon: Iterable[str]) -> dict[str, bool]:
    """For every lexicon entry in the script line: was it heard, in full, as one run of words?"""
    ref = lang.norm_words(script)
    hyp = lang.norm_words(heard_text)
    out = {}
    for entry in lexicon or ():
        words = lang.norm_words(entry)
        if not words:
            continue
        in_script = any(ref[k:k + len(words)] == words for k in range(len(ref) - len(words) + 1))
        if not in_script:
            continue
        out[entry] = any(hyp[k:k + len(words)] == words for k in range(len(hyp) - len(words) + 1))
    return out


def word_check(script: str, heard: list[dict] | str, *, lexicon: Iterable[str] = (), tagged: str | None = None,
               equivalent=None) -> dict:
    """What was heard against what was written: WER, the errors, lexicon words, repeats and tags spoken aloud.

    `equivalent(ref_tokens, hyp_tokens)` may rewrite both token lists first (spoken numbers: "2,500" said as
    "twenty-five hundred"); by default tokens must match after normalization.
    """
    from slate.voice import tags as tags_mod  # noqa: PLC0415

    heard_text = heard if isinstance(heard, str) else " ".join(w["word"] for w in heard)
    ref = lang.norm_words(script)
    hyp = lang.norm_words(heard_text)
    if equivalent is not None:
        ref, hyp = equivalent(ref, hyp)
    edits, ops, _pairs = align_words(ref, hyp)
    return {"heard": heard_text.strip(), "wer": round(edits / max(1, len(ref)), 4), "edits": edits,
            "word_errors": [f"{o}:{a}->{b}" for o, a, b in ops], "ops": ops,
            "lexicon": lexicon_heard(script, heard_text, lexicon), "repeats": repeats(ref, hyp),
            "spoken_tag_words": tags_mod.spoken_tag_words(tagged, script, hyp) if tagged else []}


# ---------------------------------------------------------------------------------------------------------------
# One take


def quiet_run(db: np.ndarray, hop_s: float, t0: float, t1: float, thr: float) -> float:
    a, b = max(0, int(t0 / hop_s)), min(len(db), int(t1 / hop_s))
    best = cur = 0
    for q in db[a:b] < thr:
        cur = cur + 1 if q else 0
        best = max(best, cur)
    return best * hop_s


def _ending_moves(f0: np.ndarray, hop: float, words: list[dict], ends: list[str], question: list[str]) -> list[dict]:
    moves = []
    for w in words:
        tok = w["word"]
        if not lang.ends_with(tok, ends):
            continue
        seg = f0[int(w["start"] / hop): int(w["end"] / hop)]
        v = seg[~np.isnan(seg)]
        if len(v) < 6:
            continue
        k = max(2, len(v) // 3)
        head, tail = np.median(v[:-k]), np.median(v[-k:])
        moves.append({"word": tok, "question": lang.ends_with(tok, question),
                      "move_st": round(float(12 * math.log2(tail / head)), 2)})
    return moves


def measure_take(path: str | Path, *, script: str, words: list[dict] | None = None, lexicon: Iterable[str] = (),
                 language: str = "en", tagged: str | None = None, logprob: float | None = None) -> dict:
    """Every proxy for one read. `words` are the aligned words (Whisper or replay); without them the transcript
    measures are left out (None), never guessed."""
    table = lang.load(language)
    x, sr = audio_mod.load(path)
    out: dict = {"duration_s": round(len(x) / sr, 3)}
    out.update(audio_mod.loudness(x, sr))
    db, h = frame_rms_db(x, sr)
    hop_s = h / sr
    va = speech_activity(db, hop_s)
    ref_words = lang.norm_words(script)
    if va is None:
        out.update({"silent": True, "words": len(ref_words)})
        return out
    span = max(1e-6, va["end_s"] - va["start_s"])
    long_pauses = [(a, b) for a, b in va["pauses"] if b - a >= 0.25]
    pause_total = sum(b - a for a, b in long_pauses)
    nwords = len(ref_words)
    out.update({
        "lead_silence_s": round(va["start_s"], 3), "tail_silence_s": round(out["duration_s"] - va["end_s"], 3),
        "speech_span_s": round(span, 3), "words": nwords, "wps_span": round(nwords / span, 3),
        "wpm_span": round(60 * nwords / span, 1), "pauses_ge_250ms": len(long_pauses),
        "pause_total_s": round(pause_total, 3),
        "pause_mean_s": round(pause_total / len(long_pauses), 3) if long_pauses else 0.0,
        "longest_pause_s": round(max((b - a for a, b in long_pauses), default=0.0), 3),
        "articulation_wps": round(nwords / max(1e-6, span - pause_total), 3),
    })
    frames = [db[int(a / hop_s): int(b / hop_s)] for a, b in long_pauses]
    pf = np.concatenate(frames) if frames else np.array([])
    out["pause_floor_dbfs"] = round(float(np.percentile(pf, 10)), 1) if len(pf) else None
    cen, hi = spectral_stats(x, sr, va["active"], hop_s)
    out["spectral_centroid_hz"] = round(cen, 0) if cen is not None else None
    out["hf_4_10k_share"] = round(hi, 4) if hi is not None else None

    x16 = audio_mod.resample(x, sr, 16000)
    db16, _ = frame_rms_db(x16, 16000)
    f0 = yin_f0(x16, 16000, n=len(db16))
    f0[db16 < va["thr_db"]] = np.nan
    f0 = clean_f0(f0)
    v = f0[~np.isnan(f0)]
    if len(v) > 10:
        p5, p50, p95 = np.percentile(v, [5, 50, 95])
        out.update({"f0_median_hz": round(float(p50), 1), "f0_p5_hz": round(float(p5), 1),
                    "f0_p95_hz": round(float(p95), 1),
                    "f0_range_st_p5_p95": round(float(12 * math.log2(p95 / p5)), 2),
                    "f0_sd_st": round(float(np.std(12 * np.log2(v / p50))), 2),
                    "voiced_share": round(float(len(v) / max(1, int(span / HOP))), 3)})
    else:
        out.update({"f0_median_hz": None, "f0_range_st_p5_p95": None, "voiced_share": 0.0})

    if words is None:
        return out
    check = word_check(script, words, lexicon=lexicon, tagged=tagged)
    out.update({k: check[k] for k in ("heard", "wer", "word_errors", "lexicon", "repeats", "spoken_tag_words")})
    ends, question = table.get("sentence_end", []), table.get("question_end", [])
    spk, flo = float(np.percentile(db, 95)), float(np.percentile(db, 3))
    qthr = max(spk - 35.0, flo + 6.0)
    bounds = [quiet_run(db, hop_s, words[k]["end"] - 0.35, words[k + 1]["start"] + 0.35, qthr)
              for k in range(len(words) - 1) if lang.ends_with(words[k]["word"], ends)]
    out["sentence_pauses"] = [round(g, 2) for g in bounds]
    out["sentence_pause_mean_s"] = round(float(np.mean(bounds)), 3) if bounds else None
    out["sentence_pause_min_s"] = round(float(np.min(bounds)), 3) if bounds else None
    moves = _ending_moves(f0, HOP, words, ends, question)
    statements = [m for m in moves if not m["question"]]
    out["sentence_endings"] = moves
    out["statement_end_mean_st"] = round(float(np.mean([m["move_st"] for m in statements])), 2) if statements else None
    out["statement_rises_gt_1p5st"] = sum(1 for m in statements if m["move_st"] > 1.5)
    out["statements_measured"] = len(statements)
    loud = db16 >= va["thr_db"]
    ratios = []
    for w in words:
        if not lang.ends_with(w["word"], ends):
            continue
        a, b = int(w["start"] / HOP), int(w["end"] / HOP)
        seg_loud = loud[a:b]
        if seg_loud.sum() >= 5:
            ratios.append(float((~np.isnan(f0[a:b]) & seg_loud).sum() / seg_loud.sum()))
    overall = float((~np.isnan(f0) & loud).sum() / max(1, loud.sum()))
    out["overall_voicing"] = round(overall, 3)
    out["final_word_voicing"] = round(float(np.mean(ratios)), 3) if ratios else None
    out["final_word_voicing_rel"] = round(float(np.mean(ratios)) / overall, 3) if ratios and overall > 0 else None
    out["whisper_mean_logprob"] = logprob
    return out
