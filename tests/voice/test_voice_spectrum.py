"""The voice's long-term spectrum against natural speech (slate.voice.spectrum), and the spectrogram's own axes: on
signals whose answers are known. Synthetic audio only."""

import math

import numpy as np
import pytest
from PIL import Image

from slate import jsonio
from slate.cli import run_cli
from slate.voice import analyze, polish, sheet, spectrum

from voice_helpers import write_rich

TEXT = "Slate hears each word, then fits it to the cut."
SR = 48000


def _tone(f: float, start: float, end: float, total: float, sr: int = SR, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(total * sr)) / sr
    on = (t >= start) & (t < end)
    return amp * np.sin(2 * np.pi * f * t) * on


def test_ansi_speech_set_against_itself_has_no_shape():
    an = spectrum.ansi_band_levels()
    # spectrum level per Hz plus the band's width: 1 kHz's third-octave is about 231.6 Hz wide
    assert an[1000] == pytest.approx(25.01 + 10 * math.log10(231.6), abs=0.01)
    assert set(spectrum.shape_vs_ansi(an).values()) == {0.0}
    # a voice 6 dB louder everywhere has the same shape; one 6 dB hotter at 2-5 kHz shows exactly there
    loud = {f: v + 6 for f, v in an.items()}
    assert set(spectrum.shape_vs_ansi(loud).values()) == {0.0}
    edgy = {f: v + (6 if 2000 <= f <= 5000 else 0) for f, v in an.items()}
    shape = spectrum.shape_vs_ansi(edgy)
    assert shape[2500] == 6.0 and shape[1000] == 0.0 and shape[6300] == 0.0


def test_a_tone_lands_in_its_third_octave_and_noise_is_brighter_than_speech():
    x = _tone(1000, 0.1, 1.9, 2.0)
    m = spectrum.measure(x, SR)
    assert max(m["thirds_db"], key=m["thirds_db"].get) == 1000
    noise = np.random.default_rng(1).standard_normal(2 * SR) * 0.1  # flat per Hz: rises 1 dB a third
    v = spectrum.measure(noise, SR)["vs_ansi_db"]
    assert v[4000] - v[500] > 15  # far more edge than natural speech's falling shape
    assert spectrum.measure(np.zeros(SR), SR)["frames"] == 0


def test_sibilant_words_are_the_hottest_and_a_plain_read_measures_less_sibilance(tmp_path):
    sib = tmp_path / "sibilant.wav"
    plain = tmp_path / "plain.wav"
    words = write_rich(sib, TEXT, seed=1)
    write_rich(plain, TEXT, seed=1, sibilance=0.0)
    m = spectrum.measure(sib, words=words)
    p = spectrum.measure(plain, words=words)
    assert m["sib_p99_db"] - p["sib_p99_db"] > 10
    assert m["bands_db"]["sib_5k_9k"] > p["bands_db"]["sib_5k_9k"] + 6
    hot = [h for h in m["hottest"] if h["kind"] == "sibilance"]
    assert hot and {h["word"].strip(".,") for h in hot[:3]} <= {"Slate", "hears", "fits"}
    # the studio polish's de-esser takes sibilance down (the same take, same loudness)
    from slate.voice import audio

    x, sr = audio.load(sib)
    y, _ = polish.apply(x, sr, polish.resolve(polish.tuning_for(None), "B")["chain"])
    assert spectrum.measure(y, sr)["sib_p99_db"] < m["sib_p99_db"] - 1


def test_pooling_weights_each_take_by_its_speech(tmp_path):
    a = spectrum.measure(_tone(500, 0.0, 3.0, 3.0), SR)
    b = spectrum.measure(_tone(4000, 0.0, 1.0, 1.0), SR)
    both = spectrum.pooled([a, b])
    assert both["takes"] == 2 and both["frames"] == a["frames"] + b["frames"]
    assert both["thirds_db"][500] > both["thirds_db"][4000]  # three times the speech at 500 Hz
    assert spectrum.public(a).keys().isdisjoint({"_ltas", "_freqs"})


def test_the_chart_and_the_analyze_report(slate_home, tmp_path):
    raw, polished = tmp_path / "raw.wav", tmp_path / "polished.wav"
    write_rich(raw, TEXT, seed=2)
    polish.polish_file(raw, polished, polish.resolve(polish.tuning_for(None), "A")["chain"])
    res = analyze.run([str(polished)], ab_ref=str(raw), spectrum=True, text=TEXT, out=tmp_path / "a")
    chart = tmp_path / "a" / "spectrum.png"
    assert res["spectrum"]["chart"] == str(chart) and Image.open(chart).size == (1400, 720)
    doc = jsonio.read(tmp_path / "a" / "analysis.json")
    assert jsonio.validate(doc, "voice_analysis") == []
    assert [t["label"] for t in doc["spectrum"]["takes"]] == [t["label"] for t in doc["takes"]]
    assert {"thirds_db", "bands_db", "vs_ansi_db", "sib_p99_db", "edge_p99_db"} <= doc["spectrum"]["takes"][0].keys()
    code, out, _ = run_cli(["voice", "analyze", str(polished), "--ab", str(raw), "--spectrum", "--sheet", "--text",
                            TEXT, "--out", str(tmp_path / "b")])
    assert code == 0 and "over natural speech by" in out and "spectrum:" in out
    # with both, the sheet rings each take's hottest edge and sibilance moments
    img = np.asarray(Image.open(tmp_path / "b" / "sheet.png").convert("RGB")).reshape(-1, 3)
    for ring in (sheet.RING_EDGE, sheet.RING_SIBILANCE):
        rgb = tuple(int(ring[i:i + 2], 16) for i in (1, 3, 5))
        assert (np.abs(img - rgb).sum(axis=1) < 30).sum() > 20, ring


def test_the_spectrogram_puts_each_tone_on_its_row_and_its_time_on_its_column():
    """A sheet is only worth reading if its axes are right: 1, 2 and 5 kHz tones at known times land on their rows
    (frequency, 0-8 kHz, high at the top) and columns (time), in the numbers and in the picture."""
    total = 3.5
    tones = ((1000, 0.5, 1.0), (2000, 1.5, 2.0), (5000, 2.5, 3.0))
    x = sum(_tone(f, a, b, total) for f, a, b in tones)
    s, top = sheet.spectrogram(x, SR, fmax=8000)
    rows, cols = s.shape
    hop = SR // 200
    nper = int(2 ** round(math.log2(1024 * SR / 48000)))
    freqs = np.linspace(0, SR / 2, nper // 2 + 1)
    freqs = freqs[freqs <= 8000][::-1]  # row 0 is the top: the highest frequency
    w, h = 700, 400
    img = np.asarray(sheet.spectrogram_image(s, top, w, h)).astype(float).sum(axis=2)
    for f, a, b in tones:
        col = int(round(((a + b) / 2 * SR - nper / 2) / hop))
        assert abs(freqs[int(np.argmax(s[:, col]))] - f) <= SR / nper  # within one bin
        px = int(round(col / cols * w))
        py = int(np.argmax(img[:, px]))
        assert abs(py - (1 - f / 8000) * h) <= 0.02 * h  # the picture's row too
        quiet = int(round(((a - 0.25) * SR - nper / 2) / hop))  # before the tone starts: nothing at its row
        assert s[int(np.argmax(s[:, col])), quiet] < s[:, col].max() - 40


# ---------------------------------------------------------------------------------------------------------------
# Every label is drawn in glyphs the font has (GAPS-FINAL A6.1), and a take given by its path keeps its shot (A6.2)


def test_every_label_on_the_charts_is_drawn_in_glyphs_its_font_has(tmp_path, monkeypatch):
    """Pillow's built-in font has no minus sign or en dash: "−20 LUFS" and "2–5 kHz edge" drew as boxes. Every string
    any voice chart draws now holds only characters its font has, with the plain text standing in ("-20 LUFS")."""
    from PIL import ImageDraw

    drawn: list[tuple[str, object]] = []
    real = ImageDraw.ImageDraw.text

    def record(self, xy, text, fill=None, font=None, *args, **kwargs):
        drawn.append((str(text), font or self.getfont()))
        return real(self, xy, text, fill, font, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    curve = {f: -20.0 - i for i, f in enumerate(spectrum.THIRDS) if 100 <= f <= 16000}
    spectrum.chart([("D03 t01 (raw) · −3 dB", "#202020", curve)], tmp_path / "c.png",
                   title="Where the harshness lives: A against B …")
    wav = tmp_path / "take.wav"
    words = write_rich(wav, TEXT, seed=3)
    sheet.ab_sheet([{"label": "A · D03 t01 (raw)", "path": wav, "measures": {}, "words": words},
                    {"label": "B · D03 t01 (polished) → −1 dB", "path": wav, "measures": {}, "words": words}],
                   tmp_path / "s.png", title="A/B: one against another against a third …",
                   headline="2–5 kHz edge ≥ 3 dB under — not over")
    text = " | ".join(s for s, _ in drawn)
    assert "-20 LUFS" in text and "500 Hz-1 kHz" in text and "2-5 kHz edge" in text and "5-9 kHz sibilance" in text
    assert "->" in text and ">=" in text and "−" not in text and "–" not in text
    for s, fnt in drawn:
        missing = [ch for ch in s if not sheet.has_glyph(fnt, ch)]
        assert not missing, (s, missing)


def test_drawable_keeps_what_the_font_has_and_spells_out_the_rest():
    f = sheet.F_SMALL
    assert sheet.drawable("Plans take minutes.", f) == "Plans take minutes."
    assert sheet.drawable("−20 LUFS, 500 Hz–1 kHz", f) == "-20 LUFS, 500 Hz-1 kHz"
    assert sheet.drawable("café ≤ 3", f) == "cafe <= 3"
    assert not sheet.has_glyph(f, "") and sheet.has_glyph(f, "A")


def test_a_take_given_by_its_path_is_labelled_with_its_shot(tmp_path):
    takes = tmp_path / "film" / "voice" / "takes" / "D03"
    takes.mkdir(parents=True)
    raw, polished = takes / "t01.wav", takes / "t01.polished.wav"
    write_rich(raw, TEXT, seed=4)
    write_rich(polished, TEXT, seed=4)
    t = analyze.resolve_target(str(raw))
    assert t["label"] == "D03 t01 (raw)" and (t["shot"], t["take"]) == ("D03", "t01")
    assert analyze.resolve_target(str(polished))["label"] == "D03 t01 (polished)"
    other = tmp_path / "loose.wav"
    write_rich(other, TEXT, seed=4)
    assert analyze.resolve_target(str(other))["label"] == "loose"  # not a film's take: its file name, as before
    res = analyze.run([str(polished)], ab_ref=str(raw), sheet=True, text=TEXT, out=tmp_path / "ab")
    assert res["reference"] == "D03 t01 (raw)"
    assert [t["label"] for t in res["takes"]] == ["D03 t01 (raw)", "D03 t01 (polished)"]
