#!/usr/bin/env node
// Golden stills for the kit (Quality test 4): a handful of kit compositions rendered on software GL and compared with
// their baselines in engine/tests/kit/golden/ at 45 dB PSNR or better. On a GPU renderer the raster isn't bit-exact
// run to run, so the two-baseline rule applies: a still passes when it matches either of two stored baselines.
// One more composition must fail: it asks for a font no machine has, and the font gate has to catch it.
//
//   node remotion/kit/node/golden.mjs            compare (prints a summary; --json for one JSON object)
//   node remotion/kit/node/golden.mjs --update   write the baselines (renders each still twice; stores a second
//                                                baseline only when the two differ)
//   node remotion/kit/node/golden.mjs --approved <film> [--brand=PATH] [--plan]
//                                                the approved-acts mode: every approved act's boundary frames and one
//                                                frame per shot, rendered with this kit and compared with the approved
//                                                render (`slate engine golden --approved <film>` runs it for you)
//   options: --gl=swangle|angle  --browser=PATH  --keep=DIR (keep the renders)
//
// Run from engine/. Exit codes: 0 pass, 1 a still failed, 3 no headless browser here (run `slate doctor --install`).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {psnrFiles} from './png.mjs';
import {bundleKit, findBrowser, removeBundle, renderKitStills} from './stills.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const ENGINE = path.resolve(here, '../../..');
export const GOLDEN_DIR = path.join(ENGINE, 'tests/kit/golden');
export const FIXTURE_FILM = path.join(ENGINE, 'tests/kit/fixtures/film-data.ts');
export const PSNR_MIN = 45;

/** The stills: one per kit component group, at a frame where it shows what it does (25 fps fixture). */
export const STILLS = [
  {id: 'Kit-Review', frame: 45, what: 'the whole review sheet: every component in the fixture brand'},
  {id: 'Kit-Review-Vertical', frame: 45, what: 'the same sheet at 9:16'},
  {id: 'Kit-Section-cards', frame: 0, what: 'a card and the app UI primitives'},
  {id: 'Kit-Section-charts', frame: 0, what: 'charts'},
  {id: 'Kit-Section-logo', frame: 40, what: 'the logo build, mid-build, its hanging keystone inside the cell'},
  {id: 'Kit-Section-motif', frame: 14, what: 'the motif and the character, mid-gesture'},
  {id: 'Kit-Section-count', frame: 7, what: 'counts mid-count: their own nines, a suffix held still, a word that follows'},
  {id: 'Kit-Section-card-turn', frame: 0, what: 'two-sided cards turned past 90°: a back face, never a missing edge'},
  {id: 'Kit-Gestures', frame: 25, what: 'the gesture sheet: every gesture at its contact (t = 1 s), at the smallest and largest size a film uses'},
  {id: 'Kit-Placeholder', frame: 110, what: 'a placeholder act with its timing overlays'},
  {id: 'Kit-Kinetic', frame: 40, what: 'kinetic type on the film’s words, captions burned in'},
  {id: 'Kit-Sting', frame: 75, what: 'the logo sting'},
];
export const MUST_FAIL = {id: 'Kit-FontMissing', frame: 0, what: 'a missing face: the font gate must fail the render'};

const baselineOf = (s, second = false) => path.join(GOLDEN_DIR, `${s.id}@${s.frame}${second ? '.b' : ''}.png`);

/**
 * The two-baseline rule: a render passes when it is within `min` dB of ANY stored baseline (one on software GL, where
 * the raster is bit-exact; up to two on a GPU, where it isn't). Returns every score for the evidence.
 */
export function compareStill(render, baselines, min = PSNR_MIN) {
  const present = baselines.filter((b) => fs.existsSync(b));
  if (!present.length) return {pass: false, psnr: [], baselines: 0, error: 'no baseline'};
  const scores = present.map((b) => psnrFiles(render, b));
  return {pass: Math.max(...scores) >= min, psnr: scores.map((x) => (x === Infinity ? 'identical' : +x.toFixed(2))), baselines: present.length};
}

function parseArgs(argv) {
  const opts = {update: false, json: false, gl: 'swangle', browser: null, keep: null, approved: null, brand: null, plan: false};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--update') opts.update = true;
    else if (a === '--json') opts.json = true;
    else if (a === '--plan') opts.plan = true;
    else if (a === '--approved') opts.approved = argv[++i] ?? null;
    else if (a.startsWith('--approved=')) opts.approved = a.slice(11);
    else if (a.startsWith('--brand=')) opts.brand = a.slice(8);
    else if (a.startsWith('--gl=')) opts.gl = a.slice(5);
    else if (a.startsWith('--browser=')) opts.browser = a.slice(10);
    else if (a.startsWith('--keep=')) opts.keep = a.slice(7);
  }
  return opts;
}

// --------------------------------------------------------------------------------------------- approved acts (D060)
// Kit changes are additive and opt-in: without its new prop a component draws exactly as before, so approved acts and
// contract frames don't change (the films' L-kit-D060). This mode proves it for one film before a kit change lands:
// every approved act's boundary frames and one frame per shot, rendered with this kit (render_mb, the approved render's
// own sample count, on software GL) and compared with the same frame of the approved render at the golden bar. An act
// is approved when approvals.json holds a person's approval of its registered render (renders/registry.json) whose
// SHA-256 is still the render's: `slate approve hero:act2 renders/act2.mov --by NAME`, say.

const readJsonOr = (file, fallback) => {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
};

/** The frames an approved act is checked at, in its own frame numbers: both boundaries and each shot's middle frame. */
export function approvedFrames(act, shots) {
  const frames = new Set([0, act.frames - 1]);
  for (const s of shots) {
    if (s.frameOut <= act.frameIn || s.frameIn >= act.frameOut) continue;
    frames.add(Math.floor((Math.max(s.frameIn, act.frameIn) + Math.min(s.frameOut, act.frameOut) - 1) / 2) - act.frameIn);
  }
  return [...frames].filter((f) => f >= 0 && f < act.frames).sort((a, b) => a - b);
}

/**
 * The film's approved acts: each registered act render a person approved (its approval's SHA-256 still the
 * registry's), with the frames to check. Acts whose render no longer lines up with the timing (the act moved) or no
 * longer hashes as approved are listed with the reason, never compared.
 */
export async function approvedActs(filmDir) {
  const {loadFilm} = await import('../../lib/film.mjs');
  const {sha256File} = await import('../../lib/common.mjs');
  const ctx = loadFilm(filmDir);
  const registry = readJsonOr(path.join(ctx.dir, 'renders', 'registry.json'), {entries: []});
  const approvals = readJsonOr(path.join(ctx.dir, 'approvals.json'), {approvals: []});
  const norm = (p) => path.normalize(String(p ?? '')).replace(/^\.\//, '');
  const shots = (ctx.shots?.shots ?? []).map((s) => ({id: s.id, frameIn: Number(s.frame_in), frameOut: Number(s.frame_out)}));
  const acts = [];
  for (const e of registry.entries ?? []) {
    const rel = norm(path.isAbsolute(e.file) ? path.relative(ctx.dir, e.file) : e.file);
    const approval = (approvals.approvals ?? []).filter((a) => norm(a.path) === rel && a.sha256 && a.sha256 === e.sha256).at(-1);
    if (!approval) continue;
    const act = ctx.acts.find((a) => a.act === Number(e.act));
    const file = path.join(ctx.dir, rel);
    const row = {act: Number(e.act), file: rel, samples: e.samples ?? null, approval: {id: approval.id, what: approval.what, by: approval.approved_by}, frames: []};
    if (!act) row.problem = `act ${e.act} isn't in timing/shots.json any more`;
    else if (!Array.isArray(e.frames) || e.frames[0] !== act.frameIn || e.frames[1] !== act.frameOut) row.problem = `act ${e.act} moved since its approved render (registered ${JSON.stringify(e.frames)}, timing now [${act.frameIn}, ${act.frameOut}])`;
    else if (!fs.existsSync(file)) row.problem = `the approved render ${rel} is missing`;
    else if ((await sha256File(file)) !== e.sha256) row.problem = `${rel} changed on disk since it was approved`;
    else if (!row.samples) row.problem = `${rel} has no sample count (an After Effects render): the kit doesn't draw it`;
    if (act && !row.problem) row.frames = approvedFrames(act, shots);
    acts.push(row);
  }
  return {ctx, acts};
}

async function ffmpegRun(args) {
  const {requireTool, run} = await import('../../lib/common.mjs');
  const res = await run(requireTool('ffmpeg'), ['-v', 'error', '-y', ...args]);
  if (res.code !== 0) throw new Error(`ffmpeg ${args.join(' ')} failed: ${res.stderr.trim().slice(-400)}`);
}

/**
 * Render every approved act's frames with this kit and compare each with the approved render. Returns {pass, acts:
 * [{act, file, frames: [{frame, psnr, pass}], problem?}], checked}. `plan` lists what it would render.
 */
export async function runApproved({film, brandPath = null, gl = 'swangle', plan = false, keep = null, minDb = PSNR_MIN}) {
  const {ctx, acts} = await approvedActs(film);
  const base = {mode: 'approved', film: ctx.dir, gl, psnrMin: minDb, plan};
  if (plan || !acts.length) {
    const problems = acts.filter((a) => a.problem);
    return {...base, pass: !problems.length, checked: 0, acts, note: acts.length ? undefined : 'no approved acts in this film: nothing a kit change could break here'};
  }
  const {renderMb} = await import('../../render_mb.mjs');
  const {useBundle} = await import('../../bundle.mjs');
  const {psnr} = await import('../../stills.mjs');
  const work = keep ?? fs.mkdtempSync(path.join(os.tmpdir(), 'slate-golden-approved-'));
  fs.mkdirSync(work, {recursive: true});
  const b = await useBundle(ctx, {brand_path: brandPath});
  let checked = 0;
  try {
    for (const a of acts) {
      if (a.problem) continue;
      const rows = [];
      for (const f of a.frames) {
        const chunk = path.join(work, `act${a.act}_f${String(f).padStart(5, '0')}.mkv`);
        await renderMb(ctx, {act: a.act, from: f, to: f, sub: a.samples, out: chunk, codec: 'lossless', gl, serve_url: b.serve_url, brand_path: brandPath, concurrency: 1});
        const now = chunk.replace(/\.mkv$/, '.now.png');
        const then = chunk.replace(/\.mkv$/, '.approved.png');
        await ffmpegRun(['-i', chunk, '-frames:v', '1', now]);
        await ffmpegRun(['-i', path.join(ctx.dir, a.file), '-vf', `select=eq(n\\,${f})`, '-frames:v', '1', then]);
        const db = await psnr(now, then);
        const pass = db !== null && db >= minDb;
        rows.push({frame: f, psnr: db === Infinity ? 'identical' : db === null ? null : +db.toFixed(2), pass});
        checked++;
      }
      a.frames = rows;
      a.pass = rows.every((r) => r.pass);
    }
  } finally {
    b.release();
    if (!keep) fs.rmSync(work, {recursive: true, force: true});
  }
  const pass = acts.every((a) => !a.problem && a.pass);
  return {...base, pass, checked, acts, renders: keep ? work : null};
}

export async function runGolden(opts) {
  const browser = findBrowser(opts.browser);
  if (!browser) return {skipped: true, pass: false, reason: 'no headless Chrome here: run `slate doctor --install` (it asks first), or set SLATE_CHROME'};
  const work = opts.keep ?? fs.mkdtempSync(path.join(os.tmpdir(), 'slate-golden-'));
  fs.mkdirSync(GOLDEN_DIR, {recursive: true});
  const started = Date.now();
  const serveUrl = await bundleKit({filmData: FIXTURE_FILM});
  const bundledMs = Date.now() - started;
  try {
    const renders = STILLS.map((s) => ({...s, out: path.join(work, `${s.id}@${s.frame}.png`)}));
    const again = opts.update ? STILLS.map((s) => ({...s, out: path.join(work, `${s.id}@${s.frame}.again.png`)})) : [];
    const failProbe = {...MUST_FAIL, out: path.join(work, `${MUST_FAIL.id}.png`)};
    const results = await renderKitStills({serveUrl, stills: [...renders, ...again, failProbe], gl: opts.gl, browserExecutable: browser});
    const byOut = new Map(results.map((r) => [r.out, r]));
    const stills = [];
    for (const s of renders) {
      const r = byOut.get(s.out);
      const row = {id: s.id, frame: s.frame, what: s.what, rendered: !!r?.ok, error: r?.error ?? null, ms: r?.ms ?? null};
      if (!r?.ok) {
        stills.push({...row, pass: false});
        continue;
      }
      if (opts.update) {
        fs.copyFileSync(s.out, baselineOf(s));
        const twin = again.find((x) => x.id === s.id && x.frame === s.frame);
        const twinR = twin ? byOut.get(twin.out) : null;
        let det = null;
        if (twinR?.ok) {
          det = psnrFiles(s.out, twin.out);
          if (det !== Infinity) fs.copyFileSync(twin.out, baselineOf(s, true));
          else if (fs.existsSync(baselineOf(s, true))) fs.rmSync(baselineOf(s, true));
        }
        stills.push({...row, pass: true, updated: true, rerunPsnr: det === Infinity ? 'identical' : det, baselines: fs.existsSync(baselineOf(s, true)) ? 2 : 1});
        continue;
      }
      const verdict = compareStill(s.out, [baselineOf(s), baselineOf(s, true)]);
      if (verdict.error) {
        stills.push({...row, pass: false, error: `no baseline at ${path.relative(ENGINE, baselineOf(s))}: run with --update, look at the stills, and keep them`});
        continue;
      }
      stills.push({...row, ...verdict});
    }
    const probe = byOut.get(failProbe.out);
    const fontGate = {
      id: MUST_FAIL.id,
      what: MUST_FAIL.what,
      rendered: !!probe?.ok,
      status: probe?.fontGate?.status ?? null,
      summary: probe?.fontGate?.summary ?? probe?.error ?? null,
      pass: !!probe && !probe.ok && /Font gate failed/.test(probe.error ?? '') && probe.fontGate?.status === 'FAIL',
    };
    const pass = stills.every((s) => s.pass) && fontGate.pass;
    return {pass, gl: opts.gl, browser, psnrMin: PSNR_MIN, bundleMs: bundledMs, totalMs: Date.now() - started, stills, fontGate, renders: opts.keep ? work : null};
  } finally {
    removeBundle(serveUrl);
    if (!opts.keep) fs.rmSync(work, {recursive: true, force: true});
  }
}

/** One line per approved act, for people. */
function describeApproved(r) {
  const lines = [];
  for (const a of r.acts) {
    if (a.problem) lines.push(`FAIL  act ${a.act}  ${a.problem}`);
    else if (r.plan) lines.push(`plan  act ${a.act}  ${a.file}, ${a.samples} samples, frames ${a.frames.join(', ')}`);
    else lines.push(`${a.pass ? 'PASS' : 'FAIL'}  act ${a.act}  ${a.frames.map((x) => `f${x.frame} ${x.psnr === 'identical' ? 'identical' : `${x.psnr} dB`}`).join(', ')}`);
  }
  if (r.note) lines.push(r.note);
  lines.push(r.plan ? 'plan only: nothing rendered' : `${r.pass ? 'every approved act draws as approved' : 'an approved act changed: the kit change is not additive'} (${r.checked} frames, ${r.gl}, ${r.psnrMin} dB bar)`);
  return lines.join('\n');
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain && parseArgs(process.argv.slice(2)).approved) {
  const opts = parseArgs(process.argv.slice(2));
  runApproved({film: path.resolve(opts.approved), brandPath: opts.brand ? path.resolve(opts.brand) : null, gl: opts.gl, plan: opts.plan, keep: opts.keep})
    .then((r) => {
      console.log(opts.json ? JSON.stringify(r) : describeApproved(r));
      process.exit(r.pass ? 0 : 1);
    })
    .catch((e) => {
      console.error(`the approved-acts check couldn't run: ${e?.stack ?? e}`);
      process.exit(e?.kind === 'missing_prerequisite' ? 3 : 1);
    });
} else if (isMain) {
  const opts = parseArgs(process.argv.slice(2));
  runGolden(opts)
    .then((r) => {
      if (opts.json) console.log(JSON.stringify(r));
      else if (r.skipped) console.log(`golden stills skipped: ${r.reason}`);
      else {
        for (const s of r.stills) console.log(`${s.pass ? 'PASS' : 'FAIL'}  ${s.id}@${s.frame}  ${s.updated ? `baseline written (${s.baselines})` : s.psnr ? `${s.psnr.join(' / ')} dB` : s.error}`);
        console.log(`${r.fontGate.pass ? 'PASS' : 'FAIL'}  ${r.fontGate.id}  font gate ${r.fontGate.status ?? 'did not report'}: ${r.fontGate.summary ?? ''}`);
        console.log(`${r.pass ? 'all golden stills pass' : 'golden stills failed'} (${r.gl}, ${(r.totalMs / 1000).toFixed(1)} s)`);
      }
      process.exit(r.skipped ? 3 : r.pass ? 0 : 1);
    })
    .catch((e) => {
      console.error(`golden stills couldn't run: ${e?.stack ?? e}`);
      process.exit(1);
    });
}
