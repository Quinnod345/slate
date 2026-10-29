"""Capture specs: what to record, validated before anything boots, opens or records.

A spec is a small JSON file (schema: capture.schema.json, $defs/spec). Times are seconds from the start of the
recording. Example, an iOS capture:

    {"kind": "ios", "name": "save-entry", "duration": 8,
     "app": {"path": "build/Demo.app", "bundle_id": "com.example.demo", "build_sha256": "<64 hex>"},
     "account": {"kind": "demo", "launch_args": ["-ui-testing", "-seed-demo-account"]},
     "place": {"shot": "S04"},
     "steps": [{"at": 0, "do": "launch"},
               {"at": 2.4, "do": "tap", "id": "save", "x": 201, "y": 742,
                "word": {"shot": "S04", "text": "save"}, "size": "small"}]}

and a web capture (through Aside, at a fixed viewport):

    {"kind": "web", "name": "pricing", "duration": 4, "root": "site/", "page": "pricing.html",
     "viewport": {"width": 1280, "height": 720},
     "steps": [{"at": 1.5, "do": "click", "selector": "#buy", "id": "buy", "word": {"shot": "S07", "text": "start"}}]}

Defaults fill what a spec leaves out: the iPhone 17 Pro, an isolated Simulator, Apple's 9:41 status bar with full
battery and full bars, a virtual clock for web pages, native frame size.
"""

from __future__ import annotations

import copy
from pathlib import Path

from slate import jsonio
from slate.errors import UsageError
from slate.gen import deps

DEFAULT_DEVICE = "iPhone 17 Pro"
DEFAULT_STATUS_BAR = {"time": "9:41", "battery": 100, "battery_state": "charged", "wifi_bars": 3, "cell_bars": 4,
                      "data_network": "wifi", "operator": ""}
DEFAULT_TOLERANCE_FRAMES = 1.0
DEFAULT_RESPONSE_S = 0.6  # after a tap the app answers; retime edits never land inside this window
EVENT_STEPS = {"tap": "tap", "click": "tap", "mark": "tap", "type": "type"}
TAP_STEPS = frozenset({"tap", "click", "mark"})


def validate(spec: dict) -> list[str]:
    """Readable problems with a spec (empty when it's valid), including checks a schema can't express."""
    problems = deps.validate_part(spec, "capture", "spec")
    if problems:
        return problems
    steps = spec.get("steps") or []
    ids = [s.get("id") for s in steps if s.get("id")]
    if len(ids) != len(set(ids)):
        problems.append("steps: every step id must be unique")
    for i, step in enumerate(steps):
        if step["at"] > spec["duration"]:
            problems.append(f"steps/{i}: at {step['at']} s is after the capture ends ({spec['duration']} s)")
        if step.get("word") and not step.get("id"):
            problems.append(f"steps/{i}: a step anchored to a word needs an id (it names the beat)")
    kind = spec["kind"]
    ios_only = {"tap", "launch", "openurl", "terminate"}
    web_only = {"click", "scroll", "key"}
    for i, step in enumerate(steps):
        if kind == "web" and step["do"] in ios_only:
            problems.append(f"steps/{i}: '{step['do']}' is an iOS step; web captures click, type, scroll and key")
        if kind == "ios" and step["do"] in web_only:
            problems.append(f"steps/{i}: '{step['do']}' is a web step; iOS captures launch, openurl, tap and type")
        if kind == "file" and step["do"] not in ("mark", "wait"):
            problems.append(f"steps/{i}: a file capture can only mark moments already in it (do: mark)")
    if kind == "file":
        for key in ("app", "account", "device", "status_bar", "tap_driver", "isolate", "url", "root", "viewport",
                    "clock", "frame"):
            if key in spec:
                problems.append(f"{key}: a file capture conforms a recording that already exists; it takes no {key}")
    if kind == "ios":
        if not any(s["do"] == "launch" for s in steps):
            problems.append("steps: an iOS capture launches its app (add {\"at\": 0, \"do\": \"launch\"})")
        for key in ("url", "root", "viewport", "clock", "frame"):
            if key in spec:
                problems.append(f"{key}: only web captures take this")
    if kind == "web":
        for key in ("app", "account", "device", "status_bar", "tap_driver", "isolate"):
            if key in spec:
                problems.append(f"{key}: only iOS captures take this")
    if spec.get("fit") in ("contain", "cover") and not spec.get("size"):
        problems.append("fit: contain and cover need a size (or leave size out to use the film's)")
    return problems


def normalize(spec: dict, film: dict | None = None) -> dict:
    """Validate, then fill the defaults. Refuses (exit 2) with every problem listed."""
    if not isinstance(spec, dict):
        raise UsageError("A capture spec is a JSON object.")
    out = copy.deepcopy(spec)
    if out.get("fit") in ("contain", "cover") and not out.get("size") and film:
        out["size"] = {"width": int(film["width"]), "height": int(film["height"])}
    problems = validate(out)
    if problems:
        listed = "\n  ".join(problems)
        raise UsageError(f"The capture spec '{spec.get('name', '?')}' isn't valid:\n  {listed}",
                         hint="Schema: src/slate/schemas/capture.schema.json ($defs/spec).")
    out.setdefault("fit", "native")
    out.setdefault("tolerance_frames", DEFAULT_TOLERANCE_FRAMES)
    out.setdefault("response", DEFAULT_RESPONSE_S)
    out["steps"] = sorted(out.get("steps") or [], key=lambda s: s["at"])
    if out["kind"] == "ios":
        out.setdefault("device", DEFAULT_DEVICE)
        out.setdefault("isolate", True)
        out.setdefault("tap_driver", "auto")
        out["status_bar"] = {**DEFAULT_STATUS_BAR, **(out.get("status_bar") or {})}
    if out["kind"] == "web":
        out.setdefault("clock", "virtual")
        out.setdefault("frame", "iframe")
        out.setdefault("quality", 95)
    return out


def load(path: str | Path, film: dict | None = None) -> dict:
    data = jsonio.read(path)
    return normalize(data, film)


def anchored_steps(spec: dict) -> list[dict]:
    """Steps that land on a voice word."""
    return [s for s in spec.get("steps") or [] if s.get("word")]


def event_steps(spec: dict) -> list[dict]:
    """Steps that make a visual event (and so a sound): taps, clicks and typing, unless marked `sound: false`."""
    return [s for s in spec.get("steps") or [] if s["do"] in EVENT_STEPS and s.get("sound", True)]


def resolve(film_dir: Path, value: str) -> Path:
    """A path from a spec: absolute, or a film file (including an inherited reference)."""
    path = Path(value).expanduser()
    return deps.resolve_file(film_dir, path)
