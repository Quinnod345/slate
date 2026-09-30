// Shared helpers for the engine-core tests: run cli.mjs the way the Python bridge does, and make scratch films.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ENGINE = path.resolve(HERE, '..', '..');
export const CLI = path.join(ENGINE, 'cli.mjs');
export const FIXTURE = path.join(HERE, 'fixtures', 'film');

/** A scratch folder for one test file, with its own SLATE_DATA (so its bundles are its own). */
export function scratch(tag = 'core') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `slate-${tag}-`));
  fs.mkdirSync(path.join(dir, 'data'), {recursive: true});
  return dir;
}

/** Copy the fixture film (or build a variant with `edit(dir)`), returning its folder. */
export function film(root, name = 'film', edit = null) {
  const dest = path.join(root, name);
  fs.rmSync(dest, {recursive: true, force: true});
  fs.cpSync(FIXTURE, dest, {recursive: true});
  if (edit) edit(dest);
  return dest;
}

/**
 * Run `node cli.mjs <cmd> --json '<args>'`. Asserts the contract: stdout is exactly one JSON object on one line.
 * Returns {code, json, stderr}.
 */
export function cli(cmd, args = {}, {data, env = {}, timeout = 240_000} = {}) {
  const res = spawnSync(process.execPath, [CLI, cmd, '--json', JSON.stringify(args)], {
    cwd: os.tmpdir(),
    encoding: 'utf8',
    timeout,
    env: {...process.env, SLATE_DATA: data ?? process.env.SLATE_DATA, SLATE_FAKE_VENDORS: '1', ...env},
  });
  const lines = res.stdout.split('\n').filter((l) => l.trim());
  if (lines.length !== 1) throw new Error(`expected exactly one JSON line on stdout, got ${lines.length}:\n${res.stdout}\nstderr:\n${res.stderr}`);
  const json = JSON.parse(lines[0]);
  return {code: res.status, json, stderr: res.stderr};
}

/** cli() that must succeed. */
export function ok(cmd, args, opts) {
  const r = cli(cmd, args, opts);
  if (r.code !== 0 || !r.json.ok) throw new Error(`${cmd} failed (${r.code}): ${JSON.stringify(r.json)}\n${r.stderr}`);
  return r.json;
}

export function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(obj, null, 1) + '\n');
}

export function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** Split the fixture's two shots into two acts (S01 → act 1, S02 → act 2). */
export function twoActs(dir) {
  const f = path.join(dir, 'timing', 'shots.json');
  const d = readJson(f);
  d.acts = [
    {index: 1, name: 'One', shots: ['S01']},
    {index: 2, name: 'Two', shots: ['S02']},
  ];
  d.shots[1].act = 2;
  writeJson(f, d);
}

/** ffprobe a file's first video stream (JSON). */
export function probe(file) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-count_packets', '-show_entries',
    'stream=codec_name,profile,pix_fmt,color_range,color_space,color_transfer,color_primaries,r_frame_rate,width,height,nb_read_packets', '-of', 'json', file], {encoding: 'utf8'});
  return JSON.parse(r.stdout).streams[0];
}

/** One decoded frame as raw planar data: {data (Buffer), width, height}. */
export function decodeFrame(file, n, pixFmt = 'gray16le') {
  const r = spawnSync('ffmpeg', ['-v', 'error', '-i', file, '-vf', `select=eq(n\\,${n}),format=${pixFmt}`, '-frames:v', '1', '-f', 'rawvideo', '-'], {maxBuffer: 1 << 26});
  if (r.status !== 0) throw new Error(`ffmpeg couldn't decode frame ${n} of ${file}: ${r.stderr}`);
  return r.stdout;
}

export const kitCore = fs.existsSync(path.join(ENGINE, 'remotion', 'kit', 'core', 'index.ts'));
