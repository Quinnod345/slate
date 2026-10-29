"""Neutral regressions for motion provenance in Remotion acts with spliced web shots.

The Node cases load the real trace helpers and a fake page reader. They do not open a browser or render media.
Every explicit child uses Slate's process boundary. These tests exercise the existing helpers on the old source,
so missing composition support fails an assertion rather than preventing collection.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate import jsonio, procs
from slate.errors import Refused
from slate.render import runner

ENGINE = Path(__file__).resolve().parents[2] / "engine"


def _node(source: str, *, env: dict | None = None) -> dict:
    result = procs.run(["node", "--input-type=module"], input=source, cwd=ENGINE,
                       env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _row(ident="card.x", shot="W01", start=150, end=180):
    return {"id": ident, "shot": shot, "prop": "x", "ease": "move",
            "frame_in": start, "frame_out": end, "from": 0, "to": end - start,
            "samples": [[f, f - start] for f in range(start, end + 1)]}


@pytest.fixture
def mixed(tmp_path):
    film = tmp_path / "neutral"
    files = {"scenes/act2/index.tsx": "// neutral base module\n",
             "scenes/shots/W01/web/index.html": "<!doctype html><title>Neutral</title>\n",
             "scenes/shared/web-helper.ts": "export const amount = 1;\n"}
    for rel, text in files.items():
        p = film / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    shots = [{"id": "B01", "act": 2, "frame_in": 120, "frame_out": 150},
             {"id": "W01", "act": 2, "engine": "web", "frame_in": 150, "frame_out": 180},
             {"id": "B02", "act": 2, "frame_in": 180, "frame_out": 210}]
    ctx = {"dir": str(film), "fps": {"num": 30000, "den": 1001, "value": 30000 / 1001},
           "acts": [{"act": 2, "frameIn": 120, "frameOut": 210, "frames": 90,
                     "shots": ["B01", "W01", "B02"]}],
           "shots": {"shots": shots}}
    base = [_row("base.before", "B01", 120, 140),
            _row("base.hidden", "W01", 150, 170), _row("base.after", "B02", 180, 200)]
    hashes = {rel: hashlib.sha256((film / rel).read_bytes()).hexdigest()
              for rel in files if rel != "scenes/act2/index.tsx"}
    sc = {"shot": "W01", "dir": str(film / "scenes/shots/W01/web"),
          "index": str(film / "scenes/shots/W01/web/index.html"), "shotRecord": shots[1]}
    page = {"error": None, "problems": [], "scene_problems": [], "misses": [],
            "page": {"trace_read": {"frame_base": "film", "act": 2, "act_frames": [120, 210],
                                    "fps_num": 30000, "fps_den": 1001, "rows": [_row()]},
                     "sources": sorted(hashes), "source_hashes": hashes}}
    return {"film": film, "ctx": ctx, "base": base, "sc": sc, "page": page}


def _write(mixed, *, edit="", dry=False, web=True):
    data = {"ctx": mixed["ctx"], "base": mixed["base"],
            "webShots": [{"sc": mixed["sc"], **mixed["page"]}] if web else []}
    return _node(f"""
import fs from 'node:fs';
import path from 'node:path';
import {{writeMotion}} from {json.dumps((ENGINE / 'remotion/events.mjs').as_uri())};
const data = {json.dumps(data)};
{edit}
const loaded = {{usesKit: true, inputs: ['scenes/act2/index.tsx'],
                hooks: {{motionTrace: {{name: 'traceMotions', fn: () => data.base}}}}}};

const file = path.join(data.ctx.dir, 'timing/motion/act2.json');
let answer;
try {{
  const result = await writeMotion(data.ctx, 2, loaded, {{dryRun: {str(dry).lower()}, webShots: data.webShots}});
  answer = {{ok: true, result}};
}} catch (error) {{
  answer = {{ok: false, error: error.message, kind: error.kind, details: error.details}};
}}
answer.exists = fs.existsSync(file);
if (answer.exists) answer.rows = JSON.parse(fs.readFileSync(file, 'utf8'));
if (answer.result?.base_file) answer.base = JSON.parse(fs.readFileSync(path.join(data.ctx.dir, answer.result.base_file), 'utf8'));
console.log(JSON.stringify(answer));
""")


def test_render_mixed_trace_uses_film_frames_and_excludes_hidden_base(mixed):
    result = _write(mixed)
    assert result["ok"], result
    assert [r["id"] for r in result["rows"]] == ["base.before", "base.after", "web:W01:card.x"]
    assert result["rows"][-1]["frame_in"] == 150
    assert result["rows"][-1]["frame_out"] == 180
    assert result["rows"][-1]["samples"] == _row()["samples"]
    assert result["rows"][-1]["source_id"] == "card.x"
    assert result["result"]["web_shots"] == ["W01"]
    assert result["result"]["frame_base"] == "film"
    assert result["base"] == mixed["base"]


def test_render_mixed_trace_records_imported_source_bytes(mixed):
    result = _write(mixed)
    assert result["ok"], result
    assert result["result"]["source_hashes"] == mixed["page"]["page"]["source_hashes"]
    assert result["result"]["sources"] == sorted(mixed["page"]["page"]["sources"])
    proof = jsonio.read(mixed["film"] / result["result"]["composition_file"])
    assert proof["frame_base"] == "film"
    assert proof["act_frames"] == [120, 210]
    assert proof["web_shots"][0]["shot"] == "W01"
    assert proof["web_shots"][0]["source_hashes"] == result["result"]["source_hashes"]


def test_render_mixed_trace_explicit_empty_registry_is_distinct_from_missing(mixed):
    result = _write(mixed, edit="data.webShots[0].page.trace_read.rows = [];")
    assert result["ok"], result
    assert [r["id"] for r in result["rows"]] == ["base.before", "base.after"]
    assert result["result"]["web_shots"] == ["W01"]
    assert result["result"]["entries"] == 2


def test_render_pure_trace_shape_and_rows_remain_unchanged(mixed):
    result = _write(mixed, web=False)
    assert result["ok"], result
    assert result["rows"] == mixed["base"]
    assert result["result"] == {"written": True, "file": "timing/motion/act2.json", "entries": 3}
    assert not (mixed["film"] / "timing/motion/sources").exists()


@pytest.mark.parametrize("edit", [
    "delete data.webShots[0].page.trace_read;",
    "data.webShots[0].page.trace_read.rows = null;",
    "data.webShots[0].page.trace_read.rows = {};",
    "data.webShots[0].page.trace_read.frame_base = 'act';",
    "data.webShots[0].page.trace_read.act = 1;",
    "data.webShots[0].page.trace_read.act_frames = [0, 90];",
    "data.webShots[0].page.trace_read.fps_den = 1;",
    "data.webShots[0].page.trace_read.rows[0].shot = 'B01';",
    "data.webShots[0].page.trace_read.rows[0].frame_in = 149;",
    "data.webShots[0].page.trace_read.rows[0].frame_out = 181;",
    "data.webShots[0].page.trace_read.rows[0].frame_in = '150';",
    "data.webShots[0].page.trace_read.rows[0].frame_out = Infinity;",
    "data.webShots[0].page.trace_read.rows[0].from = NaN;",
    "data.webShots[0].page.trace_read.rows[0].samples[0][0] = 149;",
    "data.webShots[0].page.trace_read.rows[0].samples[1][0] = 150.5;",
    "data.webShots[0].page.trace_read.rows[0].samples[1][1] = Infinity;",
    "data.webShots[0].page.trace_read.rows.push(data.webShots[0].page.trace_read.rows[0]);",
    "delete data.webShots[0].page.source_hashes;",
    "data.webShots[0].page.source_hashes[data.webShots[0].page.sources[0]] = '0'.repeat(64);",
    "data.webShots[0].page.sources = [];",
    "data.webShots[0].scene_problems = ['unknown ease was replaced by a fallback'];",
    "data.webShots[0].error = 'neutral page read failed';",
], ids=["missing-trace", "null-trace", "object-trace", "local-clock", "wrong-act", "wrong-bounds",
        "wrong-rate", "wrong-shot", "before-shot", "after-shot", "string-frame", "nonfinite-frame",
        "nonfinite-value", "outside-sample", "fractional-sample", "nonfinite-sample", "duplicate-id",
        "missing-sources", "changed-source", "source-list-mismatch", "producer-fallback", "page-failure"])
def test_render_mixed_trace_refuses_incomplete_or_malformed_web_evidence(mixed, edit):
    result = _write(mixed, edit=edit)
    assert not result["ok"], result
    assert result["kind"] == "refused"
    assert not result["exists"]


@pytest.mark.parametrize("edit", [
    "delete data.base[0].shot; data.base[0].frame_out = 160;",
    "data.base[0].frame_out = 160;",
    "data.base[1].frame_in = 140;",
    "data.base[1].frame_out = 190;",
], ids=["unowned-overlap", "other-shot-overlap", "replacement-start-overlap", "replacement-end-overlap"])
def test_render_mixed_trace_refuses_ambiguous_base_ownership(mixed, edit):
    result = _write(mixed, edit=edit)
    assert not result["ok"], result
    assert not result["exists"]
    assert "ownership" in result["error"].lower()


def test_render_mixed_trace_dry_run_validates_without_changing_current_bytes(mixed):
    file = mixed["film"] / "timing/motion/act2.json"
    file.parent.mkdir(parents=True)
    before = b'[{"previous": true}]\n'
    file.write_bytes(before)
    result = _write(mixed, dry=True)
    assert result["ok"], result
    assert result["result"]["written"] is False
    assert result["result"]["entries"] == 3
    assert file.read_bytes() == before
    assert not (file.parent / "sources").exists()
    assert not (file.parent / "failures").exists()


def test_render_mixed_failed_write_preserves_previous_bytes_then_removes_current(mixed):
    file = mixed["film"] / "timing/motion/act2.json"
    file.parent.mkdir(parents=True)
    before = json.dumps(mixed["base"], indent=4).encode() + b"\n"
    file.write_bytes(before)
    result = _write(mixed, edit="delete data.webShots[0].page.trace_read;")
    assert not result["ok"], result
    assert not file.exists()
    retained = list((file.parent / "failures").glob("*/previous.json"))
    assert len(retained) == 1
    assert retained[0].read_bytes() == before
    failure = jsonio.read(retained[0].with_name("failure.json"))
    assert failure["previous_sha256"] == hashlib.sha256(before).hexdigest()
    assert failure["reason"]


@pytest.mark.parametrize("raises", [False, True], ids=["unsuccessful-result", "bridge-refusal"])
def test_render_failed_refresh_cannot_reuse_previous_base_trace(mixed, raises):
    from slate.qa import motion

    film = mixed["film"]
    file = film / "timing/motion/act2.json"
    file.parent.mkdir(parents=True)
    before = json.dumps(mixed["base"], indent=3).encode() + b"\n"
    file.write_bytes(before)

    class Engine:
        def trace(self, act):
            if raises:
                raise Refused("The web shot has no complete trace.")
            return {"written": False, "reason": "The web shot has no complete trace."}

    result = runner._refresh_trace(film, SimpleNamespace(act=SimpleNamespace(index=2)),
                                   Engine(), SimpleNamespace(chunks=[]))
    assert result["written"] is False
    assert not file.exists()
    retained = list((file.parent / "failures").glob("*/previous.json"))
    assert len(retained) == 1
    assert retained[0].read_bytes() == before
    ctx = SimpleNamespace(fps=30000 / 1001, size=(800, 600),
                          shots=mixed["ctx"]["shots"]["shots"],
                          acts=[{"index": 2, "frame_in": 120, "frame_out": 210}],
                          brand=None, path=lambda rel: film / rel,
                          rel=lambda p: str(p.relative_to(film)), sha=jsonio.sha256)
    data = motion.load_data(film, acts=[2], ctx=ctx)
    assert motion.check_trace(data).status == "UNCHECKED"


def test_render_refresh_preserves_captured_hashes_in_lock_inputs(mixed, monkeypatch):
    inputs = {}
    digest = "a" * 64
    rel = "scenes/shared/web-helper.ts"
    monkeypatch.setattr(runner.deps, "state_lock",
                        lambda: SimpleNamespace(record=lambda *a, **kw: inputs.update(kw),
                                                resolve=lambda folder, rel: folder / rel))

    class Engine:
        def trace(self, act):
            return {"written": True, "file": "timing/motion/act2.json", "entries": 1,
                    "sources": [rel], "source_hashes": {rel: digest},
                    "frame_base": "film", "web_shots": ["W01"]}

    result = runner._refresh_trace(mixed["film"], SimpleNamespace(act=SimpleNamespace(index=2)),
                                   Engine(), SimpleNamespace(chunks=[]))
    assert inputs["inputs"][rel] == digest
    assert result["web_shots"] == ["W01"]


def test_render_emit_lock_preserves_captured_hashes(mixed, monkeypatch):
    from slate.engine import cli
    from slate.state import lock

    recorded = {}
    monkeypatch.setattr(lock, "record", lambda *a, **kw: recorded.update(kw))
    rel, digest = "scenes/shared/web-helper.ts", "b" * 64
    # Exercise the existing public emitter, not a new helper unavailable on the old source.
    monkeypatch.setattr(cli.slate_cli, "film", lambda args: mixed["film"])
    monkeypatch.setattr(cli.bridge, "call", lambda *a, **kw: {
        "acts": [{"act": 2, "file": "timing/visual_events/act2.json", "silent": False, "events": 1,
                  "sources": [rel], "source_hashes": {rel: digest},
                  "motion": {"written": True, "file": "timing/motion/act2.json", "entries": 1}}],
        "skipped": []})
    monkeypatch.setattr(cli.log, "result", lambda *a, **kw: None)
    cli._emit(SimpleNamespace(act=2, dry_run=False, json=True))
    assert recorded["inputs"][rel] == digest


def test_render_bundle_records_actual_imported_source_bytes(tmp_path):
    scene = tmp_path / "scene"
    scene.mkdir()
    entry = scene / "index.ts"
    imported = scene / "shared.ts"
    css = scene / "style.css"
    css.write_text("body {font-size:17px; transform:translateX(2px)}\n")
    entry.write_text("import {amount} from './shared'; import './style.css'; export const total = amount + 1;\n")
    imported.write_text("export const amount = 7;\n")
    result = _node(f"""
import crypto from 'node:crypto';
import {{createBundler}} from {json.dumps((ENGINE / 'web/lib/bundle.mjs').as_uri())};
const observed = {{}};
const bundler = createBundler({{
  runtimeDir: {json.dumps(str(ENGINE / 'web/runtime'))}, kitDir: {json.dumps(str(ENGINE / 'remotion/kit'))},
  nodeModules: {json.dumps(str(ENGINE / 'node_modules'))}, filmData: () => ({{contents: '', resolveDir: {json.dumps(str(scene))}}}),
  urlFor: () => null,
  recordSource: (file, body) => {{ observed[file] = crypto.createHash('sha256').update(body).digest('hex'); }}
}});
const compiled = await bundler.bundle({json.dumps(str(entry))});
console.log(JSON.stringify({{observed, inputs: compiled.inputs}}));
""")
    assert result["inputs"] == 3
    assert result["observed"] == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [entry, imported, css]}


def _public(mixed, mode, *, invalid=False, declaration="timing"):
    """Load a real neutral act and collect its web page through the fake installed-reader boundary."""
    film = mixed["film"]
    jsonio.write_atomic(film / "film.json", {"id": "neutral-mixed", "fps_num": 30000, "fps_den": 1001,
                                           "width": 800, "height": 600})
    shots = json.loads(json.dumps(mixed["ctx"]["shots"]))
    web = shots["shots"][1]
    if declaration in {"script", "legacy-lines", "legacy-missing"}:
        web.pop("engine")
        rows = [{"id": "W01", "engine": "web"}]
        act = {"shots": rows} if declaration == "script" else {"shots": [], "lines": rows}
        jsonio.write_atomic(film / "script/script.json", {"acts": [act]})
        if declaration == "legacy-missing":
            (film / "scenes/shots/W01/web/index.html").unlink()
    elif declaration == "undeclared":
        web.pop("engine")
    elif declaration == "remotion":
        web["engine"] = "remotion"
    elif declaration == "missing":
        (film / "scenes/shots/W01/web/index.html").unlink()
    jsonio.write_atomic(film / "timing/shots.json", shots)
    source = film / "scenes/act2/index.tsx"
    source.write_text("""
import {defineMotion} from '@slate/kit/core';
export function Act2() { return null; }
defineMotion({id:'base.before', shot:'B01', prop:'x', ease:'move',
  frame_in:120, frame_out:140, from:0, to:20});
defineMotion({id:'base.hidden', shot:'W01', prop:'x', ease:'move',
  frame_in:150, frame_out:170, from:0, to:2000});
export const events = [{id:'base.lands', shot:'B01', t:138 * 1001 / 30000,
  kind:'land', size:'s', note:'A neutral card settles.'}];
""")
    fake = film / "fake-web-reader.mjs"
    response = json.loads(json.dumps(mixed["page"]["page"]))
    if invalid:
        response.pop("trace_read")
    fake.write_text(f"""
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
export async function webRender(args) {{
  const log = path.join(args.film, 'reader-calls.json');
  const calls = fs.existsSync(log) ? JSON.parse(fs.readFileSync(log, 'utf8')) : [];
  calls.push(args); fs.writeFileSync(log, JSON.stringify(calls));
  const page = {json.dumps(response)};
  page.sources = ['scenes/shots/W01/web/index.html', 'scenes/shared/web-helper.ts'];
  page.source_hashes = Object.fromEntries(page.sources.map((name) =>
    [name, crypto.createHash('sha256').update(fs.readFileSync(path.join(args.film, name))).digest('hex')]));
  return {{acts:[{{act:2, beats:[], problems:[], misses:[], scene_problems:[], ...page}}], skipped:[]}};
}}
""")
    result = _node(f"""
import fs from 'node:fs';
import path from 'node:path';
import {{loadFilm}} from {json.dumps((ENGINE / 'remotion/lib/film.mjs').as_uri())};
import {{emitEvents, traceAct}} from {json.dumps((ENGINE / 'remotion/events.mjs').as_uri())};
const ctx = loadFilm({json.dumps(str(film))});
let result;
try {{
  const value = await ({'emitEvents(ctx, {act:2}).then(result => result.acts[0])' if mode == 'emit' else 'traceAct(ctx, 2)'});
  result = {{ok:true, value}};
}} catch (error) {{ result = {{ok:false, error:error.message, kind:error.kind}}; }}
const file = path.join(ctx.dir, 'timing/motion/act2.json');
result.exists = fs.existsSync(file);
if (result.exists) result.rows = JSON.parse(fs.readFileSync(file, 'utf8'));
const calls = path.join(ctx.dir, 'reader-calls.json');
result.calls = fs.existsSync(calls) ? JSON.parse(fs.readFileSync(calls, 'utf8')) : [];
console.log(JSON.stringify(result));
""", env={"SLATE_WEB_RENDER_MODULE": str(fake)})
    return result


@pytest.mark.parametrize("mode", ["emit", "trace"])
def test_render_public_paths_import_web_motion_and_source_provenance(mixed, mode):
    result = _public(mixed, mode)
    assert result["ok"], result
    assert [row["id"] for row in result["rows"]] == ["base.before", "web:W01:card.x"]
    summary = result["value"]["motion"] if mode == "emit" else result["value"]
    assert summary["web_shots"] == ["W01"]
    assert summary["frame_base"] == "film"
    assert "scenes/shared/web-helper.ts" in summary["source_hashes"]
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    assert call["emit"] is True and call["dry_run"] is True
    assert call["trace"] is False and call["trace_rows"] is True
    assert call["scene"].endswith("scenes/shots/W01/web")


@pytest.mark.parametrize("mode", ["emit", "trace"])
def test_render_public_failed_read_invalidates_trace_without_updating_events(mixed, mode):
    film = mixed["film"]
    trace = film / "timing/motion/act2.json"
    trace.parent.mkdir(parents=True)
    previous = json.dumps(mixed["base"], indent=5).encode() + b"\n"
    trace.write_bytes(previous)
    events = film / "timing/visual_events/act2.json"
    events.parent.mkdir(parents=True)
    old_events = b'[{"previous": "events"}]\n'
    events.write_bytes(old_events)
    result = _public(mixed, mode, invalid=True)
    assert not result["ok"], result
    assert result["kind"] == "refused"
    assert len(result["calls"]) == 1
    assert "trace read" in result["error"]
    assert not trace.exists()
    retained = list((trace.parent / "failures").glob("*/previous.json"))
    assert len(retained) == 1 and retained[0].read_bytes() == previous
    assert events.read_bytes() == old_events
    assert not (events.parent / "changes/act2.json").exists()


@pytest.mark.parametrize("declaration", ["undeclared", "remotion", "missing", "legacy-missing"])
def test_render_public_trace_refuses_inconsistent_engine_routing(mixed, declaration):
    trace = mixed["film"] / "timing/motion/act2.json"
    trace.parent.mkdir(parents=True)
    trace.write_text(json.dumps(mixed["base"]))
    result = _public(mixed, "trace", declaration=declaration)
    assert not result["ok"], result
    assert result["kind"] == "refused"
    assert not trace.exists()


@pytest.mark.parametrize("declaration", ["script", "legacy-lines"])
def test_render_public_trace_honors_script_only_web_declaration(mixed, declaration):
    result = _public(mixed, "trace", declaration=declaration)
    assert result["ok"], result
    assert result["value"]["source_hashes"]["script/script.json"] == jsonio.sha256(mixed["film"] / "script/script.json")
    assert "web:W01:card.x" in [row["id"] for row in result["rows"]]


@pytest.mark.parametrize("edit", [
    "data.base.push(null);",
    "data.base[0].shot = 'OTHER_ACT';",
    "data.base = null;",
    "data.base = {};",
], ids=["null-raw-row", "out-of-act-raw-row", "missing-base-registry", "nonarray-base-registry"])
def test_render_mixed_trace_never_filters_missing_or_malformed_base_into_success(mixed, edit):
    result = _write(mixed, edit=edit)
    assert not result["ok"], result
    assert not result["exists"]


def test_render_mixed_trace_rejects_conflicting_imported_source_hashes(mixed):
    edit = """
const other = JSON.parse(JSON.stringify(data.webShots[0]));
other.sc.shot = 'W02'; other.sc.shotRecord = {id:'W02', act:2, frame_in:180, frame_out:210};
other.page.trace_read.rows = [];
other.page.source_hashes['scenes/shared/web-helper.ts'] = 'c'.repeat(64);
data.webShots.push(other);
"""
    result = _write(mixed, edit=edit)
    assert not result["ok"], result
    assert not result["exists"]


@pytest.mark.parametrize("failure", [False, True], ids=["owning-refresh", "owning-refusal"])
def test_render_web_patch_refreshes_owning_remotion_composition(mixed, monkeypatch, failure):
    observed = {}
    inputs = {}
    monkeypatch.setattr(runner.shot_engines, "act_engine", lambda *a: "remotion")
    monkeypatch.setattr(runner.deps, "state_lock",
                        lambda: SimpleNamespace(record=lambda *a, **kw: inputs.update(kw)))
    model = {"id": "neutral-mixed"}

    class Owner:
        def trace(self, act):
            observed["act"] = act
            if failure:
                raise Refused("Owning web motion read failed.")
            return {"written": True, "file": "timing/motion/act2.json", "entries": 1,
                    "sources": ["scenes/shared/web-helper.ts"],
                    "source_hashes": {"scenes/shared/web-helper.ts": "d" * 64}, "web_shots": ["W01"]}

    def owner(folder, film):
        observed.update(folder=folder, film=film)
        return Owner()

    monkeypatch.setattr(runner.engines, "RemotionEngine", owner)
    trace = mixed["film"] / "timing/motion/act2.json"
    trace.parent.mkdir(parents=True)
    trace.write_text(json.dumps(mixed["base"]))
    patch = SimpleNamespace(scene=mixed["film"] / "scenes/shots/W01/web", film=model, trace=lambda act: None)
    result = runner._refresh_trace(mixed["film"], SimpleNamespace(act=SimpleNamespace(index=2)),
                                   patch, SimpleNamespace(chunks=[]))
    assert observed == {"folder": mixed["film"], "film": model, "act": 2}
    if failure:
        assert result["written"] is False
        assert not trace.exists()
        assert list((trace.parent / "failures").glob("*/previous.json"))
    else:
        assert result["written"] is True
        assert inputs["inputs"]["scenes/shared/web-helper.ts"] == "d" * 64


@pytest.mark.parametrize("edit", [
    "delete data.base[1].prop;",
    "delete data.base[1].samples;",
    "data.base[1].samples[1][1] = NaN;",
    "data.base[1].samples[1][0] = Infinity;",
    "data.base[1].samples[1][1] = [1, 2];",
    "data.base[1].id = data.base[0].id;",
    "data.base[2].id = data.base[0].id;",
], ids=["hidden-prop", "hidden-samples", "hidden-nan", "hidden-frame-infinity",
        "hidden-dimensions", "hidden-duplicate-id", "retained-duplicate-id"])
def test_render_raw_base_is_validated_before_visibility_selection(mixed, edit):
    result = _write(mixed, edit=edit)
    assert not result["ok"], result
    assert not result["exists"]


def test_render_base_optional_endpoints_and_ease_remain_optional(mixed):
    result = _write(mixed, edit="delete data.base[1].from; delete data.base[1].to; delete data.base[1].ease;")
    assert result["ok"], result
    assert result["base"][1] == {k: v for k, v in mixed["base"][1].items() if k not in {"from", "to", "ease"}}


def test_render_missing_mixed_refresh_never_falls_back_to_base_chunk_trace(mixed, monkeypatch):
    film = mixed["film"]
    jsonio.write_atomic(film / "timing/shots.json", mixed["ctx"]["shots"])
    trace = film / "timing/motion/act2.json"
    trace.parent.mkdir(parents=True)
    previous = json.dumps(mixed["base"]).encode()
    trace.write_bytes(previous)
    fallback = []
    monkeypatch.setattr(runner, "_trace_written_while_rendering",
                        lambda *a: fallback.append(True) or {"written": True, "file": "timing/motion/act2.json"})
    eng = SimpleNamespace(scene=None, trace=lambda act: None)
    result = runner._refresh_trace(film, SimpleNamespace(act=SimpleNamespace(index=2)), eng,
                                   SimpleNamespace(chunks=[]))
    assert result["written"] is False
    assert fallback == []
    assert not trace.exists()
    retained = list((trace.parent / "failures").glob("*/previous.json"))
    assert retained and retained[0].read_bytes() == previous
