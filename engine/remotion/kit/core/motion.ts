// The motion law, bound to the brand in force: its named eases, its duration scale, its staggers and their caps, how
// a move lands on a beat (T90), and the motion registry that `slate qa motion` reads.
//
// Scenes move things with the brand's names only (`tw(t, t0, dur('base'), 'enter')`). A name the brand doesn't have is
// recorded, never thrown, and falls back to a sine so the frame still renders; `motionProblems()` lists it and the
// hand-in check refuses the act until it is fixed. A brand with no motion law at all gets the kit's neutral law
// (standard CSS curves, a plain duration scale) and that is recorded too: every brand gets its own law at onboarding.
import {brandPack, memoOnBrand} from './brand';
import {BUILTIN, makeEase, parseEase, t90, type Ease} from './eases';
import {actShots, allShots, fps, frameOf, shot} from './film';
import {lerp} from './spline';
import type {BrandMotion, BrandPack} from './types';

// ------------------------------------------------------------------------------------------------------ the law
export type MotionRoles = {entrance: string; move: string; state: string; exit: string; relayout?: string; [role: string]: string | undefined};

export type MotionLaw = {
  /** the brand's named eases (these names are the only ones `slate qa motion` accepts) */
  eases: Record<string, Ease>;
  names: string[];
  lineage: Record<string, string>;
  /** the duration scale, seconds, in the pack's order */
  durations: Record<string, number>;
  durationNames: string[];
  /** stagger units in seconds (char, word, line, block, and any others the pack names) */
  staggers: Record<string, number>;
  staggerCap: {seconds: number; fraction: number};
  exitFactor: number;
  roles: MotionRoles;
  overshoot: {allowed: boolean; max: number; where: string[]};
  /** true when the brand had no motion law and the kit's neutral law stood in */
  neutral: boolean;
  problems: string[];
};

/**
 * The kit's stand-in law for a brand with none yet: standard CSS curves (W3C ease-out, ease-in-out, ease-in), a sine
 * for rest-to-rest moves, and a plain duration scale. It exists so a placeholder or a review sheet renders before
 * onboarding; it is never a brand's look.
 */
export const NEUTRAL_MOTION: BrandMotion = {
  eases: {
    enter: {bezier: [0, 0, 0.58, 1], lineage: 'CSS ease-out (W3C)'},
    move: {kind: 'sine', lineage: 'sine in-out'},
    state: {bezier: [0.42, 0, 0.58, 1], lineage: 'CSS ease-in-out (W3C)'},
    leave: {bezier: [0.42, 0, 1, 1], lineage: 'CSS ease-in (W3C)'},
    linear: {kind: 'linear', lineage: 'time and progress only'},
  },
  durations: {tick: 0.2, quick: 0.33, base: 0.5, broad: 0.8, camera: 1.2, signature: 1.8},
  staggers: {char: 0.05, word: 0.1, line: 0.133, block: 0.2},
  stagger_cap: {seconds: 0.6, fraction: 0.5},
  exit_factor: 0.75,
  roles: {entrance: 'enter', move: 'move', state: 'state', exit: 'leave'},
  overshoot: {allowed: false, max: 0},
};

const ROLE_GUESSES: Record<string, string[]> = {
  entrance: ['entrance', 'enter', 'arrive', 'in', 'appear', 'reveal'],
  move: ['move', 'glide', 'carry', 'travel', 'camera'],
  state: ['state', 'snap', 'toggle', 'switch'],
  exit: ['exit', 'leave', 'out', 'depart'],
  relayout: ['relayout', 'lift', 'make-room', 'shift'],
};

function num(v: unknown): number | null {
  const n = typeof v === 'string' ? Number(v) : (v as number);
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
}

/** Build a motion law from a brand pack's `motion` (and the film's rate, for staggers written in frames). */
export function motionLawFrom(motion: BrandMotion | undefined | null, rate?: number): MotionLaw {
  const problems: string[] = [];
  const neutral = !motion || !motion.eases || !Object.keys(motion.eases).length;
  if (neutral) problems.push("the brand has no motion law (motion.eases); the kit's neutral law stands in until onboarding writes one");
  const m: BrandMotion = neutral ? {...NEUTRAL_MOTION, ...(motion ?? {}), eases: NEUTRAL_MOTION.eases} : motion!;

  const eases: Record<string, Ease> = {};
  const lineage: Record<string, string> = {};
  for (const [name, raw] of Object.entries(m.eases ?? {})) {
    const spec = parseEase(raw);
    if (!spec) {
      problems.push(`ease "${name}" isn't a curve the kit can read (use [x1, y1, x2, y2], "linear", "sine" or {kind: "spring0"})`);
      continue;
    }
    eases[name] = makeEase(spec, name);
    const lin = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as {lineage?: unknown}).lineage : undefined;
    if (typeof lin === 'string') lineage[name] = lin;
  }
  const names = Object.keys(eases);

  // durations: {name: seconds} or {name: {seconds}} or [{name, seconds}]
  const durations: Record<string, number> = {};
  const rawDur = m.durations ?? NEUTRAL_MOTION.durations;
  const durEntries: [string, unknown][] = Array.isArray(rawDur)
    ? rawDur.map((d) => [String((d as {name: string}).name), (d as {seconds: number}).seconds])
    : Object.entries(rawDur as Record<string, unknown>);
  for (const [name, v] of durEntries) {
    const s = num(v) ?? num((v as {seconds?: unknown; s?: unknown} | null)?.seconds) ?? num((v as {s?: unknown} | null)?.s);
    if (s === null || s <= 0) problems.push(`duration "${name}" has no positive seconds`);
    else durations[name] = s;
  }
  if (!Object.keys(durations).length) {
    problems.push('the brand has no duration scale; using the neutral scale');
    Object.assign(durations, NEUTRAL_MOTION.durations as Record<string, number>);
  }

  // staggers: seconds, or {frames, at_fps} (frames at the film's rate when at_fps is missing)
  const staggers: Record<string, number> = {};
  for (const [name, v] of Object.entries(m.staggers ?? NEUTRAL_MOTION.staggers ?? {})) {
    const s = num(v) ?? num((v as {seconds?: unknown} | null)?.seconds);
    if (s !== null) {
      staggers[name] = s;
      continue;
    }
    const frames = num((v as {frames?: unknown} | null)?.frames);
    const at = num((v as {at_fps?: unknown} | null)?.at_fps) ?? rate ?? null;
    if (frames !== null && at) staggers[name] = frames / at;
    else problems.push(`stagger "${name}" needs seconds, or frames and a rate`);
  }

  const cap = m.stagger_cap ?? {};
  const staggerCap = {seconds: num(cap.seconds) ?? 0.6, fraction: num(cap.fraction) ?? 0.5};
  const exitFactor = num(m.exit_factor) ?? 0.75;

  // roles: which named ease does each job; guessed from the names when the pack doesn't say
  const roles = {} as MotionRoles;
  const given = m.roles ?? {};
  for (const role of ['entrance', 'move', 'state', 'exit', 'relayout']) {
    const pick = given[role];
    if (pick && eases[pick]) {
      roles[role] = pick;
      continue;
    }
    if (pick) problems.push(`role "${role}" names ease "${pick}", which the brand doesn't define`);
    const guess = names.find((n) => ROLE_GUESSES[role].includes(n.toLowerCase()));
    if (guess) roles[role] = guess;
    else if (role !== 'relayout') {
      roles[role] = names[0] ?? 'sine';
      if (!neutral) problems.push(`no ease for the ${role} role; add motion.roles.${role}`);
    }
  }
  for (const [role, pick] of Object.entries(given)) if (!(role in roles) && pick && eases[pick]) roles[role] = pick;

  const ov = m.overshoot ?? {};
  const overshoot = {allowed: !!ov.allowed, max: num(ov.max) ?? 0, where: Array.isArray(ov.where) ? ov.where.map(String) : []};

  return {
    eases,
    names,
    lineage,
    durations,
    durationNames: Object.keys(durations),
    staggers,
    staggerCap,
    exitFactor,
    roles,
    overshoot,
    neutral,
    problems,
  };
}

function safeFps(): number | undefined {
  try {
    return fps();
  } catch {
    return undefined;
  }
}

const lawFor = memoOnBrand((pack: BrandPack) => motionLawFrom(pack.motion, safeFps()));

/** The motion law of the brand in force. */
export function law(pack?: BrandPack): MotionLaw {
  return lawFor(pack ?? brandPack());
}

// ------------------------------------------------------------------------------------------------------ problems
const problemsSeen = new Set<string>();

/** Everything the motion law or the registry had to assume or refuse, for the hand-in check. */
export function motionProblems(): string[] {
  return [...law().problems, ...problemsSeen];
}

function problem(msg: string): void {
  problemsSeen.add(msg);
}

// ------------------------------------------------------------------------------------------------------ the eases
/**
 * A named ease of the brand in force. The kit's built-in curves (linear, sine, spring0) are always there by kind, for
 * rigs; any other unknown name is recorded and falls back to a sine.
 */
export function easeNamed(name: string): Ease {
  const L = law();
  const e = L.eases[name];
  if (e) return e;
  const builtin = (BUILTIN as Record<string, Ease>)[name];
  if (builtin) return builtin;
  const role = L.roles[name];
  if (role && L.eases[role]) return L.eases[role];
  problem(`ease "${name}" isn't one of the brand's named eases (${L.names.join(', ') || 'none'})`);
  return BUILTIN.sine;
}

/** A named ease's value at progress u (exactly 0 at or before 0, exactly 1 at or after 1). */
export function ease(name: string, u: number): number {
  return easeNamed(name)(u);
}

/** The ease a role uses (entrance, move, state, exit, relayout). */
export function roleEase(role: keyof MotionRoles | string): string {
  const L = law();
  return L.roles[role] ?? L.roles.entrance;
}

/** Eased progress of a move that starts at t0 and lasts dur (seconds): exactly 0 before, exactly 1 after. */
export function tw(t: number, t0: number, dur: number, name?: string): number {
  const e = easeNamed(name ?? roleEase('entrance'));
  if (!(dur > 0)) return t >= t0 ? 1 : 0;
  return e((t - t0) / dur);
}

// --------------------------------------------------------------------------------------------------- durations
/** Seconds for a duration token of the brand's scale (a number passes through). Unknown tokens are recorded. */
export function dur(token: string | number): number {
  if (typeof token === 'number') return token;
  const L = law();
  const s = L.durations[token];
  if (s !== undefined) return s;
  problem(`duration "${token}" isn't on the brand's scale (${L.durationNames.join(', ')})`);
  const names = L.durationNames;
  return L.durations[names[Math.floor(names.length / 2)]] ?? 0.5;
}

/**
 * The i-th shortest duration on the brand's scale (0 is the shortest), clamped to the scale. Components that need "a
 * short one" or "the camera one" without knowing a brand's names ask by rank: 0 tick, 1 quick, 2 base, 3 broad,
 * 4 camera, 5 signature on a six-step scale.
 */
export function durRank(i: number): number {
  const sorted = Object.values(law().durations).sort((a, b) => a - b);
  if (!sorted.length) return 0.5;
  return sorted[Math.max(0, Math.min(sorted.length - 1, Math.round(i)))];
}

/** Whole frames for a duration token (or seconds) at the film's rate (at least one frame). */
export function durFrames(token: string | number): number {
  return Math.max(1, frameOf(dur(token)));
}

/** An exit's duration: the brand's exit factor times its entrance (0.75 × by default). */
export function exitDur(entrance: string | number): number {
  return dur(entrance) * law().exitFactor;
}

/** Which scale token a duration (seconds) is, if it is one (within half a frame). */
export function durationToken(seconds: number): string | null {
  const L = law();
  const half = 0.5 / (safeFps() ?? 1000);
  for (const name of L.durationNames) if (Math.abs(L.durations[name] - seconds) <= half) return name;
  return null;
}

// ---------------------------------------------------------------------------------------------------- staggers
/**
 * The step between staggered items: the unit (seconds, or a name from the brand's staggers), shrunk so the whole
 * group's spread stays within the cap (0.6 s and half the entrance, by default).
 */
export function staggerStep(unit: string | number, n: number, entranceDur?: number): number {
  const L = law();
  let base: number;
  if (typeof unit === 'number') base = unit;
  else {
    const s = L.staggers[unit];
    if (s === undefined) problem(`stagger "${unit}" isn't one of the brand's staggers (${Object.keys(L.staggers).join(', ')})`);
    base = s ?? 0;
  }
  if (n <= 1) return 0;
  let cap = L.staggerCap.seconds;
  if (entranceDur !== undefined && entranceDur > 0) cap = Math.min(cap, L.staggerCap.fraction * entranceDur);
  return Math.min(base, cap / (n - 1));
}

/** When item i of n starts, relative to the group's start. */
export function staggerAt(i: number, n: number, unit: string | number, entranceDur?: number): number {
  return i * staggerStep(unit, n, entranceDur);
}

/** Eased progress of item i in a staggered group (the entrance ease unless one is named). */
export function staggered(t: number, t0: number, i: number, n: number, unit: string | number, duration: number, name?: string): number {
  return tw(t, t0 + staggerAt(i, n, unit, duration), duration, name);
}

// ------------------------------------------------------------------------------------------------------ landing
/** T90 of a named ease: the fraction of its duration at which it covers 90% of its travel. */
export function t90Of(name: string): number {
  return t90(easeNamed(name));
}

/** The start time that lands a move (90% of travel) on `land` (film seconds). */
export function startFor(land: number, duration: string | number, name?: string): number {
  return land - t90Of(name ?? roleEase('entrance')) * dur(duration);
}

/**
 * Whole film frames for a move that lands (90% of travel) on `land`: its start rounded to a frame, and its end a whole
 * number of frames later. Hand it straight to defineMotion.
 */
export function landOn(land: number, duration: string | number, name?: string): {frame_in: number; frame_out: number; ease: string} {
  const e = name ?? roleEase('entrance');
  const frame_in = frameOf(startFor(land, duration, e));
  return {frame_in, frame_out: frame_in + durFrames(duration), ease: e};
}

// ------------------------------------------------------------------------------------------------ the registry
export type MotionValue = number | number[];

/**
 * The element classes `slate qa motion` holds to different speed limits: a rig part, a UI element, a page, card or
 * camera carried across the frame, and a deliberate whip (a person's call).
 */
export const MOTION_CLASSES = ['rig', 'ui', 'carry', 'whip'] as const;
export type MotionClass = (typeof MOTION_CLASSES)[number];
/** The other names the gate reads for each class (character and mark are rig; element is ui; page and camera carry). */
const CLASS_ALIASES: Record<string, MotionClass> = {rig: 'rig', character: 'rig', mark: 'rig', ui: 'ui', element: 'ui', carry: 'carry', page: 'carry', camera: 'carry', whip: 'whip'};
/** Exemptions a move may declare, each a lesson from the films: a reading sweep, and steadiness as the meaning. */
export const MOTION_EXEMPTIONS = ['reading_sweep', 'steady'] as const;
/** A move whose id or property starts with one of these words is the camera: a carry (the gate reads it the same way). */
const CAMERA_WORDS = new Set(['camera', 'cam']);

const matchKey = (v: unknown): string => String(v).toLowerCase().replace(/[^a-z0-9]/g, '');
const firstWord = (v: unknown): string => String(v).toLowerCase().trim().split(/[^a-z0-9]+/)[0] ?? '';

export type MotionSpec = {
  id: string;
  shot: string;
  prop: string;
  ease: string;
  frame_in: number;
  frame_out: number;
  from: MotionValue;
  to: MotionValue;
  entrance: boolean;
  loop_period: number | null;
  rig: string | null;
  // Optional keys `slate qa motion` reads, written only when a move has them (docs/ARCHITECTURE.md §12):
  /** which speed limits hold it: rig, ui, carry or whip (a rig part is rig, the camera a carry, without saying so) */
  class?: MotionClass;
  /** a declared exemption (reading_sweep or steady) and why */
  exempt?: string;
  reason?: string;
  /** why this move breaks the law on purpose: its violations become REVIEW items a person accepts */
  exception?: string;
  /** the element's size in px, so a scale or a turn is speed-checked at its edges */
  size?: number | [number, number];
  /** what the values are in (px, deg, rad …) */
  unit?: string;
};

export type MotionInput<V extends MotionValue> = {
  id: string;
  shot: string;
  prop: string;
  ease?: string;
  frame_in: number;
  frame_out: number;
  from: V;
  to: V;
  entrance?: boolean;
  loop_period?: number | null;
  rig?: string | null;
  /** rig, ui, carry or whip (or the gate's other names: character, mark, element, page, camera) */
  class?: MotionClass | string;
  exempt?: string;
  reason?: string;
  exception?: string;
  size?: number | [number, number];
  unit?: string;
};

/**
 * The optional trace keys for a move, checked the way `slate qa motion` reads them. A class or exemption it wouldn't
 * know is recorded (motionProblems) and left off, never written for the gate to trip on.
 */
function traceKeys(input: {id: string; prop: string; rig?: string | null; class?: string; exempt?: string; reason?: string; exception?: string; size?: number | [number, number]; unit?: string}): Partial<MotionSpec> {
  const out: Partial<MotionSpec> = {};
  if (input.class !== undefined && input.class !== null) {
    const c = CLASS_ALIASES[matchKey(input.class)];
    if (c) out.class = c;
    else problem(`motion "${input.id}": class "${input.class}" isn't one of ${MOTION_CLASSES.join(', ')} (or character, mark, element, page, camera)`);
  }
  if (!out.class && input.rig) out.class = 'rig';
  if (!out.class && (CAMERA_WORDS.has(firstWord(input.id)) || CAMERA_WORDS.has(firstWord(input.prop)))) out.class = 'carry';
  if (input.exempt !== undefined && input.exempt !== null) {
    const ex = MOTION_EXEMPTIONS.find((k) => matchKey(k) === matchKey(input.exempt));
    if (ex) out.exempt = ex;
    else problem(`motion "${input.id}": exempt "${input.exempt}" isn't one of ${MOTION_EXEMPTIONS.join(', ')}`);
  }
  if (typeof input.reason === 'string' && input.reason.trim()) out.reason = input.reason.trim();
  if (typeof input.exception === 'string' && input.exception.trim()) out.exception = input.exception.trim();
  if (typeof input.size === 'number' ? input.size > 0 : Array.isArray(input.size) && input.size.length === 2 && input.size.every((v) => v > 0)) out.size = input.size;
  if (typeof input.unit === 'string' && input.unit.trim()) out.unit = input.unit.trim();
  return out;
}

export type MotionFn<V extends MotionValue> = ((frame: number) => V) & {
  spec: MotionSpec;
  /** the value at a film time (seconds) */
  atTime: (t: number) => V;
  /** eased progress 0 → 1 at a frame */
  progress: (frame: number) => number;
};

type Entry = {spec: MotionSpec; fn: (frame: number) => MotionValue};
const registry = new Map<string, Entry>();

function sameSpec(a: MotionSpec, b: MotionSpec): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function mixValue(from: MotionValue, to: MotionValue, u: number): MotionValue {
  if (Array.isArray(from) && Array.isArray(to)) {
    const n = Math.min(from.length, to.length);
    const out: number[] = [];
    for (let i = 0; i < n; i++) out.push(from[i] + (to[i] - from[i]) * u);
    return out;
  }
  return lerp(from as number, to as number, u);
}

/**
 * Define a move once, as data: returns a pure frame → value function, and registers the spec so the engine can sample
 * every move of an act into timing/motion/actN.json for `slate qa motion`. Frames are film frames (fractional frames,
 * the motion-blur sub-samples, are fine). Before frame_in it is `from`, after frame_out it is `to`; a looping move
 * (loop_period frames) cycles between them from frame_in to frame_out.
 *
 * Say what the element is when the gate can't tell: `class: 'carry'` for a page or card carried across the frame (a
 * rig part is rig and the camera a carry without saying so), `exempt` with a `reason` for a declared exemption,
 * `exception` for a deliberate break a person signs off. A carry may use the kit's "even" curve without the brand
 * naming it.
 *
 * Never throws: an id defined twice with a different spec, an unknown shot or ease, reversed frames or mismatched
 * values are recorded in motionProblems(), and the function still returns something drawable.
 */
export function defineMotion<V extends MotionValue>(input: MotionInput<V>): MotionFn<V> {
  const easeName = input.ease ?? roleEase(input.entrance ? 'entrance' : 'move');
  let frame_in = Math.round(input.frame_in);
  let frame_out = Math.round(input.frame_out);
  if (!input.id) problem('a motion was defined without an id');
  if (frame_in !== input.frame_in || frame_out !== input.frame_out) problem(`motion "${input.id}": frames must be whole frames (rounded)`);
  if (frame_out < frame_in) {
    problem(`motion "${input.id}": frame_out comes before frame_in (swapped)`);
    [frame_in, frame_out] = [frame_out, frame_in];
  }
  if (shot(input.shot).missing) problem(`motion "${input.id}": shot "${input.shot}" isn't in timing/shots.json`);
  easeNamed(easeName); // records an unknown name
  const vec = Array.isArray(input.from) || Array.isArray(input.to);
  if (vec && (!Array.isArray(input.from) || !Array.isArray(input.to) || input.from.length !== input.to.length)) {
    problem(`motion "${input.id}": from and to must both be numbers or both be lists of the same length`);
  }
  const period = input.loop_period ?? null;
  if (period !== null && !(period >= 1)) problem(`motion "${input.id}": loop_period must be at least 1 frame`);

  const spec: MotionSpec = {
    id: input.id,
    shot: input.shot,
    prop: input.prop,
    ease: easeName,
    frame_in,
    frame_out,
    from: input.from,
    to: input.to,
    entrance: !!input.entrance,
    loop_period: period,
    rig: input.rig ?? null,
    ...traceKeys(input),
  };

  const progress = (frame: number): number => {
    const e = easeNamed(spec.ease);
    if (period !== null && period >= 1) {
      if (frame <= frame_in || frame >= frame_out) return 0;
      const phase = ((frame - frame_in) % period) / period;
      return e(phase);
    }
    if (frame_out === frame_in) return frame >= frame_in ? 1 : 0;
    return e((frame - frame_in) / (frame_out - frame_in));
  };
  const at = (frame: number): V => {
    if (period !== null && period >= 1 && (frame <= frame_in || frame >= frame_out)) return spec.from as V;
    return mixValue(spec.from, spec.to, progress(frame)) as V;
  };

  const prev = registry.get(spec.id);
  if (prev && !sameSpec(prev.spec, spec)) problem(`motion id "${spec.id}" is defined twice with different specs (the first one stands)`);
  if (!prev) registry.set(spec.id, {spec, fn: at});

  const fn = at as MotionFn<V>;
  fn.spec = spec;
  fn.atTime = (t: number) => at(t * fps());
  fn.progress = progress;
  return fn;
}

/**
 * Register a rig channel: a value that isn't one eased move (a mark's part through several gestures) but should still
 * be traced and checked. `fn` must be a pure function of the frame. `ease` names the curve its segments use. A rig
 * channel is traced as class "rig" (its own speed limits).
 */
export function defineChannel(input: {id: string; shot: string; prop: string; ease: string; frame_in: number; frame_out: number; rig: string; entrance?: boolean; fn: (frame: number) => number}): void {
  const spec: MotionSpec = {
    id: input.id,
    shot: input.shot,
    prop: input.prop,
    ease: input.ease,
    frame_in: Math.round(input.frame_in),
    frame_out: Math.round(input.frame_out),
    from: input.fn(Math.round(input.frame_in)),
    to: input.fn(Math.round(input.frame_out)),
    entrance: !!input.entrance,
    loop_period: null,
    rig: input.rig,
    ...traceKeys({id: input.id, prop: input.prop, rig: input.rig}),
  };
  if (!registry.has(spec.id)) registry.set(spec.id, {spec, fn: input.fn});
}

/** Every registered move, in definition order. */
export function motions(): MotionSpec[] {
  return [...registry.values()].map((e) => e.spec);
}

/** Forget every registered move and recorded problem (tests, collectors). */
export function clearMotions(): void {
  registry.clear();
  problemsSeen.clear();
}

export type TraceEntry = MotionSpec & {samples: [number, MotionValue][]};

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;

/**
 * Sample registered moves at every whole frame of their span: the motion trace (timing/motion/actN.json) that
 * `slate qa motion` checks. Filter by act (its shots) or by a list of shot ids.
 */
export function traceMotions(filter: {act?: number | string; shots?: string[]} = {}): TraceEntry[] {
  let allowed: Set<string> | null = null;
  if (filter.shots) allowed = new Set(filter.shots);
  else if (filter.act !== undefined) allowed = new Set(actShots(filter.act).map((s) => s.id));
  const out: TraceEntry[] = [];
  for (const {spec, fn} of registry.values()) {
    if (allowed && !allowed.has(spec.shot)) continue;
    const samples: [number, MotionValue][] = [];
    for (let f = spec.frame_in; f <= spec.frame_out; f++) {
      const v = fn(f);
      samples.push([f, Array.isArray(v) ? v.map(round6) : round6(v)]);
    }
    out.push({...spec, samples});
  }
  return out;
}

/** Shots known to the film, for a registry check that every move belongs to a real shot. */
export function unknownShotsInRegistry(): string[] {
  const known = new Set(allShots().map((s) => s.id));
  return [...new Set(motions().filter((m) => !known.has(m.shot)).map((m) => m.shot))];
}

