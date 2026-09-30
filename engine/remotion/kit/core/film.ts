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
import type {FilmData, FilmInfo, ShotRecord, VoCue, Word} from './types';

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
/**
 * Word matching needs no language table: letters, digits and marks of any script survive, case folds, punctuation goes.
 * "%" stays so "54%" can match a transcript that kept it. How a number or symbol is *said* comes from the data
 * (`spoken`, resolved in Python by timing.spoken.forms), never from a table here.
 */
export function norm(s: string): string {
  return String(s).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}\p{M}%]+/gu, '');
}

/** The ways a token is said: itself, plus every spoken form the film data lists for it. Each form is normalised text. */
export function spokenForms(token: string): string[] {
  const out: string[] = [];
  const self = norm(token);
  if (self) out.push(self);
  const table = current?.spoken ?? {};
  const forms = table[token] ?? table[token.trim()] ?? table[self] ?? [];
  for (const f of forms) {
    const joined = norm((Array.isArray(f) ? f : [f]).join(' '));
    if (joined && !out.includes(joined)) out.push(joined);
  }
  return out;
}

/**
 * Where each occurrence of a token sits in a list of timed words: [first word index, last word index]. A form matches a
 * run of consecutive words whose normalised text joins to it, so "sixty-four" said as one word or two matches the same.
 */
export function findOccurrences(words: {w: string}[], token: string): [number, number][] {
  const forms = spokenForms(token);
  const texts = words.map((x) => norm(x.w));
  const hits: [number, number][] = [];
  let i = 0;
  while (i < texts.length) {
    let matched: [number, number] | null = null;
    if (texts[i]) {
      for (const form of forms) {
        let acc = '';
        for (let j = i; j < texts.length; j++) {
          acc += texts[j];
          if (acc === form) {
            if (!matched || j > matched[1]) matched = [i, j];
            break;
          }
          if (acc.length >= form.length || !form.startsWith(acc)) break;
        }
      }
    }
    if (matched) {
      hits.push(matched);
      i = matched[1] + 1;
    } else i++;
  }
  return hits;
}

/**
 * Film seconds at which a read starts (or ends) a word, and whether that is exact: `[t, exact]`. The same contract as
 * slate.timing.words.word_time. `occ` counts repeats from 1. Never throws; a miss is estimated and recorded.
 */
export function wordTime(id: string, token: string, occ = 1, which: 'start' | 'end' = 'start'): [number, boolean] {
  const s = shot(id);
  if (s.missing) return [0, false];
  const words = wordsOf(id);
  const base = s.start + voLead(id);
  const hits = findOccurrences(words, token);
  const hit = hits[occ - 1];
  if (hit) return [base + (which === 'start' ? words[hit[0]].start : words[hit[1]].end), true];
  const estimate = estimateWord(id, token, occ, which);
  recordMiss({
    kind: 'word',
    shot: id,
    word: token,
    occ,
    which,
    estimate,
    reason: words.length ? `the read never says "${token}"${occ > 1 ? ` a ${ordinal(occ)} time` : ''}` : 'this read has no word timings yet',
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
    if (target.includes(norm(p)) && ++seen === occ) {
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
