#!/usr/bin/env node
// Slate's engine command line: the only way the Python CLI talks to Node.
//
//   node engine/cli.mjs <command> --json '<args as JSON>'        (or --args-file <file.json>)
//
// It prints exactly one JSON object on stdout: {ok: true, engine_version, protocol, command, …} on success, or
// {ok: false, error, hint, kind, …} with a non-zero exit (1 refused, 2 usage, 3 missing prerequisite, 5 busy,
// 130 stopped by a signal or by SLATE_ENGINE_DEADLINE_S). Progress goes to stderr. Paths in args may be absolute or
// relative to the caller's folder.
//
// Every child process runs with BROWSER=none, and nothing here starts Remotion Studio; if you ever start it by hand,
// pass --no-open (engine/remotion/remotion.config.ts turns the browser off too).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import util from 'node:util';
import {pathToFileURL} from 'node:url';

process.env.BROWSER = 'none';

// stdout carries the one result object; anything a library prints goes to stderr instead.
const writeResult = process.stdout.write.bind(process.stdout);
process.stdout.write = (chunk, encoding, cb) => process.stderr.write(chunk, encoding, cb);
for (const method of ['log', 'info', 'debug']) {
  console[method] = (...a) => process.stderr.write(util.format(...a) + '\n');
}

const CALLER_CWD = process.cwd();
const {ENGINE_DIR, EXIT, EngineError, PROTOCOL, engineInfo, missing, setQuiet, slateData, usage} = await import('./remotion/lib/common.mjs');
// Remotion keeps its headless browser under the nearest package.json's node_modules: this engine's.
process.chdir(ENGINE_DIR);

const COMMANDS = {
  version: 'engine and package versions, the browser, the kit and the web renderer',
  'browser-ensure': "download Remotion's chrome-headless-shell (149) if it isn't there",
  entry: "write a film's generated entry (.slate/) and tsconfig from timing/shots.json and scenes/",
  bundle: 'make or reuse the bundle for a film\'s current sources ({release: true} deletes it; {cleanup: true} deletes every unused bundle)',
  render: 'render an act or the film with motion blur (render_mb) to ProRes 422 HQ, or a lossless .mkv chunk',
  stills: 'review stills, labelled contact sheets and review sheets from one bundle',
  'emit-events': "write each act's visual events (or its silence) and the kit's motion trace",
  trace: "write one act's motion trace (timing/motion/actN.json) from the kit's motion registry, as the act is now",
  check: "an act's hand-in check, as gate results ({acts: [N, …]} checks several with one bundle)",
  'web-render': 'render with the open web renderer (engine/web/render.mjs)',
};

let command = null;
let finished = false;

function base() {
  let info = {engine_version: null};
  try {
    info = engineInfo();
  } catch {
    // a broken package.json shouldn't hide the real error
  }
  return {engine_version: info.engine_version, protocol: PROTOCOL, command};
}

/**
 * Print the one result and exit. On macOS a write to a pipe is asynchronous, so the exit waits for the write to
 * finish; the exit also ends anything a library left running (a browser tab, a server) instead of hanging.
 */
function finish(obj, code) {
  if (finished) return;
  finished = true;
  process.exitCode = code;
  writeResult(JSON.stringify(obj) + '\n', () => process.exit(code));
}

function fail(err) {
  if (err instanceof EngineError) {
    const out = {ok: false, ...base(), error: err.message, kind: err.kind};
    if (err.hint) out.hint = err.hint;
    if (err.details) out.details = err.details;
    finish(out, EXIT[err.kind] ?? 1);
  } else {
    const message = err?.message ?? String(err);
    finish({ok: false, ...base(), error: message, kind: 'error', details: {stack: String(err?.stack ?? '').split('\n').slice(0, 8)}}, 1);
  }
}

// A signal (a caller's timeout sends SIGTERM) still ends in one JSON object, and the 'exit' handlers remove
// partial files and this process's bundles.
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(sig, () => fail(new EngineError(`stopped by ${sig}`, {kind: 'stopped'})));
}
process.on('unhandledRejection', (e) => fail(e));

// A caller with a deadline says so in SLATE_ENGINE_DEADLINE_S (the Python bridge sets it a little inside its own
// timeout). Python stops a late child with SIGKILL, which skips every cleanup; stopping ourselves first runs the 'exit'
// handlers, so no 290 MB bundle or half-written .part file outlives a timed-out call.
const deadline = Number(process.env.SLATE_ENGINE_DEADLINE_S);
if (Number.isFinite(deadline) && deadline > 0) {
  setTimeout(() => {
    fail(new EngineError(`took longer than ${deadline} s and was stopped`, {
      kind: 'stopped',
      hint: 'Give it a longer timeout, or render in smaller ranges (from/to) so each call finishes in time.',
    }));
  }, deadline * 1000).unref();
}

function parseArgv(argv) {
  const [cmd, ...rest] = argv;
  let json = null;
  let quiet = false;
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === '--json') {
      json = rest[i + 1] ?? '{}';
      i++;
    } else if (a.startsWith('--json=')) {
      json = a.slice(7);
    } else if (a === '--args-file') {
      json = fs.readFileSync(path.resolve(CALLER_CWD, rest[i + 1]), 'utf8');
      i++;
    } else if (a === '--quiet') {
      quiet = true;
    } else {
      throw usage(`Unexpected argument ${JSON.stringify(a)}.`, "Usage: node engine/cli.mjs <command> --json '<args>'");
    }
  }
  let args = {};
  if (json !== null && json.trim()) {
    try {
      args = JSON.parse(json);
    } catch (e) {
      throw usage(`The args aren't valid JSON: ${e.message}`);
    }
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw usage('The args must be a JSON object.');
  }
  return {cmd, args, quiet};
}

/** Make relative paths in args relative to the caller, not to the engine folder we moved into. */
function resolvePaths(args) {
  const out = {...args};
  // A web scene's `scene` and `frames_dir` are the film's own paths: the web renderer resolves them against the film
  // folder (or, for a scene rendered on its own, against the caller's folder, which it's handed as `cwd`).
  for (const key of ['film', 'out', 'out_dir', 'entry', 'serve_url', 'brand_path', 'bundle_root', 'browser']) {
    if (typeof out[key] === 'string' && out[key]) out[key] = path.resolve(CALLER_CWD, out[key].replace(/^~(?=$|\/)/, os.homedir()));
  }
  return out;
}

/** The neutral film (remotion/neutral), copied where the engine can write its generated entry. */
async function neutralFilm() {
  const src = path.join(ENGINE_DIR, 'remotion', 'neutral');
  const dest = path.join(slateData(), 'cache', 'neutral-film');
  for (const rel of ['film.json', 'timing/shots.json']) {
    const from = path.join(src, rel);
    const to = path.join(dest, rel);
    if (!fs.existsSync(to) || fs.readFileSync(to, 'utf8') !== fs.readFileSync(from, 'utf8')) {
      fs.mkdirSync(path.dirname(to), {recursive: true});
      fs.copyFileSync(from, to);
    }
  }
  return dest;
}

async function film(args, {optional = false} = {}) {
  const {loadFilm} = await import('./remotion/lib/film.mjs');
  if (!args.film) {
    if (optional) return loadFilm(await neutralFilm());
    throw usage('No film given: pass "film" (the film folder\'s path).', 'slate passes it for you: run slate inside a film folder or with --film.');
  }
  return loadFilm(args.film);
}

const handlers = {
  async version() {
    const {browserStatus} = await import('./remotion/browser.mjs');
    const {kitEntry, kitHooksModule, kitPresent} = await import('./remotion/entry.mjs');
    const info = engineInfo();
    const web = webModulePath();
    return {
      ...info,
      engine_dir: ENGINE_DIR,
      commands: COMMANDS,
      browser: browserStatus(),
      kit: {present: kitPresent(), entry: kitEntry(), hooks_from: kitHooksModule()},
      web: {present: fs.existsSync(web), module: web},
      pins_ok: Object.entries(info.pins).every(([name, pin]) => !pin || info.installed[name] === pin),
    };
  },
  async 'browser-ensure'() {
    const {ensureHeadlessShell} = await import('./remotion/browser.mjs');
    return ensureHeadlessShell();
  },
  async entry(args) {
    const {ensureEntry} = await import('./remotion/entry.mjs');
    const ctx = await film(args);
    return {film: ctx.id, film_dir: ctx.dir, ...(await ensureEntry(ctx, {brandPath: args.brand_path ?? null}))};
  },
  async bundle(args) {
    const b = await import('./remotion/bundle.mjs');
    if (args.cleanup && !args.film) return b.cleanupAll(args);
    const ctx = await film(args);
    return {film: ctx.id, ...(await b.bundleCommand(ctx, args))};
  },
  async render(args) {
    const {renderMb} = await import('./remotion/render_mb.mjs');
    const {traceAct} = await import('./remotion/events.mjs');
    const ctx = await film(args);
    return {film: ctx.id, ...(await renderMb(ctx, args, {onTrace: traceAct}))};
  },
  async stills(args) {
    const {stillsCommand} = await import('./remotion/stills.mjs');
    const ctx = await film(args, {optional: Boolean(args.entry)});
    return {film: ctx.id, ...(await stillsCommand(ctx, args))};
  },
  async 'emit-events'(args) {
    const {emitEvents} = await import('./remotion/events.mjs');
    const ctx = await film(args);
    return {film: ctx.id, ...(await emitEvents(ctx, args))};
  },
  async trace(args) {
    const {traceAct, webScene} = await import('./remotion/events.mjs');
    const {actOf} = await import('./remotion/lib/film.mjs');
    if (args.act === undefined || args.act === null) throw usage('Say which act to trace ("act": N).');
    const ctx = await film(args);
    const act = actOf(ctx, args.act).act;
    if (webScene(ctx.dir, act)) {
      return {film: ctx.id, act, written: false, reason: 'a web scene: the open web renderer writes its motion trace while it renders'};
    }
    return {film: ctx.id, act, ...(await traceAct(ctx, act, {brandPath: args.brand_path ?? null}))};
  },
  async check(args) {
    const {checkAct, checkActs} = await import('./remotion/check.mjs');
    const several = Array.isArray(args.acts);
    if (!several && (args.act === undefined || args.act === null)) throw usage('Say which act to check ("act": N, or "acts": [N, …] for one run with one bundle).');
    const ctx = await film(args);
    return {film: ctx.id, ...(several ? await checkActs(ctx, args) : await checkAct(ctx, args))};
  },
  async 'web-render'(args) {
    const file = webModulePath();
    if (!fs.existsSync(file)) {
      throw missing(`The open web renderer isn't in this engine yet (${file}).`, 'Update Slate and run `slate engine install` again.');
    }
    const mod = await import(pathToFileURL(file).href);
    if (typeof mod.webRender !== 'function') throw missing(`${file} doesn't export webRender(args).`);
    const result = await mod.webRender(args, {cwd: CALLER_CWD});
    return result && typeof result === 'object' ? result : {result};
  },
};

function webModulePath() {
  return process.env.SLATE_WEB_RENDER_MODULE ? path.resolve(CALLER_CWD, process.env.SLATE_WEB_RENDER_MODULE) : path.join(ENGINE_DIR, 'web', 'render.mjs');
}

try {
  const parsed = parseArgv(process.argv.slice(2));
  command = parsed.cmd ?? null;
  setQuiet(parsed.quiet);
  if (!command) throw usage(`Say which command to run: ${Object.keys(COMMANDS).join(', ')}.`, "Usage: node engine/cli.mjs <command> --json '<args>'");
  const handler = handlers[command];
  if (!handler) throw usage(`Unknown engine command ${JSON.stringify(command)}. Commands: ${Object.keys(COMMANDS).join(', ')}.`);
  const result = await handler(resolvePaths(parsed.args));
  finish({ok: true, ...base(), ...result}, 0);
} catch (e) {
  fail(e);
}
