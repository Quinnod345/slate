// check <act>: the hand-in check an act builder runs before handing an act in. Every part is a gate result
// (docs/ARCHITECTURE.md §6); only PASS counts, and a part that can't run is UNCHECKED, never a quiet pass.
//
//   handin.act<N>.typecheck      TypeScript, from the film folder, over the act and everything it imports
//   handin.act<N>.module         the act's module loads in Node without throwing (a throw at load breaks every
//                                composition in a bundle: the films' lesson 2)
//   handin.act<N>.word_misses    the kit's word lookups all found their word (none fell back to an estimate)
//   handin.act<N>.kit_problems   (acts that use the kit) what the kit had to assume or refuse, for a person to read
//   handin.act<N>.banned_words   no banned word (the brand pack's list) in the act's on-screen strings (its sources and
//                                every scene file it imports, JSON and text copy included) or its shots' on-screen text
//   handin.act<N>.events         timing/visual_events/act<N>.json exists, is valid, and matches what the act (with the
//                                kit's registrations and the captures' taps) says now, with no word lookup missing
//   handin.act<N>.contracts      the act's boundary frames are bit-identical to its neighbours' and to the named rest
//                                state, and held still for `hold` frames, so parallel builders meet exactly
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import {EngineError, listFiles, log, refused, sha256FileSync, usage} from './lib/common.mjs';
import {actOf} from './lib/film.mjs';
import {ensureEntry, contractsModule, missingIndexNote, sceneIndex, slateDir} from './entry.mjs';
import {declarations, loadAct} from './load_act.mjs';
import {computeEvents, eventsFile, validateEventsFile, webScene} from './events.mjs';
import {useBundle} from './bundle.mjs';
import {pixelHash, psnr, renderStills} from './stills.mjs';
import {browserLogCollector, fontGate} from './kit_bridge.mjs';

const SOURCE_EXT = /\.(tsx?|jsx?|mjs)$/;

function gate(name, status, summary, extra = {}) {
  return {
    gate: name,
    status,
    summary,
    evidence: extra.evidence ?? [],
    engine: extra.engine ?? {name: 'slate engine', version: 'unknown'},
    inputs: extra.inputs ?? {},
    checked_at: new Date().toISOString(),
    details: extra.details ?? {},
  };
}

function inputsFor(ctx, rels) {
  const out = {};
  for (const rel of rels) {
    const full = path.isAbsolute(rel) ? rel : path.join(ctx.dir, rel);
    const key = path.isAbsolute(rel) ? rel : rel;
    out[key] = fs.existsSync(full) && fs.statSync(full).isFile() ? sha256FileSync(full) : null;
  }
  return out;
}

const actSources = (ctx, act) =>
  listFiles(path.join(ctx.dir, 'scenes', `act${act}`)).filter((f) => SOURCE_EXT.test(f)).map((f) => path.join('scenes', `act${act}`, f));

// ---------------------------------------------------------------- type check

export function typecheck(ctx, act, name) {
  const configPath = path.join(slateDir(ctx.dir), `tsconfig.act${act}.json`);
  const engine = {name: 'typescript', version: ts.version};
  const read = ts.readConfigFile(configPath, ts.sys.readFile);
  if (read.error) return gate(name, 'UNCHECKED', `couldn't read ${path.relative(ctx.dir, configPath)}`, {engine});
  const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, path.dirname(configPath), undefined, configPath);
  const program = ts.createProgram({rootNames: parsed.fileNames, options: parsed.options});
  const diags = [...parsed.errors, ...ts.getPreEmitDiagnostics(program)].filter((d) => d.category === ts.DiagnosticCategory.Error);
  const lines = diags.map((d) => {
    const msg = ts.flattenDiagnosticMessageText(d.messageText, '\n').split('\n')[0];
    if (d.file && d.start !== undefined) {
      const {line, character} = d.file.getLineAndCharacterOfPosition(d.start);
      return `${path.relative(ctx.dir, d.file.fileName)}:${line + 1}:${character + 1} TS${d.code} ${msg}`;
    }
    return `TS${d.code} ${msg}`;
  });
  const files = program.getSourceFiles().map((f) => f.fileName).filter((f) => f.startsWith(path.join(ctx.dir, 'scenes') + path.sep));
  const inputs = inputsFor(ctx, [...files.map((f) => path.relative(ctx.dir, f)), 'tsconfig.json', path.relative(ctx.dir, configPath)]);
  if (lines.length) {
    return gate(name, 'FAIL', `${lines.length} type error${lines.length > 1 ? 's' : ''}; the first: ${lines[0]}`, {engine, evidence: lines.slice(0, 30), inputs, details: {errors: lines.length}});
  }
  return gate(name, 'PASS', `no type errors in ${files.length} scene file${files.length === 1 ? '' : 's'} (strict)`, {engine, inputs, details: {files: files.length}});
}

// ---------------------------------------------------------------- banned words

// Strings that are never on screen: event fields read by the sound engine, styling, ids, media sources.
const SKIP_KEYS = new Set(['note', 'id', 'kind', 'size', 'shot', 'ease', 'easing', 'prop', 'rig', 'className', 'style', 'key', 'src', 'href', 'fontFamily', 'transform', 'transition', 'filter', 'mixBlendMode', 'gesture', 'reason', 'in', 'out']);
const SKIP_ATTRS = new Set(['className', 'style', 'key', 'id', 'src', 'href', 'name']);
const SKIP_CALLEES = /^(console\.\w+|require|import|Error|TypeError|RangeError|staticFile|delayRender|continueRender|cancelRender)$/;

function propName(node, sf) {
  if (!node) return null;
  if (ts.isIdentifier(node) || ts.isStringLiteral(node) || ts.isNumericLiteral(node)) return node.text;
  return node.getText(sf);
}

/** On-screen string candidates in a source file: string literals and JSX text, minus the never-on-screen ones. */
export function onScreenStrings(file, text) {
  const kind = /x$/.test(file) ? ts.ScriptKind.TSX : /\.m?jsx?$/.test(file) ? ts.ScriptKind.JSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const found = [];
  const add = (node, value) => {
    if (!value || !value.trim()) return;
    const {line} = sf.getLineAndCharacterOfPosition(node.getStart(sf));
    found.push({line: line + 1, text: value});
  };
  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node) || ts.isImportTypeNode(node)) return;
    if (ts.isTypeNode(node)) return; // literal types in annotations are never drawn
    // the act's declarations to the engine (why it's silent, which rest states it meets) aren't drawn either
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && ['silent', 'contracts'].includes(node.name.text)) return;
    if (ts.isJsxAttribute(node)) {
      const n = propName(node.name, sf);
      if (SKIP_ATTRS.has(n) || n.startsWith('data-') || n.startsWith('aria-')) return;
    }
    if (ts.isPropertyAssignment(node)) {
      if (SKIP_KEYS.has(propName(node.name, sf))) return;
      visit(node.initializer);
      return;
    }
    if ((ts.isCallExpression(node) || ts.isNewExpression(node)) && SKIP_CALLEES.test(node.expression.getText(sf))) return;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) add(node, node.text);
    else if (ts.isTemplateExpression(node)) {
      add(node, [node.head.text, ...node.templateSpans.map((s) => s.literal.text)].join(' '));
    } else if (ts.isJsxText(node)) add(node, node.text);
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return found;
}

/**
 * On-screen string candidates in a JSON copy file an act imports: every string value except under the never-on-screen
 * keys, each with the line it first appears on. Null when the file doesn't parse.
 */
export function jsonStrings(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  const lines = text.split('\n');
  const lineOf = (s) => {
    const needle = JSON.stringify(s);
    const i = lines.findIndex((l) => l.includes(needle));
    return i >= 0 ? i + 1 : 1;
  };
  const found = [];
  const walk = (v, key) => {
    if (typeof v === 'string') {
      if (!SKIP_KEYS.has(key) && v.trim()) found.push({line: lineOf(v), text: v});
    } else if (Array.isArray(v)) {
      for (const x of v) walk(x, key);
    } else if (v && typeof v === 'object') {
      for (const [k, x] of Object.entries(v)) walk(x, k);
    }
  };
  walk(data, null);
  return found;
}

/** Plain-text copy an act imports (the engine loads .txt as text): each non-empty line is a candidate. */
const textLines = (text) => text.split('\n').map((t, i) => ({line: i + 1, text: t})).filter((s) => s.text.trim());

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function bannedMatcher(words) {
  return words
    .filter((w) => typeof w === 'string' && w.trim())
    .map((w) => ({word: w.trim(), re: new RegExp(`(?<![\\p{L}\\p{N}_])${escapeRe(w.trim()).replace(/\s+/g, '\\s+')}(?![\\p{L}\\p{N}_])`, 'iu')}));
}

/**
 * The files whose strings can reach the screen: the act folder's sources, plus every scene file the act's bundle
 * imports (shared scene modules, scenes/contracts, JSON and text copy). Notes and docs never reach the bundle.
 */
function screenFiles(ctx, act, loaded) {
  const own = actSources(ctx, act);
  const imported = (loaded?.inputs ?? []).filter((f) => SOURCE_EXT.test(f) || /\.(json|txt)$/.test(f));
  return [...new Set([...own, ...imported])].sort();
}

export function bannedWords(ctx, act, words, name, loaded = null) {
  const engine = {name: 'slate banned-words (typescript AST)', version: ts.version};
  const files = screenFiles(ctx, act, loaded);
  const inputs = inputsFor(ctx, [...files, 'timing/shots.json']);
  if (words === undefined || words === null) {
    return gate(name, 'UNCHECKED', "no banned-word list was given (the brand pack couldn't be read)", {engine, inputs});
  }
  const matchers = bannedMatcher(words);
  const hits = [];
  const unreadable = [];
  let strings = 0;
  for (const rel of files) {
    const text = fs.readFileSync(path.join(ctx.dir, rel), 'utf8');
    const list = rel.endsWith('.json') ? jsonStrings(text) : rel.endsWith('.txt') ? textLines(text) : onScreenStrings(rel, text);
    if (list === null) {
      unreadable.push(`${rel} isn't valid JSON, so its strings couldn't be read`);
      continue;
    }
    strings += list.length;
    for (const s of list) for (const m of matchers) if (m.re.test(s.text)) hits.push(`${rel}:${s.line}: "${s.text.trim().slice(0, 120)}" (${m.word})`);
  }
  const a = actOf(ctx, act);
  for (const s of ctx.shots?.shots ?? []) {
    if (Number(s.act) !== a.act || typeof s.on_screen !== 'string') continue;
    strings++;
    for (const m of matchers) if (m.re.test(s.on_screen)) hits.push(`timing/shots.json ${s.id} on_screen: "${s.on_screen.slice(0, 120)}" (${m.word})`);
  }
  if (hits.length) return gate(name, 'FAIL', `${hits.length} banned word${hits.length > 1 ? 's' : ''} on screen; the first: ${hits[0]}`, {engine, evidence: [...hits.slice(0, 40), ...unreadable], inputs});
  if (unreadable.length) return gate(name, 'UNCHECKED', unreadable[0], {engine, evidence: unreadable, inputs, details: {strings, banned: matchers.length}});
  const summary = matchers.length ? `none of the brand's ${matchers.length} banned words in ${strings} on-screen strings` : `the brand bans no words (${strings} on-screen strings read)`;
  return gate(name, 'PASS', summary, {engine, inputs, details: {strings, banned: matchers.length, files: files.length}});
}

// ---------------------------------------------------------------- word misses

export function wordMisses(loaded, name, inputs) {
  const engine = {name: 'slate kit word lookup', version: 'unknown'};
  if (!loaded.usesKit) return gate(name, 'PASS', "the act looks up no words (it doesn't use the kit's clock)", {engine, inputs});
  if (!loaded.hooks.wordMisses) return gate(name, 'UNCHECKED', "the kit doesn't expose its word-miss list (wordMisses()) yet", {engine, inputs});
  const raw = loaded.hooks.wordMisses.fn();
  const list = Array.isArray(raw) ? raw : [];
  const text = list.map((m) => (typeof m === 'string' ? m : JSON.stringify(m)));
  if (text.length) return gate(name, 'FAIL', `${text.length} word${text.length > 1 ? 's' : ''} fell back to an estimate: ${text.slice(0, 5).join(', ')}`, {engine, evidence: text.slice(0, 40), inputs});
  return gate(name, 'PASS', 'every word the act looks up was found in the timings', {engine, inputs});
}

/**
 * What the kit had to assume or refuse while the act loaded (a missing motion token, an ease the brand doesn't name,
 * a move on a shot the film doesn't have). A person reads these: some are fine, some are the bug.
 */
export function kitProblems(loaded, name, inputs) {
  const engine = {name: 'slate kit registries', version: 'unknown'};
  if (!loaded.hooks.problems) return gate(name, 'UNCHECKED', "the kit doesn't expose its problem lists (motionProblems())", {engine, inputs});
  const all = [...new Set(loaded.hooks.problems.fn())];
  // The kit's own collector doesn't hold this one against an act: a brand without a motion law yet gets the kit's
  // neutral law until onboarding writes one. It's listed, not failed.
  const notes = all.filter((p) => /neutral law/.test(p));
  const list = all.filter((p) => !/neutral law/.test(p));
  if (list.length) {
    return gate(name, 'REVIEW', `the kit recorded ${list.length} thing${list.length > 1 ? 's' : ''} it had to assume or refuse; the first: ${list[0]}`, {engine, evidence: [...list, ...notes].slice(0, 40), inputs, details: {from: loaded.hooks.problems.names, notes}});
  }
  const summary = notes.length ? `nothing refused; noted: ${notes[0]}` : `the kit assumed and refused nothing (${loaded.hooks.problems.names.join(', ')})`;
  return gate(name, 'PASS', summary, {engine, evidence: notes, inputs, details: {notes}});
}

// ---------------------------------------------------------------- events

const canonical = (v) => JSON.stringify(v, (k, x) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b))) : x));

export async function eventsGate(ctx, act, loaded, name) {
  const engine = {name: 'slate emit-events', version: 'protocol 1'};
  const file = eventsFile(ctx, act);
  const rel = path.relative(ctx.dir, file);
  const inputs = inputsFor(ctx, [rel, ...(loaded?.inputs ?? [])]);
  if (!fs.existsSync(file)) return gate(name, 'FAIL', `no ${rel}: run \`slate emit-events --act ${act}\``, {engine, inputs});
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return gate(name, 'FAIL', `${rel} isn't valid JSON: ${e.message}`, {engine, inputs});
  }
  const problems = validateEventsFile(ctx, act, data);
  if (problems.length) return gate(name, 'FAIL', `${rel} has ${problems.length} problem${problems.length > 1 ? 's' : ''}: ${problems[0]}`, {engine, evidence: problems.slice(0, 30), inputs});
  if (!loaded || loaded.missing) return gate(name, 'UNCHECKED', `${rel} is valid, but the act's module didn't load, so it can't be compared with the act`, {engine, inputs});
  let now;
  try {
    now = await computeEvents(ctx, act, loaded);
  } catch (e) {
    return gate(name, 'FAIL', `${rel} is valid, but the act's beats don't compute now: ${e.message.split('\n')[0]}`, {engine, inputs});
  }
  for (const f of now.captures.files) inputs[f] = inputsFor(ctx, [f])[f];
  if (now.problems.length) {
    const n = now.problems.length;
    return gate(name, 'FAIL', `${rel} can't be current: the act's beats have ${n} problem${n > 1 ? 's' : ''} now; the first: ${now.problems[0]}`, {engine, evidence: now.problems.slice(0, 30), inputs});
  }
  const want = now.silent ? {silent: true, reason: now.reason} : now.events;
  const have = Array.isArray(data) ? data : {silent: data.silent, reason: data.reason};
  if (canonical(want) !== canonical(have)) {
    return gate(name, 'FAIL', `${rel} is out of date with the act: run \`slate emit-events --act ${act}\``, {engine, inputs, details: {file_beats: Array.isArray(data) ? data.length : 0, act_beats: now.silent ? 0 : now.events.length}});
  }
  return gate(name, 'PASS', now.silent ? `the act is silent: ${now.reason}` : `${now.events.length} beats, valid and current`, {engine, inputs, details: {beats: now.silent ? 0 : now.events.length}});
}

// ---------------------------------------------------------------- contracts

/**
 * Boundaries of act N with its neighbours: [{side, from, to, out, in}], where out/in are the rest-state names the
 * outgoing act ends on and the incoming act starts on (undefined when an act declares none).
 */
async function boundaries(ctx, act, loaded) {
  const idx = ctx.acts.findIndex((a) => a.act === act);
  const mine = declarations(loaded).contracts ?? {};
  const decl = async (n) => {
    if (!sceneIndex(ctx.dir, n)) return {built: false, contracts: {}};
    try {
      return {built: true, contracts: declarations(await loadAct(ctx, n)).contracts ?? {}};
    } catch (e) {
      return {built: true, error: e.message.split('\n')[0], contracts: {}};
    }
  };
  const out = [];
  if (idx > 0) {
    const prev = ctx.acts[idx - 1];
    const p = await decl(prev.act);
    out.push({side: 'in', from: prev.act, to: act, out: p.contracts.out, in: mine.in, neighbour: p});
  } else if (mine.in) {
    out.push({side: 'in', from: null, to: act, out: undefined, in: mine.in, neighbour: null});
  }
  if (idx < ctx.acts.length - 1) {
    const next = ctx.acts[idx + 1];
    const n = await decl(next.act);
    out.push({side: 'out', from: act, to: next.act, out: mine.out, in: n.contracts.in, neighbour: n});
  } else if (mine.out) {
    out.push({side: 'out', from: act, to: null, out: mine.out, in: undefined, neighbour: null});
  }
  return out;
}

/**
 * One bundle for a whole check run: made the first time a contract needs pixels (most acts never do), shared by every
 * act after that, and given back when the run ends (deleted, when this run made it).
 */
export function lazyBundle(ctx, args) {
  let b = null;
  return {
    async get() {
      if (!b) b = await useBundle(ctx, args);
      return b;
    },
    get made() {
      return Boolean(b);
    },
    release() {
      if (b) b.release();
      b = null;
    },
  };
}

export async function contractsGate(ctx, act, loaded, name, args, bundles = null) {
  const engine = {name: 'remotion renderStill + ffmpeg pixel hash', version: 'swangle'};
  const hold = Math.max(1, Number(args.hold ?? 6));
  const gl = args.contract_gl ?? 'swangle';
  const list = await boundaries(ctx, act, loaded);
  const problems = [];
  const unchecked = [];
  const passed = [];
  const skipped = [];
  const renders = [];
  const logs = browserLogCollector();
  let fonts = null;
  const contractComps = new Set();
  if (contractsModule(ctx.dir)) {
    const info = await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
    for (const c of info.contracts) contractComps.add(c);
  }
  for (const bd of list) {
    const label = bd.side === 'in' ? `act ${bd.from ?? '(start)'} → act ${bd.to}` : `act ${bd.from} → act ${bd.to ?? '(end)'}`;
    const myName = bd.side === 'in' ? bd.in : bd.out;
    const theirName = bd.side === 'in' ? bd.out : bd.in;
    if (!myName && !theirName) {
      skipped.push(`${label}: no rest state declared on either side (not a pixel contract)`);
      continue;
    }
    if (bd.neighbour === null) {
      problems.push(`${label}: act ${act} declares "${myName}" at the film's ${bd.side === 'in' ? 'start' : 'end'}, where there is no neighbour to meet`);
      continue;
    }
    if (bd.neighbour.built && !bd.neighbour.error && myName !== theirName) {
      problems.push(`${label}: act ${bd.from} ends on ${JSON.stringify(bd.out ?? null)} but act ${bd.to} starts on ${JSON.stringify(bd.in ?? null)}`);
      continue;
    }
    const name_ = myName ?? theirName;
    const neighbourReady = bd.neighbour.built && !bd.neighbour.error;
    if (!myName) {
      problems.push(`${label}: act ${bd.side === 'in' ? bd.from : bd.to} declares "${theirName}" here, but act ${act} doesn't`);
      continue;
    }
    if (!neighbourReady && !contractComps.has(name_)) {
      unchecked.push(`${label}: act ${bd.side === 'in' ? bd.from : bd.to} isn't ${bd.neighbour.error ? 'loading' : 'built'} yet and scenes/contracts has no ${name_} to compare with`);
      continue;
    }
    renders.push({...bd, name: name_, label, neighbourReady});
  }
  if (renders.length) {
    const outDir = path.join(ctx.dir, 'qa', 'contracts', `act${act}`);
    fs.rmSync(outDir, {recursive: true, force: true});
    const own = bundles ? null : lazyBundle(ctx, args);
    const b = await (bundles ?? own).get();
    try {
      for (const r of renders) {
        const me = actOf(ctx, act);
        const myFrames = r.side === 'in' ? [...Array(Math.min(hold, me.frames)).keys()] : [...Array(Math.min(hold, me.frames)).keys()].map((i) => me.frames - Math.min(hold, me.frames) + i);
        const specs = myFrames.map((f) => ({comp: `Act${act}`, frame: f}));
        if (r.neighbourReady) {
          const n = actOf(ctx, r.side === 'in' ? r.from : r.to);
          specs.push({comp: `Act${n.act}`, frame: r.side === 'in' ? n.frames - 1 : 0});
        }
        if (contractComps.has(r.name)) specs.push({comp: `Contract-${r.name}`, frame: 0});
        const stills = await renderStills(ctx, b, specs, {outDir, gl, scale: 1, sub: 1, pixels: true, logs});
        const mineStills = stills.slice(0, myFrames.length);
        const edge = r.side === 'in' ? mineStills[0] : mineStills.at(-1);
        const ok = [];
        let held = true;
        for (const s of mineStills) {
          if (s.pixels !== edge.pixels) {
            held = false;
            problems.push(`${r.label}: act ${act} frame ${s.frame} differs from its boundary frame ${edge.frame}: the rest state must hold still for ${hold} frames`);
          }
        }
        const others = stills.slice(myFrames.length);
        for (const o of others) {
          if (o.pixels === edge.pixels) ok.push(`${o.comp}:${o.frame}`);
          else {
            const db = await psnr(edge.file, o.file);
            problems.push(`${r.label}: ${edge.comp}:${edge.frame} and ${o.comp}:${o.frame} differ (PSNR ${db === Infinity ? '∞' : db?.toFixed(2)} dB); "${r.name}" must be bit-identical`);
          }
        }
        if (!others.length) unchecked.push(`${r.label}: nothing to compare act ${act}'s "${r.name}" frames with`);
        else if (held && ok.length === others.length) passed.push(`${r.label}: "${r.name}" matches ${ok.join(' and ')} bit for bit, held ${myFrames.length} frames`);
      }
      fonts = await fontGate(logs.lines);
    } finally {
      if (own) own.release();
    }
  }
  const inputs = inputsFor(ctx, [...actSources(ctx, act), 'timing/shots.json']);
  const evidence = [...problems, ...unchecked, ...passed, ...skipped];
  const details = {hold, gl, boundaries: list.length, checked: passed.length, skipped: skipped.length};
  if (problems.length) return {gate: gate(name, 'FAIL', problems[0], {engine, evidence, inputs, details}), fonts};
  if (unchecked.length) return {gate: gate(name, 'UNCHECKED', unchecked[0], {engine, evidence, inputs, details}), fonts};
  if (!passed.length) return {gate: gate(name, 'PASS', list.length ? 'no pixel contracts at this act\'s boundaries' : 'the film has one act: no boundaries', {engine, evidence, inputs, details}), fonts};
  return {gate: gate(name, 'PASS', passed.join('; '), {engine, evidence, inputs, details}), fonts};
}

// ---------------------------------------------------------------- the check

/**
 * The check for several acts in one run ({acts: [N, …]}): one bundle at most, shared by every act's contracts.
 * Returns {acts: [{act, gates, status, passed}], gates (all of them), status (the worst), passed}.
 */
export async function checkActs(ctx, args) {
  const list = args.acts.map((a) => actOf(ctx, a).act);
  if (!list.length) throw usage('No acts to check: "acts" is empty.');
  const bundles = lazyBundle(ctx, args);
  const acts = [];
  try {
    for (const act of list) acts.push(await checkAct(ctx, {...args, act}, {bundles}));
  } finally {
    bundles.release();
  }
  const gates = acts.flatMap((a) => a.gates);
  const status = ['FAIL', 'STALE', 'UNCHECKED', 'REVIEW', 'PASS'].find((s) => gates.some((g) => g.status === s));
  return {acts, gates, status, passed: acts.every((a) => a.passed)};
}

export async function checkAct(ctx, args, {bundles = null} = {}) {
  const act = actOf(ctx, args.act).act;
  if (webScene(ctx.dir, act)) {
    throw refused(
      `Act ${act} is a web scene (scenes/act${act}/web/index.html): the hand-in check reads Remotion acts only, so it can't hand this one in yet.`,
      `Its render (\`slate render --act ${act} --engine web\`) gates its fonts, its network and its frame count, and \`slate emit-events --act ${act}\` writes and checks its beats.`,
    );
  }
  if (!sceneIndex(ctx.dir, act)) throw usage(`Act ${act} isn't built yet (${missingIndexNote(ctx.dir, act)}): there's nothing to hand in.`);
  await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
  const p = `handin.act${act}`;
  const gates = [];
  log(`act ${act}: type check`);
  gates.push(typecheck(ctx, act, `${p}.typecheck`));
  let loaded = null;
  const srcInputs = inputsFor(ctx, actSources(ctx, act));
  const esb = {name: 'esbuild', version: (await import('esbuild')).version};
  try {
    loaded = await loadAct(ctx, act);
    const files = `${loaded.inputs.length} scene file${loaded.inputs.length === 1 ? '' : 's'}${loaded.usesKit ? ', with the kit' : ''}`;
    if (loaded.indexLoaded) {
      gates.push(gate(`${p}.module`, 'PASS', `loads in Node without rendering (${files})`, {engine: esb, inputs: srcInputs}));
    } else {
      // Its beats load, the picture's module doesn't: fine if it needs the browser, a bug if it throws for real.
      gates.push(gate(`${p}.module`, 'REVIEW', `the beats load in Node (${files}), but the picture's module doesn't: ${loaded.indexError}`, {
        engine: esb,
        inputs: srcInputs,
        details: {index_error: loaded.indexError},
      }));
    }
  } catch (e) {
    if (!(e instanceof EngineError)) throw e;
    gates.push(gate(`${p}.module`, 'FAIL', e.message.split('\n').slice(0, 2).join(' '), {engine: esb, evidence: e.message.split('\n').slice(1, 10), inputs: srcInputs}));
  }
  gates.push(loaded ? wordMisses(loaded, `${p}.word_misses`, srcInputs) : gate(`${p}.word_misses`, 'UNCHECKED', "the act's module didn't load, so its word lookups couldn't run", {inputs: srcInputs}));
  if (loaded?.usesKit) gates.push(kitProblems(loaded, `${p}.kit_problems`, srcInputs));
  gates.push(bannedWords(ctx, act, args.banned_words, `${p}.banned_words`, loaded));
  gates.push(await eventsGate(ctx, act, loaded, `${p}.events`));
  if (args.contracts === false) {
    gates.push(gate(`${p}.contracts`, 'UNCHECKED', 'contracts were skipped for this run (contracts: false)', {inputs: srcInputs}));
  } else if (!loaded) {
    gates.push(gate(`${p}.contracts`, 'UNCHECKED', "the act's module didn't load, so its declared rest states are unknown", {inputs: srcInputs}));
  } else {
    log(`act ${act}: boundary contracts`);
    const c = await contractsGate(ctx, act, loaded, `${p}.contracts`, args, bundles);
    gates.push(c.gate);
    // the contract stills ran the kit's font gate: its verdict is part of the hand-in too
    if (c.fonts) gates.push({...c.fonts, gate: `${p}.fonts`});
  }
  const worst = ['FAIL', 'STALE', 'UNCHECKED', 'REVIEW', 'PASS'].find((s) => gates.some((g) => g.status === s));
  return {act, gates, status: worst, passed: gates.every((g) => g.status === 'PASS')};
}
