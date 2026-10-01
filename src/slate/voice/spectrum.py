"""Where a voice's harshness lives: its long-term spectrum against natural speech's, band by band and word by word.

From the films' polish report (`polish_report.py`, the "where her harshness lives" chart that shaped the studio
polish), generalized to any rate. Every take is brought to -20 LUFS first, so takes, voices and polish strengths
compare at one loudness, and it's measured on speech only (10 ms frames within 35 dB of the take's loud level):

- the long-term average spectrum (LTAS) in third-octave bands, against the ANSI S3.5-1997 standard speech spectrum for
  normal vocal effort (its Table 3), both set equal over 500 Hz-1 kHz, so what's compared is the shape: a voice well
  over natural speech at 2-5 kHz sounds edgy, at 5-9 kHz sibilant;
- band levels: lows 80-300 Hz, body 300 Hz-2 kHz, the clarity band 1-5 kHz (where the mix checks every word), the
  edge 2-5 kHz, sibilance 5-9 kHz and air 9-16 kHz;
- harshness over time: the 5 ms level of the edge and sibilance bands, their median and loudest moments (the 99th
  percentile), and, when the take's words are known, the word each loud moment falls in;
- `chart`: each take's third-octave curve over the ANSI shape (grey), with the edge and sibilance bands shaded.

Numpy, scipy and Pillow only, like the rest of the voice stage; neutral greys and the colour-blind-safe accents of
`slate.voice.sheet`, no brand colours or fonts. Proxies, not ears: it shows where to listen.

    spectrum.measure(path_or_samples, sr=None, words=None) -> dict     (one take)
    spectrum.pooled(measures) -> dict                                  (several takes as one voice, frame-weighted)
    spectrum.shape_vs_ansi(thirds_db) -> {band: dB over natural speech's shape}
    spectrum.chart([(name, colour, thirds_db), …], out, title=…) -> Path
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw

from slate.voice import audio as audio_mod
from slate.voice import dsp, polish

# ANSI S3.5-1997, Table 3: the standard speech spectrum level for normal vocal effort, dB per Hz, 160 Hz-8 kHz.
ANSI_S35_NORMAL = {160: 32.41, 200: 34.48, 250: 34.75, 315: 33.98, 400: 34.59, 500: 34.27, 630: 32.06, 800: 28.30,
                   1000: 25.01, 1250: 23.00, 1600: 20.15, 2000: 17.32, 2500: 13.18, 3150: 11.55, 4000: 9.33,
                   5000: 5.31, 6300: 2.59, 8000: 1.13}
THIRDS = polish.THIRDS
BANDS = {"lows_80_300": (80, 300), "body_300_2k": (300, 2000), "clarity_1k_5k": (1000, 5000),
         "edge_2k_5k": (2000, 5000), "sib_5k_9k": (5000, 9000), "air_9k_16k": (9000, 16000)}
EDGE, SIBILANCE = BANDS["edge_2k_5k"], BANDS["sib_5k_9k"]
MATCH_BANDS = (500, 630, 800, 1000)  # where the take and natural speech are set equal: 500 Hz-1 kHz
HOTTEST = 5  # the loudest edge and sibilance moments listed, word by word


def ansi_band_levels() -> dict[int, float]:
    """ANSI's spectrum levels as third-octave band levels (+10 log10 of each band's width in Hz)."""
    return {f: v + 10 * math.log10(f * (2 ** (1 / 6) - 2 ** (-1 / 6))) for f, v in ANSI_S35_NORMAL.items()}


def shape_vs_ansi(thirds: dict) -> dict[int, float]:
    """The take's third-octave levels minus ANSI's, both set equal over 500 Hz-1 kHz (dB): positive means more than
    natural speech's average shape has there. Bands the take doesn't reach (over its Nyquist) are left out."""
    an = ansi_band_levels()
    levels = {int(k): float(v) for k, v in thirds.items() if v is not None}
    ref = [f for f in MATCH_BANDS if f in levels]
    if not ref:
        return {}
    offset = np.mean([levels[f] for f in ref]) - np.mean([an[f] for f in ref])
    return {f: round(levels[f] - an[f] - float(offset), 2) for f in an if f in levels}


def _mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x.mean(axis=1) if x.ndim > 1 else x


def _db(power: float) -> float:
    return float(10 * np.log10(power + 1e-20))


def _band(ltas: np.ndarray, f: np.ndarray, lo: float, hi: float) -> float | None:
    sel = (f >= lo) & (f < hi)
    return _db(float(ltas[sel].sum())) if sel.any() else None


def _thirds(ltas: np.ndarray, f: np.ndarray, sr: int) -> dict[int, float]:
    out = {}
    for fc in THIRDS:
        if fc * 2 ** (1 / 6) > sr / 2:
            break
        level = _band(ltas, f, fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6))
        if level is not None:
            out[fc] = round(level, 2)
    return out


def _envelope(x: np.ndarray, sr: int, band: tuple[float, float], hop: int, n: int) -> np.ndarray | None:
    lo, hi = band
    if hi >= sr / 2:
        hi = sr / 2 * 0.98
    if lo >= hi:
        return None
    b, _ = dsp.band_split(x, sr, lo, hi)
    return dsp.env_db(b, sr, 5.0)[::hop][:n]


def _word(w: dict) -> tuple[str, float, float]:
    return str(w.get("word", w.get("w", w.get("text", "")))).strip(), float(w["start"]), float(w["end"])


def _hottest(env: np.ndarray, words: list[dict] | None, frames_per_s: float, kind: str,
             speech: np.ndarray) -> list[dict]:
    """The loudest 5 ms moments of one band: the loudest in each word when the words are known, else the loudest
    peaks over speech at least 0.3 s apart (word None)."""
    rows = []
    if words:
        for w in words:
            text, start, end = _word(w)
            i0, i1 = max(0, int((start - 0.03) * frames_per_s)), min(len(env), int((end + 0.03) * frames_per_s) + 1)
            if i1 <= i0:
                continue
            k = i0 + int(np.argmax(env[i0:i1]))
            rows.append({"word": text, "kind": kind, "db": round(float(env[k]), 2), "t": round(k / frames_per_s, 3)})
    else:
        gap = max(1, int(0.3 * frames_per_s))
        sel = speech[: len(env)]
        for k in np.argsort(-np.where(sel, env[: len(sel)], -np.inf)):
            if not np.isfinite(env[k]) or not sel[k] or len(rows) >= HOTTEST:
                break
            if all(abs(k - r["_k"]) >= gap for r in rows):
                rows.append({"word": None, "kind": kind, "db": round(float(env[k]), 2), "t": round(k / frames_per_s, 3),
                             "_k": int(k)})
        for r in rows:
            r.pop("_k", None)
    return sorted(rows, key=lambda r: -r["db"])[:HOTTEST]


def measure(x: np.ndarray | str | Path, sr: int | None = None, *, words: Iterable[dict] | None = None) -> dict:
    """One take's long-term spectrum, band levels, harshness over time and (with its words) its hottest words.

    Keys starting with an underscore are the raw power spectrum and its frequencies, kept for `pooled` and left out of
    JSON by `public`."""
    if isinstance(x, (str, Path)):
        x, sr = audio_mod.load(x)
    if sr is None:
        raise ValueError("measure() needs the sample rate with raw samples")
    x = _mono(x)
    lufs = polish._lufs(x, sr)  # noqa: SLF001 - the one BS.1770 meter
    if lufs > polish.SILENT_LUFS:
        x = x * dsp.db_to_gain(polish.WORK_LUFS - lufs)
    f, p, hop = polish._stft_power(x, sr)  # noqa: SLF001 - the polish report's own frames: 10 ms hop
    n = p.shape[1]
    sp = polish._speech_frames(p)  # noqa: SLF001
    if not lufs > polish.SILENT_LUFS:  # silence: every frame sits at the floor, and none of it is speech
        sp = np.zeros_like(sp)
    out: dict[str, Any] = {"sample_rate": sr, "duration_s": round(len(x) / sr, 3), "frames": int(sp.sum()),
                           "lufs_in": round(lufs, 2) if math.isfinite(lufs) else None}
    if not sp.any():
        out.update(thirds_db={}, bands_db={}, vs_ansi_db={}, edge_p50_db=None, edge_p99_db=None, sib_p50_db=None,
                   sib_p99_db=None, hottest=[], _ltas=np.zeros_like(f), _freqs=f)
        return out
    ltas = p[:, sp].mean(axis=1)
    thirds = _thirds(ltas, f, sr)
    out["thirds_db"] = thirds
    out["bands_db"] = {k: (round(v, 2) if (v := _band(ltas, f, lo, min(hi, sr / 2))) is not None else None)
                       for k, (lo, hi) in BANDS.items()}
    out["vs_ansi_db"] = shape_vs_ansi(thirds)
    frames_per_s = sr / hop
    hottest: list[dict] = []
    for key, band in (("edge", EDGE), ("sib", SIBILANCE)):
        env = _envelope(x, sr, band, hop, n)
        if env is None or not len(env):
            out[f"{key}_p50_db"] = out[f"{key}_p99_db"] = None
            continue
        sel = sp[: len(env)]
        out[f"{key}_p50_db"] = round(float(np.percentile(env[sel], 50)), 2) if sel.any() else None
        out[f"{key}_p99_db"] = round(float(np.percentile(env[sel], 99)), 2) if sel.any() else None
        hottest += _hottest(env, list(words) if words else None, frames_per_s,
                            "sibilance" if key == "sib" else "edge", sel)
    out["hottest"] = sorted(hottest, key=lambda r: -r["db"])
    out["_ltas"], out["_freqs"] = ltas, f
    return out


# Where a moment is ringed on a spectrogram: the middle of its band (on a log scale).
MARK_HZ = {"edge": math.sqrt(EDGE[0] * EDGE[1]), "sibilance": math.sqrt(SIBILANCE[0] * SIBILANCE[1])}


def marks(m: dict, n: int = HOTTEST) -> list[dict]:
    """A take's hottest moments as rings for its spectrogram panel: {t, f, kind}."""
    return [{"t": h["t"], "f": MARK_HZ[h["kind"]], "kind": h["kind"]} for h in (m.get("hottest") or [])[:n]]


def harshest_reel(lines: list[dict], out: str | Path, *, n: int = 6, half_s: float = 0.6) -> dict:
    """A listening reel of the harshest moments: each one unpolished, then at each strength, level-matched.

    lines: [{shot, measure (of the unpolished take), versions: [(label, path)…] with the unpolished take first}]. The
    loudest `n` edge and sibilance moments across the lines, `half_s` either side, cut from every version at the same
    time (the polish keeps every sample where it was)."""
    from slate.voice import reel  # noqa: PLC0415

    out = Path(out)
    moments = sorted(({"shot": ln["shot"], **h} for ln in lines for h in (ln["measure"].get("hottest") or [])),
                     key=lambda r: -r["db"])[:n]
    clips_dir = out.parent / (out.stem + "_clips")
    items, listed = [], []
    by_shot = {ln["shot"]: ln for ln in lines}
    for i, mo in enumerate(moments, 1):
        listed.append({k: mo[k] for k in ("shot", "kind", "word", "t", "db")})
        for label, path in by_shot[mo["shot"]]["versions"]:
            x, sr = audio_mod.read_wav(path)
            a, b = max(0, int((mo["t"] - half_s) * sr)), min(len(x), int((mo["t"] + half_s) * sr))
            clip = clips_dir / f"{i:02d}_{mo['shot']}_{mo['kind']}_{re_label(label)}.wav"
            clip.parent.mkdir(parents=True, exist_ok=True)
            audio_mod.write_wav(clip, x[a:b], sr, 24)
            where = f" on \"{mo['word']}\"" if mo.get("word") else ""
            items.append({"file": clip, "title": f"{mo['shot']} {mo['kind']}{where} at {mo['t']:.2f} s: {label}"})
    info = reel.build(items, out, gap_s=0.6) if items else {"reel": None, "items": []}
    return {"reel": info.get("reel"), "moments": listed}


def re_label(label: str) -> str:
    return "".join(ch if ch.isalnum() else "p" if ch == "." else "-" for ch in str(label))


def pooled(measures: list[dict]) -> dict:
    """Several takes as one voice: their speech pooled (each take's LTAS weighted by its speech frames)."""
    rows = [m for m in measures if m.get("frames") and m.get("_ltas") is not None]
    if not rows:
        return {"takes": 0, "frames": 0, "thirds_db": {}, "bands_db": {}, "vs_ansi_db": {}}
    sr = min(int(m["sample_rate"]) for m in rows)
    f = rows[0]["_freqs"]
    same = [m for m in rows if len(m["_freqs"]) == len(f) and np.allclose(m["_freqs"], f)]
    w = np.array([m["frames"] for m in same], dtype=float)
    ltas = (np.array([m["_ltas"] for m in same]) * w[:, None]).sum(axis=0) / w.sum()
    thirds = _thirds(ltas, f, sr)
    return {"takes": len(same), "frames": int(w.sum()), "thirds_db": thirds,
            "bands_db": {k: (round(v, 2) if (v := _band(ltas, f, lo, min(hi, sr / 2))) is not None else None)
                         for k, (lo, hi) in BANDS.items()},
            "vs_ansi_db": shape_vs_ansi(thirds)}


def public(m: dict) -> dict:
    """A measure without its raw arrays, ready for JSON (band keys as strings)."""
    out = {k: v for k, v in m.items() if not k.startswith("_")}
    for key in ("thirds_db", "vs_ansi_db"):
        if isinstance(out.get(key), dict):
            out[key] = {str(k): v for k, v in out[key].items()}
    return out


def chart(curves: list[tuple[str, str, dict]], out: str | Path, *, title: str, subtitle: str | None = None,
          ansi: bool = True) -> Path:
    """Third-octave long-term spectra, one curve per take, over natural speech's shape (ANSI S3.5, grey), set equal over
    500 Hz-1 kHz to the first curve, on a log frequency axis from 100 Hz to 16 kHz."""
    from slate.voice.sheet import F_BODY, F_HEAD, F_SMALL, F_TITLE, HAIR, INK, MUTED, PAGE  # noqa: PLC0415

    curves = [(name, colour, {int(k): float(v) for k, v in c.items() if v is not None}) for name, colour, c in curves]
    curves = [c for c in curves if c[2]]
    if ansi and curves:
        first = curves[0][2]
        an = ansi_band_levels()
        ref = [f for f in MATCH_BANDS if f in first]
        if ref:
            offset = np.mean([first[f] for f in ref]) - np.mean([an[f] for f in ref])
            curves.append(("natural speech (ANSI S3.5)", "#9e9e9e", {f: v + float(offset) for f, v in an.items()}))
    width, height, left, right, top, bottom = 1400, 720, 90, 320, 96, 80
    img = Image.new("RGB", (width, height), PAGE)
    d = ImageDraw.Draw(img)
    d.text((left, 24), title, fill=INK, font=F_TITLE)
    d.text((left, 58), subtitle or ("Third-octave long-term spectrum of speech, every take at −20 LUFS, set equal to "
                                    "natural speech over 500 Hz–1 kHz, so a gap between curves is a real change."),
           fill=MUTED, font=F_BODY)
    x0, x1, y0, y1 = left, width - right, top, height - bottom
    d.rectangle([x0, y0, x1, y1], outline=HAIR, fill="#ffffff")

    def fx(f: float) -> float:
        return x0 + (x1 - x0) * math.log(f / 100) / math.log(16000 / 100)

    values = [v for _, _, c in curves for v in c.values()] or [0.0]
    lo, hi = math.floor(min(values) / 5) * 5 - 5, math.ceil(max(values) / 5) * 5 + 5

    def fy(v: float) -> float:
        return y1 - (y1 - y0) * (v - lo) / max(1e-9, hi - lo)

    d.rectangle([fx(EDGE[0]), y0 + 1, fx(EDGE[1]), y1 - 1], fill="#fff4e5")
    d.rectangle([fx(SIBILANCE[0]), y0 + 1, fx(SIBILANCE[1]), y1 - 1], fill="#e8f3fb")
    d.text((fx(EDGE[0]) + 6, y0 + 6), "2–5 kHz edge", fill="#9a5b00", font=F_SMALL)
    d.text((fx(SIBILANCE[0]) + 6, y0 + 6), "5–9 kHz sibilance", fill="#005b8a", font=F_SMALL)
    for v in range(int(lo), int(hi) + 1, 5):
        d.line([(x0 + 1, fy(v)), (x1 - 1, fy(v))], fill="#f0f0f0")
        d.text((x0 - 56, fy(v) - 7), f"{v} dB", fill=MUTED, font=F_SMALL)
    for f in (100, 200, 500, 1000, 2000, 5000, 10000, 16000):
        d.text((fx(f) - 12, y1 + 8), f"{f // 1000}k" if f >= 1000 else str(f), fill=MUTED, font=F_SMALL)
    legend_y = y0
    for name, colour, c in curves:
        pts = [(fx(f), fy(v)) for f, v in sorted(c.items()) if 100 <= f <= 16000]
        if len(pts) > 1:
            d.line(pts, fill=colour, width=3 if "ANSI" in name else 4)
        for px, py in pts:
            d.ellipse([px - 3, py - 3, px + 3, py + 3], fill=colour)
        d.rectangle([x1 + 20, legend_y + 6, x1 + 36, legend_y + 22], fill=colour)
        d.text((x1 + 44, legend_y + 4), name, fill=INK, font=F_HEAD)
        legend_y += 34
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out
