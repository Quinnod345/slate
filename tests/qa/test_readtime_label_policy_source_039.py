"""A fresh label check cannot assume a different or malformed declared format policy."""

import json

import pytest

from slate.qa.checks import text
from tests.qa.test_readtime_short_labels_039 import _format, _write, label_film


@pytest.mark.parametrize("damage", ["missing", "invalid-json", "null-policy", "invalid-floor"])
def test_unreadable_declared_format_policy_is_not_assumed(label_film, tmp_path, damage):
    fmt = _format(tmp_path, {"floor": 2.0})
    film, _script, _sweep = label_film("Ready now", fmt=fmt, visible_for=1.0, settled=1.05)
    source = tmp_path / "format.json"
    if damage == "missing":
        source.unlink()
    elif damage == "invalid-json":
        source.write_text("{ invalid json\n")
    else:
        raw = json.loads(source.read_text())
        raw["read_time"] = None if damage == "null-policy" else {"floor": "fast"}
        _write(tmp_path, "format.json", raw)

    result = text.readtime(film)

    assert result.status == "UNCHECKED", result.to_dict()
    assert str(source) in result.inputs
