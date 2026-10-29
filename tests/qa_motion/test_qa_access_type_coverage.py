"""Eligible body text needs measured pixels before the type-size check can pass.

The scripted OCR and decoder isolate missing-evidence paths without loading a model or
running a media process. Existing pixel tests verify the cap-height measurement itself.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from slate.qa import access as A
from slate.qa import media, ocr


def _sample(frame=0, text="Body copy reads well here", *, box=None):
    return {
        "frame": frame,
        "label": f"frame {frame}",
        "lines": [{"text": text, "conf": 0.99, "box": box or [0.1, 0.4, 0.7, 0.2]}],
    }


@pytest.fixture
def type_check(monkeypatch):
    """Run the real check with explicit OCR samples and a controlled decoder."""
    ctx = SimpleNamespace(size=(100, 100), shot_of_frame=lambda frame: "S1")
    image = np.full((100, 100), 20, dtype=np.uint8)

    def run(samples, *, decoded=None, parts=None, cap=20.0, error=None):
        decoder_calls = []

        def decode(path, indices, *, pix_fmt):
            requested = list(indices)
            decoder_calls.append((path, requested, pix_fmt))
            if error is not None:
                raise error
            return {frame: image for frame in requested} if decoded is None else decoded

        monkeypatch.setattr(A, "_read_text_boxes",
                            lambda *args: ((samples, {"name": "scripted OCR"}, ["picture.mov"]), None))
        monkeypatch.setattr(A, "_not_for_reading", lambda ctx: set())
        monkeypatch.setattr(ocr, "_sources",
                            lambda ctx: (None, [(Path("picture.mov"), 0, 1000)] if parts is None else parts, []))
        monkeypatch.setattr(media, "read_frames", decode)
        monkeypatch.setattr(A, "cap_height", lambda crop: cap)
        return A.check_type_size(ctx), decoder_calls

    return run


def test_detected_body_text_without_a_decoded_frame_is_unchecked(type_check):
    result, _ = type_check([_sample()], decoded={})
    assert result.status == "UNCHECKED", result.summary
    assert result.details["unique_candidates"] == result.details["unmeasured"] == 1
    assert result.details["measured"] == 0
    assert result.details["omission_counts"] == {"frame_not_decoded": 1}
    assert result.details["coverage_complete"] is False


def test_missing_one_witness_prevents_pass_from_another_large_line(type_check):
    result, _ = type_check([_sample(0), _sample(1, "Another body line is here")],
                           decoded={0: np.full((100, 100), 20, dtype=np.uint8)})
    assert result.status == "UNCHECKED", result.summary
    assert result.details["measured"] == result.details["unmeasured"] == 1


def test_an_observed_small_line_still_fails_when_another_witness_is_missing(type_check):
    result, _ = type_check([_sample(0), _sample(1, "Another body line is here")],
                           decoded={0: np.full((100, 100), 20, dtype=np.uint8)}, cap=1.0)
    assert result.status == "FAIL", result.summary
    assert len(result.details["small"]) == 1
    assert result.details["coverage_complete"] is False


def test_body_candidate_outside_picture_parts_is_unchecked(type_check):
    result, calls = type_check([_sample(20)], parts=[(Path("picture.mov"), 0, 10)])
    assert result.status == "UNCHECKED", result.summary
    assert result.details["omission_counts"] == {"source_frame_unmapped": 1}
    assert not calls


def test_body_candidate_without_a_frame_is_unchecked(type_check):
    result, calls = type_check([_sample(None)])
    assert result.status == "UNCHECKED", result.summary
    assert not calls


@pytest.mark.parametrize("cap", [None, float("nan"), float("inf"), 0.0, -1.0, True])
def test_unavailable_or_invalid_cap_measurement_is_unchecked(type_check, cap):
    result, _ = type_check([_sample()], cap=cap)
    assert result.status == "UNCHECKED", result.summary


def test_empty_body_crop_is_unchecked(type_check):
    result, _ = type_check([_sample(box=[0.2, 0.4, 0.0, 0.2])])
    assert result.status == "UNCHECKED", result.summary


def test_decoder_failure_is_reported_as_missing_measurement(type_check):
    result, _ = type_check([_sample()], error=media.MediaError("Fixture decode failed."))
    assert result.status == "UNCHECKED", result.summary


def test_candidate_limit_cannot_hide_an_additional_unmeasured_line(type_check):
    samples = [_sample(i, f"Body copy candidate number {i}") for i in range(401)]
    result, _ = type_check(samples)
    assert result.status == "UNCHECKED", result.summary
    assert result.details["unique_candidates"] == 401
    assert result.details["selected_candidates"] == result.details["measured"] == 400
    assert result.details["unmeasured"] == 1
    assert result.details["omission_counts"] == {"candidate_limit": 1}


def test_all_selected_witnesses_measured_at_sufficient_size_pass(type_check):
    result, _ = type_check([_sample(0), _sample(1, "Another body line is here")])
    assert result.status == "PASS", result.summary
    assert result.details["measured"] == 2
    assert result.details["unmeasured"] == 0 and result.details["coverage_complete"] is True


def test_genuinely_no_body_candidates_keep_the_existing_pass(type_check):
    result, calls = type_check([_sample(0, "Two words"), _sample(1, "BODY COPY IN CAPITALS")])
    assert result.status == "PASS", result.summary
    assert not calls


def test_repeated_same_text_and_box_keep_one_declared_measurement(type_check):
    result, calls = type_check([_sample(0), _sample(1)])
    assert result.status == "PASS", result.summary
    assert result.details["measured"] == 1
    assert result.details["eligible"] == 2 and result.details["unique_candidates"] == 1
    assert result.details["duplicate_candidates"] == 1
    assert Counter(frame for _path, frames, _fmt in calls for frame in frames) == {0: 1}


def _faint_run(monkeypatch, samples):
    """The real check with frame 1 decoded as a line caught mid-fade (no measurable ink) and every other frame inked."""
    ctx = SimpleNamespace(size=(100, 100), shot_of_frame=lambda frame: "S1")

    def decode(path, indices, *, pix_fmt):
        return {f: np.full((100, 100), 21 if f == 1 else 20, dtype=np.uint8) for f in indices}

    monkeypatch.setattr(A, "_read_text_boxes", lambda *args: ((samples, {"name": "scripted OCR"}, ["picture.mov"]), None))
    monkeypatch.setattr(A, "_not_for_reading", lambda ctx: set())
    monkeypatch.setattr(ocr, "_sources", lambda ctx: (None, [(Path("picture.mov"), 0, 1000)], []))
    monkeypatch.setattr(media, "read_frames", decode)
    monkeypatch.setattr(A, "cap_height", lambda crop: None if float(crop.mean()) == 21 else 20.0)
    return A.check_type_size(ctx)


def test_a_faint_duplicate_of_a_measured_line_is_settled(monkeypatch):
    # the same headline, read whole at frame 0 and misread while it fades out at frame 1, in the same place
    result = _faint_run(monkeypatch, [_sample(0, "Why does that matter here"), _sample(1, "Vhy does that matter here")])
    assert result.status == "PASS", result.summary
    assert result.details["unmeasured"] == 0 and len(result.details["faint_duplicates"]) == 1


def test_a_faint_line_with_no_measured_twin_stays_unchecked(monkeypatch):
    result = _faint_run(monkeypatch, [_sample(0, "Why does that matter here"),
                                      _sample(1, "Another body line is here", box=[0.1, 0.05, 0.7, 0.2])])
    assert result.status == "UNCHECKED", result.summary
    assert result.details["omission_counts"] == {"no_cap_measurement": 1}



def _sliver_run(monkeypatch, samples, sliver_cap=1.0):
    """As _faint_run, but frame 1's crop measures a sliver (specks left as the line fades), not nothing. The film is
    100 px tall, so its floor is 1.6 px of capitals: the 1 px sliver would fail if it were measured as a line."""
    ctx = SimpleNamespace(size=(100, 100), shot_of_frame=lambda frame: "S1")

    def decode(path, indices, *, pix_fmt):
        return {f: np.full((100, 100), 21 if f == 1 else 20, dtype=np.uint8) for f in indices}

    monkeypatch.setattr(A, "_read_text_boxes", lambda *args: ((samples, {"name": "scripted OCR"}, ["picture.mov"]), None))
    monkeypatch.setattr(A, "_not_for_reading", lambda ctx: set())
    monkeypatch.setattr(ocr, "_sources", lambda ctx: (None, [(Path("picture.mov"), 0, 1000)], []))
    monkeypatch.setattr(media, "read_frames", decode)
    monkeypatch.setattr(A, "cap_height", lambda crop: sliver_cap if float(crop.mean()) == 21 else 20.0)
    return A.check_type_size(ctx)


def test_ink_far_shorter_than_its_measured_twin_is_a_faint_duplicate(monkeypatch):
    # frame 1's crop of the same 20 px box measures 1 px: that isn't the line's letters, it's the line fading out
    result = _sliver_run(monkeypatch, [_sample(0, "Why does that matter here"), _sample(1, "Vhy does that matter here")])
    assert result.status == "PASS", result.summary
    assert len(result.details["faint_duplicates"]) == 1 and result.details["measured"] == 1


def test_a_sliver_with_no_measured_twin_is_measured_and_fails(monkeypatch):
    result = _sliver_run(monkeypatch, [_sample(0, "Why does that matter here"),
                                       _sample(1, "Another body line is here", box=[0.1, 0.05, 0.7, 0.2])])
    assert result.status == "FAIL", result.summary
    assert not result.details["faint_duplicates"]
