// Load Slate's ExtendScript files in node: inline `#include`s the way ExtendScript's preprocessor does, drop the
// other preprocessor lines, and lint for anything that isn't ES3 (AE's ExtendScript is ES3 with a few extras).
//
// Used by run_vectors.mjs (the cross-language ease vectors) and the mock-AE tests in engine/tests/ae/.

import {readFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import vm from 'node:vm';

/** Read a .jsx file with its #include files inlined (paths relative to the including file). */
export function loadJsx(path, seen = new Set()) {
  const abs = resolve(path);
  if (seen.has(abs)) return `// (already included: ${abs})\n`;
  seen.add(abs);
  const src = readFileSync(abs, 'utf8');
  return src
    .split('\n')
    .map((line) => {
      const inc = /^\s*#include\s+"([^"]+)"\s*$/.exec(line);
      if (inc) return loadJsx(resolve(dirname(abs), inc[1]), seen);
      if (/^\s*#(target|targetengine|includepath|strict|script)\b/.test(line)) return `// ${line.trim()}`;
      return line;
    })
    .join('\n');
}

/** Code with comments and string literals blanked out, so the lint only sees code. */
export function codeOnly(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const d = src[i + 1];
    if (c === '/' && d === '/') {
      while (i < n && src[i] !== '\n') i++;
      continue;
    }
    if (c === '/' && d === '*') {
      i += 2;
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
      out += ' ';
      continue;
    }
    if (c === '"' || c === "'") {
      const q = c;
      i++;
      while (i < n && src[i] !== q) {
        if (src[i] === '\\') i++;
        if (src[i] === '\n') break;
        i++;
      }
      i++;
      out += '""';
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

const RULES = [
  [/\b(let|const)\s+[A-Za-z_$]/, 'let/const (ES3 has var only)'],
  [/=>/, 'arrow function'],
  [/`/, 'template literal'],
  [/\bclass\s+[A-Za-z_$]/, 'class'],
  [/\.(forEach|map|filter|reduce|reduceRight|some|every|find|findIndex|includes)\s*\(/, 'ES5+ array method'],
  [/\.(trim|trimStart|trimEnd|startsWith|endsWith|padStart|padEnd|repeat)\s*\(/, 'ES5+ string method'],
  [/\bJSON\s*\./, 'JSON object (ES3 has none; use SL.parseJSON / SL.toJSON)'],
  [/\bObject\.(keys|create|defineProperty|defineProperties|getPrototypeOf|freeze|assign|entries|values)\b/, 'ES5+ Object API'],
  [/\bArray\.(isArray|from|of)\b/, 'ES5+ Array API'],
  [/\.bind\s*\(/, 'Function.bind'],
  [/,\s*[}\]]/, 'trailing comma'],
  [/\.\.\.[A-Za-z_$[]/, 'spread/rest'],
  [/\bfor\s*\([^;)]*\bof\b/, 'for...of'],
  [/\b(get|set)\s+[A-Za-z_$][\w$]*\s*\(\s*[\w$]*\s*\)\s*\{/, 'getter/setter'],
  [/\bfunction\s*[\w$]*\s*\([^)]*=/, 'default parameter'],
  [/\b(async|await|yield)\b/, 'async/generator'],
];

/** ES3 problems in a source file: [{line, rule, text}]. Empty when clean. */
export function lintEs3(src) {
  const code = codeOnly(src);
  const problems = [];
  code.split('\n').forEach((line, idx) => {
    for (const [re, rule] of RULES) {
      if (re.test(line)) problems.push({line: idx + 1, rule, text: line.trim().slice(0, 120)});
    }
  });
  return problems;
}

/** A context with the few ExtendScript globals pure code may touch ($.writeln, $.global). */
export function es3Context(extra = {}) {
  const ctx = {console};
  ctx.$ = {global: ctx, writeln: () => {}, write: () => {}, fileName: '', stack: '', sleep: () => {}};
  Object.assign(ctx, extra);
  return vm.createContext(ctx);
}

/** Compile (and optionally run) ExtendScript source in a context; throws on syntax errors. */
export function runInContext(src, ctx, filename = 'script.jsx') {
  const script = new vm.Script(src, {filename});
  return script.runInContext(ctx);
}
