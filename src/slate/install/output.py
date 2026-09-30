"""Reading a command's JSON output when other things share its stdout.

A child's `--json` output should be one JSON object and nothing else, but native frameworks don't always agree: with
the ocr extra installed, Apple's Vision framework prints "Unable to find a valid E5 …" lines to stdout after
`slate doctor --json` has printed its report. So the installer takes the first complete JSON value it finds and
ignores whatever comes before or after it.
"""

from __future__ import annotations

import json
from typing import Any

_DECODER = json.JSONDecoder()


def first_json(text: str | None) -> Any:
    """The first complete JSON object or array in `text`, or None when there isn't one."""
    if not text:
        return None
    i = 0
    while True:
        starts = [p for p in (text.find("{", i), text.find("[", i)) if p >= 0]
        if not starts:
            return None
        start = min(starts)
        try:
            value, _end = _DECODER.raw_decode(text, start)
            return value
        except ValueError:
            i = start + 1
