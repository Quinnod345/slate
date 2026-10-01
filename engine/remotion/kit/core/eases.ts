// Eases with exact endpoints, a T90 for every one of them, and their After Effects speed/influence.
//
// A brand's named eases live in its pack (motion.eases); this file only knows how to evaluate a curve, never which
// curves a brand uses. Every ease returns exactly 0 at or before the start and exactly 1 at or after the end: a
// bisection-solved bezier left 1e-14 before a move, and "has it started?" gates opened early all over the films.
//
// The same maths lives in Python (src/slate/motion/eases.py) and is checked against one shared vectors file
// (tests/vectors/eases.json, t90.json) by vitest, pytest and the After Effects ExtendScript runner. Keep the three in
// step: same solver, same order of operations.
import {monoSpline, type SKey} from './spline';

export type Bezier = [number, number, number, number];

export type EaseSpec =
  | {kind: 'bezier'; bezier: Bezier}
  | {kind: 'linear'}
  /** (1 − cos πu) ÷ 2: at rest at both ends, top speed π/2 × average, mid-move */
  | {kind: 'sine'}
  /** a critically damped spring's shape, normalised to land exactly on 1: quick stroke, no overshoot */
  | {kind: 'spring0'; stiffness: number}
  /** the even travel profile: rest to rest through a third of the travel at each third of the time (see EVEN_KEYS) */
  | {kind: 'even'};

export type Ease = ((u: number) => number) & {spec: EaseSpec; name: string};

// ---------------------------------------------------------------------------------------------------------- bezier
/** x(s) or y(s) of a unit cubic bezier with control values a (at 1/3) and b (at 2/3). */
function bez(a: number, b: number, s: number): number {
  const r = 1 - s;
  return 3 * r * r * s * a + 3 * r * s * s * b + s * s * s;
}

function bezSlope(a: number, b: number, s: number): number {
  const r = 1 - s;
  return 3 * r * r * a + 6 * r * s * (b - a) + 3 * s * s * (1 - b);
}

/**
 * The curve parameter s where x(s) = u. Newton from s = u (fast, and exact to 1e-14 on every curve CSS allows), then
 * bisection when Newton stalls on a flat stretch (x1 = 0 or x2 = 1 makes dx/ds vanish at an end).
 */
export function solveBezierX(x1: number, x2: number, u: number): number {
  let s = u;
  for (let i = 0; i < 8; i++) {
    const err = bez(x1, x2, s) - u;
    if (Math.abs(err) < 1e-14) return s;
    const d = bezSlope(x1, x2, s);
    if (Math.abs(d) < 1e-9) break;
    s -= err / d;
    if (s < 0 || s > 1) break;
  }
  let lo = 0;
  let hi = 1;
  s = u;
  for (let i = 0; i < 64; i++) {
    s = (lo + hi) / 2;
    const x = bez(x1, x2, s);
    if (Math.abs(x - u) < 1e-15) return s;
    if (x < u) lo = s;
    else hi = s;
  }
  return (lo + hi) / 2;
}

/** A cubic-bezier ease, CSS-style (x1 and x2 within [0, 1]; y may leave [0, 1] for anticipation or overshoot). */
export function bezierAt(b: Bezier, u: number): number {
  if (!(u > 0)) return 0;
  if (u >= 1) return 1;
  const [x1, y1, x2, y2] = b;
  if (x1 === y1 && x2 === y2) return u; // a straight line
  const s = solveBezierX(x1, x2, u);
  return bez(y1, y2, s);
}

// ------------------------------------------------------------------------------------------------- analytic curves
export function sineAt(u: number): number {
  if (!(u > 0)) return 0;
  if (u >= 1) return 1;
  return (1 - Math.cos(Math.PI * u)) / 2;
}

export const SPRING0_STIFFNESS = 7;

/** 1 − (1 + a·u)·e^(−a·u), normalised to reach exactly 1 at u = 1. Starts at rest, peaks near u = 1/a. */
export function spring0At(u: number, stiffness = SPRING0_STIFFNESS): number {
  if (!(u > 0)) return 0;
  if (u >= 1) return 1;
  const a = stiffness;
  const raw = (x: number) => 1 - (1 + a * x) * Math.exp(-a * x);
  return raw(u) / raw(1);
}

/**
 * The even travel profile's keys: a monotone spline (spline.ts, at rest at both ends) through a third of the travel at
 * each third of the time. Its peak speed is 4/3 of its average, where a glide's is nearer 3: the curve for long carries
 * of type, which strobe at a glide's peak, and for camera drifts. `slate qa motion` knows it as "even" (a carry may use
 * it without the brand naming it), from the same keys through slate.motion.eases.mono_spline.
 */
export const EVEN_KEYS: readonly SKey[] = [
  [0, 0],
  [1 / 3, 1 / 3],
  [2 / 3, 2 / 3],
  [1, 1],
];

export function evenAt(u: number): number {
  if (!(u > 0)) return 0;
  if (u >= 1) return 1;
  return monoSpline(u, EVEN_KEYS);
}

// ------------------------------------------------------------------------------------------------------- the eases
/** Evaluate an ease spec at progress u, with exact endpoints. */
export function evalEase(spec: EaseSpec, u: number): number {
  if (!(u > 0)) return 0; // also NaN
  if (u >= 1) return 1;
  switch (spec.kind) {
    case 'linear':
      return u;
    case 'sine':
      return sineAt(u);
    case 'spring0':
      return spring0At(u, spec.stiffness);
    case 'even':
      return evenAt(u);
    case 'bezier':
      return bezierAt(spec.bezier, u);
  }
}

/** A callable ease with its spec and name attached. */
export function makeEase(spec: EaseSpec, name = describe(spec)): Ease {
  const fn = ((u: number) => evalEase(spec, u)) as Ease;
  Object.defineProperty(fn, 'name', {value: name});
  fn.spec = spec;
  return fn;
}

function describe(spec: EaseSpec): string {
  return spec.kind === 'bezier' ? `cubic-bezier(${spec.bezier.join(', ')})` : spec.kind;
}

/**
 * The curves the kit knows by kind, whatever the brand: straight, sine in-out, the zero-bounce spring and the even
 * travel profile. A brand ease of the same name always wins (motion.ts easeNamed).
 */
export const BUILTIN = {
  linear: makeEase({kind: 'linear'}, 'linear'),
  sine: makeEase({kind: 'sine'}, 'sine'),
  spring0: makeEase({kind: 'spring0', stiffness: SPRING0_STIFFNESS}, 'spring0'),
  even: makeEase({kind: 'even'}, 'even'),
} as const;

/**
 * Read an ease from a brand pack, forgivingly: [x1, y1, x2, y2], "cubic-bezier(…)", {bezier: […]}, {cubic_bezier: …},
 * "linear", "sine", {kind: "spring0", stiffness}. Returns null (and the caller records a problem) when it can't.
 * A bezier with x1 or x2 outside [0, 1] isn't a function of time, so it is refused.
 */
export function parseEase(raw: unknown): EaseSpec | null {
  if (raw == null) return null;
  if (typeof raw === 'string') {
    const s = raw.trim().toLowerCase();
    if (s === 'linear') return {kind: 'linear'};
    if (s === 'sine' || s === 'sine-in-out' || s === 'sway') return {kind: 'sine'};
    if (s === 'spring0' || s === 'spring') return {kind: 'spring0', stiffness: SPRING0_STIFFNESS};
    const m = s.match(/^cubic-bezier\(([^)]*)\)$/);
    if (m) return bezierSpec(m[1].split(',').map((v) => Number(v.trim())));
    return null;
  }
  if (Array.isArray(raw)) return bezierSpec(raw.map(Number));
  if (typeof raw === 'object') {
    const o = raw as Record<string, unknown>;
    if (o.bezier !== undefined) return parseEase(o.bezier);
    if (o.cubic_bezier !== undefined) return parseEase(Array.isArray(o.cubic_bezier) ? o.cubic_bezier : `cubic-bezier(${o.cubic_bezier})`);
    const kind = String(o.kind ?? o.type ?? '').toLowerCase();
    if (kind === 'bezier' || kind === 'cubic-bezier') return parseEase(o.points ?? o.values ?? o.value);
    if (kind === 'spring0' || kind === 'spring') {
      const k = Number(o.stiffness ?? SPRING0_STIFFNESS);
      return {kind: 'spring0', stiffness: Number.isFinite(k) && k > 0 ? k : SPRING0_STIFFNESS};
    }
    if (kind) return parseEase(kind);
  }
  return null;
}

function bezierSpec(v: number[]): EaseSpec | null {
  if (v.length !== 4 || v.some((x) => !Number.isFinite(x))) return null;
  const [x1, y1, x2, y2] = v;
  if (x1 < 0 || x1 > 1 || x2 < 0 || x2 > 1) return null;
  return {kind: 'bezier', bezier: [x1, y1, x2, y2]};
}

// ------------------------------------------------------------------------------------------------------------- T90
/**
 * The fraction of a move's duration at which an ease first covers `level` of its travel (0.9 by default: "land" means
 * 90% of travel everywhere in Slate, in the timeline, the kit and the sound engine). Found by a fine scan for the first
 * crossing, then bisection, so it is right for curves that overshoot too.
 */
export function tAt(e: EaseSpec | Ease, level = 0.9): number {
  const spec = typeof e === 'function' ? e.spec : e;
  const f = (u: number) => evalEase(spec, u);
  if (level <= 0) return 0;
  if (level >= 1) {
    // the first time it reaches 1 (a curve that overshoots gets there before its end)
    const N = 4096;
    for (let i = 1; i <= N; i++) if (f(i / N) >= 1) return bisect(f, (i - 1) / N, i / N, 1);
    return 1;
  }
  const N = 4096;
  let prev = 0;
  for (let i = 1; i <= N; i++) {
    const u = i / N;
    if (f(u) >= level) return bisect(f, prev, u, level);
    prev = u;
  }
  return 1;
}

function bisect(f: (u: number) => number, lo: number, hi: number, level: number): number {
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    if (f(mid) >= level) hi = mid;
    else lo = mid;
  }
  return (lo + hi) / 2;
}

/** T90: the fraction of the duration where an ease reaches 90% of its travel. */
export const t90 = (e: EaseSpec | Ease): number => tAt(e, 0.9);

/** The start time that lands a move (90% of travel) on `land`: land − T90 × dur. */
export function startForSpec(land: number, dur: number, e: EaseSpec | Ease): number {
  return land - t90(e) * dur;
}

// --------------------------------------------------------------------------------------------------- After Effects
export type AeHandle = {influence: number; speed: number};
export type AeKeyframeEase = {out: AeHandle; in: AeHandle};

/** AE's smallest influence, in percent. */
export const AE_MIN_INFLUENCE = 0.1;

/**
 * A cubic-bezier as AE keyframe eases (speed as a multiple of the segment's average speed v, influence in percent):
 * out handle influence x1 × 100, speed y1 ÷ x1; in handle influence (1 − x2) × 100, speed (1 − y2) ÷ (1 − x2).
 * A handle collapsed onto its keyframe takes AE's minimum influence and the curve's real end tangent, which then
 * points at the other control point (so (0.4, 0, 1, 1) ends at (1 − 0) ÷ (1 − 0.4) = 1.667 v).
 */
export function aeKeyframeEase(b: Bezier): AeKeyframeEase {
  const [x1, y1, x2, y2] = b;
  const handle = (dx: number, dy: number, otherDx: number, otherDy: number): AeHandle => {
    if (dx > 0) return {influence: Math.max(dx * 100, AE_MIN_INFLUENCE), speed: dy / dx};
    if (dy === 0) return {influence: AE_MIN_INFLUENCE, speed: otherDx > 0 ? otherDy / otherDx : 1};
    return {influence: AE_MIN_INFLUENCE, speed: dy / (AE_MIN_INFLUENCE / 100)};
  };
  return {
    out: handle(x1, y1, x2, y2),
    in: handle(1 - x2, 1 - y2, 1 - x1, 1 - y1),
  };
}
