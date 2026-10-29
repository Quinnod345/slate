// Scene scripts, compiled on request. A web scene is plain HTML plus ES modules that import packages by name
// ('three', 'gsap', '@react-three/fiber', '@slate/web'); the local server hands the page each script as one esbuild
// bundle, so a scene needs no build step and no node_modules of its own:
//   - bare imports resolve from the engine's node_modules (one pinned copy of three, GSAP, React for every film);
//   - '@slate/web' is the scene runtime (engine/web/runtime), '@slate/kit/...' the kit (its React-free core lives at
//     '@slate/kit/core'), and '@film/data' the film's own data: the same module a Remotion act reads (engine-core's
//     filmDataSource), so both engines draw from one clock and one brand;
//   - importing an image, model, font or media file gives its local URL (the page loads it from the same server);
//     shaders import as text; CSS imported from a script is added to the page.
// Bundles are cached per file for the life of one render: every page of a render loads identical code.
import fs from 'node:fs';
import path from 'node:path';
import {build} from 'esbuild';

export const SCRIPT_EXTS = ['.js', '.mjs', '.jsx', '.ts', '.tsx'];
const ASSET_RE = /\.(png|jpe?g|webp|gif|avif|svg|glb|gltf|bin|hdr|exr|ktx2|basis|mp4|webm|mov|wav|mp3|m4a|aac|ogg|woff2?|ttf|otf)$/i;
const TEXT_RE = /\.(glsl|vert|frag|vs|fs|wgsl|txt)$/i;

function firstExisting(candidates) {
  for (const c of candidates) {
    try {
      if (fs.statSync(c).isFile()) return c;
    } catch {
      // try the next one
    }
  }
  return null;
}

/** A module path with its extension or directory index filled in (index.ts, .tsx, …). */
function resolveModule(base) {
  return firstExisting([
    base,
    ...['.ts', '.tsx', '.mjs', '.js', '.jsx'].map((e) => base + e),
    ...['index.ts', 'index.tsx', 'index.mjs', 'index.js'].map((f) => path.join(base, f)),
  ]);
}

/**
 * Make a bundler for one render.
 *   runtimeDir   engine/web/runtime ('@slate/web')
 *   kitDir       engine/remotion/kit ('@slate/kit')
 *   nodeModules  engine/node_modules (bare imports)
 *   filmData     () => {contents, resolveDir}: the '@film/data' module's TypeScript source
 *   urlFor       (absPath) => the URL the local server serves that file at, or null
 *   recordSource optional (absPath, actualBytes) observer for a motion-provenance read
 */
export function createBundler({runtimeDir, kitDir, nodeModules, filmData, urlFor, recordSource = null}) {
  const cache = new Map();
  const readSource = (file) => {
    const bytes = fs.readFileSync(file);
    recordSource?.(file, bytes);
    return bytes;
  };

  const plugin = {
    name: 'slate-web',
    setup(b) {
      b.onResolve({filter: /^@slate\/web(\/.*)?$/}, (args) => {
        const sub = args.path.slice('@slate/web'.length).replace(/^\//, '');
        const found = resolveModule(path.join(runtimeDir, sub || 'index'));
        if (!found) return {errors: [{text: `@slate/web has no module "${sub}" (engine/web/runtime).`}]};
        return {path: found};
      });
      b.onResolve({filter: /^@slate\/kit(\/.*)?$/}, (args) => {
        const sub = args.path.slice('@slate/kit'.length).replace(/^\//, '');
        const found = resolveModule(path.join(kitDir, sub || 'index'));
        if (!found) return {errors: [{text: `@slate/kit has no module "${sub}" (engine/remotion/kit).`}]};
        return {path: found};
      });
      b.onResolve({filter: /^@film\/data$/}, () => ({path: 'film-data.ts', namespace: 'slate-film-data'}));
      b.onLoad({filter: /.*/, namespace: 'slate-film-data'}, () => {
        const {contents, resolveDir} = filmData();
        return {contents, resolveDir, loader: 'ts'};
      });
      b.onLoad({filter: ASSET_RE}, (args) => {
        const url = urlFor(args.path);
        if (!url) {
          return {
            errors: [{
              text: `${args.path} is outside the scene folder, the film folder and the brand folder, so the page can't load it. Copy it into the scene folder.`,
            }],
          };
        }
        return {contents: `export default ${JSON.stringify(url)};`, loader: 'js'};
      });
      b.onLoad({filter: TEXT_RE}, (args) => ({contents: readSource(args.path).toString('utf8'), loader: 'text'}));
      b.onLoad({filter: /\.css$/i}, (args) => {
        const css = readSource(args.path).toString('utf8');
        return {
          contents: `const s = document.createElement('style'); s.dataset.slateSource = ${JSON.stringify(path.basename(args.path))}; ` +
            `s.textContent = ${JSON.stringify(css)}; document.head.appendChild(s); export default s;`,
          loader: 'js',
        };
      });
      // Capture the same bytes esbuild consumes, including transitive imports. A metafile followed by a
      // filesystem reread can instead describe a newer module that the page never loaded.
      if (recordSource) b.onLoad({filter: /\.(?:[cm]?[jt]s|jsx|tsx|json)$/i, namespace: 'file'}, (args) => {
        const ext = path.extname(args.path).slice(1).toLowerCase();
        const loader = ext === 'mjs' || ext === 'cjs' ? 'js' : ext === 'mts' || ext === 'cts' ? 'ts' : ext;
        return {contents: readSource(args.path), loader, resolveDir: path.dirname(args.path)};
      });
    },
  };

  async function compile(file) {
    const res = await build({
      entryPoints: [file],
      absWorkingDir: path.dirname(file),
      bundle: true,
      write: false,
      format: 'esm',
      platform: 'browser',
      target: 'es2022',
      jsx: 'automatic',
      jsxImportSource: 'react',
      nodePaths: [nodeModules],
      plugins: [plugin],
      define: {'process.env.NODE_ENV': '"production"'},
      legalComments: 'none',
      charset: 'utf8',
      logLevel: 'silent',
      metafile: true,
    });
    const out = res.outputFiles.find((f) => f.path.endsWith('.js')) ?? res.outputFiles[0];
    return {code: Buffer.from(out.contents), inputs: Object.keys(res.metafile.inputs).length, warnings: res.warnings.length};
  }

  return {
    /** The bundle for one script file: {code: Buffer, inputs, warnings}. Throws esbuild's own error on failure. */
    bundle(file) {
      if (!cache.has(file)) {
        const p = compile(file);
        p.catch(() => {});
        cache.set(file, p);
      }
      return cache.get(file);
    },
  };
}

/** esbuild's error, as one readable line per problem (file:line:col: text). */
export function describeBuildError(err) {
  const errors = Array.isArray(err?.errors) ? err.errors : [];
  if (!errors.length) return String(err?.message ?? err);
  return errors
    .slice(0, 8)
    .map((e) => {
      const loc = e.location ? `${e.location.file}:${e.location.line}:${e.location.column}: ` : '';
      return `${loc}${e.text}`;
    })
    .join('\n');
}
