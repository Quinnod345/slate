// Shared helpers for Slate's Node engine: errors that map onto Slate's exit codes, logging that never touches stdout,
// child processes that can't open a browser, hashing and atomic JSON writes.
//
// stdout belongs to cli.mjs: it carries exactly one JSON object per command. Everything here logs to stderr.
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

/** The engine folder (the one holding cli.mjs and node_modules): the repo's engine/ or an installed copy. */
export const ENGINE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const REMOTION_DIR = path.join(ENGINE_DIR, 'remotion');
export const KIT_DIR = path.join(REMOTION_DIR, 'kit');
export const NODE_MODULES = path.join(ENGINE_DIR, 'node_modules');

/** The JSON command protocol. Bump it when a command's arguments or result change shape. */
export const PROTOCOL = 1;

// Exit codes shared with the Python CLI (docs/ARCHITECTURE.md §4).
export const EXIT = {
  error: 1,
  refused: 1,
  usage: 2,
  missing_prerequisite: 3,
  spend_refused: 4,
  busy: 5,
  stopped: 130,
};

/** An error the engine reports as {ok: false, error, hint, kind}. `kind` picks the exit code. */
export class EngineError extends Error {
  constructor(message, {kind = 'error', hint = null, details = null} = {}) {
    super(message);
    this.kind = kind in EXIT ? kind : 'error';
    this.hint = hint;
    this.details = details;
  }
}

export const usage = (message, hint = null, details = null) => new EngineError(message, {kind: 'usage', hint, details});
export const refused = (message, hint = null, details = null) => new EngineError(message, {kind: 'refused', hint, details});
export const missing = (message, hint = null, details = null) =>
  new EngineError(message, {kind: 'missing_prerequisite', hint, details});

let quiet = false;
export const setQuiet = (on) => {
  quiet = Boolean(on);
};

/** Progress for people, on stderr (stdout is the command's JSON result). */
export function log(message) {
  if (quiet) return;
  const stamp = new Date().toTimeString().slice(0, 8);
  process.stderr.write(`${stamp} ${message}\n`);
}

// The same PATH order slate.procs gives every child: arm64 Homebrew and ~/.local/bin first, whatever the caller's PATH
// says (macOS puts /usr/local/bin first, and on this kind of Mac it can hold stale Intel builds, an ffmpeg 7.1 among
// them), then the caller's folders, then the basics a bare launchd PATH lacks.
const PREFERRED_PATH = ['/opt/homebrew/bin', '/opt/homebrew/sbin', path.join(os.homedir(), '.local', 'bin')];
const FALLBACK_PATH = ['/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'];

/** The environment every child gets: BROWSER=none last, so nothing can turn it back on. */
export function childEnv(extra = {}) {
  const env = {...process.env, ...extra};
  const parts = [];
  for (const p of [...PREFERRED_PATH, ...(env.PATH ?? '').split(path.delimiter), ...FALLBACK_PATH]) {
    if (p && !parts.includes(p)) parts.push(p);
  }
  env.PATH = parts.join(path.delimiter);
  env.BROWSER = 'none';
  return env;
}

/** Find a tool on the augmented PATH (Homebrew and ~/.local/bin included, as slate.procs does). */
export function which(tool) {
  if (path.isAbsolute(tool)) return isExecutable(tool) ? tool : null;
  for (const dir of childEnv().PATH.split(path.delimiter)) {
    const candidate = path.join(dir, tool);
    if (isExecutable(candidate)) return candidate;
  }
  return null;
}

function isExecutable(p) {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

export function requireTool(tool, hint) {
  const found = which(process.env[`SLATE_${tool.toUpperCase()}`] ?? tool);
  if (!found) throw missing(`${tool} isn't installed or isn't on PATH.`, hint ?? 'Run `slate doctor` to see what is missing.');
  return found;
}

/**
 * Run a command to completion without a shell. Resolves {code, stdout, stderr}; stdout is a Buffer when
 * `binary` is set (raw frames), text otherwise.
 */
export function run(cmd, args, {cwd, input, binary = false, timeoutMs} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {cwd, env: childEnv(), stdio: ['pipe', 'pipe', 'pipe']});
    const out = [];
    const err = [];
    let timer = null;
    if (timeoutMs) {
      timer = setTimeout(() => {
        child.kill('SIGTERM');
        reject(new EngineError(`${path.basename(cmd)} took longer than ${Math.round(timeoutMs / 1000)} s and was stopped.`));
      }, timeoutMs);
    }
    child.stdout.on('data', (d) => out.push(d));
    child.stderr.on('data', (d) => err.push(d));
    child.on('error', (e) => {
      if (timer) clearTimeout(timer);
      reject(e);
    });
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      const stdout = Buffer.concat(out);
      resolve({code, stdout: binary ? stdout : stdout.toString('utf8'), stderr: Buffer.concat(err).toString('utf8')});
    });
    if (input !== undefined) child.stdin.end(input);
    else child.stdin.end();
  });
}

export function sha256Buffer(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}

export function sha256File(file) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash('sha256');
    fs.createReadStream(file)
      .on('data', (d) => h.update(d))
      .on('error', reject)
      .on('end', () => resolve(h.digest('hex')));
  });
}

export function sha256FileSync(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

export function readJson(file, fallback = undefined) {
  if (!fs.existsSync(file)) {
    if (fallback !== undefined) return fallback;
    throw usage(`Missing file: ${file}`);
  }
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    throw usage(`${file} isn't valid JSON: ${e.message}`);
  }
}

/** Slate's JSON style (1-space indent, trailing newline), written so readers see the old file or the new one. */
export function writeJsonAtomic(file, obj) {
  return writeTextAtomic(file, JSON.stringify(obj, null, 1) + '\n');
}

export function writeTextAtomic(file, text) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const tmp = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.tmp`);
  fs.writeFileSync(tmp, text);
  fs.renameSync(tmp, file);
  return file;
}

/** Write only when the content changed, so generated files keep their mtimes (and editors stay calm). */
export function writeIfChanged(file, text) {
  if (fs.existsSync(file) && fs.readFileSync(file, 'utf8') === text) return false;
  writeTextAtomic(file, text);
  return true;
}

/** Slate's runtime folder (SLATE_DATA, as slate.paths.data() reads it). */
export function slateData() {
  return process.env.SLATE_DATA
    ? path.resolve(process.env.SLATE_DATA.replace(/^~(?=$|\/)/, os.homedir()))
    : path.join(os.homedir(), 'Library', 'Application Support', 'Slate');
}

/** Every file under a folder (sorted, relative paths), skipping dot-folders we generate and macOS litter. */
export function listFiles(root, {skipDirs = ['node_modules', '.git'], skipHidden = true} = {}) {
  const out = [];
  if (!fs.existsSync(root)) return out;
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name === '.DS_Store') continue;
      if (skipHidden && entry.name.startsWith('.')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!skipDirs.includes(entry.name)) walk(full);
      } else if (entry.isFile()) {
        out.push(path.relative(root, full));
      }
    }
  };
  walk(root);
  return out;
}

/** The engine's own identity: its version and the pinned packages it was installed with. */
export function engineInfo() {
  const pkg = readJson(path.join(ENGINE_DIR, 'package.json'));
  const installed = (name) => {
    try {
      return JSON.parse(fs.readFileSync(path.join(NODE_MODULES, name, 'package.json'), 'utf8')).version;
    } catch {
      return null;
    }
  };
  const deps = {...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {})};
  const pins = {};
  const actual = {};
  for (const name of ['remotion', '@remotion/bundler', '@remotion/renderer', '@remotion/cli', '@remotion/google-fonts', 'react', 'react-dom', 'typescript', 'esbuild']) {
    pins[name] = deps[name] ?? null;
    actual[name] = installed(name);
  }
  return {engine_version: pkg.version, pins, installed: actual, node: process.version};
}

/** Frames → a display timecode (HH:MM:SS:FF on the nominal rate), matching slate.clock.Clock.timecode. */
export function timecode(frame, fps) {
  const nominal = Math.round(fps);
  const ff = frame % nominal;
  const total = Math.floor(frame / nominal);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(total / 3600))}:${p(Math.floor(total / 60) % 60)}:${p(total % 60)}:${p(ff)}`;
}

export function relTo(root, file) {
  const rel = path.relative(root, file);
  return rel.startsWith('..') ? file : rel;
}
