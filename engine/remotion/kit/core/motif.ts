// The motif rig: a brand mark made of N parts on a ring, moved as one system. Generalised from the films' character
// rig; none of its geometry is kept (a brand's motif comes from its pack: part count, shape, sizes, curves).
//
// The rules that made the character read as calm and deliberate are the rig's defaults, not options:
// - One clock. The pose is a pure function of the film frame, sub-frame samples included. Keys and per-part offsets
//   are whole frames, so part k at frame f is part 0 at frame f − Δk, turned: its blur is a rotated copy, not noise.
// - Three phase relationships and no others: unison (every part together), pairs (the second group a fixed number of
//   frames behind the first) and the wave (a fixed number of frames per slot, clockwise).
// - Spoke lock: a part stays on its spoke and never spins on its own; only its distance from the centre changes.
// - Every segment starts and ends at rest (the brand's curves, or the kit's sine and zero-bounce spring), exactly the
//   rest mark outside a gesture, no idle motion, no loops but breathing, and overshoot only where the brand allows it.
// - Small marks move as a whole: below a size, per-part spread becomes the mark's scale.
import {brandPack} from './brand';
import {frameOf, frameSize, secOf} from './film';
import {defineChannel, easeNamed, law, t90Of} from './motion';
import type {Pt} from './svg';
import type {BrandPack} from './types';

export type PartShape = 'rounded-square' | 'square' | 'circle' | 'diamond' | 'pill' | {path: string; viewBox: [number, number, number, number]};

export type MotifSpec = {
  parts: number;
  shape: PartShape;
  /** rounded-square corner radius, as a fraction of the part's side */
  corner: number;
  /** a part's side (or diameter) as a fraction of the mark's diameter */
  partSize: number;
  /** the ring's radius as a fraction of the mark's diameter */
  ring: number;
  /** spoke: a part turns with its slot; upright: every part keeps its own orientation */
  orient: 'spoke' | 'upright';
  /** the whole mark's turn at rest, degrees clockwise */
  restTurn: number;
  /** 2: alternate slots are two groups (the pairs phase); 1: one group */
  groups: 1 | 2;
  /** the mark's diameter as a fraction of its disc's (for the character) */
  markRatio: number;
  curves: {move: string; land: string};
  /** frames the second group trails the first in the pairs phase */
  pairsLag: number;
  /** frames per slot in the wave phase */
  waveStep: number;
  /** disc sizes (fractions of the frame's short side) at which the rig runs every phase / unison only */
  regimes: {full: number; unison: number};
  /** the most a part may dip below rest after a crest (0 unless the brand's overshoot policy allows it) */
  maxOvershoot: number;
  /** the part's own rotation at rest when orient is spoke (e.g. 45 for a diamond whose corner points in) */
  partTurn: number;
};

const DEFAULTS: MotifSpec = {
  parts: 6,
  shape: 'circle',
  corner: 0.25,
  partSize: 0.22,
  ring: 0.3,
  orient: 'spoke',
  restTurn: 0,
  groups: 2,
  markRatio: 0.5,
  curves: {move: 'sine', land: 'spring0'},
  pairsLag: 4,
  waveStep: 3,
  regimes: {full: 0.08, unison: 0.05},
  maxOvershoot: 0,
  partTurn: 0,
};

const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && Number.isFinite(Number(v)) ? Number(v) : d);

/** The motif of a brand pack, with the rig's defaults for anything it leaves out. */
export function motifSpec(raw?: Record<string, unknown> | null, pack?: BrandPack): {spec: MotifSpec; problems: string[]; declared: boolean} {
  const p = pack ?? brandPack();
  const m = (raw ?? (p.motif as Record<string, unknown> | null | undefined) ?? null) as Record<string, unknown> | null;
  const problems: string[] = [];
  if (!m) problems.push('the brand has no motif (brand.motif); the rig draws a plain ring of parts');
  const src = m ?? {};
  const parts = Math.max(1, Math.round(num(src.parts ?? src.count, DEFAULTS.parts)));
  const shapeRaw = src.shape ?? src.part ?? DEFAULTS.shape;
  let shape: PartShape = DEFAULTS.shape;
  if (typeof shapeRaw === 'string' && ['rounded-square', 'square', 'circle', 'diamond', 'pill'].includes(shapeRaw)) shape = shapeRaw as PartShape;
  else if (shapeRaw && typeof shapeRaw === 'object' && typeof (shapeRaw as {path?: unknown}).path === 'string') {
    const vb = (shapeRaw as {viewBox?: unknown}).viewBox;
    shape = {path: String((shapeRaw as {path: string}).path), viewBox: Array.isArray(vb) && vb.length === 4 ? (vb.map(Number) as [number, number, number, number]) : [-0.5, -0.5, 1, 1]};
  } else problems.push(`motif shape "${String(shapeRaw)}" isn't one the rig draws; using ${DEFAULTS.shape}`);
  const groups = (num(src.groups, parts % 2 === 0 ? 2 : 1) >= 2 && parts % 2 === 0 ? 2 : 1) as 1 | 2;
  const curves = (src.curves ?? {}) as {move?: string; land?: string};
  const ov = law(p).overshoot;
  const spec: MotifSpec = {
    parts,
    shape,
    corner: num(src.corner, DEFAULTS.corner),
    partSize: num(src.part_size ?? src.partSize, DEFAULTS.partSize),
    ring: num(src.ring ?? src.ring_radius, DEFAULTS.ring),
    orient: src.orient === 'upright' ? 'upright' : 'spoke',
    restTurn: num(src.rest_turn ?? src.restTurn, DEFAULTS.restTurn),
    groups,
    markRatio: num(src.mark_ratio ?? src.markRatio, DEFAULTS.markRatio),
    curves: {move: curves.move ?? DEFAULTS.curves.move, land: curves.land ?? DEFAULTS.curves.land},
    pairsLag: Math.round(num(src.pairs_lag ?? src.pairsLag, DEFAULTS.pairsLag)),
    waveStep: Math.max(1, Math.round(num(src.wave_step ?? src.waveStep, DEFAULTS.waveStep))),
    regimes: {
      full: num((src.regimes as {full?: unknown} | undefined)?.full, DEFAULTS.regimes.full),
      unison: num((src.regimes as {unison?: unknown} | undefined)?.unison, DEFAULTS.regimes.unison),
    },
    maxOvershoot: ov.allowed && (ov.where.length === 0 || ov.where.includes('motif')) ? Math.max(0, ov.max) : 0,
    partTurn: num(src.part_turn ?? src.partTurn, shape === 'diamond' ? 45 : 0),
  };
  return {spec, problems, declared: !!m};
}

// --------------------------------------------------------------------------------------------------------- gestures
/**
 * When a gesture lands: `frame`, or `t` in film seconds, on a whole frame. A gesture keyed to a word of the voice also
 * names the word (`word`, and `occ` for a repeat) and, where the act has them, its stressed syllable's start and end in
 * film seconds (`stress_start`, `stress_end`). The sync gate holds the gesture to landing on or before that syllable
 * ends (else the word's end). With a syllable and no frame or t, it lands on the contact: the syllable's first frame
 * plus 2 (contactAt).
 */
type When = {t?: number; frame?: number; word?: string; occ?: number; stress_start?: number; stress_end?: number};

/** The frames a gesture keyed to a stressed syllable lands after that syllable starts (the films' contact rule). */
export const CONTACT_LEAD = 2;
export type MotifGesture =
  | (When & {kind: 'enter'; from?: number; spin?: number; dur?: number})
  | (When & {kind: 'exit'; to?: number; spin?: number; dur?: number})
  | (When & {kind: 'pulse'; amount?: number; dur?: number})
  | (When & {kind: 'grow'; amount?: number})
  | (When & {kind: 'turn'; slots?: number; dir?: 1 | -1})
  | (When & {kind: 'breathe'; cycles?: number; cycle?: number; amount?: number})
  | (When & {kind: 'wave'; from?: number; dir?: 1 | -1; amount?: number; rounds?: number; dip?: number})
  | (When & {kind: 'ripple'; count?: number; spacing?: number; reach?: number})
  | (When & {kind: 'lean'; toward: Pt | 'centre'; amount?: number})
  | (When & {kind: 'hold'; until?: number; untilFrame?: number; amount?: number})
  | (When & {kind: 'home'});

export type MotifTrack = MotifGesture[];
export type GestureKind = MotifGesture['kind'];

/** Every gesture the rig draws, one cell each on the gesture sheet (review.tsx GestureSheet). */
export const GESTURE_KINDS: readonly GestureKind[] = ['enter', 'exit', 'pulse', 'grow', 'turn', 'breathe', 'wave', 'ripple', 'lean', 'hold', 'home'];

/**
 * The contact frame of a gesture (where its key pose lands): `frame`, or `t` in film seconds on a whole frame, or for
 * a gesture keyed to a stressed syllable with neither, that syllable's first frame plus CONTACT_LEAD.
 */
export function contactFrame(g: When): number {
  if (typeof g.frame === 'number') return Math.round(g.frame);
  if (typeof g.t !== 'number' && typeof g.stress_start === 'number' && Number.isFinite(g.stress_start)) return frameOf(g.stress_start) + CONTACT_LEAD;
  return frameOf(g.t ?? 0);
}

/** What a gesture keyed to a word says about it, for its beat: {word, occ?, stress_start?, stress_end?}, or null. */
export function wordKey(g: When): Pick<MotifBeat, 'word' | 'occ' | 'stress_start' | 'stress_end'> | null {
  if (typeof g.word !== 'string' || !g.word.trim()) return null;
  const out: Pick<MotifBeat, 'word' | 'occ' | 'stress_start' | 'stress_end'> = {word: g.word.trim()};
  if (typeof g.occ === 'number' && g.occ > 1) out.occ = Math.round(g.occ);
  const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  if (ok(g.stress_start) && ok(g.stress_end) && g.stress_end >= g.stress_start) {
    out.stress_start = g.stress_start;
    out.stress_end = g.stress_end;
  } else if (ok(g.stress_end)) out.stress_end = g.stress_end;
  return out;
}

/** Layout: the mark's centre and diameter in the layer it is drawn in (px). */
export type MotifLayout = {x: number; y: number; size: number};

export type PartPose = {rho: number; lift: number; op: number};
export type RippleRing = {r: number; op: number};
export type MotifPose = {
  parts: PartPose[];
  /** whole-mark scale */
  scale: number;
  /** the ring's turn, degrees clockwise (cumulative; a multiple of 360/N at rest) */
  turn: number;
  /** offset of the mark's centre, in mark diameters */
  ox: number;
  oy: number;
  opacity: number;
  ripples: RippleRing[];
  regime: 'full' | 'unison' | 'whole';
};

type Curve = (u: number) => number;

/** A channel through whole-frame keys [frame, value]: exact key values at and beyond the ends, each segment on `c`. */
export function chan(f: number, keys: readonly (readonly [number, number])[], c: Curve): number {
  if (!keys.length) return 0;
  if (f <= keys[0][0]) return keys[0][1];
  const last = keys[keys.length - 1];
  if (f >= last[0]) return last[1];
  for (let i = 0; i < keys.length - 1; i++) {
    const [f0, v0] = keys[i];
    const [f1, v1] = keys[i + 1];
    if (f < f1) return f1 > f0 ? v0 + (v1 - v0) * c((f - f0) / (f1 - f0)) : v1;
  }
  return last[1];
}

function regimeOf(spec: MotifSpec, sizePx: number, short: number): MotifPose['regime'] {
  const frac = sizePx / Math.max(1, short);
  return frac >= spec.regimes.full ? 'full' : frac >= spec.regimes.unison ? 'unison' : 'whole';
}

/** Which of the two groups a part belongs to (fixed for good, so a turn never re-sorts the paint order). */
export const groupOf = (spec: MotifSpec, i: number): 0 | 1 => (spec.groups === 2 && i % 2 === 1 ? 1 : 0);

const slotAngle = (spec: MotifSpec) => 360 / spec.parts;

/** Frames for a move that lands its 90% on contact C over D frames on curve `name`: [start, end]. */
function landing(C: number, D: number, name: string): [number, number] {
  const s = C - Math.round(t90Of(name) * D);
  return [s, s + D];
}

export type PoseOptions = {spec?: MotifSpec; layout?: (frame: number) => MotifLayout; short?: number};

// ------------------------------------------------------------------------------------------------ how gestures combine
// The rules (the films' character spec, §2.13), and what the rig does about them:
// 1. One momentary gesture at a time, with at least 4 still frames between one's end and the next one's start.
// 2. Held states persist until changed (a lean, a hold, parts sent away). Momentary gestures run on top of them, except:
//    during a hold nothing runs (but the home that releases it), and while parts are away only a lean or a hold runs.
// 3. Never two gestures on the same channel at once.
// The rig switches off whatever rules 1 to 3 forbid, so the picture never shows it, and motifLint lists it, and any
// momentary gestures closer than 4 still frames. An empty list is a clean track.

/** Gestures that run and end on their own (one at a time); a lean and a hold are held states, a ripple is an echo. */
export const MOMENTARY: readonly GestureKind[] = ['pulse', 'grow', 'turn', 'breathe', 'wave', 'enter', 'exit', 'home'];
/** Still frames a track keeps between one momentary gesture's end and the next one's start. */
export const STILL_FRAMES = 4;

/** What a gesture moves: the parts' spread, the ring's turn, the whole mark's scale, its offset, the parts' size, their
 * fade, and the echo rings around the mark. */
export type MotifChannel = 'spread' | 'turn' | 'scale' | 'offset' | 'lift' | 'fade' | 'echo';

/** A gesture as the rig runs it: its contact frame, the frames it moves over ([start, end] each, whole frames; a hold
 * has two, its pull-in and its release), the channels it moves, and whether it's momentary. */
export type GestureSpan = {g: MotifGesture; index: number; C: number; spans: [number, number][]; start: number; end: number; channels: MotifChannel[]; momentary: boolean};

const CHANNELS: Record<GestureKind, MotifChannel[]> = {
  pulse: ['spread'],
  grow: ['scale'],
  turn: ['turn'],
  breathe: ['spread', 'turn'],
  wave: ['spread', 'lift'],
  ripple: ['echo'],
  lean: ['offset'],
  hold: ['spread', 'scale'],
  home: ['offset', 'spread', 'scale'],
  enter: ['spread', 'turn', 'fade'],
  exit: ['spread', 'turn', 'fade'],
};

/** The frames each gesture of a track moves over, at the regime it runs in (the same numbers motifPose draws). */
export function gestureSpans(track: MotifTrack, opts: PoseOptions = {}): GestureSpan[] {
  const spec = opts.spec ?? motifSpec().spec;
  const N = spec.parts;
  const short = opts.short ?? filmShortSide();
  const layoutAt = opts.layout;
  const regimeAt = (C: number): MotifPose['regime'] => (layoutAt && short > 0 ? regimeOf(spec, layoutAt(C).size, short) : 'full');
  return track.map((g, index) => {
    const C = contactFrame(g);
    const regime = regimeAt(C);
    const lagMax = regime === 'full' && spec.groups === 2 ? spec.pairsLag : 0;
    let spans: [number, number][];
    switch (g.kind) {
      case 'pulse': {
        const D = Math.max(16, Math.round(g.dur ?? 18));
        const up = Math.round(D * 0.45);
        spans = [[C - up, C - up + D]];
        break;
      }
      case 'grow':
        spans = [[C - 8, C + 8]];
        break;
      case 'turn':
        spans = [landing(C, 16, spec.curves.land)];
        break;
      case 'breathe': {
        const cycles = Math.max(1, Math.round(g.cycles ?? 1));
        const cycle = Math.max(44, Math.round(g.cycle ?? 48));
        const B = C + (cycles - 1) * cycle;
        spans = [[C, Math.max(B + lagMax + 36, B + 32)]];
        break;
      }
      case 'wave': {
        const rounds = Math.max(1, Math.round(g.rounds ?? 1));
        const step = regime === 'full' ? spec.waveStep : 0;
        const dip = Math.min(Math.max(0, g.dip ?? 0), spec.maxOvershoot);
        spans = [[C - 6, C - 6 + (N * rounds - 1) * step + (dip > 0 ? 22 : 16)]];
        break;
      }
      case 'ripple': {
        const count = Math.max(1, Math.round(g.count ?? 2));
        const spacing = Math.max(1, Math.round(g.spacing ?? 5));
        spans = [[C, C + (count - 1) * spacing + 36]];
        break;
      }
      case 'lean':
        spans = [[C - 12, C + 3]];
        break;
      case 'hold': {
        const U = typeof g.untilFrame === 'number' ? Math.round(g.untilFrame) : typeof g.until === 'number' ? frameOf(g.until) : null;
        spans = U !== null ? [[C - 12, C + 3], [U, U + 20]] : [[C - 12, C + 3]];
        break;
      }
      case 'home':
        spans = [[C - 16, C + 4]];
        break;
      case 'enter': {
        const D = Math.max(16, Math.round(g.dur ?? 20));
        spans = [[C - D, C + lagMax]];
        break;
      }
      case 'exit': {
        const D = Math.max(16, Math.round(g.dur ?? 20));
        spans = [[C, C + lagMax + D]];
        break;
      }
    }
    const start = Math.min(...spans.map((x) => x[0]));
    const end = Math.max(...spans.map((x) => x[1]));
    return {g, index, C, spans, start, end, channels: CHANNELS[g.kind], momentary: MOMENTARY.includes(g.kind)};
  });
}

const overlaps = (a: GestureSpan, b: GestureSpan): boolean => a.spans.some(([s0, e0]) => b.spans.some(([s1, e1]) => s0 < e1 && s1 < e0));
const frames = (x: GestureSpan) => (x.spans.length > 1 ? x.spans.map(([s0, e0]) => `${s0}–${e0}`).join(' and ') : `${x.start}–${x.end}`);
const named = (x: GestureSpan) => `${x.g.kind} at frame ${x.C} (frames ${frames(x)})`;

export type TrackCheck = {
  /** the gestures the rig runs, in the track's own order */
  allowed: MotifGesture[];
  /** the gestures it switches off, each with the rule it broke */
  off: {g: MotifGesture; index: number; C: number; reason: string}[];
  /** momentary gestures closer than 4 still frames (they run; the lint lists them) */
  crowded: string[];
};

/**
 * How a track combines: which gestures run, which the rules switch off (and why), and which momentary gestures crowd
 * each other. A pure function of the track and the layout, so the picture, the sound and the hand-in check agree.
 */
export function checkTrack(track: MotifTrack, opts: PoseOptions = {}): TrackCheck {
  const spans = gestureSpans(track, opts).sort((a, b) => a.start - b.start || a.index - b.index);
  const kept: GestureSpan[] = [];
  const off: TrackCheck['off'] = [];
  const refuse = (x: GestureSpan, reason: string) => off.push({g: x.g, index: x.index, C: x.C, reason: `${named(x)} is switched off: ${reason}`});
  // a track whose first entrance comes before any exit starts with its parts away: they arrive with that enter
  const firstEnter = spans.find((x) => x.g.kind === 'enter');
  const startsAway = firstEnter && !spans.some((x) => x.g.kind === 'exit' && x.start < firstEnter.start) ? firstEnter : null;
  for (const x of spans) {
    if (startsAway && x !== startsAway && x.start < startsAway.end && !['lean', 'hold', 'enter'].includes(x.g.kind)) {
      refuse(x, `the parts haven't arrived yet (the ${named(startsAway)} brings them in), and only a lean or a hold runs until they do`);
      continue;
    }
    // while parts are away (an exit's start until the next enter has landed them), only a lean or a hold runs
    const away = kept.filter((k) => k.g.kind === 'exit' && k.start <= x.start).find((exit) => !kept.some((k) => k.g.kind === 'enter' && k.start >= exit.start && k.end <= x.start));
    if (away && !['lean', 'hold', 'enter'].includes(x.g.kind)) {
      refuse(x, `the parts are away (sent off by the ${named(away)}), and only a lean or a hold runs until they come back`);
      continue;
    }
    // during a hold (its pull-in until its release, or until the home that releases it) nothing runs but that home
    const hold = kept
      .filter((k) => k.g.kind === 'hold' && k.start <= x.start)
      .find((h) => {
        const released = h.spans.length > 1 ? h.end : Math.min(...kept.filter((k) => k.g.kind === 'home' && k.C >= h.C).map((k) => k.end), Infinity);
        return x.start < released;
      });
    if (hold && x.momentary && x.g.kind !== 'home') {
      refuse(x, `nothing runs during a hold (the ${named(hold)})`);
      continue;
    }
    const busy = kept.find((k) => overlaps(k, x) && k.momentary && x.momentary);
    if (busy) {
      refuse(x, `one momentary gesture at a time, and the ${named(busy)} is still running`);
      continue;
    }
    const shared = kept.map((k) => ({k, ch: k.channels.filter((c) => x.channels.includes(c))})).find(({k, ch}) => ch.length > 0 && overlaps(k, x));
    if (shared) {
      refuse(x, `never two gestures on one channel at once (the ${named(shared.k)} moves its ${shared.ch.join(' and ')} then)`);
      continue;
    }
    kept.push(x);
  }
  const crowded: string[] = [];
  const momentary = kept.filter((k) => k.momentary).sort((a, b) => a.start - b.start || a.index - b.index);
  for (let i = 1; i < momentary.length; i++) {
    const a = momentary[i - 1];
    const b = momentary[i];
    const gap = b.start - a.end;
    if (gap < STILL_FRAMES) crowded.push(`${named(a)} and ${named(b)} have ${gap} still frame${gap === 1 ? '' : 's'} between them (keep ${STILL_FRAMES} still frames between momentary gestures)`);
  }
  const keep = new Set(kept.map((k) => k.index));
  return {allowed: track.filter((_, i) => keep.has(i)), off: off.sort((a, b) => a.index - b.index), crowded};
}

/**
 * The rig's pose at a film frame (fractional frames are sub-frame samples). A pure function of the track and the
 * frame: evaluate any frame alone and it matches the same frame evaluated in sequence. Gestures the combination rules
 * forbid are switched off (checkTrack); motifLint lists them.
 */
export function motifPose(track: MotifTrack, frame: number, opts: PoseOptions = {}): MotifPose {
  const spec = opts.spec ?? motifSpec().spec;
  const N = spec.parts;
  const move = easeNamed(spec.curves.move);
  const land = easeNamed(spec.curves.land);
  // size regimes need the frame's short side: given, or the film's; without either every gesture runs in full
  const short = opts.short ?? filmShortSide();
  const layoutAt = opts.layout;
  const regimeAt = (C: number): MotifPose['regime'] => (layoutAt && short > 0 ? regimeOf(spec, layoutAt(C).size, short) : 'full');
  const f = frame;

  const rho = new Array<number>(N).fill(0);
  const lift = new Array<number>(N).fill(0);
  const op = new Array<number>(N).fill(1);
  let scale = 1;
  let turn = spec.restTurn;
  const ripples: RippleRing[] = [];
  let wholeSpread = 0;
  const regimeNow = regimeAt(Math.round(f));

  // the combination rules switch off what they forbid, so the picture never shows it (motifLint lists it)
  const sorted = checkTrack(track, {spec, layout: layoutAt, short})
    .allowed.map((g) => ({g, C: contactFrame(g)}))
    .sort((a, b) => a.C - b.C);

  // held channels: lean offset and hold amount, each a chain of moves from the running value
  let ox = 0;
  let oy = 0;
  let holdAmt = 0;
  let holdScale = 1;

  for (const {g, C} of sorted) {
    const regime = regimeAt(C);
    const lagFor = (i: number) => (regime === 'full' ? groupOf(spec, i) * spec.pairsLag : 0);
    const addRho = (i: number, v: number) => {
      if (regime === 'whole') wholeSpread += v / N;
      else rho[i] += v;
    };
    switch (g.kind) {
      case 'pulse': {
        const a = g.amount ?? 0.16;
        const D = Math.max(16, Math.round(g.dur ?? 18));
        const up = Math.round(D * 0.45);
        const v = chan(f, [[C - up, 0], [C, a], [C - up + D, 0]], move);
        for (let i = 0; i < N; i++) addRho(i, v);
        break;
      }
      case 'grow': {
        const a = g.amount ?? 0.06;
        scale *= 1 + a * chan(f, [[C - 8, 0], [C, 1], [C + 8, 0]], move);
        break;
      }
      case 'turn': {
        const slots = Math.max(1, Math.round(g.slots ?? 1));
        const [s, e] = landing(C, 16, spec.curves.land);
        turn += (g.dir ?? 1) * slots * slotAngle(spec) * chan(f, [[s, 0], [e, 1]], land);
        break;
      }
      case 'breathe': {
        const cycles = Math.max(1, Math.round(g.cycles ?? 1));
        const cycle = Math.max(44, Math.round(g.cycle ?? 48));
        const a = g.amount ?? 0.3;
        for (let k = 0; k < cycles; k++) {
          const B = C + k * cycle;
          for (let i = 0; i < N; i++) {
            const L = lagFor(i);
            addRho(i, a * chan(f, [[B + L, 0], [B + L + 12, 1], [B + L + 22, 1], [B + L + 36, 0]], move));
          }
          turn += slotAngle(spec) * chan(f, [[B + 10, 0], [B + 32, 1]], move);
        }
        break;
      }
      case 'wave': {
        const a = g.amount ?? 0.34;
        const dip = Math.min(Math.max(0, g.dip ?? 0), spec.maxOvershoot);
        const rounds = Math.max(1, Math.round(g.rounds ?? 1));
        const dir = g.dir ?? 1;
        const from = Math.round(g.from ?? 0);
        const turnSlots = Math.round((turn - spec.restTurn) / slotAngle(spec));
        for (let i = 0; i < N; i++) {
          const slot = (((i + turnSlots) % N) + N) % N;
          const order = (((dir * (slot - from)) % N) + N) % N;
          for (let r = 0; r < rounds; r++) {
            const step = regime === 'full' ? spec.waveStep : 0;
            const S = C - 6 + (order + r * N) * step;
            const keys: [number, number][] = dip > 0 ? [[S, 0], [S + 6, 1], [S + 14, -dip / a], [S + 22, 0]] : [[S, 0], [S + 6, 1], [S + 16, 0]];
            const w = chan(f, keys, move);
            addRho(i, a * w);
            if (regime !== 'whole') lift[i] += 0.1 * Math.max(0, w);
          }
        }
        break;
      }
      case 'ripple': {
        const count = Math.max(1, Math.round(g.count ?? 2));
        const spacing = Math.max(1, Math.round(g.spacing ?? 5));
        const reach = g.reach ?? 0.9;
        const dur = 36;
        for (let k = 0; k < count; k++) {
          const s = C + k * spacing;
          if (f < s || f > s + dur) continue;
          const u = (f - s) / dur;
          ripples.push({r: 0.5 + reach * move(u), op: (k === 0 ? 0.6 : 0.35) * (1 - move(u))});
        }
        break;
      }
      case 'lean': {
        const amount = g.amount ?? 0.08;
        const [s, e] = [C - 12, C + 3];
        let tx = 0;
        let ty = 0;
        if (g.toward !== 'centre' && layoutAt) {
          const L = layoutAt(C);
          const dx = g.toward.x - L.x;
          const dy = g.toward.y - L.y;
          const len = Math.hypot(dx, dy);
          if (len > 1e-6) {
            tx = (dx / len) * amount;
            ty = (dy / len) * amount;
          }
        }
        const u = chan(f, [[s, 0], [e, 1]], move);
        ox += (tx - ox) * u;
        oy += (ty - oy) * u;
        break;
      }
      case 'hold': {
        const a = g.amount ?? 0.14;
        const U = typeof g.untilFrame === 'number' ? Math.round(g.untilFrame) : typeof g.until === 'number' ? frameOf(g.until) : null;
        const u = chan(f, [[C - 12, 0], [C + 3, 1]], move);
        holdAmt += (a - holdAmt) * u;
        holdScale += (0.96 - holdScale) * u;
        if (U !== null) {
          const r = chan(f, [[U, 0], [U + 20, 1]], move);
          holdAmt += (0 - holdAmt) * r;
          holdScale += (1 - holdScale) * r;
        }
        break;
      }
      case 'home': {
        const u = chan(f, [[C - 16, 0], [C + 4, 1]], move);
        ox += (0 - ox) * u;
        oy += (0 - oy) * u;
        holdAmt += (0 - holdAmt) * u;
        holdScale += (1 - holdScale) * u;
        break;
      }
      case 'enter':
      case 'exit': {
        const D = Math.max(16, Math.round(g.dur ?? 20));
        const far = g.kind === 'enter' ? g.from ?? 2.2 : g.to ?? 2.2;
        const spin = g.spin ?? -30;
        for (let i = 0; i < N; i++) {
          const L = lagFor(i);
          // enter: the group arrives (lands) at C + its lag; exit: it leaves from C + its lag
          const s = g.kind === 'enter' ? C + L - D : C + L;
          const e = s + D;
          const u = chan(f, [[s, 0], [e, 1]], move);
          const k = g.kind === 'enter' ? 1 - u : u;
          if (regime === 'whole') wholeSpread += (far * k) / N;
          else rho[i] += far * k;
          const fade = g.kind === 'enter' ? chan(f, [[s, 0], [s + 8, 1]], move) : 1 - chan(f, [[e - 8, 0], [e, 1]], move);
          op[i] *= fade;
        }
        // the spin rides on the whole ring while the parts travel (each group keeps its own phase on its spoke)
        const s0 = g.kind === 'enter' ? C - D : C;
        const u = chan(f, [[s0, 0], [s0 + D, 1]], move);
        turn += g.kind === 'enter' ? spin * (1 - u) : spin * u;
        break;
      }
    }
  }

  // the held pull-in applies to every part on top of everything else (as scale when the mark is small)
  if (regimeNow === 'whole') wholeSpread -= holdAmt;
  else for (let i = 0; i < N; i++) rho[i] -= holdAmt;
  scale *= holdScale * (1 + 0.5 * wholeSpread);
  return {parts: rho.map((r, i) => ({rho: r, lift: lift[i], op: op[i]})), scale, turn, ox, oy, opacity: 1, ripples, regime: regimeNow};
}

function filmShortSide(): number {
  try {
    return frameSize().short;
  } catch {
    return 0;
  }
}

// ---------------------------------------------------------------------------------------------------------- geometry
export type PlacedPart = {i: number; x: number; y: number; size: number; rot: number; group: 0 | 1; op: number};

/** Where every part is drawn (px, in the layer the mark is in), for a pose at a layout. */
export function placeParts(spec: MotifSpec, pose: MotifPose, L: MotifLayout): PlacedPart[] {
  const D = L.size * pose.scale;
  const cx = L.x + pose.ox * L.size;
  const cy = L.y + pose.oy * L.size;
  const out: PlacedPart[] = [];
  for (let i = 0; i < spec.parts; i++) {
    const p = pose.parts[i];
    const ang = pose.turn + i * slotAngle(spec);
    const a = (ang * Math.PI) / 180;
    const r = spec.ring * D * (1 + p.rho);
    const size = spec.partSize * D * (1 + p.lift);
    out.push({
      i,
      x: cx + r * Math.sin(a),
      y: cy - r * Math.cos(a),
      size,
      rot: spec.orient === 'spoke' ? ang + spec.partTurn : spec.partTurn,
      group: groupOf(spec, i),
      op: p.op,
    });
  }
  return out;
}

/** Whether the mark is exactly at rest (every part home, no offset, scale 1, turn on a slot). */
export function isCalm(spec: MotifSpec, pose: MotifPose, eps = 1e-9): boolean {
  const turnOff = (((pose.turn - spec.restTurn) % slotAngle(spec)) + slotAngle(spec)) % slotAngle(spec);
  return (
    pose.parts.every((p) => Math.abs(p.rho) < eps && Math.abs(p.lift) < eps && Math.abs(p.op - 1) < eps) &&
    Math.abs(pose.scale - 1) < eps &&
    Math.abs(pose.ox) < eps &&
    Math.abs(pose.oy) < eps &&
    (turnOff < eps || slotAngle(spec) - turnOff < eps) &&
    pose.ripples.length === 0
  );
}

// -------------------------------------------------------------------------------------------------------------- sound
export type MotifBeat = {
  frame: number;
  t: number;
  gesture: GestureKind;
  kind: 'character';
  size: 's' | 'm' | 'l';
  note: string;
  /** on the contact of a gesture keyed to a word: the word, its occurrence, and its stressed syllable where known */
  word?: string;
  occ?: number;
  stress_start?: number;
  stress_end?: number;
};

const PRIORITY: GestureKind[] = ['turn', 'hold', 'wave', 'pulse', 'ripple', 'breathe', 'enter', 'exit', 'grow', 'home', 'lean'];

/**
 * The rig's sounds: one row per note, each on its own whole contact frame, one sound per frame (by priority), so the
 * events file matches the picture to the frame. Hand the rows to defineEvent (kind "character"). Gestures the
 * combination rules switch off make no sound; pass the mark's layout and the frame's short side (as to motifPose) so
 * the rules see the regime the picture runs in.
 */
export function motifBeats(track: MotifTrack, spec?: MotifSpec, opts: Omit<PoseOptions, 'spec'> = {}): MotifBeat[] {
  const s = spec ?? motifSpec().spec;
  const rows: MotifBeat[] = [];
  // the gesture's word (and its stressed syllable) goes on its contact row only: later rows of the same gesture (a
  // wave's steps, the second group's landing) follow it and aren't keyed to the word
  let keyed: ReturnType<typeof wordKey> = null;
  const add = (frame: number, gesture: GestureKind, size: MotifBeat['size'], note: string) => {
    rows.push({frame, t: secOf(frame), gesture, kind: 'character', size, note, ...(keyed ?? {})});
    keyed = null;
  };
  // a gesture the combination rules switch off makes no sound: the picture doesn't show it
  for (const g of checkTrack(track, {...opts, spec: s}).allowed) {
    const C = contactFrame(g);
    keyed = wordKey(g);
    switch (g.kind) {
      case 'turn':
        add(C, 'turn', 's', 'the mark turns and lands on itself');
        break;
      case 'pulse':
        add(C, 'pulse', 's', 'the parts spread and settle together');
        break;
      case 'grow':
        add(C, 'grow', 's', 'the mark breathes in');
        break;
      case 'breathe':
        add(C + 10, 'breathe', 's', 'first breath: the first group opens');
        if (s.groups === 2) add(C + 10 + s.pairsLag, 'breathe', 's', 'first breath: the second group follows');
        break;
      case 'wave': {
        const step = s.waveStep;
        for (let k = 0; k < s.parts * Math.max(1, g.rounds ?? 1); k++) add(C + k * step, 'wave', 's', `the wave reaches slot ${(Math.round(g.from ?? 0) + (g.dir ?? 1) * k + s.parts * 64) % s.parts}`);
        break;
      }
      case 'ripple':
        add(C, 'ripple', 'm', 'an echo rings out from the mark');
        break;
      case 'hold':
        add(C, 'hold', 's', 'the mark draws in and holds');
        break;
      case 'home':
        add(C, 'home', 's', 'everything returns to the mark');
        break;
      case 'lean':
        add(C, 'lean', 's', 'the mark leans toward its subject');
        break;
      case 'enter':
        add(C, 'enter', 'm', 'the first group lands in the ring');
        if (s.groups === 2) add(C + s.pairsLag, 'enter', 's', 'the second group lands: the mark is whole');
        break;
      case 'exit':
        add(C, 'exit', 's', 'the parts leave the ring');
        break;
    }
  }
  // one sound per frame, by priority; a word another gesture was keyed to on that frame stays with the frame's sound
  const byFrame = new Map<number, MotifBeat>();
  for (const r of rows) {
    const cur = byFrame.get(r.frame);
    if (!cur || PRIORITY.indexOf(r.gesture) < PRIORITY.indexOf(cur.gesture)) {
      const word = cur && !r.word && cur.word ? wordOfBeat(cur) : {};
      byFrame.set(r.frame, {...r, ...word});
    } else if (!cur.word && r.word) byFrame.set(r.frame, {...cur, ...wordOfBeat(r)});
  }
  return [...byFrame.values()].sort((a, b) => a.frame - b.frame);
}

function wordOfBeat(b: MotifBeat): Pick<MotifBeat, 'word' | 'occ' | 'stress_start' | 'stress_end'> {
  const out: Pick<MotifBeat, 'word' | 'occ' | 'stress_start' | 'stress_end'> = {};
  for (const k of ['word', 'occ', 'stress_start', 'stress_end'] as const) if (b[k] !== undefined) (out as Record<string, unknown>)[k] = b[k];
  return out;
}

/**
 * Everything in a track the combination rules object to: each gesture the rig switches off (a momentary gesture during
 * a hold, anything but a lean or a hold while parts are away, two momentary gestures at once, two gestures on one
 * channel at once), and momentary gestures with fewer than 4 still frames between one's end and the next one's start
 * (keep 4 still frames between momentary gestures). An empty list is a clean track. Pass the mark's layout and the
 * frame's short side, as to motifPose, so the rules see the regime the picture runs in.
 */
export function motifLint(track: MotifTrack, opts: PoseOptions = {}): string[] {
  const r = checkTrack(track, opts);
  return [...r.off.map((o) => o.reason), ...r.crowded];
}

/** A clipping container a character sits in, in the px of its layout (x, y top-left). */
export type ClipBox = {x: number; y: number; w: number; h: number};

/** A mark the act registered with registerMotifTrace: what the hand-in check's character gate reads. */
export type MotifTraceRecord = {
  rig: string;
  shot: string;
  track: MotifTrack;
  layout: (frame: number) => MotifLayout;
  from: number;
  to: number;
  spec: MotifSpec;
  short: number;
  /** drawn as a character (MarkCharacter): its disc is the layout's size ÷ the mark ratio */
  character: boolean;
  /** the clipping container it sits in, when it sits in one (a character is best drawn with none) */
  clip?: ClipBox | ((frame: number) => ClipBox);
};

const traces: MotifTraceRecord[] = [];

/**
 * Register the rig's part positions as motion channels (x and y of every part, px) so the motion trace carries them
 * and `slate qa motion` can check their speed, acceleration and symmetry (rig = the id you give). The track is kept
 * too, for the hand-in check's character gate (motifTraces, characterAcceptance): give `character: true` when it
 * draws a MarkCharacter (its layout is the mark's: the disc is the layout's size ÷ the mark ratio), and `clip` when
 * the character sits inside a clipping container (in the layout's px).
 */
export function registerMotifTrace(track: MotifTrack, o: {rig: string; shot: string; layout: (frame: number) => MotifLayout; from: number; to: number; spec?: MotifSpec; short: number; character?: boolean; clip?: ClipBox | ((frame: number) => ClipBox)}): void {
  const spec = o.spec ?? motifSpec().spec;
  const at = (frame: number) => placeParts(spec, motifPose(track, frame, {spec, layout: o.layout, short: o.short}), o.layout(frame));
  for (let i = 0; i < spec.parts; i++) {
    for (const axis of ['x', 'y'] as const) {
      defineChannel({id: `${o.rig}.part${i}.${axis}`, shot: o.shot, prop: axis, ease: spec.curves.move, frame_in: o.from, frame_out: o.to, rig: o.rig, fn: (fr) => at(fr)[i][axis]});
    }
  }
  traces.push({rig: o.rig, shot: o.shot, track, layout: o.layout, from: o.from, to: o.to, spec, short: o.short, character: o.character ?? false, ...(o.clip ? {clip: o.clip} : {})});
}

/** Every mark registered with registerMotifTrace (the engine's hand-in check reads them). */
export function motifTraces(): MotifTraceRecord[] {
  return [...traces];
}

export function clearMotifTraces(): void {
  traces.length = 0;
}

// ------------------------------------------------------------------------------------------- character acceptance
/** How much of the disc's diameter a clipping container must keep clear around the character's centre while it moves:
 * the disc grown a little, its shadow and the mark at every gesture's reach (the films' character spec, §1.6). */
export const CLEAR_CIRCLE = 0.85;

const SAMPLES_PER_FRAME = 4;
const sub = (f: number) => Array.from({length: SAMPLES_PER_FRAME}, (_, k) => f - 0.25 + (0.5 * (k + 0.5)) / SAMPLES_PER_FRAME);

/** The parts about the mark's centre, in px, and its size: what "the mark moved" means (a carried layout doesn't). */
function shape(t: MotifTraceRecord, f: number): number[] {
  const L = t.layout(f);
  const parts = placeParts(t.spec, motifPose(t.track, f, {spec: t.spec, layout: t.layout, short: t.short}), L);
  return [L.size, ...parts.flatMap((p) => [p.x - L.x, p.y - L.y, p.size, p.rot, p.op])];
}

const sameShape = (a: number[], b: number[]) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 1e-6);

/** The same picture, whichever part is where (a turn lands one slot on: the same mark, every part moved along). */
function samePicture(t: MotifTraceRecord, f: number, g: number): boolean {
  const key = (h: number) => {
    const L = t.layout(h);
    const parts = placeParts(t.spec, motifPose(t.track, h, {spec: t.spec, layout: t.layout, short: t.short}), L);
    const rows = parts.map((p) => [p.x - L.x, p.y - L.y, p.size, ((p.rot % 360) + 360) % 360, p.op].map((v) => Math.round(v * 1e4) / 1e4));
    rows.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    return [Math.round(L.size * 1e4) / 1e4, ...rows.flat()];
  };
  const a = key(f);
  const b = key(g);
  return a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 2e-4 || Math.abs(Math.abs(v - b[i]) - 360) < 2e-4);
}

/** Frame lists for a message: "12, 13, 14 and 9 more". */
const listFrames = (fs: number[]) => (fs.length > 6 ? `${fs.slice(0, 6).join(', ')} and ${fs.length - 6} more` : fs.join(', '));

/**
 * The character acceptance checks (the films' character spec; L-kit-077, L-motion-D074/D076/D077/D086/D088) on one
 * registered mark, over its frames (or the act's, when given): `problems` empty is a pass. Each problem names the rule
 * and the frames.
 * - the combination rules (motifLint): no conflicts, momentary gestures at least 4 still frames apart;
 * - calm: exactly the brand mark (isCalm) at the act's ends when the mark is on screen there, and after each gesture
 *   that returns home (a momentary gesture ends where it started; an enter and a home end calm);
 * - still between gestures: when no gesture runs, consecutive frames (and their sub-frame samples) are identical;
 * - the split point: the gap between neighbouring parts changes sign at most once out and once back per excursion of a
 *   gesture (a two-cycle breath has two), never dithering across it;
 * - clipping: while it isn't calm, the clipping container it sits in (its declared clip) holds the clear circle
 *   (0.85 of the disc's diameter around its centre) and every part, away parts' paths included.
 */
export function characterAcceptance(t: MotifTraceRecord, opts: {act?: {from: number; to: number}} = {}): {problems: string[]; checks: string[]} {
  const problems: string[] = [];
  const checks: string[] = [];
  const opt = {spec: t.spec, layout: t.layout, short: t.short};
  const pose = (f: number) => motifPose(t.track, f, opt);
  const calm = (f: number) => isCalm(t.spec, pose(f));
  const name = `${t.rig} (${t.shot})`;

  // 1. the combination rules
  const lint = motifLint(t.track, opt);
  for (const l of lint) problems.push(`${name}: ${l}`);
  checks.push(`lint: ${lint.length ? `${lint.length} finding${lint.length > 1 ? 's' : ''}` : 'clean'}`);

  const spans = gestureSpans(checkTrack(t.track, opt).allowed, opt);
  const moving = (f: number) => spans.some((x) => x.spans.some(([s0, e0]) => f > s0 && f < e0));
  const heldAt = (f: number) =>
    spans.some((x) => {
      if (x.g.kind === 'lean') return x.start <= f && !spans.some((h) => h.g.kind === 'home' && h.C >= x.C && h.end <= f);
      if (x.g.kind === 'hold') return x.start <= f && (x.spans.length > 1 ? f < x.end : !spans.some((h) => h.g.kind === 'home' && h.C >= x.C && h.end <= f));
      if (x.g.kind === 'exit') return x.start <= f && !spans.some((e) => e.g.kind === 'enter' && e.start >= x.start && e.end <= f);
      return false;
    });

  // 2. calm at the act's ends (where the mark is on screen) and after each gesture that returns home
  const ends = [opts.act && t.from <= opts.act.from ? opts.act.from : null, opts.act && t.to >= opts.act.to - 1 ? opts.act.to - 1 : null].filter((x): x is number => x !== null);
  if (!opts.act) ends.push(t.from, t.to);
  const notCalmAtEnds = [...new Set(ends)].filter((f) => !calm(f));
  if (notCalmAtEnds.length) problems.push(`${name}: not calm at the act's ${notCalmAtEnds.length > 1 ? 'ends' : 'end'} (frame ${listFrames(notCalmAtEnds)}): every act starts and ends with the mark exactly the brand mark`);
  const after: number[] = [];
  for (const x of spans) {
    const f = x.end + 1;
    if (f > t.to || moving(f)) continue;
    if (x.momentary && !['exit', 'enter', 'home'].includes(x.g.kind)) {
      if (!samePicture(t, f, x.start - 1)) after.push(f);
    } else if ((x.g.kind === 'enter' || x.g.kind === 'home') && !heldAt(f) && !calm(f)) after.push(f);
  }
  if (after.length) problems.push(`${name}: not back where it started the frame after a gesture (frame ${listFrames(after)}): a gesture ends exactly calm`);
  checks.push(`calm: ${ends.length} end${ends.length === 1 ? '' : 's'}, ${spans.length} gesture${spans.length === 1 ? '' : 's'}`);

  // 3. still between gestures
  const drift: number[] = [];
  for (let f = t.from + 1; f <= t.to; f++) {
    if (moving(f) || moving(f - 1) || spans.some((x) => x.spans.some(([s0, e0]) => f >= s0 && f - 1 <= e0))) continue;
    const ref = shape(t, f - 1);
    if (![...sub(f), f].every((s) => sameShape(shape(t, s), ref))) drift.push(f);
  }
  if (drift.length) problems.push(`${name}: moves between gestures (frame ${listFrames(drift)}): with no gesture running, consecutive frames are identical (no float, no idle)`);

  // 4. the split point: at most once out and once back across it per excursion
  const N = t.spec.parts;
  const gap = (f: number, i: number) => {
    const L = t.layout(f);
    const p = placeParts(t.spec, pose(f), L);
    const a = p[i];
    const b = p[(i + 1) % N];
    return Math.hypot(a.x - b.x, a.y - b.y) - (a.size + b.size) / 2;
  };
  for (const x of spans) {
    if (!x.momentary || N < 2) continue;
    const excursions = x.g.kind === 'breathe' ? Math.max(1, Math.round((x.g as {cycles?: number}).cycles ?? 1)) : x.g.kind === 'wave' ? Math.max(1, Math.round((x.g as {rounds?: number}).rounds ?? 1)) : 1;
    for (let i = 0; i < N; i++) {
      let last = 0;
      let changes = 0;
      const at: number[] = [];
      for (let f = x.start; f <= x.end; f += 1 / SAMPLES_PER_FRAME) {
        const g = gap(f, i);
        const sign = Math.abs(g) < 1e-9 ? 0 : Math.sign(g);
        if (sign && last && sign !== last) {
          changes++;
          at.push(Math.round(f * 100) / 100);
        }
        if (sign) last = sign;
      }
      if (changes > 2 * excursions) {
        problems.push(`${name}: the ${x.g.kind} at frame ${x.C} crosses the split point between parts ${i} and ${(i + 1) % N} ${changes} times (at frames ${listFrames(at)}): once out and once back per excursion, never dithering across it`);
        break;
      }
    }
  }

  // 5. clipping: the clear circle and every part inside the container the mark sits in, while it isn't calm
  if (t.clip) {
    const clipAt = typeof t.clip === 'function' ? t.clip : () => t.clip as ClipBox;
    const out: number[] = [];
    for (let f = t.from; f <= t.to; f++) {
      if (calm(f)) continue;
      const L = t.layout(f);
      const p = pose(f);
      const c = clipAt(f);
      const D = t.character ? L.size / Math.max(t.spec.markRatio, 1e-9) : L.size;
      const cx = L.x + p.ox * L.size;
      const cy = L.y + p.oy * L.size;
      const R = CLEAR_CIRCLE * D;
      const inside = (x0: number, y0: number, x1: number, y1: number) => x0 >= c.x - 1e-6 && y0 >= c.y - 1e-6 && x1 <= c.x + c.w + 1e-6 && y1 <= c.y + c.h + 1e-6;
      const parts = placeParts(t.spec, p, L);
      if (!inside(cx - R, cy - R, cx + R, cy + R) || !parts.every((q) => inside(q.x - q.size, q.y - q.size, q.x + q.size, q.y + q.size))) out.push(f);
    }
    if (out.length) problems.push(`${name}: its clipping container cuts into it while it moves (frame ${listFrames(out)}): keep a clear circle of ${CLEAR_CIRCLE} of the disc's diameter round its centre, and every part's path, inside the clip (or draw it with no clipping ancestor)`);
    checks.push(`clip: ${out.length ? `${out.length} frames clipped` : 'clear'}`);
  } else checks.push('clip: none declared (drawn with no clipping ancestor)');
  return {problems, checks};
}

