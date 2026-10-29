// The determinism check: any frame rendered on its own is bit-identical to the same frame rendered in sequence
// (L-render-D004). Chunked renders, patches and stills all draw frames out of order, so a scene that carries state from
// one frame to the next (a counter at module level, a ref that accumulates, a random number drawn as it goes) draws a
// different picture depending on what was drawn before it: a patch then doesn't match the act it's spliced into.
//
//   handin.act<N>.determinism   the act's first frames (up to 36) rendered in sequence in one page, and four of them
//                               each rendered alone in a fresh page, all on software GL (bit-exact run to run):
//                               every pair identical, or FAIL naming the first frame that differs
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {renderFrames, selectComposition} from '@remotion/renderer';
import {actOf} from './lib/film.mjs';
import {usage} from './lib/common.mjs';
import {mixedScenesForAct} from './lib/shot_scenes.mjs';
import {browserExecutable, chromiumOptions, renderTimeoutMs} from './browser.mjs';
import {useBundle} from './bundle.mjs';
import {pixelHash, renderStills} from './stills.mjs';

/** How many of the act's first frames are rendered in sequence, at most. */
export const SEQUENCE_FRAMES = 36;

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

/** The frames checked alone: the sequence's first and last and two between, each once. */
export function determinismPicks(n) {
  return [...new Set([0, Math.floor((n - 1) / 3), Math.floor((2 * (n - 1)) / 3), n - 1])].filter((f) => f >= 0 && f < n).sort((a, b) => a - b);
}

/** The determinism gate for one act. `bundles` is the check run's shared bundle, when it has one. */
export async function determinismGate(ctx, act, args, name, inputs, bundles = null) {
  const engine = {name: 'remotion renderFrames (in sequence) and renderStill (alone), swangle', version: 'swangle'};
  const a = actOf(ctx, act);
  const scene = args.shot === undefined ? null : mixedScenesForAct(ctx, act).find((sc) => sc.shot === String(args.shot) && sc.engine === 'remotion');
  if (args.shot !== undefined && !scene) throw usage('No Remotion replacement for the determinism shot.');
  const first = scene ? scene.frames[0] : 0;
  const length = scene ? scene.frames[1] - scene.frames[0] + 1 : a.frames;
  const n = Math.max(1, Math.min(length, Number(args.determinism_frames ?? SEQUENCE_FRAMES)));
  const last = first + n - 1;
  const picks = determinismPicks(n).map((frame) => first + frame);
  const comp = scene?.comp ?? `Act${act}`;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), `slate-determinism-act${act}-`));
  const own = bundles ? null : await useBundle(ctx, {brand_path: args.brand_path ?? null});
  const b = bundles ? await bundles.get() : own;
  try {
    const gl = 'swangle';
    const inputProps = {sub: 1};
    const composition = await selectComposition({serveUrl: b.serve_url, id: comp, inputProps, chromiumOptions: chromiumOptions(gl), browserExecutable: browserExecutable(), timeoutInMilliseconds: renderTimeoutMs(), logLevel: 'error'});
    // in sequence: one page draws frames 0…n−1 in order, so anything a frame leaves behind reaches the next
    const seq = new Map();
    await renderFrames({
      composition,
      serveUrl: b.serve_url,
      inputProps,
      outputDir: null,
      imageFormat: 'png',
      frameRange: [first, last],
      concurrency: 1,
      chromiumOptions: chromiumOptions(gl),
      browserExecutable: browserExecutable(),
      timeoutInMilliseconds: renderTimeoutMs(),
      logLevel: 'error',
      onStart: () => {},
      onFrameUpdate: () => {},
      onFrameBuffer: async (buffer, frame) => {
        if (!picks.includes(frame)) return;
        const file = path.join(tmp, `seq_f${String(frame).padStart(5, '0')}.png`);
        fs.writeFileSync(file, buffer);
        seq.set(frame, file);
      },
    });
    // alone: each picked frame in a page of its own
    const alone = await renderStills(ctx, b, picks.map((f) => ({comp, frame: f, name: `alone_f${String(f).padStart(5, '0')}.png`})), {outDir: tmp, gl, scale: 1, sub: 1, pixels: true});
    const differ = [];
    const same = [];
    for (const s of alone) {
      const file = seq.get(s.frame);
      if (!file) {
        differ.push(`frame ${s.frame}: the sequence never drew it`);
        continue;
      }
      if ((await pixelHash(file)) === s.pixels) same.push(s.frame);
      else differ.push(`frame ${s.frame}: drawn alone it differs from the same frame drawn in sequence (frames ${first}–${last} in one page)`);
    }
    const details = {sequence: [first, last], frames: picks, gl, ...(scene ? {shot: scene.shot, comp} : {})};
    if (differ.length) {
      return gate(name, 'FAIL', `${differ[0]}: the act carries state from frame to frame, so a chunk, a patch or a still draws it differently`, {engine, evidence: [...differ, 'make every frame a pure function of film time: no state kept between frames (module-level counters, refs that accumulate, random numbers drawn as it goes)'], inputs, details});
    }
    return gate(name, 'PASS', `frames ${picks.join(', ')} drawn alone match the same frames drawn in sequence (${first}–${last}), bit for bit on ${gl}`, {engine, inputs, details});
  } finally {
    if (own) own.release();
    fs.rmSync(tmp, {recursive: true, force: true});
  }
}
