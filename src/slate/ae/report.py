"""How After Effects scripts report back to Slate.

AE 26.5 returns "0" to AppleScript for every DoScript and DoScriptFile, whatever the script returns, so a script's
result has to travel another way. Two channels, the first always available:

1. **The settings channel.** The script stores values with `app.settings.saveSetting("Slate", key, value)` and flushes
   them with `app.preferences.saveToDisk()`. They land in AE's own prefs file, in a `["Settings_Slate"]` section,
   which this module reads back (bytes only; nothing here ever writes that file). Needs no special AE setting.
2. **The file channel.** When the person has turned on "Allow Scripts to Write Files and Access Network", the script
   writes `report.json` straight into the run's folder. With that setting off, a file write can raise a modal that
   stalls AE, so Slate only asks for a file when `slate.ae.prefs.check()` says the setting is on.

Slate's ExtendScript library (engine/ae/lib/slate_ae.jsx) reports one JSON object per run: `<channel>.id` names the
run, `<channel>.status` its outcome, and the JSON itself is URI-encoded (no quotes to escape) and split across
`<channel>.json.0 … json.<n-1>` because AE wraps long values. Reading the id first means a stale report from an older
run is never mistaken for this one.

The prefs file stores a value as quoted runs and bare hex bytes (`"a"C2B7"b"` is "a·b"), and wraps long values with a
trailing backslash onto tab-indented lines. Bytes are collected and decoded once; decoding each hex byte on its own is
what turned "·" into "Â·" in the film pipeline's first reader.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path

from slate.ae import prefs
from slate.errors import SlateError

SECTION = prefs.REPORT_SECTION


def decode_value(raw: str) -> str:
    """Decode one prefs value: quoted runs plus hex byte pairs, joined as bytes and decoded as UTF-8 once."""
    out = bytearray()
    i = 0
    raw = raw.strip()
    while i < len(raw):
        c = raw[i]
        if c == '"':
            j = raw.find('"', i + 1)
            if j < 0:
                out += raw[i + 1:].encode("utf-8")
                break
            out += raw[i + 1:j].encode("utf-8")
            i = j + 1
        elif re.match(r"[0-9A-Fa-f]{2}", raw[i:i + 2]):
            out.append(int(raw[i:i + 2], 16))
            i += 2
        else:
            i += 1
    return out.decode("utf-8", "replace")


def section_values(data: bytes, section: str = SECTION, prefix: str = "") -> dict[str, str]:
    """Every `key = value` in `["Settings_<section>"]` whose key starts with `prefix`, decoded."""
    text = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    m = re.search(r'\["Settings_' + re.escape(section) + r'"\]\n(.*?)(?=\n\["|\Z)', text, re.S)
    if not m:
        return {}
    body = re.sub(r"\\\n\s*", "", m.group(1))  # a line ending in a backslash continues on the next line
    out: dict[str, str] = {}
    for line in body.split("\n"):
        kv = re.match(r'\s*"([^"]+)"\s*=\s*(.*)$', line)
        if kv and kv.group(1).startswith(prefix):
            out[kv.group(1)] = decode_value(kv.group(2))
    return out


def read(section: str = SECTION, prefix: str = "", *, file: str | os.PathLike | None = None) -> dict[str, str]:
    """Read a settings section from AE's prefs file (the newest AE version's, sorted as numbers)."""
    path, _live = prefs.resolve(file)
    return section_values(path.read_bytes(), section, prefix)


def _unchunk(values: dict[str, str], channel: str) -> str | None:
    n_raw = values.get(f"{channel}.json.n")
    if n_raw is None:
        return None
    try:
        n = int(n_raw)
    except ValueError:
        return None
    parts = []
    for i in range(n):
        part = values.get(f"{channel}.json.{i}")
        if part is None:
            return None
        parts.append(part)
    return urllib.parse.unquote("".join(parts))


def read_run(run_id: str, *, channel: str = "run", report_file: str | os.PathLike | None = None,
             file: str | os.PathLike | None = None) -> dict:
    """The report of one run: {status, data, channel: "file"|"settings"} or status "MISSING" when none arrived.

    The file channel wins when the run wrote one; otherwise the settings channel is read and checked against the
    run id, so a report left over from an earlier run is never taken for this one.
    """
    if report_file and Path(report_file).is_file():
        try:
            payload = json.loads(Path(report_file).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SlateError(f"After Effects wrote a report that isn't valid JSON ({report_file}): {exc.msg}") from exc
        if payload.get("run_id") == run_id:
            return {"status": payload.get("status", "UNKNOWN"), "data": payload.get("data", {}), "channel": "file"}
    try:
        values = read(SECTION, f"{channel}.", file=file)
    except SlateError:
        values = {}
    if values.get(f"{channel}.id") != run_id:
        return {"status": "MISSING", "data": {}, "channel": "settings",
                "seen_id": values.get(f"{channel}.id")}
    text = _unchunk(values, channel)
    data: dict = {}
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SlateError(f"After Effects' report for run {run_id} didn't decode as JSON: {exc.msg}") from exc
    return {"status": values.get(f"{channel}.status", "UNKNOWN"), "data": data, "channel": "settings"}


def encode_for_settings(payload: dict, *, run_id: str, status: str, channel: str = "run",
                        chunk: int = 1000) -> dict[str, str]:
    """What the ExtendScript side stores for a report, as key -> value. Tests use it to stand in for AE."""
    text = urllib.parse.quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    parts = [text[i:i + chunk] for i in range(0, len(text), chunk)] or [""]
    out = {f"{channel}.id": run_id, f"{channel}.status": status, f"{channel}.json.n": str(len(parts))}
    for i, p in enumerate(parts):
        out[f"{channel}.json.{i}"] = p
    return out
