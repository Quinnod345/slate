// Shared helpers for the web renderer's tests: run it the way slate does (one JSON object on stdout), make scratch
// folders and films, and read pixels back out of what it wrote.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ENGINE = path.resolve(HERE, '..', '..');
export const RENDER = path.join(ENGINE, 'web', 'render.mjs');
export const CLI = path.join(ENGINE, 'cli.mjs');
export const FIXTURES = path.join(HERE, 'fixtures');
export const SCENES = path.join(FIXTURES, 'scenes');
export const BRAND = path.join(FIXTURES, 'brand');
export const FILM = path.join(FIXTURES, 'film');

/** A scratch folder with its own SLATE_DATA, SLATE_FILMS and SLATE_BRANDS (nothing touches the real ones). */
export function scratch(tag = 'web') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `slate-${tag}-`));
  for (const d of ['data', 'films', 'brands']) fs.mkdirSync(path.join(dir, d), {recursive: true});
  return dir;
}

export function envFor(root, extra = {}) {
  return {
    ...process.env,
    SLATE_DATA: path.join(root, 'data'),
    SLATE_FILMS: path.join(root, 'films'),
    SLATE_BRANDS: path.join(root, 'brands'),
    SLATE_FAKE_VENDORS: '1',
    BROWSER: 'none',
    ...extra,
  };
}

/**
 * Run the renderer on its own (`node engine/web/render.mjs --json …`), or through the engine's command line with
 * `via: 'cli'`. Asserts the contract (exactly one JSON line on stdout) and returns {code, json, stderr}.
 */
export function run(args, {root, via = 'render', env = {}, timeout = 240_000} = {}) {
  const argv = via === 'cli' ? [CLI, 'web-render', '--json', JSON.stringify(args)] : [RENDER, '--json', JSON.stringify(args)];
  const res = spawnSync(process.execPath, argv, {
    cwd: root ?? os.tmpdir(),
    encoding: 'utf8',
    timeout,
    maxBuffer: 1 << 26,
    env: envFor(root ?? os.tmpdir(), env),
  });
  const lines = (res.stdout ?? '').split('\n').filter((l) => l.trim());
  if (lines.length !== 1) throw new Error(`expected exactly one JSON line on stdout, got ${lines.length}:\n${res.stdout}\nstderr:\n${res.stderr}`);
  return {code: res.status, json: JSON.parse(lines[0]), stderr: res.stderr};
}

/** run() that must succeed. */
export function ok(args, opts) {
  const r = run(args, opts);
  if (r.code !== 0 || !r.json.ok) throw new Error(`web-render failed (${r.code}): ${JSON.stringify(r.json).slice(0, 3000)}\n${r.stderr.slice(-3000)}`);
  return r.json;
}

/** Copy the fixture film (plus a scene into scenes/act1/web when given) into `root`, returning its folder. */
export function film(root, {name = 'film', scene = null} = {}) {
  const dest = path.join(root, name);
  fs.rmSync(dest, {recursive: true, force: true});
  fs.cpSync(FILM, dest, {recursive: true});
  if (scene) fs.cpSync(path.join(SCENES, scene), path.join(dest, 'scenes', 'act1', 'web'), {recursive: true});
  return dest;
}

/** Args for a scene rendered without a film: a scratch film at the fixture's rate and size. */
export const scratchFilm = (frames, extra = {}) => ({frames, fps_num: 25, fps_den: 1, width: 320, height: 180, gl: 'swangle', concurrency: 1, ...extra});

/** One decoded frame as 8-bit rgb24 (or another ffmpeg pixel format) bytes, row by row. */
export function decodeFrame(file, n, pixFmt = 'rgb24') {
  const r = spawnSync('ffmpeg', ['-v', 'error', '-i', file, '-vf', `select=eq(n\\,${n}),format=${pixFmt}`, '-frames:v', '1', '-f', 'rawvideo', '-'], {maxBuffer: 1 << 26});
  if (r.status !== 0) throw new Error(`ffmpeg couldn't decode frame ${n} of ${file}: ${r.stderr}`);
  return r.stdout;
}

/**
 * One frame's luma as exact 8-bit levels, read straight from the 10-bit plane the renderer wrote (BT.709 limited
 * range: level = (Y - 64) x 255 / 876). ffmpeg's own 10-bit to 8-bit decode reads a couple of levels dark, which would
 * hide the very errors these tests look for.
 */
export function decodeLuma(file, n, width = 320, height = 180) {
  const raw = decodeFrame(file, n, 'yuv422p10le');
  const out = new Uint8Array(width * height);
  for (let i = 0; i < width * height; i++) {
    const y = raw.readUInt16LE(i * 2);
    out[i] = Math.max(0, Math.min(255, Math.round(((y - 64) * 255) / 876)));
  }
  return out;
}

/** A PNG's red channel, row by row (the renderer's sub-frame screenshots, written with frames_dir). */
export function decodePngRed(file) {
  const rgb = decodeFrame(file, 0, 'rgb24');
  return Uint8Array.from({length: rgb.length / 3}, (_, i) => rgb[i * 3]);
}

/** ffprobe a file's first video stream, frames counted. */
export function probe(file) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-count_packets', '-show_entries',
    'stream=codec_name,profile,pix_fmt,color_range,color_space,color_transfer,color_primaries,r_frame_rate,width,height,nb_read_packets', '-of', 'json', file], {encoding: 'utf8'});
  return JSON.parse(r.stdout).streams[0];
}

/** Is a process id still alive? */
export function alive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
