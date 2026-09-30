// The shared spoken-form vectors (tests/vectors/spoken.json), checked from the engine's side.
//
// Python resolves every frame and writes every script word's time into timing/vo_words.json ("tokens"), so the kit's
// clock only has to read JSON. What it still does itself is normalise a word into its match key and, for a word that
// isn't in the script line, scan the heard words. This test pins both to the same vectors pytest checks, with a small
// reference matcher written from the rules in the vectors file. The kit's own clock should pass the same vectors.
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';

type Target = {key: string; words: number};
type MatchVector = {
  words: string[];
  query: string;
  occ: number;
  span: [number, number] | null;
  targets: Target[];
  heard_targets: string[][];
};
type Vectors = {
  rules: {max_run: number};
  lang: string;
  normalize: {in: string; key: string}[];
  forms: {token: string; forms: string[][]}[];
  match: MatchVector[];
};

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const vectors: Vectors = JSON.parse(readFileSync(path.join(repo, 'tests/vectors/spoken.json'), 'utf8'));
const table = JSON.parse(readFileSync(path.join(repo, 'src/slate/timing/lang', `${vectors.lang}.json`), 'utf8'));

const keep = new Set<string>([...table.normalize.keep]);
const letterOrDigit = /[\p{L}\p{N}]/u;
const lettersOnly = /^\p{L}+$/u;

/** A word's match key: NFKC, lower case, letters, digits and the table's kept symbols. */
const norm = (s: string): string =>
  [...s.normalize('NFKC').toLowerCase()].filter((ch) => letterOrDigit.test(ch) || keep.has(ch)).join('');

function runMatches(keys: string[], i: number, targets: Target[], limit: number): number[] {
  const lengths = new Set<number>();
  for (const {key, words} of targets) {
    let acc = '';
    for (let length = 1; length <= Math.min(limit, words + 2); length++) {
      if (i + length > keys.length) break;
      acc += keys[i + length - 1];
      if (acc === key) {
        lengths.add(length);
        break;
      }
      if (!key.startsWith(acc)) break;
    }
  }
  return [...lengths].sort((a, b) => a - b);
}

function find(v: MatchVector, limit: number): [number, number] | null {
  const keys = v.words.map(norm);
  const own = v.targets[0]?.key;
  let seen = 0;
  let i = 0;
  while (i < keys.length) {
    let lengths = keys[i] ? runMatches(keys, i, v.targets, limit) : [];
    if (!lengths.length && keys[i] && !lettersOnly.test(keys[i]) && v.heard_targets[i].includes(own)) lengths = [1];
    if (lengths.length) {
      seen++;
      const run = Math.max(...lengths);
      if (seen === v.occ) return [i, i + run];
      i += run;
    } else {
      i++;
    }
  }
  return null;
}

describe('spoken vectors', () => {
  it('normalises words to the same match keys as Python', () => {
    for (const {in: word, key} of vectors.normalize) expect(norm(word), word).toBe(key);
  });

  it('finds every heard word the vectors expect, and misses the ones they do not', () => {
    for (const v of vectors.match) expect(find(v, vectors.rules.max_run), `${v.query} #${v.occ} in ${v.words.join(' ')}`).toEqual(v.span);
  });

  it('keeps the first target as the query itself', () => {
    for (const v of vectors.match) expect(v.targets[0].key).toBe(norm(v.query.replace(/^[^\p{L}\p{N}%&$€£¥#@+-]+|[^\p{L}\p{N}%&$€£¥#@+]+$/gu, '')));
  });

  it('says numbers only with words from the language table', () => {
    const n = table.numbers;
    const vocab = new Set<string>([
      ...n.ones, ...n.tens.filter(Boolean), n.hundred, ...n.scales, n.conjunction, n.indefinite, n.zero_letter,
      n.point, n.negative, n.range, ...Object.values(n.ordinal.irregular as Record<string, string>),
      ...Object.values(table.symbols as Record<string, string[][]>).flat(2),
      ...Object.values(table.suffixes as Record<string, string[][]>).flat(2),
      ...Object.values(table.currency as Record<string, {plural: string[]; singular: string[]}>).flatMap((c) => [...c.plural, ...c.singular]),
    ]);
    const known = (w: string) =>
      w.split(n.tens_joiner).every((part) => vocab.has(part) || vocab.has(part.replace(/(th|ieth)$/, '')) ||
        [...vocab].some((v) => part === `${v}${n.ordinal.suffix}` || part === v.replace(/y$/, 'ieth')));
    for (const {token, forms} of vectors.forms) {
      if (!/\d/.test(token)) continue;
      for (const form of forms) for (const word of form) expect(known(word), `${token}: ${word}`).toBe(true);
    }
  });

  it('gives punctuation no spoken form and a plain word itself', () => {
    const byToken = Object.fromEntries(vectors.forms.map((f) => [f.token, f.forms]));
    expect(byToken['—']).toEqual([]);
    expect(byToken['Hello.']).toEqual([['Hello']]);
    expect(byToken['664']).toContainEqual(['six', 'sixty-four']);
    expect(byToken['&']).toEqual([['and']]);
  });
});
