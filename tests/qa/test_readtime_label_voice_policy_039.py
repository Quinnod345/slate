"""Voice-matched labels retain both the spoken-text floor and explicit reading policies.

The script and picture gates must honor the same stronger floor at this intersection. The fixtures use actual
declarations and measured words, with only the OCR sweep replaced by deterministic samples.
"""

import pytest

from slate.qa.checks import text
from tests.qa.test_readtime_short_labels_039 import LABELS, _format, _script, _write, label_film


@pytest.mark.parametrize("policy_source", ["script", "format"])
@pytest.mark.parametrize("floor,settled", [(0.5, 1.25), (2.0, 1.5)])
def test_voice_matched_labels_keep_the_stronger_policy_floor(label_film, tmp_path, policy_source, floor, settled):
    policy = {"floor": floor}
    fmt = _format(tmp_path, policy) if policy_source == "format" else None
    film, script, _sweep = label_film(
        "Ready now", reading={**LABELS, "voice_matched": True},
        visible_for=settled, settled=settled,
        rules=policy if policy_source == "script" else None, fmt=fmt,
    )
    _write(film, "timing/vo_words.json", {"shots": {"S01": {"line": "Ready now", "words": [
        {"w": "Ready", "start": 0.0, "end": 0.3}, {"w": "now", "start": 0.3, "end": 0.75},
    ]}}})
    _write(film, "timing/vo_cues.json", {"cues": [{"shot": "S01", "at": 0.0, "file": "voice/fixture.wav"}]})
    assert _script(script, fmt)["script.read_time"].status == "FAIL"
    result = text.readtime(film)
    assert result.status == "FAIL", result.to_dict()
    assert f"needs {max(1.5, floor):.2f} s, voice-matched" in result.details["rows"][0]
