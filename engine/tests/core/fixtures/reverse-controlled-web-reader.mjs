// Controlled Web read boundary for offline reverse Node regressions.
// Every resolver, context generator, Remotion loader and compositor remains the actual engine implementation.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
const engine = process.env.SLATE_REVERSE_TEST_ENGINE ?? fileURLToPath(new URL('../../../', import.meta.url));
if (!engine || !path.isAbsolute(engine)) throw new Error('SLATE_REVERSE_TEST_ENGINE must name an absolute engine when set.');
const {loadFilm} = await import(pathToFileURL(path.join(engine, 'remotion/lib/film.mjs')).href);
const {filmDataFor} = await import(pathToFileURL(path.join(engine, 'web/render.mjs')).href);
const {sha256Buffer} = await import(pathToFileURL(path.join(engine, 'remotion/lib/common.mjs')).href);
let options = {};
export const calls = [];
export const returned = [];
export function configure(next = {}) { options = next; calls.length = 0; returned.length = 0; }
const event = (id, shot, frame) => ({id, shot, frame, t: frame * 1001 / 30000, kind: 'land', size: 's', note: 'Neutral shape settles.'});
export async function webRender(args) {
  calls.push({...args});
  if (!(args.emit === true && args.dry_run === true && args.trace_rows === true && args.trace === false)) {
    throw new Error('The actual dispatch did not request a non-writing raw Web trace.');
  }
  const ctx = loadFilm(args.film);
  const own = ctx.acts.find(a => a.act === args.act);
  const rows = ctx.shots.shots.filter(s => s.act === own.act);
  const hashes = {};
  const observe = key => {
    const file = path.resolve(ctx.dir, key);
    hashes[key] = sha256Buffer(fs.readFileSync(file));
  };
  for (const key of ['film.json', 'timing/shots.json', '.slate/spoken.json', '.slate/lang.json',
    path.relative(ctx.dir, path.join(args.scene, 'index.html')), 'scenes/shared/web-helper.ts']) observe(key);
  // The actual producer returns relative film paths alongside absolute dependencies.
  observe(options.external);
  let brand = null, brandDir = null;
  if (args.brand_path) {
    const requested = path.resolve(args.brand_path);
    const file = fs.statSync(requested).isDirectory() ? path.join(requested, 'brand.json') : requested;
    brand = JSON.parse(fs.readFileSync(file, 'utf8'));
    brandDir = path.dirname(file);
    observe(file);
    for (const logo of Object.values(brand.logos ?? {})) if (logo.path) observe(path.resolve(brandDir, logo.path));
  }
  const virtual = filmDataFor(ctx, options.mode === 'wrong-brand-context' ? null : brand,
    options.mode === 'wrong-brand-context' ? null : brandDir);
  let filmHash = sha256Buffer(Buffer.from(virtual.contents));
  if (options.mode === 'wrong-virtual-hash') filmHash = '0'.repeat(64);
  if (options.mode === 'bad-relative-digest') hashes['film.json'] = '0'.repeat(64);
  if (options.mode === 'conflicting-alias') hashes[path.resolve(ctx.dir, 'film.json')] = '0'.repeat(64);
  if (options.mode === 'capture-byte-drift') {
    const file = path.join(ctx.dir, 'captures/neutral/beats.json');
    fs.appendFileSync(file, ' ');
  }
  const rawEvents = rows.map(s => event('base.' + s.id, s.id, s.frame_in + 6));
  const page = {act: own.act, problems: [], scene_problems: [], misses: [], gates: [],
    raw_declarations: {events: rawEvents, silent: undefined, problems: [], misses: []},
    beats: rawEvents, source_hashes: hashes, sources: Object.keys(hashes).sort(),
    trace_read: {act: own.act, frame_base: 'film', act_frames: [own.frameIn, own.frameOut],
      fps_num: ctx.fps.num, fps_den: ctx.fps.den, film_data_sha256: filmHash,
      rows: rows.map(s => ({id: 'base.' + s.id, shot: s.id, prop: 'x', ease: 'move',
        frame_in: s.frame_in + 1, frame_out: s.frame_out - 1, from: 0, to: 1,
        samples: [[s.frame_in + 1, 0], [s.frame_out - 1, 1]]}))}};
  if (options.mode === 'source-list-mismatch') page.sources.pop();
  returned.push(page);
  return {acts: [page]};
}
