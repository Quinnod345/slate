// The act markup bench: an act's static markup (react-dom/server's renderToStaticMarkup, no pixels, no browser) at
// every frame and four sub-frame samples, the same film times the motion-blur renderer draws.
//
//   handin.act<N>.markup   no NaN, Infinity or undefined in any style or attribute at any sample: a chart fed a bad
//                          value, a layout divided by zero, a prop that never arrived (the films scanned server-side
//                          renders of their charts for exactly this before rendering pixels: L-kit-D146)
//   with `against: <git ref>`, every sample is also compared with the act as the kit at that ref of Slate's own
//                          repository draws it, so a rendering-only kit change proves itself a no-op in the old mode
//                          without rendering a pixel (L-kit-S010). `against` may instead be a kit folder (an absolute
//                          path to a folder holding a kit's index.ts, such as an installed engine's remotion/kit,
//                          which has no git history). The tilt raster mode's coverage audit joins this when the kit
//                          has the mode.
//
// In Node, 'remotion' answers the bench's frame and the film's size (useCurrentFrame, useVideoConfig), and the kit's
// font gate lets its children through: fonts load in the browser, and the hand-in check's renders hold them to the
// gate there. Everything else is the act's own code and the kit's, exactly as bundled for a render.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {ENGINE_DIR, EngineError, KIT_DIR, NODE_MODULES, run, usage} from './lib/common.mjs';
import {actOf} from './lib/film.mjs';
import {ensureEntry, sceneIndex} from './entry.mjs';
import {esbuildOptions} from './load_act.mjs';

/** Sub-frame samples per frame: the shutter the check reads at (four, as the films' bench did). */
export const MARKUP_SAMPLES = 4;
const REMOTION_ESM = path.join(NODE_MODULES, 'remotion', 'dist', 'esm', 'index.mjs');
/** What a style or attribute must never hold. */
const BAD_VALUE = /NaN|-?Infinity|undefined/;

function gate(name, status, summary, extra = {}) {
  return {
    gate: name,
    status,
    summary,
    evidence: extra.evidence ?? [],
    engine: extra.engine ?? {name: 'slate engine', version: 'unknown'},
    inputs: extra.inputs ?? {},
    checked_at: new Date().toISOString(),
    details: {...(extra.details ?? {}), stage: 'scenes'},
  };
}

/** esbuild's plugin for markup in Node: 'remotion' reads the bench's clock; the kit's FontGate passes its children. */
function markupPlugin(kitDir, tmp, tag) {
  const remotionShim = path.join(tmp, `remotion-${tag}.mjs`);
  fs.writeFileSync(
    remotionShim,
    [
      `export * from ${JSON.stringify(REMOTION_ESM)};`,
      'export const useCurrentFrame = () => globalThis.__slateMarkup.frame;',
      'export const useVideoConfig = () => globalThis.__slateMarkup.config;',
      'export const delayRender = () => 0;',
      'export const continueRender = () => {};',
      'export const cancelRender = (e) => { throw e instanceof Error ? e : new Error(String(e)); };',
      '',
    ].join('\n'),
  );
  const fontsReal = ['fonts.tsx', 'fonts.ts'].map((f) => path.join(kitDir, f)).find((f) => fs.existsSync(f));
  const fontsShim = path.join(tmp, `fonts-${tag}.tsx`);
  if (fontsReal) {
    fs.writeFileSync(fontsShim, `export * from ${JSON.stringify(fontsReal)};\nexport function FontGate({children}: {children?: unknown}) {\n  return children as never;\n}\n`);
  }
  const kit = path.resolve(kitDir) + path.sep;
  return {
    name: 'slate-markup',
    setup(b) {
      b.onResolve({filter: /^remotion$/}, () => ({path: remotionShim}));
      if (fontsReal) b.onResolve({filter: /^\.\/fonts$/}, (a) => ((path.resolve(a.resolveDir) + path.sep).startsWith(kit) && a.importer !== fontsShim ? {path: fontsShim} : undefined));
    },
  };
}

/** Build an act for markup with one kit; returns render(frame, sample) → markup. */
async function markupRenderer(ctx, act, kitDir, tmp, tag) {
  const index = sceneIndex(ctx.dir, act);
  if (!index) throw usage(`Act ${act} isn't built yet: there's no markup to read.`);
  const a = actOf(ctx, act);
  const entry = path.join(tmp, `markup-${tag}.tsx`);
  fs.writeFileSync(
    entry,
    [
      `import * as scene from ${JSON.stringify(index)};`,
      "import React from 'react';",
      "import {renderToStaticMarkup} from 'react-dom/server';",
      `const C = (scene as Record<string, unknown>).default ?? (scene as Record<string, unknown>)[${JSON.stringify(`Act${act}`)}];`,
      "export const hasComponent = typeof C === 'function';",
      'export function render(props: Record<string, unknown>): string {',
      '  return renderToStaticMarkup(React.createElement(C as React.FC<Record<string, unknown>>, props));',
      '}',
      '',
    ].join('\n'),
  );
  const opts = esbuildOptions(ctx);
  const outfile = path.join(tmp, `markup-${tag}.mjs`);
  try {
    await build({...opts, alias: {...opts.alias, '@slate/kit': kitDir}, entryPoints: [entry], outfile, plugins: [...(opts.plugins ?? []), markupPlugin(kitDir, tmp, tag)], metafile: false});
  } catch (e) {
    const first = (e?.errors ?? [])[0];
    throw new EngineError(`Act ${act} doesn't build for the markup bench${tag === 'then' ? ' with the earlier kit' : ''}: ${first ? `${first.location?.file ?? ''}:${first.location?.line ?? ''} ${first.text}` : String(e?.message ?? e).split('\n')[0]}`, {kind: 'refused'});
  }
  const mod = await import(pathToFileURL(outfile).href);
  if (!mod.hasComponent) throw usage(`Act ${act}'s index has no component to draw (a default export, or Act${act}).`);
  const config = {width: ctx.width, height: ctx.height, fps: ctx.fps.value, durationInFrames: a.frames * MARKUP_SAMPLES, id: `Act${act}`, defaultProps: {}, props: {}};
  return (frame, k) => {
    globalThis.__slateMarkup = {frame: frame * MARKUP_SAMPLES + k, config};
    return mod.render({sub: MARKUP_SAMPLES, act, firstFrame: a.frameIn});
  };
}

/** A kit folder given as `against` (an absolute path holding a kit's index.ts), or null when it's a git ref. */
export function kitFolder(against) {
  const p = String(against);
  return path.isAbsolute(p) && fs.existsSync(path.join(p, 'index.ts')) ? p : null;
}

/** The kit as it was at a git ref of the engine's own repository, extracted into `dir` (read-only git). */
export async function kitAtRef(ref, dir) {
  const top = await run('git', ['-C', ENGINE_DIR, 'rev-parse', '--show-toplevel']);
  if (top.code !== 0) throw usage(`against: ${ref} needs the engine's own git checkout, and ${ENGINE_DIR} isn't in one.`, 'Run the markup bench from Slate\'s repository (its engine/ folder), where the kit has a history.');
  const root = top.stdout.trim();
  const rel = path.relative(root, KIT_DIR).split(path.sep).join('/');
  const tar = path.join(dir, 'kit.tar');
  const res = await run('git', ['-C', root, 'archive', '--format=tar', `--output=${tar}`, ref, rel]);
  if (res.code !== 0) throw usage(`Couldn't read the kit at ${ref}: ${res.stderr.trim().split('\n')[0]}`, 'Give a ref the repository has (a commit, a branch, a tag).');
  const out = path.join(dir, 'kit-at-ref');
  fs.mkdirSync(out, {recursive: true});
  const x = await run('tar', ['-xf', tar, '-C', out]);
  if (x.code !== 0) throw usage(`Couldn't unpack the kit at ${ref}: ${x.stderr.trim()}`);
  return path.join(out, rel);
}

/** Every style or attribute value at one sample that holds NaN, Infinity or undefined. */
export function badValues(html) {
  const out = [];
  for (const m of html.matchAll(/\s([a-zA-Z_:][-a-zA-Z0-9_:.]*)="([^"]*)"/g)) {
    if (BAD_VALUE.test(m[2])) out.push(`${m[1]}="${m[2].length > 120 ? `${m[2].slice(0, 117)}…` : m[2]}"`);
  }
  return out;
}

/** Where two markups first differ, with a little of each around it. */
function firstDifference(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  const lo = Math.max(0, i - 50);
  return {at: i, now: a.slice(lo, i + 70), then: b.slice(lo, i + 70)};
}

/**
 * The markup gate for one act. Every frame, four samples each: bad values fail; with `args.against` every sample must
 * also equal the earlier kit's. Returns a gate result.
 */
export async function markupGate(ctx, act, args, name, inputs) {
  const reactVersion = JSON.parse(fs.readFileSync(path.join(NODE_MODULES, 'react', 'package.json'), 'utf8')).version;
  const engine = {name: 'slate markup bench (react-dom/server, no pixels)', version: reactVersion};
  await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), `slate-markup-act${act}-`));
  const a = actOf(ctx, act);
  try {
    let now;
    let then = null;
    try {
      now = await markupRenderer(ctx, act, KIT_DIR, tmp, 'now');
      if (args.against) then = await markupRenderer(ctx, act, kitFolder(args.against) ?? (await kitAtRef(String(args.against), tmp)), tmp, 'then');
    } catch (e) {
      if (!(e instanceof EngineError)) throw e;
      return gate(name, e.kind === 'usage' ? 'UNCHECKED' : 'FAIL', e.message.split('\n')[0], {engine, inputs, details: {against: args.against ?? null}});
    }
    const bad = [];
    const differ = [];
    const errors = [];
    let samples = 0;
    for (let f = 0; f < a.frames; f++) {
      for (let k = 0; k < MARKUP_SAMPLES; k++) {
        let html;
        try {
          html = now(f, k);
        } catch (e) {
          errors.push(`frame ${f} sample ${k}: the act throws while drawing: ${String(e?.message ?? e).split('\n')[0]}`);
          continue;
        }
        samples++;
        for (const v of badValues(html)) if (bad.length < 40) bad.push(`frame ${f} sample ${k}: ${v}`);
        if (then) {
          let old;
          try {
            old = then(f, k);
          } catch (e) {
            errors.push(`frame ${f} sample ${k}: the act throws while drawing with the earlier kit: ${String(e?.message ?? e).split('\n')[0]}`);
            continue;
          }
          if (old !== html && differ.length < 20) {
            const d = firstDifference(html, old);
            differ.push(`frame ${f} sample ${k}: differs at character ${d.at}: now …${d.now}… was …${d.then}…`);
          }
        }
      }
    }
    const details = {frames: a.frames, samples, per_frame: MARKUP_SAMPLES, against: args.against ?? null, tilt_audit: 'not run: this kit has no tilt raster mode yet'};
    if (errors.length) return gate(name, 'FAIL', `${errors.length} sample${errors.length > 1 ? 's' : ''} couldn't be drawn; the first: ${errors[0]}`, {engine, evidence: [...errors.slice(0, 20), ...bad.slice(0, 20)], inputs, details});
    if (bad.length) return gate(name, 'FAIL', `NaN, Infinity or undefined in the markup; the first: ${bad[0]}`, {engine, evidence: bad, inputs, details});
    if (differ.length) return gate(name, 'FAIL', `the act draws differently from the kit at ${args.against}; the first: ${differ[0].slice(0, 300)}`, {engine, evidence: differ, inputs, details});
    const summary = then
      ? `${samples} samples (${a.frames} frames × ${MARKUP_SAMPLES}) clean and identical to the act drawn by the kit at ${args.against}`
      : `${samples} samples (${a.frames} frames × ${MARKUP_SAMPLES}): no NaN, Infinity or undefined in any style or attribute`;
    return gate(name, 'PASS', summary, {engine, inputs, details});
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
    delete globalThis.__slateMarkup;
  }
}
