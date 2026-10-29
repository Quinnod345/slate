"""Neutral in-memory controls for additive motion evidence; no render, decode or production input is needed."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from slate.errors import MissingPrerequisite
from slate.qa import accept, checks
from slate.qa import motion as M


def _frames(kind, count=7):
    base = np.random.default_rng(19).random((48, 64), dtype=np.float32)
    y, x = np.indices(base.shape)
    frames = []
    for k in range(count):
        if kind == "translation":
            frame = np.roll(base, 3 * k, axis=1)
        elif kind == "fade":
            frame = base * (1 - 0.03 * k)
        elif kind == "aperture":
            frame = base * ((x - 32) ** 2 + (y - 24) ** 2 <= (8 + 2 * k) ** 2)
        elif kind == "composite":
            panel = (np.abs(x - (30 + k)) <= 20 + k) & (np.abs(y - 24) <= 16 + k)
            frame = np.roll(base, 3 * k, axis=1) * panel * (1 - 0.02 * k)
        else:
            raise ValueError(kind)
        frames.append(frame.astype(np.float32))
    return frames


def _scan(frames, *, diagnostics=False):
    return M.fast_pairs(frames, film_short=48, speed_limit=M.limits(64, 48)["speed"], analysis_short=48,
                        block=4, diagnostics=diagnostics)


def _legacy_pairs(pairs):
    return [{k: v for k, v in pair.items() if k != "diagnostics"} for pair in pairs]


def _residuals(a, b, row, col, block, radius, shift):
    """Recompute one block's residuals directly from its windows, independently of the collector."""
    aa, bb = M._bandpass(a), M._bandpass(b)
    padded = np.pad(bb, radius, mode="edge")
    y, x = row * block, col * block
    patch = aa[y:y + block, x:x + block]
    values = {}
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            candidate = padded[y + radius + dy:y + radius + dy + block,
                               x + radius + dx:x + radius + dx + block]
            values[(dx, dy)] = float(np.abs(patch - candidate).sum())
    dx, dy = shift
    second = min(v for (sx, sy), v in values.items() if abs(sx - dx) > 1 or abs(sy - dy) > 1)
    return values[(0, 0)], values[(dx, dy)], second, min(values.values())


@pytest.mark.parametrize("kind", ["translation", "fade", "aperture", "composite"])
def test_block_collector_retains_four_arrays_and_actual_residuals(kind):
    a, b = _frames(kind)[:2]
    legacy = M.block_motion(a, b, block=4, radius=7)
    captured = {"previous": "must be cleared"}
    detailed = M.block_motion(a, b, block=4, radius=7, diagnostics=captured)
    assert len(legacy) == len(detailed) == 4 and "previous" not in captured
    for before, after in zip(legacy, detailed):
        np.testing.assert_array_equal(before, after)
    # Use an actually changed textured block rather than relying on whether this deformation is called fast.
    candidates = np.argwhere(captured["textured"] & (captured["zero_sad"] > 1e-6))
    assert len(candidates)
    row, col = map(int, candidates[len(candidates) // 2])
    shift = (int(captured["integer_dx"][row, col]), int(captured["integer_dy"][row, col]))
    zero, best, second, unconstrained_best = _residuals(a, b, row, col, 4, 7, shift)
    for field, expected in [("zero_sad", zero), ("best_sad", best), ("second_sad", second)]:
        assert float(captured[field][row, col]) == pytest.approx(expected, rel=1e-5, abs=1e-6)
    still = zero <= unconstrained_best + max(1e-6, 0.02 * unconstrained_best)
    assert bool(captured["preferred_still"][row, col]) == still
    moved = shift != (0, 0)
    edge = abs(shift[0]) == 7 or abs(shift[1]) == 7
    distinct = best + 1e-6 * 16 <= M.PIXEL["ambiguity"] * second
    explains = not moved or best <= 0.7 * zero
    textured = float(captured["texture"][row, col]) >= captured["texture_floor"]
    assert bool(captured["moved"][row, col]) == moved
    assert bool(captured["search_edge"][row, col]) == edge
    assert bool(captured["distinct"][row, col]) == distinct
    assert bool(captured["explains_change"][row, col]) == explains
    assert bool(captured["trusted"][row, col]) == (textured and distinct and not edge and explains)


def test_empty_block_grid_still_returns_four_arrays_and_no_stale_evidence():
    tiny = np.zeros((2, 2), dtype=np.float32)
    captured = {"previous": "must be cleared"}
    result = M.block_motion(tiny, tiny, block=4, radius=3, diagnostics=captured)
    assert len(result) == 4 and captured == {}
    assert not result[2].any() and not result[3].any()


@pytest.mark.parametrize("kind", ["translation", "fade", "aperture", "composite"])
def test_diagnostic_payload_preserves_existing_pairs_and_summary(kind):
    frames = _frames(kind)
    legacy_pairs, legacy_summary = _scan(frames)
    pairs, summary = _scan(frames, diagnostics=True)
    assert _legacy_pairs(pairs) == legacy_pairs
    assert {k: v for k, v in summary.items() if k != "diagnostic_format"} == legacy_summary
    for pair in pairs:
        detail = pair["diagnostics"]
        assert detail["blocks_total"] == pair["blocks"]
        assert detail["blocks_retained"] == len(detail["blocks"])
        assert detail["blocks_truncated"] == detail["blocks_total"] - detail["blocks_retained"]
    if kind == "translation":
        assert len(pairs) >= len(frames) - 2
        assert np.median([p["speed"] for p in pairs]) == pytest.approx(3, rel=0.12)
    elif kind == "fade":
        assert pairs == []  # A stationary global fade is not granted a motion exemption by this patch.


def test_serialized_block_values_and_coherence_match_the_actual_pair():
    frames = _frames("translation")
    pairs, summary = _scan(frames, diagnostics=True)
    assert pairs
    pair = pairs[0]
    a, b = frames[pair["pair"]:pair["pair"] + 2]
    radius, block = summary["search"], summary["block"]
    match = {}
    dx, dy, trusted, _ = M.block_motion(a, b, block=block, radius=radius, diagnostics=match)
    eligible = trusted & (np.hypot(dx, dy) > summary["fast_over"])
    for item in pair["diagnostics"]["blocks"]:
        r, c = item["row"], item["col"]
        assert item["origin"] == [c * block, r * block]
        assert item["dx"] == float(dx[r, c]) and item["dy"] == float(dy[r, c])
        assert item["film_speed"] == pytest.approx(float(np.hypot(dx[r, c], dy[r, c])))
        zero, best, second, _ = _residuals(a, b, r, c, block, radius, item["integer_shift"])
        assert item["residuals"] == pytest.approx({"zero": zero, "best": best, "second": second},
                                                 rel=1e-5, abs=1e-6)
        neighbours = 0
        tolerance = item["coherence"]["tolerance"]
        for y in range(max(0, r - 1), min(eligible.shape[0], r + 2)):
            for x in range(max(0, c - 1), min(eligible.shape[1], c + 2)):
                if (y, x) != (r, c) and eligible[y, x]:
                    neighbours += int(abs(dx[y, x] - dx[r, c]) <= tolerance
                                      and abs(dy[y, x] - dy[r, c]) <= tolerance)
        assert item["coherence"]["fast_neighbours"] == neighbours >= 1
        assert item["coherence"]["coherent_fast"] and item["trust"]["trusted"]
        assert not item["trust"]["search_edge"]


def test_diagnostic_cap_reports_real_truncation_without_changing_detection(monkeypatch):
    frames = _frames("translation")
    legacy_pairs, legacy_summary = _scan(frames)
    monkeypatch.setattr(M, "PIXEL_DIAGNOSTICS", {"schema": 1, "max_blocks_per_pair": 2})
    pairs, summary = _scan(frames, diagnostics=True)
    assert _legacy_pairs(pairs) == legacy_pairs
    assert summary["fastest_measured"] == legacy_summary["fastest_measured"]
    assert pairs and any(p["blocks"] > 2 for p in pairs)
    for pair in pairs:
        detail = pair["diagnostics"]
        assert detail["blocks_retained"] == min(2, pair["blocks"])
        assert detail["blocks_truncated"] == max(0, pair["blocks"] - 2)
        if detail["blocks_truncated"]:
            assert detail["truncation_notice"] == f"Showing 2 of {pair['blocks']} fast blocks in row-major order."
        else:
            assert detail["truncation_notice"] is None


class _MemoryPath:
    def __init__(self, name, *, file=False):
        self.name, self.file = name, file

    def __str__(self):
        return self.name

    def exists(self):
        return True

    def is_file(self):
        return self.file


def _memory_cache(monkeypatch, *, quick=False):
    cache_path = _MemoryPath("neutral-cache", file=True)
    state = {"cache": None, "writes": 0}
    ctx = SimpleNamespace(dir="neutral-fixture", quick=quick, rel=lambda p: str(p),
                          path=lambda _rel: cache_path, sha=lambda _p: "registry-hash")
    monkeypatch.setattr(M.jsonio, "read", lambda _p, default=None: deepcopy(state["cache"]))

    def write(_path, value):
        state["cache"], state["writes"] = deepcopy(value), state["writes"] + 1

    monkeypatch.setattr(M.jsonio, "write_atomic", write)
    return ctx, state


def test_diagnostic_cache_identity_never_labels_an_old_summary_as_fresh(monkeypatch):
    ctx, state = _memory_cache(monkeypatch, quick=True)
    source = _MemoryPath("neutral-frame-source")
    old_key = {"sha256": "picture-hash", "version": M.__version__, "pixel": M.PIXEL}
    state["cache"] = {"key": deepcopy(old_key), "scan": {"pairs": [], "summary": {"pairs": 6}}}
    key = {**old_key, "diagnostic_format": dict(M.PIXEL_DIAGNOSTICS)}
    calls = []

    def scan():
        calls.append(1)
        pairs, summary = _scan(_frames("translation"), diagnostics=True)
        return {"pairs": pairs, "summary": summary}

    with pytest.raises(MissingPrerequisite, match="--quick"):
        M.cached_scan(ctx, "motion", source, key, scan, what="fast motion")
    assert not calls and state["writes"] == 0
    ctx.quick = False
    first = M.cached_scan(ctx, "motion", source, key, scan, what="fast motion")
    assert calls == [1] and state["writes"] == 1
    ctx.quick = True
    assert M.cached_scan(ctx, "motion", source, key, scan, what="fast motion") == first
    assert calls == [1] and key["version"] == old_key["version"]
    with pytest.raises(MissingPrerequisite, match="--quick"):
        M.cached_scan(ctx, "motion", source, {**key, "diagnostic_format": {"schema": 2}}, scan, what="fast motion")
    assert calls == [1]


@pytest.mark.parametrize("source_is_file", [False, True])
def test_public_gate_result_keeps_ordinals_review_keys_and_exact_approval_basis(monkeypatch, source_is_file):
    import json

    ctx, state = _memory_cache(monkeypatch)
    source = _MemoryPath("neutral-frame-source", file=source_is_file)
    ctx.sha = lambda p: "picture-hash" if p is source else "registry-hash"
    frames = _frames("translation")
    data = M.MotionData(size=(64, 48), acts=[1])
    monkeypatch.setattr(M, "render_sources", lambda *a, **k: ([M.RenderSource(1, source, 100, len(frames))], []))
    monkeypatch.setattr(M, "source_digest", lambda *a: "picture-hash")
    monkeypatch.setattr(M, "read_gray", lambda *a, **k: iter(frames))
    from slate.qa import media

    monkeypatch.setattr(media, "have", lambda _tool: True)
    accepted = False
    attempts = []

    def match(_dir, gate, key, basis):
        attempts.append((gate, key, deepcopy(basis)))
        return {"by": "Neutral reviewer", "at": "fixture-time", "reason": "Exact source reviewed"} if accepted else None

    monkeypatch.setattr(accept, "match", match)
    sub = M.check_pixels(ctx, data)
    result = checks.combine(M.GATE, [sub], engine={"name": "neutral fixture"}, inputs={},
                            details=M.lifted_review_items([sub]))
    assert result.status == "REVIEW"
    review = result.details["review_items"][0]
    assert review["key"] == "pixels:neutral-frame-source:101-106"
    expected_basis = {"neutral-frame-source": "picture-hash"} if source_is_file else {
        "renders/registry.json": "registry-hash"}
    assert review["basis"] == expected_basis
    scan = result.details["checks"][0]["scans"][0]
    first = scan["fast_pair_diagnostics"][0]
    assert first["source_frames"] == [0, 1] and first["film_frames"] == [100, 101]
    assert first["blocks"] and state["cache"]["key"]["diagnostic_format"] == M.PIXEL_DIAGNOSTICS
    json.dumps(result.to_dict(), allow_nan=False)
    accepted = True
    again = M.check_pixels(ctx, data)
    final = checks.combine(M.GATE, [again], engine={"name": "neutral fixture"}, inputs={},
                           details=M.lifted_review_items([again]))
    assert final.status == "PASS" and not again.details["review_items"]
    assert final.details["accepted"][0]["key"] == review["key"]
    assert final.details["accepted"][0]["basis"] == review["basis"]
    assert attempts[0] == attempts[-1] == (M.GATE, review["key"], review["basis"])
    assert state["writes"] == 1



def test_default_serializer_keeps_row_major_cap_nullable_residuals_and_film_scale():
    import json

    # Reverse insertion into a column-major mask makes storage order and row-major presentation distinct.
    shape, block, scale = (7, 7), 4, 2.5
    holes = {(0, 1), (1, 4), (2, 0), (3, 6), (4, 3), (5, 2), (6, 5)}
    expected = [(r, c) for r in range(7) for c in range(7) if (r, c) not in holes]
    assert len(expected) == 42
    mask = np.zeros(shape, dtype=bool, order="F")
    for r, c in reversed(expected):
        mask[r, c] = True
    dx, dy = np.full(shape, 3.0), np.full(shape, 4.0)
    coherence = {}
    coherent = M.coherent(mask, dx, dy, diagnostics=coherence)
    np.testing.assert_array_equal(coherent, mask)
    match = {
        "zero_sad": np.full(shape, 4.0), "best_sad": np.full(shape, 0.25),
        "second_sad": np.full(shape, 2.0), "integer_dx": np.full(shape, 3),
        "integer_dy": np.full(shape, 4), "texture": np.full(shape, 0.5), "texture_floor": 0.004,
        "textured": mask.copy(), "distinct": mask.copy(), "search_edge": np.zeros(shape, dtype=bool),
        "moved": mask.copy(), "explains_change": mask.copy(),
        "preferred_still": np.zeros(shape, dtype=bool), "trusted": mask.copy(),
    }
    # These sentinels enter only the serializer seam after coherent eligibility is established. They test the
    # nullable residual contract, not a claim that a real matcher trusted a nonfinite measurement.
    first, second, third = expected[:3]
    match["zero_sad"][first] = np.nan
    match["best_sad"][second] = np.inf
    match["second_sad"][third] = -np.inf
    detail = M._fast_block_diagnostics(coherent, dx, dy, match=match, coherence=coherence,
                                       block=block, scale=scale)
    assert detail["block_limit"] == 32  # Exercise the unchanged default; no cap override or monkeypatch.
    assert detail["blocks_total"] == 42 and detail["blocks_retained"] == 32
    assert detail["blocks_truncated"] == 10
    assert detail["truncation_notice"] == "Showing 32 of 42 fast blocks in row-major order."
    retained = detail["blocks"]
    assert [(b["row"], b["col"]) for b in retained] == expected[:32]
    assert [b["origin"] for b in retained] == [[c * block, r * block] for r, c in expected[:32]]
    for item in retained:
        assert item["dx"] == 3.0 and item["dy"] == 4.0
        assert item["film_speed"] == pytest.approx(12.5)  # The known 3-4-5 analysis vector scaled by 2.5.
    assert detail["film_pixels_per_analysis_pixel"] == 2.5
    assert retained[0]["residuals"] == {"zero": None, "best": 0.25, "second": 2.0}
    assert retained[1]["residuals"] == {"zero": 4.0, "best": None, "second": 2.0}
    assert retained[2]["residuals"] == {"zero": 4.0, "best": 0.25, "second": None}
    assert retained[3]["residuals"] == {"zero": 4.0, "best": 0.25, "second": 2.0}
    roundtrip = json.loads(json.dumps(detail, allow_nan=False))
    assert roundtrip["blocks"][0]["residuals"]["zero"] is None
    assert roundtrip["blocks"][1]["residuals"]["best"] is None
    assert roundtrip["blocks"][2]["residuals"]["second"] is None
    assert roundtrip["blocks"][3]["residuals"]["best"] == 0.25
    # Serialization must not rewrite the collector arrays that supplied the measurement evidence.
    assert np.isnan(match["zero_sad"][first])
    assert np.isposinf(match["best_sad"][second])
    assert np.isneginf(match["second_sad"][third])
