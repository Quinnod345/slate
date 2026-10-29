"""The OCR reader process: a frame Vision never returns from costs a timeout and a fresh reader, never the run.

A fake `ocrmac` on PYTHONPATH stands in for Vision inside the real reader process, so the protocol, the timeout, the
retries and the recycling all run for real. The first pixel's red value picks what the fake does with a frame.
"""

from __future__ import annotations

import time

import pytest
from PIL import Image

from slate.qa import ocr

FAKE = '''
import os, time

BLIND = False

class OCR:
    def __init__(self, image, framework=None, recognition_level=None):
        self.image = image

    def recognize(self):
        global BLIND
        if BLIND:  # a blind reader reads nothing at all, the test image included
            return []
        if self.image.size == (720, 180):  # the canary
            return [("SLATE 42", 0.99, [0.1, 0.1, 0.5, 0.2])]
        mode = self.image.getpixel((0, 0))[0]
        if mode == 5:  # goes blind on the first frame it ever sees like this, until it's replaced
            marker = os.environ["FAKE_OCR_MARKER"]
            if not os.path.exists(marker):
                open(marker, "w").close()
                BLIND = True
                return []
        if mode == 6:  # a frame with no text on it
            return []
        if mode == 1:  # never answers
            time.sleep(3600)
        if mode == 2:  # never answers the first time it sees a frame, then reads it
            marker = os.environ["FAKE_OCR_MARKER"]
            if not os.path.exists(marker):
                open(marker, "w").close()
                time.sleep(3600)
        if mode == 3:  # Vision's diagnostics, from native code and from Python
            os.write(1, b"Unable to find a valid E5 model\\n")
            print("more noise", flush=True)
        if mode == 4:
            raise ValueError("bad frame")
        return [("Hello there", 0.91234, [0.1, 0.2, 0.3, 0.45678])]
'''


@pytest.fixture
def fake_vision(tmp_path, monkeypatch, slate_home):
    pkg = tmp_path / "fake" / "ocrmac"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "ocrmac.py").write_text(FAKE)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fake"))
    monkeypatch.setenv("FAKE_OCR_MARKER", str(tmp_path / "stalled-once"))
    monkeypatch.setenv("SLATE_OCR_TIMEOUT", "1.5")
    backend = ocr.VisionBackend("accurate", {"name": "fake Vision", "version": "test"})
    yield backend
    if backend.reader:
        backend.reader.stop()


def frame(mode: int) -> Image.Image:
    im = Image.new("RGB", (64, 36), "white")
    im.putpixel((0, 0), (mode, 0, 0))
    return im


def test_ocr_reader_answers_cleanly_through_visions_own_noise(fake_vision):
    rows = fake_vision.recognize(frame(3))
    assert rows == [{"text": "Hello there", "conf": 0.912, "box": [0.1, 0.2, 0.3, 0.4568]}]
    assert fake_vision.recognize(frame(0)) == rows  # the same reader goes on answering
    assert fake_vision.reader.started == 1


def test_ocr_reader_replaces_a_stalled_reader_and_reads_the_frame_again(fake_vision):
    t0 = time.monotonic()
    rows = fake_vision.recognize(frame(2))
    assert rows[0]["text"] == "Hello there"
    assert fake_vision.reader.started == 2
    assert time.monotonic() - t0 < 10


def test_ocr_reader_stops_with_ocr_stalled_when_every_fresh_reader_stalls(fake_vision, monkeypatch):
    monkeypatch.setattr(ocr, "RETRIES", 1)
    with pytest.raises(ocr.OcrStalled) as caught:
        fake_vision.recognize(frame(1))
    assert caught.value.exit_code == 75
    assert caught.value.details["readers"] == 2
    assert fake_vision.reader.started == 2
    assert fake_vision.reader.proc is None  # the stalled reader is gone, not left running


def test_ocr_reader_replaces_a_blind_reader_and_reads_the_frame_again(fake_vision):
    rows = fake_vision.recognize(frame(5))
    assert rows[0]["text"] == "Hello there"
    assert fake_vision.reader.started == 2


def test_ocr_reader_trusts_an_empty_frame_when_the_test_image_still_reads(fake_vision):
    assert fake_vision.recognize(frame(6)) == []
    assert fake_vision.reader.started == 1


def test_ocr_reader_reports_visions_own_error(fake_vision):
    with pytest.raises(RuntimeError, match="bad frame"):
        fake_vision.recognize(frame(4))
    assert fake_vision.recognize(frame(0))[0]["text"] == "Hello there"
    assert fake_vision.reader.started == 1


def test_ocr_reader_is_replaced_after_recycle_frames(fake_vision, monkeypatch):
    monkeypatch.setattr(ocr, "RECYCLE", 2)
    for _ in range(3):
        fake_vision.recognize(frame(0))
    assert fake_vision.reader.started == 2


def test_ocr_timeout_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SLATE_OCR_TIMEOUT", "12")
    assert ocr.timeout() == 12.0
    for bad in ("soon", "0", "-3"):
        monkeypatch.setenv("SLATE_OCR_TIMEOUT", bad)
        assert ocr.timeout() == ocr.TIMEOUT
