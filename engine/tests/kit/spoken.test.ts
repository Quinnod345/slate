// The kit's clock hears words the way slate.timing.words does.
//
//   - tests/vectors/spoken.json (story-timing's shared vectors): match keys and where a query is heard, checked here
//     against the kit's own norm() and findSpan(), with the language rules read from the table they were made with.
//   - fixtures/word-times.json (generated from slate.timing.words by tests/engine_kit/test_word_time_parity.py): a
//     neutral film's reads, with the `tokens` Python writes into timing/vo_words.json, and Python's answer to each
//     lookup. The kit's wordTime() must give the same answers: script tokens first, then heard words, then an estimate
//     that is recorded as a miss.
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {afterAll, describe, expect, it} from 'vitest';
import {
  clearMisses,
  findOccurrences,
  findSpan,
  langRules,
  missLabels,
  misses,
  norm,
  setFilmData,
  stripEdges,
  targetsFor,
  tokenKey,
  wordTime,
} from '@slate/kit/core';
import type {FilmData} from '@slate/kit/core';
import FILM from './fixtures/film-data';

type Target = {key: string; words: number};
type MatchVector = {words: string[]; query: string; occ: number; span: [number, number] | null; targets: Target[]; heard_targets: string[][]};
type Vectors = {rules: {max_run: number}; lang: string; normalize: {in: string; key: string}[]; forms: {token: string; forms: string[][]}[]; match: MatchVector[]};

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const vectors: Vectors = JSON.parse(readFileSync(path.join(repo, 'tests/vectors/spoken.json'), 'utf8'));
const table = JSON.parse(readFileSync(path.join(repo, 'src/slate/timing/lang', `${vectors.lang}.json`), 'utf8'));
const times = JSON.parse(readFileSync(path.join(here, 'fixtures/word-times.json'), 'utf8'));

/** The rules the engine writes for the vectors' language (slate.engine.bridge.lang_rules), read from its table. */
function rulesFromTable() {
  const edge = new Set<string>([...table.normalize.keep]);
  for (const group of ['symbols', 'suffixes', 'currency']) for (const k of Object.keys(table[group] ?? {})) for (const ch of k) edge.add(ch);
  return {lang: vectors.lang, keep: table.normalize.keep, strip_keep: [...edge].sort().join(''), negative_marks: table.numbers.negative_marks.join(''), max_run: vectors.rules.max_run};
}

const base = (extra: Partial<FilmData>): FilmData => ({film: times.film, shots: times.shots, ...extra}) as FilmData;

afterAll(() => setFilmData(FILM as unknown as FilmData));

describe('the shared spoken vectors, heard by the kit', () => {
  it('normalises every word to the same match key as Python', () => {
    setFilmData(base({lang: rulesFromTable()}));
    for (const {in: word, key} of vectors.normalize) expect(norm(word), word).toBe(key);
  });

  it("finds every query where the vectors say, from the vectors' own targets", () => {
    setFilmData(base({lang: rulesFromTable()}));
    for (const v of vectors.match) {
      const got = findSpan(v.words.map((w) => norm(w)), v.targets, v.occ, {heardTargets: (i) => v.heard_targets[i], maxRun: vectors.rules.max_run});
      expect(got, `${v.query} #${v.occ} in "${v.words.join(' ')}"`).toEqual(v.span);
    }
  });

  it("builds the same targets from the film's spoken forms: the written key first, then each form once", () => {
    const spoken = Object.fromEntries(vectors.forms.map((f) => [f.token, f.forms]));
    setFilmData(base({lang: rulesFromTable(), spoken}));
    let checked = 0;
    for (const v of vectors.match) {
      if (!(stripEdges(v.query) in spoken) && /[\p{N}\p{S}&%]/u.test(v.query)) continue; // a form the vectors don't list
      expect(targetsFor(v.query), v.query).toEqual(v.targets);
      checked++;
    }
    expect(checked).toBeGreaterThan(5);
  });

  it('keeps a query its own key, with spoken symbols and a leading minus at its edges', () => {
    setFilmData(base({lang: rulesFromTable()}));
    expect(stripEdges('(664).')).toBe('664');
    expect(stripEdges('-5,')).toBe('-5');
    expect(stripEdges('"$15."')).toBe('$15');
    expect(tokenKey('Stone.')).toBe('stone');
    expect(tokenKey('&')).toBe('&');
    expect(langRules().max_run).toBe(vectors.rules.max_run);
  });
});

describe("word lookups read Python's tokens first, as slate.timing.words.word_time does", () => {
  const film = () => base({vo_cues: times.vo_cues, vo_words: times.vo_words, spoken: times.spoken, lang: times.lang});

  it('answers every lookup exactly as Python does, estimates and misses included', () => {
    setFilmData(film());
    for (const a of times.lookups) {
      const [t, exact] = wordTime(a.shot, a.word, a.occ, a.which);
      const what = `${a.shot} "${a.word}" #${a.occ} (${a.which})`;
      expect(exact, what).toBe(a.exact);
      expect(t, what).toBeCloseTo(a.t, 9);
    }
  });

  it("puts '&' on the second \"and\", where the script says it (a heard-word scan alone takes the first)", () => {
    setFilmData(film());
    clearMisses();
    expect(wordTime('X1', '&')).toEqual([0.2 + 1.3, true]);
    // without tokens the kit can only scan the heard words, and the first "and" wins: that's the bug tokens fix
    const noTokens = JSON.parse(JSON.stringify(times.vo_words));
    for (const rec of Object.values(noTokens.shots) as {tokens?: unknown}[]) delete rec.tokens;
    setFilmData(base({vo_cues: times.vo_cues, vo_words: noTokens, spoken: times.spoken, lang: times.lang}));
    expect(wordTime('X1', '&')).toEqual([0.2 + 0.3, true]);
    expect(misses()).toEqual([]);
  });

  it('hears "Twelve" in a heard "12", and "664" across "six" "sixty-four"', () => {
    setFilmData(film());
    clearMisses();
    expect(findOccurrences(times.vo_words.shots.X2.words, 'twelve')).toEqual([[0, 0]]);
    expect(findOccurrences(times.vo_words.shots.X2.words, '664')).toEqual([[3, 4]]);
    expect(misses()).toEqual([]);
  });

  it('records a word in the line that the read never says, at its estimated place', () => {
    setFilmData(film());
    clearMisses();
    const [t, exact] = wordTime('X3', 'word');
    expect(exact).toBe(false);
    expect(t).toBeCloseTo(4.9, 9);
    expect(missLabels()).toEqual(['X3:word']);
    expect(misses()[0].reason).toBe('in the script line but not heard in the take');
  });

  it('never throws on odd input', () => {
    setFilmData(film());
    expect(() => wordTime('X1', '', 1)).not.toThrow();
    expect(() => wordTime('X1', 'and', 0)).not.toThrow();
    expect(() => wordTime('X1', '—', 1)).not.toThrow();
    expect(findSpan(['a'], [], 1)).toBeNull();
    expect(findSpan([], [{key: 'a', words: 1}], 1)).toBeNull();
    expect(findSpan(['a'], [{key: 'a', words: 1}], 0)).toBeNull();
  });
});
