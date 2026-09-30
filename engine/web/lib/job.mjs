// What a web-render call asks for, checked and resolved before anything starts: the film and act (or a scratch film
// for a scene rendered on its own), the scene page, the frame range, the samples, the output and the policies.
//
// The film clock is the film's: frame rate and size come from film.json and the act's frames from
// timing/shots.json, through engine-core's loader (engine/remotion/lib/film.mjs), exactly as a Remotion act gets
// them. A scene rendered without a film gets a scratch film whose rate and size come from the args, or from the
// engine's neutral film (engine/remotion/neutral/film.json): never from numbers written here.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {ENGINE_DIR, KIT_DIR, NODE_MODULES, missing, readJson, usage, writeJsonAtomic} from '../../remotion/lib/common.mjs';
import {actOf, loadFilm} from '../../remotion/lib/film.mjs';
import {DEFAULT_GL, glFlags} from './browser.mjs';
import {DELIVERY_SAMPLES, DRAFT_SAMPLES, MAX_SAMPLES, outputKind} from './encode.mjs';
import {DEFAULT_EPOCH_MS, DEFAULT_SEED} from './page-clock.mjs';

/** The engine's version of the open web renderer (the runtime's WEB_RUNTIME_VERSION says the same; a test holds them). */
export const WEB_ENGINE_VERSION = '0.1.0';

export const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const RUNTIME_DIR = path.join(WEB_DIR, 'runtime');
export const TEMPLATE_DIR = path.join(WEB_DIR, 'template');
export const NEUTRAL_FILM_JSON = path.join(ENGINE_DIR, 'remotion', 'neutral', 'film.json');
export {ENGINE_DIR, KIT_DIR, NODE_MODULES};

export const DEFAULT_SHUTTER = 180;
export const MAX_CONCURRENCY = 8;
/** The neutral brand pack a scene rendered on its own is drawn in, when the repo has one (brands/demo). */
export const DEMO_BRAND_DIR = path.join(ENGINE_DIR, '..', 'brands', 'demo');

/**
 * Pages drawing at once when a call doesn't say: a quarter of the cores, from 1 to 4. Each page is a full browser tab
 * at the film's size, so this leaves the Mac usable while other work runs.
 */
export function defaultConcurrency() {
  return Math.max(1, Math.min(4, Math.floor(os.cpus().length / 4)));
}
export const LOAD_TIMEOUT_MS = 60000;
export const SEEK_TIMEOUT_MS = 30000;

/**
 * Where each of `samples` sub-frames sits, in frames from its master frame: spread across a shutter of `shutter`
 * degrees, centred on the frame (phase −90°). The kit's filmFrameOf (engine/remotion/kit/clock.tsx) uses the same
 * formula, so a web act and a Remotion act blur alike; engine/tests/web/job.test.mjs holds them together.
 */
export function sampleOffsets(samples, shutter = DEFAULT_SHUTTER) {
  const S = Math.max(1, Math.round(samples));
  if (S === 1) return [0];
  return Array.from({length: S}, (_, k) => (shutter / 360) * ((k + 0.5) / S - 0.5));
}

/** The act seconds of each sub-frame of act frame `frame`: t = (frame + offset) × fps_den ÷ fps_num. */
export function sampleTimes(frame, samples, shutter, fps) {
  return sampleOffsets(samples, shutter).map((offset, sample) => ({sample, local: frame + offset, t: ((frame + offset) * fps.den) / fps.num}));
}

function intArg(value, name, {min = -Infinity, max = Infinity} = {}) {
  const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  if (!Number.isInteger(n) || n < min || n > max) {
    const range = Number.isFinite(min) && Number.isFinite(max) ? ` from ${min} to ${max}` : Number.isFinite(min) ? ` of at least ${min}` : '';
    throw usage(`${name} must be a whole number${range} (got ${JSON.stringify(value)}).`);
  }
  return n;
}

function resolveFrom(base, p) {
  const expanded = String(p).replace(/^~(?=$|\/)/, os.homedir());
  return path.isAbsolute(expanded) ? expanded : path.resolve(base, expanded);
}

/** The scene's page: a folder holding index.html, or an .html file. */
function resolveScene(target, whatFor) {
  let dir;
  let entry;
  const stat = fs.statSync(target, {throwIfNoEntry: false});
  if (stat?.isDirectory()) {
    dir = target;
    entry = path.join(target, 'index.html');
  } else if (/\.html?$/i.test(target)) {
    dir = path.dirname(target);
    entry = target;
  } else {
    dir = target;
    entry = path.join(target, 'index.html');
  }
  if (!fs.existsSync(entry)) {
    throw missing(
      `${whatFor} has no web scene: there's no ${entry}.`,
      `Start one from the template: node engine/cli.mjs web-render --json '{"film": "<film>", "act": <N>, "scaffold": true}' (or copy ${TEMPLATE_DIR} into that folder).`,
    );
  }
  const rel = path.relative(dir, entry).split(path.sep).map(encodeURIComponent).join('/');
  return {dir: fs.realpathSync(dir), entry: fs.realpathSync(entry), urlPath: `/${rel}`};
}

/** A scratch film for a scene rendered without one: one act, one shot, the args' rate and size (or the neutral film's). */
function scratchFilm(args) {
  const neutral = readJson(NEUTRAL_FILM_JSON);
  const fpsNum = intArg(args.fps_num ?? neutral.fps_num, 'fps_num', {min: 1});
  const fpsDen = intArg(args.fps_den ?? neutral.fps_den, 'fps_den', {min: 1});
  const width = intArg(args.width ?? neutral.width, 'width', {min: 2, max: 16384});
  const height = intArg(args.height ?? neutral.height, 'height', {min: 2, max: 16384});
  let frames = args.frames ?? (args.to !== undefined && args.to !== null ? Number(args.to) + 1 : null);
  if (frames === null) {
    throw usage('Say how many frames to render ("frames": N), or give a film ("film": its folder).', 'A scene rendered without a film gets a scratch film of that many frames.');
  }
  frames = intArg(frames, 'frames', {min: 1});
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-web-film-'));
  writeJsonAtomic(path.join(dir, 'film.json'), {
    id: 'web-scene',
    title: 'A web scene rendered on its own',
    format: 'review',
    language: 'en',
    fps_num: fpsNum,
    fps_den: fpsDen,
    width,
    height,
    brand: null,
    mode: 'draft',
  });
  writeJsonAtomic(path.join(dir, 'timing', 'shots.json'), {
    generated: 'do not edit by hand: a scratch film made by the open web renderer',
    fps_num: fpsNum,
    fps_den: fpsDen,
    width,
    height,
    total_frames: frames,
    acts: [{index: 1, name: 'Scene', shots: ['W01']}],
    shots: [{id: 'W01', act: 1, frame_in: 0, frame_out: frames, frames, sec_in: 0, sec_out: (frames * fpsDen) / fpsNum, vo: '', on_screen: ''}],
  });
  return dir;
}

function readBrand(brandPath) {
  if (!brandPath) return {brand: null, brandDir: null};
  const stat = fs.statSync(brandPath, {throwIfNoEntry: false});
  const file = stat?.isDirectory() ? path.join(brandPath, 'brand.json') : brandPath;
  if (!fs.existsSync(file)) throw usage(`No brand pack at ${brandPath}.`, 'slate brand validate <name> checks a pack.');
  return {brandJson: readJson(file), brandDir: path.dirname(path.resolve(file))};
}

/** Every arg web-render reads. Anything else is a typo, refused rather than quietly ignored. */
export const KNOWN_ARGS = new Set([
  'film', 'act', 'scene', 'from', 'to', 'sub', 'samples', 'draft', 'shutter', 'out', 'codec', 'gl', 'concurrency',
  'on_blocked', 'on_fonts', 'trace', 'emit', 'scaffold', 'force', 'hashes', 'frames_dir', 'brand_path', 'frames',
  'fps_num', 'fps_den', 'width', 'height', 'seed', 'epoch', 'browser', 'load_timeout_ms', 'seek_timeout_ms',
  // passed by callers that also drive Remotion; the web renderer serves its own scene, so it's ignored
  'serve_url',
]);

/** Refuse args web-render doesn't read, naming the nearest one it does. */
export function checkArgs(args) {
  const unknown = Object.keys(args).filter((k) => !KNOWN_ARGS.has(k));
  if (!unknown.length) return;
  const near = (k) => [...KNOWN_ARGS].find((known) => known.startsWith(k.slice(0, 3)) || k.startsWith(known.slice(0, 3)));
  const said = unknown.map((k) => (near(k) ? `"${k}" (did you mean "${near(k)}"?)` : `"${k}"`)).join(', ');
  throw usage(`web-render doesn't read ${said}.`, `It reads: ${[...KNOWN_ARGS].filter((k) => k !== 'serve_url').join(', ')}.`);
}

/**
 * Resolve a web-render call into a job. Throws usage errors (exit 2) for bad args and missing-prerequisite errors
 * (exit 3) for a scene that isn't there. `cwd` is where relative paths without a film are resolved from.
 */
export async function resolveJob(args = {}, {cwd = process.cwd()} = {}) {
  const standalone = !args.film;
  if (standalone && args.emit) {
    throw usage("Emitting events and the motion trace needs a film (\"film\": its folder): they're written into its timing/.");
  }
  const filmDir = standalone ? scratchFilm(args) : resolveFrom(cwd, args.film);
  try {
    return resolveWithFilm(args, filmDir, standalone, cwd);
  } catch (err) {
    if (standalone) fs.rmSync(filmDir, {recursive: true, force: true});
    throw err;
  }
}

function resolveWithFilm(args, filmDir, standalone, cwd) {
  const emit = Boolean(args.emit);
  const ctx = loadFilm(filmDir);
  const base = standalone ? cwd : ctx.dir;

  let actArg = args.act;
  if (actArg === undefined || actArg === null || actArg === '') {
    if (ctx.acts.length === 1) actArg = ctx.acts[0].act;
    else throw usage(`Say which act to render ("act": N). ${ctx.id} has acts ${ctx.acts.map((a) => a.act).join(', ')}.`);
  }
  const m = /^Act(\d+)$/.exec(String(actArg));
  const act = actOf(ctx, m ? Number(m[1]) : actArg);

  let scene;
  if (args.scene) scene = resolveScene(resolveFrom(base, args.scene), `The scene ${args.scene}`);
  else if (standalone) scene = resolveScene(TEMPLATE_DIR, 'The template');
  else scene = resolveScene(path.join(ctx.dir, 'scenes', `act${act.act}`, 'web'), `Act ${act.act}`);

  const last = act.frames - 1;
  const from = intArg(args.from ?? 0, 'from', {min: 0});
  const to = args.to === undefined || args.to === null ? last : intArg(args.to, 'to', {min: 0});
  if (from > last || to > last || to < from) {
    throw usage(`Frame range ${from}–${to} isn't inside act ${act.act}'s 0–${last}.`, 'from and to are act frames, counted from the act\'s first frame, both included.');
  }

  const rawSamples = args.sub ?? args.samples;
  const samples = rawSamples === undefined || rawSamples === null ? (args.draft ? DRAFT_SAMPLES : DELIVERY_SAMPLES) : intArg(rawSamples, 'samples', {min: 1, max: MAX_SAMPLES});
  const shutter = args.shutter === undefined || args.shutter === null ? DEFAULT_SHUTTER : Number(args.shutter);
  if (!Number.isFinite(shutter) || shutter <= 0 || shutter > 360) throw usage(`shutter is an angle in degrees, above 0 and at most 360 (got ${JSON.stringify(args.shutter)}).`);

  let out = null;
  let kind = null;
  if (!emit) {
    if (args.out) out = resolveFrom(base, args.out);
    else if (standalone) throw usage('Say where to write the render ("out": a .mov, .mkv or .png path).');
    else out = path.join(ctx.dir, 'renders', `act${act.act}.web.mov`);
    kind = outputKind(out, args.codec ?? null);
    if (kind.still && from !== to) throw usage(`A .png is one still: give one frame ("from" and "to" both ${from}, say).`);
  }

  const gl = args.gl ?? DEFAULT_GL;
  glFlags(gl);
  const concurrency = args.concurrency === undefined || args.concurrency === null ? defaultConcurrency() : intArg(args.concurrency, 'concurrency', {min: 1, max: MAX_CONCURRENCY});
  const onBlocked = args.on_blocked ?? 'fail';
  if (!['fail', 'report'].includes(onBlocked)) throw usage(`on_blocked must be "fail" or "report" (got ${JSON.stringify(args.on_blocked)}).`);
  // A film's render stops on a substituted face, as a Remotion render does. A scene rendered on its own is a preview,
  // never delivered: there the failed gate is reported and the render goes on.
  const onFonts = args.on_fonts ?? (standalone ? 'report' : 'fail');
  if (!['fail', 'report'].includes(onFonts)) throw usage(`on_fonts must be "fail" or "report" (got ${JSON.stringify(args.on_fonts)}).`);
  const seed = args.seed === undefined || args.seed === null ? DEFAULT_SEED : intArg(args.seed, 'seed', {min: 0, max: 4294967295});
  const epoch = args.epoch === undefined || args.epoch === null ? DEFAULT_EPOCH_MS : Number(args.epoch);
  if (!Number.isFinite(epoch)) throw usage(`epoch is a time in ms since 1970 (got ${JSON.stringify(args.epoch)}).`);
  const loadTimeoutMs = args.load_timeout_ms ? intArg(args.load_timeout_ms, 'load_timeout_ms', {min: 1000}) : LOAD_TIMEOUT_MS;
  const seekTimeoutMs = args.seek_timeout_ms ? intArg(args.seek_timeout_ms, 'seek_timeout_ms', {min: 100}) : SEEK_TIMEOUT_MS;

  // A film's brand comes from slate's bridge (brand_path); a scene on its own is drawn in the neutral demo pack when
  // the repo has one, else in the kit's plain greys.
  let brandPath = args.brand_path ? resolveFrom(cwd, args.brand_path) : null;
  if (!brandPath && standalone && fs.existsSync(path.join(DEMO_BRAND_DIR, 'brand.json'))) brandPath = DEMO_BRAND_DIR;
  const {brandJson, brandDir} = readBrand(brandPath);

  return {
    mode: emit ? 'emit' : 'render',
    standalone,
    scratchDir: standalone ? ctx.dir : null,
    ctx,
    act,
    scene,
    from,
    to,
    count: to - from + 1,
    samples: emit ? 1 : samples,
    shutter,
    out,
    kind,
    gl,
    concurrency: emit ? 1 : Math.min(concurrency, to - from + 1),
    hashes: Boolean(args.hashes),
    framesDir: args.frames_dir ? resolveFrom(base, args.frames_dir) : null,
    onBlocked,
    onFonts,
    trace: args.trace === undefined ? !standalone : Boolean(args.trace) && !standalone,
    seed,
    epoch,
    loadTimeoutMs,
    seekTimeoutMs,
    brandJson,
    brandDir,
    browser: args.browser ? resolveFrom(cwd, args.browser) : null,
  };
}

/** Remove a job's scratch film, if it made one. */
export function cleanupJob(job) {
  if (job?.scratchDir) fs.rmSync(job.scratchDir, {recursive: true, force: true});
}
