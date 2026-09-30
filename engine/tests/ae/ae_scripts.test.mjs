// Slate's ExtendScript, run in node: the ease vectors, the ES3 lint, and the build, audit, queue, trace and smoke
// scripts against a mock of After Effects' scripting DOM. The mock draws keyframes the way AE does (speed and
// influence per key), so these tests check the curves AE would draw, not just that the scripts run.
import {existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {describe, expect, it} from 'vitest';
import {makeAE, settingsReport} from '../../ae/tests/ae_mock.mjs';
import {lintEs3, loadJsx} from '../../ae/tests/jsx_loader.mjs';
import {loadMath, runVectors} from '../../ae/tests/run_vectors.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const AE = path.resolve(here, '../../ae');
const SL = loadMath();

// ---------------------------------------------------------------------------------------------------- fixtures

const FPS_NUM = 24000;
const FPS_DEN = 1001;
const FPS = FPS_NUM / FPS_DEN;
const W = 1280;
const H = 720;

function neutralFilm(dir, {withAudio = true} = {}) {
  const media = {};
  const file = (rel, meta) => {
    const p = path.join(dir, rel);
    mkdirSync(path.dirname(p), {recursive: true});
    writeFileSync(p, '');
    media[p] = meta;
    return p;
  };
  const act1 = file('renders/act1.mov', {width: W, height: H, frameRate: FPS, duration: 120 / FPS, hasVideo: true});
  const act3 = file('renders/act3.mov', {width: W, height: H, frameRate: FPS, duration: 72 / FPS, hasVideo: true});
  const mix = withAudio ? file('sound/mix.wav', {duration: 264 / FPS + 0.01, hasAudio: true, hasVideo: false}) : null;
  const square = [[0, 0], [100, 0], [100, 100], [0, 100]];
  const zeros = square.map(() => [0, 0]);
  const spec = {
    generated: 'do not edit by hand: slate ae build',
    film: {id: 'neutral', title: 'Neutral', fps_num: FPS_NUM, fps_den: FPS_DEN, width: W, height: H, pixel_aspect: 1, total_frames: 264},
    project: {bpc: 16, working_space: 'Rec.709 Gamma 2.4', linear_blending: false, gpu: 'software', sample_bias_frames: 0.1,
      motion_blur: {shutter_angle: 180, shutter_phase: -90, samples: 16, adaptive_limit: 128}},
    names: {master: 'MASTER neutral', brand_board: 'BRAND neutral',
      folders: {shots: '01 Shots', acts: '02 Acts', master: '03 Master', renders: '12 Renders', audio: '11 Audio', brand: '20 Brand'}},
    paths: {aep: path.join(dir, 'ae', 'neutral.aep'), preserve_dir: path.join(dir, 'preserved')},
    acts: [
      {index: 1, name: 'Opening', comp: 'A1 Opening', shots: ['S1', 'S2'], frame_in: 0, frame_out: 120, engine: 'render',
        markers: [{frame: 30, label: 'S1/land'}]},
      {index: 2, name: 'Built in AE', comp: 'A2 Built in AE', shots: ['S3'], frame_in: 120, frame_out: 192, engine: 'ae',
        markers: [], scene: {
          layers: [
            {id: 'bg', type: 'solid', color: 'paper'},
            {id: 'card', type: 'rect', size: [400, 240], roundness: 24, fill: 'accent', transform: {position: [640, 360]}},
            {id: 'title', type: 'text', text: 'Hello', font: 'display', font_size: 48, color: 'ink', transform: {position: [640, 200]}},
            {id: 'mark', type: 'logo', logo: 'mark', width: 200, pos: [540, 500]},
          ],
          anims: [
            {id: 'card-in', layer: 'card', prop: 'position', keys: [[0, [640, 460], 'enter'], [24, [640, 360], 'linear']]},
            {id: 'card-fade', layer: 'card', prop: 'opacity', keys: [[0, 0, 'enter'], [12, 100, 'linear']]},
            {id: 'card-grow', layer: 'card', prop: 'scale', keys: [[0, [90, 90], 'enter'], [24, [100, 100], 'linear']]},
            {id: 'title-in', layer: 'title', prop: 'position', keys: [[10, [640, 240], 'overshoot'], [40, [640, 200], 'linear']]},
            {id: 'title-fade', layer: 'title', prop: 'opacity', keys: [[10, 0, 'move'], [30, 100, 'linear']]},
          ],
        }},
      {index: 3, name: 'Close', comp: 'A3 Close', shots: ['S4', 'S5'], frame_in: 192, frame_out: 264, engine: 'render', markers: []},
    ],
    shots: [
      {id: 'S1', act: 1, frame_in: 0, frame_out: 48, frames: 48, on_screen: '', picture: 'opening'},
      {id: 'S2', act: 1, frame_in: 48, frame_out: 120, frames: 72, on_screen: '', picture: 'turn'},
      {id: 'S3', act: 2, frame_in: 120, frame_out: 192, frames: 72, on_screen: 'Hello', picture: 'the card'},
      {id: 'S4', act: 3, frame_in: 192, frame_out: 240, frames: 48, on_screen: '', picture: 'close'},
      {id: 'S5', act: 3, frame_in: 240, frame_out: 264, frames: 24, on_screen: '', picture: 'end card'},
    ],
    renders: [
      {act: 1, file: act1, frame_in: 0, frame_out: 120, label: 'act 1 (remotion)'},
      {act: 3, file: act3, frame_in: 192, frame_out: 264, label: 'act 3 (remotion)'},
    ],
    audio: mix ? {file: mix} : null,
    markers: {
      shots: [0, 48, 120, 192, 240].map((f, i) => ({frame: f, label: `S${i + 1}`})),
      words: [{frame: 12.4, label: 'S1 hello'}, {frame: 20.7, label: 'S1 world'}],
      events: [{frame: 30, label: 'S1/land'}, {frame: 140, label: 'S3/card'}],
    },
    brand: {
      name: 'neutral',
      palette: {paper: '#F4F1EA', ink: '#1C1C1C', accent: '#3366FF'},
      fonts: {display: {postscript: 'MockSans-Regular', family: 'Mock Sans'}},
      eases: {enter: [0, 0, 0.58, 1], move: 'sine', overshoot: [0.34, 1.56, 0.64, 1]},
      logos: {mark: {viewBox: [0, 0, 100, 100], items: [{id: 'p1', fill: '#3366FF',
        paths: [{v: square, i: zeros, o: zeros, c: true}], bbox: [0, 0, 100, 100]}]}},
    },
    masters: {
      chunks: [0, 1, 2, 3].map((k) => ({index: k, start: k * 66, end: k * 66 + 65, frames: 66})),
      om_primary: 'H.264 - Match Render Settings - 40 Mbps', om_alpha: 'High Quality with Alpha', rs: 'Best Settings',
      dir: path.join(dir, 'masters', 'chunks'), primary_name: 'h264_c{k}.mp4', alpha_name: 'alpha_c{k}.mov',
    },
  };
  const specPath = path.join(dir, 'ae', 'build_spec.json');
  mkdirSync(path.dirname(specPath), {recursive: true});
  writeFileSync(specPath, JSON.stringify(spec));
  return {spec, specPath, media};
}

function tmp() {
  return mkdtempSync(path.join(tmpdir(), 'slate-ae-'));
}

function run(ae, script, args, runInfo = {}) {
  const ctx = {console};
  ctx.$ = {global: ctx, fileName: path.join(AE, script), stack: '', writeln() {}};
  Object.assign(ctx, ae.globals);
  ctx.SLATE_ARGS = args;
  ctx.SLATE_RUN = {run_id: `r${Math.random().toString(16).slice(2)}`, channel: 'run', section: 'Slate', report_file: null, ...runInfo};
  vm.createContext(ctx);
  const line = new vm.Script(loadJsx(path.join(AE, script)), {filename: script}).runInContext(ctx);
  return {line, rep: settingsReport(ae.env), ctx};
}

const secs = (f) => (f * FPS_DEN) / FPS_NUM;
const comp = (ae, name) => ae.getProject()._items.find((i) => i.name === name && i.layers);
const layerNamed = (c, name) => c._layers.find((l) => l.name === name);

// ---------------------------------------------------------------------------------------------------- the maths

describe('the ExtendScript ease kit', () => {
  it('matches the shared cross-language vectors', () => {
    const res = runVectors();
    expect(res.failures).toEqual([]);
    expect(res.ok).toBe(true);
    expect(res.counts.value).toBeGreaterThan(100);
  });

  it('matches the local stand-in too', () => {
    const res = runVectors({vectors: path.join(AE, 'tests/fixtures/eases.local.json')});
    expect(res.failures).toEqual([]);
  });

  it('has exact endpoints and reads every kind the kit reads', () => {
    for (const def of [[0.2, 0, 0, 1], 'cubic-bezier(0.4, 0.14, 0.3, 1)', 'sine', 'sway', 'linear', 'spring', {kind: 'spring0', stiffness: 12},
      {bezier: [0.42, 0, 0.58, 1]}, {cubic_bezier: '0.25, 0.1, 0.25, 1'}, {kind: 'bezier', points: [0.3, 0, 0.8, 0.15]}]) {
      expect(SL.easeValue(def, 0)).toBe(0);
      expect(SL.easeValue(def, -1)).toBe(0);
      expect(SL.easeValue(def, NaN)).toBe(0);
      expect(SL.easeValue(def, 1)).toBe(1);
      expect(SL.easeValue(def, 7)).toBe(1);
    }
    expect(() => SL.normEase([1.2, 0, 0.5, 1])).toThrow(/x values/);
    expect(() => SL.normEase('bouncy')).toThrow();
  });
});

describe('ES3 discipline', () => {
  const files = readdirSync(AE).filter((f) => f.endsWith('.jsx')).map((f) => path.join(AE, f))
    .concat(readdirSync(path.join(AE, 'lib')).map((f) => path.join(AE, 'lib', f)));

  it.each(files)('%s is ES3 and compiles', (f) => {
    expect(lintEs3(readFileSync(f, 'utf8'))).toEqual([]);
    expect(() => new vm.Script(loadJsx(f), {filename: f})).not.toThrow();
  });

  it('the lint catches what AE rejects', () => {
    const bad = 'let a = 1;\nvar b = [1, 2,];\nvar c = x.map(function (y) { return y; });\nvar d = JSON.stringify(a);\nvar e = () => 1;\n';
    const rules = lintEs3(bad).map((p) => p.rule);
    expect(rules).toEqual(expect.arrayContaining(['let/const (ES3 has var only)', 'trailing comma', 'ES5+ array method',
      'JSON object (ES3 has none; use SL.parseJSON / SL.toJSON)', 'arrow function']));
    expect(lintEs3('var s = "let x = y.map(z)"; // JSON.parse in a comment\n')).toEqual([]);
  });

  it('no include directive hides in a comment', () => {
    for (const f of files) {
      const lines = readFileSync(f, 'utf8').split('\n');
      let inBlock = false;
      for (const line of lines) {
        if (inBlock && /^\s*#include/.test(line)) throw new Error(`${f}: #include inside a comment`);
        if (line.includes('/*')) inBlock = true;
        if (line.includes('*/')) inBlock = false;
      }
    }
  });
});

// ---------------------------------------------------------------------------------------------------- the build

describe('build_film.jsx', () => {
  it('builds every comp on the film clock and saves the project', () => {
    const dir = tmp();
    const {spec, specPath, media} = neutralFilm(dir);
    const ae = makeAE({media});
    const {rep} = run(ae, 'build_film.jsx', {spec: specPath});
    expect(rep.status).toBe('OK');
    const P = ae.getProject();
    expect(P.bitsPerChannel).toBe(16);
    expect(P.workingSpace).toBe('Rec.709 Gamma 2.4');
    expect(P.linearBlending).toBe(false);
    expect(P.gpuAccelType).toBe(1813);
    expect(ae.env.saves).toContain(spec.paths.aep);
    expect(existsSync(spec.paths.aep)).toBe(true);
    expect(ae.env.discarded).toEqual([]);

    for (const sh of spec.shots.filter((s) => s.act !== 2)) {
      const c = comp(ae, sh.id);
      expect(Math.round(c.duration * FPS)).toBe(sh.frames);
      expect(c.frameRate).toBeCloseTo(FPS, 9);
      expect([c.width, c.height]).toEqual([W, H]);
    }
    // S2's slice of the act 1 render starts 48 frames back, a tenth of a frame early
    const s2 = comp(ae, 'S2');
    expect(s2._layers[0].startTime).toBeCloseTo(secs(0 - 48) - 0.1 / FPS, 12);
    expect(s2.comment.startsWith('RENDERED')).toBe(true);
    // acts hold their shots in order, biased
    const a1 = comp(ae, 'A1 Opening');
    const s2InA1 = a1._layers.find((l) => l.source && l.source.name === 'S2');
    expect(s2InA1.startTime).toBeCloseTo(secs(48) - 0.1 / FPS, 12);
    expect(a1.markerProperty.numKeys).toBe(1);
    // the master: every act, biased; the mix at 0; markers per shot, word and event
    const M = comp(ae, 'MASTER neutral');
    expect(Math.round(M.duration * FPS)).toBe(264);
    const a3InM = M._layers.find((l) => l.source && l.source.name === 'A3 Close');
    expect(a3InM.startTime).toBeCloseTo(secs(192) - 0.1 / FPS, 12);
    expect(M.markerProperty.numKeys).toBe(5);
    const aud = M._layers.find((l) => l.name.startsWith('AUDIO'));
    expect(aud.startTime).toBe(0);
    expect(aud.marker.numKeys).toBe(2);
    expect(layerNamed(M, 'EVENTS (timing/timeline.json)').marker.numKeys).toBe(2);
    // the masters queue: spans read back in frames, two outputs each
    expect(rep.data.queue.map((q) => [q.start, q.frames, q.match, q.outputs])).toEqual(
      [[0, 66, true, 2], [66, 66, true, 2], [132, 66, true, 2], [198, 66, true, 2]]);
    const rq = P.renderQueue._q._items;
    expect(rq[1].outputModule(2).template).toBe('High Quality with Alpha');
    expect(rq[3].outputModule(1).file.fsName).toBe(path.join(dir, 'masters/chunks/h264_c3.mp4'));
    // the brand board
    expect(rep.data.brand_board).toEqual({palette: 3, fonts: 1, logos: 1});
    expect(rep.data.fonts_used).toContain('MockSans-Regular');
    expect(rep.data.built.rendered).toEqual(['S1', 'S2', 'S4', 'S5']);
    expect(rep.data.check.frames).toEqual([]);
  });

  it('never discards unsaved work: it is saved to the preserve folder first', () => {
    const dir = tmp();
    const {spec, specPath, media} = neutralFilm(dir);
    const ae = makeAE({media, dirtyProject: true});
    const {rep} = run(ae, 'build_film.jsx', {spec: specPath});
    expect(rep.status).toBe('OK');
    expect(rep.data.preserved_project.startsWith(spec.paths.preserve_dir)).toBe(true);
    expect(ae.env.saves[0]).toBe(rep.data.preserved_project);
    expect(ae.env.discarded).toEqual([]);
  });

  it('marks an unrendered shot as a placeholder the audit fails', () => {
    const dir = tmp();
    const {spec, specPath, media} = neutralFilm(dir);
    spec.renders = spec.renders.slice(0, 1);
    writeFileSync(specPath, JSON.stringify(spec));
    const ae = makeAE({media});
    const {rep} = run(ae, 'build_film.jsx', {spec: specPath});
    expect(rep.data.built.placeholders).toEqual(['S4', 'S5']);
    const audit = run(ae, 'validate.jsx', {spec: specPath});
    const byName = Object.fromEntries(audit.rep.data.checks.map((c) => [c.check, c]));
    expect(byName['shots.picture'].status).toBe('FAIL');
    expect(byName['shots.picture'].evidence).toContain('S4,S5');
  });

  it('draws an AE-built act with the brand eases as AE speed and influence', () => {
    const dir = tmp();
    const {spec, specPath, media} = neutralFilm(dir);
    const ae = makeAE({media});
    run(ae, 'build_film.jsx', {spec: specPath});
    const a2 = comp(ae, 'A2 Built in AE');
    expect(a2.comment.startsWith('AE-BUILT')).toBe(true);
    const card = layerNamed(a2, 'card');
    const pos = card.property('ADBE Transform Group').property('ADBE Position');
    const scale = card.property('ADBE Transform Group').property('ADBE Scale');
    const enter = spec.brand.eases.enter;
    for (let f = 0; f <= 24; f++) {
      const want = 460 + (360 - 460) * SL.easeValue(enter, f / 24);
      expect(Math.abs(pos.valueAtTime(secs(f))[1] - want)).toBeLessThan(0.005 * 100);
      const ws = 90 + 10 * SL.easeValue(enter, f / 24);
      expect(Math.abs(scale.valueAtTime(secs(f))[0] - ws)).toBeLessThan(0.005 * 10);
    }
    // ease-out's out-handle sits on its key: AE gets the smallest influence and the curve's real tangent
    expect(pos.keyOutTemporalEase(1)[0].influence).toBeCloseTo(0.1, 12);
    expect(pos.keyInTemporalEase(2)[0].influence).toBeCloseTo(42, 9);
    // the overshooting title is keyed per dimension (AE's spatial speed can't run backwards)
    const title = layerNamed(a2, 'title');
    const tpos = title.property('ADBE Transform Group').property('ADBE Position');
    expect(tpos.dimensionsSeparated).toBe(true);
    let lowest = Infinity;
    for (let f = 10; f <= 40; f++) {
      const want = 240 + (200 - 240) * SL.easeValue(spec.brand.eases.overshoot, (f - 10) / 30);
      const got = tpos.valueAtTime(secs(f))[1];
      expect(Math.abs(got - want)).toBeLessThan(0.005 * 40);
      lowest = Math.min(lowest, got);
    }
    expect(lowest).toBeLessThan(200); // it really overshoots
    // the sine fade runs through the exact-ease expression
    const op = title.property('ADBE Transform Group').property('ADBE Opacity');
    expect(op.expression).toContain('Slate: exact eases');
    const ctx = vm.createContext({Math, numKeys: op.numKeys, key: (k) => ({time: op.keyTime(k), value: op.keyValue(k)}),
      value: 0, add: (a, b) => a + b, mul: (a, b) => a * b, sub: (a, b) => a - b});
    for (const f of [8, 10, 15, 20, 25, 30, 34]) {
      ctx.time = secs(f);
      ctx.value = op.valueAtTime(secs(f)); // AE's `value`: the property without its expression
      const got = vm.runInContext(op.expression, ctx);
      expect(got).toBeCloseTo(100 * SL.easeValue('sine', (f - 10) / 20), 9);
    }
    // the logo became native shape groups
    const logo = layerNamed(a2, 'mark');
    expect(logo.property('ADBE Root Vectors Group').property('p1').property('ADBE Vectors Group').numProperties).toBe(2);
  });
});

// ---------------------------------------------------------------------------------------------------- the audit

describe('validate.jsx', () => {
  it('passes a fresh build and fails what drifts', () => {
    const dir = tmp();
    const {specPath, media} = neutralFilm(dir);
    const ae = makeAE({media});
    run(ae, 'build_film.jsx', {spec: specPath});
    const first = run(ae, 'validate.jsx', {spec: specPath});
    expect(first.rep.status).toBe('OK');
    const checks = first.rep.data.checks;
    expect(checks.filter((c) => c.status !== 'PASS')).toEqual([]);
    expect(checks.map((c) => c.check)).toEqual(['project.bpc', 'project.workingSpace', 'project.linearBlending',
      'shots.comps', 'shots.picture', 'acts.comps', 'footage.missing', 'renders.format', 'master.frames', 'master.audio',
      'master.markers', 'queue.chunks', 'fonts', 'expressions', 'motionblur.native']);

    comp(ae, 'S1').duration = secs(47);
    comp(ae, 'A2 Built in AE')._layers.find((l) => l.name === 'card').motionBlur = false;
    ae.getProject().workingSpace = 'sRGB IEC61966-2.1';
    const second = run(ae, 'validate.jsx', {spec: specPath});
    const failed = second.rep.data.checks.filter((c) => c.status === 'FAIL').map((c) => c.check);
    expect(failed).toEqual(['project.workingSpace', 'shots.comps', 'motionblur.native']);
    const shots = second.rep.data.checks.find((c) => c.check === 'shots.comps');
    expect(shots.evidence).toContain('S1: frames 47!=48');
  });

  it('reports fonts it cannot list as UNCHECKED, and missing fonts as FAIL', () => {
    const dir = tmp();
    const {specPath, media} = neutralFilm(dir, {withAudio: false});
    const ae = makeAE({media, missingFonts: ['MockSans-Regular']});
    run(ae, 'build_film.jsx', {spec: specPath});
    const {rep} = run(ae, 'validate.jsx', {spec: specPath});
    const byName = Object.fromEntries(rep.data.checks.map((c) => [c.check, c.status]));
    expect(byName.fonts).toBe('FAIL');
    expect(byName['master.audio']).toBe('UNCHECKED');
  });
});

// ---------------------------------------------------------------------------------------------------- queue, trace, smoke

describe('queue_masters.jsx, trace_export.jsx and smoke_build.jsx', () => {
  it('re-queues an existing project with spans read back', () => {
    const dir = tmp();
    const {spec, specPath, media} = neutralFilm(dir);
    const ae = makeAE({media});
    run(ae, 'build_film.jsx', {spec: specPath});
    spec.masters.chunks = [0, 1].map((k) => ({index: k, start: k * 132, end: k * 132 + 131, frames: 132}));
    writeFileSync(specPath, JSON.stringify(spec));
    const {rep} = run(ae, 'queue_masters.jsx', {spec: specPath});
    expect(rep.status).toBe('OK');
    expect(rep.data.queue.map((q) => [q.start, q.frames])).toEqual([[0, 132], [132, 132]]);
  });

  it('samples AE-built acts as AE evaluates them', () => {
    const dir = tmp();
    const {specPath, media} = neutralFilm(dir);
    const ae = makeAE({media});
    run(ae, 'build_film.jsx', {spec: specPath});
    const {rep} = run(ae, 'trace_export.jsx', {spec: specPath, acts: [2]});
    expect(rep.status).toBe('OK');
    const tr = rep.data.traces['2'];
    const names = tr.map((t) => `${t.layer}/${t.match_name}`);
    expect(names).toEqual(expect.arrayContaining(['card/ADBE Position', 'card/ADBE Scale', 'title/ADBE Position_1']));
    const cardPos = tr.find((t) => t.layer === 'card' && t.match_name === 'ADBE Position');
    expect(cardPos.frame_in).toBe(0);
    expect(cardPos.frame_out).toBe(24);
    expect(cardPos.samples).toHaveLength(25);
  });

  it('smoke: stops on unsaved work, and otherwise draws the ease within a hair', () => {
    const args = {out_dir: tmp(), comp_name: 'SLATE_SMOKE', width: 960, height: 540, fps_num: 30, fps_den: 1, frames: 30,
      start_frame: 0, end_frame: 24, from_x: 120, to_x: 840, y: 270, diameter: 72, ease: [0.05, 0.7, 0.1, 1.0],
      bg: '#141414', dot: '#FFD633'};
    const dirty = makeAE({dirtyProject: true, prefs: {'Main Pref Section v2': {Pref_SCRIPTING_FILE_NETWORK_SECURITY: 0}}});
    expect(run(dirty, 'smoke_build.jsx', args).rep.status).toBe('ABORT open project has unsaved changes');
    expect(dirty.env.saves).toEqual([]);
    expect(dirty.env.discarded).toEqual([]);

    const ae = makeAE({prefs: {'Main Pref Section v2': {Pref_SCRIPTING_FILE_NETWORK_SECURITY: 1}}});
    const {rep} = run(ae, 'smoke_build.jsx', args);
    expect(rep.status).toBe('OK');
    expect(rep.data.pref_scripting_file_network_security).toBe(1);
    expect(rep.data.curve_max_error_px).toBeLessThan(0.05);
    expect(rep.data.readback.key1_out[1]).toBeCloseTo(5, 9); // influence = x1 = 5%
    expect(rep.data.om_templates).toContain('High Quality');
    expect(existsSync(path.join(args.out_dir, 'smoke.aep'))).toBe(true);
  });

  it('reports through a file when scripts may write files', () => {
    const dir = tmp();
    const report = path.join(dir, 'report.json');
    const ae = makeAE({});
    run(ae, 'open_project.jsx', {}, {run_id: 'rfile', report_file: report});
    const got = JSON.parse(readFileSync(report, 'utf8'));
    expect(got.run_id).toBe('rfile');
    expect(got.status).toBe('OK');
    expect(got.data.file).toBe('(untitled)');
    expect(settingsReport(ae.env).status).toBe('OK');
  });
});

describe('the small scripts and every kind of key', () => {
  it('open_project, purge, software_gpu and font_check run and report', () => {
    const dir = tmp();
    const aep = path.join(dir, 'p.aep');
    writeFileSync(aep, '{}');
    const ae = makeAE({fonts: [[{familyName: 'Mock Sans', styleName: 'Bold', postScriptName: 'MockSans-Bold'}],
      [{familyName: 'Other', styleName: 'Regular', postScriptName: 'Other-Regular'}]]});
    expect(run(ae, 'open_project.jsx', {}).rep.data).toMatchObject({file: '(untitled)', dirty: false, items: 0});
    expect(run(ae, 'purge.jsx', {}).rep.status).toBe('OK');
    expect(ae.env.purged).toBe(1);
    const sw = run(ae, 'software_gpu.jsx', {aep, preserve_dir: path.join(dir, 'kept')});
    expect(sw.rep.status).toBe('OK');
    expect(ae.getProject().gpuAccelType).toBe(1813);
    const fonts = run(ae, 'font_check.jsx', {families: ['Mock Sans', 'Missing Face'], pattern: '^(Mock\\ Sans|Missing\\ Face)$'});
    expect(fonts.rep.data.families).toEqual({'Mock Sans': ['Bold:MockSans-Bold']});
    expect(fonts.rep.data.missing).toEqual(['Missing Face']);
  });

  it('SL.anim draws hold, linear, spring (by expression) and path morphs on the film clock', () => {
    const ae = makeAE({});
    const ctx = {console};
    ctx.$ = {global: ctx, fileName: 'inline.jsx', stack: '', writeln() {}};
    Object.assign(ctx, ae.globals);
    vm.createContext(ctx);
    new vm.Script(loadJsx(path.join(AE, 'lib/slate_math.jsx')) + '\n' + loadJsx(path.join(AE, 'lib/slate_ae.jsx'))).runInContext(ctx);
    const S = ctx.SL;
    const film = S.film({film: {width: 640, height: 360, fps_num: 25, fps_den: 1, pixel_aspect: 1},
      project: {}, brand: {eases: {settle: {kind: 'spring0', stiffness: 9}, firm: [0.2, 0.7, 0.2, 1]}, palette: {ink: '#101010'}}});
    const c = S.comp(film, 'K', 50);
    const L = S.shapeLayer(c, 'blob', 'comp');
    S.group(L, 'blob');
    S.path(S.contents(L).property('blob'), [[0, 0], [10, 0], [10, 10]], true);
    const op = S.xf(L, 'opacity');
    S.anim(op, film, [[0, 0, 'hold'], [10, 100, 'linear'], [20, 50, 'settle'], [30, 80, 'firm'], [40, 20, 'linear']]);
    expect(op.valueAtTime(5 / 25)).toBe(0); // hold
    expect(op.valueAtTime(15 / 25)).toBeCloseTo(75, 6); // linear 100 -> 50
    expect(op.keyOutInterpolationType(1)).toBe(6614);
    expect(op.keyInInterpolationType(3)).toBe(6612); // the key after a linear segment arrives linear
    expect(op.expression).toContain('spring0'); // the spring segment is exact through the expression
    // the firm segment (30 -> 40) is AE speed and influence: AE's own curve follows the ease
    for (let f = 30; f <= 40; f++) {
      expect(Math.abs(op.valueAtTime(f / 25) - (80 + (20 - 80) * S.easeValue('firm', (f - 30) / 10)))).toBeLessThan(0.005 * 60);
    }
    // the spring segment (20 -> 30) runs through the exact-ease expression
    const ex = vm.createContext({Math, numKeys: op.numKeys, key: (k) => ({time: op.keyTime(k), value: op.keyValue(k)}),
      add: (a, b) => a + b, mul: (a, b) => a * b, sub: (a, b) => a - b});
    for (let f = 20; f <= 30; f++) {
      ex.time = f / 25;
      ex.value = op.valueAtTime(f / 25);
      const want = 50 + 30 * S.easeValue({kind: 'spring0', stiffness: 9}, (f - 20) / 10);
      expect(vm.runInContext(op.expression, ex)).toBeCloseTo(want, 9);
    }
    const shape = S.contents(S.contents(L).property('blob')).property(1).property('ADBE Vector Shape');
    S.anim(shape, film, [[0, {v: [[0, 0], [10, 0], [10, 10]], i: [[0, 0], [0, 0], [0, 0]], o: [[0, 0], [0, 0], [0, 0]], c: true}, 'firm'],
      [25, {v: [[0, 100], [10, 100], [10, 110]], i: [[0, 0], [0, 0], [0, 0]], o: [[0, 0], [0, 0], [0, 0]], c: true}, 'linear']]);
    for (let f = 0; f <= 25; f += 5) {
      const y = shape.valueAtTime(f / 25).vertices[0][1];
      expect(Math.abs(y - 100 * S.easeValue('firm', f / 25))).toBeLessThan(0.5); // paths ease on normalised progress
    }
    const col = S.contents(S.contents(L).property('blob')).property(1);
    expect(col.matchName).toBe('ADBE Vector Shape - Group');
  });
});
