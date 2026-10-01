"""A/B spectrogram sheets: takes, tags or polish strengths side by side, measured, for the owner's eye and ear.

From the films' `spectro_ab.py`, `polish_report.py` and `tag_abc_v3.py`: one picture per comparison, every take on one
time scale (so a length difference is real) and one colour scale (each take brought to -20 LUFS first, so a brighter
picture means a brighter sound, not a louder file). Each panel shows:

- the spectrogram, 0-8 kHz (12 kHz for polish comparisons), 80 dB of range, with 2, 5 and 9 kHz marked where they fit;
- the words under it, when the take's heard words are known;
- the pitch (F0) contour on a log scale, with the reference take's median dashed across it;
- the level contour;
- a column of measurements: breath and effort against the reference, pitch, brightness, loudness and pace, creak, the
  last word's movement, a clean ending, sibilance and edge, and what Whisper heard.

Drawn with numpy and Pillow only (its own built-in font, neutral greys and a colour-blind-safe accent set; no brand
colours or fonts). Pillow's built-in font has no minus sign, en or em dash, arrow or "at least" sign, so every piece of
text goes through `drawable()` on its way to the page: a character the font can't draw becomes the plain text that
says the same thing ("-", "->", ">="), never the font's empty box. Proxies, not ears: the sheet shows what to listen
for.
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import signal

from slate.voice import audio as audio_mod
from slate.voice.measure import HOP

INK, INK2, MUTED, HAIR, PAGE, CARD = "#202020", "#404040", "#707070", "#d6d6d6", "#f6f6f6", "#ffffff"
# Okabe and Ito's colour-blind-safe set: the reference take is neutral ink, the others take these in turn.
ACCENTS = ("#0072b2", "#d55e00", "#009e73", "#cc79a7", "#e69f00", "#56b4e9")
GOOD, BAD = "#007a5e", "#b23a00"
RING_EDGE, RING_SIBILANCE = "#e69f00", "#56b4e9"  # the hottest edge and sibilance moments on a spectrogram
MAGMA = ((0.0, (0, 0, 4)), (0.25, (80, 18, 123)), (0.5, (183, 55, 121)), (0.75, (252, 137, 97)), (1.0, (252, 253, 191)))
SPEC_H, F0_H, LV_H, WORD_H = 240, 120, 54, 32
LEFT, RIGHT_COL, PAD = 74, 500, 24


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an old Pillow without sized default fonts
        return ImageFont.load_default()


F_TITLE, F_HEAD, F_BODY, F_SMALL = font(24), font(17), font(14), font(12)

# What a character the font can't draw is written as instead: the plain text a reader takes the same way.
PLAIN = {"\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",
         "\u2026": "...", "\u2192": "->", "\u2190": "<-", "\u2194": "<->", "\u2265": ">=", "\u2264": "<=",
         "\u2260": "!=", "\u2248": "~", "\u00b1": "+/-", "\u00d7": "x", "\u00b5": "u", "\u03bc": "u", "\u2018": "'",
         "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00b7": "-", "\u2022": "-", "\u00a0": " ", "\u2009": " ",
         "\u202f": " ", "\u00bd": "1/2", "\u00bc": "1/4", "\u00be": "3/4", "\u00b0": " deg"}
_MISSING = "\ue000"  # a private-use character: every font draws its missing-glyph box for it
_GLYPHS: dict[tuple[int, str], bool] = {}


def _mask(fnt: Any, ch: str) -> tuple:
    m = fnt.getmask(ch)
    return m.size, bytes(m)


def has_glyph(fnt: Any, ch: str) -> bool:
    """True when the font draws `ch` as itself rather than as its missing-glyph box (measured: the character's mask
    against a private-use character's, which no font has)."""
    if ch in "\n\r\t" or ch == " ":
        return True
    key = (id(fnt), ch)
    if key not in _GLYPHS:
        try:
            _GLYPHS[key] = _mask(fnt, ch) != _mask(fnt, _MISSING)
        except Exception:  # noqa: BLE001 - a font that can't even measure the character can't draw it
            _GLYPHS[key] = False
    return _GLYPHS[key]


def drawable(text: Any, fnt: Any) -> str:
    """`text` as the font can draw it: each character it lacks becomes its plain-text equivalent (a minus sign or a
    dash becomes "-", an ellipsis "...", an arrow "->"), an accented letter its base letter, and anything else "?",
    so a chart never shows the font's empty box where a character should be."""
    out = []
    for ch in str(text):
        if has_glyph(fnt, ch):
            out.append(ch)
            continue
        alt = PLAIN.get(ch)
        if alt is None:
            alt = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(alt if alt and all(has_glyph(fnt, c) for c in alt) else "?")
    return "".join(out)


class Draw(ImageDraw.ImageDraw):
    """ImageDraw whose text is always drawable in the font it's set in (see drawable()); measuring agrees with
    drawing, so layouts that wrap by width still fit."""

    def text(self, xy, text, fill=None, font=None, *args, **kwargs):  # noqa: D102 - ImageDraw's own
        return super().text(xy, drawable(text, font or self.getfont()), fill, font, *args, **kwargs)

    def textlength(self, text, font=None, *args, **kwargs):  # noqa: D102 - ImageDraw's own
        return super().textlength(drawable(text, font or self.getfont()), font, *args, **kwargs)


def draw(img: Image.Image) -> Draw:
    """A drawing surface for a voice chart (every chart in slate.voice draws through this)."""
    return Draw(img)


def colormap(v: np.ndarray) -> np.ndarray:
    """0-1 to RGB along a magma-like ramp (dark to bright)."""
    v = np.clip(v, 0, 1)
    out = np.zeros(v.shape + (3,))
    for (a, ca), (b, cb) in zip(MAGMA[:-1], MAGMA[1:]):
        m = (v >= a) & (v <= b)
        t = ((v - a) / (b - a))[m]
        for k in range(3):
            out[..., k][m] = ca[k] + (cb[k] - ca[k]) * t
    return out.astype(np.uint8)


def spectrogram(x: np.ndarray, sr: int, *, fmax: float = 8000.0) -> tuple[np.ndarray, float]:
    """(dB array rows high-to-low frequency, its top dB): a 21 ms Hann window every 5 ms."""
    nper = int(2 ** round(math.log2(1024 * sr / 48000)))
    hop = max(1, sr // 200)
    if len(x) < nper:
        x = np.concatenate([x, np.zeros(nper - len(x))])
    f, _t, z = signal.stft(x, sr, window="hann", nperseg=nper, noverlap=nper - hop, boundary=None, padded=False)
    s = 10 * np.log10(np.abs(z) ** 2 + 1e-20)
    s = s[f <= fmax][::-1]
    return s, float(s.max()) if s.size else -100.0


def spectrogram_image(s: np.ndarray, top: float, w: int, h: int, rng: float = 80.0) -> Image.Image:
    return Image.fromarray(colormap((s - (top - rng)) / rng)).resize((max(1, w), h), Image.BILINEAR)


def _at_minus20(x: np.ndarray, sr: int) -> np.ndarray:
    from slate.voice import dsp, polish  # noqa: PLC0415

    lufs = polish._lufs(x, sr)  # noqa: SLF001
    return x * dsp.db_to_gain(polish.WORK_LUFS - lufs) if lufs > polish.SILENT_LUFS else x


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: Any, width: int) -> list[str]:
    lines, cur = [], ""
    for w in str(text).replace("→", "to").split():
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def _fmt(v: Any, spec: str, unit: str = "") -> str:
    if v is None:
        return "n/a"
    try:
        return format(v, spec) + unit
    except (TypeError, ValueError):
        return str(v)


def measurement_rows(m: dict, vs: dict | None = None) -> list[tuple[str, str]]:
    """The measurement column for one take (analyze.measure_take's output; `vs` from analyze.ab against the
    reference)."""
    line = m.get("line") or {}
    rows: list[tuple[str, str]] = []
    if vs and vs.get("breath"):
        b = vs["breath"]
        verdict = "whisper-like" if b.get("whisper") else ("breathier" if b.get("breathier") else "full voice")
        rows.append(("Breath index vs A (lower = fuller)", f"{b['index']:+.2f} · {verdict}"))
    rows += [
        ("Voiced frames", _fmt(line.get("voiced_frac"), ".2f")),
        ("CPPS · HNR", f"{_fmt(line.get('cpps_db'), '.2f', ' dB')} · {_fmt(line.get('hnr_db'), '.1f', ' dB')}"),
        ("H1-H2 · tilt", f"{_fmt(line.get('h1h2_db'), '.1f', ' dB')} · {_fmt(line.get('tilt_db'), '.1f', ' dB')}"),
        ("F0 median · range", f"{_fmt(line.get('f0_median_hz'), '.0f', ' Hz')} · "
                              f"{_fmt(line.get('f0_range_st'), '.1f', ' st')}"),
        ("Brightness · F2", f"{_fmt(line.get('centroid_hz'), '.0f', ' Hz')} · {_fmt(line.get('f2_hz'), '.0f', ' Hz')}"),
        ("Loudness · pace", f"{_fmt(m.get('lufs'), '.1f', ' LUFS')} · "
                            f"{_fmt((m.get('pacing') or {}).get('wpm'), '.0f', ' wpm')}"),
        ("Creak", _fmt(None if line.get("creak_frac") is None else 100 * line["creak_frac"], ".1f",
                       "% of voiced frames")),
    ]
    bands = m.get("bands") or {}
    rows.append(("Sibilance · edge vs whole", f"{_fmt(bands.get('sibilance_vs_full_db'), '+.1f', ' dB')} · "
                                              f"{_fmt(bands.get('edge_vs_full_db'), '+.1f', ' dB')}"))
    rise = m.get("last_word_rise_st")
    rows.append(("Last word: pitch across it", "n/a" if rise is None else
                 f"{rise:+.1f} st ({'rises' if rise > 0.5 else 'falls' if rise < -0.5 else 'level'})"))
    tail = (m.get("complete") or {}).get("tail_below_speech_db")
    rows.append(("End of take (last 20 ms)", "n/a" if tail is None else
                 f"{tail:.0f} dB under the speech ({'clean' if tail >= 35 else 'cut off mid-word'})"))
    w = m.get("whisper")
    if w:
        ok = w.get("wer") == 0 and not w.get("repeats") and not w.get("spoken_tag_words")
        rows.append(("Whisper", "word for word" if ok else str(w.get("heard") or "")[:44]))
    if vs:
        rows.append(("Pitch vs A", _fmt(vs.get("f0_shift_st"), "+.2f", " st")))
        rows.append(("S peaks · edge peaks vs A", f"{_fmt(vs.get('sibilance_peak_db'), '+.1f', ' dB')} · "
                                                  f"{_fmt(vs.get('edge_peak_db'), '+.1f', ' dB')}"))
    return rows


def _panel(d: ImageDraw.ImageDraw, img: Image.Image, x0: int, y0: int, e: dict, px_s: float, top: float,
           base_f0: float | None, fmax: float) -> int:
    F = e["frames"]
    w = max(1, int(round(F.duration * px_s)))
    d.text((x0, y0), e["label"], fill=INK, font=F_HEAD)
    y0 += 26
    img.paste(spectrogram_image(e["spec"], top, w, SPEC_H), (x0, y0))
    step = 2000 if fmax <= 8000 else 3000
    for f in range(0, int(fmax) + 1, step):
        y = y0 + SPEC_H * (1 - f / fmax)
        d.line([(x0 - 6, y), (x0, y)], fill=MUTED)
        d.text((x0 - 56, y - 7), f"{f // 1000} kHz" if f else "0", fill=MUTED, font=F_SMALL)
    for f in (2000, 5000, 9000):
        if f < fmax:
            y = y0 + SPEC_H * (1 - f / fmax)
            d.line([(x0, y), (x0 + w, y)], fill=(255, 255, 255), width=1)
    if e.get("span_start_s") is not None:
        xs = x0 + e["span_start_s"] * px_s
        d.line([(xs, y0), (xs, y0 + SPEC_H)], fill=e["accent"], width=2)
    for mk in e.get("marks") or []:  # the hottest edge and sibilance moments (slate.voice.spectrum), ringed
        mx, my = x0 + float(mk["t"]) * px_s, y0 + SPEC_H * (1 - min(float(mk["f"]), fmax) / fmax)
        colour = RING_SIBILANCE if mk.get("kind") == "sibilance" else RING_EDGE
        d.ellipse([mx - 9, my - 9, mx + 9, my + 9], outline=colour, width=2)
    y = y0 + SPEC_H + 2
    row_end = [-1e9, -1e9]
    for wd in e.get("words") or []:
        txt = str(wd["word"]).strip()
        wx = x0 + float(wd["start"]) * px_s
        r = 0 if wx >= row_end[0] + 4 else 1
        d.text((wx, y + 3 + 14 * r), txt, fill=INK2, font=F_SMALL)
        row_end[r] = wx + d.textlength(txt, font=F_SMALL)
    y += WORD_H
    d.rectangle([x0, y, x0 + w, y + F0_H], outline=HAIR, fill=CARD)
    lo, hi = 70.0, 500.0

    def fy(f: float) -> float:
        return y + F0_H * (1 - math.log(f / lo) / math.log(hi / lo))

    for f in (100, 200, 300, 500):
        d.line([(x0 - 6, fy(f)), (x0, fy(f))], fill=MUTED)
        d.text((x0 - 56, fy(f) - 7), f"{f} Hz", fill=MUTED, font=F_SMALL)
    if base_f0:
        by = fy(min(max(base_f0, lo), hi))
        for xx in range(x0, x0 + w, 12):
            d.line([(xx, by), (min(xx + 6, x0 + w), by)], fill=MUTED)
    seg: list[tuple[float, float]] = []
    for i, f in enumerate(F.f0):
        if not np.isfinite(f) or f < lo or f > hi:
            if len(seg) > 1:
                d.line(seg, fill=e["accent"], width=3)
            seg = []
            continue
        seg.append((x0 + i * HOP * px_s, fy(float(f))))
    if len(seg) > 1:
        d.line(seg, fill=e["accent"], width=3)
    d.text((x0 + w - 6 - d.textlength("pitch (F0)", font=F_SMALL), y + F0_H - 17), "pitch (F0)", fill=MUTED,
           font=F_SMALL)
    y += F0_H + 4
    d.rectangle([x0, y, x0 + w, y + LV_H], outline=HAIR, fill=CARD)
    lv = np.convolve(F.db, np.ones(10) / 10, mode="same")
    pts = [(x0 + i * HOP * px_s, y + LV_H * (1 - (min(max(v, -60), -10) + 60) / 50)) for i, v in enumerate(lv)]
    if len(pts) > 1:
        d.line(pts, fill=INK2, width=2)
    d.text((x0 + w - 6 - d.textlength("level", font=F_SMALL), y + 3), "level", fill=MUTED, font=F_SMALL)
    return y + LV_H + 10


def ab_sheet(entries: list[dict], out: str | Path, *, title: str, headline: str | None = None,
             subtitle: str | None = None, fmax: float = 8000.0, ok: bool | None = None) -> Path:
    """One picture: every entry ({label, path, frames, measures, words?, vs?, span_start_s?}) as a panel, the first
    being the reference. Returns the PNG's path."""
    from slate.voice import breath  # noqa: PLC0415

    if not entries:
        raise ValueError("an A/B sheet needs at least one take")
    for i, e in enumerate(entries):
        e.setdefault("accent", INK2 if i == 0 else ACCENTS[(i - 1) % len(ACCENTS)])
        if e.get("frames") is None:
            e["frames"] = breath.Frames(e["path"])
        x, sr = audio_mod.load(e["path"])
        e["spec"], e["top"] = spectrogram(_at_minus20(x, sr), sr, fmax=fmax)
    top = max(e["top"] for e in entries)
    max_dur = max(e["frames"].duration for e in entries)
    px_s = min(220.0, 1400.0 / max(max_dur, 1e-3))
    width = LEFT + int(max_dur * px_s) + PAD + RIGHT_COL
    block_h = 26 + SPEC_H + 2 + WORD_H + F0_H + 4 + LV_H + 10
    meas = draw(Image.new("RGB", (1, 1)))
    tw = width - LEFT - 24
    title_l = wrap(meas, title, F_TITLE, tw)
    head_l = wrap(meas, headline, F_BODY, tw) if headline else []
    sub = subtitle or ("Every take on one time scale and one colour scale (each brought to -20 LUFS). The dashed line "
                       "is the first take's median pitch. Proxies, not ears: listen before you pick.")
    sub_l = wrap(meas, sub, F_SMALL, tw)
    header_h = 20 + 32 * len(title_l) + 6 + 21 * len(head_l) + 6 + 16 * len(sub_l) + 14
    rows_h = max(len(measurement_rows(e.get("measures") or {}, e.get("vs"))) for e in entries) * 19 + 60
    height = header_h + len(entries) * (max(block_h, rows_h) + 16) + 40
    img = Image.new("RGB", (width, height), PAGE)
    d = draw(img)
    y = 20
    for s in title_l:
        d.text((LEFT, y), s, fill=INK, font=F_TITLE)
        y += 32
    y += 6
    for s in head_l:
        d.text((LEFT, y), s, fill=(GOOD if ok else BAD) if ok is not None else INK2, font=F_BODY)
        y += 21
    y += 6
    for s in sub_l:
        d.text((LEFT, y), s, fill=MUTED, font=F_SMALL)
        y += 16
    y += 14
    base_f0 = ((entries[0].get("measures") or {}).get("line") or {}).get("f0_median_hz")
    for e in entries:
        start = y
        y_end = _panel(d, img, LEFT, y, e, px_s, top, base_f0, fmax)
        cx, cy = LEFT + int(max_dur * px_s) + PAD, start + 26
        d.text((cx, cy), "Measurements", fill=e["accent"], font=F_HEAD)
        cy += 24
        for k, v in measurement_rows(e.get("measures") or {}, e.get("vs")):
            d.text((cx, cy), k, fill=MUTED, font=F_SMALL)
            d.text((cx + 210, cy), v, fill=INK, font=F_SMALL)
            cy += 19
        for note in e.get("notes") or []:
            for s in wrap(d, note, F_SMALL, RIGHT_COL - 20):
                d.text((cx, cy), s, fill=e["accent"], font=F_SMALL)
                cy += 16
        y = max(y_end, cy) + 16
    d.text((LEFT, height - 26), "Measured with slate voice analyze (YIN pitch, CPPS, HNR, H1-H2, tilt, BS.1770). "
                                "Proxies, not ears.", fill=MUTED, font=F_SMALL)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.part.png")
    img.save(tmp, optimize=True)
    tmp.replace(out)
    return out


POLISH_NOTE = ("The polish changes the room and the EQ, which the breath measures read as breath (reverb fills the "
               "cepstrum, a low shelf lifts H1-H2), so this sheet leaves the breath verdict out: judge breath on the "
               "unpolished takes.")


def compare_files(pairs: list[tuple[str, str | Path]], out: str | Path, *, title: str, text: str | None = None,
                  words: list[list[dict] | None] | None = None, fmax: float = 12000.0, breath: bool = True) -> Path:
    """A sheet for plain files ([(label, path)]): measured here, the first file as the reference. `breath=False`
    for versions of one take that differ by processing (the polish), where a breath verdict would read the room."""
    from slate.voice import analyze  # noqa: PLC0415

    entries = []
    ref = None
    for i, (label, path) in enumerate(pairs):
        w = words[i] if words and i < len(words) else None
        m = analyze.measure_take(path, text=text, words=w, contour_every=None)
        vs = analyze.ab(ref, m) if ref is not None else None
        if vs is not None and not breath:
            vs["breath"] = None
        entries.append({"label": label, "path": path, "measures": m, "words": w, "vs": vs})
        ref = ref or m
    return ab_sheet(entries, out, title=title, fmax=fmax, subtitle=None if breath else (
        "Every version on one time scale and one colour scale (each brought to -20 LUFS). " + POLISH_NOTE))


def contour_chart(entries: list[tuple[str, str, list[tuple[float, float]], dict | None]], out: str | Path, *,
                  title: str, note: str) -> Path:
    """The last word's pitch for several takes on one axis (log Hz, ms from the word's start): [(label, colour,
    [(ms, hz)], contour)]."""
    W, H, L, R, T, B = 1100, 560, 80, 340, 70, 60
    img = Image.new("RGB", (W, H), PAGE)
    d = draw(img)
    d.text((L, 20), title, fill=INK, font=F_TITLE)
    x0, x1, y0, y1 = L, W - R, T, H - B
    d.rectangle([x0, y0, x1, y1], outline=HAIR, fill=CARD)
    tmax = max([p[0] for _, _, tr, _ in entries for p in tr] + [300.0]) + 20
    hz = [p[1] for _, _, tr, _ in entries for p in tr]
    lo, hi = (min(hz) / 1.12, max(hz) * 1.12) if hz else (120.0, 480.0)

    def fx(ms: float) -> float:
        return x0 + (x1 - x0) * max(0.0, ms) / tmax

    def fy(f: float) -> float:
        return y1 - (y1 - y0) * math.log(f / lo) / math.log(hi / lo)

    for g in (100, 120, 140, 160, 180, 200, 220, 250, 300, 350, 400, 450):
        if lo <= g <= hi:
            d.line([(x0, fy(g)), (x1, fy(g))], fill="#eeeeee")
            d.text((x0 - 58, fy(g) - 7), f"{g} Hz", fill=MUTED, font=F_SMALL)
    for ms in range(0, int(tmax), 100):
        d.text((fx(ms) - 10, y1 + 6), f"{ms} ms", fill=MUTED, font=F_SMALL)
    ly = y0
    for label, colour, track, c in entries:
        pts = [(fx(ms), fy(min(max(f, lo), hi))) for ms, f in track]
        if len(pts) > 1:
            d.line(pts, fill=colour, width=4)
        for p in pts:
            d.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3], fill=colour)
        d.rectangle([x1 + 16, ly + 4, x1 + 30, ly + 18], fill=colour)
        d.text((x1 + 38, ly + 2), label[:40], fill=INK, font=F_BODY)
        if c:
            d.text((x1 + 38, ly + 22), f"{c['start_hz']:.0f} Hz, peak {c['peak_hz']:.0f}, ends {c['end_hz']:.0f}",
                   fill=INK2, font=F_SMALL)
            d.text((x1 + 38, ly + 38), f"falls {c['fall_peak_to_end_st']:.1f} st · range {c['range_st']:.1f} st",
                   fill=INK2, font=F_SMALL)
        ly += 70
    for i, s in enumerate(wrap(d, note, F_SMALL, W - 2 * L)[:2]):
        d.text((L, H - 32 + 14 * i), s, fill=MUTED, font=F_SMALL)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out
