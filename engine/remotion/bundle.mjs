// One bundle per film per source hash, reused for every act and still, deleted after use.
//
// A bundle is keyed on everything that can change the picture's code: the film's scenes, the kit, the timing JSON
// and brand the entry inlines, the generated entry itself and the engine's lockfile. Bundles live in
// SLATE_DATA/cache/bundles/<film>-<hash>/ so they can be found, reused and cleaned; the films' bundles lived in the
// temp folder, and 56 of them (290 MB each) filled 14 GB before anyone noticed.
//
// Ownership: a command that makes its own bundle deletes it when it exits (on success, on a throw, and on SIGTERM
// from a timed-out caller). A caller that wants one bundle for many renders runs `bundle`, passes its serve_url to
// each render and still, then runs `bundle` with {release: true}. Every process using a bundle holds a lease
// (.leases/<pid>), and nothing deletes a bundle while another live process holds one.
import fs from 'node:fs';
import path from 'node:path';
import {bundle as remotionBundle} from '@remotion/bundler';
import {ENGINE_DIR, EngineError, KIT_DIR, NODE_MODULES, engineInfo, listFiles, log, sha256Buffer, sha256FileSync, slateData, usage} from './lib/common.mjs';
import {TIMING_FILES} from './lib/film.mjs';
import {ensureEntry, filmDataFile, kitPresent, slateDir} from './entry.mjs';

/** Bump when the bundle's makeup changes (aliases, public dir handling), so old bundles are never reused. */
export const BUNDLE_FORMAT = 1;
const MARKER = '.slate-bundle.json';
const LEASES = '.leases';

export function bundlesRoot(args = {}) {
  return path.resolve(args.bundle_root ?? path.join(slateData(), 'cache', 'bundles'));
}

const safeId = (id) => String(id).replace(/[^A-Za-z0-9._-]+/g, '-').slice(0, 60) || 'film';

/** The files the engine writes in .slate/ (entry.mjs), the only ones it hashes there. */
const ENGINE_GLUE = /^(entry\.tsx|Root\.tsx|film\.tsx|act\d+\.tsx|film-data\.ts|placeholder\.tsx|kit-placeholder\.tsx|broken\.tsx|rest-states\.tsx|label\.tsx|spoken\.json)$/;

// A file being written next to the sources (an editor's or a builder's temp file) is never a source.
const TEMP_NAME = /(\.tmp(\.|$)|\.swp$|~$)/;

/** A stable label for a source: film files relative to the film, kit files relative to the kit. */
function labelFor(ctx, file) {
  const abs = path.resolve(file);
  if (abs.startsWith(ctx.dir + path.sep)) return `film/${path.relative(ctx.dir, abs)}`;
  if (abs.startsWith(KIT_DIR + path.sep)) return `kit/${path.relative(KIT_DIR, abs)}`;
  return abs;
}

/**
 * The files that go into a bundle: esbuild walks the entry's imports with the renderer's aliases, with packages left
 * out (their versions are the lockfile's). Returns absolute paths, or null when esbuild can't read the sources (the
 * webpack bundle then reports the real error).
 */
export async function bundleInputs(ctx, entryFile) {
  const {build} = await import('esbuild');
  const {esbuildOptions} = await import('./load_act.mjs');
  try {
    const res = await build({...esbuildOptions(ctx), entryPoints: [entryFile], write: false, packages: 'external', outdir: path.join(slateDir(ctx.dir), '.inputs'), banner: {}});
    return Object.keys(res.metafile.inputs)
      .filter((p) => !p.includes(':'))
      .map((p) => path.resolve(ctx.dir, p))
      // packages are pinned by the lockfile, which the hash includes; only our own sources are hashed file by file
      .filter((p) => !p.startsWith(NODE_MODULES + path.sep) && fs.existsSync(p));
  } catch {
    return null;
  }
}

/**
 * The source hash: sha256 over (label, file sha256) for every file that goes into the bundle, plus film.json, the
 * engine's lockfile, the Remotion version and the film's folder. Kit files the film never imports don't change it, so
 * a kit edit elsewhere doesn't throw away a good bundle. When the imports can't be read, every scene and kit file
 * counts.
 */
export async function sourceHash(ctx, {entry}) {
  const parts = new Map();
  const add = (file) => {
    if (TEMP_NAME.test(path.basename(file))) return;
    try {
      parts.set(labelFor(ctx, file), sha256FileSync(file));
    } catch (e) {
      if (e.code !== 'ENOENT') throw e; // renamed away while we looked: it isn't a source any more
    }
  };
  const inputs = await bundleInputs(ctx, entry);
  if (inputs) {
    for (const f of inputs) add(f);
  } else {
    // only the engine's own files in .slate/ (the state module keeps its lock and hash cache there too)
    const sd = slateDir(ctx.dir);
    for (const name of fs.readdirSync(sd).sort()) if (ENGINE_GLUE.test(name)) add(path.join(sd, name));
    for (const rel of listFiles(path.join(ctx.dir, 'scenes'))) add(path.join(ctx.dir, 'scenes', rel));
    for (const rel of Object.values(TIMING_FILES)) if (fs.existsSync(path.join(ctx.dir, rel))) add(path.join(ctx.dir, rel));
    for (const rel of listFiles(KIT_DIR)) add(path.join(KIT_DIR, rel));
  }
  add(entry);
  add(path.join(ctx.dir, 'film.json'));
  const lock = path.join(ENGINE_DIR, 'package-lock.json');
  if (fs.existsSync(lock)) parts.set('engine/package-lock.json', sha256FileSync(lock));
  const lines = [...parts.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}\0${v}`);
  lines.push(`format\0${BUNDLE_FORMAT}`, `remotion\0${engineInfo().installed.remotion}`, `inputs\0${inputs ? 'imports' : 'all'}`);
  // A film is its folder: a bundle's public/ is a link into that folder, so two copies of a film never share one.
  lines.push(`film_dir\0${ctx.dir}`);
  return sha256Buffer(lines.join('\n'));
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';
  }
}

function liveLeases(dir) {
  const ld = path.join(dir, LEASES);
  if (!fs.existsSync(ld)) return [];
  const live = [];
  for (const name of fs.readdirSync(ld)) {
    const pid = Number(name);
    if (Number.isInteger(pid) && pidAlive(pid)) live.push(pid);
    else fs.rmSync(path.join(ld, name), {force: true});
  }
  return live;
}

export function takeLease(dir) {
  fs.mkdirSync(path.join(dir, LEASES), {recursive: true});
  fs.writeFileSync(path.join(dir, LEASES, String(process.pid)), JSON.stringify({pid: process.pid, at: new Date().toISOString()}));
}

export function dropLease(dir) {
  fs.rmSync(path.join(dir, LEASES, String(process.pid)), {force: true});
}

/** Delete a bundle unless another live process holds a lease on it. Returns true when it's gone. */
export function removeBundle(dir) {
  if (!fs.existsSync(dir)) return true;
  const others = liveLeases(dir).filter((pid) => pid !== process.pid);
  if (others.length) {
    log(`keeping ${path.basename(dir)}: in use by ${others.join(', ')}`);
    return false;
  }
  fs.rmSync(dir, {recursive: true, force: true});
  return true;
}

function dirBytes(dir) {
  let total = 0;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, {withFileTypes: true})) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.isFile()) total += fs.statSync(full).size;
    }
  };
  if (fs.existsSync(dir)) walk(dir);
  return total;
}

/** Bundles on disk: [{dir, film, hash, made_at, leases, bytes}]. */
export function listBundles(args = {}) {
  const root = bundlesRoot(args);
  if (!fs.existsSync(root)) return [];
  return fs
    .readdirSync(root)
    .filter((n) => !n.startsWith('.'))
    .map((n) => {
      const dir = path.join(root, n);
      let meta = {};
      try {
        meta = JSON.parse(fs.readFileSync(path.join(dir, MARKER), 'utf8'));
      } catch {
        meta = {incomplete: true};
      }
      return {dir, name: n, film: meta.film ?? null, film_dir: meta.film_dir ?? null, hash: meta.hash ?? null, made_at: meta.made_at ?? null, leases: liveLeases(dir), bytes: dirBytes(dir), incomplete: Boolean(meta.incomplete)};
    });
}

/**
 * Delete this film's other bundles (and any unleased bundle older than maxAgeHours), keeping `keep`. A film is its
 * folder, not its id: two folders can share an id (a copy, a derived cut) and must not prune each other's bundles.
 */
export function pruneBundles(args, {filmDir = null, keep = null, maxAgeHours = 24} = {}) {
  const removed = [];
  const now = Date.now();
  for (const b of listBundles(args)) {
    if (b.dir === keep) continue;
    const old = b.made_at && now - Date.parse(b.made_at) > maxAgeHours * 3600e3;
    const sameFilm = filmDir && b.film_dir === filmDir;
    const stale = b.incomplete && fs.statSync(b.dir).mtimeMs < now - 3600e3;
    if ((sameFilm || old || stale) && !b.leases.length && removeBundle(b.dir)) removed.push(b.name);
  }
  return [...removed, ...removeDeadTemps(args)];
}

/** Half-finished bundles whose process died mid-bundle (a killed render leaves one behind). */
function removeDeadTemps(args) {
  const removed = [];
  const root = bundlesRoot(args);
  if (!fs.existsSync(root)) return removed;
  for (const n of fs.readdirSync(root)) {
    if (!n.startsWith('.tmp-')) continue;
    const pid = Number(n.split('-').at(-1));
    if (!Number.isInteger(pid) || !pidAlive(pid)) {
      fs.rmSync(path.join(root, n), {recursive: true, force: true});
      removed.push(n);
    }
  }
  return removed;
}

function webpackOverrideFor(ctx) {
  const aliases = {'@film/data': filmDataFile(ctx.dir)};
  if (kitPresent()) aliases['@slate/kit'] = KIT_DIR;
  return (config) => ({
    ...config,
    resolve: {
      ...config.resolve,
      alias: {...(config.resolve?.alias ?? {}), ...aliases},
      // The film folder has no node_modules: its scenes' packages come from the engine's.
      modules: [...(config.resolve?.modules ?? ['node_modules']), NODE_MODULES],
    },
  });
}

/**
 * Folders a scene reads as image sequences with staticFile(): conformed captures (captures/<name>/frames) and plates
 * (plates/<shot>/plate/frames, plates/<shot>/matte/frames), each f%05d.jpg at the film's rate.
 */
export const MEDIA_DIRS = ['captures', 'plates'];

/**
 * The folder served as the bundle's public/: .slate/public, holding links (never copies) to everything in the film's
 * own public/ and to its captures/ and plates/, so `staticFile('captures/demo/frames/f00012.jpg')` reads the film's
 * files in place. A name the film's public/ already uses wins. Returns {dir, links, warnings}.
 *
 * Links are safe to delete: removing a bundle or this folder removes the links, never what they point at.
 */
export function publicDir(ctx) {
  const dir = path.join(slateDir(ctx.dir), 'public');
  fs.mkdirSync(dir, {recursive: true});
  const want = new Map();
  const warnings = [];
  const own = path.join(ctx.dir, 'public');
  if (fs.existsSync(own) && fs.statSync(own).isDirectory()) {
    for (const name of fs.readdirSync(own)) if (name !== '.DS_Store') want.set(name, path.join('..', '..', 'public', name));
  }
  for (const name of MEDIA_DIRS) {
    if (!fs.existsSync(path.join(ctx.dir, name))) continue;
    if (want.has(name)) warnings.push(`public/${name} shadows the film's ${name}/ folder in staticFile() paths`);
    else want.set(name, path.join('..', '..', name));
  }
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = fs.lstatSync(full);
    // only the links this function made are ever removed here
    if (st.isSymbolicLink() && (!want.has(name) || fs.readlinkSync(full) !== want.get(name))) fs.unlinkSync(full);
  }
  for (const [name, target] of want) {
    const full = path.join(dir, name);
    if (fs.lstatSync(full, {throwIfNoEntry: false})) continue;
    fs.symlinkSync(target, full);
  }
  return {dir, links: [...want.keys()].sort(), warnings};
}

function explainBundleError(e) {
  const msg = String(e?.message ?? e);
  if (/@slate\/kit/.test(msg) && !kitPresent()) {
    return new EngineError(`A scene imports '@slate/kit', but this engine has no kit yet (${KIT_DIR}).`, {
      kind: 'missing_prerequisite',
      hint: 'Install the engine again with `slate engine install`, or build the scene without the kit for now.',
    });
  }
  return new EngineError(`The bundle failed: ${msg.split('\n').slice(0, 12).join('\n')}`, {
    kind: 'refused',
    hint: 'Run `slate check <act>` for a type check that points at the file and line.',
  });
}

/**
 * The bundle for a film's current sources: reused when one with the same hash exists, made otherwise.
 * Takes a lease for this process. Returns {serve_url, hash, reused, dir}.
 */
export async function getBundle(ctx, args = {}) {
  // Always refresh the film's entry: a custom entry (a kit review sheet) still reads the film through '@film/data'.
  const entryInfo = await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
  const entryFile = args.entry ? path.resolve(args.entry) : entryInfo.entry;
  if (!fs.existsSync(entryFile)) throw usage(`No entry file at ${entryFile}.`);
  const hash = await sourceHash(ctx, {entry: entryFile});
  const root = bundlesRoot(args);
  fs.mkdirSync(root, {recursive: true});
  const name = `${safeId(ctx.id)}-${args.entry ? 'entry-' : ''}${hash.slice(0, 16)}`;
  const dir = path.join(root, name);
  if (fs.existsSync(path.join(dir, MARKER))) {
    takeLease(dir);
    log(`reusing bundle ${name}`);
    return {serve_url: dir, dir, hash, reused: true, entry: entryInfo};
  }
  const tmp = path.join(root, `.tmp-${name}-${process.pid}`);
  fs.rmSync(tmp, {recursive: true, force: true});
  // A process stopped mid-bundle (a signal, the caller's deadline) takes its half-made bundle with it.
  const dropTmp = () => fs.rmSync(tmp, {recursive: true, force: true});
  process.once('exit', dropTmp);
  const t0 = Date.now();
  let lastPct = -1;
  log(`bundling ${ctx.id} (${hash.slice(0, 12)})`);
  const pub = publicDir(ctx);
  for (const w of pub.warnings) log(`note: ${w}`);
  try {
    await remotionBundle({
      entryPoint: entryFile,
      outDir: tmp,
      enableCaching: false,
      rootDir: ctx.dir,
      publicDir: pub.dir,
      symlinkPublicDir: true,
      // the public folder holds links to the film's own media on purpose
      onSymlinkDetected: () => {},
      webpackOverride: webpackOverrideFor(ctx),
      onProgress: (p) => {
        const step = Math.floor(p / 25) * 25;
        if (step > lastPct) {
          lastPct = step;
          log(`  bundle ${step}%`);
        }
      },
    });
  } catch (e) {
    dropTmp();
    process.removeListener('exit', dropTmp);
    throw explainBundleError(e);
  }
  process.removeListener('exit', dropTmp);
  fs.writeFileSync(path.join(tmp, MARKER), JSON.stringify({film: ctx.id, film_dir: ctx.dir, hash, entry: entryFile, made_at: new Date().toISOString(), format: BUNDLE_FORMAT}, null, 1) + '\n');
  try {
    fs.renameSync(tmp, dir);
  } catch (e) {
    // another process finished the same bundle first: use theirs
    fs.rmSync(tmp, {recursive: true, force: true});
    if (!fs.existsSync(path.join(dir, MARKER))) throw e;
  }
  takeLease(dir);
  pruneBundles(args, {filmDir: ctx.dir, keep: dir});
  log(`bundled in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  return {serve_url: dir, dir, hash, reused: false, entry: entryInfo};
}

/**
 * A bundle a command can use and give back: the caller's serve_url when passed (the caller owns it), otherwise one
 * this process gets and deletes when it's done, whatever way it exits.
 */
export async function useBundle(ctx, args = {}) {
  if (args.serve_url) {
    const dir = path.resolve(args.serve_url);
    if (!fs.existsSync(path.join(dir, MARKER))) throw usage(`No Slate bundle at ${dir}.`, 'Run the bundle command again and pass its serve_url.');
    takeLease(dir);
    return {serve_url: dir, dir, owned: false, reused: true, hash: JSON.parse(fs.readFileSync(path.join(dir, MARKER), 'utf8')).hash, release: () => dropLease(dir)};
  }
  const b = await getBundle(ctx, args);
  // Only a bundle this process made is this process's to delete. One it found already on disk was kept by someone
  // (a `bundle` call whose caller passes serve_url along); pruning removes it once it's stale.
  const owned = !b.reused && !args.keep_bundle;
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    dropLease(b.dir);
    if (owned) removeBundle(b.dir);
  };
  process.once('exit', release);
  return {...b, owned, release};
}

/** The `bundle` command: make or reuse the film's bundle (kept for the caller), or release/clean up bundles. */
export async function bundleCommand(ctx, args) {
  if (args.release) {
    const removed = [];
    for (const b of listBundles(args)) {
      if (b.film_dir === ctx.dir || (args.serve_url && path.resolve(args.serve_url) === b.dir)) {
        dropLease(b.dir);
        if (removeBundle(b.dir)) removed.push(b.name);
      }
    }
    return {released: removed};
  }
  const b = await getBundle(ctx, args);
  // The caller owns this bundle now: this process's lease ends when it exits, and the bundle stays for the renders.
  dropLease(b.dir);
  // a custom entry (a review sheet) has its own compositions; only the film's entry lists them here
  return {
    serve_url: b.serve_url,
    bundle_hash: b.hash,
    reused: b.reused,
    compositions: args.entry ? null : (b.entry?.compositions ?? null),
    // acts left out because their modules don't build: every other act renders from this bundle
    broken: b.entry?.broken ?? [],
  };
}

/** `bundle` with {cleanup: true} and no film: remove every unleased bundle (disk janitor, doctor). */
export function cleanupAll(args) {
  const removed = removeDeadTemps(args);
  for (const b of listBundles(args)) if (!b.leases.length && removeBundle(b.dir)) removed.push(b.name);
  return {removed, remaining: listBundles(args).map((b) => b.name)};
}
