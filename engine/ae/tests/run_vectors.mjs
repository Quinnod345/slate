#!/usr/bin/env node
// The ExtendScript vectors runner: executes Slate's ES3 ease maths (engine/ae/lib/slate_math.jsx) in node, with a
// minimal ExtendScript shim, against the shared cross-language vectors that pytest and vitest also check.
//
//   node engine/ae/tests/run_vectors.mjs            human summary; exit 0 when every vector passes
//   node engine/ae/tests/run_vectors.mjs --json     one JSON object on stdout
//   node engine/ae/tests/run_vectors.mjs --vectors <eases.json> [--t90 <t90.json>]
//
// Which vectors: tests/vectors/eases.json and t90.json (written by the engine-kit builder from the kit's own law)
// when they exist, otherwise the local stand-in engine/ae/tests/fixtures/eases.local.json. The checks:
//   value    SL.easeValue(ease, u) against every sample (exact endpoints included);
//   t90      SL.t90, and t50/t99 from t90.json;
//   start    SL.startFor(land, dur, ease) from t90.json;
//   ae       SL.aeEase against the kit's After Effects speed and influence for every bezier;
//   spline   SL.monoSpline against the kit's monotone camera splines;
//   roundtrip  SL.segEase's speeds and influences, evaluated the way After Effects draws a segment, give back the
//            ease itself (rising, falling and large-valued segments) to 0.5% of travel: AE's smallest influence
//            (0.1%) can't draw a vertical tangent exactly, which is the only place it falls short.
// It also lints the ES3 file, so a stray `let` or `.map(` fails here before it fails inside After Effects.

import {existsSync, readFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {es3Context, lintEs3, loadJsx, runInContext} from './jsx_loader.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ENGINE_AE = resolve(HERE, '..');
export const REPO = resolve(ENGINE_AE, '..', '..');
export const SHARED = resolve(REPO, 'tests', 'vectors', 'eases.json');
export const SHARED_T90 = resolve(REPO, 'tests', 'vectors', 't90.json');
export const LOCAL = resolve(HERE, 'fixtures', 'eases.local.json');
export const MATH = resolve(ENGINE_AE, 'lib', 'slate_math.jsx');
const ROUNDTRIP_TOL = 5e-3;

/** Load the ES3 maths into a fresh ExtendScript-like context and return its SL namespace. */
export function loadMath() {
  const src = loadJsx(MATH);
  const ctx = es3Context();
  runInContext(src + '\n;SL;', ctx, 'slate_math.jsx');
  return ctx.SL;
}

const close = (got, want, tol) => Math.abs(got - want) <= tol * Math.max(1, Math.abs(want));

function roundtrip(SL, name, def, failures, counts) {
  if (def.kind !== 'bezier') return;
  for (const dv of [1, -250, 1234.5]) {
    const seg = SL.segEase(def.points, dv);
    let worst = 0;
    for (let i = 0; i <= 200; i++) {
      const u = i / 200;
      worst = Math.max(worst, Math.abs(SL.aeSegmentValue(seg, dv, u) - SL.easeValue(def, u)));
    }
    counts.roundtrip++;
    if (!(worst <= ROUNDTRIP_TOL)) failures.push({check: 'roundtrip', ease: name, dv, worst});
    if (!(seg.out[1] >= 0.1 && seg.out[1] <= 100 && seg.inn[1] >= 0.1 && seg.inn[1] <= 100)) {
      failures.push({check: 'roundtrip', ease: name, dv, problem: 'influence outside AE range 0.1..100'});
    }
  }
}

/** The shared format: eases.json {tolerance, eases: [{name, spec, samples, t90, ae}], splines} + t90.json. */
function checkShared(SL, easesPath, t90Path, failures, counts) {
  const doc = JSON.parse(readFileSync(easesPath, 'utf8'));
  const tol = doc.tolerance ?? 1e-12;
  for (const e of doc.eases || []) {
    const def = SL.normEase(e.spec);
    counts.eases++;
    for (const [u, want] of e.samples || []) {
      const got = SL.easeValue(def, u);
      counts.value++;
      if (!close(got, want, tol)) failures.push({check: 'value', ease: e.name, u, want, got});
    }
    if (e.t90 !== undefined) {
      const got = SL.t90(def);
      counts.t90++;
      if (!close(got, e.t90, tol)) failures.push({check: 't90', ease: e.name, want: e.t90, got});
    }
    if (e.ae) {
      const got = SL.aeEase(def.points);
      counts.ae++;
      for (const side of ['out', 'in']) {
        for (const k of ['influence', 'speed']) {
          if (!close(got[side][k], e.ae[side][k], tol)) {
            failures.push({check: 'ae', ease: e.name, side, key: k, want: e.ae[side][k], got: got[side][k]});
          }
        }
      }
    }
    roundtrip(SL, e.name, def, failures, counts);
  }
  for (const s of doc.splines || []) {
    for (const [t, want] of s.samples || []) {
      const got = SL.monoSpline(t, s.keys);
      counts.spline++;
      if (!close(got, want, tol)) failures.push({check: 'spline', spline: s.name, t, want, got});
    }
  }
  if (t90Path && existsSync(t90Path)) {
    const t = JSON.parse(readFileSync(t90Path, 'utf8'));
    const ttol = t.tolerance ?? 1e-9;
    for (const r of t.t90 || []) {
      const def = SL.normEase(r.spec);
      for (const [key, level] of [['t50', 0.5], ['t90', 0.9], ['t99', 0.99]]) {
        if (r[key] === undefined) continue;
        const got = SL.tAt(def, level);
        counts.t90++;
        if (!close(got, r[key], ttol)) failures.push({check: key, ease: r.name, want: r[key], got});
      }
    }
    for (const r of t.start_for || []) {
      const got = SL.startFor(r.land, r.dur, SL.normEase(r.spec));
      counts.start++;
      if (!close(got, r.start, ttol)) failures.push({check: 'start_for', ease: r.ease, want: r.start, got});
    }
  }
}

/** The local stand-in: {eases: {name: def}, cases: [{ease, u, value}], t90: {name: x}, tolerance}. */
function checkLocal(SL, path, failures, counts) {
  const doc = JSON.parse(readFileSync(path, 'utf8'));
  const tol = {value: 1e-9, t90: 1e-6, ...(doc.tolerance || {})};
  const defs = {};
  for (const [name, raw] of Object.entries(doc.eases || {})) {
    defs[name] = SL.normEase(raw);
    counts.eases++;
    roundtrip(SL, name, defs[name], failures, counts);
  }
  for (const c of doc.cases || []) {
    const got = SL.easeValue(defs[c.ease], c.u);
    counts.value++;
    if (!(Math.abs(got - c.value) <= tol.value)) failures.push({check: 'value', ease: c.ease, u: c.u, want: c.value, got});
  }
  for (const [name, want] of Object.entries(doc.t90 || {})) {
    const got = SL.t90(defs[name]);
    counts.t90++;
    if (!(Math.abs(got - want) <= tol.t90)) failures.push({check: 't90', ease: name, want, got});
  }
}

export function pickVectors(explicit, explicitT90) {
  if (explicit) {
    const doc = JSON.parse(readFileSync(resolve(explicit), 'utf8'));
    const shared = Array.isArray(doc.eases);
    return {path: resolve(explicit), t90: explicitT90 ? resolve(explicitT90) : null, source: shared ? 'given-shared' : 'given-local'};
  }
  if (existsSync(SHARED)) return {path: SHARED, t90: SHARED_T90, source: 'shared'};
  return {path: LOCAL, t90: null, source: 'local'};
}

/** Run every check. Returns {ok, source, path, counts, failures, lint}. */
export function runVectors({vectors, t90} = {}) {
  const picked = pickVectors(vectors, t90);
  const SL = loadMath();
  const failures = [];
  const counts = {eases: 0, value: 0, t90: 0, start: 0, ae: 0, spline: 0, roundtrip: 0};
  const lint = lintEs3(readFileSync(MATH, 'utf8'));
  for (const p of lint) failures.push({check: 'es3', ...p});
  if (picked.source === 'shared' || picked.source === 'given-shared') checkShared(SL, picked.path, picked.t90, failures, counts);
  else checkLocal(SL, picked.path, failures, counts);
  return {ok: failures.length === 0 && counts.value > 0, source: picked.source, path: picked.path, t90: picked.t90,
    counts, failures, lint};
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const args = process.argv.slice(2);
  const json = args.includes('--json');
  const opt = (flag) => {
    const i = args.indexOf(flag);
    return i >= 0 ? args[i + 1] : undefined;
  };
  const res = runVectors({vectors: opt('--vectors'), t90: opt('--t90')});
  if (json) {
    process.stdout.write(JSON.stringify(res) + '\n');
  } else {
    const c = res.counts;
    console.log(`ExtendScript vectors (${res.source}: ${res.path})`);
    console.log(`  ${c.eases} eases: ${c.value} values, ${c.t90} T-levels, ${c.start} landings, ${c.ae} AE eases, ` +
      `${c.spline} spline samples, ${c.roundtrip} AE round trips`);
    for (const f of res.failures.slice(0, 20)) console.log('  FAIL ' + JSON.stringify(f));
    if (res.failures.length > 20) console.log(`  …and ${res.failures.length - 20} more`);
    console.log(res.ok ? 'PASS' : 'FAIL');
  }
  process.exitCode = res.ok ? 0 : 1;
}
