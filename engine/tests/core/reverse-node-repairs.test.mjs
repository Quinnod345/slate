// Offline regressions for reverse Node composition, captured sources and shared copy coverage.
// Use the installed engine by default; an absolute staged engine can be selected explicitly for offline integration.
// No browser, renderer, decoder, vendor or ordinary engine bridge is replaced or invoked by this harness.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {test} from 'vitest';
const engine = process.env.SLATE_REVERSE_TEST_ENGINE ?? fileURLToPath(new URL('../../', import.meta.url));
if (!engine || !path.isAbsolute(engine)) throw new Error('SLATE_REVERSE_TEST_ENGINE must name an absolute engine when set.');
const uri = rel => pathToFileURL(path.join(engine, rel)).href;
const {loadFilm} = await import(uri('remotion/lib/film.mjs'));
const {mixedScenesForAct, canonicalSource} = await import(uri('remotion/lib/shot_scenes.mjs'));
const {emitEvents} = await import(uri('remotion/events.mjs'));
const {ensureEntry} = await import(uri('remotion/entry.mjs'));
const {checkRemotionShots} = await import(uri('remotion/check.mjs'));
const {sha256Buffer} = await import(uri('remotion/lib/common.mjs'));
const readerPath = fileURLToPath(new URL('./fixtures/reverse-controlled-web-reader.mjs', import.meta.url));
const reader = await import(pathToFileURL(readerPath).href);
const put = (root, rel, contents) => {
  const file = path.join(root, rel);
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, contents);
  return file;
};
const event = (id, shot, frame, kind = 'land') => ({id, shot, frame, t: frame * 1001 / 30000,
  kind, size: 's', note: 'Neutral shape settles.'});
function fixture({owner = 3, twoActs = false, sharedCopy = false} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-reverse-node-controls-'));
  const film = path.join(root, 'film');
  const num = owner === true ? 1 : Number(owner);
  const groups = twoActs ? [{act: 3, raw: 3, start: 90, shot: 'R01'}, {act: 4, raw: 4, start: 126, shot: 'R02'}]
    : [{act: num, raw: owner, start: 90, shot: 'R01'}];
  const shots = groups.flatMap(g => [
    {id: 'B' + g.act + 'a', act: g.raw, frame_in: g.start, frame_out: g.start + 12},
    {id: g.shot, act: g.raw, engine: 'remotion', frame_in: g.start + 12, frame_out: g.start + 24},
    {id: 'B' + g.act + 'b', act: g.raw, frame_in: g.start + 24, frame_out: g.start + 36},
  ]);
  put(film, 'film.json', JSON.stringify({id: 'neutral-reverse-controls', width: 640, height: 360,
    fps_num: 30000, fps_den: 1001, engine: 'web', title: 'Neutral'}));
  put(film, 'timing/shots.json', JSON.stringify({shots}));
  put(film, '.slate/spoken.json', '{}\n');
  put(film, '.slate/lang.json', '{"language":"en","direction":"ltr"}\n');
  put(film, 'scenes/shared/web-helper.ts', 'export const neutral = 1;\n');
  const external = put(root, 'external-dependency.mjs', 'export const neutral = true;\n');
  const outside = put(root, 'outside-copy.tsx', 'export default function Outside(){return <span>outsidebanned outside claim</span>;}\nexport const frameIn=19;\n');
  if (sharedCopy) put(film, 'scenes/shared/copy.tsx',
    'export default function Shared(){return <span>peerbanned never promise</span>;}\nexport const frameIn=17;\n');
  for (const g of groups) {
    put(film, 'scenes/act' + g.act + '/web/index.html', '<!doctype html><title>Neutral base</title>\n');
    const extra = sharedCopy
      ? "import Shared from '../../../shared/copy';\nimport Outside from " + JSON.stringify(outside) + ";\n"
      : '';
    const component = sharedCopy ? 'return <><Shared/><Outside/></>;' : 'return null;';
    put(film, 'scenes/shots/' + g.shot + '/remotion/index.tsx',
      "import {defineMotion} from '@slate/kit/core';\n" + extra +
      "defineMotion({id:'card.x',shot:" + JSON.stringify(g.shot) + ",prop:'x',ease:'move',frame_in:" +
      (g.start + 13) + ',frame_out:' + (g.start + 23) + ",from:0,to:1});\n" +
      'export default function NeutralShot(){' + component + '}\n' +
      'export const events=' + JSON.stringify([event('shot.' + g.shot, g.shot, g.start + 19)]) + ';\n');
  }
  const captures = groups.map(g => event('capture.' + g.shot, g.shot, g.start + 21, 'tap'));
  const captureText = JSON.stringify({control: 'unique reverse capture parse authority', events: captures});
  const captureFile = put(film, 'captures/neutral/beats.json', captureText);
  return {root, film, groups, external, captureText, captureFile,
    cleanup: () => fs.rmSync(root, {recursive: true, force: true})};
}
async function dispatch(f, options = {}, args = {}) {
  reader.configure({external: f.external, ...options});
  const previous = process.env.SLATE_WEB_RENDER_MODULE;
  process.env.SLATE_WEB_RENDER_MODULE = readerPath;
  try { return await emitEvents(loadFilm(f.film), {dry_run: true, ...args}); }
  finally {
    if (previous === undefined) delete process.env.SLATE_WEB_RENDER_MODULE;
    else process.env.SLATE_WEB_RENDER_MODULE = previous;
  }
}
function noActiveEvidence(f) {
  assert.equal(fs.existsSync(path.join(f.film, 'timing/motion')), false);
  assert.equal(fs.existsSync(path.join(f.film, 'timing/visual_events')), false);
}
for (const owner of [true, '3']) test('raw owner refuses without coercion: ' + JSON.stringify(owner), async () => {
  const f = fixture({owner});
  try {
    const ctx = loadFilm(f.film); // This deliberately uses the actual loader's historically permissive Number conversion.
    assert.throws(() => mixedScenesForAct(ctx, ctx.acts[0].act), error =>
      error.kind === 'refused' && /positive whole owning act/.test(error.message));
    reader.configure({external: f.external});
    await assert.rejects(dispatch(f), error => error.kind === 'refused' && /positive whole owning act/.test(error.message));
    assert.equal(reader.calls.length, 0, 'Raw schema refusal must happen before the controlled page read.');
    noActiveEvidence(f);
  } finally { f.cleanup(); }
});
test('valid reverse, pure Web and forward declarations keep exact owner frames', () => {
  const f = fixture();
  try {
    const reverse = mixedScenesForAct(loadFilm(f.film), 3);
    assert.deepEqual(reverse.map(s => ({shot: s.shot, engine: s.engine, frames: s.frames, comp: s.comp})),
      [{shot: 'R01', engine: 'remotion', frames: [12, 23], comp: 'Shot-R01'}]);
    fs.rmSync(path.join(f.film, 'scenes/shots/R01/remotion'), {recursive: true});
    let shots = JSON.parse(fs.readFileSync(path.join(f.film, 'timing/shots.json'), 'utf8'));
    delete shots.shots[1].engine;
    fs.writeFileSync(path.join(f.film, 'timing/shots.json'), JSON.stringify(shots));
    assert.deepEqual(mixedScenesForAct(loadFilm(f.film), 3), []);
    fs.rmSync(path.join(f.film, 'scenes/act3/web'), {recursive: true});
    put(f.film, 'scenes/act3/index.tsx', 'export default function Act3(){return null;}\n');
    shots.shots[1].engine = 'web';
    fs.writeFileSync(path.join(f.film, 'timing/shots.json'), JSON.stringify(shots));
    put(f.film, 'scenes/shots/R01/web/index.html', '<!doctype html><title>Neutral forward</title>\n');
    assert.deepEqual(mixedScenesForAct(loadFilm(f.film), 3).map(s => ({shot: s.shot, engine: s.engine, frames: s.frames, comp: s.comp})),
      [{shot: 'R01', engine: 'web', frames: [12, 23], comp: null}]);
  } finally { f.cleanup(); }
});
test('public emit parses one capture generation across two real reverse module loads', async () => {
  const f = fixture({twoActs: true});
  const originalParse = JSON.parse;
  let parses = 0;
  JSON.parse = function(value, ...rest) {
    if (value === f.captureText) parses++;
    return originalParse.call(this, value, ...rest);
  };
  try {
    const out = await dispatch(f);
    assert.equal(parses, 1, 'One observed capture set must supply both acts.');
    assert.equal(reader.calls.length, 2);
    assert.deepEqual(out.skipped, []);
    assert.deepEqual(out.acts.map(a => a.act), [3, 4]);
    for (let i = 0; i < out.acts.length; i++) {
      const a = out.acts[i], g = f.groups[i];
      assert.deepEqual(a.problems, []);
      assert.equal(a.capture_beats, 1);
      assert.deepEqual(a.changes.added.map(e => e.id),
        ['base.B' + g.act + 'a', 'shot.' + g.shot, 'capture.' + g.shot, 'base.B' + g.act + 'b']);
      assert.equal(a.motion.entries, 3);
      assert.equal(a.motion.base_engine, 'web');
      assert.deepEqual(a.motion.remotion_shots, [g.shot]);
      const captured = canonicalSource(f.captureFile);
      assert.equal(a.source_hashes[captured], sha256Buffer(Buffer.from(f.captureText)));
      assert.equal(a.source_hashes[canonicalSource(f.external)], sha256Buffer(fs.readFileSync(f.external)));
      assert.ok(Object.hasOwn(a.source_hashes, canonicalSource(path.join(f.film, '.slate/lang.json'))));
    }
    assert.ok(reader.returned.every(p => p.sources.some(k => !path.isAbsolute(k)) &&
      p.sources.some(k => path.isAbsolute(k))), 'Use the genuine producer key shape.');
    noActiveEvidence(f);
  } finally { JSON.parse = originalParse; f.cleanup(); }
});
const refusals = [
  ['bad-relative-digest', /Captured source changed or disappeared/],
  ['conflicting-alias', /Conflicting captured source aliases/],
  ['source-list-mismatch', /source list and captured map disagree/],
  ['wrong-virtual-hash', /virtual film data does not match/],
  ['capture-byte-drift', /Capture source changed after its read/],
];
for (const [mode, message] of refusals) test('actual dispatch refuses observed proof mismatch: ' + mode, async () => {
  const f = fixture();
  try {
    await assert.rejects(dispatch(f, {mode}), error => error.kind === 'refused' && message.test(error.message));
    assert.equal(reader.calls.length, 1, 'A proof negative must reach the actual Web-read boundary.');
    noActiveEvidence(f);
  } finally { f.cleanup(); }
});
test('custom brand JSON and embedded SVG use the actual Web virtual context', async () => {
  const f = fixture();
  try {
    const brandFile = put(f.root, 'brand/custom-name.json', '{"id":"neutral","logos":{"mark":{"path":"mark.svg"}}}\n');
    const svg = put(f.root, 'brand/mark.svg', '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0h1v1H0z"/></svg>\n');
    const out = await dispatch(f, {}, {brand_path: brandFile});
    assert.equal(reader.calls.length, 1);
    assert.deepEqual(out.acts[0].problems, []);
    assert.equal(out.acts[0].source_hashes[canonicalSource(svg)], sha256Buffer(fs.readFileSync(svg)));
    await assert.rejects(dispatch(f, {mode: 'wrong-brand-context'}, {brand_path: brandFile}),
      error => error.kind === 'refused' && /virtual film data does not match/.test(error.message));
    assert.equal(reader.calls.length, 1);
    noActiveEvidence(f);
  } finally { f.cleanup(); }
});
for (const alias of [false, true]) test('actual shared-copy gates preserve physical coverage, alias=' + alias, async () => {
  const f = fixture({sharedCopy: true});
  try {
    const filmPath = alias ? path.join(f.root, 'film-alias') : f.film;
    if (alias) fs.symlinkSync(f.film, filmPath, 'dir');
    const ctx = loadFilm(filmPath);
    await ensureEntry(ctx);
    const gates = await checkRemotionShots(ctx, 3,
      {banned_words: ['peerbanned', 'outsidebanned'], never_claim: ['never promise', 'outside claim']});
    const module = gates.find(g => g.gate === 'handin.act3.shot.R01.module');
    assert.equal(module.status, 'PASS', JSON.stringify(module));
    const shared = canonicalSource(path.join(f.film, 'scenes/shared/copy.tsx'));
    assert.equal(module.inputs[shared], sha256Buffer(fs.readFileSync(shared)));
    for (const suffix of ['banned_words', 'never_claim', 'literals']) {
      const g = gates.find(g => g.gate === 'handin.act3.shot.R01.' + suffix);
      assert.equal(g.status, 'FAIL', JSON.stringify(g));
      const matches = g.evidence.filter(line => /shared[\\/]copy\.tsx/.test(line));
      assert.equal(matches.length, 1, 'Scan the canonical shared source exactly once: ' + JSON.stringify(g));
      assert.ok(g.evidence.every(line => !/outside-copy\.tsx/.test(line)), 'Outside-scene dependencies remain outside copy scope.');
    }
  } finally { f.cleanup(); }
});
