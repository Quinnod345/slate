// The kit carries no brand: no company, product or character names, no licensed faces, no colours of its own, and no
// fixed frame size or rate in its logic. Fixtures and tests may name what they like; the kit's sources may not.
//
// The names this guards against are held here only as SHA-256 prefixes, so the check keeps company material out of
// the repo too: every word (and every pair of neighbouring words) in the kit's sources is hashed and compared.
import {createHash} from 'node:crypto';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';

const here = path.dirname(fileURLToPath(import.meta.url));
const kit = path.resolve(here, '../../remotion/kit');

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.(ts|tsx|mjs|js|json|md)$/.test(name)) out.push(p);
  }
  return out;
}

const SOURCES = files(kit);

/** Code without comments, so the rules judge what runs rather than what explains it. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');
}

const h = (s: string) => createHash('sha256').update(s).digest('hex').slice(0, 16);

/** Company, product, character and licensed-face names, and a font kit id, as hashes (words and word pairs). */
const GUARDED = new Set([
  '2ad60e2919a3ae8f',
  'e252b79c39aa92dd',
  'fd73d009d4b851d4',
  '16149626b0365e04',
  'ff9b5c834ecab632',
  '531074a5255eac9d',
  '129eaf4b1219beda',
  'db4ea2e2c2875a50',
  '61acc4cab1bfb5d3',
  'bdd6bed80a39febc',
  'f8d73da6e93bc010',
]);

describe('no brand in the kit', () => {
  it('has sources to check', () => {
    expect(SOURCES.length).toBeGreaterThan(20);
  });

  it('names no company, product, character or licensed face (comments and docs included)', () => {
    const hits: string[] = [];
    for (const f of SOURCES) {
      const words = readFileSync(f, 'utf8').toLowerCase().match(/[a-z0-9]+/g) ?? [];
      words.forEach((w, i) => {
        if (GUARDED.has(h(w)) || (i + 1 < words.length && GUARDED.has(h(`${w} ${words[i + 1]}`)))) hits.push(`${path.relative(kit, f)}: word ${i + 1}`);
      });
    }
    expect(hits).toEqual([]);
  });

  it('can still see a guarded word (the check itself works)', () => {
    // a neutral stand-in proves the hashing path: hash a word, find it in a sample text
    const sample = 'draw the placeholder card';
    const probe = new Set([h('placeholder')]);
    expect((sample.match(/[a-z0-9]+/g) ?? []).some((w) => probe.has(h(w)))).toBe(true);
  });

  it('holds no colour literals outside the colour maths (white and black anchors only)', () => {
    const hex = /#[0-9a-f]{6}\b|#[0-9a-f]{3}\b/gi;
    const hits: string[] = [];
    for (const f of SOURCES.filter((x) => /\.(ts|tsx|mjs)$/.test(x))) {
      const found = (code(readFileSync(f, 'utf8')).match(hex) ?? []).filter((c) => !/^#(FFFFFF|000000)$/i.test(c));
      if (found.length) hits.push(`${path.relative(kit, f)}: ${found.join(' ')}`);
    }
    expect(hits).toEqual([]);
  });

  it('assumes no frame size or rate in its logic', () => {
    // frame sizes, and the NTSC rates in any spelling (a 30000 ms timeout is not a frame rate)
    const fixed = /\b(1920|1080)\b|30000\s*\/\s*1001|\b1001\b|\b(29\.97|23\.976|59\.94)\b/;
    const hits: string[] = [];
    for (const f of SOURCES.filter((x) => /\.(ts|tsx|mjs)$/.test(x))) {
      code(readFileSync(f, 'utf8'))
        .split('\n')
        .forEach((line, i) => {
          if (fixed.test(line)) hits.push(`${path.relative(kit, f)}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(hits).toEqual([]);
  });
});
