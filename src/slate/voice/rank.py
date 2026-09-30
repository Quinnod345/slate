"""Rank reads by measured proxies against the voice's targets (ported from the films' `rank_previews.py`).

A proxy score is not a listening test. It separates reads that are rushed, run-on, monotone, sing-song,
up-talking, noisy or misheard from reads that are none of those things; the owner still picks by ear.

Parts (weights from the films; a part that can't be measured for a batch is left out and the rest renormalized):
  words       Whisper hears the text: low WER, and every lexicon word comes through (a miss halves it)
  pace        words per minute over the speech span inside the target (150-170 for a narrator)
  endings     statements end on a fall (mean last-third move <= -1 st), with no statement rising > 1.5 st
  breathing   a real breath after each full stop (mean sentence pause in range, none under the minimum)
  range       pitch range p5-p95 in semitones inside the target (expressive, not monotone, not sing-song)
  clean       the gap floor at or below the target (digital-quiet gaps); a faint floor scores 0.7
  voicing     sentence-final words stay voiced relative to the rest (a low ratio flags creak or a breathy fade)
  pitch_fit   median F0 inside the window a design prompt aimed for (only when one is given)
  confidence  Whisper's mean log-probability, relative to the batch
"""

from __future__ import annotations

WEIGHTS = {"words": 0.20, "pace": 0.20, "endings": 0.12, "breathing": 0.10, "range": 0.10, "clean": 0.08,
           "lift": 0.08, "voicing": 0.07, "pitch_fit": 0.03, "confidence": 0.02}


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def parts(m: dict, targets: dict, *, pitch_hz: list | None = None, conf_range: tuple | None = None,
          lift_range: tuple | None = None) -> dict:
    """Each part's score in 0..1 for one read's metrics, or None when the part can't be measured."""
    s: dict[str, float | None] = {k: None for k in WEIGHTS}
    wer = m.get("wer")
    if wer is not None:
        missed = [k for k, heard in (m.get("lexicon") or {}).items() if not heard]
        s["words"] = _clip(1 - 10 * wer) * (0.5 if missed else 1.0)
    wpm = m.get("wpm_span")
    lo, hi = (targets.get("wpm") or [150, 170])
    if wpm is not None:
        s["pace"] = 1.0 if lo <= wpm <= hi else _clip(1 - 0.05 * (wpm - hi if wpm > hi else lo - wpm))
    end = m.get("statement_end_mean_st")
    if m.get("statements_measured"):
        s["endings"] = _clip((1.0 if end <= -1 else 0.6 if end <= 0 else 0.2)
                             - 0.15 * (m.get("statement_rises_gt_1p5st") or 0))
    g = m.get("sentence_pause_mean_s")
    plo, phi = targets.get("sentence_pause_s") or [0.35, 0.7]
    if g is not None:
        b = 1.0 if plo <= g <= phi else (0.6 if plo - 0.1 <= g <= phi + 0.2 else 0.2)
        if (m.get("sentence_pause_min_s") or 0) < (targets.get("sentence_pause_min_s") or 0.12):
            b -= 0.3
        s["breathing"] = _clip(b)
    r = m.get("f0_range_st_p5_p95")
    rlo, rhi = targets.get("f0_range_st") or [9.5, 13.0]
    if r is not None:
        s["range"] = 1.0 if rlo <= r <= rhi else (0.6 if rlo - 1.5 <= r <= rhi + 1.5 else 0.2)
    floor = m.get("pause_floor_dbfs")
    target_floor = targets.get("gap_floor_dbfs", -75.0)
    if floor is not None:
        s["clean"] = 1.0 if floor <= target_floor else (0.7 if floor <= target_floor + 15 else 0.3)
    fv = m.get("final_word_voicing_rel")
    if fv is not None:
        s["voicing"] = 1.0 if fv >= 0.6 else 0.6 if fv >= 0.45 else 0.2
    window = pitch_hz or targets.get("f0_hz")
    f0 = m.get("f0_median_hz")
    if window and f0:
        wlo, whi = window
        s["pitch_fit"] = 1.0 if wlo <= f0 <= whi else (0.5 if wlo - 10 <= f0 <= whi + 10 else 0.0)
    lift = m.get("lift_st")
    if lift is not None and lift_range and lift_range[1] != lift_range[0]:
        s["lift"] = (lift - lift_range[0]) / (lift_range[1] - lift_range[0])
    c = m.get("whisper_mean_logprob")
    if c is not None and conf_range and conf_range[1] != conf_range[0]:
        s["confidence"] = (c - conf_range[0]) / (conf_range[1] - conf_range[0])
    return s


def score(part_scores: dict) -> float:
    """Weighted mean of the measured parts (unmeasured parts drop out and the weights renormalize)."""
    used = {k: v for k, v in part_scores.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in used)
    if not total_w:
        return 0.0
    return round(sum(WEIGHTS[k] * v for k, v in used.items()) / total_w, 3)


def _range(rows: list[dict], key: str) -> tuple | None:
    vals = [r["metrics"].get(key) for r in rows if r.get("metrics") and r["metrics"].get(key) is not None]
    return (min(vals), max(vals)) if vals else None


def rank(rows: list[dict], targets: dict, *, group: str | None = None) -> dict:
    """Rank rows ({id, metrics, [pitch_hz], [group key]}). Returns {ranked, best_by_<group>, weights}."""
    conf, lift = _range(rows, "whisper_mean_logprob"), _range(rows, "lift_st")
    ranked = []
    for r in rows:
        m = r.get("metrics") or {}
        ps = parts(m, targets, pitch_hz=r.get("pitch_hz"), conf_range=conf, lift_range=lift)
        missed = [k for k, heard in (m.get("lexicon") or {}).items() if not heard]
        ranked.append({**{k: v for k, v in r.items() if k != "metrics"}, "score": score(ps),
                       "parts": {k: (round(v, 2) if v is not None else None) for k, v in ps.items()},
                       "lexicon_missed": missed})
    ranked.sort(key=lambda x: -x["score"])
    out = {"weights": WEIGHTS, "ranked": ranked,
           "note": "Proxies, not ears: these rank reads for a shortlist; the owner picks by listening."}
    if group:
        best: dict = {}
        for x in ranked:
            best.setdefault(x.get(group), x)
        out[f"best_by_{group}"] = best
    return out
