"""Neutral array and report regressions for photometric ambiguity; no media decoding or child work."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import json
import os
import socket
import subprocess

import numpy as np
import pytest

from slate import gates, jsonio, procs
from slate.qa import accept, motion as M

SIZE = (1080, 1920)  # Neutral fixture geometry, never a production frame-size default.
ALPHA = (0.884075, 0.929535, 0.957908)


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    attempted = []

    def deny(name):
        def blocked(*args, **kwargs):
            attempted.append(name)
            raise AssertionError(f"Neutral test attempted {name}")
        return blocked

    for module, name in [(procs, "run"), (procs, "spawn_detached"), (subprocess, "Popen"),
                         (subprocess, "run"), (os, "system"), (socket, "create_connection")]:
        monkeypatch.setattr(module, name, deny(f"{module.__name__}.{name}"))
    monkeypatch.setattr(socket.socket, "connect", deny("socket.socket.connect"))
    yield
    assert attempted == [], "A caught external-work attempt cannot count as a passing test."


def _heading_mask():
    from PIL import Image, ImageDraw, ImageFont

    w, h = SIZE
    image = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(image)
    font, subfont = ImageFont.load_default(size=79), ImageFont.load_default(size=34)
    text, sub = "Stationary. Local controls.", "Illustrative local controls"
    draw.text(((w - draw.textlength(text, font=font)) / 2, 288), text, font=font, fill=255)
    draw.text(((w - draw.textlength(sub, font=subfont)) / 2, 394), sub, font=subfont, fill=255)
    return image


def _heading_frames(method, shutter=False):
    """Known one-pixel upward motion: composite and quantize before resizing, as a raster pipeline does."""
    from PIL import Image
    from scipy import ndimage

    size = M.analysis_size(*SIZE)
    high = np.asarray(_heading_mask(), dtype=np.float32) / 255.0
    frames = []
    for k, alpha in enumerate(ALPHA):
        fractions = (0.0, 0.125, 0.25, 0.375) if shutter else (0.0,)
        masks = [ndimage.shift(high, (-k - fraction, 0), order=1, mode="constant", cval=0)
                 for fraction in fractions]
        composite = np.mean([0.08 + (0.9 - 0.08) * mask * alpha for mask in masks], axis=0)
        raster = np.clip(np.rint(composite * 255.0), 0, 255).astype(np.uint8)
        mode = Image.Resampling.LANCZOS if method == "lanczos" else Image.Resampling.BICUBIC
        frames.append(np.asarray(Image.fromarray(raster).resize(size, mode), dtype=np.float32) / 255.0)
    return frames


@pytest.fixture(scope="module", params=[pytest.param(("lanczos", False), id="lanczos"),
                                       pytest.param(("bicubic", True), id="bicubic-shutter")])
def slow_heading(request):
    return _heading_frames(*request.param)


def _scan(frames, diagnostics=True):
    return M.fast_pairs(frames, film_short=min(SIZE), speed_limit=M.limits(*SIZE)["speed"],
                        analysis_short=min(M.analysis_size(*SIZE)), diagnostics=diagnostics)


def test_quantized_slow_heading_fade_is_not_a_fast_translation(slow_heading):
    # Original detector: two coherent consecutive false matches above 45 px/frame. Physical motion is 1 px/frame.
    pairs, summary = _scan(slow_heading)
    assert pairs == []
    assert summary["pairs"] == 2 and summary["cuts"] == 0
    assert summary["fast_over"] == pytest.approx(37.5)


def test_slow_heading_retains_raw_vector_but_rejects_photometric_ambiguity(slow_heading):
    captured = {}
    detailed = M.block_motion(*slow_heading[:2], block=4, radius=7, diagnostics=captured)
    ordinary = M.block_motion(*slow_heading[:2], block=4, radius=7)
    for a, b in zip(detailed, ordinary):
        np.testing.assert_array_equal(a, b)
    dx, dy, trusted, _ = detailed
    ambiguous = captured["photometric_ambiguous"]
    raw_eligible = (captured["textured"] & captured["distinct"] & ~captured["search_edge"]
                    & captured["explains_change"])
    raw_fast = np.hypot(dx, dy) > 37.5 / 22.5
    assert np.any(ambiguous & raw_eligible & raw_fast)
    assert not trusted[ambiguous].any()
    assert np.all(captured["same_gain_valid"][ambiguous])
    assert np.all(captured["matched_gain_valid"][ambiguous])
    assert np.all(captured["same_gain_sad"][ambiguous] + 1e-6 * 16
                  <= M.PIXEL["ambiguity"] * captured["matched_gain_sad"][ambiguous])


def _physical_frames(kind, fade=False, count=7):
    from PIL import Image

    w, h = M.analysis_size(*SIZE)
    if kind == "texture":
        base = np.random.default_rng(19).random((h, w), dtype=np.float32) * 0.65 + 0.15
    else:
        mask = np.asarray(_heading_mask().resize((w, h), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
        base = mask
    frames = []
    for k in range(count):
        gain = 0.4 + 0.08 * k if fade else 0.96
        shifted = np.roll(base, 3 * k, axis=1)
        frames.append(shifted * gain if kind == "texture" else 0.08 + (0.9 - 0.08) * shifted * gain)
    return frames


@pytest.mark.parametrize("kind,fade", [pytest.param("texture", False, id="texture"),
                                      pytest.param("texture", True, id="texture-fade"),
                                      pytest.param("heading", False, id="heading"),
                                      pytest.param("heading", True, id="heading-fade")])
def test_physical_fast_translation_stays_visible(kind, fade):
    pairs, _ = _scan(_physical_frames(kind, fade))
    assert len(pairs) == 6
    assert np.median([p["speed"] for p in pairs]) == pytest.approx(67.5, rel=0.12)
    assert all(p["blocks"] >= M.PIXEL["min_blocks"] for p in pairs)


@pytest.mark.parametrize("kind", ["periodic", "slow", "stationary", "global-fade", "local-fade"])
def test_existing_quiet_and_repetitive_guards_remain(kind):
    from PIL import Image
    from scipy import ndimage

    w, h = M.analysis_size(*SIZE)
    base = np.random.default_rng(19).random((h, w), dtype=np.float32) * 0.65 + 0.15
    if kind == "periodic":
        pattern = np.tile(np.array([0.15, 0.25, 0.85, 0.75], dtype=np.float32), (h, (w + 3) // 4))[:, :w]
        frames = [np.roll(pattern, 3 * k, axis=1) for k in range(7)]
    elif kind == "slow":
        frames = [ndimage.shift(base, (0, 4 * k / 22.5), order=1, mode="nearest") for k in range(7)]
    elif kind == "stationary":
        frames = [base] * 7
    elif kind == "global-fade":
        frames = [base * (0.4 + 0.08 * k) for k in range(7)]
    else:
        mask = np.asarray(_heading_mask().resize((w, h), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
        frames = [0.08 + (0.9 - 0.08) * mask * (0.4 + 0.08 * k) for k in range(7)]
    pairs, summary = _scan(frames)
    assert pairs == []
    assert summary["cuts"] == (6 if kind == "periodic" else 0)


@pytest.mark.parametrize("kind", ["positive", "zero", "negative", "nonfinite"])
def test_gain_fit_needs_a_finite_positive_explanation(kind):
    a = np.array([[1.0, -1.0], [2.0, -2.0]], dtype=np.float32)
    b = 2 * a if kind == "positive" else np.zeros_like(a) if kind == "zero" else -a
    if kind == "nonfinite":
        b = a.copy()
        b[0, 0] = np.inf
    gain, residual, valid = M._positive_gain_fit(a, b)
    if kind == "positive":
        assert bool(valid) and float(gain) == pytest.approx(0.5) and float(residual) == 0.0
    else:
        assert not bool(valid) and np.isposinf(residual)


def test_retained_fast_diagnostics_keep_raw_residuals_and_symmetric_fits():
    frames = _physical_frames("texture", True)
    pairs, summary = _scan(frames)
    assert summary["diagnostic_format"]["schema"] == 2
    item = pairs[0]["diagnostics"]["blocks"][0]
    a, b = frames[pairs[0]["pair"]:pairs[0]["pair"] + 2]
    aa, bb = M._bandpass(a), M._bandpass(b)
    row, col = item["row"], item["col"]
    y, x, block, radius = row * 4, col * 4, 4, summary["search"]
    dx, dy = item["integer_shift"]
    p = aa[y:y + block, x:x + block]
    padded = np.pad(bb, radius, mode="edge")
    q0 = bb[y:y + block, x:x + block]
    qb = padded[y + radius + dy:y + radius + dy + block,
                x + radius + dx:x + radius + dx + block]
    assert item["residuals"]["zero"] == pytest.approx(float(np.abs(p - q0).sum()), rel=1e-5, abs=1e-6)
    assert item["residuals"]["best"] == pytest.approx(float(np.abs(p - qb).sum()), rel=1e-5, abs=1e-6)
    for name, q in [("zero", q0), ("matched", qb)]:
        gain = float(np.sum(p * q) / np.sum(q * q))
        fitted = float(np.sum(np.abs(p - gain * q)))
        assert item["photometric"][name]["gain"] == pytest.approx(gain, rel=1e-5, abs=1e-6)
        assert item["photometric"][name]["sad"] == pytest.approx(fitted, rel=1e-5, abs=1e-6)
        assert item["photometric"][name]["valid"]
    assert item["photometric"]["model"] == "positive_gain"
    assert not item["photometric"]["ambiguous"] and item["trust"]["trusted"]
    json.loads(json.dumps(pairs[0]["diagnostics"], allow_nan=False))


class _MemoryPath:
    def __init__(self, name, *, file=False):
        self.name, self.file = name, file

    def __str__(self):
        return self.name

    def __fspath__(self):
        return self.name

    def exists(self):
        return True

    def is_file(self):
        return self.file


def test_old_pixel_scan_is_unchecked_in_quick_mode_then_recomputed_once(monkeypatch, tmp_path):
    source, cache = _MemoryPath("neutral-array-source"), _MemoryPath("neutral-cache", file=True)
    state = {"writes": 0, "reads": 0}
    old_key = {"file": str(source), "sha256": "picture-hash", "film_frame": 100, "frames": 3,
               "analysis": list(M.analysis_size(*SIZE)), "film_short": min(SIZE),
               "speed_limit": M.limits(*SIZE)["speed"], "pixel": deepcopy(M.PIXEL), "version": M.__version__,
               "diagnostic_format": {"schema": 1, "max_blocks_per_pair": 32}}
    state["cache"] = {"key": old_key, "scan": {"pairs": [], "summary": {
        "pairs": 2, "cuts": 0, "fastest_measured": 0.0, "block": 4, "search": 7,
        "analysis_short_side": 48, "fast_over": 37.5, "measurable_up_to": 157.5,
        "lone_fast_pairs_ignored": 0, "diagnostic_format": {"schema": 1, "max_blocks_per_pair": 32}}}}
    ctx = SimpleNamespace(dir=tmp_path, quick=True, rel=lambda p: str(p), path=lambda rel: cache,
                          sha=lambda p: "registry-hash")
    data = M.MotionData(size=SIZE, acts=[1])
    monkeypatch.setattr(M, "render_sources", lambda *args, **kwargs: ([M.RenderSource(1, source, 100, 3)], []))
    monkeypatch.setattr(M, "source_digest", lambda *args: "picture-hash")
    monkeypatch.setattr(M.jsonio, "read", lambda *args, **kwargs: deepcopy(state["cache"]))
    monkeypatch.setattr(accept, "match", lambda *args, **kwargs: None)

    def write(path, value):
        state["writes"] += 1
        state["cache"] = deepcopy(value)

    def read(*args, **kwargs):
        state["reads"] += 1
        return iter(_heading_frames("lanczos"))

    monkeypatch.setattr(M.jsonio, "write_atomic", write)
    monkeypatch.setattr(M, "read_gray", read)
    old_bytes = deepcopy(state["cache"])
    first = M.check_pixels(ctx, data)
    assert first.status == "UNCHECKED" and "--quick" in first.summary
    assert state["reads"] == state["writes"] == 0 and state["cache"] == old_bytes
    ctx.quick = False
    fresh = M.check_pixels(ctx, data)
    assert fresh.status == "PASS"
    assert state["reads"] == state["writes"] == 1
    assert state["cache"]["key"]["pixel_producer"] == M.PIXEL_PRODUCER
    assert fresh.details["scans"][0]["pixel_producer"] == M.PIXEL_PRODUCER
    assert state["cache"]["key"]["diagnostic_format"]["schema"] == 2
    ctx.quick = True
    again = M.check_pixels(ctx, data)
    assert again.to_dict() == fresh.to_dict()
    assert state["reads"] == state["writes"] == 1


@pytest.mark.parametrize("revision", [None, 0, 2, True, 1.0, "1"], ids=[
    "absent", "old", "different", "bool", "float", "string"])
def test_old_motion_result_is_stale_without_mutating_report_history(tmp_path, revision):
    engine = {"name": "previous neutral detector", "version": "fixture"}
    if revision is not None:
        engine["rule_revision"] = revision
    result = gates.GateResult("motion", "PASS", "old array check passed", engine=engine,
                              details={"measurement": {"source": "neutral"}})
    gates.write_report(tmp_path, [result])
    report, history = tmp_path / "qa/gates.json", tmp_path / "qa/history.jsonl"
    before = report.read_bytes(), history.read_bytes()
    loaded = gates.load_report(tmp_path)["motion"]
    assert loaded.status == "STALE" and loaded.details["was"] == "PASS"
    assert "rule revision" in loaded.summary
    assert loaded.engine == engine
    assert (report.read_bytes(), history.read_bytes()) == before
    assert result.status == "PASS" and "was" not in result.details


def test_matching_motion_revision_remains_valid(tmp_path):
    result = gates.GateResult("motion", "PASS", "current neutral check", engine={"rule_revision": 1})
    gates.write_report(tmp_path, [result])
    assert gates.rule_revision("motion") == 1
    assert gates.load_report(tmp_path)["motion"].to_dict() == result.to_dict()


@pytest.mark.parametrize("name,engine", [
    pytest.param("handin.act2.motion", {}, id="trace-handin"),
    pytest.param("handin.act2.shots", {"rule_revision": 1}, id="shot-handin"),
    pytest.param("access", {"rule_revision": 1}, id="access"),
    pytest.param("sound.voice_limiter", {"rule_revision": 1}, id="sound"),
    pytest.param("generic", {}, id="generic")])
def test_unrelated_producers_keep_their_existing_readback(tmp_path, name, engine):
    result = gates.GateResult(name, "PASS", "neutral existing check", engine=engine)
    gates.write_report(tmp_path, [result])
    assert gates.load_report(tmp_path)[name].to_dict() == result.to_dict()
    assert gates.rule_revision("handin.act2.motion") is None
    assert gates.rule_revision("handin.act2.shots") == 1
    assert gates.rule_revision("access") == gates.rule_revision("sound.voice_limiter") == 1


def test_current_motion_result_still_goes_stale_when_its_input_changes(tmp_path):
    source = tmp_path / "neutral-input.txt"
    source.write_text("first", encoding="utf-8")
    result = gates.GateResult("motion", "PASS", "neutral check", engine={"rule_revision": 1},
                              inputs=gates.inputs_for(tmp_path, ["neutral-input.txt"]))
    gates.write_report(tmp_path, [result])
    source.write_text("second", encoding="utf-8")
    loaded = gates.load_report(tmp_path)["motion"]
    assert loaded.status == "STALE" and "inputs changed" in loaded.summary


def _context(monkeypatch, tmp_path):
    from slate.qa import film

    ctx = SimpleNamespace(dir=tmp_path, film={"id": "neutral"}, acts=[{"index": 1}], brand=None,
                          path=lambda rel: tmp_path / rel, inputs=lambda paths: {})
    data = M.MotionData(size=SIZE, acts=[1], traced=[1])
    monkeypatch.setattr(film, "context", lambda _dir: ctx)
    monkeypatch.setattr(M, "load_data", lambda *args, **kwargs: data)
    monkeypatch.setattr(M, "CHECKS", {"trace": lambda data, ctx: M._sub("trace", "PASS", "neutral trace")})
    return ctx


def test_fresh_emitter_stamps_rule_revision_even_when_pixels_cannot_pass(monkeypatch, tmp_path):
    _context(monkeypatch, tmp_path)
    result = M.evaluate(tmp_path, pixels=False)
    assert result.status == "UNCHECKED"
    assert result.engine["rule_revision"] == gates.rule_revision("motion") == 1
    assert result.engine["pixel_producer"] == M.PIXEL_PRODUCER
    gates.write_report(tmp_path, [result])
    assert gates.load_report(tmp_path)["motion"].to_dict() == result.to_dict()


def test_trace_only_handin_keeps_its_original_producer(monkeypatch, tmp_path):
    _context(monkeypatch, tmp_path)
    jsonio.write_atomic(tmp_path / "timing/motion/act1.json", [])
    monkeypatch.setattr(M, "_trace_state", lambda *args: ("fresh", []))
    result = M.handin_gate(tmp_path, 1)
    assert result.status == "PASS" and result.gate == "handin.act1.motion"
    assert result.engine == gates.engine("slate qa motion (the trace checks, no pixels)", M.__version__)
    assert "rule_revision" not in result.engine and "pixel_producer" not in result.engine
