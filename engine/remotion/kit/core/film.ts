// The film clock, React-free, so a scene, an act's beats module and the Node collectors all read the same numbers.
//
// Everything comes from one module, `@film/data`, which the engine generates from the film folder (film.json and the
// timing files Python resolved). The kit never re-derives a shot boundary: a shot starts at frame_in × fps_den ÷ fps_num
// seconds, exactly as slate.clock.Clock.secs_of does, and seconds become frames with the same round-half-to-even Python
// uses, so a move keyed here lands on the frame the Python side expects.
//
// Lookups never throw. A word that isn't in a read (or a shot that doesn't exist) returns an estimate and is recorded;
// `misses()` lists every one, and the hand-in check (`slate check`) refuses an act while the list isn't empty. A throw at
// module load would take every composition in the bundle down with it (the films learned that twice).
import rawFilmData from '@film/data';
import type {FilmData, FilmInfo, LangRules, ShotRecord, VoCue, VoToken, Word} from './types';

// ------------------------------------------------------------------------------------------------------------ the data
type Cache = {
  shots?: Map<string, ShotRecord>;
  order?: ShotRecord[];
  cues?: Map<string, VoCue>;
};

let current: FilmData = unwrap(rawFilmData);
let cache: Cache = {};
const listeners = new Set<() => void>();

function unwrap(mod: unknown): FilmData {
  const m = mod as {default?: unknown} | null | undefined;
  const data = (m && typeof m === 'object' && 'default' in m && m.default && typeof m.default === 'object' ? m.default : m) as FilmData;
  return data ?? ({film: undefined, shots: {shots: []}} as unknown as FilmData);
}

/** The film data the kit is reading (the `@film/data` module, unless a test or review sheet swapped it). */
export function filmData(): FilmData {
  return current;
}

/**
 * Point the kit at other film data: tests, review sheets and the Node collectors use this. Clears every cache and the
 * miss list, and tells anything that memoised on the old data (tokens, the motion law) to recompute.
 */
export function setFilmData(data: FilmData | {default: FilmData}): void {
  current = unwrap(data);
  cache = {};
  missed.clear();
  for (const fn of listeners) fn();
}

/** Run `fn` whenever the film data is swapped (the brand and motion caches use it). */
export function onFilmDataChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ----------------------------------------------------------------------------------------------------- rate and size
function info(): FilmInfo {
  return (current?.film ?? {}) as FilmInfo;
}

/** The film's frame rate as a ratio (from film.json, then shots.json). Throws, clearly, when neither has one. */
export function fpsRational(): {num: number; den: number} {
  const f = info();
  const s = current?.shots ?? ({} as FilmData['shots']);
  const num = Number(f.fps_num ?? s.fps_num);
  const den = Number(f.fps_den ?? s.fps_den);
  if (!Number.isFinite(num) || !Number.isFinite(den) || num <= 0 || den <= 0) {
    throw new Error(
      'The film data has no frame rate (film.fps_num / film.fps_den). Regenerate the engine entry from the film folder ' +
        '(`slate engine entry`), so the kit reads the rate film.json declares.',
    );
  }
  return {num, den};
}

/** Frames per second as a number (fps_num ÷ fps_den). */
export function fps(): number {
  const {num, den} = fpsRational();
  return num / den;
}

/** The film's frame size (from film.json, then shots.json). Throws, clearly, when neither has one. */
export function frameSize(): {width: number; height: number; short: number; long: number} {
  const f = info();
  const s = current?.shots ?? ({} as FilmData['shots']);
  const width = Number(f.width ?? s.width);
  const height = Number(f.height ?? s.height);
  if (!(width > 0) || !(height > 0)) {
    throw new Error(
      'The film data has no frame size (film.width / film.height). Regenerate the engine entry from the film folder ' +
        '(`slate engine entry`).',
    );
  }
  return {width, height, short: Math.min(width, height), long: Math.max(width, height)};
}

/** Python's round(): halves go to the even neighbour, so frames here match slate.clock exactly. */
export function roundHalfEven(x: number): number {
  if (!Number.isFinite(x)) return x;
  const r = Math.round(x);
  if (Math.abs(x % 1) === 0.5) return 2 * Math.round(x / 2);
  return r;
}

/** The exact start time (film seconds) of a film frame. Fractional frames (sub-frame samples) are fine. */
export function secOf(frame: number): number {
  const {num, den} = fpsRational();
  return (frame * den) / num;
}

/** The nearest whole film frame to a time in film seconds (the same rounding as slate.clock.Clock.frame_of). */
export function frameOf(t: number): number {
  return roundHalfEven(t * fps());
}

/** A film time as a fractional frame (no rounding): what a sub-frame sample needs. */
export function frameAt(t: number): number {
  return t * fps();
}

/** HH:MM:SS:FF on the nominal rate, for burn-ins and overlays (the same counting as slate.clock.Clock.timecode). */
export function timecode(frame: number): string {
  const nominal = Math.round(fps());
  const f = Math.max(0, Math.floor(frame));
  const ff = f % nominal;
  const total = Math.floor(f / nominal);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${two(Math.floor(total / 3600))}:${two(Math.floor(total / 60) % 60)}:${two(total % 60)}:${two(ff)}`;
}

// ------------------------------------------------------------------------------------------------------------ misses
export type Miss = {
  kind: 'word' | 'shot' | 'vo' | 'cue';
  shot: string;
  word?: string;
  occ?: number;
  which?: 'start' | 'end';
  reason: string;
  estimate?: number;
};

const missed = new Map<string, Miss>();

function recordMiss(m: Miss): void {
  const key = `${m.kind}|${m.shot}|${m.word ?? ''}|${m.occ ?? ''}|${m.which ?? ''}`;
  if (!missed.has(key)) missed.set(key, m);
}

/** Every lookup that fell back to an estimate, in the order they happened. Must be empty at hand-in. */
export function misses(): Miss[] {
  return [...missed.values()];
}

/** The misses as short labels: `S12:owner`, `S12:owner#2`, `S12:owner(end)`, `S99 (no such shot)`. */
export function missLabels(): string[] {
  return misses().map((m) => {
    if (m.kind === 'shot') return `${m.shot} (no such shot)`;
    if (m.kind === 'vo') return `${m.shot} (no words for this read)`;
    if (m.kind === 'cue') return `${m.shot} (no VO cue)`;
    return `${m.shot}:${m.word}${m.occ && m.occ > 1 ? `#${m.occ}` : ''}${m.which === 'end' ? '(end)' : ''}`;
  });
}

/** Forget the misses (tests, or a collector that evaluates several acts in one process). */
export function clearMisses(): void {
  missed.clear();
}

// ------------------------------------------------------------------------------------------------------------- shots
function shotIndex(): Map<string, ShotRecord> {
  if (!cache.shots) {
    const list = (current?.shots?.shots ?? []).slice().sort((a, b) => a.frame_in - b.frame_in);
    cache.order = list;
    cache.shots = new Map(list.map((s) => [s.id, s]));
  }
  return cache.shots;
}

/** Every shot, in film order. */
export function allShots(): ShotRecord[] {
  shotIndex();
  return cache.order ?? [];
}

export type Shot = {
  id: string;
  act: number | string;
  /** first film frame of the shot */
  frameIn: number;
  /** the frame after its last frame (the next shot's frameIn): shots are contiguous */
  frameOut: number;
  frames: number;
  /** film seconds */
  start: number;
  end: number;
  dur: number;
  vo: string;
  onScreen: string;
  /** true when the shot doesn't exist (the lookup was recorded as a miss) */
  missing: boolean;
  raw: ShotRecord | null;
};

/** A shot's span. Never throws: an unknown id is recorded as a miss and comes back empty at 0 s. */
export function shot(id: string): Shot {
  const s = shotIndex().get(id);
  if (!s) {
    recordMiss({kind: 'shot', shot: id, reason: 'no such shot in timing/shots.json'});
    return {id, act: '', frameIn: 0, frameOut: 0, frames: 0, start: 0, end: 0, dur: 0, vo: '', onScreen: '', missing: true, raw: null};
  }
  const frameIn = s.frame_in;
  const frameOut = s.frame_out;
  const start = secOf(frameIn);
  const end = secOf(frameOut);
  return {
    id,
    act: s.act,
    frameIn,
    frameOut,
    frames: frameOut - frameIn,
    start,
    end,
    dur: end - start,
    vo: String(s.vo ?? ''),
    onScreen: String(s.on_screen ?? ''),
    missing: false,
    raw: s,
  };
}

/** The shot a film time falls in (the last one when t is past the end, the first when before the start). */
export function shotAt(t: number): Shot | null {
  const list = allShots();
  if (!list.length) return null;
  const f = frameAt(t);
  let hit = list[0];
  for (const s of list) {
    if (s.frame_in <= f) hit = s;
    else break;
  }
  return shot(hit.id);
}

const sameAct = (a: unknown, b: unknown) => String(a) === String(b);

/** The shots of one act, in order. */
export function actShots(act: number | string): Shot[] {
  return allShots()
    .filter((s) => sameAct(s.act, act))
    .map((s) => shot(s.id));
}

/** Every act, in film order, as ids (numbers stay numbers). */
export function acts(): (number | string)[] {
  const seen: (number | string)[] = [];
  for (const s of allShots()) if (!seen.some((a) => sameAct(a, s.act))) seen.push(s.act);
  return seen;
}

export type ActSpan = {act: number | string; first: string; last: string; frameIn: number; frameOut: number; frames: number; start: number; end: number};

/** An act's span: its first and last shot and its film frames (frameOut exclusive). Null for an unknown act. */
export function actSpan(act: number | string): ActSpan | null {
  const list = actShots(act);
  if (!list.length) return null;
  const frameIn = Math.min(...list.map((s) => s.frameIn));
  const frameOut = Math.max(...list.map((s) => s.frameOut));
  return {act, first: list[0].id, last: list[list.length - 1].id, frameIn, frameOut, frames: frameOut - frameIn, start: secOf(frameIn), end: secOf(frameOut)};
}

/** Master-frame length of a span of shots (for registering a composition). */
export function spanFrames(firstShot: string, lastShot: string): number {
  return shot(lastShot).frameOut - shot(firstShot).frameIn;
}

// --------------------------------------------------------------------------------------------------------------- VO
function cueIndex(): Map<string, VoCue> {
  if (!cache.cues) cache.cues = new Map((current?.vo_cues?.cues ?? []).map((c) => [c.shot, c]));
  return cache.cues;
}

/** Seconds into its shot at which a shot's read starts (timing/vo_cues.json). A shot with no cue is recorded. */
export function voLead(id: string): number {
  const c = cueIndex().get(id);
  if (c && Number.isFinite(Number(c.at))) return Number(c.at);
  const fallback = Number((current?.vo_cues as {lead?: number} | undefined)?.lead ?? 0);
  recordMiss({kind: 'cue', shot: id, reason: 'no VO cue for this shot in timing/vo_cues.json'});
  return Number.isFinite(fallback) ? fallback : 0;
}

function wordsOf(id: string): Word[] {
  return current?.vo_words?.shots?.[id]?.words ?? [];
}

/** Whether a shot's read has word timings. */
export const hasWords = (id: string): boolean => wordsOf(id).length > 0;

/** The line a shot's read says (vo_words' line, else the shot's VO text). */
export function voLine(id: string): string {
  return String(current?.vo_words?.shots?.[id]?.line ?? shotIndex().get(id)?.vo ?? '');
}

/**
 * When a shot's read starts and ends, in film seconds. Without word timings it spans from the cue to the shot's end, and
 * that's recorded (there's nothing to sync to yet).
 */
export function voSpan(id: string): {start: number; end: number; exact: boolean} {
  const s = shot(id);
  const words = wordsOf(id);
  const base = s.start + voLead(id);
  if (words.length) return {start: base + words[0].start, end: base + words[words.length - 1].end, exact: true};
  if (!s.missing) recordMiss({kind: 'vo', shot: id, reason: 'no word timings for this read in timing/vo_words.json'});
  return {start: base, end: Math.max(base, s.end), exact: false};
}

// ------------------------------------------------------------------------------------------------------------ words
// The kit keeps no language table. How a word becomes a match key, and how a number or symbol is said, both come from
// the data Python writes (slate.timing): `lang` (the language's match rules), `spoken` (each number's and symbol's
// spoken forms) and each read's `tokens` (every script word already lined up with what was heard). The rules are the
// ones in tests/vectors/spoken.json, which pytest checks against slate.timing.words and vitest against this file.

/** When the film data has no `lang` (an older entry): letters and digits, plus "%". */
const FALLBACK_RULES: Required<LangRules> = {lang: '', keep: '%', strip_keep: '%', negative_marks: '-', max_run: 6};
const LETTER_OR_DIGIT = /[\p{L}\p{N}]/u;
const LETTERS_ONLY = /^\p{L}+$/u;
const DIGIT = /\p{Nd}/u;

/** The film language's match rules (from '@film/data' `lang`), with the fallback filling any gap. */
export function langRules(): Required<LangRules> {
  const raw = (current?.lang ?? {}) as LangRules;
  const run = Number(raw.max_run);
  return {
    lang: typeof raw.lang === 'string' ? raw.lang : FALLBACK_RULES.lang,
    keep: typeof raw.keep === 'string' ? raw.keep : FALLBACK_RULES.keep,
    strip_keep: typeof raw.strip_keep === 'string' ? raw.strip_keep : typeof raw.keep === 'string' ? raw.keep : FALLBACK_RULES.strip_keep,
    negative_marks: typeof raw.negative_marks === 'string' ? raw.negative_marks : FALLBACK_RULES.negative_marks,
    max_run: Number.isInteger(run) && run > 0 ? run : FALLBACK_RULES.max_run,
  };
}

/**
 * A word's match key: NFKC, lower case, then only letters, digits and the language's kept symbols ("Sixty-four." →
 * "sixtyfour", "54%" → "54%", "$15." → "$15"). `keep` overrides the film's kept symbols.
 */
export function norm(s: string, keep?: string): string {
  const k = keep ?? langRules().keep;
  let out = '';
  for (const ch of String(s ?? '').normalize('NFKC').toLowerCase()) {
    if (LETTER_OR_DIGIT.test(ch) || k.includes(ch)) out += ch;
  }
  return out;
}

/** A token without the punctuation around it ("(664)." → "664"); spoken symbols and a leading minus stay ("-5", "$15"). */
export function stripEdges(token: string): string {
  const {strip_keep, negative_marks} = langRules();
  const chars = [...String(token ?? '').normalize('NFKC').trim()];
  const kept = (ch: string) => LETTER_OR_DIGIT.test(ch) || strip_keep.includes(ch);
  let start = 0;
  let end = chars.length;
  while (start < end && !kept(chars[start])) {
    if (negative_marks.includes(chars[start]) && start + 1 < end && DIGIT.test(chars[start + 1])) break;
    start++;
  }
  while (end > start && !kept(chars[end - 1])) end--;
  return chars.slice(start, end).join('');
}

/** A token's own match key: the key its script word has in vo_words' `tokens`. */
export const tokenKey = (token: string): string => norm(stripEdges(token));

/** One way a token can be heard: the joined match key of a spoken form, and how many words that form is. */
export type MatchTarget = {key: string; words: number};

function formsOf(token: string): string[][] {
  const table = current?.spoken ?? {};
  const edge = stripEdges(token);
  const forms = table[edge] ?? table[token] ?? table[String(token).trim()] ?? table[norm(token)] ?? [];
  return forms.map((f) => (Array.isArray(f) ? f.map(String) : [String(f)]));
}

/**
 * Every way a token is heard, its own key first (slate.timing.words.candidates): the written form, then each spoken
 * form the film data lists for it ("64" → "64", "sixtyfour", "sixfour"), each once.
 */
export function targetsFor(token: string): MatchTarget[] {
  const out: MatchTarget[] = [];
  const own = tokenKey(token);
  if (own) out.push({key: own, words: 1});
  for (const form of formsOf(token)) {
    const key = form.map((w) => norm(w)).join('');
    if (key && !out.some((t) => t.key === key)) out.push({key, words: form.length});
  }
  return out;
}

/** The ways a token is said, as match keys (its own first). */
export function spokenForms(token: string): string[] {
  return targetsFor(token).map((t) => t.key);
}

/** Lengths L such that keys[i..i+L) joined is one of the targets (runs of at most min(maxRun, words + 2) words). */
function runMatches(keys: string[], i: number, targets: MatchTarget[], maxRun: number): number[] {
  const lengths = new Set<number>();
  for (const {key, words} of targets) {
    let acc = '';
    for (let length = 1; length <= Math.min(maxRun, words + 2); length++) {
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

export type FindOptions = {
  /** match targets for each heard word (a heard number's spoken forms); computed from the film data when absent */
  heardTargets?: (i: number) => string[];
  /** the longest run of heard words one token is said as (the language's max_run by default) */
  maxRun?: number;
};

/**
 * Every place the targets are heard in a list of heard-word keys, as [first, end) index spans, scanning left to right
 * (the rules in tests/vectors/spoken.json): the longest run that matches a target wins and the scan skips past it; a
 * heard word that isn't all letters (a number or a symbol) also matches on its own when one of its own targets is the
 * query's key ("12" heard for "twelve").
 */
export function findSpans(keys: string[], targets: MatchTarget[], opts: FindOptions = {}): [number, number][] {
  const hits: [number, number][] = [];
  if (!keys.length || !targets.length) return hits;
  const maxRun = opts.maxRun ?? langRules().max_run;
  const own = targets[0].key;
  let i = 0;
  while (i < keys.length) {
    let lengths = keys[i] ? runMatches(keys, i, targets, maxRun) : [];
    if (!lengths.length && keys[i] && !LETTERS_ONLY.test(keys[i]) && opts.heardTargets?.(i).includes(own)) lengths = [1];
    if (lengths.length) {
      const run = Math.max(...lengths);
      hits.push([i, i + run]);
      i += run;
    } else i++;
  }
  return hits;
}

/** The occ-th place (from 1) the targets are heard: [first, end), or null. See findSpans. */
export function findSpan(keys: string[], targets: MatchTarget[], occ = 1, opts: FindOptions = {}): [number, number] | null {
  if (!Number.isInteger(occ) || occ < 1) return null;
  return findSpans(keys, targets, opts)[occ - 1] ?? null;
}

function heardTargetsOf(words: {w: string}[]): (i: number) => string[] {
  const memo = new Map<number, string[]>();
  return (i) => {
    if (!memo.has(i)) memo.set(i, spokenForms(String(words[i]?.w ?? '')));
    return memo.get(i) as string[];
  };
}

/**
 * Where each occurrence of a token is heard in a list of timed words: [first word index, last word index]. A form
 * matches a run of consecutive words whose keys join to it, so "sixty-four" said as one word or two matches the same.
 */
export function findOccurrences(words: {w: string}[], token: string): [number, number][] {
  const keys = words.map((x) => norm(String(x.w ?? '')));
  return findSpans(keys, targetsFor(token), {heardTargets: heardTargetsOf(words)}).map(([a, b]) => [a, b - 1]);
}

function tokensOf(id: string): VoToken[] {
  const list = current?.vo_words?.shots?.[id]?.tokens;
  return Array.isArray(list) ? list : [];
}

/** The occ-th script token of a read whose key is the query's (slate.timing.words.token_span). */
function tokenSpan(id: string, token: string, occ: number): VoToken | null {
  const key = tokenKey(token);
  if (!key || !Number.isInteger(occ) || occ < 1) return null;
  let seen = 0;
  for (const t of tokensOf(id)) {
    if (t && t.key === key && ++seen === occ) return t;
  }
  return null;
}

/**
 * Film seconds at which a read starts (or ends) a word, and whether that is exact: `[t, exact]`. The same contract as
 * slate.timing.words.word_time. `occ` counts repeats from 1. Never throws; a miss is estimated and recorded.
 *
 * The script's own words come first (vo_words' `tokens`: Python lined the script up with what was heard, so it knows
 * which "and" the "&" in "Sam and Jo run Salt & Stone" was); then anything heard, for spellings the script doesn't use.
 */
export function wordTime(id: string, token: string, occ = 1, which: 'start' | 'end' = 'start'): [number, boolean] {
  const s = shot(id);
  if (s.missing) return [0, false];
  const words = wordsOf(id);
  const base = s.start + voLead(id);
  const tok = tokenSpan(id, token, occ);
  if (tok && tok.exact && Number.isFinite(Number(tok[which]))) return [base + Number(tok[which]), true];
  const keys = words.map((x) => norm(String(x.w ?? '')));
  const hit = findSpan(keys, targetsFor(token), occ, {heardTargets: heardTargetsOf(words)});
  if (hit) return [base + (which === 'start' ? words[hit[0]].start : words[hit[1] - 1].end), true];
  const inLine = tok && Number.isFinite(Number(tok[which]));
  const estimate = inLine ? base + Number(tok[which]) : estimateWord(id, token, occ, which);
  recordMiss({
    kind: 'word',
    shot: id,
    word: token,
    occ,
    which,
    estimate,
    reason: inLine
      ? 'in the script line but not heard in the take'
      : words.length
        ? `the read never says "${token}"${occ > 1 ? ` a ${ordinal(occ)} time` : ''}`
        : 'this read has no word timings yet',
  });
  return [estimate, false];
}

/** Film seconds at which a read starts (or ends) a word. Never throws: see wordTime(). */
export function word(id: string, token: string, occ = 1, which: 'start' | 'end' = 'start'): number {
  return wordTime(id, token, occ, which)[0];
}

function ordinal(n: number): string {
  return `${n}${n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th'}`;
}

/** Where a word sits in its script line, spread across the read: only ever used for a miss. */
function estimateWord(id: string, token: string, occ: number, which: 'start' | 'end'): number {
  const span = voSpan(id);
  const line = voLine(id);
  const parts = line.split(/\s+/).filter(Boolean);
  const target = spokenForms(token);
  const total = line.length || 1;
  let seen = 0;
  let chars = 0;
  for (const p of parts) {
    if (spokenForms(p).some((k) => target.includes(k)) && ++seen === occ) {
      const f = (chars + (which === 'end' ? p.length : 0)) / total;
      return span.start + Math.min(1, Math.max(0, f)) * (span.end - span.start);
    }
    chars += p.length + 1;
  }
  return which === 'end' ? span.end : span.start;
}

/**
 * The contact frame for a word, in film seconds on a whole frame: the frame its stressed syllable starts plus `lead`
 * frames (2 by default). A character's key pose lands here, and its sound sits on it.
 */
export function contactAt(stressStart: number, lead = 2): number {
  return secOf(frameOf(stressStart) + lead);
}
