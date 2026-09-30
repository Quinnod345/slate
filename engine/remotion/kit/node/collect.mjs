// The kit side of `slate emit-events`, `slate check` and the motion trace: load an act's beats modules in Node exactly
// as the picture reads them, and collect what they defined. The engine's cli.mjs calls collectAct() and writes the
// files (timing/visual_events/actN.json, timing/motion/actN.json); this module writes nothing into a film.
//
// A beats module imports from '@slate/kit/core' (or '@slate/kit') and defines its beats at module level with word(),
// defineEvent() and defineMotion(). Lookups never throw, so a half-written act still loads and its misses are listed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export const KIT_DIR = path.resolve(here, '..');
export const ENGINE_DIR = path.resolve(KIT_DIR, '../..');

/**
 * Collect an act: {act, events (or {silent, reason}), eventProblems, trace, motionProblems, misses, missLabels, ok}.
 * `beats` are the act's beats module paths; `filmData` the module `@film/data` points at (JSON or a generated TS
 * module). ok is true when nothing was missed and nothing was refused.
 */
export async function collectAct({act, beats = [], filmData, silentReason, kitDir = KIT_DIR}) {
  if (!filmData) throw new Error('collectAct needs filmData: the path of the module `@film/data` points at');
  const {build} = await import('esbuild');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-collect-'));
  try {
    const entry = path.join(tmp, 'entry.ts');
    const imports = beats.map((b, i) => `import * as b${i} from ${JSON.stringify(path.resolve(b))};`).join('\n');
    fs.writeFileSync(
      entry,
      `import * as K from '@slate/kit/core';
${imports}
export const loaded = [${beats.map((_, i) => `Object.keys(b${i}).length`).join(', ')}];
export function collect(act, silent) {
  const events = K.actEvents(act, silent);
  const list = Array.isArray(events) ? events : [];
  return {
    act,
    events,
    eventProblems: [...K.eventProblems(), ...K.validateEvents(list, act)],
    trace: K.traceMotions({act}),
    motionProblems: K.motionProblems(),
    misses: K.misses(),
    missLabels: K.missLabels(),
  };
}
`,
    );
    const out = path.join(tmp, 'collect.mjs');
    await build({
      entryPoints: [entry],
      bundle: true,
      platform: 'node',
      format: 'esm',
      target: 'node20',
      outfile: out,
      jsx: 'automatic',
      loader: {'.json': 'json'},
      alias: {'@slate/kit': kitDir, '@film/data': path.resolve(filmData)},
      nodePaths: [path.join(ENGINE_DIR, 'node_modules')],
      logLevel: 'error',
    });
    const mod = await import(pathToFileURL(out).href);
    const r = mod.collect(act, silentReason);
    const noEvents = Array.isArray(r.events) && r.events.length === 0;
    return {...r, loaded: mod.loaded, ok: r.misses.length === 0 && r.eventProblems.length === 0 && r.motionProblems.filter((p) => !/neutral law/.test(p)).length === 0 && !noEvents};
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  // node remotion/kit/node/collect.mjs <act> <film-data module> <beats.ts> [<beats.ts> …]
  const [act, filmData, ...beats] = process.argv.slice(2);
  if (!act || !filmData) {
    console.error('usage: node remotion/kit/node/collect.mjs <act> <film-data module> <beats.ts> [<beats.ts> …]');
    process.exit(2);
  }
  collectAct({act: /^\d+$/.test(act) ? Number(act) : act, beats, filmData})
    .then((r) => {
      console.log(JSON.stringify(r));
      process.exit(r.ok ? 0 : 1);
    })
    .catch((e) => {
      console.error(String(e?.stack ?? e));
      process.exit(1);
    });
}
