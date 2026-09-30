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
type When = {t?: number; frame?: number};
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

/** The contact frame of a gesture (where its key pose lands): `frame`, or `t` in film seconds on a whole frame. */
export function contactFrame(g: When): number {
  if (typeof g.frame === 'number') return Math.round(g.frame);
  return frameOf(g.t ?? 0);
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

/**
 * The rig's pose at a film frame (fractional frames are sub-frame samples). A pure function of the track and the
 * frame: evaluate any frame alone and it matches the same frame evaluated in sequence.
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

  const sorted = track.map((g) => ({g, C: contactFrame(g)})).sort((a, b) => a.C - b.C);

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
export type MotifBeat = {frame: number; t: number; gesture: GestureKind; kind: 'character'; size: 's' | 'm' | 'l'; note: string};

const PRIORITY: GestureKind[] = ['turn', 'hold', 'wave', 'pulse', 'ripple', 'breathe', 'enter', 'exit', 'grow', 'home', 'lean'];

/**
 * The rig's sounds: one row per note, each on its own whole contact frame, one sound per frame (by priority), so the
 * events file matches the picture to the frame. Hand the rows to defineEvent (kind "character").
 */
export function motifBeats(track: MotifTrack, spec?: MotifSpec): MotifBeat[] {
  const s = spec ?? motifSpec().spec;
  const rows: MotifBeat[] = [];
  const add = (frame: number, gesture: GestureKind, size: MotifBeat['size'], note: string) => rows.push({frame, t: secOf(frame), gesture, kind: 'character', size, note});
  for (const g of track) {
    const C = contactFrame(g);
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
  // one sound per frame, by priority
  const byFrame = new Map<number, MotifBeat>();
  for (const r of rows) {
    const cur = byFrame.get(r.frame);
    if (!cur || PRIORITY.indexOf(r.gesture) < PRIORITY.indexOf(cur.gesture)) byFrame.set(r.frame, r);
  }
  return [...byFrame.values()].sort((a, b) => a.frame - b.frame);
}

/** Combination rules a track should meet: momentary gestures at least 4 frames apart. Empty is clean. */
export function motifLint(track: MotifTrack): string[] {
  const momentary = track.filter((g) => ['pulse', 'grow', 'turn', 'wave', 'breathe', 'enter', 'exit'].includes(g.kind)).map((g) => ({g, C: contactFrame(g)}));
  momentary.sort((a, b) => a.C - b.C);
  const out: string[] = [];
  for (let i = 1; i < momentary.length; i++) {
    if (momentary[i].C - momentary[i - 1].C < 4) out.push(`${momentary[i - 1].g.kind} and ${momentary[i].g.kind} are ${momentary[i].C - momentary[i - 1].C} frames apart (keep 4 still frames between momentary gestures)`);
  }
  return out;
}

/**
 * Register the rig's part positions as motion channels (x and y of every part, px) so the motion trace carries them
 * and `slate qa motion` can check their speed, acceleration and symmetry (rig = the id you give).
 */
export function registerMotifTrace(track: MotifTrack, o: {rig: string; shot: string; layout: (frame: number) => MotifLayout; from: number; to: number; spec?: MotifSpec; short: number}): void {
  const spec = o.spec ?? motifSpec().spec;
  const at = (frame: number) => placeParts(spec, motifPose(track, frame, {spec, layout: o.layout, short: o.short}), o.layout(frame));
  for (let i = 0; i < spec.parts; i++) {
    for (const axis of ['x', 'y'] as const) {
      defineChannel({id: `${o.rig}.part${i}.${axis}`, shot: o.shot, prop: axis, ease: spec.curves.move, frame_in: o.from, frame_out: o.to, rig: o.rig, fn: (fr) => at(fr)[i][axis]});
    }
  }
}

