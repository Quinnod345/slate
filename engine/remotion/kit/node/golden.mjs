#!/usr/bin/env node
// Golden stills for the kit (Quality test 4): a handful of kit compositions rendered on software GL and compared with
// their baselines in engine/tests/kit/golden/ at 45 dB PSNR or better. On a GPU renderer the raster isn't bit-exact
// run to run, so the two-baseline rule applies: a still passes when it matches either of two stored baselines.
// One more composition must fail: it asks for a font no machine has, and the font gate has to catch it.
//
//   node remotion/kit/node/golden.mjs            compare (prints a summary; --json for one JSON object)
//   node remotion/kit/node/golden.mjs --update   write the baselines (renders each still twice; stores a second
//                                                baseline only when the two differ)
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
  {id: 'Kit-Section-logo', frame: 40, what: 'the logo build, mid-build'},
  {id: 'Kit-Section-motif', frame: 14, what: 'the motif and the character, mid-gesture'},
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
  const opts = {update: false, json: false, gl: 'swangle', browser: null, keep: null};
  for (const a of argv) {
    if (a === '--update') opts.update = true;
    else if (a === '--json') opts.json = true;
    else if (a.startsWith('--gl=')) opts.gl = a.slice(5);
    else if (a.startsWith('--browser=')) opts.browser = a.slice(10);
    else if (a.startsWith('--keep=')) opts.keep = a.slice(7);
  }
  return opts;
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

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
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
