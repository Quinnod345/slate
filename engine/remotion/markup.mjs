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
//                          which has no git history). The comparison draws the act in hint mode (the render's input
//                          prop raster: "hint"): an act's hint-mode drawing is its reference.
//   the tilt coverage audit: the act is drawn again with raster: "tilt"; when it draws in tilt mode at all (the kit's
//                          RasterModeProvider), every sample is held to the tilt mode's placement rules: every text
//                          tilted inside the innermost layer that holds it (a 3D transform, a filter, an opacity below
//                          1, a blend mode or a raster hint), no tilt holding a shape outside a Card's face, no tilted
//                          plane in a 3D world or under a clip-path, and no raster hints (will-change) at all. A text
//                          an act leaves untilted on purpose says so: data-slate-untilted="<why>" on its element.
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
import {mixedScenesForAct} from './lib/shot_scenes.mjs';
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
      'export const getInputProps = () => (globalThis.__slateMarkup && globalThis.__slateMarkup.inputProps) || {};',
      'export const cancelRender = (e) => { throw e instanceof Error ? e : new Error(String(e)); };',
      '',
    ].join('\n'),
  );
  const fontsReal = ['fonts.tsx', 'fonts.ts'].map((f) => path.join(kitDir, f)).find((f) => fs.existsSync(f));
  const fontsShim = path.join(tmp, `fonts-${tag}.tsx`);
  if (fontsReal) {
    fs.writeFileSync(fontsShim, `export * from ${JSON.stringify(fontsReal)};\nexport function FontGate({children}: {children?: unknown}) {\n  return children as never;\n}\n`);
  }
  // real paths on both sides: a kit unpacked under a temp folder reached through a symlink (macOS's /var is
  // /private/var) is still the kit, or its FontGate would hold back every child and the act would draw nothing
  const real = (p) => {
    try {
      return fs.realpathSync(p);
    } catch {
      return path.resolve(p);
    }
  };
  const kit = real(path.resolve(kitDir)) + path.sep;
  return {
    name: 'slate-markup',
    setup(b) {
      b.onResolve({filter: /^remotion$/}, () => ({path: remotionShim}));
      if (fontsReal) b.onResolve({filter: /^\.\/fonts$/}, (a) => ((real(path.resolve(a.resolveDir)) + path.sep).startsWith(kit) && real(a.importer) !== real(fontsShim) ? {path: fontsShim} : undefined));
    },
  };
}

/** Build an act for markup with one kit; returns render(frame, sample) → markup. */
async function markupRenderer(ctx, act, kitDir, tmp, tag, scene = null) {
  const index = scene?.index ?? sceneIndex(ctx.dir, act);
  if (!index) throw usage(`Act ${act} isn't built yet: there's no markup to read.`);
  const a = actOf(ctx, act);
  const entry = path.join(tmp, `markup-${tag}.tsx`);
  fs.writeFileSync(
    entry,
    [
      `import * as scene from ${JSON.stringify(index)};`,
      "import React from 'react';",
      "import {renderToStaticMarkup} from 'react-dom/server';",
      `const C = (scene as Record<string, unknown>).default ?? (scene as Record<string, unknown>)[${JSON.stringify(scene ? 'Shot' + scene.shot : `Act${act}`)}];`,
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
  const config = {width: ctx.width, height: ctx.height, fps: ctx.fps.value, durationInFrames: a.frames * MARKUP_SAMPLES, id: scene?.comp ?? `Act${act}`, defaultProps: {}, props: {}};
  return (frame, k, inputProps = {}) => {
    globalThis.__slateMarkup = {frame: frame * MARKUP_SAMPLES + k, config, inputProps};
    return mod.render({sub: MARKUP_SAMPLES, act, firstFrame: a.frameIn});
  };
}

// ---------------------------------------------------------------------------------------------- the tilt coverage audit
const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
const ENTITIES = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0'};
const decode = (t) => t.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => (e[0] === '#' ? String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : Number(e.slice(1))) : ENTITIES[e.toLowerCase()] ?? m));

/** A static markup string (react-dom/server's, well formed) as a tree: {tag, attrs, style, children, parent} and {text}. */
export function parseMarkup(html) {
  const root = {tag: '#root', attrs: {}, style: {}, children: [], parent: null};
  let cur = root;
  for (const m of html.matchAll(/<(\/?)([a-zA-Z][\w:.-]*)([^>]*?)(\/?)>|([^<]+)/g)) {
    if (m[5] !== undefined) {
      cur.children.push({text: decode(m[5]), parent: cur});
      continue;
    }
    const tag = m[2].toLowerCase();
    if (m[1] === '/') {
      let n = cur;
      while (n && n.tag !== tag) n = n.parent;
      if (n && n.parent) cur = n.parent;
      continue;
    }
    const attrs = {};
    for (const a of m[3].matchAll(/([^\s=/]+)(?:="([^"]*)")?/g)) attrs[a[1].toLowerCase()] = decode(a[2] ?? '');
    const node = {tag, attrs, style: parseStyle(attrs.style ?? ''), children: [], parent: cur};
    cur.children.push(node);
    if (m[4] !== '/' && !VOID_TAGS.has(tag)) cur = node;
  }
  return root;
}

/** An inline style as {property: value}, split on the semicolons outside any brackets (a data URL keeps its own). */
export function parseStyle(text) {
  const out = {};
  let depth = 0;
  let start = 0;
  const take = (part) => {
    const i = part.indexOf(':');
    if (i > 0) out[part.slice(0, i).trim().toLowerCase()] = part.slice(i + 1).trim();
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '(') depth++;
    else if (c === ')') depth = Math.max(0, depth - 1);
    else if (c === ';' && depth === 0) {
      take(text.slice(start, i));
      start = i + 1;
    }
  }
  take(text.slice(start));
  return out;
}

const THREE_D = /translate3d|translatez|rotatex|rotatey|rotate3d|perspective\(|matrix3d|scale3d|scalez/i;
const isTilt = (n) => Boolean(n.attrs?.['data-slate-tilt']) || /skewY\(/i.test(n.style?.transform ?? '');
/** What makes an element a layer Chrome may raster on its own: a 3D transform, a filter, opacity below 1, a blend. */
function layerWhy(n) {
  const s = n.style ?? {};
  if (s['will-change'] && s['will-change'] !== 'auto') return 'a raster hint';
  if (s['transform-style'] === 'preserve-3d' || THREE_D.test(s.transform ?? '')) return 'a 3D transform';
  if (s.filter && s.filter !== 'none') return 'a filter';
  if (s['backdrop-filter'] && s['backdrop-filter'] !== 'none') return 'a filter';
  if (s.opacity !== undefined && Number(s.opacity) < 1) return 'an opacity below 1';
  if (s['mix-blend-mode'] && s['mix-blend-mode'] !== 'normal') return 'a blend mode';
  return null;
}
const SHAPE_TAGS = new Set(['svg', 'img', 'canvas', 'video', 'hr']);
/** Whether an element draws a shape of its own (a fill, a border, a shadow, an outline, an SVG or an image). */
function shapeWhy(n) {
  if (SHAPE_TAGS.has(n.tag)) return `an <${n.tag}>`;
  const s = n.style ?? {};
  const bg = s.background ?? s['background-color'] ?? s['background-image'];
  if (bg && !/^(none|transparent|initial|inherit)$/i.test(bg) && !/rgba\([^)]*,\s*0\)$/i.test(bg)) return 'a fill';
  for (const k of ['border', 'border-top', 'border-bottom', 'border-left', 'border-right', 'outline']) {
    if (s[k] && !/^(none|0|0px)$/i.test(s[k]) && !/\b(none|hidden)\b/i.test(s[k])) return 'a border';
  }
  if (s['box-shadow'] && s['box-shadow'] !== 'none') return 'a shadow';
  return null;
}
const hidden = (n) => /^(hidden|collapse)$/i.test(n.style?.visibility ?? '') || n.style?.display === 'none';

/**
 * The tilt mode's placement rules on one sample's markup: findings [{rule, what}] and the counts. A text is checked
 * from its element up: the first tilt it meets must come at or before the first layer boundary (the same element may be
 * both: type moved by its own transform carries the tilt in it); text in an SVG or hidden is skipped, and an element
 * marked data-slate-untilted (with why) excuses everything in it.
 */
export function tiltAudit(html) {
  const root = parseMarkup(html);
  const findings = [];
  const exempt = new Set();
  let texts = 0;
  let tilts = 0;
  const label = (n) => {
    const words = [];
    const walk = (x) => {
      if (words.join(' ').length > 40) return;
      if (x.text !== undefined) {
        if (x.text.trim()) words.push(x.text.trim());
      } else x.children.forEach(walk);
    };
    walk(n);
    const t = words.join(' ').replace(/\s+/g, ' ');
    return t.length > 40 ? `${t.slice(0, 37)}…` : t;
  };
  const visit = (n, ctx) => {
    if (n.text !== undefined) {
      const t = n.text.trim();
      if (!t || ctx.svg || ctx.hidden) return;
      if (ctx.exempt) {
        exempt.add(`"${t.slice(0, 40)}" (${ctx.exempt})`);
        return;
      }
      texts++;
      for (let e = n.parent; e && e.tag !== '#root'; e = e.parent) {
        if (isTilt(e)) return;
        const why = layerWhy(e);
        if (why) {
          findings.push({rule: 'untilted', what: `"${t.slice(0, 40)}" isn't tilted inside its layer (${why} on <${e.tag}>)`});
          return;
        }
      }
      findings.push({rule: 'untilted', what: `"${t.slice(0, 40)}" isn't tilted`});
      return;
    }
    const kind = n.attrs?.['data-slate-tilt'];
    const tilt = isTilt(n);
    if (tilt) tilts++;
    if (n.style?.['will-change'] && n.style['will-change'] !== 'auto') findings.push({rule: 'hint', what: `<${n.tag}> carries a raster hint (will-change: ${n.style['will-change']}) in tilt mode`});
    if (kind === 'plane' && ctx.world) findings.push({rule: 'plane', what: `a tilted plane ("${label(n)}") sits in a 3D world: tilt each text where it sits instead (TiltSpan)`});
    if (kind === 'plane' && ctx.clip) findings.push({rule: 'plane', what: `a tilted plane ("${label(n)}") sits under a clip-path: tilt each text where it sits instead (TiltSpan)`});
    if (ctx.tilt && ctx.tilt !== 'face' && n.tag !== '#root') {
      const why = shapeWhy(n);
      if (why) findings.push({rule: 'shape', what: `a tilt (${ctx.tilt}) holds ${why} on <${n.tag}>${label(n) ? ` ("${label(n)}")` : ''}: tilt type only, never shapes`});
    }
    const next = {
      ...ctx,
      svg: ctx.svg || n.tag === 'svg',
      hidden: ctx.hidden || hidden(n),
      exempt: ctx.exempt || (n.attrs?.['data-slate-untilted'] !== undefined ? n.attrs['data-slate-untilted'] || 'no reason given' : null),
      world: ctx.world || n.style?.['transform-style'] === 'preserve-3d',
      clip: ctx.clip || Boolean(n.style?.['clip-path'] && n.style['clip-path'] !== 'none'),
      // the innermost tilt holding this subtree: a face tilt may hold the face's shapes (faceTilt), any other may not
      tilt: tilt ? kind || 'a skew' : ctx.tilt,
    };
    // a tilt's own element may be a fill only when it's a face; its children are judged against the tilt above them
    if (tilt && kind !== 'face' && n.tag !== '#root') {
      const why = shapeWhy(n);
      if (why) findings.push({rule: 'shape', what: `a tilt (${kind || 'a skew'}) is itself ${why} on <${n.tag}>: tilt type only, never shapes`});
    }
    for (const c of n.children) visit(c, next);
  };
  visit(root, {svg: false, hidden: false, exempt: null, world: false, clip: false, tilt: null});
  return {findings, texts, tilts, exempt: [...exempt]};
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
  const scene = args.shot === undefined ? null : mixedScenesForAct(ctx, act).find((sc) => sc.shot === String(args.shot) && sc.engine === 'remotion');
  if (args.shot !== undefined && !scene) throw usage('No Remotion replacement for the requested markup shot.');
  const first = scene ? scene.frames[0] : 0;
  const last = scene ? scene.frames[1] : a.frames - 1;
  try {
    let now;
    let then = null;
    try {
      now = await markupRenderer(ctx, act, KIT_DIR, tmp, 'now', scene);
      if (args.against) then = await markupRenderer(ctx, act, kitFolder(args.against) ?? (await kitAtRef(String(args.against), tmp)), tmp, 'then', scene);
    } catch (e) {
      if (!(e instanceof EngineError)) throw e;
      return gate(name, e.kind === 'usage' ? 'UNCHECKED' : 'FAIL', e.message.split('\n')[0], {engine, inputs, details: {against: args.against ?? null}});
    }
    const bad = [];
    const differ = [];
    const errors = [];
    let samples = 0;
    const HINT = {raster: 'hint'};
    const TILT = {raster: 'tilt'};
    for (let f = first; f <= last; f++) {
      for (let k = 0; k < MARKUP_SAMPLES; k++) {
        let html;
        try {
          html = now(f, k, HINT);
        } catch (e) {
          errors.push(`frame ${f} sample ${k}: the act throws while drawing: ${String(e?.message ?? e).split('\n')[0]}`);
          continue;
        }
        samples++;
        for (const v of badValues(html)) if (bad.length < 40) bad.push(`frame ${f} sample ${k}: ${v}`);
        if (then) {
          let old;
          try {
            old = then(f, k, HINT);
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
    // the act again in tilt mode: drawn without errors or bad values, and, where it draws any tilt, held to the rules
    const audit = {samples: 0, tilted_samples: 0, texts: 0, findings: 0, exempt: new Set()};
    const findings = [];
    for (let f = first; f <= last; f++) {
      for (let k = 0; k < MARKUP_SAMPLES; k++) {
        let html;
        try {
          html = now(f, k, TILT);
        } catch (e) {
          errors.push(`frame ${f} sample ${k}: the act throws while drawing in tilt mode: ${String(e?.message ?? e).split('\n')[0]}`);
          continue;
        }
        audit.samples++;
        for (const v of badValues(html)) if (bad.length < 40) bad.push(`frame ${f} sample ${k} (tilt mode): ${v}`);
        if (!html.includes('data-slate-tilt')) continue;
        audit.tilted_samples++;
        const r = tiltAudit(html);
        audit.texts += r.texts;
        r.exempt.forEach((x) => audit.exempt.add(x));
        for (const x of r.findings) {
          audit.findings++;
          if (findings.length < 40) findings.push(`frame ${f} sample ${k}: ${x.what}`);
        }
      }
    }
    const tiltAuditDetails = audit.tilted_samples
      ? {run: true, samples: audit.samples, tilted_samples: audit.tilted_samples, texts: audit.texts, findings: audit.findings, exempt: [...audit.exempt].slice(0, 20)}
      : {run: false, why: 'the act draws nothing in tilt mode (no RasterModeProvider): there is nothing to audit'};
    const details = {frames: last - first + 1, frame_range: [first, last], ...(scene ? {shot: scene.shot, comp: scene.comp} : {}), samples, per_frame: MARKUP_SAMPLES, against: args.against ?? null, tilt_audit: tiltAuditDetails};
    if (errors.length) return gate(name, 'FAIL', `${errors.length} sample${errors.length > 1 ? 's' : ''} couldn't be drawn; the first: ${errors[0]}`, {engine, evidence: [...errors.slice(0, 20), ...bad.slice(0, 20)], inputs, details});
    if (bad.length) return gate(name, 'FAIL', `NaN, Infinity or undefined in the markup; the first: ${bad[0]}`, {engine, evidence: bad, inputs, details});
    if (differ.length) return gate(name, 'FAIL', `the act draws differently from the kit at ${args.against}; the first: ${differ[0].slice(0, 300)}`, {engine, evidence: differ, inputs, details});
    if (findings.length) return gate(name, 'FAIL', `the tilt mode's placement rules are broken at ${audit.findings} place${audit.findings > 1 ? 's' : ''}; the first: ${findings[0]}`, {engine, evidence: findings, inputs, details});
    const tiltLine = audit.tilted_samples
      ? `; in tilt mode every text is tilted inside its layer (${audit.texts} text runs over ${audit.tilted_samples} samples${audit.exempt.size ? `, ${audit.exempt.size} left untilted on purpose` : ''}), no tilt holds a shape outside a card's face, no plane sits in a 3D world or under a clip, and nothing is hinted`
      : '';
    const summary = then
      ? `${samples} samples (${a.frames} frames × ${MARKUP_SAMPLES}) clean and identical to the act drawn by the kit at ${args.against}${tiltLine}`
      : `${samples} samples (${a.frames} frames × ${MARKUP_SAMPLES}): no NaN, Infinity or undefined in any style or attribute${tiltLine}`;
    return gate(name, 'PASS', summary, {engine, inputs, details});
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
    delete globalThis.__slateMarkup;
  }
}
