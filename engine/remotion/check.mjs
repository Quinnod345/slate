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
//   handin.act<N>.never_claim    (when the caller passes the list) no on-screen line uses anything the brand or the
//                                film never claims; every line that does is printed for the builder to read
//   handin.act<N>.literals       no literal film or act frame numbers in the act's sources: every time is keyed to
//                                words, shots or contract constants, so a re-voice moves the picture with the voice
//   handin.act<N>.margins        every timing margin the act records (the slack between beats the picture depends on)
//                                is not negative, and the rig lint is clean for every mark it draws
//   handin.act<N>.character      (acts that draw a mark character) the character acceptance checks on each mark
//   handin.act<N>.markup         (with `markup: true`) the act's static markup at every frame and four sub-frame samples
//                                has no NaN, Infinity or undefined in any style or attribute; with `against: <git ref>`
//                                (or a kit folder) every sample is identical to the act drawn by the kit at that ref;
//                                drawn in tilt mode too, it's held to the tilt mode's placement rules (markup.mjs)
//   handin.act<N>.determinism    (with `determinism: true`) sampled frames rendered alone and in sequence on software
//                                GL are identical
//
// An act built for the open web renderer (scenes/act<N>/web/index.html) is handed in the same way, through that
// renderer: its scene scripts are type-checked from the film folder like any act; its page is opened once in a dry-run
// emit (nothing written), which proves it loads and draws (handin.act<N>.page), and hands back its beats, its word
// lookups, what the kit had to assume, and its own network and font gates (handin.act<N>.network, .fonts); its banned
// words are read from its scripts, its page's text and its copy files; its boundary frames, drawn by the same renderer,
// are held to the rest states its neighbours declare (handin.act<N>.contracts).
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import {EngineError, listFiles, log, refused, sha256FileSync, usage} from './lib/common.mjs';
import {actOf} from './lib/film.mjs';
import {ensureEntry, contractsModule, missingIndexNote, sceneIndex, slateDir} from './entry.mjs';
import {actMarginList, declarations, loadAct, rigLint} from './load_act.mjs';
import {actShotScenes, captureBeats, computeEvents, emitWebAct, eventsFile, validateEventsFile, webRenderer, webScene, webShotPage} from './events.mjs';
import {useBundle} from './bundle.mjs';
import {pixelHash, psnr, renderStills} from './stills.mjs';
import {browserLogCollector, fontGate} from './kit_bridge.mjs';

const SOURCE_EXT = /\.(tsx?|jsx?|mjs)$/;

/** The stage `slate status` files the hand-in gates under (their names don't say it; "banned" reads as the script's). */
export const STAGE = 'scenes';

function gate(name, status, summary, extra = {}) {
  return {
    gate: name,
    status,
    summary,
    evidence: extra.evidence ?? [],
    engine: extra.engine ?? {name: 'slate engine', version: 'unknown'},
    inputs: extra.inputs ?? {},
    checked_at: new Date().toISOString(),
    details: {...(extra.details ?? {}), stage: STAGE},
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
  return typecheckWith(ctx, path.join(slateDir(ctx.dir), `tsconfig.act${act}.json`), name);
}

/** The type check with one generated tsconfig (an act's, or a web shot's: .slate/tsconfig.shot-<SHOT>.json). */
export function typecheckWith(ctx, configPath, name) {
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

const HTML_VISIBLE_ATTRS = /\s(?:alt|title|placeholder|aria-label|value|content)\s*=\s*("([^"]*)"|'([^']*)')/gi;

/**
 * On-screen string candidates in a web scene's page: its text between tags and the attributes a viewer can read (alt,
 * title, placeholder, aria-label, value, content), never what's inside <script> or <style> (the scripts are read as
 * code, by onScreenStrings) or a comment. Each with its line.
 */
export function htmlStrings(text) {
  // blank out what's never drawn, keeping line breaks so line numbers still match the file
  const blank = (m) => m.replace(/[^\n]/g, ' ');
  const visible = text.replace(/<!--[\s\S]*?-->/g, blank).replace(/<(script|style)\b[\s\S]*?<\/\1\s*>/gi, blank);
  const found = [];
  const lineAt = (i) => visible.slice(0, i).split('\n').length;
  for (const m of visible.matchAll(/>([^<]+)</g)) {
    const value = m[1].replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
    if (value.trim()) found.push({line: lineAt(m.index + 1), text: value.trim()});
  }
  for (const m of visible.matchAll(/<[a-z][^>]*>/gi)) {
    for (const a of m[0].matchAll(HTML_VISIBLE_ATTRS)) {
      const value = a[2] ?? a[3] ?? '';
      if (value.trim()) found.push({line: lineAt(m.index), text: value.trim()});
    }
  }
  return found.sort((a, b) => a.line - b.line);
}

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
  // a web scene's page and the copy it serves itself (the renderer doesn't report what a page fetched, so every page,
  // JSON and text file in its folder counts)
  const web = webScene(ctx.dir, act)
    ? listFiles(path.join(ctx.dir, 'scenes', `act${act}`, 'web')).filter((f) => /\.(html?|json|txt)$/i.test(f)).map((f) => path.join('scenes', `act${act}`, 'web', f))
    : [];
  return [...new Set([...own, ...imported, ...web])].sort();
}

export function bannedWords(ctx, act, words, name, loaded = null, {files: only = null, shots: onlyShots = null} = {}) {
  const engine = {name: 'slate banned-words (typescript AST)', version: ts.version};
  const files = only ?? screenFiles(ctx, act, loaded);
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
    const list = rel.endsWith('.json') ? jsonStrings(text) : rel.endsWith('.txt') ? textLines(text) : /\.html?$/i.test(rel) ? htmlStrings(text) : onScreenStrings(rel, text);
    if (list === null) {
      unreadable.push(`${rel} isn't valid JSON, so its strings couldn't be read`);
      continue;
    }
    strings += list.length;
    for (const s of list) for (const m of matchers) if (m.re.test(s.text)) hits.push(`${rel}:${s.line}: "${s.text.trim().slice(0, 120)}" (${m.word})`);
  }
  const a = actOf(ctx, act);
  for (const s of ctx.shots?.shots ?? []) {
    if (Number(s.act) !== a.act || typeof s.on_screen !== 'string' || (onlyShots && !onlyShots.includes(String(s.id)))) continue;
    strings++;
    for (const m of matchers) if (m.re.test(s.on_screen)) hits.push(`timing/shots.json ${s.id} on_screen: "${s.on_screen.slice(0, 120)}" (${m.word})`);
  }
  if (hits.length) return gate(name, 'FAIL', `${hits.length} banned word${hits.length > 1 ? 's' : ''} on screen; the first: ${hits[0]}`, {engine, evidence: [...hits.slice(0, 40), ...unreadable], inputs});
  if (unreadable.length) return gate(name, 'UNCHECKED', unreadable[0], {engine, evidence: unreadable, inputs, details: {strings, banned: matchers.length}});
  const summary = matchers.length ? `none of the brand's ${matchers.length} banned words in ${strings} on-screen strings` : `the brand bans no words (${strings} on-screen strings read)`;
  return gate(name, 'PASS', summary, {engine, inputs, details: {strings, banned: matchers.length, files: files.length}});
}

// ---------------------------------------------------------------- never-claim

/** Lower case, letters, digits and % only: the text gates' squash (slate.qa.textmatch.squash), so they agree. */
export const squash = (text) => String(text).toLowerCase().replace(/[^\p{L}\p{M}\p{N}_%]/gu, '').replace(/_/g, '');

/**
 * Every on-screen line that uses something the brand or the film never claims (the same list script.claims reads),
 * printed for the builder to read (L-qa-D036). `list` undefined: the caller didn't ask (no gate); null: the list
 * couldn't be read (UNCHECKED); [] : the brand and the film never-claim nothing.
 */
export function neverClaim(ctx, act, list, name, loaded = null, {files: only = null, shots: onlyShots = null} = {}) {
  const engine = {name: 'slate never-claim (typescript AST)', version: ts.version};
  const files = only ?? screenFiles(ctx, act, loaded);
  const inputs = inputsFor(ctx, [...files, 'timing/shots.json']);
  if (list === null) return gate(name, 'UNCHECKED', "the never-claim list couldn't be read (the brand pack, or the film's brief/truth.json)", {engine, inputs});
  const phrases = (list ?? []).map((x) => String(x ?? '').trim()).filter((x) => squash(x));
  const hits = [];
  let strings = 0;
  const look = (where, text) => {
    strings++;
    const st = squash(text);
    for (const p of phrases) if (st.includes(squash(p))) hits.push(`${where}: "${String(text).trim().slice(0, 120)}" says "${p}", which the film never claims`);
  };
  for (const rel of files) {
    const text = fs.readFileSync(path.join(ctx.dir, rel), 'utf8');
    const found = rel.endsWith('.json') ? jsonStrings(text) : rel.endsWith('.txt') ? textLines(text) : /\.html?$/i.test(rel) ? htmlStrings(text) : onScreenStrings(rel, text);
    for (const s of found ?? []) look(`${rel}:${s.line}`, s.text);
  }
  const a = actOf(ctx, act);
  for (const s of ctx.shots?.shots ?? []) if (Number(s.act) === a.act && typeof s.on_screen === 'string' && (!onlyShots || onlyShots.includes(String(s.id)))) look(`timing/shots.json ${s.id} on_screen`, s.on_screen);
  if (hits.length) return gate(name, 'FAIL', `${hits.length} on-screen line${hits.length > 1 ? 's use' : ' uses'} something the film never claims; the first: ${hits[0]}`, {engine, evidence: hits.slice(0, 40), inputs});
  const summary = phrases.length ? `none of the ${phrases.length} never-claims in ${strings} on-screen strings` : `the brand and the film never-claim nothing (${strings} on-screen strings read)`;
  return gate(name, 'PASS', summary, {engine, inputs, details: {strings, never_claim: phrases.length}});
}

// ---------------------------------------------------------------- frame literals

/** Properties whose value is a film or act frame. */
const FRAME_PROPS = new Set(['frame', 'frame_in', 'frame_out', 'frameIn', 'frameOut', 'untilFrame', 'until_frame', 'startFrame', 'endFrame', 'start_frame', 'end_frame', 'atFrame', 'at_frame', 'fromFrame', 'toFrame']);
/** Calls that turn a frame into time: a literal argument is a literal frame. */
const FRAME_CALLS = /^(secOf|secondsOf|frameToSec|frameToSeconds|timecode|filmSecond|filmSeconds)$/;
/** Names that hold a frame (an identifier, a property, a constant), for comparisons and declarations. */
const FRAME_NAME = /frame$|frame_?(in|out)$|^frame|_frame_/i;
const COMPARE = new Set([ts.SyntaxKind.LessThanToken, ts.SyntaxKind.LessThanEqualsToken, ts.SyntaxKind.GreaterThanToken, ts.SyntaxKind.GreaterThanEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken]);
export const ALLOW_LITERAL = 'slate-allow-frame-literal';

const literalValue = (node) => {
  if (!node) return null;
  if (ts.isNumericLiteral(node)) return Number(node.text);
  if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.MinusToken && ts.isNumericLiteral(node.operand)) return -Number(node.operand.text);
  return null;
};
const nameOf = (node, sf) => {
  if (!node) return '';
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) return node.text;
  if (ts.isPropertyAccessExpression(node)) return node.name.text;
  if (ts.isCallExpression(node)) return nameOf(node.expression, sf);
  if (ts.isStringLiteral(node)) return node.text;
  return '';
};

/**
 * Literal film or act frame numbers in a source file (L-timing-D041): a number given as a frame (`frame: 120`,
 * `frame_in: 0`), a frame turned into time (`secOf(90)`), a frame compared with a number (`frame >= 120`), a
 * Sequence placed at a number (`from={30}`), a frame constant (`const LAND_FRAME = 120`). Offsets from something keyed
 * to a word or a shot (`shot('S03').frameIn + 12`) are fine. A line saying `slate-allow-frame-literal: why` (or the
 * line above it) is let through. Returns [{line, text, why}].
 */
export function frameLiterals(file, text) {
  const kind = /x$/.test(file) ? ts.ScriptKind.TSX : /\.m?jsx?$/.test(file) ? ts.ScriptKind.JSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const lines = text.split('\n');
  const allowed = (line) => [line - 1, line - 2].some((i) => i >= 0 && lines[i]?.includes(ALLOW_LITERAL));
  const found = [];
  const add = (node, why) => {
    const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
    if (!allowed(line)) found.push({line, text: (lines[line - 1] ?? '').trim().slice(0, 140), why});
  };
  const visit = (node) => {
    if (ts.isPropertyAssignment(node) && FRAME_PROPS.has(nameOf(node.name, sf)) && literalValue(node.initializer) !== null) {
      add(node, `${nameOf(node.name, sf)}: ${literalValue(node.initializer)} is a literal frame`);
    } else if (ts.isCallExpression(node) && FRAME_CALLS.test(nameOf(node.expression, sf)) && literalValue(node.arguments[0]) !== null) {
      add(node, `${nameOf(node.expression, sf)}(${literalValue(node.arguments[0])}) turns a literal frame into time`);
    } else if (ts.isBinaryExpression(node) && COMPARE.has(node.operatorToken.kind)) {
      const [named, lit] = literalValue(node.right) !== null ? [node.left, literalValue(node.right)] : [node.right, literalValue(node.left)];
      if (lit !== null && Math.abs(lit) >= 2 && FRAME_NAME.test(nameOf(named, sf))) add(node, `${nameOf(named, sf)} is compared with the literal frame ${lit}`);
    } else if (ts.isJsxAttribute(node) && nameOf(node.name, sf) === 'from' && node.initializer && ts.isJsxExpression(node.initializer)) {
      const v = literalValue(node.initializer.expression);
      if (v !== null && v >= 1) add(node, `from={${v}} places a sequence at a literal frame`);
    } else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && FRAME_NAME.test(node.name.text) && !/frames$/i.test(node.name.text)) {
      const v = literalValue(node.initializer);
      if (v !== null && Math.abs(v) >= 2) add(node, `${node.name.text} = ${v} is a literal frame`);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return found;
}

/** The literals gate: every literal frame in the act's own sources (or the files given), with file and line. */
export function literalsGate(ctx, act, name, only = null) {
  const engine = {name: 'slate frame-literal lint (typescript AST)', version: ts.version};
  const files = only ?? [...actSources(ctx, act), ...(webScene(ctx.dir, act) ? listFiles(path.join(ctx.dir, 'scenes', `act${act}`, 'web')).filter((f) => SOURCE_EXT.test(f)).map((f) => path.join('scenes', `act${act}`, 'web', f)) : [])];
  const unique = [...new Set(files)].sort();
  const inputs = inputsFor(ctx, unique);
  const hits = [];
  for (const rel of unique) for (const h of frameLiterals(rel, fs.readFileSync(path.join(ctx.dir, rel), 'utf8'))) hits.push(`${rel}:${h.line}: ${h.why} (${h.text})`);
  if (hits.length) {
    return gate(name, 'FAIL', `${hits.length} literal frame${hits.length > 1 ? 's' : ''} in the act; the first: ${hits[0]}`, {engine, evidence: [...hits.slice(0, 40), 'key every time to a word (word()), a shot (shot().frameIn) or a contract constant, so a re-voice moves the picture with the voice'], inputs, details: {literals: hits.length}});
  }
  return gate(name, 'PASS', `no literal frames in ${unique.length} source file${unique.length === 1 ? '' : 's'}: every time is keyed to words, shots or contracts`, {engine, inputs, details: {files: unique.length}});
}

// ---------------------------------------------------------------- timing margins and the rig lint

/** The margins gate: every margin's slack, and the rig lint; FAIL while a margin is negative or the lint finds anything. */
export async function marginsGate(loaded, name, inputs) {
  const engine = {name: 'slate timing margins and rig lint', version: 'unknown'};
  const list = await actMarginList(loaded);
  const {traces, findings} = rigLint(loaded);
  const broken = list.filter((m) => !(Number.isFinite(m.slack) && m.slack >= 0));
  const lines = list.map((m) => `${Number.isFinite(m.slack) ? m.slack.toFixed(3).padStart(8) : String(m.slack).padStart(8)} s  ${m.slack >= 0 ? '' : 'BROKEN  '}${m.name}`);
  const evidence = [...lines, ...findings.map((f) => `rig lint: ${f}`)];
  const details = {margins: list.length, broken: broken.length, rigs: traces.length, lint: findings.length};
  if (broken.length || findings.length) {
    const first = broken.length ? `${broken[0].name} (${Number.isFinite(broken[0].slack) ? `${broken[0].slack.toFixed(3)} s` : 'not a number'})` : findings[0];
    const what = [broken.length ? `${broken.length} timing margin${broken.length > 1 ? 's' : ''} broken` : null, findings.length ? `${findings.length} rig lint finding${findings.length > 1 ? 's' : ''}` : null].filter(Boolean).join(' and ');
    return gate(name, 'FAIL', `${what}; the first: ${first}`, {engine, evidence: evidence.slice(0, 60), inputs, details});
  }
  if (!list.length && !traces.length) return gate(name, 'PASS', 'the act records no timing margins and draws no mark', {engine, inputs, details});
  const least = list.length ? Math.min(...list.map((m) => m.slack)) : null;
  const summary = [list.length ? `${list.length} margin${list.length > 1 ? 's' : ''}, the least ${least.toFixed(3)} s` : 'no margins', traces.length ? `the rig lint clean on ${traces.length} mark${traces.length > 1 ? 's' : ''}` : null].filter(Boolean).join('; ');
  return gate(name, 'PASS', summary, {engine, evidence, inputs, details});
}

// ---------------------------------------------------------------- the character

/** The character acceptance checks on every mark the act registered (registerMotifTrace); null when it draws none. */
export function characterGate(ctx, act, loaded, name, inputs) {
  const traces = loaded?.hooks?.motifTraces ? loaded.hooks.motifTraces.fn() ?? [] : [];
  if (!traces.length) return null;
  const engine = {name: 'slate character acceptance (the kit)', version: 'unknown'};
  const accept = loaded.kit?.characterAcceptance;
  if (typeof accept !== 'function') return gate(name, 'UNCHECKED', "the act draws a mark, but this kit has no characterAcceptance() to check it with", {engine, inputs});
  const a = actOf(ctx, act);
  const problems = [];
  const checks = [];
  for (const t of traces) {
    const r = accept(t, {act: {from: a.frameIn, to: a.frameOut}});
    problems.push(...r.problems);
    checks.push(`${t.rig}: ${r.checks.join('; ')}`);
  }
  if (problems.length) return gate(name, 'FAIL', `${problems.length} character check${problems.length > 1 ? 's' : ''} failed; the first: ${problems[0]}`, {engine, evidence: [...problems.slice(0, 40), ...checks], inputs, details: {marks: traces.length, problems: problems.length}});
  return gate(name, 'PASS', `${traces.length} mark${traces.length > 1 ? 's' : ''}: calm, still between gestures, clear of the split point and every clip, the rig lint clean`, {engine, evidence: checks, inputs, details: {marks: traces.length}});
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

export async function eventsGate(ctx, act, loaded, name, args = {}) {
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
    now = await computeEvents(ctx, act, loaded, {args});
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
      } catch (e) {
        // A face the renderer can't load cancels the still with the gate's report: that's the fonts gate failing (never
        // a crash of the whole check), and the contracts couldn't be compared.
        const failed = await fontGate([...logs.lines, String(e?.message ?? e)]);
        if (!failed || failed.status === 'PASS') throw e;
        fonts = failed;
        unchecked.unshift(`the contract stills didn't render: the font gate failed (${failed.summary})`);
      }
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

// ---------------------------------------------------------------- web acts

const worstOf = (gates) => ['FAIL', 'STALE', 'UNCHECKED', 'REVIEW', 'PASS'].find((s) => gates.some((g) => g.status === s));

/** What a page's refusal says about where it broke: the page's own errors and the bundler's, for the evidence. */
function pageEvidence(err) {
  const d = err?.details ?? {};
  const lines = [];
  for (const e of d.page_errors ?? []) lines.push(`page error: ${String(e.message ?? e).split('\n')[0]}`);
  for (const b of d.bundle_problems ?? []) lines.push(`couldn't compile ${b.file ? path.basename(b.file) : 'a script'}: ${String(b.message ?? '').split('\n')[0]}`);
  if (err?.hint) lines.push(`next: ${err.hint}`);
  return lines.slice(0, 20);
}

/** The events gate for a web act: the file against what the page says now (from the dry-run emit). */
function webEventsGate(ctx, act, page, name, inputsBase) {
  const engine = {name: 'slate emit-events (web scene, dry run)', version: 'protocol 1'};
  const file = eventsFile(ctx, act);
  const rel = path.relative(ctx.dir, file);
  const inputs = inputsFor(ctx, [rel, ...inputsBase, ...(page?.captures?.files ?? [])]);
  if (!fs.existsSync(file)) return gate(name, 'FAIL', `no ${rel}: run \`slate emit-events --act ${act}\``, {engine, inputs});
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return gate(name, 'FAIL', `${rel} isn't valid JSON: ${e.message}`, {engine, inputs});
  }
  const problems = validateEventsFile(ctx, act, data);
  if (problems.length) return gate(name, 'FAIL', `${rel} has ${problems.length} problem${problems.length > 1 ? 's' : ''}: ${problems[0]}`, {engine, evidence: problems.slice(0, 30), inputs});
  if (!page) return gate(name, 'UNCHECKED', `${rel} is valid, but the page didn't open, so it can't be compared with the act`, {engine, inputs});
  if (page.problems?.length) {
    const n = page.problems.length;
    return gate(name, 'FAIL', `${rel} can't be current: the page's beats have ${n} problem${n > 1 ? 's' : ''} now; the first: ${page.problems[0]}`, {engine, evidence: page.problems.slice(0, 30), inputs});
  }
  const want = Array.isArray(page.beats) ? page.beats : {silent: true, reason: page.beats?.reason};
  const have = Array.isArray(data) ? data : {silent: data.silent, reason: data.reason};
  if (canonical(want) !== canonical(have)) {
    return gate(name, 'FAIL', `${rel} is out of date with the page: run \`slate emit-events --act ${act}\``, {engine, inputs, details: {file_beats: Array.isArray(data) ? data.length : 0, page_beats: Array.isArray(want) ? want.length : 0}});
  }
  return gate(name, 'PASS', Array.isArray(want) ? `${want.length} beats, valid and current with the page` : `the act is silent: ${want.reason}`, {engine, inputs, details: {beats: Array.isArray(want) ? want.length : 0}});
}

/**
 * A web act's boundary contracts. A web scene declares no rest states of its own, so its boundaries are its neighbours':
 * where the act before it ends on a rest state ("out"), the web act's first `hold` frames must draw exactly that, and
 * where the act after it starts from one ("in"), its last `hold` frames must. The web act's frames are drawn by the
 * open web renderer (one sample each, software GL, nothing kept but the stills in qa/contracts/act<N>/), the
 * neighbour's boundary frame and `Contract-<Name>` (when scenes/contracts has it) by Remotion's renderStill, and the
 * frames compare by their decoded pixels, as a Remotion act's contracts do: the same picture, bit for bit, held still.
 */
async function webContractsGate(ctx, act, args, name, inputs, bundles = null) {
  const engine = {name: 'slate boundary contracts (open web renderer + remotion renderStill, pixel hash)', version: 'protocol 1'};
  if (args.contracts === false) return gate(name, 'UNCHECKED', 'contracts were skipped for this run (contracts: false)', {engine, inputs});
  const idx = ctx.acts.findIndex((a) => a.act === act);
  const declared = async (n) => {
    if (!sceneIndex(ctx.dir, n)) return {built: false, contracts: {}};
    try {
      return {built: true, contracts: declarations(await loadAct(ctx, n)).contracts ?? {}};
    } catch (e) {
      return {built: true, error: String(e?.message ?? e).split('\n')[0], contracts: {}};
    }
  };
  const list = [];
  const unreadable = [];
  if (idx > 0) {
    const prev = ctx.acts[idx - 1].act;
    const c = await declared(prev);
    if (c.error) unreadable.push(`act ${prev} didn't load, so its rest states are unknown (${c.error})`);
    else if (c.contracts.out) list.push({side: 'in', neighbour: prev, name: c.contracts.out, ready: c.built, label: `act ${prev} → act ${act}`});
  }
  if (idx >= 0 && idx < ctx.acts.length - 1) {
    const next = ctx.acts[idx + 1].act;
    const c = await declared(next);
    if (c.error) unreadable.push(`act ${next} didn't load, so its rest states are unknown (${c.error})`);
    else if (c.contracts.in) list.push({side: 'out', neighbour: next, name: c.contracts.in, ready: c.built, label: `act ${act} → act ${next}`});
  }
  if (!list.length) {
    if (unreadable.length) return gate(name, 'UNCHECKED', unreadable[0], {engine, evidence: unreadable, inputs});
    return gate(name, 'PASS', ctx.acts.length > 1 ? 'no pixel contracts at this act\'s boundaries (neither neighbour declares a rest state there)' : 'the film has one act: no boundaries', {engine, inputs});
  }
  const hold = Math.max(1, Number(args.hold ?? 6));
  const gl = args.contract_gl ?? 'swangle';
  const me = actOf(ctx, act);
  const n = Math.min(hold, me.frames);
  const contractComps = new Set();
  if (contractsModule(ctx.dir)) {
    const info = await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
    for (const c of info.contracts) contractComps.add(c);
  }
  const problems = [];
  const unchecked = [...unreadable];
  const passed = [];
  const outDir = path.join(ctx.dir, 'qa', 'contracts', `act${act}`);
  fs.rmSync(outDir, {recursive: true, force: true});
  fs.mkdirSync(outDir, {recursive: true});
  const own = bundles ? null : lazyBundle(ctx, args);
  const logs = browserLogCollector();
  try {
    const webRender = await webRenderer(act);
    for (const bd of list) {
      const others = [];
      if (bd.ready) others.push({comp: `Act${bd.neighbour}`, frame: bd.side === 'in' ? actOf(ctx, bd.neighbour).frames - 1 : 0});
      if (contractComps.has(bd.name)) others.push({comp: `Contract-${bd.name}`, frame: 0});
      if (!others.length) {
        unchecked.push(`${bd.label}: act ${bd.neighbour} isn't built yet and scenes/contracts has no ${bd.name} to compare with`);
        continue;
      }
      // the web act's frames at this boundary: its first n (meeting the act before) or its last n (the act after)
      const from = bd.side === 'in' ? 0 : me.frames - n;
      const to = from + n - 1;
      const framesDir = path.join(outDir, `web-${bd.side}`);
      try {
        await webRender({film: ctx.dir, act, from, to, samples: 1, gl, out: path.join(outDir, `web-${bd.side}.mkv`), frames_dir: framesDir, trace: false, on_blocked: 'report', on_fonts: 'report', ...(args.brand_path ? {brand_path: args.brand_path} : {}), ...(args.load_timeout_ms ? {load_timeout_ms: args.load_timeout_ms} : {})});
      } catch (e) {
        if (!(e instanceof EngineError)) throw e;
        unchecked.push(`${bd.label}: the web act's boundary frames didn't render (${e.message.split('\n')[0]})`);
        continue;
      }
      const mine = [];
      let stills;
      try {
        for (let f = from; f <= to; f++) {
          const file = path.join(framesDir, `f${String(f).padStart(5, '0')}_s00.png`);
          mine.push({comp: `Act${act}`, frame: f, file, pixels: await pixelHash(file)});
        }
        const b = await (bundles ?? own).get();
        stills = await renderStills(ctx, b, others, {outDir, gl, scale: 1, sub: 1, pixels: true, logs});
      } catch (e) {
        // a frame the renderer didn't write, or a still a face cancelled (the fonts gate says which): not compared
        if (!(e instanceof EngineError) && !/font gate/i.test(String(e?.message ?? e))) throw e;
        unchecked.push(`${bd.label}: the boundary frames couldn't be compared (${String(e?.message ?? e).split('\n')[0]})`);
        continue;
      } finally {
        fs.rmSync(path.join(outDir, `web-${bd.side}.mkv`), {force: true});
      }
      const edge = bd.side === 'in' ? mine[0] : mine.at(-1);
      let held = true;
      for (const s of mine) {
        if (s.pixels !== edge.pixels) {
          held = false;
          problems.push(`${bd.label}: act ${act} frame ${s.frame} differs from its boundary frame ${edge.frame}: the rest state must hold still for ${n} frames`);
        }
      }
      const ok = [];
      for (const o of stills) {
        if (o.pixels === edge.pixels) ok.push(`${o.comp}:${o.frame}`);
        else {
          const db = await psnr(edge.file, o.file);
          problems.push(`${bd.label}: ${edge.comp}:${edge.frame} (the web act) and ${o.comp}:${o.frame} differ (PSNR ${db === Infinity ? '∞' : db?.toFixed(2)} dB); "${bd.name}" must be bit-identical`);
        }
      }
      if (held && ok.length === stills.length) passed.push(`${bd.label}: the web act's ${bd.side === 'in' ? 'first' : 'last'} ${n} frames draw "${bd.name}" exactly as ${ok.join(' and ')}, held still`);
    }
  } finally {
    if (own) own.release();
  }
  const details = {hold: n, gl, boundaries: list.length, checked: passed.length};
  const evidence = [...problems, ...unchecked, ...passed];
  if (problems.length) return gate(name, 'FAIL', problems[0], {engine, evidence, inputs, details});
  if (unchecked.length) return gate(name, 'UNCHECKED', unchecked[0], {engine, evidence, inputs, details});
  return gate(name, 'PASS', passed.join('; '), {engine, evidence, inputs, details});
}

/**
 * The hand-in check for a web act (scenes/act<N>/web/index.html), through the open web renderer. See the header: the
 * same gates as a Remotion act's where they mean the same thing, read from the page itself.
 */
export async function checkWebAct(ctx, act, args, {bundles = null} = {}) {
  await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
  const p = `handin.act${act}`;
  const gates = [];
  log(`act ${act}: type check (a web scene)`);
  gates.push(typecheck(ctx, act, `${p}.typecheck`));
  const files = screenFiles(ctx, act, null);
  const srcInputs = inputsFor(ctx, files);
  const engine = {name: 'slate web renderer (a dry-run emit: nothing written)', version: 'protocol 1'};
  let page = null;
  let failed = null;
  log(`act ${act}: opening its page (a dry run: nothing is written)`);
  try {
    // a network attempt is a gate here, not a refusal; a caller may give a page less time to register itself
    const extra = {on_blocked: 'report', ...(args.load_timeout_ms ? {load_timeout_ms: args.load_timeout_ms} : {})};
    const out = await emitWebAct(ctx, act, args, captureBeats(ctx), {dryRun: true, extra});
    page = out.find((a) => Number(a.act) === Number(act)) ?? null;
    if (!page) failed = refused('The open web renderer said nothing about the act.');
  } catch (e) {
    if (!(e instanceof EngineError)) throw e;
    failed = e;
  }
  if (failed) {
    const status = failed.kind === 'missing_prerequisite' ? 'UNCHECKED' : 'FAIL';
    gates.push(gate(`${p}.page`, status, failed.message.split('\n')[0], {engine, evidence: pageEvidence(failed), inputs: srcInputs, details: {kind: failed.kind}}));
    gates.push(gate(`${p}.word_misses`, 'UNCHECKED', "the page didn't open, so its word lookups couldn't run", {inputs: srcInputs}));
  } else {
    const loads = `${page.events ?? 0} beat${page.events === 1 ? '' : 's'}${page.silent ? ' (silent)' : ''}`;
    gates.push(gate(`${p}.page`, 'PASS', `the page loads and draws its first frame in the open web renderer without an error (${loads})`, {engine, inputs: srcInputs}));
    const wordEngine = {name: 'slate kit word lookup (in the page)', version: 'unknown'};
    const misses = page.misses ?? [];
    if (!page.declared_misses) {
      gates.push(gate(`${p}.word_misses`, 'UNCHECKED', "the scene doesn't report its word lookups (a scene built with defineScene() from '@slate/web' does)", {engine: wordEngine, inputs: srcInputs}));
    } else if (misses.length) {
      gates.push(gate(`${p}.word_misses`, 'FAIL', `${misses.length} word${misses.length > 1 ? 's' : ''} fell back to an estimate: ${misses.slice(0, 5).join(', ')}`, {engine: wordEngine, evidence: misses.slice(0, 40), inputs: srcInputs}));
    } else {
      gates.push(gate(`${p}.word_misses`, 'PASS', 'every word the page looks up was found in the timings', {engine: wordEngine, inputs: srcInputs}));
    }
    const missSet = new Set(misses);
    const kit = [...new Set((page.scene_problems ?? []).filter((x) => !missSet.has(x)))];
    const notes = kit.filter((x) => /neutral law/.test(x));
    const list = kit.filter((x) => !/neutral law/.test(x));
    const kitEngine = {name: 'slate kit registries (in the page)', version: 'unknown'};
    if (list.length) gates.push(gate(`${p}.kit_problems`, 'REVIEW', `the kit recorded ${list.length} thing${list.length > 1 ? 's' : ''} it had to assume or refuse; the first: ${list[0]}`, {engine: kitEngine, evidence: [...list, ...notes].slice(0, 40), inputs: srcInputs, details: {notes}}));
    else gates.push(gate(`${p}.kit_problems`, 'PASS', notes.length ? `nothing refused; noted: ${notes[0]}` : 'the kit assumed and refused nothing', {engine: kitEngine, evidence: notes, inputs: srcInputs, details: {notes}}));
    // the page's own gates: what it reached for (always refused) and whether its fonts were the brand's
    for (const g of page.gates ?? []) {
      const suffix = g.gate === 'web.network' ? 'network' : g.gate;
      gates.push({...g, gate: `${p}.${suffix}`, inputs: {...(g.inputs ?? {}), ...srcInputs}, details: {...(g.details ?? {}), stage: STAGE}});
    }
  }
  gates.push(bannedWords(ctx, act, args.banned_words, `${p}.banned_words`, null));
  if (args.never_claim !== undefined) gates.push(neverClaim(ctx, act, args.never_claim, `${p}.never_claim`, null));
  gates.push(literalsGate(ctx, act, `${p}.literals`));
  gates.push(webEventsGate(ctx, act, page, `${p}.events`, files));
  log(`act ${act}: boundary contracts`);
  gates.push(await webContractsGate(ctx, act, args, `${p}.contracts`, inputsFor(ctx, [...files, 'timing/shots.json']), bundles));
  return {act, engine: 'web', gates, status: worstOf(gates), passed: gates.every((g) => g.status === 'PASS')};
}

// ---------------------------------------------------------------- shots in another engine

/**
 * The hand-in of each shot of a Remotion act drawn in the open web renderer (scenes/shots/<SHOT>/web; the rules are
 * slate.render.shot_engines'): its type check, its page (opened once, in a dry run: nothing written), its word
 * lookups and what the kit had to assume, its network wall and fonts, its banned words, never-claims and literal
 * frames. Each gate is handin.act<N>.shot.<SHOT>.<part>; the act's events gate already holds the page's beats.
 */
export async function checkWebShots(ctx, act, args) {
  const out = [];
  for (const sc of actShotScenes(ctx, act)) {
    const p = `handin.act${act}.shot.${sc.shot}`;
    const rel = path.relative(ctx.dir, sc.dir);
    const files = listFiles(sc.dir).map((f) => path.join(rel, f));
    const sources = files.filter((f) => SOURCE_EXT.test(f));
    const copy = files.filter((f) => SOURCE_EXT.test(f) || /\.(html?|json|txt)$/i.test(f));
    const inputs = inputsFor(ctx, files);
    log(`act ${act}: shot ${sc.shot} in the open web renderer`);
    out.push(typecheckWith(ctx, path.join(slateDir(ctx.dir), `tsconfig.shot-${sc.shot}.json`), `${p}.typecheck`));
    const page = await webShotPage(ctx, act, sc, args, {on_blocked: 'report', ...(args.load_timeout_ms ? {load_timeout_ms: args.load_timeout_ms} : {})});
    const engine = {name: 'slate web renderer (a dry-run emit of the shot: nothing written)', version: 'protocol 1'};
    if (page.error) {
      const status = page.failure?.kind === 'missing_prerequisite' ? 'UNCHECKED' : 'FAIL';
      out.push(gate(`${p}.page`, status, page.error, {engine, evidence: pageEvidence(page.failure), inputs}));
    } else {
      out.push(gate(`${p}.page`, 'PASS', `the shot's page loads and draws in the open web renderer (${page.beats.length} beat${page.beats.length === 1 ? '' : 's'})`, {engine, inputs}));
      if (page.declared_misses === false || page.declared_misses === undefined) {
        out.push(gate(`${p}.word_misses`, 'UNCHECKED', "the shot's page doesn't report its word lookups (a scene built with defineScene() from '@slate/web' does)", {inputs}));
      } else if (page.misses.length) {
        out.push(gate(`${p}.word_misses`, 'FAIL', `${page.misses.length} word${page.misses.length > 1 ? 's' : ''} fell back to an estimate: ${page.misses.slice(0, 5).join(', ')}`, {evidence: page.misses.slice(0, 40), inputs}));
      } else out.push(gate(`${p}.word_misses`, 'PASS', 'every word the shot looks up was found in the timings', {inputs}));
      const kit = [...new Set(page.scene_problems.filter((x) => !page.misses.includes(x) && !/neutral law/.test(x)))];
      out.push(kit.length ? gate(`${p}.kit_problems`, 'REVIEW', `the kit recorded ${kit.length} thing${kit.length > 1 ? 's' : ''} it had to assume or refuse; the first: ${kit[0]}`, {evidence: kit.slice(0, 40), inputs}) : gate(`${p}.kit_problems`, 'PASS', 'the kit assumed and refused nothing', {inputs}));
      for (const g of page.gates) {
        const suffix = g.gate === 'web.network' ? 'network' : g.gate;
        out.push({...g, gate: `${p}.${suffix}`, inputs: {...(g.inputs ?? {}), ...inputs}, details: {...(g.details ?? {}), stage: STAGE}});
      }
    }
    out.push(bannedWords(ctx, act, args.banned_words, `${p}.banned_words`, null, {files: copy, shots: [sc.shot]}));
    if (args.never_claim !== undefined) out.push(neverClaim(ctx, act, args.never_claim, `${p}.never_claim`, null, {files: copy, shots: [sc.shot]}));
    out.push(literalsGate(ctx, act, `${p}.literals`, sources));
  }
  return out;
}

export async function checkAct(ctx, args, {bundles = null} = {}) {
  const act = actOf(ctx, args.act).act;
  if (webScene(ctx.dir, act)) return checkWebAct(ctx, act, args, {bundles});
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
  if (args.never_claim !== undefined) gates.push(neverClaim(ctx, act, args.never_claim, `${p}.never_claim`, loaded));
  gates.push(literalsGate(ctx, act, `${p}.literals`));
  gates.push(await eventsGate(ctx, act, loaded, `${p}.events`, args));
  if (loaded) {
    gates.push(await marginsGate(loaded, `${p}.margins`, srcInputs));
    const ch = characterGate(ctx, act, loaded, `${p}.character`, srcInputs);
    if (ch) gates.push(ch);
  } else {
    gates.push(gate(`${p}.margins`, 'UNCHECKED', "the act's module didn't load, so its timing margins and rig lint couldn't be read", {inputs: srcInputs}));
  }
  gates.push(...(await checkWebShots(ctx, act, args)));
  if (args.markup) {
    log(`act ${act}: markup bench`);
    const {markupGate} = await import('./markup.mjs');
    gates.push(await markupGate(ctx, act, args, `${p}.markup`, srcInputs));
  }
  if (args.determinism) {
    log(`act ${act}: determinism (frames alone and in sequence)`);
    const {determinismGate} = await import('./determinism.mjs');
    gates.push(await determinismGate(ctx, act, args, `${p}.determinism`, srcInputs, bundles));
  }
  if (args.contracts === false) {
    gates.push(gate(`${p}.contracts`, 'UNCHECKED', 'contracts were skipped for this run (contracts: false)', {inputs: srcInputs}));
  } else if (!loaded) {
    gates.push(gate(`${p}.contracts`, 'UNCHECKED', "the act's module didn't load, so its declared rest states are unknown", {inputs: srcInputs}));
  } else {
    log(`act ${act}: boundary contracts`);
    const c = await contractsGate(ctx, act, loaded, `${p}.contracts`, args, bundles);
    gates.push(c.gate);
    // the contract stills ran the kit's font gate: its verdict is part of the hand-in too
    if (c.fonts) gates.push({...c.fonts, gate: `${p}.fonts`, details: {...(c.fonts.details ?? {}), stage: STAGE}});
  }
  const worst = ['FAIL', 'STALE', 'UNCHECKED', 'REVIEW', 'PASS'].find((s) => gates.some((g) => g.status === s));
  return {act, gates, status: worst, passed: gates.every((g) => g.status === 'PASS')};
}
