"""Mixed-act events must describe visible composition while retaining independent capture beats.

The actual computeEvents helper receives a neutral loaded module and a fake page reader. Each explicit child goes
through slate.procs.run. These regressions do not import a scene, open a browser, render media or change product code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from slate import procs

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine"
READER = ENGINE / "tests/core/fixtures/mixed-event-reader.mjs"


def _event(ident, shot, frame, *, kind="land", source=None):
    row = {"id": ident, "shot": shot, "frame": frame, "t": frame * 1001 / 30000,
           "kind": kind, "size": "s", "note": "A neutral card settles."}
    if source:
        row["source"] = source
    return row


@pytest.fixture
def neutral(tmp_path):
    film = tmp_path / "neutral"
    files = {"scenes/act2/index.tsx": "// Supplied loaded module; never compiled by this test.\n",
             "scenes/shots/W01/web/index.html": "<!doctype html><title>Neutral web shot</title>\n"}
    for name, content in files.items():
        file = film / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    shots = [{"id": "B01", "act": 2, "frame_in": 120, "frame_out": 150},
             {"id": "W01", "act": 2, "engine": "web", "frame_in": 150, "frame_out": 180},
             {"id": "B02", "act": 2, "frame_in": 180, "frame_out": 210}]
    ctx = {"dir": str(film), "fps": {"num": 30000, "den": 1001, "value": 30000 / 1001},
           "acts": [{"act": 2, "frameIn": 120, "frameOut": 210, "frames": 90,
                     "shots": ["B01", "W01", "B02"]}], "shots": {"shots": shots}}
    html = "scenes/shots/W01/web/index.html"
    digest = hashlib.sha256((film / html).read_bytes()).hexdigest()
    data = {"ctx": ctx,
            "exports": [_event("base.visible", "B01", 132),
                        _event("base.hidden.export", "W01", 159)],
            "kit": [_event("kit.hidden", "W01", 166), _event("kit.visible", "B02", 192)],
            "capture": _event("capture.independent", "W01", 174, kind="tap", source="capture:neutral"),
            "page": {"act": 2, "beats": [_event("web.visible", "W01", 171)],
                     "problems": [], "scene_problems": [], "misses": [], "gates": [],
                     "trace_read": {"frame_base": "film", "act": 2, "act_frames": [120, 210],
                                    "fps_num": 30000, "fps_den": 1001, "rows": []},
                     "sources": [html], "source_hashes": {html: digest}}}
    return {"film": film, "data": data}


def _compute(neutral, *, routing="timing", hidden=True, edit=""):
    film, data = neutral["film"], json.loads(json.dumps(neutral["data"]))
    web = data["ctx"]["shots"]["shots"][1]
    if routing == "pure":
        web.pop("engine")
        (film / "scenes/shots/W01/web/index.html").unlink()
    elif routing in {"script", "legacy"}:
        web.pop("engine")
        rows = [{"id": "W01", "engine": "web"}]
        act = {"shots": rows} if routing == "script" else {"shots": [], "lines": rows}
        file = film / "script/script.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"acts": [act]}), encoding="utf-8")
    elif routing == "timing":
        # The resolved declaration wins over an older conflicting script declaration.
        file = film / "script/script.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"acts": [{"shots": [{"id": "W01", "engine": "remotion"}]}]}),
                        encoding="utf-8")
    elif routing == "timing-remotion":
        web["engine"] = "remotion"
        file = film / "script/script.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"acts": [{"shots": [{"id": "W01", "engine": "web"}]}]}),
                        encoding="utf-8")
    else:
        raise AssertionError(f"Unknown fixture routing: {routing}")
    if not hidden:
        data["exports"] = data["exports"][:1]
        data["kit"] = data["kit"][1:]
    protected = {str(file.relative_to(film)): file.read_bytes() for file in film.rglob("*") if file.is_file()}
    source = f"""
import {{computeEvents}} from {json.dumps((ENGINE / "remotion/events.mjs").as_uri())};
import {{configure, calls}} from {json.dumps(READER.as_uri())};
const data = {json.dumps(data)};
{edit}
configure(data.page);
const loaded = {{
  usesKit: true, extra: {{events: data.exports}},
  hooks: {{kitEvents: {{name: 'actEvents', fn: () => data.kit}}}}
}};
const captures = {{byAct: new Map([[2, [data.capture]]]), strays: [], notes: [],
                  files: ['captures/neutral/beats.json']}};
let answer;
try {{
  const result = await computeEvents(data.ctx, 2, loaded, {{captures}});
  answer = {{ok: true, events: result.events, problems: result.problems, notes: result.notes,
             misses: result.misses, captures: result.captures,
             web_shots: result.webShots.map((item) => item.sc.shot)}};
}} catch (error) {{
  answer = {{ok: false, kind: error.kind, error: error.message}};
}}
answer.calls = calls;
console.log(JSON.stringify(answer));
"""
    process = procs.run(["node", "--input-type=module"], input=source, cwd=ENGINE,
                        env={"SLATE_WEB_RENDER_MODULE": str(READER)}, timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    after = {str(file.relative_to(film)): file.read_bytes() for file in film.rglob("*") if file.is_file()}
    assert after == protected, "computeEvents changed source or generated files during a pure read"
    return result


def _visible_expected(neutral):
    data = neutral["data"]
    return [data["exports"][0], data["page"]["beats"][0], data["capture"], data["kit"][1]]


def _assert_page_read(result, neutral):
    assert len(result["calls"]) == 1, result
    assert result["web_shots"] == ["W01"], result
    call = result["calls"][0]
    assert call == {"film": str(neutral["film"]), "act": 2, "emit": True, "dry_run": True,
                    "scene": str(neutral["film"] / "scenes/shots/W01/web"),
                    "trace": False, "trace_rows": True}


def test_pure_act_keeps_export_kit_and_capture_events_unchanged(neutral):
    result = _compute(neutral, routing="pure")
    assert result["ok"], result
    data = neutral["data"]
    expected = sorted([*data["exports"], *data["kit"], data["capture"]], key=lambda e: (e["t"], e["id"]))
    assert result["events"] == expected
    assert result["problems"] == [] and result["calls"] == [] and result["web_shots"] == []
    assert result["captures"] == {"beats": 1, "files": ["captures/neutral/beats.json"]}


@pytest.mark.parametrize("routing", ["timing", "script", "legacy"])
def test_mixed_act_excludes_hidden_base_exports_and_kit_beats(neutral, routing):
    result = _compute(neutral, routing=routing)
    assert result["ok"], result
    _assert_page_read(result, neutral)
    assert result["problems"] == [], result
    assert result["events"] == _visible_expected(neutral), (
        "Mixed visible composition leaked base events owned by the replaced W01 shot: "
        f"{[event.get('id') for event in result['events']]}")
    assert result["captures"]["beats"] == 1


def test_visible_base_web_and_independent_capture_are_retained_once_on_film_clock(neutral):
    result = _compute(neutral, hidden=False)
    assert result["ok"] and result["problems"] == [], result
    _assert_page_read(result, neutral)
    assert result["events"] == _visible_expected(neutral)
    assert [event["frame"] for event in result["events"]] == [132, 171, 174, 192]
    assert [event["t"] for event in result["events"]] == [frame * 1001 / 30000 for frame in [132, 171, 174, 192]]
    assert len({event["id"] for event in result["events"]}) == 4
    assert sum(event.get("source") == "capture:neutral" for event in result["events"]) == 1
    assert result["captures"] == {"beats": 1, "files": ["captures/neutral/beats.json"]}


@pytest.mark.parametrize("edit, reason", [
    ("data.exports[1].t = NaN;", "t must be a number"),
    ("data.kit[0].kind = 'invalid-kind';", "isn't one of"),
    ("data.exports[1].note = '';", "needs a note"),
    ("data.kit[0].id = data.exports[0].id;", 'the id "base.visible" is used twice'),
    ("data.exports.push(null);", ("needs an id", "not an object")),
], ids=["hidden-nonfinite-time", "hidden-invalid-kind", "hidden-missing-note",
        "hidden-duplicate-id", "null-raw-base"])
def test_raw_malformed_base_evidence_still_reports_existing_validation(neutral, edit, reason):
    result = _compute(neutral, edit=edit)
    assert result["ok"], result
    _assert_page_read(result, neutral)
    reasons = (reason,) if isinstance(reason, str) else reason
    assert any(part in problem for part in reasons for problem in result["problems"]), result
    assert result["problems"], "Filtering a hidden shot must not conceal malformed source evidence"


def test_web_beat_outside_its_owned_shot_is_reported_and_not_added(neutral):
    result = _compute(neutral, edit="data.page.beats[0].t = 130 * 1001 / 30000;")
    assert result["ok"], result
    _assert_page_read(result, neutral)
    assert any("web.visible" in problem and "isn't inside W01" in problem for problem in result["problems"]), result
    assert "web.visible" not in [event["id"] for event in result["events"]]


def test_timing_remotion_declaration_does_not_use_an_older_script_web_declaration(neutral):
    result = _compute(neutral, routing="timing-remotion")
    assert not result["ok"] and result["kind"] == "refused", result
    assert "doesn't declare engine: web" in result["error"]
    assert result["calls"] == [], result



def test_hidden_impacts_do_not_count_toward_the_visible_act_limit(neutral):
    result = _compute(neutral, edit="data.exports[1].kind = 'impact'; data.kit[0].kind = 'impact';")
    assert result["ok"] and result["problems"] == [], result
    _assert_page_read(result, neutral)
    assert result["events"] == _visible_expected(neutral)


def test_visible_impacts_still_use_the_existing_act_limit(neutral):
    result = _compute(neutral, edit="data.exports[0].kind = 'impact'; data.kit[1].kind = 'impact';")
    assert result["ok"], result
    _assert_page_read(result, neutral)
    assert any("2 impacts in act 2" in problem for problem in result["problems"]), result
    assert len(result["events"]) == 4


def test_pure_act_impact_limit_is_unchanged(neutral):
    result = _compute(neutral, routing="pure",
                      edit="data.exports[1].kind = 'impact'; data.kit[0].kind = 'impact';")
    assert result["ok"] and result["calls"] == [], result
    assert any("2 impacts in act 2" in problem for problem in result["problems"]), result
    assert len(result["events"]) == 5
