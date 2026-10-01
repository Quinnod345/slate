#!/usr/bin/env node
// The open web renderer: Slate's third engine, and the one that needs no Remotion licence (docs/PLAN.md §2 and
// decision 5). A scene is a local page that can draw its act at any time t; this file captures it frame by frame.
//
//   node engine/cli.mjs web-render --json '<args>'        (how slate calls it: cli.mjs imports webRender)
//   node engine/web/render.mjs --json '<args>'            (the same, on its own; tests use both)
//
// One render, start to finish:
//   1. the call is resolved against the film clock (lib/job.mjs): film.json's rate and size, the act's frames from
//      timing/shots.json, the scene page (<film>/scenes/act<N>/web/index.html, or the template for a scene on its own);
//   2. a static server bound to 127.0.0.1 on a port the OS picks serves the scene folder, the film folder (read-only)
//      and the brand folder, compiling scene scripts on request (lib/server.mjs, lib/bundle.mjs);
//   3. one headless Chrome starts: Remotion's chrome-headless-shell, driven by puppeteer (lib/browser.mjs). Every request
//      that isn't to the local server is refused and written down, and a proxy wall catches whatever request
//      interception can't see. puppeteer only ever loads our own local page;
//   4. before the page's own scripts run, its clock is replaced (lib/page-clock.mjs): Date, performance.now and
//      requestAnimationFrame follow the frame time, Math.random is seeded, CSS animations are paused and set;
//   5. the page registers window.slateScene = {duration, fps, seek(t), ready()} (engine/web/runtime); the renderer
//      awaits ready() once, checks the scene's length and rate against the film clock and its fonts against the brand;
//   6. for each frame, each motion-blur sample: the clock is set, seek(t) is awaited (t = (frame + offset) ×
//      fps_den ÷ fps_num, act seconds), then fonts, images and a painted frame, then a PNG screenshot;
//   7. the PNGs stream into ffmpeg in frame order, which averages each frame's samples in 16-bit and encodes ProRes 422
//      HQ 10-bit BT.709 (a master) or lossless x264 in the same 10-bit 4:2:2 (a chunk) (lib/encode.mjs);
//   8. the output is written as <out>.part, its frames are counted, and only an exact count replaces <out>;
//   9. the act's motion trace (tweens made with the runtime's tween()) goes to timing/motion/act<N>.json.
// Frames go to pages in turn and every page only moves forward in time, so any number of pages draw the same pictures.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {EXIT, EngineError, KIT_DIR, NODE_MODULES, log, missing, refused, relTo, requireTool, sha256Buffer, sha256File, slateData, usage, useEngineTmp, writeJsonAtomic} from '../remotion/lib/common.mjs';
import {actOf, loadFilm, realFile} from '../remotion/lib/film.mjs';
import {filmDataSource, inlineBrand} from '../remotion/entry.mjs';
import {GENERATED, eventsFile, motionFile, validateEvents} from '../remotion/events.mjs';
import {fontGate, kitCore} from '../remotion/kit_bridge.mjs';
import {findBrowser, guardTargets, launchBrowser, preparePage, removeProfile, startWall} from './lib/browser.mjs';
import {createBundler} from './lib/bundle.mjs';
import {countFrames, ffmpegArgs, frameHashes, probe, startEncoder, stillArgs} from './lib/encode.mjs';
import {RUNTIME_DIR, TEMPLATE_DIR, WEB_ENGINE_VERSION, checkArgs, cleanupJob, resolveJob, sampleTimes} from './lib/job.mjs';
import {startServer} from './lib/server.mjs';

export {WEB_ENGINE_VERSION};
/** The scene protocol this renderer speaks (runtime/scene.ts SCENE_PROTOCOL). */
export const SCENE_PROTOCOL = 1;

const PROGRESS_EVERY_MS = 15000;
const POLL_MS = 50;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function withTimeout(promise, ms, what) {
  let timer;
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(refused(`${what} took longer than ${Math.round(ms / 1000)} s.`, 'A scene must answer quickly: load heavy assets in ready(), never in seek().')), ms);
  });
  return Promise.race([promise, guard]).finally(() => clearTimeout(timer));
}

// ------------------------------------------------------------------------------------------------ the session
const FONT_EXTS = ['.woff2', '.woff', '.ttf', '.otf'];
// Longest names first, so "SemiBold" isn't read as "Bold" nor "ExtraLight" as "Light".
const WEIGHT_WORDS = [
  ['extralight', 200], ['ultralight', 200], ['semibold', 600], ['demibold', 600], ['extrabold', 800], ['ultrabold', 800],
  ['hairline', 100], ['thin', 100], ['light', 300], ['regular', 400], ['normal', 400], ['book', 400], ['medium', 500],
  ['bold', 700], ['black', 900], ['heavy', 900],
];

/** Where Slate keeps local copies of font families a pack serves from off this Mac: <SLATE_DATA>/fonts/<Family>/. */
export const fontsDir = () => path.join(slateData(), 'fonts');

/** The folders a family's local files may sit in: the brand's own fonts/<Family>/, then Slate's. */
export function fontFolders(family, brandDir = null) {
  const names = [...new Set([family, family.replace(/\s+/g, '')])];
  const roots = [brandDir ? path.join(brandDir, 'fonts') : null, fontsDir()].filter(Boolean);
  return roots.flatMap((r) => names.map((n) => path.join(r, n)));
}

/**
 * A font file's weight and style, read from its name the way type foundries name them: "PublicSans-SemiBoldItalic"
 * is 600 italic, "Face-Regular" 400. A variable font ("…-VariableFont_wght", "…[wght]") covers every weight.
 */
export function faceOfFile(name) {
  const stem = path.basename(name, path.extname(name));
  const lower = stem.toLowerCase();
  const italic = /italic|oblique/.test(lower);
  if (/variable|\[wght/.test(lower)) return {weight: '1 1000', style: italic ? 'italic' : 'normal'};
  const part = (stem.includes('-') ? stem.slice(stem.lastIndexOf('-') + 1) : '').toLowerCase().replace(/[^a-z]/g, '');
  const hit = WEIGHT_WORDS.find(([word]) => part.includes(word));
  return {weight: String(hit ? hit[1] : 400), style: italic ? 'italic' : 'normal'};
}

/**
 * Every font source the brand pack names, made local for the page: file and stylesheet paths mapped to the URLs the
 * local server serves them at (fontUrls), and families the pack serves from Google Fonts or a URL off this Mac mapped
 * to local copies of their files, when someone has put them in a fonts folder (fontFiles). The page can't fetch
 * anything else.
 */
async function fontSources(job, server) {
  const out = {fontUrls: {}, fontFiles: {}};
  if (!job.brandJson) return out;
  const core = await kitCore();
  if (!core?.fontPlan) return out;
  for (const face of core.fontPlan(job.brandJson).faces ?? []) {
    const src = face.source ?? {};
    if (src.kind === 'installed') continue;
    const url = typeof src.url === 'string' ? src.url : null;
    if ((src.kind === 'file' || src.kind === 'url') && url && !/^(https?:)?\/\//i.test(url)) {
      let abs = null;
      if (url.startsWith('file:')) abs = fileURLToPath(url);
      else if (path.isAbsolute(url)) abs = url;
      else if (job.brandDir) abs = path.resolve(job.brandDir, url);
      const local = abs && fs.existsSync(abs) ? server.urlFor(abs) : null;
      if (local) {
        out.fontUrls[url] = local;
        continue;
      }
      // named but not found: a copy in a fonts folder still counts, and the page says the file isn't here
    }
    if (out.fontFiles[face.family]) continue;
    for (const dir of fontFolders(face.family, job.brandDir)) {
      if (!fs.statSync(dir, {throwIfNoEntry: false})?.isDirectory()) continue;
      const files = fs.readdirSync(dir).filter((n) => FONT_EXTS.includes(path.extname(n).toLowerCase())).sort();
      const mapped = files.map((n) => ({...faceOfFile(n), url: server.urlFor(path.join(dir, n))})).filter((f) => f.url);
      if (mapped.length) {
        out.fontFiles[face.family] = mapped;
        break;
      }
    }
  }
  return out;
}

function globalsFor(job, fonts) {
  const {ctx, act} = job;
  return {
    engine: 'web',
    version: WEB_ENGINE_VERSION,
    protocol: SCENE_PROTOCOL,
    mode: job.mode,
    standalone: job.standalone,
    film: {id: ctx.id, fps_num: ctx.fps.num, fps_den: ctx.fps.den, width: ctx.width, height: ctx.height},
    act: {act: act.act, name: act.name, frameIn: act.frameIn, frameOut: act.frameOut, frames: act.frames, shots: act.shots},
    frames: [job.from, job.to],
    samples: job.samples,
    shutter: job.shutter,
    fontUrls: fonts.fontUrls,
    fontFiles: fonts.fontFiles,
    fontDir: fontsDir(),
  };
}

/**
 * The film's '@film/data' module for the page: engine-core's own source (filmDataSource), so a web act reads exactly
 * the numbers a Remotion act reads. It is compiled as if it sat at the film's root. There, engine-core's import paths
 * for files in dot-folders (.slate/spoken.json, which slate's bridge writes) come out without their "./" and would be
 * read as package names, so they get it back here.
 */
export function filmDataFor(ctx, brandJson = null, brandDir = null) {
  const brand = brandJson ? inlineBrand(brandJson, brandDir) : null;
  // written as if it sat at the film's root, so its imports ('./timing/shots.json', './.slate/spoken.json') resolve there
  const contents = filmDataSource(ctx, path.join(ctx.dir, 'slate-web-film-data.ts'), {brand, brandDir});
  return {contents, resolveDir: ctx.dir};
}

/** The film's media folders a scene reads as image sequences (engine-core's MEDIA_DIRS: captures and plates). */
const MEDIA_DIRS = ['captures', 'plates'];

/**
 * Extra mounts for a derived film: its reused captures and plates live in its parent (.slate/refs.json, written by
 * slate's bridge from slate.state.lock.resolve), so /_slate/film/captures/… serves the parent's folder, exactly as the
 * Remotion bundle links it. A film with its own copies gets none.
 */
export function mediaMounts(ctx) {
  const out = {};
  for (const name of MEDIA_DIRS) {
    const own = path.join(ctx.dir, name);
    const real = realFile(ctx.dir, name);
    if (real !== own && fs.existsSync(real)) out[`/_slate/film/${name}/`] = real;
  }
  return out;
}

/**
 * Everything a render needs running: the server, the wall, the browser and its pages. close() stops all of it (and
 * runs on process exit too, so a stopped render never leaves a browser behind, or its profile folder).
 */
async function openSession(job) {
  const browserInfo = await findBrowser(job.browser);
  const filmData = filmDataFor(job.ctx, job.brandJson, job.brandDir);
  let server = null;
  const bundler = createBundler({
    runtimeDir: RUNTIME_DIR,
    kitDir: KIT_DIR,
    nodeModules: NODE_MODULES,
    filmData: () => filmData,
    urlFor: (p) => (server ? server.urlFor(p) : null),
  });
  const session = {browserInfo, server: null, wall: null, browser: null, pages: [], strays: [], closed: false};
  const kill = () => {
    try {
      session.browser?.process()?.kill('SIGKILL');
    } catch {
      // already gone
    }
    removeProfile(session.browser?.slateProfileDir);
  };
  process.once('exit', kill);
  session.close = async () => {
    if (session.closed) return;
    session.closed = true;
    process.removeListener('exit', kill);
    if (session.browser) {
      await withTimeout(session.browser.close(), 15000, 'closing the browser').catch(() => kill());
      removeProfile(session.browser.slateProfileDir);
    }
    await session.server?.close();
    await session.wall?.close();
  };
  try {
    server = await startServer({mounts: {'/': job.scene.dir, '/_slate/film/': job.ctx.dir, ...mediaMounts(job.ctx), '/_slate/brand/': job.brandDir}, bundler});
    session.server = server;
    session.wall = await startWall();
    try {
      session.browser = await launchBrowser({
        executablePath: browserInfo.path,
        gl: job.gl,
        width: job.ctx.width,
        height: job.ctx.height,
        wallPort: session.wall.port,
        timeoutMs: job.loadTimeoutMs,
      });
    } catch (err) {
      throw missing(`The headless browser at ${browserInfo.path} didn't start: ${String(err?.message ?? err).split('\n')[0]}`, 'Run `node engine/cli.mjs browser-ensure` (or `slate doctor`) to check it.');
    }
    session.pid = session.browser.process()?.pid ?? null;
    guardTargets(session.browser, (url) => session.strays.push({url: String(url).slice(0, 500)}));
    session.globals = globalsFor(job, await fontSources(job, server));
    return session;
  } catch (err) {
    await session.close();
    throw err;
  }
}

/** Every network attempt so far: requests the pages were refused, and connections the wall turned away. */
function networkAttempts(session) {
  const blocked = session.pages.flatMap((p) => p.blocked);
  return {blocked, wall: [...(session.wall?.refused ?? [])], strays: [...session.strays]};
}

function networkGate(session, attempts) {
  const n = attempts.blocked.length + attempts.wall.length + attempts.strays.length;
  const first = attempts.blocked[0]?.url ?? attempts.wall[0]?.target ?? attempts.strays[0]?.url ?? null;
  return {
    gate: 'web.network',
    status: n ? 'FAIL' : 'PASS',
    summary: n
      ? `the scene tried to reach the network ${n} time${n > 1 ? 's' : ''} (first: ${first}); every attempt was refused`
      : 'the scene loaded only its own local files; nothing reached for the network',
    evidence: [...attempts.blocked.map((b) => `refused ${b.type} ${b.url}`), ...attempts.wall.map((w) => `wall refused ${w.method} ${w.target}`), ...attempts.strays.map((s) => `closed a new window for ${s.url}`)].slice(0, 50),
    engine: {name: 'slate web renderer (request interception + proxy wall)', version: WEB_ENGINE_VERSION},
    inputs: {},
    checked_at: new Date().toISOString(),
    details: {attempts: n, origin: session.server?.origin ?? null},
  };
}

function failIfBlocked(job, session, where) {
  if (job.onBlocked !== 'fail') return;
  const attempts = networkAttempts(session);
  const n = attempts.blocked.length + attempts.wall.length + attempts.strays.length;
  if (!n) return;
  const first = attempts.blocked[0]?.url ?? attempts.wall[0]?.target ?? attempts.strays[0]?.url;
  throw refused(
    `The scene tried to reach the network ${where} (${first}${n > 1 ? `, and ${n - 1} more` : ''}), and the web renderer never lets a page do that.`,
    'Copy what it needs into the scene folder (or the film folder, served at /_slate/film/). To render anyway and list the attempts, pass "on_blocked": "report".',
    {network: attempts, gates: [networkGate(session, attempts)]},
  );
}

function failIfPageErrors(session, where) {
  const errors = session.pages.flatMap((p) => p.errors);
  if (!errors.length) return;
  const e = errors[0];
  throw refused(
    `The scene threw ${where}: ${e.message.split('\n')[0]}${errors.length > 1 ? ` (and ${errors.length - 1} more)` : ''}.`,
    'Open the scene\'s script at the line in the stack below. A scene must draw every frame without throwing.',
    {page_errors: errors.slice(0, 10), bundle_problems: session.server?.problems ?? []},
  );
}

/** Wait for window.slateScene, run ready() once, and read what the scene declares. */
async function loadScene(job, session, index) {
  const where = {current: null};
  const p = await preparePage(session.browser, {
    origin: session.server.origin,
    clock: {epoch: job.epoch, seed: job.seed},
    globals: session.globals,
    width: job.ctx.width,
    height: job.ctx.height,
    frameRef: () => where.current,
  });
  p.index = index;
  p.where = where;
  session.pages.push(p);
  const url = `${session.server.origin}${job.scene.urlPath}`;
  try {
    await p.page.goto(url, {waitUntil: 'load', timeout: job.loadTimeoutMs});
  } catch (err) {
    throw refused(`The scene page didn't load (${String(err?.message ?? err).split('\n')[0]}).`, `It is ${job.scene.entry}.`, {bundle_problems: session.server.problems});
  }
  const deadline = Date.now() + job.loadTimeoutMs;
  for (;;) {
    const state = await p.page.evaluate(() => {
      const s = window.slateScene;
      if (!s) return null;
      return {seek: typeof s.seek === 'function'};
    });
    if (state?.seek) break;
    if (state && !state.seek) throw refused("window.slateScene has no seek(t) function.", 'defineScene() from @slate/web builds a complete one.');
    if (p.errors.length || session.server.problems.length) {
      const e = p.errors[0]?.message ?? session.server.problems[0]?.message ?? 'unknown';
      throw refused(`The scene failed before it registered window.slateScene: ${e.split('\n')[0]}`, 'Fix the script, then render again.', {page_errors: p.errors.slice(0, 10), bundle_problems: session.server.problems});
    }
    if (Date.now() > deadline) {
      throw refused(
        `The scene never registered window.slateScene (waited ${Math.round(job.loadTimeoutMs / 1000)} s).`,
        "A web scene ends with defineScene() from '@slate/web', or sets window.slateScene = {duration, fps, seek(t), ready()} itself.",
      );
    }
    await sleep(POLL_MS);
  }
  const readyCall = withTimeout(
    p.page.evaluate(async () => {
      const s = window.slateScene;
      if (typeof s.ready === 'function') await s.ready();
      window.__slateClock.markStarted();
      const fps = s.fps;
      let report = null;
      try {
        report = typeof s.fonts === 'function' ? s.fonts() : null;
      } catch {
        report = null;
      }
      return {
        duration: typeof s.duration === 'number' ? s.duration : null,
        fps: fps === undefined || fps === null ? null : typeof fps === 'number' ? {value: fps} : {num: Number(fps.num), den: Number(fps.den)},
        protocol: s.protocol ?? null,
        runtime: s.runtime ?? null,
        fonts: report ?? window.__SLATE_FONT_GATE__ ?? null,
        problems: typeof s.problems === 'function' ? s.problems() : [],
        has_trace: typeof s.trace === 'function',
        videos: document.querySelectorAll('video').length,
      };
    }),
    job.loadTimeoutMs,
    "The scene's ready()",
  );
  // A scene that throws while it gets ready may never finish getting ready: stop at the first error, not the timeout.
  let watch = null;
  const thrown = new Promise((_, reject) => {
    watch = setInterval(() => {
      if (!p.errors.length) return;
      clearInterval(watch);
      reject(refused(`The scene threw while it got ready: ${p.errors[0].message.split('\n')[0]}`, 'Fix the script, then render again. A scene must get ready without throwing.'));
    }, POLL_MS);
  });
  let info;
  try {
    info = await Promise.race([readyCall, thrown]);
  } catch (err) {
    // what the page said while it hung is usually the answer
    const refusal = err instanceof EngineError ? err : refused(`The scene's ready() failed: ${String(err?.message ?? err).split('\n')[0]}`, 'Fix the script, then render again.');
    refusal.details = {...(refusal.details ?? {}), page_errors: p.errors.slice(0, 10), console: p.consoleLines.slice(0, 20)};
    throw refusal;
  } finally {
    clearInterval(watch);
  }
  p.info = info;
  return p;
}

/**
 * The checks that hold before a single frame is drawn: rate, length, fonts, the network, errors. Emitting beats
 * doesn't draw, so there a failed font gate is reported rather than refused.
 */
async function checkScene(job, session, p) {
  const {ctx, act} = job;
  const info = p.info;
  const fps = ctx.fps;
  if (info.protocol !== null && info.protocol > SCENE_PROTOCOL) {
    throw refused(`The scene speaks scene protocol ${info.protocol}; this renderer speaks ${SCENE_PROTOCOL}.`, 'Update Slate (`slate update`).');
  }
  if (info.fps) {
    const same = info.fps.value !== undefined ? Math.abs(info.fps.value - fps.value) < 1e-9 : info.fps.num * fps.den === info.fps.den * fps.num;
    if (!same) {
      const said = info.fps.value ?? `${info.fps.num}/${info.fps.den}`;
      throw refused(`The scene runs at ${said} fps, but ${ctx.id} runs at ${fps.rational}.`, "Leave fps to defineScene() (it reads the film's rate), or set it from film.json.");
    }
  }
  const actSeconds = (act.frames * fps.den) / fps.num;
  if (info.duration !== null && Math.abs(info.duration - actSeconds) > 0.5 / fps.value) {
    throw refused(
      `The scene says it lasts ${info.duration.toFixed(3)} s, but act ${act.act} is ${actSeconds.toFixed(3)} s (${act.frames} frames) in timing/shots.json.`,
      'Leave duration to defineScene() (it reads the act from the film clock), or run `slate timing` if the act changed.',
    );
  }
  failIfPageErrors(session, 'while it loaded');
  failIfBlocked(job, session, 'while it loaded');
  const gates = [];
  const warnings = [];
  if (info.fonts) {
    const g = await fontGate([`SLATE_FONT_GATE ${JSON.stringify(info.fonts)}`]);
    if (g) {
      gates.push(g);
      if (g.status !== 'PASS' && job.mode === 'render' && job.onFonts === 'fail') {
        throw new EngineError(`Act ${act.act} stopped: the font gate failed (${g.summary}).`, {
          kind: 'refused',
          hint: `The brand's fonts must render as declared, never substituted: install the missing face on this Mac, or put its font files in ${fontsDir()}/<Family>/.`,
          details: {gates: [g]},
        });
      }
      if (g.status !== 'PASS') warnings.push(`the font gate failed (${g.summary}); rendered anyway because on_fonts is "report", so the type may be a substitute`);
    }
  }
  return {gates, warnings};
}

/** A refusal carries the browser's process id, so a caller can check nothing was left running. */
function withBrowserPid(err, session) {
  if (err instanceof EngineError && session?.pid) err.details = {...(err.details ?? {}), browser_pid: session.pid};
  return err;
}

// ---------------------------------------------------------------------------------------------------- drawing
/** Draw one sub-frame: clock, seek, CSS animations, queued animation frames, then a painted frame; then a PNG. */
async function drawSample(job, p, frame, s) {
  const info = {t: s.t, frame, sample: s.sample, samples: job.samples, local: s.local, filmFrame: job.act.frameIn + s.local};
  p.where.current = frame;
  try {
    await withTimeout(
      p.page.evaluate(async (i) => {
        const c = window.__slateClock;
        c.set(i.t * 1000);
        await window.slateScene.seek(i.t, i);
        c.syncAnimations();
        c.flush();
        await c.settle();
      }, info),
      job.seekTimeoutMs,
      `Frame ${frame} (t = ${s.t.toFixed(4)} s)`,
    );
  } catch (err) {
    if (err instanceof EngineError) throw err;
    throw refused(`The scene threw at frame ${frame} (t = ${s.t.toFixed(4)} s): ${String(err?.message ?? err).split('\n')[0]}`, 'A scene must draw every frame without throwing.', {
      frame,
      t: s.t,
      stack: String(err?.stack ?? '').split('\n').slice(0, 6),
    });
  }
  const shot = await p.cdp.send('Page.captureScreenshot', {format: 'png', fromSurface: true, captureBeyondViewport: false, optimizeForSpeed: true});
  return Buffer.from(shot.data, 'base64');
}

/** Frames go to pages in turn (frame i to page i mod n): every page moves forward in time, and pending stays small. */
async function drawFrames(job, session, onFrame) {
  const count = job.count;
  const pages = session.pages;
  const t0 = Date.now();
  let done = 0;
  let lastLog = t0;
  const state = {failed: false};
  await Promise.all(
    pages.map(async (p, pi) => {
      for (let i = pi; i < count; i += pages.length) {
        if (state.failed) return;
        const frame = job.from + i;
        const buffers = [];
        try {
          for (const s of sampleTimes(frame, job.samples, job.shutter, job.ctx.fps)) {
            if (state.failed) return;
            buffers.push(await drawSample(job, p, frame, s));
          }
          failIfPageErrors(session, `at frame ${frame}`);
          failIfBlocked(job, session, `at frame ${frame}`);
          await onFrame(i, frame, buffers);
        } catch (err) {
          state.failed = true;
          throw err;
        }
        done++;
        const now = Date.now();
        if (now - lastLog > PROGRESS_EVERY_MS) {
          lastLog = now;
          const rate = done / ((now - t0) / 1000);
          log(`  ${done}/${count} frames, ${rate.toFixed(2)}/s, ~${Math.round((count - done) / Math.max(rate, 1e-6))} s left`);
        }
      }
    }),
  );
}

// ---------------------------------------------------------------------------------------------------- the trace
/** The act's motion trace from the page (the kit's registry, through the runtime), limited to the act's shots. */
async function collectTrace(job, p) {
  if (!p.info.has_trace) {
    return {rows: null, reason: "the scene doesn't come from defineScene(), so it has no motion trace (moves made with @slate/web's tween() are traced)"};
  }
  const rows = await p.page.evaluate(() => window.slateScene.trace());
  if (!Array.isArray(rows)) return {rows: null, reason: "the scene's trace() didn't return a list"};
  const shots = new Set(job.act.shots);
  return {rows: rows.filter((r) => r && (r.shot === undefined || r.shot === null || shots.has(r.shot)))};
}

function writeTrace(job, trace) {
  if (!trace.rows) return {written: false, reason: trace.reason};
  const file = motionFile(job.ctx, job.act.act);
  writeJsonAtomic(file, trace.rows);
  return {written: true, file: relTo(job.ctx.dir, file), entries: trace.rows.length};
}

// ----------------------------------------------------------------------------------------------------- render
async function renderJob(job) {
  requireTool('ffmpeg', 'Install ffmpeg (`slate doctor --install` offers it).');
  requireTool('ffprobe');
  const {ctx, act} = job;
  const out = job.out;
  const part = `${out}.part`;
  fs.mkdirSync(path.dirname(out), {recursive: true});
  fs.rmSync(part, {force: true});
  if (job.framesDir) fs.mkdirSync(job.framesDir, {recursive: true});
  const t0 = Date.now();
  const session = await openSession(job);
  let encoder = null;
  const stop = () => {
    encoder?.kill();
    fs.rmSync(part, {force: true});
  };
  process.once('exit', stop);
  try {
    for (let i = 0; i < job.concurrency; i++) await loadScene(job, session, i);
    const first = session.pages[0];
    const {gates, warnings} = await checkScene(job, session, first);
    const argv = job.kind.still
      ? stillArgs({sub: job.samples, fps: ctx.fps, out: part})
      : ffmpegArgs({sub: job.samples, fps: ctx.fps, codec: job.kind.codec, out: part, format: job.kind.format});
    encoder = startEncoder(argv);
    log(`act ${act.act}: ${job.count} frame${job.count > 1 ? 's' : ''} × ${job.samples} samples → ${path.basename(out)} (web, ${job.kind.codec}, gl ${job.gl}, ${session.pages.length} page${session.pages.length > 1 ? 's' : ''})`);

    const pending = new Map();
    let next = 0;
    let chain = Promise.resolve();
    const flush = () => {
      chain = chain.then(async () => {
        while (pending.has(next)) {
          const buffers = pending.get(next);
          pending.delete(next);
          next++;
          for (const b of buffers) await encoder.write(b);
        }
      });
      return chain;
    };
    await drawFrames(job, session, async (i, frame, buffers) => {
      if (job.framesDir) {
        buffers.forEach((b, k) => fs.writeFileSync(path.join(job.framesDir, `f${String(frame).padStart(5, '0')}_s${String(k).padStart(2, '0')}.png`), b));
      }
      pending.set(i, buffers);
      await flush();
    });
    await flush();
    encoder.end();
    await encoder.done;

    const count = job.kind.still ? (fs.existsSync(part) ? 1 : 0) : await countFrames(part);
    if (count !== job.count) {
      const short = out.replace(/(\.[^.]+)$/, '.short$1');
      if (fs.existsSync(part)) fs.renameSync(part, short);
      throw refused(
        `Act ${act.act} rendered ${count} frames, but ${job.count} were asked for; ${path.basename(out)} is untouched.`,
        'Render the range again; if it repeats, look for frames that throw in the scene.',
        {short_render: fs.existsSync(short) ? short : null, frame_count: count, expected: job.count},
      );
    }
    fs.renameSync(part, out);

    const attempts = networkAttempts(session);
    gates.unshift(networkGate(session, attempts));
    const trace = job.trace ? writeTrace(job, await collectTrace(job, first)) : {written: false, reason: job.standalone ? 'a scene rendered without a film has no timing/ to write into' : 'not asked for'};
    const seconds = (Date.now() - t0) / 1000;
    log(`${path.basename(out)}: ${job.count} frame${job.count > 1 ? 's' : ''} in ${seconds.toFixed(1)} s`);
    const result = {
      engine: 'web',
      web_engine_version: WEB_ENGINE_VERSION,
      file: out,
      act: act.act,
      frames: [job.from, job.to],
      film_frames: [act.frameIn + job.from, act.frameIn + job.to + 1],
      frame_count: count,
      samples: job.samples,
      shutter: job.shutter,
      codec: job.kind.codec,
      gl: job.gl,
      fps: ctx.fps.rational,
      width: ctx.width,
      height: ctx.height,
      sha256: await sha256File(out),
      bundle_hash: sha256Buffer(Buffer.from(JSON.stringify(session.server.served()))),
      seconds: Math.round(seconds * 10) / 10,
      stream: await probe(out),
      pages: session.pages.length,
      standalone: job.standalone,
      scene: {
        entry: job.scene.entry,
        runtime: first.info.runtime,
        protocol: first.info.protocol,
        duration: first.info.duration,
        problems: first.info.problems,
      },
      browser: {path: session.browserInfo.path, version: session.browserInfo.version, source: session.browserInfo.source, pid: session.pid, profile: session.browser?.slateProfileDir ?? null},
      network: attempts,
      console: session.pages.flatMap((pg) => pg.consoleLines).slice(0, 50),
      warnings: [
        ...warnings,
        ...(first.info.videos ? [`the scene has ${first.info.videos} <video> element(s): a browser can't seek video frame-exactly; show footage as an image sequence (bindSequence from @slate/web)`] : []),
      ],
      trace,
      gates,
    };
    if (job.hashes && !job.kind.still) result.frame_hashes = await frameHashes(out);
    if (job.framesDir) result.frames_dir = job.framesDir;
    return result;
  } catch (err) {
    throw withBrowserPid(err, session);
  } finally {
    process.removeListener('exit', stop);
    if (encoder) encoder.kill();
    fs.rmSync(part, {force: true});
    await session.close();
  }
}

// ------------------------------------------------------------------------------------------------------- emit
/**
 * What the page declares about its act, once it has drawn its first frame: its beats (film seconds), its silence, what
 * the kit and tween() had to assume or refuse, and its word lookups that fell back to an estimate (null when the scene
 * doesn't report them: only defineScene() scenes do).
 */
async function readDeclarations(p) {
  return p.page.evaluate(async () => {
    const s = window.slateScene;
    const events = typeof s.events === 'function' ? await s.events() : (s.events ?? null);
    return {
      events,
      silent: s.silent ?? null,
      problems: typeof s.problems === 'function' ? s.problems() : [],
      misses: typeof s.misses === 'function' ? s.misses() : null,
    };
  });
}

/**
 * The act's beats from the page, checked with engine-core's own rules, and everything that stops them being written:
 * {silent, reason} or {events}, plus {problems}. The same rules as a Remotion act's (events.mjs computeEvents): a
 * silent act gives its reason and no beats, an act with beats passes validateEvents, and no beat is written while a
 * word lookup fell back to an estimate (its sound would land on a guessed time).
 */
function judgeDeclarations(job, decl) {
  const {act} = job;
  const problems = [];
  const list = Array.isArray(decl.events) ? decl.events : [];
  const misses = Array.isArray(decl.misses) ? decl.misses.map((m) => (typeof m === 'string' ? m : JSON.stringify(m))) : [];
  if (misses.length) {
    problems.push(`${misses.length} word lookup${misses.length > 1 ? 's' : ''} fell back to an estimate (${misses.slice(0, 5).join(', ')}${misses.length > 5 ? '…' : ''}): the beats would land on guessed times`);
  }
  const silentReason = typeof decl.silent === 'string' ? decl.silent : decl.silent?.reason;
  if (decl.silent !== null && decl.silent !== undefined && decl.silent !== false) {
    if (typeof silentReason !== 'string' || !silentReason.trim()) problems.push(`act ${act.act} says it's silent but gives no reason: defineScene({silent: 'why this act has no beats'})`);
    if (list.length) problems.push(`act ${act.act} says it's silent but also has ${list.length} beat${list.length > 1 ? 's' : ''}: keep one, its beats or its silence`);
    return {silent: true, reason: typeof silentReason === 'string' ? silentReason.trim() : '', problems, misses};
  }
  if (!list.length) {
    problems.push(`act ${act.act} has no beats and doesn't say it's silent: define its beats next to the moves that make them (defineEvent from '@slate/kit/core'), or say why it has none (defineScene({silent: '…'}))`);
    return {events: [], problems, misses};
  }
  const sorted = list.map((e) => ({...e})).sort((a, b) => a.t - b.t || String(a.id).localeCompare(String(b.id)));
  problems.push(...validateEvents(job.ctx, act.act, sorted));
  return {events: sorted, problems, misses};
}

/**
 * The act's visual beats and motion trace, written from the page itself: the same modules the picture reads, so a
 * beat, its sound and its frame agree (one emitter, as for Remotion acts). Checked with engine-core's own rules.
 * With {dry_run: true} nothing is written: the result says what would be, and what stops it (the hand-in check and
 * `slate emit-events --dry-run` read it), and the page's own gates (its network wall, its fonts) come back too.
 */
async function emitJob(job) {
  const {ctx, act} = job;
  const session = await openSession(job);
  try {
    const p = await loadScene(job, session, 0);
    const {gates} = await checkScene(job, session, p);
    await drawSample(job, p, 0, {t: 0, local: 0, sample: 0});
    failIfPageErrors(session, 'at its first frame');
    const decl = await readDeclarations(p);
    const judged = judgeDeclarations(job, decl);
    const file = eventsFile(ctx, act.act);
    const rel = relTo(ctx.dir, file);
    if (!job.dryRun && judged.problems.length) {
      const n = judged.problems.length;
      throw refused(`Act ${act.act}'s beats don't pass (${n} problem${n > 1 ? 's' : ''}); nothing was written:\n  ${judged.problems.slice(0, 20).join('\n  ')}`, 'Fix them in the scene, then emit again.', {problems: judged.problems, misses: judged.misses});
    }
    let summary;
    if (judged.silent) {
      if (!job.dryRun) writeJsonAtomic(file, {silent: true, reason: judged.reason, generated: GENERATED});
      summary = {act: act.act, file: rel, silent: true, reason: judged.reason, events: 0};
    } else {
      const kinds = {};
      for (const e of judged.events) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
      if (!job.dryRun) writeJsonAtomic(file, judged.events);
      summary = {act: act.act, file: rel, silent: false, events: judged.events.length, kinds, span_s: judged.events.length ? [judged.events[0].t, judged.events.at(-1).t] : null};
    }
    const trace = await collectTrace(job, p);
    if (job.dryRun) {
      summary.motion = trace.rows ? {written: false, would_write: relTo(ctx.dir, motionFile(ctx, act.act)), entries: trace.rows.length, reason: 'dry run'} : {written: false, reason: trace.reason};
      summary.dry_run = true;
      summary.written = false;
      summary.problems = judged.problems;
      summary.misses = judged.misses;
      summary.declared_misses = Array.isArray(decl.misses);
      summary.beats = judged.silent ? {silent: true, reason: judged.reason} : judged.events;
      gates.unshift(networkGate(session, networkAttempts(session)));
      summary.gates = gates;
      log(`act ${act.act}: ${judged.problems.length ? `${judged.problems.length} problem(s)` : 'would pass'}; nothing written (dry run)`);
    } else {
      summary.motion = writeTrace(job, trace);
      log(`act ${act.act}: ${summary.silent ? `silent (${summary.reason})` : `${summary.events} beats`} → ${summary.file}`);
    }
    summary.scene_problems = decl.problems;
    summary.engine = 'web';
    return {acts: [summary], skipped: [], browser: {pid: session.pid, profile: session.browser?.slateProfileDir ?? null}, ...(job.dryRun ? {dry_run: true} : {})};
  } catch (err) {
    throw withBrowserPid(err, session);
  } finally {
    await session.close();
  }
}

// --------------------------------------------------------------------------------------------------- scaffold
/**
 * Copy the template into <film>/scenes/act<N>/web/, the start of every web act (`slate engine scaffold --act N` calls
 * this; a brand's look.engine "web" picks it by default). Refuses to overwrite a scene that's already there unless
 * forced.
 */
function scaffold(args, {cwd}) {
  if (!args.film) throw usage('Say which film to add a web scene to ("film": its folder).');
  const ctx = loadFilm(path.resolve(cwd, String(args.film)));
  if ((args.act === undefined || args.act === null) && ctx.acts.length !== 1) {
    throw usage(`Say which act to start a web scene in ("act": N). ${ctx.id} has acts ${ctx.acts.map((a) => a.act).join(', ')}.`);
  }
  const act = actOf(ctx, args.act ?? ctx.acts[0].act);
  const dest = path.join(ctx.dir, 'scenes', `act${act.act}`, 'web');
  const existing = fs.existsSync(dest) ? fs.readdirSync(dest).filter((n) => n !== '.DS_Store') : [];
  if (existing.length && !args.force) {
    throw refused(`Act ${act.act} already has a web scene (${relTo(ctx.dir, dest)}); nothing was changed.`, 'Pass "force": true to replace it with the template.');
  }
  fs.mkdirSync(dest, {recursive: true});
  const files = [];
  for (const name of fs.readdirSync(TEMPLATE_DIR).sort()) {
    if (name === '.DS_Store') continue;
    const from = path.join(TEMPLATE_DIR, name);
    if (!fs.statSync(from).isFile()) continue;
    fs.copyFileSync(from, path.join(dest, name));
    files.push(relTo(ctx.dir, path.join(dest, name)));
  }
  log(`act ${act.act}: web scene started from the template → ${relTo(ctx.dir, dest)}`);
  return {engine: 'web', scaffolded: true, act: act.act, dir: dest, files};
}

// --------------------------------------------------------------------------------------------------- the door
/**
 * The web-render command. Returns a plain object (cli.mjs wraps it in {ok: true, …}); throws EngineError for
 * refusals (exit 1), usage (exit 2) and missing prerequisites (exit 3).
 */
export async function webRender(args = {}, {cwd = process.cwd()} = {}) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw usage('web-render takes a JSON object of args.');
  checkArgs(args);
  if (args.scaffold) return scaffold(args, {cwd});
  const job = await resolveJob(args, {cwd});
  try {
    return job.mode === 'emit' ? await emitJob(job) : await renderJob(job);
  } finally {
    cleanupJob(job);
  }
}

// ------------------------------------------------------------------------------------------- on its own (tests)
function parseArgv(argv) {
  let json = null;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--json') json = argv[++i] ?? '{}';
    else if (a.startsWith('--json=')) json = a.slice(7);
    else if (a === '--args-file') json = fs.readFileSync(path.resolve(argv[++i]), 'utf8');
    else throw usage(`Unexpected argument ${JSON.stringify(a)}.`, "Usage: node engine/web/render.mjs --json '<args>'");
  }
  if (json === null || !json.trim()) return {};
  try {
    return JSON.parse(json);
  } catch (e) {
    throw usage(`The args aren't valid JSON: ${e.message}`);
  }
}

async function main() {
  process.env.BROWSER = 'none';
  useEngineTmp();
  const write = process.stdout.write.bind(process.stdout);
  // stdout carries one JSON object; anything a library prints goes to stderr
  process.stdout.write = (chunk, encoding, cb) => process.stderr.write(chunk, encoding, cb);
  console.log = (...a) => process.stderr.write(`${a.join(' ')}\n`);
  let out;
  let code = 0;
  try {
    const result = await webRender(parseArgv(process.argv.slice(2)));
    out = {ok: true, command: 'web-render', ...result};
  } catch (err) {
    if (err instanceof EngineError) {
      out = {ok: false, command: 'web-render', error: err.message, kind: err.kind, ...(err.hint ? {hint: err.hint} : {}), ...(err.details ? {details: err.details} : {})};
      code = EXIT[err.kind] ?? 1;
    } else {
      out = {ok: false, command: 'web-render', error: String(err?.message ?? err), kind: 'error', details: {stack: String(err?.stack ?? '').split('\n').slice(0, 8)}};
      code = 1;
    }
  }
  process.exitCode = code;
  write(`${JSON.stringify(out)}\n`, () => process.exit(code));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => process.exit(130));
  await main();
}
