// Load an act's modules in Node, without rendering anything, so its beats can be read.
//
// esbuild bundles the act with the same '@slate/kit' and '@film/data' aliases the renderer uses, then Node imports the
// result. Everything computed at module level (the act's beats, the kit's word lookups and motion registry) runs
// exactly as it does in the picture, so the events written from it land on the frames the picture uses. That is how
// the films' 22 emitters worked; this is the one.
//
// The act's modules, in scenes/act<N>/:
//   index.*   the picture (default export: the act's component). Loaded too, for its declarations; when it needs the
//             browser to load, the engine falls back to beats.* and events.* alone and says so.
//   beats.*   the beats, React-free, computed at module level with the kit's core (the films' pattern).
//   events.*  the events, when the act keeps them apart from its beats.
// What the engine reads from them (events.* wins, then beats.*, then index.*):
//   events       VisualEvent[] or () => VisualEvent[]
//   silent       a string: why the act has no beats (an act with no beats must say so)
//   contracts    {in?: string, out?: string}: the rest states its first and last frames match
// And from '@slate/kit', when the act uses the kit, by name (engine/remotion/kit/core): the same calls the kit's own
// node/collect.mjs collectAct() makes, on the same module instances the act's modules registered into. The engine
// loads the act itself rather than through collectAct() because it also loads the act's picture (index.*), with media
// and Google Fonts imports stubbed for Node, and acts that don't use the kit at all.
//   word misses     missLabels() | misses()                  → the lookups that fell back to an estimate
//   motion trace    traceMotions({act})                      → rows of the §12 motion trace (timing/motion/actN.json)
//   kit beats       actEvents(act)                           → beats the act registered with defineEvent()
//   event problems  eventProblems()                          → what defineEvent() had to record (emit refuses on them)
//   problems        motionProblems(), unknownShotsInRegistry() → what the motion registry had to assume or refuse
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {EngineError, KIT_DIR, NODE_MODULES} from './lib/common.mjs';
import {filmDataFile, kitHooksModule, kitPresent, sceneBeats, sceneEvents, sceneIndex} from './entry.mjs';

/**
 * The kit functions the engine calls, by name, each with how it's called for act N. Earlier names win; the later
 * ones are the spellings the films used, kept so an older kit still works.
 */
export const KIT_HOOKS = {
  wordMisses: [
    ['missLabels', (fn) => fn()],
    ['misses', (fn) => fn()],
    ['wordMisses', (fn) => fn()],
    ['cMisses', (fn) => fn()],
  ],
  motionTrace: [
    ['traceMotions', (fn, act) => fn({act})],
    ['motionTrace', (fn, act) => fn(act)],
  ],
  kitEvents: [
    ['actEvents', (fn, act) => fn(act)],
    ['registeredEvents', (fn) => fn()],
  ],
  // what defineEvent() had to record: emit-events refuses on these (they're the events gate's, not kit_problems')
  eventProblems: [['eventProblems', (fn) => fn()]],
  problems: [
    ['motionProblems', (fn) => fn()],
    ['unknownShotsInRegistry', (fn) => fn().map((s) => `a move is registered on shot ${s}, which the film doesn't have`)],
  ],
};

// Everything a scene might import that has no meaning in Node: fonts load in the browser, media is never read here.
const EMPTY_LOADERS = Object.fromEntries(
  ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.css', '.woff', '.woff2', '.ttf', '.otf', '.mp4', '.mov', '.webm', '.wav', '.mp3', '.aac', '.m4a', '.glb', '.gltf', '.hdr', '.exr', '.ktx2'].map((e) => [e, 'empty']),
);

/** Google Fonts (and local font files) load in the browser; in Node they're a stub with the same shape. */
const stubFonts = {
  name: 'slate-stub-fonts',
  setup(b) {
    b.onResolve({filter: /^@remotion\/(google-fonts|fonts)(\/.*)?$/}, (a) => ({path: a.path, namespace: 'slate-stub'}));
    b.onLoad({filter: /.*/, namespace: 'slate-stub'}, () => ({
      contents:
        'export const loadFont = () => ({fontFamily: "sans-serif", fonts: {}, unicodeRanges: {}, waitUntilDone: () => Promise.resolve()});\n' +
        'export const getInfo = () => ({fontFamily: "sans-serif", fonts: {}});\nexport const getAvailableFonts = () => [];\nexport default {};\n',
      loader: 'js',
    }));
  },
};

export function esbuildOptions(ctx, extra = {}) {
  const alias = {'@film/data': filmDataFile(ctx.dir)};
  if (kitPresent()) alias['@slate/kit'] = KIT_DIR;
  return {
    bundle: true,
    platform: 'node',
    format: 'esm',
    target: 'node20',
    jsx: 'automatic',
    loader: {'.json': 'json', '.svg': 'text', '.txt': 'text', ...EMPTY_LOADERS},
    alias,
    nodePaths: [NODE_MODULES],
    define: {'process.env.NODE_ENV': '"production"'},
    plugins: [stubFonts],
    // bundled CommonJS that calls require() on a Node built-in needs a real require in an ES module
    banner: {js: "import {createRequire as __slateRequire} from 'module'; const require = __slateRequire(import.meta.url);"},
    logLevel: 'silent',
    absWorkingDir: ctx.dir,
    metafile: true,
    ...extra,
  };
}

/** The first hook the kit has, bound to act N: {name, fn: () => result}, or null. */
const firstHook = (mod, list, act) => {
  for (const [name, call] of list) {
    if (typeof mod?.[name] === 'function') return {name, fn: () => call(mod[name], act)};
  }
  return null;
};

/** Every problem list the kit keeps (each hook that exists), as one list of strings. */
const allProblems = (mod, act) => {
  const found = KIT_HOOKS.problems.filter(([name]) => typeof mod?.[name] === 'function');
  if (!found.length) return null;
  return {
    names: found.map(([name]) => name),
    fn: () => found.flatMap(([name, call]) => (call(mod[name], act) ?? []).map((p) => (typeof p === 'string' ? p : JSON.stringify(p)))),
  };
};

function formatBuildError(e, ctx) {
  const errors = e?.errors ?? [];
  if (!errors.length) return String(e?.message ?? e);
  return errors
    .slice(0, 8)
    .map((x) => (x.location ? `${path.relative(ctx.dir, path.resolve(ctx.dir, x.location.file))}:${x.location.line}:${x.location.column}: ${x.text}` : x.text))
    .join('\n');
}

/** Does any film file in this bundle import something that resolved into the kit? */
function usesKit(metafile, ctx) {
  const kit = path.resolve(KIT_DIR) + path.sep;
  return Object.keys(metafile.inputs).some((p) => path.resolve(ctx.dir, p).startsWith(kit));
}

async function bundleAndImport(ctx, act, tmp, files, tag) {
  const build1 = async (withKit) => {
    const lines = [];
    const names = [];
    for (const [name, file] of Object.entries(files)) {
      if (file) {
        lines.push(`import * as ${name} from ${JSON.stringify(file)};`);
        names.push(name);
      } else {
        lines.push(`const ${name} = {};`);
        names.push(name);
      }
    }
    lines.push(withKit ? `import * as kit from '${kitHooksModule()}';` : 'const kit = {};');
    lines.push(`export {${[...names, 'kit'].join(', ')}};`, '');
    const entry = path.join(tmp, `entry-${tag}-${withKit ? 'kit' : 'plain'}.mjs`);
    fs.writeFileSync(entry, lines.join('\n'));
    const outfile = path.join(tmp, `out-${tag}-${withKit ? 'kit' : 'plain'}.mjs`);
    try {
      const res = await build({...esbuildOptions(ctx), entryPoints: [entry], outfile});
      return {outfile, metafile: res.metafile};
    } catch (e) {
      throw new EngineError(`Act ${act}'s modules don't build:\n${formatBuildError(e, ctx)}`, {
        kind: 'refused',
        hint: `Fix it, then run \`slate check ${act}\` for the full type check.`,
        details: {stage: 'build'},
      });
    }
  };
  // First without the kit, to learn whether the act uses it; the kit is only loaded (and run) when it does.
  let built = await build1(false);
  const kitUsed = kitPresent() && usesKit(built.metafile, ctx);
  if (kitUsed && kitHooksModule()) built = await build1(true);
  try {
    const mod = await import(pathToFileURL(built.outfile).href);
    return {mod, metafile: built.metafile, kitUsed};
  } catch (e) {
    throw new EngineError(`Act ${act}'s module throws while loading: ${e?.message ?? e}`, {
      kind: 'refused',
      hint: 'Word lookups and beats run at module level: they must never throw (a throw there breaks every composition).',
      details: {stage: 'load', stack: String(e?.stack ?? '').split('\n').slice(0, 6)},
    });
  }
}

/**
 * Load act N. Returns {act, index, beatsFile, eventsFile, scene, beats, extra, kit, usesKit, inputs, hooks,
 * indexLoaded, indexError}. Throws EngineError(kind 'refused') when the act's modules can't be built or throw while
 * loading, with the file and line when esbuild has them (a throw at module load breaks every composition in a bundle).
 */
export async function loadAct(ctx, act) {
  const index = sceneIndex(ctx.dir, act);
  if (!index) return {act, index: null, missing: true};
  const beatsFile = sceneBeats(ctx.dir, act);
  const eventsFile = sceneEvents(ctx.dir, act);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), `slate-act${act}-`));
  try {
    let loaded;
    let indexLoaded = true;
    let indexError = null;
    try {
      loaded = await bundleAndImport(ctx, act, tmp, {scene: index, beats: beatsFile, extra: eventsFile}, 'all');
    } catch (e) {
      // The picture may need the browser to load; its beats must not. Try them alone before giving up. A module that
      // doesn't even build is broken for the renderer too, so that never falls back.
      if (!(beatsFile || eventsFile) || !(e instanceof EngineError) || e.details?.stage !== 'load') throw e;
      indexLoaded = false;
      indexError = e.message.split('\n')[0];
      loaded = await bundleAndImport(ctx, act, tmp, {scene: null, beats: beatsFile, extra: eventsFile}, 'beats');
    }
    const {mod, metafile, kitUsed} = loaded;
    const inputs = Object.keys(metafile.inputs)
      .map((p) => path.resolve(ctx.dir, p))
      .filter((p) => p.startsWith(path.join(ctx.dir, 'scenes') + path.sep))
      .map((p) => path.relative(ctx.dir, p))
      .sort();
    const kit = mod.kit ?? {};
    return {
      act,
      index,
      beatsFile,
      eventsFile,
      scene: mod.scene ?? {},
      beats: mod.beats ?? {},
      extra: mod.extra ?? {},
      kit,
      usesKit: kitUsed,
      inputs,
      indexLoaded,
      indexError,
      hooks: {
        wordMisses: firstHook(kit, KIT_HOOKS.wordMisses, act),
        motionTrace: firstHook(kit, KIT_HOOKS.motionTrace, act),
        kitEvents: firstHook(kit, KIT_HOOKS.kitEvents, act),
        eventProblems: firstHook(kit, KIT_HOOKS.eventProblems, act),
        problems: allProblems(kit, act),
      },
    };
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
  }
}

/** The act's own declarations, wherever they are: events.* wins, then beats.*, then index.*. */
export function declarations(loaded) {
  const sources = [loaded.extra, loaded.beats, loaded.scene].filter(Boolean);
  const pick = (...names) => {
    for (const src of sources) for (const n of names) if (src && n in src && src[n] !== undefined) return src[n];
    return undefined;
  };
  return {events: pick('events', 'visualEvents'), silent: pick('silent'), contracts: pick('contracts') ?? null};
}
